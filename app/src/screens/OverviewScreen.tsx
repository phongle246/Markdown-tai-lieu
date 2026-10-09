import { useState } from "react";
import { get, post, put } from "../api";
import type { Ctx } from "../App";
import type { Chapter } from "../types";
import { StatusChip } from "./common";
import ChapterEditor from "./ChapterEditor";

export default function OverviewScreen({ ctx }: { ctx: Ctx }) {
  const p = ctx.project!;
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [edit, setEdit] = useState<Chapter[] | null>(null);
  const toggle = (k: string) => setSel((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });
  const saveChapters = async () => {
    try { const ov = await put(`/api/projects/${p.id}/chapters`, { chapters: edit }); ctx.setProject(ov); setEdit(null); ctx.toast("Chapters saved — changed chapters were reset"); }
    catch (e: any) { ctx.toast(e.message, "err"); }
  };
  const warnings = p.chapters.filter((c) => c.confidence < 0.7 || c.issues.length);
  const busy = !!ctx.job && ["running", "paused"].includes(ctx.job.status);
  return (
    <section>
      <header className="page-head"><h1>{p.book.title}</h1>
        <div className="row">
          <button onClick={() => ctx.go("search")}>Search book</button>
          <button onClick={() => post(`/api/projects/${p.id}/open-folder`).catch((e) => ctx.toast(e.message, "err"))}>Open output folder</button>
          <button onClick={() => ctx.go("export")}>Export ZIP</button>
        </div>
      </header>
      <div className="card facts">
        <div><label>Source</label><span title={p.book.source_file}>{p.book.source_file_name}</span></div>
        <div><label>SHA-256</label><code>{p.book.source_sha256.slice(0, 16)}…</code></div>
        <div><label>Edition</label><span>{p.book.edition || "—"}</span></div>
        <div><label>Pages</label><span>{p.book.pages}</span></div>
        <div><label>Project folder</label><span title={p.root}>{p.root}</span></div>
        <div><label>Progress</label><span>{p.progress.done}/{p.progress.total} chapters ({p.progress.percent}%)</span></div>
      </div>
      {warnings.length > 0 && <p className="warn">⚠ {warnings.length} chapter(s) have low-confidence or conflicting boundaries — please review them (Edit chapters).</p>}
      <div className="row">
        <button className="primary" disabled={busy || !sel.size} onClick={() => ctx.startJob([...sel])}>Convert selected ({sel.size})</button>
        <button disabled={busy} onClick={() => ctx.startJob("all")}>Convert all</button>
        <button onClick={() => setEdit(edit ? null : p.chapters.map((c) => ({ ...c })))}>{edit ? "Cancel editing" : "Edit chapters"}</button>
      </div>
      {edit ? (
        <>
          <ChapterEditor chapters={edit} pages={p.book.pages} onChange={setEdit} />
          <div className="row end"><button className="primary" onClick={saveChapters}>Save chapters</button></div>
        </>
      ) : (
        <div className="card">
          <table className="grid">
            <thead><tr><th><input type="checkbox" checked={sel.size === p.chapters.length} onChange={(e) => setSel(e.target.checked ? new Set(p.chapters.map((c) => c.key)) : new Set())} /></th>
              <th>#</th><th>Chapter</th><th>Pages</th><th>Status</th><th>Issues</th><th></th></tr></thead>
            <tbody>
              {p.chapters.map((c) => {
                const st = c.state?.status;
                const cnt = c.state?.counts ?? {};
                return (
                  <tr key={c.key} className={c.confidence < 0.7 ? "flag" : ""}>
                    <td><input type="checkbox" checked={sel.has(c.key)} onChange={() => toggle(c.key)} /></td>
                    <td>{c.number ?? ""}</td>
                    <td><b>{c.title}</b>{c.part && <small className="muted"> · {c.part}</small>}
                      {st === "PROCESSING" && c.state?.pages_total ? <small className="muted"> · {c.state.pages_done}/{c.state.pages_total} pages</small> : null}
                      {c.state?.error && <div className="error small">{c.state.error}</div>}</td>
                    <td>{c.start}–{c.end}</td>
                    <td><StatusChip status={st} interrupted={c.interrupted && !(busy && ctx.job?.progress.chapter === c.key)} /></td>
                    <td className="small">{(cnt.CRITICAL ?? 0) + (cnt.HIGH ?? 0) > 0 ? <b className="error">{(cnt.CRITICAL ?? 0) + (cnt.HIGH ?? 0)} high</b> : null} {cnt.MEDIUM ? `${cnt.MEDIUM} med ` : ""}{cnt.LOW ? `${cnt.LOW} low` : ""}</td>
                    <td className="row">
                      <button disabled={busy} onClick={() => ctx.startJob([c.key], !c.interrupted && !!st && st !== "NOT_STARTED")}>{c.interrupted ? "Resume" : st && st !== "NOT_STARTED" ? "Re-run" : "Convert"}</button>
                      {c.has_markdown && <button onClick={() => ctx.go({ view: "review", chapter: c.key })}>Review</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
