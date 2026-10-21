// The `api` client against a stubbed `fetch`, for world-scoped
// library calls: worlds, entities, characters, PCs, greetings and their images
// (`./endpoints/worlds`, `./endpoints/characters`).
import { api, ApiError } from "../client";
import { onArtChanged } from "../../appEvents";
import type { LocalizeEvent } from "../stream";
import { THUMB_REV } from "../thumbs";
import { fixture } from "../../testkit/fixture";
import { jsonOk, sseResponse } from "../../testkit/apiResponses";

test("referenced image subjects send the full URL in the body rather than a route segment", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;
  const image = "/api/worlds/realm/characters/seraphine/versions/default/images/embed-art";
  await api.setImageSubjects("realm", "open", image, ["mara"]);
  expect(fetchMock).toHaveBeenCalledWith("/api/worlds/realm/greetings/open/subjects",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ image, subjects: ["mara"] }) }));
});

test.each<{ tags: string[]; query: string }>([
  { tags: ["sailor", "free & curious"], query: "sailor%2Cfree%20%26%20curious" },
  { tags: [], query: "" },
])("greeting preview encodes proposed tags in a read-only world GET: $tags", async ({ tags, query }) => {
  const rows = [{ id: "open", name: "Open", available: true, reasons: [], pcless: false, location: "" }];
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk(rows));
  globalThis.fetch = fetchMock;
  expect(await api.previewGreetings("realm", tags)).toEqual(rows);
  expect(fetchMock).toHaveBeenCalledWith(`/api/worlds/realm/greeting-availability?tags=${query}`,
    expect.objectContaining({ method: "GET" }));
  expect(fetchMock.mock.calls[0]?.[1]?.body).toBeUndefined();
});

test("createWorld POSTs the name", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "w" }));
  globalThis.fetch = fetchMock;
  await api.createWorld("Drowned Realm");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Drowned Realm" }) }),
  );
});

test("localizeImages posts to the localize endpoint and forwards SSE events", async () => {
  const changed = vi.fn(); const unsubscribe = onArtChanged(changed);
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    sseResponse([
      'data: {"total":1}\n\n',
      'data: {"done":1,"total":1}\n\n',
      'data: {"summary":{"total":1,"localized":1,"skipped":0,"failed":0,"capped":false}}\n\n',
    ]),
  );
  globalThis.fetch = fetchMock;
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
  unsubscribe(); expect(changed).toHaveBeenCalledOnce();
});

test("addTag POSTs the name to the world tags endpoint", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "student" }));
  globalThis.fetch = fetchMock;
  await api.addTag("w", "Student");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/tags",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Student" }) }),
  );
});

test("listEntities resolves the scope base path", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock;
  await api.listEntities({ kind: "campaign", id: "run" }, "lore");
  expect(fetchMock).toHaveBeenCalledWith("/api/campaigns/run/lore", expect.objectContaining({ method: "GET" }));
});

test("updateEntity PUTs keys", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;
  await api.updateEntity({ kind: "world", id: "w" }, "lore", "salt", { keys: "pact,salt" });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/lore/salt",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ keys: "pact,salt" }) }),
  );
});

test("setEdges PUTs to the greeting edges endpoint", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;
  await api.setEdges({ kind: "world", id: "w" }, "g1", { leads_to: ["g2"] });
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/greetings/g1/edges",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ leads_to: ["g2"] }) }),
  );
});

test("lorebookImport POSTs the entries array", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ created: [] }));
  globalThis.fetch = fetchMock;
  const entries = [{ name: "A", keys: ["k"], body: "b", category: "lore" as const }];
  await api.lorebookImport("w", entries);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/lorebook/import",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ entries }) }),
  );
});

test("lorebookParse posts multipart form data", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ entries: [] }));
  globalThis.fetch = fetchMock;
  await api.lorebookParse("w", new File(["{}"], "wi.json"), "lorebook");
  const [path, opts] = fetchMock.mock.calls[0];
  expect(path).toBe("/api/worlds/w/lorebook/parse");
  expect(opts?.method).toBe("POST");
  expect(opts?.body).toBeInstanceOf(FormData);
});

test("entity image helpers hit the scope-aware routes", async () => {
  const scope = { kind: "campaign", id: "run" } as const;
  expect(api.entityImageUrl(scope, "locations", "crypt", "avatar"))
    .toBe("/api/campaigns/run/locations/crypt/images/avatar");
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock;
  await api.listEntityImages({ kind: "world", id: "w" }, "locations", "crypt");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/locations/crypt/images",
    expect.objectContaining({ method: "GET" }),
  );

});

test("scope-parameterized calls route to worlds or campaigns", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock;
  await api.listCharacters({ kind: "campaign", id: "run" });
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/characters",
    expect.objectContaining({ method: "GET" }));
  await api.readGreeting({ kind: "world", id: "w" }, "g1");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/worlds/w/greetings/g1",
    expect.objectContaining({ method: "GET" }));
});

test("a fresh greeting list never joins one already on the wire", async () => {
  // The plot map writes back what this read hands it, so an answer started
  // before somebody's edge write would be sent back over it.
  let settleOld: (v: Response) => void = () => {};
  const fetchMock = vi.fn<typeof fetch>()
    .mockReturnValueOnce(new Promise((res) => { settleOld = res; }))
    .mockResolvedValue(jsonOk([{ id: "new" }]));
  globalThis.fetch = fetchMock;
  const scope = { kind: "world", id: "w" } as const;

  const stale = api.listGreetings(scope);          // in flight, pre-write
  expect(await api.listGreetings(scope, { fresh: true })).toEqual([{ id: "new" }]);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock).toHaveBeenLastCalledWith("/api/worlds/w/greetings",
    expect.objectContaining({ method: "GET" }));
  settleOld(jsonOk([{ id: "old" }]));
  await stale;
});

test("greeting marks and version picks POST to their campaign routes", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;
  await api.markGreeting("run", "g1", "skipped");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/greetings/g1/mark",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ status: "skipped" }) }));
  await api.pickVersion("run", "characters", "mara", "veteran");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/run/characters/mara/pick-version",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ version: "veteran" }) }));
});

test("exportWorldUrl is a plain href the browser can download", () => {
  expect(api.exportWorldUrl("saltmarch")).toBe("/api/worlds/saltmarch/export.zip");
});

test("forkWorld POSTs the new name to the source world's fork route", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "saltmarch-fork" }));
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

test("importWorld POSTs the raw zip body", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "saltmarch-2" }));
  globalThis.fetch = fetchMock;
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
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(fixture<Response>({
    ok: false, status: 400, statusText: "Bad Request",
    json: async () => ({ detail: "not a world bundle: no grimoire-bundle.json" }),
  }));
  // toBeInstanceOf, not toMatchObject: a plain object carrying the same two
  // fields would satisfy a shape match while callers that branch on ApiError
  // stopped working.
  await expect(api.importWorld(new Blob([]))).rejects.toBeInstanceOf(ApiError);
  await expect(api.importWorld(new Blob([]))).rejects.toMatchObject({
    status: 400, detail: "not a world bundle: no grimoire-bundle.json",
  });
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

test("setCharacterName PUTs the rename under whichever scope is open", async () => {
  // Scope-aware on purpose (#13): the Name field is editable in campaign scope
  // too, where the write must land on the campaign's own copy rather than the
  // world record every campaign inherits from.
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;

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
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ pc: "winifred", version: "young" }));
  globalThis.fetch = fetchMock;

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
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ pc: "winifred", version: "default" }));
  globalThis.fetch = fetchMock;
  await api.createPC("w", { name: "Winifred" });
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/worlds/w/pcs",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Winifred" }) }),
  );
});

test("actor and entity image URLs take the same {w, v} query the cover builders do", () => {
  // A tile or a portrait asks for a `?w=` downscale; without one every grid
  // pulled every original at full size to draw it at a few dozen pixels.
  const scope = { kind: "world", id: "realm" } as const;
  const actor = "/api/worlds/realm/characters/seraphine/versions/v1/images/avatar";
  expect(api.actorImageUrl(scope, "characters", "seraphine", "v1", "avatar")).toBe(actor);
  // A `w` carries the thumbnail pipeline's revision as `t`, so a browser's
  // cache is keyed on how the thumbnail was made as well as on its source.
  expect(api.actorImageUrl(scope, "characters", "seraphine", "v1", "avatar", { w: 512, v: "abc" }))
    .toBe(`${actor}?w=512&t=${THUMB_REV}&v=abc`);
  expect(api.actorImageUrl(scope, "characters", "seraphine", "v1", "avatar", { w: 128 }))
    .toBe(`${actor}?w=128&t=${THUMB_REV}`);
  expect(api.actorImageUrl(scope, "pcs", "mara", "v1", "avatar", { v: "t1" }))
    .toBe("/api/worlds/realm/pcs/mara/versions/v1/images/avatar?v=t1");
  // Empty options are no options: a missing token is a bare (revalidated) URL,
  // never `?v=` naming nothing.
  expect(api.actorImageUrl(scope, "characters", "seraphine", "v1", "avatar", { v: "" })).toBe(actor);

  const loc = "/api/campaigns/run/locations/saltmarch/images/avatar";
  expect(api.entityImageUrl({ kind: "campaign", id: "run" }, "locations", "saltmarch", "avatar"))
    .toBe(loc);
  expect(api.entityImageUrl({ kind: "campaign", id: "run" }, "locations", "saltmarch", "avatar",
                            { w: 256, v: "abc" }))
    .toBe(`${loc}?w=256&t=${THUMB_REV}&v=abc`);
});

test("the campaign image library's four URLs are the four routes that serve it", async () => {
  // Worth pinning harder than most: `campaignImageUrl`'s output is not merely
  // fetched, it is WRITTEN INTO A POST and saved (#376). A wrong template here
  // does not fail a request — it files a permanently broken reference into a
  // transcript, and every export that reads it degrades the image to alt text.
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;

  expect(api.campaignImageUrl("run", "coastline")).toBe("/api/campaigns/run/images/coastline");
  expect(api.campaignImageUrl("run", "coastline", { w: 160 }))
    .toBe(`/api/campaigns/run/images/coastline?w=160&t=${THUMB_REV}`);
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

test("reclassifyEntity posts the destination kind under the record's current one", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "tidewatch", campaigns: ["saltmarch"] }));
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
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "tidewatch" }));
  vi.stubGlobal("fetch", fetchMock);
  await api.reclassifyEntity({ kind: "campaign", id: "saltmarch" }, "lore", "tidewatch", "items");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/saltmarch/lore/tidewatch/reclassify",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ to: "items" }) }),
  );
});

test("a confirmed Chub gallery download invalidates common Art", async () => {
  const changed = vi.fn(); const unsubscribe = onArtChanged(changed);
  globalThis.fetch = vi.fn().mockResolvedValue(sseResponse(['data: {"summary":{"attempted":2,"stored":1}}\n\n']));
  await api.downloadCharacterChubGallery("realm", "seraphine", "default", vi.fn());
  unsubscribe(); expect(changed).toHaveBeenCalledOnce();
});

test("fresh entity summaries do not join a pre-write in-flight listing", async () => {
  let finishOld!: (response: ReturnType<typeof jsonOk>) => void;
  const fetched = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
    .mockResolvedValueOnce(jsonOk([{ id: "saltmarch", name: "Saltmarch", has_image: true }]));
  globalThis.fetch = fetched;
  const older = api.listEntities({ kind: "world", id: "realm" }, "items");
  const fresh = await api.listEntities({ kind: "world", id: "realm" }, "items", true);
  finishOld(jsonOk([]));
  await older;
  expect(fresh[0].has_image).toBe(true);
  expect(fetched).toHaveBeenCalledTimes(2);
});

test("image query defaults accept missing values without losing a version or width", () => {
  expect(api.worldImageUrl("realm", "map", { w: undefined, v: undefined })).toBe('/api/worlds/realm/images/map');
  expect(api.worldImageUrl("realm", "map", { w: 0, v: "" })).toBe('/api/worlds/realm/images/map');
  expect(api.worldImageUrl("realm", "map", { w: 160, v: "rev" })).toBe(`/api/worlds/realm/images/map?w=160&t=${THUMB_REV}&v=rev`);
});
