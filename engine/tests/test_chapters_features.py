import json
import re

import pymupdf
import pytest

import fixtures
from conftest import read
from textbook2md import blocks as B, chapters, service
from textbook2md.pipeline import JobControl, convert_chapter, convert_many
from textbook2md.textclean import BOLD, SUP, Span

CFG = {"ocr_enabled": True}


# ------------------------------------------------------------- chapter detection (X, XI)

def test_outline_chapters_with_parts_and_page_ranges(book_pdf):
    info = service.inspect_pdf(book_pdf)
    chs = info["chapters"]
    assert [(c["number"], c["title"], c["start"], c["end"], c["part"]) for c in chs] == [
        (1, "Introduction to Anemia", 2, 3, "Hematology"), (2, "Respiratory Distress", 4, 7, "Respiratory"),
        (3, "Fluid Therapy", 8, 10, "Fluids")]
    assert all(c["confidence"] >= 0.9 and c["source"] == "outline" for c in chs)
    assert info["book"]["title"] == "Test Textbook of Pediatrics" and info["book"]["edition"] == "1"
    assert info["warnings"] == []


def test_toc_page_fallback(tmp_path):
    fixtures.make_toc_pages_book(tmp_path / "t.pdf")
    info = service.inspect_pdf(tmp_path / "t.pdf")
    assert [(c["number"], c["start"], c["end"], c["source"]) for c in info["chapters"]] == [
        (1, 3, 4, "toc"), (2, 5, 6, "toc"), (3, 7, 8, "toc")]
    assert any("no outline" in w.lower() for w in info["warnings"])


def test_layout_fallback_when_no_outline_or_toc(book_pdf, tmp_path):
    d = pymupdf.open(str(book_pdf))
    d.set_toc([])
    d.save(str(tmp_path / "n.pdf"))
    chs = service.inspect_pdf(tmp_path / "n.pdf")["chapters"]
    assert [(c["number"], c["start"], c["source"]) for c in chs] == [(1, 2, "layout"), (2, 4, "layout"), (3, 8, "layout")]
    assert all(c["confidence"] <= 0.6 for c in chs)


def test_no_structure_flags_manual_review(tmp_path):
    fixtures.make_single_col(tmp_path / "s.pdf")
    info = service.inspect_pdf(tmp_path / "s.pdf")
    assert len(info["chapters"]) == 1 and info["chapters"][0]["confidence"] < 0.5
    assert info["warnings"]


def test_conflicting_boundaries_are_flagged():
    chs = [chapters.Chapter("", 1, "A", 5, 9), chapters.Chapter("", 1, "B", 7, 12), chapters.Chapter("", 5, "C", 13, 99)]
    chapters.assign_keys(chs)
    chapters.validate(chs, 50)
    assert chs[1].confidence < 0.5 and chs[2].confidence < 0.5 and chs[0].confidence < 0.5
    assert len({c.key for c in chs}) == 3


def test_chapter_sharing_a_page_is_split_by_position(tmp_path):
    fixtures.make_shared_page(tmp_path / "s.pdf")
    p = service.create_project(tmp_path / "s.pdf", tmp_path / "kb")
    c1, c2 = p.chapters
    assert (c1.start, c1.end, c2.start, c2.end) == (1, 1, 1, 2) and c1.end_y and c2.start_y
    convert_many(p, [c.key for c in p.chapters], CFG, JobControl())
    a, b = [read(p, k).split("---\n", 2)[2] for k in ("ch_001", "ch_002")]
    assert "Second chapter body" not in a and "Second chapter body" in b
    assert "First Topic" in a and "First Topic" not in b


# ------------------------------------------------------------------- tables, figures

def test_large_table_goes_to_separate_file_and_keeps_all_rows(tmp_path):
    fixtures.make_big_table(tmp_path / "t.pdf", rows=60)
    p = service.create_project(tmp_path / "t.pdf", tmp_path / "kb")
    convert_chapter(p, "ch_001", CFG, JobControl())
    md = read(p, "ch_001")
    assert "(../tables/ch_001/table_001_01.md)" in md and "| Analyte 5 |" not in md
    tbl = (p.root / "tables/ch_001/table_001_01.md").read_text()
    assert "| Analyte 1 | mg/dL | 1.5 | 2.25 |" in tbl and "| Analyte 59 | mg/dL | 59.5 | 118.25 |" in tbl   # page-2 continuation merged
    assert tbl.count("\n| Analyte") == 59
    assert "**TABLE 1.1** Laboratory reference values by age" in tbl
    from textbook2md import export
    assert export.check_links(p.root) == []
    iss = json.loads((p.hidden / "issues/ch_001.json").read_text())
    assert not [i for i in iss["issues"] if i["category"] in ("numeric", "tables") and i["severity"] in ("HIGH", "CRITICAL")], iss["issues"]
    sm = json.loads((p.root / "source_maps/ch_001.json").read_text())
    assert next(b for b in sm["blocks"] if b["type"] == "table")["source_pages"] == [1, 2]


def test_many_figures_all_extracted_with_captions(tmp_path):
    n = fixtures.make_many_figures(tmp_path / "f.pdf")
    p = service.create_project(tmp_path / "f.pdf", tmp_path / "kb")
    convert_chapter(p, "ch_001", CFG, JobControl())
    md = read(p, "ch_001")
    imgs = sorted((p.root / "assets/images/ch_001").glob("*"))
    assert len(imgs) == n == 6
    for i in range(1, n + 1):
        assert f"**Figure 1.{i}.** Panel {i} shows finding number {i} at {i * 10} mm." in md
        assert f"![Figure 1.{i}](../assets/images/ch_001/figure_001_{i:02d}.png)" in md
    assert len({f.read_bytes() for f in imgs}) == n                         # no duplicate extraction
    iss = json.loads((p.hidden / "issues/ch_001.json").read_text())
    assert not [i for i in iss["issues"] if i["category"] == "figures"], iss["issues"]


# ------------------------------------------------------------- footnotes, formulas, misc

def test_footnote_and_equation(tmp_path):
    fixtures.make_misc(tmp_path / "m.pdf")
    p = service.create_project(tmp_path / "m.pdf", tmp_path / "kb")
    convert_chapter(p, "ch_007", CFG, JobControl())
    md = read(p, "ch_007")
    assert "Mosteller method[^fn1-2] is widely used" in md
    assert "[^fn1-2]: Mosteller RD. N Engl J Med. 1987;317:1098." in md
    assert "$$\n\\text{BSA} = \\sqrt{\\text{height} \\times \\text{weight} / 3600}\n$$" in md
    iss = json.loads((p.hidden / "issues/ch_007.json").read_text())
    assert not [i for i in iss["issues"] if i["category"] == "numeric"], iss["issues"]


# ------------------------------------------------------------------ OCR integration

def test_ocr_pipeline_flags_low_confidence_digits(tmp_path, book_pdf, monkeypatch):
    from textbook2md import ocr
    monkeypatch.setattr(ocr, "available", lambda: True)
    words = [{"text": t, "conf": c, "bbox": [150 + 120 * i, 300, 250 + 120 * i, 340], "line": ("1", "1", "1")}
             for i, (t, c) in enumerate([("Give", .97), ("10", .5), ("mL/kg", .96)])]
    monkeypatch.setattr(ocr, "ocr_png", lambda *a, **k: words)
    p = service.create_project(book_pdf, tmp_path / "kb")
    convert_chapter(p, "ch_003", CFG, JobControl())
    md = read(p, "ch_003")
    assert "Give 10 mL/kg" in md and "<!-- UNCERTAIN: low OCR confidence, page 10 -->" in md
    iss = json.loads((p.hidden / "issues/ch_003.json").read_text())["issues"]
    assert any(i["code"] == "OCR_LOW_CONFIDENCE" and i["severity"] == "HIGH" for i in iss)       # digits → HIGH
    assert not any(i["code"] == "PAGE_UNREADABLE" for i in iss)


# ------------------------------------------------------------------- unit helpers

def mkpara(text, size, bold=False, lines=1, kind="para"):
    ln = {"raw": text, "size": size, "bold": bold, "italic": False, "spans": [[text, 0]], "bbox": [0, 0, 1, 1], "id": "p1-0"}
    pa = B.Para(lines=[ln] * lines, pages=[1])
    pa.spans, pa.text, pa.size, pa.bold, pa.kind = [Span(text)], text, size, bold, kind
    return pa


def test_heading_confidence_signals():
    body = 9.5
    assert B.heading_conf(mkpara("Clinical Manifestations", 13, True), body) >= 0.85
    assert 0.55 <= B.heading_conf(mkpara("Epidemiology", 9.5, True), body) < 0.7
    assert B.heading_conf(mkpara("This is a long sentence that ends with a period.", 9.5, True), body) < 0.6
    assert B.heading_conf(mkpara("a lowercase start", 13, True), body) < 0.6
    assert B.heading_conf(mkpara("Plain body text", 9.5, False), body) == 0
    assert B.heading_conf(mkpara("KEY POINTS", 9.5, True), body) == 0                # callout title, not a heading
    assert B.heading_conf(mkpara("1.2 Numbered Heading", 11, True), body) > B.heading_conf(mkpara("Numbered Heading", 11, True), body)


def test_heading_levels_by_size_and_memory():
    mem = {}
    hs = [mkpara("A", 13, True), mkpara("B", 11, True), mkpara("C", 11, True), mkpara("D", 9.5, True)]
    B.assign_levels(hs, mem, set(), 9.5)
    assert [h.h_level for h in hs] == [2, 3, 3, 4]
    assert mem["heading_styles"] == {"13|b": 2, "11|b": 3, "10|b": 4}
    later = [mkpara("X", 11, True), mkpara("Y", 13, True)]
    B.assign_levels(later, mem, set(), 9.5)
    assert [h.h_level for h in later] == [3, 2]                                   # conversion memory reused


def test_table_serialization_gfm_and_html():
    def cell(r, c, t, rs=1, cs=1):
        return {"r": r, "c": c, "rs": rs, "cs": cs, "md": t, "raw": t, "spans": [[t, BOLD if r == 0 else 0]]}
    simple = {"cells": [cell(0, 0, "A"), cell(0, 1, "B|x"), cell(1, 0, "1"), cell(1, 1, "2.5")], "nrows": 2, "ncols": 2,
              "header_rows": 1, "complex": False}
    md, kind = B.render_table(simple)
    assert kind == "gfm" and md == "| A | B\\|x |\n|---|---|\n| 1 | 2.5 |"
    cx = {"cells": [cell(0, 0, "H", cs=2), cell(1, 0, "x"), cell(1, 1, "y")], "nrows": 2, "ncols": 2, "header_rows": 1, "complex": True}
    html, kind = B.render_table(cx)
    assert kind == "html" and '<th colspan="2">H</th>' in html and "<td>x</td><td>y</td>" in html


def test_latex_conversion():
    sp = [Span("x = a"), Span("2", SUP), Span(" ± 3 × 4")]
    assert B.to_latex(sp) == "x = a^{2} \\pm 3 \\times 4"
    assert B._sqrt_braces("\\sqrt(a+(b))") == "\\sqrt{a+(b)}"


def test_stable_section_ids_disambiguate_duplicates():
    from textbook2md.assemble import assign_sections
    blocks = [B.Block(id="1", type="heading", level=1, clean="Ch"), B.Block(id="2", type="heading", level=2, clean="Treatment"),
              B.Block(id="3", type="heading", level=3, clean="Dosing"), B.Block(id="4", type="heading", level=2, clean="Other"),
              B.Block(id="5", type="heading", level=3, clean="Dosing"), B.Block(id="6", type="paragraph", clean="x")]
    secs = assign_sections(blocks, "bk1", {"key": "ch_012"})
    ids = [s["section_id"] for s in secs]
    assert ids[0] == "bk1-ch012" and len(set(ids)) == len(ids)
    assert ids[2] == "bk1-ch012-dosing" and ids[4] == "bk1-ch012-other-dosing"
    assert blocks[-1].section_id == ids[4] and blocks[-1].heading_path == ["Ch", "Other", "Dosing"]
