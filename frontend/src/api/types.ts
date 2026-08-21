/** The API's data model: every payload shape the backend sends or takes, plus
 *  the handful of constants and pure helpers that describe them.
 *
 *  Split out of `client.ts`, which had grown to 2100 lines by holding both the
 *  types and the calls -- two groups of exports that never reference each
 *  other, which is exactly the seam the code-health report named. `client.ts`
 *  re-exports everything here, so `import { api, type Card } from
 *  "../api/client"` keeps working and no caller had to change.
 *
 *  Nothing in this module may import from `client.ts`: the types describe the
 *  wire format and know nothing about how a request is made.
 */
import type { Model } from "./models";
import type { RollProposalPayload } from "./stream";

export type LLMConnectionKind = "openrouter" | "claude" | "openai_compatible";
// `model` is what is STORED (what the connection editor edits); `effective_model`
// is what a generation on it will actually run — they differ for `claude`
// alone, which substitutes a default for an unset model. Any surface naming
// the model a connection will use wants the second (#77).
export type LLMConnection = {
  id: string; kind: LLMConnectionKind; name: string;
  base_url: string; model: string; effective_model: string;
  post_process: "none" | "strict";
  key_set: boolean; rev: string;
};
export type LLMConnectionDetail = LLMConnection & { models: Model[]; fetched_at: string };
/** The active connection as `GET /config` reports it — id, kind, name, and the
 *  EFFECTIVE model (a `claude` connection with none configured still runs one).
 *  Named rather than inlined on `Config` because two surfaces read it: the
 *  status bar, and the reroll popover's route picker (#77). */
export type ActiveConnection = {
  id: string; kind: LLMConnectionKind; name: string; model: string;
};
export type LLMConnectionDraft = {
  kind?: LLMConnectionKind; name?: string; base_url?: string; api_key?: string;
  model?: string; post_process?: "none" | "strict";
};
export type ModelsRefreshResult = { models: Model[]; fetched_at: string; rev: string };
export type Config = {
  theme: string; system_prompt: string;
  quote_color: string; user_label: string; assistant_label: string;
  active_connection_id: string;
  active_connection: ActiveConnection | null;
  ready: boolean;
  /** Seconds of silence before an LLM call is abandoned; "0" disables. */
  llm_timeout: string;
  /** Seconds one absorb's whole LLM sequence may take; "0" disables. */
  absorb_budget: string;
  /** Seconds one non-streaming LLM call may take in total; "0" disables. */
  llm_call_budget: string;
  /** Re-attempts a transiently-failed generation gets before its connection is
   *  given up on (#144); "0" is the old one-attempt behaviour. */
  llm_retries: string;
  /** Connection a generation falls back to once the active one is exhausted,
   *  tried once; "" = no fallback. */
  fallback_connection_id: string;
  context_budget: string;
  /** Recent transcript messages every keyword scan reads — world info, chronicle
   *  recall, keyed mechanics rules and the semantic-recall query all share this
   *  window. "0" empties it; a scene opener's prompt and a director's note seed
   *  activation themselves either way. */
  context_scan_depth: string;
  archive_depth: string;
  /** "on" once the setup wizard has been finished or dismissed (#194). */
  setup_done: string;
  /** Server's verdict on whether to show the setup wizard: the flag is unset
   *  AND the store holds no worlds and no campaigns. Derived rather than left
   *  to the client so a boot needs one request, not three. */
  first_run: boolean;
  /** The store this config describes, so a client can tell that a decision it
   *  made about `first_run` belongs to a library it is no longer looking at. */
  data_dir: string;
  prompt_log_depth: string;
  /** Posts of transcript tail the transient-state ledger is read over; "0" disables it. */
  turnstate_depth: string;
  /** Consecutive recorded values that promote a transient field to character state. */
  promote_streak: string;
  /** Posts between live rolling-summary refreshes; "0" turns the automatic
   *  refresh off, leaving only the inspector's own Refresh button. */
  rolling_summary_every: string;
  /** Posts between scene-break questions; "0" turns the whole feature off,
   *  panel included. Only the cadence — the heuristic still has to agree
   *  before anything reaches a provider, so the real cost is well under one
   *  call per this many posts. */
  scene_break_every: string;
  /** Characters the off-scene cast's "known to exist" tier may name; "0" = no
   *  ceiling. Over it, the ones the in-scene cast mentions are kept first. */
  offscene_known_limit: string;
  /** Semantic recall: the openai_compatible connection serving /embeddings, "" = off. */
  embeddings_connection_id: string;
  embeddings_model: string;
  /** Entries a similarity pass may add on top of the keyword ones; "0" = off. */
  semantic_recall_depth: string;
  /** Cosine floor a recalled entry must clear. */
  semantic_recall_threshold: string;
  /** "on" applies the stored prompt layout; "off" (the default) renders the
   *  catalog and LEAVES the layout on disk, so it can be A/B'd. */
  prompt_layout_enabled: string;
  /** "on" renders the active-speaker section in group scenes; "off" default. */
  speaker_turn_taking: string;
  /** "on" once automatic backups are enabled (#32); "off" on every install
   *  until someone turns them on. */
  backup_enabled: string;
  /** Hours between automatic backups. */
  backup_interval_hours: string;
  /** Archives kept by the retention sweep; "0" keeps every one. */
  backup_keep: string;
  /** Where archives are written; "" means `<data dir>/backups`. */
  backup_dir: string;
  /** Model turns a retcon replay may redo before the transcript offers to fork
   *  the campaign first (#80). A threshold, not a limit: "0" nudges every
   *  replay rather than none. */
  replay_fork_threshold: string;
  /** Days a clock advance may cross before the panel offers to checkpoint the
   *  campaign first (#107). The same kind of threshold as the one above, and
   *  the same "0" reading — every skip that crosses a day is asked about. The
   *  comparison itself is the server's (`AdvanceDigest.fork`); this is only
   *  where the number is set. */
  advance_fork_threshold: string;
  /** The quietest level `store/logs.py` writes down. The STORED setting, which
   *  is not necessarily what is in force: an unrecognized value is narrowed to
   *  the default on the server, and `GET /logs/level` reports the real one. */
  log_level: string;
};
/**
 * The subset of Config the Configuration page writes — the mirror of the
 * backend's `ConfigUpdate`. Named rather than inlined at each call site: it
 * was spelled out three times, and the copies drifted the moment a field was
 * added.
 */
export type ConfigUpdate = Partial<Pick<Config,
  "theme" | "system_prompt" | "quote_color" | "user_label" | "assistant_label" |
  "active_connection_id" | "llm_timeout" | "absorb_budget" | "llm_call_budget" |
  "llm_retries" | "fallback_connection_id" |
  "context_budget" | "context_scan_depth" | "archive_depth" |
  "setup_done" | "prompt_log_depth" |
  "turnstate_depth" | "promote_streak" | "rolling_summary_every" |
  "scene_break_every" |
  "offscene_known_limit" |
  "embeddings_connection_id" | "embeddings_model" |
  "semantic_recall_depth" | "semantic_recall_threshold" |
  "prompt_layout_enabled" | "speaker_turn_taking" |
  "backup_enabled" | "backup_interval_hours" | "backup_keep" | "backup_dir" |
  "replay_fork_threshold" | "advance_fork_threshold" | "log_level">>;
/** One archive written by `store/backups.py`. */
export type BackupEntry = {
  name: string;
  /** Bytes on disk. */
  size: number;
  /** When it was taken, read from its own filename. */
  created: string;
};
/** The archives, and the directory they live in — which is a setting, so the
 *  answer to "why is this list empty" is often "you moved it". */
export type BackupList = { dir: string; backups: BackupEntry[] };
/** A `POST /api/backups`: the refreshed listing, plus what that call did.
 *  `retention_error` is set when the archive was written but the sweep that
 *  follows it could not run — a success with a problem attached, which is a
 *  third outcome the two-state ok/failed shape could not say. */
export type BackupRun = BackupList & {
  created: string;
  swept: string[];
  retention_error: string | null;
};
export type DataDirInfo = {
  data_dir: string;
  default: string;
  is_default: boolean;
  source: "env" | "custom" | "default";
  exists: boolean;
};
/** A file a sync client left behind when two devices wrote the same record
 *  (#35). `path` is relative to the store root, slash-separated. */
export type StoreConflict = {
  path: string; name: string; tool: string;
  kind: "file" | "directory";
  /** null for a directory — a conflicted folder is reported, not measured. */
  size: number | null;
  modified: string;
};
export type StoreConflicts = { conflicts: StoreConflict[]; truncated: boolean };
export type WorldMeta = {
  id: string;
  name: string;
  created: string;
  updated: string;
  counts: Record<string, number>;
  module?: string;
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
};
/** `done` is the scene's absorb mark: End Scene run to completion and its
 *  changes accepted, written into the scene's own frontmatter by
 *  `scenes.mark_absorbed`. It is what the rail marks and what the composer
 *  hides itself for. */
export type SceneMeta = { id: string; title: string; model: string; created: string; updated: string; date: string; pcless?: boolean; done?: boolean };
export type Message = { role: "user" | "assistant"; content: string; speaker?: string };
export type Scene = { meta: { id: string; title: string; response_preset?: string }; messages: Message[] };
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

// entities
// The kinds THIS BUILD knows about, and the union everything else types
// against. The import dialogs' Category dropdown is this list intersected with
// `GET /api/entity-kinds` (see `components/useEntityKinds.ts`), so a kind
// added to `store.entities.ENTITY_KINDS` reaches it without either dialog
// being edited (#138) — once this list learns the kind, which
// `test_the_frontend_ships_the_same_kind_list` requires. It has to stay the
// compile-time union anyway: the tabs, labels and per-kind field table are all
// written against named kinds, which is the same reason the dropdown will not
// offer a kind that is missing from it.
// Import it from `../api/types` and not through `../api/client`: a component
// that reads it at module scope would otherwise crash every suite that mocks
// the client wholesale, including suites that only import a helper out of it.
export const ENTITY_KINDS = ["locations", "lore", "items", "groups", "creatures"] as const;
export type EntityKind = (typeof ENTITY_KINDS)[number];
/** A kind as it comes back over HTTP: one of the above, or one this build has
 *  not heard of. `str` on the wire too (`routes.models.LoreEntry.category`),
 *  validated against `entities.ENTITY_KINDS` at the commit boundary rather than
 *  by its type. Use `EntityKind` for kinds this code names itself.
 *
 *  `string & {}` rather than a bare `string`: it widens to any string exactly
 *  as the wire does, but keeps the five known kinds as editor completions
 *  instead of collapsing the union away. */
export type EntityKindName = EntityKind | (string & {});
export type EntityScope = { kind: "world" | "campaign"; id: string };

// ---- library moves (#52, #53, #60) ----
//
// The kinds that can move between a campaign and its world. Wider than
// EntityKind on both ends: greetings are a flat synced record too, and promote
// carries actors. Which of the four operations accepts which kind is the
// store's rule (`store/sync.py`), reported per record by `libraryStatus` —
// this type only says what is addressable.
export type LibraryKind = EntityKind | "greetings" | "characters" | "pcs";

/** Where one campaign record stands relative to its world's library.
 *
 *  `can_promote` / `can_push` are the server's own preconditions rather than
 *  anything derived here: an editor that recomputed them would drift into
 *  offering the button that 409s. */
export type LibraryStatus = {
  in_library: boolean;
  diverged: boolean;
  can_promote: boolean;
  can_push: boolean;
};

export type DivergedRecord = { ref: { kind: EntityKind | "greetings"; id: string }; name: string };

/** A campaign that would notice a library record going away. `has_copy` says
 *  whether it already holds its own — the ones that do not are what demote's
 *  copy-down is for. */
export type LibraryDependent = { id: string; name: string; has_copy: boolean };

// Mirrors backend/src/grimoire/store/entity_schema.py — keep in sync.
export const ENTITY_FIELDS: Record<EntityKind, { key: string; label: string }[]> = {
  locations: [
    { key: "climate", label: "Climate" },
    { key: "persistence", label: "Weather persistence" },
    { key: "weather_zone", label: "Weather zone" },
  ],
  lore: [],
  items: [{ key: "item_type", label: "Type" }, { key: "rarity", label: "Rarity" }],
  groups: [{ key: "group_type", label: "Type" }],
  creatures: [{ key: "creature_type", label: "Type" }, { key: "threat", label: "Threat" }],
};

// Mirrors store.entities.SECRECY_LEVELS. `owners` says what puts an entry in
// the prompt; `secrecy` says how the prompt may use it once there — "secret"
// renders under a "don't let uninvolved characters reveal this" heading,
// "gm-only" never reaches the model at all. Absent == "public".
export const SECRECY_LEVELS = ["public", "secret", "gm-only"] as const;
export type Secrecy = (typeof SECRECY_LEVELS)[number];
export const SECRECY_LABELS: Record<Secrecy, string> = {
  public: "Public", secret: "Secret", "gm-only": "GM-only",
};

// `tokens` is what this record's body costs when it reaches a prompt, counted
// server-side with the same tokenizer as the context inspector (#51). Optional
// because it is a measurement rather than stored data — a payload written
// before it existed simply has none, and the badge stays off. Measured
// regardless of `secrecy`: it is the cost of the text, and a gm-only body
// costs nothing because it is never sent, not because it is short.
export type EntitySummary = { id: string; name: string; keys?: string; owners?: string;
  secrecy?: string; has_image?: boolean; image_v?: string | null; tokens?: number } & Record<string, unknown>;
export type EntityDetail = {
  meta: { id: string; name: string; keys?: string; owners?: string; secrecy?: string;
    sd_prompt?: string } & Record<string, unknown>;
  body: string;
  tokens?: number;
  /** Content hash of the record as read. Echo it back on save and the write is
   *  refused with 409 `stale_record` if the file moved underneath (#35). */
  rev: string;
};

// characters (V3 cards)
export type CardData = {
  name: string;
  description?: string;
  personality?: string;
  scenario?: string;
  first_mes?: string;
  mes_example?: string;
  system_prompt?: string;
  post_history_instructions?: string;
  alternate_greetings?: string[];
  creator?: string;
  creator_notes?: string;
  tags?: string[];
  character_book?: { entries?: unknown[] };
  extensions?: { sd_prompt?: string; [k: string]: unknown };
  [k: string]: unknown;
};
export type Card = { spec: string; spec_version: string; data: CardData };
/** The card containers the backend reads and writes (`_EXPORT_MEDIA`). */
export type CardFormat = "json" | "png" | "charx";
export type VersionRef = { id: string; name: string };
export type CharacterSummary = {
  id: string; name: string; default_version: string; has_avatar?: boolean;
  /** Cache token for the avatar's current bytes. Spent as `?v=`, which the
   *  server answers immutable — so it must name the BYTES, never a counter. */
  avatar_v?: string | null;
  avatar_focus?: number | null; gallery_count?: number; localized_count?: number;
  greeting_count?: number; tagline?: string; versions: VersionRef[];
};
export type CharacterDetail = {
  meta: { id: string; name: string; default_version: string; birthdate?: string };
  versions: { id: string; name: string; card: Card; images?: string[];
              /** Per-image cache token, keyed by the names in `images`. */
              image_v?: Record<string, string>;
              /** What each image DEPICTS, in the author's words, keyed by the
               *  names in `images`. A key is absent while an image has never
               *  been reviewed and `""` once it has been reviewed and left
               *  deliberately undescribed — the two are not the same, and only
               *  the first belongs in the describe queue. */
              image_descriptions?: Record<string, string>;
              avatar_focus?: number | null; chub_source?: string; is_chub?: boolean;
              /** Embedded-lorebook entries the import would actually commit —
               *  server-side, through the same normalization the import runs,
               *  so it excludes the disabled and blank entries `character_book`
               *  can carry. Never count `card.data.character_book.entries`. */
              importable_lore?: number }[];
};
export type ChubImportResult = {
  character: string;
  version: string;
  updated: boolean;
  gallery: { attempted: number; stored: number };
  lore: { lorebooks_found: number; created: { kind: string; id: string }[] };
};
export type ChubUnlinkedVersion = { character: string; character_name: string; version: string; version_name: string };

// PCs
export type Persona = { name: string; pronouns: string; summary: string; description: string; birthdate?: string };
export type PCSummary = {
  id: string; name: string; tags: string[]; default_version: string; versions: VersionRef[];
  // Same derived image fields a CharacterSummary carries, bar `localized_count`
  // — only a character card's text is localized, so a PC has no `embed-` images.
  has_avatar?: boolean; avatar_focus?: number | null; gallery_count?: number;
};
export type PCDetail = {
  meta: { id: string; name: string; tags: string[]; default_version: string };
  versions: { id: string; name: string; persona: Persona; images?: string[];
              /** See `CharacterDetail` — absent and `""` differ. */
              image_descriptions?: Record<string, string>;
              avatar_focus?: number | null }[];
};

// campaign sync: what the world has that a campaign has not taken yet (#6, #8).
// A ref is `{kind, id}` and the shapes on either side of it differ by kind, so
// `IncomingBlob` is the union flattened into optional fields rather than a
// discriminated one: the backend tags nothing (`store/sync.py`), and which
// field arrived IS the discriminant.
export type IncomingRef = { kind: string; id: string };
/** `new` has no campaign copy to compare against; `update` means the campaign
 *  copy still matches the base the world moved on from, so taking the world's
 *  version loses nothing; `conflict` means both sides changed. */
export type IncomingStatus = "new" | "update" | "conflict";
/** One side of an incoming change. `card` for a locked character version,
 *  `persona` for a locked PC version, `body` for everything else — an entity, a
 *  plot map, or the version list of an actor with no locked version. */
export type IncomingBlob = {
  name: string; version?: string; body?: string; card?: Card; persona?: Persona;
};
export type IncomingItem = {
  ref: IncomingRef; status: IncomingStatus; world: IncomingBlob;
  /** Absent when the campaign has no copy of its own to weigh against. */
  mine?: IncomingBlob;
};
/** One campaign descended from a world, and how much of that world it has not
 *  taken yet — the world-side half of the same question (#8). */
export type WorldCampaignPending = {
  id: string; name: string;
  pending: { new: number; update: number; conflict: number };
};

// greetings & plot maps
export type GreetingMark = "played" | "completed" | "skipped" | null;
export type Greeting = {
  id: string;
  name: string;
  character: string;
  version: string;
  present: string[];
  requires_tags: string[];
  predecessor_join: "all" | "any";
  pcless?: boolean;
  /** A location id, "" for none (#218). Always on the wire — a greeting
   *  written before the key existed lacks it in its FRONTMATTER, and the store
   *  reads that back as "". Optional here for the same reason `pcless` is. */
  location?: string;
  mark?: GreetingMark;   // campaign lists carry it
};
export type Edges = { leads_to: string[]; excludes: string[] };
export type GreetingDetail = { meta: Greeting; body: string; edges: Edges; predecessors: string[];
  /** See EntityDetail.rev (#35). */
  rev: string };
export type GreetingDraft = {
  name: string;
  character: string;
  version: string;
  body?: string;
  present?: string[];
  requires_tags?: string[];
  predecessor_join?: "all" | "any";
  pcless?: boolean;
  location?: string;
};
export type Style = { id: string; name: string; description: string; tags: string[]; built_in: boolean };
export type StyleDetail = { meta: Style; body: string };
export type StyleDraft = { name: string; description?: string; tags?: string[]; body?: string };

// response presets & the response bundle (scoped preset + overrides + resolution)
// The explicit "clear the inherited style" sentinel, mirroring
// response_presets.STYLE_CLEAR byte for byte. "" is a different answer — "this
// scope has no opinion, keep walking outward". The U+2063 prefix (invisible
// separator, as in scenes.ROLL_SPEAKER) keeps it out of slugify's reach, so a
// user style genuinely named "None" — id `none` — stays an ordinary style.
// Defined here, not per-component, so the two pickers cannot drift apart.
export const STYLE_CLEAR = "⁣none";
export type ResponsePresetSummary = {
  id: string; name: string; description?: string; built_in: boolean;
  style_id?: string; length_preset?: string;
  reply_words?: string; blocks?: string; paragraphs?: string;
  speakers?: string; blocks_per_speaker?: string;
  validity?: { valid: boolean; issues: string[] };
};
export type ResponsePresetDetail = { meta: ResponsePresetSummary; validity: { valid: boolean; issues: string[] } };
export type ResponsePresetDraft = {
  name: string; description?: string; style_id?: string; length_preset?: string;
  knobs?: Record<string, number> | null;
};
export type LengthPreset = {
  reply_words: number; blocks: number; paragraphs: number; speakers: number; blocks_per_speaker: number;
};
export type ResponsePresetUsageEntry = {
  scope: "global" | "campaign" | "scene"; id: string; name: string;
  before: Partial<ResponseEffective>; after: Partial<ResponseEffective>;
};
/** A scope the impact scan could not read. Rendering the affected list without
 * these turns a partial answer into a confident "nothing else changes" — the
 * one thing a preview shown before an irreversible delete must never do. */
export type ResponsePresetUsageSkip = {
  scope: "campaign" | "scene"; id: string; name: string; reason: string;
};
export type ResponsePresetUsage = {
  affected: ResponsePresetUsageEntry[]; unevaluated?: ResponsePresetUsageSkip[];
};
export type ResponseFields = {
  response_preset: string; style_id: string;
  length_reply_words: string; length_blocks: string; length_paragraphs: string;
  length_speakers: string; length_blocks_per_speaker: string;
};
export type ResponseEffective = {
  style_id: string; reply_words: number; blocks: number; paragraphs: number;
  speakers: number; blocks_per_speaker: number;
};
export type ResponseProvenance = Record<string, { scope: string; source?: string }>;
// A one-shot, unpersisted per-turn override — the same scope-shaped dict
// response_presets.resolve(turn=...) accepts server-side: a bare
// {response_preset: id} or loose knob overrides.
export type ResponseOverride = Partial<ResponseFields>;
/** Everything ONE reroll may override, all of it riding that call alone.
 *  `connection_id` and `model` are the manual route override (#77): the
 *  connection to send this reroll to, and the model to drive it at. Empty
 *  means the standing configuration for that half — they compose, so a
 *  connection with no model uses the connection's own. */
export type RegenerateOverrides = {
  guidance?: string;
  response?: ResponseOverride;
  connection_id?: string;
  model?: string;
};
export type ResponseBundle = ResponseFields & { effective: ResponseEffective; provenance: ResponseProvenance };
export type Availability = {
  id: string; name: string; available: boolean; reasons: string[]; unlocked: boolean;
  pcless?: boolean;
  /** The greeting's location id, "" for none — what `greetingDraft` pre-fills
   *  the confirm form's location picker from (#218). */
  location?: string;
  mark?: GreetingMark;
};
export type Appearance = { gid: string; greeting_name: string; name: string; url: string; thumb?: string };

// cast
export type Actor = { kind: "characters" | "pcs"; id: string; role: "player" | "npc"; name: string };
/** What the newest turn's prose suggests about the cast (#97, #98). Every
 *  entry is a candidate the reader confirms or dismisses; nothing here has
 *  been applied. */
export type CastChanges = {
  enter: { kind: string; id: string; name: string; mentioned_by: string[] }[];
  leave: { kind: string; id: string; name: string; quote: string }[];
  unknown: { name: string; mentioned_by: string[] }[];
};
/** What the seated cast's CARDS suggest about who else belongs (#96) — the
 *  other half of `CastChanges`, which reads the turn's prose instead. Each is
 *  a character this campaign has not seen who was named in the card text of
 *  someone on stage; `mentioned_by` is a list of character *ids*, which the
 *  caller resolves to names. */
export type Suggestion = { character: string; name: string; mentioned_by: string[] };
export type RosterEntry = {
  kind: string; id: string; version: string; role: string; scenes: string[];
};
export type SceneLocationRef = { id: string; name: string };
export type SceneLocation = { current: SceneLocationRef | null; visited: SceneLocationRef[] };
export type SceneDatetimeCast = { kind: string; id: string; name: string; age: number | null; birthday_today: boolean };
export type SceneDatetimeFacts = {
  native: string; friendly: string; weekday: string; secondary_friendly: string | null;
  holidays_today: string[]; upcoming: { name: string; in_days: number } | null; cast: SceneDatetimeCast[];
};
export type SceneDatetime = { current: SceneDatetimeFacts | null; history: string[]; suggested: string | null };
export type CalendarBlock = {
  provider: string; region: string;
  custom_holidays: Array<{ name: string; month: number | string; day?: number; nth?: number; weekday?: number }>;
  anchor: { native: string; gregorian: string } | null;
};
export type CalendarMonth = { key: string; name: string; days: number };
/** Where a calendar lives: a campaign's own copy, or the world default it was
 *  created from (#223). Structurally an `EntityScope` and deliberately the
 *  same type, so the one URL builder serves both surfaces. */
export type CalendarScope = EntityScope;

/** Split a native datetime on its trailing Thh:mm only — month tokens may contain T. */
export function splitNativeDate(native: string): { date: string; time: string | null } {
  const m = native.match(/T(\d{1,2}:\d{2})$/);
  return m ? { date: native.slice(0, m.index), time: m[1] } : { date: native, time: null };
}

export type CalendarConfig = {
  primary: CalendarBlock; secondary: CalendarBlock | null; confirmed: boolean;
  /** How long a thread or commitment may go untouched before the ledger calls
   *  it stale (#103). Sent back on save: a client that drops it is a campaign
   *  reset to a threshold nobody chose. */
  stale_after_days: number;
};

// ---- the campaign clock (#100) ----
/** One row of the clock's log: where time went, why, and when that was recorded. */
export type ClockLogEntry = { from: string; to: string; reason: string; at: string };
export type CampaignClock = { now: string; friendly: string; log: ClockLogEntry[] };

/** One image in a campaign's own library (#376). `v` is the cache token an
 *  `?v=` URL is answered `immutable` for. */
/** One stored image with no description entry at all — the describe backlog.
 *  Key ABSENT, not empty: an image reviewed and deliberately left undescribed
 *  is finished, and never appears here. */
export type UndescribedImage = {
  kind: string; id: string; vid: string; name: string;
  record_name: string; url: string;
};
export type CampaignImage = {
  name: string; ext: string; v: string;
  /** What the picture shows. `described` is separate on purpose: `description`
   *  is `""` both for "never reviewed" and for "reviewed, nothing to say", and
   *  only `described` tells them apart. */
  description?: string; described?: boolean;
};
/** How long a record has been owed (#103), computed at read time and never
 *  stored. `overdue` needs a `due` the campaign's calendar can parse — a
 *  deadline written in the fiction's own words ("before the harvest moon") ages
 *  by staleness alone. `due_in` is the not-yet-due side of the same number. */
export type Aging = {
  state: "ok" | "stale" | "overdue";
  days_since: number | null; days_over: number | null; due_in: number | null;
};
/** One scheduled event (#101): a dated thing this campaign has planned, and the
 *  stamp the clock writes when it reaches the day. `fired` is null until then,
 *  and carries both reckonings — `at` is wall-clock, `moment` the in-world date
 *  the clock landed on. */
export type ScheduledEvent = {
  id: string; name: string; date: string; friendly: string; note: string;
  fired: { at: string; moment: string } | null;
  /** The campaign's present is past this day and nothing ever fired it — a
   *  reading the server computes, never a stored state. No advance can reach
   *  such an event (a span starting at "now" cannot contain a day behind it),
   *  so the row is asking to be re-dated forward or deleted. */
  passed: boolean;
};
/** What an advance crosses. Deterministic, so the preview and the advance that
 *  follows it report the same thing. `truncated` means the span was too long to
 *  itemize — `elapsed_days` is exact either way, and `events` is listed however
 *  long the span, since those are the campaign's own authored rows and the ones
 *  the advance fires. */
export type AdvanceDigest = {
  from: string; to: string; from_friendly: string; to_friendly: string;
  elapsed_days: number; backward: boolean; truncated: boolean;
  holidays: { name: string; native: string; friendly: string; in_days: number }[];
  birthdays: { name: string; age: number; native: string; friendly: string }[];
  events: (ScheduledEvent & { in_days: number })[];
  // The ledger's rows without the resolved scene label: the digest reads the
  // stores directly and joins no scene titles, so the type says so rather than
  // promising a field the panel would render as `undefined`.
  open_threads: (Omit<PlotThread, "scene"> & { aging: Aging })[];
  commitments: (Omit<Commitment, "scene"> & { aging: Aging })[];
  /** Counted over both lists, aged against the moment the move LANDS on — what
   *  the skip will leave overdue, which is the question before confirming it. */
  aging: { overdue: number; stale: number; stale_after: number };
  /** The checkpoint nudge (#107): true when this span is long enough that the
   *  panel offers to fork the campaign before skipping it. Server-computed for
   *  the reason every other number here is — the span is calendar arithmetic,
   *  and in "skip to a date" mode the client cannot know it without asking. */
  fork: boolean;
  /** The configured day count `fork` was reached by, so the prompt can say what
   *  "large" means in this install without a second request for it. */
  fork_threshold: number;
};
/** `to` skips to a date, `days` advances by a duration; `to` wins if both are sent. */
export type AdvanceRequest = { to?: string; days?: number; reason?: string };

// ---- weather (#45, #195) and climates (#40) ----
export const WEATHER_AXES = ["condition", "temperature", "wind"] as const;
export type WeatherAxis = (typeof WEATHER_AXES)[number];
export type WeatherAxes = Record<WeatherAxis, string>;
/** "procedural" means drawn, not authored — the HUD marks the other two. */
export type WeatherSource = Record<WeatherAxis, "procedural" | "manual" | "extractor">;

export type WeatherSpan = {
  id: string; location?: string; from: string; to: string | null;
  condition?: string; temperature?: string; wind?: string;
  note?: string; source?: string; seq?: number; set_at?: string; suppress?: string[];
};

export type SceneWeather = {
  weather: WeatherAxes | null;
  source?: WeatherSource;
  procedural?: WeatherAxes;
  stack?: WeatherSpan[];
  climate?: string;
  season?: string;
  location: string | null;
  native: string | null;
  /** The block ordinal. */
  ordinal?: number;
  /** Blocks from here to the end of the displayed date. Server-computed: the
   *  ordinal alone cannot distinguish 01:00 (the previous date's night, with a
   *  whole day ahead) from an ordinary 22:00 night. */
  blocks_left_today?: number;
  /** The active season's entries, per axis. Server-supplied: the client cannot
   *  derive them without reimplementing the climate fallback chain and the
   *  calendar's year-fraction arithmetic. */
  tables?: Record<WeatherAxis, string[]>;
};

export type WeatherOverrideBody = {
  location: string; start: string; end?: string | null;
  condition?: string; temperature?: string; wind?: string;
  note?: string; suppress?: string[]; clear?: boolean; blocks?: number | null;
  /** Which moment `blocks` is counted from, when not `start`. */
  blocks_from?: string;
};

export type WeatherRangeBody = {
  location: string; start: string; end?: string | null; axes?: WeatherAxis[];
  /** A block count instead of an `end`. Server-side so the client never has to
   *  reimplement the calendar's month lengths. */
  blocks?: number | null;
};

export type ClimateSummary = { id: string; name: string; builtin: boolean; custom: boolean };
export type ClimateEntry = { name: string; weight: number; requires_temp?: string[] };
export type ClimateSeason = {
  name: string; from: number; to: number;
  temperature: ClimateEntry[]; conditions: ClimateEntry[]; wind: ClimateEntry[];
};
export type Climate = { id: string; name: string; persistence: number; seasons: ClimateSeason[] };
/** `dropped` sections were rendered but left out of the prompt by the budget
 *  packer; they still carry their text so the inspector can show what was cut.
 *  `trimmed` is how many history messages the packer dropped from the front —
 *  0 on every section except Conversation history. */
export type ContextSection = {
  /** Stable section identity (#29). OPTIONAL because a prompt snapshot frozen
   *  before ids existed does not carry one — those predate editable labels
   *  too, so their labels are still unique and are a safe fallback key. */
  id?: string;
  label: string; text: string; tokens: number;
  tier: "lock-in" | "spotlight" | "background" | "archive" | "recalled" | "history";
  dropped: boolean; trimmed: number;
  /** The section carries content the reader pinned, so the packer left it alone
   *  whatever its tier (#129). Optional: snapshots frozen before pins existed
   *  have no such field, and they are rendered by this same component. */
  pinned?: boolean;
};
/** One row of the prompt layout editor. `label` is what the INSPECTOR calls the
 *  section — never what the model reads, which each template emits itself.
 *  `default_label` is the catalog's, shown as the input's placeholder. */
export type PromptLayoutSection = {
  id: string; label: string; default_label: string;
  tier: string; enabled: boolean;
};
export type PromptLayout = { enabled: boolean; sections: PromptLayoutSection[] };
/** `total_tokens` counts kept sections only — what was actually sent.
 *  `budget_tokens` is 0 when no budget is configured (nothing is dropped). */
/** One bucket of the usage ledger (#152) — a window, a model, a task, a scene.
 *  `cost_usd` is money a provider charged; `estimated_usd` is what
 *  subscription-billed calls would have cost and did not, so the two are never
 *  added together. `unpriced_calls` is what neither covers: a total is only the
 *  whole story when that is zero. The cache pair is a slice *of*
 *  `prompt_tokens` (#148) and is deliberately absent from `total_tokens`. */
export type UsageBucket = {
  calls: number; errors: number;
  prompt_tokens: number; completion_tokens: number; total_tokens: number;
  cache_read_tokens: number; cache_write_tokens: number;
  /** The three money columns, and **no two of them may be added**. `cost_usd`
   *  is what a provider charged. `estimated_usd` is what a subscription-billed
   *  call would have cost at API rates and did not. `modelled_usd` is what the
   *  user's own per-token table (#158) says the calls nobody priced would have
   *  cost — the weakest of the three, and the only one grimoire computed. Each
   *  has its own call count so a view can say how much of a total is which. */
  cost_usd: number; estimated_usd: number; modelled_usd: number;
  priced_calls: number; unpriced_calls: number;
  subscription_calls: number; modelled_calls: number;
  /** The slice of `unpriced_calls` that NO rate could ever price, because the
   *  provider reported no token counts either. The split is what lets a view
   *  tell a reader whether typing a rate would help — for these it would not,
   *  and saying so anyway sends them to an action that cannot succeed. */
  unmetered_calls: number;
  duration_ms: number;
};
/** A bucket with the thing it buckets — a task name, a model, a day. */
export type UsageBreakdown = UsageBucket & { key: string };
/** One metered call (#153). `cost_usd` is **null, not 0**, when the provider
 *  priced nothing: no OpenAI-compatible endpoint reports a price today, and
 *  rendering those turns as free is the one thing this view must not do. */
export type UsageTurn = {
  ts: string; task: string; model: string;
  status: string; error: string; attempts: number;
  prompt_tokens: number; completion_tokens: number; total_tokens: number;
  cache_read_tokens: number; cache_write_tokens: number;
  cost_usd: number | null; cost_basis: string;
  /** What the user's rate table says this cost, for a turn `cost_usd` is null
   *  for. Null when it is priced already, and null when nothing can price it —
   *  the two are told apart by `cost_usd`, not by this. */
  modelled_usd: number | null;
  /** The transcript index of the player post this turn was answering, or null
   *  when it answered none (an absorb, a summary, an opener). */
  post: number | null;
  duration_ms: number;
};
/** One player post's spend: every call made answering it, the first reply and
 *  each reroll of it. Keyed by transcript index.
 *
 *  `rerolls` counts the calls that RE-answered the post, which is not
 *  `calls - 1`: a turn continued past a dice roll is two calls and one answer. */
export type UsagePostBucket = UsageBucket & { post: number; rerolls: number };
/** One scene's all-time spend as the campaign list sees it, with the scene
 *  named from its own file. `missing` marks a bucket whose scene has been
 *  deleted — its spend is still in the list, because it is still in the total. */
export type SceneCostRow = UsageBucket & {
  scene: string; title: string; created: string; updated: string;
  first_ts: string; last_ts: string; missing: boolean;
};
/** What a campaign has cost, scene by scene, over the ledger's whole history.
 *  `since`/`until` is the window that could actually be scanned — a library
 *  whose oldest month file was deleted by hand cannot reach past what is left. */
export type CampaignSceneCosts = {
  campaign: string; since: string; until: string; generated_at: string;
  /** The order the server applied before capping the list — echoed back, so a
   *  view can tell an answer to the sort it asked for from a stale one. */
  order: string;
  totals: UsageBucket; scenes: SceneCostRow[];
  listed: number; truncated: boolean;
};
/** One model's per-token rates (#158), in dollars per 1,000 tokens. The cache
 *  pair is optional, and its absence is not zero: cache counts are slices of
 *  the prompt, so a table naming no cache rate has already priced them at the
 *  prompt rate. */
export type PricingEntry = {
  prompt_usd_per_1k?: number; completion_usd_per_1k?: number;
  cache_read_usd_per_1k?: number; cache_write_usd_per_1k?: number;
};
export type PricingTable = {
  rates: Record<string, PricingEntry>;
  /** The file is there and could not be parsed. Carried as a 200 flag rather
   *  than an error status because the two mean opposite things to an editor:
   *  no rates is a form to fill in, unreadable is a form that must not be
   *  offered — saving it would replace the real file with nothing. */
  unreadable?: boolean;
  detail?: string;
  fields: string[]; default_key: string; max_entries: number;
};
/** What one scene's turns cost. `since`/`until` is the window actually scanned
 *  — the scene's own lifetime, clamped by the server — and `truncated` says the
 *  `turns` list was cut, which never moves `totals`. */
export type SceneUsage = {
  campaign: string; scene: string; since: string; until: string; generated_at: string;
  /** The scan could not reach back to the scene's start — a scene played over
   *  more than a year. Every figure is a floor, and `by_post` is missing
   *  buckets entirely for the older posts, which in a transcript is
   *  indistinguishable from a post that cost nothing. */
  clamped: boolean;
  totals: UsageBucket; by_task: UsageBreakdown[]; by_post: UsagePostBucket[];
  turns: UsageTurn[]; listed: number; truncated: boolean;
};
/** Where a campaign stands against its budget (#153). `level: "off"` is a
 *  campaign that has set none, and carries no spend fields at all — the server
 *  does not scan for a number nobody asked for, so reading `spent_usd` as 0
 *  there would be reading a figure that was never measured. */
export type CampaignBudget = {
  limit_usd: number; period: "monthly" | "total";
  level: "off" | "ok" | "warn" | "over"; warn_fraction: number;
  since?: string; until?: string;
  spent_usd?: number; estimated_usd?: number;
  unpriced_calls?: number; calls?: number; fraction?: number;
};
export type SceneContext = {
  model: string; total_tokens: number; dropped_tokens: number;
  budget_tokens: number; sections: ContextSection[];
};
/** One user pin or exclude (#129) as the panel sees it: the rule, the target it
 *  names resolved to something displayable, and how many posts it has left.
 *  `remaining` is null for a standing rule; `missing` marks a rule whose target
 *  the campaign no longer has — inert, but shown rather than hidden so it can
 *  be cleared. */
export type PinRule = {
  ref: string; kind: string; id: string; name: string; missing: boolean;
  mode: "pin" | "exclude"; scope: "scene" | "campaign"; sid: string;
  ttl_posts: number; remaining: number | null; created: string;
};
/** One past turn's frozen prompt, as listed. The section text is not here —
 *  it lives in the entry itself, which is large enough that shipping every
 *  one of them would defeat the point of a list. */
export type PromptEntry = {
  id: string; scene: string; ts: string; model: string;
  task: "chat" | "director" | "retry" | "regenerate" | "continuation" | "opener"
      | "replay";
  total_tokens: number; dropped_tokens: number; budget_tokens: number;
};
/** A frozen breakdown: the same shape `getSceneContext` returns, plus which
 *  turn it was. Rendered by the same component, pointed at stored text. */
export type PromptSnapshot = SceneContext & Omit<PromptEntry, "scene">;
/** One line of a prompt-section diff (#130). The record-diff vocabulary plus
 *  `skip`: a run of unchanged lines too far from any change to be worth
 *  printing, collapsed to its `count`. A prompt section is the whole
 *  transcript, so without it one appended exchange ships several hundred rows
 *  to say the rest stood still. `text` is present and empty on a `skip`, so a
 *  reader written against `DiffLine` meets an op it does not know rather than a
 *  row with no content field at all. */
export type ContextDiffLine = {
  op: "equal" | "insert" | "delete" | "skip"; text: string; count?: number;
};
/** What one side of a comparison says about one section — everything except
 *  the text, which the diff lines carry. */
export type PromptDiffFacts = {
  label: string; tokens: number; dropped: boolean; trimmed: number; pinned: boolean;
  /** The packing tier the section sat in — its priority, since the packer drops
   *  from the bottom of a tier. Compared, because a release that re-tiers a
   *  catalog section changes what gets cut first while every other fact can
   *  stay identical. */
  tier: string;
};
/** One section, compared. `base`/`head` is null on the side that does not have
 *  it. `diff` is empty on an `unchanged` row, and ALSO on a `changed` one whose
 *  words are identical — a section the packer dropped this turn and kept last
 *  turn is a change with nothing to show line by line. */
export type PromptDiffSection = {
  id: string; label: string;
  status: "added" | "removed" | "changed" | "unchanged";
  /** The section sits at a different point in the prompt than it did — the
   *  layout editor (#29) moved it. Beside `status` rather than inside it,
   *  because a drag and a rewrite are different things and one section can do
   *  both; a pure move is `unchanged` and `moved`. */
  moved: boolean;
  base: PromptDiffFacts | null; head: PromptDiffFacts | null;
  diff: ContextDiffLine[];
};
/** Which turn each end of the comparison was, and what it totalled. `id` is
 *  `"live"` for the composition as it stands now — its `task` is `"live"` too
 *  and it has no timestamp, being a preview rather than a turn that happened. */
export type PromptDiffSide = {
  id: string; task: string; ts: string; model: string;
  total_tokens: number; dropped_tokens: number; budget_tokens: number;
};
/** `base` -> `head`, section by section (#130). No summary count and no token
 *  delta: both are derived from what is already here, and the server declines
 *  to ship a figure that could disagree with the rows beside it. */
export type PromptDiff = {
  base: PromptDiffSide; head: PromptDiffSide; sections: PromptDiffSection[];
};
/** The live running summary of a scene still being played (#85).
 *  `at` is how many posts it covers, `total` how many there are; `stale` means
 *  the posts it covered have since been rerolled, edited or trimmed, so it
 *  describes a transcript that no longer exists. `due` is what a POST without
 *  `force` would decide — the gate lives on the server, so the client never
 *  has to know what `every` means. */
export type RollingSummary = {
  summary: string; at: number; total: number;
  stale: boolean; every: number; due: boolean;
};
/** `refreshed` is false whenever the call spent nothing: not due, an empty
 *  scene, or a provider that answered with no text. */
export type RollingSummaryRefresh = RollingSummary & { refreshed: boolean };
/** One deterministic reason the scene-break detector is asking (#84). `detail`
 *  is the only field with a reader — `kind` and `weight` are the scorer's
 *  bookkeeping, carried so a future panel can group or rank without a second
 *  round trip. */
export type SceneBreakSignal = { kind: string; weight: number; detail: string };
/** The scene-break detector's state for one scene (#84).
 *
 *  `verdict` is a tri-state: "" is "nothing has been asked, or the last answer
 *  was dismissed", which the panel says differently from "asked, and the model
 *  said no". `posts`/`score`/`signals` are the heuristic's side — what has
 *  happened since the last question and whether it adds up — and `due` is what
 *  a POST without `force` would decide, so the client never has to know what
 *  `every` means. */
export type SceneBreak = {
  verdict: "" | "yes" | "no";
  reason: string; title: string;
  /** The answer describes posts that have since been rerolled, edited or cut,
   *  so it reasoned about a transcript that no longer exists. The prose is
   *  still shown — it is the best thing anyone has — it just stops claiming to
   *  be about the scene on screen. A scene with no answer is never stale. */
  stale: boolean;
  posts: number; score: number; signals: SceneBreakSignal[];
  every: number; due: boolean;
};
/** `asked` is false whenever the call spent nothing: the heuristic declined, a
 *  forced call found nothing new, or the transcript moved under the answer. */
export type SceneBreakAnswer = SceneBreak & { asked: boolean };
/** Where a cast member's text came from (#99). `library` is the world's record
 *  as this campaign locked it; `override` is that record with campaign edits on
 *  top; `emergent` is a character the campaign owns outright, with no library
 *  record behind it. Derived per read from hashes the lock already records —
 *  see `store/appearances/versions.py:actor_source`. */
export type CastSource = "library" | "override" | "emergent";
export type CastDetail = {
  kind: "characters" | "pcs"; id: string; name: string; version: string; body: string;
  source: CastSource;
};
/** One feeling this actor holds toward another in the room. The three axes run
 *  0–5 and the column draws them as five pips each. */
export type Feeling = {
  ref: string; kind: string; id: string; name: string;
  trust: number; affection: number; tension: number; note: string;
};
/** Everything the campaign has decided about one actor — the play view's
 *  dossier column. Every field is a record the absorb pass already writes;
 *  `standing` / `knows` / `suspects` and `feels_toward` had no reader outside a
 *  staged review row until this existed. */
export type Casefile = {
  // The casefile route only ever answers for an actor, and its portrait URL
  // keys on this, so it names the two actor kinds rather than any string.
  kind: "characters" | "pcs"; id: string; name: string; version: string; role: string;
  /** The scenes she is cast in, oldest first, labelled — a scene id is a
   *  filename, and the column puts these in a sentence. */
  scenes: { id: string; title: string }[];
  /** The title of the newest of them. */
  last_seen: string;
  standing: string; knows: string; suspects: string;
  dossier: string;
  /** The one-line identity, meaningful only for someone never played. */
  tagline: string;
  feels_toward: Feeling[];
  standing_facts: StandingFact[];
};
export type TimelineEvent = { date: string; text: string };
/** Why one stored field is what it is: the excerpt the extractor cited, who it
 *  attributed it to, its own 0–1 rating (`null` when it gave none) and the band
 *  `absorb/routing.py` weighed those into.
 *
 *  `band` is stored rather than derived here on purpose — it is certainty
 *  weighted by authority, and a second copy of that table on the client is how
 *  the panel and the review end up disagreeing about the same row. */
export type Citation = {
  quote: string; speaker: string; certainty: number | null;
  authority: string; band: string;
  /** The recording scene's id, and its title resolved at read time — a title
   *  frozen into the stored citation would name a scene a later rename
   *  retired. */
  scene: string; scene_title?: string;
  recorded: string;
};
/** Keyed `"<kind>/<id>#<field>"`. A field with no entry is the normal case: the
 *  later absorb phases rest on no transcript citation, and anything written
 *  before the store existed — or edited by hand — has none. */
export type Provenance = Record<string, Citation>;
/** How much weight one staged proposal has earned (#110/#112), computed by
 *  `store/absorb/routing.py`. Display and default-checkbox state only — the
 *  server never reads it back on save, and a `low` row a reviewer ticks anyway
 *  applies exactly like any other. */
export type EditReview = {
  /** The extractor's own 0-1 rating, or null when it gave none. Poorly
   *  calibrated by construction, so it is shown and ordered by, never trusted
   *  as a probability. */
  certainty: number | null;
  /** The excerpt it cited, and the transcript label it attributed them to.
   *  Both "" when it cited nothing. */
  quote: string; speaker: string;
  /** What the transcript actually corroborates about that speaker, relative to
   *  the record being changed. `unattributed` means the citation cannot be
   *  checked — nobody spoke under that name, or two speakers answer to it. */
  authority: "narration" | "self" | "other" | "unattributed" | "uncited";
  /** `certainty` weighted by `authority`, and the band the panel routes on. */
  score: number; band: "high" | "medium" | "low";
};
export type StagedEdit = {
  id: string; kind: "character_state" | "lore" | "authored" | "relationship" | "bond" | "plot"
    | "commitment" | "fact" | "new_character" | "new_location" | "new_lore" | "sheet"
    | "dossier" | "voice_drift";
  target: { kind: string; id: string }; label: string; field: string;
  before: string; after: string; authored: boolean;
  payload?: Record<string, unknown>;
  /** Present on the rows `absorb.materialize` staged from the extraction, and
   *  absent on the ones staged by the later phases (dossier, voice, sheet),
   *  which rest on no transcript citation to weigh. An absent block routes as
   *  `medium`: shown and pre-approved, exactly as every row was before #110. */
  review?: EditReview;
  /** Set once the reviewer has answered a conflict on this row (#111): the
   *  reviewer's authorization to write over a target that moved since the
   *  scene was absorbed. Both values authorize; they differ in whether `after`
   *  is still the staged text or one the reviewer merged by hand. Absent means
   *  unanswered, and the save is refused. */
  resolve?: "replace" | "merge";
  /** The stored value the reviewer was shown when they answered. Sent with
   *  `resolve` so the server can hold the retry to it: the flag authorizes
   *  overwriting *that* text, not whatever the record holds by the time the
   *  save lands. */
  resolve_from?: string;
};
/** A staged edit whose target no longer matches the value it was staged
 *  against (#111). Carries everything the three choices need — what is stored
 *  now, and a merged draft where merging into the field makes sense — so
 *  answering one costs no extra round-trip. */
export type EditConflict = {
  id: string; label: string; kind: string; field: string;
  before: string; after: string; stored: string; reason: string;
  mergeable: boolean; merged: string;
  /** Position in the submitted `edits` array. The only reliable way back to
   *  the row: `id` is not unique (only plot threads are deduped), and the
   *  response omits the rows that were fine, so ordinal position among the
   *  conflicts does not line up with ordinal position among the edits. */
  index: number;
};
export type MechanicsDrop = { id: string; field?: string; reason: string };
/** The two facts a bare status cannot carry, on every phase that makes an LLM
 *  call: whether a request reached the model at all, and whether the absorb's
 *  shared time budget is why it did not. A phase stopped by the clock is worth
 *  retrying as-is; one that failed on its own merits is not. */
export type PhaseAttempt = { attempted: boolean; budget_exhausted: boolean };
export type Mechanics = PhaseAttempt & {
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
  warnings: string[]; dropped: MechanicsDrop[];
};
export type DossierFailure = { id: string; reason: string };
export type Dossiers = PhaseAttempt & {
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
  proposed: string[]; failed: DossierFailure[];
  /** NPCs the absorb budget ran out before reaching — never attempted (#243). */
  skipped: string[];
};
export type VoiceCheck = PhaseAttempt & {
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
  /** NPCs whose dialogue was judged against their anchor — only ones that HAVE
   *  an anchor are judged at all, which is what keeps the extra calls opt-in. */
  checked: string[];
  /** The subset of `checked` that came back out of voice (#59). */
  flagged: string[];
  /** The subset of `checked` that said too little to judge. Named separately
   *  because `checked` minus `flagged` would otherwise read as "confirmed in
   *  voice" for a character nobody actually heard — and silence never clears a
   *  standing corrective. */
  unjudged: string[];
  failed: DossierFailure[];
  /** Anchored NPCs the absorb budget ran out before reaching — never attempted. */
  skipped: string[];
};
/** One row per LLM-backed step of a single absorb, in run order. A projection of
 *  `mechanics`/`dossiers`/`voice` (never a second source of truth) that also covers
 *  the extraction, so a run cut short by the time budget is legible as one instead
 *  of looking like a model with nothing to suggest. */
export type AbsorbPhase = PhaseAttempt & {
  name: "extraction" | "dossiers" | "voice" | "audit";
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
};
export type SceneAbsorb = {
  one_line: string; summary: string; keywords: string[];
  timeline_events: TimelineEvent[]; cast: string[]; location: string; date: string;
  edits: StagedEdit[];
  mechanics: Mechanics;
  /** Idempotency key for this review's save (#235) — replaying a spent one
   *  returns the first result instead of committing twice. */
  commit_token: string;
  dossiers: Dossiers;
  voice: VoiceCheck;
  phases: AbsorbPhase[];
  /** Empty for the ordinary end-of-scene absorb, which has no later scene to
   *  disagree with. Non-empty after a retcon of an older scene, which is the
   *  case it exists for. */
  contradictions: Contradiction[];
};
/** One staged row a scene played AFTER this one already answered differently
 *  (#78). Advisory: it names the later scene and how that scene's authorship was
 *  established, and nothing about the save changes because of it — the reviewer
 *  reads the badge and decides. `source` is `citation` (the quote behind the
 *  later write), `changes` (that scene's write-back to this record) or `thread`
 *  (a plot or commitment thread whose last beat is that scene's). */
export type Contradiction = {
  id: string; scene: string; label: string; source: "citation" | "changes" | "thread";
};
/** What a retcon did beyond rewriting the post (#78): the cascade's reversal
 *  report, plus the scenes played after this one — the ones a re-extraction can
 *  contradict, and the reason to re-absorb the scene and read the badges. */
export type RetconReport = CascadeReport & { later: string[] };
/** A live retcon replay (#79). `next` is what the walk owes: a model turn to
 *  generate, the player's own posts to re-post first, or nothing. `gone` means
 *  the scene was deleted under the session — its backlog is the only copy of
 *  those posts, so it is reported rather than silently discarded. */
export type ReplaySession = {
  scene: string; cut: number; done: number; steps: number; turns_left: number;
  next: "generation" | "verbatim" | "done"; staged: boolean; created: string;
  gone: boolean;
  /** A replayed reply is in the transcript, waiting on accept or another try.
   *  The server's answer rather than the client's memory of having run a turn —
   *  a reload loses that memory, and running the turn again would land a second
   *  reply beside the first. */
  pending: boolean;
};
/** What a replay from this post would cost, before anything is cut (#79/#80).
 *  `fork` is the nudge: over the configured threshold, offer to copy the
 *  campaign first. `blocked`, when non-empty, is why this span cannot be
 *  replayed at all. */
export type ReplayPreview = {
  posts: number; turns: number; threshold: number; fork: boolean; blocked: string;
};
export type SceneSuggestion = {
  title: string; premise: string; date?: string;
  cast: { kind: string; id: string; name: string }[];
  location: { id: string; name: string } | null;
};
/** A row of the scene ledger (#88) — a saved idea, or a greeting composed into
 *  the same shape. Deliberately a superset of `SceneSuggestion`, so one card
 *  renderer covers both: `cast` and `location` come back resolved.
 *
 *  `source` is `"greeting"` for the composed entries, whose ids are
 *  `greeting:<gid>` and whose status writes delegate to the greeting's own
 *  marks server-side. */
export type SceneIdea = {
  id: string; title: string; premise: string; date: string;
  cast: { kind: string; id: string; name: string }[];
  location: { id: string; name: string } | null;
  pcless: boolean;
  source: "llm" | "user" | "greeting";
  status: "active" | "used" | "dismissed";
  created: string; used_scene: string;
};
export type SceneIdeaDraft = {
  title?: string; premise?: string; cast?: string[]; location?: string;
  date?: string; pcless?: boolean; source?: "llm" | "user";
};
export type SceneIntentResult = {
  title: string; date: string;
  location: { id: string; name: string } | null;
  cast: { kind: string; id: string; name: string }[];
};
export type ChronicleEntry = {
  id: string; one_line: string; summary: string; keywords: string[];
  cast: string[]; location: string; date: string; absorbed: string;
};
/** What a cascade post-delete actually did (#75). Counts rather than a bare
 *  ack, because the reversal reaches records the transcript does not show.
 *
 *  Two different kinds of incomplete, and they must not be conflated:
 *  `refused` names records that could not be put back (something wrote to them
 *  after this scene did, or the kind carries no reversal), which keep the value
 *  the deleted scene gave them; `failed` names cleanup STEPS that could not run
 *  at all — a garbled `plot.json` and the like. The cut itself always happened
 *  by the time either is non-empty, which is why they are reported rather than
 *  raised. A count of zero beside a name in `failed` means "not known", not
 *  "none". */
export type CascadeReport = {
  index: number; removed: number; was_absorbed: boolean;
  records: number; refused: { label: string; reason: string }[];
  chronicle: boolean; plot_beats: number; commitment_beats: number;
  changes: number; citations: number; failed: string[];
};
export type DiffLine = { op: "equal" | "insert" | "delete"; text: string };
export type FieldDiff = { field: string; label: string; diff: DiffLine[] };
export type RecordChange = {
  ref: { kind: string; id: string }; name: string;
  scene: { id: string; title: string; date: string };
  fields: FieldDiff[];
};

/** One row of the append-only change journal (#31). `RecordChange` is the
 *  rolling view — the latest delta per record — and this is the history behind
 *  it, newest first, with the reversal the server is willing to perform.
 *
 *  `undoable` is the SERVER's answer and is never re-derived here: whether a
 *  change can be put back depends on what it wrote and whether the record has
 *  moved since, and a second copy of that rule in the client would be the kind
 *  of drift that ends with a button offering what the store refuses. `why`
 *  carries the reason when it is false. */
export type JournalEntry = {
  id: string; ts: string; source: string; kind: string;
  ref: { kind: string; id: string }; name: string; label: string; field: string;
  scene: { id: string; title: string; date: string };
  diff: DiffLine[];
  undoable: boolean; why: string;
  undone: { ts: string; by: string } | null;
};

// continuity ledger (#117). `kind` is promise | threat | foreshadowing and
// `status` open | fulfilled | broken | expired — a commitment resolves, where a
// plot thread only advances. Contradictions are the fourth section this view is
// named for and arrive with #111.
export type LedgerScene = { id: string; title: string; date: string };
export type PlotThread = {
  id: string; title: string; status: string;
  last_scene: string; latest_beat: string; scene: LedgerScene;
  /** Present on ledger and digest rows (#103); the digest's own type restates
   *  it as required, since every row there is aged. */
  aging?: Aging;
};
export type Commitment = PlotThread & { kind: string; due: string };
/** A standing fact on the ledger (#114). `scene` is the scene that RECORDED it,
 *  not one that last moved it: a fact's text never changes once written, and a
 *  fact that stopped being true is retired off this list rather than rewritten. */
export type StandingFact = {
  id: string; text: string; date: string; scene: LedgerScene;
};
/** A fact that stopped being true (#114), and the half of facts.json that never
 *  left the server until the ledger got its own screen (4e).
 *
 *  `scene` is still the scene that RECORDED it — a retired fact keeps its dated
 *  place in the ledger — and `retired_scene` is the one that ENDED it.
 *  `superseded_by` names the fact that replaced this one and is "" when nothing
 *  did, which is the whole difference between a truth another truth overtook
 *  and one that simply lapsed. It is a bare id pointing into `facts` or back
 *  into `retired` of the same response: the replacement's text is on its own
 *  row, and shipping it twice would let the two copies disagree. */
export type RetiredFact = StandingFact & {
  superseded_by: string; retired_scene: LedgerScene;
};
/** One line of relationships.json. Two shapes share it because the reader's
 *  question is what stands between two people: `kind: "feeling"` is directed
 *  (a→b, metered 0–5, not reciprocated by construction) and `kind: "bond"` is
 *  symmetric, `type`d ("kin", "sworn") and dated to `scene`, which is the empty
 *  label for every feeling. */
export type LedgerRelationship = {
  id: string; kind: string; a: string; b: string; a_name: string; b_name: string;
  trust: number; affection: number; tension: number;
  note: string; type: string; since_scene: string; scene: LedgerScene;
};
export type LedgerFact = { id: string; one_line: string; date: string; title: string };
export type Ledger = {
  plot: PlotThread[]; commitments: Commitment[]; facts: StandingFact[];
  retired: RetiredFact[]; relationships: LedgerRelationship[];
  chronicle: LedgerFact[];
  /** This campaign's staleness threshold, beside the rows rather than on each
   *  of them: a panel saying "40 days untouched" needs to be able to say what
   *  this campaign calls too long. */
  stale_after_days: number;
};

// keyword search (#33)
/** One record the query matched.
 *
 *  `scope` and `root` together say *which* record: a world's lore and a
 *  campaign's fork of it carry the same `id`, and only the scope tells them
 *  apart. `sub` is what inside the record matched where a record has parts — a
 *  card version, a persona version, a relationship's side — and "" where it
 *  does not. */
export type SearchHit = {
  scope: "world" | "campaign";
  root: string;
  root_name: string;
  kind: string;
  id: string;
  sub: string;
  name: string;
  /** A one-line window of the body around the first matching term, ellipsed at
   *  either end. Plain text, not markdown: the emphasis runs are stripped. */
  snippet: string;
  score: number;
};
export type SearchMode = "keyword" | "semantic";

export type SearchResult = {
  q: string;
  /** The query as the server split it — phrases kept whole — so the client
   *  highlights exactly what matched rather than re-implementing the split.
   *  Empty in semantic mode: nothing matched a term, so nothing is marked. */
  terms: string[];
  /** Hits after the kind filter; `hits` is this list cut to the limit. */
  total: number;
  /** Hits per kind BEFORE the kind filter, so a chip can say what dropping the
   *  current filter would find. */
  facets: Record<string, number>;
  scopes: Record<string, number>;
  truncated: boolean;
  hits: SearchHit[];
  /** The ranking that actually produced this page, which is not always the one
   *  that was asked for: semantic mode needs an embeddings connection, and
   *  falls back to keyword when it has none rather than erroring (#34). */
  mode?: SearchMode;
  requested_mode?: SearchMode;
  /** Why the two differ, written to be shown to the reader. "" when they do
   *  not. */
  note?: string;
  /** Semantic mode only: passages of the corpus that had a vector to score
   *  against, out of how many there are. A query warms a bounded number of
   *  them, so a large library indexes over several searches rather than
   *  stalling the first one. */
  indexed?: number;
  corpus?: number;
};

// the play timeline (#198) — the ledger's other half. The ledger answers what
// is still open; this answers what happened, in play order, one card per scene.
//
// `one_line`, `location` and `done` exist only after the absorb, and a campaign
// being played is normally a scene or two ahead of it — so the ORDINARY card
// carries none of them and falls back to its title and its own date. Treat them
// as optional content, never as "still loading".
//
// The absorb's full `summary` is deliberately absent: a card is one line, and
// shipping the whole campaign's prose for a view that renders none of it is the
// biggest thing on the wire paying for nothing. `one_line` already falls back
// to it server-side for the save that left `one_line` empty.
/** One beat of a plot thread, on the card of the scene it landed in — the
 *  "thread pair" the timeline is for: what moved, and where. `title`/`status`
 *  are the THREAD's, repeated per beat so a card needs no second lookup. */
export type TimelineBeat = {
  thread: string; title: string; status: string; text: string;
};
export type TimelineScene = {
  id: string; title: string; one_line: string;
  /** The scene's own opening moment, falling back to the chronicle's date. */
  date: string;
  location: string; done: boolean; pcless: boolean; beats: TimelineBeat[];
};
/** Only the threads with a beat on some card: a chip that filters to nothing
 *  is worse than no chip. */
export type TimelineThread = { id: string; title: string; status: string };
export type Timeline = { scenes: TimelineScene[]; threads: TimelineThread[] };

// pre-scene briefing (#118) — the ledger's per-scene sibling. The rows are the
// ledger's, minus the `scene` label (this view is about who, not when) and plus
// `involves`: the display names of the scene's cast this row can be traced to,
// empty for a row it cannot. `focus` names who the flag was computed against —
// the scene's players, or its whole cast when it is an offscreen scene with
// none. Rows are ordered flagged-first and never filtered: an unflagged
// commitment is still owed.
export type BriefingRow = {
  id: string; title: string; status: string;
  last_scene: string; latest_beat: string; involves: string[];
};
export type BriefingCommitment = BriefingRow & { kind: string; due: string };
export type BriefingFact = { id: string; one_line: string; title: string; date: string };
export type Briefing = {
  focus: string[]; plot: BriefingRow[]; commitments: BriefingCommitment[];
  relationships: string[]; last_time: BriefingFact | null;
};

// scene import (#92) — a grimoire transcript read back in. The draft is a
// proposal: parsing writes nothing, and every field here is one the review form
// can change before it is committed. `cast` is what the speaker labels resolved
// to in this campaign, `unmatched` the labels that resolved to nobody, and
// `warnings` everything the file could not settle on its own (a header bit that
// is either a date or a location, a date this campaign's calendar cannot read,
// text the marker grammar will not carry).
export type SceneImportCast = {
  label: string; kind: "characters" | "pcs"; id: string; name: string; role: "player" | "npc";
};
export type SceneImportDraft = {
  title: string; date: string; location: string; pcless: boolean;
  messages: Message[];
  /** The source's reply boundaries, when it had some that still fit. Nothing
   *  for the reviewer to decide — it rides the draft back to the commit so an
   *  imported scene rerolls one generation rather than its whole trailing run. */
  turn_sizes: number[] | null;
  cast: SceneImportCast[];
  unmatched: string[];
  warnings: string[];
};

// lorebook import
// `EntityKindName`, not `EntityKind`: a draft's category is whatever the server
// said a row may be filed under (`GET /api/entity-kinds`), which is allowed to
// name a kind added after this build shipped (#138). Narrowing it to the local
// union would only be a cast that claims something the round trip does not.
export type LoreEntryDraft = { name: string; keys: string[]; body: string; category: EntityKindName };

// scenario-card import (#217) — one card describing a whole setting, split into
// the records a world is made of. A proposal speaks in cast NAMES, not ids: the
// characters it proposes do not exist while it is being reviewed, and the
// backend resolves the names once they do.
export type ScenarioCharacterDraft = {
  name: string; description: string; personality: string;
  /** The import will reuse a world character of this name rather than create
   *  one. Advisory: the backend re-resolves at import time. */
  exists?: boolean;
};
export type ScenarioGreetingDraft = {
  name: string; body: string; character: string; present: string[];
};
export type ScenarioProposal = {
  characters: ScenarioCharacterDraft[];
  entries: LoreEntryDraft[];
  greetings: ScenarioGreetingDraft[];
};
export type ScenarioArtSummary = {
  total: number; localized: number; skipped: number; failed: number; capped: boolean;
};
export type ScenarioImportResult = {
  characters: { name: string; id: string; version: string; created: boolean }[];
  entries: { kind: string; id: string }[];
  greetings: { name: string; id: string }[];
  art: ScenarioArtSummary;
};

// dice rolls
export type DieDetail = { value: number; rolls: number[]; kept: boolean };
export type RollResult = {
  notation: string; seed: number; dice: DieDetail[]; modifier: number;
  pool_target: number | null; vs: number | null;
  total: number | null; successes: number | null; outcome: string | null;
};
export type RollEntry = {
  id: string; ts: string; scene: string | null; label: string | null; result: RollResult;
};
export type ProposalRecord = { id: string; status: string; payload: RollProposalPayload; resolution: CheckResolution | null };
export type CheckResolution = { check: string; check_label: string; actor: string; actor_label: string; notation: string; tier: string | null; difficulty: number | null; modifier: number; roll_id?: string };
export type SceneCheckActor = { ref: string; label: string; sheet_type: string; checks: [string, string][] };

// campaign group state (#47)
export type GroupState = {
  goals: string; resources: string; focus: string;
  public_perception: string; secrets: string; updated?: string;
};

// modules
export type LayoutNode = {
  row?: LayoutNode[]; column?: LayoutNode[]; group?: string;
  fields?: string[]; derived?: string[]; title?: string; grid?: boolean;
};
export type ModuleTheme = {
  colors?: Partial<Record<"bg" | "ink" | "muted" | "accent" | "rule", string>>;
  fonts?: Partial<Record<"display" | "body", string>>;
  dots?: string; corners?: string;
};
export type DisplayError = {
  source: "layout" | "theme";
  // "*" = file-level failure that dropped every layout
  sheet_type: string | null;
  message: string;
};

export type ModuleSummary = {
  id: string; name: string; description: string;
  version: string; source: "builtin" | "user"; valid: boolean;
  display_ok?: boolean;
};
export type ModuleField = {
  key: string; label?: string; type: string;
  max?: number; min?: number; default?: number;
  ref_kind?: string;
};
export type ModuleSheetType = {
  label: string; kind: string; groups: string[];
  fields: ModuleField[]; derived?: Record<string, string>;
  creation?: { pools: Record<string, { budget: number | string; costs: Record<string, number> }> };
  advancement?: { pool: string; costs: Record<string, string> };
};
export type ModuleEditResult = {
  ok: boolean; errors: string[]; display_errors: DisplayError[];
  impact?: { sheet_types: string[]; sheets_migrated: number;
             sheets_newly_invalid: number; dangling_refs: number };
  sample?: Record<string, { fields: Record<string, unknown>;
                            derived: Record<string, number | boolean> }>;
  migration?: { migrated: number; skipped: string[] };
};
export type ModuleRenameKind =
  "group" | "field" | "derived" | "sheet_type" | "check" | "rule" | "content";

export type ModuleDetail = {
  id: string;
  source: "builtin" | "user";
  manifest: { id: string; name: string; description?: string; version?: string; dice?: string; notes?: string };
  sheets: { groups: Record<string, { label?: string; fields: ModuleField[]; derived?: Record<string, string> }>;
            sheet_types: Record<string, ModuleSheetType> };
  checks: Record<string, { label?: string; roll?: string; requires?: string[]; rules?: string[];
                           difficulty?: number; outcomes?: { label: string; when: string }[] }>;
  rules: { id: string; keys: string[]; always: boolean; on_roll: boolean; sheet_types: string[] }[];
  content: { kind: string; id: string; name: string; sheet_type: string | null }[];
  errors: string[];
  layout?: { sheet_types: Record<string, LayoutNode> };
  layout_source?: Record<string, unknown>;
  theme?: ModuleTheme;
  display_errors?: DisplayError[];
};
export type ModuleContentEntry = {
  kind: string; id: string; name: string; body: string; keys: string;
  sheet_type: string | null; fields: Record<string, unknown>;
};
export type CampaignModule = {
  setting: string; resolved: string | null; source: "campaign" | "world" | null;
};

// sheets (Phase 3 mechanics)
export type Sheet = {
  sheet_type: string | null;
  fields: Record<string, unknown>;
  derived: Record<string, number | boolean>;
  errors: string[];
  gen: string | null;
};
export type SheetExpected = { sheet_type: string | null; fields: Record<string, unknown>; gen: string | null } | null;
export type SheetCoverage = Record<string, { total: number; sheeted: number; invalid: number }>;

/** One cast member on the sheets roster: `coverage` counts these, this names
 *  them. `sheet_type`/`errors`/`creation_pending` describe the stored sheet and
 *  are the empty answers when `sheeted` is false. */
export type SheetRosterRow = {
  id: string;
  name: string;
  sheeted: boolean;
  sheet_type: string | null;
  errors: string[];
  /** The module's creation pools this sheet has never been through — non-empty
   *  only while its values are still exactly the schema defaults, which is the
   *  state a bulk create leaves them in. Empty for a sheet anyone has worked
   *  on, and for a type with no creation step. */
  creation_pending: string[];
};

export type SheetRoster = Record<string, SheetRosterRow[]>;

/** What one bulk create did. Every cast member it looked at is in `created`,
 *  `failed`, or was already sheeted; every kind it could not choose a type for
 *  is in `skipped` with the reason. */
export type SheetBulkResult = {
  created: { kind: string; id: string; name: string; sheet_type: string;
             creation_pending: string[] }[];
  skipped: { kind: string; reason: string }[];
  failed: { kind: string; id: string; detail: string }[];
};

// ---- observability: performance, errors, the structured log (#154/#155/#156) ----
/** The five severities `store.logs` writes, quietest first. A floor everywhere
 *  it is used as a filter: `warning` means warnings and worse. */
export type LogLevel = "debug" | "info" | "warning" | "error" | "critical";

export type LogRow = {
  ts: string;
  level: LogLevel;
  module: string;
  message: string;
  kind?: string;
  campaign?: string;
  scene?: string;
  task?: string;
  trace?: string;
};

export type LogPage = {
  rows: LogRow[];
  /** Every module present in the WINDOW, not just on this page — so a filter
   *  dropdown built from it does not lose an option when something else gets
   *  chatty. `counts` and `total` are the window's too. */
  modules: string[];
  counts: Record<LogLevel, number>;
  total: number;
  truncated: boolean;
  level: LogLevel;
  since: string;
  until: string;
  levels: LogLevel[];
};

export type LogTailEvent = {
  cursor: string;
  /** Absent on the opening frame, which carries a cursor and no backlog. */
  rows?: LogRow[];
};

export type ErrorKindCount = { kind: string; count: number };
export type ErrorModule = {
  module: string;
  count: number;
  kinds: ErrorKindCount[];
  last: string;
  last_detail: string;
};
export type ErrorSummary = {
  since: string; until: string; days: number;
  total: number;
  modules: ErrorModule[];
  kinds: ErrorKindCount[];
  daily: { day: string; count: number }[];
  rows: LogRow[];
  truncated: boolean;
};

/** One latency distribution: a bucket of calls with its percentiles.
 *
 *  `errors` here counts CALLS THAT FAILED, out of the usage ledger — which is
 *  the only source that also knows how many succeeded, so it is the only one
 *  that can give `error_rate` a denominator. `Stats.errors` is the other
 *  question and the other source; see there. */
export type PerfBucket = {
  key: string;
  calls: number;
  errors: number;
  error_rate: number;
  /** True when the window held more calls than one distribution keeps, so the
   *  percentiles are over a sample. Both tails are preserved. */
  sampled: boolean;
  p50: number; p90: number; p99: number;
  min: number; max: number;
};

export type Stats = {
  days: number; since: string; until: string; campaign: string;
  generated_at: string;
  percentiles: number[];
  totals: PerfBucket;
  by_task: PerfBucket[];
  by_model: PerfBucket[];
  /** Chronological: a trend is read left to right. */
  by_day: PerfBucket[];
  /** Failures RECORDED ANYWHERE, from the error store — including the ones
   *  that were never a call, so this total and `totals.errors` differ on
   *  purpose. */
  errors: ErrorSummary;
};

export type LogLevelInfo = { level: LogLevel; levels: LogLevel[] };
