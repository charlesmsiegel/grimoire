/**
 * REST client for narrative-extras endpoints (extras-design §REST surface).
 *
 * Used by the entity-detail ExtrasTable and the HUD's PresentCastChip
 * pinned-extra renderer. The shape of each ExtraValue mirrors the backend
 * Pydantic model in ``grimoire.types.extras``.
 */

import { api } from "./client";

export type ExtrasScope = "library" | "campaign-local" | "override";

export type ExtraScalar = string | number | boolean | null;
export type ExtraValueShape = ExtraScalar | ExtraScalar[] | Record<string, ExtraScalar>;

export interface ExtraValue {
  value: ExtraValueShape;
  set_at: string;
  set_by: string;
  source_evidence: string | null;
  scope: ExtrasScope;
}

export type ExtrasMap = Record<string, ExtraValue>;

export interface SetResultPayload {
  extra: ExtraValue;
  warnings: string[];
}

export interface SearchHit {
  entity_kind: string;
  entity_id: string;
  key: string;
  value_text: string;
}

// --------------------------------------------------------------------------
// Library scope
// --------------------------------------------------------------------------

export async function listLibraryExtras(
  worldId: string,
  kind: string,
  entityId: string,
): Promise<ExtrasMap> {
  const body = await api.get<{ extras: ExtrasMap }>(
    `/api/library/${encodeURIComponent(worldId)}/${encodeURIComponent(kind)}/${encodeURIComponent(entityId)}/extras`,
  );
  return body.extras;
}

export async function putLibraryExtra(
  worldId: string,
  kind: string,
  entityId: string,
  key: string,
  value: ExtraValueShape,
  options: { actor?: string; evidence?: string | null } = {},
): Promise<SetResultPayload> {
  return api.put<SetResultPayload>(
    `/api/library/${encodeURIComponent(worldId)}/${encodeURIComponent(kind)}/${encodeURIComponent(entityId)}/extras/${encodeURIComponent(key)}`,
    {
      value,
      actor: options.actor,
      evidence: options.evidence ?? null,
    },
  );
}

export async function deleteLibraryExtra(
  worldId: string,
  kind: string,
  entityId: string,
  key: string,
): Promise<void> {
  await api.delete<void>(
    `/api/library/${encodeURIComponent(worldId)}/${encodeURIComponent(kind)}/${encodeURIComponent(entityId)}/extras/${encodeURIComponent(key)}`,
  );
}

// --------------------------------------------------------------------------
// Campaign scope
// --------------------------------------------------------------------------

export async function listCampaignExtras(
  campaignId: string,
  kind: string,
  entityId: string,
  worldId?: string | null,
): Promise<ExtrasMap> {
  const url = new URL(
    `/api/campaigns/${encodeURIComponent(campaignId)}/${encodeURIComponent(kind)}/${encodeURIComponent(entityId)}/extras`,
    window.location.origin,
  );
  if (worldId) url.searchParams.set("world_id", worldId);
  const body = await api.get<{ extras: ExtrasMap }>(url.pathname + url.search);
  return body.extras;
}

export async function listCampaignExtrasRaw(
  campaignId: string,
  kind: string,
  entityId: string,
  scope: ExtrasScope,
  worldId?: string | null,
): Promise<ExtrasMap> {
  const url = new URL(
    `/api/campaigns/${encodeURIComponent(campaignId)}/${encodeURIComponent(kind)}/${encodeURIComponent(entityId)}/extras/raw`,
    window.location.origin,
  );
  url.searchParams.set("scope", scope);
  if (worldId) url.searchParams.set("world_id", worldId);
  const body = await api.get<{ extras: ExtrasMap }>(url.pathname + url.search);
  return body.extras;
}

export async function putCampaignExtra(
  campaignId: string,
  kind: string,
  entityId: string,
  key: string,
  value: ExtraValueShape,
  options: {
    scope?: ExtrasScope;
    worldId?: string | null;
    actor?: string;
    evidence?: string | null;
  } = {},
): Promise<SetResultPayload> {
  const url = new URL(
    `/api/campaigns/${encodeURIComponent(campaignId)}/${encodeURIComponent(kind)}/${encodeURIComponent(entityId)}/extras/${encodeURIComponent(key)}`,
    window.location.origin,
  );
  if (options.scope) url.searchParams.set("scope", options.scope);
  if (options.worldId) url.searchParams.set("world_id", options.worldId);
  return api.put<SetResultPayload>(url.pathname + url.search, {
    value,
    actor: options.actor,
    evidence: options.evidence ?? null,
  });
}

export async function deleteCampaignExtra(
  campaignId: string,
  kind: string,
  entityId: string,
  key: string,
  options: { scope?: ExtrasScope; worldId?: string | null } = {},
): Promise<void> {
  const url = new URL(
    `/api/campaigns/${encodeURIComponent(campaignId)}/${encodeURIComponent(kind)}/${encodeURIComponent(entityId)}/extras/${encodeURIComponent(key)}`,
    window.location.origin,
  );
  if (options.scope) url.searchParams.set("scope", options.scope);
  if (options.worldId) url.searchParams.set("world_id", options.worldId);
  await api.delete<void>(url.pathname + url.search);
}

// --------------------------------------------------------------------------
// Pin / Promotion
// --------------------------------------------------------------------------

export async function pinExtra(
  campaignId: string,
  kind: string,
  entityId: string,
  key: string,
  pinned: boolean,
): Promise<void> {
  const segment = pinned ? "pin" : "unpin";
  await api.post<void>(
    `/api/campaigns/${encodeURIComponent(campaignId)}/${encodeURIComponent(kind)}/${encodeURIComponent(entityId)}/extras/${encodeURIComponent(key)}/${segment}`,
  );
}

export async function promoteToLibrary(
  campaignId: string,
  kind: string,
  entityId: string,
  key: string,
  worldId: string,
): Promise<SetResultPayload> {
  const url = new URL(
    `/api/campaigns/${encodeURIComponent(campaignId)}/${encodeURIComponent(kind)}/${encodeURIComponent(entityId)}/extras/${encodeURIComponent(key)}/promote-to-library`,
    window.location.origin,
  );
  url.searchParams.set("world_id", worldId);
  return api.post<SetResultPayload>(url.pathname + url.search);
}

export async function promoteToFact(
  campaignId: string,
  kind: string,
  entityId: string,
  key: string,
): Promise<{ fact_id: string }> {
  return api.post<{ fact_id: string }>(
    `/api/campaigns/${encodeURIComponent(campaignId)}/${encodeURIComponent(kind)}/${encodeURIComponent(entityId)}/extras/${encodeURIComponent(key)}/promote-to-fact`,
  );
}

// --------------------------------------------------------------------------
// Search
// --------------------------------------------------------------------------

export async function searchExtras(
  q: string,
  options: { kind?: string; key?: string; limit?: number } = {},
): Promise<SearchHit[]> {
  const url = new URL("/api/search/extras", window.location.origin);
  url.searchParams.set("q", q);
  if (options.kind) url.searchParams.set("kind", options.kind);
  if (options.key) url.searchParams.set("key", options.key);
  if (options.limit) url.searchParams.set("limit", String(options.limit));
  const body = await api.get<{ hits: SearchHit[] }>(url.pathname + url.search);
  return body.hits;
}
