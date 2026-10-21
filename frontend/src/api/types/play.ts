import type { Aging, Commitment, PlotThread } from "./ledger";
import type { ArtProvenance, ArtSourceRef, EntityScope } from "./records";

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
  phase: string;
  sequence: number | null;
  optional: boolean;
  mark?: GreetingMark;   // campaign lists carry it
  /** The greeting's plot-map edges, exactly as its own read reports them. On
   *  list rows only (a detail read's `meta` never has it: the detail carries
   *  `edges` beside it), so a screen that wants every greeting's edges asks
   *  once rather than once per greeting. ABSENT when the server could not read
   *  the plot map: that is "unknown", not "no edges", and a caller that writes
   *  edges back must not treat it as empty. */
  edges?: Edges;
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
  phase?: string;
  sequence?: number | null;
  optional?: boolean;
};
export type Style = { id: string; name: string; description: string; tags: string[]; built_in: boolean };
export type StyleDetail = { meta: Style; body: string };
export type StyleDraft = { name: string; description?: string; tags?: string[]; body?: string };

// Response targets and the scoped response bundle.
// The explicit "clear the inherited style" sentinel, mirroring
// response_presets.STYLE_CLEAR byte for byte. "" is a different answer — "this
// scope has no opinion, keep walking outward". The U+2063 prefix (invisible
// separator, as in scenes.ROLL_SPEAKER) keeps it out of slugify's reach, so a
// user style genuinely named "None" — id `none` — stays an ordinary style.
// Defined here, not per-component, so the two pickers cannot drift apart.
export const STYLE_CLEAR = "⁣none";
export type ResponseFields = {
  response_preset: string; style_id: string;
  length_reply_words: string; length_blocks: string; length_paragraphs: string;
  length_speakers: string; length_blocks_per_speaker: string;
  response_opening_words: string; response_opening_paragraphs: string;
  response_continuation_words: string; response_continuation_paragraphs: string;
};
export type ResponseEffective = {
  style_id: string; reply_words: number; blocks: number; paragraphs: number;
  speakers: number; blocks_per_speaker: number;
  opening: { words: number; paragraphs: number };
  continuation: { words: number; paragraphs: number };
};
export type ResponseProvenance = Record<string, { scope: string; source?: string }>;
// A one-shot, unpersisted per-turn override — the same scope-shaped dict
// response_presets.resolve(turn=...) accepts server-side: a bare
// {response_preset: id} or loose knob overrides.
export type ResponseOverride = Partial<ResponseFields>;
/** Everything ONE reroll may override, all of it riding that call alone.
 *  `provider`, `model` and `preset` are the route override (#77, spec 5.6):
 *  the provider to send this reroll to, the model to run there and the
 *  sampler preset (`PRESET_CLEAR` for none) to run it under. An absent part is
 *  the scene route's own -- a provider alone keeps the standing model, and the
 *  server asks for a model when there is none to keep. (`connection_id` is the
 *  legacy name for `provider`, still read by the server and sent by nothing.) */
export type RegenerateOverrides = {
  guidance?: string;
  response?: ResponseOverride;
  provider?: string;
  model?: string;
  preset?: string;
};
export type ResponseBundle = ResponseFields & { effective: ResponseEffective; provenance: ResponseProvenance };

/** A scene's group-play settings: who replies next, and how much they talk.
 *  `sitting_out` and `order_list` hold actor references (`kind:id`). */
export type GroupSettings = {
  order: "directed" | "manual" | "list" | "natural";
  order_list: string[];
  talkativeness: Record<string, number>;
  sitting_out: string[];
  auto_rounds: number;
};
/** World preview: the gating verdict without campaign history (#68). */
export type GreetingAvailabilityPreview = {
  id: string; name: string; available: boolean; reasons: string[];
  pcless?: boolean;
  /** The greeting's location id, "" for none — what `greetingDraft` pre-fills
   *  the confirm form's location picker from (#218). */
  location?: string;
};
export type Availability = GreetingAvailabilityPreview & {
  unlocked: boolean;
  mark?: GreetingMark;
  recommendation?: "successor" | "phase_optional" | null;
};
export type Appearance = {
  gid: string; greeting_name: string; name: string; url: string; thumb?: string; copyable?: boolean;
  /** The served picture's identity in the image store; absent for a legacy
   *  file and for a remote reference. */
  image_id?: string | null;
};

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
  /** Whether this version lock has scene evidence and belongs in campaign cast. */
  appeared: boolean;
  /** The token of the avatar `version` resolves to, spent as `?v=` so the
   *  portrait is cached immutable; null when there is no avatar. */
  avatar_v?: string | null;
};
export type SceneLocationRef = { id: string; name: string };
export type SceneLocation = { current: SceneLocationRef | null; visited: SceneLocationRef[] };
export type SceneDatetimeCast = { kind: string; id: string; name: string; age: number | null; birthday_today: boolean };
export type SceneDatetimeFacts = {
  native: string; friendly: string; weekday: string; secondary_friendly: string | null;
  holidays_today: string[]; upcoming: { name: string; in_days: number } | null; cast: SceneDatetimeCast[];
  /** Warn-once pre-notices for this scene's own moment (#106) — imminent and
   *  not yet acknowledged. Judged from the scene's date, not the campaign
   *  clock, so a flashback is not warned about next week. */
  notices: Notice[];
};

/** One thing about to happen that the reader has not been told about yet
 *  (#106). `key` names the OCCURRENCE — the day plus the thing — which is what
 *  makes next year's Midwinter warn again after this year's was dismissed, and
 *  is the only field the dismiss route reads. */
export type Notice = {
  key: string; kind: "holiday" | "event"; name: string;
  in_days: number; friendly: string;
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
  /** How far ahead an imminent event is warned about (#106). Sent back on save
   *  like `stale_after_days`, but its unset value is `null` rather than 0: 0 is
   *  a real setting here — a campaign that has switched the warnings off — so
   *  it cannot double as "no opinion" the way a staleness threshold's 0 does.
   *  The server always sends a number; `null` is only ever the client's own
   *  cleared-field state on the way back. */
  warn_days: number | null;
};

// ---- the campaign clock (#100) ----
/** One row of the clock's log: where time went, why, and when that was recorded. */
export type ClockLogEntry = { from: string; to: string; reason: string; at: string };
export type CampaignClock = {
  now: string; friendly: string; log: ClockLogEntry[];
  /** The campaign's write token (#409): an opaque value that changes whenever
   *  the campaign is written. Only ever compared for equality — it is not a
   *  version, a count or an ordering — and handed back to `/advance` as
   *  `expect_revision` to say which state a move was priced against. */
  revision: string;
};

/** One image in a campaign's own library (#376). `v` is the cache token an
 *  `?v=` URL is answered `immutable` for. */
/** One stored image with no description entry at all — the describe backlog.
 *  Key ABSENT, not empty: an image reviewed and deliberately left undescribed
 *  is finished, and never appears here. */
export type UndescribedImage = {
  kind: string; id: string; vid: string; name: string;
  record_name: string; url: string;
  /** Present only when migration found this picture described several ways and
   *  left the choice to the player: each candidate text and where it came from.
   *  The picture has no description until one is saved. */
  conflicts?: DescriptionConflict[];
};
/** One candidate text of a migration conflict (`UndescribedImage.conflicts`). */
export type DescriptionConflict = { text: string; from: string };
/** One tile in the world gallery (#200) — every image the world holds, from
 *  whichever of the eight bases it hangs off.
 *
 *  `url` and `thumb` both carry the `?v=` token, so a grid caches immutable
 *  instead of revalidating a request per tile. `described` is key presence and
 *  `description` is the text, the distinction the sidecar turns on: an image
 *  reviewed and deliberately left blank is described with `""`.
 *
 *  `subjects` reaches only greeting art, whose sidecar is who-is-in-the-picture
 *  rather than what-it-depicts. `undefined` on every other kind (it has no such
 *  sidecar) AND on a greeting image nobody has tagged yet — which is what the
 *  tagging queue is still going to ask about. `[]` is an answered "nobody". */
export type GalleryImage = {
  kind: string; id: string; vid: string; name: string; record_name: string;
  url: string; thumb: string; ext: string;
  described: boolean; description: string;
  subjects?: string[] | null;
  /** The picture's identity in the image store; absent for a legacy file. */
  image_id?: string | null;
  v?: string; placement?: ArtSourceRef; provenance?: ArtProvenance;
};
export type CampaignImage = {
  name: string; ext: string; v: string;
  /** The picture's identity in the image store; absent for a legacy file. */
  image_id?: string | null;
  /** True when the picture belongs to the campaign's WORLD and this campaign
   *  is only reading through to it. The two are different sentences everywhere
   *  they are shown: the picker offers "remove from this campaign" (which hides
   *  it, reversibly) for one and a real delete for the other, and only a
   *  campaign's own image can be described here rather than in its world. */
  inherited?: boolean;
  /** What the picture shows. `described` is separate on purpose: `description`
   *  is `""` both for "never reviewed" and for "reviewed, nothing to say", and
   *  only `described` tells them apart. */
  description?: string; described?: boolean;
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
  birthdays: { name: string; age: number | null; native: string; friendly: string }[];
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
/** `to` skips to a date, `days` advances by a duration; `to` wins if both are sent.
 *
 *  `expect_revision` is the campaign's write token as this move was priced
 *  against it (#409). Omitted, the advance runs against whatever the campaign
 *  is when it arrives, which is what every caller had before it existed; sent
 *  and stale, the server answers 409 `campaign_moved` and the caller re-prices.
 *  `/advance/preview` ignores it — the preview is where a token is picked up,
 *  not where one is spent. */
export type AdvanceRequest = {
  to?: string; days?: number; reason?: string; expect_revision?: string;
};

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
