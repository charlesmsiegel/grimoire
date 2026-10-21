/** The app-wide calls: the config and store location, backups and image
 *  maintenance, the nav rail's shell read, calendars, the To do list, and the
 *  observability pages. Spread into `api` by `client.ts`. */
import { announceRootMoved, cacheConfig, cachedConfig, forgetRemembered, invalidateConfigCache,
  memoKey, noteRoot, recall, remembering, retireAllInflight } from "../freshness";
import { notifyConfig, notifyShell } from "../notify";
import type { RunHandle } from "../stream";
import { request, streamGet } from "../transport";
import type { BackupList, BackupRun, CalendarYear, ChoreItems, Config, ConfigUpdate,
  DataDirInfo, ErrorSummary, ImageBackupRun, ImageMaintenanceReport, LogLevel, LogLevelInfo,
  LogPage, LogTailEvent, PromptLayout, ShellPayload, Stats, StoreConflicts, TodoPayload } from "../types";
import { campaignsChanged } from "../../appEvents";
import { encodeSegment } from "../../urlSegment";

export const appApi = {
  /** `fresh` forces a new request and re-seeds the cache with it. The cache is
   *  keyed to nothing but this tab's own writes, so a store populated by
   *  another tab, another device, or a sync client is invisible to it — and
   *  `first_run` is a statement about the store, not about this session, so a
   *  caller re-deciding whether to show setup has to actually ask (#194). */
  getConfig: (opts?: { fresh?: boolean }) => {
    let config = cachedConfig();
    if (!config || opts?.fresh) {
      config = request<Config>("GET", "/api/config", undefined, { fresh: true })
        .then((cfg) => {
          // Every config names the store it describes, and this is how the
          // remembered reads hear that it is no longer the one they came from.
          noteRoot(cfg.data_dir);
          return cfg;
        })
        .catch((err) => {
          cacheConfig(null); // never cache a failure
          throw err;
        });
      cacheConfig(config);
    }
    return config;
  },
  putConfig: (body: ConfigUpdate) =>
    request<Config>("PUT", "/api/config", body).then((cfg) => {
      cacheConfig(Promise.resolve(cfg)); // the write's response is the fresh config
      noteRoot(cfg.data_dir);
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
  createImageBackup: () => request<ImageBackupRun>("POST", "/api/backups/images"),
  // The image store's migration and collection (stage 4). A start is a 202 with
  // a run, `attempt` is its idempotency key, and the report -- read back by run
  // id, which outlives the run's own record -- is the result. Cancel is the
  // GLOBAL run cancel: these runs belong to the app, not to a world.
  startImageMigration: (dry_run: boolean, attempt: string, signal?: AbortSignal) =>
    request<{ run: RunHandle }>("POST", "/api/maintenance/images/migrate",
                                { dry_run }, { attempt, signal }),
  startImageGc: (body: { dry_run: boolean; token?: string }, attempt: string,
                 signal?: AbortSignal) =>
    request<{ run: RunHandle }>("POST", "/api/maintenance/images/gc", body,
                                { attempt, signal }),
  listGlobalRuns: (signal?: AbortSignal) =>
    request<{ runs: RunHandle[] }>("GET", "/api/runs", undefined,
                                   { fresh: true, signal }),
  getGlobalRun: (id: string, signal?: AbortSignal) =>
    request<{ run: RunHandle }>("GET", `/api/runs/${encodeSegment(id)}`, undefined,
                                { fresh: true, signal }),
  cancelGlobalRun: (id: string) =>
    request<{ run: RunHandle }>("POST", `/api/runs/${encodeSegment(id)}/cancel`),
  getImageMaintenanceReport: (runId: string, signal?: AbortSignal) =>
    request<ImageMaintenanceReport>(
      "GET", `/api/maintenance/images/reports/${encodeSegment(runId)}`, undefined,
      { fresh: true, signal }),
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
        // ...and everything remembered from it, unconditionally: the old root
        // and the new one can be spelled the same (a move back, a pointer
        // cleared to a default that matches), and the rows are not the same
        // rows. `noteRoot` then adopts the new root as the memo's owner.
        forgetRemembered();
        noteRoot(info.data_dir);
        // ...and in every other tab of this origin, which would otherwise
        // paint the old store's rows on its next navigation.
        announceRootMoved();
        // ...and both things the shell's chrome is showing. A new root has its
        // own campaigns and its own connections, so the sidebar's links point
        // at campaigns that need not exist here and the status bar names a
        // connection from the old store.
        campaignsChanged();
        return notifyConfig(info);
      }),

  /** The nav rail's badges. `cid` is the campaign the browser remembers, which
   *  may name one that has since been deleted -- the route answers
   *  `campaign: null` and 200 for that rather than 404, so the caller can tell
   *  a gone campaign from a failed request. Never shared: the rail refetches
   *  because something changed, and joining a read issued before that change is
   *  precisely the answer it is trying to replace. */
  getShell: (cid?: string | null) =>
    request<ShellPayload>("GET", `/api/shell${cid ? `?campaign=${encodeURIComponent(cid)}` : ""}`,
                          undefined, { fresh: true }),

  /** Every registered calendar: the built-ins plus whatever the store's own
   *  `calendars/` directory registers. */
  listCalendarProviders: () =>
    request<{ providers: { id: string; name: string }[] }>("GET", "/api/calendars/providers")
      .then((r) => r.providers),
  /** One calendar's months and observances for a year. `year` omitted means
   *  the calendar's own current year, which is the only sane default when a
   *  Hebrew year is ~5786 and a homebrew calendar's could be anything. */
  getCalendarYear: (provider: string, year?: number, region = "") =>
    request<CalendarYear>("GET",
      `/api/calendars/${encodeURIComponent(provider)}/year`
      + `?region=${encodeURIComponent(region)}${year ? `&year=${year}` : ""}`),

  /** Everything the app noticed, for the open campaign. Remembered per scope,
   *  so a second visit to the same scope paints the last answer while its own
   *  read is on the wire (`TodoView`, through `useRevalidated`); always sent
   *  fresh, as it always was. */
  getTodo: (cid?: string | null) =>
    remembering(memoKey("todo", cid ?? ""),
      request<TodoPayload>("GET", `/api/todo${cid ? `?campaign=${encodeURIComponent(cid)}` : ""}`,
                           undefined, { fresh: true })),
  rememberedTodo: (cid?: string | null) => recall<TodoPayload>(memoKey("todo", cid ?? "")),
  /** The instances behind one chore's count. A second call rather than part of
   *  the list, because naming every instance of every chore is the cost the
   *  list is built to avoid — record previews load as they enter view. */
  getChoreItems: (choreId: string, cid?: string | null) =>
    request<ChoreItems>("GET",
      `/api/todo/${encodeURIComponent(choreId)}/items`
      + `${cid ? `?campaign=${encodeURIComponent(cid)}` : ""}`, undefined, { fresh: true }),

  /** Wave a chore off, or take it back. `notifyShell` because the rail's badge
   *  is the count of what is NOT ignored, and this moves it without a
   *  navigation. */
  setChoreIgnored: (choreId: string, ignoredOn: boolean, campaign?: string) =>
    request<{ ok: boolean; ignored: string[] }>(
      "PUT", `/api/todo/${encodeURIComponent(choreId)}/ignored`,
      { ignored: ignoredOn, ...(campaign ? { campaign } : {}) }).then(notifyShell),

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
