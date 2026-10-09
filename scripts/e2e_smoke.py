#!/usr/bin/env python3
"""Browser smoke test of manual acceptance flows A + B (needs `pip install playwright` and a Chromium).

    python scripts/make_demo_pdf.py /tmp/demo/book.pdf
    python -m textbook2md serve --port 8799 --static app/dist &
    python scripts/e2e_smoke.py http://127.0.0.1:8799 /tmp/demo --chromium /opt/pw-browsers/chromium-1194/chrome-linux/chrome
"""
import argparse
import sys
import time

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("url")
ap.add_argument("pdf_dir", help="folder containing book.pdf")
ap.add_argument("--chromium", default=None)
ap.add_argument("--out", default=None, help="project output folder (default: <pdf_dir>/kb_<time>)")
a = ap.parse_args()

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=a.chromium, args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1500, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(a.url)
    # Flow A: import → chapters detected → convert → report
    pg.click("text=New book project")
    pg.click("text=Select PDF…")
    pg.fill(".pathbar input", a.pdf_dir)
    pg.press(".pathbar input", "Enter")
    pg.click("text=book.pdf")
    out = a.out or f"{a.pdf_dir}/kb_{int(time.time())}"
    pg.fill("input[placeholder^='default']", out)
    pg.click("text=Create project")
    pg.click("text=Convert all")
    pg.wait_for_selector("text=Finished", timeout=120_000)
    # Flow B: search → open section → open source page
    pg.click("nav >> text=Search")
    pg.fill("input.big", "ferrous sulfate")
    pg.wait_for_selector(".result")
    pg.click("text=Open source page 2")
    pg.wait_for_selector(".pagewrap img")
    assert pg.locator(".bbox").count() >= 1, "source page opened without a highlighted block"
    b.close()
    if errors:
        print("JS errors:", errors)
        sys.exit(1)
print("OK")
