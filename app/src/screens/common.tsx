import { useEffect, useState } from "react";
import { get } from "../api";

export const STATUS_LABEL: Record<string, string> = {
  NOT_STARTED: "Not started", PROCESSING: "Processing", COMPLETED: "Completed",
  COMPLETED_WITH_WARNINGS: "Completed · warnings", REVIEW_REQUIRED: "Review required", FAILED: "Failed",
};
export function StatusChip({ status, interrupted }: { status?: string; interrupted?: boolean }) {
  const s = status ?? "NOT_STARTED";
  const label = interrupted && s === "PROCESSING" ? "Interrupted · resume" : STATUS_LABEL[s] ?? s;
  return <span className={`chip st-${interrupted && s === "PROCESSING" ? "INTERRUPTED" : s}`}>{label}</span>;
}
export const SevChip = ({ s }: { s: string }) => <span className={`chip sev-${s}`}>{s}</span>;

/** Server-side file browser (works identically inside the desktop shell and a browser). */
export function PathPicker({ mode, onPick, onClose, start }: { mode: "pdf" | "dir"; onPick: (p: string) => void; onClose: () => void; start?: string }) {
  const [state, setState] = useState<{ path: string; parent: string; items: { name: string; dir: boolean; path: string }[] } | null>(null);
  const [err, setErr] = useState("");
  const load = (p?: string) => get(`/api/fs?path=${encodeURIComponent(p ?? "")}`).then(setState).catch((e) => setErr(e.message));
  useEffect(() => { load(start); }, []);
  return (
    <div className="modal" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h3>{mode === "pdf" ? "Choose a PDF" : "Choose a folder"}</h3>
        <div className="pathbar"><button onClick={() => state && load(state.parent)}>↑</button><input value={state?.path ?? ""} onChange={(e) => state && setState({ ...state, path: e.target.value })} onKeyDown={(e) => e.key === "Enter" && load(state?.path)} /></div>
        {err && <p className="error">{err}</p>}
        <ul className="files">
          {state?.items.filter((i) => i.dir || mode === "pdf").map((i) => (
            <li key={i.path} onClick={() => (i.dir ? load(i.path) : onPick(i.path))}>{i.dir ? "📁" : "📄"} {i.name}</li>
          ))}
        </ul>
        <div className="row end">
          {mode === "dir" && <button className="primary" onClick={() => state && onPick(state.path)}>Use this folder</button>}
          <button onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
