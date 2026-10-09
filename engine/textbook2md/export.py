"""Exports: project ZIP (portable), chapter export, RAG package, optional Obsidian flavour."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from .project import Project, ProjectError

EXCLUDE_DIRS = {".t2md", "__pycache__"}


def to_obsidian(md: str) -> str:
    """Optional flavour: callouts and wikilinks. Canonical Markdown is untouched."""
    out = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        m = re.match(r"^> \*\*(.+?)\*\*\s*$", lines[i])
        if m and (i == 0 or not lines[i - 1].startswith(">")):
            out.append(f"> [!note] {m.group(1)}")
            i += 1
            if i < len(lines) and lines[i].strip() == ">":
                i += 1
            continue
        out.append(lines[i])
        i += 1
    text = "\n".join(out)
    text = re.sub(r"\[([^\]]+)\]\(\./([^)#\s]+)\.md(?:#[^)]*)?\)", lambda m: f"[[{m.group(2)}|{m.group(1)}]]", text)
    return text


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and not (set(p.relative_to(root).parts) & EXCLUDE_DIRS) and not p.name.startswith(".tmp_"):
            yield p


def export_zip(project: Project, dest: str | Path, include_pdf: bool = False, obsidian: bool = False) -> str:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    top = project.root.name
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in _iter_files(project.root):
            rel = p.relative_to(project.root)
            if obsidian and rel.parts[0] == "chapters" and p.suffix == ".md":
                z.writestr(f"{top}/{rel.as_posix()}", to_obsidian(p.read_text(encoding="utf-8")))
            else:
                z.write(p, f"{top}/{rel.as_posix()}")
        if include_pdf and project.pdf_path().exists():
            z.write(project.pdf_path(), f"{top}/source/{project.pdf_path().name}")
    return str(dest)


def export_chapter(project: Project, key: str, dest: str | Path) -> str:
    c = project.chapter(key)
    md = project.chapter_md_path(c)
    if not md.exists():
        raise ProjectError(f"Chapter {key} has not been converted yet.")
    root = project.root
    files = [md, root / "source_maps" / f"{key}.json", root / "reports" / f"{key}_conversion_report.md"]
    for sub in ("assets/images", "tables"):
        d = root / sub / key
        if d.exists():
            files += [p for p in d.rglob("*") if p.is_file()]
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            if p.exists():
                z.write(p, p.relative_to(root).as_posix())
    return str(dest)


def export_rag(project: Project, dest: str | Path) -> str:
    root = project.root
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in ("rag/chunks.jsonl", "manifest.json", "book.yaml"):
            if (root / rel).exists():
                z.write(root / rel, rel)
        for p in sorted((root / "source_maps").glob("*.json")):
            z.write(p, f"source_maps/{p.name}")
        z.writestr("RAG_README.md",
                   "# RAG package\n\n`rag/chunks.jsonl` — one JSON object per chunk (verbatim Markdown of a section; never summarised).\n"
                   "Fields: id, book, edition, chapter, chapter_title, section_id, heading_path, source_pages, text, content_type, references.\n"
                   "`source_maps/` maps every block to its PDF page and bounding box for citation.\n")
    return str(dest)


def check_links(root: str | Path) -> list[str]:
    """Return broken relative links/images found in the project's Markdown files."""
    root = Path(root)
    broken = []
    for md in root.rglob("*.md"):
        if ".t2md" in md.parts:
            continue
        text = re.sub(r"`[^`\n]*`", "", md.read_text(encoding="utf-8"))
        for m in re.finditer(r"!?\[[^\]]*\]\(([^)\s]+)\)", text):
            tgt = m.group(1)
            if re.match(r"^[a-z]+:", tgt) or tgt.startswith("#"):
                continue
            path = tgt.split("#")[0]
            if path and not (md.parent / path).resolve().exists():
                broken.append(f"{md.relative_to(root)} → {tgt}")
    return broken
