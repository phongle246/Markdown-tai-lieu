"""Provider abstraction (OpenAI / Gemini). AI is optional and never the source of textbook text."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .util import now_iso

DEFAULTS = {"openai": "gpt-4o-mini", "gemini": "gemini-2.0-flash"}


class AIError(Exception):
    pass


def settings_dir() -> Path:
    return Path(os.environ.get("T2MD_HOME") or Path.home() / ".textbook2md")


DEFAULT_SETTINGS = {
    "ai_enabled": False, "provider": "openai", "openai_api_key": "", "gemini_api_key": "",
    "openai_model": DEFAULTS["openai"], "gemini_model": DEFAULTS["gemini"],
    "ocr_enabled": True, "ocr_lang": "eng", "output_dir": str(Path.home() / "TextbookKB"),
    "theme": "system", "markdown_mode": "standard",
}


def load_settings() -> dict:
    p = settings_dir() / "settings.json"
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(p.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return s


def save_settings(new: dict) -> dict:
    cur = load_settings()
    for k, v in new.items():
        if k not in DEFAULT_SETTINGS:
            continue
        if k.endswith("_api_key") and (v is None or v == "" or set(str(v)) <= {"•", "*"}):
            continue  # masked placeholder → keep stored secret
        cur[k] = v
    d = settings_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / "settings.json"
    p.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return cur


def public_settings(s: dict | None = None) -> dict:
    s = dict(s or load_settings())
    for k in ("openai_api_key", "gemini_api_key"):
        s[k + "_set"] = bool(s.get(k))
        s[k] = "••••••••" if s.get(k) else ""
    return s


class AIClient:
    def __init__(self, settings: dict, log_path: Path | None = None):
        self.s = settings
        self.provider = settings.get("provider", "openai")
        self.log_path = log_path
        self.requests = 0
        self.chars_sent = 0

    @property
    def enabled(self) -> bool:
        return bool(self.s.get("ai_enabled") and self.key)

    @property
    def key(self) -> str:
        return self.s.get(f"{self.provider}_api_key", "")

    @property
    def model(self) -> str:
        return self.s.get(f"{self.provider}_model") or DEFAULTS[self.provider]

    def _log(self, purpose: str, prompt: str) -> None:
        self.requests += 1
        self.chars_sent += len(prompt)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": now_iso(), "provider": self.provider, "model": self.model,
                                    "purpose": purpose, "chars_sent": len(prompt), "preview": prompt[:240]},
                                   ensure_ascii=False) + "\n")

    def ask(self, purpose: str, system: str, user: str, max_chars: int = 6000, retries: int = 3) -> str:
        if not self.enabled:
            raise AIError("AI is disabled or no API key is set (Settings → AI provider).")
        user = user[:max_chars]
        self._log(purpose, system + "\n" + user)
        if self.provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
            body = {"model": self.model, "temperature": 0,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        elif self.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            headers = {"x-goog-api-key": self.key, "Content-Type": "application/json"}
            body = {"systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {"temperature": 0}}
        else:
            raise AIError(f"Unknown provider: {self.provider}")
        delay = 2.0
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read().decode())
                if self.provider == "openai":
                    return data["choices"][0]["message"]["content"]
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise AIError(f"{self.provider}: invalid API key or no access (HTTP {e.code}).") from e
                if e.code == 429 and attempt < retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise AIError(f"{self.provider}: HTTP {e.code} — {e.read().decode(errors='replace')[:200]}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise AIError(f"{self.provider}: network error — {e}") from e
        raise AIError("unreachable")

    def ask_json(self, purpose: str, system: str, user: str, **kw):
        txt = self.ask(purpose, system + "\nReturn ONLY valid JSON.", user, **kw).strip()
        if txt.startswith("```"):
            txt = txt.strip("`")
            txt = txt[txt.find("\n") + 1:] if txt.startswith("json") else txt
        try:
            return json.loads(txt)
        except json.JSONDecodeError as e:
            raise AIError(f"Model returned invalid JSON: {txt[:120]}") from e

    def test_connection(self) -> dict:
        try:
            out = self.ask("test_connection", "Reply with the single word OK.", "ping", max_chars=50, retries=0)
            return {"ok": True, "provider": self.provider, "model": self.model, "reply": out.strip()[:40]}
        except AIError as e:
            return {"ok": False, "provider": self.provider, "model": self.model, "error": str(e)}


SYS_HEADING = ("You classify whether a single line extracted from a textbook PDF is a section heading. "
               "Never rewrite text. Answer JSON: {\"heading\": true|false, \"level\": 2|3|4}.")


def heading_classifier(client: AIClient):
    def fn(text, paras, p):
        if not client.enabled:
            return None
        i = paras.index(p)
        prev = paras[i - 1].text[:100] if i > 0 else ""
        nxt = paras[i + 1].text[:100] if i + 1 < len(paras) else ""
        try:
            r = client.ask_json("heading_classification", SYS_HEADING,
                                f"PREVIOUS: {prev}\nLINE: {text}\nNEXT: {nxt}", max_chars=600)
            return int(r.get("level", 3)) if r.get("heading") else None
        except (AIError, ValueError, TypeError, AttributeError):
            return None
    return fn
