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
/** What a role or route needs of a model (`capabilities.NEEDS`): `tools` is
 *  a route's (`requires`), asked of a pin picker. */
export type CapabilityNeed = "generate" | "vision" | "embed" | "decide" | "tools";
/** Every capability (`capabilities.NAMES`, held equal by a backend test). */
export type CapabilityName =
  | "generate" | "stream" | "vision" | "embed" | "decide_native"
  | "structured_output" | "prefill" | "tools";
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
  /** Live migration hold, including retirement after the layout switches. */
  active?: boolean;
  progress?: { phase: "safety_backup" | "retirement_backup"; done: number; total: number | null };
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
/** One model's per-token rates (#158), in dollars per 1,000 tokens. The cache
 *  pair is optional, and its absence is not zero: cache counts are slices of
 *  the prompt, so a table naming no cache rate has already priced them at the
 *  prompt rate. */
export type PricingEntry = {
  prompt_usd_per_1k?: number; completion_usd_per_1k?: number;
  cache_read_usd_per_1k?: number; cache_write_usd_per_1k?: number;
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
  /** The resolved model's size (01i); null when nothing resolves and on a
   *  native decision, which sends no prompt to pack. */
  limits: CardLimits | null;
};
/** A card's or row's model size (01i, spec 6.3): the resolved primary's
 *  window and max output, the riding fallback's window (null when none
 *  rides), and what a prompt may hold across them -- `tokens` null when no
 *  window is known, 0 (with a `reason`) when the reply reserve fills it. */
export type CardLimits = {
  window: ModelLimit;
  max_output: ModelLimit;
  fallback_window: ModelLimit | null;
  ceiling: { tokens: number | null; binding: [string, string] | null; reason: string };
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
  /** The stated embedding options its requests are built from (01h), in
   *  their stored shape; null while it is off or when none are stated. */
  options?: EmbeddingOptionsBlock | null;
  /** As on `RoleCard`; null while the role is off. */
  rate: RateInfo | null;
  /** As on `RoleCard`, null while the role is off; nothing is reserved for a
   *  reply, so its ceiling is the window itself. */
  limits: CardLimits | null;
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
  /** As on `RoleCard`. */
  limits: CardLimits | null;
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
/** How an embedding request says query or document (01h): not at all, as a
 *  text prefix, or as a request field. */
export type EmbeddingInputMode = "none" | "prefix" | "param";
/** A model's stated embedding options (01h), as stored: only the fields its
 *  mode uses, and a width only when one is requested. `{}` is none. */
export type EmbeddingOptionsBlock = {
  input?: EmbeddingInputMode;
  query_prefix?: string; document_prefix?: string;
  param_field?: string; query_value?: string; document_value?: string;
  dimensions?: number; dimensions_field?: string;
};
/** A probe's filed verdict. A failed `embed` probe whose endpoint ignored a
 *  requested width carries both widths (01h). */
export type VerifiedResult = {
  ok: boolean; at?: string; error?: string; dims?: number;
  requested_dims?: number; returned_dims?: number;
};
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
  /** The stated embedding options block as it is on disk (01h), which may be
   *  one the validator refuses; absent or null when none is stated. */
  embedding?: EmbeddingOptionsBlock | null;
  /** The block is refused by the validator, so the Embedding role names no
   *  space with this model; `embedding_invalid_reason` is the fixed sentence. */
  embedding_invalid?: boolean;
  embedding_invalid_reason?: string;
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
  /** The model's embedding options (01h): a block replaces them, `{}`
   *  removes them. A change of their document side on the Embedding role's
   *  model asks `confirm_embedding`. */
  embedding?: EmbeddingOptionsBlock;
};
/** The capabilities a test call has a probe for (`probes.PROBES`). */
export type TestableCapability = "generate" | "vision" | "embed" | "decide_native" | "tools";
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
 *  earlier failure that answered for it too. The `tools` probe adds two:
 *  `no_tool_call` (the model answered without calling -- filed, read as
 *  unverified) and `capped` (the reply reached the cap first -- not filed). */
export type ModelTestProbe = {
  ok: boolean; kind?: string; error?: string; dims?: number;
  /** A failed `embed` probe whose endpoint ignored a requested width (01h). */
  code?: string; requested_dims?: number; returned_dims?: number;
};
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
