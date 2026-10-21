// The `api` client against a stubbed `fetch`, for inference and
// writing settings: connections, model facts and tests, default prompts
// (`./endpoints/settings`).
import { api, ApiError } from "../client";
import { onConfigChanged } from "../../appEvents";
import { fixture } from "../../testkit/fixture";
import { draftRunResponse, jsonBody, jsonOk } from "../../testkit/apiResponses";

test.each([
  ["world", "/api/worlds/realm/default-prompt", () => api.getWorldDefaultPrompt("realm")],
  ["campaign", "/api/campaigns/saltmarch/default-prompt", () => api.getCampaignDefaultPrompt("saltmarch")],
  ["focus", "/api/campaigns/saltmarch/scenes/mara/focus", () => api.getSceneFocus("saltmarch", "mara")],
] as const)("%s prompt-field reads never join a pre-save request", async (_name, path, read) => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ text: "", status: "empty" }));
  globalThis.fetch = fetchMock;
  await Promise.all([read(), read()]);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock).toHaveBeenCalledWith(path, expect.objectContaining({ method: "GET" }));
});

test.each([
  ["world", "/api/worlds/realm/default-prompt", (text: string) => api.setWorldDefaultPrompt("realm", text)],
  ["campaign", "/api/campaigns/saltmarch/default-prompt", (text: string) => api.setCampaignDefaultPrompt("saltmarch", text)],
  ["focus", "/api/campaigns/saltmarch/scenes/mara/focus", (text: string) => api.setSceneFocus("saltmarch", "mara", text)],
] as const)("%s prompt-field saves send only the addressed text", async (_name, path, save) => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ text: "Let Mara choose.", status: "ok" }));
  globalThis.fetch = fetchMock;
  await save("Let Mara choose.");
  expect(fetchMock).toHaveBeenCalledWith(path, expect.objectContaining({
    method: "PUT", body: JSON.stringify({ text: "Let Mara choose." }),
  }));
});

test("refreshing a catalog announces, so a cached model list is dropped", async () => {
  // `models.ts` keeps a page-load copy of the ACTIVE connection's catalog and
  // drops it on this signal. Without it, a refresh on the Connections page
  // updates the store and the editor while every scene inspector goes on
  // sizing prompts against the list this request replaced (#149).
  const seen: string[] = [];
  const off = onConfigChanged(() => seen.push("config"));
  // The refresh is detached (#398), so the POST answers with a run and the
  // catalog is its result. Landed on arrival is the duplicate-delivery shape --
  // a re-POST of an attempt that already finished adopts its outcome -- which
  // is the one that needs no timers to express.
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(
    jsonOk({ run: { id: "r1", attempt_id: "a1", state: "landed", next_index: 0,
                    cls: "draft", kind: "models-refresh",
                    result: { models: [], fetched_at: "2026-08-21", rev: "r1" } } }));

  await api.refreshConnectionModels("openrouter");
  expect(seen).toEqual(["config"]);
  off();
});

test("a model refresh whose run was reaped reads its catalog back off the store",
     async () => {
  // The one draft with a DURABLE result: the sidecar is written before the run
  // goes terminal, so a tab suspended past the retention window would be told a
  // refresh failed that actually succeeded — and `notifyConfig` would never
  // fire, leaving every scene inspector sizing prompts against the old list.
  vi.useFakeTimers();
  try {
    const seen: string[] = [];
    const off = onConfigChanged(() => seen.push("config"));
    // Dispatched by URL, not by call order: this test deliberately has another
    // read of the same connection in flight, so the order is not fixed.
    let sent = "";
    const before = { id: "openrouter", models: [{ id: "old" }],
                     fetched_at: "2026-08-01", fetched_by: "", rev: "r1" };
    // The first connection read stays PENDING until the test lets it go. That
    // is what makes the shared-GET map matter at all: it only holds promises
    // that have not settled, so a read that resolves promptly could never be
    // joined by anything.
    let release = () => {};
    const held = new Promise<Response>((r) => { release = () => r(jsonOk(before)); });
    let asked = 0;
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(
      (input, init) => {
        const path = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (path.endsWith("/models/refresh")) {
          sent = new Headers(init?.headers).get("X-Grimoire-Attempt") ?? "";
          return Promise.resolve(draftRunResponse("running"));
        }
        if (path.includes("/runs/")) {
          return Promise.resolve(fixture<Response>({ ok: false, status: 404,
                                   json: async () => ({ detail: "no such run",
                                                        kind: "run_gone" }) }));
        }
        return asked++ === 0 ? held : Promise.resolve(jsonOk(
          { id: "openrouter", models: [{ id: "m-1" }], fetched_at: "2026-08-27",
            fetched_by: sent, rev: "r1" }));
      });
    globalThis.fetch = fetchMock;
    // A read of the same connection ALREADY IN FLIGHT, issued before the
    // refresh wrote. Shared GETs would hand this one back to the verification,
    // which would then see no `fetched_by` and report a reap for a refresh that
    // landed -- so that read has to be `fresh`.
    const stale = api.readConnection("openrouter").catch(() => null);

    const pending = api.refreshConnectionModels("openrouter");
    // The verification read happens inside this window, while `stale` is still
    // unsettled -- so a recovery that joined it would still be waiting here.
    await vi.advanceTimersByTimeAsync(5000);
    release();

    expect((await pending).rev).toBe("r1");
    expect((await pending).models).toEqual([{ id: "m-1" }]);
    expect(seen).toEqual(["config"]);
    await stale;
    off();
  } finally {
    vi.useRealTimers();
  }
});

test("a refresh that was reaped without ever writing is still a failure", async () => {
  // `run_gone` on its own does not mean the fetch landed. An empty
  // `fetched_at` is a connection whose catalog was never written, and calling
  // that a success would report a refresh nobody got.
  vi.useFakeTimers();
  try {
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(draftRunResponse("running"))
      .mockResolvedValueOnce({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) })
      .mockResolvedValueOnce(jsonOk({ id: "openrouter", models: [], fetched_at: "",
                                      fetched_by: "", rev: "r1" }));

    const failed = api.refreshConnectionModels("openrouter")
      .then(() => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);

    expect((await failed as ApiError).kind).toBe("run_gone");
  } finally {
    vi.useRealTimers();
  }
});

test("a refresh recovers only what its OWN attempt wrote", async () => {
  // Two traps in recovering a durable result, and the second is why a
  // timestamp cannot do the job: a connection that already had a catalog has a
  // `fetched_at` whatever happened, AND a second tab refreshing the same
  // connection moves it. Global drafts overlap by design, so the only evidence
  // is which attempt the sidecar names.
  vi.useFakeTimers();
  try {
    // Somebody ELSE's refresh wrote this one -- the second tab this
    // connection was open in. A fresh timestamp is not evidence for us.
    const theirs = { id: "openrouter", models: [{ id: "theirs" }],
                     fetched_at: "2026-08-27", fetched_by: "another-tab",
                     rev: "r1" };
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(draftRunResponse("running"))
      .mockResolvedValueOnce({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) })
      .mockResolvedValueOnce(jsonOk(theirs));

    const failed = api.refreshConnectionModels("openrouter")
      .then(() => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);

    expect((await failed as ApiError).kind).toBe("run_gone");
  } finally {
    vi.useRealTimers();
  }
});

// ---- inference settings (slice C) ----
test("an Embedding change carries its confirmation only when the reader gave it", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({}));
  globalThis.fetch = fetchMock;
  const body = { roles: { embedding: { selection: { provider: "saltmarch", model: "e" } } } };

  await api.putInferenceSettings(body);
  await api.putInferenceSettings(body, { confirmEmbedding: true });

  expect(fetchMock.mock.calls[0][0]).toBe("/api/inference/settings");
  expect(jsonBody(fetchMock.mock.calls[0][1])).toEqual(body);
  expect(jsonBody(fetchMock.mock.calls[1][1]))
    .toEqual({ ...body, confirm_embedding: true });
});

test("a campaign's roles and routes are read and written under the campaign", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({}));
  globalThis.fetch = fetchMock;
  await api.getCampaignInference("run");
  await api.putCampaignInference("run", { routes: { scene: { use: "fast" } } });
  expect(fetchMock.mock.calls[0][0]).toBe("/api/campaigns/run/inference");
  expect(fetchMock.mock.calls[1]).toEqual(["/api/campaigns/run/inference",
    expect.objectContaining({ method: "PUT",
                              body: JSON.stringify({ routes: { scene: { use: "fast" } } }) })]);
});

test("a health check sends confirm only when asked to", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    jsonOk({ ok: true, kind: "", detail: "", checked_at: "" }));
  globalThis.fetch = fetchMock;
  await api.checkConnection("claude");
  await api.checkConnection("claude", { confirm: true });
  expect(fetchMock.mock.calls[0][1]?.body).toBeUndefined();
  expect(fetchMock.mock.calls[1][1]?.body).toBe(JSON.stringify({ confirm: true }));
});

test("model facts address the model in the query, and the test runs as a global draft",
     async () => {
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(jsonOk({}))
    .mockResolvedValueOnce(draftRunResponse("landed", { kind: "model-test",
      result: { provider: "saltmarch", model: "vendor/m", rev: "r", results: {},
                recorded: false } }));
  globalThis.fetch = fetchMock;

  await api.readModelFacts("saltmarch", "vendor/m");
  const result = await api.runModelTest(
    "saltmarch", { model: "vendor/m", capabilities: ["generate"], confirm: true });

  expect(fetchMock.mock.calls[0][0]).toBe("/api/llm-connections/saltmarch/facts?model=vendor%2Fm");
  expect(fetchMock.mock.calls[1][0]).toBe("/api/llm-connections/saltmarch/test");
  expect(jsonBody(fetchMock.mock.calls[1][1])).toEqual(
    { model: "vendor/m", capabilities: ["generate"], confirm: true });
  expect(result.model).toBe("vendor/m");
});

test("a model test sent under a caller's attempt id names that id", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(draftRunResponse("landed", {
    kind: "model-test",
    result: { provider: "saltmarch", model: "vendor/m", rev: "r", results: {}, recorded: false },
  }));
  globalThis.fetch = fetchMock;

  await api.runModelTest(
    "saltmarch", { model: "vendor/m", capabilities: ["generate"], confirm: true },
    { attempt: "held-attempt" });

  // The same id a second ask would send, which the server answers with the
  // run it already started rather than a new one.
  expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("X-Grimoire-Attempt")).toBe("held-attempt");
});
