import { api } from "./client";
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
  await api.setEdges("w", "g1", { leads_to: ["g2"] });
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
