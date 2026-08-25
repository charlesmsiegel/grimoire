import { isAbortError, parseSSEChunk, type ChatEvent, type LocalizeEvent,
  type ChubGalleryEvent, type RunHandle, type TaglineBatchEvent } from "./stream";
import { campaignsChanged, configChanged } from "../appEvents";
import { isProviderFailure } from "./errors";
import { encodeSegment } from "../urlSegment";
// `errorText` and `isOffline` used to live here, next to `ApiError`. They are
// in `./errors` now — a leaf with no imports of its own, for the reason its
// docstring gives: the components that render an error are tested against a
// mock that replaces THIS module wholesale.

// Re-exported so every existing `from "../api/client"` import keeps working;
// imported by name for the ones the calls below actually mention.
export * from "./types";
import {
  type Actor, type AdvanceDigest, type AdvanceRequest, type Appearance, type Availability,
  type BackupList, type BackupRun, type Briefing, type CalendarConfig, type CalendarMonth,
  type CalendarScope, type CampaignClock, type CampaignImage, type CampaignMeta,
  type CampaignModule, type Card,
  type CampaignBudget,
  type CampaignSceneCosts,
  type CardFormat, type CascadeReport, type Casefile, type CastChanges, type CastDetail,
  type ForkReport,
  type CatalogDraft, type CharacterDetail,
  type CharacterSummary, type CheckResolution, type ChronicleEntry, type ChubImportResult,
  type ChubUnlinkedVersion, type Climate, type ClimateSummary, type Config, type ConfigUpdate,
  type DataDirInfo, type DivergedRecord, type Dossiers, type EntityDetail, type EntityKind,
  type EntityScope,
  type EntitySummary, type ErrorSummary, type GalleryImage, type Greeting, type GreetingDetail, type GreetingDraft,
  type GroupState, type HealthCheckResult,
  type IncomingItem, type IncomingRef, type JournalEntry, type LLMConnection,
  type LLMConnectionDetail, type LLMConnectionDraft, type Ledger, type LengthPreset,
  type LibraryDependent, type LibraryKind, type LibraryStatus,
  type LogLevel, type LogLevelInfo, type LogPage, type LogTailEvent,
  type LoreEntryDraft, type Mechanics, type Message, type Model,
  type ModelsRefreshResult,
  type ModuleContentEntry,
  type ModuleDetail, type ModuleEditResult, type ModuleRenameKind, type ModuleSummary,
  type PCDetail, type PCSummary, type Persona, type PinRule, type PricingEntry,
  type PricingTable, type PromptDiff, type PromptEntry,
  type PromptLayout, type PromptSnapshot, type ProposalRecord, type Provenance,
  type RecordChange, type RegenerateOverrides, type ReplayPreview, type ReplaySession, type ResponseBundle, type ResponseFields, type ResponseOverride,
  type ResponsePresetDetail, type ResponsePresetDraft, type ResponsePresetSummary,
  type ResponsePresetUsage, type RollEntry, type RollingSummary, type RollingSummaryRefresh,
  type RetconReport, type RosterEntry, type RoutingBundle, type ScenarioImportResult, type ScenarioProposal, type SceneAbsorb,
  type SceneAlternates, type SceneCheckActor, type SceneContext, type SceneDatetime,
  type SceneIdea, type SceneIdeaDraft, type SceneImportDraft, type SceneIntentResult,
  type SceneLocation,
  type SceneBreak, type SceneBreakAnswer,
  type SceneMeta, type ScenePage, type SceneSuggestion, type SceneUsage,
  type SceneWeather, type ScheduledEvent, type SearchMode,
  type SearchResult, type Sheet, type SheetBulkResult, type SheetCoverage,
  type SheetExpected, type SheetRoster, type StagedEdit, type Stats,
  type StoreConflicts, type Style, type StyleDetail, type StyleDraft, type Suggestion,
  type Timeline, type TimelineEvent, type UndescribedImage,
  type WeatherOverrideBody, type WeatherRangeBody,
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
    const err = new ApiError(res.status, data.detail ?? res.statusText, data.kind, data);
    // A provider that just refused us has changed what the status bar is
    // claiming about it (#146). The server recorded the failure as it
    // happened; this is what makes the client go and read it, rather than
    // showing yesterday's verdict until the next navigation.
    if (isProviderFailure(err)) {
      invalidateConfigCache();
      configChanged();
    }
    throw err;
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
  const p: Promise<T> = requestRaw<T>(method, path, body).finally(() => {
    if (inflightGets.get(path) === p) inflightGets.delete(path);
  });
  inflightGets.set(path, p);
  return p;
}

/** How often a computing run is polled while it works, and how far that backs
 *  off once it is clearly going to take a while.
 *
 *  The request is local and the answer is a few hundred bytes, so a second is
 *  cheap for the case that matters -- an audit retry that answers in ten -- but
 *  an absorb runs for minutes, and asking six hundred times to learn the same
 *  thing is noise in every log the user might read. So it stays at
 *  `RUN_POLL_MS` for the first `RUN_POLL_QUICK` asks and then settles at
 *  `RUN_POLL_SLOW_MS`, which is the most a reader ever waits to be told their
 *  review is ready. Exported so a test can drive the cadence rather than infer
 *  it from a sleep.
 */
export const RUN_POLL_MS = 1000;
export const RUN_POLL_QUICK = 10;
export const RUN_POLL_SLOW_MS = 5000;
/** How many consecutive polls may fail before the wait gives up. Generous,
 *  because the failures this rides out are exactly the ones the feature is
 *  about, and cheap, because a run that is really gone answers the same 404
 *  each time and the store is asked next. */
export const RUN_POLL_MISSES = 5;
/** How many times a durable READ is retried before its failure is reported.
 *  The store is where a detached run's answer lives, so a dropped fetch on the
 *  way to it is the same class of nothing-happened as a dropped poll. */
export const READ_RETRIES = 3;

/** Wait for a detached run to stop running, and answer with what it became.
 *
 *  A poll, not a held connection, and that is the whole point: the client may
 *  not be there for the whole of a ten-minute absorb -- a locked phone, a
 *  suspended tab, a closed laptop -- and nothing about the run depends on it
 *  being there. Coming back and asking again is a complete recovery.
 *
 *  A failed run is raised as the HTTP failure the same work would have been
 *  when these routes answered synchronously, status and kind included, so a
 *  caller's existing handling of `missing_key` or a timeout is unchanged --
 *  only where it reads them from moved.
 *
 *  `signal` stops the WAITING. It cannot stop the run: that is what detached
 *  means, and `api.discardReview` (or `api.cancelRun`) is how a caller says
 *  stop.
 */
async function awaitRun(cid: string, sid: string, started: RunHandle,
                        signal?: AbortSignal): Promise<RunHandle> {
  let run = started;
  let missed = 0;
  for (let asked = 0; run.state === "running"; asked++) {
    await sleepUnlessAborted(
      asked < RUN_POLL_QUICK ? RUN_POLL_MS : RUN_POLL_SLOW_MS, signal);
    try {
      run = (await request<{ run: RunHandle }>(
        "GET", `/api/campaigns/${cid}/scenes/${sid}/runs/${run.id}`,
        undefined, { fresh: true })).run;
      missed = 0;
    } catch (err) {
      // A FAILED POLL IS NOT A FAILED RUN. The conditions this feature exists
      // for -- a backgrounded WebView, a suspended tab resuming into a dead
      // socket, a dropped localhost fetch -- all show up here, and ending the
      // wait for one of them reports a failure for a run that is still
      // generating: the reader gets a banner, the composer unlocks over a
      // scene the server is still holding, and nothing opens the review when
      // it does land.
      //
      // Three things are not transient and are not retried: an abort (the
      // caller saying stop), a 404 (the run is gone -- reaped, or never this
      // scene's -- which is decisive and sends the caller to the store, where
      // the answer actually lives), and running out of patience. Everything
      // else is asked again.
      if (isAbortError(err) || (err instanceof ApiError && err.status === 404)) throw err;
      if (++missed <= RUN_POLL_MISSES) continue;
      // OUT OF PATIENCE IS NOT A TERMINAL STATE EITHER. Nothing here has
      // observed the run stop; all that is known is that this one URL has not
      // answered six times running. Reported as a failure, the caller clears
      // its latch and its Stop goes with it, over a run the server may hold
      // for as long as an unbounded budget allows.
      //
      // So the last word goes to a different question, asked of a different
      // path: is this scene still running a review at all? A "yes" resets the
      // patience and the wait goes on; anything else -- including this ask
      // failing too, which is the network really being gone -- lets the
      // original failure stand.
      const still = await api.findRun(cid, sid).then((r) => r.run, () => null);
      if (still?.id !== run.id || still.state !== "running") throw err;
      missed = 0;
    }
  }
  if (run.state === "landed") return run;
  const error = run.error ?? undefined;
  throw new ApiError(error?.status ?? 409,
                     error?.detail ?? `the run ended ${run.state}`,
                     error?.kind ?? run.state, error);
}

/** The stored review a scoped retry folded itself into, when its run is gone.
 *
 *  A retry is a detached run like the absorb, and it merges its phase into the
 *  durable record before its own run record is anything but a receipt. Runs
 *  stop being discoverable after the retention window, and a suspended tab --
 *  a locked phone, the case this whole feature exists for -- is away longer
 *  than that routinely. The poll then 404s on a retry that landed perfectly
 *  well, the panel keeps showing the phase it was retrying, and saving commits
 *  those stale rows and clears the record: the completed retry is gone, and
 *  nothing ever said so.
 *
 *  ONLY for `run_gone`, and that narrowness is the point. A run that ended
 *  `failed` never merged anything, so reading the record back would hand the
 *  panel the PRE-retry phase and present it as this retry's result -- a wrong
 *  answer dressed as a right one. A phase that ran and found nothing reports
 *  that as a status on a landed run and never reaches here at all.
 *
 *  Matched on the generation, for `absorbScene`'s reason: a scene can be
 *  holding a review this retry has nothing to do with.
 */
/** The run a POST may have started, when its reply never arrived.
 *
 *  A POST WITH NO RESPONSE IS AMBIGUOUS. The server can have accepted it,
 *  reserved the run and started generating, and the 202 be lost on the way back
 *  -- a dropped link, a WebView backgrounded in the same second. Reported as a
 *  failure, the caller clears its latch for work that is running: the scene is
 *  held against play for as long as it takes, the next request is refused
 *  `run_in_flight`, and for an absorb there is not even a generation to offer a
 *  Stop with. For a scoped retry it is worse than that -- the run merges into
 *  the durable review while the panel keeps the phase it was retrying, and
 *  saving then commits those stale rows over the completed retry.
 *
 *  Only for a failure that carried NO reply. An `ApiError` means the server
 *  answered -- `already_absorbed`, a missing key, a busy scene -- and every one
 *  of those is the caller's to handle, not a run to adopt.
 *
 *  `kind` is what tells one review run from another: they all share the
 *  `review` class, so an absorb must not adopt a retry and a retry must not
 *  adopt an absorb.
 */
async function adoptLostStart(cid: string, sid: string, kind: string,
                              err: unknown): Promise<{ run: RunHandle;
                                                       generation: string }> {
  if (err instanceof ApiError || isAbortError(err)) throw err;
  const live = await api.liveReview(cid, sid).catch(() => null);
  if (live?.kind !== kind || !live.review_generation) throw err;
  return { run: live, generation: live.review_generation };
}

async function reapedPhase(cid: string, sid: string, generation: string,
                           err: unknown): Promise<SceneAbsorb> {
  if (!(err instanceof ApiError) || err.kind !== "run_gone") throw err;
  const stored = await api.pendingReview(cid, sid).catch(() => null);
  if (!stored?.review || stored.generation !== generation) throw err;
  return stored.review;
}

function sleepUnlessAborted(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      // The same shape `fetch` throws, so `isAbortError` recognises it and a
      // caller that already tells an abort from a failure needs no new branch.
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Read an SSE stream from a GET, for attaching to a run already in flight.
 *
 *  `streamPost` exists for starting work; this exists for joining it. Same
 *  parser, same index callback, no body and no attempt header -- the run is
 *  already named in the path.
 */
async function streamGet<T = ChatEvent>(
  path: string,
  onEvent: (e: T) => void,
  signal?: AbortSignal,
  onIndex?: (index: number) => void,
): Promise<void> {
  const res = await fetch(path, { signal });
  if (!res.ok || !res.body) {
    // Typed rather than left `any`: this app's error handler puts `kind` at the
    // top level of the body, so the shape is known and reading it blind only
    // costs the type-checker's help.
    const data = await res.json().catch(() => ({})) as
      { detail?: string; kind?: string };
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = parseSSEChunk<T>(buffer, decoder.decode(value, { stream: true }),
                              onEvent, onIndex);
  }
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

// `signal` unsubscribes; it no longer cancels. Aborting used to BE the cancel:
// the backend saw the disconnect, persisted whatever the model had produced,
// and unwound. A turn is detached from its request now -- that is the whole
// point, so a locked phone does not kill a generation -- so an abort closes
// only this subscriber and the run carries on. A caller that means "stop
// generating" has to say so, with `api.cancelRun`; aborting alone leaves the
// provider spending and the scene held. Callers still tell an abort from a
// real failure with `isAbortError`.
//
// `attempt` is the caller's own id for this turn, sent as `X-Grimoire-Attempt`.
// It is what makes a turn addressable BEFORE its leading `run` frame arrives:
// the id is chosen here, so a connection that dies between the server
// accepting the work and the first frame reaching the browser still leaves the
// client able to find the run (`api.findRun`) and stop it. It doubles as the
// idempotency key -- re-sending the same id replays the original outcome
// instead of running the turn twice.
async function streamPost<T = ChatEvent>(
  path: string,
  body: unknown,
  onEvent: (e: T) => void,
  signal?: AbortSignal,
  attempt?: string,
  onIndex?: (index: number) => void,
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
    headers: {
      "Content-Type": "application/json",
      ...(attempt ? { "X-Grimoire-Attempt": attempt } : {}),
    },
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
    buffer = parseSSEChunk<T>(buffer, decoder.decode(value, { stream: true }),
                              onEvent, onIndex);
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

/** The `PCCreate` body, which both PC create routes take (#14). */
type PCCreateBody = { name: string; tags?: string[]; version_name?: string; persona?: Persona };

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
  /** `fresh` for the caller refetching *because* a world just changed — the
   *  in-flight share would hand it a read issued before that mutation, which
   *  is precisely the answer it is trying to replace. Same trade
   *  `listCampaigns` makes; a fork can run for a minute, so the read it is
   *  racing is not hypothetical (Codex review). */
  listWorlds: (fresh = false) =>
    request<WorldMeta[]>("GET", "/api/worlds", undefined, { fresh }),
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
  /** Fork `wid` into a brand-new world called `name` (#41) — a deep copy of the
   *  whole directory, sharing nothing with the world it came from and changing
   *  nothing about it. No `invalidateConfigCache`, unlike `createWorld` and
   *  `importWorld`: forking needs a world to fork, so `first_run` was already
   *  false before the call and the cached config still says so. */
  forkWorld: (wid: string, name: string) =>
    request<{ id: string }>("POST", `/api/worlds/${wid}/fork`, { name }),

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
  // `director` (#83) says this turn is a director note rather than a player
  // post: sent, never stored. Omitted from the body unless set, so the server
  // sees the same request every earlier client sent and its own inference —
  // an offscreen scene, or an empty send — still decides those.
  chat: (cid: string, sid: string, content: string, onEvent: (e: ChatEvent) => void,
         response?: ResponseOverride, signal?: AbortSignal, attempt?: string,
         onIndex?: (i: number) => void, director?: boolean) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/chat`,
               { content, ...(response ? { response } : {}), ...(director ? { director: true } : {}) },
               onEvent, signal, attempt, onIndex),
  /** Ask a detached run to stop.
   *
   *  Closing the connection is no longer the cancel. A turn now outlives the
   *  socket that started it -- which is the whole point -- so aborting the
   *  fetch only drops this subscriber: the provider keeps generating, keeps
   *  spending, and persists a reply the player pressed Stop on. The run has to
   *  be told. Best-effort by design: it is a request, not a guarantee, and the
   *  run ends when its provider call unwinds.
   */
  cancelRun: (cid: string, sid: string, runId: string) =>
    request<{ run: RunHandle }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/runs/${runId}/cancel`),

  /** The run this attempt id produced, if the server got that far.
   *
   *  How Stop finds its target when the leading `run` frame never arrived --
   *  the server accepted and detached the turn, and the response died before
   *  the first frame. Without this, that window has nothing to cancel and the
   *  provider runs on after the player stopped it. Answers `{run: null}` on a
   *  scene with no such run rather than 404, so "the send never landed" is an
   *  ordinary answer and not an error to handle.
   */
  /** Stop the turn an attempt id names, whether or not it has a run yet.
   *
   *  Discovery alone is a one-shot lookup, and the POST it is stopping may
   *  have been accepted and then blocked in the server's synchronous setup --
   *  so "no run for this attempt" does not mean nothing is going to happen:
   *  the route can still reserve and detach a turn after the lookup returns.
   *  Recording the cancel against the attempt is what closes that, because the
   *  reservation consumes it.
   */
  cancelAttempt: (cid: string, sid: string, attempt: string) =>
    request<{ run: RunHandle | null }>(
      "POST",
      // A QUERY parameter, because the server takes an attempt id verbatim and
      // one containing `/` cannot survive a path segment -- the router matches
      // on the decoded path, so it would split and reach no route at all.
      `/api/campaigns/${cid}/scenes/${sid}/attempt-cancel`
        + `?attempt=${encodeURIComponent(attempt)}`),

  /** Whether this attempt's post is still in the scene, plus its run if one
   *  is still known.
   *
   *  The durable question, and the only one left once the run record has been
   *  reaped. `retained: false` after a failure means the backend took the
   *  player's post back off the transcript -- so the words exist nowhere but
   *  in this client, and belong back in the composer.
   */
  /** The scene an identity names right now.
   *
   *  What a completion-notification tap resolves through. The intent carries
   *  the identity precisely because a `sid` goes stale on rename and a
   *  notification can sit unread for a long time.
   */
  /** Attach to a run's buffered frames from `from` onward, INCLUSIVE.
   *
   *  The half of detachment the client side rests on. The backend keeps every
   *  frame a run has produced, so a client that was away -- a locked phone, a
   *  suspended tab, a dropped socket -- reads the ones it missed and then keeps
   *  reading live until the run ends. Without this the turn survives on the
   *  server and the screen never shows it.
   *
   *  `from` is one past the last frame actually read, never the run's
   *  `next_index`: that is the live tail, and resuming from it drops everything
   *  generated while the client was away, which is the whole reply.
   */
  attachRun: (cid: string, sid: string, runId: string, from: number,
              onEvent: (e: ChatEvent) => void, signal?: AbortSignal,
              onIndex?: (i: number) => void) =>
    streamGet(`/api/campaigns/${cid}/scenes/${sid}/runs/${runId}/stream?from=${from}`,
              onEvent, signal, onIndex),

  // The THIRD hop of the notification tap, after the two the Android shell
  // makes, and it needs the same treatment: `safe_id` permits characters that
  // are reserved in a URI, so a raw `cid` with a `?` or `#` in it turns the
  // rest of this path into a query or a fragment and the backend is asked
  // about a truncated campaign. `encodeSegment` rather than
  // `encodeURIComponent` for the path part -- it is what every other route
  // here uses, so a segment cannot be encoded two different ways.
  sceneByIdentity: (cid: string, identity: string) =>
    request<{ id: string }>(
      "GET",
      `/api/campaigns/${encodeSegment(cid)}/scene-by-identity`
      + `?identity=${encodeURIComponent(identity)}`,
      undefined, { fresh: true }),

  attemptState: (cid: string, sid: string, attempt: string) =>
    request<{ attempt: string; retained: boolean; run: RunHandle | null }>(
      "GET",
      `/api/campaigns/${cid}/scenes/${sid}/attempt-state`
        + `?attempt=${encodeURIComponent(attempt)}`,
      undefined, { fresh: true }),

  // `attempt` is optional, and the difference matters. WITH one this asks
  // "what became of my send?"; WITHOUT one the route answers with the scene's
  // newest run, which is the only question a client that has lost its local
  // state can ask -- a full reload, or the Android WebView's renderer being
  // restarted, leaves the provider empty while the backend turn generates on.
  findRun: (cid: string, sid: string, attempt?: string) =>
    request<{ run: RunHandle | null }>(
      "GET",
      `/api/campaigns/${cid}/scenes/${sid}/run`
      + (attempt ? `?attempt=${encodeURIComponent(attempt)}` : ""),
      undefined, { fresh: true }),

  // `attempt`/`onIndex` ride every turn producer, not just `chat`. All five are
  // detached server-side, so a call that omits the attempt lets the server mint
  // its own -- and then the id this client recorded names a run the server never
  // had. Stop addresses nothing and recovery asks about an attempt that does not
  // exist, which is precisely the window the registry was built for (codex, P1).
  retry: (cid: string, sid: string, onEvent: (e: ChatEvent) => void, response?: ResponseOverride,
          signal?: AbortSignal, attempt?: string, onIndex?: (i: number) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/retry`,
               response ? { response } : undefined, onEvent, signal, attempt, onIndex),
  /** Reroll the trailing generation.
   *
   *  `body` is one object rather than a parameter each, and that is not
   *  cosmetic: every field of it is optional and the `signal` sits behind
   *  them, so a plain reroll had to spell out an `undefined` per field to
   *  reach it — and the caller, which decides them independently, needed one
   *  branch per COMBINATION. Two fields was already four branches; the route
   *  override (#77) would have made it eight.
   *
   *  Empty fields are dropped rather than sent, so an untouched popover posts
   *  no body at all — `""` and "unset" mean the same thing for all four, and
   *  the server reads a missing field as the standing configuration. Falsy is
   *  the test because every field here is a string or an object; a field whose
   *  `false` or `0` meant something would need its own rule rather than this
   *  one, and would be wrong to add without one.
   */
  regenerate: (cid: string, sid: string, onEvent: (e: ChatEvent) => void,
               body?: RegenerateOverrides, signal?: AbortSignal, attempt?: string,
               onIndex?: (i: number) => void) => {
    const payload = Object.fromEntries(
      Object.entries(body ?? {}).filter(([, v]) => Boolean(v)));
    return streamPost(`/api/campaigns/${cid}/scenes/${sid}/regenerate`,
                      Object.keys(payload).length ? payload : undefined,
                      onEvent, signal, attempt, onIndex);
  },

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
                    onEvent: (e: ChatEvent) => void, signal?: AbortSignal,
                    attempt?: string, onIndex?: (i: number) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/roll-proposal`, body, onEvent, signal,
               attempt, onIndex),
  getSceneChecks: (cid: string, sid: string) =>
    request<{ actors: SceneCheckActor[] }>("GET", `/api/campaigns/${cid}/scenes/${sid}/checks`),
  rollCheck: (cid: string, sid: string,
              body: { check: string; actor: string; difficulty?: number; modifier?: number }) =>
    request<{ ok: boolean; resolution: CheckResolution; message: string }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/check`, body),

  getWorld: (wid: string) =>
    request<{ meta: WorldMeta; body: string; counts: Record<string, number> }>("GET", `/api/worlds/${wid}`),

  /** Every category an entity may be filed under, server-side and in its own
   *  order. Read by the import review tables so their per-row Category
   *  dropdown is the store's list rather than a copy of it (#138). */
  entityKinds: () => request<{ kinds: string[] }>("GET", "/api/entity-kinds"),

  // entities, world or campaign scope
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
  /** Move a record to another generic kind, keeping its id where the
   *  destination is free (#119). World scope sweeps every campaign of that
   *  world and reports which; campaign scope moves only that campaign's copy.
   *  `rev` is the same precondition a save carries: the record is about to be
   *  moved, and moving text somebody else has rewritten is the write it
   *  refuses. The returned id is the one to navigate to -- it differs from
   *  `id` when the destination already held that slug. */
  reclassifyEntity: (scope: EntityScope, kind: EntityKind, id: string,
                     to: EntityKind, rev?: string | null) =>
    request<{ id: string; campaigns?: string[] }>(
      "POST", `${entityBase(scope)}/${kind}/${id}/reclassify`,
      { to, ...(rev ? { rev } : {}) }),

  // library moves: campaign -> world, and back (#52, #53, #60). `kind` is a
  // LibraryKind rather than EntityKind: promote and the status read carry
  // actors too, which the flat-entity CRUD above never does.
  libraryStatus: (cid: string, kind: LibraryKind, id: string) =>
    request<LibraryStatus>("GET", `/api/campaigns/${cid}/${kind}/${id}/library`),
  promoteToLibrary: (cid: string, kind: LibraryKind, id: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/${kind}/${id}/promote`),
  pushToLibrary: (cid: string, kind: LibraryKind, id: string, force = false) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/${kind}/${id}/push`, { force }),
  listDiverged: (cid: string) =>
    request<DivergedRecord[]>("GET", `/api/campaigns/${cid}/diverged`),
  libraryDependents: (wid: string, kind: LibraryKind, id: string) =>
    request<LibraryDependent[]>("GET", `/api/worlds/${wid}/${kind}/${id}/dependents`),
  demoteFromLibrary: (wid: string, kind: LibraryKind, id: string,
                      body: { copy_down: boolean; target?: string | null } = { copy_down: true }) =>
    request<{ copied_down: string[]; dependents: string[] }>(
      "POST", `/api/worlds/${wid}/${kind}/${id}/demote`, body),

  // tags
  listTags: (wid: string) => request<Record<string, string>>("GET", `/api/worlds/${wid}/tags`),
  addTag: (wid: string, name: string) => request<{ id: string }>("POST", `/api/worlds/${wid}/tags`, { name }),
  renameTag: (wid: string, tid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/worlds/${wid}/tags/${tid}`, { name }),
  deleteTag: (wid: string, tid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/tags/${tid}`),

  // characters
  listCharacters: (scope: EntityScope) => request<CharacterSummary[]>("GET", `${entityBase(scope)}/characters`),
  // Both scopes since #60: a campaign-scoped create makes a character who
  // exists only here, with no world counterpart and no sync ref — which is
  // exactly what "emergent" means. `promoteToLibrary` is what ends that.
  createCharacter: (scope: EntityScope, body: { name: string; version_name?: string; card?: Card }) =>
    request<{ character: string; version: string }>("POST", `${entityBase(scope)}/characters`, body),
  readCharacter: (scope: EntityScope, cid: string) =>
    request<CharacterDetail>("GET", `${entityBase(scope)}/characters/${cid}`),
  setDefaultVersion: (scope: EntityScope, cid: string, vid: string) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/characters/${cid}`, { default_version: vid }),
  /** Rename the container (#13). Scope-aware, unlike `setCharacterBirthdate`:
   *  the Name field is editable in campaign scope too, where the write
   *  materializes the campaign's own copy and leaves the world's name alone. */
  setCharacterName: (scope: EntityScope, cid: string, name: string) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/characters/${cid}/name`, { name }),
  setCharacterBirthdate: (wid: string, cid: string, birthdate: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/characters/${cid}/birthdate`, { birthdate }),
  // Both scopes since #60: campaign scope removes the character from THIS
  // campaign -- an emergent NPC outright, an inherited one by tombstone --
  // and leaves the library's alone. Creating one you cannot delete was the
  // gap this closes.
  deleteCharacter: (scope: EntityScope, cid: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/characters/${cid}`),
  getCharacterTagline: (wid: string, cid: string) =>
    request<{ tagline: string }>("GET", `/api/worlds/${wid}/characters/${cid}/tagline`),
  setCharacterTagline: (wid: string, cid: string, tagline: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/characters/${cid}/tagline`, { tagline }),
  generateCharacterTagline: (wid: string, cid: string) =>
    request<{ tagline: string }>("POST", `/api/worlds/${wid}/characters/${cid}/tagline/generate`),
  /** Derive a tagline for every character in the world that has none (#57).
   *
   *  A stream rather than a call per character: the roster can be hundreds
   *  long, each entry costs a provider call, and a progress line the user can
   *  walk away from is the difference between a feature and a frozen tab.
   *  Unlike `generateCharacterTagline` this PERSISTS as it goes — see
   *  `TaglineBatchEvent` — so nothing here needs a save afterwards. */
  generateWorldTaglines: (wid: string, onEvent: (e: TaglineBatchEvent) => void,
                          signal?: AbortSignal) =>
    streamPost<TaglineBatchEvent>(`/api/worlds/${wid}/characters/taglines/generate`,
                                  undefined, onEvent, signal),
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
    request<{ name: string; ext: string; v: string; description?: string; described?: boolean }[]>(
      "GET", `${entityBase(scope)}/${kind}/${eid}/images`),
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

  // ---- the campaign's own image library (#376) ----
  /** Images that belong to the campaign and to none of its records — what a
   *  narrator post has to draw on, since "Grimoire" is not an actor with a
   *  version to hold art. `name` goes into the URL raw: the server accepts
   *  only names that survive one (`store.campaign_images.addressable`), which
   *  is the same rule its listing filters by, so a name this builder is ever
   *  handed is already URL- and markdown-safe. */
  campaignImageUrl: (cid: string, name: string, opts?: { w?: number; v?: string }) => {
    const q = new URLSearchParams();
    if (opts?.w) q.set("w", String(opts.w));
    if (opts?.v) q.set("v", opts.v);
    const qs = q.toString();
    return `/api/campaigns/${cid}/images/${name}${qs ? `?${qs}` : ""}`;
  },
  listCampaignImages: (cid: string) =>
    request<CampaignImage[]>("GET", `/api/campaigns/${cid}/images`),
  putCampaignImage: (cid: string, name: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<CampaignImage>(`/api/campaigns/${cid}/images/${name}`, form, "PUT");
  },
  deleteCampaignImage: (cid: string, name: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/images/${name}`),
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
  // Both PC creates post the one `PCCreate` body, so both spell it the same:
  // `version_name` names the first version (the server defaults it to
  // "default"), exactly as `createCharacter` already names a character's.
  createCampaignPC: (cid: string, body: PCCreateBody) =>
    request<{ pc: string; version: string }>("POST", `/api/campaigns/${cid}/pcs`, body),
  listCampaignPCs: (cid: string) => request<PCSummary[]>("GET", `/api/campaigns/${cid}/pcs`),
  createPC: (wid: string, body: PCCreateBody) =>
    request<{ pc: string; version: string }>("POST", `/api/worlds/${wid}/pcs`, body),
  readPC: (scope: EntityScope, pid: string) => request<PCDetail>("GET", `${entityBase(scope)}/pcs/${pid}`),
  updatePC: (scope: EntityScope, pid: string, patch: { default_version?: string; tags?: string[] }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/pcs/${pid}`, patch),
  deletePC: (scope: EntityScope, pid: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/pcs/${pid}`),
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
                            predecessor_join?: string; pcless?: boolean; location?: string;
                            rev?: string }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/greetings/${gid}`, patch),
  deleteGreeting: (scope: EntityScope, gid: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/greetings/${gid}`),
  setEdges: (scope: EntityScope, gid: string, edges: { leads_to?: string[]; excludes?: string[] }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/greetings/${gid}/edges`, edges),
  importGreetings: (wid: string, body: { character: string; version: string }) =>
    request<{ greetings: string[] }>("POST", `/api/worlds/${wid}/greetings/import`, body),
  getGreetingSubjects: (wid: string, gid: string) =>
    request<Record<string, string[]>>("GET", `/api/worlds/${wid}/greetings/${gid}/subjects`),
  /** Every image in this scope with no description entry — the backlog
   *  `DescribeQueue` steps through. A world's queue is its own art; a
   *  campaign's is only what the world's cannot reach — its own image library,
   *  which hangs off no record, and art it has diverged. */
  listUndescribedImages: (scope: EntityScope) =>
    request<UndescribedImage[]>("GET", `${entityBase(scope)}/images/undescribed`),
  /** Every image in a world, from all eight bases, in one response (#200).
   *  World-scoped only: a campaign reaches most of its art through its world,
   *  and the art it has diverged is listed in its own editors — the same split
   *  `listUndescribedImages` draws. One request rather than one per record per
   *  version, which is the whole reason the route exists.
   *
   *  `fresh` for a read taken AFTER a write, which is the case the coalescing
   *  above cannot serve: handed a promise issued before the subjects PUT, the
   *  caller gets a pre-write answer and leaves the tile it just tagged marked
   *  unfinished. Same reasoning, and the same flag, as `campaignChanges`. */
  listWorldImages: (wid: string, fresh = false) =>
    request<GalleryImage[]>("GET", `/api/worlds/${wid}/gallery`, undefined, { fresh }),
  /** Describe one image. `description: ""` is meaningful and is NOT the same as
   *  never having described it: it means "reviewed, nothing to say", which
   *  takes the image out of the describe queue without offering it to the
   *  model. Campaign-scoped writes land campaign-side, so describing art a
   *  thin campaign still inherits does not diverge the art itself. */
  setCharacterImageDescription: (scope: EntityScope, cid: string, vid: string,
                                 name: string, description: string) =>
    request<{ ok: boolean }>("PUT",
      `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/${encodeSegment(name)}/description`,
      { description }),
  /** Ask the model what a picture shows. A PREVIEW: the caller decides whether
   *  to keep it and persists through `setCharacterImageDescription`. World-side
   *  only — a description drafted from the bytes is a claim about the bytes,
   *  and a campaign reaches most of its art through its world. */
  draftCharacterImageDescription: (wid: string, cid: string, vid: string, name: string) =>
    request<{ description: string }>("POST",
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${encodeSegment(name)}/description/draft`),
  draftPCImageDescription: (wid: string, pid: string, vid: string, name: string) =>
    request<{ description: string }>("POST",
      `/api/worlds/${wid}/pcs/${pid}/versions/${vid}/images/${encodeSegment(name)}/description/draft`),
  draftEntityImageDescription: (wid: string, kind: EntityKind, eid: string, name: string) =>
    request<{ description: string }>("POST",
      `/api/worlds/${wid}/${kind}/${eid}/images/${encodeSegment(name)}/description/draft`),
  draftCampaignImageDescription: (cid: string, name: string) =>
    request<{ description: string }>("POST",
      `/api/campaigns/${cid}/images/${encodeSegment(name)}/description/draft`),
  setPCImageDescription: (scope: EntityScope, pid: string, vid: string,
                          name: string, description: string) =>
    request<{ ok: boolean }>("PUT",
      `${entityBase(scope)}/pcs/${pid}/versions/${vid}/images/${encodeSegment(name)}/description`,
      { description }),
  setEntityImageDescription: (scope: EntityScope, kind: EntityKind, eid: string,
                              name: string, description: string) =>
    request<{ ok: boolean }>("PUT",
      `${entityBase(scope)}/${kind}/${eid}/images/${encodeSegment(name)}/description`, { description }),
  setCampaignImageDescription: (cid: string, name: string, description: string) =>
    request<{ ok: boolean }>("PUT",
      `/api/campaigns/${cid}/images/${encodeSegment(name)}/description`, { description }),
  setImageSubjects: (wid: string, gid: string, name: string, subjects: string[]) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/greetings/${gid}/images/${name}/subjects`, { subjects }),
  listImageAppearances: (wid: string, cid: string) =>
    request<Appearance[]>("GET", `/api/worlds/${wid}/characters/${cid}/appearances`),
  /** The greeting-image tagging backlog. `fresh` for the same reason
   *  `listWorldImages` takes it: a re-read after the queue has written is
   *  asking what the store holds NOW, and a promise issued before those PUTs
   *  answers a question nobody asked. */
  listUntaggedImages: (wid: string, fresh = false) =>
    request<Appearance[]>("GET", `/api/worlds/${wid}/subjects/untagged`, undefined, { fresh }),
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
  /** `seedLocation` false means the caller has already decided this scene's
   *  location (including deciding it has none), so the greeting's own must not
   *  be seeded over that — see StartFromGreeting.seed_location (#218). */
  startFromGreeting: (cid: string, sid: string, greeting: string, seedLocation = true) =>
    request<{ ok: boolean; id: string }>("POST", `/api/campaigns/${cid}/scenes/${sid}/start-from-greeting`,
                                         { greeting, seed_location: seedLocation }),
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
  // Both scopes: a campaign's calendar, and the world default it was created
  // from (#223). One store file (calendar.json) under two roots, so the scope
  // is carried in the URL and nowhere else.
  getCalendarConfig: (scope: CalendarScope) =>
    request<CalendarConfig>("GET", `${entityBase(scope)}/calendar`),
  getCalendarProviders: () =>
    request<{ providers: { id: string; name: string }[] }>("GET", "/api/calendars/providers"),

  // ---- the campaign clock (#100) ----
  getCampaignClock: (cid: string) =>
    request<CampaignClock>("GET", `/api/campaigns/${cid}/clock`),
  /** The digest an advance would produce, writing nothing. Needs no reason. */
  previewAdvance: (cid: string, body: AdvanceRequest) =>
    request<{ digest: AdvanceDigest }>("POST", `/api/campaigns/${cid}/advance/preview`, body),
  advanceTime: (cid: string, body: AdvanceRequest) =>
    /** `fired` is the subset of `digest.events` this move actually stamped —
     *  empty for a backward correction, which reports what it un-lived without
     *  un-firing it. */
    request<{ ok: boolean; moved: boolean; now: string; friendly: string;
              digest: AdvanceDigest; fired: ScheduledEvent[] }>(
      "POST", `/api/campaigns/${cid}/advance`, body),

  // ---- scheduled events (#101) ----
  campaignEvents: (cid: string) =>
    // `fresh`: the clock fires events, so a cached list would show a campaign
    // its own past as still upcoming. `now` is the moment `passed` was judged
    // against, so the panel can say what "already gone by" means here.
    request<{ events: ScheduledEvent[]; now: string; friendly: string }>(
      "GET", `/api/campaigns/${cid}/events`, undefined, { fresh: true }),
  createCampaignEvent: (cid: string, body: { name: string; date: string; note?: string }) =>
    request<{ ok: boolean; id: string }>("POST", `/api/campaigns/${cid}/events`, body),
  /** Every field is optional: what is not sent keeps the stored value. */
  updateCampaignEvent: (cid: string, eid: string,
                        body: { name?: string; date?: string; note?: string }) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/events/${eid}`, body),
  /** Take back a fire stamp — the undo for an advance made by mistake. */
  unfireCampaignEvent: (cid: string, eid: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/events/${eid}/unfire`),
  deleteCampaignEvent: (cid: string, eid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/events/${eid}`),

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
    request<{ months: CalendarMonth[] }>("GET", `${entityBase(scope)}/calendar/months?year=${year}`),
  setCalendarConfig: (scope: CalendarScope, cfg: CalendarConfig) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/calendar`, cfg),
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
  refreshConnectionModels: (id: string) =>
    request<ModelsRefreshResult>("POST", `/api/llm-connections/${id}/models/refresh`)
      .then(notifyConfig),
  /** The catalog for a connection that has been described but not saved (#149)
   *  — the New-connection form and the setup wizard, where there is no id to
   *  refresh yet. Nothing is cached server-side and nothing is stored. */
  previewModels: (draft: CatalogDraft) =>
    request<{ models: Model[] }>("POST", "/api/model-catalog", draft),
  /** Ask this connection's provider whether it can serve, right now (#146).
   *
   *  Resolves for a *failing* connection too: the answer is in `ok`, and the
   *  request only rejects when the question itself could not be asked (an id
   *  that does not exist). Invalidates the cached config because the check's
   *  verdict is what the status bar reads. */
  checkConnection: (id: string) =>
    request<HealthCheckResult>("POST", `/api/llm-connections/${id}/health`).then((r) => {
      invalidateConfigCache();
      return notifyConfig(r);
    }),

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

  // Per-task model routing (#142), both scopes. `fresh` on the reads: the
  // bundle carries what OTHER scopes resolve to, so a cached copy would show
  // an inherited value from before the write that prompted the reload.
  getGlobalRouting: () => request<RoutingBundle>("GET", "/api/routing", undefined, { fresh: true }),
  // `notifyConfig` on both writes: the status bar names the model the next turn
  // will run on, and a route is now one of the things that decides it.
  setGlobalRouting: (routes: Record<string, string>) =>
    request<RoutingBundle>("PUT", "/api/routing", { routes }).then(notifyConfig),
  getCampaignRouting: (cid: string) =>
    request<RoutingBundle>("GET", `/api/campaigns/${cid}/routing`, undefined, { fresh: true }),
  setCampaignRouting: (cid: string, routes: Record<string, string>) =>
    request<RoutingBundle>("PUT", `/api/campaigns/${cid}/routing`, { routes }).then(notifyConfig),

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
  getCampaignSceneCosts: (cid: string, order = "cost") =>
    request<CampaignSceneCosts>(
      "GET", `/api/campaigns/${cid}/usage/scenes?order=${encodeURIComponent(order)}`),
  // The per-model rate table (#158). `fresh` on the read, because saving a rate
  // and seeing the old table is the one thing an editor must not do.
  getPricing: () => request<PricingTable>("GET", "/api/pricing", undefined, { fresh: true }),
  setPricing: (rates: Record<string, PricingEntry>) =>
    request<{ rates: Record<string, PricingEntry> }>("PUT", "/api/pricing", { rates }),
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
  // `against` is another entry id, or "live" for the composition as it stands
  // now — the comparison the feature is named for (#130). `fresh`, unlike
  // `getScenePrompt` above and for the reason that route is not: against "live"
  // only one end is frozen, so the answer moves with the store, and it is
  // re-read on the refreshKey a completed turn bumps precisely because that
  // turn is what moved it. Turn-against-turn could be shared, but one cache
  // rule for one route is worth more than the request it would save.
  getScenePromptDiff: (cid: string, sid: string, eid: string, against: string) =>
    request<PromptDiff>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/prompts/${eid}/diff`
             + `?against=${encodeURIComponent(against)}`, undefined, { fresh: true }),
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
               signal?: AbortSignal, attempt?: string, onIndex?: (i: number) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/replay/turn`, undefined, onEvent, signal,
               attempt, onIndex),
  acceptReplay: (cid: string, sid: string) =>
    request<ReplaySession | null>("POST", `/api/campaigns/${cid}/scenes/${sid}/replay/accept`),
  // `restore` defaults to putting the unreplayed originals back — sent
  // explicitly so the destructive answer is never the one a dropped field
  // produces.
  cancelReplay: (cid: string, sid: string, restore: boolean) =>
    request<{ scene: string; restored: number; dropped: number }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/replay/cancel`, { restore }),
  /** The run this id names, right now. What a review is polled through.
   *
   *  A review is not a stream: End Scene is a form to read, not text arriving
   *  word by word, so there is nothing to tail and `attachRun` is the wrong
   *  shape for it. The server answers the POST with a run and the client asks
   *  about it until it stops running. */
  getRun: (cid: string, sid: string, runId: string) =>
    request<{ run: RunHandle }>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/runs/${runId}`,
      undefined, { fresh: true }),

  /** Wait for a detached run to stop, and answer with what it became.
   *
   *  Exported so the review panel can adopt a run it did not start -- one that
   *  was still generating when the phone locked, which is the case this whole
   *  feature exists for. */
  awaitRun: (cid: string, sid: string, run: RunHandle, signal?: AbortSignal) =>
    awaitRun(cid, sid, run, signal),

  /** The review still being prepared for this scene, if one is.
   *
   *  Asked when there is no stored review and the panel wants to know whether
   *  that means "none" or "not yet". The class check is the whole of it: the
   *  newest run on a scene is as likely to be a chat turn, and adopting one of
   *  those as a review would leave End Scene spinning over a reply. */
  liveReview: async (cid: string, sid: string) => {
    // RETRIED, for `pendingReview`'s reason and with more riding on it. This is
    // the question the adoption pass asks FIRST, and a rejection is not an
    // answer of "nothing is running": read as one, it opens a stored payload a
    // live retry is about to replace, and the reviewer's approvals are then
    // saved over the retry that landed underneath them.
    for (let asked = 0; ; asked++) {
      try {
        const found = (await api.findRun(cid, sid)).run;
        return found && found.state === "running" && found.cls === "review"
          ? found : null;
      } catch (err) {
        const transient = !(err instanceof ApiError) || err.kind === "busy";
        if (!transient || isAbortError(err) || asked >= READ_RETRIES) throw err;
        await sleepUnlessAborted(RUN_POLL_MS);
      }
    }
  },

  /** This scene's stored end-of-scene review, if one is waiting to be saved.
   *
   *  `review` is null and `stale` is set when the transcript has moved since
   *  the review was prepared -- the reviewer is told to re-run rather than
   *  shown a summary of posts that are no longer there. Both null means there
   *  is simply no review, which is the ordinary case on every mount. */
  pendingReview: async (cid: string, sid: string) => {
    // RETRIED, for `awaitRun`'s reason and in the place it matters most. This
    // read is the answer to "did the absorb land", and every caller treats a
    // rejection as "it did not": the latch clears, the panel never opens, and
    // the adoption effect that would have asked again does not re-run while
    // the same scene stays selected. So a dropped fetch on the one read that
    // happens right after a locked phone comes back loses a review that is
    // sitting on disk -- the loss this whole feature exists to prevent, in the
    // last two lines of it.
    //
    // Transient failures only. A `busy` 409 is the sidecar held for a moment
    // by a sync client and clears on its own; `review_unreadable` and a 404
    // are answers, and asking again just spends time before reporting them.
    for (let asked = 0; ; asked++) {
      try {
        return await request<{ review: SceneAbsorb | null; generation: string | null;
                               stale: { prepared_posts: number; current_posts: number } | null }>(
          "GET", `/api/campaigns/${cid}/scenes/${sid}/pending-review`,
          undefined, { fresh: true });
      } catch (err) {
        const transient = !(err instanceof ApiError) || err.kind === "busy";
        if (!transient || isAbortError(err) || asked >= READ_RETRIES) throw err;
        await sleepUnlessAborted(RUN_POLL_MS);
      }
    }
  },

  /** Throw the stored review away, and stop whatever is still making it.
   *
   *  Named by GENERATION rather than by run id: an absorb and the retries of
   *  its phases all belong to one review, and Cancel means "stop preparing
   *  this", not "stop the most recent thing". Answers only once the runs it
   *  flagged have really stopped, so the caller may start a fresh absorb
   *  immediately -- the scene's exclusion key is free by then. */
  discardReview: (cid: string, sid: string, generation: string) =>
    request<{ removed: boolean; stopped: number }>(
      "DELETE", `/api/campaigns/${cid}/scenes/${sid}/pending-review`
        + `?generation=${encodeURIComponent(generation)}`),

  // `force` re-runs an absorb the backend has already recorded in the chronicle;
  // without it that POST is a 409 (kind "already_absorbed") -- see #235.
  //
  // Detached (#396): the POST answers 202 the moment the run is reserved, so
  // there is no socket for a locked phone to take down, and the review is read
  // back off the store once the run lands. The wait is the client's now, and
  // it is a poll rather than a held connection precisely because the client
  // may not be there for the whole of it.
  //
  // `onStarted` is handed the review's generation the moment the POST answers,
  // minutes before the review itself. That is the only window in which the
  // caller can offer a way OUT: a `review` holds the scene against play for as
  // long as it runs, and `absorb_budget = 0` means nothing bounds that -- so a
  // panel with no generation to name has no way to stop what it started.
  absorbScene: async (cid: string, sid: string, force = false,
                      onStarted?: (generation: string) => void) => {
    let started: { run: RunHandle; generation: string };
    try {
      started = await request<{ run: RunHandle; generation: string }>(
        "POST", `/api/campaigns/${cid}/scenes/${sid}/absorb${force ? "?force=true" : ""}`);
    } catch (err) {
      // A POST WITH NO RESPONSE IS AMBIGUOUS. The server can have accepted it,
      // reserved the run and started generating, and the 202 be lost on the way
      // back -- a dropped link, a WebView backgrounded in the same second. The
      // caller then clears its latch for an absorb that is running: the scene
      // is held against play for as long as it takes, End scene answers
      // `run_in_flight`, and there is no generation to offer a Stop with,
      // because `onStarted` never fired.
      //
      // Only for a failure that carried NO reply. An `ApiError` means the
      // server answered -- `already_absorbed`, a missing key, a busy scene --
      // and every one of those is the caller's to handle, not a run to adopt.
      // ...and it has to be an ABSORB. `review` is the class a whole review's
      // runs share, so a scoped retry of some EARLIER review's phase wears it
      // too -- and adopting one would install that review's generation and
      // hand its summary back as this End scene's result.
      started = await adoptLostStart(cid, sid, "absorb", err);
    }
    onStarted?.(started.generation);
    try {
      await awaitRun(cid, sid, started.run);
    } catch (err) {
      // THE STORE IS THE ANSWER, not the run record. A run stops being
      // discoverable after `REAP_SECONDS`, and a suspended tab -- a locked
      // phone, exactly the case this feature is for -- can easily be away
      // longer than that: the poll then 404s on a run whose review landed
      // perfectly well and is sitting on disk. Failing there would report the
      // one loss this whole change exists to prevent.
      //
      // MATCHED ON THE GENERATION, not merely on something being stored. A
      // scene can already be holding an EARLIER review the reader never saved,
      // and handing that back for a run that genuinely failed would show them
      // a stale summary as this absorb's result -- a wrong answer presented as
      // a right one, which is worse than the failure it replaced.
      const recovered = await api.pendingReview(cid, sid).catch(() => null);
      if (!recovered?.review || recovered.generation !== started.generation) throw err;
      return { review: recovered.review, generation: recovered.generation };
    }
    const pending = await api.pendingReview(cid, sid);
    if (!pending.review || pending.generation !== started.generation) {
      // The run landed and this run's record is not what is there. Either the
      // scene moved on between the two -- a turn appended, a post was cut,
      // which is what the watermark exists to catch -- or the review was
      // discarded while it was being prepared. Reporting the refusal beats
      // handing the panel a null it would render as an empty review, or
      // somebody else's review as this one's.
      throw new ApiError(409, "the scene changed while the review was being "
        + "prepared — end the scene again", "review_stale",
        pending.stale ?? undefined);
    }
    return { review: pending.review, generation: pending.generation };
  },
  saveChronicle: (cid: string, sid: string,
                  body: { one_line: string; summary: string; keywords: string[];
                          timeline_events: TimelineEvent[]; edits: StagedEdit[];
                          commit_token?: string }) =>
    request<ChronicleEntry & { applied: string[];
      failures: { id: string; reason: string; kind: "conflict" | "error" }[] }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/chronicle`, body),
  getChronicle: (cid: string) =>
    request<ChronicleEntry[]>("GET", `/api/campaigns/${cid}/chronicle`),
  // Both scoped retries are detached runs of their own (#396), folded into the
  // stored review by the server as well as into the panel by the caller. The
  // `signal` no longer reaches the work -- it stops this client WAITING, which
  // is all an abort could ever do once the run outlives the request. What stops
  // the work is `discardReview`, which flags every run preparing that review.
  //
  // Each still answers with what THIS retry produced rather than with the
  // merged review, because that is the question the panel is asking: the
  // reviewer's own typing and approvals live only in the browser, so the merge
  // on screen has to be the local one and the stored merge is what a client
  // coming back later reads.
  retryAudit: async (cid: string, sid: string, signal?: AbortSignal) => {
    let started: { run: RunHandle; generation: string };
    try {
      started = await request<{ run: RunHandle; generation: string }>(
        "POST", `/api/campaigns/${cid}/scenes/${sid}/audit`, undefined, { signal });
    } catch (err) {
      started = await adoptLostStart(cid, sid, "audit", err);
    }
    try {
      const run = await awaitRun(cid, sid, started.run, signal);
      return (run.result ?? {}) as unknown as { mechanics: Mechanics; edits: StagedEdit[] };
    } catch (err) {
      // The server folded this retry into the stored review before the run
      // record was reaped, so the phase is on disk even though the run is not.
      const stored = await reapedPhase(cid, sid, started.generation, err);
      return { mechanics: stored.mechanics,
               edits: stored.edits.filter((e) => e.kind === "sheet") };
    }
  },
  // The dossier phase's sibling to retryAudit (#286): re-runs that phase alone,
  // on a fresh budget, without disturbing the rest of the open review.
  retryDossiers: async (cid: string, sid: string, signal?: AbortSignal) => {
    let started: { run: RunHandle; generation: string };
    try {
      started = await request<{ run: RunHandle; generation: string }>(
        "POST", `/api/campaigns/${cid}/scenes/${sid}/dossiers`, undefined, { signal });
    } catch (err) {
      started = await adoptLostStart(cid, sid, "dossiers", err);
    }
    try {
      const run = await awaitRun(cid, sid, started.run, signal);
      return (run.result ?? {}) as unknown as { dossiers: Dossiers; edits: StagedEdit[] };
    } catch (err) {
      const stored = await reapedPhase(cid, sid, started.generation, err);
      // `proposed` is the phase's own list of who it prepared a dossier for --
      // the same projection `pending_reviews.merge_dossiers` kept the rows by,
      // so this reads back exactly the rows that retry contributed.
      const proposed = new Set(stored.dossiers?.proposed ?? []);
      return { dossiers: stored.dossiers,
               edits: stored.edits.filter(
                 (e) => e.kind === "dossier" && proposed.has(e.target?.id)) };
    }
  },
  opener: (cid: string, sid: string, prompt: string, onEvent: (e: ChatEvent) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/opener`, { prompt }, onEvent),
  firstPost: (cid: string, sid: string, text: string) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/scenes/${sid}/first-post`, { text }),

  // scene import (#92). The same split as the lorebook pair below: parse
  // writes NOTHING and hands back a draft to review, and only sceneImport
  // creates the scene — in one request, so a half-imported scene cannot exist
  // because the browser navigated away between two of them.
  sceneImportParse: (cid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<SceneImportDraft>(`/api/campaigns/${cid}/scenes/import/parse`, form);
  },
  sceneImport: (cid: string, body: {
    title: string; date: string; location: string; pcless: boolean;
    messages: Message[]; cast: { kind: string; id: string; role?: string }[];
    turn_sizes: number[] | null;
  }) => request<{ id: string; messages: number; cast: number }>(
    "POST", `/api/campaigns/${cid}/scenes/import`, body),

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
  getCampaignSheetRoster: (cid: string) =>
    request<{ roster: SheetRoster }>("GET", `/api/campaigns/${cid}/sheets/roster`),
  createMissingSheets: (cid: string, types: Record<string, string>) =>
    request<SheetBulkResult>("POST", `/api/campaigns/${cid}/sheets/create-missing`,
      { types }),
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

  // ---- observability (#154/#155/#156) ----
  //
  // Three reads and one stream over two files. Every one of them is `fresh`:
  // this page exists to show what is happening RIGHT NOW, and a reply shared
  // with an in-flight read of the same path would answer a reload with the
  // numbers that prompted it.
  getStats: (days: number, campaign = "") =>
    request<Stats>("GET", `/api/stats?days=${days}` +
      (campaign ? `&campaign=${encodeURIComponent(campaign)}` : ""),
      undefined, { fresh: true }),
  getErrorSummary: (days: number, opts: { module?: string; campaign?: string; limit?: number } = {}) =>
    request<ErrorSummary>("GET", "/api/errors?" + String(new URLSearchParams({
      days: String(days),
      ...(opts.module ? { module: opts.module } : {}),
      ...(opts.campaign ? { campaign: opts.campaign } : {}),
      ...(opts.limit ? { limit: String(opts.limit) } : {}),
    })), undefined, { fresh: true }),
  getLogs: (opts: { level?: LogLevel; module?: string; q?: string; campaign?: string;
                    since?: string; until?: string; days?: number;
                    limit?: number } = {}) =>
    request<LogPage>("GET", "/api/logs?" + String(new URLSearchParams({
      ...(opts.days ? { days: String(opts.days) } : {}),
      ...(opts.level ? { level: opts.level } : {}),
      ...(opts.module ? { module: opts.module } : {}),
      ...(opts.q ? { q: opts.q } : {}),
      ...(opts.campaign ? { campaign: opts.campaign } : {}),
      ...(opts.since ? { since: opts.since } : {}),
      ...(opts.until ? { until: opts.until } : {}),
      ...(opts.limit ? { limit: String(opts.limit) } : {}),
    })), undefined, { fresh: true }),
  getLogLevel: () => request<LogLevelInfo>("GET", "/api/logs/level", undefined, { fresh: true }),
  /** Follow the log as it is written.
   *
   *  `streamGet`, the same primitive a client uses to re-attach to a running
   *  turn -- an SSE GET with no body. The first frame carries a cursor and no
   *  rows: the tail is about what happens NEXT, and `getLogs` is where the
   *  backlog comes from. Hand the newest cursor back on reconnect and nothing
   *  written during the gap is missed.
   *
   *  `signal` is how it ends. Nothing on the server side of this writes, so an
   *  abort has nothing to reconcile -- unlike a scene turn, which is why this
   *  is a plain stream and not a run.
   */
  streamLogTail: (opts: { cursor?: string; level?: LogLevel; module?: string;
                          q?: string; campaign?: string },
                  onEvent: (e: LogTailEvent) => void, signal?: AbortSignal) =>
    streamGet<LogTailEvent>("/api/logs/tail?" + String(new URLSearchParams({
      ...(opts.cursor ? { cursor: opts.cursor } : {}),
      ...(opts.level ? { level: opts.level } : {}),
      ...(opts.module ? { module: opts.module } : {}),
      ...(opts.q ? { q: opts.q } : {}),
      ...(opts.campaign ? { campaign: opts.campaign } : {}),
    })), onEvent, signal),
};
