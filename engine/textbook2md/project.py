"""Project model: one textbook = one portable folder. App state lives in .t2md/ and is rebuildable."""
from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path

import pymupdf
import yaml

from . import APP_VERSION, PARSER_VERSION
from .chapters import Chapter, assign_keys
from .models import NOT_STARTED, PROCESSING
from .util import atomic_write_json, atomic_write_text, now_iso, read_json, sha256_file, slugify

SUBDIRS = ["chapters", "assets/images", "tables", "source_maps", "indexes", "rag", "reports", "metadata"]
_lock = threading.RLock()


class ProjectError(Exception):
    """Actionable, user-presentable error."""


def open_pdf(path: str | Path) -> pymupdf.Document:
    p = Path(path)
    if not p.exists():
        raise ProjectError(f"Source PDF not found: {p}. Re-link the PDF in Book Overview → Source.")
    try:
        doc = pymupdf.open(str(p))
    except Exception as e:
        raise ProjectError(f"Cannot open PDF (corrupt or unsupported): {e}") from e
    if doc.needs_pass or doc.is_encrypted:
        doc.close()
        raise ProjectError("The PDF is encrypted/password-protected. Remove the password (e.g. print to a new PDF) and try again.")
    if doc.page_count == 0:
        raise ProjectError("The PDF has no pages.")
    return doc


class Project:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.hidden = self.root / ".t2md"
        if not (self.root / "book.yaml").exists():
            raise ProjectError(f"Not a textbook2md project (missing book.yaml): {self.root}")
        self.book: dict = {}
        self.chapters: list[Chapter] = []
        self.load()

    # ------------------------------------------------------------------ create
    @classmethod
    def create(cls, pdf_path: str | Path, root: str | Path, book: dict, chapters: list[Chapter]) -> "Project":
        pdf_path, root = Path(pdf_path).resolve(), Path(root).resolve()
        if (root / "book.yaml").exists():
            raise ProjectError(f"A project already exists in {root}")
        root.mkdir(parents=True, exist_ok=True)
        for d in SUBDIRS + [".t2md/pages", ".t2md/blocks", ".t2md/issues", ".t2md/labels"]:
            (root / d).mkdir(parents=True, exist_ok=True)
        data = dict(book)
        data.update({
            "source_file": str(pdf_path), "source_file_name": pdf_path.name, "source_sha256": sha256_file(pdf_path),
            "created": now_iso(), "app_version": APP_VERSION,
        })
        data["chapters"] = [c.to_dict() for c in chapters]
        atomic_write_text(root / "book.yaml", yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000))
        p = cls(root)
        p.write_manifest()
        return p

    # -------------------------------------------------------------------- io
    def load(self) -> None:
        data = yaml.safe_load((self.root / "book.yaml").read_text(encoding="utf-8")) or {}
        self.chapters = [Chapter.from_dict(c) for c in data.pop("chapters", [])]
        self.book = data
        self.hidden.mkdir(exist_ok=True)
        for d in ("pages", "blocks", "issues", "labels"):
            (self.hidden / d).mkdir(exist_ok=True)

    def save_book(self) -> None:
        with _lock:
            data = dict(self.book)
            data["chapters"] = [c.to_dict() for c in self.chapters]
            atomic_write_text(self.root / "book.yaml", yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000))

    def set_chapters(self, chapters: list[Chapter]) -> None:
        """Manual correction of detected chapters. Invalidates state of changed chapters."""
        assign_keys(chapters)
        old = {c.key: c for c in self.chapters}
        for c in chapters:
            o = old.get(c.key)
            if o and (o.start, o.end, o.start_y, o.end_y) != (c.start, c.end, c.start_y, c.end_y):
                self.reset_chapter_state(c.key)
            if c.source != "manual" and (o is None or (o.start, o.end, o.title) != (c.start, c.end, c.title)):
                c.source = "manual"
                c.confidence = 1.0
        self.chapters = chapters
        self.save_book()
        mem = self.memory()
        mem.setdefault("previous_fixes", []).append({"at": now_iso(), "action": "chapters edited manually",
                                                     "count": len(chapters)})
        self.save_memory(mem)
        self.write_manifest()

    def chapter(self, key: str) -> Chapter:
        for c in self.chapters:
            if c.key == key:
                return c
        raise ProjectError(f"Unknown chapter: {key}")

    def chapter_filename(self, c: Chapter) -> str:
        return f"{c.key}_{slugify(c.title, 50)}.md"

    def chapter_md_path(self, c: Chapter) -> Path:
        return self.root / "chapters" / self.chapter_filename(c)

    def pdf_path(self) -> Path:
        return Path(self.book["source_file"])

    def verify_source(self) -> None:
        p = self.pdf_path()
        if not p.exists():
            raise ProjectError(f"Source PDF not found at {p}. Use “Re-link PDF” to point to the original file.")
        if sha256_file(p) != self.book["source_sha256"]:
            raise ProjectError("The PDF on disk differs from the one this project was created from (SHA-256 mismatch). "
                               "Re-link the original PDF or create a new project.")

    def relink_pdf(self, new_path: str | Path) -> None:
        new_path = Path(new_path)
        if sha256_file(new_path) != self.book["source_sha256"]:
            raise ProjectError("That PDF does not match the project's source SHA-256.")
        self.book["source_file"] = str(new_path.resolve())
        self.save_book()

    # ------------------------------------------------------------------ state
    @property
    def state_path(self) -> Path:
        return self.hidden / "state.json"

    def state(self) -> dict:
        return read_json(self.state_path, {"chapters": {}})

    def chapter_state(self, key: str) -> dict:
        return self.state().get("chapters", {}).get(key, {"status": NOT_STARTED})

    def update_chapter_state(self, key: str, **kw) -> dict:
        with _lock:
            st = self.state()
            cs = st.setdefault("chapters", {}).setdefault(key, {"status": NOT_STARTED})
            cs.update(kw)
            cs["updated"] = now_iso()
            atomic_write_json(self.state_path, st)
            return cs

    def reset_chapter_state(self, key: str) -> None:
        with _lock:
            st = self.state()
            st.get("chapters", {}).pop(key, None)
            atomic_write_json(self.state_path, st)
        shutil.rmtree(self.hidden / "pages" / key, ignore_errors=True)

    # ----------------------------------------------------------------- memory
    def memory(self) -> dict:
        return read_json(self.root / "metadata" / "conversion_memory.json", {
            "chapter_naming": {"pattern": "ch_{number:03d}_{slug}.md"},
            "heading_styles": {}, "table_rules": {"strategy": "lines", "fallback": "text-below-caption"},
            "figure_naming": {"pattern": "figure_{chapter}_{nn}.{ext}"},
            "layout": {}, "running_patterns": [], "repeated_xrefs": [], "abbreviations": [], "previous_fixes": []})

    def save_memory(self, mem: dict) -> None:
        atomic_write_json(self.root / "metadata" / "conversion_memory.json", mem)

    # --------------------------------------------------------------- manifest
    def write_manifest(self) -> dict:
        st = self.state().get("chapters", {})
        chs = []
        for c in self.chapters:
            s = st.get(c.key, {"status": NOT_STARTED})
            md = self.chapter_md_path(c)
            ent = {"key": c.key, "number": c.number, "title": c.title, "source_pages": [c.start, c.end],
                   "status": s.get("status", NOT_STARTED), "markdown": f"chapters/{md.name}" if md.exists() else None}
            if md.exists():
                ent["markdown_sha256"] = sha256_file(md)
                ent["source_map"] = f"source_maps/{c.key}.json"
                ent["report"] = f"reports/{c.key}_conversion_report.md"
                ent["assets"] = sorted(str(p.relative_to(self.root)) for p in (self.root / "assets" / "images" / c.key).glob("*")) \
                    if (self.root / "assets" / "images" / c.key).exists() else []
                ent["tables"] = sorted(str(p.relative_to(self.root)) for p in (self.root / "tables" / c.key).glob("*")) \
                    if (self.root / "tables" / c.key).exists() else []
                ent["converted_at"] = s.get("finished")
            chs.append(ent)
        m = {"project": {k: self.book.get(k) for k in ("title", "edition", "authors", "book_id", "pages")},
             "source": {"file": self.book.get("source_file_name"), "sha256": self.book.get("source_sha256")},
             "software": {"app_version": APP_VERSION, "parser_version": PARSER_VERSION},
             "created": self.book.get("created"), "updated": now_iso(), "chapters": chs}
        atomic_write_json(self.root / "manifest.json", m)
        return m

    # -------------------------------------------------------------- summaries
    def overview(self) -> dict:
        st = self.state().get("chapters", {})
        chs = []
        for c in self.chapters:
            s = st.get(c.key, {"status": NOT_STARTED})
            d = c.to_dict()
            d["state"] = s
            d["interrupted"] = s.get("status") == PROCESSING
            d["has_markdown"] = self.chapter_md_path(c).exists()
            chs.append(d)
        done = sum(1 for c in chs if c["state"].get("status") in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "REVIEW_REQUIRED"))
        return {"book": self.book, "root": str(self.root), "chapters": chs,
                "progress": {"done": done, "total": len(chs), "percent": round(100 * done / max(len(chs), 1), 1)}}
