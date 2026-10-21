// The `api` client against a stubbed `fetch`, for campaign-scoped
// calls: the campaign, its cast, calendar, notices, search, the ledger and the
// continuity review (`./endpoints/campaigns`, `./endpoints/ledger`).
import { api, ApiError } from "../client";
import { onConfigChanged, onShellChanged } from "../../appEvents";
import type { DriverLink, GraphEdge, LinkRelation, ReconcileRunError, StoryGraph } from "../types";
import { fixture } from "../../testkit/fixture";
import { jsonBody, jsonOk } from "../../testkit/apiResponses";

test("campaign settings reads are fresh even when requests overlap", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ overrides: {}, inherited: {}, effective: {} }));
  globalThis.fetch = fetchMock;
  await Promise.all([api.getCampaignSettings("saltmarch"), api.getCampaignSettings("saltmarch")]);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock).toHaveBeenCalledWith("/api/campaigns/saltmarch/settings", expect.objectContaining({ method: "GET" }));
});

test("campaign settings saves notify only after success and preserve null, zero and empty prompt", async () => {
  const changed = vi.fn();
  const stop = onConfigChanged(changed);
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(fixture({ ok: false, status: 400, json: async () => ({ detail: "invalid" }) }))
    .mockResolvedValueOnce(jsonOk({ overrides: {}, inherited: {}, effective: {} }));
  globalThis.fetch = fetchMock;
  try {
    await expect(api.putCampaignSettings("saltmarch", { context_budget: "-1" })).rejects.toThrow("invalid");
    expect(changed).not.toHaveBeenCalled();
    const patch = { context_budget: "0", recap_depth: null, system_prompt: "" };
    await api.putCampaignSettings("saltmarch", patch);
    expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/saltmarch/settings", expect.objectContaining({ method: "PUT", body: JSON.stringify(patch) }));
    expect(changed).toHaveBeenCalledTimes(1);
  } finally { stop(); }
});

test("createCampaign POSTs name + world", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "run" }));
  globalThis.fetch = fetchMock;
  await api.createCampaign("Run One", "drowned-realm");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ name: "Run One", world: "drowned-realm" }),
    }),
  );
});

test("setSceneDatetime PUTs the datetime under its scene", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true, advanced: true, friendly: "4 July 2026" }));
  globalThis.fetch = fetchMock;
  await api.setSceneDatetime("run", "s1", "2026-07-04");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/datetime",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ datetime: "2026-07-04" }) }),
  );
});

test("getSceneDatetime GETs the scene datetime", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ current: null, history: [] }));
  globalThis.fetch = fetchMock;
  await api.getSceneDatetime("run", "s1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/datetime",
    expect.objectContaining({ method: "GET" }),
  );
});

test("createCampaign includes region when given", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "run" }));
  globalThis.fetch = fetchMock;
  await api.createCampaign("Run One", "w1", "GB");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Run One", world: "w1", region: "GB" }) }),
  );
});

test("addToCast POSTs kind+id to the scene cast endpoint", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;
  await api.addToCast("run", "s1", { kind: "pcs", id: "elara" });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/cast",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ kind: "pcs", id: "elara" }) }),
  );
});

test("campaignChanges GETs the campaign changes endpoint", async () => {
  const rows = [{ ref: { kind: "lore", id: "pact" }, name: "The Pact",
    scene: { id: "s1", title: "S", date: "" },
    fields: [{ field: "body", label: "The Pact — lore",
      diff: [{ op: "insert", text: "new" }] }] }];
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk(rows));
  globalThis.fetch = fetchMock;
  const out = await api.campaignChanges("c1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/c1/changes",
    expect.objectContaining({ method: "GET" }),
  );
  expect(out).toEqual(rows);
});

test("addCastBatch POSTs every ref to the batch endpoint", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true, added: 2, skipped: [] }));
  globalThis.fetch = fetchMock;
  await api.addCastBatch("run", "s1", [
    { kind: "pcs", id: "elara" },
    { kind: "characters", id: "sera" },
  ]);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/cast/batch",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ refs: [{ kind: "pcs", id: "elara" }, { kind: "characters", id: "sera" }] }),
    }),
  );
});

test("a ledger read never joins an in-flight one", async () => {
  // The ledger is re-read precisely when the records behind it have moved — an
  // absorb save, a scene rename. Sharing the pre-change request would answer
  // the refresh with exactly the data it was asked to replace, and nothing
  // would fetch again afterwards.
  let settle: (v: Response) => void = () => {};
  const fetchMock = vi.fn<typeof fetch>().mockReturnValue(new Promise((res) => { settle = res; }));
  globalThis.fetch = fetchMock;
  const first = api.campaignLedger("run");
  const second = api.campaignLedger("run");
  expect(fetchMock).toHaveBeenCalledTimes(2);
  settle(jsonOk({ plot: [], commitments: [], facts: [], chronicle: [] }));
  await Promise.all([first, second]);
});

test("search builds the query string and omits what was not asked for", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ hits: [] }));
  globalThis.fetch = fetchMock;
  await api.search("the salt pact");
  expect(fetchMock.mock.calls[0][0]).toBe("/api/search?q=the+salt+pact");

  await api.search("salt", { scope: "campaign", root: "run", kinds: ["lore", "scenes"], limit: 10 });
  expect(fetchMock.mock.calls[1][0])
    .toBe("/api/search?q=salt&scope=campaign&root=run&kinds=lore%2Cscenes&limit=10");
});

test("two searches for the same query are two requests, not one shared read", async () => {
  // The shared-promise cache is keyed on the path, so an edit-and-undo would
  // otherwise be answered by the read issued before the edit.
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ hits: [] }));
  globalThis.fetch = fetchMock;
  await Promise.all([api.search("salt"), api.search("salt")]);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("a search can be aborted by the page that asked for it", async () => {
  // Safe only because a search is `fresh`: the promise is nobody else's.
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ hits: [] }));
  globalThis.fetch = fetchMock;
  const controller = new AbortController();
  await api.search("salt", {}, controller.signal);
  expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
});

// ---- the calendar config, on both scopes (#223) ----
//
// One store file, two roots: the campaign's copy and the world default it was
// created from. The URL is the only thing that says which, so it is what these
// pin — a scope that silently fell back to /api/campaigns would edit some other
// record's calendar and still resolve.
test("getCalendarConfig GETs the campaign's calendar", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ confirmed: false }));
  globalThis.fetch = fetchMock;
  await api.getCalendarConfig({ kind: "campaign", id: "run" });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/calendar", expect.objectContaining({ method: "GET" }));
});

test("getCalendarConfig GETs the world's calendar", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ confirmed: false }));
  globalThis.fetch = fetchMock;
  await api.getCalendarConfig({ kind: "world", id: "realm" });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/realm/calendar", expect.objectContaining({ method: "GET" }));
});

test("setCalendarConfig PUTs to the scope it was given", async () => {
  const cfg = { primary: { provider: "hebrew", region: "IL", custom_holidays: [], anchor: null },
                secondary: null, confirmed: true, stale_after_days: 30, warn_days: 7 };
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;
  await api.setCalendarConfig({ kind: "world", id: "realm" }, cfg);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/realm/calendar",
    expect.objectContaining({ method: "PUT", body: JSON.stringify(cfg) }));
});

test("getCalendarMonths asks the scope's own calendar for its months", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ months: [] }));
  globalThis.fetch = fetchMock;
  await api.getCalendarMonths({ kind: "campaign", id: "run" }, 2026);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/calendar/months?year=2026", expect.objectContaining({ method: "GET" }));
  await api.getCalendarMonths({ kind: "world", id: "realm" }, 5786);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/worlds/realm/calendar/months?year=5786", expect.objectContaining({ method: "GET" }));
});

// ---- the notices channel covers derived inputs, not just dismissals (#106) --

test("writes that change what is imminent announce it on the notices channel", async () => {
  // A pre-notice is derived from events.json, the warn window and the clock —
  // and the panels that write those sit in the same rail as the banner showing
  // them, with no way to refresh it. So the mutators announce, per `appEvents`.
  const { onNoticesChanged } = await import("../../appEvents");
  const heard = vi.fn();
  const off = onNoticesChanged(heard);
  globalThis.fetch = vi.fn().mockResolvedValue(jsonOk({ ok: true }));

  await api.createCampaignEvent("c", { name: "The coronation", date: "2026-05-12" });
  await api.updateCampaignEvent("c", "the-coronation", { date: "2026-05-14" });
  await api.unfireCampaignEvent("c", "the-coronation");
  await api.deleteCampaignEvent("c", "the-coronation");
  await api.setCalendarConfig({ kind: "campaign", id: "c" }, {
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true, stale_after_days: 30, warn_days: 0 });
  await api.advanceTime("c", { days: 3, reason: "the caravan sets out" });

  expect(heard).toHaveBeenCalledTimes(6);
  off();
});

test("an event delete answered partial_delete still announces on the notices channel", async () => {
  // The 500 says the delete landed and only its link cleanup did not: the
  // event and its acknowledgements are gone, so the inspector and the chooser
  // would keep showing its notice. Any other refusal changed nothing.
  const { onNoticesChanged } = await import("../../appEvents");
  const heard = vi.fn();
  const off = onNoticesChanged(heard);

  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: false, status: 500,
    json: async () => ({ kind: "partial_delete", landed: ["event"],
                         detail: "the event was deleted" }) });
  await expect(api.deleteCampaignEvent("c", "the-coronation"))
    .rejects.toMatchObject({ kind: "partial_delete" });
  expect(heard).toHaveBeenCalledTimes(1);

  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: false, status: 409,
    json: async () => ({ kind: "malformed", detail: "repair it first" }) });
  await expect(api.deleteCampaignEvent("c", "the-coronation"))
    .rejects.toMatchObject({ kind: "malformed" });
  expect(heard).toHaveBeenCalledTimes(1);
  off();
});

test("a scene datetime write announces only when it moved the clock", async () => {
  // `clock.observe` runs on this path, so a scene carrying the campaign present
  // forward fires the events it crossed — but a flashback or a re-date changes
  // nothing campaign-wide, and the inspector already reloads its own `when`.
  const { onNoticesChanged } = await import("../../appEvents");
  const heard = vi.fn();
  const off = onNoticesChanged(heard);

  globalThis.fetch = vi.fn().mockResolvedValue(
    jsonOk({ ok: true, advanced: true, friendly: "4 July 2026", id: "s1",
             clock: { moved: false, now: "2026-07-04" } }));
  await api.setSceneDatetime("c", "s1", "2026-07-04");
  expect(heard).not.toHaveBeenCalled();

  globalThis.fetch = vi.fn().mockResolvedValue(
    jsonOk({ ok: true, advanced: true, friendly: "8 July 2026", id: "s1",
             clock: { moved: true, now: "2026-07-08" } }));
  await api.setSceneDatetime("c", "s1", "2026-07-08");
  expect(heard).toHaveBeenCalledTimes(1);
  off();
});

test("forkCampaign sends from_index only when it is given", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "b" }));
  globalThis.fetch = fetchMock;
  await api.forkCampaign("c1", "B", "s1", {}, 3);
  expect(jsonBody(fetchMock.mock.calls[0][1])).toEqual(
    { name: "B", from_scene: "s1", from_index: 3 });
  await api.forkCampaign("c1", "B", "s1");
  expect(jsonBody(fetchMock.mock.calls[1][1])).toEqual({ name: "B", from_scene: "s1" });
  await api.forkCampaign("c1", "B", "s1", {}, 0);
  expect(jsonBody(fetchMock.mock.calls[2][1])).toEqual(
    { name: "B", from_scene: "s1", from_index: 0 });
});

// ---- the continuity review (Slice D) ---------------------------------------

describe("staleCurrent", () => {
  const stale = (current: unknown) =>
    new ApiError(409, "records have changed", "stale_candidate",
                 { kind: "stale_candidate", detail: "records have changed", current });

  test("reads the current records off a well-formed 409", () => {
    const current = {
      fingerprint: "fp2",
      records: [{ ref: "thread:mara-s-map", title: "Mara's map" },
                { ref: "thread:winifred-s-chart", title: "Winifred's chart" }],
    };
    expect(api.staleCurrent(stale(current))).toEqual(current);
  });

  test("is null for a missing or malformed current", () => {
    expect(api.staleCurrent(new ApiError(409, "x", "stale_candidate"))).toBeNull();
    expect(api.staleCurrent(new ApiError(409, "x", "stale_candidate", {}))).toBeNull();
    expect(api.staleCurrent(stale(null))).toBeNull();
    expect(api.staleCurrent(stale("fp2"))).toBeNull();
    expect(api.staleCurrent(stale({ fingerprint: 2, records: [] }))).toBeNull();
    expect(api.staleCurrent(stale({ fingerprint: "fp2" }))).toBeNull();
    expect(api.staleCurrent(stale({ fingerprint: "fp2", records: "no" }))).toBeNull();
    expect(api.staleCurrent(stale({ fingerprint: "fp2", records: [null] }))).toBeNull();
    expect(api.staleCurrent(stale({ fingerprint: "fp2", records: [{ ref: 3 }] }))).toBeNull();
    expect(api.staleCurrent(new Error("not an api error"))).toBeNull();
    expect(api.staleCurrent(undefined)).toBeNull();
  });
});

test("the continuity mutators address their routes, encoding what is not a slug", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;
  await api.removeAlias("run", "thread:mara/map");
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/continuity/aliases?ref=thread%3Amara%2Fmap",
    expect.objectContaining({ method: "DELETE" }));
  await api.removeLink("run", "l1");
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/continuity/links/l1", expect.objectContaining({ method: "DELETE" }));
  await api.applyCandidate("run", "possible_duplicate-01",
                           { op: "alias", canonical: "thread:winifred-s-chart" });
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/continuity/candidates/possible_duplicate-01/apply",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ op: "alias", canonical: "thread:winifred-s-chart" }) }));
  await api.dismissCandidate("run", "possible_thread_closure-01", "keep_open");
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/continuity/candidates/possible_thread_closure-01/dismiss",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ decision: "keep_open" }) }));
  await api.restoreSuppression("run", "fp1");
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/continuity/suppressions/fp1", expect.objectContaining({ method: "DELETE" }));
});

test("a ledger delete asks for force only when told to", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;
  await api.ledgerDeleteThread("run", "mara-s-map");
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/ledger/threads/mara-s-map", expect.objectContaining({ method: "DELETE" }));
  await api.ledgerDeleteThread("run", "mara-s-map", true);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/ledger/threads/mara-s-map?force=true",
    expect.objectContaining({ method: "DELETE" }));
  await api.ledgerDeleteCommitment("run", "mara-s-oath", true);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/ledger/commitments/mara-s-oath?force=true",
    expect.objectContaining({ method: "DELETE" }));
});

test("continuityDrivers reads fresh", async () => {
  // The chooser reads again after a stale refusal; a shared read would hand it
  // the answer it was refused against.
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ drivers: [], anchors: [] }));
  globalThis.fetch = fetchMock;

  await Promise.all([api.continuityDrivers("c"), api.continuityDrivers("c")]);

  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/campaigns/c/continuity/drivers");
  expect((fetchMock.mock.calls[0][1] as { method: string }).method).toBe("GET");
});

test("continuityGraph reads fresh and encodes the campaign", async () => {
  // A lens change never re-reads, so the one read a mount makes must be its
  // own. `request` shares only an in-flight GET, so the two calls overlap: the
  // first is still open when the second is issued, and a shared read would
  // reach `fetch` once.
  const graph: StoryGraph = {
    now: { native: "", friendly: "", fixed: null }, nodes: [], edges: [], omitted: [],
  };
  let releaseFirst: (v: Response) => void = () => {};
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk(graph));
  globalThis.fetch = fetchMock;

  const older = api.continuityGraph("a b");   // in flight, unresolved
  const newer = api.continuityGraph("a b");   // issues its own, not shared
  expect(fetchMock).toHaveBeenCalledTimes(2);
  for (const [url, init] of fetchMock.mock.calls as [string, { method: string }][]) {
    expect(url).toBe("/api/campaigns/a%20b/continuity/graph");
    expect(init.method).toBe("GET");
  }

  expect(await newer).toEqual(graph);
  releaseFirst(jsonOk(graph));
  expect(await older).toEqual(graph);
});

test("an edge's relation is typed by its kind, and a driver link's is a LinkRelation", () => {
  // Type-level pins, checked by `tsc` (§20: typed unions, not open string
  // bags); the runtime asserts only keep the bindings used.
  const relationOf = (e: GraphEdge): LinkRelation | null => {
    if (e.kind !== "link") return null;
    const r: LinkRelation = e.relation;
    return r;
  };
  const d: LinkRelation = ({ relation: "pays_off" } as DriverLink).relation;
  // @ts-expect-error a driver link names one of the reviewed-link relations
  const bad: DriverLink["relation"] = "not_a_relation";

  expect(relationOf({ id: "e", kind: "link", from: "thread:a", to: "thread:b",
                      source: "reviewed", relation: "continues", candidate_id: null }))
    .toBe("continues");
  expect(relationOf({ id: "f", kind: "appeared_in", from: "characters:mara",
                      to: "scene:s1", source: "structural", relation: null,
                      candidate_id: null })).toBeNull();
  expect([d, bad]).toEqual(["pays_off", "not_a_relation"]);
});

test("removeAlias and removeLink tell the shell", async () => {
  // An unmerge puts the merged record back on the Ledger, so the rail's
  // Ledger count moves without the pathname moving.
  const spy = vi.fn();
  const off = onShellChanged(spy);
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  await api.removeAlias("run", "thread:mara-s-map");
  expect(spy).toHaveBeenCalledTimes(1);
  await api.removeLink("run", "l1");
  expect(spy).toHaveBeenCalledTimes(2);
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(
    fixture<Response>({ ok: false, status: 409, json: async () => ({ detail: "busy", kind: "busy" }) }));
  await expect(api.removeAlias("run", "thread:mara-s-map")).rejects.toThrow();
  await expect(api.removeLink("run", "l1")).rejects.toThrow();
  expect(spy).toHaveBeenCalledTimes(2);
  off();
});

test("a continuity apply or dismiss answered partial_* still tells the shell", async () => {
  // §22: a `partial_apply` / `partial_dismiss` 500 names parts that landed --
  // a thread closed, a finding set aside -- so the rail's Ledger tail and
  // Todo's continuity chore move as on a success. Any other refusal changed
  // nothing.
  const spy = vi.fn();
  const off = onShellChanged(spy);
  const failing = (body: object) => vi.fn<typeof fetch>().mockResolvedValue(
    fixture<Response>({ ok: false, status: 500, json: async () => body }));
  globalThis.fetch = failing({ kind: "partial_apply", landed: ["status"],
                               detail: "the finding was only partly applied" });
  await expect(api.applyCandidate("run", "possible_thread_closure-01", { op: "close" }))
    .rejects.toMatchObject({ kind: "partial_apply" });
  expect(spy).toHaveBeenCalledTimes(1);
  globalThis.fetch = failing({ kind: "partial_dismiss", landed: ["suppression"],
                               detail: "the finding was set aside" });
  await expect(api.dismissCandidate("run", "possible_thread_closure-01", "dismiss"))
    .rejects.toMatchObject({ kind: "partial_dismiss" });
  expect(spy).toHaveBeenCalledTimes(2);
  globalThis.fetch = failing({ kind: "io", detail: "the finding could not be set aside" });
  await expect(api.dismissCandidate("run", "possible_thread_closure-01", "dismiss"))
    .rejects.toMatchObject({ kind: "io" });
  await expect(api.applyCandidate("run", "possible_thread_closure-01", { op: "close" }))
    .rejects.toMatchObject({ kind: "io" });
  expect(spy).toHaveBeenCalledTimes(2);
  off();
});

test("a failed reconcile run's body has a type", () => {
  // Type-level: only tsc can fail this line, since vitest erases types.
  const e: Partial<ReconcileRunError> = { saved: true, follow_on: false };
  expect(e.follow_on).toBe(false);
});
