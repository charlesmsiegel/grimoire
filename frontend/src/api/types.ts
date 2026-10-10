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
import type { RollProposalPayload, RunHandle } from "./stream";

/** One entry of a provider's model catalog, as the backend normalizes it.
 *
 *  A wire shape since #149: the catalog is fetched server-side, from whichever
 *  provider the connection names, so the browser no longer parses OpenRouter's
 *  response format. `models.ts` re-exports this and owns the labels drawn from
 *  it. `null` where a provider volunteered no metadata — distinct from `0`,
 *  which is a context length of zero and a price of free. */
export type Model = {
  id: string;
  name: string;
  context: number | null;
  prompt: string | null;
  completion: string | null;
  /** Whether the provider says this model reads images (#377); `null` when it
   *  did not say. Absent on a catalog cached before the field existed. */
  vision?: boolean | null;
  /** What the model produces ("text", "embeddings", "image", ...), when the
   *  provider says. Absent means it did not say, and reads as a text model. */
  outputs?: string[];
  /** Per-model capabilities a provider states (Anthropic's models API today);
   *  only the keys the provider stated are present. */
  features?: {
    structured_output?: boolean;
    adaptive_thinking?: boolean;
    enabled_thinking?: boolean;
    /** False exactly when this model's thinking cannot be turned off. */
    disabled_thinking?: boolean;
    effort?: string[];
    max_tokens?: number;
  };
};
/** A connection's image override (#377): `""` follows the catalog. */
export type VisionOverride = "" | "on" | "off";

export type LLMConnectionKind = "openrouter" | "claude" | "openai_compatible" | "anthropic";
// `model` is what is STORED (what the connection editor edits); `effective_model`
// is what a generation on it will actually run — they differ for `claude`
// alone, which substitutes a default for an unset model. Any surface naming
// the model a connection will use wants the second (#77).
export type LLMConnection = {
  id: string; kind: LLMConnectionKind; name: string;
  base_url: string; model: string; effective_model: string;
  post_process: "none" | "strict";
  /** Whether an OpenAI-compatible endpoint takes top-k, min-p and repetition
   *  penalty; "" reads as standard. */
  sampler_support?: "" | "standard" | "extended";
  vision?: VisionOverride;
  /** Whether Keep writing sends a cut-short reply back as the start of the
   *  model's own turn (prefill) rather than asking it to continue. */
  prefill?: boolean;
  key_set: boolean; rev: string; health: ProviderHealth;
  /** The provider preset it was made from (`ProviderPreset.id`); "" for a
   *  record that names none, whose preset the server infers where it reads. */
  preset?: string;
  /** "" for a record that states none; its preset's billing then applies. */
  billing?: "" | ProviderBilling;
};
/** How a provider's calls are paid for (spec 4.1). */
export type ProviderBilling = "metered" | "subscription";
/** One place the stored settings name a provider (`settings.used_by`): a role,
 *  a role's fallback, or a route's chosen pin, at global or campaign scope. */
export type ProviderUse = {
  kind: "role" | "fallback" | "route"; key: string;
  scope: "global" | "campaign"; cid?: string;
};
export type LLMConnectionDetail = LLMConnection & {
  /** What names this provider, read on the detail only (it reads every
   *  campaign). Absent from an update's answer. */
  used_by?: ProviderUse[];
  models: Model[]; fetched_at: string;
  /** What this connection's OWN preset sends on it, and what it drops. */
  sampling?: SamplingReport | null;
  /** Which refresh ATTEMPT wrote the cached catalog (#398), or `""` for one
   *  written before the field existed or by a caller that named none. The only
   *  durable trace a `draft` leaves, and it exists for one question: a client
   *  whose run was reaped asks whether ITS refresh landed, which a timestamp
   *  cannot answer once a second tab can move it. */
  fetched_by?: string;
};
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
  sampler_support?: "" | "standard" | "extended";
  vision?: VisionOverride; prefill?: boolean;
  /** The provider preset it is made from (`ProviderPreset.id`); "" or absent
   *  names none, and the server infers one where it is read. */
  preset?: string;
  /** "" or absent takes the preset's billing. */
  billing?: "" | ProviderBilling;
  /** An edit that moves the Embedding role's vector space (a new key or
   *  address on the provider it embeds through) re-embeds the library, and is
   *  refused with 400 `confirm_embedding` without this. */
  confirm_embedding?: boolean;
};

/** A preset's provider-neutral reasoning effort (`llm_sampling.REASONING`).
 *  `max` is GLM's own level: sent to a GLM model only, unsupported elsewhere. */
export type ReasoningEffort = "off" | "low" | "medium" | "high" | "max";
/** The nine sampler parameters and the reasoning effort, as a preset stores
 *  them. Every one optional: an absent parameter is the backend's own
 *  default, never a zero. */
export type SamplerParams = {
  temperature?: number; top_p?: number; top_k?: number; min_p?: number;
  repetition_penalty?: number; frequency_penalty?: number; presence_penalty?: number;
  max_tokens?: number; stop?: string[]; reasoning_effort?: ReasoningEffort;
};
export type SamplerParamName = keyof SamplerParams;
/** One row of the server's parameter table (`llm_sampling.table()`): bounds
 *  for a number, limits for the stop strings, `choices` for a choice. */
export type SamplerParamSpec = {
  name: SamplerParamName; label: string; kind: "float" | "int" | "stop" | "choice";
  min?: number; max?: number; max_entries?: number; max_chars?: number;
  choices?: string[];
};
export type SamplerPreset = {
  id: string; name: string; params: SamplerParams; notes: string;
  /** Where an import came from (`"sillytavern"`), "" for a hand-made preset. */
  source: string;
};
export type SamplerPresetDraft = { name: string; params: SamplerParams; notes: string };
/** What a SillyTavern import did with each key of the file. */
export type SamplerImportReport = {
  mapped: { param: string; from: string; value: unknown }[];
  neutral: { param: string; from: string; value: unknown }[];
  skipped: { param: string; from: string; value: unknown; why: string }[];
  invalid: { key: string; why: string }[];
  unmapped: string[];
  notes: string[];
};
/** What one connection is sent from its resolved preset, and what not (the
 *  sampler-presets spec). `scope` is where the preset came from: a route at
 *  `campaign` or `global` scope, the `connection` itself, a reroll's own
 *  `override`, or `none`. A `preset_id` of "" with a route or override scope
 *  means that scope cleared it. */
export type SamplingReport = {
  preset_id: string; preset_name: string;
  scope: "campaign" | "global" | "connection" | "override" | "none" | "";
  kind: string;
  applied: SamplerParams;
  dropped: { param: string; reason: string }[];
  /** False only for an OpenRouter connection with no cached catalog: it sends
   *  everything and cannot say whether the model takes it. */
  verified: boolean;
};
/** What a role needs of a model (`capabilities.NEEDS`). */
export type CapabilityNeed = "generate" | "vision" | "embed" | "decide";
export type CapabilityName =
  | "generate" | "stream" | "vision" | "embed" | "decide_native"
  | "structured_output" | "prefill";
/** `adapter` outranks a passed `test`, then `user`, then a failed `test` (`unknown`, with its `error`), then `catalog`, `preset`, `name`. */
export type CapabilitySource =
  | "adapter" | "test" | "user" | "catalog" | "preset" | "name" | "unknown";
/** A failed test call is `unknown` from source `test`, never `no`, and carries
 *  the provider's `error`; no other answer has one. */
export type CapabilityValue = {
  value: "yes" | "no" | "unknown"; source: CapabilitySource; error?: string;
};
/** A catalog row in a capability group: the entry, what put it there
 *  (`reason`) and every capability with the source that said so. */
export type CapabilityModel = Model & {
  reason: string;
  capabilities: Record<CapabilityName, CapabilityValue>;
};
/** A provider preset (`capabilities.preset_body`): what a new provider is made
 *  from, and what its wire protocol always, possibly or never does. Not to be
 *  confused with a sampler preset, which is a `preset_id` everywhere else. */
export type ProviderPreset = {
  id: string; label: string; kind: LLMConnectionKind; base_url: string;
  url_locked: boolean; billing: "metered" | "subscription"; reports_price: boolean;
  always: CapabilityName[]; possible: CapabilityName[]; never: CapabilityName[];
};
/** `GET /api/providers/presets`: each preset, and whether its health check
 *  GENERATES (the Claude subscription) -- such a check is sent only with
 *  `confirm: true`, so a page offers it behind a button rather than running it. */
export type ProviderPresetOption = ProviderPreset & { generating_check: boolean };
/** `GET /api/llm-connections/{id}/capabilities`. Every catalog row, embedding
 *  models included. When the provider rules the need out for every model,
 *  `groups` and `hidden` are empty and `reason` says why; otherwise `reason`
 *  is `null`. */
export type ModelCapabilities = {
  /** The provider preset; the sampler preset is `preset_id` elsewhere. */
  provider_preset: ProviderPreset;
  need: CapabilityNeed;
  groups: { fits: CapabilityModel[]; unverified: CapabilityModel[] };
  hidden: { id: string; reason: string }[];
  reason: string | null;
};
/** One control of a sampler preset on a model, and why it is what it is. */
export type ControlPreview = {
  state: "supported" | "translated" | "unsupported" | "unknown" | "n/a";
  /** The wire field it is sent as; `null` when it is not sent. */
  wire: string | null;
  why: string; source: CapabilitySource;
};
/** `POST /api/inference/controls`: what a sampler preset sends on a provider's
 *  model — `llm_sampling.effective`. */
export type ControlsPreview = {
  requested: Record<string, unknown>;
  effective: Record<string, unknown>;
  controls: Record<string, ControlPreview>;
};
export type ModelsRefreshResult = { models: Model[]; fetched_at: string; rev: string };
/** What a connection's provider last actually did (#146).
 *
 *  `unknown` is not a third kind of failure: it means nothing has been
 *  observed yet — a fresh app, or a connection nothing has used. `kind` is the
 *  same vocabulary a generation failure carries, so the dot's tooltip and the
 *  scene's error say the same thing about the same connection. */
export type ProviderHealth = {
  state: "unknown" | "ok" | "error";
  kind: string;
  detail: string;
  at: string;
};
/** The answer to one on-demand check. `ok: false` is a successful request. */
export type HealthCheckResult = {
  ok: boolean; kind: string; detail: string; checked_at: string;
};

// ---- Inference settings: roles, routes, model facts, the test call ----
// The shapes of `store/inference/settings.py` (`view`, `write`) and of the
// facts and test routes in `routes/config.py`. Nothing here restates a rule
// behind them: what resolves, what inherits and what is refused all come back
// from the server.

/** The three roles a campaign may override; Embedding is the library's alone. */
export type GenerativeRole = "primary" | "fast" | "decision";
export type InferenceRole = GenerativeRole | "embedding";
/** A stored choice of provider, model and sampler preset; each part `""` when
 *  unset. A write replaces a named selection WHOLE, so a part sent as `""` is
 *  cleared. */
export type InferenceSelection = { provider: string; model: string; preset: string };
/** The Embedding role's choice: no preset, because an embedding samples nothing. */
export type EmbeddingSelection = { provider: string; model: string };
/** What a role or route resolves to (the view's `Sel`): the primary attempt
 *  the seam would send, with the names to show. `via` is `route` for a pin and
 *  `role` for a role's slot; `scope` is where the choice was read. */
export type ResolvedSelection = {
  provider: string; provider_name: string; model: string;
  preset: string; preset_name: string;
  via: "route" | "role" | ""; scope: "campaign" | "global" | "none";
};
/** Where the one-time move to the new settings layout stands
 *  (`migrate.status`). `newer` is a store a newer build has written: this one
 *  will not change its model settings, and play continues. */
export type MigrationStatus = {
  state: "done" | "pending" | "running" | "failed" | "newer";
  /** Why a `failed` upgrade stopped ("the safety backup failed: ..."), else "". */
  reason: string;
  /** What the upgrade could not reach yet (a busy campaign), each with why. */
  skipped: string[];
  /** Retirement's own account (inference slice I): what it has still to do,
   *  and why its last pass stopped short. Never moves `state`; not rendered. */
  retirement?: { left: string[]; failed: string };
};
/** One thing the old model settings could not carry over, shown on `/models`
 *  until dismissed (`GET /api/inference/settings`'s `retirement_notes`). Its
 *  `id` names what it is about, never its wording, so a dismissal holds on
 *  every device and through every later upgrade. */
export type RetiredNote = {
  id: string;
  /** `"global"`, or `"campaign:<id>"` for one campaign's settings. */
  scope: string;
  /** The campaign's name for a campaign scope; `""` for the global one. */
  scope_name: string;
  subject: string;
  provider_id: string;
  effort: string;
  kind: "route_preset" | "unrepresentable" | "fact_not_carried";
  /** The sentence to show, ending "— this was not carried over." */
  text: string;
};
/** What would price a role's or route's calls (spec 3.5): `pricing.rate_for_call`
 *  on the resolved selection, and where it came from. `none` is no rate at all
 *  (calls read unpriced; never drawn as $0); `native` is a native decision,
 *  which is never modelled. `entry` is per 1,000 tokens, as the ledger keeps it. */
export type RateInfo = {
  source: "provider" | "table" | "none" | "native";
  entry?: PricingEntry;
};
/** One generative role card. `inherits` is what this scope would run on with
 *  its own choice for the role cleared -- what "Same as ..." shows -- and
 *  `problem` is the seam's own refusal of `resolves` (no key, a known
 *  capability `no`), never a copy of it. */
export type RoleCard = {
  stored: InferenceSelection;
  fallback: InferenceSelection;
  resolves: ResolvedSelection | null;
  inherits: ResolvedSelection | null;
  problem: string | null;
  /** What the resolved fallback is KNOWN unable to do (spec 5.3), so it is
   *  never sent: the seam refuses nothing over it, so this is the only place
   *  it shows. Empty when the fallback is sent, or there is none. */
  fallback_missing: CapabilityName[];
  /** Why the stored fallback cannot send at all (no key, no base URL), so it
   *  is left out of the chain as silently; null when it can, or there is none. */
  fallback_problem: string | null;
  /** On the Decision card, the backend its model is answered by: `native`
   *  (the provider's decisions endpoint), `structured` (generation against a
   *  schema), or `""` when it can do neither and is refused. `""` on every
   *  other role. The server's resolution, never re-derived here. */
  decision_mode: DecisionMode;
  /** The resolved model's `decide_native` -- the capabilities the resolver
   *  decided on -- so a `structured` decision is worded without a read of
   *  its own. `no` only when it is KNOWN (a name-rule guess is `unknown`):
   *  the one value under which "no native decision API" may be said. */
  decides_natively: DecidesNatively;
  /** What would price this role's calls; null when nothing resolves. */
  rate: RateInfo | null;
};
/** Which backend answers a decision (`RoleCard.decision_mode`). */
export type DecisionMode = "native" | "structured" | "";
/** What is known of a resolved model's native decisions
 *  (`RoleCard.decides_natively`). */
export type DecidesNatively = "yes" | "no" | "unknown";
/** The Embedding role (global scope only). `on` is whether anything embeds;
 *  `problem` is null when it does, else the server's short reason it does not
 *  ("No provider chosen", "<provider> has no key set", ...). */
export type EmbeddingCard = {
  stored: EmbeddingSelection;
  resolves: ResolvedSelection | null;
  on: boolean;
  problem: string | null;
  /** As on `RoleCard`; null while the role is off. */
  rate: RateInfo | null;
};
/** What a route's `use` says: inherit (`""`), a generative role, or the
 *  route's own pin (`"model"`). */
export type RouteUse = "" | GenerativeRole | "model";
/** One routing row. `role` is the role that supplied what it resolves to;
 *  `null` for a pin, or for nothing at all. */
export type RouteRow = {
  key: string; label: string; hint: string; tasks: string[];
  operation: "generate" | "decide";
  default_role: GenerativeRole;
  /** What the serving model must also do (the image route's `vision`). */
  requires: CapabilityNeed[];
  campaign_scoped: boolean;
  use: RouteUse;
  pin: InferenceSelection;
  /** This scope's sampler preset for the route: "" inherits, and
   *  `preset_clear` stops inheriting. */
  preset: string;
  resolves: ResolvedSelection | null;
  inherits: ResolvedSelection | null;
  problem: string | null;
  /** As on `RoleCard`: what the route's fallback is known unable to do. */
  fallback_missing: CapabilityName[];
  /** As on `RoleCard`: why the route's fallback cannot send at all. */
  fallback_problem: string | null;
  /** As on `RoleCard`, for a decide route; `""` on a generate one. */
  decision_mode: DecisionMode;
  /** As on `RoleCard`. */
  decides_natively: DecidesNatively;
  role: GenerativeRole | null;
  /** The role the route walks (its own `use`, else its default), whichever
   *  role ends up supplying it -- a route can use Decision while Decision
   *  inherits Fast. `null` for a pin. */
  uses: GenerativeRole | null;
  /** As on `RoleCard`. */
  rate: RateInfo | null;
};
/** A provider as the settings view lists it, with whether it can send at all. */
export type InferenceProvider = {
  id: string; name: string;
  /** `""` for a record that names no kind: the view reads it as
   *  `str(kind or "")` rather than inferring one. */
  kind: LLMConnectionKind | "";
  /** The provider preset it was made from, or the one inferred for it. */
  preset: string;
  usable: boolean;
  /** Why it cannot send (the seam's own sentence); null exactly when usable. */
  problem: string | null;
  /** The model the provider's own record names ("" for none): what a reroll
   *  naming the provider alone runs while the store is at format 1. At
   *  format 2 a provider has no model of its own and this is not used. */
  own_model?: string;
};
/** `GET /api/inference/settings` and `GET /api/campaigns/{cid}/inference`. A
 *  campaign's view has no Embedding card and only the campaign-scoped routes. */
export type InferenceSettings = {
  /** The store's settings layout; "1" is the legacy one. */
  format: string;
  newer: boolean;
  migration: MigrationStatus;
  roles: Record<GenerativeRole, RoleCard> & { embedding?: EmbeddingCard };
  routes: RouteRow[];
  providers: InferenceProvider[];
  presets: { id: string; name: string }[];
  /** The sampler-preset sentinel meaning "no preset at this scope". */
  preset_clear: string;
  /** What the old model settings could not carry over, not yet dismissed:
   *  every scope's on the library's view, the global ones and the campaign's
   *  own on a campaign's. */
  retirement_notes: RetiredNote[];
};
/** `sampler_presets.PRESET_CLEAR` byte for byte (U+2063 + "none"): a route's
 *  "no preset at this scope", which stops it inheriting one. "" is the other
 *  answer, "no opinion here". The views carry it too (`preset_clear`). */
export const PRESET_CLEAR = "⁣none";
/** A route's part of a write: only the fields named change. */
export type RouteWrite = { use?: RouteUse; pin?: InferenceSelection; preset?: string };
/** `PUT /api/campaigns/{cid}/inference`'s body. A named selection is replaced
 *  whole; a role, route or field the body does not name is left alone. */
export type CampaignInferenceWrite = {
  roles?: Partial<Record<GenerativeRole, { selection?: InferenceSelection;
                                           fallback?: InferenceSelection }>>;
  routes?: Record<string, RouteWrite>;
  presets?: Record<string, string>;
};
/** `PUT /api/inference/settings`'s body: a campaign's, plus the Embedding role
 *  (no preset, no fallback). */
export type InferenceWrite = Omit<CampaignInferenceWrite, "roles"> & {
  roles?: CampaignInferenceWrite["roles"] & { embedding?: { selection?: EmbeddingSelection } };
};

/** One recorded test result, kept only for the provider's current `rev`. */
export type VerifiedResult = { ok: boolean; at?: string; error?: string; dims?: number };
/** `GET /api/llm-connections/{id}/facts`: what the user said about one model
 *  on one provider, what a test call found, and every capability as it
 *  resolves with them. */
export type ModelFacts = {
  provider: string; model: string;
  /** The post-image preference: `off` stops post images and asserts nothing. */
  vision: VisionOverride;
  prefill: boolean | null;
  post_process: "" | "none" | "strict";
  /** The model's own per-token rates on this provider (slice E), used before
   *  the pricing table; `null` when none are stated. A stated `0` is a price. */
  rates: PricingEntry | null;
  verified: Partial<Record<CapabilityName, VerifiedResult>>;
  overrides: Partial<Record<CapabilityName, "yes" | "no">>;
  capabilities: Record<CapabilityName, CapabilityValue>;
  /** The model's size as the user STATED it (01i): what the form edits.
   *  `null` when not stated. */
  context_window: number | null;
  max_output: number | null;
  /** The model's size as a call would use it: the stated value, else the
   *  catalog's, else unknown -- each with the catalog's own figure beside it. */
  limits: { window: ModelLimit; max_output: ModelLimit };
  /** The facts file exists and could not be read: nothing above is what the
   *  user said, and a save is refused, so none is offered. */
  unreadable?: boolean;
  /** Why, when `unreadable`: `held` -- another program has the file (a save
   *  answers 503, try again) -- or `mangled` -- it does not parse, which a
   *  person has to fix (a save answers 409 `facts_unreadable`). */
  unreadable_reason?: "held" | "mangled";
};
/** Where a model's size came from (01i): the user's word, the provider's
 *  catalog, or nowhere. */
export type LimitSource = "user" | "catalog" | "unknown";
/** One size fact: a positive token count, `null` exactly when `unknown`.
 *  `catalog` (the facts panel only) is the listing's own figure. */
export type ModelLimit = { value: number | null; source: LimitSource; catalog?: number | null };
/** `PUT /api/llm-connections/{id}/facts`. A field left out is left as it is;
 *  an override of `""` removes that override. */
export type ModelFactsUpdate = {
  model: string;
  vision?: VisionOverride;
  prefill?: boolean;
  post_process?: "none" | "strict";
  overrides?: Partial<Record<CapabilityName, "" | "yes" | "no">>;
  /** A save that turns the Embedding role on (it embeds the library) is
   *  refused with 400 `confirm_embedding` without this. */
  confirm_embedding?: boolean;
  /** The model's own rates: both base rates, no unknown field, and an unset
   *  rate LEFT OUT (a present `null` is refused); `{}` clears them. */
  rates?: PricingEntry | Record<string, never>;
  /** The model's size on this provider (01i): a positive whole number sets
   *  one, `0` removes it. A max output above the window is refused. */
  context_window?: number;
  max_output?: number;
};
/** The capabilities a test call has a probe for (`probes.PROBES`). */
export type TestableCapability = "generate" | "vision" | "embed" | "decide_native";
/** `POST /api/llm-connections/{id}/test/preview`: what a test would send.
 *  `estimated_cost_usd` is `null` when no source (catalog, rates) states a price; 0 is only
 *  ever a stated free model. */
export type ModelTestPreview = {
  provider: string; provider_id: string; model: string;
  sends: { capability: TestableCapability; description: string }[];
  estimated_cost_usd: number | null;
  /** Which source priced it: the provider's `catalog`, or the user's `rates`
   *  (the model's own, else `pricing.json`). `null` with a null estimate. */
  estimate_basis: "catalog" | "rates" | null;
};
/** One probe's outcome. `kind` is `not_sent` for a probe skipped after an
 *  earlier failure that answered for it too. */
export type ModelTestProbe = { ok: boolean; kind?: string; error?: string; dims?: number };
/** What a landed test run holds. `recorded` is whether any verdict was filed
 *  -- none is when nothing answered for the model, or the provider was edited
 *  while the probes were out. */
export type ModelTestResult = {
  provider: string; model: string; rev: string;
  results: Partial<Record<TestableCapability, ModelTestProbe>>;
  recorded: boolean;
};
/** One generative role as `GET /config` names it: the provider's name, the
 *  model it runs and the preset's name ("" for none, or one that is gone). */
export type RoleSummary = { provider_name: string; model: string; preset_name: string };
/** `GET /config`'s roles at a glance (`store/inference/settings.summary`).
 *  The cascade's answer, so a role that selects nothing of its own already
 *  names what it inherits; `null` is a role nothing at all selects. Pure:
 *  it resolves nothing and refuses nothing. */
export type InferenceSummary = {
  roles: Record<GenerativeRole, RoleSummary | null>;
  /** Whether anything embeds -- the backend's own resolve, the one gate every
   *  embedder shares (recall, the art catalogue, search by meaning, the
   *  continuity checks). Recall depth is not part of it. */
  embedding_on: boolean;
};
export type Config = {
  theme: string; system_prompt: string;
  quote_color: string; user_label: string; assistant_label: string;
  active_connection_id: string;
  active_connection: ActiveConnection | null;
  ready: boolean;
  inference: InferenceSummary;
  /** What the active connection's provider last did (#146), or null when there
   *  is no active connection. Read from the server's registry — no network
   *  call to the provider happens on a config read. */
  health: ProviderHealth | null;
  /** Seconds of silence before an LLM call is abandoned; "0" disables. */
  llm_timeout: string;
  /** Seconds one absorb's whole LLM sequence may take; "0" disables. */
  absorb_budget: string;
  /** Seconds one non-streaming LLM call may take in total; "0" disables. */
  llm_call_budget: string;
  /** Re-attempts a transiently-failed generation gets before its connection is
   *  given up on (#144); "0" is the old one-attempt behaviour. */
  llm_retries: string;
  context_budget: string;
  /** Longest anchor text the prompt and the drift judge see, in CODE
   *  POINTS. Server-owned so the editor's warning cannot drift from the
   *  backend's truncation; count it with [...text].length, not .length. */
  voice_anchor_cap?: number;
  /** Recent transcript messages every keyword scan reads — world info, chronicle
   *  recall, keyed mechanics rules and the semantic-recall query all share this
   *  window. "0" empties it; a scene opener's prompt and a director's note seed
   *  activation themselves either way. */
  context_scan_depth: string;
  /** Levels of world-info recursion a turn runs: entries pulled in by the text
   *  of entries already activated. "0" (the default) is off; at most "3". The
   *  server reports the effective value, so a hand-edited "9" reads as "3". */
  lore_recursion_depth: string;
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
  /** Entries a similarity pass may add on top of the keyword ones; "0" = recall
   *  off. It turns off recall only, never the other embedders. */
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
  /** The GLOBAL scene-tracker switch, "on" | "off"; a campaign may override it
   *  (`getCampaignTracker`). */
  tracker: string;
  /** "on" adds the perception rider to the prompt; "off" keeps the tracker's
   *  record without that prompt cost. */
  perception_rider: string;
  /** "on" sends post images to a model that can read them (#377). */
  send_images: string;
  /** How many of the newest images in the sent history go out. */
  send_images_limit: string;
  /** Whether images would reach the connection the global chat route uses:
   *  "off" (setting off), "yes", "no" (text only) or "unknown" (the catalog
   *  does not say). Read-only. */
  send_images_reach?: SendImagesReach;
};
export type SendImagesReach = "off" | "yes" | "no" | "unknown" | "none";
/**
 * The subset of Config the Configuration page writes — the mirror of the
 * backend's `ConfigUpdate`. Named rather than inlined at each call site: it
 * was spelled out three times, and the copies drifted the moment a field was
 * added.
 */
export type ConfigUpdate = Partial<Pick<Config,
  "theme" | "system_prompt" | "quote_color" | "user_label" | "assistant_label" |
  "llm_timeout" | "absorb_budget" | "llm_call_budget" |
  "llm_retries" |
  "context_budget" | "context_scan_depth" | "lore_recursion_depth" | "archive_depth" |
  "setup_done" | "prompt_log_depth" |
  "rolling_summary_every" |
  "scene_break_every" |
  "offscene_known_limit" |
  "semantic_recall_depth" | "semantic_recall_threshold" |
  "prompt_layout_enabled" | "speaker_turn_taking" |
  "backup_enabled" | "backup_interval_hours" | "backup_keep" | "backup_dir" |
  "replay_fork_threshold" | "advance_fork_threshold" | "log_level" |
  "tracker" | "perception_rider" | "send_images" | "send_images_limit">>;
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
export type BackupList = { dir: string; backups: BackupEntry[]; image_backups: BackupEntry[] };
/** A `POST /api/backups`: the refreshed listing, plus what that call did.
 *  `retention_error` is set when the archive was written but the sweep that
 *  follows it could not run — a success with a problem attached, which is a
 *  third outcome the two-state ok/failed shape could not say. */
export type BackupRun = BackupList & {
  created: string;
  swept: string[];
  retention_error: string | null;
};
export type ImageBackupRun =
  | (BackupList & { created: string; listing_error: null })
  | { dir: string; created: string; listing_error: string };
export type DataDirInfo = {
  data_dir: string;
  default: string;
  is_default: boolean;
  source: "env" | "custom" | "default";
  exists: boolean;
};
/** What an image-store migration reports, planned or done (stage 4, spec §11).
 *  A dry run and a real run share this shape: `dry_run` says which, and the
 *  "what it did" counters are zero in a dry run. `outcome` is how the pass
 *  ended; `state` is only present on the minimal report a pass that raised
 *  leaves behind. */
export type ImageMigrationReport = {
  kind: "image-migration";
  run_id?: string;
  mode?: "plan" | "migrate";
  dry_run: boolean;
  outcome: "done" | "cancelled" | "failed" | "root-changed";
  state?: string;
  error?: string | null;
  stopped_at?: string | null;
  legacy_files: number;
  unique_images: number;
  exact_duplicates: number;
  pixel_variants: number;
  bytes_before: number;
  bytes_after: number;
  /** The plan's figure on a dry run; on a real run, what it actually freed. */
  bytes_reclaimed: number;
  description_conflicts: number;
  placed: number;
  already_placed: number;
  legacy_deleted: number;
  /** Files left where they were, and why (`path` is store-relative). */
  untouched: { path: string; reason: string }[];
  skipped: { reason?: string; path?: string }[];
  errors: { reason?: string; path?: string; error?: string }[];
  format1_collections: unknown[];
  collections_converted: unknown[];
  collections_kept: unknown[];
};
/** One image the collector would delete: `blob` is the blob key, `bytes` what
 *  deleting it frees (the blob only counts when every holder goes too). */
export type ImageGcRow = { id: string | null; blob: string; bytes: number };
/** An unreachable image that is not collectable yet. `collectable_at` is epoch
 *  seconds, `null` when no date can be promised (unreadable sidecars). */
export type ImageGcProtected = {
  id: string | null; blob: string | null; why: string; collectable_at: number | null;
};
export type ImageGcBlocker = { path: string; reason: string };
/** One file the collector removed: the object it belonged to, its blob key, its
 *  size. `id` is null for an orphan blob, which no sidecar named. */
export interface ImageGcDeletedRow { id: string | null; blob: string; bytes: number }

/** What the collector reports. `mode` `scan` is the dry run; `collect` the
 *  deletion. `token` is single-use and only present when something is
 *  collectable and nothing blocked the walk. Times are epoch seconds. */
export type ImageGcReport = {
  kind: "image-gc";
  mode: "scan" | "collect";
  run_id: string;
  state: "complete" | "blocked" | "cancelled" | "refused" | "root-changed" | "failed"
    | "running";
  grace_days: number | null;
  counts: {
    placements: number; objects: number; blobs: number; unreferenced: number;
    collectable: number; collectable_blobs: number; protected: number;
  };
  collectable: ImageGcRow[];
  collectable_blobs: ImageGcRow[];
  protected: ImageGcProtected[];
  reclaimable_bytes: number;
  blocking: ImageGcBlocker[];
  unreadable_sidecars: string[];
  clock_skew: { id: string | null; blob: string | null; what: string }[];
  token: string | null;
  token_expires_at: number | null;
  /** One row per file removed, appended as each unlink lands; `bytes` is the total. */
  deleted: { objects: ImageGcDeletedRow[]; blobs: ImageGcDeletedRow[]; bytes: number };
  skipped: { id: string | null; blob: string | null; reason: string }[];
  kept_blobs: { id: string; blob: string; reason: string }[];
  /** Reachable ids with no readable sidecar yet: orphan blobs are kept. */
  unarrived_objects: string[];
  /** Thumbnails or index entries a collection could not remove (best effort). */
  cache_cleanup: { path: string; reason: string }[];
  error: string | null;
};
export type ImageMaintenanceReport = ImageMigrationReport | ImageGcReport;
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

// entities
// The kinds THIS BUILD knows about, and the union everything else types
// against. The import dialogs' Category dropdown is this list intersected with
// `GET /api/entity-kinds` (see `components/useEntityKinds.ts`), so a kind
// added to `store.entities.ENTITY_KINDS` reaches it without either dialog
// being edited (#138) — once this list learns the kind, which
// `test_the_frontend_ships_the_same_kind_list` requires. The reclassify
// picker enumerates the array too, so a kind present in the union but
// missing from the list is simply unofferable, with nothing to say so. It has to stay the
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

// ---- typed entity fields (#37, #222) ----
//
// Mirrors backend/src/grimoire/store/entity_schema.py — keep in sync.

/** Every kind of record a `ref` field may name: the five entity kinds plus the
 *  two actor kinds. Mirrors `entity_schema.REF_KINDS`. */
export type RefKind = EntityKind | "characters" | "pcs";

/** A `choice` whose options are only known at runtime names the list it
 *  draws from; `EntityEditor` resolves each through the API. Mirrors
 *  `entity_schema.OPTION_SOURCES`. */
export type OptionSource = "climates";

/** One typed field on an entity kind. Mirrors store/entity_schema.py FIELDS,
 *  key for key: the spec is the whole constraint (#221), the save boundary
 *  refuses what a spec does not admit and the form renders the control the
 *  spec implies, so a bound or an option list declared here and not there
 *  (or the reverse) is a picker offering what the server rejects.
 *
 *  A `ref` field names other records in the `<kind>:<id>` spelling `owners:`
 *  uses — one, or a comma-separated list when `multi`. `kinds` is what makes
 *  it a picker rather than a text box: the editor offers exactly those kinds'
 *  records, and the backend refuses anything else at the save boundary. */
export type EntityFieldSpec = {
  key: string;
  label: string;
  widget: "text" | "ref" | "number" | "choice";
  /** `ref`: the kinds it may name, and whether it holds a list. */
  kinds?: readonly RefKind[];
  multi?: boolean;
  /** `number`: inclusive bounds, each optional. */
  min?: number;
  max?: number;
  /** `choice`: exactly one of a literal option list or a named source. */
  options?: readonly string[];
  source?: OptionSource;
};

export const ENTITY_FIELDS: Record<EntityKind, EntityFieldSpec[]> = {
  locations: [
    { key: "climate", label: "Climate", widget: "choice", source: "climates" },
    { key: "persistence", label: "Weather persistence", widget: "number", min: 0, max: 1 },
    { key: "weather_zone", label: "Weather zone", widget: "text" },
  ],
  lore: [],
  items: [
    { key: "item_type", label: "Type", widget: "text" },
    { key: "rarity", label: "Rarity", widget: "text" },
    { key: "holder", label: "Held by", widget: "ref",
      kinds: ["characters", "pcs", "groups", "locations"] },
  ],
  groups: [
    { key: "group_type", label: "Type", widget: "text" },
    { key: "leader", label: "Leader", widget: "ref", kinds: ["characters", "pcs"] },
    { key: "headquarters", label: "Headquarters", widget: "ref", kinds: ["locations"] },
  ],
  creatures: [
    { key: "creature_type", label: "Type", widget: "text" },
    { key: "threat", label: "Threat", widget: "text" },
    { key: "habitat", label: "Habitat", widget: "ref", kinds: ["locations"], multi: true },
  ],
};

/** One activation control on a world-info entry. Mirrors
 *  `store.lore_fields.FIELD_KEYS`, in the same order, and its bounds and enums:
 *  a bound declared here and not there is an input offering what the save
 *  boundary refuses with a 400. Every kind carries them, so they live beside
 *  `ENTITY_FIELDS` rather than inside any one kind's list.
 *
 *  Values are flat strings like every other frontmatter field. `bool` is the
 *  string `"true"`, and its off state is the BLANK string -- the backend takes
 *  no `"false"` -- which is also how any field is cleared. `refs` is a comma
 *  list of `characters:<id>` / `pcs:<id>`. */
export const ACTIVATION_FIELDS: {
  key: string;
  label: string;
  widget: "text" | "number" | "choice" | "bool" | "refs";
  min?: number;
  max?: number;
  options?: string[];
}[] = [
  { key: "secondary_keys", label: "Secondary keys", widget: "text" },
  { key: "key_logic", label: "Key logic", widget: "choice",
    options: ["and_any", "and_all", "not_any", "not_all"] },
  { key: "scan_depth", label: "Scan depth", widget: "number", min: 0, max: 100 },
  { key: "sticky", label: "Sticky", widget: "number", min: 0, max: 50 },
  { key: "cooldown", label: "Cooldown", widget: "number", min: 0, max: 50 },
  { key: "priority", label: "Priority", widget: "number", min: 0, max: 1000 },
  { key: "keep", label: "Keep under budget", widget: "bool" },
  { key: "recursion", label: "Recursion", widget: "choice",
    options: ["both", "pulled_only", "pulls_only", "none"] },
  { key: "known_by", label: "Known by", widget: "refs" },
];

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
  greeting_count?: number; tagline?: string;
  /** Whether a world-level voice anchor exists. OPTIONAL, so a response or
   *  fixture predating it reads as unknown rather than as "no anchor" —
   *  the backlog filters on `=== false`, never on falsiness. */
  has_voice_anchor?: boolean;
  versions: VersionRef[];
};
/** A world version of a character the campaign no longer holds (a pick purged
 *  it), passed through to the campaign page beside the version it shows.
 *  Read-only there: the pictures are the world's and so are the descriptions.
 *  Campaign reads only; a world read never carries them, and the list is empty
 *  when the campaign cannot see the world's (detached or deleted record, world
 *  gone) or holds every version that has art. Default version first. */
export type BaseVersion = {
  id: string; name: string; images: string[];
  image_v: Record<string, string>;
  /** Image identity per image name, only for images the image store holds;
   *  a legacy file has no entry. */
  image_ids?: Record<string, string>;
  image_descriptions: Record<string, string>;
};
/** The world's copy of a picture that a campaign file of the same name hides
 *  from the version's `images`. Read-only, and served from the WORLD route: the
 *  campaign route resolves that name to the campaign's own file. */
export type ShadowedImage = {
  name: string; v: string; description?: string;
  /** The world copy's identity in the image store; absent for a legacy file. */
  image_id?: string | null;
};
export type CharacterDetail = {
  meta: { id: string; name: string; default_version: string; birthdate?: string };
  base_versions?: BaseVersion[];
  versions: { id: string; name: string; card: Card; images?: string[];
              /** Per-image cache token, keyed by the names in `images`. */
              image_v?: Record<string, string>;
              /** Image identity keyed by the names in `images`, present only
               *  for images the image store holds (a legacy file has none). */
              image_ids?: Record<string, string>;
              /** Campaign reads only: the names in `images` the campaign holds
               *  no file for -- the world's, read through the overlay. */
              inherited?: string[];
              /** Campaign reads only: see `ShadowedImage`. */
              world_shadowed?: ShadowedImage[];
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
export type Persona = {
  name: string; pronouns: string; summary: string; description: string; birthdate?: string;
  /** Profile fields (#65). Optional because a persona written before they
   *  existed reads them as "", and older payloads may omit them entirely. */
  goals?: string; player_notes?: string;
};
/** One earlier text of a PC version (#67). `saved` is when that text was
 *  replaced; `name` is the persona name it held. Newest first in a listing. */
export type PCRevision = { id: string; saved: string; name: string };
export type PCSummary = {
  id: string; name: string; tags: string[]; default_version: string; versions: VersionRef[];
  // Same derived image fields a CharacterSummary carries, bar `localized_count`
  // — only a character card's text is localized, so a PC has no `embed-` images.
  has_avatar?: boolean; avatar_focus?: number | null; gallery_count?: number;
  /** See `CharacterSummary.avatar_v`: the avatar's bytes, spent as `?v=`. */
  avatar_v?: string | null;
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
/** The composition overview (#71): one row per ref the campaign holds against
 *  the library — every sync.md ref plus every version-locked actor — with the
 *  state derived from the same world/base/mine hash comparison the sync engine
 *  runs on. Unlike `IncomingStatus` there is no `new` (a ref is only listed
 *  once the campaign holds it) and there IS `diverged`/`insync`, the two
 *  states `/incoming` never reports. */
export type CompositionState = "conflict" | "update" | "diverged" | "insync";
/** The appearance record's version lock, when the actor has one — a different
 *  system from the sync ref (its upgrade verb is `import_version`, not
 *  accept), which is why it rides beside the state instead of inside it. */
export type CompositionLock = { version: string; role: string; scenes: string[] };
export type CompositionRow = {
  ref: IncomingRef; name: string; state: CompositionState;
  /** Pinned against the sync engine: `/incoming` stops offering this ref and
   *  accept/reject ignore it, while `state` still says what the pin holds off. */
  pinned: boolean;
  lock: CompositionLock | null;
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
export type Availability = {
  id: string; name: string; available: boolean; reasons: string[]; unlocked: boolean;
  pcless?: boolean;
  /** The greeting's location id, "" for none — what `greetingDraft` pre-fills
   *  the confirm form's location picker from (#218). */
  location?: string;
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
/** `dropped` sections were rendered but left out of the prompt by the budget
 *  packer; they still carry their text so the inspector can show what was cut.
 *  `trimmed` is how many history messages the packer dropped from the front —
 *  0 on every section except Conversation history. */
/** How an owned entry's owner is present (spec §7): `via` is the ref the
 *  presence came through (a holder, a leader, a location), null when there is
 *  none to name. */
export type OwnerPresence = {
  type: "cast" | "current_location" | "held_by" | "led_by" | "headquarters" | "habitat" | "activated";
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
  /** World info and Recalled lore rows only: each entry, why it is in, and
   *  whether the packer shed it (spec §10). Optional: a capture recorded
   *  before this existed has none, and is rendered by this same component. */
  entries?: LoreEntryRow[];
  /** Display names for every ref an entry's reason mentions. */
  names?: Record<string, string>;
  /** World info only: entries held back (cooldown). */
  held_back?: HeldEntryRow[];
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
/** How a breakdown's token counts were made (`store.tokens.counting`).
 *  `tokenizer` is a tiktoken encoding name (`cl100k_base`), `heuristic` (the
 *  characters/4 fallback) or `mixed` (some strings fell back). `native` only when the tokenizer that counted is the
 *  model's own; for most backends it is not, and the counts are estimates.
 *  Even native counts are text only — the provider adds per-message framing. */
export type TokenCounting = { tokenizer: string; native: boolean };
export type SceneContext = {
  model: string; total_tokens: number; dropped_tokens: number;
  budget_tokens: number; sections: ContextSection[];
  /** Absent on a snapshot frozen before it existed — read as an estimate. */
  token_count?: TokenCounting;
  /** The sampler preset this turn is (or was) sent with. Absent on a snapshot
   *  frozen before presets existed. */
  sampling?: SamplingReport | null;
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
      | "continuity-identity" | "continuity-reconcile";
  /** `"decide"` on a decision capture, which is kept in a retention pool of
   *  its own; absent on a generation, and on a speaker pick filed before
   *  decisions had an operation (`isDecision` covers both). */
  operation?: "decide";
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
  /** As `RosterEntry.avatar_v`: the drawer portrait's `?v=`. */
  avatar_v?: string | null;
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
  /** As `RosterEntry.avatar_v`: the dossier portrait's `?v=`. */
  avatar_v?: string | null;
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
  /** Present on a proposed-new plot thread or commitment the existing-record
   *  check examined (spec §10.3), and only there: a row with no plausible
   *  stored neighbour carries none. Optional because reviews stored before the
   *  check existed lack it. */
  identity_check?: IdentityCheck;
};
/** Why a stored record was offered as a possible match, as the server measured
 *  it (`continuity.similarity.lexical`, plus the clause that admitted it). */
export type IdentitySignals = {
  title_equal: boolean; slug_equal: boolean;
  /** Token and character-3-gram Jaccard over the identity texts. */
  tokens: number; chars: number;
  /** Null when semantic matching was off or either text had no vector. */
  cosine: number | null;
  actors: string[]; scenes: string[]; anchors: string[];
  via?: "lexical" | "structural" | "semantic" | null;
};
/** One stored record the proposed row might be. `ref` is a prefixed canonical
 *  ref (`thread:<id>` / `commitment:<id>`), never a bare id. */
export type IdentityCandidate = {
  ref: string; title: string; status: string; latest_beat: string;
  signals: IdentitySignals;
};
export type IdentityCheck = {
  /** The resolver's word, or `unchecked` when it gave none for this row. */
  decision: "existing" | "new" | "uncertain" | "unchecked";
  /** `downgraded`: an `existing` the acceptance guard refused (closed, already
   *  moved, never offered). `hint_only`: the check never answered. */
  status: "accepted" | "downgraded" | "hint_only";
  reason: string;
  proposed: { title: string; why_new: string; distinguished_from: string[] };
  candidates: IdentityCandidate[];
  /** Complete staged rows the reviewer may swap this one for, each with its
   *  own server-computed `before`. Client-side only once staged: the save
   *  strips them (`editRows.wireEdit`). */
  alternatives?: StagedEdit[];
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
  /** The subset of `flagged` whose verdict came without a note, because the
   *  Decision model answers natively and a native answer has no rationale.
   *  Shown, but nothing is stored for them. Optional: reviews stored before
   *  this existed lack it. */
  noteless?: string[];
  failed: DossierFailure[];
  /** Anchored NPCs the absorb budget ran out before reaching — never attempted. */
  skipped: string[];
};
/** One row per LLM-backed step of a single absorb, in run order. A projection of
 *  `mechanics`/`dossiers`/`voice` (never a second source of truth) that also covers
 *  the extraction, so a run cut short by the time budget is legible as one instead
 *  of looking like a model with nothing to suggest. */
export type AbsorbPhase = PhaseAttempt & {
  name: "extraction" | "identity" | "dossiers" | "voice" | "audit";
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
};
/** The existing-record check's phase block (spec §10.4). `matching` says where
 *  the possible-match lists came from; `counts` is flat and text-free.
 *  `fallback` is why semantic matching, though configured, did not stand this
 *  run (empty when it did) -- kept apart from `reason`, which a partial or
 *  failed resolver owns. Absent on reviews stored before it existed. */
export type IdentityPhase = PhaseAttempt & {
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
  matching: "basic" | "semantic";
  fallback?: string;
  counts: Record<string, number>;
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
  /** Absent on reviews stored before the existing-record check existed. */
  identity?: IdentityPhase;
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
/** The story-pressure vocabulary (capstone §16, §20). Each union is held to
 *  its Python tuple by `backend/tests/test_suggest_controls.py`, which parses
 *  this file -- so each stays a plain union of string literals. */
export type DriverKind = "thread" | "commitment" | "event" | "birthday" | "holiday";
export type DriverAction =
  | "advance" | "close_candidate" | "address" | "fulfill_candidate" | "break_candidate"
  | "expire_candidate" | "anchor";
/** Declared in the chooser's display order (`pressure.SORT_ORDER`). */
export type PressureState =
  "overdue" | "today" | "due_soon" | "upcoming" | "passed" | "stale" | "ok";
export type TimeMode = "auto" | "near" | "move" | "anchor";
export type AnchorRelation = "before" | "on" | "after" | "by";
/** What the reader set on one driver. Not a server vocabulary: the request
 *  carries it as three ref lists. */
export type DriverControl = "normal" | "focus" | "avoid" | "must";
/** A reviewed link on a driver; `relation` is the story graph's `LinkRelation`
 *  (the server sends only `effective.LINK_RELATIONS` values here). */
export type DriverLink = { id: string; relation: LinkRelation; other: string;
                           direction: "out" | "in" | "both" };
/** One row of `GET /continuity/drivers`. */
export type Driver = {
  ref: string; kind: DriverKind; label: string; summary: string; actors: string[];
  status: string;
  pressure: { state: PressureState; in_days: number | null; friendly: string };
  time_anchors: string[]; links: DriverLink[];
};
/** A dated temporal item a batch may be anchored to. A month-only birthday has
 *  no `fixed` day, so it takes only the `on` relation. */
export type AnchorOption = {
  ref: string; kind: "event" | "birthday" | "holiday"; label: string; native: string;
  friendly: string; fixed: number | null; in_days: number | null;
  precision: "exact" | "yearless" | "month";
};
export type DriversSnapshot = {
  now: string; friendly: string; fixed: number | null; matching: "basic" | "semantic";
  drivers: Driver[]; anchors: AnchorOption[];
};
/** A driver a generated card claims to serve, as the server validated it. */
export type SuggestionDriver = { ref: string; kind: DriverKind; action: DriverAction;
                                 label: string };
export type SuggestionAnchor = { ref: string; kind: DriverKind; relation: AnchorRelation;
                                 label: string; friendly: string; in_days: number | null };
export type RefLabel = { ref: string; label: string };
/** A generated card. The provenance fields are optional: a reply from before
 *  them, or a fixture, has none, and reads as claiming nothing (§27). */
export type SceneSuggestion = {
  title: string; premise: string; date?: string;
  cast: { kind: string; id: string; name: string }[];
  location: { id: string; name: string } | null;
  drivers?: SuggestionDriver[];
  time_anchor?: SuggestionAnchor | null;
  date_friendly?: string;
  in_days?: number | null;
  date_rejected?: boolean;
  /** Which rule blanked the date: the card's anchor, or the `near`/`move`
   *  time setting -- which can refuse an anchored card's derived date too.
   *  Null when nothing did; absent on a reply from before it (§15.2). */
  date_rejected_by?: "anchor" | "time" | null;
  unmet_must?: RefLabel[];
  avoided?: RefLabel[];
};
/** The body of `POST /scene-suggestions`. Every field defaults in
 *  `api.sceneSuggestions`, so a caller names only what it sets. */
export type SceneSuggestionsOptions = {
  after?: string; offscreen?: boolean; direction?: string; rank?: boolean;
  focus_refs?: string[]; avoid_refs?: string[]; must_refs?: string[];
  time_mode?: TimeMode; time_anchor_ref?: string;
  time_anchor_relation?: AnchorRelation | "";
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
  /** Derived on read (§17): what the idea was about and whether it still is.
   *  Absent on a composed greeting and on an older server. */
  drivers?: (SuggestionDriver & { state: "live" | "finished" })[];
  /** No `in_days`: the read derives the anchor's state, not its distance. */
  time_anchor?: (Omit<SuggestionAnchor, "in_days">
                 & { native: string; state: "live" | "finished" }) | null;
  stale_reason?: string;
  anchor_date?: string;
};
export type SceneIdeaDraft = {
  title?: string; premise?: string; cast?: string[]; location?: string;
  date?: string; pcless?: boolean; source?: "llm" | "user";
  drivers?: { ref: string; action: DriverAction }[];
  time_anchor?: { ref: string; relation: AnchorRelation };
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
/** What starting a replay answers: the session, the cut's cascade report and,
 *  for a replay begun inside a branch, the sibling it runs in. */
export type ReplayStarted = ReplaySession & { cascade: CascadeReport; branched?: string };
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
/** A record merged into a ledger row's canonical (capstone §12.5): it is not a
 *  row of its own, so its canonical names it. */
export type LedgerAlias = { ref: string; title: string; status: string };
export type PlotThread = {
  id: string; title: string; status: string;
  last_scene: string; latest_beat: string; scene: LedgerScene;
  /** Present on ledger and digest rows (#103); the digest's own type restates
   *  it as required, since every row there is aged. */
  aging?: Aging;
  /** What was merged into this row. Present on ledger and digest rows (empty
   *  when nothing was); optional because other rows of this shape omit it. */
  aliases?: LedgerAlias[];
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
/** One applied relationship delta (#63): how a standing on the ledger got
 *  where it is.
 *
 *  `LedgerRelationship` is the current-value view — what `relationships.json`
 *  holds now — and this is the append-only account behind it, newest first. The
 *  pair is unjoined and ORDERED for a feeling (`a` feels toward `b`) and
 *  unordered for a bond, the same asymmetry the store's two key formats carry;
 *  `label` is how the absorb named the pair at the time, which survives a
 *  rename that `a_name`/`b_name` follow.
 *
 *  `before`/`after` are rendered standings, not values: the numbers live in
 *  `relationships.json`, and this row is the sentence about them changing.
 *  `source` is "absorb" or "undo" — a reversal appends its own row rather than
 *  deleting the one it put back. It says a reversal happened and not which
 *  direction, since undoing an undo is a redo; `before`/`after` say which way
 *  this one ran. */
export type RelationshipChange = {
  id: string; ts: string; source: string; kind: string;
  a: string; b: string; a_name: string; b_name: string;
  label: string; before: string; after: string;
  scene: LedgerScene;
};
export type LedgerFact = { id: string; one_line: string; date: string; title: string };
/** Payloads for the ledger's hand edits (`routes/ledger.py`).
 *
 *  Optional rather than defaulted-to-empty throughout, and the distinction is
 *  load-bearing: the store mutators behind these read a blank as "keep what is
 *  stored" (a title, a status) or as "clear it" (a commitment's deadline), so
 *  sending `""` where the user changed nothing is an instruction they never
 *  gave. Omitted means the payload said nothing about that field.
 */
export type ThreadSave = {
  title?: string;
  status?: string;
  /** APPENDED, not replaced — a beat is a thing that happened. */
  beat?: string;
  scene?: string;
};
export type CommitmentSave = ThreadSave & {
  kind?: string;
  /** Three-valued: absent keeps the stored deadline, `""` clears it, text sets it. */
  due?: string;
};
export type FactSave = { text?: string; date?: string; scene?: string };
export type FactRecord = { text: string; date?: string; scene?: string; supersedes?: string };
export type RelationshipSave = {
  a: string; b: string;
  trust?: number; affection?: number; tension?: number; note?: string;
  /** Present addresses the undirected bond and the meters are ignored; absent,
   *  this is the directional feeling `a` holds toward `b`. */
  bond?: string;
  scene?: string;
};
export type ChronicleLineSave = { one_line?: string; date?: string };

export type Ledger = {
  plot: PlotThread[]; commitments: Commitment[]; facts: StandingFact[];
  retired: RetiredFact[]; relationships: LedgerRelationship[];
  chronicle: LedgerFact[];
  /** This campaign's staleness threshold, beside the rows rather than on each
   *  of them: a panel saying "40 days untouched" needs to be able to say what
   *  this campaign calls too long. */
  stale_after_days: number;
};

// ---- continuity review (capstone Slice D) ------------------------------
//
// Findings the reconciliation sweep cached in `continuity_candidates.json`,
// joined at read time to the records they name (§12.2), and what acting on
// one sends (§21). The wire shapes are the backend's (`routes/continuity.py`,
// `store/continuity/pending.py`); nothing here re-derives a verdict.

/** The four finding kinds (§6.1). Nothing else is ever cached. */
export type CandidateKind =
  | "possible_duplicate" | "possible_relation"
  | "possible_thread_closure" | "possible_commitment_resolution";
/** Where a finding is reviewed (§6.2), plus the two lists of what was already
 *  decided: aliases and links, and dismissals. */
export type ContinuityGroup = "overlaps" | "closures" | "resolutions" | "reviewed" | "dismissed";

/** Why a finding was found. A pair carries the similarity signals; a lifecycle
 *  finding carries its `reason` (`stale`, `overdue`, `touched`) and a temporal
 *  pairing `reason: "temporal"`. Every key is optional because which ones a
 *  record has depends on how it was found. */
export type CandidateSignals = {
  title_exact?: boolean; slug_equal?: boolean;
  /** max(token Jaccard, character-3-gram Jaccard). */
  lexical?: number;
  /** Null when semantic matching was off or a text had no vector. */
  cosine?: number | null;
  shared_actors?: string[]; shared_scenes?: string[]; shared_anchors?: string[];
  /** The clause that admitted a pair, or a pressure item's kind for an
   *  overdue commitment. */
  via?: string | null;
  reason?: "stale" | "overdue" | "touched" | "temporal";
  days_since?: number | null;
  in_days?: number | null;
  /** The event an overdue commitment's linked deadline names. */
  event?: string;
};
/** The model's proposed decision, or null when none was asked or it was
 *  voided. `from`/`to` are refs; blank on a downgraded `uncertain`. */
export type CandidateProposal = {
  decision: string; from: string; to: string; relation: string; status: string;
  reason: string; evidence_scenes: string[];
};
/** One record a finding names, as it is now. `gone` when it is no longer a
 *  current canonical record (deleted, merged away). An event side carries only
 *  its title and date (`due`). */
export type CandidateRecord = {
  ref: string;
  /** The record type off the ref prefix: `thread`, `commitment` or `event`. */
  kind: string;
  title: string; status: string;
  /** A commitment's own kind (`promise`, `threat`, `foreshadowing`), as the
   *  play prompt prints it. Blank for a thread, an event or a gone record. A
   *  merge keeps the canonical's (§5.1). */
  commitment_kind: string;
  latest_beat: string;
  last_scene: { id: string; title: string };
  pressure: { state: string; in_days: number | null; friendly: string } | null;
  aliases: LedgerAlias[];
  due: string;
  beats: { scene: string; text: string }[];
  gone: boolean;
};
export type ContinuityCandidate = {
  id: string; kind: CandidateKind; group: ContinuityGroup; refs: string[];
  /** The CACHED fingerprint: what an apply's default `expect_fingerprint` is. */
  fingerprint: string;
  stale: boolean;
  stale_reason: "records" | "evidence" | null;
  signals: CandidateSignals;
  proposal: CandidateProposal | null;
  created: string;
  records: CandidateRecord[];
};
/** What the effective view set aside, and why (§26). */
export type ContinuityDiagnostics = {
  dangling_aliases: { ref: string; to: string; reason: string }[];
  broken_links: { id: string; reason: string }[];
  hidden_links: { id: string; reason: string }[];
  unreadable: string[];
};
/** `GET .../continuity/candidates` (§12.2, Decision 23). */
export type ContinuityCandidates = {
  generated: string;
  matching: "basic" | "semantic";
  diagnostics: ContinuityDiagnostics & {
    /** continuity.json sections the reader had to discard. */
    malformed: string[];
    cache_malformed: boolean;
  };
  /** The sweep running on the campaign now, so the section can follow it. */
  run: RunHandle | null;
  /** A display name for every scene id, actor ref and event ref mentioned. */
  names: Record<string, string>;
  /** Every scene, newest first by play order -- for a finding's detail, so it
   *  is empty when the cache holds no finding (the read skips the scene list). */
  scenes: { id: string; title: string }[];
  candidates: ContinuityCandidate[];
};
/** `POST .../candidates/{id}/apply` (§21): flat, with a key named `from`. */
export type ContinuityApply = {
  op: "alias" | "link" | "close" | "resolve" | "keep_open";
  canonical?: string;
  from?: string; to?: string; relation?: string;
  status?: string;
  beat?: string; scene?: string;
  copy_due?: boolean;
  accept_status_change?: boolean;
  expect_fingerprint?: string;
};
/** What a landed apply answers. `affected` comes with a merge only: the other
 *  records that now resolve to the kept one (§5.1's Response), each with the
 *  name the review shows for it, so no ref is worded on the client. */
export type ContinuityApplied = {
  ok: boolean;
  applied: string[];
  affected?: { ref: string; name: string }[];
};
/** A reconciliation run's result (`routes/continuity._blank_result`). */
export type ReconcileResult = {
  sweep: "full" | "incremental";
  matching: string;
  embedding: "off" | "configured" | "failure";
  llm: "off" | "ok" | "failed" | "skipped";
  reason: string;
  /** The refusal's fixed kind (`incapable`, `missing_key`, ...) when `llm` is
   *  "off"; "" otherwise. */
  reason_kind: string;
  candidates: number; adjudicated: number;
  /** Candidates the model's chunks did not read (a failed or garbled chunk
   *  beside an answered one). Counted only on an `ok` sweep: a failed run
   *  throws, so no result carries it. */
  unanswered: number;
  pairs_capped: boolean; superseded: boolean;
  continuity: "ok" | "malformed";
  follow_on: boolean;
};
/** A failed reconciliation run's `error` (`routes/continuity._reconcile_work`):
 *  `saved` is whether its first persist landed, so the findings listed are this
 *  sweep's, and `follow_on` whether it was the follow-on pass that failed. Read
 *  as `Partial` by `failedNote`, since a refusal of another kind carries none
 *  of it. */
export type ReconcileRunError = {
  kind: string; detail: string; status: number;
  sweep: "full" | "incremental"; saved: boolean; follow_on: boolean;
};
export type ContinuityAliasRow = {
  ref: string; to: string; canonical: string; title: string; to_title: string;
  created: string; source: string; note: string; dangling: boolean; reason: string;
};
export type ContinuityLinkRow = {
  id: string; relation: string; a: string; b: string; a_raw: string; b_raw: string;
  scene: string; note: string; created: string; a_title: string; b_title: string;
};
export type ContinuityRawLink = {
  id: string; a: string; b: string; relation: string; scene: string; note: string;
  created: string; state: "ok" | "broken" | "hidden"; reason: string;
  /** Each end's title, or its ref when the record is gone (`review.describe`). */
  a_title: string; b_title: string;
};
/** A stored dismissal. `live` is whether it still names the records as they
 *  are; one whose records have since changed suppresses nothing (§12.7). */
export type ContinuitySuppression = {
  fingerprint: string; kind: string; refs: string[]; decision: string; created: string;
  live: boolean; titles: string[];
};
/** `GET .../continuity`. */
export type ContinuityState = {
  aliases: ContinuityAliasRow[]; links: ContinuityLinkRow[]; raw_links: ContinuityRawLink[];
  suppressions: ContinuitySuppression[];
  diagnostics: ContinuityDiagnostics;
  malformed: string[]; unreadable: string[];
  matching: "basic" | "semantic";
};

// ---- the story graph (capstone Slice F) --------------------------------
//
// `GET /continuity/graph` (§19, §20): one read-only payload of nodes and edges
// the page draws under four client-side lenses. The wire shapes are
// `store/continuity/graph.py`'s. A dated node is placed only by `fixed` and
// `in_days`; `native` is calendar text and is never parsed here.
//
// Each vocabulary below is held to its Python tuple by
// `backend/tests/test_continuity_graph.py`, which parses this file -- so each
// stays a plain union of string literals, in the tuple's order. The chooser's
// `DriverKind`, `DriverAction`, `PressureState`, `AnchorRelation` and the
// review's `CandidateKind` are reused, never redeclared.

export type NodeKind =
  | "scene" | "character" | "pc" | "location" | "thread" | "commitment" | "event" | "idea"
  | "birthday" | "holiday";
export type EdgeKind =
  | "appeared_in" | "occurred_at" | "opened_in" | "advanced_in" | "touched_in" | "closed_in"
  | "resolved_in" | "involves" | "serves" | "anchored_to" | "feeling" | "bond"
  | "birthday_of" | "link" | "merged_into" | "possible_duplicate" | "possible_relation";
export type EdgeSource = "structural" | "reviewed" | "candidate" | "alias";
/** A reviewed link's relation (`effective.LINK_RELATIONS`). */
export type LinkRelation =
  | "continues" | "subthread_of" | "pays_off" | "before" | "on" | "after" | "by"
  | "related_to";
export type EventStatus = "scheduled" | "fired" | "passed" | "undated";
/** A source the read could not use; named in `omitted` so a broken read never
 *  looks like an empty campaign. */
export type GraphPart =
  | "calendar" | "scenes" | "chronicle" | "plot" | "commitments" | "events" | "continuity"
  | "candidates" | "relationships" | "scene_ideas" | "names";

/** A dated node's four fields. A null `fixed` is undated (no calendar, or a
 *  date the calendar cannot read), never day zero. */
export type GraphDate = { native: string; friendly: string; fixed: number | null;
                          in_days: number | null };
export type NodePressure = { state: PressureState; in_days: number | null; friendly: string };
/** A visible review finding on the node it names; `other` is the pair's other
 *  end, null for a lifecycle finding. */
export type GraphFinding = { id: string; kind: CandidateKind; other: string | null };
/** A thread or commitment, canonical or merged away (`merged_into` set). */
export type ArcFields = {
  status: string; live: boolean; merged_into: string | null; aliases: LedgerAlias[];
  latest_beat: string; pressure: NodePressure | null; focusable: boolean;
  findings: GraphFinding[];
};
export type GraphNode = { id: string; label: string } & (
  | ({ kind: "scene"; order: number; done: boolean; pcless: boolean; place: string }
     & GraphDate)
  | { kind: "character" | "pc" | "location" }
  | ({ kind: "thread" } & ArcFields)
  | ({ kind: "commitment"; commitment_kind: string; due: string } & ArcFields & GraphDate)
  | ({ kind: "event"; status: EventStatus; pressure: NodePressure | null; anchorable: boolean;
       findings: GraphFinding[] } & GraphDate)
  | ({ kind: "holiday"; pressure: NodePressure; anchorable: boolean } & GraphDate)
  | ({ kind: "birthday"; actor: string; precision: "exact" | "yearless" | "month";
       age: number | null; pressure: NodePressure; anchorable: boolean } & GraphDate)
  | ({ kind: "idea"; premise: string; source: string; pcless: boolean;
       time_anchor: { ref: string; relation: AnchorRelation; native: string } | null }
     & GraphDate)
);
/** `relation` is typed by `kind` (§20: typed unions, not open string bags):
 *  only a link, a `serves` and an `anchored_to` edge carry one. */
export type GraphEdge = {
  id: string; from: string; to: string; source: EdgeSource; candidate_id: string | null;
} & (
  | { kind: "link"; relation: LinkRelation }
  | { kind: "serves"; relation: DriverAction }
  | { kind: "anchored_to"; relation: AnchorRelation }
  | { kind: "feeling"; relation: null; trust: number; affection: number; tension: number;
      note: string }
  | { kind: "bond"; relation: null; bond_type: string; since_scene: string }
  | { kind: Exclude<EdgeKind, "link" | "serves" | "anchored_to" | "feeling" | "bond">;
      relation: null }
);
export type StoryGraph = {
  now: { native: string; friendly: string; fixed: number | null };
  nodes: GraphNode[]; edges: GraphEdge[]; omitted: GraphPart[];
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
  /** Present only on a closed branch: the absorbed sibling that closed it
   *  (the scene listing's `closed_by`). */
  closed_by?: { sid: string; title: string };
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
export type LoreEntryDraft = {
  name: string; keys: string[]; body: string; category: EntityKindName;
  /** The advanced ST activation fields parse stashed (#20) — carried through
   *  the review table untouched and committed as `st_extensions` frontmatter.
   *  Absent when the source entry had none. */
  extensions?: Record<string, unknown>;
};

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
 *  thread that is owed. `detail` is what makes the row worth expanding to:
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
  /** The theme headings, in reading order (story, character, art, rules, then
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
