from pathlib import Path

import pymupdf

from textbook2md import layout


def rec(book_pdf, page, tmp_path):
    doc = pymupdf.open(str(book_pdf))
    r = layout.page_record(doc, page - 1, layout.LayoutOptions(), tmp_path / "img")
    doc.close()
    return r


def test_two_column_reading_order(book_pdf, tmp_path):
    r = rec(book_pdf, 4, tmp_path)
    assert r["gutter"] and 290 < r["gutter"] < 320
    lines = [i for i in r["items"] if i["t"] == "line" and not i["zone"]]
    cols = [i["col"] for i in lines]
    # title (F) then a left-column run, then a right-column run — never L,R,L,R interleaving
    body = [c for c in cols if c != "F"]
    assert body == sorted(body, key=lambda c: c != "L")
    assert "".join(body).count("LR") == 1
    texts = [i["raw"] for i in lines]
    assert texts.index("Pathophysiology") < texts.index("Clinical Manifestations")


def test_single_column_page_has_no_gutter(book_pdf, tmp_path):
    assert rec(book_pdf, 2, tmp_path)["gutter"] is None


def test_running_header_footer_flagged_as_zone(book_pdf, tmp_path):
    r = rec(book_pdf, 4, tmp_path)
    zones = {i["raw"] for i in r["items"] if i["t"] == "line" and i["zone"]}
    assert any("CHAPTER 2" in z for z in zones) and any("Copyright" in z for z in zones)


def test_ruled_table_detected_with_header(book_pdf, tmp_path):
    r = rec(book_pdf, 5, tmp_path)
    t = next(i for i in r["items"] if i["t"] == "table")
    assert (t["nrows"], t["ncols"]) == (5, 4) and not t["complex"] and t["header_rows"] == 1
    cells = {(c["r"], c["c"]): c["raw"] for c in t["cells"]}
    assert cells[(0, 1)] == "Rate (breaths/min)" and cells[(1, 1)].replace("–", "-") == "30-60"


def test_merged_header_table_is_complex_with_spans(book_pdf, tmp_path):
    r = rec(book_pdf, 8, tmp_path)
    t = next(i for i in r["items"] if i["t"] == "table")
    assert t["complex"] and t["header_rows"] == 2
    spans = {c["raw"]: (c["rs"], c["cs"]) for c in t["cells"]}
    assert spans["Fluid"] == (2, 1) and spans["Electrolytes (mEq/L)"] == (1, 2)


def test_raster_figure_keeps_original_bytes(book_pdf, tmp_path):
    r = rec(book_pdf, 5, tmp_path)
    f = next(i for i in r["items"] if i["t"] == "figure")
    assert f["kind"] == "raster" and f["how"] == "original-bytes" and (tmp_path / "img" / f["tmp"]).exists()


def test_vector_figure_rendered_with_labels(book_pdf, tmp_path):
    r = rec(book_pdf, 8, tmp_path)
    f = next(i for i in r["items"] if i["t"] == "figure")
    assert "vector" in f["kind"] and {"Assess", "Reassess"} <= set(f["labels"])
    texts = [i["raw"] for i in r["items"] if i["t"] == "line"]
    assert "Assess" not in texts                    # label text moved out of the body flow


def test_callout_box_detected(book_pdf, tmp_path):
    r = rec(book_pdf, 4, tmp_path)
    assert len(r["boxes"]) == 1
    boxed = [i["raw"] for i in r["items"] if i["t"] == "line" and i.get("box") == 0]
    assert boxed[0] == "KEY POINTS" and len(boxed) == 3


def test_scanned_page_flagged_when_ocr_unavailable(book_pdf, tmp_path, monkeypatch):
    from textbook2md import ocr
    monkeypatch.setattr(ocr, "available", lambda: False)
    r = rec(book_pdf, 10, tmp_path)
    assert r["scanned"] and "ocr_unavailable" in r["flags"]
    assert r["items"][0]["kind"] == "scanned_page"


def test_scanned_page_uses_ocr_when_available(book_pdf, tmp_path, monkeypatch):
    from textbook2md import ocr
    monkeypatch.setattr(ocr, "available", lambda: True)
    words = [{"text": t, "conf": c, "bbox": [100 + 60 * i, 200, 150 + 60 * i, 230], "line": ("1", "1", "1")}
             for i, (t, c) in enumerate([("Give", 0.96), ("10", 0.55), ("mL/kg", 0.97)])]
    monkeypatch.setattr(ocr, "ocr_png", lambda *a, **k: words)
    r = rec(book_pdf, 10, tmp_path)
    assert r["ocr"] and r["items"][0]["raw"] == "Give 10 mL/kg"
    assert r["items"][0]["min_conf"] == 0.55 and r["items"][0]["ocr"]


def test_tables_not_confused_with_text_native_pages(book_pdf, tmp_path):
    r = rec(book_pdf, 2, tmp_path)
    assert not r.get("scanned") and r["native_chars"] > 500
