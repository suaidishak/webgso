export type User = {
  id: number; email: string; name: string; role: string | null; status: string;
  organisation: string | null; organisation_type: string | null; plants: string[] | null;
  created_at?: string; approved_at?: string | null; last_login_at?: string | null;
};

const KEY = "gso_token";
export const getToken = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
export const setToken = (t: string | null) => {
  try { t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY); } catch { /* ignore */ }
};

export class ApiError extends Error { constructor(public status: number, msg: string) { super(msg); } }

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const t = getToken();
  if (t) headers.authorization = `Bearer ${t}`;
  const res = await fetch(`/api/v1${path}`, {
    method: opts.method || "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && t) { setToken(null); location.hash = "#/login"; }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    const d = data?.detail;
    throw new ApiError(res.status, typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join("; ") : res.statusText);
  }
  return data as T;
}

export const fmt = (s?: string | null) => (s ? s.replace("T", " ").slice(0, 16) : "—");
export const CATS = ["rolling", "dayahead", "weekahead", "fourmonthsahead", "mmf"] as const;
export const CAT_LABEL: Record<string, string> = {
  rolling: "Rolling", dayahead: "Day-ahead", weekahead: "Week-ahead", fourmonthsahead: "4-months", mmf: "MMF",
};

/** Download a file from the API with the user's token (a plain link can't send the Authorization header). */
export async function download(path: string, fallbackName = "export.csv") {
  const headers: Record<string, string> = {};
  const t = getToken();
  if (t) headers.authorization = `Bearer ${t}`;
  const res = await fetch(`/api/v1${path}`, { headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { const d = await res.json(); if (typeof d?.detail === "string") msg = d.detail; } catch { /* not JSON */ }
    throw new ApiError(res.status, msg);
  }
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "")?.[1] || fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
