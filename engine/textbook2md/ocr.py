"""Optional OCR via the `tesseract` CLI. Used ONLY for pages/blocks without native text."""
from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

LOW_CONF = 0.80


def available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_png(png_path: str | Path, lang: str = "eng", psm: int = 3) -> list[dict]:
    """Return word dicts: text, conf (0..1), bbox in image pixels, line key."""
    if not available():
        raise RuntimeError("tesseract not installed")
    res = subprocess.run(
        ["tesseract", str(png_path), "stdout", "-l", lang, "--psm", str(psm), "tsv"],
        capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "tesseract failed")
    words = []
    for row in csv.DictReader(io.StringIO(res.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        txt = (row.get("text") or "").strip()
        if not txt or row.get("level") != "5":
            continue
        try:
            conf = float(row["conf"]) / 100.0
        except (ValueError, KeyError):
            continue
        l, t, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        words.append({"text": txt, "conf": max(conf, 0.0), "bbox": [l, t, l + w, t + h],
                      "line": (row["block_num"], row["par_num"], row["line_num"])})
    return words


def words_to_lines(words: list[dict], scale: float) -> list[dict]:
    """Group OCR words into lines in PDF-point coordinates (scale = px per pt)."""
    groups: dict[tuple, list[dict]] = {}
    for w in words:
        groups.setdefault(w["line"], []).append(w)
    lines = []
    for ws in groups.values():
        ws.sort(key=lambda w: w["bbox"][0])
        x0 = min(w["bbox"][0] for w in ws) / scale
        y0 = min(w["bbox"][1] for w in ws) / scale
        x1 = max(w["bbox"][2] for w in ws) / scale
        y1 = max(w["bbox"][3] for w in ws) / scale
        lines.append({
            "bbox": [x0, y0, x1, y1],
            "text": " ".join(w["text"] for w in ws),
            "conf": sum(w["conf"] for w in ws) / len(ws),
            "min_conf": min(w["conf"] for w in ws),
            "size": (y1 - y0) * 0.85,
        })
    lines.sort(key=lambda l: (l["bbox"][1], l["bbox"][0]))
    return lines
