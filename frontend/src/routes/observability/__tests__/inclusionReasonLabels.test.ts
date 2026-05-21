import { describe, expect, it } from "vitest";

import { REASON_LABELS } from "../inclusionReasonLabels";

describe("REASON_LABELS", () => {
  it("provides a human label for every InclusionReason", () => {
    expect(REASON_LABELS).toMatchInlineSnapshot(`
      {
        "commitment_open_to_pc": "Open commitment to PC",
        "composition_default": "Composition default",
        "extras_default_visible": "Extras default",
        "extras_pinned_to_hud": "Extras pinned",
        "keyword_triggered": "Keyword triggered",
        "lore_after_cast": "Lore after cast",
        "lore_archive": "Lore archive",
        "lore_at_depth": "Lore at depth",
        "lore_before_cast": "Lore before cast",
        "mechanics_relevant": "Mechanics relevant",
        "mentioned_in_recent_posts": "Mentioned recently",
        "pc_card": "PC card",
        "pinned_by_user": "Pinned by user",
        "present_in_scene": "Present in scene",
        "relationship_to_present": "Relationship to present",
        "scene_anchor": "Scene anchor",
        "style_guide_active": "Style guide active",
        "transient_state_active": "Transient state active",
      }
    `);
  });
});
