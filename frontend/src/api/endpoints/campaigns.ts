/** Campaign-scoped calls: the campaign records themselves, sync with the
 *  world, search, the cast, and the campaign's calendar, clock, events,
 *  notices and weather. Spread into `api` by `client.ts`. */
import { invalidateConfigCache, memoKey, recall, remembering } from "../freshness";
import { notifyCampaigns, notifyConfig } from "../notify";
import { entityBase } from "../paths";
import { ApiError, request } from "../transport";
import type { Actor, AdvanceDigest, AdvanceRequest, Availability, CalendarConfig, CalendarMonth,
  CalendarScope, CampaignClock, CampaignMeta, CampaignSettingKey, CampaignSettings, CastChanges,
  Climate, ClimateSummary, CompositionRow, ForkGuards, ForkReport, IncomingItem, IncomingRef,
  Notice, RosterEntry, SceneDatetime, SceneLocation, SceneWeather, ScheduledEvent, SearchMode,
  SearchResult, Suggestion, WeatherOverrideBody, WeatherRangeBody, WeatherSpan,
  WorldCampaignPending } from "../types";
import { noticesChanged } from "../../appEvents";
import { encodeSegment } from "../../urlSegment";

export const campaignsApi = {
  // campaigns
  // `fresh` for the caller refetching *because* a campaign just changed: the
  // in-flight share would hand it a read issued before that mutation, which is
  // precisely the answer it is trying to replace. Navigation refetches leave
  // it off, since sharing is free when the caller does not care when the read
  // started (see `request`, and `campaignLedger` for the same trade).
  // Remembered, for the shelf's first render (`CampaignsView`).
  listCampaigns: (fresh = false) =>
    remembering(memoKey("campaigns"),
                request<CampaignMeta[]>("GET", "/api/campaigns", undefined, { fresh })),
  rememberedCampaigns: () => recall<CampaignMeta[]>(memoKey("campaigns")),
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
  // Presence is meaningful here: empty prompts and zero counts are overrides.
  // Always ask now so a newly opened form cannot adopt a pre-save request.
  getCampaignSettings: (cid: string) =>
    request<CampaignSettings>("GET", `/api/campaigns/${encodeSegment(cid)}/settings`, undefined, { fresh: true }),
  putCampaignSettings: (cid: string, fields: Partial<Record<CampaignSettingKey, string | null>>) =>
    request<CampaignSettings>("PUT", `/api/campaigns/${encodeSegment(cid)}/settings`, fields).then(notifyConfig),
  renameCampaign: (cid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/campaigns/${cid}`, { name }).then(notifyCampaigns),
  deleteCampaign: (cid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}`).then(notifyCampaigns),
  /** Fork `cid` into a new campaign. `fromScene` cuts the copy back to that
   *  scene — it and everything before it stay, everything after it comes off
   *  the fork and nothing at all happens to `cid`. Omitted, the fork is of the
   *  campaign as it stands. `notifyCampaigns` for the same reason
   *  `createCampaign` sends it: the shelf and the sidebar gain a row. */
  /** `guards.idempotencyKey` makes a repeat safe (#409): the same key, against
   *  the same source, is answered with the fork the first call made
   *  (`replayed: true`) instead of copying the campaign again. Mint one BEFORE
   *  the request, and derive it from the operation rather than at random where
   *  you can — a key held only in component state is lost by the reload it
   *  exists to survive.
   *
   *  `guards.expectRevision` refuses the copy if the source has been written
   *  since it was priced. A client can check that itself and cannot BIND it:
   *  between its own check and this call is a whole request, and only the
   *  server holds the source's lock across the copy. */
  forkCampaign: (cid: string, name: string, fromScene?: string, guards: ForkGuards = {},
                 fromIndex?: number) =>
    request<ForkReport>("POST", `/api/campaigns/${cid}/fork`,
      { name, ...(fromScene ? { from_scene: fromScene } : {}),
        ...(fromIndex !== undefined ? { from_index: fromIndex } : {}),
        ...(guards.idempotencyKey ? { idempotency_key: guards.idempotencyKey } : {}),
        ...(guards.expectRevision ? { expect_revision: guards.expectRevision } : {}) },
    ).then(notifyCampaigns),

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
  // `fresh` for `getIncoming`'s reason: the panel re-reads this right after a
  // pin toggle or an accept resolved a row.
  getComposition: (cid: string) =>
    request<{ rows: CompositionRow[] }>(
      "GET", `/api/campaigns/${cid}/composition`, undefined, { fresh: true }),
  setSyncPin: (cid: string, ref: IncomingRef, pinned: boolean) =>
    request<{ pinned: string[] }>(
      "PUT", `/api/campaigns/${cid}/composition/pins`, { ref, pinned }),
  // Counted by running `incoming` for every campaign in the world, so it is
  // `fresh` for the same reason: it is read again after a campaign resolved
  // something, and a shared read would still be showing the old count.
  worldCampaigns: (wid: string) =>
    request<WorldCampaignPending[]>("GET", `/api/worlds/${wid}/campaigns`, undefined, { fresh: true }),

  // search (#33). Never coalesced with an in-flight read: a search page issues
  // one of these per settled keystroke, and the shared-promise cache is keyed
  // on the path — which for a repeated query is the same path, so a result
  // arriving for the query BEFORE an edit-and-undo would be served as the
  // answer to the current one. Being `fresh` is also what makes `signal` safe
  // here: no other caller can be awaiting this promise, so aborting it settles
  // it for the one that asked (the hazard `request` describes for shared GETs).
  search: (q: string, opts?: { scope?: string; root?: string; kinds?: string[];
                               mode?: SearchMode; limit?: number },
           signal?: AbortSignal) => {
    const params = new URLSearchParams({ q });
    if (opts?.scope) params.set("scope", opts.scope);
    if (opts?.root) params.set("root", opts.root);
    if (opts?.kinds?.length) params.set("kinds", opts.kinds.join(","));
    if (opts?.mode) params.set("mode", opts.mode);
    if (opts?.limit) params.set("limit", String(opts.limit));
    return request<SearchResult>("GET", `/api/search?${params}`, undefined,
                                 { fresh: true, signal });
  },

  // campaign cast & play
  /** Remembered, because a campaign's roster grid will not paint without it:
   *  it filters to who has appeared, and painting the unfiltered cast first
   *  would show every inherited character and then yank most of them away. */
  listAppearances: (cid: string) =>
    remembering(memoKey("appearances", cid),
                request<RosterEntry[]>("GET", `/api/campaigns/${cid}/appearances`)),
  rememberedAppearances: (cid: string) => recall<RosterEntry[]>(memoKey("appearances", cid)),
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
   *  be seeded over that — see StartFromGreeting.seed_location (#218).
   *  `seed` false opens the scene from the greeting WITHOUT posting its body:
   *  the cast is seated and the scene stamped, and the first post comes from
   *  `opener` in adapt mode (#91). */
  startFromGreeting: (cid: string, sid: string, greeting: string, seedLocation = true, seed = true) =>
    request<{ ok: boolean; id: string }>("POST", `/api/campaigns/${cid}/scenes/${sid}/start-from-greeting`,
                                         { greeting, seed_location: seedLocation, seed }),
  getSceneLocation: (cid: string, sid: string) =>
    request<SceneLocation>("GET", `/api/campaigns/${cid}/scenes/${sid}/location`),
  setSceneLocation: (cid: string, sid: string, location: string) =>
    request<{ ok: boolean; moved: boolean; name: string }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/location`, { location }),
  /** `fresh` for a caller reading *because* something just changed — a notice
   *  dismissed (#106). Without it the read joins whatever GET of this path is
   *  already in flight, and one issued before the write answers with the state
   *  before it: the acknowledged notice comes back and the banner returns. */
  getSceneDatetime: (cid: string, sid: string, opts?: { fresh?: boolean }) =>
    request<SceneDatetime>("GET", `/api/campaigns/${cid}/scenes/${sid}/datetime`,
                           undefined, opts),
  setSceneDatetime: async (cid: string, sid: string, datetime: string) => {
    const r = await request<{ ok: boolean; advanced: boolean; friendly: string; id: string;
                              // Whether this scene's moment carried the campaign clock
                              // forward with it (#100). Forward only: a flashback
                              // reports moved: false.
                              clock?: { moved: boolean; now: string } }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/datetime`, { datetime });
    // Only when the clock actually moved (#106). The backend runs
    // `clock.observe` on this path, so a scene that carries the campaign present
    // forward also fires the events it crossed and shifts every lead time the
    // campaign-scoped banner is showing -- and that banner can be on screen
    // while this write happens, since `SceneConfirmForm` dates a new scene from
    // inside the chooser. Gated on `moved` rather than emitted always: the
    // inspector already reloads its own `when` after this write, so an
    // unconditional emit would add a second read to the commonest write in the
    // app for the flashbacks and re-dates that change nothing campaign-wide.
    if (r.clock?.moved) noticesChanged();
    return r;
  },
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
  /** The digest an advance would produce, writing nothing. Needs no reason.
   *
   *  `revision` is the campaign's write token as of this pricing (#409) — hand
   *  it back as `expect_revision` when confirming, and the advance is refused
   *  rather than measured from a state the reader never saw. */
  previewAdvance: (cid: string, body: AdvanceRequest) =>
    request<{ digest: AdvanceDigest; revision: string }>(
      "POST", `/api/campaigns/${cid}/advance/preview`, body),
  /** `fired` is the subset of `digest.events` this move actually stamped —
   *  empty for a backward correction, which reports what it un-lived without
   *  un-firing it. */
  advanceTime: async (cid: string, body: AdvanceRequest) => {
    const r = await request<{ ok: boolean; moved: boolean; now: string; friendly: string;
                              digest: AdvanceDigest; fired: ScheduledEvent[] }>(
      "POST", `/api/campaigns/${cid}/advance`, body);
    noticesChanged();   // the moment notices are judged from moved, and events fired
    return r;
  },

  // ---- scheduled events (#101) ----
  campaignEvents: (cid: string) =>
    // `fresh`: the clock fires events, so a cached list would show a campaign
    // its own past as still upcoming. `now` is the moment `passed` was judged
    // against, so the panel can say what "already gone by" means here.
    request<{ events: ScheduledEvent[]; now: string; friendly: string }>(
      "GET", `/api/campaigns/${cid}/events`, undefined, { fresh: true }),
  // Each of the four emits on the notices channel: a pre-notice is DERIVED from
  // events.json, so filing, re-dating, deleting or un-firing an event changes
  // what is imminent — and `EventsPanel` sits in the same rail as the When
  // section that shows it, with no way to refresh it (#106).
  createCampaignEvent: async (cid: string, body: { name: string; date: string; note?: string }) => {
    const r = await request<{ ok: boolean; id: string }>(
      "POST", `/api/campaigns/${cid}/events`, body);
    noticesChanged();
    return r;
  },
  /** Every field is optional: what is not sent keeps the stored value. */
  updateCampaignEvent: async (cid: string, eid: string,
                              body: { name?: string; date?: string; note?: string }) => {
    const r = await request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/events/${eid}`, body);
    noticesChanged();
    return r;
  },
  /** Take back a fire stamp — the undo for an advance made by mistake. */
  unfireCampaignEvent: async (cid: string, eid: string) => {
    const r = await request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/events/${eid}/unfire`);
    noticesChanged();
    return r;
  },
  deleteCampaignEvent: async (cid: string, eid: string) => {
    try {
      const r = await request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/events/${eid}`);
      noticesChanged();
      return r;
    } catch (err) {
      // A `partial_delete` 500 says the delete landed and only its link
      // cleanup did not: the event and its acknowledgements are gone either
      // way, so the notice readers re-read as they would on success. Any
      // other refusal changed nothing.
      if (err instanceof ApiError && err.kind === "partial_delete") noticesChanged();
      throw err;
    }
  },

  // ---- warn-once pre-notices (#106) ----
  /** What is imminent and unacknowledged, judged from the campaign clock. The
   *  scene-scoped answer rides on `getSceneDatetime` instead, which is sharper
   *  where there is a scene; this one serves the surfaces that plan a scene
   *  before there is one.
   *
   *  `fresh`: dismissing a notice is meant to take effect immediately, and a
   *  cached list would hand the reader back the banner they just closed. */
  campaignNotices: (cid: string) =>
    request<{ notices: Notice[]; now: string; warn_days: number }>(
      "GET", `/api/campaigns/${cid}/notices`, undefined, { fresh: true }),
  /** Acknowledge notices, by key. Campaign-wide and permanent for that
   *  occurrence — the next occurrence has a different key and warns again. */
  dismissNotices: async (cid: string, keys: string[], scene = "") => {
    const r = await request<{ ok: boolean; marked: string[] }>(
      "POST", `/api/campaigns/${cid}/notices`, { keys, scene });
    noticesChanged();   // every surface showing this ledger is now a payload behind
    return r;
  },
  /** The undo for a banner closed by mistake: dismissing will not overwrite an
   *  existing acknowledgement, so this is the only way back. */
  restoreNotices: async (cid: string, keys: string[]) => {
    const r = await request<{ ok: boolean; forgotten: string[] }>(
      "POST", `/api/campaigns/${cid}/notices/forget`, { keys });
    noticesChanged();
    return r;
  },

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
  /** Emits on the notices channel too: this write carries `warn_days` — the
   *  width of the window a pre-notice is computed over, and `0` switches them
   *  off entirely — as well as the calendars whose holidays they come from. */
  setCalendarConfig: async (scope: CalendarScope, cfg: CalendarConfig) => {
    const r = await request<{ ok: boolean }>("PUT", `${entityBase(scope)}/calendar`, cfg);
    noticesChanged();
    return r;
  },
};
