"""Numeric fingerprinting: every number in the source must survive into the Markdown."""
from __future__ import annotations

import html
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

# optional comparator prefix glued to the number; decimals / thousands; optional percent
NUM_RE = re.compile(r"(?<![A-Za-z0-9])(?:[<>≤≥±~])?\s?\d+(?:[.,]\d+)*(?:\s?%)?")
UNIT_RE = re.compile(
    r"^\s?(?:mg|mcg|µg|μg|ug|g|kg|ml|mL|dL|L|mmol|mEq|IU|units?|U|mmHg|bpm|cm|mm|m2|"
    r"mg/|mcg/|µg/|%|/min|/dose|ng|pg|mOsm|kcal|J|Gy|°C|°F)", re.I)


def tokens(text: str) -> list[str]:
    out = []
    for m in NUM_RE.finditer(unicodedata.normalize("NFC", text)):
        t = re.sub(r"\s+", "", m.group(0))
        out.append(t.rstrip(".,"))
    return [t for t in out if t]


def strip_markdown(md: str) -> str:
    """Remove syntax so only source-derived text remains (for fingerprint comparison)."""
    t = re.sub(r"^---\n.*?\n---\n", "", md, count=1, flags=re.S)
    t = re.sub(r"<!-- generated:toc -->.*?<!-- /generated:toc -->", "", t, flags=re.S)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"\[\^fn\d+-([^\]]+)\](:?)", r"\1 ", t)         # footnote label → its source marker
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)          # images (alt text is generated)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)       # links → text
    t = re.sub(r"</?(?:sup|sub)>", "", t)
    t = re.sub(r"<br\s*/?>", " ", t)
    t = re.sub(r"<a [^>]*></a>", "", t)
    t = re.sub(r"</?(?:t[dhr]|thead|tbody|table|details|summary|p|div)[^>]*>", " ", t)
    t = re.sub(r"<(https?://[^>]+)>", r"\1", t)
    t = re.sub(r"^[ \t]*(?:>[ \t]?)+", "", t, flags=re.M)             # blockquote markers
    t = t.replace("|", " ")
    t = re.sub(r"\\([\\`*_{}\[\]()#+\-.!<>|])", r"\1", t)
    t = t.replace("*", "")
    t = re.sub(r"^\s*-{3,}\s*$", " ", t, flags=re.M)      # GFM table delimiter rows / hr
    t = re.sub(r"^\s*:?-{2,}:?(?:\s*\|?\s*:?-{2,}:?)*\s*$", " ", t, flags=re.M)
    return html.unescape(t)               # HTML-table cells escape &lt; &amp;


@dataclass
class NumIssue:
    kind: str            # MISSING_NUMBER | CHANGED_NUMBER | UNEXPECTED_NUMBER
    severity: str
    source: str | None
    markdown: str | None
    page: int | None = None
    context: str = ""


def _clinical(tok: str, ctx: str) -> bool:
    return bool("%" in tok or "." in tok or UNIT_RE.match(ctx or "") or tok[:1] in "<>≤≥±")


def _similar(a: str, b: str) -> bool:
    da, db = re.sub(r"\D", "", a), re.sub(r"\D", "", b)
    if da == db and a != b:
        return True                                  # separator/sign/percent change
    if sorted(da) == sorted(db) and len(da) > 1:
        return True                                  # transposition
    if len(da) == len(db) and sum(x != y for x, y in zip(da, db)) == 1:
        return True                                  # single digit changed
    if abs(len(da) - len(db)) == 1 and (da.startswith(db) or db.startswith(da) or da.endswith(db) or db.endswith(da)):
        return True                                  # digit dropped/added
    return False


def compare(source: list[tuple[str, int, str]], md_tokens: list[tuple[str, int | None]]) -> list[NumIssue]:
    """source: (token, page, ctx); md_tokens: (token, page|None). Multiset comparison."""
    sc = Counter(t for t, _, _ in source)
    mc = Counter(t for t, _ in md_tokens)
    missing = sc - mc
    unexpected = mc - sc
    info_s: dict[str, list[tuple[int, str]]] = {}
    for t, p, c in source:
        info_s.setdefault(t, []).append((p, c))
    info_m: dict[str, list[int | None]] = {}
    for t, p in md_tokens:
        info_m.setdefault(t, []).append(p)
    issues: list[NumIssue] = []
    miss_list = [t for t, n in sorted(missing.items()) for _ in range(n)]
    unex_list = [t for t, n in sorted(unexpected.items()) for _ in range(n)]
    for m in list(miss_list):
        for u in list(unex_list):
            if _similar(m, u):
                p, c = info_s[m][0]
                sev = "CRITICAL" if _clinical(m, c) else "HIGH"
                issues.append(NumIssue("CHANGED_NUMBER", sev, m, u, p, c))
                miss_list.remove(m)
                unex_list.remove(u)
                break
    for m in miss_list:
        p, c = info_s[m][0]
        sev = "HIGH" if _clinical(m, c) else "MEDIUM"
        issues.append(NumIssue("MISSING_NUMBER", sev, m, None, p, c))
    for u in unex_list:
        sev = "MEDIUM"
        issues.append(NumIssue("UNEXPECTED_NUMBER", sev, None, u, (info_m[u] or [None])[0]))
    return issues
