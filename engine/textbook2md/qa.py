"""Passes 12–14: source-fidelity, numeric and searchability QA (+ completeness/structure/tables/figures/citations)."""
from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter

from . import numeric
from .blocks import Block
from .models import Issue
from .textclean import PUA_RE, CID_RE
from .numeric import strip_markdown

LIG_RE = re.compile("[\ufb00-\ufb06]")
SPACED_RE = re.compile(r"(?<![A-Za-z])(?:[A-Za-z] ){5,}[A-Za-z](?![A-Za-z])")
CIT_BRACKET = re.compile(r"\[(\d{1,4}(?:\s*[–\-]\s*\d{1,4})?(?:\s*,\s*\d{1,4}(?:\s*[–\-]\s*\d{1,4})?)*)\]")


def _rect_overlap(a, b) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def expand_citations(s: str) -> set[int]:
    out = set()
    for part in re.split(r"\s*,\s*", s):
        m = re.match(r"(\d+)\s*[–\-]\s*(\d+)$", part)
        if m and 0 <= int(m.group(2)) - int(m.group(1)) <= 60:
            out.update(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.strip().isdigit():
            out.add(int(part))
    return out


def _alnum_counter(s: str) -> Counter:
    s = unicodedata.normalize("NFKC", s).lower()
    return Counter(c for c in s if c.isalnum())


def run_qa(chapter: dict, pages: list[dict], blocks: list[Block], md_text: str, table_files: dict,
           removed: list[dict], expected_pages: list[int], built_issues: list[Issue], stats: dict) -> tuple[list[Issue], dict]:
    issues: list[Issue] = []
    add = lambda sev, code, msg, page=None, block=None, cat="structure": issues.append(Issue(sev, code, msg, page, block, cat))
    metrics: dict = {}
    page_nos = {p["page"] for p in pages}

    # ---------- completeness
    for pno in expected_pages:
        if pno not in page_nos:
            add("CRITICAL", "PAGE_SKIPPED", f"Source page {pno} was not processed.", pno, cat="completeness")
    block_pages = {pg for b in blocks for pg in b.pages}
    char_counts = {}
    for p in pages:
        char_counts[p["page"]] = p.get("native_chars", 0)
        has_content = any(it["t"] != "line" or not it.get("zone") for it in p["items"])
        if p["page"] not in block_pages and has_content:
            # content existed on the page but nothing reached Markdown
            if not p.get("scanned"):
                add("CRITICAL", "PAGE_NOT_IN_MARKDOWN", f"Page {p['page']} has extracted items but none reached the Markdown.", p["page"], cat="completeness")
    native = [c for c in char_counts.values() if c > 0]
    if len(native) >= 4:
        med = statistics.median(native)
        for pno, c in char_counts.items():
            if 0 < c < 0.2 * med and pno not in (expected_pages[0], expected_pages[-1]):
                add("MEDIUM", "PAGE_TEXT_SHORT", f"Page {pno} has unexpectedly little text ({c} chars vs median {int(med)}).", pno, cat="completeness")
    for p in pages:
        if p.get("native_chars", 0) == 0 and not p["items"] and not p.get("scanned"):
            add("LOW", "PAGE_BLANK", f"Page {p['page']} has no extractable content (blank?).", p["page"], cat="completeness")
        for f in p.get("flags", []):
            if f == "encoding_artifact":
                add("MEDIUM", "ENCODING_ARTIFACT", f"Page {p['page']} contains private-use/(cid:)/replacement characters (broken font map?).", p["page"], cat="searchability")
            elif f.startswith("ocr_failed"):
                add("HIGH", "OCR_FAILED", f"OCR failed on page {p['page']}: {f.split(':', 1)[1]}", p["page"], cat="ocr")
    # character-level loss (independent of reading order)
    src_chars = Counter()
    for p in pages:
        for it in p["items"]:
            if it["t"] == "line" and not any(r["page"] == p["page"] and _rect_overlap(r["bbox"], it["bbox"]) for r in removed):
                src_chars.update(_alnum_counter(it["raw"]))
            elif it["t"] == "table":
                for c in it["cells"]:
                    src_chars.update(_alnum_counter(c["raw"]))
            elif it["t"] == "figure":
                for l in it.get("labels", []):
                    src_chars.update(_alnum_counter(l))
    md_all = strip_markdown(md_text) + "\n".join(strip_markdown(t) for t in table_files.values())
    md_chars = _alnum_counter(md_all)
    lost = sum((src_chars - md_chars).values())
    total = sum(src_chars.values()) or 1
    metrics["char_loss_ratio"] = round(lost / total, 5)
    if lost / total > 0.01:
        add("HIGH", "TEXT_LOSS", f"{lost / total:.2%} of source characters are missing from the Markdown.", cat="completeness")
    elif lost / total > 0.003:
        add("MEDIUM", "TEXT_LOSS_MINOR", f"{lost / total:.2%} of source characters are missing from the Markdown.", cat="completeness")

    # ---------- structure
    heads = [b for b in blocks if b.type == "heading"]
    if not heads:
        add("MEDIUM", "NO_HEADINGS", "No headings were detected in this chapter.", cat="structure")
    elif sum(1 for h in heads if h.level == 1) != 1:
        add("MEDIUM", "H1_COUNT", f"Expected exactly one H1, found {sum(1 for h in heads if h.level == 1)}.", cat="structure")
    prev = 0
    for h in heads:
        if prev and h.level > prev + 1:
            add("MEDIUM", "HEADING_JUMP", f"Heading level jumps from H{prev} to H{h.level} at \"{h.clean[:60]}\".", h.pages[0], h.id)
        prev = h.level
    for a, b in zip(blocks, blocks[1:]):
        if a.type == "heading" and b.type == "heading" and b.level <= a.level and a.level > 1:
            add("LOW", "EMPTY_SECTION", f"Section \"{a.clean[:50]}\" has no content before the next heading.", a.pages[0], a.id)

    # ---------- tables
    n_tables_src = sum(1 for p in pages for it in p["items"] if it["t"] == "table")
    tbl_blocks = [b for b in blocks if b.type == "table"]
    metrics["tables"] = len(tbl_blocks)
    for b in tbl_blocks:
        ex = b.extra
        text = ex["table_text"] if ex.get("big") else b.md
        toks = numeric.tokens(strip_markdown(text))
        # table numbers in caption/notes are in the same text; compare multisets
        src = Counter(ex["raw_nums"])
        have = Counter(toks)
        # caption numbers (e.g. 'TABLE 2.1') are not part of raw_nums; remove them from the md side
        for t in numeric.tokens(ex.get("caption", "")):
            if have.get(t, 0) > src.get(t, 0):
                have[t] -= 1
        for t in numeric.tokens(" ".join(ex.get("notes", []))):
            if have.get(t, 0) > src.get(t, 0):
                have[t] -= 1
        miss, extra = src - have, have - src
        if miss or extra:
            add("HIGH", "TABLE_NUMERIC_MISMATCH",
                f"Table on page {b.page}: numeric values differ from source (missing {sorted(miss)[:6]}, unexpected {sorted(extra)[:6]}).",
                b.page, b.id, "tables")
        if b.conf < 0.7:
            add("HIGH", "TABLE_LOW_CONFIDENCE", f"Table on page {b.page} was reconstructed with low confidence ({b.conf:.2f}, {ex['strategy']} strategy) — REVIEW REQUIRED.", b.page, b.id, "tables")
        if ex["cells"] and ex["empty_cells"] / ex["cells"] > 0.4:
            add("MEDIUM", "TABLE_SPARSE", f"Table on page {b.page} has {ex['empty_cells']}/{ex['cells']} empty cells.", b.page, b.id, "tables")
        if ex.get("holes"):
            add("MEDIUM", "TABLE_IRREGULAR", f"Table on page {b.page} has irregular cell geometry (rendered as HTML).", b.page, b.id, "tables")
        if ex.get("header_inferred"):
            add("LOW", "TABLE_HEADER_INFERRED", f"Table on page {b.page}: header row inferred (first row used).", b.page, b.id, "tables")
        if ex["kind"] == "html":
            add("LOW", "TABLE_HTML", f"Table on page {b.page} has merged cells/multi-row header; embedded as HTML.", b.page, b.id, "tables")
    if n_tables_src and not tbl_blocks:
        add("HIGH", "TABLE_COUNT", f"{n_tables_src} table(s) detected on the pages but none reached the Markdown.", cat="tables")

    # ---------- figures
    expected_figs = sum(1 for p in pages for it in p["items"] if it["t"] == "figure")
    fig_blocks = [b for b in blocks if b.type in ("figure", "unreadable")]
    metrics["figures"] = len(fig_blocks)
    if expected_figs != len(fig_blocks):
        add("HIGH", "FIGURE_COUNT", f"Figure count mismatch: {expected_figs} detected on pages vs {len(fig_blocks)} in Markdown.", cat="figures")
    cap_labels = {b.extra.get("label") for b in fig_blocks if b.extra.get("label")}
    mentioned = set(re.findall(r"\b(?:Figure|Fig\.)\s+(\d+\.\d+[A-Za-z]?)", strip_markdown("\n".join(b.md for b in blocks if b.type in ("paragraph", "list", "callout")))))
    num = chapter.get("number")
    for m in sorted(mentioned):
        if num is not None and m.split(".")[0] == str(num) and f"Figure {m}" not in cap_labels:
            add("MEDIUM", "FIGURE_MENTION_MISSING", f"Text refers to Figure {m} but no figure with that caption was extracted.", cat="figures")
    # ---------- citations
    ref_nums = {b.extra.get("ref_number") for b in blocks if b.type == "reference" and b.extra.get("ref_number")}
    metrics["references"] = len([b for b in blocks if b.type == "reference"])
    cited: set[int] = set()
    for b in blocks:
        if b.type in ("paragraph", "list", "callout", "footnote"):
            for m in CIT_BRACKET.finditer(strip_markdown(b.md)):
                cited |= expand_citations(m.group(1))
            for m in re.finditer(r"<sup>([\d,\s–\-]+)</sup>", b.md):
                cited |= expand_citations(m.group(1).strip(", "))
    if cited and not ref_nums:
        add("MEDIUM", "CITATIONS_NO_REFERENCES", f"{len(cited)} in-text citation numbers found but no numbered reference list was detected.", cat="citations")
    elif ref_nums:
        bad = sorted(c for c in cited if c not in ref_nums)
        if bad:
            add("MEDIUM", "CITATION_NOT_IN_LIST", f"In-text citations without a matching reference entry: {bad[:15]}.", cat="citations")
        if ref_nums and max(ref_nums) - min(ref_nums) + 1 != len(ref_nums):
            gaps = sorted(set(range(min(ref_nums), max(ref_nums) + 1)) - ref_nums)
            add("LOW", "REFERENCE_GAPS", f"Reference numbering has gaps: {gaps[:15]}.", cat="citations")
    metrics["citations_cited"] = len(cited)

    # ---------- numeric fingerprint (source words vs Markdown)
    src_tokens = []
    for p in pages:
        for x0, y0, x1, y1, tok, ctx in p.get("nums", []):
            bb = [x0, y0, x1, y1]
            if any(r["page"] == p["page"] and _rect_overlap(r["bbox"], bb) for r in removed):
                continue
            src_tokens.append((tok, p["page"], ctx))
    md_tokens = []
    for b in blocks:
        txt = table_files.get(b.id) if b.extra.get("big") else b.md
        for t in numeric.tokens(strip_markdown(txt or b.md)):
            md_tokens.append((t, b.page))
    nissues = numeric.compare(src_tokens, md_tokens)
    metrics["numeric_tokens_source"] = len(src_tokens)
    metrics["numeric_tokens_markdown"] = len(md_tokens)
    metrics["numeric_issues"] = [vars(i) for i in nissues]
    for ni in nissues:
        label = {"MISSING_NUMBER": "MISSING NUMBER", "CHANGED_NUMBER": "CHANGED NUMBER", "UNEXPECTED_NUMBER": "UNEXPECTED NUMBER"}[ni.kind]
        detail = f"{ni.source!r}→{ni.markdown!r}" if ni.kind == "CHANGED_NUMBER" else repr(ni.source or ni.markdown)
        add(ni.severity, ni.kind, f"{label}: {detail}" + (f" (near “{ni.context}”)" if ni.context else ""), ni.page, cat="numeric")

    # ---------- searchability
    if LIG_RE.search(md_text):
        add("MEDIUM", "LIGATURE_LEFT", "Un-normalised ligature characters remain in the Markdown.", cat="searchability")
    if PUA_RE.search(md_text) or CID_RE.search(md_text) or "\ufffd" in md_text:
        add("MEDIUM", "ENCODING_IN_MD", "Private-use / (cid:) / replacement characters remain in the Markdown.", cat="searchability")
    for m in SPACED_RE.finditer(strip_markdown(md_text)):
        add("LOW", "SPACED_LETTERS", f"Letter-spaced text (possible extraction artefact): “{m.group(0)[:30]}”.", cat="searchability")
        break
    if re.search(r"[a-z]- [a-z]{2,}", strip_markdown(md_text)):
        add("LOW", "HYPHEN_SPACE", "Possible un-joined line-end hyphenation (\"x- y\") remains.", cat="searchability")
    return built_issues + issues, metrics
