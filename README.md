# textbook2md — PDF textbook → structured, source‑traceable Markdown knowledge base

A **local‑first desktop application** that turns a textbook PDF (e.g. *Nelson Textbook of Pediatrics*) into a
portable folder of clean **English Markdown** — one file per chapter — with figures, tables, references, stable section
IDs, a page‑level **source map**, full‑text search, RAG chunks, QA reports and a side‑by‑side **PDF ⇄ Markdown review**
screen.

* **No rewriting.** Text comes from the PDF only. No summarising, paraphrasing, translating or "fixing" — AI (optional)
  never produces textbook text.
* **Traceable.** Every block maps to *PDF page + bounding box + Markdown lines + heading path*
  (`source_maps/ch_xxx.json`). Search results jump to the original page.
* **Portable.** The output folder is plain Markdown/JSON/PNG. If this app disappears, the knowledge base still works in
  Obsidian, VS Code, GitHub or any RAG stack.
* **Private.** The original PDF is opened read‑only and never modified. Everything deterministic runs locally.

> **Hướng dẫn nhanh (Tiếng Việt):** cài Python ≥ 3.10 → `pip install -e ".[test]"` → `cd app && npm install && npm run build`
> → `python -m textbook2md serve --static app/dist` rồi mở địa chỉ in ra (hoặc dùng bản desktop Tauri).
> Chọn **New book project** → chọn file PDF → kiểm tra danh sách chương → **Convert all** → dùng **Search** / **Review**
> để đối chiếu Markdown với trang PDF gốc. Kết quả là thư mục Markdown chuẩn, có thể dùng làm nguồn cho bước dịch tiếng Việt sau này.

## Contents
[What it does](#what-it-does) · [Tech stack & architecture](#tech-stack--architecture) · [Install](#installation) ·
[Development](#development) · [Build / package](#build--package) · [AI provider](#ai-provider-setup-optional) ·
[OCR](#ocr-setup-optional) · [Output structure](#output-project-structure) · [Markdown conventions](#markdown-conventions) ·
[Source mapping](#source-mapping) · [Search](#search) · [Export](#export) · [QA](#quality-assurance) ·
[Limitations](#known-limitations) · [Tests](#tests) · [Roadmap](#roadmap) · [Architecture doc](docs/ARCHITECTURE.md)

## What it does

```
PDF → inspect → chapters (outline / TOC / layout) → per page: layout, columns, reading order, text, tables, figures
    → per chapter: cleanup, headings, lists, callouts, captions, references, cross‑refs → Markdown + source map
    → QA (completeness, structure, numbers, tables, figures, citations, searchability) → report
    → FTS5 index, RAG chunks, book index, generated navigation indexes
```

| Capability | How |
|---|---|
| Chapter detection | PDF outline (strongest) → printed TOC → "Chapter N" layout pattern; confidence + conflict flags; manual editor; chapters sharing a page are split by outline position |
| Two‑column reading order | gutter detection on line geometry; full‑width items split regions; left → right per region; continuation across columns/pages |
| Cleanup | ligatures, soft/line‑end hyphenation (joined only with evidence from the book's own vocabulary), line joining, running header/footer + page‑number removal |
| Headings | font size/weight clustering → H1–H4, per‑heading confidence, ambiguous ones kept as text and flagged, book‑level style memory; optional AI tie‑break |
| Lists / callouts | bullets (even when drawn in a symbol font), nested/numbered lists, drawn boxes (KEY POINTS …) → blockquote callouts |
| Tables | ruled tables via PyMuPDF; caption‑anchored text strategy for rule‑less tables; GFM when simple, **HTML with row/colspan** when merged; huge tables → `tables/ch_x/*.md`; page‑spanning tables merged |
| Figures | original image bytes when possible, region render for vector figures; captions kept whole; in‑figure native text kept as `<details>` labels |
| Citations / refs | superscripts → `<sup>`; reference list kept verbatim with `<a id="ref-N">` anchors; clickable URLs only |
| Footnotes / formulas | `[^id]` footnotes; stand‑alone equations → `$$ LaTeX $$` |
| Cross‑references | `Chapter 201`, `Table 123.2`, `Fig. 123.4` linked when the target is converted, otherwise recorded in `metadata/unresolved_references.json` and auto‑linked later |
| Scanned pages | OCR **only** for pages without native text (Tesseract); low‑confidence words flagged, never guessed; without OCR the page image is kept and marked unreadable |
| Resume | page‑level checkpoints + chapter state; pause / cancel / resume; crash → reopen → continue |

## Tech stack & architecture

| Layer | Choice | Why |
|---|---|---|
| Desktop shell | **Tauri 2** (Rust) | small, native webview, spawns/stops the engine sidecar |
| UI | **React + TypeScript + Vite** | document‑focused UI, dark mode, no UI framework |
| Engine | **Python 3.10+** (`engine/textbook2md`) | PyMuPDF for text/layout/tables/figures; stdlib `http.server` sidecar; SQLite **FTS5** |
| Storage | plain files in the project folder (+ rebuildable `.t2md/` cache, SQLite index) | portability |

Deviations from the suggested stack (and why): `pdfplumber` is not needed — PyMuPDF's span/word/drawing/table APIs cover
layout, tables and figures with one dependency; the sidecar uses the standard library instead of FastAPI (zero extra
runtime deps, tiny bundle); the PDF pane is rendered server‑side as PNG (PyMuPDF) instead of embedding pdf.js, which
keeps huge PDFs lazy and lets block bounding boxes be overlaid in exact PDF coordinates.

```
 Tauri window ──http://127.0.0.1:<port>──▶ Python engine (textbook2md.server)
   React UI                                    ├─ pipeline  (passes 1‑15, resumable)
                                               ├─ project   (book.yaml, manifest, state, memory)
                                               ├─ indexer   (FTS5, RAG, indexes)  └─ export
```
The engine binds to `127.0.0.1` only, checks the `Host` header and requires a per‑launch token on every `/api` call.
Full details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Installation

Requirements: Python ≥ 3.10, Node ≥ 18; Rust + Tauri prerequisites only for the desktop shell; Tesseract optional.

```bash
git clone <this repo> && cd Markdown-tai-lieu
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"            # engine (PyMuPDF, PyYAML) + pytest
cd app && npm install && npm run build && cd ..
```

### Run (browser mode — works everywhere)
```bash
python -m textbook2md serve --static app/dist       # prints http://127.0.0.1:<port>
```
Open the printed URL. (For UI development set `T2MD_DEV=1`, run `python -m textbook2md serve --port 8765` and
`cd app && npm run dev`; Vite proxies `/api` to the engine.)

### Run (desktop shell)
```bash
cd src-tauri && cargo tauri dev        # needs: cargo install tauri-cli --version "^2", WebKitGTK (Linux) / Xcode CLT (macOS)
```
The shell starts `python3 -m textbook2md serve --port 0` (override with `T2MD_PYTHON`) and opens a window on it.

### Command line
```bash
python -m textbook2md inspect book.pdf                     # metadata + detected chapters (JSON)
python -m textbook2md new book.pdf --out ~/TextbookKB/nelson22
python -m textbook2md convert ~/TextbookKB/nelson22 --chapters ch_001,ch_002   # or --all, --force
python -m textbook2md search  ~/TextbookKB/nelson22 "ferrous sulfate"
python -m textbook2md export  ~/TextbookKB/nelson22 --zip nelson22.zip [--obsidian] [--include-pdf]
python -m textbook2md export  ~/TextbookKB/nelson22 --rag nelson22_rag.zip
python scripts/make_demo_pdf.py demo.pdf                   # synthetic two‑column demo textbook
```

## Development

```
engine/textbook2md/   layout.py blocks.py textclean.py chapters.py assemble.py qa.py numeric.py crossref.py
                      pipeline.py project.py indexer.py export.py server.py ai.py ocr.py report.py cli.py
engine/tests/         fixtures.py (PDF generator) · lo_fixture.py (LibreOffice‑rendered PDF) · test_*.py
app/                  React + TS UI (src/screens/*)
src-tauri/            Tauri 2 shell (Rust)
scripts/              make_demo_pdf.py · e2e_smoke.py (Playwright) · build_engine.sh
```

## Build / package
```bash
cd app && npm run build                  # type‑checks and builds the UI → app/dist
scripts/build_engine.sh                  # PyInstaller one‑file engine → src-tauri/binaries/textbook2md-engine-<triple>
cd src-tauri && cargo tauri build --config tauri.release.conf.json     # installer incl. the engine sidecar
# dev builds (`cargo build`, `cargo tauri dev`) run the Python sources instead of the sidecar; the placeholder icons in
# src-tauri/icons can be replaced with `cargo tauri icon your.png`.
# Linux build deps: libwebkit2gtk-4.1-dev libgtk-3-dev libsoup-3.0-dev librsvg2-dev libxdo-dev
```

## AI provider setup (optional)
Settings → *AI provider*: choose **OpenAI** or **Gemini**, paste your key, set a model, **Test connection**.
Keys live in `~/.textbook2md/settings.json` (mode 600), are never returned to the UI, never logged, and are sent only to
the selected provider (Gemini via the `x-goog-api-key` header, not the URL). AI is used for just three things and always
with small payloads: (1) resolving *ambiguous* heading candidates (the line + neighbours' first 100 chars), (2) chapter
specialty/tags (title + heading list), (3) classifying **existing heading strings** into navigation indexes (terms not
present in the headings are discarded). *Settings → AI usage* lists every request (provider, purpose, characters, preview).
With AI off, or when a provider fails / is rate‑limited, the deterministic result is used and a `AI_UNAVAILABLE` note is added to the report.

## OCR setup (optional)
Install Tesseract (`brew install tesseract` / `apt install tesseract-ocr`); the app auto‑detects it. OCR runs only for
pages without native text, at 300 dpi; confidence < 0.80 produces an `<!-- UNCERTAIN … -->` marker and an issue
(HIGH when digits are involved). Without Tesseract those pages are kept as page images and flagged `PAGE_UNREADABLE`.

## Output project structure
```
Book_Project/
├── README.md  00_BOOK_INDEX.md  book.yaml  manifest.json
├── chapters/ch_001_title.md …            ← canonical Markdown (YAML front matter + body)
├── assets/images/ch_001/figure_001_01.png
├── tables/ch_001/table_001_04.md          ← only very large tables
├── source_maps/ch_001.json                ← block → page / bbox / lines / heading path / fingerprint
├── indexes/{diseases,drugs,signs_symptoms,topics}.md     ← GENERATED navigation, labelled as such
├── rag/chunks.jsonl
├── reports/ch_001_conversion_report.md
├── metadata/{unresolved_references.json,conversion_memory.json}
└── .t2md/   (page cache, SQLite index, state — rebuildable, excluded from exports)
```

## Markdown conventions
* **Page markers:** `<!-- source_page: 2471 -->` before the first block of each source page, and inline where a
  paragraph crosses a page. Hidden when rendered; the review screen's *Source mode* shows `[Trang gốc: 2471]`.
* **Section IDs:** `## Heading` + `<!-- section_id: nelson22-ch123-clinical-manifestations -->` (stable across re‑runs;
  collisions disambiguated by parent heading). `## Contents` is generated between `<!-- generated:toc -->` markers.
* **Tables:** GFM or HTML (`rowspan`/`colspan`); anchors `<a id="table-2-1">`. **Figures:** `![Figure 2.1](../assets/…)`
  + full caption; `<a id="figure-2-1">`. **Callouts:** `> **KEY POINTS**` blockquotes. **Footnotes:** `[^fn12-3]`.
  **Math:** `$$…$$`. **References:** `<a id="ref-12"></a>12. …` verbatim.
* **Uncertain text:** `<!-- UNCERTAIN: … -->` and `[Unreadable source text]` — never guessed.
* Standard CommonMark/GFM by default; Obsidian callouts + wikilinks only in an *export* option.

## Source mapping
`source_maps/ch_xxx.json` → `blocks[]`: `block_id, type, section_id, heading_path, source_pages, bboxes[{page,bbox}],
md_line_start/end, fingerprint, confidence, flags` and `pages{}` with a SHA‑256 of each page's extracted text, plus the
source PDF SHA‑256 and parser version. The Review screen uses this to highlight the block's rectangle on the PDF page.

## Search
SQLite FTS5 (`.t2md/index.sqlite`, rebuilt with `reindex`). Phrases in quotes, prefix match on the last word, filters for
chapter / content type / specialty. Results show chapter, heading path, highlighted snippet, source page, type and two
actions: **Open section** (Markdown block, PDF follows) and **Open source page** (PDF page with the block highlighted).

## Export
Project **ZIP** (unzip anywhere — links keep working; PDF bundled only on request), **chapter** ZIP, **RAG package**,
optional **Obsidian** flavour (applied to the exported copy only).

## Quality assurance
Every chapter gets a status — `COMPLETED`, `COMPLETED_WITH_WARNINGS`, `REVIEW_REQUIRED`, `FAILED` — from issue severities
(`CRITICAL`, `HIGH` ⇒ review required). Checks: skipped pages, page→Markdown coverage, character‑loss ratio vs.
the independent word stream, heading jumps/H1 count, **numeric fingerprint** (MISSING / CHANGED / UNEXPECTED number;
changed dose‑like values are CRITICAL), per‑table numeric comparison + confidence, figure count/caption association,
citation ↔ reference list, ligature/encoding leftovers, un‑joined hyphenation. Numbers are never auto‑corrected.

## Known limitations
* **Not validated on the real Nelson PDF.** Verified on synthetic fixtures, a LibreOffice‑rendered two‑column PDF with
  automatic hyphenation, and an arbitrary designed PDF (no crash). Expect to tune heuristics on real books — use the
  Review screen and conversion reports; conversion memory carries lessons between chapters.
* **Desktop shell:** `cargo build` of `src-tauri` succeeds and the resulting app starts the Python sidecar and shows the UI
  (verified headless under Xvfb on Linux). Not exercised here: macOS/Windows builds, `cargo tauri build` installers and the
  PyInstaller sidecar (`scripts/build_engine.sh`).
* OCR and the OpenAI/Gemini calls are covered by tests with mocks only (no Tesseract / API keys in CI).
* Layouts not handled: 3+ columns, text wrapped around figures, rotated pages/tables, sidebars spanning columns.
  Rule‑less tables rely on a caption‑anchored text strategy (confidence 0.62 → flagged for review).
* Inline Unicode math is kept as text; only stand‑alone equations become LaTeX. Unnumbered/author‑year reference lists are
  preserved but not cross‑checked against in‑text citations.
* FTS search is lexical (no stemming/semantic search yet).

## Tests
```bash
pytest                      # 90 tests: reading order, dehyphenation, joining, headings, tables, source mapping, numeric
                            # fingerprint, stable IDs, cross‑refs, manifest, export, resume, server API, AI/OCR (mocked),
                            # LibreOffice‑rendered real PDF (skipped if soffice is missing)
cd app && npm run typecheck && npm run build
python scripts/e2e_smoke.py <url> <pdf_dir> --chromium <path>     # browser flows A + B
```

## Roadmap
**Phase 2** Vietnamese translation from the verified Markdown (bilingual files, glossary, terminology memory) ·
**Phase 3** local embeddings, "Ask this textbook", cross‑book search · **Phase 4** study notes, flashcards, MCQs, concept
maps — always kept separate from canonical source Markdown. The block/section IDs and source maps were designed so a
translation layer can align sentence‑by‑sentence with the English source.
