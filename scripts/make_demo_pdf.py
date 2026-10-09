#!/usr/bin/env python3
"""Generate the synthetic demo textbook used by the tests (two-column chapters, tables, figures, scanned page).

    python scripts/make_demo_pdf.py demo.pdf
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine" / "tests"))
import fixtures  # noqa: E402

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "demo_textbook.pdf"
    fixtures.make_book(out)
    print("wrote", out)
