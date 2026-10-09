import { useState } from "react";
import { post } from "../api";
import type { Ctx } from "../App";
import type { Book, Chapter, Overview } from "../types";
import { PathPicker } from "./common";
import ChapterEditor from "./ChapterEditor";

export default function NewProject({ ctx }: { ctx: Ctx }) {
  const [pdf, setPdf] = useState("");
  const [picker, setPicker] = useState<"pdf" | "dir" | null>(null);
  const [info, setInfo] = useState<{ book: Book; chapters: Chapter[]; warnings: string[] } | null>(null);
  const [out, setOut] = useState("");
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("");
  const [edition, setEdition] = useState("");

  const inspect = async (p: string) => {
    setPicker(null); setPdf(p); setBusy(true);
    try {
      const r = await post("/api/inspect", { pdf: p });
      setInfo(r); setTitle(r.book.title); setEdition(r.book.edition);
    } catch (e: any) { ctx.toast(e.message, "err"); setInfo(null); } finally { setBusy(false); }
  };
  const create = async () => {
    if (!info) return;
    setBusy(true);
    try {
      const ov = await post<Overview>("/api/projects", { pdf, title, edition, out_dir: out || undefined, chapters: info.chapters });
      ctx.setProject(ov); ctx.toast("Project created"); ctx.go("overview");
    } catch (e: any) { ctx.toast(e.message, "err"); } finally { setBusy(false); }
  };
  return (
    <section>
      <header className="page-head"><h1>New book project</h1></header>
      <ol className="steps"><li className={pdf ? "done" : "now"}>Select PDF</li><li className={info ? "done" : ""}>Inspect &amp; detect chapters</li><li>Review chapters</li><li>Create &amp; convert</li></ol>
      <div className="row"><input readOnly value={pdf} placeholder="No PDF selected" className="grow" /><button className="primary" onClick={() => setPicker("pdf")}>Select PDF…</button></div>
      {busy && <p className="muted">Inspecting source…</p>}
      {info && (
        <>
          <div className="card form">
            <label>Title<input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
            <label>Edition<input value={edition} onChange={(e) => setEdition(e.target.value)} /></label>
            <label>Output folder (optional)<span className="row"><input className="grow" value={out} onChange={(e) => setOut(e.target.value)} placeholder="default: Settings → output folder" /><button onClick={() => setPicker("dir")}>Browse…</button></span></label>
            <p className="muted">{info.book.pages} pages · outline {info.book.has_outline ? "found" : "not found"} · {info.chapters.length} chapter(s) detected</p>
          </div>
          {info.warnings.map((w) => <p key={w} className="warn">⚠ {w}</p>)}
          <ChapterEditor chapters={info.chapters} pages={info.book.pages} onChange={(c) => setInfo({ ...info, chapters: c })} />
          <div className="row end"><button className="primary" disabled={busy} onClick={create}>Create project</button></div>
        </>
      )}
      {picker && <PathPicker mode={picker} onPick={(p) => (picker === "pdf" ? inspect(p) : (setOut(p + "/" + (title || "book").replace(/\W+/g, "_")), setPicker(null)))} onClose={() => setPicker(null)} />}
    </section>
  );
}
