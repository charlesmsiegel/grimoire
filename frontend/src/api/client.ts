import { parseSSEChunk, type ChatEvent, type LocalizeEvent, type ChubGalleryEvent, type RollProposalPayload } from "./stream";
import type { Model } from "./models";
import { campaignsChanged, configChanged } from "../appEvents";

/** Announce a campaign mutation once it has actually landed, passing the
 *  response through untouched. Sits on the three campaign mutators rather than
 *  in their callers so no view can forget: the persistent sidebar has no other
 *  way to learn that a rename on `/` changed a row it is showing. A rejected
 *  request never reaches here, so a failed delete cannot blank the rail. */
function notifyCampaigns<T>(result: T): T {
  campaignsChanged();
  return result;
}

/** Same shape for the config channel. Hung off the calls that already
 *  invalidate the config cache: invalidating only guarantees the *next* read
 *  is fresh, and the status bar has no reason to read again until something
 *  tells it to -- which is exactly the stale window this closes. */
function notifyConfig<T>(result: T): T {
  configChanged();
  return result;
}

export class ApiError extends Error {
  /** `body` is the whole decoded error payload. Most callers only need
   *  `detail`/`kind`, but a route can attach structured data a retry has to act
   *  on rather than merely display — the chronicle save's conflict rows (#111)
   *  are the first — and flattening the response to two strings would drop it. */
  constructor(public status: number, public detail: string, public kind?: string,
              public body?: Record<string, unknown>) {
    super(detail);
  }
}

/** What to show the user for a failed call.
 *
 *  `catch (err: any)` followed by `err.detail ?? String(err)` is what this
 *  replaces, at every call site that had it. The `any` bought nothing and cost
 *  the compiler's check on everything else the handler touched.
 *
 *  It reads `detail` structurally rather than only off `ApiError`, and that is
 *  the part that has to stay: `request` and `requestForm` do throw `ApiError`,
 *  but a rejection can also arrive as a bare `{detail}` from a stream error
 *  frame or a hand-built rejection, and those used to render their message.
 *  An `instanceof` test alone would quietly turn every one of them into
 *  "[object Object]" on screen.
 *
 *  Two deliberate departures from the `??` it replaces, both because `??` only
 *  guards null and undefined:
 *
 *  - an EMPTY detail falls back instead of rendering as a blank banner. The
 *    old expression showed the empty string, and did so from `ApiError` and a
 *    plain object alike, so a backend that answered `{"detail": ""}` produced
 *    an error box with nothing in it.
 *  - a null or non-object rejection no longer throws. `err.detail` on `null`
 *    is a TypeError raised inside the `catch`, which is the one place a
 *    throw has nowhere left to go. */
export function errorText(err: unknown): string {
  const detail = err instanceof ApiError ? err.detail
    : typeof err === "object" && err !== null
      ? (err as { detail?: unknown }).detail
      : undefined;
  return typeof detail === "string" && detail ? detail : String(err);
}

async function requestRaw<T>(method: string, path: string, body?: unknown,
                             signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind, data);
  }
  return res.json() as Promise<T>;
}

// Identical GETs that overlap share one request: opening a scene fires the
// same cast/appearances/datetime lookups from several components at once.
// The map only holds in-flight promises, so nothing is ever served stale.
const inflightGets = new Map<string, Promise<unknown>>();

// `fresh` opts out of the sharing above, for the rare caller whose whole reason
// for asking is that it needs a read issued *now*. Sharing is normally free
// because the answer cannot be stale — the map only holds in-flight promises —
// but that reasoning assumes the caller does not care when the read happened.
// A read verifying that a write has landed does care: handed a promise started
// before that write, it gets a pre-write answer and concludes the write never
// happened (#95, `CampaignView.settleProposal`).
//
// It is never *served* the shared promise, and it also retires the entry: a
// caller asking for a fresh read is telling us the thing behind this path just
// changed, so any read still in flight predates that change and must not be
// handed to whoever asks next. Dropping the map entry cannot disturb the
// callers already awaiting that promise — they hold it directly, and it still
// resolves for them — it only stops new ones joining. Without this the stale
// read outlives the refresh that was supposed to replace it, and the next
// caller along adopts it.
function retireInflight(path: string): void {
  inflightGets.delete(path);
}

/** Retire *every* pending GET. Only a store move needs this: it repoints the
 *  root, so each in-flight read is describing a library that is no longer the
 *  one being shown -- the Library hub's five section counts as much as the
 *  campaign list. Retiring by path would mean listing every store-scoped
 *  endpoint here and keeping that list current, which is the enumeration this
 *  file has already been bitten by. */
function retireAllInflight(): void {
  inflightGets.clear();
}

// `signal` aborts the request. Only non-GETs take one today, and deliberately
// so: an aborted GET would settle the shared promise below for every caller
// waiting on it, not just the one that asked to stop.
function request<T>(method: string, path: string, body?: unknown,
                    opts?: { fresh?: boolean; signal?: AbortSignal }): Promise<T> {
  if (method !== "GET" || opts?.fresh) {
    if (opts?.fresh) retireInflight(path);
    return requestRaw<T>(method, path, body, opts?.signal);
  }
  const pending = inflightGets.get(path);
  if (pending) return pending as Promise<T>;
  // Only retire the entry if it is still *this* promise. A retired read still
  // settles, and by then a later caller may have installed a replacement for
  // the same path -- an unconditional delete evicts that live entry, so the
  // callers after it each issue their own request instead of joining it. On
  // /api/campaigns that is a full scan of every campaign's scenes per caller.
  let p: Promise<T>;
  p = requestRaw<T>(method, path, body).finally(() => {
    if (inflightGets.get(path) === p) inflightGets.delete(path);
  });
  inflightGets.set(path, p);
  return p;
}

async function requestForm<T>(path: string, form: FormData, method = "POST"): Promise<T> {
  const res = await fetch(path, { method, body: form });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  return res.json() as Promise<T>;
}

export type LLMConnectionKind = "openrouter" | "claude" | "openai_compatible";
export type LLMConnection = {
  id: string; kind: LLMConnectionKind; name: string;
  base_url: string; model: string; post_process: "none" | "strict";
  key_set: boolean; rev: string;
};
export type LLMConnectionDetail = LLMConnection & { models: Model[]; fetched_at: string };
export type LLMConnectionDraft = {
  kind?: LLMConnectionKind; name?: string; base_url?: string; api_key?: string;
  model?: string; post_process?: "none" | "strict";
};
export type ModelsRefreshResult = { models: Model[]; fetched_at: string; rev: string };
export type Config = {
  theme: string; system_prompt: string;
  quote_color: string; user_label: string; assistant_label: string;
  active_connection_id: string;
  active_connection: { id: string; kind: LLMConnectionKind; name: string; model: string } | null;
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
  "context_budget" | "archive_depth" | "setup_done" | "prompt_log_depth" |
  "turnstate_depth" | "promote_streak" | "rolling_summary_every" |
  "offscene_known_limit" |
  "embeddings_connection_id" | "embeddings_model" |
  "semantic_recall_depth" | "semantic_recall_threshold" |
  "prompt_layout_enabled" | "speaker_turn_taking" |
  "backup_enabled" | "backup_interval_hours" | "backup_keep" | "backup_dir">>;
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
  /** Title of the newest scene carrying an absorb mark: how far the chronicle,
   *  the ledger and the dossiers are caught up to. Deliberately not derivable
   *  from `scenes` — playing a scene ahead of the absorb is the normal state
   *  of a campaign in progress. "" when nothing has been absorbed yet. */
  absorbed_through?: string;
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
export type SceneAlternate = {
  id: string; created: string; guidance: string; posts: number; preview: string;
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

// entities (locations | lore)
export type EntityKind = "locations" | "lore" | "items" | "groups" | "creatures";
export type EntityScope = { kind: "world" | "campaign"; id: string };

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
  avatar_focus?: number | null; gallery_count?: number; localized_count?: number;
  greeting_count?: number; tagline?: string; versions: VersionRef[];
};
export type CharacterDetail = {
  meta: { id: string; name: string; default_version: string; birthdate?: string };
  versions: { id: string; name: string; card: Card; images?: string[];
              avatar_focus?: number | null; chub_source?: string; is_chub?: boolean }[];
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
              avatar_focus?: number | null }[];
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
export type ResponseBundle = ResponseFields & { effective: ResponseEffective; provenance: ResponseProvenance };
export type Availability = {
  id: string; name: string; available: boolean; reasons: string[]; unlocked: boolean;
  pcless?: boolean;
  mark?: GreetingMark;
};
export type Appearance = { gid: string; greeting_name: string; name: string; url: string; thumb?: string };

// cast
export type Actor = { kind: "characters" | "pcs"; id: string; role: "player" | "npc"; name: string };
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
export type CalendarScope = { kind: "campaign" | "world"; id: string };

/** Split a native datetime on its trailing Thh:mm only — month tokens may contain T. */
export function splitNativeDate(native: string): { date: string; time: string | null } {
  const m = native.match(/T(\d{1,2}:\d{2})$/);
  return m ? { date: native.slice(0, m.index), time: m[1] } : { date: native, time: null };
}

export type CalendarConfig = { primary: CalendarBlock; secondary: CalendarBlock | null; confirmed: boolean };

// ---- the campaign clock (#100) ----
/** One row of the clock's log: where time went, why, and when that was recorded. */
export type ClockLogEntry = { from: string; to: string; reason: string; at: string };
export type CampaignClock = { now: string; friendly: string; log: ClockLogEntry[] };
/** What an advance crosses. Deterministic, so the preview and the advance that
 *  follows it report the same thing. `truncated` means the span was too long to
 *  itemize — `elapsed_days` is exact either way. */
export type AdvanceDigest = {
  from: string; to: string; from_friendly: string; to_friendly: string;
  elapsed_days: number; backward: boolean; truncated: boolean;
  holidays: { name: string; native: string; friendly: string; in_days: number }[];
  birthdays: { name: string; age: number; native: string; friendly: string }[];
  open_threads: { id: string; title: string; status: string; last_scene: string; latest_beat: string }[];
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
  task: "chat" | "director" | "retry" | "regenerate" | "continuation" | "opener";
  total_tokens: number; dropped_tokens: number; budget_tokens: number;
};
/** A frozen breakdown: the same shape `getSceneContext` returns, plus which
 *  turn it was. Rendered by the same component, pointed at stored text. */
export type PromptSnapshot = SceneContext & Omit<PromptEntry, "scene">;
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
export type CastDetail = { kind: "characters" | "pcs"; id: string; name: string; version: string; body: string };
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

// lorebook import
export type LoreEntryDraft = { name: string; keys: string[]; body: string; category: EntityKind };

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

function entityBase(scope: EntityScope): string {
  return scope.kind === "world" ? `/api/worlds/${scope.id}` : `/api/campaigns/${scope.id}`;
}

// `signal` is how a turn gets cancelled (#95). Aborting closes the connection,
// which the backend sees as a disconnect — it persists whatever the model had
// produced and unwinds — so there is no cancel endpoint to call and nothing to
// clean up on this side beyond letting the rejection out. Callers tell an abort
// from a real failure with `isAbortError`.
async function streamPost<T = ChatEvent>(
  path: string,
  body: unknown,
  onEvent: (e: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  // Tagged so a caller can tell "the server never got this" from "the server
  // got it and then something went wrong" (#95). The line between them is the
  // response: `post_chat` appends the player's post and *then* returns the
  // streaming response, so a response arriving means the post exists, and a
  // failure before one means it may never have been written. A chat that
  // cannot tell the difference has to guess whether the prompt still exists
  // anywhere, and guessing either way loses or duplicates it.
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  }).catch((err) => {
    if (err && typeof err === "object") (err as { beforeResponse?: boolean }).beforeResponse = true;
    throw err;
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = parseSSEChunk<T>(buffer, decoder.decode(value, { stream: true }), onEvent);
  }
}

// The config is fetched at app start and again by campaign/config views; it
// only changes through putConfig / a data-dir move, so cache it until then.
let configCache: Promise<Config> | null = null;

export function invalidateConfigCache() {
  configCache = null;
  // The resolved cache is only half of what is stale. An in-flight GET of the
  // same path is a read issued before whatever prompted this call, and the
  // next getConfig() -- now that the cache is empty -- would join it and store
  // the pre-change connection. Both holders of an old answer go together.
  retireInflight("/api/config");
}

export const api = {
  /** `fresh` forces a new request and re-seeds the cache with it. The cache is
   *  keyed to nothing but this tab's own writes, so a store populated by
   *  another tab, another device, or a sync client is invisible to it — and
   *  `first_run` is a statement about the store, not about this session, so a
   *  caller re-deciding whether to show setup has to actually ask (#194). */
  getConfig: (opts?: { fresh?: boolean }) => {
    if (!configCache || opts?.fresh) {
      configCache = request<Config>("GET", "/api/config", undefined, { fresh: true })
        .catch((err) => {
          configCache = null; // never cache a failure
          throw err;
        });
    }
    return configCache;
  },
  putConfig: (body: ConfigUpdate) =>
    request<Config>("PUT", "/api/config", body).then((cfg) => {
      configCache = Promise.resolve(cfg); // the write's response is the fresh config
      return notifyConfig(cfg);
    }),
  getPromptLayout: () => request<PromptLayout>("GET", "/api/prompt-layout"),
  /** Replaces the stored layout wholesale — `[]` is Reset. A partial list is
   *  not a patch: the server merges anything it omits back beside its catalog
   *  neighbours, so sending a subset would barely reorder anything. */
  putPromptLayout: (sections: { id: string; label: string; enabled: boolean }[]) =>
    request<PromptLayout>("PUT", "/api/prompt-layout", { sections }),
  listBackups: () => request<BackupList>("GET", "/api/backups"),
  /** Back up now. The response is the refreshed listing, so nothing has to
   *  re-read it — and it names what retention removed, which is the half of
   *  the operation nobody sees happen. */
  createBackup: () => request<BackupRun>("POST", "/api/backups"),
  getDataDir: () => request<DataDirInfo>("GET", "/api/config/data-dir"),
  // `fresh`: the whole reason to ask is to see the store as it is *now* --
  // after a move, or after the user has been out to their file manager to
  // resolve one of these. A shared in-flight read predating that is exactly
  // the stale answer this call must not get.
  getStoreConflicts: () =>
    request<StoreConflicts>("GET", "/api/store/conflicts", undefined, { fresh: true }),
  putDataDir: (data_dir: string | null) =>
    request<DataDirInfo>("PUT", "/api/config/data-dir", { data_dir })
      .then((info) => {
        invalidateConfigCache(); // a store move can change everything
        // ...including every read still in flight against the old root. The
        // Library hub's counts are the clearest case: five GETs that would
        // otherwise resolve with the previous store's totals and be adopted.
        retireAllInflight();
        // ...and both things the shell's chrome is showing. A new root has its
        // own campaigns and its own connections, so the sidebar's links point
        // at campaigns that need not exist here and the status bar names a
        // connection from the old store.
        campaignsChanged();
        return notifyConfig(info);
      }),

  // worlds
  listWorlds: () => request<WorldMeta[]>("GET", "/api/worlds"),
  // The config carries `first_run`, which is partly a statement about whether
  // the store holds anything — so creating the first world changes the config
  // response even though nothing wrote to config.md through this client (#194).
  createWorld: (name: string) =>
    request<{ id: string }>("POST", "/api/worlds", { name }).then((r) => {
      invalidateConfigCache();
      return r;
    }),
  renameWorld: (wid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/worlds/${wid}`, { name }),
  deleteWorld: (wid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}`),

  // world bundles (#54). Export is a plain href so the browser streams the zip
  // straight to disk -- a world runs to a gigabyte, which is not something to
  // pull through fetch and hold in a Blob.
  exportWorldUrl: (wid: string) => `/api/worlds/${wid}/export.zip`,
  importWorld: (file: Blob) =>
    fetch("/api/worlds/import", { method: "POST", body: file,
      headers: { "content-type": "application/zip" } }).then(async (r) => {
        if (!r.ok) {
          const data = await r.json().catch(() => ({}));
          throw new ApiError(r.status, data.detail ?? r.statusText, data.kind);
        }
        invalidateConfigCache();   // same as createWorld: this changes `first_run`
        return r.json() as Promise<{ id: string }>;
      }),

  // campaigns
  // `fresh` for the caller refetching *because* a campaign just changed: the
  // in-flight share would hand it a read issued before that mutation, which is
  // precisely the answer it is trying to replace. Navigation refetches leave
  // it off, since sharing is free when the caller does not care when the read
  // started (see `request`, and `campaignLedger` for the same trade).
  listCampaigns: (fresh = false) =>
    request<CampaignMeta[]>("GET", "/api/campaigns", undefined, { fresh }),
  createCampaign: (name: string, world: string, region?: string, calendar?: string, module?: string,
                   climate?: string) =>
    request<{ id: string }>("POST", "/api/campaigns",
      { name, world, ...(region ? { region } : {}), ...(calendar ? { calendar } : {}), ...(module ? { module } : {}),
        ...(climate ? { climate } : {}) }).then((r) => {
      invalidateConfigCache();   // same as createWorld: this changes `first_run`
      return notifyCampaigns(r);  // and the sidebar's Recent rail gains a row
    }),
  getCampaign: (cid: string) =>
    request<{ meta: CampaignMeta; body: string }>("GET", `/api/campaigns/${cid}`),
  renameCampaign: (cid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/campaigns/${cid}`, { name }).then(notifyCampaigns),
  deleteCampaign: (cid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}`).then(notifyCampaigns),
  // `fresh` for the caller re-reading *because* an undo just repointed one of
  // these deltas: handed a promise started before that write, it would conclude
  // the reversal never happened.
  campaignChanges: (cid: string, fresh = false) =>
    request<RecordChange[]>("GET", `/api/campaigns/${cid}/changes`, undefined, { fresh }),
  // `fresh`, for the reason the ledger below opts out of sharing: the history
  // is re-read precisely when it has just grown — an absorb save, or an undo
  // this same panel performed — and a shared in-flight promise would answer
  // that with the list as it was before the row appeared.
  campaignJournal: (cid: string) =>
    request<JournalEntry[]>("GET", `/api/campaigns/${cid}/journal`, undefined, { fresh: true }),
  undoJournalEntry: (cid: string, jid: string) =>
    request<{ ok: boolean; entry: JournalEntry }>(
      "POST", `/api/campaigns/${cid}/journal/${jid}/undo`),
  // Never shared with an in-flight read of the same path: the ledger is re-read
  // precisely when the records behind it have moved (an absorb save, a scene
  // rename), and the dedupe would answer that with the pre-change response.
  // It has one consumer, so the sharing it opts out of was buying nothing.
  // Not `fresh`: this is a rolling log of citations for values that are already
  // on screen, so a copy one absorb old is stale about a line the reader has
  // not been shown yet either.
  campaignProvenance: (cid: string) =>
    request<Provenance>("GET", `/api/campaigns/${cid}/provenance`),
  campaignLedger: (cid: string) =>
    request<Ledger>("GET", `/api/campaigns/${cid}/ledger`, undefined, { fresh: true }),
  // `fresh`, for the reason the ledger opts out of sharing: the timeline is
  // re-read precisely when the records behind it have moved — a scene ended, a
  // beat recorded, a scene renamed — and the dedupe would answer that with the
  // response from before the absorb landed.
  campaignTimeline: (cid: string) =>
    request<Timeline>("GET", `/api/campaigns/${cid}/timeline`, undefined, { fresh: true }),

  // search (#33). Never coalesced with an in-flight read: a search page issues
  // one of these per settled keystroke, and the shared-promise cache is keyed
  // on the path — which for a repeated query is the same path, so a result
  // arriving for the query BEFORE an edit-and-undo would be served as the
  // answer to the current one.
  search: (q: string, opts?: { scope?: string; root?: string; kinds?: string[];
                               mode?: SearchMode; limit?: number }) => {
    const params = new URLSearchParams({ q });
    if (opts?.scope) params.set("scope", opts.scope);
    if (opts?.root) params.set("root", opts.root);
    if (opts?.kinds?.length) params.set("kinds", opts.kinds.join(","));
    if (opts?.mode) params.set("mode", opts.mode);
    if (opts?.limit) params.set("limit", String(opts.limit));
    return request<SearchResult>("GET", `/api/search?${params}`, undefined, { fresh: true });
  },

  // scenes
  // Never coalesced, like the scene, alternates and proposal reads above — and
  // opted out for every caller rather than at each mutation's call site, which
  // is the same lesson those three record.
  //
  // The scene list decides which sid the URL may name (#87), and `CampaignView`
  // orders its reads by when they were ISSUED so a superseded one cannot
  // install over a newer. A shared read breaks that ordering rather than merely
  // being stale: it is as old as the request it joined, so a read issued after
  // a rename can be handed a promise from before it and still carry the newest
  // sequence number — retiring the genuinely post-rename relist and installing
  // a list with the old id in it. `fresh` at the caller's choice was tried, and
  // left exactly that hole for the one caller that did not pass it.
  //
  // Nothing is lost by it: this component is the only caller, so the sharing
  // had no second asker to share with.
  listScenes: (cid: string) =>
    request<SceneMeta[]>("GET", `/api/campaigns/${cid}/scenes`, undefined, { fresh: true }),
  createScene: (cid: string, title?: string, suggestedDate?: string, pcless?: boolean) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/scenes`,
      { title, suggested_date: suggestedDate, pcless }),
  // Never shared, like the alternates and proposal reads. `selectScene` is the
  // refresh every mutating path funnels through, and a shared read is as old as
  // the request it joined — so a reroll or swap firing while an earlier refresh
  // was open would pair a fresh alternate set with a PRE-mutation transcript,
  // and the readiness gate cannot tell: the transcript carries the new window
  // token either way. Opted out for every caller rather than at each mutation's
  // call site, because a rule each caller has to remember is one the next
  // caller forgets.
  getScene: (cid: string, sid: string, window?: { limit: number; before?: number }) => {
    const qs = window
      ? "?" + new URLSearchParams({
          limit: String(window.limit),
          ...(window.before === undefined ? {} : { before: String(window.before) }),
        })
      : "";
    return request<ScenePage>("GET", `/api/campaigns/${cid}/scenes/${sid}${qs}`,
                              undefined, { fresh: true });
  },
  renameScene: (cid: string, sid: string, title: string) =>
    request<{ id: string; title: string }>("PUT", `/api/campaigns/${cid}/scenes/${sid}`, { title }),
  deleteScene: (cid: string, sid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/scenes/${sid}`),

  // `response` is a one-shot, unpersisted per-turn override (the length chip
  // beside Send) — rides only this call, exactly like regenerate's guidance.
  chat: (cid: string, sid: string, content: string, onEvent: (e: ChatEvent) => void,
         response?: ResponseOverride, signal?: AbortSignal) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/chat`,
               response ? { content, response } : { content }, onEvent, signal),
  retry: (cid: string, sid: string, onEvent: (e: ChatEvent) => void, response?: ResponseOverride,
          signal?: AbortSignal) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/retry`,
               response ? { response } : undefined, onEvent, signal),
  regenerate: (cid: string, sid: string, onEvent: (e: ChatEvent) => void, guidance?: string,
               response?: ResponseOverride, signal?: AbortSignal) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/regenerate`,
               (guidance || response)
                 ? { ...(guidance ? { guidance } : {}), ...(response ? { response } : {}) }
                 : undefined,
               onEvent, signal),

  // Reroll alternates: every variant of the generation a reroll would replace,
  // `active` being the one the transcript is showing (null once a reroll's
  // stream died and left the slot empty). Previews only — picking one is what
  // brings its full text back, as transcript.
  // Never coalesced, for the same reason `getRollProposal` is not. `fetchAlternates`
  // stamps the answer with the window token current when it *issued* the read —
  // that is the whole readiness gate — and a shared read is as old as the request
  // it joined. A reroll or swap firing while an earlier GET is still open would
  // otherwise attach that older set to the newer transcript: the counter names
  // the wrong active take, and an arrow promotes a still-valid but wrong id.
  getAlternates: (cid: string, sid: string) =>
    request<SceneAlternates>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/alternates`, undefined, { fresh: true }),
  pickAlternate: (cid: string, sid: string, id: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/scenes/${sid}/alternates/${id}`),

  // dice rolls
  roll: (cid: string, sid: string, notation: string, label?: string) =>
    request<{ ok: boolean; roll: RollEntry; message: string }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/roll`,
      { notation, ...(label ? { label } : {}) }),
  listRolls: (cid: string) => request<RollEntry[]>("GET", `/api/campaigns/${cid}/rolls`),
  // Never coalesced, for every caller. `CampaignView` orders proposal writes by
  // the order their reads were *issued*, and a shared read is as old as the
  // request it joined rather than as new as the claim it was handed — so a
  // newer claim could be attached to an older answer and outrank a fresher one
  // (#95). Opting the endpoint out restores the invariant that ordering assumes
  // instead of guarding each place the mismatch shows up. It costs nothing:
  // sharing exists for lookups several components fire at once, and this one
  // has a single caller.
  getRollProposal: (cid: string, sid: string) =>
    request<{ record: ProposalRecord | null }>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/roll-proposal`, undefined, { fresh: true }),
  resolveProposal: (cid: string, sid: string,
                    body: { proposal: string; action: "accept" | "decline";
                            check?: string; actor?: string;
                            difficulty?: number; modifier?: number },
                    onEvent: (e: ChatEvent) => void, signal?: AbortSignal) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/roll-proposal`, body, onEvent, signal),
  getSceneChecks: (cid: string, sid: string) =>
    request<{ actors: SceneCheckActor[] }>("GET", `/api/campaigns/${cid}/scenes/${sid}/checks`),
  rollCheck: (cid: string, sid: string,
              body: { check: string; actor: string; difficulty?: number; modifier?: number }) =>
    request<{ ok: boolean; resolution: CheckResolution; message: string }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/check`, body),

  getWorld: (wid: string) =>
    request<{ meta: WorldMeta; body: string; counts: Record<string, number> }>("GET", `/api/worlds/${wid}`),

  // entities (locations | lore), world or campaign scope
  listEntities: (scope: EntityScope, kind: EntityKind) =>
    request<EntitySummary[]>("GET", `${entityBase(scope)}/${kind}`),
  createEntity: (scope: EntityScope, kind: EntityKind,
                 body: { name: string; body?: string; keys?: string; owners?: string;
                         secrecy?: string; fields?: Record<string, string> }) =>
    request<{ id: string }>("POST", `${entityBase(scope)}/${kind}`, body),
  readEntity: (scope: EntityScope, kind: EntityKind, id: string) =>
    request<EntityDetail>("GET", `${entityBase(scope)}/${kind}/${id}`),
  updateEntity: (scope: EntityScope, kind: EntityKind, id: string,
                 patch: { name?: string; body?: string; keys?: string; owners?: string;
                          secrecy?: string; fields?: Record<string, string>; rev?: string }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/${kind}/${id}`, patch),
  deleteEntity: (scope: EntityScope, kind: EntityKind, id: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/${kind}/${id}`),

  // tags
  listTags: (wid: string) => request<Record<string, string>>("GET", `/api/worlds/${wid}/tags`),
  addTag: (wid: string, name: string) => request<{ id: string }>("POST", `/api/worlds/${wid}/tags`, { name }),
  renameTag: (wid: string, tid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/worlds/${wid}/tags/${tid}`, { name }),
  deleteTag: (wid: string, tid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/tags/${tid}`),

  // characters
  listCharacters: (scope: EntityScope) => request<CharacterSummary[]>("GET", `${entityBase(scope)}/characters`),
  createCharacter: (wid: string, body: { name: string; version_name?: string; card?: Card }) =>
    request<{ character: string; version: string }>("POST", `/api/worlds/${wid}/characters`, body),
  readCharacter: (scope: EntityScope, cid: string) =>
    request<CharacterDetail>("GET", `${entityBase(scope)}/characters/${cid}`),
  setDefaultVersion: (scope: EntityScope, cid: string, vid: string) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/characters/${cid}`, { default_version: vid }),
  setCharacterBirthdate: (wid: string, cid: string, birthdate: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/characters/${cid}/birthdate`, { birthdate }),
  deleteCharacter: (wid: string, cid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/characters/${cid}`),
  getCharacterTagline: (wid: string, cid: string) =>
    request<{ tagline: string }>("GET", `/api/worlds/${wid}/characters/${cid}/tagline`),
  setCharacterTagline: (wid: string, cid: string, tagline: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/characters/${cid}/tagline`, { tagline }),
  generateCharacterTagline: (wid: string, cid: string) =>
    request<{ tagline: string }>("POST", `/api/worlds/${wid}/characters/${cid}/tagline/generate`),
  /** Scope-aware: a campaign-local character (an absorb `new_character`, say) has
   *  no world counterpart, so the campaign route is the only way it can ever be
   *  given an anchor. Campaign scope reads through the overlay and writes
   *  campaign-side. */
  getCharacterVoiceAnchor: (scope: EntityScope, cid: string) =>
    request<{ voice_anchor: string }>("GET", `${entityBase(scope)}/characters/${cid}/voice-anchor`),
  /** A blank anchor REMOVES it, opting the character back out of drift detection. */
  setCharacterVoiceAnchor: (scope: EntityScope, cid: string, voice_anchor: string) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/characters/${cid}/voice-anchor`, { voice_anchor }),
  generateCharacterVoiceAnchor: (scope: EntityScope, cid: string) =>
    request<{ voice_anchor: string }>("POST", `${entityBase(scope)}/characters/${cid}/voice-anchor/generate`),
  createVersion: (scope: EntityScope, cid: string, body: { name: string; card: Card }) =>
    request<{ version: string }>("POST", `${entityBase(scope)}/characters/${cid}/versions`, body),
  updateVersion: (scope: EntityScope, cid: string, vid: string, card: Card) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/characters/${cid}/versions/${vid}`, { card }),
  deleteVersion: (scope: EntityScope, cid: string, vid: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/characters/${cid}/versions/${vid}`),
  importCharacter: (wid: string, file: File, format: string, into?: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("format", format);
    if (into) form.append("into", into);
    return requestForm<{ character: string; version: string }>(`/api/worlds/${wid}/characters/import`, form);
  },
  localizeImages: (wid: string, cid: string, vid: string, onEvent: (e: LocalizeEvent) => void) =>
    streamPost<LocalizeEvent>(
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/localize`, undefined, onEvent),
  imageUrl: (wid: string, cid: string, vid: string, name: string) =>
    `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${name}`,
  /** A card download for one version. Not a `request` — the response is binary
   *  and the route names the file, so this is an href for a `<a download>`. */
  exportUrl: (wid: string, cid: string, vid: string, format: CardFormat) =>
    `/api/worlds/${wid}/characters/${cid}/versions/${vid}/export?format=${format}`,
  putImage: (scope: EntityScope, cid: string, vid: string, name: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ name: string; ext: string }>(
      `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/${name}`, form, "PUT");
  },
  deleteImage: (scope: EntityScope, cid: string, vid: string, name: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/${name}`),
  promoteImage: (scope: EntityScope, cid: string, vid: string, name: string) =>
    request<{ ok: boolean }>("POST", `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/${name}/promote`),
  setAvatarFocus: (scope: EntityScope, cid: string, vid: string, focus: number) =>
    request<{ ok: boolean }>("PUT",
      `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/avatar/focus`, { focus }),
  entityImageUrl: (scope: EntityScope, kind: EntityKind, eid: string, name: string) =>
    `${entityBase(scope)}/${kind}/${eid}/images/${name}`,
  listEntityImages: (scope: EntityScope, kind: EntityKind, eid: string) =>
    request<{ name: string; ext: string; v: string }[]>("GET", `${entityBase(scope)}/${kind}/${eid}/images`),
  campaignCoverUrl: (cid: string, opts?: { w?: number; v?: string }) => {
    const q = new URLSearchParams();
    if (opts?.w) q.set("w", String(opts.w));
    if (opts?.v) q.set("v", opts.v);
    const qs = q.toString();
    return `/api/campaigns/${cid}/cover${qs ? `?${qs}` : ""}`;
  },
  putCampaignCover: (cid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ ext: string; v: string }>(`/api/campaigns/${cid}/cover`, form, "PUT");
  },
  deleteCampaignCover: (cid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/cover`),
  putEntityImage: (scope: EntityScope, kind: EntityKind, eid: string, name: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ name: string; ext: string }>(
      `${entityBase(scope)}/${kind}/${eid}/images/${name}`, form, "PUT");
  },
  deleteEntityImage: (scope: EntityScope, kind: EntityKind, eid: string, name: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/${kind}/${eid}/images/${name}`),
  promoteEntityImage: (scope: EntityScope, kind: EntityKind, eid: string, name: string) =>
    request<{ ok: boolean }>("POST", `${entityBase(scope)}/${kind}/${eid}/images/${name}/promote`),
  importCharacterBook: (wid: string, cid: string, vid: string) =>
    request<{ created: { kind: string; id: string }[] }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/lorebook/import`),
  importCharacterFromChub: (wid: string, url: string, into?: string, intoVersion?: string) =>
    request<ChubImportResult>(
      "POST", `/api/worlds/${wid}/characters/import/chub`,
      into ? { url, into, into_version: intoVersion } : { url }),
  setCharacterChubSource: (wid: string, cid: string, vid: string, url: string) =>
    request<{ chub_source: string }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-source`, { url }),
  clearCharacterChubSource: (wid: string, cid: string, vid: string) =>
    request<{ chub_source: string }>(
      "DELETE", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-source`),
  downloadCharacterChubGallery: (wid: string, cid: string, vid: string, onEvent: (e: ChubGalleryEvent) => void) =>
    streamPost<ChubGalleryEvent>(
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-gallery`, undefined, onEvent),
  downloadCharacterChubLorebooks: (wid: string, cid: string, vid: string) =>
    request<{ lorebooks_found: number; created: { kind: string; id: string }[] }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/chub-lorebooks`),
  findChubUnlinked: (wid: string) =>
    request<{ versions: ChubUnlinkedVersion[] }>("GET", `/api/worlds/${wid}/characters/chub-unlinked`),

  // pcs
  listPCs: (scope: EntityScope) => request<PCSummary[]>("GET", `${entityBase(scope)}/pcs`),
  createCampaignPC: (cid: string, body: { name: string; tags?: string[]; persona?: Persona }) =>
    request<{ pc: string; version: string }>("POST", `/api/campaigns/${cid}/pcs`, body),
  listCampaignPCs: (cid: string) => request<PCSummary[]>("GET", `/api/campaigns/${cid}/pcs`),
  createPC: (wid: string, body: { name: string; tags?: string[]; persona?: Persona }) =>
    request<{ pc: string; version: string }>("POST", `/api/worlds/${wid}/pcs`, body),
  readPC: (scope: EntityScope, pid: string) => request<PCDetail>("GET", `${entityBase(scope)}/pcs/${pid}`),
  updatePC: (scope: EntityScope, pid: string, patch: { default_version?: string; tags?: string[] }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/pcs/${pid}`, patch),
  deletePC: (wid: string, pid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/pcs/${pid}`),
  createPCVersion: (scope: EntityScope, pid: string, body: { name: string; persona: Persona }) =>
    request<{ version: string }>("POST", `${entityBase(scope)}/pcs/${pid}/versions`, body),
  updatePCVersion: (scope: EntityScope, pid: string, vid: string, persona: Persona) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/pcs/${pid}/versions/${vid}`, { persona }),
  deletePCVersion: (scope: EntityScope, pid: string, vid: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/pcs/${pid}/versions/${vid}`),
  // PC images (#219) — the character calls one folder over. Kept as their own
  // entries rather than folded into `putImage` & co. with a kind argument: the
  // character helpers are called from a dozen places that have no PC in hand,
  // and a required extra argument on all of them buys nothing here.
  pcImageUrl: (scope: EntityScope, pid: string, vid: string, name: string) =>
    `${entityBase(scope)}/pcs/${pid}/versions/${vid}/images/${name}`,
  listPCImages: (scope: EntityScope, pid: string, vid: string) =>
    request<{ name: string; ext: string; v: string }[]>(
      "GET", `${entityBase(scope)}/pcs/${pid}/versions/${vid}/images`),
  putPCImage: (scope: EntityScope, pid: string, vid: string, name: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ name: string; ext: string }>(
      `${entityBase(scope)}/pcs/${pid}/versions/${vid}/images/${name}`, form, "PUT");
  },
  deletePCImage: (scope: EntityScope, pid: string, vid: string, name: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/pcs/${pid}/versions/${vid}/images/${name}`),
  promotePCImage: (scope: EntityScope, pid: string, vid: string, name: string) =>
    request<{ ok: boolean }>("POST",
      `${entityBase(scope)}/pcs/${pid}/versions/${vid}/images/${name}/promote`),
  setPCAvatarFocus: (scope: EntityScope, pid: string, vid: string, focus: number) =>
    request<{ ok: boolean }>("PUT",
      `${entityBase(scope)}/pcs/${pid}/versions/${vid}/images/avatar/focus`, { focus }),

  // greetings & plot maps
  listGreetings: (scope: EntityScope) => request<Greeting[]>("GET", `${entityBase(scope)}/greetings`),
  createGreeting: (scope: EntityScope, draft: GreetingDraft) =>
    request<{ id: string }>("POST", `${entityBase(scope)}/greetings`, draft),
  readGreeting: (scope: EntityScope, gid: string) =>
    request<GreetingDetail>("GET", `${entityBase(scope)}/greetings/${gid}`),
  updateGreeting: (scope: EntityScope, gid: string,
                   patch: { name?: string; body?: string; present?: string[]; requires_tags?: string[];
                            predecessor_join?: string; pcless?: boolean; rev?: string }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/greetings/${gid}`, patch),
  deleteGreeting: (scope: EntityScope, gid: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/greetings/${gid}`),
  setEdges: (scope: EntityScope, gid: string, edges: { leads_to?: string[]; excludes?: string[] }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/greetings/${gid}/edges`, edges),
  importGreetings: (wid: string, body: { character: string; version: string }) =>
    request<{ greetings: string[] }>("POST", `/api/worlds/${wid}/greetings/import`, body),
  getGreetingSubjects: (wid: string, gid: string) =>
    request<Record<string, string[]>>("GET", `/api/worlds/${wid}/greetings/${gid}/subjects`),
  setImageSubjects: (wid: string, gid: string, name: string, subjects: string[]) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/greetings/${gid}/images/${name}/subjects`, { subjects }),
  listImageAppearances: (wid: string, cid: string) =>
    request<Appearance[]>("GET", `/api/worlds/${wid}/characters/${cid}/appearances`),
  listUntaggedImages: (wid: string) =>
    request<Appearance[]>("GET", `/api/worlds/${wid}/subjects/untagged`),
  copyGreetingImage: (scope: EntityScope, cid: string, vid: string,
                      body: { gid: string; name: string; slot: "avatar" | "gallery" }) =>
    request<{ name: string; ext: string }>(
      "POST", `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/copy-from-greeting`, body),

  // campaign world-copy actions
  markGreeting: (cid: string, gid: string, status: "completed" | "skipped" | "none") =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/greetings/${gid}/mark`, { status }),
  pickVersion: (cid: string, kind: "characters" | "pcs", aid: string, version: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/${kind}/${aid}/pick-version`, { version }),
  importVersion: (cid: string, kind: "characters" | "pcs", aid: string, version: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/${kind}/${aid}/import-version`, { version }),
  /** One actor's image, whichever kind it is: `kind` IS the asset base, so
   *  characters and PCs address their art identically (#219). The single
   *  builder for both — the character-only `actorImageUrl` and campaign-only
   *  `campaignImageUrl` it replaced were the same URL twice over, and every
   *  place that drew a portrait beside a name had "characters" written into
   *  it, which is what left every PC showing initials. */
  actorImageUrl: (scope: EntityScope, kind: "characters" | "pcs", aid: string,
                  vid: string, name: string) =>
    `${entityBase(scope)}/${kind}/${aid}/versions/${vid}/images/${name}`,

  // campaign cast & play
  listAppearances: (cid: string) => request<RosterEntry[]>("GET", `/api/campaigns/${cid}/appearances`),
  addCastBatch: (cid: string, sid: string, refs: { kind: string; id: string; version?: string; role?: string }[]) =>
    request<{ ok: boolean; added: number; skipped: string[] }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/cast/batch`, { refs }),
  getCast: (cid: string, sid: string) => request<Actor[]>("GET", `/api/campaigns/${cid}/scenes/${sid}/cast`),
  addToCast: (cid: string, sid: string,
              body: { kind: string; id: string; version?: string; role?: string }) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/scenes/${sid}/cast`, body),
  removeFromCast: (cid: string, sid: string, kind: string, id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/scenes/${sid}/cast/${kind}/${id}`),
  availableGreetings: (cid: string, after?: string) =>
    request<Availability[]>("GET",
      `/api/campaigns/${cid}/greetings/available${after ? `?after=${encodeURIComponent(after)}` : ""}`),
  startFromGreeting: (cid: string, sid: string, greeting: string) =>
    request<{ ok: boolean; id: string }>("POST", `/api/campaigns/${cid}/scenes/${sid}/start-from-greeting`, { greeting }),
  getSceneLocation: (cid: string, sid: string) =>
    request<SceneLocation>("GET", `/api/campaigns/${cid}/scenes/${sid}/location`),
  setSceneLocation: (cid: string, sid: string, location: string) =>
    request<{ ok: boolean; moved: boolean; name: string }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/location`, { location }),
  getSceneDatetime: (cid: string, sid: string) =>
    request<SceneDatetime>("GET", `/api/campaigns/${cid}/scenes/${sid}/datetime`),
  setSceneDatetime: (cid: string, sid: string, datetime: string) =>
    request<{ ok: boolean; advanced: boolean; friendly: string; id: string;
              // Whether this scene's moment carried the campaign clock forward
              // with it (#100). Forward only: a flashback reports moved: false.
              clock?: { moved: boolean; now: string } }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/datetime`, { datetime }),
  getCalendarConfig: (cid: string) =>
    request<CalendarConfig>("GET", `/api/campaigns/${cid}/calendar`),
  getCalendarProviders: () =>
    request<{ providers: { id: string; name: string }[] }>("GET", "/api/calendars/providers"),

  // ---- the campaign clock (#100) ----
  getCampaignClock: (cid: string) =>
    request<CampaignClock>("GET", `/api/campaigns/${cid}/clock`),
  /** The digest an advance would produce, writing nothing. Needs no reason. */
  previewAdvance: (cid: string, body: AdvanceRequest) =>
    request<{ digest: AdvanceDigest }>("POST", `/api/campaigns/${cid}/advance/preview`, body),
  advanceTime: (cid: string, body: AdvanceRequest) =>
    request<{ ok: boolean; moved: boolean; now: string; friendly: string; digest: AdvanceDigest }>(
      "POST", `/api/campaigns/${cid}/advance`, body),

  // ---- weather (#45, #195) and climates (#40) ----
  getSceneWeather: (cid: string, sid: string, opts?: { location?: string; native?: string }) => {
    const q = new URLSearchParams();
    if (opts?.location) q.set("location", opts.location);
    if (opts?.native) q.set("native", opts.native);
    const tail = q.toString() ? `?${q}` : "";
    return request<SceneWeather>("GET", `/api/campaigns/${cid}/scenes/${sid}/weather${tail}`);
  },
  setWeatherOverride: (cid: string, body: WeatherOverrideBody) =>
    request<WeatherSpan | { cleared: number }>("PUT", `/api/campaigns/${cid}/weather`, body),
  replaceWeatherOverride: (cid: string, storageKey: string, spanId: string,
                           body: WeatherOverrideBody) =>
    request<WeatherSpan>("PUT", `/api/campaigns/${cid}/weather/${storageKey}/${spanId}`, body),
  deleteWeatherOverride: (cid: string, storageKey: string, spanId: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/weather/${storageKey}/${spanId}`),
  clearWeather: (cid: string, body: WeatherRangeBody) =>
    request<{ cleared: number }>("POST", `/api/campaigns/${cid}/weather/clear`, body),
  resumeWeather: (cid: string, body: WeatherRangeBody) =>
    request<{ resumed: number }>("POST", `/api/campaigns/${cid}/weather/resume`, body),
  listClimates: () => request<{ climates: ClimateSummary[] }>("GET", "/api/climates"),
  readClimate: (id: string) =>
    request<{ climate: Climate; builtin: boolean; custom: boolean }>("GET", `/api/climates/${id}`),
  saveClimate: (id: string, doc: Climate) =>
    request<{ climate: Climate }>("PUT", `/api/climates/${id}`, doc),
  climateReferrers: (id: string) =>
    request<{ campaigns: { id: string; name: string }[];
              locations: { campaign: string; id: string; name: string }[] }>(
      "GET", `/api/climates/${id}/referrers`),
  deleteClimate: (id: string) =>
    request<{ ok: boolean; reverted_to_preset: boolean }>("DELETE", `/api/climates/${id}`),
  getCalendarMonths: (scope: CalendarScope, year: number) =>
    request<{ months: CalendarMonth[] }>(
      "GET",
      `/api/${scope.kind === "campaign" ? "campaigns" : "worlds"}/${scope.id}/calendar/months?year=${year}`),
  setCalendarConfig: (cid: string, cfg: CalendarConfig) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/calendar`, cfg),
  listConnections: () => request<LLMConnection[]>("GET", "/api/llm-connections"),
  createConnection: (draft: LLMConnectionDraft) =>
    request<{ id: string }>("POST", "/api/llm-connections", draft).then((r) => {
      // The backend never auto-activates a freshly-created connection (see
      // store/llm_connections.py's delete_connection, which clears
      // active_connection_id specifically so a same-slug recreation can't
      // silently reactivate) — this invalidation is defense in depth, not
      // load-bearing, in case that ever changes.
      invalidateConfigCache();
      return notifyConfig(r);
    }),
  readConnection: (id: string) => request<LLMConnectionDetail>("GET", `/api/llm-connections/${id}`),
  updateConnection: (id: string, patch: Partial<LLMConnectionDraft>) =>
    request<LLMConnectionDetail>("PUT", `/api/llm-connections/${id}`, patch).then((r) => {
      invalidateConfigCache();
      return notifyConfig(r);
    }),
  deleteConnection: (id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/llm-connections/${id}`).then((r) => {
      invalidateConfigCache();
      return notifyConfig(r);
    }),
  refreshConnectionModels: (id: string) =>
    request<ModelsRefreshResult>("POST", `/api/llm-connections/${id}/models/refresh`),

  listStyles: () => request<Style[]>("GET", "/api/styles"),
  createStyle: (draft: StyleDraft) => request<{ id: string }>("POST", "/api/styles", draft),
  readStyle: (sid: string) => request<StyleDetail>("GET", `/api/styles/${sid}`),
  updateStyle: (sid: string, patch: Partial<StyleDraft>) =>
    request<{ ok: boolean }>("PUT", `/api/styles/${sid}`, patch),
  deleteStyle: (sid: string) => request<{ ok: boolean }>("DELETE", `/api/styles/${sid}`),
  duplicateStyle: (sid: string) => request<{ id: string }>("POST", `/api/styles/${sid}/duplicate`),

  // response presets
  listResponsePresets: () => request<ResponsePresetSummary[]>("GET", "/api/response-presets"),
  createResponsePreset: (draft: ResponsePresetDraft) =>
    request<{ id: string }>("POST", "/api/response-presets", draft),
  getResponsePreset: (pid: string) => request<ResponsePresetDetail>("GET", `/api/response-presets/${pid}`),
  updateResponsePreset: (pid: string, patch: Partial<ResponsePresetDraft>) =>
    request<{ ok: boolean }>("PUT", `/api/response-presets/${pid}`, patch),
  deleteResponsePreset: (pid: string) => request<{ ok: boolean }>("DELETE", `/api/response-presets/${pid}`),
  duplicateResponsePreset: (pid: string) =>
    request<{ id: string }>("POST", `/api/response-presets/${pid}/duplicate`),
  responsePresetUsage: (pid: string) =>
    request<ResponsePresetUsage>("GET", `/api/response-presets/${pid}/usage`),
  listLengthPresets: () => request<Record<string, LengthPreset>>("GET", "/api/length-presets"),

  // response bundle (scoped preset + overrides + resolution), all three scopes
  getGlobalResponse: () => request<ResponseBundle>("GET", "/api/response"),
  setGlobalResponse: (patch: Partial<ResponseFields>) =>
    request<{ ok: boolean }>("PUT", "/api/response", patch),
  getCampaignResponse: (cid: string) => request<ResponseBundle>("GET", `/api/campaigns/${cid}/response`),
  setCampaignResponse: (cid: string, patch: Partial<ResponseFields>) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/response`, patch),
  getSceneResponse: (cid: string, sid: string) =>
    request<ResponseBundle>("GET", `/api/campaigns/${cid}/scenes/${sid}/response`),
  setSceneResponse: (cid: string, sid: string, patch: Partial<ResponseFields>) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/scenes/${sid}/response`, patch),

  getSceneContext: (cid: string, sid: string) =>
    request<SceneContext>("GET", `/api/campaigns/${cid}/scenes/${sid}/context`),
  // Pins are campaign-scoped with the scene as a parameter: one read returns
  // this scene's rules and the campaign-wide ones they override. `fresh`,
  // because a pin is read straight after being set and a cached list would show
  // the state before the write that prompted the reload.
  getPins: (cid: string, sid: string) =>
    request<{ pins: PinRule[] }>("GET",
      `/api/campaigns/${cid}/pins?sid=${encodeURIComponent(sid)}`, undefined, { fresh: true }),
  setPin: (cid: string, body: { ref: string; mode: "pin" | "exclude";
                                scope?: "scene" | "campaign"; sid?: string; ttl_posts?: number }) =>
    request<{ ok: boolean; pin: PinRule }>("POST", `/api/campaigns/${cid}/pins`, body),
  removePin: (cid: string, ref: string, scope: "scene" | "campaign", sid: string) =>
    request<{ ok: boolean }>("DELETE",
      `/api/campaigns/${cid}/pins?ref=${encodeURIComponent(ref)}`
      + `&scope=${scope}&sid=${encodeURIComponent(sid)}`),
  // `fresh`, like `campaignLedger`: a briefing is a continuity view, and the
  // one moment it is read is right after the previous scene's save — exactly
  // when a cached copy would still be showing the commitment that save resolved.
  sceneBriefing: (cid: string, sid: string) =>
    request<Briefing>("GET", `/api/campaigns/${cid}/scenes/${sid}/briefing`,
                      undefined, { fresh: true }),
  // `fresh`, like `campaignLedger` and `sceneBriefing`: this is re-read on the
  // refreshKey a completed generation bumps, and the turn that generation just
  // captured is the entire reason for the re-read. A shared in-flight GET from
  // before the turn would answer it with a list that cannot contain the new
  // row, leaving Turn history a turn behind until some later refresh.
  listScenePrompts: (cid: string, sid: string) =>
    request<{ entries: PromptEntry[] }>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/prompts`, undefined, { fresh: true }),
  // Deliberately NOT `fresh`: a snapshot is frozen by construction, so two
  // readers of one entry can share an answer that cannot go stale.
  getScenePrompt: (cid: string, sid: string, eid: string) =>
    request<PromptSnapshot>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/prompts/${eid}`),
  // `fresh`, like `sceneBriefing`: the panel re-reads this immediately after the
  // automatic POST commits, which is exactly when a shared in-flight GET issued
  // *before* that write would hand back a pre-write answer — and the read token
  // would then install it as the newest word, hiding a summary that is durable
  // on the server with no later reread scheduled.
  getRollingSummary: (cid: string, sid: string) =>
    request<RollingSummary>("GET", `/api/campaigns/${cid}/scenes/${sid}/rolling-summary`,
                            undefined, { fresh: true }),
  /** Ask the server to refold the summary. Without `force` it is a no-op
   *  unless enough posts have landed, which is why the play loop can fire it
   *  after every turn: the gate is the server's, not the caller's. */
  /** `upto` bounds the fold to a transcript the caller knows was a clean
   *  boundary — the play loop releases the scene before firing this, so a fast
   *  next send can append a player post the fold would otherwise swallow, and
   *  the reply that answers it is only an append, so it would stay out of the
   *  "current" summary until another whole threshold went by. */
  refreshRollingSummary: (cid: string, sid: string, force = false, upto?: number) => {
    const params = new URLSearchParams();
    if (force) params.set("force", "true");
    if (upto !== undefined) params.set("upto", String(upto));
    const qs = params.toString();
    return request<RollingSummaryRefresh>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/rolling-summary${qs ? `?${qs}` : ""}`);
  },
  sceneSuggestions: (cid: string, after?: string, offscreen?: boolean,
                     direction?: string, rank = true) => {
    const params = new URLSearchParams();
    if (after) params.set("after", after);
    if (offscreen) params.set("offscreen", "true");
    if (direction) params.set("direction", direction);
    if (!rank) params.set("rank", "false");
    const qs = params.toString();
    return request<{ suggestions: SceneSuggestion[]; greeting_picks?: string[]; next_date?: string }>(
      "POST", `/api/campaigns/${cid}/scene-suggestions${qs ? `?${qs}` : ""}`);
  },
  sceneIntent: (cid: string, text: string, offscreen: boolean) =>
    request<SceneIntentResult>("POST", `/api/campaigns/${cid}/scene-intent`,
      { text, offscreen }),
  // The scene ledger (#88). `fresh`, like the continuity ledger: the picker
  // re-reads this precisely when it has just written to it (a save, a dismiss,
  // a restore), and a shared or cached response would answer that with the
  // list from before the write.
  // `greetings=false` declines the composed greeting half. Composing it parses
  // the frontmatter of every greeting in the campaign, and the picker -- the
  // only caller -- renders greetings from its own ranked `availableGreetings`
  // read and drops those rows. A management surface wants them and omits this.
  listSceneIdeas: (cid: string, greetings = true) =>
    request<SceneIdea[]>("GET",
      `/api/campaigns/${cid}/scene-ideas${greetings ? "" : "?greetings=false"}`,
      undefined, { fresh: true }),
  saveSceneIdea: (cid: string, idea: SceneIdeaDraft) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/scene-ideas`, idea),
  setSceneIdeaStatus: (cid: string, lid: string,
                       status: "active" | "used" | "dismissed", scene?: string) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/scene-ideas/${encodeURIComponent(lid)}`,
                             { status, ...(scene ? { scene } : {}) }),
  getCastDetail: (cid: string, sid: string, kind: string, id: string) =>
    request<CastDetail>("GET", `/api/campaigns/${cid}/scenes/${sid}/cast/${kind}/${id}`),
  // `fresh`, like the ledger and the briefing: this is a continuity view, and
  // the absorb that rewrote every file behind it has just run.
  getCasefile: (cid: string, sid: string, kind: string, id: string) =>
    request<Casefile>("GET", `/api/campaigns/${cid}/scenes/${sid}/cast/${kind}/${id}/casefile`,
                      undefined, { fresh: true }),
  editMessage: (cid: string, sid: string, index: number, content: string) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/scenes/${sid}/messages/${index}`, { content }),
  // Cascade post-delete (#75): this post and everything after it, plus the
  // reversal of what the scene wrote. The reply is a report, not an ack — a
  // record the compare-and-swap refused to put back is the one thing the
  // transcript afterwards cannot show, so the caller has to be able to say so.
  deleteMessagesFrom: (cid: string, sid: string, index: number) =>
    request<CascadeReport>("DELETE", `/api/campaigns/${cid}/scenes/${sid}/messages/${index}`),
  // `force` re-runs an absorb the backend has already recorded in the chronicle;
  // without it that POST is a 409 (kind "already_absorbed") -- see #235.
  absorbScene: (cid: string, sid: string, force = false) =>
    request<SceneAbsorb>("POST",
      `/api/campaigns/${cid}/scenes/${sid}/absorb${force ? "?force=true" : ""}`),
  saveChronicle: (cid: string, sid: string,
                  body: { one_line: string; summary: string; keywords: string[];
                          timeline_events: TimelineEvent[]; edits: StagedEdit[];
                          commit_token?: string }) =>
    request<ChronicleEntry & { applied: string[];
      failures: { id: string; reason: string; kind: "conflict" | "error" }[] }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/chronicle`, body),
  getChronicle: (cid: string) =>
    request<ChronicleEntry[]>("GET", `/api/campaigns/${cid}/chronicle`),
  // Both scoped retries take a `signal`. Releasing the review they belong to
  // has to reach the *server*: these run one LLM call per present NPC on a
  // budget of their own, and `absorb_budget = 0` means that budget is
  // unbounded — so a retry the reviewer walked away from would otherwise keep
  // spending time and credits on a review that no longer exists. Aborting
  // closes the connection, which is what the endpoint watches for.
  retryAudit: (cid: string, sid: string, signal?: AbortSignal) =>
    request<{ mechanics: Mechanics; edits: StagedEdit[] }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/audit`, undefined, { signal }),
  // The dossier phase's sibling to retryAudit (#286): re-runs that phase alone,
  // on a fresh budget, without disturbing the rest of the open review.
  retryDossiers: (cid: string, sid: string, signal?: AbortSignal) =>
    request<{ dossiers: Dossiers; edits: StagedEdit[] }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/dossiers`, undefined, { signal }),
  opener: (cid: string, sid: string, prompt: string, onEvent: (e: ChatEvent) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/opener`, { prompt }, onEvent),
  firstPost: (cid: string, sid: string, text: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/scenes/${sid}/first-post`, { text }),

  // lorebook import
  lorebookParse: (wid: string, file: File, format: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("format", format);
    return requestForm<{ entries: LoreEntryDraft[] }>(`/api/worlds/${wid}/lorebook/parse`, form);
  },
  lorebookImport: (wid: string, entries: LoreEntryDraft[]) =>
    request<{ created: { kind: string; id: string }[] }>("POST", `/api/worlds/${wid}/lorebook/import`, { entries }),

  // scenario-card import (#217). The two parse calls write nothing — they hand
  // back a proposal to review; only scenarioImport touches the world.
  scenarioParse: (wid: string, file: File, format: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("format", format);
    return requestForm<ScenarioProposal>(`/api/worlds/${wid}/scenario/parse`, form);
  },
  scenarioParseUrl: (wid: string, url: string) =>
    request<ScenarioProposal>("POST", `/api/worlds/${wid}/scenario/parse-url`, { url }),
  scenarioImport: (wid: string, proposal: ScenarioProposal, art: boolean) =>
    request<ScenarioImportResult>("POST", `/api/worlds/${wid}/scenario/import`, { ...proposal, art }),

  // campaign group state (#47)
  getGroupState: (cid: string, gid: string) =>
    request<GroupState>("GET", `/api/campaigns/${cid}/groups/${gid}/state`),
  putGroupState: (cid: string, gid: string, state: Omit<GroupState, "updated">) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/groups/${gid}/state`, state),

  // modules
  listModules: () => request<ModuleSummary[]>("GET", "/api/modules"),
  readModule: (mid: string) => request<ModuleDetail>("GET", `/api/modules/${mid}`),
  readModuleContent: (mid: string, kind: string, id: string) =>
    request<ModuleContentEntry>("GET", `/api/modules/${mid}/content/${kind}/${id}`),
  instantiateContent: (scope: EntityScope, kind: string, mid: string, contentId: string) =>
    request<{ id: string }>(
      "POST",
      `${entityBase(scope)}/${kind}/instantiate/${mid}/${contentId}`),
  getCampaignModule: (cid: string) =>
    request<CampaignModule>("GET", `/api/campaigns/${cid}/module`),
  setCampaignModule: (cid: string, module: string) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/module`, { module }),
  setWorldModule: (wid: string, module: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/module`, { module }),

  // module authoring (Phase 8)
  createModule: (name: string) => request<{ id: string }>("POST", "/api/modules", { name }),
  duplicateModule: (mid: string, name: string) =>
    request<{ id: string }>("POST", `/api/modules/${mid}/duplicate`, { name }),
  importModule: (file: Blob) =>
    fetch("/api/modules/import", { method: "POST", body: file,
      headers: { "content-type": "application/zip" } }).then(async (r) => {
        if (!r.ok) {
          const data = await r.json().catch(() => ({}));
          throw new ApiError(r.status, data.detail ?? r.statusText, data.kind);
        }
        return r.json() as Promise<{ id: string }>;
      }),
  exportModuleUrl: (mid: string) => `/api/modules/${mid}/export`,
  putModuleManifest: (mid: string, body: { name: string; description: string;
    version: string; dice: string; notes: string; dry_run: boolean }) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/manifest`, body),
  putModuleGroup: (mid: string, gid: string, group: unknown, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/groups/${gid}`,
      { group, dry_run: dryRun }),
  deleteModuleGroup: (mid: string, gid: string, dryRun = false) =>
    request<ModuleEditResult>("DELETE",
      `/api/modules/${mid}/groups/${gid}${dryRun ? "?dry_run=1" : ""}`),
  putModuleSheetType: (mid: string, tid: string, sheetType: unknown, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/sheet-types/${tid}`,
      { sheet_type: sheetType, dry_run: dryRun }),
  deleteModuleSheetType: (mid: string, tid: string, dryRun = false) =>
    request<ModuleEditResult>("DELETE",
      `/api/modules/${mid}/sheet-types/${tid}${dryRun ? "?dry_run=1" : ""}`),
  putModuleCheck: (mid: string, id: string, check: unknown, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/checks/${id}`,
      { check, dry_run: dryRun }),
  deleteModuleCheck: (mid: string, id: string, dryRun = false) =>
    request<ModuleEditResult>("DELETE",
      `/api/modules/${mid}/checks/${id}${dryRun ? "?dry_run=1" : ""}`),
  putModuleCheckDefaults: (mid: string, defaults: Record<string, unknown>, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/check-defaults`,
      { defaults, dry_run: dryRun }),
  putModuleRule: (mid: string, slug: string, flags: unknown, body: string, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/rules/${slug}`,
      { flags, body, dry_run: dryRun }),
  deleteModuleRule: (mid: string, slug: string, dryRun = false) =>
    request<ModuleEditResult>("DELETE",
      `/api/modules/${mid}/rules/${slug}${dryRun ? "?dry_run=1" : ""}`),
  readModuleRule: (mid: string, slug: string) =>
    request<{ meta: Record<string, string>; body: string }>(
      "GET", `/api/modules/${mid}/rules/${slug}`),
  putModuleContent: (mid: string, kind: string, id: string, body: Record<string, unknown>, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/content/${kind}/${id}`,
      { ...body, dry_run: dryRun }),
  deleteModuleContent: (mid: string, kind: string, id: string, dryRun = false) =>
    request<ModuleEditResult>("DELETE",
      `/api/modules/${mid}/content/${kind}/${id}${dryRun ? "?dry_run=1" : ""}`),
  putModuleLayout: (mid: string, layout: unknown, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/layout`, { layout, dry_run: dryRun }),
  putModuleTheme: (mid: string, theme: unknown, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/theme`, { theme, dry_run: dryRun }),
  renameModulePart: (mid: string, kind: ModuleRenameKind, address: Record<string, string>,
                     to: string, dryRun = false) =>
    request<ModuleEditResult>("POST", `/api/modules/${mid}/rename`,
      { kind, address, to, dry_run: dryRun }),

  // sheets
  getCampaignSheets: (cid: string) =>
    request<{ coverage: SheetCoverage; refs: [string, string][] }>(
      "GET", `/api/campaigns/${cid}/sheets`),
  getWorldSheetsIndex: (wid: string) =>
    request<{ modules: string[]; default: string }>("GET", `/api/worlds/${wid}/sheets`),
  getWorldSheets: (wid: string, mid: string) =>
    request<{ coverage: SheetCoverage; refs: [string, string][] }>(
      "GET", `/api/worlds/${wid}/sheets/${mid}`),
  getSheet: (scope: EntityScope, mid: string, kind: string, eid: string) =>
    request<{ sheet: Sheet | null }>(
      "GET",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}`),
  putSheet: (scope: EntityScope, mid: string, kind: string, eid: string,
             body: { sheet_type: string; fields: Record<string, unknown> | null; expected: SheetExpected }) =>
    request<{ ok: boolean }>(
      "PUT",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}`,
      body),
  putSheetCreation: (scope: EntityScope, mid: string, kind: string, eid: string,
                     body: { sheet_type: string; spends: Record<string, Record<string, number>>; expected: SheetExpected }) =>
    request<{ sheet: Sheet }>(
      "PUT",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}/creation`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}/creation`,
      body),
  advanceSheet: (cid: string, kind: string, eid: string, field: string) =>
    request<{ sheet: Sheet }>("POST", `/api/campaigns/${cid}/sheets/${kind}/${eid}/advance`, { field }),
  deleteSheet: (scope: EntityScope, mid: string, kind: string, eid: string, gen: string | null) =>
    request<{ ok: boolean }>(
      "DELETE",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}${gen ? `?gen=${encodeURIComponent(gen)}` : ""}`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}${gen ? `?gen=${encodeURIComponent(gen)}` : ""}`),
};
