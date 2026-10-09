import type { Chapter } from "../types";

/** Manual correction of detected chapters (titles, numbers, page ranges, add / remove). */
export default function ChapterEditor({ chapters, pages, onChange, locked }: { chapters: Chapter[]; pages: number; onChange: (c: Chapter[]) => void; locked?: Set<string> }) {
  const upd = (i: number, patch: Partial<Chapter>) => onChange(chapters.map((c, k) => (k === i ? { ...c, ...patch } : c)));
  const add = () => {
    const last = chapters[chapters.length - 1];
    const start = last ? Math.min(last.end + 1, pages) : 1;
    onChange([...chapters, { key: "", number: (last?.number ?? 0) + 1, title: "New chapter", start, end: Math.min(start, pages), part: "", confidence: 1, source: "manual", start_y: null, end_y: null, issues: [] }]);
  };
  return (
    <div className="card">
      <table className="grid">
        <thead><tr><th>#</th><th>Title</th><th>Start</th><th>End</th><th>Detected by</th><th></th></tr></thead>
        <tbody>
          {chapters.map((c, i) => {
            const bad = c.confidence < 0.7 || c.issues.length > 0;
            const dis = locked?.has(c.key);
            return (
              <tr key={i} className={bad ? "flag" : ""} title={c.issues.join("\n")}>
                <td><input className="num" disabled={dis} value={c.number ?? ""} onChange={(e) => upd(i, { number: e.target.value === "" ? null : +e.target.value })} /></td>
                <td><input disabled={dis} value={c.title} onChange={(e) => upd(i, { title: e.target.value })} /></td>
                <td><input className="num" disabled={dis} type="number" min={1} max={pages} value={c.start} onChange={(e) => upd(i, { start: +e.target.value, start_y: null })} /></td>
                <td><input className="num" disabled={dis} type="number" min={1} max={pages} value={c.end} onChange={(e) => upd(i, { end: +e.target.value, end_y: null })} /></td>
                <td>{c.source} · {(c.confidence * 100).toFixed(0)}%{bad && " ⚠ review"}</td>
                <td><button disabled={dis} onClick={() => onChange(chapters.filter((_, k) => k !== i))}>✕</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="row"><button onClick={add}>+ Add chapter</button></div>
    </div>
  );
}
