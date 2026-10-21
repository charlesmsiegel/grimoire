/** Inference and writing settings: connections and their models, styles,
 *  response bundles, roles and routes, author's notes and default prompts,
 *  sampler presets, costs and pricing, regex rules, quick replies and play
 *  decisions. Spread into `api` by `client.ts`. */
import { invalidateConfigCache } from "../freshness";
import { announceModels, notifyConfig } from "../notify";
import { draftRun } from "../runs";
import { type RunHandle, newAttemptId } from "../stream";
import { ApiError, request } from "../transport";
import type { AuthorsNote, AuthorsNotes, AuthorsNotesNext, CampaignBudget,
  CampaignDefaultPrompt, CampaignInferenceWrite, CampaignPlayDecisions, CampaignSceneCosts,
  CapabilityNeed, ControlsPreview, GroupSettings, HealthCheckResult, InferenceSettings,
  InferenceWrite, LLMConnection, LLMConnectionDetail, LLMConnectionDraft, ModelCapabilities,
  ModelFacts, ModelFactsUpdate, ModelTestPreview, ModelTestResult, ModelsRefreshResult,
  MonthlyCosts, PlayDecisionValues, PlayDecisionsInfo, PricingEntry, PricingTable, PromptField,
  ProviderPresetOption, QuickReply, QuickReplyDraft, QuickReplyEntry, QuickReplyScope,
  QuickReplySet, RegexBundle, RegexImportRow, RegexLayer, RegexRule, RegexScope, RegexStep,
  RegexTestBody, ResponseBundle, ResponseFields, SamplerImportReport, SamplerParamSpec,
  SamplerPreset, SamplerPresetDraft, SceneUsage, Style, StyleDetail, StyleDraft,
  TestableCapability } from "../types";
import { encodeSegment } from "../../urlSegment";

/** Re-fetch a connection's catalog, and recover the one draft whose result
 *  outlives its run.
 *
 *  Every other preview lives only on the run, so a reap is the honest end of
 *  it. This one writes the catalog beside the connection BEFORE the run goes
 *  terminal, so a tab suspended past the retention window would be told a
 *  refresh failed that had in fact succeeded -- and `notifyConfig` would never
 *  fire, leaving every scene inspector sizing prompts against the list this
 *  request replaced.
 *
 *  **Recovery asks whether THIS attempt wrote, and nothing weaker will do.**
 *  Not "does a catalog exist" -- one does for any connection ever refreshed,
 *  so a refresh that failed and was reaped would hand the stale list back as a
 *  success. Not "is the catalog newer than it was" either: two tabs refreshing
 *  the same connection overlap by design (global drafts declare no exclusion
 *  key), so the other tab's success would be read as this one's. The attempt
 *  id is stamped into the sidecar by the run that wrote it, and that is the
 *  only thing that answers the question asked.
 *
 *  An id that does not match -- including the `""` of a sidecar written before
 *  the field existed -- recovers nothing and the reap stands.
 */
async function refreshModels(id: string,
                             signal?: AbortSignal): Promise<ModelsRefreshResult> {
  const attempt = newAttemptId();
  try {
    return await draftRun<ModelsRefreshResult>({ at: "global" }, (a) =>
      request<{ run: RunHandle }>(
        "POST", `/api/llm-connections/${encodeSegment(id)}/models/refresh`,
        undefined, { attempt: a, signal }), { signal, attempt });
  } catch (err) {
    // Only `run_gone`. Any other failure is the run saying what went wrong,
    // and the store cannot overrule it.
    if (!(err instanceof ApiError) || err.kind !== "run_gone") throw err;
    // `fresh`, and NOT `api.readConnection`. Shared in-flight GETs are normally
    // free because the answer cannot be stale -- the map only holds promises
    // that have not settled -- but that reasoning assumes the caller does not
    // care WHEN the read happened. This one does: it is verifying a write, and
    // a read issued before that write (across the same suspension that let the
    // run be reaped) would answer with the pre-refresh sidecar and no
    // `fetched_by`, reporting a reap for a refresh that landed.
    const conn = await request<LLMConnectionDetail>(
      "GET", `/api/llm-connections/${encodeSegment(id)}`, undefined,
      { fresh: true, signal });
    if (conn.fetched_by !== attempt) throw err;
    return { models: conn.models, fetched_at: conn.fetched_at, rev: conn.rev };
  }
}

/** A quick-reply set's path: the world's, or the campaign's own layer. */
function quickRepliesPath(scope: QuickReplyScope): string {
  return scope.kind === "world"
    ? `/api/worlds/${scope.wid}/quick-replies`
    : `/api/campaigns/${scope.cid}/quick-replies`;
}

/** The four regex levels share one body and differ only in path. */
function regexPath(scope: RegexScope): string {
  switch (scope.kind) {
    case "global": return "/api/regex";
    case "world": return `/api/worlds/${scope.wid}/regex`;
    case "campaign": return `/api/campaigns/${scope.cid}/regex`;
    case "connection": return `/api/llm-connections/${encodeSegment(scope.id)}/regex`;
  }
}

export const settingsApi = {
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
  /** Re-fetch a saved connection's catalog from its provider and cache it
   *  server-side (#149).
   *
   *  Announces, like the other connection mutators: this one does not change
   *  the config, but `models.ts` holds a page-load copy of the ACTIVE
   *  connection's catalog and that signal is what drops it. Without this, a
   *  refresh on the Connections page updates the store and the editor while
   *  every scene inspector goes on sizing prompts against the list this
   *  request replaced. */
  refreshConnectionModels: (id: string, signal?: AbortSignal) =>
    refreshModels(id, signal).then(announceModels),
  /** Ask this connection's provider whether it can serve, right now (#146).
   *
   *  Resolves for a *failing* connection too: the answer is in `ok`, and the
   *  request only rejects when the question itself could not be asked (an id
   *  that does not exist). Invalidates the cached config because the check's
   *  verdict is what the status bar reads.
   *
   *  A provider whose check GENERATES (`generating_check` on its preset: the
   *  Claude subscription) is refused with a 400 unless `confirm` is passed,
   *  and nothing is sent -- so pass it only from the step where the reader
   *  agreed to send one short message. The free checks ignore it. */
  checkConnection: (id: string, opts?: { confirm?: boolean }) =>
    request<HealthCheckResult>("POST", `/api/llm-connections/${encodeSegment(id)}/health`,
      opts?.confirm ? { confirm: true } : undefined).then((r) => {
      invalidateConfigCache();
      return notifyConfig(r);
    }),
  /** The provider presets a new provider is made from (OpenRouter, Anthropic,
   *  z.ai, ...), each saying whether its health check generates. */
  listProviderPresets: () =>
    request<ProviderPresetOption[]>("GET", "/api/providers/presets"),
  /** What is known of `model` on a provider ("" for its own): the user's
   *  statements, the test results for its current rev, and every capability
   *  as it resolves with them. `fresh`: a test or an edit just moved them. */
  readModelFacts: (id: string, model = "") =>
    request<ModelFacts>(
      "GET", `/api/llm-connections/${encodeSegment(id)}/facts?model=${encodeURIComponent(model)}`,
      undefined, { fresh: true }),
  /** State what the user knows of one model. A field left out is left alone.
   *  Announced (`announceModels`): a capability override on the model Primary
   *  resolves to can move `ready`, which the header reads, and the model's
   *  rates are among the facts a model view shows. */
  putModelFacts: (id: string, body: ModelFactsUpdate) =>
    request<ModelFacts>("PUT", `/api/llm-connections/${encodeSegment(id)}/facts`, body)
      .then(announceModels),
  /** What a test call would send and roughly cost. Sends nothing, meters
   *  nothing, starts no run -- it is what the confirmation shows. */
  previewModelTest: (id: string, body: { model: string; capabilities: TestableCapability[] }) =>
    request<ModelTestPreview>(
      "POST", `/api/llm-connections/${encodeSegment(id)}/test/preview`, body),
  /** Run a test call: one probe per capability, which may cost money. A draft
   *  run polled to its result, so a dropped connection does not lose it.
   *
   *  `confirm: true` is in the type rather than added here: the server refuses
   *  a test without it, and a caller has to have shown the preview and been
   *  answered before it may write it.
   *
   *  `attempt` names the run, so a caller that outlives one wait can ask again
   *  under the same id and be handed the run that id already started (or its
   *  outcome) rather than a second, paid one. Omitted, each call is new. */
  runModelTest: (id: string,
                 body: { model: string; capabilities: TestableCapability[]; confirm: true },
                 opts?: { signal?: AbortSignal | undefined; attempt?: string | undefined }) =>
    draftRun<ModelTestResult>({ at: "global" }, (attempt) =>
      request<{ run: RunHandle }>(
        "POST", `/api/llm-connections/${encodeSegment(id)}/test`, body,
        { attempt, signal: opts?.signal }),
      { signal: opts?.signal, attempt: opts?.attempt })
      // A landed test can file a verified capability, which outranks the
      // catalog: it can move `GET /config`'s `ready` and every model view.
      .then(announceModels),

  listStyles: () => request<Style[]>("GET", "/api/styles"),
  createStyle: (draft: StyleDraft) => request<{ id: string }>("POST", "/api/styles", draft),
  readStyle: (sid: string) => request<StyleDetail>("GET", `/api/styles/${sid}`),
  updateStyle: (sid: string, patch: Partial<StyleDraft>) =>
    request<{ ok: boolean }>("PUT", `/api/styles/${sid}`, patch),
  deleteStyle: (sid: string) => request<{ ok: boolean }>("DELETE", `/api/styles/${sid}`),
  duplicateStyle: (sid: string) => request<{ id: string }>("POST", `/api/styles/${sid}/duplicate`),

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
  // Group play: the scene's speaker-order settings. PUT replaces the whole set
  // and answers with what the server stored after normalising it.
  getSceneGroup: (cid: string, sid: string) =>
    request<GroupSettings>("GET", `/api/campaigns/${cid}/scenes/${sid}/group`, undefined, { fresh: true }),
  setSceneGroup: (cid: string, sid: string, s: GroupSettings) =>
    request<{ ok: boolean; settings: GroupSettings }>("PUT", `/api/campaigns/${cid}/scenes/${sid}/group`, s),

  // Roles and routes (the inference settings, spec 10). `fresh` on the reads:
  // each row carries what OTHER scopes resolve to, so a cached copy would show
  // an inherited answer from before the write that prompted the reload. Both
  // writes answer with the fresh view, and are refused 409 `newer_format` or
  // `not_migrated` while the store is not at this build's layout.
  getInferenceSettings: () =>
    request<InferenceSettings>("GET", "/api/inference/settings", undefined, { fresh: true }),
  /** Write the library's roles and routes. A change of the Embedding role to
   *  a model re-embeds the library, which may cost money, so the server
   *  refuses it (400 `confirm_embedding`) unless `confirmEmbedding` says the
   *  reader agreed. Invalidates the cached config: the status bar names what
   *  Primary resolves to. */
  putInferenceSettings: (body: InferenceWrite, opts?: { confirmEmbedding?: boolean }) =>
    request<InferenceSettings>("PUT", "/api/inference/settings",
      opts?.confirmEmbedding ? { ...body, confirm_embedding: true } : body).then((r) => {
      invalidateConfigCache();
      return notifyConfig(r);
    }),
  /** Dismiss one "not carried over" note for good: it is recorded in the
   *  library, so every device stops showing it. 404 for an id nobody knows. */
  dismissRetiredNote: (id: string) =>
    request<{ ok: boolean }>("POST",
      `/api/inference/retired-notes/${encodeSegment(id)}/dismiss`),
  getCampaignInference: (cid: string) =>
    request<InferenceSettings>("GET", `/api/campaigns/${encodeSegment(cid)}/inference`,
      undefined, { fresh: true }),
  /** One campaign's role and route overrides. Announces: the play header
   *  names the model a turn will run on. */
  putCampaignInference: (cid: string, body: CampaignInferenceWrite) =>
    request<InferenceSettings>("PUT", `/api/campaigns/${encodeSegment(cid)}/inference`, body)
      .then(notifyConfig),
  // Author's notes (play controls V). `fresh` on the reads: the panel reloads
  // right after a save, and a cached copy would show the note it just replaced.
  getAuthorsNotes: (cid: string) =>
    request<AuthorsNotes>("GET", `/api/campaigns/${cid}/authors-notes`, undefined, { fresh: true }),
  // Authorial fields must not join a read opened before their save. They use
  // the same explicit fresh boundary as author's notes, not another cache.
  getWorldDefaultPrompt: (wid: string) =>
    request<PromptField>("GET", `/api/worlds/${wid}/default-prompt`, undefined, { fresh: true }),
  setWorldDefaultPrompt: (wid: string, text: string) =>
    request<PromptField>("PUT", `/api/worlds/${wid}/default-prompt`, { text }),
  getCampaignDefaultPrompt: (cid: string) =>
    request<CampaignDefaultPrompt>("GET", `/api/campaigns/${cid}/default-prompt`, undefined, { fresh: true }),
  setCampaignDefaultPrompt: (cid: string, text: string) =>
    request<CampaignDefaultPrompt>("PUT", `/api/campaigns/${cid}/default-prompt`, { text }),
  getSceneFocus: (cid: string, sid: string) =>
    request<PromptField>("GET", `/api/campaigns/${cid}/scenes/${sid}/focus`, undefined, { fresh: true }),
  setSceneFocus: (cid: string, sid: string, text: string) =>
    request<PromptField>("PUT", `/api/campaigns/${cid}/scenes/${sid}/focus`, { text }),
  setCampaignAuthorsNote: (cid: string, note: AuthorsNote) =>
    request<AuthorsNotes>("PUT", `/api/campaigns/${cid}/authors-notes/campaign`, note),
  setCharacterAuthorsNote: (cid: string, ref: string, note: AuthorsNote) =>
    request<AuthorsNotes>("PUT",
      `/api/campaigns/${cid}/authors-notes/characters/${encodeURIComponent(ref)}`, note),
  setSceneAuthorsNote: (cid: string, sid: string, note: AuthorsNote) =>
    request<AuthorsNotes>("PUT", `/api/campaigns/${cid}/scenes/${sid}/authors-note`, note),
  getAuthorsNotesNext: (cid: string, sid: string) =>
    request<AuthorsNotesNext>("GET", `/api/campaigns/${cid}/scenes/${sid}/authors-notes/next`,
      undefined, { fresh: true }),
  // Sampler presets. `fresh` on the list: an editor that saved and then shows
  // the cached pre-save list is the one thing it must not do.
  listSamplerPresets: () =>
    request<{ presets: SamplerPreset[]; params: SamplerParamSpec[] }>(
      "GET", "/api/sampler-presets", undefined, { fresh: true }),
  // The three writes (and the import below) announce, like every mutator of
  // what a model view reads (`announceModels`): `GET /config` names each
  // role's preset, the Models views list them, and a delete clears the roles
  // and routes that named one.
  createSamplerPreset: (draft: SamplerPresetDraft) =>
    request<SamplerPreset>("POST", "/api/sampler-presets", draft).then(announceModels),
  updateSamplerPreset: (pid: string, draft: SamplerPresetDraft) =>
    request<SamplerPreset>("PUT", `/api/sampler-presets/${encodeURIComponent(pid)}`, draft)
      .then(announceModels),
  deleteSamplerPreset: (pid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/sampler-presets/${encodeURIComponent(pid)}`)
      .then(announceModels),
  /** A connection's catalog grouped by what a role needs of a model (fits,
   *  unverified, hidden), embedding models included. `model` narrows it to one
   *  id. `fresh`: a test call or an override just moved a model between groups. */
  readConnectionCapabilities: (id: string, need: CapabilityNeed, model?: string) =>
    request<ModelCapabilities>(
      "GET",
      `/api/llm-connections/${encodeSegment(id)}/capabilities?need=${need}` +
        (model ? `&model=${encodeURIComponent(model)}` : ""),
      undefined, { fresh: true }),
  /** What a sampler preset ("" for none) sends on a connection's model ("" for
   *  its own), control by control. */
  /** `operation: "decide"` previews it as a decision: on a model answered
   *  natively, every control is `n/a`. */
  previewControls: (body: { preset_id: string; provider: string; model: string;
                            operation?: "decide" }) =>
    request<ControlsPreview>("POST", "/api/inference/controls", body),
  /** A SillyTavern preset file, already parsed by the browser. */
  importSamplerPreset: (body: { name: string; data: unknown; include_max_tokens: boolean }) =>
    request<{ preset: SamplerPreset; report: SamplerImportReport }>(
      "POST", "/api/sampler-presets/import", body).then(announceModels),

  // Cost (#153). `fresh` on both: a turn that just landed is exactly what makes
  // a reader open the Cost section, and a cached read issued before it would
  // show the spend from before the turn they are asking about.
  getSceneUsage: (cid: string, sid: string) =>
    request<SceneUsage>("GET", `/api/campaigns/${cid}/scenes/${sid}/usage`,
                        undefined, { fresh: true }),
  // All-time, and deliberately not `fresh`: this is a report opened on purpose
  // rather than a figure read mid-turn, and it scans the whole ledger.
  // `order` goes to the server because the list is capped there, after the
  // sort: re-ordering the response here would make every ordering but the
  // default mean "…of the most expensive N".
  getCampaignSceneCosts: (cid: string, order = "cost", month = "") =>
    request<CampaignSceneCosts>(
      "GET", `/api/campaigns/${cid}/usage/scenes?order=${encodeURIComponent(order)}`
      + (month ? `&month=${encodeURIComponent(month)}` : "")),
  getMonthlyCosts: (month = "", campaign = "") =>
    request<MonthlyCosts>("GET", `/api/usage/monthly?month=${encodeURIComponent(month)}${campaign ? `&campaign=${encodeURIComponent(campaign)}` : ""}`,
                          undefined, { fresh: true }),
  // The per-model rate table (#158). `fresh` on the read, because saving a rate
  // and seeing the old table is the one thing an editor must not do.
  getPricing: () => request<PricingTable>("GET", "/api/pricing", undefined, { fresh: true }),
  setPricing: (rates: Record<string, PricingEntry>) =>
    request<{ rates: Record<string, PricingEntry> }>("PUT", "/api/pricing", { rates }),
  getCampaignBudget: (cid: string) =>
    request<CampaignBudget>("GET", `/api/campaigns/${cid}/budget`, undefined, { fresh: true }),
  setCampaignBudget: (cid: string, body: { budget_usd: number | null; budget_period?: string }) =>
    request<CampaignBudget>("PUT", `/api/campaigns/${cid}/budget`, body),
  getRegex: (scope: RegexScope, opts?: { fresh?: boolean }) =>
    request<RegexBundle>("GET", regexPath(scope), undefined, opts),
  putRegex: (scope: RegexScope, layer: RegexLayer) =>
    request<RegexBundle>("PUT", regexPath(scope), layer),
  testRegex: (body: RegexTestBody) =>
    request<{ steps: RegexStep[]; result: string }>("POST", "/api/regex/test", body),
  previewRegexImport: (data: unknown) =>
    request<{ rows: RegexImportRow[] }>("POST", "/api/regex/import/preview", { data }),
  importRegex: (scope: RegexScope, rows: Omit<RegexRule, "id">[]) =>
    request<RegexBundle & { added: string[] }>("POST", "/api/regex/import", { scope, rows }),
  getQuickReplies: (scope: QuickReplyScope) =>
    request<QuickReplySet>("GET", quickRepliesPath(scope)),
  /** Replaces the whole set; `expect` is the digest the caller read (409
   *  `set_changed` when the stored set moved since). */
  setQuickReplies: (scope: QuickReplyScope, replies: (QuickReplyEntry | QuickReplyDraft)[], expect: string) =>
    request<QuickReplySet>("PUT", quickRepliesPath(scope), { replies, expect }),
  getEffectiveQuickReplies: (cid: string) =>
    request<{ replies: QuickReply[] }>("GET", `/api/campaigns/${cid}/quick-replies/effective`),
  getPlayDecisionsInfo: () => request<PlayDecisionsInfo>("GET", "/api/play-decisions"),
  getCampaignPlayDecisions: (cid: string) =>
    request<CampaignPlayDecisions>("GET", `/api/campaigns/${cid}/play-decisions`),
  /** Only the fields given are written; "" follows the global one again. */
  setCampaignPlayDecisions: (cid: string, values: Partial<PlayDecisionValues>) =>
    request<CampaignPlayDecisions>("PUT", `/api/campaigns/${cid}/play-decisions`, values),
};
