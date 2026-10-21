export type WorldMeta = {
  id: string;
  name: string;
  created: string;
  updated: string;
  counts: Record<string, number>;
  module?: string;
  /** The world profile (#38). `genre` rides on every shelf row; `tone` and
   *  `themes` only on `GET /worlds/{wid}`. Empty for a world without one, and
   *  absent from an older server. */
  genre?: string;
  tone?: string;
  themes?: string[];
  /** Cache-busting token for the world's cover, `""` when it has none. Derived
   *  by the route rather than stored in `world.md` — the same split the
   *  campaigns list makes, and for the same reason. */
  cover?: string;
};
/** What a world says about itself (#38): its description is `world.md`'s body,
 *  the rest its frontmatter. `themes` are descriptive only -- the greeting-
 *  gating tag vocabulary is a different thing (`listTags`). */
export type WorldProfile = { genre: string; tone: string; themes: string[]; description: string };
/** `GET /worlds/{wid}`: the world page's header and its column's numbers in
 *  one read.
 *
 *  `counts` is a directory tally per record kind. `campaigns` is how many
 *  campaigns are played in the world -- the one fact on that page that is not
 *  a record inside it, and the only field the route reads from outside the
 *  world. `null` when a campaign it had to look at could not be read, and
 *  absent from a server older than the field: both are "unknown" to the page,
 *  a dash, never a 0. */
export type WorldDetail = {
  meta: WorldMeta;
  body: string;
  counts: Record<string, number>;
  campaigns?: number | null;
};
export type CampaignSettingKey =
  | "context_scan_depth" | "context_budget" | "recap_depth" | "archive_depth"
  | "rolling_summary_every" | "scene_break_every"
  | "system_prompt" | "user_label" | "assistant_label";

export type CampaignSettings = {
  overrides: Partial<Record<CampaignSettingKey, string>>;
  inherited: Record<CampaignSettingKey, string>;
  effective: Record<CampaignSettingKey, string>;
};

export type CampaignMeta = {
  id: string;
  name: string;
  world: string;
  world_name?: string;
  created: string;
  updated: string;
  scenes: number;
  last_scene: string;
  /** The opening paragraph of campaign.md's body — the pitch the campaign was
   *  started from, shown as each card's blurb. "" when the body is empty. */
  blurb?: string;
  /** How many of `scenes` carry an absorb mark: how much of the chronicle, the
   *  ledger and the dossiers is caught up. Deliberately not derivable from
   *  `scenes` — playing a scene ahead of the absorb is the normal state of a
   *  campaign in progress. 0 when nothing has been absorbed yet, never absent:
   *  the list endpoint computes it beside `scenes` on the same pass, and the
   *  backend that answers is the one serving this bundle. */
  absorbed: number;
  /** Whole-campaign high-water mark: the later of campaign.md's `updated` and
   *  its newest scene's. `updated` alone misses play entirely, so anything
   *  ranking by "recently worked on" wants this. Only the list endpoint
   *  computes it -- GET /campaigns/{cid} returns the bare meta. */
  activity?: string;
  module?: string;
  /** Cache-busting token for the campaign's cover image, "" when it has none.
   *  A token rather than a boolean: it also makes the URL change when the
   *  bytes do, so a replaced cover cannot keep rendering from cache. */
  cover?: string;
  /** Lineage (#72): the id of the campaign this one was forked from, "" for one
   *  that was created rather than forked. An id and not a name, so a rename on
   *  either side leaves the link intact. It may name a campaign that is no
   *  longer in the list — a deleted parent leaves its children as roots, which
   *  is what the shelf renders them as. */
  parent?: string;
  /** The scene a retrospective fork was cut at, "" for a fork from where the
   *  campaign stood. Only the first kind is an approximation of a past state,
   *  so the two are worth telling apart on the card. */
  forked_from_scene?: string;
};

/** What a fork actually did (#72). `removed_scenes` is empty for a fork from
 *  now; for one cut at an earlier scene it lists, in play order, the scenes the
 *  copy does not have.
 *
 *  `records`, `refused` and `failed` mean exactly what they mean in
 *  `CascadeReport`, because that is what produced them: the cut runs each
 *  removed scene through the same reversal a cascade post-delete uses. So
 *  `refused` names records that kept what a removed scene gave them, and
 *  `failed` names cleanup that could not run — neither is a failure of the
 *  fork, which by then exists. */
export type ForkReport = {
  id: string; from_scene: string; removed_scenes: string[];
  records: number; refused: { label: string; reason: string }[]; failed: string[];
  /** Whether this answer is a REPLAY of an earlier fork made under the same
   *  idempotency key rather than a copy this call took (#409). A caller that
   *  retried after a lost response gets `true` and the first fork's report
   *  verbatim, which is how it learns it did not make a second campaign. */
  replayed: boolean;
  /** The post a fork AT A POST kept its scene through (play controls III).
   *  Absent on a fork cut at a scene, and on a report replayed from before it
   *  existed. */
  cut_at?: number;
};
/** A fork request's optional guards (#409). `idempotencyKey` makes a repeat
 *  safe; `expectRevision` is the source's write token as the caller priced the
 *  copy against it, checked under the source's own lock so a campaign written
 *  in between costs a refusal rather than a `copytree` of a state nobody asked
 *  for. */
export type ForkGuards = { idempotencyKey?: string; expectRevision?: string };
/** `done` is the scene's absorb mark: End Scene run to completion and its
 *  changes accepted, written into the scene's own frontmatter by
 *  `scenes.mark_absorbed`. It is what the rail marks and what the composer
 *  hides itself for. */
export type SceneMeta = {
  id: string; title: string; model: string; created: string; updated: string;
  date: string;
  /** The campaign-location id the scene ended at, "" if it never set one.
   *  Off the same frontmatter line as `date`, so the scenes list gets
   *  "when & where" without a second read per scene. */
  place?: string;
  pcless?: boolean; done?: boolean;
  /** The scene's branch group (play controls III), present only on a scene
   *  whose group has more than one member: siblings share it, and the scene
   *  they were branched from carries it too (its own identity is the group). */
  branch_group?: string;
  /** The identity of the scene this one was branched from. */
  branch_of?: string;
  /** The absorbed sibling that makes this branch read-only. Derived by the
   *  server, never stored: deleting or un-absorbing that sibling reopens it. */
  closed_by?: { sid: string; title: string };
};
export type Message = { role: "user" | "assistant"; content: string; speaker?: string;
  actor_ref?: string;
  response_thinking?: string;
  response_id?: string; response_part?: string; response_status?: "complete" | "incomplete";
  context_changed?: boolean; response_can_reroll?: boolean;
  post_id?: string;
  /** The text a display-phase regex rule shows in place of `content`, present
   *  only when it differs. `rewritten` marks a post whose stored text a rule
   *  rewrote, and `rewrite_key` names the record that says so (a later part of
   *  a response has a key of its own); `connection` is the connection that
   *  produced it. */
  shown?: string; rewritten?: boolean; rewrite_key?: string; connection?: string;
  /** Hidden from context: the ISO time it was hidden. Absent on a post that is
   *  in context -- the server never writes `false`. The post stays in the
   *  transcript and every export; it reaches no prompt. */
  excluded?: string };
// The response settings a record's prompt rendered (`settings`) and, for a roll
// continuation, the ones its resume prompt rendered (`resume_settings`).
export type ResponseSettingsRecord = { style_id: string; phase: string; words: number; paragraphs: number };
// `made_by` is the call that wrote a variant. Every key is optional because
// unknown is absent, never zero: a variant from before it existed has no
// `made_by`, and a key the provider never reported is missing rather than
// guessed. `note` is only what the player typed, never the app's template.
export type ResponseVariant = {
  id: string; content: string; reasoning?: string; status: string; issue?: string | null;
  made_by?: {
    task?: string; connection_id?: string; connection?: string; model?: string; provider?: string;
    composed?: "primary" | "resume"; guidance?: string; note?: string;
    // Only on a resume-composed variant: the settings its own prompt rendered,
    // since a later roll fence overwrites the record's `resume_settings`.
    settings?: ResponseSettingsRecord;
  };
};
export type ResponseRecord = { content: string; id: string; actor_ref: string | null; speaker: string; status: string;
  /** The inspector's "Approach" line (roadmap 02 §6.5), worded by the server;
   *  "" when no turn decision was read. */
  approach?: string;
  round_id: string | null; active_variant: string; context_changed: boolean; can_reroll: boolean;
  variants: ResponseVariant[];
  settings?: ResponseSettingsRecord; resume_settings?: ResponseSettingsRecord };
// The lock-free read the swipe arrows are drawn from (`GET .../responses/{rid}/swipe`):
// ids, statuses and provenance, never a variant's text. `active` indexes `variants` and is
// null when the record's active id matches none, or when `edited`: the post's prose was
// hand-edited and is no variant's, so it is not "n of m" and has no provenance.
// `settings` / `resume_settings` are null for a response from before they were recorded.
export type ResponseSwipe = {
  active: number | null;
  variants: Pick<ResponseVariant, "id" | "status" | "made_by">[];
  settings: ResponseSettingsRecord | null; resume_settings: ResponseSettingsRecord | null;
  can_reroll: boolean; editable: boolean; round_open: boolean; edited: boolean;
};
export type PassageCharacterDraft = { name: string; description: string; mes_example: string; quotes: string[] };
export type PassageCharacterInput = { name: string; passage: string; source_text: string };
export type PassageCharacterSave = PassageCharacterInput & { description: string; mes_example: string; existing_ref?: string };
export type Scene = { meta: { id: string; title: string; response_preset?: string;
  /** the greeting this scene was started from, verbatim or adapted (#91) */
  greeting?: string }; messages: Message[] };
// One stored variant of the generation a reroll replaces. `posts` is how many
// transcript posts it becomes (one reply can split per speaker), `preview` is
// clipped server-side, and `guidance` is the reroll hint that produced it.
// `id` is derived from the variant's content, not its position: retention drops
// the oldest take when a full set grows, and every index below it shifts.
// `model` is the route this variant was generated on (#77). The regenerate
// route stamps every reroll it archives, so "" means **no record** rather than
// "the scene's model" — it is what a variant written before the override
// existed carries, and what one reconciled out of the transcript rather than
// archived (a plain turn's reply, a hand edit) carries. A reader shows nothing
// for those instead of naming a model it would only be guessing at. Kept in
// step with `store.alternates`' module docstring, which says the same.
export type SceneAlternate = {
  id: string; created: string; guidance: string; model: string;
  posts: number; preview: string;
};
export type SceneAlternates = { active: number | null; alternates: SceneAlternate[] };
// A windowed read (`getScene` with a `limit`) carries the tail of the
// transcript plus the cursor to walk backwards from. `offset` is the absolute
// index of `messages[0]` — the index `editMessage` takes — so a client holding
// one page addresses a post exactly as one holding the whole scene does. The
// fields are absent from an unwindowed read, which returns the scene whole.
// `has_user_message` covers the WHOLE transcript, not the window: a tail page
// cannot tell on its own whether the run it holds was answering a player.
export type ScenePage = Scene & {
  offset?: number; total?: number; has_older?: boolean; has_user_message?: boolean;
};
