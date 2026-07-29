import { parseSSEChunk, type ChatEvent, type LocalizeEvent, type ChubGalleryEvent, type RollProposalPayload } from "./stream";
import type { Model } from "./models";

export class ApiError extends Error {
  constructor(public status: number, public detail: string, public kind?: string) {
    super(detail);
  }
}

async function requestRaw<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  return res.json() as Promise<T>;
}

// Identical GETs that overlap share one request: opening a scene fires the
// same cast/appearances/datetime lookups from several components at once.
// The map only holds in-flight promises, so nothing is ever served stale.
const inflightGets = new Map<string, Promise<unknown>>();

function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (method !== "GET") return requestRaw<T>(method, path, body);
  const pending = inflightGets.get(path);
  if (pending) return pending as Promise<T>;
  const p = requestRaw<T>(method, path, body).finally(() => inflightGets.delete(path));
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
  active_connection: { id: string; kind: LLMConnectionKind; name: string } | null;
  ready: boolean;
  /** Seconds of silence before an LLM call is abandoned; "0" disables. */
  llm_timeout: string;
  /** Seconds one absorb's whole LLM sequence may take; "0" disables. */
  absorb_budget: string;
};
export type DataDirInfo = {
  data_dir: string;
  default: string;
  is_default: boolean;
  source: "env" | "custom" | "default";
  exists: boolean;
};
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
  module?: string;
};
export type SceneMeta = { id: string; title: string; model: string; created: string; updated: string; date: string; pcless?: boolean };
export type Message = { role: "user" | "assistant"; content: string; speaker?: string };
export type Scene = { meta: { id: string; title: string; response_preset?: string }; messages: Message[] };

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

export type EntitySummary = { id: string; name: string; keys?: string; owners?: string;
  has_image?: boolean; image_v?: string | null } & Record<string, unknown>;
export type EntityDetail = {
  meta: { id: string; name: string; keys?: string; owners?: string; sd_prompt?: string } & Record<string, unknown>;
  body: string;
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
export type PCSummary = { id: string; name: string; tags: string[]; default_version: string; versions: VersionRef[] };
export type PCDetail = {
  meta: { id: string; name: string; tags: string[]; default_version: string };
  versions: { id: string; name: string; persona: Persona }[];
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
export type GreetingDetail = { meta: Greeting; body: string; edges: Edges; predecessors: string[] };
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
export type RosterEntry = { kind: string; id: string; version: string; role: string; scenes: string[] };
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
export type ContextSection = { label: string; text: string; tokens: number };
export type SceneContext = { model: string; total_tokens: number; sections: ContextSection[] };
export type CastDetail = { kind: "characters" | "pcs"; id: string; name: string; version: string; body: string };
export type TimelineEvent = { date: string; text: string };
export type StagedEdit = {
  id: string; kind: "character_state" | "lore" | "authored" | "relationship" | "bond" | "plot"
    | "new_character" | "new_location" | "new_lore" | "sheet";
  target: { kind: string; id: string }; label: string; field: string;
  before: string; after: string; authored: boolean;
  payload?: Record<string, unknown>;
};
export type MechanicsDrop = { id: string; field?: string; reason: string };
export type Mechanics = {
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
  warnings: string[]; dropped: MechanicsDrop[];
};
export type DossierFailure = { id: string; reason: string };
export type Dossiers = {
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
  refreshed: string[]; failed: DossierFailure[];
  /** NPCs the absorb budget ran out before reaching — never attempted (#243). */
  skipped: string[];
};
export type SceneAbsorb = {
  one_line: string; summary: string; keywords: string[];
  timeline_events: TimelineEvent[]; cast: string[]; location: string; date: string;
  edits: StagedEdit[];
  mechanics: Mechanics;
  dossiers: Dossiers;
};
export type SceneSuggestion = {
  title: string; premise: string; date?: string;
  cast: { kind: string; id: string; name: string }[];
  location: { id: string; name: string } | null;
};
export type ChronicleEntry = {
  id: string; one_line: string; summary: string; keywords: string[];
  cast: string[]; location: string; date: string; absorbed: string;
};
export type DiffLine = { op: "equal" | "insert" | "delete"; text: string };
export type FieldDiff = { field: string; label: string; diff: DiffLine[] };
export type RecordChange = {
  ref: { kind: string; id: string }; name: string;
  scene: { id: string; title: string; date: string };
  fields: FieldDiff[];
};

// lorebook import
export type LoreEntryDraft = { name: string; keys: string[]; body: string; category: EntityKind };

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

async function streamPost<T = ChatEvent>(
  path: string,
  body: unknown,
  onEvent: (e: T) => void,
): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
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
}

export const api = {
  getConfig: () => {
    if (!configCache) {
      configCache = request<Config>("GET", "/api/config").catch((err) => {
        configCache = null; // never cache a failure
        throw err;
      });
    }
    return configCache;
  },
  putConfig: (body: Partial<{ theme: string; system_prompt: string; quote_color: string; user_label: string; assistant_label: string; active_connection_id: string; llm_timeout: string; absorb_budget: string }>) =>
    request<Config>("PUT", "/api/config", body).then((cfg) => {
      configCache = Promise.resolve(cfg); // the write's response is the fresh config
      return cfg;
    }),
  getDataDir: () => request<DataDirInfo>("GET", "/api/config/data-dir"),
  putDataDir: (data_dir: string | null) =>
    request<DataDirInfo>("PUT", "/api/config/data-dir", { data_dir })
      .then((info) => {
        invalidateConfigCache(); // a store move can change everything
        return info;
      }),

  // worlds
  listWorlds: () => request<WorldMeta[]>("GET", "/api/worlds"),
  createWorld: (name: string) => request<{ id: string }>("POST", "/api/worlds", { name }),
  renameWorld: (wid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/worlds/${wid}`, { name }),
  deleteWorld: (wid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}`),

  // campaigns
  listCampaigns: () => request<CampaignMeta[]>("GET", "/api/campaigns"),
  createCampaign: (name: string, world: string, region?: string, calendar?: string, module?: string,
                   climate?: string) =>
    request<{ id: string }>("POST", "/api/campaigns",
      { name, world, ...(region ? { region } : {}), ...(calendar ? { calendar } : {}), ...(module ? { module } : {}),
        ...(climate ? { climate } : {}) }),
  getCampaign: (cid: string) =>
    request<{ meta: CampaignMeta; body: string }>("GET", `/api/campaigns/${cid}`),
  renameCampaign: (cid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/campaigns/${cid}`, { name }),
  deleteCampaign: (cid: string) => request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}`),
  campaignChanges: (cid: string) =>
    request<RecordChange[]>("GET", `/api/campaigns/${cid}/changes`),

  // scenes
  listScenes: (cid: string) => request<SceneMeta[]>("GET", `/api/campaigns/${cid}/scenes`),
  createScene: (cid: string, title?: string, suggestedDate?: string, pcless?: boolean) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/scenes`,
      { title, suggested_date: suggestedDate, pcless }),
  getScene: (cid: string, sid: string) =>
    request<Scene>("GET", `/api/campaigns/${cid}/scenes/${sid}`),
  renameScene: (cid: string, sid: string, title: string) =>
    request<{ id: string; title: string }>("PUT", `/api/campaigns/${cid}/scenes/${sid}`, { title }),
  deleteScene: (cid: string, sid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/scenes/${sid}`),

  // `response` is a one-shot, unpersisted per-turn override (the length chip
  // beside Send) — rides only this call, exactly like regenerate's guidance.
  chat: (cid: string, sid: string, content: string, onEvent: (e: ChatEvent) => void,
         response?: ResponseOverride) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/chat`,
               response ? { content, response } : { content }, onEvent),
  retry: (cid: string, sid: string, onEvent: (e: ChatEvent) => void, response?: ResponseOverride) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/retry`,
               response ? { response } : undefined, onEvent),
  regenerate: (cid: string, sid: string, onEvent: (e: ChatEvent) => void, guidance?: string,
               response?: ResponseOverride) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/regenerate`,
               (guidance || response)
                 ? { ...(guidance ? { guidance } : {}), ...(response ? { response } : {}) }
                 : undefined,
               onEvent),

  // dice rolls
  roll: (cid: string, sid: string, notation: string, label?: string) =>
    request<{ ok: boolean; roll: RollEntry; message: string }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/roll`,
      { notation, ...(label ? { label } : {}) }),
  listRolls: (cid: string) => request<RollEntry[]>("GET", `/api/campaigns/${cid}/rolls`),
  getRollProposal: (cid: string, sid: string) =>
    request<{ record: ProposalRecord | null }>("GET", `/api/campaigns/${cid}/scenes/${sid}/roll-proposal`),
  resolveProposal: (cid: string, sid: string,
                    body: { proposal: string; action: "accept" | "decline";
                            check?: string; actor?: string;
                            difficulty?: number; modifier?: number },
                    onEvent: (e: ChatEvent) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/roll-proposal`, body, onEvent),
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
                 body: { name: string; body?: string; keys?: string; owners?: string; fields?: Record<string, string> }) =>
    request<{ id: string }>("POST", `${entityBase(scope)}/${kind}`, body),
  readEntity: (scope: EntityScope, kind: EntityKind, id: string) =>
    request<EntityDetail>("GET", `${entityBase(scope)}/${kind}/${id}`),
  updateEntity: (scope: EntityScope, kind: EntityKind, id: string,
                 patch: { name?: string; body?: string; keys?: string; owners?: string; fields?: Record<string, string> }) =>
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
  campaignImageUrl: (cid: string, char: string, vid: string, name: string) =>
    `/api/campaigns/${cid}/characters/${char}/versions/${vid}/images/${name}`,
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

  // greetings & plot maps
  listGreetings: (scope: EntityScope) => request<Greeting[]>("GET", `${entityBase(scope)}/greetings`),
  createGreeting: (scope: EntityScope, draft: GreetingDraft) =>
    request<{ id: string }>("POST", `${entityBase(scope)}/greetings`, draft),
  readGreeting: (scope: EntityScope, gid: string) =>
    request<GreetingDetail>("GET", `${entityBase(scope)}/greetings/${gid}`),
  updateGreeting: (scope: EntityScope, gid: string,
                   patch: { name?: string; body?: string; present?: string[]; requires_tags?: string[]; predecessor_join?: string; pcless?: boolean }) =>
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
  actorImageUrl: (scope: EntityScope, cid: string, vid: string, name: string) =>
    `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/${name}`,

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
    request<{ ok: boolean; advanced: boolean; friendly: string; id: string }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/datetime`, { datetime }),
  getCalendarConfig: (cid: string) =>
    request<CalendarConfig>("GET", `/api/campaigns/${cid}/calendar`),
  getCalendarProviders: () =>
    request<{ providers: { id: string; name: string }[] }>("GET", "/api/calendars/providers"),

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
      return r;
    }),
  readConnection: (id: string) => request<LLMConnectionDetail>("GET", `/api/llm-connections/${id}`),
  updateConnection: (id: string, patch: Partial<LLMConnectionDraft>) =>
    request<LLMConnectionDetail>("PUT", `/api/llm-connections/${id}`, patch).then((r) => {
      invalidateConfigCache();
      return r;
    }),
  deleteConnection: (id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/llm-connections/${id}`).then((r) => {
      invalidateConfigCache();
      return r;
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
  sceneSuggestions: (cid: string, after?: string, offscreen?: boolean) => {
    const params = new URLSearchParams();
    if (after) params.set("after", after);
    if (offscreen) params.set("offscreen", "true");
    const qs = params.toString();
    return request<{ suggestions: SceneSuggestion[]; greeting_picks?: string[]; next_date?: string }>(
      "POST", `/api/campaigns/${cid}/scene-suggestions${qs ? `?${qs}` : ""}`);
  },
  getCastDetail: (cid: string, sid: string, kind: string, id: string) =>
    request<CastDetail>("GET", `/api/campaigns/${cid}/scenes/${sid}/cast/${kind}/${id}`),
  editMessage: (cid: string, sid: string, index: number, content: string) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/scenes/${sid}/messages/${index}`, { content }),
  absorbScene: (cid: string, sid: string) =>
    request<SceneAbsorb>("POST", `/api/campaigns/${cid}/scenes/${sid}/absorb`),
  saveChronicle: (cid: string, sid: string,
                  body: { one_line: string; summary: string; keywords: string[];
                          timeline_events: TimelineEvent[]; edits: StagedEdit[] }) =>
    request<ChronicleEntry & { applied: string[];
      sheet_failures: { id: string; reason: string; kind: "conflict" | "error" }[] }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/chronicle`, body),
  getChronicle: (cid: string) =>
    request<ChronicleEntry[]>("GET", `/api/campaigns/${cid}/chronicle`),
  retryAudit: (cid: string, sid: string) =>
    request<{ mechanics: Mechanics; edits: StagedEdit[] }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/audit`),
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
