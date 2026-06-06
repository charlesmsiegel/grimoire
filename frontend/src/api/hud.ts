/**
 * Scene HUD client. Mirrors the Pydantic types in
 * ``backend/src/grimoire/types/hud.py`` and the routes in
 * ``backend/src/grimoire/api/hud.py``.
 */

import { api } from "./client";

export type WidgetStatus = "ok" | "error" | "timeout" | "hidden";

export type RenderHint = "row" | "block" | "chip-list" | "banner" | "composite";

export type WidgetScope = "campaign" | "scene" | "pc" | "present_npc";

export interface WidgetRead {
  endpoint: string;
  poll_interval_s: number | null;
}

export interface WidgetEdit {
  kind: string;
  endpoint: string;
  schema_ref: string | null;
}

export interface HudWidget {
  id: string;
  title: string;
  scope: WidgetScope;
  visible_when: string | null;
  render_hint: string;
  read: WidgetRead;
  edit: WidgetEdit | null;
  refresh_on: string[];
  stale_threshold_s: number | null;
  owner_module: string | null;
}

export interface WidgetSnapshot {
  id: string;
  status: WidgetStatus;
  data: unknown;
  error: string | null;
  stale: boolean;
  title: string | null;
  render_hint: string | null;
}

export interface AggregateResult {
  campaign_id: string;
  scene_id: string | null;
  generated_at: string;
  widgets: WidgetSnapshot[];
}

export interface OrderedWidgetPayload {
  id: string;
  visible: boolean;
  options: Record<string, unknown>;
}

export interface WidgetGroupPayload {
  title: string;
  widgets: string[];
}

export interface HudConfigPayload {
  density: string;
  position: string;
  ordered_widgets: OrderedWidgetPayload[];
  groups: WidgetGroupPayload[];
  pinned_extras: Record<string, string[]>;
}

const base = (campaignId: string) => `/api/campaigns/${encodeURIComponent(campaignId)}/hud`;

export const hudApi = {
  aggregate(
    campaignId: string,
    signal?: AbortSignal,
    sceneId?: string | null,
  ): Promise<AggregateResult> {
    const url = sceneId
      ? `${base(campaignId)}?scene_id=${encodeURIComponent(sceneId)}`
      : base(campaignId);
    return api.get<AggregateResult>(url, { signal });
  },
  widget(
    campaignId: string,
    widgetId: string,
    signal?: AbortSignal,
    sceneId?: string | null,
  ): Promise<WidgetSnapshot> {
    const url = sceneId
      ? `${base(campaignId)}/widgets/${encodeURIComponent(widgetId)}?scene_id=${encodeURIComponent(sceneId)}`
      : `${base(campaignId)}/widgets/${encodeURIComponent(widgetId)}`;
    return api.get<WidgetSnapshot>(url, { signal });
  },
  available(campaignId: string, signal?: AbortSignal): Promise<HudWidget[]> {
    return api.get<HudWidget[]>(`${base(campaignId)}/widgets/available`, { signal });
  },
  getConfig(campaignId: string, signal?: AbortSignal): Promise<HudConfigPayload> {
    return api.get<HudConfigPayload>(`${base(campaignId)}/config`, { signal });
  },
  putConfig(campaignId: string, config: HudConfigPayload): Promise<HudConfigPayload> {
    return api.put<HudConfigPayload>(`${base(campaignId)}/config`, config);
  },
  resetConfig(campaignId: string): Promise<HudConfigPayload> {
    return api.post<HudConfigPayload>(`${base(campaignId)}/config/reset`);
  },
};
