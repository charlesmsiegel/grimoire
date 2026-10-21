import type { CampaignImage } from "./play";

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
  /** The log became unreadable — moved, synced away, deleted under the poll.
   *  The stream keeps going: the file usually comes back, and a tail that
   *  ended on the first hiccup would have to be restarted by hand. */
  error?: { detail: string; kind: string };
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
  by_campaign?: { campaign_id: string; count: number }[];
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
  /** Calls whose duration the ledger could not read (a hand-edited row): still
   *  counted in `calls`, never in the latencies, which are over the rest. */
  unparsed_durations: number;
  /** Null when no call in the bucket was timed -- none at all, or only
   *  unparsed ones: there is no latency to report, and 0 would be one. */
  p50: number | null; p90: number | null; p99: number | null;
  min: number | null; max: number | null;
};

export type Stats = {
  days: number; since: string; until: string; campaign: string;
  generated_at: string;
  percentiles: number[];
  totals: PerfBucket;
  by_task: PerfBucket[];
  by_model: PerfBucket[];
  by_campaign?: PerfBucket[];
  /** Chronological: a trend is read left to right. */
  by_day: (PerfBucket & { rerolls?: number; eligible_turns?: number;
                          reroll_rate?: number | null })[];
  /** Failures RECORDED ANYWHERE, from the error store — including the ones
   *  that were never a call, so this total and `totals.errors` differ on
   *  purpose. */
  errors: ErrorSummary;
};

export type LogLevelInfo = { level: LogLevel; levels: LogLevel[] };

/** What the nav rail badges, in one read (`GET /api/shell`).
 *
 *  Every optional count is nullable from the first commit, so a later slice
 *  filling one in is a value change and not a schema change. The distinction
 *  the whole payload turns on: `0` means nothing is waiting, `null` means
 *  nobody computed it — and the rail draws them differently, no tail at all
 *  versus a tail reading 0. It is the cost rule ("a price nobody reported is
 *  never rendered as zero") one domain over.
 *
 *  `campaign.money` is the all-time rollup, drawn by the campaign hub's money
 *  card (the rail's Costs rows carry no tail: one tail cannot hold three
 *  columns). It waited for `store.usage_rollup` rather than for a cheaper
 *  substitute: a bounded 30-day window would have put the same unlabelled
 *  figure on screen meaning something else. Three columns, never summed, and
 *  a `partial` flag — because a figure that cannot be computed must draw
 *  nothing rather than $0.00.
 *
 *  There is no `library` field either: the number of library sections lives in
 *  `librarySections.ts`, and answering it from Python as well would be one
 *  manifest in two languages with nothing holding them level. */
export type ShellCampaign = {
  id: string;
  name: string;
  /** The world id, alongside `world_name` — the same pairing `CampaignMeta`
   *  uses. Needed to address the world's own pages (its images section among
   *  them); `world_name` alone is a label, not a link. Optional so a fixture
   *  frozen before this field existed still type-checks; the live route
   *  always sends it. */
  world?: string;
  world_name: string;
  scenes: number;
  /** Scenes whose frontmatter `done` is not set. `turns` counts that scene's
   *  own transcript blocks that are actual model replies (not player posts,
   *  not a manual dice roll or scene-transition line) — `null` when the
   *  transcript could not be read, never `0` for that case; a scene that
   *  opens cleanly and truly has no replies yet reports the real `0`. */
  open: { sid: string; title: string; turns: number | null }[];
  /** Live canonical commitments (a merged pair counts once). `null` when the
   *  ledger could not be counted -- a garbled commitments.json -- never `0`
   *  for that case: the rail draws no tail and the hub says so in words. */
  ledger_open: number | null;
  /** Null when the campaign binds no mechanics module. "This module keeps no
   *  sheets" is legal, and is not "0 of 0". */
  sheets: { sheeted: number; total: number } | null;
  /** Undecided proposals across every scene holding a pending review. */
  unreviewed: number | null;
  /** Which scenes are holding one, so the hub can link straight at it rather
   *  than making the reader hunt for which scene was absorbed. */
  pending: { sid: string; proposals: number }[];
  /** Images with no description text — deliberately not `untagged`, which is
   *  greeting art with no subjects recorded and stays a separate word. `null`
   *  when the world cannot be read; a world that reads cleanly with nothing
   *  outstanding reports the real `0`. */
  images_undescribed: number | null;
  /** What this campaign has cost over the ledger's whole history.
   *
   *  Optional so a fixture frozen before the field existed still type-checks.
   *  The three columns are separate claims about money and adding any two of
   *  them produces a number nobody can recover — `components/cost.tsx` is the
   *  only thing that formats them, and it is where that rule lives. */
  money?: ShellMoney;
};

/** Three money columns and how complete they are.
 *
 *  `partial` is the field that keeps a badge honest: true means the aggregate
 *  could not be brought up to date, so every figure beside it is a zero nobody
 *  measured. A campaign the ledger has simply never mentioned is `partial:
 *  false` with zeros, which IS a measurement — "nothing was spent here". */
export type ShellMoney = {
  calls: number;
  cost_usd: number;
  estimated_usd: number;
  modelled_usd: number;
  unpriced_calls: number;
  unmetered_calls: number;
  subscription_calls: number;
  modelled_calls: number;
  priced_calls: number;
  total_tokens: number;
  /** The breakdown counts `UsageBucket` names. The aggregate always carries
   *  them; optional because a response from an older build does not. */
  modelled_subscription_calls?: number;
  unpriced_subscription_calls?: number;
  unpriced_native_calls?: number;
  estimated_token_calls?: number;
  partial: boolean;
};

export type ShellPayload = {
  campaigns: number;
  campaign: ShellCampaign | null;
  /** How many chores the user has NOT waved off, or null with no campaign
   *  open. An ignored chore is counted nowhere — that is the whole point of
   *  ignoring one. */
  todo: number | null;
};

/** One thing the app noticed. Derived on every read, never stored: a chore at
 *  zero leaves the list, so a label's number is always this request's. */
export type Chore = {
  id: string;
  campaign_id?: string;
  campaign_name?: string;
  /** What the chore is about, which is what decides whether it can be answered
   *  with no campaign open. `campaign` needs one; `world` is a fact about a
   *  world and `library` about the whole store, so both answer either way — and
   *  that is exactly the moment just after importing a world, when its backlog
   *  is largest and no campaign exists yet. */
  scope: "campaign" | "world" | "library";
  group: string;
  severity: "note" | "warn" | "alert";
  n: number;
  /** What it is. */
  what: string;
  /** Why it matters — the half a bare count cannot carry. */
  why: string;
  /** Where to go and fix it, or null when there is nowhere yet. */
  fix: string | null;
  fix_label: string;
};

/** One instance behind a chore's count — the character with no tagline, the
 *  thread that is owed. `detail` is what makes the row worth reading:
 *  a list of bare names is the count again, spelled out. */
export type ChoreItem = {
  id: string;
  label: string;
  detail: string;
  fix?: string;
};

export type ChoreItems = {
  items: ChoreItem[];
  total: number;
  /** True when `items` is a capped view of `total`. Stated rather than left to
   *  be inferred from a short list, which reads as a complete one. */
  truncated: boolean;
};

export type TodoPayload = {
  chores: Chore[];
  /** Waved off, kept with a Restore so the decision is reversible rather than
   *  forgotten. */
  ignored: Chore[];
  count: number;
  /** The theme headings, in reading order (scenes, wrap-up, commitments, continuity,
   *  character, art, rules, then
   *  costs — `GROUP_ORDER` in `routes/todo.py`), and only the ones with
   *  something under them. The server decides, because deriving the order
   *  from the chore list reorders the headings whenever the data moves: the
   *  chore list is ordered by severity, so the first chore of each theme would
   *  decide where that theme lands. Optional so a payload from before this
   *  field falls back to first-appearance order rather than rendering
   *  nothing. */
  groups?: string[];
};


/** One calendar's year, as the Library's reference view reads it.
 *
 *  `year` is the calendar's OWN year — a Hebrew one is around 5786 — and is
 *  what the server resolved when none was asked for, so a caller can adopt it
 *  rather than assuming a Gregorian default that most calendars cannot
 *  represent. */
export type CalendarYear = {
  id: string;
  name: string;
  year: number;
  region: string;
  months: { key: string; name: string; days: number }[];
  holidays: {
    name: string; fixed: number;
    /** The key of the month it lands in, matching one of `months[].key`.
     *  Resolved server-side, because the protocol's two halves disagree:
     *  `months()` yields a key ("01", "Tishrei") and `describe()` a month
     *  NUMBER (8, 12). Grouping by the number finds no month and silently
     *  renders a year with no holidays in it. */
    month_key: string;
    month?: string | number; month_name?: string; day?: number; friendly?: string;
  }[];
};

/** What `GET /api/campaigns/{cid}/images` answers with.

 *  An envelope rather than a bare array since the library began reading through
 *  to the world: `hidden` names the inherited pictures this campaign has
 *  tombstoned, which appear in no listing by construction and so would be
 *  unreachable — and un-restorable — if they were not reported separately. */
export type CampaignLibrary = { images: CampaignImage[]; hidden: string[] };

/** One image in a WORLD's own library (`store/world_images.py`). No
 *  `inherited`: a world's library is its own by definition, and it is the thing
 *  campaigns inherit FROM. */
export type WorldImage = {
  name: string; ext: string; v: string;
  /** The picture's identity in the image store; absent for a legacy file. */
  image_id?: string | null;
  description?: string; described?: boolean;
};

/** A character or PC that places an image. `scope` is `world:<wid>` or
 *  `campaign:<cid>`; `name` is the placement's name within that record's version
 *  (`avatar`, `gallery_1`), not the record's display name. */
export type ImageUsageActor = { scope: string; id: string; vid: string; name: string };
/** A location, item, group, creature or lore entry that places an image. */
export type ImageUsageEntity = { scope: string; kind: string; id: string; vid: string; name: string };
/** A greeting whose art is the image. */
export type ImageUsageGreeting = { scope: string; id: string; name: string };
/** A name in a world's own image library. */
export type ImageUsageWorldImage = { wid: string; name: string };
/** A name in a campaign's own image library. */
export type ImageUsageCampaignImage = { cid: string; name: string };
/** A world's or campaign's cover. */
export type ImageUsageCover = { scope: string };
/** A collection of a world that holds the image. */
export type ImageUsageCollection = { wid: string; collection: string };
export type ImageUsageEntry =
  | ImageUsageActor | ImageUsageEntity | ImageUsageGreeting | ImageUsageWorldImage
  | ImageUsageCampaignImage | ImageUsageCover | ImageUsageCollection;
/** Where one image object is placed (`GET /api/images/{id}/usage`): exactly
 *  these eight buckets, each a list that may be empty. */
export type ImageUsageReport = {
  characters: ImageUsageActor[];
  pcs: ImageUsageActor[];
  entities: ImageUsageEntity[];
  greetings: ImageUsageGreeting[];
  world_images: ImageUsageWorldImage[];
  campaign_images: ImageUsageCampaignImage[];
  covers: ImageUsageCover[];
  collections: ImageUsageCollection[];
};

// --- the scene state tracker (routes/tracker.py) ---------------------------------

/** Who knows a value: everyone present, or exactly these refs. */
export type TrackerAware = "present" | string[];
export type TrackerValue = { value: string | string[]; aware: TrackerAware; set_by?: "user" };
export type TrackerActor = { present: boolean; fields: Record<string, TrackerValue> };
/** `{ref: actor}` -- one record's whole state of the scene. */
export type TrackerSnapshot = Record<string, TrackerActor>;
export type TrackerField = {
  key: string; label: string; type: "text" | "list" | "enum";
  aware: "present" | "self"; hint: string; options?: string[];
  /** Switched off: kept in the list so a stored value still has a label. */
  off?: boolean;
};
export type TrackerEntry = {
  status: "pending" | "ok" | "failed";
  changed: [string, string, string | string[]][];
  flags: { upstream_changed: boolean; text_changed: boolean };
  /** On a failed entry; "interrupted" for a `pending` one nothing is running. */
  error?: string;
};
/** `GET .../tracker`: every tracked post's key in order, with its index entry. */
export type TrackerSummary = {
  enabled: boolean;
  names: Record<string, string>;
  keys: { index: number; key: string }[];
  entries: Record<string, TrackerEntry>;
  /** `{ref: visible_mood}` for the characters present at the tail. */
  moods: Record<string, string>;
  /** `{field key: label}`, switched-off fields included. */
  labels: Record<string, string>;
};
export type TrackerRecord = {
  key: string; status: TrackerEntry["status"]; flags: TrackerEntry["flags"];
  /** Null for a record that failed before it ever landed. */
  snapshot: TrackerSnapshot | null;
  fields: TrackerField[]; names: Record<string, string>; error?: string;
};
/** One layer of field definitions. The world and campaign layers may add,
 *  change or switch off; the scene layer may only switch off or add. */
export type TrackerLayer = {
  fields?: TrackerField[]; change?: Record<string, Partial<TrackerField>>; off?: string[];
};
export type TrackerLayerBundle = {
  layer: TrackerLayer; effective: TrackerField[]; inherited: TrackerField[];
};
export type TrackerEdits = Record<string, Record<string, {
  value?: string | string[]; aware?: TrackerAware;
}>>;
/** Where a field-definition layer lives. */
export type TrackerScope =
  | { kind: "world"; wid: string }
  | { kind: "campaign"; cid: string }
  | { kind: "scene"; cid: string; sid: string };
/** A campaign's own switch: "" follows the global one. */
export type TrackerSetting = "" | "on" | "off";
export type CampaignTrackerSetting = { setting: TrackerSetting; enabled: boolean };

/** A campaign's own play-decision switches: "" follows the global one, and
 *  `effective` is what each one comes to. */
export type PlayDecisionValues = { speaker_sampling: string; turn_plan: string; play_tool: string };
export type CampaignPlayDecisions = PlayDecisionValues & { effective: PlayDecisionValues };
/** Whether the global Decision model reports a distribution for the speaker
 *  pick (null: the pick resolves to nothing). */
export type PlayDecisionsInfo = { pick_samples: boolean | null };

/** One output-processing regex rule (spec section 2). `id` is minted on create
 *  and unique across every level, because an `off` list elsewhere names it. */
export type RegexRule = {
  id: string; name: string; enabled: boolean;
  pattern: string; flags: string; replacement: string; trim: string[];
  targets: ("model" | "user")[]; applies: ("display" | "prompt")[];
  rewrite_stored: boolean; min_depth: number | null; max_depth: number | null;
  imported: { from: string; pattern: string; notes: string[] } | null;
};
export type RegexLevel = "connection" | "global" | "world" | "campaign";
/** A rule as a level inherits it. `source` is the connection id for a
 *  connection's rule and empty for every other level. `off` is "switched off
 *  by THIS level"; `off_by` names the level whose `off` switched it off, absent
 *  while none has. At a campaign, `off_by: "world"` on a connection or global
 *  rule is a switch the campaign cannot undo: the rule never runs there. */
export type RegexEntry = {
  level: RegexLevel; rule: RegexRule; off: boolean; source: string;
  off_by?: "world" | "campaign";
};
/** One level's file. `off` names inherited rule ids; only the world and the
 *  campaign may carry any. */
export type RegexLayer = { rules: RegexRule[]; off: string[] };
export type RegexBundle = {
  layer: RegexLayer; inherited: RegexEntry[]; warnings: Record<string, string[]>;
};
export type RegexScope =
  | { kind: "global" }
  | { kind: "world"; wid: string }
  | { kind: "campaign"; cid: string }
  | { kind: "connection"; id: string };
/** One rule's turn in the test pane's trace. `reason` is why it did not apply. */
export type RegexStep = {
  rule_id: string; level: RegexLevel; name: string; applied: boolean;
  reason: string | null; matches: number; text_after: string;
};
export type RegexTestBody = {
  scope: RegexScope; text: string; role: "model" | "user";
  phase: "display" | "prompt" | "store"; depth: number;
  draft?: Partial<RegexRule>; connection?: string;
};
/** A post's stored rewrite: what was written before a `rewrite_stored` rule
 *  changed it, the ids of the rules that did, and when. */
export type SceneRewrite = { original: string; rules: string[]; at: string };
/** One SillyTavern script in an import preview: the proposed rule (no id, and
 *  `null` for one that will not translate), the translator's verdict and notes,
 *  and the script as given. */
export type RegexImportRow = {
  index: number; name: string; verdict: "exact" | "approximate" | "untranslatable";
  notes: string[]; rule: Omit<RegexRule, "id"> | null; original: unknown;
};
/** An author's note (play controls V): a standing instruction inserted into
 *  the history `depth` posts from the end (0 = after the last), on every
 *  `every`-th turn. */
export type AuthorsNote = { text: string; depth: number; every: number };
/** A campaign's notes; scene notes keyed by sid (the server resolves them). */
export type AuthorsNotes = {
  campaign: AuthorsNote | null;
  scenes: Record<string, AuthorsNote>;
  characters: Record<string, AuthorsNote>;
};
/** Which notes apply to the scene's NEXT turn. A character entry applies when
 *  that character speaks. */
export type AuthorsNotesNext = {
  turn: number; count: number;
  notes: { level: "campaign" | "scene" | "character"; depth: number; every: number;
           applies: boolean; ref?: string; name?: string }[];
};


/** A composer quick reply (store/quick_replies.py). The kind decides which of
 *  the optional fields it carries: `send`/`direct` → `text` + `mode`; `roll` →
 *  `notation` + `roll_label?`; `task` → `task`; `opener` → none. */
export type QuickReplyKind = "send" | "direct" | "roll" | "task" | "opener";
export type QuickReplyTask = "rolling_summary" | "scene_break" | "next_scene";
export type QuickReplyMode = "send" | "insert";
export type QuickReply = {
  id: string; label: string; kind: QuickReplyKind;
  text?: string; mode?: QuickReplyMode; notation?: string; roll_label?: string; task?: QuickReplyTask;
};
/** A campaign entry hiding the world reply with the same id. */
export type QuickReplyHide = { id: string; hidden: true };
export type QuickReplyEntry = QuickReply | QuickReplyHide;
/** An entry not saved yet: the server mints its id. */
export type QuickReplyDraft = Omit<QuickReply, "id"> & { id?: string };
/** A stored set. `digest` goes back as `expect` on the next PUT; a campaign
 *  set also carries the world replies it layers on as `inherited`. */
export type QuickReplySet = {
  version: 1; replies: QuickReplyEntry[]; digest: string; inherited?: QuickReply[];
};
export type QuickReplyScope = { kind: "world"; wid: string } | { kind: "campaign"; cid: string };
