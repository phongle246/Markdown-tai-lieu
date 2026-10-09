import { useEffect, useRef, useState } from "react";
import { get } from "../api";
import type { Ctx } from "../App";
import type { SearchResult } from "../types";
import { snippetHtml } from "../markdown";

const TYPES = ["", "text", "heading", "list", "table", "figure", "callout", "reference", "formula", "footnote"];

export default function Search({ ctx }: { ctx: Ctx }) {
  const p = ctx.project!;
  const [q, setQ] = useState(ctx.nav.query ?? "");
  const [chapter, setChapter] = useState("");
  const [type, setType] = useState("");
  const [spec, setSpec] = useState("");
  const [res, setRes] = useState<SearchResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  useEffect(() => {
    if (!q.trim()) { setRes(null); return; }
    const my = ++seq.current;
    const t = setTimeout(async () => {
      setBusy(true);
      try {
        const r = await get(`/api/projects/${p.id}/search?q=${encodeURIComponent(q)}&chapter=${chapter}&type=${type}&specialty=${encodeURIComponent(spec)}`);
        if (my === seq.current) setRes(r.results);
      } catch (e: any) { ctx.toast(e.message, "err"); } finally { setBusy(false); }
    }, 200);
    return () => clearTimeout(t);
  }, [q, chapter, type, spec]);
  const done = p.chapters.filter((c) => c.has_markdown);
  return (
    <section>
      <header className="page-head"><h1>Search</h1><span className="muted">{p.book.title}{p.book.edition ? ` · ed. ${p.book.edition}` : ""} · {done.length} converted chapter(s)</span></header>
      <div className="row">
        <input autoFocus className="grow big" placeholder="Search the whole book (phrases in “quotes”, prefix match on the last word)…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={chapter} onChange={(e) => setChapter(e.target.value)}><option value="">All chapters</option>{done.map((c) => <option key={c.key} value={c.key}>{c.number} {c.title}</option>)}</select>
        <select value={type} onChange={(e) => setType(e.target.value)}>{TYPES.map((t) => <option key={t} value={t}>{t || "All content types"}</option>)}</select>
        <input style={{ width: 150 }} placeholder="Specialty" value={spec} onChange={(e) => setSpec(e.target.value)} />
      </div>
      {done.length === 0 && <p className="warn">Nothing is searchable yet — convert at least one chapter.</p>}
      {busy && <p className="muted small">Searching…</p>}
      {res && res.length === 0 && <p className="muted">No results.</p>}
      <div className="results">
        {res?.map((r) => (
          <article className="card result" key={r.block_id}>
            <div className="small muted">Ch {r.chapter_no ?? r.chapter} · {r.chapter_title} <span className={`chip type-${r.type}`}>{r.type}</span></div>
            <div className="path">{r.heading_path || r.chapter_title}</div>
            <p className="snip" dangerouslySetInnerHTML={{ __html: snippetHtml(r.snippet) }} />
            <div className="row">
              <span className="small muted">Source page {r.pages.join(", ")}</span><span className="grow" />
              <button onClick={() => ctx.go({ view: "review", chapter: r.chapter, block: r.block_id, page: r.page, focus: "md" })}>Open section</button>
              <button onClick={() => ctx.go({ view: "review", chapter: r.chapter, page: r.page, block: r.block_id, focus: "pdf" })}>Open source page {r.page}</button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
