"""Pass 11: Markdown assembly, stable section IDs, source map."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

from . import APP_VERSION, PARSER_VERSION, crossref
from .blocks import Block
from .numeric import strip_markdown
from .util import fingerprint, github_anchor_slugs, slugify

TOC_MAX = 40


def assign_sections(blocks: list[Block], book_id: str, chapter: dict) -> list[dict]:
    root = f"{book_id}-ch{chapter['key'][3:]}"
    used = {root}
    stack: list[tuple[int, str, str]] = []   # level, title, id
    sections = []
    cur_id = root
    for b in blocks:
        if b.type == "heading":
            while stack and stack[-1][0] >= b.level:
                stack.pop()
            title = re.sub(r"<[^>]+>|[*_\\]", "", b.clean or b.md).strip()
            if b.level == 1:
                sid = root
            else:
                slug = slugify(title)
                sid = f"{root}-{slug}"
                if sid in used and stack:
                    sid = f"{root}-{slugify(stack[-1][1])}-{slug}"
                n = 2
                base = sid
                while sid in used:
                    sid = f"{base}-{n}"
                    n += 1
            used.add(sid)
            stack.append((b.level, title, sid))
            cur_id = sid
            sections.append({"section_id": sid, "title": title, "level": b.level,
                             "path": [s[1] for s in stack]})
        b.section_id = cur_id
        b.heading_path = [s[1] for s in stack]
    return sections


def front_matter(book: dict, chapter: dict, source_pages: str, meta: dict) -> str:
    fm = {
        "book_title": book["title"],
        "edition": str(book.get("edition") or ""),
        "chapter_number": chapter.get("number"),
        "chapter_title": chapter["title"],
        "chapter_id": chapter["key"],
        "source_file": book.get("source_file_name", ""),
        "source_pages": source_pages,
        "language": "en",
        "content_type": "textbook_chapter",
        "specialty": meta.get("specialty", []),
        "tags": meta.get("tags", []),
        "source_sha256": book["source_sha256"],
        "conversion_version": f"{APP_VERSION}/{PARSER_VERSION}",
    }
    return "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=1000) + "---\n"


def default_meta(chapter: dict) -> dict:
    part = re.sub(r"^\s*part\s+[\dIVXLC]+[\s.:\-–—]*", "", chapter.get("part") or "", flags=re.I).strip()
    stop = {"and", "the", "of", "in", "for", "with", "from", "disease", "diseases", "disorders", "disorder", "children"}
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", chapter["title"])]
    tags = []
    for w in words:
        if w not in stop and w not in tags:
            tags.append(w)
    return {"specialty": [part.lower()] if part else [], "tags": tags[:6]}


def build_markdown(blocks: list[Block], book: dict, chapter: dict, meta: dict, source_pages: str,
                   resolver, toc: bool = True):
    """Return (markdown_text, source_map_entries, unresolved_refs, resolved_refs)."""
    sections = assign_sections(blocks, book["book_id"], chapter)
    unresolved, resolved = [], []
    # cross-reference linking on prose-like blocks only
    for b in blocks:
        if b.type in ("paragraph", "list", "callout", "footnote"):
            new, res, unres = crossref.link_text(b.md, resolver)
            b.md = new
            for r in res:
                resolved.append((b, r))
            for r in unres:
                unresolved.append((b, r))
    h1 = [i for i, b in enumerate(blocks) if b.type == "heading" and b.level == 1]
    heads = [(b.level, b.clean.strip()) for b in blocks if b.type == "heading"]
    toc_lines: list[str] = []
    h2 = [b for b in blocks if b.type == "heading" and b.level == 2]
    insert_after = h1[0] if h1 else -1
    has_toc = bool(toc and len(h2) >= 3)
    # GitHub-style anchors (the generated "Contents" heading takes part in duplicate numbering)
    titles = []
    for i, b in enumerate(blocks):
        titles.append(re.sub(r"<[^>]+>|[*_\\]", "", b.clean or b.md).strip() if b.type == "heading" else None)
        if i == insert_after and has_toc:
            titles.append("__contents__")
    named = [("Contents" if t == "__contents__" else t) for t in titles if t is not None]
    slugs = github_anchor_slugs(named)
    anchor_of, k = [], 0
    for t in titles:
        if t is None:
            continue
        anchor_of.append((t, slugs[k]))
        k += 1
    head_anchors = [a for t, a in anchor_of if t != "__contents__"]
    for sec, anc in zip(sections, head_anchors):
        sec["anchor"] = anc
    if has_toc:
        toc_lines = ["<!-- generated:toc -->", "## Contents", ""]
        count = 0
        hi = 0
        for b in blocks:
            if b.type != "heading":
                continue
            if b.level == 2 and count < TOC_MAX:
                toc_lines.append(f"- [{sections[hi]['title']}](#{sections[hi]['anchor']})")
                count += 1
            hi += 1
        toc_lines += ["<!-- /generated:toc -->"]
    # ---- compose
    lines: list[str] = []
    fm = front_matter(book, chapter, source_pages, meta).rstrip("\n").split("\n")
    lines += fm + [""]
    entries = []
    last_page = None
    for i, b in enumerate(blocks):
        pg0 = b.pages[0] if b.pages else None
        if pg0 is not None and pg0 != last_page:
            lines.append(f"<!-- source_page: {pg0} -->")
        start = len(lines) + 1
        md = b.md
        if b.type == "list" and len(set(b.pages)) > 1:
            md = md  # list items carry inline markers (added in block construction)
        lines.extend(md.split("\n"))
        if b.type == "heading":
            lines.append(f"<!-- section_id: {b.section_id} -->")
        end = len(lines)
        lines.append("")
        last_page = b.pages[-1] if b.pages else last_page
        stripped = strip_markdown(md)
        entries.append({
            "block_id": b.id, "type": b.type, "section_id": b.section_id, "heading_path": b.heading_path,
            "source_pages": b.pages, "bboxes": b.bboxes,
            "md_line_start": start, "md_line_end": end,
            "fingerprint": fingerprint(stripped), "confidence": round(b.conf, 3), "flags": b.flags,
        })
        if i == insert_after and toc_lines:
            lines.extend(toc_lines)
            lines.append("")
    text = "\n".join(lines).rstrip("\n") + "\n"
    return text, entries, sections, unresolved, resolved


def write_assets(blocks: list[Block], chapter: dict, root: Path, tmp_dir: Path) -> dict:
    """Copy extracted figures and write large tables. Returns asset inventory."""
    key = chapter["key"]
    imgs, tables = [], []
    for b in blocks:
        if b.type in ("figure", "unreadable") and b.extra.get("asset_src"):
            dst = root / "assets" / "images" / key / b.extra["asset_name"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tmp_dir / b.extra["asset_src"], dst)
            imgs.append(str(dst.relative_to(root)))
        if b.type == "table" and b.extra.get("big"):
            dst = root / "tables" / key / b.extra["table_file"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            body = b.extra["table_text"].replace(f"../assets/", "../../assets/")
            dst.write_text(body + "\n", encoding="utf-8")
            tables.append(str(dst.relative_to(root)))
    return {"images": imgs, "tables": tables}
