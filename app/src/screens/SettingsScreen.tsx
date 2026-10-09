import { useEffect, useState } from "react";
import { get, post, put } from "../api";
import type { Ctx } from "../App";
import type { Settings } from "../types";
import { PathPicker } from "./common";

export default function SettingsScreen({ ctx }: { ctx: Ctx }) {
  const [s, setS] = useState<Settings | null>(ctx.settings);
  const [test, setTest] = useState<string>("");
  const [picker, setPicker] = useState(false);
  const [ocr, setOcr] = useState<boolean | null>(null);
  const [log, setLog] = useState<any[]>([]);
  useEffect(() => { setS(ctx.settings); }, [ctx.settings]);
  useEffect(() => { get("/api/health").then((h) => setOcr(h.ocr_available)).catch(() => {}); }, []);
  useEffect(() => { if (ctx.project) get(`/api/projects/${ctx.project.id}/ai-log`).then((r) => setLog(r.items)).catch(() => {}); }, [ctx.project?.id]);
  if (!s) return <section><p className="muted">Loading settings…</p></section>;
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS({ ...s, [k]: v });
  const save = async () => { try { await put("/api/settings", s); await ctx.reloadSettings(); ctx.toast("Settings saved"); } catch (e: any) { ctx.toast(e.message, "err"); } };
  const doTest = async () => {
    setTest("Testing…");
    try {
      const r = await post("/api/settings/test", s);
      setTest(r.ok ? `✔ Connected to ${r.provider} (${r.model})` : `✖ ${r.error}`);
    } catch (e: any) { setTest(`✖ ${e.message}`); }
  };
  const prov = s.provider;
  return (
    <section>
      <header className="page-head"><h1>Settings</h1><button className="primary" onClick={save}>Save</button></header>
      <div className="card form">
        <h3>AI provider (optional)</h3>
        <p className="muted small">AI is used only for ambiguous heading classification, chapter metadata and navigation indexes. Canonical text always comes from the PDF. Only short snippets (a heading line and its neighbours, or heading lists) are sent — see the usage log below.</p>
        <label className="inline"><input type="checkbox" checked={s.ai_enabled} onChange={(e) => set("ai_enabled", e.target.checked)} /> AI enhancements ON</label>
        <label>Provider<select value={prov} onChange={(e) => set("provider", e.target.value as any)}><option value="openai">OpenAI</option><option value="gemini">Gemini</option></select></label>
        <label>API key {s[`${prov}_api_key_set` as const] ? <span className="ok small">(stored)</span> : null}
          <input type="password" autoComplete="off" value={s[`${prov}_api_key` as const]} placeholder="paste your key" onChange={(e) => set(`${prov}_api_key` as any, e.target.value)} /></label>
        <label>Model<input value={prov === "openai" ? s.openai_model : s.gemini_model} onChange={(e) => set((prov === "openai" ? "openai_model" : "gemini_model") as any, e.target.value)} /></label>
        <div className="row"><button onClick={doTest}>Test connection</button><span className={test.startsWith("✔") ? "ok" : "error"}>{test}</span></div>
        <p className="muted small">The key is stored locally in <code>~/.textbook2md/settings.json</code> (mode 600) and is only sent to the selected provider.</p>
      </div>
      <div className="card form">
        <h3>OCR</h3>
        <label className="inline"><input type="checkbox" checked={s.ocr_enabled} onChange={(e) => set("ocr_enabled", e.target.checked)} /> Use OCR for pages without native text</label>
        <label>Language<input value={s.ocr_lang} onChange={(e) => set("ocr_lang", e.target.value)} /></label>
        <p className={ocr ? "ok small" : "warn small"}>{ocr === null ? "" : ocr ? "✔ Tesseract found." : "Tesseract is not installed — scanned pages are kept as images and flagged “unreadable” (install: brew install tesseract / apt install tesseract-ocr)."}</p>
      </div>
      <div className="card form">
        <h3>Output &amp; appearance</h3>
        <label>Default output folder<span className="row"><input className="grow" value={s.output_dir} onChange={(e) => set("output_dir", e.target.value)} /><button onClick={() => setPicker(true)}>Browse…</button></span></label>
        <label>Theme<select value={s.theme} onChange={(e) => set("theme", e.target.value as any)}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
        <label>Markdown mode (for exports)<select value={s.markdown_mode} onChange={(e) => set("markdown_mode", e.target.value as any)}><option value="standard">Standard GFM / CommonMark</option><option value="obsidian">Obsidian (callouts + wikilinks)</option></select></label>
      </div>
      {ctx.project && (
        <div className="card"><h3>AI usage for this project</h3>
          {log.length === 0 ? <p className="muted">No AI requests have been made.</p> : (
            <table className="grid small"><thead><tr><th>When</th><th>Provider</th><th>Purpose</th><th>Chars sent</th><th>Preview of data sent</th></tr></thead>
              <tbody>{log.slice(-30).map((l, i) => <tr key={i}><td>{l.at.slice(11, 19)}</td><td>{l.provider}/{l.model}</td><td>{l.purpose}</td><td>{l.chars_sent}</td><td><code>{l.preview.slice(0, 120)}</code></td></tr>)}</tbody></table>)}
        </div>)}
      {picker && <PathPicker mode="dir" onPick={(d) => { set("output_dir", d); setPicker(false); }} onClose={() => setPicker(false)} />}
    </section>
  );
}
