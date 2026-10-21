import type { ModelLimit, PricingEntry, SamplingReport } from "./inference";

/** `dropped` sections were rendered but left out of the prompt by the budget
 *  packer; they still carry their text so the inspector can show what was cut.
 *  `trimmed` is how many history messages the packer dropped from the front —
 *  0 on every section except Conversation history. */
/** How an owned entry's owner is present (spec §7): `via` is the ref the
 *  presence came through (a holder, a leader, a location), null when there is
 *  none to name. */
export type OwnerPresence = {
  type: "cast" | "current_location" | "held_by" | "led_by" | "member_present" | "headquarters" | "habitat" | "activated";
  via?: string | null;
};
/** An entry's owner and how it is present, carried by any reason that passed
 *  the owner gate. */
type OwnerPart = { owner?: string; owner_presence?: OwnerPresence };
/** Why an entry is in the prompt (Task 7's reason dicts, spec §10). JSON-safe
 *  and for the inspector only; no reason ever reaches a prompt. */
export type LoreReason = OwnerPart & (
  | { type: "key"; key: string; secondary?: string | null; post?: number | null; seed?: boolean }
  | { type: "keyless" }
  | { type: "pinned" }
  | { type: "sticky"; from_post?: number | null; remaining: number }
  | { type: "recursion"; via: string; key?: string }
  | { type: "recall"; score: number }
);
/** One entry of a World info or Recalled lore row. Absent on a capture
 *  recorded before reasons existed. */
export type LoreEntryRow = {
  ref: string; name: string; kind?: string | null; secrecy?: string;
  priority?: number; keep?: boolean; level?: number;
  reason: LoreReason; shed: boolean;
};
/** An entry World info held back this turn (a cooldown). */
export type HeldEntryRow = {
  ref: string; name: string; reason: { type: "cooldown"; remaining: number };
};
/** Frozen retrieval reasons; these fields are display metadata, never prompt prose. */
export type HistorySignalRound = {
  structural: { relation: string; refs: string[] }[] | null;
  lexical: { score: number; matched: string[] } | null;
  semantic: { cosine: number; query: number } | null;
  rerank: { level: string | null; position: number | null; backend: string } | null;
};
export type HistoryEvidenceRow = {
  items: {
    scene: string; identity: string | null; key: string; title: string;
    date?: string; location?: string; tier: number; admitted_by: string[];
    rounds: HistorySignalRound[];
    ranks: { round: number; signal: string; rank: number }[];
    excerpts: number[][]; evidence_ids?: string[];
    posts?: { index: number; key: string; part: number | null; speaker: string; text: string }[]; summary: string; tokens: number; shed: boolean;
  }[];
  coverage: { tiers: number[]; semantic: string; admitted: number; agreement: number;
              verdict: string; eligible?: number;
              /** "off", "ok", or "skipped:<why>" -- why RRF order stood. Absent
               *  on a capture taken before the rerank existed. */
              rerank?: string };
  ceiling: number; budget_from: string[]; query_digest?: string;
  /** Admitted but not in the section: the strongest few in merge order, and
   *  how many there were in all. Absent on a capture taken before they were
   *  listed. */
  candidates?: {
    scene: string; key: string; tier: number; admitted_by: string[];
    rounds: HistorySignalRound[];
    ranks: { round: number; signal: string; rank: number }[];
  }[];
  unselected?: number;
  /** Roadmap 11's classification of this section's items for the reader it
   *  was composed for: the narrator, or one NPC (`actor:characters:<id>`).
   *  Inspector data only -- no basis code reaches a message. Absent on a
   *  capture taken before 11-S3. */
  epistemic?: EpistemicDetail;
  /** Roadmap 10's planning trace, present only when planning was in play.
   *  The questions and terms are model-written and never reach the prompt. */
  plan?: HistoryPlanTrace;
};
export type HistoryPlanRound = {
  questions: string[]; terms: string[]; subjects: string[];
  scene_keys: string[]; evidence_ids: string[];
};
export type HistoryPlanTrace = {
  terminal: string; trigger: string; sufficiency: string; sufficiency_source: string;
  cheap_retrieval_failed: boolean; e0: HistoryPlanRound; rounds: HistoryPlanRound[];
  steps: { task: string; outcome: string; ms: number; rows: number }[];
  plans: { subjects: string[]; questions: string[]; terms: string[] }[];
  gain: string[];
};
export type KnowledgeFingerprint = {
  key: string; part: string; variant: string; digest: string; shown: string;
};
/** One perspective's access to retrieved history (spec 11 §3). */
export type EpistemicAccess = "witnessed" | "known" | "suspected" | "narrator_only" | "unknown";
export type EpistemicSlice = {
  part: "posts" | "summary"; posts: number[]; access: EpistemicAccess; basis: string[];
  /** Each post's fingerprint as classified (11-S3): what an accept sends as
   *  `expect`. Empty for the summary and for a post that moved. Absent on a
   *  capture taken before the accept existed. */
  keys?: KnowledgeFingerprint[];
};
export type EpistemicItem = {
  key: string; scene: string; admitted: boolean;
  state: "attributed" | "withheld" | "unattributed";
  header: { title?: string; date?: string; location?: string };
  known_to: string[]; suspected_by: string[];
  slices: EpistemicSlice[];
  /** Each classified ref's own `[access, basis]`, per post index and for
   *  the whole scene ("summary"). */
  by_ref?: Record<string, Record<string, [EpistemicAccess, string]>>;
  /** The step's actor stood in this scene (spec 11 §5.1 (a)); absent when
   *  not, and on the narrator's rows. */
  stood?: boolean;
};
export type EpistemicDetail = {
  perspective: string;
  /** Parts the Decision stage (11-S6) made visible, and every part it was
   *  eligible to ask about, as [scene key, part, posts]. */
  decided: number;
  pending?: [string, string, number[]][];
  /** Who the refs are, as the prompt named them. */
  names?: Record<string, string>;
  /** What history has cost this step's round so far (spec 11 §14 Q7): its
   *  retrievals, one per speaker that recalled, and the query embeddings
   *  they sent. Absent on the live preview. */
  round?: { retrievals: number; embeds: number };
  items: EpistemicItem[];
};
/** #131: one source's exact rendered contribution, frozen with its section. */
/** #128: why a cast source is in the prompt, stated by its section. */
export type ContextSourceReason =
  | { type: "cast" }
  | { type: "off_scene"; tier: "active" | "known" };
export type ContextSource = {
  id: string;
  label: string; text: string;
  ref?: string;
  /** A lore entry removed by packing; its text remains available to inspect. */
  shed?: boolean;
  /** Cast sources only: the fact that put this character in the prompt. Optional:
   *  a snapshot frozen before it existed has none. */
  reason?: ContextSourceReason;
};
export type ContextSection = {
  /** Stable section identity (#29). OPTIONAL because a prompt snapshot frozen
   *  before ids existed does not carry one — those predate editable labels
   *  too, so their labels are still unique and are a safe fallback key. */
  id?: string;
  label: string; text: string; tokens: number;
  /** Optional for older snapshots and sections with no source boundaries. */
  sources?: ContextSource[];
  tier: "lock-in" | "spotlight" | "background" | "archive" | "recalled" | "history-recall" | "history-aside" | "history";
  dropped: boolean; trimmed: number;
  /** The section carries content the reader pinned, so the packer left it alone
   *  whatever its tier (#129). Optional: snapshots frozen before pins existed
   *  have no such field, and they are rendered by this same component. */
  pinned?: boolean;
  /** World info and Recalled lore rows only: each entry, why it is in, and
   *  whether the packer shed it (spec §10). Optional: a capture recorded
   *  before this existed has none, and is rendered by this same component. */
  entries?: LoreEntryRow[];
  /** Display names for every ref an entry's reason mentions. */
  names?: Record<string, string>;
  /** World info only: entries held back (cooldown). */
  held_back?: HeldEntryRow[];
  history?: HistoryEvidenceRow;
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
  /** Breakdown counts, each a slice of a count above and never money (spec
   *  9.1): `modelled_subscription_calls` sits inside `modelled_calls`,
   *  `unpriced_subscription_calls` inside `unpriced_calls` -- calls a
   *  subscription served that no provider billed -- `unpriced_native_calls`
   *  inside `unpriced_calls` too -- native decisions, which no rate prices,
   *  and never also counted in `unmetered_calls` -- and `estimated_token_calls`
   *  inside `calls`, the ones whose token counts were counted here because the
   *  provider reported none. `/usage` omits each while it is zero, so a reader
   *  takes absent as 0. */
  modelled_subscription_calls?: number;
  unpriced_subscription_calls?: number;
  unpriced_native_calls?: number;
  estimated_token_calls?: number;
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
  /** What the provider bills by (`"subscription"`, `"metered"`): a label,
   *  never a figure -- `cost_basis` alone moves money between columns. Absent
   *  from an older build. */
  billing?: string;
  /** The token counts (and so any modelled figure) rest on a local count,
   *  because the provider reported none. */
  tokens_estimated?: boolean;
};
/** One player post's spend: every call made answering it, the first reply and
 *  each reroll of it. Keyed by transcript index.
 *
 *  `rerolls` counts the calls that RE-answered the post, which is not
 *  `calls - 1`: a turn continued past a dice roll is two calls and one answer. */
export type UsagePostBucket = UsageBucket & {
  post: number; rerolls: number;
  /** Post images the post's calls sent (#377); absent when none. */
  images?: number;
};
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
  available_months?: string[];
  /** Model strings the ledger holds that no rate prices, and whose calls DO
   *  carry token counts — so a rate would price them. Names the reason behind
   *  `unpriced_calls`, which is otherwise just a number. One entry per call
   *  shape, so a model served by two providers is two entries: `facts_model`
   *  is the key the model's own rates are stated under on `provider_id` (the
   *  model that was asked for, when the answer named a dated snapshot), and a
   *  row filed before providers were named has `provider_id` "" and only the
   *  pricing table can price it. */
  unpriced_models?: { model: string; facts_model: string; provider_id: string;
                      calls: number }[];
  /** Whether a model's own rates can be written now: only on a store at the
   *  current model-settings format, so a line never opens an editor that
   *  cannot save. Before it, the pricing table is the only rate on offer. */
  rates_editable?: boolean;
  /** The store's model settings were written by a newer version, which is
   *  why `rates_editable` is false: past the upgrade, never waiting for it. */
  rates_newer?: boolean;
  campaign: string; since: string; until: string; generated_at: string;
  /** The order the server applied before capping the list — echoed back, so a
   *  view can tell an answer to the sort it asked for from a stale one. */
  order: string;
  totals: UsageBucket; scenes: SceneCostRow[];
  listed: number; truncated: boolean;
};
export type MonthlyCostBucket = UsageBucket & { estimated_total_usd: number };
export type MonthlyCosts = {
  month: string; since: string; until: string; available_months: string[];
  totals: MonthlyCostBucket;
  campaigns: (MonthlyCostBucket & { campaign_id: string; campaign_name: string })[];
  unassigned: MonthlyCostBucket;
  trend: (MonthlyCostBucket & { month: string })[];
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
/** How a breakdown's token counts were made (`store.tokens.counting`).
 *  `tokenizer` is a tiktoken encoding name (`cl100k_base`), `heuristic` (the
 *  characters/4 fallback) or `mixed` (some strings fell back). `native` only when the tokenizer that counted is the
 *  model's own; for most backends it is not, and the counts are estimates.
 *  Even native counts are text only — the provider adds per-message framing. */
export type TokenCounting = { tokenizer: string; native: boolean };
export type PromptFieldStatus = "ok" | "empty" | "unreadable";
export type PromptField = { text: string; status: PromptFieldStatus };
export type CampaignDefaultPrompt = PromptField & {
  world: { id: string | null; text: string; status: PromptFieldStatus | "unavailable" };
};
export type PromptFieldStatuses = {
  world_default_prompt?: PromptFieldStatus | "unavailable";
  campaign_default_prompt?: PromptFieldStatus;
  scene_focus?: PromptFieldStatus;
};
export type SceneContext = {
  model: string; total_tokens: number; dropped_tokens: number;
  budget_tokens: number; sections: ContextSection[];
  /** Status only, not a prompt row/token charge. Frozen entries keep their
   *  captured statuses; old entries may omit this block entirely. */
  prompt_field_statuses?: PromptFieldStatuses;
  /** Absent on a snapshot frozen before it existed — read as an estimate. */
  token_count?: TokenCounting;
  /** The sampler preset this turn is (or was) sent with. Absent on a snapshot
   *  frozen before presets existed. */
  sampling?: SamplingReport | null;
  /** The window of `model` as the backend resolved it (01i), which the bar is
   *  drawn against. Absent on a snapshot frozen before it existed, which falls
   *  back to the catalog lookup; `value: null` is a window nobody knows. */
  model_window?: ModelLimit;
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
      | "replay" | "extend"
      // Decision captures (roadmap 01b): one entry per decision, not per turn.
      | "response-selector" | "scene-break" | "voice-drift"
      | "continuity-identity" | "continuity-reconcile"
      // Roadmap 12's investigations, filed at campaign level: a run's tool
      // loop (one entry per run) and its decide tool's calls.
      | "investigation-continuity" | "investigation-question" | "investigation-decide";
  /** `"decide"` on a decision capture and `"tools"` on an investigation's
   *  tool loop, each kept in a retention pool of its own; absent on a
   *  generation, and on a speaker pick filed before decisions had an
   *  operation (`isDecision` covers both). */
  operation?: "decide" | "tools";
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
  /** Null on a capture frozen before counters were recorded. */
  token_count?: TokenCounting | null;
  /** The model's window (01i); null or absent on a capture frozen before it. */
  model_window?: ModelLimit | null;
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
