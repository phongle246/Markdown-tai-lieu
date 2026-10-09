import hashlib
import json
import re

import yaml

from conftest import read
from textbook2md import indexer
from textbook2md.util import sha256_file


def front(md):
    return yaml.safe_load(md.split("---\n")[1])


def test_original_pdf_never_modified(converted):
    assert sha256_file(converted.pdf_path()) == converted.book["source_sha256"]


def test_front_matter(converted):
    fm = front(read(converted, "ch_002"))
    assert fm["book_title"] == "Test Textbook of Pediatrics" and fm["chapter_number"] == 2
    assert fm["source_pages"] == "4-7" and fm["language"] == "en" and fm["content_type"] == "textbook_chapter"
    assert fm["source_sha256"] == converted.book["source_sha256"] and "conversion_version" in fm
    assert "respiratory" in fm["specialty"] and isinstance(fm["tags"], list)


def test_headings_hierarchy_and_stable_section_ids(converted):
    md = read(converted, "ch_001")
    heads = re.findall(r"^(#{1,4}) (.+)$\n<!-- section_id: (\S+) -->", md, flags=re.M)
    assert [(h[0], h[1]) for h in heads][:3] == [("#", "Chapter 1 Introduction to Anemia"), ("##", "Definition"), ("##", "Treatment")]
    assert heads[0][2] == "test1-ch001" and heads[1][2] == "test1-ch001-definition"
    assert len({h[2] for h in heads}) == len(heads)
    # H1 appears exactly once; no heading level jumps
    levels = [len(h[0]) for h in heads]
    assert levels.count(1) == 1 and all(b - a <= 1 for a, b in zip(levels, levels[1:]))


def test_paragraphs_joined_and_dehyphenated(converted):
    md = read(converted, "ch_001")
    assert "evaluation requires a systematic approach" in md          # evalu-/ation joined
    assert "pediatric hematologist" in md and "pedi-" not in md        # vocab-evidenced dehyphenation
    assert "Long-term follow-up" in md                                  # real hyphen untouched
    assert "reticulocyte count.\n" not in md.split("## Definition")[1].split("\n\n")[0]  # no one-sentence-per-line
    assert "Copyright ©" not in md and "CHAPTER 1 Introduction" not in md  # running header/footer removed


def test_two_column_text_order_in_markdown(converted):
    md = read(converted, "ch_002")
    assert md.index("## Pathophysiology") < md.index("## Clinical Manifestations") < md.index("## Diagnosis")
    # left column "Pathophysiology" text precedes right column "Clinical Manifestations"
    assert md.index("approach.<sup>1</sup>") < md.index("grunting, flaring")


def test_lists_preserved_with_nesting_and_numbering(converted):
    md = read(converted, "ch_001")
    assert "- Ferrous sulfate 3 mg/kg/day\n- Ferrous gluconate 5 mg/kg/day\n  - give between meals\n- Parenteral iron 1.5 mg/kg" in md
    assert "1. Confirm the diagnosis\n2. Treat the underlying cause\n3. Reassess hemoglobin in 4 weeks" in md


def test_callout_box_markdown(converted):
    md = read(converted, "ch_002")
    assert "> **KEY POINTS**\n>\n> - Tachypnea is the earliest sign\n> - Oxygen saturation below 92% needs support" in md


def test_simple_table_is_gfm(converted):
    md = read(converted, "ch_002")
    assert "| Age | Rate (breaths/min) | Heart rate | SpO2 (%) |" in md
    assert "| Toddler | 20–30 | 90–140 | 94.5 |" in md and "| Newborn | 30–60 | 120–160 | ≥ 95 |" in md
    assert "**TABLE 2.1** Normal respiratory rates by age" in md and 'id="table-2-1"' in md
    assert "SpO2, oxygen saturation. Values are for awake children." in md      # table footnote kept


def test_complex_table_is_html_with_spans(converted):
    md = read(converted, "ch_003")
    assert '<th rowspan="2">Fluid</th><th colspan="2">Electrolytes (mEq/L)</th><th rowspan="2">Use</th>' in md
    assert "<td>D5 0.45% NaCl</td><td>77</td><td>20</td>" in md


def test_figures_extracted_with_captions_and_labels(converted):
    md = read(converted, "ch_002")
    assert "![Figure 2.1](../assets/images/ch_002/figure_002_01.png)" in md
    assert "**Figure 2.1.** Chest radiograph of a child with bronchiolitis showing hyperinflation." in md
    assert (converted.root / "assets/images/ch_002/figure_002_01.png").stat().st_size > 100
    md3 = read(converted, "ch_003")
    assert "<summary>Figure text / labels</summary>" in md3 and "- Bolus 20 mL/kg" in md3
    assert "base64" not in md and "base64" not in md3


def test_references_and_citations_preserved(converted):
    md = read(converted, "ch_001")
    assert '<a id="ref-1"></a>1. Smith J, Lee K. Iron deficiency in infancy. Pediatrics. 2010;125:1–10. doi:10.1542/peds.2009-1' in md
    assert "PMID: 22345678 <https://example.org/iron>." in md
    assert "[2,3]" in md and "<sup>1</sup>" in md and "[1]" in md


def test_page_markers_and_unreadable_page(converted):
    md = read(converted, "ch_002")
    assert [int(x) for x in re.findall(r"<!-- source_page: (\d+) -->", md)] == [4, 5, 6, 7]
    md3 = read(converted, "ch_003")
    assert "<!-- UNCERTAIN: source text unreadable, page 10 -->" in md3 and "[Unreadable source text]" in md3


def test_inline_page_marker_for_paragraph_spanning_pages(converted):
    md = read(converted, "ch_001")
    assert re.search(r"reticulocyte count\. <!-- source_page: 3 --> and the follow-up visit", md)


def test_source_map_traceability(converted):
    sm = json.loads((converted.root / "source_maps/ch_002.json").read_text())
    lines = read(converted, "ch_002").split("\n")
    kinds = {b["type"] for b in sm["blocks"]}
    assert {"heading", "paragraph", "table", "figure", "callout", "reference"} <= kinds
    for b in sm["blocks"]:
        assert b["source_pages"] and b["bboxes"] and b["section_id"] and len(b["fingerprint"]) == 12
        seg = "\n".join(lines[b["md_line_start"] - 1: b["md_line_end"]])
        assert seg.strip(), b
    tbl = next(b for b in sm["blocks"] if b["type"] == "table")
    assert tbl["source_pages"] == [5] and "Diagnosis" in tbl["heading_path"]
    assert lines[tbl["md_line_start"] - 1].startswith("<a id=\"table-2-1\">")


def test_numeric_qa_clean_on_test_book(converted):
    for key in ("ch_001", "ch_002"):
        r = json.loads((converted.hidden / "issues" / f"{key}.json").read_text())
        assert not [i for i in r["issues"] if i["category"] == "numeric"], r["issues"]
        assert r["metrics"]["numeric_tokens_source"] > 20
        assert r["metrics"]["char_loss_ratio"] < 0.002


def test_statuses_and_reports(converted):
    st = converted.state()["chapters"]
    assert st["ch_001"]["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS")
    assert st["ch_003"]["status"] == "REVIEW_REQUIRED"                        # scanned page w/o OCR → HIGH
    rep = (converted.root / "reports/ch_003_conversion_report.md").read_text()
    for h in ("## Status", "## Source", "## Structure", "## Pages Processed", "## Tables", "## Figures", "## OCR",
              "## Numeric QA", "## Citation QA", "## Uncertain Text", "## Possible Source Typos",
              "## Unresolved Cross References", "## Human Review Recommended"):
        assert h in rep
    assert "REVIEW REQUIRED" in rep and "PAGE_UNREADABLE" in rep


def test_citation_qa_flags_missing_reference_list(converted):
    r = json.loads((converted.hidden / "issues" / "ch_003.json").read_text())
    assert any(i["code"] == "CITATIONS_NO_REFERENCES" for i in r["issues"])
    r2 = json.loads((converted.hidden / "issues" / "ch_001.json").read_text())
    assert not any(i["category"] == "citations" for i in r2["issues"])


def test_book_level_outputs(converted):
    root = converted.root
    idx = (root / "00_BOOK_INDEX.md").read_text()
    assert "| Chapter | Title | Specialty | Source Pages | Markdown File | Status |" in idx
    assert "(chapters/ch_001_introduction-to-anemia.md)" in idx and "2–3" in idx
    readme = (root / "README.md").read_text()
    for s in ("page marker", "Obsidian", "Generated navigation", "chapters/"):
        assert s.lower() in readme.lower()
    man = json.loads((root / "manifest.json").read_text())
    assert man["source"]["sha256"] == converted.book["source_sha256"] and len(man["chapters"]) == 3
    assert man["chapters"][0]["markdown_sha256"] and man["chapters"][1]["assets"]
    for name in ("diseases", "drugs", "signs_symptoms", "topics"):
        t = (root / "indexes" / f"{name}.md").read_text()
        assert "Generated navigation index — not part of the original textbook." in t
    mem = json.loads((root / "metadata/conversion_memory.json").read_text())
    assert mem["heading_styles"] and "table_rules" in mem and mem["layout"]["body_size"]


def test_rag_chunks(converted):
    rows = [json.loads(l) for l in (converted.root / "rag/chunks.jsonl").read_text().splitlines()]
    assert rows
    need = {"id", "book", "edition", "chapter", "chapter_title", "section_id", "heading_path", "source_pages", "text",
            "content_type", "references"}
    assert all(need <= set(r) for r in rows)
    assert len({r["id"] for r in rows}) == len(rows)
    types = {r["content_type"] for r in rows}
    assert {"text", "table", "reference", "callout"} <= types
    t = next(r for r in rows if r["content_type"] == "table")
    assert t["source_pages"] == [5] and "| Newborn |" in t["text"]
    # chunks are verbatim packaging: every chunk text occurs in its chapter file
    for r in rows[:30]:
        md = (converted.root / r["markdown_file"]).read_text()
        body = re.sub(r"<!-- source_page: \d+ -->\s?", "", md)
        for part in r["text"].split("\n\n")[:2]:
            assert part in body


def test_search_finds_text_and_returns_page(converted):
    res = indexer.search(converted, "ferrous sulfate")
    assert res and res[0]["chapter"] == "ch_001" and res[0]["page"] == 2 and "[[" in res[0]["snippet"]
    res = indexer.search(converted, "bronchiolitis", chapter="ch_002")
    fig = [r for r in res if r["type"] == "figure"]
    assert fig and fig[0]["page"] == 5 and {r["page"] for r in res} >= {5, 7}
    assert indexer.search(converted, "tachypnea") and indexer.search(converted, "tachyp")       # prefix
    assert indexer.search(converted, "Normal respiratory", ctype="table")
    assert not indexer.search(converted, "zzzzqqq")
    r = indexer.search(converted, "grunting flaring")[0]
    assert r["heading_path"].endswith("Clinical Manifestations") and r["line"] > 0 and r["md_file"].startswith("chapters/")


def test_idempotent_rerun_keeps_ids_and_hash(converted, tmp_path):
    from textbook2md.pipeline import JobControl, convert_chapter
    before = read(converted, "ch_002")
    ids_before = [b["block_id"] for b in json.loads((converted.root / "source_maps/ch_002.json").read_text())["blocks"]]
    convert_chapter(converted, "ch_002", {"ocr_enabled": True}, JobControl(), force=True)
    assert read(converted, "ch_002") == before
    ids_after = [b["block_id"] for b in json.loads((converted.root / "source_maps/ch_002.json").read_text())["blocks"]]
    assert ids_before == ids_after
