import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from textbook2md import ai, indexer, server


@pytest.fixture(scope="module")
def api():
    server.DEV = True
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, raw=False):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                data = r.read()
                return data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            return {"_status": e.code, **json.loads(e.read())}
    yield call
    httpd.shutdown()


def test_full_api_flow(api, book_pdf, tmp_path):
    assert api("GET", "/api/health")["ok"]
    info = api("POST", "/api/inspect", {"pdf": str(book_pdf)})
    assert len(info["chapters"]) == 3
    proj = api("POST", "/api/projects", {"pdf": str(book_pdf), "out_dir": str(tmp_path / "kb")})
    pid = proj["id"]
    assert api("GET", "/api/projects")["projects"][0]["id"] == pid
    # start conversion of two chapters, poll like the UI does
    job = api("POST", f"/api/projects/{pid}/convert", {"chapters": ["ch_001", "ch_002"]})["job"]
    for _ in range(200):
        j = api("GET", "/api/job")["job"]
        if j["status"] not in ("running", "paused"):
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    ov = api("GET", f"/api/projects/{pid}")
    assert all(c["state"]["status"].startswith("COMPLETED") for c in ov["chapters"][:2])
    assert ov["progress"]["done"] == 2
    # content + blocks for the review screen
    content = api("GET", f"/api/projects/{pid}/chapters/ch_002/content")
    tbl = next(b for b in content["blocks"] if b["type"] == "table")
    assert tbl["source_pages"] == [5] and "| Newborn |" in tbl["markdown"] and tbl["bboxes"][0]["bbox"]
    assert "## Status" in content["report"]
    # page image for the PDF pane
    png = api("GET", f"/api/projects/{pid}/page/5.png?zoom=1", raw=True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert api("GET", f"/api/projects/{pid}/page/99.png").get("_status") == 400
    # search → open section / open source page
    res = api("GET", f"/api/projects/{pid}/search?q=ferrous")["results"]
    assert res and res[0]["page"] == 2 and res[0]["md_file"].startswith("chapters/")
    # assets served from inside the project only
    assert api("GET", f"/api/projects/{pid}/file?path=assets/images/ch_002/figure_002_01.png", raw=True)[:4] == b"\x89PNG"
    assert api("GET", f"/api/projects/{pid}/file?path=../../etc/passwd").get("_status") == 400
    assert api("GET", f"/api/projects/{pid}/file?path=.t2md/state.json").get("_status") == 400
    # export
    out = api("POST", f"/api/projects/{pid}/export", {"kind": "zip", "dest": str(tmp_path / "x.zip")})
    assert (tmp_path / "x.zip").exists() and out["path"].endswith("x.zip")
    # manual chapter correction through the API
    chs = ov["chapters"]
    chs[2]["title"] = "Fluid Therapy (edited)"
    assert api("PUT", f"/api/projects/{pid}/chapters", {"chapters": chs})["chapters"][2]["title"].endswith("(edited)")


def test_pause_resume_cancel_endpoints(api, book_pdf, tmp_path):
    pid = api("POST", "/api/projects", {"pdf": str(book_pdf), "out_dir": str(tmp_path / "kb2")})["id"]
    api("POST", f"/api/projects/{pid}/convert", {"chapters": "all"})
    api("POST", "/api/job/pause")
    assert api("GET", "/api/job")["job"]["status"] in ("paused", "done", "running")
    api("POST", "/api/job/cancel")
    for _ in range(100):
        if api("GET", "/api/job")["job"]["status"] not in ("running", "paused"):
            break
        time.sleep(0.1)
    assert api("GET", "/api/job")["job"]["status"] in ("cancelled", "done")


def test_settings_never_return_secret(api):
    r = api("PUT", "/api/settings", {"provider": "gemini", "gemini_api_key": "SECRET-123", "ai_enabled": True})
    assert r["gemini_api_key_set"] and "SECRET" not in json.dumps(r)
    r2 = api("PUT", "/api/settings", {"gemini_api_key": "••••••••"})                 # masked value must not overwrite
    assert ai.load_settings()["gemini_api_key"] == "SECRET-123" and r2["gemini_api_key_set"]
    api("PUT", "/api/settings", {"ai_enabled": False})


def test_token_required_when_not_dev(book_pdf):
    server.DEV = False
    try:
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/health")
        assert e.value.code == 401
        r = urllib.request.Request(base + "/api/health", headers={"X-T2MD-Token": server.TOKEN})
        assert json.loads(urllib.request.urlopen(r).read())["ok"]
        r = urllib.request.Request(base + "/api/health", headers={"X-T2MD-Token": server.TOKEN, "Host": "evil.example.com"})
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(r)
        httpd.shutdown()
    finally:
        server.DEV = True


# --------------------------------------------------------------------- AI provider abstraction

class FakeAI(ai.AIClient):
    def __init__(self, replies):
        super().__init__({"ai_enabled": True, "provider": "openai", "openai_api_key": "k"})
        self.replies, self.prompts = replies, []

    def ask(self, purpose, system, user, max_chars=6000, retries=3):
        self._log(purpose, system + user)
        self.prompts.append((purpose, user))
        return self.replies[purpose]


def test_ai_disabled_without_key():
    c = ai.AIClient({"ai_enabled": True, "provider": "openai", "openai_api_key": ""})
    assert not c.enabled
    with pytest.raises(ai.AIError):
        c.ask("x", "s", "u")


def test_provider_request_shapes(monkeypatch):
    sent = {}

    class R:
        def __init__(self, d): self.d = d
        def read(self): return json.dumps(self.d).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_open(req, timeout=0):
        sent["url"], sent["headers"], sent["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        if "openai" in req.full_url:
            return R({"choices": [{"message": {"content": "OK"}}]})
        return R({"candidates": [{"content": {"parts": [{"text": "OK"}]}}]})
    monkeypatch.setattr(ai.urllib.request, "urlopen", fake_open)
    o = ai.AIClient({"ai_enabled": True, "provider": "openai", "openai_api_key": "sk-1"})
    assert o.test_connection()["ok"] and sent["url"].startswith("https://api.openai.com/") and sent["headers"]["Authorization"] == "Bearer sk-1"
    g = ai.AIClient({"ai_enabled": True, "provider": "gemini", "gemini_api_key": "g-1"})
    assert g.test_connection()["ok"] and "generativelanguage.googleapis.com" in sent["url"]
    assert "g-1" not in sent["url"] and sent["headers"]["X-goog-api-key"] == "g-1"        # key travels in a header, not the URL


def test_invalid_key_is_actionable(monkeypatch):
    def boom(req, timeout=0):
        raise ai.urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)
    monkeypatch.setattr(ai.urllib.request, "urlopen", boom)
    r = ai.AIClient({"ai_enabled": True, "provider": "openai", "openai_api_key": "bad"}).test_connection()
    assert not r["ok"] and "invalid API key" in r["error"]


def test_ai_heading_resolution_sends_minimal_context(tmp_path, book_pdf):
    from textbook2md import service
    from textbook2md.pipeline import JobControl, convert_chapter
    p = service.create_project(book_pdf, tmp_path / "kb")
    client = FakeAI({"heading_classification": '{"heading": true, "level": 3}',
                     "chapter_metadata": '{"specialty": ["Hematology"], "tags": ["anemia", "iron"]}',
                     "navigation_index": '{"diseases": ["Definition", "INVENTED TERM"], "drugs": [], "signs_symptoms": []}'})
    convert_chapter(p, "ch_001", {"ocr_enabled": True, "ai_enabled": True}, JobControl(), ai=client)
    md = next((p.root / "chapters").glob("ch_001*.md")).read_text()
    meta = md.split("---\n")[1]
    assert "- anemia" in meta and "- iron" in meta and "hematology" in meta.lower()
    assert {pu for pu, _ in client.prompts} >= {"chapter_metadata", "navigation_index"}
    # only headings/titles are sent for metadata & navigation — never body paragraphs
    sent = " ".join(u for pu, u in client.prompts if pu in ("chapter_metadata", "navigation_index"))
    assert "Ferrous sulfate" not in sent and "Definition" in sent
    # invented terms are discarded from navigation indexes
    diseases = (p.root / "indexes/diseases.md").read_text()
    assert "INVENTED TERM" not in diseases and "Definition" in diseases
    assert "Generated navigation index" in diseases
    # usage log shows what was sent where
    assert client.requests >= 2 and client.chars_sent > 0
