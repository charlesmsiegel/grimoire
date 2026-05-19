/**
 * Context Inspector REST client.
 *
 * Backs the Inspector panel: live preview while the user types, per-source
 * inclusion-reason drill-down, pin / exclude controls, and diff between
 * two previews (or a preview and a prior canonical turn).
 *
 * Endpoints come from spec
 * `docs/superpowers/specs/2026-05-19-context-inspector-design.md`.
 */

import { api } from "./client";

export type ContextTier = "lock-in" | "spotlight" | "background" | "archive";

export type InclusionReason =
  | "present_in_scene"
  | "mentioned_in_recent_posts"
  | "commitment_open_to_pc"
  | "keyword_triggered"
  | "relationship_to_present"
  | "pinned_by_user"
  | "scene_anchor"
  | "mechanics_relevant"
  | "style_guide_active"
  | "pc_card"
  | "composition_default"
  | "extras_pinned_to_hud"
  | "extras_default_visible"
  | "lore_before_cast"
  | "lore_after_cast"
  | "lore_at_depth"
  | "lore_archive"
  | "transient_state_active";

export interface PreviewSummary {
  handle: string;
  per_tier_tokens: Record<ContextTier, number>;
  per_tier_budget: Record<ContextTier, number>;
  source_count: number;
  messages_hash: string;
}

export interface PreviewResponse {
  handle: string;
  summary: PreviewSummary;
}

export interface ContextSourceExplanation {
  source_id: string;
  owner_id: string | null;
  kind: string;
  scope: string;
  tier: ContextTier;
  library_version: number | null;
  inclusion_reasons: InclusionReason[];
  tokens: number;
  summary: string;
}

export interface SourceVersionChange {
  source_id: string;
  before: number | null;
  after: number | null;
}

export interface ContextDiff {
  entities_added: ContextSourceExplanation[];
  entities_removed: ContextSourceExplanation[];
  entities_changed_tier: ContextSourceExplanation[];
  budget_shifts: Record<ContextTier, number>;
  source_version_changes: SourceVersionChange[];
}

export interface PinTarget {
  source_id?: string | null;
  entity_kind?: string | null;
  entity_id?: string | null;
}

export interface PinRow {
  id: string;
  campaign_id: string;
  branch_id: string;
  kind: "pin" | "exclude";
  target_kind: "source" | "entity";
  target_source_id: string | null;
  target_entity_kind: string | null;
  target_entity_id: string | null;
  created_at: string;
  created_by: string;
  created_at_turn_id: string | null;
  expires_at_turn_id: string | null;
  cleared_at: string | null;
  cleared_by: string | null;
}

interface PreviewArgs {
  playerInput: string;
  sessionId: string;
  branchId?: string;
  pcRef?: string;
}

interface PinArgs {
  target: PinTarget;
  kind: "pin" | "exclude";
  ttlTurns?: number | null;
  createdAtTurnId?: string | null;
  branchId?: string | null;
}

function base(campaignId: string) {
  return `/api/campaigns/${encodeURIComponent(campaignId)}/context`;
}

export const inspectorApi = {
  preview(campaignId: string, args: PreviewArgs, signal?: AbortSignal): Promise<PreviewResponse> {
    return api.post(`${base(campaignId)}/preview`, {
      player_input: args.playerInput,
      session_id: args.sessionId,
      branch_id: args.branchId ?? null,
      pc_ref: args.pcRef ?? null,
    }, { signal });
  },

  getPreview(campaignId: string, handle: string, sessionId: string) {
    return api.get(
      `${base(campaignId)}/preview/${encodeURIComponent(handle)}`,
      { query: { session_id: sessionId } },
    );
  },

  explain(campaignId: string, handle: string, sessionId: string): Promise<ContextSourceExplanation[]> {
    return api.get(
      `${base(campaignId)}/preview/${encodeURIComponent(handle)}/explain`,
      { query: { session_id: sessionId } },
    );
  },

  pin(campaignId: string, args: PinArgs): Promise<{ pin_id: string; kind: "pin" | "exclude" }> {
    return api.post(`${base(campaignId)}/pins`, {
      target: args.target,
      kind: args.kind,
      ttl_turns: args.ttlTurns ?? null,
      created_at_turn_id: args.createdAtTurnId ?? null,
      branch_id: args.branchId ?? null,
    });
  },

  clearPin(campaignId: string, pinId: string): Promise<{ cleared: string }> {
    return api.delete(`${base(campaignId)}/pins/${encodeURIComponent(pinId)}`);
  },

  listPins(campaignId: string, branchId?: string): Promise<PinRow[]> {
    return api.get(`${base(campaignId)}/pins`, {
      query: { branch_id: branchId ?? null },
    });
  },

  diff(
    campaignId: string,
    a: string,
    b: string,
    sessionId: string | null,
  ): Promise<ContextDiff> {
    return api.post(`${base(campaignId)}/diff`, {
      a,
      b,
      session_id: sessionId,
    });
  },
};
