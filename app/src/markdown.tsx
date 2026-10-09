import DOMPurify from "dompurify";
import { marked } from "marked";
import { fileUrl } from "./api";

marked.setOptions({ gfm: true, breaks: false });

/** Render a Markdown fragment from a chapter file. Page markers become visible only in source mode. */
export function renderMd(md: string, pid: string, showPages: boolean): string {
  let t = md.replace(/<!-- source_page: (\d+) -->/g, (_m, n) =>
    showPages ? `<span class="pg" data-page="${n}">[Trang gốc: ${n}]</span>` : "");
  t = t.replace(/<!-- UNCERTAIN: ([^>]*?) -->/g, (_m, s) => `<span class="uncertain">⚠ ${s}</span>`);
  t = t.replace(/<!--[\s\S]*?-->/g, "");
  t = t.replace(/\$\$\n([\s\S]*?)\n\$\$/g, (_m, s) => `<pre class="math">${s.replace(/</g, "&lt;")}</pre>`);
  let html = marked.parse(t, { async: false }) as string;
  html = html.replace(/src="\.\.\/(assets\/[^"]+)"/g, (_m, p) => `src="${fileUrl(pid, decodeURI(p))}"`);
  html = html.replace(/href="\.\.\/(tables\/[^"#]+\.md)"/g, (_m, p) => `href="#" data-table="${p}"`);
  html = html.replace(/href="\.\/([^"#]+\.md)(#[^"]*)?"/g, (_m, f, a) => `href="#" data-md="${f}" data-anchor="${(a ?? "").slice(1)}"`);
  return DOMPurify.sanitize(html, { ADD_ATTR: ["data-page", "data-md", "data-anchor", "data-table", "id", "colspan", "rowspan"], ADD_TAGS: ["details", "summary"] });
}

export function snippetHtml(s: string): string {
  const esc = s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc.replace(/\[\[/g, "<mark>").replace(/\]\]/g, "</mark>");
}
