/**
 * Human labels for InclusionReason values, shared between the live
 * Context Inspector (next-turn preview) and the past-turn lens. Keep
 * the map exhaustive — TypeScript's `Record<InclusionReason, string>`
 * enforces that at the type level.
 */

import type { InclusionReason } from "../../api/inspector";

export const REASON_LABELS: Record<InclusionReason, string> = {
  present_in_scene: "Present in scene",
  mentioned_in_recent_posts: "Mentioned recently",
  commitment_open_to_pc: "Open commitment to PC",
  keyword_triggered: "Keyword triggered",
  relationship_to_present: "Relationship to present",
  pinned_by_user: "Pinned by user",
  scene_anchor: "Scene anchor",
  mechanics_relevant: "Mechanics relevant",
  style_guide_active: "Style guide active",
  pc_card: "PC card",
  composition_default: "Composition default",
  extras_pinned_to_hud: "Extras pinned",
  extras_default_visible: "Extras default",
  lore_before_cast: "Lore before cast",
  lore_after_cast: "Lore after cast",
  lore_at_depth: "Lore at depth",
  lore_archive: "Lore archive",
  transient_state_active: "Transient state active",
};
