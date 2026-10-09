from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import APP_VERSION, indexer, service
from .ai import load_settings
from .pipeline import JobControl, convert_many
from .project import Project, ProjectError


def _progress(ev):
    if "page" in ev and ev.get("stage"):
        print(f"  [{ev['chapter']}] {ev['stage']} p.{ev['page']} ({ev.get('done', 0)}/{ev.get('total', '?')})", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser("textbook2md", description="PDF textbook → structured Markdown knowledge base")
    ap.add_argument("--version", action="version", version=APP_VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("inspect"); s.add_argument("pdf")
    s = sub.add_parser("new"); s.add_argument("pdf"); s.add_argument("--out", required=True)
    s.add_argument("--title"); s.add_argument("--edition")
    s = sub.add_parser("convert"); s.add_argument("project"); s.add_argument("--chapters", help="comma list of keys, e.g. ch_001,ch_002")
    s.add_argument("--all", action="store_true"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("search"); s.add_argument("project"); s.add_argument("query"); s.add_argument("--limit", type=int, default=10)
    s = sub.add_parser("status"); s.add_argument("project")
    s = sub.add_parser("reindex"); s.add_argument("project")
    s = sub.add_parser("export"); s.add_argument("project"); s.add_argument("--zip"); s.add_argument("--rag")
    s.add_argument("--obsidian", action="store_true"); s.add_argument("--include-pdf", action="store_true")
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=0); s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--static", help="path to the built frontend (dist)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "inspect":
            print(json.dumps(service.inspect_pdf(a.pdf), indent=2, ensure_ascii=False))
        elif a.cmd == "new":
            p = service.create_project(a.pdf, a.out, {"title": a.title, "edition": a.edition})
            print(f"Created project at {p.root} with {len(p.chapters)} chapters")
        elif a.cmd == "convert":
            p = Project(a.project)
            keys = [c.key for c in p.chapters] if a.all or not a.chapters else a.chapters.split(",")
            res = convert_many(p, keys, load_settings(), JobControl(), _progress, a.force)
            for k, v in res.items():
                print(k, v.get("status"), v.get("error", ""))
        elif a.cmd == "search":
            for r in indexer.search(Project(a.project), a.query, limit=a.limit):
                print(f"[{r['chapter']}] p.{r['page']} {r['heading_path']}\n    {r['snippet']}")
        elif a.cmd == "status":
            print(json.dumps(Project(a.project).overview()["progress"], indent=2))
            for c in Project(a.project).overview()["chapters"]:
                print(f"{c['key']:10} {c['state'].get('status', 'NOT_STARTED'):24} pp.{c['start']}-{c['end']}  {c['title']}")
        elif a.cmd == "reindex":
            print(indexer.rebuild_index(Project(a.project)), "units indexed")
        elif a.cmd == "export":
            from . import export
            p = Project(a.project)
            if a.zip:
                print(export.export_zip(p, a.zip, include_pdf=a.include_pdf, obsidian=a.obsidian))
            if a.rag:
                print(export.export_rag(p, a.rag))
        elif a.cmd == "serve":
            from . import server
            server.serve(a.host, a.port, a.static)
    except ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
