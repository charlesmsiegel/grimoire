import { z } from "zod";

/**
 * Wire shapes for scenes and posts. The scene endpoints serialize the scene
 * manager's dataclasses (`Scene` / `Post` / `Thread` in
 * `backend/src/grimoire/scenes/types.py`) through `to_payload`, so
 * `in_game_start` / `in_game_end` are bare ISO-8601 strings (not the time
 * engine's `{moment, calendar_id}` object), the closing summary lives in
 * `final_summary`, and `mood` / `running_summary` are nullable.
 *
 * Two read views exist over the same `/scenes` payload — the campaign views'
 * `SceneSummary` and the play view's `ApiScene` — so both names alias one
 * schema here and cannot drift from each other. Only fields the frontend
 * reads are declared; unknown extras are tolerated.
 */

/**
 * The time engine's timestamp object (`grimoire.types.common.InGameTime`),
 * used by time-advance payloads. Scene start/end times are *not* this shape —
 * they arrive as plain ISO strings (see module doc).
 */
export const InGameTimeSchema = z.object({
  moment: z.string(),
  calendar_id: z.string().nullable().optional(),
});
export type InGameTime = z.infer<typeof InGameTimeSchema>;

export const ThreadSchema = z.object({
  text: z.string(),
  introduced_at_post: z.number().nullable(),
  paid_off_at_post: z.number().nullable(),
});
export type Thread = z.infer<typeof ThreadSchema>;

export const NarratorResponseModeSchema = z.enum([
  "all_at_once",
  "per_character",
  "per_character_multi_call",
]);
export type NarratorResponseMode = z.infer<typeof NarratorResponseModeSchema>;

export const ApiSceneSchema = z.object({
  id: z.string(),
  campaign_id: z.string(),
  ordinal: z.number(),
  slug: z.string(),
  title: z.string(),
  location_ref: z.string().nullable(),
  in_game_start: z.string().nullable(),
  in_game_end: z.string().nullable(),
  present_character_refs: z.array(z.string()),
  present_pc_refs: z.array(z.string()),
  mood: z.string().nullable(),
  post_count: z.number(),
  threads_introduced: z.array(ThreadSchema),
  threads_paid_off: z.array(ThreadSchema),
  tags: z.array(z.string()),
  closed: z.boolean(),
  last_advance_at_post: z.number(),
  running_summary: z.string().nullable(),
  final_summary: z.string().nullable(),
  key_beats: z.array(z.string()),
  // Per-scene override; null = inherit the campaign default.
  narrator_response_mode: NarratorResponseModeSchema.nullable(),
});
export type ApiScene = z.infer<typeof ApiSceneSchema>;

/** The campaign views' name for the same wire shape (see module doc). */
export const SceneSummarySchema = ApiSceneSchema;
export type SceneSummary = ApiScene;

export const AuthorKindSchema = z.enum(["pc", "narrator", "npc", "system"]);

export const ApiAlternateSchema = z.object({
  id: z.string(),
  post_id: z.string(),
  text: z.string(),
  delta_set_id: z.string(),
  author_kind: AuthorKindSchema,
  model: z.string().nullable().optional(),
  prompt_hash: z.string().nullable().optional(),
  steering_hint: z.string().nullable().optional(),
  tokens: z.number().nullable().optional(),
  pinned: z.boolean(),
  is_primary: z.boolean(),
  created_at: z.string().nullable().optional(),
});
export type ApiAlternate = z.infer<typeof ApiAlternateSchema>;

export const ApiPostSchema = z.object({
  id: z.string(),
  scene_id: z.string(),
  order_in_scene: z.number(),
  author_kind: AuthorKindSchema,
  body: z.string(),
  is_player: z.boolean(),
  created_at: z.string(),
  turn_id: z.string(),
  author_pc_ref: z.string().nullable().optional(),
  author_npc_ref: z.string().nullable().optional(),
  alternates: z.array(ApiAlternateSchema).optional(),
  primary_alternate_id: z.string().nullable().optional(),
});
export type ApiPost = z.infer<typeof ApiPostSchema>;

export const SceneDetailSchema = z.object({
  scene: ApiSceneSchema,
  body: z.string(),
  posts: z.array(ApiPostSchema),
});
export type SceneDetail = z.infer<typeof SceneDetailSchema>;

export const PaginatedPostsResponseSchema = z.object({
  posts: z.array(ApiPostSchema),
  has_more: z.boolean(),
});
export type PaginatedPostsResponse = z.infer<typeof PaginatedPostsResponseSchema>;
