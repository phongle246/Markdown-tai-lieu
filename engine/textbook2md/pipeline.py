"""Chapter conversion orchestration: 15 passes, page-level checkpoints, resumable."""
from __future__ import annotations

import json
import re
import threading
import traceback
from collections import Counter
from pathlib import Path

import pymupdf

from . import APP_VERSION, PARSER_VERSION, crossref, indexer, layout, qa, report
from .ai import AIClient, heading_classifier, load_settings
from .assemble import build_markdown, default_meta, write_assets
from .blocks import BuildCtx, build_blocks
from .chapters import profile_book
from .models import (COMPLETED, FAILED, NOT_STARTED, PROCESSING, Issue, SEV_RANK, status_from_issues)
from .project import Project, ProjectError, open_pdf
from .textclean import Vocab
from .util import atomic_write_json, atomic_write_text, now_iso, read_json, sha256_file, sha256_text

STAGES = ["Parsing pages", "Reading layout", "Extracting text", "Extracting tables", "Extracting figures",
          "Building Markdown", "Running QA", "Indexing"]


class Cancelled(Exception):
    pass


class JobControl:
    """Pause / resume / cancel, honoured at page boundaries."""

    def __init__(self):
        self._run = threading.Event()
        self._run.set()
        self.cancelled = False

    def pause(self):
        self._run.clear()

    def resume(self):
        self._run.set()

    def cancel(self):
        self.cancelled = True
        self._run.set()

    @property
    def paused(self):
        return not self._run.is_set()

    def checkpoint(self):
        self._run.wait()
        if self.cancelled:
            raise Cancelled()


def _noop(*a, **k):
    pass


def ensure_profile(project: Project, doc) -> dict:
    mem = project.memory()
    if not mem.get("layout", {}).get("profiled"):
        prof = profile_book(doc)
        mem["running_patterns"] = prof["running_patterns"]
        mem["repeated_xrefs"] = prof["repeated_xrefs"]
        mem["layout"] = {"body_size": prof["body_size"], "profiled": True, "sampled_pages": prof["sampled_pages"]}
        project.save_memory(mem)
    return mem


def _page_cache_ok(rec: dict, ch, opts: layout.LayoutOptions) -> bool:
    return rec.get("_parser") == PARSER_VERSION and rec.get("_ocr") == opts.ocr_enabled


def convert_chapter(project: Project, key: str, settings: dict | None = None, ctrl: JobControl | None = None,
                    progress=_noop, force: bool = False, ai: AIClient | None = None) -> dict:
    settings = settings or load_settings()
    ctrl = ctrl or JobControl()
    ch = project.chapter(key)
    cur = project.chapter_state(key)
    if cur.get("status") in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "REVIEW_REQUIRED") and not force:
        return cur
    if force:
        project.reset_chapter_state(key)
    total = ch.end - ch.start + 1
    project.update_chapter_state(key, status=PROCESSING, pages_total=total, stage=STAGES[0], error=None,
                                 started=now_iso(), finished=None, pages_done=cur.get("pages_done", 0) if not force else 0)
    doc = None
    try:
        project.verify_source()
        doc = open_pdf(project.pdf_path())
        return _run(project, doc, ch, settings, ctrl, progress, ai)
    except Cancelled:
        project.update_chapter_state(key, status=PROCESSING, stage="Paused/cancelled — resumable", interrupted=True)
        raise
    except ProjectError as e:
        project.update_chapter_state(key, status=FAILED, error=str(e), stage="Failed")
        raise
    except Exception as e:  # never crash silently
        project.update_chapter_state(key, status=FAILED, error=f"{type(e).__name__}: {e}", stage="Failed",
                                     traceback=traceback.format_exc()[-1800:])
        raise
    finally:
        if doc is not None:
            doc.close()


def _run(project: Project, doc, ch, settings, ctrl: JobControl, progress, ai: AIClient | None) -> dict:
    key = ch.key
    book = project.book
    mem = ensure_profile(project, doc)
    opts = layout.LayoutOptions(ocr_enabled=bool(settings.get("ocr_enabled", True)), ocr_lang=settings.get("ocr_lang", "eng"),
                                skip_xrefs=set(mem.get("repeated_xrefs", [])))
    cache_dir = project.hidden / "pages" / key
    cache_dir.mkdir(parents=True, exist_ok=True)
    img_dir = cache_dir / "img"
    total = ch.end - ch.start + 1
    pages: list[dict] = []
    reused = 0
    for i, pno in enumerate(range(ch.start, ch.end + 1)):
        ctrl.checkpoint()
        cfile = cache_dir / f"p{pno:05d}.json"
        rec = read_json(cfile)
        clip = (ch.start_y if pno == ch.start else None, ch.end_y if pno == ch.end else None)
        if rec and _page_cache_ok(rec, ch, opts) and rec.get("_clip") == list(clip):
            reused += 1
        else:
            progress({"chapter": key, "stage": STAGES[1], "page": pno, "done": i, "total": total})
            rec = layout.page_record(doc, pno - 1, opts, img_dir, clip)
            rec["_parser"], rec["_ocr"], rec["_clip"] = PARSER_VERSION, opts.ocr_enabled, list(clip)
            atomic_write_json(cfile, rec)
        pages.append(rec)
        project.update_chapter_state(key, pages_done=i + 1, stage=STAGES[2], interrupted=False)
        progress({"chapter": key, "stage": STAGES[2], "page": pno, "done": i + 1, "total": total, "reused": reused})
    ctrl.checkpoint()
    # ---------------------------------------------------------------- chapter-level passes
    progress({"chapter": key, "stage": STAGES[5], "page": ch.end, "done": total, "total": total})
    vocab = Vocab.from_json(read_json(project.hidden / "vocab.json"))
    working = json.loads(json.dumps(pages))        # passes below mutate; keep the cache pristine
    ai = ai or AIClient(settings, project.hidden / "ai_log.jsonl")
    chapter_d = ch.to_dict()
    ctx = BuildCtx(chapter=chapter_d, book=book, pages=working, memory=mem, vocab=vocab,
                   running_patterns=set(mem.get("running_patterns", [])),
                   ai_heading=heading_classifier(ai) if ai.enabled else None)
    built = build_blocks(ctx)
    blocks = built["blocks"]
    # label registry (before linking so same-chapter refs resolve)
    labels = built["labels"]
    for b in blocks:
        if b.type == "table" and b.extra.get("big") and b.extra.get("label"):
            labels[b.extra["label"]]["table_file"] = f"../tables/{key}/{b.extra['table_file']}"
    registry = {}
    for lf in (project.hidden / "labels").glob("*.json"):
        d = read_json(lf)
        if d and d["key"] != key and d.get("number") is not None:
            registry[d["number"]] = d
    md_name = project.chapter_filename(ch)
    resolver = crossref.make_resolver(registry, ch.number, f"chapters/{md_name}", labels)
    meta = default_meta(chapter_d)
    if ai.enabled:
        meta = _ai_meta(ai, chapter_d, blocks, meta)
    src_pages = f"{ch.start}-{ch.end}"
    text, entries, sections, unresolved, resolved = build_markdown(blocks, book, chapter_d, meta, src_pages, resolver)
    # ---------------------------------------------------------------------- QA
    progress({"chapter": key, "stage": STAGES[6], "page": ch.end, "done": total, "total": total})
    table_files = {b.id: b.extra["table_text"] for b in blocks if b.type == "table" and b.extra.get("big")}
    issues, metrics = qa.run_qa(chapter_d, working, blocks, text, table_files, built["removed"],
                                list(range(ch.start, ch.end + 1)), ctx.issues, built["stats"])
    issues += _typo_issues(blocks)
    issues += [Issue("LOW", "UNRESOLVED_XREF", f"Cross-reference not (yet) resolvable: {r.text}", b.page, b.id, "structure")
               for b, r in unresolved if False]
    for ent in entries:
        pass
    issues.sort(key=lambda i: (SEV_RANK[i.severity], i.page or 0))
    status = status_from_issues(issues)
    # --------------------------------------------------------------- write outputs
    root = project.root
    atomic_write_text(project.chapter_md_path(ch), text)
    assets = write_assets(blocks, chapter_d, root, img_dir)
    atomic_write_json(root / "source_maps" / f"{key}.json", {
        "chapter": key, "chapter_number": ch.number, "chapter_title": ch.title, "markdown_file": f"chapters/{md_name}",
        "source_file": book["source_file_name"], "source_sha256": book["source_sha256"], "source_pages": [ch.start, ch.end],
        "generated": now_iso(), "parser_version": PARSER_VERSION, "sections": sections, "blocks": entries})
    ent_by_id = {e["block_id"]: e for e in entries}
    blocks_json = {
        "chapter": chapter_d, "md_file": f"chapters/{md_name}", "meta": meta, "sections": sections,
        "blocks": [{"id": b.id, "type": b.type, "md": b.md, "raw": b.raw, "clean": b.clean, "pages": b.pages,
                    "bboxes": b.bboxes, "conf": b.conf, "level": b.level, "heading_path": b.heading_path,
                    "section_id": b.section_id, "flags": b.flags, "md_line_start": ent_by_id[b.id]["md_line_start"],
                    "md_line_end": ent_by_id[b.id]["md_line_end"],
                    "extra": {k: v for k, v in b.extra.items() if k in ("label", "caption", "labels", "ref_number", "table_text", "big", "kind", "title")}}
                   for b in blocks]}
    atomic_write_json(indexer.blocks_file(project, key), blocks_json)
    atomic_write_json(project.hidden / "labels" / f"{key}.json",
                      {"key": key, "number": ch.number, "file": f"chapters/{md_name}", "labels": labels})
    unres_all = read_json(root / "metadata" / "unresolved_references.json", [])
    unres_all = [u for u in unres_all if u["chapter"] != key]
    seen = set()
    for b, r in unresolved:
        k2 = (b.id, r.text)
        if k2 in seen:
            continue
        seen.add(k2)
        unres_all.append({"chapter": key, "block_id": b.id, "page": b.page, "section_id": b.section_id, "text": r.text,
                          "kind": r.kind, "label": r.label, "target_chapter": r.target_chapter})
    atomic_write_json(root / "metadata" / "unresolved_references.json", unres_all)
    # memory / vocab
    local = set(built.get("running_local", []))
    mem["layout"]["body_size"] = built["stats"]["body"]
    mem["table_rules"]["last_strategy"] = Counter(t.get("strategy", "") for p in working for t in p["items"] if t["t"] == "table").most_common(1)
    project.save_memory(mem)
    vocab_all = Vocab.from_json(read_json(project.hidden / "vocab.json"))
    vocab_all.update(ctx.vocab)
    atomic_write_json(project.hidden / "vocab.json", vocab_all.to_json())
    # report
    ch_unres = [u for u in unres_all if u["chapter"] == key]
    extra = {
        "version": f"textbook2md {APP_VERSION} / {PARSER_VERSION}", "blocks": len(blocks),
        "headings": sum(1 for b in blocks if b.type == "heading"), "sections": len(sections),
        "removed": len(built["removed"]), "dehyphenated": sum(len(p.get("_dh", [])) for p in []) or _count_dh(blocks),
        "kept_hyphen": 0, "pages_processed": len(pages), "pages_expected": total,
        "scanned_pages": sum(1 for p in pages if p.get("scanned")), "ocr_pages": sum(1 for p in pages if p.get("ocr")),
        "unresolved": ch_unres}
    idicts = [i.to_dict() for i in issues]
    atomic_write_text(root / "reports" / f"{key}_conversion_report.md", report.render(chapter_d, book, status, idicts, metrics, extra))
    atomic_write_json(project.hidden / "issues" / f"{key}.json", {"status": status, "issues": idicts, "metrics": metrics})
    project.update_chapter_state(key, status=status, finished=now_iso(), stage="Completed", pages_done=total,
                                 md_sha256=sha256_text(text), counts={s: sum(1 for i in issues if i.severity == s) for s in SEV_RANK},
                                 error=None, interrupted=False)
    progress({"chapter": key, "stage": STAGES[7], "page": ch.end, "done": total, "total": total})
    indexer.index_chapter(project, key)
    resolve_pending(project)
    indexer.refresh_project_outputs(project, ai if ai.enabled else None)
    return project.chapter_state(key)


def _count_dh(blocks) -> int:
    return sum(1 for b in blocks if "hyphen" in " ".join(b.flags))


def _typo_issues(blocks) -> list[Issue]:
    out = []
    for b in blocks:
        if b.type != "paragraph":
            continue
        for m in re.finditer(r"\b([A-Za-z]{2,})\s+\1\b", b.clean):
            if m.group(1).lower() in ("had", "that", "is", "do"):
                continue
            out.append(Issue("LOW", "POSSIBLE_SOURCE_TYPO", f"Repeated word “{m.group(0)}” kept as in source.", b.page, b.id, "typo"))
    return out[:20]


def _ai_meta(ai: AIClient, chapter: dict, blocks, meta: dict) -> dict:
    heads = [b.clean for b in blocks if b.type == "heading"][:40]
    try:
        r = ai.ask_json("chapter_metadata", "From the chapter title and headings propose specialty (1-3 medical specialties) and up to 8 "
                        "lowercase keyword tags. JSON: {\"specialty\":[],\"tags\":[]}. Use only terms that fit the given headings.",
                        f"TITLE: {chapter['title']}\nHEADINGS:\n" + "\n".join(heads), max_chars=3000)
        sp = [str(x).lower() for x in r.get("specialty", [])][:3]
        tg = [str(x).lower() for x in r.get("tags", [])][:8]
        return {"specialty": sp or meta["specialty"], "tags": tg or meta["tags"]}
    except Exception:
        return meta


def resolve_pending(project: Project) -> int:
    """Link cross-references whose targets have been converted since. Edits only the affected block lines."""
    root = project.root
    path = root / "metadata" / "unresolved_references.json"
    pending = read_json(path, [])
    if not pending:
        return 0
    registry = {}
    for lf in (project.hidden / "labels").glob("*.json"):
        d = read_json(lf)
        if d and d.get("number") is not None:
            registry[d["number"]] = d
    by_ch: dict[str, list[dict]] = {}
    for u in pending:
        by_ch.setdefault(u["chapter"], []).append(u)
    remaining, fixed = [], 0
    for key, items in by_ch.items():
        lab = read_json(project.hidden / "labels" / f"{key}.json") or {}
        smap = read_json(root / "source_maps" / f"{key}.json")
        if not smap or not lab:
            remaining += items
            continue
        md_path = root / smap["markdown_file"]
        lines = md_path.read_text(encoding="utf-8").split("\n")
        resolver = crossref.make_resolver(registry, lab.get("number"), lab["file"], lab.get("labels", {}))
        entries = {e["block_id"]: e for e in smap["blocks"]}
        changed = False
        touched_blocks = {}
        for u in items:
            ent = entries.get(u["block_id"])
            if not ent:
                continue
            ref = crossref.parse(crossref.XREF_RE.search(u["text"]))
            if resolver(ref):
                touched_blocks[u["block_id"]] = ent
        for bid, ent in touched_blocks.items():
            a, b = ent["md_line_start"] - 1, ent["md_line_end"]
            seg = "\n".join(lines[a:b])
            new, res, _ = crossref.link_text(seg, resolver)
            if new != seg:
                lines[a:b] = new.split("\n")
                changed = True
        if changed:
            atomic_write_text(md_path, "\n".join(lines))
            bj = read_json(indexer.blocks_file(project, key))
            if bj:                                   # keep the RAG/search cache identical to the Markdown
                for blk in bj["blocks"]:
                    if blk["id"] in touched_blocks:
                        blk["md"] = crossref.link_text(blk["md"], resolver)[0]
                atomic_write_json(indexer.blocks_file(project, key), bj)
        for u in items:
            ent = entries.get(u["block_id"])
            ref = crossref.parse(crossref.XREF_RE.search(u["text"]))
            if ent and resolver(ref):
                fixed += 1
            else:
                remaining.append(u)
        if changed:
            project.update_chapter_state(key, md_sha256=sha256_text("\n".join(lines)))
    if fixed:
        atomic_write_json(path, remaining)
    return fixed


def convert_many(project: Project, keys: list[str], settings: dict | None = None, ctrl: JobControl | None = None,
                 progress=_noop, force=False) -> dict:
    settings = settings or load_settings()
    ctrl = ctrl or JobControl()
    results = {}
    for n, k in enumerate(keys):
        try:
            ctrl.checkpoint()
            progress({"chapter": k, "stage": "Starting", "chapter_index": n, "chapter_total": len(keys)})
            results[k] = convert_chapter(project, k, settings, ctrl, progress, force)
        except Cancelled:
            results[k] = {"status": "CANCELLED"}
            break
        except Exception as e:
            results[k] = {"status": FAILED, "error": str(e)}
    project.write_manifest()
    return results
