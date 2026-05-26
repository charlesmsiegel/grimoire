/**
 * Expression sprites: REST client + `useExpression` hook.
 *
 * The hook caches by `(campaignId, characterId, turnId)` so a re-render
 * doesn't re-fetch the same sprite. Cache is process-local; the browser
 * still caches the PNGs themselves via standard HTTP.
 */

import { useEffect, useState } from "react";

import { api } from "./client";

export interface ExpressionResponse {
  emotion: string;
  sprite_url: string | null;
  fallback_used: boolean;
}

export interface ExpressionVocabulary {
  core: string[];
  extensions: Record<string, string[]>;
}

export async function fetchExpression(
  campaignId: string,
  characterId: string,
  asOfTurn?: string | null,
): Promise<ExpressionResponse> {
  return api.get<ExpressionResponse>(
    `/api/campaigns/${encodeURIComponent(campaignId)}/characters/${encodeURIComponent(
      characterId,
    )}/expression`,
    { query: asOfTurn ? { as_of_turn: asOfTurn } : {} },
  );
}

export async function setPcExpression(
  campaignId: string,
  characterId: string,
  body: { emotion: string; post_id: string; scene_id?: string; turn_id?: string },
): Promise<ExpressionResponse> {
  return api.patch<ExpressionResponse>(
    `/api/campaigns/${encodeURIComponent(campaignId)}/characters/${encodeURIComponent(
      characterId,
    )}/expression`,
    body,
  );
}

export async function fetchVocabulary(): Promise<ExpressionVocabulary> {
  return api.get<ExpressionVocabulary>("/api/expressions/vocabulary");
}

interface CacheEntry {
  data: ExpressionResponse;
  expiresAt: number;
}

const cache = new Map<string, CacheEntry>();
const notFoundCache = new Set<string>();
const CACHE_TTL_MS = 60_000;

function cacheKey(campaignId: string, characterId: string, turnId?: string | null): string {
  return `${campaignId}::${characterId}::${turnId ?? ""}`;
}

export function invalidateExpression(
  campaignId: string,
  characterId: string,
  turnId?: string | null,
): void {
  cache.delete(cacheKey(campaignId, characterId, turnId));
  notFoundCache.delete(`${campaignId}::${characterId}`);
}

export function useExpression(
  campaignId: string | null,
  characterId: string | null,
  turnId?: string | null,
): { data: ExpressionResponse | null; loading: boolean; error: Error | null } {
  const [state, setState] = useState<{
    data: ExpressionResponse | null;
    loading: boolean;
    error: Error | null;
  }>({ data: null, loading: false, error: null });

  useEffect(() => {
    if (!campaignId || !characterId) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    const key = cacheKey(campaignId, characterId, turnId);
    const cached = cache.get(key);
    if (cached && cached.expiresAt > Date.now()) {
      setState({ data: cached.data, loading: false, error: null });
      return;
    }
    const charKey = `${campaignId}::${characterId}`;
    if (notFoundCache.has(charKey)) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let active = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fetchExpression(campaignId, characterId, turnId)
      .then((data) => {
        if (!active) return;
        cache.set(key, { data, expiresAt: Date.now() + CACHE_TTL_MS });
        setState({ data, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (!active) return;
        if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 404) {
          notFoundCache.add(charKey);
        }
        setState({ data: null, loading: false, error: err instanceof Error ? err : new Error(String(err)) });
      });
    return () => {
      active = false;
    };
  }, [campaignId, characterId, turnId]);

  return state;
}
