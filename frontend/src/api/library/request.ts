/**
 * Library API transport: a thin 30-second GET cache over the app-wide `api`
 * client — one HTTP layer, one error shape (ApiError, parsed JSON detail).
 * Any non-GET through this module invalidates the whole cache so list views
 * refresh after mutations.
 */

import type { ZodType } from "zod";

import { ApiError, api } from "../client";

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
  /** Observational response-shape check, forwarded to `api` (issue #599). */
  checkSchema?: ZodType;
}

export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts?: RequestOptions,
): Promise<T> {
  const useCache = method === "GET" && opts?.cache !== false;
  const url = `${API_BASE}${path}`;
  if (useCache) {
    const hit = cacheGet(url);
    if (hit !== undefined) return hit as T;
  }
  let value: T;
  switch (method) {
    case "GET":
      value = await api.get<T>(url, { checkSchema: opts?.checkSchema });
      break;
    case "POST":
      value = await api.post<T>(url, body);
      break;
    case "PUT":
      value = await api.put<T>(url, body);
      break;
    case "PATCH":
      value = await api.patch<T>(url, body);
      break;
    case "DELETE":
      value = await api.delete<T>(url);
      break;
    default:
      throw new Error(`unsupported method ${method}`);
  }
  if (method !== "GET") clearLibraryCache();
  if (useCache) cacheSet(url, value);
  return value;
}
