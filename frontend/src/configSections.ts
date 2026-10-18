/** Settings vocabulary shared by the form, its column and the app palette.
 *  A destination and its draft ownership stay in one table as new sections appear. */
export const DRAFT_FIELDS = [
  // No legacy inference key (`active_connection_id`, `fallback_connection_id`,
  // `embeddings_connection_id`, `embeddings_model`): which provider and model
  // each role runs is the Models page's, and at format 2 the server refuses a
  // write of any of them -- it would reach older builds only.
  "llm_retries",
  "llm_timeout", "absorb_budget", "llm_call_budget",
  "context_budget", "context_scan_depth", "lore_recursion_depth", "archive_depth",
  "prompt_log_depth", "offscene_known_limit",
  "speaker_turn_taking", "prompt_layout_enabled",
  "semantic_recall_depth", "semantic_recall_threshold",
  "history_recall_depth", "history_recall_budget", "history_recall_threshold", "history_recall_rerank",
  "history_plan", "history_plan_repair",
  "system_prompt",
  "quote_color", "user_label", "assistant_label",
  "rolling_summary_every", "scene_break_every", "replay_fork_threshold",
  "advance_fork_threshold",
  "backup_enabled", "backup_interval_hours", "backup_keep", "backup_dir",
  "log_level",
  "tracker", "perception_rider",
  "send_images", "send_images_limit",
  "decide_speaker_sampling", "decide_turn_plan", "decide_play_tool",
  "investigation_continuity", "investigation_question", "investigation_spend_ceiling",
  // `theme` is deliberately NOT here. The look is persisted the moment it is
  // picked, by `useThemeSetting`, because three controls now offer it (this
  // page, the first-run wizard, the header toggle) and a draft field would let
  // this page's stale copy overwrite a choice made from one of the others on
  // the next unrelated Save.
] as const;
export type DraftField = (typeof DRAFT_FIELDS)[number];
export type Draft = Record<DraftField, string>;

export type SectionId =
  | "storage" | "backups" | "logging" | "timeouts"
  | "setup"
  | "context" | "layout" | "tracker" | "decisions" | "investigation"
  | "system-prompt" | "response"
  | "transcript" | "output" | "playing" | "appearance";

/** Settings → Inference: three pages of their own, linked from the column's
 *  first group. Links, not panes -- they own no draft field, so they never
 *  carry an unsaved dot, and their keys never take part in `?section=`. */
type LinkDef = { key: "inference-providers" | "inference-models" | "inference-presets";
                 group: "Inference"; label: string; to: string };
export const LINKS: LinkDef[] = [
  { key: "inference-providers", group: "Inference", label: "Providers", to: "/providers" },
  { key: "inference-models", group: "Inference", label: "Models", to: "/models" },
  { key: "inference-presets", group: "Inference", label: "Presets", to: "/presets" },
];

/** The column, as data: four groups -- Inference's links (`LINKS`) above
 *  three groups of sections -- fourteen sections, and which draft
 *  fields each one owns — the last part is what lets a section carry an
 *  unsaved dot, so the footer's count is always findable rather than being a
 *  number about somewhere else. */
export type SectionDef = { id: SectionId; group: string; label: string; fields: DraftField[] };
export const SECTIONS: SectionDef[] = [
  { id: "storage", group: "The install", label: "Storage", fields: [] },
  { id: "backups", group: "The install", label: "Backups",
    fields: ["backup_enabled", "backup_interval_hours", "backup_keep", "backup_dir"] },
  { id: "logging", group: "The install", label: "Logging",
    fields: ["log_level"] },
  { id: "timeouts", group: "The install", label: "Timeouts & retries",
    fields: ["llm_timeout", "absorb_budget", "llm_call_budget", "llm_retries"] },
  // No fields: the wizard writes each step as it is answered, which is what
  // makes Back navigation rather than undo. This section is the way back INTO
  // it once setup is done, which the router otherwise makes unreachable.
  { id: "setup", group: "The install", label: "First-run setup", fields: [] },
  { id: "context", group: "What the model sees", label: "Context",
    fields: ["context_budget", "context_scan_depth", "lore_recursion_depth", "archive_depth",
             "prompt_log_depth", "offscene_known_limit", "speaker_turn_taking", "send_images",
             "send_images_limit", "semantic_recall_depth", "semantic_recall_threshold",
             "history_recall_depth", "history_recall_budget", "history_recall_threshold",
             "history_recall_rerank", "history_plan", "history_plan_repair"] },
  { id: "layout", group: "What the model sees", label: "Prompt layout",
    fields: ["prompt_layout_enabled"] },
  { id: "tracker", group: "What the model sees", label: "Scene tracker",
    fields: ["tracker", "perception_rider"] },
  { id: "decisions", group: "What the model sees", label: "Play decisions",
    fields: ["decide_speaker_sampling", "decide_turn_plan", "decide_play_tool"] },
  { id: "investigation", group: "What the model sees", label: "History investigation",
    fields: ["investigation_continuity", "investigation_question",
             "investigation_spend_ceiling"] },
  { id: "system-prompt", group: "What the model sees", label: "System prompt",
    fields: ["system_prompt"] },
  { id: "response", group: "What the model sees", label: "Response targets", fields: [] },
  { id: "transcript", group: "What you see", label: "Transcript",
    fields: ["quote_color", "user_label", "assistant_label"] },
  // No draft fields: rules save as you edit them, through their own routes,
  // like Token rates.
  { id: "output", group: "What you see", label: "Output processing", fields: [] },
  { id: "playing", group: "What you see", label: "While playing",
    fields: ["rolling_summary_every", "scene_break_every", "replay_fork_threshold",
             "advance_fork_threshold"] },
  // No draft fields: the look is persisted the moment it is picked, through
  // `useThemeSetting`, so it is not something this page's Save writes. The
  // section is the prose that explains the choice; the control is the pinned
  // picker below, reachable from whichever section you happen to be reading.
  { id: "appearance", group: "What you see", label: "Appearance", fields: [] },
];
export const GROUPS = SECTIONS.reduce<string[]>(
  (out, s) => (out.includes(s.group) ? out : [...out, s.group]), []);

