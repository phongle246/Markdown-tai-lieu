from __future__ import annotations

from .models import SEV_RANK, Issue, report_status_label

CATS = {
    "Structure": ("structure",), "Tables": ("tables",), "Figures": ("figures",), "OCR": ("ocr",),
    "Numeric QA": ("numeric",), "Citation QA": ("citations",), "Completeness": ("completeness",),
    "Searchability": ("searchability",), "Possible Source Typos": ("typo",),
}


def _fmt(i: dict) -> str:
    where = f" (p. {i['page']})" if i.get("page") else ""
    return f"- **{i['severity']}** `{i['code']}`{where}: {i['message']}"


def render(chapter: dict, book: dict, status: str, issues: list[dict], metrics: dict, extra: dict) -> str:
    by = {k: [i for i in issues if i["category"] in v] for k, v in CATS.items()}
    sev_count = {s: sum(1 for i in issues if i["severity"] == s) for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    out = [f"# Conversion Report — {chapter['key']}: {chapter['title']}", "", "## Status", "",
           f"**{report_status_label(status)}**", "",
           "Issues: " + ", ".join(f"{k} {v}" for k, v in sev_count.items()), "",
           "## Source", "",
           f"- Book: {book['title']} (edition {book.get('edition') or 'n/a'})",
           f"- Source file: `{book.get('source_file_name')}`",
           f"- Source SHA-256: `{book['source_sha256']}`",
           f"- Chapter pages: {chapter['start']}–{chapter['end']}",
           f"- Chapter detection: {chapter.get('source')} (confidence {chapter.get('confidence')})",
           f"- Converted with: {extra.get('version')}", "",
           "## Structure", "",
           f"- Blocks: {extra.get('blocks')}  · Headings: {extra.get('headings')}  · Sections: {extra.get('sections')}",
           f"- Running headers/footers removed: {extra.get('removed')}",
           f"- Line-end hyphenation joined: {extra.get('dehyphenated')} (kept hyphen: {extra.get('kept_hyphen')})"]
    out += [_fmt(i) for i in by["Structure"]] or ["- No structural issues."]
    out += ["", "## Pages Processed", "",
            f"- {extra.get('pages_processed')} of {extra.get('pages_expected')} pages processed "
            f"({chapter['start']}–{chapter['end']}); scanned/OCR pages: {extra.get('scanned_pages')}",
            f"- Character loss vs. source: {metrics.get('char_loss_ratio', 0):.3%}"]
    out += [_fmt(i) for i in by["Completeness"]]
    out += ["", "## Tables", "", f"- Tables in Markdown: {metrics.get('tables', 0)}"]
    out += [_fmt(i) for i in by["Tables"]] or ["- No table issues."]
    out += ["", "## Figures", "", f"- Figures in Markdown: {metrics.get('figures', 0)}"]
    out += [_fmt(i) for i in by["Figures"]] or ["- No figure issues."]
    out += ["", "## OCR", "", f"- OCR pages: {extra.get('ocr_pages', 0)}"]
    out += [_fmt(i) for i in by["OCR"]] or ["- OCR not used." if not extra.get("ocr_pages") else "- No OCR issues."]
    out += ["", "## Numeric QA", "",
            f"- Numeric tokens in source: {metrics.get('numeric_tokens_source', 0)} · in Markdown: {metrics.get('numeric_tokens_markdown', 0)}"]
    out += [_fmt(i) for i in by["Numeric QA"]] or ["- All numeric tokens match the source."]
    out += ["", "## Citation QA", "",
            f"- References detected: {metrics.get('references', 0)} · distinct in-text citation numbers: {metrics.get('citations_cited', 0)}"]
    out += [_fmt(i) for i in by["Citation QA"]] or ["- Citation markers and reference list are consistent."]
    unc = [i for i in issues if i["code"] in ("PAGE_UNREADABLE", "OCR_LOW_CONFIDENCE", "HYPHEN_UNCERTAIN", "HEADING_AMBIGUOUS")]
    out += ["", "## Uncertain Text", ""] + ([_fmt(i) for i in unc] or ["- None."])
    out += ["", "## Possible Source Typos", ""] + ([_fmt(i) for i in by["Possible Source Typos"]] or ["- None detected. (Source text is never corrected.)"])
    out += [""] + [_fmt(i) for i in by["Searchability"]]
    unres = extra.get("unresolved", [])
    out += ["", "## Unresolved Cross References", ""] + (
        [f"- {u['text']} (p. {u.get('page')})" for u in unres[:60]] + ([f"- … {len(unres) - 60} more"] if len(unres) > 60 else [])
        if unres else ["- None."])
    rec = [i for i in issues if SEV_RANK[i["severity"]] <= 1]
    out += ["", "## Human Review Recommended", ""]
    if rec:
        pages = sorted({i["page"] for i in rec if i.get("page")})
        out += [f"Review in side-by-side mode. Pages to check: {', '.join(map(str, pages)) or 'n/a'}.", ""] + [_fmt(i) for i in rec]
    else:
        out += ["- No CRITICAL/HIGH issues. Spot-check recommended for numeric-dense tables."]
    return "\n".join(out) + "\n"
