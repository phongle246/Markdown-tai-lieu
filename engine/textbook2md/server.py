"""Local HTTP sidecar for the desktop UI (Tauri webview or browser). Binds to 127.0.0.1 only."""
from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pymupdf

from . import APP_VERSION, export, indexer, ocr, service
from .ai import AIClient, load_settings, public_settings, save_settings, settings_dir
from .chapters import Chapter
from .pipeline import Cancelled, JobControl, convert_many
from .project import Project, ProjectError, open_pdf
from .util import atomic_write_json, now_iso, read_json

TOKEN = secrets.token_urlsafe(24)
DEV = bool(os.environ.get("T2MD_DEV"))


def registry_path() -> Path:
    return settings_dir() / "projects.json"


def registry() -> dict:
    return read_json(registry_path(), {})


def project_id(path: str | Path) -> str:
    return hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:10]


def register(project: Project) -> str:
    reg = registry()
    pid = project_id(project.root)
    reg[pid] = {"path": str(project.root), "last_opened": now_iso()}
    settings_dir().mkdir(parents=True, exist_ok=True)
    atomic_write_json(registry_path(), reg)
    return pid


def get_project(pid: str) -> Project:
    ent = registry().get(pid)
    if not ent:
        raise ProjectError("Unknown project. Re-open it from the library.")
    return Project(ent["path"])


class Jobs:
    """One conversion job at a time; progress is polled by the UI."""

    def __init__(self):
        self.lock = threading.Lock()
        self.job: dict | None = None
        self.ctrl: JobControl | None = None

    def start(self, project: Project, keys: list[str], force: bool) -> dict:
        with self.lock:
            if self.job and self.job["status"] in ("running", "paused"):
                raise ProjectError("A conversion is already running. Pause/cancel it first.")
            self.ctrl = JobControl()
            job = {"id": secrets.token_hex(4), "project": project.root.name, "project_path": str(project.root),
                   "chapters": keys, "status": "running", "progress": {}, "results": {}, "started": now_iso(), "log": []}
            self.job = job

        def progress(ev):
            job["progress"] = ev
            if ev.get("stage") and (not job["log"] or job["log"][-1] != f"{ev.get('chapter')}: {ev['stage']}"):
                job["log"] = (job["log"] + [f"{ev.get('chapter')}: {ev['stage']}"])[-30:]

        def run():
            try:
                job["results"] = convert_many(project, keys, load_settings(), self.ctrl, progress, force)
                job["status"] = "cancelled" if any(v.get("status") == "CANCELLED" for v in job["results"].values()) else "done"
                if any(v.get("status") == "FAILED" for v in job["results"].values()):
                    job["status"] = "done_with_errors"
            except Exception as e:  # pragma: no cover
                job["status"] = "failed"
                job["error"] = f"{type(e).__name__}: {e}"
            job["finished"] = now_iso()

        threading.Thread(target=run, daemon=True, name="convert").start()
        return job

    def snapshot(self) -> dict | None:
        if not self.job:
            return None
        j = dict(self.job)
        if self.ctrl and self.ctrl.paused and j["status"] == "running":
            j["status"] = "paused"
        return j


JOBS = Jobs()
_docs: dict[str, pymupdf.Document] = {}
_doc_lock = threading.Lock()


def render_page(project: Project, n: int, zoom: float) -> bytes:
    key = str(project.root)
    with _doc_lock:
        doc = _docs.get(key)
        if doc is None:
            doc = _docs[key] = open_pdf(project.pdf_path())
        if not (1 <= n <= doc.page_count):
            raise ProjectError(f"Page {n} is outside the document (1–{doc.page_count}).")
        pix = doc[n - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        return pix.tobytes("png")


# ------------------------------------------------------------------------------ routes

def chapter_payload(project: Project, key: str) -> dict:
    c = project.chapter(key)
    md_path = project.chapter_md_path(c)
    if not md_path.exists():
        raise ProjectError("This chapter has not been converted yet.")
    text = md_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    smap = read_json(project.root / "source_maps" / f"{key}.json", {"blocks": []})
    iss = read_json(project.hidden / "issues" / f"{key}.json", {"issues": [], "metrics": {}})
    blocks = []
    for b in smap["blocks"]:
        seg = "\n".join(lines[b["md_line_start"] - 1: b["md_line_end"]])
        blocks.append({**b, "markdown": seg})
    rep = project.root / "reports" / f"{key}_conversion_report.md"
    sizes = {}
    for n in range(c.start, c.end + 1):
        rec = read_json(project.hidden / "pages" / key / f"p{n:05d}.json")
        if rec:
            sizes[n] = [rec["w"], rec["h"]]
    return {"page_sizes": sizes, "chapter": c.to_dict(), "markdown": text, "blocks": blocks, "issues": iss["issues"], "status": iss.get("status"),
            "report": rep.read_text(encoding="utf-8") if rep.exists() else "", "sections": smap.get("sections", []),
            "markdown_file": smap.get("markdown_file")}


def list_dir(path: str | None) -> dict:
    p = Path(path or Path.home()).expanduser()
    if not p.is_dir():
        p = p.parent if p.parent.is_dir() else Path.home()
    items = []
    try:
        for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if e.name.startswith("."):
                continue
            if e.is_dir():
                items.append({"name": e.name, "dir": True, "path": str(e)})
            elif e.suffix.lower() == ".pdf":
                items.append({"name": e.name, "dir": False, "path": str(e), "size": e.stat().st_size})
    except PermissionError:
        pass
    return {"path": str(p), "parent": str(p.parent), "items": items, "home": str(Path.home())}


class Handler(BaseHTTPRequestHandler):
    server_version = f"textbook2md/{APP_VERSION}"
    static_dir: Path | None = None

    def log_message(self, *a):
        pass

    # --- helpers
    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _bytes(self, data: bytes, ctype: str, code=200, cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode() or "{}") if n else {}

    def _authorized(self, q) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "[::1]"):
            return False
        return DEV or self.headers.get("X-T2MD-Token") == TOKEN or q.get("token", [""])[0] == TOKEN

    # --- dispatch
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path
        try:
            if not path.startswith("/api/"):
                return self._static(path)
            if not self._authorized(q):
                return self._json({"error": "Unauthorized"}, 401)
            res = self._api(method, path, q)
            if res is not None:
                self._json(res)
        except ProjectError as e:
            self._json({"error": str(e)}, 400)
        except Cancelled:
            self._json({"error": "cancelled"}, 409)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]}, 500)

    def _static(self, path):
        d = self.static_dir
        if not d or not d.exists():
            return self._bytes(b"textbook2md API is running. Build the UI with `npm run build` in app/.", "text/plain")
        rel = path.lstrip("/") or "index.html"
        f = (d / rel).resolve()
        if not str(f).startswith(str(d.resolve())) or not f.is_file():
            f = d / "index.html"
        data = f.read_bytes()
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if f.name == "index.html":
            data = data.replace(b"</head>", f'<script>window.__T2MD_TOKEN="{TOKEN}"</script></head>'.encode(), 1)
        self._bytes(data, ctype)

    def _api(self, method, path, q):
        seg = [urllib.parse.unquote(s) for s in path.split("/")[2:]]
        g = lambda k, d=None: q.get(k, [d])[0]
        if seg == ["health"]:
            return {"ok": True, "version": APP_VERSION, "ocr_available": ocr.available()}
        if seg == ["settings"]:
            if method == "PUT":
                return public_settings(save_settings(self._body()))
            return public_settings()
        if seg == ["settings", "test"]:
            s = load_settings()
            body = self._body()
            for k in ("provider", "openai_model", "gemini_model"):
                if body.get(k):
                    s[k] = body[k]
            for k in ("openai_api_key", "gemini_api_key"):
                if body.get(k) and not set(body[k]) <= {"•", "*"}:
                    s[k] = body[k]
            s["ai_enabled"] = True
            return AIClient(s).test_connection()
        if seg == ["fs"]:
            return list_dir(g("path"))
        if seg == ["inspect"]:
            return service.inspect_pdf(self._body()["pdf"])
        if seg == ["job"]:
            return {"job": JOBS.snapshot()}
        if seg[:1] == ["job"] and len(seg) == 2 and method == "POST":
            if not JOBS.ctrl:
                raise ProjectError("No active job.")
            {"pause": JOBS.ctrl.pause, "resume": JOBS.ctrl.resume, "cancel": JOBS.ctrl.cancel}[seg[1]]()
            return {"job": JOBS.snapshot()}
        if seg == ["projects"]:
            if method == "POST":
                b = self._body()
                s = load_settings()
                info = service.inspect_pdf(b["pdf"])
                out = b.get("out_dir") or str(service.default_project_dir(s["output_dir"], {**info["book"], **{k: v for k, v in b.items() if v and k in ("title", "edition")}}))
                p = service.create_project(b["pdf"], out, {k: b.get(k) for k in ("title", "edition", "authors", "book_id")}, b.get("chapters"))
                pid = register(p)
                return {"id": pid, **p.overview()}
            out = []
            for pid, ent in registry().items():
                try:
                    p = Project(ent["path"])
                    ov = p.overview()
                    out.append({"id": pid, "title": p.book["title"], "edition": p.book.get("edition"), "path": ent["path"],
                                "progress": ov["progress"], "last_opened": ent.get("last_opened"),
                                "has_cover": (p.hidden / "cover.png").exists()})
                except ProjectError:
                    out.append({"id": pid, "title": Path(ent["path"]).name, "path": ent["path"], "missing": True})
            out.sort(key=lambda x: x.get("last_opened") or "", reverse=True)
            return {"projects": out}
        if seg == ["projects", "open"]:
            p = Project(self._body()["path"])
            pid = register(p)
            return {"id": pid, **p.overview()}
        if seg[:1] == ["projects"] and len(seg) >= 2:
            pid = seg[1]
            if method == "DELETE" and len(seg) == 2:
                reg = registry()
                reg.pop(pid, None)
                atomic_write_json(registry_path(), reg)
                return {"ok": True}
            p = get_project(pid)
            rest = seg[2:]
            if not rest:
                reg = registry()
                reg[pid]["last_opened"] = now_iso()
                atomic_write_json(registry_path(), reg)
                return {"id": pid, **p.overview()}
            if rest == ["chapters"] and method == "PUT":
                p.set_chapters([Chapter.from_dict(c) for c in self._body()["chapters"]])
                return {"id": pid, **p.overview()}
            if rest == ["convert"]:
                b = self._body()
                keys = [c.key for c in p.chapters] if b.get("chapters") in (None, "all") else b["chapters"]
                for k in keys:
                    p.chapter(k)
                return {"job": JOBS.start(p, keys, bool(b.get("force")))}
            if rest[0] == "chapters" and len(rest) == 3 and rest[2] == "content":
                return chapter_payload(p, rest[1])
            if rest == ["search"]:
                return {"results": indexer.search(p, g("q", ""), g("chapter") or None, g("type") or None, g("specialty") or None, int(g("limit", "50")))}
            if rest[0] == "page" and len(rest) == 2:
                n = int(rest[1].split(".")[0])
                self._bytes(render_page(p, n, float(g("zoom", "1.4"))), "image/png", cache=True)
                return None
            if rest == ["cover.png"]:
                f = p.hidden / "cover.png"
                self._bytes(f.read_bytes() if f.exists() else b"", "image/png", 200 if f.exists() else 404)
                return None
            if rest == ["file"]:
                f = (p.root / g("path", "")).resolve()
                if not str(f).startswith(str(p.root)) or ".t2md" in f.parts or not f.is_file():
                    raise ProjectError("File not found.")
                self._bytes(f.read_bytes(), mimetypes.guess_type(str(f))[0] or "application/octet-stream", cache=True)
                return None
            if rest == ["export"]:
                b = self._body()
                kind, dest = b["kind"], b["dest"]
                if kind == "zip":
                    return {"path": export.export_zip(p, dest, bool(b.get("include_pdf")), b.get("markdown_mode") == "obsidian")}
                if kind == "rag":
                    return {"path": export.export_rag(p, dest)}
                if kind == "chapter":
                    return {"path": export.export_chapter(p, b["key"], dest)}
                raise ProjectError("Unknown export kind.")
            if rest == ["reindex"]:
                n = indexer.rebuild_index(p)
                indexer.refresh_project_outputs(p)
                return {"units": n}
            if rest == ["unresolved"]:
                return {"items": read_json(p.root / "metadata" / "unresolved_references.json", [])}
            if rest == ["ai-log"]:
                f = p.hidden / "ai_log.jsonl"
                rows = [json.loads(l) for l in f.read_text().splitlines()[-200:]] if f.exists() else []
                return {"items": rows}
            if rest == ["open-folder"]:
                cmd = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
                subprocess.Popen([cmd, str(p.root)])
                return {"ok": True}
            if rest == ["relink"]:
                p.relink_pdf(self._body()["pdf"])
                return {"ok": True}
        raise ProjectError(f"Unknown endpoint: {method} {path}")


def serve(host: str = "127.0.0.1", port: int = 0, static: str | None = None, announce=True) -> ThreadingHTTPServer:
    Handler.static_dir = Path(static) if static else None
    httpd = ThreadingHTTPServer((host, port), Handler)
    if announce:
        print(f"T2MD_PORT={httpd.server_address[1]}", flush=True)
        print(f"textbook2md {APP_VERSION} listening on http://{host}:{httpd.server_address[1]}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return httpd
