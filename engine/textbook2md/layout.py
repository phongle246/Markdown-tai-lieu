"""Passes 1–4 + 8–9 at page level: inspection, layout analysis, reading order, extraction,
tables and figures. Output is a JSON-serialisable page record that is checkpointed to disk."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import numeric, ocr as ocrmod
from .textclean import (BOLD, ITALIC, SUB, SUP, Span, has_encoding_artifacts, join_span_lines,
                        normalize_chars, spans_to_md)

ZONE_FRAC = 0.085
CAPTION_RE = re.compile(r"^\s*(figure|fig\.?|table|box|algorithm|plate)\s+[\dIVXA-Z]+[\d.\-A-Za-z]*\s*[.:|]?", re.I)
FIG_CAPTION_RE = re.compile(r"^\s*(figure|fig\.?|plate|algorithm)\s+[\dIVXA-Z]+[\d.\-A-Za-z]*", re.I)
TABLE_CAPTION_RE = re.compile(r"^\s*table\s+[\dIVXA-Z]+[\d.\-A-Za-z]*", re.I)


@dataclass
class LayoutOptions:
    ocr_enabled: bool = True
    ocr_lang: str = "eng"
    skip_xrefs: set = field(default_factory=set)
    fig_dpi: int = 200
    min_native_chars: int = 25


# ----------------------------------------------------------------------------- spans

def _is_bold(font: str, flags: int) -> bool:
    f = font.lower()
    return bool(flags & 16) or any(k in f for k in ("bold", "black", "heavy", "semibold", "-bd", "demi"))


def _is_italic(font: str, flags: int) -> bool:
    f = font.lower()
    return bool(flags & 2) or "italic" in f or "oblique" in f


def line_from_dict(ln: dict) -> dict | None:
    spans = [s for s in ln.get("spans", []) if s.get("text", "") != ""]
    if not spans:
        return None
    # dominant span (by character count) defines size/baseline
    dom = max(spans, key=lambda s: len(s["text"].strip()) or 0)
    size = dom["size"]
    def baseline(sp):  # origin.y is unreliable when a line starts with a superscript; derive it from the bbox
        return sp["bbox"][3] + sp.get("descender", -0.22) * sp["size"]
    base_y = baseline(dom)
    out: list[Span] = []
    for s in spans:
        bits = 0
        if _is_bold(s["font"], s["flags"]):
            bits |= BOLD
        if _is_italic(s["font"], s["flags"]):
            bits |= ITALIC
        smaller = s["size"] <= size * 0.86
        if smaller and baseline(s) < base_y - 0.18 * size:
            bits |= SUP
        elif smaller and baseline(s) > base_y + 0.12 * size:
            bits |= SUB
        out.append(Span(normalize_chars(s["text"]), bits))
    plain = "".join(s.text for s in out)
    if not plain.strip():
        return None
    nchars = sum(len(s["text"].strip()) for s in spans) or 1
    bold_chars = sum(len(s["text"].strip()) for s in spans if _is_bold(s["font"], s["flags"]))
    ital_chars = sum(len(s["text"].strip()) for s in spans if _is_italic(s["font"], s["flags"]))
    return {
        "t": "line", "bbox": [round(v, 2) for v in ln["bbox"]],
        "spans": [s.to_json() for s in out], "raw": plain,
        "size": round(size, 2), "bold": bold_chars / nchars >= 0.8, "italic": ital_chars / nchars >= 0.8,
        "font": dom["font"], "conf": 1.0, "ocr": False,
    }


# ------------------------------------------------------------------------ column logic

def detect_gutter(lines: list[dict], page_w: float) -> float | None:
    body = [l for l in lines if l.get("t", "line") == "line" and not l.get("zone")]
    if len(body) < 8:
        return None
    x0 = min(l["bbox"][0] for l in body)
    x1 = max(l["bbox"][2] for l in body)
    W = x1 - x0
    if W < page_w * 0.4:
        return None
    narrow = [l for l in body if (l["bbox"][2] - l["bbox"][0]) < 0.62 * W and len(l["raw"].strip()) > 2]
    if len(narrow) < 8:
        return None
    lo, hi = int(x0 + 0.30 * W), int(x0 + 0.70 * W)
    allow = max(1, int(0.08 * len(narrow)))
    ok = []
    for x in range(lo, hi + 1):
        c = sum(1 for l in narrow if l["bbox"][0] + 0.5 < x < l["bbox"][2] - 0.5)
        ok.append(c <= allow)
    gaps, start = [], None
    for i, flag in enumerate(ok + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            gaps.append((lo + start, lo + i - 1))
            start = None
    gaps = [g for g in gaps if g[1] - g[0] >= 7]
    if not gaps:
        return None
    g0, g1 = max(gaps, key=lambda g: g[1] - g[0])
    g = (g0 + g1) / 2
    left = [l for l in narrow if l["bbox"][2] <= g + 1]
    right = [l for l in narrow if l["bbox"][0] >= g - 1]
    if len(left) < 3 or len(right) < 3:
        return None
    if (len(left) + len(right)) < 0.85 * len(narrow):
        return None
    return g


def classify_col(bbox, g: float | None) -> str:
    if g is None:
        return "F"
    x0, x1 = bbox[0], bbox[2]
    if x0 < g - 3 and x1 > g + 3:
        return "F"
    return "L" if (x0 + x1) / 2 < g else "R"


def order_items(items: list[dict], g: float | None) -> list[dict]:
    """Reading order: full-width items separate regions; inside a region left column then right."""
    def key(it):
        b = it["bbox"]
        return (b[1], b[0])

    def rowsort(seq):
        seq = sorted(seq, key=key)
        out, row = [], []
        for it in seq:
            if row:
                ref = row[0]["bbox"]
                h = max(ref[3] - ref[1], 1)
                if abs((it["bbox"][1] + it["bbox"][3]) / 2 - (ref[1] + ref[3]) / 2) <= 0.35 * h:
                    row.append(it)
                    continue
                out.extend(sorted(row, key=lambda r: r["bbox"][0]))
                row = []
            row.append(it)
        out.extend(sorted(row, key=lambda r: r["bbox"][0]))
        return out

    top = [i for i in items if i.get("zone") == "top"]
    bottom = [i for i in items if i.get("zone") == "bottom"]
    body = [i for i in items if not i.get("zone")]
    ordered: list[dict] = []
    region_l: list[dict] = []
    region_r: list[dict] = []

    def flush():
        ordered.extend(rowsort(region_l))
        ordered.extend(rowsort(region_r))
        region_l.clear()
        region_r.clear()

    for it in sorted(body, key=key):
        it["col"] = classify_col(it["bbox"], g)
        if it["col"] == "F":
            flush()
            ordered.append(it)
        elif it["col"] == "L":
            region_l.append(it)
        else:
            region_r.append(it)
    flush()
    if g is None:
        ordered = rowsort(body)
        for it in ordered:
            it["col"] = "F"
    for it in top + bottom:
        it["col"] = "F"
    return rowsort(top) + ordered + rowsort(bottom)


# --------------------------------------------------------------------------- tables

def _edges(vals: list[float], tol: float = 2.0) -> list[float]:
    vals = sorted(vals)
    out: list[list[float]] = []
    for v in vals:
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(c) / len(c) for c in out]


def _idx(edges: list[float], v: float) -> int:
    return min(range(len(edges)), key=lambda i: abs(edges[i] - v))


def _cell_md(page, rect, vocab=None) -> tuple[str, str, list]:
    d = page.get_text("dict", clip=pymupdf.Rect(rect))
    lines = []
    for b in d.get("blocks", []):
        for ln in b.get("lines", []):
            r = line_from_dict(ln)
            if r:
                lines.append([Span.from_json(s) for s in r["spans"]])
    if not lines:
        return "", "", []
    joined = join_span_lines(lines, vocab)
    raw = " ".join("".join(sp.text for sp in l) for l in lines)
    return (spans_to_md(joined).replace("|", "\\|"), re.sub(r"\s+", " ", raw).strip(),
            [sp.to_json() for sp in joined])


def build_table(page, tbl, strategy: str) -> dict | None:
    cells = []
    for row in tbl.rows:
        for c in row.cells:
            if c is not None:
                cells.append(tuple(c))
    if len(cells) < 4:
        return None
    xs = _edges([c[0] for c in cells] + [c[2] for c in cells])
    ys = _edges([c[1] for c in cells] + [c[3] for c in cells])
    ncols, nrows = len(xs) - 1, len(ys) - 1
    if ncols < 2 or nrows < 2:
        return None
    grid, bold_rows = [], {}
    for c in cells:
        c0, c1 = _idx(xs, c[0]), _idx(xs, c[2])
        r0, r1 = _idx(ys, c[1]), _idx(ys, c[3])
        if c1 <= c0 or r1 <= r0:
            continue
        md, raw, sp = _cell_md(page, c)
        grid.append({"r": r0, "c": c0, "rs": r1 - r0, "cs": c1 - c0, "md": md, "raw": raw, "spans": sp})
        d = page.get_text("dict", clip=pymupdf.Rect(c))
        nb = nt = 0
        for b in d.get("blocks", []):
            for ln in b.get("lines", []):
                for s in ln["spans"]:
                    n = len(s["text"].strip())
                    nt += n
                    nb += n if _is_bold(s["font"], s["flags"]) else 0
        bold_rows.setdefault(r0, []).append((nb, nt))
    grid.sort(key=lambda g: (g["r"], g["c"]))
    occupied = {(g["r"] + dr, g["c"] + dc) for g in grid for dr in range(g["rs"]) for dc in range(g["cs"])}
    holes = nrows * ncols - len(occupied)
    header_rows = 0
    for r in range(min(nrows, 3)):
        stats = bold_rows.get(r, [])
        tb, tt = sum(a for a, _ in stats), sum(b for _, b in stats)
        if tt and tb / tt >= 0.7:
            header_rows += 1
        else:
            break
    if header_rows == 0:
        header_rows = 1
        header_inferred = True
    else:
        header_inferred = False
    complex_ = any(g["rs"] > 1 or g["cs"] > 1 for g in grid) or holes > 0 or header_rows > 1
    nonempty = sum(1 for g in grid if g["md"])
    empty_ratio = 1 - nonempty / max(len(grid), 1)
    conf = 0.92 if strategy == "lines" else 0.62
    if empty_ratio > 0.5:
        conf -= 0.25
    if holes:
        conf -= 0.1
    raw_text = page.get_text("text", clip=pymupdf.Rect(tbl.bbox))
    return {
        "t": "table", "bbox": [round(v, 2) for v in tbl.bbox], "nrows": nrows, "ncols": ncols,
        "cells": grid, "complex": complex_, "header_rows": header_rows, "header_inferred": header_inferred,
        "conf": round(conf, 2), "strategy": strategy, "holes": holes,
        "raw_nums": numeric.tokens(normalize_chars(raw_text)),
    }


def find_tables(page, lines: list[dict]) -> list[dict]:
    out: list[dict] = []
    try:
        found = page.find_tables()
    except Exception:
        return out
    for t in found.tables:
        tb = build_table(page, t, "lines")
        if tb and not _is_box_like(tb):
            out.append(tb)
    # fallback: booktabs-style tables without vertical rules, located via "Table N" captions
    for ln in lines:
        if not TABLE_CAPTION_RE.match(ln["raw"]) or ln.get("zone"):
            continue
        b = ln["bbox"]
        if any(abs(t["bbox"][1] - b[3]) < 60 or (t["bbox"][1] < b[3] < t["bbox"][3]) for t in out):
            continue
        # region below the caption down to the next large vertical gap
        below = sorted((l for l in lines if l["bbox"][1] >= b[3] - 1 and not l.get("zone")
                        and abs(l["bbox"][0] - b[0]) < page.rect.width * 0.5), key=lambda l: l["bbox"][1])
        y_end, prev = b[3], b[3]
        for l in below:
            if l["bbox"][1] - prev > 2.6 * (l["bbox"][3] - l["bbox"][1]):
                break
            y_end = max(y_end, l["bbox"][3])
            prev = l["bbox"][3]
        if y_end - b[3] < 25:
            continue
        clip = pymupdf.Rect(page.rect.x0 + 20, b[3] - 1, page.rect.x1 - 20, y_end + 2)
        try:
            found = page.find_tables(clip=clip, strategy="text")
        except Exception:
            continue
        for t in found.tables:
            tb = build_table(page, t, "text")
            if tb and tb["ncols"] >= 2 and tb["nrows"] >= 3:
                out.append(tb)
                break
    return out


def _is_box_like(tb: dict) -> bool:
    return tb["nrows"] * tb["ncols"] <= 2 or sum(1 for c in tb["cells"] if c["md"]) <= 1


# -------------------------------------------------------------------------- figures

def _rect_union(rects):
    r = pymupdf.Rect(rects[0])
    for x in rects[1:]:
        r |= pymupdf.Rect(x)
    return r


def find_figures(doc, page, pno: int, lines: list[dict], tables: list[dict], opts: LayoutOptions,
                 img_dir: Path, boxes: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (figure items, remaining lines). Lines inside a figure become its labels."""
    H, W = page.rect.height, page.rect.width
    cands: list[tuple[pymupdf.Rect, str, int | None]] = []
    table_rects = [pymupdf.Rect(t["bbox"]) for t in tables]
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        infos = []
    for info in infos:
        r = pymupdf.Rect(info["bbox"])
        xref = info.get("xref") or 0
        if r.width < 36 or r.height < 36 or xref in opts.skip_xrefs:
            continue
        if r.width * r.height >= 0.85 * W * H:
            continue  # page-sized background / scanned page: handled elsewhere
        if r.y1 < H * ZONE_FRAC or r.y0 > H * (1 - ZONE_FRAC):
            continue
        if any(r.intersects(t) and (r & t).get_area() > 0.5 * r.get_area() for t in table_rects):
            continue
        cands.append((r, "raster", xref))
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    vec = []
    for d in drawings:
        r = d["rect"]
        if (r.width < 2.5 and r.height > 30) or (r.height < 2.5 and r.width > 0.55 * W):
            continue  # page-wide rules (header underline etc.)
        if any(r.intersects(t) for t in table_rects):
            continue
        if any(pymupdf.Rect(b["bbox"]).contains(r) for b in boxes):
            continue
        if r.width * r.height >= 0.8 * W * H:
            continue
        vec.append(d)
    if vec:
        try:
            clusters = page.cluster_drawings(drawings=vec, x_tolerance=15, y_tolerance=15)
        except Exception:
            clusters = []
        for cr in clusters:
            n = sum(1 for d in vec if cr.contains(d["rect"] + (-1, -1, 1, 1)) or cr.intersects(d["rect"]))
            area = cr.width * cr.height
            if cr.width < 60 or cr.height < 40:
                continue
            if cr.y1 < H * ZONE_FRAC or cr.y0 > H * (1 - ZONE_FRAC):
                continue
            if n >= 6 or (area >= 120 * 120 and n >= 2):
                cands.append((cr, "vector", None))
    # merge overlapping candidates
    merged: list[dict] = []
    for r, kind, xref in sorted(cands, key=lambda c: (c[0].y0, c[0].x0)):
        for m in merged:
            if (m["rect"] + (-4, -4, 4, 4)).intersects(r):
                m["rect"] |= r
                m["kinds"].add(kind)
                if xref:
                    m["xrefs"].append(xref)
                break
        else:
            merged.append({"rect": pymupdf.Rect(r), "kinds": {kind}, "xrefs": [xref] if xref else []})
    figs, used = [], set()
    img_dir.mkdir(parents=True, exist_ok=True)
    for k, m in enumerate(merged, 1):
        r = m["rect"] + (-2, -2, 2, 2)
        r &= page.rect
        inside = [i for i, l in enumerate(lines)
                  if r.contains(pymupdf.Point((l["bbox"][0] + l["bbox"][2]) / 2, (l["bbox"][1] + l["bbox"][3]) / 2))
                  and not FIG_CAPTION_RE.match(l["raw"]) and not TABLE_CAPTION_RE.match(l["raw"])]
        # a mostly-text region (e.g. a boxed paragraph) is not a figure
        if len(inside) > 60 and m["kinds"] == {"vector"}:
            continue
        used.update(inside)
        labels = [lines[i]["raw"].strip() for i in sorted(inside, key=lambda i: (round(lines[i]["bbox"][1]), lines[i]["bbox"][0]))]
        path, how = _save_figure(doc, page, pno, k, r, m, labels, opts, img_dir)
        figs.append({"t": "figure", "bbox": [round(v, 2) for v in r], "tmp": path, "how": how,
                     "kind": "+".join(sorted(m["kinds"])), "labels": labels, "conf": 0.9})
    rest = [l for i, l in enumerate(lines) if i not in used]
    return figs, rest


def _save_figure(doc, page, pno, k, rect, m, labels, opts, img_dir: Path) -> tuple[str, str]:
    base = img_dir / f"p{pno + 1:05d}_{k:02d}"
    if m["kinds"] == {"raster"} and len(m["xrefs"]) == 1 and not labels:
        try:
            info = doc.extract_image(m["xrefs"][0])
            ext = info.get("ext", "")
            if (ext in ("png", "jpeg", "jpg") and not info.get("smask") and info.get("image")
                    and abs((info["width"] / max(info["height"], 1)) / (rect.width / max(rect.height, 1)) - 1) < 0.06):
                out = base.with_suffix("." + ("jpg" if ext == "jpeg" else ext))
                out.write_bytes(info["image"])
                return out.name, "original-bytes"
        except Exception:
            pass
    pix = page.get_pixmap(clip=rect, dpi=opts.fig_dpi)
    out = base.with_suffix(".png")
    pix.save(str(out))
    return out.name, "region-render"


# --------------------------------------------------------------------------- callout boxes

def find_boxes(page, lines: list[dict]) -> list[dict]:
    boxes = []
    W, H = page.rect.width, page.rect.height
    try:
        drawings = page.get_drawings()
    except Exception:
        return boxes
    for d in drawings:
        fill = d.get("fill")
        r = d["rect"]
        if not fill or r.width < 80 or r.height < 30 or r.get_area() > 0.7 * W * H:
            continue
        if all(c > 0.97 for c in fill):
            continue  # white
        n = sum(1 for l in lines if r.contains(pymupdf.Point((l["bbox"][0] + l["bbox"][2]) / 2,
                                                              (l["bbox"][1] + l["bbox"][3]) / 2)))
        if n >= 2:
            boxes.append({"bbox": [round(v, 2) for v in r], "fill": [round(c, 2) for c in fill], "nlines": n})
    # keep outermost distinct boxes only
    boxes.sort(key=lambda b: -(b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1]))
    kept: list[dict] = []
    for b in boxes:
        br = pymupdf.Rect(b["bbox"])
        if any(pymupdf.Rect(k["bbox"]).contains(br) for k in kept):
            continue
        kept.append(b)
    return kept


# ----------------------------------------------------------------------------- page

def page_record(doc, pno: int, opts: LayoutOptions, img_dir: Path,
                clip_y: tuple[float | None, float | None] = (None, None)) -> dict:
    page = doc[pno]
    W, H = page.rect.width, page.rect.height
    rec: dict = {"page": pno + 1, "w": round(W, 2), "h": round(H, 2), "items": [], "flags": [],
                 "ocr": False, "gutter": None, "boxes": []}
    d = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    lines = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            r = line_from_dict(ln)
            if r:
                lines.append(r)
    y_lo, y_hi = clip_y
    if y_lo is not None:
        lines = [l for l in lines if l["bbox"][1] >= y_lo - 2]
    if y_hi is not None:
        lines = [l for l in lines if l["bbox"][1] < y_hi - 2]
    for l in lines:
        yc = (l["bbox"][1] + l["bbox"][3]) / 2
        l["zone"] = "top" if yc < H * ZONE_FRAC else ("bottom" if yc > H * (1 - ZONE_FRAC) else None)
        if has_encoding_artifacts(l["raw"]):
            rec["flags"].append("encoding_artifact")
    native_chars = sum(len(l["raw"].strip()) for l in lines if not l["zone"])
    rec["native_chars"] = native_chars

    # scanned / image-only page → OCR fallback (never for text-native pages)
    page_img = [i for i in (page.get_image_info() or [])
                if pymupdf.Rect(i["bbox"]).get_area() >= 0.5 * W * H]
    if native_chars < opts.min_native_chars and page_img:
        rec["scanned"] = True
        return _scanned_page(doc, page, rec, opts, img_dir, y_lo, y_hi)

    boxes = find_boxes(page, lines)
    tables = find_tables(page, lines)
    # remove table text from flow (but keep caption-like lines)
    trects = [pymupdf.Rect(t["bbox"]) for t in tables]
    flow = []
    for l in lines:
        c = pymupdf.Point((l["bbox"][0] + l["bbox"][2]) / 2, (l["bbox"][1] + l["bbox"][3]) / 2)
        if any(r.contains(c) for r in trects) and not CAPTION_RE.match(l["raw"]):
            continue
        flow.append(l)
    figs, flow = find_figures(doc, page, pno, flow, tables, opts, img_dir, boxes)
    for l in flow:
        c = pymupdf.Point((l["bbox"][0] + l["bbox"][2]) / 2, (l["bbox"][1] + l["bbox"][3]) / 2)
        for bi, b in enumerate(boxes):
            if pymupdf.Rect(b["bbox"]).contains(c):
                l["box"] = bi
                break
    items = flow + tables + figs
    for t in tables + figs:
        t["zone"] = None
    g = detect_gutter(flow + tables + figs, W)
    rec["gutter"] = round(g, 2) if g else None
    rec["boxes"] = boxes
    ordered = order_items(items, g)
    for n, it in enumerate(ordered):
        it["order"] = n
        it["id"] = f"p{pno + 1}-{n:03d}"
    rec["items"] = ordered
    rec["nums"] = _page_numbers(page, y_lo, y_hi)
    return rec


def _page_numbers(page, y_lo, y_hi) -> list:
    """Independent numeric tokens from word-level extraction (for numeric QA).

    Words are joined into one stream so that comparator prefixes ("≥ 95") tokenise exactly
    like they do in the Markdown."""
    import bisect
    words = page.get_text("words")
    words.sort(key=lambda w: (w[5], w[6], w[7]))
    keep = []
    for w in words:
        if y_lo is not None and w[1] < y_lo - 2:
            continue
        if y_hi is not None and w[1] >= y_hi - 2:
            continue
        keep.append(w)
    text, starts = "", []
    for w in keep:
        starts.append(len(text))
        text += normalize_chars(w[4]) + " "
    out = []
    for m in numeric.NUM_RE.finditer(text):
        tok = re.sub(r"\s+", "", m.group(0)).rstrip(".,")
        if not tok:
            continue
        wi = max(bisect.bisect_right(starts, m.start()) - 1, 0)
        w = keep[wi]
        ctx = text[m.end(): m.end() + 14].strip()
        out.append([round(w[0], 1), round(w[1], 1), round(w[2], 1), round(w[3], 1), tok, ctx])
    return out


def _scanned_page(doc, page, rec, opts, img_dir, y_lo, y_hi) -> dict:
    W, H = page.rect.width, page.rect.height
    img_dir.mkdir(parents=True, exist_ok=True)
    png = img_dir / f"p{page.number + 1:05d}_scan.png"
    pix = page.get_pixmap(dpi=300)
    pix.save(str(png))
    scale = 300 / 72.0
    items: list[dict] = []
    if opts.ocr_enabled and ocrmod.available():
        try:
            words = ocrmod.words_to_lines(ocrmod.ocr_png(png, opts.ocr_lang), scale)
            for ln in words:
                items.append({
                    "t": "line", "bbox": [round(v, 2) for v in ln["bbox"]],
                    "spans": [[normalize_chars(ln["text"]), 0]], "raw": normalize_chars(ln["text"]),
                    "size": round(ln["size"], 2), "bold": False, "italic": False, "font": "ocr",
                    "conf": round(ln["conf"], 3), "min_conf": round(ln["min_conf"], 3), "ocr": True,
                    "zone": "top" if ln["bbox"][3] < H * ZONE_FRAC else ("bottom" if ln["bbox"][1] > H * (1 - ZONE_FRAC) else None),
                })
            rec["ocr"] = True
            g = detect_gutter(items, W)
            rec["gutter"] = g
            items = order_items(items, g)
            for n, it in enumerate(items):
                it["order"] = n
                it["id"] = f"p{page.number + 1}-{n:03d}"
            rec["items"] = items
            rec["nums"] = [[*ln["bbox"], t, ""] for ln in items for t in numeric.tokens(ln["raw"])]
            png.unlink(missing_ok=True)
            return rec
        except Exception as e:  # OCR failure must never be silent
            rec["flags"].append(f"ocr_failed:{e}")
    else:
        rec["flags"].append("ocr_unavailable")
    # keep the source image so no content is lost; mark text as unreadable
    rec["items"] = [{
        "t": "figure", "bbox": [0, 0, round(W, 2), round(H, 2)], "tmp": png.name, "how": "page-render",
        "kind": "scanned_page", "labels": [], "conf": 0.0, "zone": None, "col": "F", "order": 0,
        "id": f"p{page.number + 1}-000"}]
    rec["nums"] = []
    return rec
