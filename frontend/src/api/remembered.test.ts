// The api client's remembered reads, and the intent prefetch that fills them,
// against the real client with `fetch` stubbed.
//
// The memo is module state, and this file shares one copy of the module. Each
// test therefore starts by pointing the client at a store root of its own --
// which is itself the behaviour under test: a root the memo has not seen
// forgets everything remembered under the last one.
import { api } from "./client";
import { prefetchScope, resetPrefetch } from "./prefetch";

const WORLD = { kind: "world", id: "realm" } as const;
const OTHER_WORLD = { kind: "world", id: "saltmarch" } as const;
const SAME_ID_CAMPAIGN = { kind: "campaign", id: "realm" } as const;

const row = (id: string, name: string) => ({
  id, name, default_version: "default", versions: [{ id: "default", name: "default" }],
});

type Call = { path: string; init: RequestInit };

function jsonOk(value: unknown) {
  return { ok: true, status: 200, json: async () => value };
}

/** A `fetch` that answers every request through `answer`, and records it. A
 *  `Promise` from `answer` holds the response until the test settles it. */
function stubFetch(answer: (path: string, init: RequestInit) => unknown) {
  const calls: Call[] = [];
  globalThis.fetch = vi.fn(async (path: string, init: RequestInit) => {
    calls.push({ path, init });
    const value = await answer(path, init);
    return value instanceof Error ? Promise.reject(value) : jsonOk(value);
  }) as unknown as typeof fetch;
  return calls;
}

/** A promise and the hand that settles it. */
function held<T>() {
  let resolve: (v: T) => void = () => {};
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

let rootN = 0;
/** Point the client at a store root nobody has used yet: a config read
 *  reporting it, as the app makes on every navigation. */
async function freshRoot(): Promise<string> {
  rootN += 1;
  const root = `/libraries/test-${rootN}`;
  stubFetch(() => ({ data_dir: root }));
  await api.getConfig({ fresh: true });
  return root;
}

beforeEach(() => {
  resetPrefetch();
});

test("nothing is remembered until a config has said which store this is", async () => {
  // First in the file on purpose: no config has been read in this module yet.
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();
});

test("a character list is remembered for its own scope, and no other", async () => {
  await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  expect(api.rememberedCharacters(WORLD)).toEqual([row("seraphine", "Seraphine")]);
  expect(api.rememberedCharacters(OTHER_WORLD)).toBeUndefined();
  // Same id, other kind: a campaign called "realm" is not the world "realm".
  expect(api.rememberedCharacters(SAME_ID_CAMPAIGN)).toBeUndefined();
});

test("the world record and a campaign's roster are remembered too", async () => {
  await freshRoot();
  stubFetch((path) => (path === "/api/worlds/realm"
    ? { meta: { id: "realm", name: "Realm" }, body: "", counts: {}, campaigns: 2 }
    : [{ kind: "characters", id: "mara", version: "default", scenes: ["001"] }]));
  await api.getWorld("realm");
  await api.listAppearances("run");
  expect(api.rememberedWorld("realm")?.campaigns).toBe(2);
  expect(api.rememberedWorld("saltmarch")).toBeUndefined();
  expect(api.rememberedAppearances("run")?.[0].id).toBe("mara");
});

test("a config naming a different store forgets everything", async () => {
  await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  await freshRoot();   // another tab moved the store; this navigation's config says so
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();
});

test("a config naming the same store forgets nothing", async () => {
  const root = await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  stubFetch(() => ({ data_dir: root }));
  await api.getConfig({ fresh: true });
  expect(api.rememberedCharacters(WORLD)).toHaveLength(1);
});

test("moving the store here forgets everything, even to a path spelled the same", async () => {
  const root = await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  stubFetch(() => ({ data_dir: root, default: root, is_default: true, source: "default", exists: true }));
  await api.putDataDir(root);
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();
});

test("a store moved in another tab is forgotten here before anything paints", async () => {
  // Another tab's move reached this one only through the config read the app
  // makes on navigation -- from an effect, after the route's first render,
  // which is the render that paints from memory. So that render drew the old
  // library's cast, linking to its ids, for a round trip.
  await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  window.dispatchEvent(new StorageEvent("storage", { key: "grimoire.store.moved", newValue: "x" }));
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();
  // ...and nothing is remembered again until a config says which store it is.
  await api.listCharacters(WORLD);
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();
  await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  expect(api.rememberedCharacters(WORLD)).toHaveLength(1);
});

test("a key other than the store move leaves the memo alone", async () => {
  await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  window.dispatchEvent(new StorageEvent("storage", { key: "grimoire.focus", newValue: "1" }));
  expect(api.rememberedCharacters(WORLD)).toHaveLength(1);
});

test("moving the store here tells this origin's other tabs", async () => {
  const root = await freshRoot();
  const before = localStorage.getItem("grimoire.store.moved");
  stubFetch(() => ({ data_dir: root, default: root, is_default: true, source: "default", exists: true }));
  await api.putDataDir(root);
  const after = localStorage.getItem("grimoire.store.moved");
  expect(after).toBeTruthy();
  expect(after).not.toBe(before);
});

test("a read in flight across a store change is not remembered for the new one", async () => {
  await freshRoot();
  const list = held<unknown>();
  stubFetch(() => list.promise);
  const read = api.listCharacters(WORLD);
  await freshRoot();
  list.resolve([row("seraphine", "Seraphine")]);
  await read;
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();
});

test("creating a character through the client forgets the remembered roster", async () => {
  await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  stubFetch(() => ({ character: "mara", version: "default" }));
  await api.createCharacter(WORLD, { name: "Mara" });
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();
  // ...and the read the grid makes next is what is remembered from then on.
  stubFetch(() => [row("seraphine", "Seraphine"), row("mara", "Mara")]);
  await api.listCharacters(WORLD);
  expect(api.rememberedCharacters(WORLD)?.map((r) => r.id)).toEqual(["seraphine", "mara"]);
});

test("a write to a world forgets its campaigns' rosters too, which inherit it", async () => {
  await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters({ kind: "campaign", id: "run" });
  stubFetch(() => ({ ok: true }));
  await api.deleteCharacter(WORLD, "seraphine");
  expect(api.rememberedCharacters({ kind: "campaign", id: "run" })).toBeUndefined();
});

test("a read answered from before a write does not outlive the write", async () => {
  await freshRoot();
  const list = held<unknown>();
  const write = held<unknown>();
  stubFetch((path, init) => (init.method === "GET" ? list.promise : write.promise));
  const writing = api.deleteCharacter(WORLD, "mara");
  // Issued while the delete is on the wire, and answered from before it.
  const reading = api.listCharacters(WORLD);
  list.resolve([row("seraphine", "Seraphine"), row("mara", "Mara")]);
  await reading;
  write.resolve({ ok: true });
  await writing;
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();
});

test("a read sent before a write is not remembered by a caller who joined it after", async () => {
  // The grid's reload after a create or a delete asks for the list the moment
  // the write settles -- and an identical GET still on the wire from before
  // the write is what the client's in-flight sharing hands it. That answer
  // predates the write however late it was joined.
  await freshRoot();
  const list = held<unknown>();
  stubFetch((path, init) => (init.method === "GET" ? list.promise : { ok: true }));
  const early = api.listCharacters(WORLD);
  await api.deleteCharacter(WORLD, "mara");
  const late = api.listCharacters(WORLD);
  list.resolve([row("seraphine", "Seraphine"), row("mara", "Mara")]);
  await Promise.all([early, late]);
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();
});

test("uploads and streamed writes forget as well", async () => {
  await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  stubFetch(() => ({ character: "mara", version: "default" }));
  await api.importCharacter("realm", new File(["{}"], "mara.json"), "json");
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();

  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    body: { getReader: () => ({ read: async () => ({ value: undefined, done: true }) }) },
  })) as unknown as typeof fetch;
  await api.localizeImages("realm", "seraphine", "default", () => {});
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();
});

test("a write outside worlds and campaigns leaves the memo alone", async () => {
  await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  stubFetch(() => ({ sections: [] }));
  await api.putPromptLayout([]);
  expect(api.rememberedCharacters(WORLD)).toHaveLength(1);
});

test("a failed read drops what was remembered under it", async () => {
  await freshRoot();
  stubFetch(() => [row("seraphine", "Seraphine")]);
  await api.listCharacters(WORLD);
  globalThis.fetch = vi.fn(async () => ({
    ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "world not found" }),
  })) as unknown as typeof fetch;
  await expect(api.listCharacters(WORLD)).rejects.toThrow("world not found");
  expect(api.rememberedCharacters(WORLD)).toBeUndefined();
});

test("the describe backlog can be counted without being downloaded", async () => {
  const calls = stubFetch(() => ({ count: 7 }));
  await expect(api.countUndescribedImages(WORLD)).resolves.toBe(7);
  await expect(api.countUndescribedImages({ kind: "campaign", id: "run" })).resolves.toBe(7);
  expect(calls.map((c) => c.path)).toEqual([
    "/api/worlds/realm/images/undescribed?count=1",
    "/api/campaigns/run/images/undescribed?count=1",
  ]);
});

// ---- the prefetch, end to end ----

test("a page that mounts while a prefetch is in flight joins it rather than asking again", async () => {
  await freshRoot();
  const list = held<unknown>();
  const world = held<unknown>();
  const calls = stubFetch((path) => (path.endsWith("/characters") ? list.promise : world.promise));
  prefetchScope(WORLD);
  await Promise.resolve();
  await Promise.resolve();
  expect(calls.map((c) => c.path).sort()).toEqual(["/api/worlds/realm", "/api/worlds/realm/characters"]);
  // The page's own reads, made the moment it mounts.
  const pageList = api.listCharacters(WORLD);
  const pageWorld = api.getWorld("realm");
  expect(calls).toHaveLength(2);
  list.resolve([row("seraphine", "Seraphine")]);
  world.resolve({ meta: { id: "realm", name: "Realm" }, body: "", counts: {}, campaigns: 1 });
  await expect(pageList).resolves.toEqual([row("seraphine", "Seraphine")]);
  await pageWorld;
  expect(api.rememberedCharacters(WORLD)).toEqual([row("seraphine", "Seraphine")]);
  expect(api.rememberedWorld("realm")?.meta.name).toBe("Realm");
});

test("a prefetch that has landed leaves the next page something to paint, and is not repeated", async () => {
  await freshRoot();
  const calls = stubFetch((path) => (path.endsWith("/characters")
    ? [row("seraphine", "Seraphine")]
    : { meta: { id: "realm", name: "Realm" }, body: "", counts: {} }));
  prefetchScope(WORLD);
  await vi.waitFor(() => expect(api.rememberedCharacters(WORLD)).toBeDefined());
  resetPrefetch();   // past the dedup window: only the memo stands in the way now
  prefetchScope(WORLD);
  await Promise.resolve();
  expect(calls).toHaveLength(2);
});

test("a campaign's prefetch includes the roster its grid filters by", async () => {
  await freshRoot();
  const calls = stubFetch(() => []);
  prefetchScope({ kind: "campaign", id: "run" });
  await vi.waitFor(() => expect(calls).toHaveLength(2));
  expect(calls.map((c) => c.path).sort())
    .toEqual(["/api/campaigns/run/appearances", "/api/campaigns/run/characters"]);
});

test("the memo keeps the answers used most recently, not every roster ever opened", async () => {
  await freshRoot();
  stubFetch((path) => [row(path.split("/")[3], "Someone")]);
  await api.listCharacters({ kind: "world", id: "w0" });
  for (let i = 1; i <= 40; i++) {
    await api.listCharacters({ kind: "world", id: `w${i}` });
    // The first world is still being painted from, so it stays.
    expect(api.rememberedCharacters({ kind: "world", id: "w0" })).toBeDefined();
  }
  expect(api.rememberedCharacters({ kind: "world", id: "w40" })).toBeDefined();
  expect(api.rememberedCharacters({ kind: "world", id: "w1" })).toBeUndefined();
});
