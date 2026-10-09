"""Passes 5–10: cleanup, heading/list/callout reconstruction, tables, figures, citations → blocks."""
from __future__ import annotations

import html
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from . import numeric
from .chapters import norm_pattern
from .layout import FIG_CAPTION_RE, TABLE_CAPTION_RE
from .models import Issue
from .textclean import (BOLD, ITALIC, SUB, SUP, JoinLog, Span, Vocab, ends_sentence, escape_line_start,
                        join_span_lines, spans_plain, spans_to_md, starts_continuation)

BULLET_RE = re.compile(r"^\s*([•·▪●◦○■▫‣⁃∙]|[–—-])\s*(\S.*)?$")
DASH_ONLY = set("–—-")
NUM_ITEM_RE = re.compile(r"^\s*(\(?\d{1,3}[.)])\s+(\S.*)$")
ALPHA_ITEM_RE = re.compile(r"^\s*(\(?[a-h][.)])\s+(\S.*)$")
REF_HEADINGS = re.compile(r"^(references?|bibliography|suggested reading|further reading|selected references|"
                          r"recommended reading|key references)\s*:?$", re.I)
CALLOUT_TITLES = re.compile(r"^(key points?|clinical pearls?|important|controversies|summary|pearls?|"
                            r"key concepts?|clinical tip|warning|caution|note|box \d+[\d.]*.*|what'?s new|"
                            r"red flags?|learning objectives|take[- ]home points?)\s*:?$", re.I)
XREF_LABEL = re.compile(r"^\s*(figure|fig\.?|table|box|algorithm|plate)\s*([\dIVXA-Z][\d.\-A-Za-z]*?)\s*[.:|]?\s", re.I)
LABEL_PREFIX = re.compile(r"^((?:figure|fig\.?|table|box|algorithm|plate)\s+[\dIVXA-Z][\d.\-A-Za-z]*?\.?)(?=\s|$)", re.I)
FOOTNOTE_MARK = re.compile(r"^[*†‡§¶]$")
SYMBOLS_LATEX = {"×": r"\times ", "÷": r"\div ", "±": r"\pm ", "≥": r"\geq ", "≤": r"\leq ", "≠": r"\neq ",
                 "≈": r"\approx ", "−": "-", "–": "-", "√": r"\sqrt", "∑": r"\sum ", "∫": r"\int ", "∞": r"\infty ",
                 "α": r"\alpha ", "β": r"\beta ", "γ": r"\gamma ", "δ": r"\delta ", "Δ": r"\Delta ", "μ": r"\mu ",
                 "µ": r"\mu ", "π": r"\pi ", "σ": r"\sigma ", "λ": r"\lambda ", "θ": r"\theta ", "→": r"\rightarrow ",
                 "°": r"^{\circ}", "·": r"\cdot "}


@dataclass
class Para:
    lines: list[dict]
    pages: list[int]
    kind: str = "para"
    marker: str | None = None          # list marker text
    ordered: bool = False
    x0: float = 0.0
    spans: list[Span] = field(default_factory=list)
    text: str = ""
    size: float = 0.0
    bold: bool = False
    conf: float = 1.0
    box: int | None = None
    page_box: tuple | None = None
    h_conf: float = 0.0
    h_level: int = 0
    log: JoinLog = field(default_factory=JoinLog)

    @property
    def first(self):
        return self.lines[0]


@dataclass
class Block:
    id: str
    type: str                       # heading|paragraph|list|table|figure|callout|reference|footnote|unreadable|formula|caption
    md: str = ""
    raw: str = ""
    clean: str = ""
    pages: list[int] = field(default_factory=list)
    bboxes: list[dict] = field(default_factory=list)
    conf: float = 1.0
    level: int = 0
    heading_path: list[str] = field(default_factory=list)
    section_id: str = ""
    flags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    box: int | None = None

    @property
    def page(self):
        return self.pages[0] if self.pages else None


@dataclass
class BuildCtx:
    chapter: dict
    book: dict
    pages: list[dict]
    memory: dict
    vocab: Vocab
    running_patterns: set
    ai_heading: Callable | None = None
    issues: list[Issue] = field(default_factory=list)

    def issue(self, sev, code, msg, page=None, block=None, cat="structure"):
        self.issues.append(Issue(sev, code, msg, page, block, cat))


# ------------------------------------------------------------------------ pass 5: cleanup

def remove_running(ctx: BuildCtx) -> dict:
    pages = ctx.pages
    n = len(pages)
    cnt: Counter = Counter()
    for pg in pages:
        seen = set()
        for it in pg["items"]:
            if it["t"] == "line" and it.get("zone"):
                seen.add(norm_pattern(it["raw"]))
        cnt.update(seen)
    thresh = max(2, int(0.6 * n + 0.5)) if n >= 2 else 10 ** 9
    local = {p for p, c in cnt.items() if c >= thresh and p}
    patterns = set(ctx.running_patterns) | local
    removed = []
    from .layout import reorder_page
    for pg in pages:
        keep, changed = [], False
        for it in pg["items"]:
            if it["t"] == "line" and it.get("zone"):
                pat = norm_pattern(it["raw"])
                if pat in patterns or re.fullmatch(r"(page\s*)?#+", pat) or re.fullmatch(r"[ivxlc]+", pat):
                    removed.append({"page": pg["page"], "bbox": it["bbox"], "text": it["raw"]})
                    continue
                # a margin-zone line that is not a running header/footer is ordinary content (e.g. a
                # footnote or a body line close to the page edge): put it back into the reading flow
                it["zone"] = None
                changed = True
            keep.append(it)
        pg["items"] = keep
        if changed:
            reorder_page(pg)
    return {"removed": removed, "local_patterns": sorted(local)}


# --------------------------------------------------------------------- pass 5b: paragraphs

def body_stats(pages: list[dict]) -> dict:
    sizes: Counter = Counter()
    gaps = []
    for pg in pages:
        prev = None
        for it in pg["items"]:
            if it["t"] != "line" or it.get("zone"):
                prev = None
                continue
            sizes[round(it["size"], 1)] += len(it["raw"].strip())
            if prev and prev["col"] == it["col"] and abs(prev["size"] - it["size"]) < 0.3:
                g = it["bbox"][1] - prev["bbox"][3]
                if -2 < g < it["size"] * 1.5:
                    gaps.append(g)
            prev = it
    body = sizes.most_common(1)[0][0] if sizes else 10.0
    return {"body": body, "gap": statistics.median(gaps) if gaps else 1.0}


def _col_right(pages):
    d = {}
    for pg in pages:
        for it in pg["items"]:
            if it["t"] == "line" and not it.get("zone"):
                k = (pg["page"], it["col"])
                d[k] = max(d.get(k, 0), it["bbox"][2])
    return d


def marker_of(line: dict, allow_dash=True):
    raw = line["raw"]
    m = BULLET_RE.match(raw)
    if m and m.group(2):
        if m.group(1) in DASH_ONLY and not allow_dash:
            return None
        if m.group(1) in DASH_ONLY and not re.match(r"^\s*[–—-]\s+\S", raw):
            return None
        return ("bullet", m.group(1))
    m = NUM_ITEM_RE.match(raw)
    if m and re.match(r"[A-Z(\[\d]", m.group(2)[0]):
        return ("num", m.group(1))
    m = ALPHA_ITEM_RE.match(raw)
    if m:
        return ("alpha", m.group(1))
    return None


def is_caption_start(line: dict, body: float) -> bool:
    raw = line["raw"]
    if not (FIG_CAPTION_RE.match(raw) or TABLE_CAPTION_RE.match(raw)):
        return False
    m = XREF_LABEL.match(raw + " ")
    if not m:
        return False
    first_bold = bool(line["spans"] and line["spans"][0][1] & BOLD)
    after = raw[m.end() - 1:].lstrip()[:1] if m else ""
    punct = bool(re.match(r"^\s*(figure|fig\.?|table|box|algorithm|plate)\s+[\dIVXA-Z][\d.\-A-Za-z]*?[.:|]", raw, re.I))
    upper = raw.split()[0].isupper() and len(raw.split()[0]) > 2
    return first_bold or punct or upper or line["size"] < body - 0.4


def segment(ctx: BuildCtx, stats: dict) -> list:
    """Return stream of ('para', Para) | ('table', item, page) | ('figure', item, page)."""
    body, gap0 = stats["body"], stats["gap"]
    colr = _col_right(ctx.pages)
    stream: list = []
    cur: Para | None = None
    prev: dict | None = None
    prev_page = None

    def close():
        nonlocal cur
        if cur:
            stream.append(("para", cur))
        cur = None

    for pg in ctx.pages:
        page = pg["page"]
        for it in pg["items"]:
            if it["t"] != "line":
                close()
                prev = None
                stream.append((it["t"], it, page))
                continue
            mk = marker_of(it)
            brk = cur is None or prev is None
            if not brk:
                same_col_page = prev_page == page and prev["col"] == it["col"]
                size_d = abs(it["size"] - prev["size"])
                if mk:
                    brk = True
                elif size_d > 0.7:
                    brk = True
                elif (it["bold"] != prev["bold"]) and (it["bold"] or prev["bold"]) and (
                        len(it["raw"]) < 90 and it["bold"] or len(prev["raw"]) < 90 and prev["bold"]):
                    brk = True
                elif it.get("box") != prev.get("box"):
                    brk = True
                elif is_caption_start(it, body) or (is_caption_start(prev, body) and cur and len(cur.lines) == 1
                                                    and it["size"] < prev["size"] - 5):
                    brk = True
                elif same_col_page:
                    gap = it["bbox"][1] - prev["bbox"][3]
                    in_list = bool(cur.marker)
                    if gap > gap0 + 0.45 * it["size"]:
                        brk = True
                    elif (not in_list) and it["bbox"][0] > prev["bbox"][0] + 0.9 * it["size"] and gap > -1 \
                            and it["bbox"][0] - prev["bbox"][0] < 4 * it["size"] and len(cur.lines) >= 1 and \
                            ends_sentence(prev["raw"]):
                        brk = True
                    elif (prev["bbox"][2] < colr.get((page, it["col"]), 1e9) - 3.2 * it["size"]
                          and ends_sentence(prev["raw"]) and not cur.marker):
                        brk = True
                    elif cur.marker and it["bbox"][0] < prev["bbox"][0] - 3 and not marker_of(it):
                        brk = True
                else:
                    # continuation across column / page: only when the sentence is unfinished
                    if ends_sentence(prev["raw"]) and not starts_continuation(it["raw"]):
                        brk = True
                    elif it["bold"] != prev["bold"]:
                        brk = True
            if brk:
                close()
                cur = Para(lines=[it], pages=[page], x0=it["bbox"][0], box=it.get("box"))
                if mk:
                    cur.kind, cur.marker, cur.ordered = "list", mk[1], mk[0] == "num"
            else:
                cur.lines.append(it)
                if page not in cur.pages:
                    cur.pages.append(page)
            prev, prev_page = it, page
    close()
    return stream


def finish_para(p: Para, vocab: Vocab, tag_pages=True) -> None:
    """Join lines into spans (dehyphenation etc.), compute features."""
    groups: list[list[list[Span]]] = []
    last_page = None
    for ln in p.lines:
        pg = int(ln["id"].split("-")[0][1:]) if "id" in ln else p.pages[0]
        spans = [Span.from_json(s) for s in ln["spans"]]
        if last_page is None or pg != last_page:
            groups.append([])
            last_page = pg
            ln_pg = pg
        groups[-1].append(spans)
    joined_groups = []
    for gi, g in enumerate(groups):
        js = join_span_lines(g, vocab, p.log)
        joined_groups.append(js)
    if len(joined_groups) > 1:
        for gi in range(1, len(joined_groups)):
            if joined_groups[gi]:
                pgn = p.pages[gi] if gi < len(p.pages) else p.pages[-1]
                joined_groups[gi][0] = Span(joined_groups[gi][0].text, joined_groups[gi][0].bits, pgn)
        spans = join_span_lines(joined_groups, vocab, p.log)
    else:
        spans = joined_groups[0] if joined_groups else []
    if p.marker:
        spans = strip_marker(spans, p.marker)
    p.spans = spans
    p.text = spans_plain(spans).strip()
    sizes = [l["size"] for l in p.lines]
    p.size = statistics.median(sizes)
    p.bold = all(l["bold"] for l in p.lines)
    p.conf = min(l.get("conf", 1.0) for l in p.lines)


def strip_marker(spans: list[Span], marker: str) -> list[Span]:
    spans = [Span(s.text, s.bits, s.mark) for s in spans]
    m = marker.strip()
    while spans and not spans[0].text.strip():
        spans.pop(0)
    consumed = ""
    while spans and len(consumed) < len(m):
        t = spans[0].text.lstrip() if not consumed else spans[0].text
        take = m[len(consumed): len(consumed) + len(t)]
        if t.startswith(take):
            consumed += take
            rest = t[len(take):]
            if rest:
                spans[0] = Span(rest.lstrip(), spans[0].bits, spans[0].mark)
            else:
                spans.pop(0)
        else:
            break
    while spans and not spans[0].text.strip():
        spans.pop(0)
    if spans:
        spans[0] = Span(spans[0].text.lstrip(), spans[0].bits, spans[0].mark)
    return spans


# --------------------------------------------------------------------------- headings

def heading_conf(p: Para, body: float) -> float:
    if p.kind == "list" or len(p.lines) > 3 or p.box is not None:
        return 0.0
    t = p.text
    if not t or len(t) > 160 or CALLOUT_TITLES.match(t.rstrip(":").strip()):
        return 0.0
    ratio = p.size / body if body else 1.0
    if ratio >= 1.45:
        c = 0.95
    elif ratio >= 1.22:
        c = 0.9
    elif ratio >= 1.1:
        c = 0.8
    elif p.bold and ratio >= 0.97:
        c = 0.62
    elif p.bold:
        c = 0.4
    elif all(l["italic"] for l in p.lines) and len(t) < 70 and ratio >= 0.97:
        c = 0.35
    else:
        return 0.0
    if t.endswith(".") and not re.search(r"\b(vs|etc|Dr|Jr|Inc)\.$", t):
        c -= 0.3
    if len(t) > 100:
        c -= 0.2
    if len(p.lines) > 2:
        c -= 0.15
    if t[0].islower():
        c -= 0.4
    if re.match(r"^\d+(\.\d+)*\s+\S", t):
        c += 0.08
    if is_caption_start(p.first, 0):
        c = 0.0
    return max(0.0, min(c, 0.99))


def assign_levels(heads: list[Para], memory: dict, title_para_ids: set, body: float) -> None:
    mem = memory.setdefault("heading_styles", {})
    styles = {}
    for p in heads:
        if id(p) in title_para_ids:
            continue
        key = f"{round(p.size)}|{'b' if p.bold else 'n'}"
        styles.setdefault(key, (p.size, p.bold))
    order = sorted(styles.items(), key=lambda kv: (-kv[1][0], not kv[1][1]))
    levels: dict[str, int] = {}
    prev_level = 1
    for key, _ in order:
        if key in mem:                                   # conversion memory wins (same book → same styles)
            levels[key] = mem[key]
        else:                                            # dense ranking below the nearest larger style
            levels[key] = max(2, min(prev_level + 1, 4)) if prev_level >= 2 else 2
        prev_level = levels[key]
    for key, lvl in levels.items():
        mem[key] = min(lvl, 4)
    for p in heads:
        if id(p) in title_para_ids:
            p.h_level = 1
        else:
            key = f"{round(p.size)}|{'b' if p.bold else 'n'}"
            p.h_level = min(levels[key], 4)


def match_title(text: str, title: str) -> float:
    def toks(s):
        s = re.sub(r"^\s*(chapter|ch\.?)\s*\d+\s*", "", s, flags=re.I)
        return set(re.findall(r"[a-z0-9]+", s.lower()))
    a, b = toks(text), toks(title)
    if not b:
        return 0.0
    return len(a & b) / len(b)


# ----------------------------------------------------------------------- tables/figures

def spans_to_html(spans: list[Span]) -> str:
    out = []
    from .textclean import merge_runs
    for run in merge_runs(spans):
        txt = html.escape(run.text, quote=False)
        if not txt.strip():
            out.append(txt)
            continue
        if run.bits & SUP:
            txt = f"<sup>{txt}</sup>"
        elif run.bits & SUB:
            txt = f"<sub>{txt}</sub>"
        if run.bits & ITALIC:
            txt = f"<i>{txt}</i>"
        if run.bits & BOLD:
            txt = f"<b>{txt}</b>"
        out.append(txt)
    return re.sub(r"\s{2,}", " ", "".join(out)).strip()


def render_table(t: dict) -> tuple[str, str]:
    """Return (markdown, kind) where kind in {'gfm','html'}."""
    cells = t["cells"]
    nrows, ncols, hr = t["nrows"], t["ncols"], t["header_rows"]
    if not t["complex"]:
        grid = [[""] * ncols for _ in range(nrows)]
        for c in cells:
            sp = [Span.from_json(s) for s in c["spans"]]
            grid[c["r"]][c["c"]] = (spans_to_md(sp, strip_bold=c["r"] < hr).replace("|", "\\|")
                                    if c["r"] < hr else c["md"])
        lines = ["| " + " | ".join(grid[0]) + " |", "|" + "|".join(["---"] * ncols) + "|"]
        lines += ["| " + " | ".join(r) + " |" for r in grid[1:]]
        return "\n".join(lines), "gfm"
    covered = set()
    by_pos = {(c["r"], c["c"]): c for c in cells}
    out = ["<table>"]
    for r in range(nrows):
        if r == 0:
            out.append("<thead>")
        if r == hr:
            out.append("</thead>")
            out.append("<tbody>")
        row = []
        for c in range(ncols):
            if (r, c) in covered:
                continue
            cell = by_pos.get((r, c))
            if not cell:
                row.append("<td></td>" if r >= hr else "<th></th>")
                continue
            for dr in range(cell["rs"]):
                for dc in range(cell["cs"]):
                    covered.add((r + dr, c + dc))
            tag = "th" if r < hr else "td"
            attrs = (f' rowspan="{cell["rs"]}"' if cell["rs"] > 1 else "") + (f' colspan="{cell["cs"]}"' if cell["cs"] > 1 else "")
            sp = [Span.from_json(s) for s in cell["spans"]]
            if tag == "th":
                sp = [Span(s.text, s.bits & ~BOLD) for s in sp]
            row.append(f"<{tag}{attrs}>{spans_to_html(sp)}</{tag}>")
        out.append("<tr>" + "".join(row) + "</tr>")
    out.append("</tbody>" if hr < nrows else "</thead>")
    out.append("</table>")
    return "\n".join(out), "html"


def merge_continued_tables(stream: list) -> list:
    # page-spanning continuation: table (last of page p) + table (first of page p+1)
    merged: list = []
    for i, el in enumerate(stream):
        if el[0] == "table" and merged:
            # find previous element that is a table with only noise between (none)
            prev = merged[-1]
            if prev[0] == "table" and prev[2] + 1 == el[2] and not prev[1]["complex"] and not el[1]["complex"] \
                    and prev[1]["ncols"] == el[1]["ncols"] and el[1]["bbox"][1] < 140 and not prev[1].get("cont_blocked"):
                a, b = prev[1], el[1]
                drop = 0
                if [c["raw"] for c in b["cells"] if c["r"] == 0] == [c["raw"] for c in a["cells"] if c["r"] == 0]:
                    drop = 1
                off = a["nrows"] - drop
                for c in b["cells"]:
                    if c["r"] < drop:
                        continue
                    c2 = dict(c)
                    c2["r"] = c["r"] + off
                    a["cells"].append(c2)
                a["nrows"] = a["nrows"] + b["nrows"] - drop
                a["raw_nums"] = a["raw_nums"] + b["raw_nums"]
                a.setdefault("pages", [prev[2]]).append(el[2])
                a["continued"] = True
                continue
        merged.append(el)
    return merged


# ------------------------------------------------------------------------------ formulas

def _sqrt_braces(s: str) -> str:
    out, i = "", 0
    while i < len(s):
        if s.startswith(r"\sqrt", i):
            j = i + 5
            if j < len(s) and s[j] == "(":
                depth, k = 0, j
                while k < len(s):
                    depth += (s[k] == "(") - (s[k] == ")")
                    if depth == 0:
                        break
                    k += 1
                out += r"\sqrt{" + _sqrt_braces(s[j + 1:k]) + "}"
                i = k + 1
                continue
        out += s[i]
        i += 1
    return out


def to_latex(spans: list[Span]) -> str:
    parts = []
    for sp in spans:
        t = re.sub(r"([A-Za-z]{2,}(?: [A-Za-z]{2,})*)", lambda m: "\x00" + m.group(1) + "\x01", sp.text)
        out, i = [], 0
        for seg in re.split("(\x00[^\x01]*\x01)", t):
            if seg.startswith("\x00"):
                out.append(r"\text{" + seg[1:-1] + "}")
                continue
            for k, v in SYMBOLS_LATEX.items():
                seg = seg.replace(k, v)
            out.append(seg.replace("%", r"\%").replace("&", r"\&").replace("#", r"\#"))
        t = "".join(out)
        if sp.bits & SUP:
            t = "^{" + t.strip() + "}"
        elif sp.bits & SUB:
            t = "_{" + t.strip() + "}"
        parts.append(t)
    return _sqrt_braces(re.sub(r"\s+", " ", "".join(parts))).strip()


def looks_like_formula(p: Para, page_w: float, body: float) -> bool:
    if len(p.lines) != 1 or len(p.text) > 140 or p.kind == "list":
        return False
    if abs(p.size - body) > 0.8 or "=" not in p.text:
        return False
    if not (re.search(r"[×÷√∑∫±≥≤≈]", p.text) or any(s.bits & (SUP | SUB) for s in p.spans)):
        return False
    b = p.first["bbox"]
    return abs((b[0] + b[2]) / 2 - page_w / 2) < 0.14 * page_w if p.first.get("col") == "F" else False


# ----------------------------------------------------------------------------- build

def para_md(p: Para, strip_bold=False) -> str:
    return escape_line_start(spans_to_md(p.spans, strip_bold=strip_bold))


def bold_label(md: str) -> str:
    """Wrap a plain 'Figure 2.1.' label in bold (formatting only)."""
    if md.startswith("**") or md.startswith("*"):
        return md
    m = LABEL_PREFIX.match(md)
    return f"**{m.group(1)}**{md[m.end():]}" if m else md


def label_of(text: str) -> str:
    m = XREF_LABEL.match(text.strip() + " ")
    if not m:
        return ""
    kind = m.group(1).lower().rstrip(".")
    kind = "Figure" if kind in ("fig", "figure") else kind.capitalize()
    return f"{kind} {m.group(2).rstrip('.')}"


def bbox_of(p: Para):
    out = []
    for pg in p.pages:
        bs = [l["bbox"] for l in p.lines if int(l["id"].split("-")[0][1:]) == pg]
        if bs:
            out.append({"page": pg, "bbox": [min(b[0] for b in bs), min(b[1] for b in bs),
                                              max(b[2] for b in bs), max(b[3] for b in bs)]})
    return out


def build_blocks(ctx: BuildCtx) -> dict:
    ch = ctx.chapter
    tag = ch["key"][3:]
    pages_by_no = {pg["page"]: pg for pg in ctx.pages}
    for pg in ctx.pages:
        for it in pg["items"]:
            if it["t"] == "line":
                t = it["raw"].rstrip()
                if t[-1:] in "-‐‑":
                    t = re.sub(r"\S+$", "", t)
                ctx.vocab.feed(t)
    cleanup = remove_running(ctx)
    stats = body_stats(ctx.pages)
    body = stats["body"]
    stream = merge_continued_tables(segment(ctx, stats))
    for el in stream:
        if el[0] == "para":
            finish_para(el[1], ctx.vocab)
    # --- heading candidates
    paras = [e[1] for e in stream if e[0] == "para"]
    join_stats = {"dehyphenated": sum(len(p.log.dehyphenated) for p in paras),
                  "kept": sum(len(p.log.kept_hyphen) for p in paras),
                  "uncertain": sum(len(p.log.uncertain) for p in paras)}
    for p in paras:
        p.h_conf = heading_conf(p, body)
    # --- chapter title (H1) on first page
    first_page = ctx.pages[0]["page"] if ctx.pages else 0
    title_ids: set[int] = set()
    title_text = None
    cands = [p for p in paras if p.pages[0] == first_page and p.size >= 1.35 * body and p.h_conf >= 0.8]
    if cands:
        mx = max(p.size for p in cands)
        group = [p for p in cands if abs(p.size - mx) < 1.0]
        # only a contiguous leading run counts
        idxs = [paras.index(p) for p in group]
        run = [group[0]]
        for a, b in zip(idxs, idxs[1:]):
            if b == a + 1:
                run.append(group[idxs.index(b)])
            else:
                break
        joined = " ".join(p.text for p in run)
        if match_title(joined, ch["title"]) >= 0.5 or len(run) == 1:
            title_ids = {id(p) for p in run}
            title_text = run[0]
            if len(run) > 1:
                run[0].spans = join_span_lines([p.spans for p in run], ctx.vocab)
                run[0].text = spans_plain(run[0].spans).strip()
                run[0].lines = [l for p in run for l in p.lines]
                title_ids = {id(run[0])}
                for p in run[1:]:
                    p.h_conf = -1.0
            if match_title(run[0].text, ch["title"]) < 0.5:
                ctx.issue("LOW", "TITLE_MISMATCH",
                          f"First-page title \"{run[0].text}\" differs from outline title \"{ch['title']}\"",
                          first_page)
    heads = [p for p in paras if p.h_conf >= 0.6 or id(p) in title_ids]
    assign_levels(heads, ctx.memory, title_ids, body)
    # --- AI-assisted resolution of ambiguous heading candidates (optional)
    ambiguous = [p for p in paras if 0.4 <= p.h_conf < 0.6 and id(p) not in title_ids]
    for p in ambiguous:
        verdict = ctx.ai_heading(p.text, paras, p) if ctx.ai_heading else None
        if verdict:
            p.h_conf, p.h_level = 0.7, verdict
        else:
            ctx.issue("MEDIUM", "HEADING_AMBIGUOUS",
                      f"Possible heading kept as text (confidence {p.h_conf:.2f}): \"{p.text[:80]}\"",
                      p.pages[0])

    # --- main classification walk
    seq: list = []          # ('block', Block) | ('caption', kind, Para) | ('li', Para)
    refs_mode, refs_level = False, 9
    fig_n = tbl_n = 0
    pending_li: list[Para] = []
    footnotes: list[tuple[int, Para]] = []

    def new_block(btype, **kw) -> Block:
        return Block(id="", type=btype, **kw)

    def flush_list():
        nonlocal pending_li
        if not pending_li:
            return
        seq.append(("block", make_list_block(pending_li)))
        pending_li = []

    def make_list_block(items: list[Para]) -> Block:
        xs = sorted({round(i.x0 / 4) * 4 for i in items})
        lvl_of = {x: n for n, x in enumerate(xs)}
        lines, stack = [], []
        prev_pg = None
        for it in items:
            lvl = lvl_of[round(it.x0 / 4) * 4]
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            indent = "".join(" " * w for _, w in stack)
            if it.marker and it.marker.strip(" ") and it.ordered:
                mk = re.sub(r"[()]", "", it.marker)
                mk = mk if mk.endswith(".") else mk[:-1] + "."
                width = len(mk) + 1
            elif it.marker and re.match(r"^\(?[a-h][.)]$", it.marker):
                mk, width = "-", 2
                it.spans = [Span(it.marker + " ", 0)] + it.spans
            else:
                mk, width = "-", 2
            stack.append((lvl, width))
            pm = ""
            if prev_pg is not None and it.pages[0] != prev_pg:
                pm = f"<!-- source_page: {it.pages[0]} --> "
            prev_pg = it.pages[-1]
            lines.append(f"{indent}{mk} {pm}{spans_to_md(it.spans)}")
        b = new_block("list", md="\n".join(lines),
                      raw="\n".join(l["raw"] for it in items for l in it.lines),
                      clean="\n".join(it.text for it in items),
                      pages=sorted({pg for it in items for pg in it.pages}),
                      bboxes=[bb for it in items for bb in bbox_of(it)], box=items[0].box,
                      conf=min(i.conf for i in items))
        return b

    ref_entries: list[Para] = []

    def flush_refs():
        nonlocal ref_entries
        for e in ref_entries:
            num = None
            m = re.match(r"^\s*\[?\(?(\d{1,4})[\].)]*", e.marker or "")
            if e.marker and m:
                num = m.group(1)
            txt = spans_to_md(e.spans)
            txt = re.sub(r"(?<![<\(\[\w/@])(https?://[^\s<>()\]]+?)(?=[.,;:]?(?:\s|$))", r"<\1>", txt)
            prefix = f'<a id="ref-{num}"></a>{e.marker} ' if num else ""
            b = new_block("reference", md=prefix + txt, raw="\n".join(l["raw"] for l in e.lines), clean=e.text,
                          pages=e.pages, bboxes=bbox_of(e), conf=e.conf)
            b.extra = {"ref_number": int(num) if num else None}
            seq.append(("block", b))
        ref_entries = []

    for el in stream:
        if el[0] != "para":
            flush_list()
            flush_refs()
            seq.append((el[0], el[1], el[2]))
            continue
        p: Para = el[1]
        if p.h_conf == -1.0:
            continue
        is_head = id(p) in title_ids or p.h_conf >= 0.6
        if is_head:
            flush_list()
            flush_refs()
            lvl = 1 if id(p) in title_ids else max(p.h_level, 2)
            hs = [Span(s.text, s.bits & ~BOLD) for s in p.spans]
            if hs and all(s.bits & ITALIC for s in hs if s.text.strip()):
                hs = [Span(s.text, s.bits & ~ITALIC) for s in hs]
            b = new_block("heading", level=lvl, md="#" * lvl + " " + spans_to_md(hs),
                          raw="\n".join(l["raw"] for l in p.lines), clean=p.text, pages=p.pages,
                          bboxes=bbox_of(p), conf=p.h_conf)
            refs_mode = bool(REF_HEADINGS.match(p.text.strip()))
            refs_level = lvl
            seq.append(("block", b))
            continue
        if is_caption_start(p.first, body) and not refs_mode:
            flush_list()
            kind = "table" if TABLE_CAPTION_RE.match(p.text) else "figure"
            seq.append(("caption", kind, p))
            continue
        if refs_mode:
            if p.kind == "list":
                ref_entries.append(p)
            elif ref_entries and not ends_sentence(ref_entries[-1].text) and not re.match(r"^\s*\[?\d", p.text):
                last = ref_entries[-1]
                last.spans = join_span_lines([last.spans, p.spans], ctx.vocab)
                last.text = spans_plain(last.spans).strip()
                last.lines += p.lines
                last.pages = sorted(set(last.pages + p.pages))
            else:
                ref_entries.append(p)
            continue
        if p.kind == "list":
            if pending_li and (pending_li[-1].box != p.box or (
                    pending_li[-1].ordered != p.ordered and abs(pending_li[-1].x0 - p.x0) < 4)):
                flush_list()
            pending_li.append(p)
            continue
        flush_list()
        pg = pages_by_no[p.pages[0]]
        if looks_like_formula(p, pg["w"], body):
            tex = to_latex(p.spans)
            b = new_block("formula", md=f"$$\n{tex}\n$$", raw=p.first["raw"], clean=p.text, pages=p.pages,
                          bboxes=bbox_of(p), conf=0.7)
            ctx.issue("LOW", "FORMULA_LATEX", f"Standalone equation converted to LaTeX; verify: {p.text[:70]}", p.pages[0])
            seq.append(("block", b))
            continue
        if p.size < body - 0.5 and p.first["bbox"][1] > 0.75 * pg["h"] and p.spans and \
                (p.spans[0].bits & SUP or FOOTNOTE_MARK.match(p.text[:1] or "")):
            footnotes.append((len(seq), p))
            seq.append(("footnote", p))
            continue
        md = para_md(p)
        b = new_block("paragraph", md=md, raw="\n".join(l["raw"] for l in p.lines), clean=p.text, pages=p.pages,
                      bboxes=bbox_of(p), conf=p.conf, box=p.box)
        if p.log.uncertain:
            b.flags.append("hyphen_uncertain")
            ctx.issue("LOW", "HYPHEN_UNCERTAIN", "Line-end hyphen kept as in source: " + ", ".join(p.log.uncertain[:5]),
                      p.pages[0])
        if p.first.get("ocr") and p.first.get("min_conf", 1) < 0.8 or p.conf < 0.8:
            b.flags.append("ocr_low_conf")
            b.md += f"\n<!-- UNCERTAIN: low OCR confidence, page {p.pages[0]} -->"
            sev = "HIGH" if re.search(r"\d", p.text) else "MEDIUM"
            ctx.issue(sev, "OCR_LOW_CONFIDENCE", f"OCR confidence {p.conf:.2f}: \"{p.text[:70]}\"", p.pages[0], cat="ocr")
        seq.append(("block", b))
    flush_list()
    flush_refs()

    # --- footnotes: attach to the marker in a preceding paragraph of the same page
    fn_blocks: dict[int, list[Block]] = {}
    for pos, p in footnotes:
        marker = p.spans[0].text.strip() if p.spans else ""
        marker = marker if marker else p.text[:1]
        body_txt = spans_to_md(p.spans[1:]) if p.spans[0].text.strip() == marker else p.text[len(marker):].strip()
        pat = f"<sup>{re.escape(marker)}</sup>" if not FOOTNOTE_MARK.match(marker) else re.escape(marker)
        target = None
        for i in range(pos - 1, -1, -1):
            e = seq[i]
            if e[0] == "block" and e[1].type == "paragraph" and p.pages[0] in e[1].pages and re.search(pat, e[1].md):
                target = (i, e[1])
                break
        fid = f"fn{p.pages[0]}-{re.sub(r'[^0-9A-Za-z]', '', marker) or 'x'}"
        if target:
            i, tb = target
            tb.md = re.sub(pat, f"[^{fid}]", tb.md, count=1)
            fb = new_block("footnote", md=f"[^{fid}]: {body_txt}", raw="\n".join(l["raw"] for l in p.lines),
                           clean=p.text, pages=p.pages, bboxes=bbox_of(p), conf=0.85)
            fn_blocks.setdefault(i, []).append(fb)
            seq[pos] = ("skip",)
        else:
            md = para_md(p)
            seq[pos] = ("block", new_block("paragraph", md=md, raw="\n".join(l["raw"] for l in p.lines), clean=p.text,
                                           pages=p.pages, bboxes=bbox_of(p), conf=p.conf))
            ctx.issue("LOW", "FOOTNOTE_UNLINKED", f"Footnote text kept in place; marker not found: {p.text[:60]}", p.pages[0])

    # --- captions ↔ figures/tables, then final block list
    out: list[Block] = []
    used_caps: set[int] = set()
    for i, e in enumerate(seq):
        if e[0] == "caption":
            continue
    def cap_near(i, kind, page, bbox):
        for j in (i - 1, i + 1):
            if 0 <= j < len(seq) and seq[j][0] == "caption" and seq[j][1] == kind and j not in used_caps:
                cp: Para = seq[j][2]
                if page in cp.pages:
                    cb = cp.first["bbox"]
                    d = min(abs(cb[1] - bbox[3]), abs(bbox[1] - cp.lines[-1]["bbox"][3]))
                    if d < 90:
                        used_caps.add(j)
                        return cp
        # any unused caption of that kind on the same page within reach (e.g. other column order)
        best, bd = None, 1e9
        for j, s in enumerate(seq):
            if s[0] == "caption" and s[1] == kind and j not in used_caps and page in s[2].pages:
                cb = s[2].first["bbox"]
                d = min(abs(cb[1] - bbox[3]), abs(bbox[1] - s[2].lines[-1]["bbox"][3]))
                hov = min(cb[2], bbox[2]) - max(cb[0], bbox[0])
                if hov > 0 and d < bd and d < 90:
                    best, bd, bj = s[2], d, j
        if best:
            used_caps.add(bj)
        return best

    asset_dir_rel = f"../assets/images/{ch['key']}"
    table_dir_rel = f"../tables/{ch['key']}"
    labels: dict[str, dict] = {}
    for i, e in enumerate(seq):
        if e[0] == "skip" or e[0] == "caption":
            continue
        if e[0] == "block":
            out.append(e[1])
            out.extend(fn_blocks.get(i, []))
        elif e[0] == "figure":
            it, page = e[1], e[2]
            if it["kind"] == "scanned_page":
                fig_n += 1
                fname = f"figure_{tag}_{fig_n:02d}{_ext(it['tmp'])}"
                b = new_block("unreadable", md=f"<!-- UNCERTAIN: source text unreadable, page {page} -->\n[Unreadable source text]\n\n"
                                               f"![Scanned page {page}]({asset_dir_rel}/{fname})",
                              raw="", clean="", pages=[page], bboxes=[{"page": page, "bbox": it["bbox"]}], conf=0.0)
                b.extra = {"asset_src": it["tmp"], "asset_name": fname, "scanned": True}
                ctx.issue("HIGH", "PAGE_UNREADABLE",
                          f"Page {page} has no native text and could not be OCR-read (OCR unavailable or failed); page image kept as figure.",
                          page, cat="ocr")
                out.append(b)
                continue
            fig_n += 1
            cap = cap_near(i, "figure", page, it["bbox"])
            fname = f"figure_{tag}_{fig_n:02d}{_ext(it['tmp'])}"
            lab = label_of(cap.text) if cap else ""
            cap_md = bold_label(spans_to_md(cap.spans)) if cap else ""
            alt = lab or f"Figure {fig_n}"
            parts = []
            if lab:
                aid = "figure-" + re.sub(r"[^0-9a-z]+", "-", lab.split(" ", 1)[1].lower()).strip("-")
                parts.append(f'<a id="{aid}"></a>')
                labels[lab] = {"anchor": aid, "type": "figure"}
            parts.append(f"![{alt}]({asset_dir_rel}/{fname})")
            if cap_md:
                parts.append("\n" + cap_md)
            if it["labels"]:
                lines = "\n".join(f"- {escape_line_start(spans_to_md([Span(t, 0)]))}" for t in it["labels"])
                parts.append("\n<details>\n<summary>Figure text / labels</summary>\n\n" + lines + "\n\n</details>")
            b = new_block("figure", md="\n".join(parts), raw=(cap.first["raw"] if cap else ""),
                          clean=(cap.text if cap else ""), pages=[page] + ([p for p in cap.pages if p != page] if cap else []),
                          bboxes=[{"page": page, "bbox": it["bbox"]}], conf=it.get("conf", 0.9))
            b.extra = {"asset_src": it["tmp"], "asset_name": fname, "label": lab, "caption": cap.text if cap else "",
                       "labels": it["labels"], "how": it["how"]}
            if not cap:
                ctx.issue("MEDIUM", "FIGURE_NO_CAPTION", f"Figure on page {page} has no associated caption.", page, cat="figures")
            out.append(b)
        elif e[0] == "table":
            it, page = e[1], e[2]
            tbl_n += 1
            cap = cap_near(i, "table", page, it["bbox"])
            md, kind = render_table(it)
            lab = label_of(cap.text) if cap else ""
            cap_md = bold_label(spans_to_md(cap.spans)) if cap else ""
            notes = []
            j = i + 1
            while j < len(seq) and seq[j][0] == "block" and seq[j][1].type == "paragraph" and \
                    seq[j][1].pages[0] == (it.get("pages") or [page])[-1] and seq[j][1].bboxes and \
                    seq[j][1].bboxes[0]["bbox"][1] - it["bbox"][3] < 40 and len(notes) < 4:
                nb = seq[j][1]
                # footnote-like: smaller than body text
                sz = next((pr.size for pr in paras if pr.text == nb.clean), body)
                if sz < body - 0.5:
                    notes.append(nb)
                    seq[j] = ("skip",)
                    j += 1
                else:
                    break
            big = it["nrows"] > 40 or it["nrows"] * it["ncols"] > 300
            aid = ""
            if lab:
                aid = "table-" + re.sub(r"[^0-9a-z]+", "-", lab.split(" ", 1)[1].lower()).strip("-")
                labels[lab] = {"anchor": aid, "type": "table"}
            anchor = f'<a id="{aid}"></a>\n' if aid else ""
            notes_md = "\n\n".join(n.md for n in notes)
            tfile = f"table_{tag}_{tbl_n:02d}.md"
            full = (anchor + (cap_md + "\n\n" if cap_md else "") + md + ("\n\n" + notes_md if notes_md else ""))
            if big:
                title = cap_md or f"Table {tbl_n}"
                bmd = f"{anchor}[{title}]({table_dir_rel}/{tfile})"
            else:
                bmd = full
            b = new_block("table", md=bmd, raw=(cap.first["raw"] if cap else ""), clean=" ".join(c["raw"] for c in it["cells"]),
                          pages=sorted(set([page] + it.get("pages", []) + (cap.pages if cap else []))),
                          bboxes=[{"page": page, "bbox": it["bbox"]}], conf=it["conf"])
            b.extra = {"label": lab, "kind": kind, "nrows": it["nrows"], "ncols": it["ncols"], "big": big,
                       "table_file": tfile if big else None, "table_text": full if big else None,
                       "caption": cap.text if cap else "",
                       "raw_nums": it["raw_nums"], "strategy": it["strategy"], "complex": it["complex"],
                       "header_inferred": it.get("header_inferred"), "empty_cells": sum(1 for c in it["cells"] if not c["md"]),
                       "cells": len(it["cells"]), "holes": it.get("holes", 0), "notes": [n.clean for n in notes]}
            out.append(b)
            for n in notes:
                b.pages = sorted(set(b.pages + n.pages))
        else:
            continue
    # orphan captions
    for j, s in enumerate(seq):
        if s[0] == "caption" and j not in used_caps:
            cp: Para = s[2]
            sev = "HIGH" if s[1] == "figure" else "MEDIUM"
            ctx.issue(sev, "CAPTION_ORPHAN", f"{s[1].capitalize()} caption without extracted {s[1]}: \"{cp.text[:70]}\"",
                      cp.pages[0], cat="figures" if s[1] == "figure" else "tables")
            b = new_block("caption", md=bold_label(spans_to_md(cp.spans)), raw="\n".join(l["raw"] for l in cp.lines),
                          clean=cp.text, pages=cp.pages, bboxes=bbox_of(cp), conf=0.5)
            pos = next((k for k, bb in enumerate(out) if bb.page and bb.page > cp.pages[0]), len(out))
            # keep reading order: insert before first block from a later page, after blocks of the same page
            same = [k for k, bb in enumerate(out) if bb.pages and cp.pages[0] in bb.pages]
            if same:
                ys = cp.first["bbox"][1]
                pos = same[-1] + 1
                for k in same:
                    if out[k].bboxes and out[k].bboxes[0]["page"] == cp.pages[0] and out[k].bboxes[0]["bbox"][1] > ys \
                            and out[k].bboxes[0]["bbox"][0] >= cp.first["bbox"][0] - 5:
                        pos = k
                        break
            out.insert(pos, b)
    # --- callout grouping by drawn box (+ title-based fallback)
    out = group_callouts(out, ctx, pages_by_no)
    for n, b in enumerate(out, 1):
        b.id = f"{ch['key']}-b{n:04d}"
    return {"blocks": out, "labels": labels, "removed": cleanup["removed"], "stats": stats, "join": join_stats,
            "title_para": title_text, "n_tables": tbl_n, "n_figures": fig_n}


def _ext(name: str) -> str:
    import os
    return os.path.splitext(name)[1] or ".png"


def group_callouts(blocks: list[Block], ctx: BuildCtx, pages_by_no) -> list[Block]:
    out: list[Block] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.box is not None and b.type in ("paragraph", "list"):
            j = i
            group = []
            while j < len(blocks) and blocks[j].box == b.box and blocks[j].type in ("paragraph", "list") \
                    and blocks[j].pages[0] == b.pages[0]:
                group.append(blocks[j])
                j += 1
            title = group[0].clean.strip()
            has_title = bool(CALLOUT_TITLES.match(title.rstrip(":"))) or (group[0].md.startswith("**") and len(title) < 40 and len(group) > 1)
            body_blocks = group[1:] if has_title else group
            lines = []
            if has_title:
                lines += [f"> **{re.sub(r'[*]', '', title).strip()}**", ">"]
            for k, g in enumerate(body_blocks):
                lines += ["> " + l if l else ">" for l in g.md.split("\n")]
                if k < len(body_blocks) - 1:
                    lines.append(">")
            cb = Block(id="", type="callout", md="\n".join(lines), raw="\n".join(g.raw for g in group),
                       clean="\n".join(g.clean for g in group), pages=group[0].pages,
                       bboxes=[bb for g in group for bb in g.bboxes], conf=min(g.conf for g in group))
            cb.extra = {"title": title if has_title else ""}
            out.append(cb)
            i = j
            continue
        if b.type == "paragraph" and CALLOUT_TITLES.match(b.clean.strip().rstrip(":")) and len(b.clean) < 40 and \
                (b.md.startswith("**") or b.clean.isupper()):
            j = i + 1
            group = []
            while j < len(blocks) and len(group) < 8 and blocks[j].type in ("paragraph", "list") and \
                    blocks[j].pages[0] == b.pages[0]:
                group.append(blocks[j])
                j += 1
            if group:
                lines = [f"> **{re.sub(r'[*]', '', b.clean).strip()}**", ">"]
                for k, g in enumerate(group):
                    lines += ["> " + l if l else ">" for l in g.md.split("\n")]
                    if k < len(group) - 1:
                        lines.append(">")
                cb = Block(id="", type="callout", md="\n".join(lines), raw="\n".join(g.raw for g in [b] + group),
                           clean="\n".join(g.clean for g in [b] + group), pages=b.pages,
                           bboxes=[bb for g in [b] + group for bb in g.bboxes], conf=0.6)
                cb.extra = {"title": b.clean, "inferred": True}
                ctx.issue("LOW", "CALLOUT_INFERRED", f"Callout \"{b.clean}\" extent inferred (no drawn box).", b.pages[0])
                out.append(cb)
                i = j
                continue
        out.append(b)
        i += 1
    return out
