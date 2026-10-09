"""Synthetic textbook PDF generator used by the test-suite (no binary fixtures in git).

Layout features: single column chapter, two-column chapters, running header/footer, forced
line-end hyphenation, superscript citations, bullet/numbered lists, callout box, ruled table,
complex (merged-cell) table, raster figure, vector figure, references and one scanned page.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pymupdf

W, H = 612, 792
LM, RM, TM, BM = 54, 54, 80, 70
_LIB = Path("/usr/share/fonts/truetype/liberation")
_FILES = {"r": "LiberationSerif-Regular.ttf", "b": "LiberationSerif-Bold.ttf",
          "i": "LiberationSerif-Italic.ttf", "bi": "LiberationSerif-BoldItalic.ttf"}
if all((_LIB / f).exists() for f in _FILES.values()):
    FONTS = {"r": "lsr", "b": "lsb", "i": "lsi", "bi": "lsbi"}
    _FONT_OBJS = {k: pymupdf.Font(fontfile=str(_LIB / f)) for k, f in _FILES.items()}
    UNICODE_OK = True
else:  # base-14 fallback (no ≥ − – glyphs)
    FONTS = {"r": "tiro", "b": "tibo", "i": "tiit", "bi": "tibi"}
    _FONT_OBJS = {}
    UNICODE_OK = False
_ALIAS = {"tiro": "r", "tibo": "b", "tiit": "i"}


def tlen(text, fontname, fontsize):
    key = next((k for k, v in FONTS.items() if v == fontname), None)
    if _FONT_OBJS and key:
        return _FONT_OBJS[key].text_length(text, fontsize=fontsize)
    return pymupdf.get_text_length(text, fontname=fontname, fontsize=fontsize)


def put(page, xy, text, fontname, fontsize):
    """insert_text with automatic font registration."""
    if _FONT_OBJS:
        fontname = FONTS.get(_ALIAS.get(fontname, ""), fontname) if fontname in _ALIAS else fontname
        if fontname in FONTS.values():
            key = next(k for k, v in FONTS.items() if v == fontname)
            page.insert_font(fontname=fontname, fontfile=str(_LIB / _FILES[key]))
    page.insert_text(xy, text, fontname=fontname, fontsize=fontsize)


class Cursor:
    def __init__(self, page, x0, x1, y0, y1):
        self.page, self.x0, self.x1, self.y, self.ymax = page, x0, x1, y0, y1


def tokenize(text: str):
    """Tokens: (word, style, sup). `**bold**`, `*italic*`, `^{sup}`; `¦` forces a line end."""
    toks = []
    for part in re.split(r"(\*\*.+?\*\*|\*.+?\*|\^\{.+?\})", text):
        if not part:
            continue
        style, sup = "r", False
        if part.startswith("**"):
            style, part = "b", part[2:-2]
        elif part.startswith("*"):
            style, part = "i", part[1:-1]
        elif part.startswith("^{"):
            sup, part = True, part[2:-1]
        for w in part.split(" "):
            if w == "":
                continue
            if "¦" in w:
                a, b = w.split("¦")
                toks.append((a, style, sup, True))
                if b:
                    toks.append((b, style, sup, False))
            else:
                toks.append((w, style, sup, False))
    return toks


def para(cur: Cursor, text: str, size=9.5, indent=0.0, lead=1.28, gap=5, style_override=None, first_indent=0.0) -> bool:
    """Draw a wrapped paragraph; returns False if it ran out of room (text truncated)."""
    toks = tokenize(text)
    lines, line, width, force = [], [], 0.0, False
    avail = cur.x1 - cur.x0 - indent
    for w, st, sup, brk in toks:
        st = style_override or st
        fs = size * (0.6 if sup else 1.0)
        tw = tlen(w, FONTS[st], fs)
        sp = tlen(" ", FONTS["r"], size) if line and not sup else 0
        lim = avail - (first_indent if not lines else 0)
        if line and width + sp + tw > lim:
            lines.append(line)
            line, width = [], 0.0
            sp = 0
        line.append((w, st, sup, sp))
        width += sp + tw
        if brk:
            lines.append(line)
            line, width = [], 0.0
    if line:
        lines.append(line)
    for n, ln in enumerate(lines):
        if cur.y + size > cur.ymax:
            return False
        x = cur.x0 + indent + (first_indent if n == 0 else 0)
        for w, st, sup, sp in ln:
            x += sp
            fs = size * (0.6 if sup else 1.0)
            y = cur.y + size * 0.8 - (size * 0.35 if sup else 0)
            put(cur.page, (x, y), w, FONTS[st], fs)
            x += tlen(w, FONTS[st], fs)
        cur.y += size * lead
    cur.y += gap
    return True


def heading(cur, text, size, style="b", gap_before=8, gap_after=4):
    cur.y += gap_before
    para(cur, text, size=size, style_override=style, gap=gap_after)


def header_footer(page, chapter_no, title, pno, pdf_page):
    put(page, (LM, 40), f"CHAPTER {chapter_no} {title}", "tiit", 8)
    put(page, (W - RM - 20, 40), str(pno), "tiro", 8)
    put(page, (LM, H - 30), "Copyright © Test Publisher. All rights reserved.", "tiro", 7)


def png_bytes(w=160, h=100) -> bytes:
    pm = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), False)
    pm.clear_with(200)
    for x in range(0, w, 10):
        pm.set_rect(pymupdf.IRect(x, 0, x + 3, h), (30, 60, 120))
    for y in range(0, h, 10):
        pm.set_rect(pymupdf.IRect(0, y, w, y + 2), (120, 30, 60))
    return pm.tobytes("png")


LOREM1 = ("Anemia is among the most common ¦hematologic problems in children and its evalu-¦ation requires a "
          "systematic approach.^{1} The prevalence varies with age, and iron deficiency remains the leading "
          "cause worldwide [1]. Hemoglobin below 11.0 g/dL in children aged 6 months to 5 years defines "
          "anemia, and a ferritin below 12 µg/L supports iron deficiency. Long-term follow-up is essential, "
          "and the pedi-¦atric hematologist should be involved when the response is poor.")
LOREM2 = ("Treatment of iron deficiency consists of oral ferrous sulfate at 3 mg/kg/day of elemental iron "
          "divided once or twice daily for 8 to 12 weeks (95% CI 1.2–3.4). A rise in hemoglobin of 1.0 g/dL "
          "after 4 weeks confirms the diagnosis; the reticulocyte count peaks at day 5 to 10 [2,3] (see Chapter 2, Table 2.1 and Fig. 3.1).")
FILL = ("The pediatric clinical evaluation includes a careful history, a dietary assessment, and a physical examination "
        "looking for pallor, tachycardia, and flow murmurs. Laboratory evaluation begins with a complete blood "
        "count with red cell indices and a reticulocyte count. ")


def make_book(path: str | Path) -> dict:
    doc = pymupdf.open()
    toc = []
    # ---- page 1: title page
    p = doc.new_page(width=W, height=H)
    put(p, (100, 300), "Test Textbook of Pediatrics", "tibo", 26)
    put(p, (100, 340), "1st Edition", "tiro", 14)
    toc.append([1, "Part I Hematology", 2])
    # ---- chapter 1: single column (pages 2-3)
    p = doc.new_page(width=W, height=H)
    header_footer(p, 1, "Introduction to Anemia", 2, 2)
    cur = Cursor(p, LM, W - RM, TM, H - BM)
    para(cur, "Chapter 1 Introduction to Anemia", size=20, style_override="b", gap=10)
    para(cur, "*Jane Roe, MD*", size=10, gap=10)
    heading(cur, "Definition", 13)
    para(cur, LOREM1 + ' ' + LOREM1)
    para(cur, FILL * 2)
    heading(cur, "Treatment", 13)
    para(cur, LOREM2)
    heading(cur, "Iron Preparations", 11, style="bi")
    para(cur, "• Ferrous sulfate 3 mg/kg/day", indent=12, gap=1)
    para(cur, "• Ferrous gluconate 5 mg/kg/day", indent=12, gap=1)
    para(cur, "– give between meals", indent=26, gap=1)
    para(cur, "• Parenteral iron 1.5 mg/kg", indent=12, gap=6)
    para(cur, "1. Confirm the diagnosis", indent=12, gap=1)
    para(cur, "2. Treat the underlying cause", indent=12, gap=1)
    para(cur, "3. Reassess hemoglobin in 4 weeks", indent=12, gap=6)
    para(cur, FILL * 3)
    p = doc.new_page(width=W, height=H)
    header_footer(p, 1, "Introduction to Anemia", 3, 3)
    cur = Cursor(p, LM, W - RM, TM, H - BM)
    para(cur, "and the follow-up visit should confirm normalization of the hemoglobin concentration. " + FILL)
    heading(cur, "References", 13)
    para(cur, "1. Smith J, Lee K. Iron deficiency in infancy. Pediatrics. 2010;125:1–10. doi:10.1542/peds.2009-1", indent=0, gap=2)
    para(cur, "2. Brown A, et al. Oral iron dosing. J Pediatr. 2012;160:44–49. PMID: 22345678 https://example.org/iron.", gap=2)
    para(cur, "3. Garcia M. Reticulocyte kinetics. Blood. 2008;111:12–19.", gap=2)
    toc.append([2, "Chapter 1 Introduction to Anemia", 2])
    # ---- chapter 2: two columns (pages 4-7)
    toc.append([1, "Part II Respiratory", 4])
    colw = (W - LM - RM - 18) / 2
    for k, pg in enumerate(range(4, 8)):
        p = doc.new_page(width=W, height=H)
        header_footer(p, 2, "Respiratory Distress", pg, pg)
        top = TM
        if k == 0:
            cur = Cursor(p, LM, W - RM, TM, H - BM)
            para(cur, "Chapter 2 Respiratory Distress", size=20, style_override="b", gap=8)
            top = cur.y + 6
        L = Cursor(p, LM, LM + colw, top, H - BM)
        R = Cursor(p, LM + colw + 18, W - RM, top, H - BM)
        if k == 0:
            para(L, "Respiratory distress is a frequent cause of admission. " + FILL, gap=4)
            heading(L, "Pathophysiology", 13)
            para(L, FILL + LOREM1, gap=4)
            # callout box (filled rect) in left column
            y0 = L.y + 4
            p.draw_rect(pymupdf.Rect(L.x0, y0, L.x1, y0 + 92), color=(0.5, 0.5, 0.5), fill=(0.92, 0.92, 0.92))
            L.y = y0 + 6
            para(Cursor(p, L.x0 + 4, L.x1 - 4, L.y, y0 + 90), "**KEY POINTS**", gap=3)
            c2 = Cursor(p, L.x0 + 4, L.x1 - 4, L.y + 14, y0 + 90)
            para(c2, "• Tachypnea is the earliest sign", gap=1, size=9)
            para(c2, "• Oxygen saturation below 92% needs support", gap=1, size=9)
            L.y = y0 + 98
            para(L, FILL * 3, gap=4)
            para(R, FILL * 6, gap=4)
            heading(R, "Clinical Manifestations", 13)
            para(R, "Infants may present with grunting, flaring, and retractions; the respiratory rate may exceed 60 "
                    "breaths/min. PaO₂ below 60 mmHg indicates failure. " + FILL * 3)
        elif k == 1:
            para(L, FILL * 4, gap=4)
            heading(L, "Diagnosis", 13)
            para(L, FILL * 4)
            # table caption + ruled table in right column
            para(R, "**TABLE 2.1** Normal respiratory rates by age", size=9, gap=4)
            rows = [["Age", "Rate (breaths/min)", "Heart rate", "SpO2 (%)"],
                    ["Newborn", "30–60", "120–160", "≥ 95"],
                    ["Infant", "25–40", "100–150", "≥ 95"],
                    ["Toddler", "20–30", "90–140", "94.5"],
                    ["Child", "18–25", "80–120", "≥ 94"]]
            ty = R.y
            cw = [48, 78, 54, 45]
            for ri, row in enumerate(rows):
                x = R.x0
                for ci, cell in enumerate(row):
                    r = pymupdf.Rect(x, ty, x + cw[ci], ty + 16)
                    p.draw_rect(r, color=(0, 0, 0), width=0.6)
                    put(p, (x + 3, ty + 11.5), cell, "tibo" if ri == 0 else "tiro", 8.5)
                    x += cw[ci]
                ty += 16
            R.y = ty + 6
            para(R, "SpO2, oxygen saturation. Values are for awake children.", size=8, gap=6)
            # raster figure with caption
            p.insert_image(pymupdf.Rect(R.x0, R.y, R.x0 + 160, R.y + 100), stream=png_bytes())
            R.y += 108
            para(R, "**Figure 2.1.** Chest radiograph of a child with ¦bronchiolitis showing hyperinflation.", size=8.5, gap=6)
            para(R, FILL * 3)
        elif k == 2:
            para(L, FILL * 5)
            para(R, FILL * 5)
        else:
            cur = Cursor(p, LM, W - RM, TM, H - BM)
            heading(cur, "References", 13)
            para(cur, "1. Adams R. Bronchiolitis outcomes. Pediatrics. 2015;135:e1–e9.", gap=2)
            para(cur, "2. Wong T, Li Y. Oxygen saturation targets. N Engl J Med. 2016;374:55–60.", gap=2)
    toc.append([2, "Chapter 2 Respiratory Distress", 4])
    # ---- chapter 3 (pages 8-10)
    toc.append([1, "Part III Fluids", 8])
    p = doc.new_page(width=W, height=H)
    header_footer(p, 3, "Fluid Therapy", 8, 8)
    cur = Cursor(p, LM, W - RM, TM, H - BM)
    para(cur, "Chapter 3 Fluid Therapy", size=20, style_override="b", gap=10)
    heading(cur, "Maintenance Fluids", 13)
    para(cur, "Maintenance volume follows the Holliday–Segar rule: 100 mL/kg for the first 10 kg, 50 mL/kg for the "
              "next 10 kg, and 20 mL/kg thereafter. Isotonic saline with 5% dextrose is preferred [1]. " + FILL)
    # vector figure: boxes + arrows with labels
    fy = cur.y + 6
    for i, lab in enumerate(["Assess", "Bolus 20 mL/kg", "Reassess"]):
        r = pymupdf.Rect(LM + 10 + i * 150, fy, LM + 120 + i * 150, fy + 40)
        p.draw_rect(r, color=(0, 0, 0), fill=(0.85, 0.9, 1), width=1)
        put(p, (r.x0 + 8, r.y0 + 24), lab, "tiro", 9)
        if i < 2:
            p.draw_line((r.x1, fy + 20), (r.x1 + 30, fy + 20), color=(0, 0, 0), width=1.2)
            p.draw_line((r.x1 + 24, fy + 16), (r.x1 + 30, fy + 20), color=(0, 0, 0), width=1.2)
            p.draw_line((r.x1 + 24, fy + 24), (r.x1 + 30, fy + 20), color=(0, 0, 0), width=1.2)
    p.draw_line((LM + 10, fy + 52), (LM + 400, fy + 52), color=(0, 0, 0), width=1)
    p.draw_line((LM + 10, fy + 60), (LM + 400, fy + 60), color=(0.3, 0.3, 0.3), width=1)
    p.draw_circle((LM + 200, fy + 76), 10, color=(0, 0, 0), fill=(1, 0.8, 0.8))
    cur.y = fy + 100
    para(cur, "**Figure 3.1.** Algorithm for fluid resuscitation in dehydration.", size=8.5, gap=8)
    # complex table with merged header
    para(cur, "**TABLE 3.1** Electrolyte requirements", size=9, gap=4)
    ty, x0 = cur.y, LM
    cws = [90, 70, 70, 70]
    def cell(r, txt, bold=False):
        p.draw_rect(r, color=(0, 0, 0), width=0.6)
        put(p, (r.x0 + 3, r.y0 + 11.5), txt, "tibo" if bold else "tiro", 8.5)
    cell(pymupdf.Rect(x0, ty, x0 + 90, ty + 32), "Fluid", True)
    cell(pymupdf.Rect(x0 + 90, ty, x0 + 230, ty + 16), "Electrolytes (mEq/L)", True)
    cell(pymupdf.Rect(x0 + 230, ty, x0 + 300, ty + 32), "Use", True)
    cell(pymupdf.Rect(x0 + 90, ty + 16, x0 + 160, ty + 32), "Na", True)
    cell(pymupdf.Rect(x0 + 160, ty + 16, x0 + 230, ty + 32), "K", True)
    for i, row in enumerate([["0.9% NaCl", "154", "0", "Bolus"], ["D5 0.45% NaCl", "77", "20", "Maintenance"]]):
        xx = x0
        for ci, c in enumerate(row):
            cell(pymupdf.Rect(xx, ty + 32 + i * 16, xx + cws[ci] if ci else xx + 90, ty + 48 + i * 16), c)
            xx += 90 if ci == 0 else 70
    cur.y = ty + 32 + 32 + 10
    para(cur, "Corrected sodium = measured Na + 1.6 × ((glucose − 100) / 100). " + FILL)
    # page 9 text, page 10 scanned
    p = doc.new_page(width=W, height=H)
    header_footer(p, 3, "Fluid Therapy", 9, 9)
    cur = Cursor(p, LM, W - RM, TM, H - BM)
    heading(cur, "Monitoring", 13, gap_before=0)
    para(cur, "Urine output of at least 1 mL/kg/hour indicates adequate perfusion. " + FILL * 2)
    # scanned page: render a text page to an image, then embed as image only
    tmp = pymupdf.open()
    tp = tmp.new_page(width=W, height=H)
    c3 = Cursor(tp, LM, W - RM, TM, H - BM)
    para(c3, "Scanned addendum: give 10 mL/kg of 0.9% saline.", size=12)
    pix = tp.get_pixmap(dpi=150)
    p = doc.new_page(width=W, height=H)
    p.insert_image(p.rect, stream=pix.tobytes("png"))
    toc.append([2, "Chapter 3 Fluid Therapy", 8])
    doc.set_toc(toc)
    doc.set_metadata({"title": "Test Textbook of Pediatrics", "author": "Test Authors"})
    doc.save(str(path))
    doc.close()
    return {"pages": 10, "chapters": [(1, 2, 3), (2, 4, 7), (3, 8, 10)]}


def make_single_col(path: str | Path, n_pages=3) -> None:
    doc = pymupdf.open()
    for i in range(n_pages):
        p = doc.new_page(width=W, height=H)
        cur = Cursor(p, LM, W - RM, TM, H - BM)
        if i == 0:
            para(cur, "Simple Handbook", size=20, style_override="b")
        para(cur, FILL * 6)
    doc.save(str(path))
    doc.close()


def draw_table(page, x, y, rows, widths, rh=14, header=True, size=8):
    for ri, row in enumerate(rows):
        xx = x
        for ci, cell in enumerate(row):
            r = pymupdf.Rect(xx, y, xx + widths[ci], y + rh)
            page.draw_rect(r, color=(0, 0, 0), width=0.5)
            put(page, (xx + 2, y + rh - 4), cell, "tibo" if (header and ri == 0) else "tiro", size)
            xx += widths[ci]
        y += rh
    return y


def make_many_figures(path):
    """Chapter 1: two-column pages with six raster figures + captions (some captions below, one above)."""
    doc = pymupdf.open()
    colw = (W - LM - RM - 18) / 2
    n = 0
    for pg in range(2):
        p = doc.new_page(width=W, height=H)
        header_footer(p, 1, "Imaging", 2 + pg, 2 + pg)
        top = TM
        if pg == 0:
            cur = Cursor(p, LM, W - RM, TM, H - BM)
            para(cur, "Chapter 1 Imaging", size=20, style_override="b", gap=8)
            top = cur.y + 6
        cols = [Cursor(p, LM, LM + colw, top, H - BM), Cursor(p, LM + colw + 18, W - RM, top, H - BM)]
        for col in cols:
            for k in range(2 if pg == 0 else 1):
                para(col, FILL, gap=4)
                n += 1
                p.insert_image(pymupdf.Rect(col.x0, col.y, col.x0 + 150, col.y + 90), stream=png_bytes(150 + n, 90))
                col.y += 96
                para(col, f"**Figure 1.{n}.** Panel {n} shows finding number {n} at {n * 10} mm.", size=8.5, gap=6)
    doc.set_toc([[1, "Chapter 1 Imaging", 1]])
    doc.save(str(path))
    doc.close()
    return n


def make_big_table(path, rows=60):
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    header_footer(p, 1, "Reference Values", 2, 2)
    cur = Cursor(p, LM, W - RM, TM, H - BM)
    para(cur, "Chapter 1 Reference Values", size=20, style_override="b", gap=8)
    para(cur, "**TABLE 1.1** Laboratory reference values by age", size=9, gap=4)
    data = [["Test", "Unit", "Low", "High"]] + [[f"Analyte {i}", "mg/dL", f"{i}.5", f"{i * 2}.25"] for i in range(1, rows)]
    draw_table(p, LM, cur.y, data[:42], [120, 70, 60, 60], rh=13)
    p2 = doc.new_page(width=W, height=H)
    header_footer(p2, 1, "Reference Values", 3, 3)
    draw_table(p2, LM, TM, data[42:], [120, 70, 60, 60], rh=13, header=False)
    doc.set_toc([[1, "Chapter 1 Reference Values", 1]])
    doc.save(str(path))
    doc.close()


def make_shared_page(path):
    """Two chapters on one page; the outline gives the y position of chapter 2."""
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    cur = Cursor(p, LM, W - RM, TM, H - BM)
    para(cur, "Chapter 1 First Topic", size=20, style_override="b", gap=8)
    para(cur, FILL * 2)
    y2 = cur.y + 30
    cur.y = y2
    para(cur, "Chapter 2 Second Topic", size=20, style_override="b", gap=8)
    para(cur, "Second chapter body text with 42 mg. " + FILL)
    p2 = doc.new_page(width=W, height=H)
    cur = Cursor(p2, LM, W - RM, TM, H - BM)
    para(cur, FILL * 2)
    doc.set_toc([[1, "Chapter 1 First Topic", 1, {"kind": pymupdf.LINK_GOTO, "page": 0, "to": pymupdf.Point(0, 50)}],
                 [1, "Chapter 2 Second Topic", 1, {"kind": pymupdf.LINK_GOTO, "page": 0, "to": pymupdf.Point(0, y2 - 10)}]])
    doc.save(str(path))
    doc.close()
    return y2


def make_misc(path):
    """Footnote with superscript marker, standalone equation, unnumbered refs, TOC-less layout."""
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    cur = Cursor(p, LM, W - RM, TM, H - BM)
    para(cur, "Chapter 7 Body Surface Area", size=20, style_override="b", gap=10)
    heading(cur, "Calculation", 13)
    para(cur, "The Mosteller method^{2} is widely used in oncology dosing. " + FILL)
    cx = pymupdf.get_text_length("BSA = √(height × weight / 3600)", fontname="helv", fontsize=9.5)
    put(p, ((W - tlen("BSA = √(height × weight / 3600)", FONTS["r"], 9.5)) / 2, cur.y + 10), "BSA = √(height × weight / 3600)", FONTS["r"], 9.5)
    cur.y += 30
    para(cur, "where height is in cm and weight in kg; typical BSA is 1.73 m for adults.")
    foot = Cursor(p, LM, W - RM, H - 120, H - 60)
    para(foot, "^{2} Mosteller RD. N Engl J Med. 1987;317:1098.", size=7.5)
    doc.save(str(path))
    doc.close()


def make_toc_pages_book(path):
    """No outline: a printed contents page with dot leaders, chapters start on later pages."""
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    cur = Cursor(p, LM, W - RM, TM, H - BM)
    para(cur, "Contents", size=18, style_override="b")
    for n, (t, pg) in enumerate([("Introduction to Anemia", 3), ("Respiratory Distress", 5), ("Fluid Therapy", 7)], 1):
        put(p, (LM, cur.y + 10), f"{n}  {t} " + "." * 40 + f" {pg}", FONTS["r"], 10)
        cur.y += 16
    doc.new_page(width=W, height=H)                  # page 2: blank (offset 0 → printed page == pdf page)
    for n, t in enumerate(["Introduction to Anemia", "Respiratory Distress", "Fluid Therapy"], 1):
        for k in range(2):
            p = doc.new_page(width=W, height=H)
            cur = Cursor(p, LM, W - RM, TM, H - BM)
            if k == 0:
                para(cur, f"Chapter {n} {t}", size=20, style_override="b", gap=8)
            para(cur, FILL * 3)
    doc.save(str(path))
    doc.close()
