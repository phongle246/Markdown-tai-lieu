"""Search (SQLite FTS5), RAG chunks, book index, README and generated navigation indexes."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from . import APP_VERSION
from .ai import AIClient, AIError
from .models import COMPLETED, COMPLETED_WITH_WARNINGS, NOT_STARTED, REVIEW_REQUIRED
from .numeric import strip_markdown
from .util import atomic_write_text, now_iso, read_json

DONE_STATES = (COMPLETED, COMPLETED_WITH_WARNINGS, REVIEW_REQUIRED)
GENERATED_NOTE = "> **Generated navigation index — not part of the original textbook.**"


def blocks_file(project, key: str) -> Path:
    return project.hidden / "blocks" / f"{key}.json"


def load_chapter_blocks(project, key: str) -> dict | None:
    return read_json(blocks_file(project, key))


# ----------------------------------------------------------------------------- search

def db_path(project) -> Path:
    return project.hidden / "index.sqlite"


def connect(project) -> sqlite3.Connection:
    con = sqlite3.connect(db_path(project))
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS units(
            id INTEGER PRIMARY KEY, chapter TEXT, chapter_no INTEGER, chapter_title TEXT, specialty TEXT,
            section_id TEXT, heading_path TEXT, type TEXT, pages TEXT, page INTEGER, block_id TEXT,
            md_file TEXT, line_start INTEGER, text TEXT);
        CREATE INDEX IF NOT EXISTS units_ch ON units(chapter);
        CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(text, heading_path, chapter_title,
            tokenize='unicode61 remove_diacritics 2');
    """)
    return con


CONTENT_TYPE = {"paragraph": "text", "heading": "heading", "list": "list", "table": "table", "figure": "figure",
                "callout": "callout", "reference": "reference", "formula": "formula", "footnote": "footnote",
                "caption": "figure", "unreadable": "text"}


def index_chapter(project, key: str) -> int:
    data = load_chapter_blocks(project, key)
    con = connect(project)
    with con:
        ids = [r[0] for r in con.execute("SELECT id FROM units WHERE chapter=?", (key,))]
        con.executemany("DELETE FROM units_fts WHERE rowid=?", [(i,) for i in ids])
        con.execute("DELETE FROM units WHERE chapter=?", (key,))
        if not data:
            return 0
        ch = data["chapter"]
        n = 0
        for b in data["blocks"]:
            text = b.get("clean") or ""
            if b["type"] == "table":
                text = (b["extra"].get("caption", "") + " " + text).strip()
            elif b["type"] == "figure":
                text = (text + " " + " ".join(b["extra"].get("labels", []))).strip()
            if not text.strip():
                continue
            cur = con.execute(
                "INSERT INTO units(chapter,chapter_no,chapter_title,specialty,section_id,heading_path,type,pages,page,block_id,md_file,line_start,text)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, ch.get("number"), ch["title"], ", ".join(data["meta"].get("specialty", [])), b["section_id"],
                 " › ".join(b["heading_path"]), CONTENT_TYPE.get(b["type"], "text"), json.dumps(b["pages"]),
                 b["pages"][0] if b["pages"] else None, b["id"], data["md_file"], b.get("md_line_start"), text))
            con.execute("INSERT INTO units_fts(rowid,text,heading_path,chapter_title) VALUES(?,?,?,?)",
                        (cur.lastrowid, text, " ".join(b["heading_path"]), ch["title"]))
            n += 1
    con.close()
    return n


def rebuild_index(project) -> int:
    p = db_path(project)
    if p.exists():
        p.unlink()
    total = 0
    st = project.state().get("chapters", {})
    for c in project.chapters:
        if st.get(c.key, {}).get("status") in DONE_STATES:
            total += index_chapter(project, c.key)
    return total


def fts_query(q: str) -> str:
    q = q.strip()
    parts = re.findall(r'"([^"]+)"|(\S+)', q)
    terms = []
    for i, (phrase, word) in enumerate(parts):
        if phrase:
            terms.append('"' + phrase.replace('"', "") + '"')
        else:
            w = re.sub(r'[^\w\-]', "", word, flags=re.U)
            if not w:
                continue
            w = w.replace("-", " ")
            terms.append(f'"{w}"' + ("*" if i == len(parts) - 1 else ""))
    return " ".join(terms)


def search(project, q: str, chapter: str | None = None, ctype: str | None = None,
           specialty: str | None = None, limit: int = 50) -> list[dict]:
    fq = fts_query(q)
    if not fq or not db_path(project).exists():
        return []
    con = connect(project)
    sql = ("SELECT u.*, snippet(units_fts, 0, '[[', ']]', ' … ', 28) AS snip FROM units_fts "
           "JOIN units u ON u.id = units_fts.rowid WHERE units_fts MATCH ?")
    args: list = [fq]
    if chapter:
        sql += " AND u.chapter = ?"
        args.append(chapter)
    if ctype:
        sql += " AND u.type = ?"
        args.append(ctype)
    if specialty:
        sql += " AND u.specialty LIKE ?"
        args.append(f"%{specialty}%")
    sql += " ORDER BY bm25(units_fts, 1.0, 0.4, 0.2) LIMIT ?"
    args.append(limit)
    try:
        rows = con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        rows = []
    con.close()
    return [{"chapter": r["chapter"], "chapter_no": r["chapter_no"], "chapter_title": r["chapter_title"],
             "section_id": r["section_id"], "heading_path": r["heading_path"], "snippet": r["snip"],
             "type": r["type"], "pages": json.loads(r["pages"]), "page": r["page"], "block_id": r["block_id"],
             "md_file": r["md_file"], "line": r["line_start"], "specialty": r["specialty"]} for r in rows]


# -------------------------------------------------------------------------------- RAG

def _chunk_md(b: dict) -> str:
    md = b["md"]
    md = re.sub(r"<!-- source_page: \d+ -->\s?", "", md)
    return md.strip()


def build_chunks_for_chapter(project, data: dict, max_chars: int = 2200) -> list[dict]:
    book = project.book
    ch = data["chapter"]
    chunks = []
    sections: dict[str, list[dict]] = {}
    order = []
    for b in data["blocks"]:
        if b["section_id"] not in sections:
            order.append(b["section_id"])
        sections.setdefault(b["section_id"], []).append(b)
    for sid in order:
        blist = sections[sid]
        n = 0
        buf: list[dict] = []
        size = 0

        def emit(blocks: list[dict], ctype: str):
            nonlocal n
            text = "\n\n".join(_chunk_md(b) for b in blocks)
            if not text.strip():
                return
            cites = sorted({int(x) for m in re.finditer(r"\[(\d{1,4}(?:[,–\-]\s*\d{1,4})*)\]", strip_markdown(text))
                            for x in re.findall(r"\d+", m.group(1))})
            pages = sorted({p for b in blocks for p in b["pages"]})
            n += 1
            chunks.append({
                "id": f"{sid}#{n}", "book": book["title"], "book_id": book["book_id"], "edition": str(book.get("edition") or ""),
                "chapter": ch.get("number"), "chapter_key": ch["key"], "chapter_title": ch["title"], "section_id": sid,
                "heading_path": blocks[0]["heading_path"], "source_pages": pages, "text": text, "content_type": ctype,
                "references": cites, "markdown_file": data["md_file"], "block_ids": [b["id"] for b in blocks],
            })

        def flush():
            nonlocal buf, size
            if buf:
                emit(buf, "text" if all(b["type"] in ("paragraph", "heading", "list", "formula", "footnote") for b in buf) else "mixed")
            buf, size = [], 0

        for b in blist:
            t = b["type"]
            if t == "table":
                flush()
                text = b["extra"].get("table_text") or b["md"]
                emit([dict(b, md=text)], "table")
            elif t == "reference":
                flush()
                emit([b], "reference")
            elif t in ("figure", "unreadable", "caption"):
                flush()
                emit([b], "figure_caption")
            elif t == "callout":
                flush()
                emit([b], "callout")
            else:
                L = len(b["md"])
                if buf and size + L > max_chars:
                    flush()
                buf.append(b)
                size += L
        flush()
    # merge consecutive reference chunks of one section into groups of ~max_chars
    out, i = [], 0
    while i < len(chunks):
        c = chunks[i]
        if c["content_type"] == "reference":
            j, text, ids, pages, cites = i, [], [], set(), set()
            while j < len(chunks) and chunks[j]["content_type"] == "reference" and chunks[j]["section_id"] == c["section_id"] \
                    and sum(len(t) for t in text) < max_chars:
                text.append(chunks[j]["text"]); ids += chunks[j]["block_ids"]
                pages |= set(chunks[j]["source_pages"]); cites |= set(chunks[j]["references"])
                j += 1
            c = dict(c, text="\n\n".join(text), block_ids=ids, source_pages=sorted(pages), references=sorted(cites))
            out.append(c)
            i = j
        else:
            out.append(c)
            i += 1
    # re-number ids per section
    counters: dict[str, int] = defaultdict(int)
    for c in out:
        counters[c["section_id"]] += 1
        c["id"] = f"{c['section_id']}#{counters[c['section_id']]}"
    return out


def build_rag(project) -> int:
    st = project.state().get("chapters", {})
    lines = []
    for c in project.chapters:
        if st.get(c.key, {}).get("status") in DONE_STATES:
            data = load_chapter_blocks(project, c.key)
            if data:
                lines += [json.dumps(x, ensure_ascii=False) for x in build_chunks_for_chapter(project, data)]
    atomic_write_text(project.root / "rag" / "chunks.jsonl", "\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


# --------------------------------------------------------------------- book index / README

def build_book_index(project) -> None:
    st = project.state().get("chapters", {})
    b = project.book
    rows = ["| Chapter | Title | Specialty | Source Pages | Markdown File | Status |", "|---:|---|---|---|---|---|"]
    for c in project.chapters:
        s = st.get(c.key, {}).get("status", NOT_STARTED)
        meta = (load_chapter_blocks(project, c.key) or {}).get("meta", {})
        spec = ", ".join(meta.get("specialty", [])) or re.sub(r"^\s*part\s+[\dIVXLC]+[\s.:\-–—]*", "", c.part or "", flags=re.I)
        md = project.chapter_md_path(c)
        link = f"[{md.name}](chapters/{md.name})" if md.exists() else "—"
        title = f"[{c.title}](chapters/{md.name})" if md.exists() else c.title
        rows.append(f"| {c.number if c.number is not None else ''} | {title} | {spec} | {c.start}–{c.end} | {link} | {s} |")
    text = (f"# {b['title']} — Book Index\n\n"
            f"Edition: {b.get('edition') or 'n/a'} · Source: `{b.get('source_file_name')}` · "
            f"SHA-256: `{b['source_sha256'][:16]}…`\n\n" + "\n".join(rows) + "\n")
    atomic_write_text(project.root / "00_BOOK_INDEX.md", text)


def build_readme(project) -> None:
    b = project.book
    text = f"""# {b['title']} — Markdown Knowledge Base

Generated by **textbook2md {APP_VERSION}** · last update {now_iso()[:10]}

* **Source book:** {b['title']}, edition {b.get('edition') or 'n/a'}
* **Source file:** `{b.get('source_file_name')}` (SHA-256 `{b['source_sha256']}`) — the original PDF is never modified.
* **Language:** English (verbatim source text; no summarisation, paraphrase or translation).

## Folder structure

| Path | Content |
|---|---|
| `00_BOOK_INDEX.md` | Clickable chapter list with status |
| `chapters/` | One Markdown file per chapter (YAML front matter + body) |
| `assets/images/ch_*/` | Figures extracted from the PDF (never redrawn) |
| `tables/ch_*/` | Very large tables stored as separate files and linked from the chapter |
| `source_maps/` | `ch_*.json` — block → PDF page / bounding box / Markdown lines / heading path |
| `indexes/` | **Generated** navigation indexes (diseases, drugs, signs & symptoms, topics) |
| `rag/chunks.jsonl` | Section-based chunks for retrieval (verbatim Markdown, never summarised) |
| `reports/` | Per-chapter conversion & QA reports |
| `metadata/` | `unresolved_references.json`, `conversion_memory.json` |
| `book.yaml`, `manifest.json` | Book metadata, chapter definitions, hashes, conversion state |

## Conventions

* **Page markers:** `<!-- source_page: 2471 -->` precedes the first block of each source page (and appears inline where
  a paragraph continues onto a new page). Comments are invisible when rendered; the review mode shows them as
  `[Trang gốc: 2471]`.
* **Section IDs:** headings are followed by `<!-- section_id: … -->`; IDs are stable across re-runs.
* **Tables:** GFM tables when simple; HTML tables (rowspan/colspan) for merged cells or multi-row headers; huge tables
  live in `tables/` and are linked from the chapter.
* **Figures:** `![Figure n](../assets/images/ch_xxx/figure_xxx_nn.png)` followed by the full original caption.
  Text printed inside a figure (when natively available) is kept in a collapsible `<details>` block.
* **Footnotes:** Markdown footnotes `[^id]`. **Formulas:** LaTeX (`$$ … $$`).
* **Citations & references:** kept exactly as in the source; references carry `<a id="ref-N"></a>` anchors.
* **Uncertain text:** `<!-- UNCERTAIN: … -->` marks anything that could not be read reliably — never guessed.
* **Generated content:** everything under `indexes/` and the `## Contents` list are *generated navigation aids*, not
  textbook text.

## Using it

* **Obsidian:** open this folder as a vault (standard Markdown; export with “Obsidian mode” for callouts/wikilinks).
* **VS Code:** open the folder; Markdown preview renders tables, footnotes and math (with a KaTeX extension).
* **GitHub:** push the folder; relative links and images resolve.
* **AI / RAG:** ingest `rag/chunks.jsonl`; use `source_maps/` to cite the original PDF page.
"""
    atomic_write_text(project.root / "README.md", text)


# ------------------------------------------------------------------- navigation indexes

DRUG_SUFFIX = re.compile(r"\b[A-Za-z]{3,}(?:cillin|mycin|micin|azole|vir|mab|olol|pril|sartan|statin|prazole|cycline|floxacin|"
                         r"tidine|parin|zepam|barbital|thiazide|semide|sone|solone|zolam|setron|caine|profen|dronate|"
                         r"nib|tinib|ciclovir|navir|bactam|penem|oxacin|mide)\b")
SIGNS = ["fever", "cough", "vomiting", "diarrhea", "jaundice", "cyanosis", "dyspnea", "tachypnea", "wheezing", "stridor",
         "pallor", "rash", "seizure", "seizures", "headache", "abdominal pain", "failure to thrive", "hypotonia", "edema",
         "lethargy", "irritability", "dehydration", "hepatomegaly", "splenomegaly", "murmur", "apnea", "bleeding", "petechiae"]
DISEASE_WORDS = re.compile(r"(?i)\b(disease|syndrome|disorder|infection|itis|osis|emia|pathy|anemia|deficiency|cancer|leukemia|"
                           r"lymphoma|tumou?r|failure|distress|sepsis|asthma|pneumonia|fever|poisoning|malformation)s?\b")


def _headings(data: dict, levels=(2, 3)):
    out = []
    for s in data.get("sections", []):
        if s["level"] in levels:
            out.append(s)
    return out


def build_semantic_indexes(project, ai: AIClient | None = None) -> dict:
    st = project.state().get("chapters", {})
    cats = {"diseases": [], "drugs": [], "signs_symptoms": [], "topics": []}
    used_ai = False
    for c in project.chapters:
        if st.get(c.key, {}).get("status") not in DONE_STATES:
            continue
        data = load_chapter_blocks(project, c.key)
        if not data:
            continue
        md = project.chapter_md_path(c).name
        secs = data.get("sections", [])
        base = f"../chapters/{md}"
        for s in secs:
            if s["level"] in (2, 3):
                cats["topics"].append((s["title"], c, f"{base}#{s.get('anchor', '')}", s["title"]))
        # deterministic, source-derived
        text_by_sec: dict[str, str] = defaultdict(str)
        for b in data["blocks"]:
            text_by_sec[b["section_id"]] += " " + (b.get("clean") or "")
        sec_anchor = {s["section_id"]: s for s in secs}
        chapter_title_entry = (c.title, c, f"{base}", "chapter")
        if DISEASE_WORDS.search(c.title):
            cats["diseases"].append(chapter_title_entry)
        for s in secs:
            if s["level"] in (2, 3) and DISEASE_WORDS.search(s["title"]):
                cats["diseases"].append((s["title"], c, f"{base}#{s.get('anchor', '')}", s["title"]))
            low = s["title"].lower()
            for sg in SIGNS:
                if re.search(rf"\b{sg}\b", low):
                    cats["signs_symptoms"].append((sg, c, f"{base}#{s.get('anchor', '')}", s["title"]))
        seen_drugs: dict[str, int] = defaultdict(int)
        first_sec: dict[str, str] = {}
        for sid, txt in text_by_sec.items():
            for m in DRUG_SUFFIX.finditer(txt):
                w = m.group(0).lower()
                seen_drugs[w] += 1
                first_sec.setdefault(w, sid)
        for w, n in seen_drugs.items():
            if n >= 2:
                s = sec_anchor.get(first_sec[w])
                anc = f"#{s.get('anchor', '')}" if s else ""
                cats["drugs"].append((w, c, f"{base}{anc}", s["title"] if s else c.title))
        if ai is not None and ai.enabled:
            # AI only CLASSIFIES existing heading strings; any term not present in the headings is discarded.
            heads = [s["title"] for s in secs if s["level"] in (2, 3)][:80]
            try:
                r = ai.ask_json("navigation_index", "Classify the given textbook headings. Only return strings copied "
                                 "EXACTLY from the list. JSON: {\"diseases\":[],\"drugs\":[],\"signs_symptoms\":[]}",
                                "\n".join(heads) + f"\nCHAPTER: {c.title}", max_chars=5000)
                used_ai = True
                for cat in ("diseases", "drugs", "signs_symptoms"):
                    for term in r.get(cat, []):
                        if term in heads:
                            s = next(x for x in secs if x["title"] == term)
                            cats[cat].append((term, c, f"{base}#{s.get('anchor', '')}", term))
            except (AIError, AttributeError, TypeError) as e:
                ai.errors.append(str(e))
    titles = {"diseases": "Diseases & Conditions", "drugs": "Drugs", "signs_symptoms": "Signs & Symptoms", "topics": "Topics"}
    for cat, entries in cats.items():
        grouped: dict[str, list] = defaultdict(list)
        for term, c, link, where in entries:
            grouped[term.strip()].append((c, link, where))
        lines = [f"# {titles[cat]} — index", "", GENERATED_NOTE, "",
                 ("_Entries are derived deterministically from headings and recurring term patterns in the source"
                  + (" and optionally classified by an AI model (headings only)" if used_ai else "") + "._"), ""]
        for term in sorted(grouped, key=str.lower):
            refs = "; ".join(f"[Ch {c.number if c.number is not None else c.key} — {where}]({link})"
                             for c, link, where in grouped[term][:8])
            lines.append(f"- **{term}** — {refs}")
        atomic_write_text(project.root / "indexes" / f"{cat}.md", "\n".join(lines) + "\n")
    return {k: len(v) for k, v in cats.items()}


def refresh_project_outputs(project, ai: AIClient | None = None) -> None:
    build_rag(project)
    build_book_index(project)
    build_readme(project)
    build_semantic_indexes(project, ai)
    project.write_manifest()
