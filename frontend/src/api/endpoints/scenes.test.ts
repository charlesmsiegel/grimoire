// The `api` client against a stubbed `fetch`, for scene play: the
// transcript, the turn producers and the reads that must never be shared
// (`./endpoints/scenes`).
import { api } from "../client";
import { draftRunResponse, jsonBody, jsonOk, sseResponse } from "../../testkit/apiResponses";

test("scene creation carries reviewed initial focus in the same request", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "mara" }));
  globalThis.fetch = fetchMock;
  await api.createScene("saltmarch", "Mara", undefined, false, "Let Mara choose.");
  expect(fetchMock).toHaveBeenCalledWith("/api/campaigns/saltmarch/scenes", expect.objectContaining({
    method: "POST", body: JSON.stringify({ title: "Mara", pcless: false, focus: "Let Mara choose." }),
  }));
});

test("fresh context after focus publication does not join a pre-save request", async () => {
  let finishOld: (value: Response) => void = () => {};
  const fetchMock = vi.fn<typeof fetch>()
    .mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }))
    .mockResolvedValueOnce(jsonOk({ text: "Seraphine chooses.", status: "ok" }))
    .mockResolvedValueOnce(jsonOk({ sections: [{ id: "scene_focus", text: "Seraphine chooses." }] }));
  globalThis.fetch = fetchMock;
  const old = api.getSceneContext("saltmarch", "mara");
  try {
    await api.setSceneFocus("saltmarch", "mara", "Seraphine chooses.");
    const current = api.getSceneContext("saltmarch", "mara", { fresh: true });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect((await current).sections[0].text).toBe("Seraphine chooses.");
  } finally {
    finishOld(jsonOk({ sections: [{ id: "scene_focus", text: "Mara waits." }] }));
    expect((await old).sections[0].text).toBe("Mara waits.");
  }
});

test("renameScene PUTs to the scene under its campaign", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "s2", title: "New" }));
  globalThis.fetch = fetchMock;
  await api.renameScene("run", "s1", "New");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ title: "New" }) }),
  );
});

test("deleteScene issues DELETE under its campaign", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;
  await api.deleteScene("run", "s1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1",
    expect.objectContaining({ method: "DELETE" }),
  );
});

test("chat posts to the scene chat endpoint and forwards SSE events", async () => {
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockResolvedValue(sseResponse(['data: {"delta":"hi"}\n\n', 'data: {"done":true}\n\n']));
  globalThis.fetch = fetchMock;
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
    .fn<typeof fetch>()
    .mockResolvedValue(sseResponse([": heartbeat\n\n", 'data: {"delta":"hi"}\n\n']));
  globalThis.fetch = fetchMock;
  const controller = new AbortController();
  const events: unknown[] = [];
  await api.chat("run", "s1", "hello", (e) => events.push(e), undefined, controller.signal);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/chat",
    expect.objectContaining({ signal: controller.signal }),
  );
  expect(events).toEqual([{ delta: "hi" }]);
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
  let releaseFirst: (v: Response) => void = () => {};
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk({ active: 1, alternates: [{ id: "b" }] }));
  globalThis.fetch = fetchMock;

  const older = api.getAlternates("run", "s1");   // in flight, unresolved
  const newer = api.getAlternates("run", "s1");   // issues its own, not shared
  expect(fetchMock).toHaveBeenCalledTimes(2);

  expect(await newer).toEqual({ active: 1, alternates: [{ id: "b" }] });
  releaseFirst(jsonOk({ active: 0, alternates: [{ id: "a" }] }));
  expect(await older).toEqual({ active: 0, alternates: [{ id: "a" }] });
});

test("swipe reads never coalesce, so a refresh after a swipe sees the swipe", async () => {
  // `useResponseSwipe` re-reads after every activate, reroll and delete. A
  // shared read is as old as the request it joined, so a refresh issued while
  // the pre-mutation GET is still open would install that older state: the
  // counter names the take the swipe just replaced.
  let releaseFirst: (v: Response) => void = () => {};
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk({ active: 1, variants: [{ id: "a" }, { id: "b" }] }));
  globalThis.fetch = fetchMock;

  const older = api.getResponseSwipe("run", "s1", "r1");   // in flight, unresolved
  const newer = api.getResponseSwipe("run", "s1", "r1");   // issues its own, not shared
  expect(fetchMock).toHaveBeenCalledTimes(2);

  expect((await newer).active).toBe(1);
  releaseFirst(jsonOk({ active: 0, variants: [{ id: "a" }, { id: "b" }] }));
  expect((await older).active).toBe(0);
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
  let releaseFirst: (v: Response) => void = () => {};
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk({ messages: [{ role: "assistant", content: "new" }] }));
  globalThis.fetch = fetchMock;

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
  let releaseFirst: (v: Response) => void = () => {};
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk([{ id: "s1-renamed" }]));
  globalThis.fetch = fetchMock;

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
  let releaseFirst: (v: Response) => void = () => {};
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk({ record: { id: "pr-1" } }));
  globalThis.fetch = fetchMock;

  const older = api.getRollProposal("run", "s1");   // in flight, unresolved
  const newer = api.getRollProposal("run", "s1");   // issues its own, not shared
  expect(fetchMock).toHaveBeenCalledTimes(2);

  expect(await newer).toEqual({ record: { id: "pr-1" } });
  releaseFirst(jsonOk({ record: null }));
  expect(await older).toEqual({ record: null });    // its own answer, not the newer one
});

test("retry posts to the scene retry endpoint", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(sseResponse(['data: {"done":true}\n\n']));
  globalThis.fetch = fetchMock;
  await api.retry("run", "s1", () => {});
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/retry",
    expect.objectContaining({ method: "POST" }),
  );
});

test("the scene ledger routes carry the idea and its status", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;
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

test("getScene without a window GETs the scene bare", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ meta: {}, messages: [] }));
  globalThis.fetch = fetchMock;
  await api.getScene("run", "s1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1",
    expect.objectContaining({ method: "GET" }),
  );
});

test("getScene passes the window as limit/before query params", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ meta: {}, messages: [], offset: 0, total: 0, has_older: false }));
  globalThis.fetch = fetchMock;
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

test("prompt-list reads never coalesce, so a post-generation refresh sees the new turn", async () => {
  // The list is re-read on the refreshKey a completed generation bumps, and the
  // turn that generation just captured is the entire reason for the re-read. A
  // shared in-flight GET from before the turn answers with a list that cannot
  // contain the new row, leaving Turn history a turn behind (#157).
  let releaseFirst: (v: Response) => void = () => {};
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockImplementationOnce(() => new Promise((r) => { releaseFirst = r; }))
    .mockResolvedValue(jsonOk({ entries: [{ id: "000002" }] }));
  globalThis.fetch = fetchMock;

  const older = api.listScenePrompts("run", "s1");   // opened before the turn landed
  const newer = api.listScenePrompts("run", "s1");   // issues its own, not shared
  expect(fetchMock).toHaveBeenCalledTimes(2);

  expect(await newer).toEqual({ entries: [{ id: "000002" }] });
  releaseFirst(jsonOk({ entries: [] }));
  expect(await older).toEqual({ entries: [] });
});

test("one frozen snapshot IS shared, because it cannot go stale", async () => {
  // The exception to the exception: a captured prompt never changes, so two
  // readers of the same entry may share one answer.
  let release: (v: Response) => void = () => {};
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(() => new Promise((r) => { release = r; }));
  globalThis.fetch = fetchMock;
  const a = api.getScenePrompt("run", "s1", "000001");
  const b = api.getScenePrompt("run", "s1", "000001");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  release(jsonOk({ id: "000001", sections: [] }));
  expect(await a).toEqual({ id: "000001", sections: [] });
  expect(await b).toEqual({ id: "000001", sections: [] });
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
  ["extendResponse",
   (on, a, oi) => api.extendResponse("c1", "s1", "r1", on, undefined, undefined, a, oi)],
];

test("extendResponse posts its overrides to the response's extend route", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(sseResponse(['data: {"done":true}\n\n']));
  globalThis.fetch = fetchMock;
  await api.extendResponse("c1", "s1", "r1", () => {},
                           { guidance: "Colder", provider: "realm", model: "qwen3", preset: "cold" });
  expect(fetchMock.mock.calls[0][0]).toBe("/api/campaigns/c1/scenes/s1/responses/r1/extend");
  expect(jsonBody(fetchMock.mock.calls[0][1])).toEqual(
    { guidance: "Colder", provider: "realm", model: "qwen3", preset: "cold" });
});

test.each(TURN_PRODUCERS)(
  "%s sends its attempt and reports the wire index", async (_name, call) => {
    // All five routes are detached server-side. One that drops the attempt lets
    // the server mint its own, so the id the caller recorded names a run that
    // never existed -- Stop addresses nothing and recovery asks about an
    // attempt no route ever heard of. Four of the five dropped it (codex, P1).
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      sseResponse(['id: 4\ndata: {"delta":"The lamps are lit."}\n\n']));
    globalThis.fetch = fetchMock;
    const indexes: number[] = [];

    await call(() => {}, "a-7", (i) => indexes.push(i));

    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("X-Grimoire-Attempt")).toBe("a-7");
    // and the resume cursor is fed the WIRE index, which is the other half of
    // the pair -- a producer that forwarded one and not the other would leave
    // a reattach resuming from zero and replaying the whole reply.
    expect(indexes).toEqual([4]);
  });

test("editMessage sends restore only when asked, so every plain edit is unchanged", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock;
  await api.editMessage("run", "s1", 2, "plain");
  await api.editMessage("run", "s1", 2, "the original", { restore: true });
  expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/campaigns/run/scenes/s1/messages/2",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ content: "plain" }) }));
  expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/campaigns/run/scenes/s1/messages/2",
    expect.objectContaining({ method: "PUT",
                              body: JSON.stringify({ content: "the original", restore: true }) }));
});

test("branchScene POSTs the branch point, and a title only when given", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ id: "s1-b", scene: { meta: {}, messages: [] } }));
  globalThis.fetch = fetchMock;
  await api.branchScene("c1", "s1", 2);
  expect(fetchMock).toHaveBeenCalledWith("/api/campaigns/c1/scenes/s1/branch",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ through: 2 }) }));
  await api.branchScene("c1", "s1", 2, "Winifred");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/campaigns/c1/scenes/s1/branch",
    expect.objectContaining({ body: JSON.stringify({ through: 2, title: "Winifred" }) }));
});

test("startReplay asks for a branch only when told to", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ cut: 2, cascade: {} }));
  globalThis.fetch = fetchMock;
  await api.startReplay("c1", "s1", 2, true);
  expect(jsonBody(fetchMock.mock.calls[0][1])).toEqual({ index: 2, branch: true });
  await api.startReplay("c1", "s1", 2);
  expect(jsonBody(fetchMock.mock.calls[1][1])).toEqual({ index: 2 });
});

test("sceneSuggestions sends its options as one JSON body", async () => {
  // A body rather than a query string: the controls are lists, and the server
  // ignores the query whenever a body is present (§16.3).
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    draftRunResponse("landed", { kind: "scene-suggestions", result: { suggestions: [] } }));
  globalThis.fetch = fetchMock;

  await api.sceneSuggestions("c", { after: "s1", focus_refs: ["thread:mara-s-map"] });

  const [url, init] = fetchMock.mock.calls[0] as [string, { method: string; body: string }];
  expect(url).toBe("/api/campaigns/c/scene-suggestions");
  expect(init.method).toBe("POST");
  expect(JSON.parse(init.body)).toEqual({
    offscreen: false, direction: "", rank: true, focus_refs: ["thread:mara-s-map"],
    avoid_refs: [], must_refs: [], time_mode: "auto", time_anchor_ref: "",
    time_anchor_relation: "", after: "s1",
  });

  await api.sceneSuggestions("c");
  const last = fetchMock.mock.calls[fetchMock.mock.calls.length - 1] as [string, { body: string }];
  const bare = JSON.parse(last[1].body);
  expect(bare).not.toHaveProperty("after");
});
