/**
 * Minimal typed HTTP client for the FastAPI backend.
 *
 * Errors are normalized to {@link ApiError} so callers can branch on status
 * without parsing the response twice.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

interface RequestOptions {
  signal?: AbortSignal;
  query?: Record<string, string | number | boolean | undefined | null>;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(path, window.location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null) continue;
      url.searchParams.set(k, String(v));
    }
  }
  return url.pathname + url.search;
}

async function parseBody(res: Response): Promise<unknown> {
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) return res.json();
  const text = await res.text();
  return text ? text : null;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: RequestOptions = {},
): Promise<T> {
  const init: RequestInit = { method, signal: opts.signal };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(buildUrl(path, opts.query), init);
  if (!res.ok) {
    const detail = await parseBody(res).catch((err) => {
      console.warn(`[api] failed to parse error body for ${method} ${path}:`, err);
      return null;
    });
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  // Reject HTML bodies loudly. When a path is missing the /api prefix in dev,
  // Vite's SPA fallback returns index.html with HTTP 200; without this guard
  // callers downstream blow up with confusing type errors (e.g. ".find is not
  // a function" on a string).
  const ct = res.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) {
    throw new ApiError(
      res.status,
      null,
      `expected JSON response from ${path} but got ${ct || "unknown content-type"}`,
    );
  }
  return (await parseBody(res)) as T;
}

async function requestText(
  method: string,
  path: string,
  opts: RequestOptions = {},
): Promise<string> {
  const res = await fetch(buildUrl(path, opts.query), { method, signal: opts.signal });
  if (!res.ok) {
    const detail = await parseBody(res).catch((err) => {
      console.warn(`[api] failed to parse error body for ${method} ${path}:`, err);
      return null;
    });
    throw new ApiError(res.status, detail);
  }
  return res.text();
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => request<T>("GET", path, undefined, opts),
  getText: (path: string, opts?: RequestOptions) => requestText("GET", path, opts),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>("POST", path, body, opts),
  put: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>("PUT", path, body, opts),
  patch: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>("PATCH", path, body, opts),
  delete: <T>(path: string, opts?: RequestOptions) => request<T>("DELETE", path, undefined, opts),
};
