import json
import re

import pytest

from conftest import read
from textbook2md import crossref, export, service
from textbook2md.chapters import Chapter
from textbook2md.pipeline import Cancelled, JobControl, convert_chapter, convert_many
from textbook2md.project import Project, ProjectError
from textbook2md import layout


# ------------------------------------------------------------------ resume (acceptance flow D)

def test_interrupted_conversion_resumes_from_page_cache(book_pdf, tmp_path, monkeypatch):
    p = service.create_project(book_pdf, tmp_path / "kb")
    ctrl = JobControl()
    seen = []

    def progress(ev):
        if ev.get("stage") == "Extracting text" and ev["done"] == 2:
            ctrl.cancel()                         # simulate the app being closed after 2 pages
        seen.append(ev)

    with pytest.raises(Cancelled):
        convert_chapter(p, "ch_002", {"ocr_enabled": True}, ctrl, progress)
    st = p.chapter_state("ch_002")
    assert st["status"] == "PROCESSING" and st["pages_done"] == 2 and st["interrupted"]
    assert p.overview()["chapters"][1]["interrupted"] is True            # visible after "re-opening"
    assert not p.chapter_md_path(p.chapter("ch_002")).exists()

    # "reopen the app": brand new Project object + counting extraction calls
    p2 = Project(p.root)
    calls = []
    orig = layout.page_record
    monkeypatch.setattr(layout, "page_record", lambda *a, **k: (calls.append(a[1] + 1), orig(*a, **k))[1])
    convert_chapter(p2, "ch_002", {"ocr_enabled": True}, JobControl())
    assert calls == [6, 7]                                               # only the missing pages were re-extracted
    assert p2.chapter_state("ch_002")["status"].startswith("COMPLETED")
    # result identical to an uninterrupted run
    p3 = service.create_project(book_pdf, tmp_path / "kb2")
    convert_chapter(p3, "ch_002", {"ocr_enabled": True}, JobControl())
    assert read(p2, "ch_002") == read(p3, "ch_002")


def test_state_persists_across_reopen_flow_c(converted):
    p = Project(converted.root)
    ov = p.overview()
    assert ov["progress"]["done"] == 3 and ov["progress"]["percent"] == 100
    assert all(c["has_markdown"] for c in ov["chapters"])


def test_pause_and_cancel_between_pages(book_pdf, tmp_path):
    p = service.create_project(book_pdf, tmp_path / "kb")
    ctrl = JobControl()
    ctrl.pause()
    assert ctrl.paused
    ctrl.resume()
    ctrl.cancel()
    res = convert_many(p, ["ch_001", "ch_002"], {"ocr_enabled": True}, ctrl)
    assert res["ch_001"]["status"] == "CANCELLED"


def test_failed_chapter_does_not_stop_batch(book_pdf, tmp_path):
    p = service.create_project(book_pdf, tmp_path / "kb")
    p.chapters[0].end = 999                     # invalid page range → failure
    p.save_book()
    res = convert_many(p, ["ch_001", "ch_002"], {"ocr_enabled": True}, JobControl())
    assert res["ch_001"]["status"] == "FAILED" and p.chapter_state("ch_001")["error"]
    assert res["ch_002"]["status"].startswith("COMPLETED")


# ------------------------------------------------------------- cross references (XLVI)

def test_unresolved_then_resolved_when_target_converted(book_pdf, tmp_path):
    p = service.create_project(book_pdf, tmp_path / "kb")
    convert_chapter(p, "ch_001", {"ocr_enabled": True}, JobControl())
    unres = json.loads((p.root / "metadata/unresolved_references.json").read_text())
    assert {u["text"] for u in unres} == {"Chapter 2", "Table 2.1", "Fig. 3.1"}
    assert "see Chapter 2, Table 2.1 and Fig. 3.1" in read(p, "ch_001")           # kept verbatim
    convert_chapter(p, "ch_002", {"ocr_enabled": True}, JobControl())
    md = read(p, "ch_001")
    assert "[Chapter 2](./ch_002_respiratory-distress.md)" in md
    assert "[Table 2.1](./ch_002_respiratory-distress.md#table-2-1)" in md
    assert "Fig. 3.1" in md and "[Fig. 3.1]" not in md                              # target not converted yet
    unres = json.loads((p.root / "metadata/unresolved_references.json").read_text())
    assert {u["text"] for u in unres} == {"Fig. 3.1"}
    convert_chapter(p, "ch_003", {"ocr_enabled": True}, JobControl())
    assert "[Fig. 3.1](./ch_003_fluid-therapy.md#figure-3-1)" in read(p, "ch_001")
    assert json.loads((p.root / "metadata/unresolved_references.json").read_text()) == []
    assert not export.check_links(p.root)


def test_crossref_linker_is_idempotent_and_skips_links():
    reg = {5: {"file": "chapters/ch_005_x.md", "labels": {"Table 5.1": {"anchor": "table-5-1"}}}}
    res = crossref.make_resolver(reg, 1, "chapters/ch_001_a.md", {})
    out, ok, bad = crossref.link_text("See Chapter 5 and Table 5.1 and Table 9.9; <!-- Chapter 5 --> `Chapter 5`", res)
    assert "[Chapter 5](./ch_005_x.md)" in out and "[Table 5.1](./ch_005_x.md#table-5-1)" in out
    assert "<!-- Chapter 5 -->" in out and "`Chapter 5`" in out and [r.text for r in bad] == ["Table 9.9"]
    again, ok2, _ = crossref.link_text(out, res)
    assert again == out and ok2 == []


# ------------------------------------------------------------------------ project model

def test_project_create_manifest_and_portable_yaml(converted):
    import yaml
    book = yaml.safe_load((converted.root / "book.yaml").read_text())
    assert book["source_sha256"] and len(book["chapters"]) == 3 and book["book_id"] == "test1"
    assert (converted.root / "manifest.json").exists()


def test_cannot_create_twice(book_pdf, tmp_path):
    service.create_project(book_pdf, tmp_path / "kb")
    with pytest.raises(ProjectError):
        service.create_project(book_pdf, tmp_path / "kb")


def test_manual_chapter_correction_resets_changed_chapters(book_pdf, tmp_path):
    p = service.create_project(book_pdf, tmp_path / "kb")
    convert_chapter(p, "ch_001", {"ocr_enabled": True}, JobControl())
    chs = [Chapter.from_dict(c.to_dict()) for c in p.chapters]
    chs[0].end = 2                                  # shrink chapter 1
    p.set_chapters(chs)
    assert p.chapter("ch_001").source == "manual" and p.chapter_state("ch_001")["status"] == "NOT_STARTED"
    assert p.memory()["previous_fixes"]


def test_source_sha_mismatch_is_actionable(book_pdf, tmp_path):
    import shutil
    pdf = tmp_path / "copy.pdf"
    shutil.copy(book_pdf, pdf)
    p = service.create_project(pdf, tmp_path / "kb")
    pdf.write_bytes(pdf.read_bytes() + b"\n%tampered")
    with pytest.raises(ProjectError, match="SHA-256"):
        p.verify_source()
    with pytest.raises(ProjectError):
        convert_chapter(p, "ch_001", {}, JobControl())
    assert p.chapter_state("ch_001")["status"] == "FAILED"


def test_encrypted_and_corrupt_pdfs_give_actionable_errors(tmp_path, book_pdf):
    import pymupdf
    enc = tmp_path / "enc.pdf"
    d = pymupdf.open(str(book_pdf))
    d.save(str(enc), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    with pytest.raises(ProjectError, match="encrypted"):
        service.inspect_pdf(enc)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(ProjectError):
        service.inspect_pdf(bad)
    with pytest.raises(ProjectError, match="not found"):
        service.inspect_pdf(tmp_path / "missing.pdf")


# ------------------------------------------------------------------------ export (flow E)

def test_zip_export_is_portable(converted, tmp_path):
    import zipfile
    z = tmp_path / "kb.zip"
    export.export_zip(converted, z)
    out = tmp_path / "elsewhere"
    zipfile.ZipFile(z).extractall(out)
    root = out / converted.root.name
    assert (root / "00_BOOK_INDEX.md").exists() and (root / "rag/chunks.jsonl").exists()
    assert not (root / ".t2md").exists() and not list(root.rglob("*.pdf"))          # PDF not bundled by default
    assert export.check_links(root) == []                                            # links & images resolve
    export.export_zip(converted, tmp_path / "kb2.zip", include_pdf=True)
    assert any(n.endswith(".pdf") for n in zipfile.ZipFile(tmp_path / "kb2.zip").namelist())


def test_obsidian_mode_is_export_only(converted, tmp_path):
    import zipfile
    z = tmp_path / "obs.zip"
    export.export_zip(converted, z, obsidian=True)
    names = zipfile.ZipFile(z).namelist()
    ch2 = zipfile.ZipFile(z).read(next(n for n in names if "ch_002_" in n and n.endswith(".md"))).decode()
    assert "> [!note] KEY POINTS" in ch2
    assert "> **KEY POINTS**" in read(converted, "ch_002")                          # canonical untouched
    ch1 = zipfile.ZipFile(z).read(next(n for n in names if "ch_001_" in n and n.endswith(".md"))).decode()
    assert "[[ch_002_respiratory-distress|Chapter 2]]" in ch1


def test_chapter_and_rag_exports(converted, tmp_path):
    import zipfile
    export.export_chapter(converted, "ch_002", tmp_path / "c.zip")
    n = zipfile.ZipFile(tmp_path / "c.zip").namelist()
    assert any(x.startswith("chapters/ch_002") for x in n) and "assets/images/ch_002/figure_002_01.png" in n
    export.export_rag(converted, tmp_path / "r.zip")
    n = zipfile.ZipFile(tmp_path / "r.zip").namelist()
    assert "rag/chunks.jsonl" in n and "source_maps/ch_002.json" in n
