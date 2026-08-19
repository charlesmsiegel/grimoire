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
