// `useRevalidated`: paint from memory, then from the server, and never let an
// answer that is out of date by the time it lands reach the screen.
//
// Most of these drive the hook with a scripted `read`. The joined-read cases
// use the real client with `fetch` stubbed, because what they are about is the
// epoch the client stamps on a read it shares.
import { act, renderHook, waitFor } from "@testing-library/react";
import { api } from "./client";
import { useRevalidated } from "./useRevalidated";

type Rows = { id: string; name: string }[];

/** A promise and the hands that settle it. */
function held<T>() {
  let resolve: (v: T) => void = () => {};
  let reject: (e: unknown) => void = () => {};
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const BEFORE: Rows = [{ id: "realm", name: "Realm" }];
const AFTER: Rows = [{ id: "realm", name: "Realm Renamed" }];

/** Every value the hook handed back, render by render, with the key it was
 *  rendered for: a frame that showed the wrong thing for one render is still
 *  a frame that showed it. */
function track<T>(key: () => string, recall: (k: string) => T | undefined,
                  read: (k: string, fresh: boolean) => Promise<T>) {
  const frames: { key: string; data: T | undefined; stale: boolean; failed: boolean }[] = [];
  const hook = renderHook(({ k }: { k: string }) => {
    const r = useRevalidated<T>(k, () => recall(k), (fresh) => read(k, fresh));
    frames.push({ key: k, data: r.data, stale: r.stale, failed: r.failed });
    return r;
  }, { initialProps: { k: key() } });
  return { ...hook, frames };
}

test("a remembered answer is painted on the first render, stale, and the read is still asked", async () => {
  const answer = held<Rows>();
  const read = vi.fn(() => answer.promise);
  const { result, frames } = track(() => "worlds", () => BEFORE, read);
  expect(frames[0]).toEqual({ key: "worlds", data: BEFORE, stale: true, failed: false });
  expect(read).toHaveBeenCalledWith("worlds", false);
  await act(async () => { answer.resolve(AFTER); });
  expect(result.current.data).toEqual(AFTER);
  expect(result.current.stale).toBe(false);
});

test("with nothing remembered there is nothing to paint, and nothing is stale", () => {
  const { frames } = track(() => "worlds", () => undefined, () => new Promise<Rows>(() => {}));
  expect(frames[0]).toEqual({ key: "worlds", data: undefined, stale: false, failed: false });
});

test("overtaking: a reload's answer is not painted over by the older read landing after it", async () => {
  // Mount; GET A is in flight. Rename; reload() issues GET B, which resolves
  // first. A then resolves. The page shows B's list.
  const a = held<Rows>();
  const b = held<Rows>();
  const read = vi.fn((_k: string, fresh: boolean) => (fresh ? b.promise : a.promise));
  const { result, frames } = track(() => "worlds", () => undefined, read);
  let reloaded: Promise<boolean> = Promise.resolve(false);
  act(() => { reloaded = result.current.reload(); });
  expect(read).toHaveBeenLastCalledWith("worlds", true);
  await act(async () => { b.resolve(AFTER); });
  expect(result.current.data).toEqual(AFTER);
  await act(async () => { a.resolve(BEFORE); });
  expect(result.current.data).toEqual(AFTER);
  expect(frames.some((f) => f.data === BEFORE)).toBe(false);
  await expect(reloaded).resolves.toBe(true);
});

test("a failed read drops what was painted and says so; reload clears it and asks fresh", async () => {
  const first = held<Rows>();
  const read = vi.fn((_k: string, fresh: boolean) =>
    (fresh ? Promise.resolve(AFTER) : first.promise));
  const { result } = track(() => "worlds", () => BEFORE, read);
  expect(result.current.data).toEqual(BEFORE);
  await act(async () => { first.reject(new Error("offline")); });
  expect(result.current).toMatchObject({ data: undefined, stale: false, failed: true });
  let ok = false;
  await act(async () => { ok = await result.current.reload(); });
  expect(ok).toBe(true);
  expect(result.current).toMatchObject({ data: AFTER, stale: false, failed: false });
});

test("reload answers false when the read it started fails", async () => {
  const read = vi.fn((_k: string, fresh: boolean) =>
    (fresh ? Promise.reject(new Error("offline")) : Promise.resolve(BEFORE)));
  const { result } = track(() => "worlds", () => undefined, read);
  await waitFor(() => expect(result.current.data).toEqual(BEFORE));
  let ok = true;
  await act(async () => { ok = await result.current.reload(); });
  expect(ok).toBe(false);
  expect(result.current.failed).toBe(true);
});

test("key change: the next key's render never shows the last key's payload", async () => {
  // Switch cid A -> B with A's payload on screen: B's render never shows A's
  // chores, where Ignore would act on the wrong campaign.
  const A: Rows = [{ id: "sheets", name: "3 cast members without a sheet" }];
  const B: Rows = [{ id: "owed", name: "1 open commitment" }];
  const bAnswer = held<Rows>();
  const recall = (k: string) => (k === "a" ? A : undefined);
  const read = vi.fn((k: string) => (k === "a" ? Promise.resolve(A) : bAnswer.promise));
  const { result, frames, rerender } = track(() => "a", recall, read);
  await waitFor(() => expect(result.current.stale).toBe(false));
  rerender({ k: "b" });
  expect(read).toHaveBeenLastCalledWith("b", false);
  expect(frames.filter((f) => f.key === "b").every((f) => f.data === undefined)).toBe(true);
  await act(async () => { bAnswer.resolve(B); });
  expect(result.current.data).toEqual(B);
  expect(frames.filter((f) => f.key === "b").some((f) => f.data === A)).toBe(false);
});

test("key change: a key with a remembered answer paints it in the same render", () => {
  const A: Rows = [{ id: "a", name: "Seraphine" }];
  const B: Rows = [{ id: "b", name: "Mara" }];
  const recall = (k: string) => (k === "a" ? A : B);
  const { frames, rerender } = track(() => "a", recall, () => new Promise<Rows>(() => {}));
  rerender({ k: "b" });
  const bFrames = frames.filter((f) => f.key === "b");
  expect(bFrames.length).toBeGreaterThan(0);
  expect(bFrames.every((f) => f.data === B && f.stale)).toBe(true);
});

test("an answer for a key the page has left is never painted", async () => {
  const aAnswer = held<Rows>();
  const read = vi.fn((k: string) => (k === "a" ? aAnswer.promise : new Promise<Rows>(() => {})));
  const { result, frames, rerender } = track(() => "a", () => undefined, read);
  rerender({ k: "b" });
  await act(async () => { aAnswer.resolve(BEFORE); });
  expect(result.current.data).toBeUndefined();
  expect(frames.some((f) => f.data === BEFORE)).toBe(false);
});

// ---- answers older than the memo epoch, against the real client ----

type Call = { path: string; init: RequestInit };

function stubFetch(answer: (path: string, init: RequestInit, calls: Call[]) => unknown) {
  const calls: Call[] = [];
  globalThis.fetch = vi.fn(async (path: string, init: RequestInit) => {
    calls.push({ path, init });
    const value = await answer(path, init, calls);
    return { ok: true, status: 200, json: async () => value };
  }) as unknown as typeof fetch;
  return calls;
}

let rootN = 0;
async function freshRoot(): Promise<void> {
  rootN += 1;
  const root = `/libraries/revalidated-${rootN}`;
  stubFetch(() => ({ data_dir: root }));
  await api.getConfig({ fresh: true });
}

const worldReads = (calls: Call[]) =>
  calls.filter((c) => c.path === "/api/worlds" && (c.init.method ?? "GET") === "GET");

test("joined pre-write read: its answer is discarded and a fresh read issued", async () => {
  // A non-fresh read starts, a write lands, and a page mounts and joins the
  // first read -- the in-flight sharing hands any caller the GET on the wire,
  // and a write's retirement only stops callers that ask after it. Its answer
  // describes the store from before the write.
  await freshRoot();
  const early = held<unknown>();
  const calls = stubFetch((path, init, seen) => {
    if ((init.method ?? "GET") !== "GET") return { sections: [] };
    return worldReads(seen).length === 1 ? early.promise : AFTER;
  });
  const preWrite = api.listWorlds();
  await api.putPromptLayout([]);
  const read = vi.fn((_k: string, fresh: boolean) => (fresh ? api.listWorlds(true) : preWrite));
  const { result, frames } = track(() => "worlds", () => api.rememberedWorlds(), read);
  await act(async () => { early.resolve(BEFORE); });
  await waitFor(() => expect(result.current.data).toEqual(AFTER));
  expect(read).toHaveBeenLastCalledWith("worlds", true);
  expect(worldReads(calls)).toHaveLength(2);
  expect(frames.some((f) => f.data !== undefined && f.data[0].name === "Realm")).toBe(false);
});

test("a page that really joins a read sent before a store change re-reads it fresh", async () => {
  // A config read that names a different root forgets but does not retire, so
  // the sharing can still hand a page mounting after it the read sent before.
  await freshRoot();
  const early = held<unknown>();
  const calls = stubFetch((path, _init, seen) =>
    (path === "/api/worlds" && worldReads(seen).length === 1 ? early.promise : AFTER));
  const before = api.listWorlds();
  rootN += 1;
  const moved = `/libraries/revalidated-${rootN}`;
  const config = stubFetch(() => ({ data_dir: moved }));
  await api.getConfig({ fresh: true });
  expect(config).toHaveLength(1);
  // The page's own non-fresh read joins the one on the wire...
  globalThis.fetch = vi.fn(async (path: string, init: RequestInit) => {
    calls.push({ path, init });
    return { ok: true, status: 200, json: async () => AFTER };
  }) as unknown as typeof fetch;
  const { result, frames } = track(() => "worlds", () => api.rememberedWorlds(),
                                   (_k, fresh) => api.listWorlds(fresh));
  expect(worldReads(calls)).toHaveLength(1);
  // ...and when that lands, it is not painted: a fresh read goes out instead.
  await act(async () => { early.resolve(BEFORE); });
  await waitFor(() => expect(result.current.data).toEqual(AFTER));
  expect(worldReads(calls)).toHaveLength(2);
  expect(frames.some((f) => f.data !== undefined && f.data[0].name === "Realm")).toBe(false);
  await before;
});

test("a run this client sees end makes a mounted page ask again", async () => {
  const { announceRunSettled } = await import("./memoEpoch");
  let n = 0;
  const read = vi.fn(() => Promise.resolve(n++ === 0 ? BEFORE : AFTER));
  const { result } = track(() => "worlds", () => undefined, read);
  await waitFor(() => expect(result.current.data).toEqual(BEFORE));
  await act(async () => { announceRunSettled(); });
  await waitFor(() => expect(result.current.data).toEqual(AFTER));
  expect(read).toHaveBeenLastCalledWith("worlds", true);
});

test("an unmounted page no longer listens for a run's end", async () => {
  const { announceRunSettled } = await import("./memoEpoch");
  const read = vi.fn(() => Promise.resolve(BEFORE));
  const { result, unmount } = track(() => "worlds", () => undefined, read);
  await waitFor(() => expect(result.current.data).toEqual(BEFORE));
  unmount();
  const calls = read.mock.calls.length;
  act(() => { announceRunSettled(); });
  expect(read.mock.calls.length).toBe(calls);
});
