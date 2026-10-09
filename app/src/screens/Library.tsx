import { useEffect, useState } from "react";
import { coverUrl, get, post, api } from "../api";
import type { Ctx } from "../App";
import type { LibraryItem, Overview } from "../types";
import { PathPicker } from "./common";

export default function Library({ ctx }: { ctx: Ctx }) {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [picker, setPicker] = useState(false);
  const load = () => get("/api/projects").then((r) => setItems(r.projects)).catch((e) => ctx.toast(e.message, "err"));
  useEffect(() => { load(); }, []);
  const open = async (it: LibraryItem) => {
    try { const ov = await get<Overview>(`/api/projects/${it.id}`); ctx.setProject(ov); ctx.go("overview"); }
    catch (e: any) { ctx.toast(e.message, "err"); }
  };
  const openPath = async (path: string) => {
    setPicker(false);
    try { const ov = await post<Overview>("/api/projects/open", { path }); ctx.setProject(ov); ctx.go("overview"); }
    catch (e: any) { ctx.toast(e.message, "err"); }
  };
  const remove = async (it: LibraryItem) => {
    if (!confirm(`Remove “${it.title}” from the library? Files on disk are kept.`)) return;
    await api("DELETE", `/api/projects/${it.id}`); load();
  };
  return (
    <section>
      <header className="page-head"><h1>Projects</h1>
        <div className="row"><button onClick={() => setPicker(true)}>Open existing folder…</button><button className="primary" onClick={() => ctx.go("new")}>New book project</button></div>
      </header>
      {items.length === 0 && <p className="muted">No books yet. Create a project from a textbook PDF — the original file is never modified.</p>}
      <div className="cards">
        {items.map((it) => (
          <article key={it.id} className="card book" onDoubleClick={() => open(it)}>
            <div className="cover">{it.has_cover ? <img src={coverUrl(it.id)} alt="" /> : <span>PDF</span>}</div>
            <div className="meta">
              <h3>{it.title}</h3>
              <p className="muted">{it.edition ? `Edition ${it.edition} · ` : ""}{it.missing ? "folder missing" : `${it.progress?.done}/${it.progress?.total} chapters`}</p>
              {it.progress && <div className="bar"><div style={{ width: `${it.progress.percent}%` }} /></div>}
              <p className="muted small">Last opened {it.last_opened?.slice(0, 16).replace("T", " ") ?? "—"}</p>
              <div className="row"><button className="primary" disabled={it.missing} onClick={() => open(it)}>Open</button><button onClick={() => remove(it)}>Remove</button></div>
            </div>
          </article>
        ))}
      </div>
      {picker && <PathPicker mode="dir" onPick={openPath} onClose={() => setPicker(false)} />}
    </section>
  );
}
