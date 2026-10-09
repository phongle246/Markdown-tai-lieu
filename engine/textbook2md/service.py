"""High-level operations shared by the CLI and the HTTP API."""
from __future__ import annotations

from pathlib import Path

from . import chapters as chmod
from .project import Project, ProjectError, open_pdf
from .util import slugify


def inspect_pdf(pdf_path: str | Path) -> dict:
    doc = open_pdf(pdf_path)
    try:
        meta = chmod.detect_metadata(doc, pdf_path)
        chs = chmod.detect_chapters(doc)
        warnings = []
        if not meta["has_outline"]:
            warnings.append("The PDF has no outline/bookmarks; chapters were detected from the TOC/layout (lower confidence).")
        low = [c for c in chs if c.confidence < 0.7 or c.issues]
        if low:
            warnings.append(f"{len(low)} chapter(s) need review (low confidence or conflicting boundaries).")
        return {"book": meta, "chapters": [c.to_dict() for c in chs], "warnings": warnings}
    finally:
        doc.close()


def create_project(pdf_path: str | Path, out_dir: str | Path, overrides: dict | None = None,
                   chapters: list[dict] | None = None) -> Project:
    info = inspect_pdf(pdf_path)
    book = info["book"]
    book.update({k: v for k, v in (overrides or {}).items() if v not in (None, "")})
    chs = [chmod.Chapter.from_dict(c) for c in (chapters or info["chapters"])]
    chmod.assign_keys(chs)
    root = Path(out_dir)
    if root.exists() and (root / "book.yaml").exists():
        raise ProjectError(f"A project already exists in {root}")
    p = Project.create(pdf_path, root, book, chs)
    try:  # cover thumbnail for the library view
        import pymupdf
        doc = open_pdf(pdf_path)
        doc[0].get_pixmap(dpi=40).save(str(p.hidden / "cover.png"))
        doc.close()
    except Exception:
        pass
    return p


def default_project_dir(base: str | Path, book: dict) -> Path:
    return Path(base) / (slugify(f"{book['title']} {book.get('edition') or ''}") or "book")
