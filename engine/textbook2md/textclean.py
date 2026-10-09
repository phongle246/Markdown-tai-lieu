"""Deterministic text cleanup: ligatures, dehyphenation, line joining, span → Markdown.

Nothing here rewrites wording. All transformations are reversible in spirit
(removing artificial line breaks / encoding artefacts only).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BOLD, ITALIC, SUP, SUB = 1, 2, 4, 8

LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
    "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}
_STRIP = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_SPACES = dict.fromkeys(map(ord, "       \t"), " ")
HYPHENS = "-‐‑"
SOFT_HYPHEN = "­"
PUA_RE = re.compile("[-]")
CID_RE = re.compile(r"\(cid:\d+\)")

COMPOUND_PREFIXES = {
    "non", "anti", "self", "long", "short", "well", "high", "low", "post", "semi", "multi",
    "cross", "half", "full", "mid", "inter", "intra", "extra", "ultra", "co", "pro", "all",
    "x", "t", "b", "type", "case", "dose", "follow", "life", "time", "risk", "second", "first",
    "third", "single", "double", "two", "three", "four", "five", "ten", "early", "late", "near",
}
COMMON_SUFFIXES = (
    "tion", "sion", "ment", "ness", "ity", "ical", "ology", "ative", "ing", "ed", "ly", "ous",
    "able", "ible", "ance", "ence", "ism", "itis", "emia", "osis", "pathy", "ation", "ator",
    "ized", "ised", "ies", "ate", "ant", "ent", "ive", "ular", "ary", "ory", "ics", "oma",
    "ogenic", "plasia", "trophy", "scopy", "tomy", "stomy", "uria", "penia", "cytosis",
)


@dataclass
class Span:
    text: str
    bits: int = 0
    mark: int | None = None   # source-page transition marker rendered before this span

    def to_json(self):
        return [self.text, self.bits]

    @staticmethod
    def from_json(j):
        return Span(j[0], j[1])


def normalize_chars(s: str) -> str:
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    s = s.translate(_STRIP).translate(_SPACES)
    s = "".join(ch for ch in s if ch == "\n" or ch >= " " and ch != "\x7f")
    return s


def has_encoding_artifacts(s: str) -> bool:
    return bool(PUA_RE.search(s) or CID_RE.search(s) or "�" in s)


class Vocab:
    """Words observed un-hyphenated / hyphenated *inside* lines of the source itself."""

    def __init__(self):
        self.plain: set[str] = set()
        self.hyph: set[str] = set()

    def feed(self, text: str) -> None:
        for tok in re.findall(r"[A-Za-z][A-Za-z\-]*[A-Za-z]|[A-Za-z]", text):
            t = tok.lower()
            if "-" in t:
                self.hyph.add(t)
            elif len(t) >= 4:
                self.plain.add(t)

    def to_json(self):
        return {"plain": sorted(self.plain)[:150000], "hyph": sorted(self.hyph)[:50000]}

    @staticmethod
    def from_json(j):
        v = Vocab()
        if j:
            v.plain = set(j.get("plain", []))
            v.hyph = set(j.get("hyph", []))
        return v

    def update(self, other: "Vocab"):
        self.plain |= other.plain
        self.hyph |= other.hyph


JOIN, KEEP, UNCERTAIN = "join", "keep", "uncertain"


def decide_hyphen(prefix: str, suffix: str, vocab: Vocab | None) -> str:
    if not suffix or suffix[0].isupper() or not suffix[0].isalpha():
        return KEEP
    p, s = prefix.lower(), suffix.lower()
    joined, hy = p + s, f"{p}-{s}"
    if vocab:
        if joined in vocab.plain:
            return JOIN
        if hy in vocab.hyph:
            return KEEP
    if p in COMPOUND_PREFIXES or len(p) <= 1:
        return KEEP
    if len(p) >= 3 and any(s == suf or s.endswith(suf) for suf in COMMON_SUFFIXES) and len(s) <= 12:
        return JOIN
    return UNCERTAIN


@dataclass
class JoinLog:
    dehyphenated: list[str] = field(default_factory=list)
    kept_hyphen: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)


def _tail_word(text: str) -> str:
    m = re.search(r"([A-Za-z]+)$", text)
    return m.group(1) if m else ""


def _head_word(text: str) -> str:
    m = re.match(r"([A-Za-z]+)", text)
    return m.group(1) if m else ""


def join_span_lines(lines: list[list[Span]], vocab: Vocab | None = None,
                    log: JoinLog | None = None) -> list[Span]:
    """Join physical lines into one logical paragraph's spans."""
    out: list[Span] = []
    log = log if log is not None else JoinLog()
    for i, line in enumerate(lines):
        line = [Span(s.text, s.bits, s.mark) for s in line if s.text != ""]
        if not line:
            continue
        if not out:
            out.extend(line)
            continue
        prev_text = "".join(s.text for s in out).rstrip()
        nxt_text = "".join(s.text for s in line).lstrip()
        tail = prev_text[-1:]
        if tail and (tail == SOFT_HYPHEN or tail in HYPHENS) and len(prev_text) > 1 and not prev_text[-2].isspace() \
                and nxt_text:
            soft = tail == SOFT_HYPHEN
            before = prev_text[:-1]
            pw, sw = _tail_word(before), _head_word(nxt_text)
            if soft:
                verdict = JOIN
            elif pw and sw and before[-len(pw) - 1:-len(pw)] not in tuple(HYPHENS):
                verdict = decide_hyphen(pw, sw, vocab)
            else:
                verdict = KEEP            # digits / capitals after the hyphen: keep as in source, no space
            _strip_trailing(out, 1, keep_hyphen=(verdict != JOIN))
            _lstrip(line)
            out.extend(line)
            if pw and sw:
                word = f"{pw}-{sw}"
                {JOIN: log.dehyphenated, KEEP: log.kept_hyphen, UNCERTAIN: log.uncertain}[verdict].append(word)
            continue
        _rstrip(out)
        _lstrip(line)
        out.append(Span(" ", out[-1].bits if out else 0))
        out.extend(line)
    return out


def _strip_trailing(spans: list[Span], n: int, keep_hyphen: bool) -> None:
    # remove trailing whitespace, then the hyphen char (unless kept)
    _rstrip(spans)
    if not spans:
        return
    last = spans[-1]
    if last.text and (last.text[-1] in HYPHENS or last.text[-1] == SOFT_HYPHEN):
        if keep_hyphen and last.text[-1] != SOFT_HYPHEN:
            last.text = last.text[:-1] + "-"
        else:
            last.text = last.text[:-1]


def _rstrip(spans: list[Span]) -> None:
    while spans:
        t = spans[-1].text.rstrip()
        if t:
            spans[-1].text = t
            return
        spans.pop()


def _lstrip(spans: list[Span]) -> None:
    while spans:
        t = spans[0].text.lstrip()
        if t:
            spans[0].text = t
            return
        spans.pop(0)


def spans_plain(spans: list[Span]) -> str:
    return "".join(s.text for s in spans)


_ESC_RE = re.compile(r"([*`\\])|(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])|<(?=[A-Za-z!?/])")


def escape_md(text: str) -> str:
    return _ESC_RE.sub(lambda m: "\\" + m.group(0), text)


def escape_line_start(text: str) -> str:
    """Prevent accidental block syntax at the start of a plain paragraph."""
    if re.match(r"^(#{1,6}\s|>|[-+*]\s|\d+[.)]\s|={3,}|-{3,}|\|)", text):
        return "\\" + text
    return text


def merge_runs(spans: list[Span]) -> list[Span]:
    out: list[Span] = []
    for s in spans:
        if not s.text:
            continue
        if out and out[-1].bits == s.bits and s.mark is None:
            out[-1] = Span(out[-1].text + s.text, s.bits, out[-1].mark)
        else:
            out.append(Span(s.text, s.bits, s.mark))
    return out


def spans_to_md(spans: list[Span], strip_bold: bool = False, escape: bool = True) -> str:
    parts = []
    for run in merge_runs(spans):
        bits = run.bits & ~BOLD if strip_bold else run.bits
        txt = run.text
        if run.mark is not None:
            parts.append(f" <!-- source_page: {run.mark} --> ")
        lead = txt[: len(txt) - len(txt.lstrip())]
        trail = txt[len(txt.rstrip()):]
        core = txt.strip()
        if not core:
            parts.append(txt)
            continue
        core = escape_md(core) if escape else core
        if bits & SUP:
            core = f"<sup>{core}</sup>"
        elif bits & SUB:
            core = f"<sub>{core}</sub>"
        b, i = bool(bits & BOLD), bool(bits & ITALIC)
        if b and i:
            core = f"***{core}***"
        elif b:
            core = f"**{core}**"
        elif i:
            core = f"*{core}*"
        parts.append(lead + core + trail)
    return re.sub(r"[ ]{2,}", " ", "".join(parts)).strip()


TERMINAL_RE = re.compile(r"[.!?:;)\]\"'”’]\s*$")


def ends_sentence(text: str) -> bool:
    return bool(TERMINAL_RE.search(text.rstrip()))


def starts_continuation(text: str) -> bool:
    t = text.lstrip()
    return bool(t) and (t[0].islower() or t[0] in ",;)]")
