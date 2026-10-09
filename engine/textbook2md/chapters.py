"""Pass 1 (source inspection) + chapter detection + book profile."""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from .textclean import normalize_chars
from .util import slugify

CH_NUM_RE = re.compile(r"^\s*(?:chapter|ch\.?)\s*(\d{1,4})\b[\s.:\-–—]*(.*)$", re.I)
NUM_TITLE_RE = re.compile(r"^\s*(\d{1,4})[\s.:\-–—)]+(\S.*)$")
BACK_FRONT = re.compile(r"^(contents?|table of contents|index|preface|foreword|acknowledg\w*|copyright|title page|"
                        r"cover|about the authors?|dedication|list of contributors|contributors|front matter|back matter)\b", re.I)
PART_RE = re.compile(r"^\s*part\s+[\dIVXLC]+\b[\s.:\-–—]*(.*)$", re.I)


@dataclass
class Chapter:
    key: str
    number: int | None
    title: str
    start: int            # 1-based PDF page
    end: int
    part: str = ""
    confidence: float = 0.9
    source: str = "outline"
    start_y: float | None = None
    end_y: float | None = None
    issues: list[str] = field(default_factory=list)
    outline_title: str = ""

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return Chapter(**{k: v for k, v in d.items() if k in Chapter.__dataclass_fields__})

    @property
    def pages(self):
        return list(range(self.start, self.end + 1))


def parse_chapter_title(raw: str) -> tuple[int | None, str]:
    raw = normalize_chars(raw).strip()
    m = CH_NUM_RE.match(raw)
    if m:
        return int(m.group(1)), (m.group(2).strip() or raw)
    m = NUM_TITLE_RE.match(raw)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, raw


def assign_keys(chs: list[Chapter]) -> None:
    used: set[str] = set()
    for i, c in enumerate(chs, 1):
        base = f"ch_{c.number:03d}" if c.number is not None else f"ch_x{i:03d}"
        key, n = base, 0
        while key in used:
            n += 1
            key = f"{base}{chr(96 + n)}"
        used.add(key)
        c.key = key


def validate(chs: list[Chapter], npages: int) -> None:
    prev_end = 0
    for c in chs:
        if c.start < 1 or c.end > npages or c.end < c.start:
            c.confidence = min(c.confidence, 0.2)
            c.issues.append(f"invalid page range {c.start}-{c.end} (document has {npages} pages)")
        if c.start <= prev_end and not (c.start == prev_end and c.start_y is not None):
            c.confidence = min(c.confidence, 0.3)
            c.issues.append("page range overlaps the previous chapter")
        prev_end = max(prev_end, c.end)
    nums = [c.number for c in chs if c.number is not None]
    for n, k in Counter(nums).items():
        if k > 1:
            for c in chs:
                if c.number == n:
                    c.confidence = min(c.confidence, 0.4)
                    c.issues.append(f"duplicate chapter number {n}")
    for a, b in zip(chs, chs[1:]):
        if a.number is not None and b.number is not None and b.number != a.number + 1:
            b.issues.append(f"chapter number jumps from {a.number} to {b.number}")
            b.confidence = min(b.confidence, 0.75)


def from_outline(doc) -> list[Chapter]:
    toc = doc.get_toc(simple=False)
    if not toc:
        return []
    entries = []
    for lvl, title, page, dest in toc:
        y = None
        try:
            to = dest.get("to") if isinstance(dest, dict) else None
            if to is not None and page >= 1:
                y = float(to.y)
        except Exception:
            y = None
        entries.append((lvl, normalize_chars(title).strip(), page, y))
    levels = sorted({e[0] for e in entries})
    numbered = {L: sum(1 for e in entries if e[0] == L and parse_chapter_title(e[1])[0] is not None
                       and not PART_RE.match(e[1])) for L in levels}
    L = max(levels, key=lambda l: (numbered[l], -l))
    use_numbers = numbered[L] >= 3 or (numbered[L] >= 1 and numbered[L] >= 0.6 * sum(1 for e in entries if e[0] == L))
    if not use_numbers:
        cand = [l for l in levels if l <= 2 and sum(1 for e in entries if e[0] == l and not PART_RE.match(e[1])) >= 2]
        L = min(cand, key=lambda l: -sum(1 for e in entries if e[0] == l)) if cand else levels[0]
    chs: list[Chapter] = []
    npages = doc.page_count
    part = ""
    for i, (lvl, title, page, y) in enumerate(entries):
        if lvl < L:
            m = PART_RE.match(title)
            part = (m.group(1).strip() if m and m.group(1).strip() else title)
            continue
        if lvl > L:
            continue
        if BACK_FRONT.match(title) or PART_RE.match(title):
            continue
        num, t = parse_chapter_title(title)
        if use_numbers and num is None:
            continue
        end, end_y = npages, None
        for lvl2, title2, page2, y2 in entries[i + 1:]:
            if lvl2 <= L:
                if page2 > page:
                    end = page2 - 1
                else:
                    end, end_y = page, y2
                break
        chs.append(Chapter(key="", number=num if use_numbers else None, title=t, start=page, end=max(end, page),
                           part=part, confidence=0.95, source="outline", end_y=end_y if end_y else None,
                           outline_title=title))
    for a, b in zip(chs, chs[1:]):
        if a.end == b.start and a.end_y is not None:
            b.start_y = a.end_y
        elif a.end == b.start:
            a.issues.append("next chapter starts on the same page but no position is known")
            a.confidence = min(a.confidence, 0.5)
    assign_keys(chs)
    return chs


TOC_LINE = re.compile(r"^\s*(?:chapter\s+)?(\d{1,4})[.\s]+(.{3,90}?)\s*[.·…\s]{2,}\s*(\d{1,5})\s*$", re.I)


def from_toc_pages(doc, max_scan: int = 40) -> list[Chapter]:
    found = []
    for pno in range(min(max_scan, doc.page_count)):
        text = normalize_chars(doc[pno].get_text("text"))
        hits = [TOC_LINE.match(l) for l in text.splitlines()]
        hits = [h for h in hits if h]
        if len(hits) >= 3:
            found.extend((int(h.group(1)), h.group(2).strip(" .·…"), int(h.group(3))) for h in hits)
    if len(found) < 3:
        return []
    offset = _page_offset(doc, found)
    chs = []
    for i, (num, title, printed) in enumerate(found):
        start = printed + offset
        end = (found[i + 1][2] + offset - 1) if i + 1 < len(found) else doc.page_count
        chs.append(Chapter(key="", number=num, title=title, start=start, end=max(end, start),
                           confidence=0.8, source="toc"))
    assign_keys(chs)
    return chs


def _page_offset(doc, found) -> int:
    votes = Counter()
    for num, title, printed in found[:6]:
        key = re.sub(r"\W+", " ", title.lower()).strip()[:30]
        for off in range(0, min(doc.page_count - printed, 60)):
            pno = printed + off - 1
            if 0 <= pno < doc.page_count and key in re.sub(r"\W+", " ", doc[pno].get_text("text").lower()):
                votes[off] += 1
                break
    return votes.most_common(1)[0][0] if votes else 0


def from_layout(doc) -> list[Chapter]:
    """Chapter-opening pages: a very large line near the page top that starts with a chapter number."""
    starts = []
    for pno in range(doc.page_count):
        d = doc[pno].get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
        best = None
        for b in d["blocks"]:
            for ln in b.get("lines", []):
                for s in ln["spans"]:
                    if best is None or s["size"] > best[0]:
                        best = (s["size"], normalize_chars(" ".join(sp["text"] for sp in ln["spans"])).strip(), ln["bbox"][1])
        if best and best[0] >= 16 and best[2] < doc[pno].rect.height * 0.4:
            m = CH_NUM_RE.match(best[1])
            if m:
                starts.append((pno + 1, int(m.group(1)), m.group(2).strip() or best[1]))
    chs = []
    for i, (p, num, title) in enumerate(starts):
        end = starts[i + 1][0] - 1 if i + 1 < len(starts) else doc.page_count
        chs.append(Chapter(key="", number=num, title=title, start=p, end=max(end, p), confidence=0.6, source="layout"))
    assign_keys(chs)
    return chs


def detect_chapters(doc) -> list[Chapter]:
    for fn in (from_outline, from_toc_pages, from_layout):
        chs = fn(doc)
        if chs:
            validate(chs, doc.page_count)
            return chs
    c = Chapter(key="ch_x001", number=None, title="Entire document", start=1, end=doc.page_count,
                confidence=0.3, source="single",
                issues=["no outline, TOC or chapter pattern found — define chapter boundaries manually"])
    return [c]


# ----------------------------------------------------------------------------- metadata

def detect_metadata(doc, pdf_path: str | Path) -> dict:
    md = doc.metadata or {}
    title = (md.get("title") or "").strip()
    first = ""
    for pno in range(min(6, doc.page_count)):
        first += "\n" + normalize_chars(doc[pno].get_text("text"))
    if not title:
        d = doc[0].get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
        spans = [(s["size"], s["text"].strip()) for b in d["blocks"] for l in b.get("lines", []) for s in l["spans"] if s["text"].strip()]
        if spans:
            big = max(s[0] for s in spans)
            title = " ".join(t for sz, t in spans if sz >= big - 0.5)
    title = title or Path(pdf_path).stem.replace("_", " ")
    edition = ""
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\s+edition\b", first, re.I)
    if m:
        edition = m.group(1)
    else:
        m = re.search(r"\bedition\s*[:\-]?\s*(\d{1,2})\b", first, re.I)
        edition = m.group(1) if m else ""
    first_word = slugify(title).split("-")[0] if title else "book"
    if first_word in {"the", "a", "an", "test"} and len(slugify(title).split("-")) > 1:
        first_word = slugify(title).split("-")[1] if first_word != "test" else "test"
    book_id = f"{first_word}{edition}" if edition else first_word
    return {"title": title, "edition": edition, "authors": (md.get("author") or "").strip(),
            "book_id": book_id, "pages": doc.page_count, "pdf_title": md.get("title") or "",
            "encrypted": bool(doc.is_encrypted), "has_outline": bool(doc.get_toc())}


# ------------------------------------------------------------------------------ profile

def norm_pattern(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", normalize_chars(text).lower())).strip()


def profile_book(doc, sample: int = 60) -> dict:
    n = doc.page_count
    m = min(sample, n)
    idx = sorted({int(i * (n - 1) / max(m - 1, 1)) for i in range(m)})
    zone_counts: Counter = Counter()
    sizes: Counter = Counter()
    xref_pages: Counter = Counter()
    gutters = []
    for pno in idx:
        page = doc[pno]
        H = page.rect.height
        d = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
        seen = set()
        for b in d["blocks"]:
            for ln in b.get("lines", []):
                t = "".join(s["text"] for s in ln["spans"]).strip()
                if not t:
                    continue
                yc = (ln["bbox"][1] + ln["bbox"][3]) / 2
                if yc < H * 0.085 or yc > H * 0.915:
                    seen.add(norm_pattern(t))
                else:
                    for s in ln["spans"]:
                        sizes[round(s["size"], 1)] += len(s["text"].strip())
        zone_counts.update(seen)
        try:
            for info in page.get_image_info(xrefs=True):
                if info.get("xref"):
                    xref_pages[info["xref"]] += 1
        except Exception:
            pass
    k = len(idx)
    running = sorted(p for p, c in zone_counts.items() if c >= max(3, 0.25 * k) and p)
    repeated = sorted(x for x, c in xref_pages.items() if c >= max(3, 0.25 * k))
    body = sizes.most_common(1)[0][0] if sizes else 10.0
    return {"body_size": body, "running_patterns": running, "repeated_xrefs": repeated, "sampled_pages": k}
