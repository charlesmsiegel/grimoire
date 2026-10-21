// The `api` client against a stubbed `fetch`, for the cached config
// and the store move: what a write, a provider failure and a move each make the
// client forget (`./freshness`).
import { api, invalidateConfigCache } from "./client";
import { onCampaignsChanged, onConfigChanged } from "../appEvents";
import { fixture } from "../testkit/fixture";
import { jsonOk } from "../testkit/apiResponses";

const CFG = {
  theme: "t", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter" as const, name: "OpenRouter" }, ready: true,
};

test("getConfig is cached across sequential calls until a config write", async () => {
  invalidateConfigCache();
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock;
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
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock;
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
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock;
  await api.getConfig();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fetchMock.mockResolvedValue(jsonOk({ id: "new-conn" }));
  await api.createConnection({ kind: "openai_compatible", name: "New Endpoint" });

  fetchMock.mockResolvedValue(jsonOk(CFG));
  await api.getConfig();  // must hit the network again
  expect(fetchMock).toHaveBeenCalledTimes(3);  // 1 getConfig + 1 createConnection + 1 fresh getConfig
  invalidateConfigCache();
});

test("a model-facts write invalidates the config cache and announces", async () => {
  // A capability override on Primary's model can move `ready`, which the
  // header reads off the cached config -- so the write has to drop it.
  invalidateConfigCache();
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock;
  await api.getConfig();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  const seen: string[] = [];
  const off = onConfigChanged(() => seen.push("config"));
  fetchMock.mockResolvedValue(jsonOk({ model: "m", user: {}, tests: {}, resolved: {} }));
  await api.putModelFacts("openrouter", { model: "m" });
  off();
  expect(seen).toEqual(["config"]);

  fetchMock.mockResolvedValue(jsonOk({ ...CFG, ready: false }));
  const got = await api.getConfig();  // must hit the network again
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(got.ready).toBe(false);
  invalidateConfigCache();
});

test("every mutator of what a model view reads drops the cached config and announces",
     async () => {
  // Sampler presets (named by GET /config's roles and listed by the Models
  // views), a landed model test (a verified capability can move `ready`) and a
  // catalog refresh. Each must drop the cached config and announce, or a view
  // keeps showing what the write replaced.
  const preset = { id: "warm", name: "Warm", params: {}, notes: "", source: "" };
  const run = (kind: string, result: unknown) => jsonOk({ run: {
    id: "r1", attempt_id: "a1", state: "landed", next_index: 0, cls: "draft", kind, result } });
  const mutators: [string, () => Promise<unknown>, unknown][] = [
    ["createSamplerPreset", () => api.createSamplerPreset({ name: "Warm", params: {}, notes: "" }),
     preset],
    ["updateSamplerPreset",
     () => api.updateSamplerPreset("warm", { name: "Brisk", params: {}, notes: "" }), preset],
    ["deleteSamplerPreset", () => api.deleteSamplerPreset("warm"), { ok: true }],
    ["importSamplerPreset", () => api.importSamplerPreset(
      { name: "Warm", data: {}, include_max_tokens: false }), { preset, report: {} }],
    ["runModelTest", () => api.runModelTest(
      "saltmarch", { model: "vendor/m", capabilities: ["generate"], confirm: true }),
     null],
    ["refreshConnectionModels", () => api.refreshConnectionModels("saltmarch"), null],
  ];
  for (const [name, call, body] of mutators) {
    invalidateConfigCache();
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk(CFG));
    globalThis.fetch = fetchMock;
    await api.getConfig();

    const seen: string[] = [];
    const off = onConfigChanged(() => seen.push(name));
    fetchMock.mockResolvedValue(
      name === "runModelTest" ? run("model-test", { provider: "saltmarch", model: "vendor/m",
                                                     rev: "r", results: {}, recorded: true })
      : name === "refreshConnectionModels" ? run("models-refresh",
                                                 { models: [], fetched_at: "t", rev: "r" })
      : jsonOk(body));
    await call();
    off();
    expect(seen, name).toEqual([name]);

    const calls = fetchMock.mock.calls.length;
    fetchMock.mockResolvedValue(jsonOk({ ...CFG, ready: false }));
    expect((await api.getConfig()).ready, name).toBe(false);  // read again
    expect(fetchMock.mock.calls.length, name).toBe(calls + 1);
  }
  invalidateConfigCache();
});

test("moving the store announces on both channels — everything changed", async () => {
  // A new root has its own campaigns and its own connections, so the sidebar's
  // links can point at campaigns that do not exist here and the status bar
  // names a connection from the old store. /config never changes the pathname,
  // so nothing else would tell them.
  const seen: string[] = [];
  const offC = onCampaignsChanged(() => seen.push("campaigns"));
  const offK = onConfigChanged(() => seen.push("config"));
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(
    jsonOk({ data_dir: "/sync/grimoire", is_default: false, source: "custom" }));

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
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(
    fixture<Response>({ ok: false, status: 400, json: async () => ({ detail: "not a directory" }) }));

  await expect(api.putDataDir("/nope")).rejects.toThrow();
  expect(seen).toEqual([]);
  off();
});

test("invalidating the config cache retires the in-flight config GET too", async () => {
  // The resolved cache is only half of what is stale: with the cache cleared,
  // the next getConfig() would otherwise join a read issued before the change.
  let settleOld: (v: Response) => void = () => {};
  const fetchMock = vi.fn<typeof fetch>()
    .mockReturnValueOnce(new Promise((res) => { settleOld = res; }))
    .mockResolvedValue(jsonOk({ theme: "codex", active_connection_id: "new" }));
  globalThis.fetch = fetchMock;

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
  let settleWorlds: (v: Response) => void = () => {};
  const fetchMock = vi.fn<typeof fetch>()
    .mockReturnValueOnce(new Promise((res) => { settleWorlds = res; }))
    .mockResolvedValue(jsonOk([{ id: "new-store-world" }]));
  globalThis.fetch = fetchMock;

  const stale = api.listWorlds();                       // in flight against the old root
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(
    jsonOk({ data_dir: "/sync/grimoire", is_default: false, source: "custom" }));
  await api.putDataDir("/sync/grimoire");

  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(jsonOk([{ id: "new-store-world" }]));
  const after = api.listWorlds();                        // must not rejoin the pre-move read
  settleWorlds(jsonOk([{ id: "old-store-world" }]));
  expect((await after)[0].id).toBe("new-store-world");
  await stale;
});

test("a fork does not invalidate the cached config", async () => {
  // Unlike createWorld and importWorld: forking needs a world to fork, so
  // `first_run` was already false and the cached config still says so.
  invalidateConfigCache();
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk(CFG));
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

test("a successful world import invalidates the cached config", async () => {
  // Importing into an empty store flips `first_run`, exactly as createWorld
  // does -- a stale cached config would leave the setup wizard showing.
  invalidateConfigCache();
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock;
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
