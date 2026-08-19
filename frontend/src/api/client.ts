import { parseSSEChunk, type ChatEvent, type LocalizeEvent, type ChubGalleryEvent } from "./stream";
import { campaignsChanged, configChanged } from "../appEvents";

// Re-exported so every existing `from "../api/client"` import keeps working;
// imported by name for the ones the calls below actually mention.
export * from "./types";
import {
  type Actor, type AdvanceDigest, type AdvanceRequest, type Appearance, type Availability,
  type BackupList, type BackupRun, type Briefing, type CalendarConfig, type CalendarMonth,
  type CalendarScope, type CampaignClock, type CampaignMeta, type CampaignModule, type Card,
  type CampaignBudget,
  type CardFormat, type CascadeReport, type Casefile, type CastChanges, type CastDetail,
  type ForkReport,
  type CharacterDetail,
  type CharacterSummary, type CheckResolution, type ChronicleEntry, type ChubImportResult,
  type ChubUnlinkedVersion, type Climate, type ClimateSummary, type Config, type ConfigUpdate,
  type DataDirInfo, type Dossiers, type EntityDetail, type EntityKind, type EntityScope,
  type EntitySummary, type Greeting, type GreetingDetail, type GreetingDraft, type GroupState,
  type IncomingItem, type IncomingRef, type JournalEntry, type LLMConnection,
  type LLMConnectionDetail, type LLMConnectionDraft, type Ledger, type LengthPreset,
  type LoreEntryDraft, type Mechanics, type ModelsRefreshResult, type ModuleContentEntry,
  type ModuleDetail, type ModuleEditResult, type ModuleRenameKind, type ModuleSummary,
  type PCDetail, type PCSummary, type Persona, type PinRule, type PromptEntry,
  type PromptLayout, type PromptSnapshot, type ProposalRecord, type Provenance,
  type RecordChange, type ReplayPreview, type ReplaySession, type ResponseBundle, type ResponseFields, type ResponseOverride,
  type ResponsePresetDetail, type ResponsePresetDraft, type ResponsePresetSummary,
  type ResponsePresetUsage, type RollEntry, type RollingSummary, type RollingSummaryRefresh,
  type RetconReport, type RosterEntry, type ScenarioImportResult, type ScenarioProposal, type SceneAbsorb,
  type SceneAlternates, type SceneCheckActor, type SceneContext, type SceneDatetime,
  type SceneIdea, type SceneIdeaDraft, type SceneIntentResult, type SceneLocation,
  type SceneBreak, type SceneBreakAnswer,
  type SceneMeta, type ScenePage, type SceneSuggestion, type SceneUsage,
  type SceneWeather, type SearchMode,
  type SearchResult, type Sheet, type SheetCoverage, type SheetExpected, type StagedEdit,
  type StoreConflicts, type Style, type StyleDetail, type StyleDraft, type Suggestion,
  type Timeline, type TimelineEvent, type WeatherOverrideBody, type WeatherRangeBody,
  type WeatherSpan,
  type WorldCampaignPending, type WorldMeta,
} from "./types";

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
  // Fork (#72), which is what the replay nudge offers (#80): a copy of the
  // campaign as it stands, so an expensive or destructive thing can be done to
  // the copy. `notifyCampaigns`, like create and rename — the sidebar's Recent
  // rail gains a row.
  forkCampaign: (cid: string, name: string) =>
    request<{ id: string; name: string }>(
      "POST", `/api/campaigns/${cid}/fork`, { name }).then(notifyCampaigns),
  deleteCampaign: (cid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}`).then(notifyCampaigns),
  /** Fork `cid` into a new campaign. `fromScene` cuts the copy back to that
   *  scene — it and everything before it stay, everything after it comes off
   *  the fork and nothing at all happens to `cid`. Omitted, the fork is of the
   *  campaign as it stands. `notifyCampaigns` for the same reason
   *  `createCampaign` sends it: the shelf and the sidebar gain a row. */
  forkCampaign: (cid: string, name: string, fromScene?: string) =>
    request<ForkReport>("POST", `/api/campaigns/${cid}/fork`,
      { name, ...(fromScene ? { from_scene: fromScene } : {}) }).then(notifyCampaigns),
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

  // ---- campaign sync (#6) ----
  // `fresh`, for the reason the ledger opts out of sharing: the review panel
  // re-reads this precisely because an accept or a reject just resolved a row,
  // and a shared in-flight promise would answer with the list as it stood
  // before that write landed -- leaving the resolved row on screen.
  getIncoming: (cid: string) =>
    request<IncomingItem[]>("GET", `/api/campaigns/${cid}/incoming`, undefined, { fresh: true }),
  // Both take a list, so one object and "all of them" are the same call. Both
  // also notify the campaigns channel: advancing a base calls `campaigns.touch`
  // server-side (`store/sync._advance`), so the sidebar's Recent rail is sorted
  // on a value this just moved -- the same reason rename and delete notify.
  acceptIncoming: (cid: string, refs: IncomingRef[]) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/incoming/accept`, { refs })
      .then(notifyCampaigns),
  rejectIncoming: (cid: string, refs: IncomingRef[]) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/incoming/reject`, { refs })
      .then(notifyCampaigns),
  // Counted by running `incoming` for every campaign in the world, so it is
  // `fresh` for the same reason: it is read again after a campaign resolved
  // something, and a shared read would still be showing the old count.
  worldCampaigns: (wid: string) =>
    request<WorldCampaignPending[]>("GET", `/api/worlds/${wid}/campaigns`, undefined, { fresh: true }),

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
  castChanges: (cid: string, sid: string) =>
    request<CastChanges>("GET", `/api/campaigns/${cid}/scenes/${sid}/cast-changes`),
  /** Create a character the prose invented and seat it, campaign-side (#98). */
  createEmergentCast: (cid: string, sid: string, name: string) =>
    request<{ character: string; version: string; name: string }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/cast/emergent`, { name }),
  /** The card-text mention scan (#96): who the seated cast's cards name that
   *  this campaign has not seen yet. Distinct from `castChanges`, which reads
   *  the turn's prose — the two find different people. */
  getSuggestions: (cid: string, sid: string) =>
    request<Suggestion[]>("GET", `/api/campaigns/${cid}/scenes/${sid}/suggestions`),
  /** Shared by both: one per-scene dismissal list, one meaning — "not this
   *  character, in this scene" — so silencing a name in either surface
   *  silences it in the other. */
  dismissSuggestion: (cid: string, sid: string, character: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/scenes/${sid}/suggestions/dismiss`,
                             { character }),
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

  // Cost (#153). `fresh` on both: a turn that just landed is exactly what makes
  // a reader open the Cost section, and a cached read issued before it would
  // show the spend from before the turn they are asking about.
  getSceneUsage: (cid: string, sid: string) =>
    request<SceneUsage>("GET", `/api/campaigns/${cid}/scenes/${sid}/usage`,
                        undefined, { fresh: true }),
  getCampaignBudget: (cid: string) =>
    request<CampaignBudget>("GET", `/api/campaigns/${cid}/budget`, undefined, { fresh: true }),
  setCampaignBudget: (cid: string, body: { budget_usd: number | null; budget_period?: string }) =>
    request<CampaignBudget>("PUT", `/api/campaigns/${cid}/budget`, body),
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
  // `fresh`, like `getRollingSummary` and for its reason: the panel re-reads
  // this immediately after the automatic POST commits, which is exactly when a
  // shared in-flight GET issued *before* that write would hand back a pre-write
  // answer and install it as the newest word.
  getSceneBreak: (cid: string, sid: string) =>
    request<SceneBreak>("GET", `/api/campaigns/${cid}/scenes/${sid}/scene-break`,
                        undefined, { fresh: true }),
  /** Ask the server whether the scene has reached a place to stop. Without
   *  `force` it is a no-op unless the heuristic agrees, which is why the play
   *  loop can fire it after every turn: the gate is the server's, not the
   *  caller's. `upto` bounds the question to a transcript the caller knows was
   *  a clean boundary — same hazard `refreshRollingSummary` documents, since a
   *  question that took an unanswered player post as the scene's END would be
   *  asking about a beat whose reply had not arrived. */
  askSceneBreak: (cid: string, sid: string, force = false, upto?: number) => {
    const params = new URLSearchParams();
    if (force) params.set("force", "true");
    if (upto !== undefined) params.set("upto", String(upto));
    const qs = params.toString();
    return request<SceneBreakAnswer>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/scene-break${qs ? `?${qs}` : ""}`);
  },
  /** "Not here" — retires the proposal and starts the count again from the
   *  scene as it stands, so the same posts cannot re-earn the same suggestion.
   *  Answers with the state it wrote. */
  dismissSceneBreak: (cid: string, sid: string) =>
    request<SceneBreak>("POST", `/api/campaigns/${cid}/scenes/${sid}/scene-break/dismiss`),
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
  // Retcon (#78): the same rewrite `editMessage` makes, plus the reversal of
  // what this scene's absorb wrote and the clearing of `done`, so the scene can
  // be extracted again over the text that is now there. The two are separate
  // calls rather than a flag because they are different intentions — a typo fix
  // must not un-absorb a finished scene.
  retconMessage: (cid: string, sid: string, index: number, content: string) =>
    request<RetconReport>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/messages/${index}/retcon`, { content }),
  // Retcon replay (#79). `fresh` for every read here, like the alternates and
  // proposal reads: the session moves with each step of the walk, and a shared
  // read is as old as the request it joined.
  replayPreview: (cid: string, sid: string, index: number) =>
    request<ReplayPreview>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/replay/preview?index=${index}`,
      undefined, { fresh: true }),
  getReplay: (cid: string, sid: string) =>
    request<ReplaySession | null>("GET", `/api/campaigns/${cid}/scenes/${sid}/replay`,
                                  undefined, { fresh: true }),
  // Answers with the session in the same shape `getReplay` does, plus the
  // cascade's report — the backlog is never on the wire, however this is asked.
  startReplay: (cid: string, sid: string, index: number) =>
    request<ReplaySession & { cascade: CascadeReport }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/replay`, { index }),
  // Streams like `chat` and for the same reason: it re-posts the player's own
  // words and then generates one reply against the edited history. Rerolling
  // that reply is plain `regenerate` — it is the trailing run.
  replayTurn: (cid: string, sid: string, onEvent: (e: ChatEvent) => void,
               signal?: AbortSignal) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/replay/turn`, undefined, onEvent, signal),
  acceptReplay: (cid: string, sid: string) =>
    request<ReplaySession | null>("POST", `/api/campaigns/${cid}/scenes/${sid}/replay/accept`),
  // `restore` defaults to putting the unreplayed originals back — sent
  // explicitly so the destructive answer is never the one a dropped field
  // produces.
  cancelReplay: (cid: string, sid: string, restore: boolean) =>
    request<{ scene: string; restored: number; dropped: number }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/replay/cancel`, { restore }),
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
