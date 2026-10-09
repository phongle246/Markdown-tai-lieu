import { useEffect, useMemo, useRef, useState } from "react";
import { get, pageUrl } from "../api";
import type { Ctx } from "../App";
import type { BlockInfo, ChapterContent, Issue } from "../types";
import { renderMd } from "../markdown";
import { SevChip, StatusChip } from "./common";

const ZOOM = 1.5;

export default function Review({ ctx }: { ctx: Ctx }) {
  const p = ctx.project!;
  const converted = p.chapters.filter((c) => c.has_markdown);
  const [key, setKey] = useState(ctx.nav.chapter ?? converted[0]?.key ?? "");
  const [content, setContent] = useState<ChapterContent | null>(null);
  const [page, setPage] = useState(ctx.nav.page ?? 0);
  const [sel, setSel] = useState<string | null>(ctx.nav.block ?? null);
  const [source, setSource] = useState(true);
  const [raw, setRaw] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [wide, setWide] = useState(ctx.nav.focus === "pdf");
  const mdRef = useRef<HTMLDivElement>(null);
  const pending = useRef<{ block?: string | null; page?: number }>({ block: ctx.nav.block, page: ctx.nav.page });

  useEffect(() => {
    if (ctx.nav.chapter && ctx.nav.chapter !== key) setKey(ctx.nav.chapter);
    pending.current = { block: ctx.nav.block, page: ctx.nav.page };
    setWide(ctx.nav.focus === "pdf");
    if (ctx.nav.block && content) focusBlock(ctx.nav.block, ctx.nav.page);
  }, [ctx.nav]);

  useEffect(() => {
    if (!key) return;
    setContent(null);
    get<ChapterContent>(`/api/projects/${p.id}/chapters/${key}/content`)
      .then((c) => {
        setContent(c);
        const pn = pending.current;
        const blk = pn.block ? c.blocks.find((b) => b.block_id === pn.block) : undefined;
        setSel(blk?.block_id ?? null);
        setPage(pn.page ?? blk?.source_pages[0] ?? c.chapter.start);
        pending.current = {};
        if (blk) setTimeout(() => scrollToBlock(blk.block_id), 80);
      })
      .catch((e) => ctx.toast(e.message, "err"));
  }, [key]);

  const scrollToBlock = (id: string) => mdRef.current?.querySelector(`[data-blk="${id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  const focusBlock = (id: string, pg?: number) => {
    const b = content?.blocks.find((x) => x.block_id === id);
    setSel(id);
    setPage(pg ?? b?.source_pages[0] ?? page);
    setTimeout(() => scrollToBlock(id), 30);
  };
  const jumpIssue = (i: Issue) => {
    if (i.block_id) focusBlock(i.block_id, i.page ?? undefined);
    else if (i.page) {
      setPage(i.page);
      const b = content?.blocks.find((x) => x.source_pages.includes(i.page!));
      if (b) { setSel(b.block_id); setTimeout(() => scrollToBlock(b.block_id), 30); }
    }
  };
  // sync Markdown → page marker
  const gotoPageInMd = (n: number) => {
    setPage(n);
    const b = content?.blocks.find((x) => x.source_pages[0] === n) ?? content?.blocks.find((x) => x.source_pages.includes(n));
    if (b) { setSel(b.block_id); scrollToBlock(b.block_id); }
  };

  const issuesByBlock = useMemo(() => {
    const m = new Map<string, Issue[]>();
    content?.issues.forEach((i) => i.block_id && m.set(i.block_id, [...(m.get(i.block_id) ?? []), i]));
    return m;
  }, [content]);
  const selBlock = content?.blocks.find((b) => b.block_id === sel);
  const boxes = selBlock?.bboxes.filter((b) => b.page === page) ?? [];
  const ch = p.chapters.find((c) => c.key === key);
  const lo = ch?.start ?? 1, hi = ch?.end ?? p.book.pages;
  const [pw, ph] = content?.page_sizes?.[String(page)] ?? [612, 792];

  const onMdClick = (e: React.MouseEvent) => {
    const t = e.target as HTMLElement;
    const pg = t.closest("[data-page]") as HTMLElement | null;
    if (pg) { setPage(+pg.dataset.page!); return; }
    const a = t.closest("a") as HTMLAnchorElement | null;
    if (a?.dataset.md) {
      e.preventDefault();
      const k = a.dataset.md.split("_").slice(0, 2).join("_");
      ctx.go({ view: "review", chapter: k });
    } else if (a?.getAttribute("href")?.startsWith("#") && a.getAttribute("href")!.length > 1) {
      e.preventDefault();
      mdRef.current?.querySelector(`[id="${a.getAttribute("href")!.slice(1)}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  if (!converted.length) return <section><h1>Review</h1><p className="muted">Convert a chapter first, then review it side-by-side with the source PDF.</p></section>;

  let lastPage = -1;
  return (
    <section className="review">
      <header className="toolbar">
        <select value={key} onChange={(e) => { pending.current = {}; setKey(e.target.value); }}>{converted.map((c) => <option key={c.key} value={c.key}>{c.number} {c.title}</option>)}</select>
        {content && <StatusChip status={content.status} />}
        <span className="grow" />
        <label><input type="checkbox" checked={source} onChange={(e) => setSource(e.target.checked)} /> Source mode (page markers)</label>
        <label><input type="checkbox" checked={raw} onChange={(e) => setRaw(e.target.checked)} /> Raw Markdown</label>
        <button onClick={() => setWide(!wide)}>{wide ? "Show Markdown" : "Wide PDF"}</button>
      </header>
      <div className={`panes ${wide ? "wide" : ""}`}>
        <div className="pane pdf">
          <div className="toolbar small">
            <button disabled={page <= lo} onClick={() => setPage(page - 1)}>◀</button>
            <input className="num" type="number" value={page} min={1} max={p.book.pages} onChange={(e) => setPage(+e.target.value)} />
            <span className="muted">/ {lo}–{hi}</span>
            <button disabled={page >= hi} onClick={() => setPage(page + 1)}>▶</button>
            <button onClick={() => setZoom(Math.max(0.4, zoom - 0.2))}>−</button><button onClick={() => setZoom(Math.min(2.5, zoom + 0.2))}>+</button>
            <button onClick={() => gotoPageInMd(page)}>Find in Markdown →</button>
          </div>
          <div className="scroller">
            {page > 0 && (
              <div className="pagewrap" style={{ width: `${zoom * 100}%` }}>
                <img src={pageUrl(p.id, page, ZOOM)} alt={`PDF page ${page}`} draggable={false} />
                {boxes.map((b, i) => <div key={i} className="bbox" style={{ left: `${(b.bbox[0] / pw) * 100}%`, top: `${(b.bbox[1] / ph) * 100}%`, width: `${((b.bbox[2] - b.bbox[0]) / pw) * 100}%`, height: `${((b.bbox[3] - b.bbox[1]) / ph) * 100}%` }} />)}
              </div>
            )}
          </div>
        </div>
        {!wide && (
          <div className="pane md">
            <div className="scroller" ref={mdRef} onClick={onMdClick}>
              {!content && <p className="muted">Loading…</p>}
              {content?.blocks.map((b: BlockInfo) => {
                const first = b.source_pages[0];
                const marker = source && first !== lastPage;
                lastPage = b.source_pages[b.source_pages.length - 1];
                const iss = issuesByBlock.get(b.block_id);
                return (
                  <div key={b.block_id}>
                    {marker && <div className="pgmark" data-page={first}>[Trang gốc: {first}]</div>}
                    <div data-blk={b.block_id} className={`block ${sel === b.block_id ? "sel" : ""} ${iss ? "warn" : ""} t-${b.type}`} onClick={() => { setSel(b.block_id); if (!b.source_pages.includes(page)) setPage(first); }} title={`${b.type} · p.${b.source_pages.join(",")} · ${b.section_id}`}>
                      {raw ? <pre className="raw">{b.markdown}</pre> : <div className="md-body" dangerouslySetInnerHTML={{ __html: renderMd(b.markdown, p.id, source) }} />}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        <aside className="pane warnings">
          <h3>Warnings ({content?.issues.length ?? 0})</h3>
          <div className="scroller">
            {content?.issues.length === 0 && <p className="muted">No issues. 🎉</p>}
            {content?.issues.map((i, k) => (
              <div key={k} className="issue" onClick={() => jumpIssue(i)}>
                <SevChip s={i.severity} /> <b>{i.code}</b>{i.page ? <span className="muted"> · p.{i.page}</span> : null}
                <div className="small">{i.message}</div>
              </div>
            ))}
            {content && <details><summary>Conversion report</summary><pre className="log">{content.report}</pre></details>}
          </div>
        </aside>
      </div>
    </section>
  );
}

