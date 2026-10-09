# Architecture

## Product understanding
One textbook = one **project folder**. The app converts the PDF chapter‑by‑chapter into canonical English Markdown whose
every block can be traced back to a PDF page and rectangle. Priorities, in order: source fidelity → completeness →
reading order → numeric/table accuracy → structure → searchability → RAG friendliness → readability → polish.
Hard rules: never modify the PDF, never summarise/paraphrase/translate/correct, AI is optional and never a text source,
generated material (indexes, TOC, tags) is labelled and kept apart from source‑derived text.

## Processes
```
React UI ──fetch──▶ server.py (ThreadingHTTPServer, 127.0.0.1, token + Host check)
                       ├─ Jobs → pipeline.convert_many → convert_chapter (thread, pause/cancel at page boundaries)
                       ├─ service/project (create, inspect, chapters, state)  ├─ indexer (search, rag, indexes)
                       └─ export, render_page (PyMuPDF → PNG), ai.AIClient (OpenAI/Gemini), ocr (tesseract CLI)
```

## Data model
* `book.yaml` — title, edition, book_id, source file + **SHA‑256**, `chapters[]` (key, number, title, start, end, part,
  confidence, source, start_y/end_y for shared pages). User‑editable; edits reset only the changed chapters.
* `.t2md/state.json` — per chapter: status, pages_done/total, stage, counts, md_sha256, error, interrupted.
* `.t2md/pages/<ch>/pNNNNN.json` — **page record** (checkpoint): lines with spans (text, bits bold/italic/sup/sub), bbox,
  size, zone, column, raw text; tables (cells with spans, rowspan/colspan); figures (tmp image, labels); boxes; word‑stream
  numeric tokens; OCR confidences. `raw` is never discarded → "compare with source".
* `.t2md/blocks/<ch>.json` — final blocks (`md`, `raw`, `clean`, pages, bboxes, heading_path, section_id, flags) used by search/RAG.
* `source_maps/<ch>.json`, `manifest.json`, `rag/chunks.jsonl`, `reports/*`, `metadata/*` — the portable deliverables.

## Pipeline (15 passes) → code
| Pass | Where |
|---|---|
| 1 Source inspection | `chapters.detect_metadata/profile_book`, `layout.page_record` (native‑text vs scanned test) |
| 2 Layout analysis | `layout.assign_zones` (running header/footer candidates), `detect_gutter`, `find_boxes` |
| 3 Reading order | `layout.order_items` (full‑width items split regions; L→R per region), `reorder_page` after un‑zoning |
| 4 Text extraction | `layout.line_from_dict` (spans; super/subscript from bbox‑derived baseline), `merge_marker_fragments`, OCR fallback |
| 5 Cleanup | `textclean` (ligatures, dehyphenation with `Vocab` evidence, dash/slash joins), `blocks.remove_running`, `finish_para` |
| 6 Headings | `blocks.heading_conf`, `assign_levels` (+ conversion memory, optional AI) |
| 7 Lists / callouts | `blocks.segment`, `make_list_block`, `group_callouts` |
| 8 Tables | `layout.find_tables/build_table`, `blocks.render_table`, `merge_continued_tables` |
| 9 Figures | `layout.find_figures/_save_figure`, caption association in `blocks.build_blocks` |
| 10 Citations/references | reference mode in `build_blocks`, `crossref`, citation QA |
| 11 Markdown assembly | `assemble.build_markdown` (front matter, section IDs, page markers, TOC, source map) |
| 12 Fidelity QA | `qa.run_qa` (completeness, char‑loss vs independent word stream, structure, tables, figures) |
| 13 Numeric QA | `numeric.compare` over page word stream vs Markdown tokens |
| 14 Searchability QA | ligature/PUA/spaced‑letter/hyphen leftovers |
| 15 Index / RAG | `indexer.index_chapter`, `build_rag`, `build_semantic_indexes`, book index, README |

Passes 1–4 + 8–9 run per page and are checkpointed; passes 5–15 are deterministic and fast, so a resumed chapter
re‑assembles from the cache. Re‑running the same source yields byte‑identical Markdown and identical block/section IDs.

## Design decisions worth knowing
* **Evidence‑based dehyphenation:** join only when the joined word appears elsewhere in the book un‑hyphenated (or by
  suffix heuristics); real compounds (`long-term`) are kept; unknowns are kept + flagged.
* **Zone lines are not trusted blindly:** header/footer removal needs a repeated pattern or a bare page number; any other
  line in the margin zone is returned to the reading flow and the page is re‑ordered.
* **Independent numeric source:** the numeric fingerprint comes from PyMuPDF's *word* extraction, not from the line
  model used to build the Markdown, so lost or altered lines are detected rather than inherited.
* **Provenance first:** blocks keep `pages[]` (a paragraph can span pages; an inline marker records the crossing) and `bboxes[]`.
* **AI containment:** prompts contain only headings or one ambiguous line with 100‑char neighbours; model output is
  validated (headings must exist verbatim) and never written into chapter text.
