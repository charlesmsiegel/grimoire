import { ApiError } from "../client";

export { ApiError };

const API_BASE = "/api";
const CACHE_TTL_MS = 30_000;

interface CacheEntry {
  expires: number;
  value: unknown;
}

const cache = new Map<string, CacheEntry>();

function cacheGet(url: string): unknown | undefined {
  const entry = cache.get(url);
  if (!entry) return undefined;
  if (entry.expires < Date.now()) {
    cache.delete(url);
    return undefined;
  }
  return entry.value;
}

function cacheSet(url: string, value: unknown): void {
  cache.set(url, { expires: Date.now() + CACHE_TTL_MS, value });
}

export function clearLibraryCache(): void {
  cache.clear();
}

export interface RequestOptions {
  cache?: boolean;
}

export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: RequestInit,
  opts?: RequestOptions,
): Promise<T> {
  const useCache = method === "GET" && opts?.cache !== false;
  const url = `${API_BASE}${path}`;
  if (useCache) {
    const hit = cacheGet(url);
    if (hit !== undefined) return hit as T;
  }
  const headers = new Headers(init?.headers);
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const res = await fetch(url, {
    ...init,
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text);
  }
  if (method !== "GET") clearLibraryCache();
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) return undefined as T;
  const parsed = (await res.json()) as T;
  if (useCache) cacheSet(url, parsed);
  return parsed;
}
