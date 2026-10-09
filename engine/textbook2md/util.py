from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(text: str) -> str:
    """Short, whitespace/markup-insensitive fingerprint of a block of text."""
    norm = re.sub(r"[^0-9a-z]+", "", unicodedata.normalize("NFKC", text).lower())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def slugify(text: str, max_len: int = 60) -> str:
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:max_len].strip("-") or "untitled"


def atomic_write_text(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_write_json(path: str | Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def github_anchor_slugs(headings: list[str]) -> list[str]:
    """GitHub-style heading anchors, with -1/-2 suffixes for duplicates."""
    seen: dict[str, int] = {}
    out = []
    for h in headings:
        s = re.sub(r"[^\w\- ]", "", h.lower(), flags=re.UNICODE).strip().replace(" ", "-")
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.append(s if n == 0 else f"{s}-{n}")
    return out


def now_iso() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")
