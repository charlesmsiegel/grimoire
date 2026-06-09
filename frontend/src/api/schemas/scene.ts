import { z } from "zod";

/**
 * Wire shapes for scenes and posts (`Scene` / `Post` / `Thread` in
 * `backend/src/grimoire/types/scene.py`, serialized through `to_payload`).
 * Two read views exist over the same `/scenes` payload: the campaign views'
 * `SceneSummary` and the play view's `ApiScene`; both are declared here so
 * they cannot drift from each other. Only fields the frontend reads are
 * declared; unknown extras are tolerated.
 */

export const InGameTimeSchema = z.object({
  moment: z.string(),
  calendar_id: z.string().nullable().optional(),
});
export type InGameTime = z.infer<typeof InGameTimeSchema>;

export const ThreadSchema = z.object({
  text: z.string(),
  introduced_in_post: z.string().nullable().optional(),
  paid_off_in_post: z.string().nullable().optional(),
  tags: z.array(z.string()),
});
export type Thread = z.infer<typeof ThreadSchema>;

export const SceneSummarySchema = z.object({
  id: z.string(),
  campaign_id: z.string(),
  ordinal: z.number(),
  slug: z.string(),
  title: z.string(),
  location_ref: z.string().nullable(),
  in_game_start: InGameTimeSchema.nullable(),
  in_game_end: InGameTimeSchema.nullable(),
  present_character_refs: z.array(z.string()),
  present_pc_refs: z.array(z.string()),
  mood: z.string(),
  post_count: z.number(),
  tags: z.array(z.string()),
  closed: z.boolean(),
  threads_introduced: z.array(ThreadSchema),
  threads_paid_off: z.array(ThreadSchema),
  summary: z.string(),
  key_beats: z.array(z.string()),
});
export type SceneSummary = z.infer<typeof SceneSummarySchema>;

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
  in_game_start: InGameTimeSchema.nullable(),
  in_game_end: InGameTimeSchema.nullable(),
  present_character_refs: z.array(z.string()),
  present_pc_refs: z.array(z.string()),
  mood: z.string(),
  post_count: z.number(),
  closed: z.boolean(),
  last_advance_at_post: z.number().nullable(),
  running_summary: z.string(),
  summary: z.string(),
  threads_introduced: z.array(z.object({ text: z.string() })).optional(),
  threads_paid_off: z.array(z.object({ text: z.string() })).optional(),
  // Scene override; only present on responses that carry narrator-mode state.
  narrator_response_mode: NarratorResponseModeSchema.nullable().optional(),
});
export type ApiScene = z.infer<typeof ApiSceneSchema>;

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
