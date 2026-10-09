import { useCallback, useEffect, useRef, useState } from "react";
import { get, post } from "./api";
import type { Job, Nav, Overview, Settings, View } from "./types";
import Library from "./screens/Library";
import NewProject from "./screens/NewProject";
import OverviewScreen from "./screens/OverviewScreen";
import Convert from "./screens/Convert";
import Search from "./screens/Search";
import Review from "./screens/Review";
import Export from "./screens/Export";
import SettingsScreen from "./screens/SettingsScreen";

const NAV: { view: View; label: string; needsProject: boolean }[] = [
  { view: "library", label: "Projects", needsProject: false },
  { view: "overview", label: "Book overview", needsProject: true },
  { view: "convert", label: "Convert", needsProject: true },
  { view: "search", label: "Search", needsProject: true },
  { view: "review", label: "Review", needsProject: true },
  { view: "export", label: "Export", needsProject: true },
  { view: "settings", label: "Settings", needsProject: false },
];

export interface Ctx {
  project: Overview | null; setProject: (o: Overview | null) => void; refresh: () => Promise<void>;
  nav: Nav; go: (n: Nav | View) => void; job: Job | null; startJob: (chapters: string[] | "all", force?: boolean) => Promise<void>;
  jobAction: (a: "pause" | "resume" | "cancel") => Promise<void>; settings: Settings | null; reloadSettings: () => Promise<void>;
  toast: (m: string, kind?: "ok" | "err") => void;
}

export default function App() {
  const [project, setProject] = useState<Overview | null>(null);
  const [nav, setNav] = useState<Nav>({ view: "library" });
  const [job, setJob] = useState<Job | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [msg, setMsg] = useState<{ m: string; kind: string } | null>(null);
  const projRef = useRef<Overview | null>(null);
  projRef.current = project;

  const toast = useCallback((m: string, kind: "ok" | "err" = "ok") => {
    setMsg({ m, kind });
    setTimeout(() => setMsg(null), kind === "err" ? 9000 : 3500);
  }, []);
  const reloadSettings = useCallback(async () => { setSettings(await get<Settings>("/api/settings")); }, []);
  const refresh = useCallback(async () => {
    const p = projRef.current;
    if (p) setProject(await get<Overview>(`/api/projects/${p.id}`));
  }, []);
  const go = useCallback((n: Nav | View) => setNav(typeof n === "string" ? { view: n } : n), []);

  // restore the last opened project after a reload / app restart
  useEffect(() => {
    const id = localStorage.getItem("t2md.lastProject");
    if (id) get<Overview>(`/api/projects/${id}`).then((o) => { setProject(o); setNav({ view: "overview" }); }).catch(() => localStorage.removeItem("t2md.lastProject"));
  }, []);
  useEffect(() => { if (project) localStorage.setItem("t2md.lastProject", project.id); }, [project?.id]);
  useEffect(() => { reloadSettings().catch((e) => toast(e.message, "err")); get("/api/job").then((r) => setJob(r.job)).catch(() => {}); }, []);
  useEffect(() => {
    const t = settings?.theme ?? "system";
    document.documentElement.dataset.theme = t === "system" ? "" : t;
  }, [settings?.theme]);

  // poll the active job
  useEffect(() => {
    if (!job || !["running", "paused"].includes(job.status)) return;
    const t = setInterval(async () => {
      try {
        const r = await get<{ job: Job }>("/api/job");
        setJob(r.job);
        if (r.job && !["running", "paused"].includes(r.job.status)) { await refresh(); }
      } catch { /* engine busy */ }
    }, 700);
    return () => clearInterval(t);
  }, [job?.id, job?.status]);
  // keep sidebar statuses fresh while converting
  useEffect(() => { if (job?.status === "running") refresh().catch(() => {}); }, [job?.progress?.chapter, job?.progress?.done]);

  const startJob = async (chapters: string[] | "all", force = false) => {
    if (!project) return;
    try {
      const r = await post<{ job: Job }>(`/api/projects/${project.id}/convert`, { chapters, force });
      setJob(r.job);
      go("convert");
    } catch (e: any) { toast(e.message, "err"); }
  };
  const jobAction = async (a: "pause" | "resume" | "cancel") => {
    try { const r = await post<{ job: Job }>(`/api/job/${a}`); setJob(r.job); } catch (e: any) { toast(e.message, "err"); }
  };

  const ctx: Ctx = { project, setProject, refresh, nav, go, job, startJob, jobAction, settings, reloadSettings, toast };
  const running = job && ["running", "paused"].includes(job.status);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">textbook<span>2</span>md</div>
        {project && <div className="current" title={project.root}><b>{project.book.title}</b><small>{project.progress.done}/{project.progress.total} chapters · {project.progress.percent}%</small></div>}
        <nav>
          {NAV.map((n) => (
            <button key={n.view} className={nav.view === n.view || (n.view === "library" && nav.view === "new") ? "active" : ""}
              disabled={n.needsProject && !project} onClick={() => go(n.view)}>
              {n.label}{n.view === "convert" && running ? <i className="dot" /> : null}
            </button>
          ))}
        </nav>
        <div className="privacy">Local-first: the PDF stays on this computer. {settings?.ai_enabled ? `AI (${settings.provider}) is ON — only headings / ambiguous lines are sent.` : "AI is OFF — nothing leaves this machine."}</div>
      </aside>
      <main>
        {nav.view === "library" && <Library ctx={ctx} />}
        {nav.view === "new" && <NewProject ctx={ctx} />}
        {nav.view === "overview" && project && <OverviewScreen ctx={ctx} />}
        {nav.view === "convert" && project && <Convert ctx={ctx} />}
        {nav.view === "search" && project && <Search ctx={ctx} />}
        {nav.view === "review" && project && <Review ctx={ctx} />}
        {nav.view === "export" && project && <Export ctx={ctx} />}
        {nav.view === "settings" && <SettingsScreen ctx={ctx} />}
      </main>
      {msg && <div className={`toast ${msg.kind}`} onClick={() => setMsg(null)}>{msg.m}</div>}
    </div>
  );
}
