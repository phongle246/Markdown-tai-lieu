import type { Ctx } from "../App";
import { StatusChip } from "./common";

const STAGES = ["Parsing pages", "Reading layout", "Extracting text", "Extracting tables", "Extracting figures", "Building Markdown", "Running QA", "Indexing"];

export default function Convert({ ctx }: { ctx: Ctx }) {
  const j = ctx.job, p = ctx.project!;
  const active = j && ["running", "paused"].includes(j.status);
  const prog = j?.progress ?? {};
  const cur = p.chapters.find((c) => c.key === prog.chapter);
  const stageIdx = STAGES.findIndex((s) => s === prog.stage);
  const pct = prog.total ? Math.round(((prog.done ?? 0) / prog.total) * 100) : 0;
  const overall = j && j.chapters.length ? Math.round((j.chapters.filter((k) => ["COMPLETED", "COMPLETED_WITH_WARNINGS", "REVIEW_REQUIRED", "FAILED"].includes(p.chapters.find((c) => c.key === k)?.state?.status ?? "")).length / j.chapters.length) * 100) : 0;
  return (
    <section>
      <header className="page-head"><h1>Convert</h1>
        <div className="row">
          {active && j!.status === "running" && <button onClick={() => ctx.jobAction("pause")}>Pause</button>}
          {active && j!.status === "paused" && <button className="primary" onClick={() => ctx.jobAction("resume")}>Resume</button>}
          {active && <button onClick={() => ctx.jobAction("cancel")}>Cancel</button>}
        </div>
      </header>
      {!j && <p className="muted">No conversion has been started in this session. Choose chapters in <a href="#" onClick={(e) => { e.preventDefault(); ctx.go("overview"); }}>Book overview</a>. Interrupted chapters resume from the last completed page.</p>}
      {j && (
        <div className="card">
          <p><b>{active ? (j.status === "paused" ? "Paused" : "Converting") : j.status === "cancelled" ? "Cancelled — resumable" : j.status === "done" ? "Finished" : j.status}</b>
            {cur && <> · chapter <b>{cur.number ?? ""} {cur.title}</b></>}{prog.page ? <> · page <b>{prog.page}</b></> : null}</p>
          <div className="stages">{STAGES.map((s, i) => <span key={s} className={`stage ${i < stageIdx || (!active && j.status === "done") ? "done" : i === stageIdx ? "now" : ""}`}>{s}</span>)}</div>
          <label className="small muted">This chapter · {prog.done ?? 0}/{prog.total ?? "?"} pages</label><div className="bar"><div style={{ width: `${pct}%` }} /></div>
          <label className="small muted">Selection · {overall}%</label><div className="bar"><div style={{ width: `${overall}%` }} /></div>
          {j.error && <p className="error">{j.error}</p>}
          <details><summary>Activity log</summary><pre className="log">{j.log.join("\n")}</pre></details>
        </div>
      )}
      <div className="card">
        <table className="grid"><thead><tr><th>Chapter</th><th>Pages</th><th>Status</th><th>Progress</th></tr></thead>
          <tbody>{p.chapters.filter((c) => !j || j.chapters.includes(c.key) || c.state?.status !== "NOT_STARTED").map((c) => (
            <tr key={c.key}><td>{c.number} {c.title}</td><td>{c.start}–{c.end}</td>
              <td><StatusChip status={c.state?.status} interrupted={c.interrupted && !(active && prog.chapter === c.key)} />{c.state?.error && <div className="error small">{c.state.error}</div>}</td>
              <td>{c.state?.pages_total ? `${c.state.pages_done ?? 0}/${c.state.pages_total}` : ""}</td></tr>))}</tbody></table>
      </div>
    </section>
  );
}
