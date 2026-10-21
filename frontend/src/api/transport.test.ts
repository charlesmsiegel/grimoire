// The `api` client against a stubbed `fetch`, for the request path:
// in-flight sharing of identical GETs, retirement, and the error shape a refusal
// reaches the caller with (`./transport`).
import { api } from "./client";
import { onConfigChanged } from "../appEvents";
import { fixture } from "../testkit/fixture";
import { jsonOk } from "../testkit/apiResponses";

test("identical in-flight GETs are shared", async () => {
  // The general rule, pinned so the exception below reads as an exception.
  let release: (v: Response) => void = () => {};
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(() => new Promise((r) => { release = r; }));
  globalThis.fetch = fetchMock;
  const a = api.getSceneDatetime("run", "s1");
  const b = api.getSceneDatetime("run", "s1");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  release(jsonOk({ current: null, history: [] }));
  expect(await a).toEqual({ current: null, history: [] });
  expect(await b).toEqual({ current: null, history: [] });
});

// ---- request coalescing ----
test("concurrent identical GETs share one request; later calls fetch fresh", async () => {
  let release: (v: Response) => void = () => {};
  const fetchMock = vi.fn<typeof fetch>().mockReturnValue(new Promise((r) => { release = r; }));
  globalThis.fetch = fetchMock;
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
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock;
  await Promise.all([api.getCast("run", "s1"), api.getCast("run", "s2")]);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("a failed GET is not reused by the next call", async () => {
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(fixture({ ok: false, status: 500, statusText: "boom", json: async () => ({}) }))
    .mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock;
  await expect(api.getCast("run", "s1")).rejects.toThrow();
  await expect(api.getCast("run", "s1")).resolves.toEqual([]);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("overlapping identical GETs share one request", async () => {
  // The behaviour the ledger opts out of below, pinned first so the opt-out
  // reads as a deliberate exception rather than as the rule. `getCast` is the
  // exemplar because opening a scene fires it from several components at once,
  // which is what the sharing is for — `listScenes` used to stand here and no
  // longer coalesces at all (#87).
  let settle: (v: Response) => void = () => {};
  const fetchMock = vi.fn<typeof fetch>().mockReturnValue(new Promise((res) => { settle = res; }));
  globalThis.fetch = fetchMock;
  const a = api.getCast("run", "s1");
  const b = api.getCast("run", "s1");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  settle(jsonOk([]));
  await Promise.all([a, b]);
});

test("a provider failure announces on the config channel — the status bar is stale", async () => {
  // The server records what a provider did as it happens (#146); the client
  // only finds out by reading the config again, and nothing else would tell it
  // to before the next navigation.
  const seen: string[] = [];
  const off = onConfigChanged(() => seen.push("config"));
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(
    fixture<Response>({ ok: false, status: 502, json: async () => ({ detail: "bad key", kind: "auth" }) }));

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
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(
    fixture<Response>({ ok: false, status: 400, json: async () => ({ detail: "not a directory", kind: "data_dir" }) }));

  await expect(api.putDataDir("/nope")).rejects.toThrow();
  expect(seen).toEqual([]);
  off();
});

test("a fresh read retires the pending one, so the next caller cannot adopt it", async () => {
  // The fresh read bypasses the share, but leaving the pre-mutation GET in the
  // map means the *next* caller joins it and stores the very answer the fresh
  // read replaced — the refresh undone by whoever asks next.
  let settleOld: (v: Response) => void = () => {};
  const fetchMock = vi.fn<typeof fetch>()
    .mockReturnValueOnce(new Promise((res) => { settleOld = res; }))
    .mockResolvedValue(jsonOk([{ id: "new" }]));
  globalThis.fetch = fetchMock;

  const stale = api.listCampaigns();            // in flight, pre-mutation
  await api.listCampaigns(true);                 // fresh: must retire the above
  const next = api.listCampaigns();              // a later navigation read

  expect(fetchMock).toHaveBeenCalledTimes(3);    // it did NOT rejoin the stale one
  settleOld(jsonOk([{ id: "old" }]));
  expect(await next).toEqual([{ id: "new" }]);
  await stale;
});

test("a retired read settling does not evict the replacement that took its place", async () => {
  // A retired GET still settles. If its cleanup deletes unconditionally it
  // removes whatever entry is live by then, and every later caller issues its
  // own request — on /api/campaigns that is a full scan per caller.
  let settleOld: (v: Response) => void = () => {};
  const fetchMock = vi.fn<typeof fetch>()
    .mockReturnValueOnce(new Promise((res) => { settleOld = res; }))   // 1: pre-mutation
    .mockReturnValueOnce(new Promise(() => {}))                        // 2: fresh, never settles
    .mockReturnValueOnce(new Promise(() => {}));                       // 3: the replacement
  globalThis.fetch = fetchMock;

  const stale = api.listCampaigns();       // installed in the map
  void api.listCampaigns(true);             // fresh: retires the entry above
  void api.listCampaigns();                 // installs a replacement entry
  settleOld(jsonOk([]));                    // the retired read finally answers
  await stale;

  void api.listCampaigns();                 // must JOIN the replacement, not refetch
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

test("a 502 from an LLM route reaches the caller with its kind intact", async () => {
  // The whole offline story (#210) hangs off this one lift: the backend's
  // HTTPException handler flattens a dict detail, so `kind` arrives beside
  // `detail` at the top level of the body, and `request` has to carry it onto
  // the error. `isOffline` reads nothing else. Two branches of CampaignView
  // (`already_absorbed`, `edit_conflicts`) have always read it too, and until
  // now nothing proved `request` populated it at all -- every test that
  // branches on a kind hands its component a hand-built object.
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(fixture<Response>({
    ok: false, status: 502, statusText: "Bad Gateway",
    json: async () => ({ detail: "connection reset", kind: "network" }),
  }));
  await expect(api.generateCharacterTagline("w", "seraphine")).rejects.toMatchObject({
    status: 502, detail: "connection reset", kind: "network",
  });
});

test("a stream that is refused before any body carries its kind too", async () => {
  // `streamPost`'s non-2xx path builds its own ApiError; the opener composer
  // and the chat view both read `kind` off it.
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(fixture<Response>({
    ok: false, status: 502, statusText: "Bad Gateway",
    json: async () => ({ detail: "connection reset", kind: "network" }),
  }));
  await expect(api.opener("run", "s1", "a foggy harbor", () => {}))
    .rejects.toMatchObject({ status: 502, kind: "network" });
});

test("optional transport inputs preserve JSON bytes and fetch defaults", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "mara" }));
  globalThis.fetch = fetchMock;
  await api.createScene("saltmarch", "", undefined, false, "");
  expect(fetchMock.mock.calls[0]?.[1]?.body).toBe('{"title":"","pcless":false,"focus":""}');
  await api.getGlobalRun("run");
  const defaults = fetchMock.mock.calls[1]?.[1];
  expect(defaults?.body).toBeUndefined();
  expect(defaults?.signal).toBeUndefined();
  expect(defaults?.headers).toEqual({});

  const controller = new AbortController();
  await api.getGlobalRun("run", controller.signal);
  expect(fetchMock.mock.calls[2]?.[1]?.signal).toBe(controller.signal);
});
