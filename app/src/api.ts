declare global { interface Window { __T2MD_TOKEN?: string } }
const TOKEN = () => window.__T2MD_TOKEN ?? "";

export class ApiError extends Error {}

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", "X-T2MD-Token": TOKEN() },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Cannot reach the local engine. Is the textbook2md sidecar running?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.error ?? `HTTP ${res.status}`);
  return data as T;
}
export const get = <T = any>(p: string) => api<T>("GET", p);
export const post = <T = any>(p: string, b?: unknown) => api<T>("POST", p, b ?? {});
export const put = <T = any>(p: string, b?: unknown) => api<T>("PUT", p, b ?? {});

export const pageUrl = (pid: string, n: number, zoom = 1.5) =>
  `/api/projects/${pid}/page/${n}.png?zoom=${zoom}&token=${encodeURIComponent(TOKEN())}`;
export const fileUrl = (pid: string, p: string) =>
  `/api/projects/${pid}/file?path=${encodeURIComponent(p)}&token=${encodeURIComponent(TOKEN())}`;
export const coverUrl = (pid: string) => `/api/projects/${pid}/cover.png?token=${encodeURIComponent(TOKEN())}`;
