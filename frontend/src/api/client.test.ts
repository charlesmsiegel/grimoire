import { api, invalidateConfigCache } from "./client";
import type { LocalizeEvent } from "./stream";

function sseResponse(chunks: string[]) {
  let i = 0;
  return {
    ok: true,
    body: {
      getReader() {
        return {
          read: async () =>
            i < chunks.length
              ? { value: new TextEncoder().encode(chunks[i++]), done: false }
              : { value: undefined, done: true },
        };
      },
    },
  };
}

function jsonOk(value: unknown) {
  return { ok: true, json: async () => value };
}

test("createCampaign POSTs name + world", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "run" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
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
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true, advanced: true, friendly: "4 July 2026" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.setSceneDatetime("run", "s1", "2026-07-04");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/datetime",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ datetime: "2026-07-04" }) }),
  );
});

test("getSceneDatetime GETs the scene datetime", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ current: null, history: [] }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getSceneDatetime("run", "s1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/datetime",
    expect.objectContaining({ method: "GET" }),
  );
});

test("createCampaign includes region when given", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "run" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.createCampaign("Run One", "w1", "GB");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Run One", world: "w1", region: "GB" }) }),
  );
});

test("createWorld POSTs the name", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "w" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.createWorld("Drowned Realm");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Drowned Realm" }) }),
  );
});

test("renameScene PUTs to the scene under its campaign", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "s2", title: "New" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.renameScene("run", "s1", "New");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ title: "New" }) }),
  );
});

test("deleteScene issues DELETE under its campaign", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.deleteScene("run", "s1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1",
    expect.objectContaining({ method: "DELETE" }),
  );
});

test("chat posts to the scene chat endpoint and forwards SSE events", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValue(sseResponse(['data: {"delta":"hi"}\n\n', 'data: {"done":true}\n\n']));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const events: unknown[] = [];
  await api.chat("run", "s1", "hello", (e) => events.push(e));
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/chat",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ content: "hello" }) }),
  );
  expect(events).toEqual([{ delta: "hi" }, { done: true }]);
});

test("chat hands its abort signal to fetch, and ignores heartbeat comments", async () => {
  // The signal is the whole cancel mechanism (#95) — there is no cancel
  // endpoint, so a signal that never reaches fetch is a Stop button that does
  // nothing. The heartbeat frames ride the same stream and must stay invisible.
  const fetchMock = vi
    .fn()
    .mockResolvedValue(sseResponse([": heartbeat\n\n", 'data: {"delta":"hi"}\n\n']));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const controller = new AbortController();
  const events: unknown[] = [];
  await api.chat("run", "s1", "hello", (e) => events.push(e), undefined, controller.signal);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/chat",
    expect.objectContaining({ signal: controller.signal }),
  );
  expect(events).toEqual([{ delta: "hi" }]);
});

test("identical in-flight GETs are shared", async () => {
  // The general rule, pinned so the exception below reads as an exception.
  let release: (v: unknown) => void = () => {};
  const fetchMock = vi.fn().mockImplementation(() => new Promise((r) => { release = r; }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const a = api.getSceneDatetime("run", "s1");
  const b = api.getSceneDatetime("run", "s1");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  release(jsonOk({ current: null, history: [] }));
  expect(await a).toEqual({ current: null, history: [] });
  expect(await b).toEqual({ current: null, history: [] });
});

test("alternate reads never coalesce, so a set matches the transcript it was read for", async () => {
  // `fetchAlternates` stamps the answer with the window token current when it
  // ISSUED the read — that is the whole readiness gate. A shared read is as old
  // as the request it joined, so a reroll firing while an earlier GET is open
  // would attach that older set to the newer transcript: the counter names the
  // wrong active take, and an arrow promotes a still-valid but wrong id.
  //
  // Here rather than in CampaignView, whose suite mocks `api.*` wholesale and
  // so never executes the coalescing layer at all.
  let releaseFirst: (v: unknown) => void = () => {};
  const fetchMock = vi
    .fn()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk({ active: 1, alternates: [{ id: "b" }] }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const older = api.getAlternates("run", "s1");   // in flight, unresolved
  const newer = api.getAlternates("run", "s1");   // issues its own, not shared
  expect(fetchMock).toHaveBeenCalledTimes(2);

  expect(await newer).toEqual({ active: 1, alternates: [{ id: "b" }] });
  releaseFirst(jsonOk({ active: 0, alternates: [{ id: "a" }] }));
  expect(await older).toEqual({ active: 0, alternates: [{ id: "a" }] });
});

test("scene reads never coalesce, so a refresh after a mutation sees the mutation", async () => {
  // The alternates read being fresh is only half of it: the readiness gate
  // compares a set against the TRANSCRIPT it was read with, and `selectScene` is
  // the refresh every mutating path funnels through. A shared read is as old as
  // the request it joined, so a reroll or swap firing while an earlier refresh
  // is open would pair a fresh set with a pre-mutation transcript — the same
  // wrong-active-take and wrong-promotion the alternates fix closed, reached
  // from the other side.
  //
  // Opted out for every caller rather than at each mutation's call site: the
  // one thing this PR has learned repeatedly is that a rule each caller must
  // remember is a rule the next caller forgets.
  let releaseFirst: (v: unknown) => void = () => {};
  const fetchMock = vi
    .fn()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk({ messages: [{ role: "assistant", content: "new" }] }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const older = api.getScene("run", "s1", { limit: 40 });   // in flight, unresolved
  const newer = api.getScene("run", "s1", { limit: 40 });   // issues its own, not shared
  expect(fetchMock).toHaveBeenCalledTimes(2);

  expect((await newer).messages[0].content).toBe("new");
  releaseFirst(jsonOk({ messages: [{ role: "assistant", content: "old" }] }));
  expect((await older).messages[0].content).toBe("old");
});

test("proposal reads never coalesce, so claim order is request order", async () => {
  // CampaignView orders proposal writes by the order their reads were *issued*.
  // A shared read breaks that: it is as old as the request it joined, not as
  // new as the claim it was handed, so a newer claim can carry an older answer
  // and outrank a fresher one. The endpoint opts out for every caller, which
  // removes the mismatch rather than guarding each place it surfaces (#95).
  //
  // Tested here and not in CampaignView, whose suite mocks `api.*` wholesale
  // and so never executes the coalescing layer at all — the reason the first
  // version of this fix looked verified while doing nothing.
  let releaseFirst: (v: unknown) => void = () => {};
  const fetchMock = vi
    .fn()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk({ record: { id: "pr-1" } }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const older = api.getRollProposal("run", "s1");   // in flight, unresolved
  const newer = api.getRollProposal("run", "s1");   // issues its own, not shared
  expect(fetchMock).toHaveBeenCalledTimes(2);

  expect(await newer).toEqual({ record: { id: "pr-1" } });
  releaseFirst(jsonOk({ record: null }));
  expect(await older).toEqual({ record: null });    // its own answer, not the newer one
});

test("localizeImages posts to the localize endpoint and forwards SSE events", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    sseResponse([
      'data: {"total":1}\n\n',
      'data: {"done":1,"total":1}\n\n',
      'data: {"summary":{"total":1,"localized":1,"skipped":0,"failed":0,"capped":false}}\n\n',
    ]),
  );
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const events: LocalizeEvent[] = [];
  await api.localizeImages("w", "c", "v", (e) => events.push(e));
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/characters/c/versions/v/localize",
    expect.objectContaining({ method: "POST" }),
  );
  expect(events).toEqual([
    { total: 1 },
    { done: 1, total: 1 },
    { summary: { total: 1, localized: 1, skipped: 0, failed: 0, capped: false } },
  ]);
});

test("retry posts to the scene retry endpoint", async () => {
  const fetchMock = vi.fn().mockResolvedValue(sseResponse(['data: {"done":true}\n\n']));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.retry("run", "s1", () => {});
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/retry",
    expect.objectContaining({ method: "POST" }),
  );
});

test("addTag POSTs the name to the world tags endpoint", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "student" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.addTag("w", "Student");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/tags",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Student" }) }),
  );
});

test("listEntities resolves the scope base path", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.listEntities({ kind: "campaign", id: "run" }, "lore");
  expect(fetchMock).toHaveBeenCalledWith("/api/campaigns/run/lore", expect.objectContaining({ method: "GET" }));
});

test("updateEntity PUTs keys", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.updateEntity({ kind: "world", id: "w" }, "lore", "salt", { keys: "pact,salt" });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/lore/salt",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ keys: "pact,salt" }) }),
  );
});

test("addToCast POSTs kind+id to the scene cast endpoint", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.addToCast("run", "s1", { kind: "pcs", id: "elara" });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/cast",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ kind: "pcs", id: "elara" }) }),
  );
});

test("setEdges PUTs to the greeting edges endpoint", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.setEdges({ kind: "world", id: "w" }, "g1", { leads_to: ["g2"] });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/greetings/g1/edges",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ leads_to: ["g2"] }) }),
  );
});

test("lorebookImport POSTs the entries array", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ created: [] }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const entries = [{ name: "A", keys: ["k"], body: "b", category: "lore" as const }];
  await api.lorebookImport("w", entries);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/lorebook/import",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ entries }) }),
  );
});

test("lorebookParse posts multipart form data", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ entries: [] }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.lorebookParse("w", new File(["{}"], "wi.json"), "lorebook");
  const [path, opts] = fetchMock.mock.calls[0];
  expect(path).toBe("/api/worlds/w/lorebook/parse");
  expect(opts.method).toBe("POST");
  expect(opts.body).toBeInstanceOf(FormData);
});

test("campaignChanges GETs the campaign changes endpoint", async () => {
  const rows = [{ ref: { kind: "lore", id: "pact" }, name: "The Pact",
    scene: { id: "s1", title: "S", date: "" },
    fields: [{ field: "body", label: "The Pact — lore",
      diff: [{ op: "insert", text: "new" }] }] }];
  const fetchMock = vi.fn().mockResolvedValue(jsonOk(rows));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const out = await api.campaignChanges("c1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/c1/changes",
    expect.objectContaining({ method: "GET" }),
  );
  expect(out).toEqual(rows);
});

test("promoteImage POSTs the promote route", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.promoteImage({ kind: "world", id: "w" }, "sera", "v1", "gallery_2");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/characters/sera/versions/v1/images/gallery_2/promote",
    expect.objectContaining({ method: "POST" }),
  );
});

test("entity image helpers hit the scope-aware routes", async () => {
  const scope = { kind: "campaign", id: "run" } as const;
  expect(api.entityImageUrl(scope, "locations", "crypt", "avatar"))
    .toBe("/api/campaigns/run/locations/crypt/images/avatar");
  const fetchMock = vi.fn().mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.listEntityImages({ kind: "world", id: "w" }, "locations", "crypt");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/locations/crypt/images",
    expect.objectContaining({ method: "GET" }),
  );
  await api.promoteEntityImage({ kind: "world", id: "w" }, "locations", "crypt", "gallery_1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/locations/crypt/images/gallery_1/promote",
    expect.objectContaining({ method: "POST" }),
  );
});

test("scope-parameterized calls route to worlds or campaigns", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.listCharacters({ kind: "campaign", id: "run" });
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/characters",
    expect.objectContaining({ method: "GET" }));
  await api.readGreeting({ kind: "world", id: "w" }, "g1");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/worlds/w/greetings/g1",
    expect.objectContaining({ method: "GET" }));
});

test("greeting marks and version picks POST to their campaign routes", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.markGreeting("run", "g1", "skipped");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/greetings/g1/mark",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ status: "skipped" }) }));
  await api.pickVersion("run", "characters", "mara", "veteran");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/characters/mara/pick-version",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ version: "veteran" }) }));
});

// ---- request coalescing ----
const CFG = {
  theme: "t", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter" as const, name: "OpenRouter" }, ready: true,
};

test("concurrent identical GETs share one request; later calls fetch fresh", async () => {
  let release: (v: unknown) => void = () => {};
  const fetchMock = vi.fn().mockReturnValue(new Promise((r) => { release = r; }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const a = api.getCast("run", "s1");
  const b = api.getCast("run", "s1");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  release(jsonOk([]));
  expect(await a).toEqual([]);
  expect(await b).toEqual([]);
  fetchMock.mockResolvedValue(jsonOk([{ kind: "pcs", id: "x" }]));
  await api.getCast("run", "s1");
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("concurrent GETs to different URLs are not coalesced", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await Promise.all([api.getCast("run", "s1"), api.getCast("run", "s2")]);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("a failed GET is not reused by the next call", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: false, status: 500, statusText: "boom", json: async () => ({}) })
    .mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await expect(api.getCast("run", "s1")).rejects.toThrow();
  await expect(api.getCast("run", "s1")).resolves.toEqual([]);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("getConfig is cached across sequential calls until a config write", async () => {
  invalidateConfigCache();
  const fetchMock = vi.fn().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getConfig();
  await api.getConfig();
  expect(fetchMock).toHaveBeenCalledTimes(1); // App + CampaignView share one fetch
  fetchMock.mockResolvedValue(jsonOk({ ...CFG, theme: "dark" }));
  await api.putConfig({ theme: "dark" });     // the write refreshes the cache…
  const got = await api.getConfig();          // …so this needs no new GET
  expect(got.theme).toBe("dark");
  expect(fetchMock).toHaveBeenCalledTimes(2);
  invalidateConfigCache();
});

test("updating a connection invalidates the config cache", async () => {
  invalidateConfigCache();
  const fetchMock = vi.fn().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getConfig();
  await api.getConfig();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fetchMock.mockResolvedValue(jsonOk({ id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "", model: "m", post_process: "none", key_set: true, rev: "r2" }));
  await api.updateConnection("openrouter", { name: "OpenRouter" });

  fetchMock.mockResolvedValue(jsonOk({ ...CFG, ready: true }));
  await api.getConfig();  // must hit the network again -- the cache was invalidated
  expect(fetchMock).toHaveBeenCalledTimes(3);  // 1 getConfig + 1 updateConnection + 1 fresh getConfig
  invalidateConfigCache();
});

test("creating a connection also invalidates the config cache", async () => {
  // Defense in depth (store/llm_connections.py's delete_connection clears
  // active_connection_id specifically so creation is never supposed to
  // silently change what's active) — still proves the client doesn't rely
  // solely on that server-side guarantee to stay correct.
  invalidateConfigCache();
  const fetchMock = vi.fn().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getConfig();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fetchMock.mockResolvedValue(jsonOk({ id: "new-conn" }));
  await api.createConnection({ kind: "openai_compatible", name: "New Endpoint" });

  fetchMock.mockResolvedValue(jsonOk(CFG));
  await api.getConfig();  // must hit the network again
  expect(fetchMock).toHaveBeenCalledTimes(3);  // 1 getConfig + 1 createConnection + 1 fresh getConfig
  invalidateConfigCache();
});

test("addCastBatch POSTs every ref to the batch endpoint", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true, added: 2, skipped: [] }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
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

test("getScene without a window GETs the scene bare", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ meta: {}, messages: [] }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getScene("run", "s1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1",
    expect.objectContaining({ method: "GET" }),
  );
});

test("getScene passes the window as limit/before query params", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ meta: {}, messages: [], offset: 0, total: 0, has_older: false }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getScene("run", "s1", { limit: 40 });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1?limit=40",
    expect.objectContaining({ method: "GET" }),
  );
  // a distinct page is a distinct path, so the in-flight GET dedupe can't
  // serve page 2 the tail it already has in flight
  await api.getScene("run", "s1", { limit: 40, before: 80 });
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/scenes/s1?limit=40&before=80",
    expect.objectContaining({ method: "GET" }),
  );
});

test("overlapping identical GETs share one request", async () => {
  // The behaviour the ledger opts out of below, pinned first so the opt-out
  // reads as a deliberate exception rather than as the rule.
  let settle: (v: unknown) => void = () => {};
  const fetchMock = vi.fn().mockReturnValue(new Promise((res) => { settle = res; }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const a = api.listScenes("run");
  const b = api.listScenes("run");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  settle(jsonOk([]));
  await Promise.all([a, b]);
});

test("a ledger read never joins an in-flight one", async () => {
  // The ledger is re-read precisely when the records behind it have moved — an
  // absorb save, a scene rename. Sharing the pre-change request would answer
  // the refresh with exactly the data it was asked to replace, and nothing
  // would fetch again afterwards.
  let settle: (v: unknown) => void = () => {};
  const fetchMock = vi.fn().mockReturnValue(new Promise((res) => { settle = res; }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const first = api.campaignLedger("run");
  const second = api.campaignLedger("run");
  expect(fetchMock).toHaveBeenCalledTimes(2);
  settle(jsonOk({ plot: [], commitments: [], chronicle: [] }));
  await Promise.all([first, second]);
});
