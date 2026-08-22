import { api, ApiError, invalidateConfigCache } from "./client";
import { onCampaignsChanged, onConfigChanged } from "../appEvents";
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

test("scene list reads never coalesce, so issue order is request order", async () => {
  // The scene list decides which sid the URL may name (#87), and `CampaignView`
  // orders its reads by when they were ISSUED so a superseded read cannot
  // install over a newer one. A shared read breaks that ordering rather than
  // merely being stale: it is as old as the request it joined, so a read issued
  // AFTER a rename can be handed a promise from before it and still carry the
  // newest sequence number — retiring the genuinely post-rename relist and
  // installing a list that still holds the old id.
  //
  // Here rather than in CampaignView, whose suite mocks `api.*` wholesale and
  // so never executes the coalescing layer at all.
  let releaseFirst: (v: unknown) => void = () => {};
  const fetchMock = vi
    .fn()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk([{ id: "s1-renamed" }]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const older = api.listScenes("run");    // in flight, pre-rename
  const newer = api.listScenes("run");    // issues its own, not shared
  expect(fetchMock).toHaveBeenCalledTimes(2);

  expect(await newer).toEqual([{ id: "s1-renamed" }]);
  releaseFirst(jsonOk([{ id: "s1" }]));
  expect(await older).toEqual([{ id: "s1" }]);
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

test("the scene ledger routes carry the idea and its status", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.saveSceneIdea("run", { title: "The tide-book", premise: "P", source: "llm" });
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/scene-ideas",
    expect.objectContaining({ method: "POST",
      body: JSON.stringify({ title: "The tide-book", premise: "P", source: "llm" }) }));
  await api.setSceneIdeaStatus("run", "the-tide-book", "used", "001--s");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/scene-ideas/the-tide-book",
    expect.objectContaining({ method: "PUT",
      body: JSON.stringify({ status: "used", scene: "001--s" }) }));
  // a greeting entry's id carries a colon, which has to survive the path
  await api.setSceneIdeaStatus("run", "greeting:reckoning", "dismissed");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/scene-ideas/greeting%3Areckoning",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ status: "dismissed" }) }));
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
  // reads as a deliberate exception rather than as the rule. `getCast` is the
  // exemplar because opening a scene fires it from several components at once,
  // which is what the sharing is for — `listScenes` used to stand here and no
  // longer coalesces at all (#87).
  let settle: (v: unknown) => void = () => {};
  const fetchMock = vi.fn().mockReturnValue(new Promise((res) => { settle = res; }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const a = api.getCast("run", "s1");
  const b = api.getCast("run", "s1");
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
  settle(jsonOk({ plot: [], commitments: [], facts: [], chronicle: [] }));
  await Promise.all([first, second]);
});

test("moving the store announces on both channels — everything changed", async () => {
  // A new root has its own campaigns and its own connections, so the sidebar's
  // links can point at campaigns that do not exist here and the status bar
  // names a connection from the old store. /config never changes the pathname,
  // so nothing else would tell them.
  const seen: string[] = [];
  const offC = onCampaignsChanged(() => seen.push("campaigns"));
  const offK = onConfigChanged(() => seen.push("config"));
  globalThis.fetch = vi.fn().mockResolvedValue(
    jsonOk({ data_dir: "/sync/grimoire", is_default: false, source: "custom" })) as any;

  await api.putDataDir("/sync/grimoire");
  expect(seen.sort()).toEqual(["campaigns", "config"]);
  offC(); offK();
});

test("a rejected store move announces nothing", async () => {
  // The old root is still the live one; telling the shell to refetch would
  // have it re-read the store it is already showing, and a failed move must
  // not look like a successful one.
  const seen: string[] = [];
  const off = onConfigChanged(() => seen.push("config"));
  globalThis.fetch = vi.fn().mockResolvedValue(
    { ok: false, status: 400, json: async () => ({ detail: "not a directory" }) }) as any;

  await expect(api.putDataDir("/nope")).rejects.toThrow();
  expect(seen).toEqual([]);
  off();
});

test("refreshing a catalog announces, so a cached model list is dropped", async () => {
  // `models.ts` keeps a page-load copy of the ACTIVE connection's catalog and
  // drops it on this signal. Without it, a refresh on the Connections page
  // updates the store and the editor while every scene inspector goes on
  // sizing prompts against the list this request replaced (#149).
  const seen: string[] = [];
  const off = onConfigChanged(() => seen.push("config"));
  globalThis.fetch = vi.fn().mockResolvedValue(
    jsonOk({ models: [], fetched_at: "2026-08-21", rev: "r1" })) as any;

  await api.refreshConnectionModels("openrouter");
  expect(seen).toEqual(["config"]);
  off();
});

test("a provider failure announces on the config channel — the status bar is stale", async () => {
  // The server records what a provider did as it happens (#146); the client
  // only finds out by reading the config again, and nothing else would tell it
  // to before the next navigation.
  const seen: string[] = [];
  const off = onConfigChanged(() => seen.push("config"));
  globalThis.fetch = vi.fn().mockResolvedValue(
    { ok: false, status: 502, json: async () => ({ detail: "bad key", kind: "auth" }) }) as any;

  await expect(api.generateCharacterTagline("realm", "mara")).rejects.toThrow();
  expect(seen).toEqual(["config"]);
  off();
});

test("a failure that is not a provider's announces nothing", async () => {
  // `kind` is the app's word for "what sort of failure this is", and one route
  // uses it for something that is not a provider at all — a data-dir refusal
  // is not an LLM going down.
  const seen: string[] = [];
  const off = onConfigChanged(() => seen.push("config"));
  globalThis.fetch = vi.fn().mockResolvedValue(
    { ok: false, status: 400, json: async () => ({ detail: "not a directory", kind: "data_dir" }) }) as any;

  await expect(api.putDataDir("/nope")).rejects.toThrow();
  expect(seen).toEqual([]);
  off();
});

test("a fresh read retires the pending one, so the next caller cannot adopt it", async () => {
  // The fresh read bypasses the share, but leaving the pre-mutation GET in the
  // map means the *next* caller joins it and stores the very answer the fresh
  // read replaced — the refresh undone by whoever asks next.
  let settleOld: (v: unknown) => void = () => {};
  const fetchMock = vi.fn()
    .mockReturnValueOnce(new Promise((res) => { settleOld = res; }))
    .mockResolvedValue(jsonOk([{ id: "new" }]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const stale = api.listCampaigns();            // in flight, pre-mutation
  await api.listCampaigns(true);                 // fresh: must retire the above
  const next = api.listCampaigns();              // a later navigation read

  expect(fetchMock).toHaveBeenCalledTimes(3);    // it did NOT rejoin the stale one
  settleOld(jsonOk([{ id: "old" }]));
  expect(await next).toEqual([{ id: "new" }]);
  await stale;
});

test("invalidating the config cache retires the in-flight config GET too", async () => {
  // The resolved cache is only half of what is stale: with the cache cleared,
  // the next getConfig() would otherwise join a read issued before the change.
  let settleOld: (v: unknown) => void = () => {};
  const fetchMock = vi.fn()
    .mockReturnValueOnce(new Promise((res) => { settleOld = res; }))
    .mockResolvedValue(jsonOk({ theme: "codex", active_connection_id: "new" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const stale = api.getConfig();
  invalidateConfigCache();
  const next = api.getConfig();

  expect(fetchMock).toHaveBeenCalledTimes(2);
  settleOld(jsonOk({ theme: "codex", active_connection_id: "old" }));
  expect((await next).active_connection_id).toBe("new");
  await stale;
});

test("moving the store retires every pending read, not just config and campaigns", async () => {
  // The Library hub fires five section counts. Any still in flight when the
  // root changes would resolve with the previous store's totals, and the hub
  // would adopt them.
  let settleWorlds: (v: unknown) => void = () => {};
  const fetchMock = vi.fn()
    .mockReturnValueOnce(new Promise((res) => { settleWorlds = res; }))
    .mockResolvedValue(jsonOk([{ id: "new-store-world" }]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const stale = api.listWorlds();                       // in flight against the old root
  globalThis.fetch = vi.fn().mockResolvedValue(
    jsonOk({ data_dir: "/sync/grimoire", is_default: false, source: "custom" })) as any;
  await api.putDataDir("/sync/grimoire");

  globalThis.fetch = vi.fn().mockResolvedValue(jsonOk([{ id: "new-store-world" }])) as any;
  const after = api.listWorlds();                        // must not rejoin the pre-move read
  settleWorlds(jsonOk([{ id: "old-store-world" }]));
  expect((await after)[0].id).toBe("new-store-world");
  await stale;
});

test("a retired read settling does not evict the replacement that took its place", async () => {
  // A retired GET still settles. If its cleanup deletes unconditionally it
  // removes whatever entry is live by then, and every later caller issues its
  // own request — on /api/campaigns that is a full scan per caller.
  let settleOld: (v: unknown) => void = () => {};
  const fetchMock = vi.fn()
    .mockReturnValueOnce(new Promise((res) => { settleOld = res; }))   // 1: pre-mutation
    .mockReturnValueOnce(new Promise(() => {}))                        // 2: fresh, never settles
    .mockReturnValueOnce(new Promise(() => {}));                       // 3: the replacement
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const stale = api.listCampaigns();       // installed in the map
  api.listCampaigns(true);                  // fresh: retires the entry above
  api.listCampaigns();                      // installs a replacement entry
  settleOld(jsonOk([]));                    // the retired read finally answers
  await stale;

  api.listCampaigns();                      // must JOIN the replacement, not refetch
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

test("prompt-list reads never coalesce, so a post-generation refresh sees the new turn", async () => {
  // The list is re-read on the refreshKey a completed generation bumps, and the
  // turn that generation just captured is the entire reason for the re-read. A
  // shared in-flight GET from before the turn answers with a list that cannot
  // contain the new row, leaving Turn history a turn behind (#157).
  let releaseFirst: (v: unknown) => void = () => {};
  const fetchMock = vi
    .fn()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk({ entries: [{ id: "000002" }] }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const older = api.listScenePrompts("run", "s1");   // opened before the turn landed
  const newer = api.listScenePrompts("run", "s1");   // issues its own, not shared
  expect(fetchMock).toHaveBeenCalledTimes(2);

  expect(await newer).toEqual({ entries: [{ id: "000002" }] });
  releaseFirst(jsonOk({ entries: [] }));
  expect(await older).toEqual({ entries: [] });
});

test("exportWorldUrl is a plain href the browser can download", () => {
  expect(api.exportWorldUrl("saltmarch")).toBe("/api/worlds/saltmarch/export.zip");
});

test("forkWorld POSTs the new name to the source world's fork route", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "saltmarch-fork" }));
  // No `as unknown as typeof fetch`, for the reason the turn-producer test
  // below gives: the ratchet counts that assertion and this file already
  // carries sixty of them.
  globalThis.fetch = fetchMock;
  expect(await api.forkWorld("saltmarch", "Saltmarch (fork)")).toEqual({ id: "saltmarch-fork" });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/saltmarch/fork",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ name: "Saltmarch (fork)" }),
    }),
  );
});

test("a fork does not invalidate the cached config", async () => {
  // Unlike createWorld and importWorld: forking needs a world to fork, so
  // `first_run` was already false and the cached config still says so.
  invalidateConfigCache();
  const fetchMock = vi.fn().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock;
  await api.getConfig();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fetchMock.mockResolvedValue(jsonOk({ id: "w-2" }));
  await api.forkWorld("w", "Copy");

  fetchMock.mockResolvedValue(jsonOk(CFG));
  await api.getConfig();                        // still cached: 2 calls, not 3
  expect(fetchMock).toHaveBeenCalledTimes(2);
  invalidateConfigCache();
});

test("importWorld POSTs the raw zip body", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "saltmarch-2" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const file = new Blob([new Uint8Array([0x50, 0x4b])], { type: "application/zip" });
  expect(await api.importWorld(file)).toEqual({ id: "saltmarch-2" });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/import",
    expect.objectContaining({
      method: "POST",
      body: file,                                  // raw body, not FormData
      headers: { "content-type": "application/zip" },
    }),
  );
});

test("importWorld surfaces the server's rejection as an ApiError", async () => {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: false, status: 400, statusText: "Bad Request",
    json: async () => ({ detail: "not a world bundle: no grimoire-bundle.json" }),
  }) as unknown as typeof fetch;
  // toBeInstanceOf, not toMatchObject: a plain object carrying the same two
  // fields would satisfy a shape match while callers that branch on ApiError
  // stopped working.
  await expect(api.importWorld(new Blob([]))).rejects.toBeInstanceOf(ApiError);
  await expect(api.importWorld(new Blob([]))).rejects.toMatchObject({
    status: 400, detail: "not a world bundle: no grimoire-bundle.json",
  });
});

test("a successful world import invalidates the cached config", async () => {
  // Importing into an empty store flips `first_run`, exactly as createWorld
  // does -- a stale cached config would leave the setup wizard showing.
  invalidateConfigCache();
  const fetchMock = vi.fn().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getConfig();
  await api.getConfig();
  expect(fetchMock).toHaveBeenCalledTimes(1);   // cached

  fetchMock.mockResolvedValue(jsonOk({ id: "w" }));
  await api.importWorld(new Blob([]));

  fetchMock.mockResolvedValue(jsonOk({ ...CFG, first_run: false }));
  await api.getConfig();                        // must re-fetch, not serve the cache
  expect(fetchMock).toHaveBeenCalledTimes(3);
  invalidateConfigCache();
});

test("one frozen snapshot IS shared, because it cannot go stale", async () => {
  // The exception to the exception: a captured prompt never changes, so two
  // readers of the same entry may share one answer.
  let release: (v: unknown) => void = () => {};
  const fetchMock = vi.fn().mockImplementation(() => new Promise((r) => { release = r; }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const a = api.getScenePrompt("run", "s1", "000001");
  const b = api.getScenePrompt("run", "s1", "000001");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  release(jsonOk({ id: "000001", sections: [] }));
  expect(await a).toEqual({ id: "000001", sections: [] });
  expect(await b).toEqual({ id: "000001", sections: [] });
});

test("exportUrl points at the version's export route, one URL per format", () => {
  // A plain link the browser downloads, like the campaign exports — no fetch,
  // so there is nothing here to mock.
  expect(api.exportUrl("w", "seraphine", "default", "json"))
    .toBe("/api/worlds/w/characters/seraphine/versions/default/export?format=json");
  expect(api.exportUrl("w", "seraphine", "winter", "png"))
    .toBe("/api/worlds/w/characters/seraphine/versions/winter/export?format=png");
  expect(api.exportUrl("w", "seraphine", "winter", "charx"))
    .toBe("/api/worlds/w/characters/seraphine/versions/winter/export?format=charx");
});

test("search builds the query string and omits what was not asked for", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ hits: [] }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.search("the salt pact");
  expect(fetchMock.mock.calls[0][0]).toBe("/api/search?q=the+salt+pact");

  await api.search("salt", { scope: "campaign", root: "run", kinds: ["lore", "scenes"], limit: 10 });
  expect(fetchMock.mock.calls[1][0])
    .toBe("/api/search?q=salt&scope=campaign&root=run&kinds=lore%2Cscenes&limit=10");
});

test("two searches for the same query are two requests, not one shared read", async () => {
  // The shared-promise cache is keyed on the path, so an edit-and-undo would
  // otherwise be answered by the read issued before the edit.
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ hits: [] }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await Promise.all([api.search("salt"), api.search("salt")]);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});


test("setCharacterName PUTs the rename under whichever scope is open", async () => {
  // Scope-aware on purpose (#13): the Name field is editable in campaign scope
  // too, where the write must land on the campaign's own copy rather than the
  // world record every campaign inherits from.
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  await api.setCharacterName({ kind: "world", id: "w" }, "seraphine", "Winifred");
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/worlds/w/characters/seraphine/name",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ name: "Winifred" }) }),
  );

  await api.setCharacterName({ kind: "campaign", id: "run" }, "seraphine", "Winifred");
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/characters/seraphine/name",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ name: "Winifred" }) }),
  );
});

test("both PC creates carry version_name through to their own route", async () => {
  // The server's `PCCreate` has always had `version_name`; only these two body
  // types left it out, so no caller could name a PC's first version without
  // casting past the client (#14). Sent verbatim, and to the endpoint whose
  // scope was asked for -- world PCs and a campaign's own are separate records.
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ pc: "winifred", version: "young" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  await api.createPC("w", { name: "Winifred", version_name: "Young" });
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/worlds/w/pcs",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ name: "Winifred", version_name: "Young" }),
    }),
  );

  await api.createCampaignPC("run", { name: "Winifred", version_name: "Young" });
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/pcs",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ name: "Winifred", version_name: "Young" }),
    }),
  );
});

test("a PC create that names no version sends none, leaving the server's default", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ pc: "winifred", version: "default" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.createPC("w", { name: "Winifred" });
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/worlds/w/pcs",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Winifred" }) }),
  );
});

test("a 502 from an LLM route reaches the caller with its kind intact", async () => {
  // The whole offline story (#210) hangs off this one lift: the backend's
  // HTTPException handler flattens a dict detail, so `kind` arrives beside
  // `detail` at the top level of the body, and `request` has to carry it onto
  // the error. `isOffline` reads nothing else. Two branches of CampaignView
  // (`already_absorbed`, `edit_conflicts`) have always read it too, and until
  // now nothing proved `request` populated it at all -- every test that
  // branches on a kind hands its component a hand-built object.
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: false, status: 502, statusText: "Bad Gateway",
    json: async () => ({ detail: "connection reset", kind: "network" }),
  }) as unknown as typeof fetch;
  await expect(api.generateCharacterTagline("w", "seraphine")).rejects.toMatchObject({
    status: 502, detail: "connection reset", kind: "network",
  });
});

test("a stream that is refused before any body carries its kind too", async () => {
  // `streamPost`'s non-2xx path builds its own ApiError; the opener composer
  // and the chat view both read `kind` off it.
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: false, status: 502, statusText: "Bad Gateway",
    json: async () => ({ detail: "connection reset", kind: "network" }),
  }) as unknown as typeof fetch;
  await expect(api.opener("run", "s1", "a foggy harbor", () => {}))
    .rejects.toMatchObject({ status: 502, kind: "network" });
});

// ---- the calendar config, on both scopes (#223) ----
//
// One store file, two roots: the campaign's copy and the world default it was
// created from. The URL is the only thing that says which, so it is what these
// pin — a scope that silently fell back to /api/campaigns would edit some other
// record's calendar and still resolve.
test("getCalendarConfig GETs the campaign's calendar", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ confirmed: false }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getCalendarConfig({ kind: "campaign", id: "run" });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/calendar", expect.objectContaining({ method: "GET" }));
});

test("getCalendarConfig GETs the world's calendar", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ confirmed: false }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getCalendarConfig({ kind: "world", id: "realm" });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/realm/calendar", expect.objectContaining({ method: "GET" }));
});

test("setCalendarConfig PUTs to the scope it was given", async () => {
  const cfg = { primary: { provider: "hebrew", region: "IL", custom_holidays: [], anchor: null },
                secondary: null, confirmed: true, stale_after_days: 30 };
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.setCalendarConfig({ kind: "world", id: "realm" }, cfg);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/realm/calendar",
    expect.objectContaining({ method: "PUT", body: JSON.stringify(cfg) }));
});

test("getCalendarMonths asks the scope's own calendar for its months", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ months: [] }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getCalendarMonths({ kind: "campaign", id: "run" }, 2026);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/calendar/months?year=2026", expect.objectContaining({ method: "GET" }));
  await api.getCalendarMonths({ kind: "world", id: "realm" }, 5786);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/worlds/realm/calendar/months?year=5786", expect.objectContaining({ method: "GET" }));
});

test("the campaign image library's four URLs are the four routes that serve it", async () => {
  // Worth pinning harder than most: `campaignImageUrl`'s output is not merely
  // fetched, it is WRITTEN INTO A POST and saved (#376). A wrong template here
  // does not fail a request — it files a permanently broken reference into a
  // transcript, and every export that reads it degrades the image to alt text.
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  expect(api.campaignImageUrl("run", "coastline")).toBe("/api/campaigns/run/images/coastline");
  expect(api.campaignImageUrl("run", "coastline", { w: 160 }))
    .toBe("/api/campaigns/run/images/coastline?w=160");
  expect(api.campaignImageUrl("run", "coastline", { v: "a1" }))
    .toBe("/api/campaigns/run/images/coastline?v=a1");

  await api.listCampaignImages("run");
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/images", expect.objectContaining({ method: "GET" }));

  await api.putCampaignImage("run", "coastline", new File(["x"], "c.png"));
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/images/coastline", expect.objectContaining({ method: "PUT" }));

  await api.deleteCampaignImage("run", "coastline");
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/campaigns/run/images/coastline", expect.objectContaining({ method: "DELETE" }));
});

// ---- the attempt rides every turn producer, not just `chat` ----

/** Each detached turn route, called with an attempt and an index callback.
 *
 *  Written as calls rather than as argument positions on purpose: what broke
 *  was the trailing arguments quietly not being forwarded, and a test that
 *  described the signature by index would have been edited to match rather
 *  than failing.
 */
const TURN_PRODUCERS: [string,
                       (on: (e: unknown) => void, attempt: string,
                        onIndex: (i: number) => void) => Promise<void>][] = [
  ["chat", (on, a, oi) => api.chat("c1", "s1", "hi", on, undefined, undefined, a, oi)],
  ["retry", (on, a, oi) => api.retry("c1", "s1", on, undefined, undefined, a, oi)],
  ["regenerate",
   (on, a, oi) => api.regenerate("c1", "s1", on, undefined, undefined, a, oi)],
  ["resolveProposal",
   (on, a, oi) => api.resolveProposal("c1", "s1", { proposal: "pr-1", action: "decline" },
                                      on, undefined, a, oi)],
  ["replayTurn", (on, a, oi) => api.replayTurn("c1", "s1", on, undefined, a, oi)],
];

test.each(TURN_PRODUCERS)(
  "%s sends its attempt and reports the wire index", async (_name, call) => {
    // All five routes are detached server-side. One that drops the attempt lets
    // the server mint its own, so the id the caller recorded names a run that
    // never existed -- Stop addresses nothing and recovery asks about an
    // attempt no route ever heard of. Four of the five dropped it (codex, P1).
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(['id: 4\ndata: {"delta":"The lamps are lit."}\n\n']));
    // No `as unknown as typeof fetch` here, unlike its neighbours: the ratchet
    // counts that assertion and this file is already carrying 61 of them.
    globalThis.fetch = fetchMock;
    const indexes: number[] = [];

    await call(() => {}, "a-7", (i) => indexes.push(i));

    expect(fetchMock.mock.calls[0][1].headers["X-Grimoire-Attempt"]).toBe("a-7");
    // and the resume cursor is fed the WIRE index, which is the other half of
    // the pair -- a producer that forwarded one and not the other would leave
    // a reattach resuming from zero and replaying the whole reply.
    expect(indexes).toEqual([4]);
  });

test("reclassifyEntity posts the destination kind under the record's current one", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "tidewatch", campaigns: ["saltmarch"] }));
  vi.stubGlobal("fetch", fetchMock);
  const out = await api.reclassifyEntity({ kind: "world", id: "realm" }, "lore", "tidewatch",
                                         "locations", "r1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/realm/lore/tidewatch/reclassify",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ to: "locations", rev: "r1" }),
    }),
  );
  expect(out).toEqual({ id: "tidewatch", campaigns: ["saltmarch"] });
});

test("reclassifyEntity omits an absent rev rather than sending a null precondition", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "tidewatch" }));
  vi.stubGlobal("fetch", fetchMock);
  await api.reclassifyEntity({ kind: "campaign", id: "saltmarch" }, "lore", "tidewatch", "items");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/saltmarch/lore/tidewatch/reclassify",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ to: "items" }) }),
  );
});

// ---- the review family, detached (#396) ------------------------------------
//
// `absorbScene` is no longer one request that waits: the POST answers 202 the
// moment the run is reserved -- which is what makes a locked phone survivable
// -- and the client polls, then reads the review off the store. These pin the
// three parts of that a caller depends on: it waits, it answers with the
// stored review, and a run that failed raises the failure the synchronous
// route used to raise.

function runResponse(state: string, extra: Record<string, unknown> = {}) {
  return jsonOk({ run: { id: "r1", attempt_id: null, state, next_index: 0,
                         cls: "review", ...extra } });
}

test("absorbScene polls the run and answers with the stored review", async () => {
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(runResponse("running"))
      .mockResolvedValueOnce(runResponse("landed"))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "They met." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.absorbScene("run", "s1");
    // Two ticks of the poll, so the "still running" answer is really waited on
    // rather than the loop spinning through it.
    await vi.advanceTimersByTimeAsync(5000);
    const got = await pending;
    expect(got.generation).toBe("gen1");
    expect(got.review.one_line).toBe("They met.");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/campaigns/run/scenes/s1/absorb");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/campaigns/run/scenes/s1/runs/r1");
    expect(fetchMock.mock.calls[3][0])
      .toBe("/api/campaigns/run/scenes/s1/pending-review");
  } finally {
    vi.useRealTimers();
  }
});

test("a failed run raises the failure the synchronous route used to raise", async () => {
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(runResponse("failed", {
        error: { kind: "timeout", detail: "absorb time budget exhausted", status: 504 } }));
    globalThis.fetch = fetchMock;
    const pending = api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); },
      (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);
    const err = await pending;
    // The status and kind travel with the run, so a caller that already knows
    // what to do with a 504 timeout needs no second shape to understand.
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(504);
    expect((err as ApiError).kind).toBe("timeout");
  } finally {
    vi.useRealTimers();
  }
});

test("a landed absorb whose record is gone is reported as stale, not as an empty review",
     async () => {
  // The window the watermark exists for, from the client's side: the run
  // landed and the scene moved on between the two calls. Handing the panel a
  // null would render as a review with nothing in it, which reads as a model
  // that had nothing to say.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "landed", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(jsonOk({ review: null, generation: "gen1",
                                      stale: { prepared_posts: 1, current_posts: 2 } }));
    globalThis.fetch = fetchMock;
    const err = await api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    expect((err as ApiError).kind).toBe("review_stale");
  } finally {
    vi.useRealTimers();
  }
});

test("discardReview names the generation, and only that", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ removed: true, stopped: 1 }));
  globalThis.fetch = fetchMock;
  await api.discardReview("run", "s1", "gen/1");
  expect(fetchMock.mock.calls[0][0])
    .toBe("/api/campaigns/run/scenes/s1/pending-review?generation=gen%2F1");
  expect(fetchMock.mock.calls[0][1]).toEqual(
    expect.objectContaining({ method: "DELETE" }));
});

test("liveReview ignores a live run that is not a review", async () => {
  // The newest run on a scene is as likely to be a chat turn, and adopting one
  // of those as a review would leave End Scene spinning over a reply.
  const fetchMock = vi.fn().mockResolvedValue(jsonOk(
    { run: { id: "r1", state: "running", cls: "turn", attempt_id: "a", next_index: 3 } }));
  globalThis.fetch = fetchMock;
  expect(await api.liveReview("run", "s1")).toBeNull();
});

test("liveReview answers with a review still being prepared", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk(
    { run: { id: "r1", state: "running", cls: "review", attempt_id: null,
             next_index: 0, review_generation: "gen1" } }));
  globalThis.fetch = fetchMock;
  expect((await api.liveReview("run", "s1"))?.review_generation).toBe("gen1");
});

test("an absorb whose run was reaped is recovered from the store", async () => {
  // The case this whole feature exists for, from the client's side: the tab was
  // suspended for longer than the run record lives, so the poll 404s on a run
  // whose review landed and is on disk. Failing there would report exactly the
  // loss the durable review was built to prevent.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) })
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "They met." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.absorbScene("run", "s1");
    await vi.advanceTimersByTimeAsync(5000);
    expect((await pending).review.one_line).toBe("They met.");
  } finally {
    vi.useRealTimers();
  }
});

test("a failed run does not hand back the review that was already there", async () => {
  // The fallback matches on GENERATION, not on something being stored. A scene
  // can be holding an earlier review the reader never saved, and handing that
  // back for a run that genuinely failed shows a stale summary as this
  // absorb's result -- a wrong answer presented as a right one.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen2" }))
      .mockResolvedValueOnce(runResponse("failed", {
        error: { kind: "run_failed", detail: "the extractor blew up", status: 409 } }))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "An older review." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const failed = api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);
    expect((await failed as ApiError).kind).toBe("run_failed");
  } finally {
    vi.useRealTimers();
  }
});

test("a landed run whose record belongs to another review is refused", async () => {
  // Discarded and re-absorbed from a second tab while this one waited. The
  // record on disk is a review, and it is not this one's.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "landed", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "Somebody else's." },
                                      generation: "gen2", stale: null }));
    globalThis.fetch = fetchMock;
    const failed = api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);
    expect((await failed as ApiError).kind).toBe("review_stale");
  } finally {
    vi.useRealTimers();
  }
});

test("a reaped run with nothing on disk still reports the failure", async () => {
  // The counterweight: the fallback must not swallow a real failure into
  // silence. Nothing stored means the absorb really is gone.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) })
      .mockResolvedValueOnce(jsonOk({ review: null, generation: null, stale: null }));
    globalThis.fetch = fetchMock;
    const failed = api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);
    expect((await failed as ApiError).kind).toBe("run_gone");
  } finally {
    vi.useRealTimers();
  }
});

test("a failed poll is not a failed run", async () => {
  // The conditions this feature exists for -- a backgrounded WebView, a
  // suspended tab resuming into a dead socket -- all show up as a failed poll,
  // and ending the wait for one reports a failure for a run that is still
  // generating: a banner, a composer unlocked over a scene the server is still
  // holding, and nothing to open the review when it lands.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(runResponse("landed"))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "They met." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.absorbScene("run", "s1");
    await vi.advanceTimersByTimeAsync(20000);
    expect((await pending).review.one_line).toBe("They met.");
  } finally {
    vi.useRealTimers();
  }
});

test("a poll that keeps failing does give up", async () => {
  // The counterweight: riding out a transient failure must not become waiting
  // forever on a server that is gone.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockRejectedValue(new TypeError("Failed to fetch"));
    globalThis.fetch = fetchMock;
    const failed = api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(60000);
    expect(await failed).toBeInstanceOf(TypeError);
  } finally {
    vi.useRealTimers();
  }
});

test("an absorb whose 202 never arrived is adopted, not reported as failed", async () => {
  // The server can accept the POST, reserve the run and start generating, and
  // the 202 be lost on the way back -- a dropped link, a WebView backgrounded
  // in the same second. Reported as a failure, the caller clears its latch for
  // an absorb that is running: the scene is held for as long as it takes, End
  // scene answers `run_in_flight`, and there is no generation to offer a Stop
  // with, because `onStarted` never fired.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))        // the lost 202
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             kind: "absorb", attempt_id: null,
                                             next_index: 0,
                                             review_generation: "gen1" } }))
      .mockResolvedValueOnce(runResponse("landed"))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "They met." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const named: string[] = [];
    const pending = api.absorbScene("run", "s1", false, (g) => named.push(g));
    await vi.advanceTimersByTimeAsync(5000);

    expect((await pending).review.one_line).toBe("They met.");
    // ...and the Stop the panel needs is named, which is the half that makes
    // the adoption worth doing at all.
    expect(named).toEqual(["gen1"]);
  } finally {
    vi.useRealTimers();
  }
});

test("an absorb the server refused in words is not adopted", async () => {
  // Only a failure that carried NO reply is ambiguous. An `ApiError` means the
  // server answered -- `already_absorbed`, a missing key, a busy scene -- and
  // every one of those is the caller's to handle. Adopting a live run there
  // would swallow the confirmation prompt a re-absorb depends on.
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: false, status: 409,
                             json: async () => ({ detail: "already absorbed",
                                                  kind: "already_absorbed" }) });
  globalThis.fetch = fetchMock;

  const failed = await api.absorbScene("run", "s1").then(
    () => { throw new Error("resolved"); }, (e: unknown) => e);

  expect((failed as ApiError).kind).toBe("already_absorbed");
  expect(fetchMock).toHaveBeenCalledTimes(1);       // no discovery attempt
});

test("a live retry of an OLDER review is not adopted as this absorb", async () => {
  // `review` is the class a whole review's runs share, so a scoped retry of
  // some earlier review's phase wears it too. Adopted here, it would install
  // that review's generation and hand its summary back as this End scene's
  // result -- a stale review presented as the one just asked for.
  const fetchMock = vi.fn()
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValueOnce(jsonOk({ run: { id: "r9", state: "running", cls: "review",
                                           kind: "audit", attempt_id: null,
                                           next_index: 0,
                                           review_generation: "gen-old" } }));
  globalThis.fetch = fetchMock;

  const failed = await api.absorbScene("run", "s1").then(
    () => { throw new Error("resolved"); }, (e: unknown) => e);

  expect(failed).toBeInstanceOf(TypeError);
});

test("an absorb that failed with no run to find still reports the failure", async () => {
  // The counterweight: a POST that never reached the server has nothing to
  // adopt, and silence there would leave the reader looking at a scene that
  // never started ending.
  const fetchMock = vi.fn()
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValueOnce(jsonOk({ run: null }));
  globalThis.fetch = fetchMock;

  const failed = await api.absorbScene("run", "s1").then(
    () => { throw new Error("resolved"); }, (e: unknown) => e);

  expect(failed).toBeInstanceOf(TypeError);
});

test("a reaped audit retry is read back off the stored review", async () => {
  // A retry is a detached run too, and it merges its phase into the durable
  // record before its own run record is anything but a receipt. A tab away
  // longer than the retention window polls a retry that landed perfectly well
  // and 404s: the panel keeps the phase it was retrying, and saving commits
  // those stale rows and clears the record. The completed retry is gone, and
  // nothing said so.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r2", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) })
      .mockResolvedValueOnce(jsonOk({
        review: { mechanics: { status: "ok" },
                  edits: [{ id: "sheet:mara", kind: "sheet" },
                          { id: "lore:tea", kind: "lore" }] },
        generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.retryAudit("run", "s1");
    await vi.advanceTimersByTimeAsync(5000);

    const got = await pending;
    expect(got.mechanics.status).toBe("ok");
    // Only the rows this phase owns: the audit proposes the sheet edits and
    // proposes all of them, and the rest of the review is not its to hand back.
    expect(got.edits.map((e) => e.id)).toEqual(["sheet:mara"]);
  } finally {
    vi.useRealTimers();
  }
});

test("a reaped dossier retry hands back only the rows it re-proposed", async () => {
  // `proposed` is the phase's own list of who it prepared a dossier for -- the
  // same projection the server's merge kept the rows by -- so this reads back
  // exactly that retry's contribution and leaves the first pass's other NPCs
  // where they are.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r2", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) })
      .mockResolvedValueOnce(jsonOk({
        review: { dossiers: { status: "ok", proposed: ["aese"] },
                  edits: [{ id: "d:aese", kind: "dossier", target: { id: "aese" } },
                          { id: "d:winifred", kind: "dossier",
                            target: { id: "winifred" } }] },
        generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.retryDossiers("run", "s1");
    await vi.advanceTimersByTimeAsync(5000);

    expect((await pending).edits.map((e) => e.id)).toEqual(["d:aese"]);
  } finally {
    vi.useRealTimers();
  }
});

test("a retry that FAILED is not answered with the phase it was retrying", async () => {
  // The narrowness is the point. A run that ended `failed` never merged
  // anything, so reading the record back hands the panel the PRE-retry phase
  // and presents it as this retry's result -- a wrong answer dressed as a right
  // one, and the reviewer never learns the retry did not run.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r2", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(runResponse("failed", {
        error: { kind: "run_failed", detail: "the auditor blew up", status: 409 } }))
      // The store has a perfectly good record for THIS review -- the pre-retry
      // one. Queued deliberately: the only thing that must stop it being
      // handed back is the failure being a failure and not a reaping.
      .mockResolvedValue(jsonOk({ review: { mechanics: { status: "failed" }, edits: [] },
                                  generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const failed = api.retryAudit("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);

    expect((await failed as ApiError).kind).toBe("run_failed");
  } finally {
    vi.useRealTimers();
  }
});

test("a reaped retry whose review has been replaced still reports the failure", async () => {
  // Matched on the generation, for `absorbScene`'s reason: a scene can be
  // holding a review this retry has nothing to do with.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r2", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) })
      .mockResolvedValueOnce(jsonOk({ review: { mechanics: { status: "ok" }, edits: [] },
                                      generation: "gen2", stale: null }));
    globalThis.fetch = fetchMock;
    const failed = api.retryAudit("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);

    expect((await failed as ApiError).kind).toBe("run_gone");
  } finally {
    vi.useRealTimers();
  }
});
