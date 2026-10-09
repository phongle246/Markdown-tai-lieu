import { useState } from "react";
import { post } from "../api";
import type { Ctx } from "../App";
import { PathPicker } from "./common";

export default function Export({ ctx }: { ctx: Ctx }) {
  const p = ctx.project!;
  const [dir, setDir] = useState("");
  const [picker, setPicker] = useState(false);
  const [pdf, setPdf] = useState(false);
  const [chapter, setChapter] = useState(p.chapters.find((c) => c.has_markdown)?.key ?? "");
  const [out, setOut] = useState("");
  const slug = p.root.split("/").pop();
  const run = async (kind: "zip" | "rag" | "chapter") => {
    if (!dir) { ctx.toast("Choose a destination folder first", "err"); return; }
    const name = kind === "zip" ? `${slug}.zip` : kind === "rag" ? `${slug}_rag.zip` : `${chapter}.zip`;
    try {
      const r = await post(`/api/projects/${p.id}/export`, { kind, dest: `${dir}/${name}`, include_pdf: pdf, key: chapter, markdown_mode: ctx.settings?.markdown_mode });
      setOut(r.path); ctx.toast(`Exported to ${r.path}`);
    } catch (e: any) { ctx.toast(e.message, "err"); }
  };
  return (
    <section>
      <header className="page-head"><h1>Export</h1></header>
      <div className="card form">
        <label>Destination folder<span className="row"><input className="grow" value={dir} onChange={(e) => setDir(e.target.value)} placeholder="/path/to/folder" /><button onClick={() => setPicker(true)}>Browse…</button></span></label>
        <label className="inline"><input type="checkbox" checked={pdf} onChange={(e) => setPdf(e.target.checked)} /> Bundle the original PDF in the ZIP (off by default)</label>
        <p className="muted small">Markdown flavour for exports: <b>{ctx.settings?.markdown_mode ?? "standard"}</b> (change in Settings). The canonical project always stays portable GFM/CommonMark.</p>
      </div>
      <div className="cards">
        <article className="card"><h3>Project ZIP</h3><p className="muted">Whole knowledge base: chapters, assets, tables, source maps, indexes, RAG, reports. Unzip anywhere — links keep working.</p><button className="primary" onClick={() => run("zip")}>Export ZIP</button></article>
        <article className="card"><h3>RAG package</h3><p className="muted"><code>rag/chunks.jsonl</code> + source maps + manifest for retrieval pipelines.</p><button onClick={() => run("rag")}>Export RAG package</button></article>
        <article className="card"><h3>Single chapter</h3>
          <select value={chapter} onChange={(e) => setChapter(e.target.value)}>{p.chapters.filter((c) => c.has_markdown).map((c) => <option key={c.key} value={c.key}>{c.number} {c.title}</option>)}</select>
          <p className="muted">Chapter Markdown with its figures, tables, source map and report.</p><button onClick={() => run("chapter")} disabled={!chapter}>Export chapter</button></article>
      </div>
      {out && <p className="ok">✔ {out}</p>}
      {picker && <PathPicker mode="dir" onPick={(d) => { setDir(d); setPicker(false); }} onClose={() => setPicker(false)} />}
    </section>
  );
}
