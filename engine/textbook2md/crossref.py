"""Cross-reference linking: 'see Chapter 201', 'Table 123.2', 'Fig. 123.4'.

Links are only added when the target is known (converted). Everything else is kept verbatim and
recorded in metadata/unresolved_references.json for later automatic resolution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

XREF_RE = re.compile(r"\b(?P<kind>Chapter|Table|Figure|Fig\.|Box)\s+(?P<num>\d+(?:\.\d+)*[A-Za-z]?)(?![\d])")
_SKIP_RE = re.compile(r"(<!--.*?-->|<[^>\n]+>|!\[[^\]]*\]\([^)]*\)|\[[^\]]*\]\([^)]*\)|`[^`]*`|\[\^[^\]]+\])", re.S)


@dataclass
class Ref:
    text: str
    kind: str          # chapter|table|figure|box
    num: str           # "123.2"
    label: str         # "Table 123.2" | "Chapter 201"
    target_chapter: int | None


def norm_kind(k: str) -> str:
    k = k.lower().rstrip(".")
    return "figure" if k in ("fig", "figure") else k


def parse(m: re.Match) -> Ref:
    kind = norm_kind(m.group("kind"))
    num = m.group("num")
    label = f"{kind.capitalize()} {num}"
    if kind == "chapter":
        tgt = int(re.match(r"\d+", num).group(0))
    elif "." in num:
        tgt = int(num.split(".")[0])
    else:
        tgt = None
    return Ref(m.group(0), kind, num, label, tgt)


Resolver = Callable[[Ref], "str | None"]


def link_text(md: str, resolver: Resolver) -> tuple[str, list[Ref], list[Ref]]:
    """Return (new_md, resolved_refs, unresolved_refs). Idempotent: existing links are left alone."""
    resolved: list[Ref] = []
    unresolved: list[Ref] = []
    out = []
    pos = 0
    for m in _SKIP_RE.finditer(md):
        out.append(_link_segment(md[pos:m.start()], resolver, resolved, unresolved))
        out.append(m.group(0))
        pos = m.end()
    out.append(_link_segment(md[pos:], resolver, resolved, unresolved))
    return "".join(out), resolved, unresolved


def _link_segment(seg: str, resolver, resolved, unresolved) -> str:
    def repl(m: re.Match) -> str:
        # skip when immediately preceded by "[" (already part of a link label) or in a bold caption label
        ref = parse(m)
        url = resolver(ref)
        if url:
            resolved.append(ref)
            return f"[{m.group(0)}]({url})"
        unresolved.append(ref)
        return m.group(0)
    return XREF_RE.sub(repl, seg)


def make_resolver(registry: dict, current_number: int | None, current_file: str,
                  current_labels: dict) -> Resolver:
    """registry: {chapter_number: {"file": "chapters/x.md", "labels": {label: {...}}}}"""
    def resolve(ref: Ref) -> str | None:
        if ref.kind == "chapter":
            ent = registry.get(ref.target_chapter)
            if not ent or ent["file"] == current_file:
                return None
            return "./" + ent["file"].split("/")[-1]
        tgt = ref.target_chapter if ref.target_chapter is not None else current_number
        labels = current_labels if tgt == current_number and ref.label in current_labels else None
        ent_file = None
        if labels is None:
            ent = registry.get(tgt)
            if not ent:
                return None
            labels = ent["labels"]
            ent_file = ent["file"]
        info = labels.get(ref.label)
        if not info:
            return None
        if info.get("table_file"):
            return info["table_file"]
        if ent_file and ent_file != current_file:
            return f"./{ent_file.split('/')[-1]}#{info['anchor']}"
        return f"#{info['anchor']}"
    return resolve
