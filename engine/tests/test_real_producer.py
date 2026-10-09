"""Cross-check with a PDF produced by an independent engine (LibreOffice): real justified two-column text,
automatic hyphenation, bullets drawn in a symbol font, merged-cell table, header/footer fields."""
import json
import re

import pytest

import lo_fixture
from textbook2md import service
from textbook2md.pipeline import JobControl, convert_chapter

pytestmark = pytest.mark.skipif(not lo_fixture.HAVE_SOFFICE, reason="LibreOffice not installed")


@pytest.fixture(scope="module")
def lo_project(tmp_path_factory):
    work = tmp_path_factory.mktemp("lo")
    pdf = lo_fixture.make_libreoffice_pdf(work)
    p = service.create_project(pdf, work / "kb")           # no outline → layout-based chapter detection
    assert p.chapters[0].source == "layout" and p.chapters[0].number == 1
    convert_chapter(p, p.chapters[0].key, {"ocr_enabled": False}, JobControl())
    return p


def test_real_pdf_reconstructs_every_sentence(lo_project):
    md = next((lo_project.root / "chapters").glob("ch_001*.md")).read_text()
    body = md.split("---\n", 2)[2]
    assert body.count(lo_fixture.EXPECTED_SENTENCE) == 21          # 10 paragraphs × 2 + 1 × 1, every one rebuilt from wrapped/hyphenated lines
    assert not re.search(r"[a-z]- [a-z]", body)                        # no un-joined hyphenation
    assert "<sup>1</sup>" in body and "Copyright" not in body and "Page 1" not in body       # running header/footer gone
    assert body.index("# Chapter 1 Thalassemia") < body.index("## Epidemiology")             # title before body, even if a column line sits level with it


def test_real_pdf_lists_tables_figures_references(lo_project):
    md = next((lo_project.root / "chapters").glob("ch_001*.md")).read_text()
    assert "- Transfusion every 3–4 weeks to keep pre-transfusion Hb above 9.5 g/dL\n- Chelation with deferasirox 20–30 mg/kg/day" in md
    assert "1. Confirm the diagnosis by hemoglobin electrophoresis\n2. Start regular transfusions" in md
    assert '<th colspan="2">Hematology</th>' in md and "<td>MCV 60–70</td>" in md and "<td>&lt;7.0</td>" in md
    assert "![Figure 1](../assets/images/ch_001/figure_001_01.png)" in md and "**Figure 1.** Peripheral blood smear" in md
    assert md.count('<a id="ref-') == 3 and "doi:10.1016/S0140-6736(17)31822-6" in md


def test_real_pdf_qa_is_clean(lo_project):
    r = json.loads((lo_project.hidden / "issues/ch_001.json").read_text())
    assert r["metrics"]["char_loss_ratio"] == 0 and r["metrics"]["numeric_issues"] == []
    assert r["metrics"]["numeric_tokens_source"] == r["metrics"]["numeric_tokens_markdown"] > 100
    assert not [i for i in r["issues"] if i["severity"] in ("CRITICAL", "HIGH")], r["issues"]
