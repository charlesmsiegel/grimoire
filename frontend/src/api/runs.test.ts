// The `api` client against a stubbed `fetch`, for the detached draft
// runs and the streaming opener's recovery (`./runs`).
import { api, ApiError, RefreshRefused } from "./client";
import { onConfigChanged } from "../appEvents";
import { fixture } from "../testkit/fixture";
import { draftRunResponse, jsonOk, sseResponse } from "../testkit/apiResponses";

// ---- the draft family, detached (#398) -------------------------------------
//
// The twelve computing draft routes are `start -> poll -> unwrap` and nothing
// else, which is why they share one helper. These pin what that helper owes
// each of them: it polls the SUBJECT's run routes, it hands back the result
// unchanged, it raises a failure as the HTTP failure the synchronous route
// raised, and it does not read a lookup it could not make as "there is no run".

/** An SSE response whose socket dies part-way — the case detachment exists for.
 *
 *  `sseResponse` reaches a clean EOF, which is a stream that ENDED rather than
 *  one that was cut, and a client has no reason to pick that back up. */
function sseCutOff(chunks: string[]): Response {
  let i = 0;
  const body = new ReadableStream<Uint8Array<ArrayBuffer>>({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(new TextEncoder().encode(chunks[i++]));
      else controller.error(new TypeError("Failed to fetch"));
    },
  }, { highWaterMark: 0 });
  return fixture({ ok: true, body });
}

test("a draft polls its own subject's runs and unwraps the result", async () => {
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(draftRunResponse("running"))
      .mockResolvedValueOnce(draftRunResponse("running"))
      .mockResolvedValueOnce(draftRunResponse("landed",
        { result: { tagline: "Keeps the tide-gate." } }));
    globalThis.fetch = fetchMock;

    const pending = api.generateCharacterTagline("realm", "mara");
    await vi.advanceTimersByTimeAsync(5000);

    expect(await pending).toEqual({ tagline: "Keeps the tide-gate." });
    expect(fetchMock.mock.calls[0][0])
      .toBe("/api/worlds/realm/characters/mara/tagline/generate");
    // The WORLD's run routes, not a scene's: a tagline belongs to no scene, and
    // before #398 there was nowhere to ask about it at all.
    expect(fetchMock.mock.calls[1][0]).toBe("/api/worlds/realm/runs/r1");
  } finally {
    vi.useRealTimers();
  }
});

test("the start carries an attempt id, so a duplicate delivery is not paid for twice",
     async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    draftRunResponse("landed", { result: { tagline: "x" } }));
  globalThis.fetch = fetchMock;

  await api.generateCharacterTagline("realm", "mara");

  const init = fetchMock.mock.calls[0][1] as { headers: Record<string, string> };
  expect(init.headers["X-Grimoire-Attempt"]).toBeTruthy();
});

test("a Refresh refused at its start says so; a run gone from its poll does not", async () => {
  // Both are a 4xx with no `sweep`, but only the POST's refusal ran nothing:
  // a poll's 404 is a run reaped or a server restarted, after it may have
  // saved its findings (§26).
  globalThis.fetch = vi.fn().mockResolvedValueOnce({
    ok: false, status: 409,
    json: async () => ({ kind: "busy", detail: "the storage location is being changed" }),
  });
  const refused = await api.reconcileContinuity("run").catch((e: unknown) => e);
  expect(refused).toBeInstanceOf(RefreshRefused);
  expect((refused as ApiError).kind).toBe("busy");

  vi.useFakeTimers();
  try {
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(draftRunResponse("running", { kind: "continuity-reconcile" }))
      .mockResolvedValueOnce({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run for this subject",
                                                    kind: "run_gone" }) });
    const gone = api.reconcileContinuity("run").catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);
    const err = await gone;
    expect(err).toBeInstanceOf(ApiError);
    expect(err).not.toBeInstanceOf(RefreshRefused);
    expect((err as ApiError).kind).toBe("run_gone");
  } finally {
    vi.useRealTimers();
  }
});

test("a failed draft raises the failure the synchronous route used to raise", async () => {
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(draftRunResponse("running"))
      .mockResolvedValueOnce(draftRunResponse("failed", {
        error: { kind: "rate_limit", detail: "slow down", status: 429 } }));
    globalThis.fetch = fetchMock;

    const failed = api.generateCharacterTagline("realm", "mara")
      .then(() => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);

    const err = await failed;
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(429);
    expect((err as ApiError).kind).toBe("rate_limit");
  } finally {
    vi.useRealTimers();
  }
});

test("a start whose reply never arrived adopts the run it may have made", async () => {
  // A POST WITH NO RESPONSE IS AMBIGUOUS: the server can have accepted it,
  // reserved the run and begun a provider call, and the 202 be lost on the way
  // back. Reported as a failure, the user starts it again and pays twice.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(jsonOk({ runs: [
        { id: "r1", attempt_id: "a1", state: "running", next_index: 0, cls: "draft" }] }))
      .mockResolvedValueOnce(draftRunResponse("landed", { result: { tagline: "x" } }));
    globalThis.fetch = fetchMock;

    const pending = api.generateCharacterTagline("realm", "mara");
    await vi.advanceTimersByTimeAsync(5000);

    expect(await pending).toEqual({ tagline: "x" });
    // Found by the ATTEMPT, never by the kind: drafts overlap on one coarse
    // subject by design, so "the world's in-flight tagline run" is ambiguous.
    expect(fetchMock.mock.calls[1][0]).toContain("/api/worlds/realm/runs?attempt=");
  } finally {
    vi.useRealTimers();
  }
});

test("a lookup that could not be made is not read as 'there is no run'", async () => {
  // The defect the first phase of this work shipped three times: a
  // `.catch(() => null)` on a discovery turns "I could not ask" into "there is
  // nothing there". The lookup is retried, and only a real empty answer settles
  // it -- so the run started before the network went is still adopted.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(jsonOk({ runs: [
        { id: "r1", attempt_id: "a1", state: "running", next_index: 0, cls: "draft" }] }))
      .mockResolvedValueOnce(draftRunResponse("landed", { result: { tagline: "x" } }));
    globalThis.fetch = fetchMock;

    const pending = api.generateCharacterTagline("realm", "mara");
    await vi.advanceTimersByTimeAsync(10000);

    expect(await pending).toEqual({ tagline: "x" });
  } finally {
    vi.useRealTimers();
  }
});

test("a server that answered is the caller's to handle, never a run to adopt", async () => {
  // An `ApiError` means the request was received and refused -- a 404 for a
  // character that is gone, a 409 for a missing key. Adopting a run for one of
  // those would hide a setup mistake behind a wait that never ends.
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    fixture({ ok: false, status: 404, json: async () => ({ detail: "character not found" }) }));
  globalThis.fetch = fetchMock;

  await expect(api.generateCharacterTagline("realm", "nobody")).rejects.toThrow();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

// ---- the opener, the one streaming draft ------------------------------------

test("an opener whose connection dies is picked back up where it stopped", async () => {
  // The acceptance criterion in one test: backgrounding the app during an
  // opener used to lose it outright, because the only copy of each frame was
  // the one already on the wire.
  const seen: string[] = [];
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(sseCutOff([
      'data: {"run":{"id":"r1","attempt_id":"a1","state":"running","next_index":0}}\n\n',
      'id: 0\ndata: {"delta":"The "}\n\n',
    ]))
    // `next_index` is 2: the run kept generating while the client was away, so
    // the live tail is AHEAD of what was delivered. That gap is the whole
    // reason a client resumes from what it read rather than from the tail.
    .mockResolvedValueOnce(jsonOk({ run: { id: "r1", attempt_id: "a1", state: "running",
                                           next_index: 2, cls: "draft" } }))
    .mockResolvedValueOnce(sseResponse([
      'id: 1\ndata: {"delta":"quay."}\n\n', 'id: 2\ndata: {"done":true}\n\n',
    ]));
  globalThis.fetch = fetchMock;

  await api.opener("run", "s1", "begin", (e) => { if (e.delta) seen.push(e.delta); });

  expect(seen.join("")).toBe("The quay.");
  // Found by the attempt, so a second opener on the same scene cannot be
  // mistaken for this one.
  expect(fetchMock.mock.calls[1][0]).toContain("attempt=");
  // From ONE PAST the last frame actually delivered, never from the run's
  // `next_index`: that is the live tail, and resuming there drops the reply.
  expect(fetchMock.mock.calls[2][0])
    .toBe("/api/campaigns/run/scenes/s1/runs/r1/stream?from=1");
});

test("an opener the server never heard of reports the failure it really had",
     async () => {
  const fetchMock = vi.fn<typeof fetch>()
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValueOnce(jsonOk({ run: null }));
  globalThis.fetch = fetchMock;

  await expect(api.opener("run", "s1", "begin", () => {})).rejects.toThrow("Failed to fetch");
});

test("an opener lookup that could not be made does not end the recovery", async () => {
  // A failed discovery has not answered "there is no run". Ending here would
  // abandon a generation that is still going -- and the reader would see a
  // half-written opener with no way back to the rest of it.
  const seen: string[] = [];
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(sseCutOff([
      'data: {"run":{"id":"r1","attempt_id":"a1","state":"running","next_index":0}}\n\n',
      'id: 0\ndata: {"delta":"The "}\n\n',
    ]))
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValueOnce(jsonOk({ run: { id: "r1", attempt_id: "a1", state: "running",
                                           next_index: 2, cls: "draft" } }))
    .mockResolvedValueOnce(sseResponse([
      'id: 1\ndata: {"delta":"quay."}\n\n', 'id: 2\ndata: {"done":true}\n\n',
    ]));
  globalThis.fetch = fetchMock;

  await api.opener("run", "s1", "begin", (e) => { if (e.delta) seen.push(e.delta); });

  expect(seen.join("")).toBe("The quay.");
});

test("an opener stream closed cleanly mid-generation is still picked back up", async () => {
  // A proxy or a WebView can end the response body without an error: the
  // reader reports `done` and `streamPost` resolves. A recovery hung off the
  // exception alone returns happily and leaves a half-written opener on screen
  // while the server finishes the rest into a buffer nobody reads.
  const seen: string[] = [];
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(sseResponse([
      'data: {"run":{"id":"r1","attempt_id":"a1","state":"running","next_index":0}}\n\n',
      'id: 0\ndata: {"delta":"The "}\n\n',
    ]))
    .mockResolvedValueOnce(jsonOk({ run: { id: "r1", attempt_id: "a1", state: "running",
                                           next_index: 2, cls: "draft" } }))
    .mockResolvedValueOnce(sseResponse([
      'id: 1\ndata: {"delta":"quay."}\n\n', 'id: 2\ndata: {"done":true}\n\n',
    ]));
  globalThis.fetch = fetchMock;

  await api.opener("run", "s1", "begin", (e) => { if (e.delta) seen.push(e.delta); });

  expect(seen.join("")).toBe("The quay.");
});

test("an opener that really did finish is not re-attached to", async () => {
  // The other half of the same rule: a `done` frame IS the end, and a client
  // that went back for more would spend a request per opener forever.
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(sseResponse([
    'data: {"run":{"id":"r1","attempt_id":"a1","state":"running","next_index":0}}\n\n',
    'id: 0\ndata: {"delta":"The quay."}\n\n', 'id: 1\ndata: {"done":true}\n\n',
  ]));
  globalThis.fetch = fetchMock;

  await api.opener("run", "s1", "begin", () => {});

  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test("a failed draft announces on the config channel — the status bar is stale",
     async () => {
  // A detached failure never goes through `requestRaw`'s non-2xx branch: the
  // 202 was a success and the provider failed minutes later. Without the same
  // announcement the status bar shows the verdict from before the failure
  // until the next navigation (#146).
  vi.useFakeTimers();
  try {
    const seen: string[] = [];
    const off = onConfigChanged(() => seen.push("config"));
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(draftRunResponse("running"))
      .mockResolvedValueOnce(draftRunResponse("failed", {
        error: { kind: "auth", detail: "bad key", status: 502 } }));

    const failed = api.generateCharacterTagline("realm", "mara").catch(() => null);
    await vi.advanceTimersByTimeAsync(5000);
    await failed;

    expect(seen).toEqual(["config"]);
    off();
  } finally {
    vi.useRealTimers();
  }
});

test("an opener whose scene was renamed under it is still picked back up", async () => {
  // An opener holds no exclusion key — deliberately, so it cannot refuse the
  // `first-post` it exists to feed — so another tab may rename the scene while
  // it runs. A rename mints a new `sid`, and the run is indexed by the scene's
  // identity, so the old id resolves to no run and every re-attach would
  // abandon a generation the server is still buffering.
  const seen: string[] = [];
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(sseCutOff([
      'data: {"run":{"id":"r1","attempt_id":"a1","state":"running",'
        + '"next_index":0,"scene_identity":"ident-1"}}\n\n',
      'id: 0\ndata: {"delta":"The "}\n\n',
    ]))
    // The old id knows nothing about it any more.
    .mockResolvedValueOnce(jsonOk({ run: null }))
    .mockResolvedValueOnce(jsonOk({ id: "s2-renamed" }))
    .mockResolvedValueOnce(jsonOk({ run: { id: "r1", attempt_id: "a1",
                                           state: "running", next_index: 2,
                                           cls: "draft" } }))
    .mockResolvedValueOnce(sseResponse([
      'id: 1\ndata: {"delta":"quay."}\n\n', 'id: 2\ndata: {"done":true}\n\n',
    ]));
  globalThis.fetch = fetchMock;

  await api.opener("run", "s1", "begin", (e) => { if (e.delta) seen.push(e.delta); });

  expect(seen.join("")).toBe("The quay.");
  expect(fetchMock.mock.calls[2][0]).toContain("scene-by-identity?identity=ident-1");
  // And the re-attach goes to the scene's CURRENT id, not the one the stream
  // was opened on.
  expect(fetchMock.mock.calls[4][0])
    .toBe("/api/campaigns/run/scenes/s2-renamed/runs/r1/stream?from=1");
});

test("an opener renamed between the lookup and the attach is still picked back up",
     async () => {
  // The rename can land in the gap AFTER discovery answered and BEFORE the
  // attach goes out, and then it is the attach that 404s. Re-resolving only on
  // an empty lookup leaves that window open.
  const seen: string[] = [];
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(sseCutOff([
      'data: {"run":{"id":"r1","attempt_id":"a1","state":"running",'
        + '"next_index":0,"scene_identity":"ident-1"}}\n\n',
      'id: 0\ndata: {"delta":"The "}\n\n',
    ]))
    // Discovery still answers on the old id...
    .mockResolvedValueOnce(jsonOk({ run: { id: "r1", attempt_id: "a1",
                                           state: "running", next_index: 2,
                                           cls: "draft" } }))
    // ...and the attach is what finds it gone.
    .mockResolvedValueOnce(fixture({ ok: false, status: 404,
                             json: async () => ({ detail: "no such run",
                                                  kind: "run_gone" }) }))
    .mockResolvedValueOnce(jsonOk({ id: "s2-renamed" }))
    .mockResolvedValueOnce(jsonOk({ run: { id: "r1", attempt_id: "a1",
                                           state: "running", next_index: 2,
                                           cls: "draft" } }))
    .mockResolvedValueOnce(sseResponse([
      'id: 1\ndata: {"delta":"quay."}\n\n', 'id: 2\ndata: {"done":true}\n\n',
    ]));
  globalThis.fetch = fetchMock;

  await api.opener("run", "s1", "begin", (e) => { if (e.delta) seen.push(e.delta); });

  expect(seen.join("")).toBe("The quay.");
  expect(fetchMock.mock.calls[5][0])
    .toBe("/api/campaigns/run/scenes/s2-renamed/runs/r1/stream?from=1");
});

test("aborting a draft stops the request in flight, not only the wait between them",
     async () => {
  // `sleepUnlessAborted` covers the gap BETWEEN requests. A caller that aborts
  // while a poll or a start is actually in flight -- an unreachable local
  // server on Android is the case -- would otherwise wait out the transport.
  const seen: (AbortSignal | undefined)[] = [];
  globalThis.fetch = vi.fn().mockImplementation(
    (_p: string, init: { signal?: AbortSignal }) => {
      seen.push(init.signal);
      return Promise.resolve(draftRunResponse("landed", { result: { tagline: "x" } }));
    });
  const stop = new AbortController();

  await api.generateCharacterTagline("realm", "mara", stop.signal);

  expect(seen[0]).toBe(stop.signal);
});

test("an identity lookup that could not be made does not end the opener recovery",
     async () => {
  // The same rule as the run lookup, in the one place I had written its
  // opposite: a `scene-by-identity` that fails transiently has NOT established
  // that the scene stayed put, so concluding no-move abandons an opener still
  // buffered under the new id. Only a 404 is an answer.
  const seen: string[] = [];
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(sseCutOff([
      'data: {"run":{"id":"r1","attempt_id":"a1","state":"running",'
        + '"next_index":0,"scene_identity":"ident-1"}}\n\n',
      'id: 0\ndata: {"delta":"The "}\n\n',
    ]))
    .mockResolvedValueOnce(jsonOk({ run: null }))
    // The resolver itself falls over -- a 409 while the scan could not read
    // every candidate is the server's own "ask me again".
    .mockResolvedValueOnce(fixture({ ok: false, status: 409,
                             json: async () => ({ detail: "busy", kind: "busy" }) }))
    // Second attempt: the lookup answers, and the scene has moved.
    .mockResolvedValueOnce(jsonOk({ run: null }))
    .mockResolvedValueOnce(jsonOk({ id: "s2-renamed" }))
    .mockResolvedValueOnce(jsonOk({ run: { id: "r1", attempt_id: "a1",
                                           state: "running", next_index: 2,
                                           cls: "draft" } }))
    .mockResolvedValueOnce(sseResponse([
      'id: 1\ndata: {"delta":"quay."}\n\n', 'id: 2\ndata: {"done":true}\n\n',
    ]));
  globalThis.fetch = fetchMock;

  await api.opener("run", "s1", "begin", (e) => { if (e.delta) seen.push(e.delta); });

  expect(seen.join("")).toBe("The quay.");
});

test("a scene that really is gone ends the opener recovery rather than looping",
     async () => {
  // The other half: a 404 from `scene-by-identity` IS an answer -- nothing
  // carries that identity any more -- so the original failure stands instead
  // of burning every remaining attempt on a scene that is not coming back.
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(sseCutOff([
      'data: {"run":{"id":"r1","attempt_id":"a1","state":"running",'
        + '"next_index":0,"scene_identity":"ident-1"}}\n\n',
    ]))
    .mockResolvedValueOnce(jsonOk({ run: null }))
    .mockResolvedValueOnce(fixture({ ok: false, status: 404,
                             json: async () => ({ detail: "no scene carries that identity",
                                                  kind: "scene_gone" }) }));
  globalThis.fetch = fetchMock;

  await expect(api.opener("run", "s1", "begin", () => {}))
    .rejects.toThrow("Failed to fetch");
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

test("aborting an opener stops its recovery lookups, not just its streams", async () => {
  // The recovery makes two kinds of request of its own -- discovery and the
  // rename resolution -- and both went out without a signal, so aborting while
  // one was in flight waited out the transport. Same gap as the draft fetches,
  // in the helpers `streamDraft` reaches for rather than in `draftRun`.
  const signals: (AbortSignal | undefined)[] = [];
  const stop = new AbortController();
  globalThis.fetch = vi.fn().mockImplementation(
    (path: string, init?: { signal?: AbortSignal }) => {
      signals.push(init?.signal);
      if (String(path).includes("/opener")) {
        return Promise.resolve(sseCutOff([
          'data: {"run":{"id":"r1","attempt_id":"a1","state":"running",'
            + '"next_index":0,"scene_identity":"ident-1"}}\n\n',
        ]));
      }
      if (String(path).includes("/run?attempt=")) return Promise.resolve(jsonOk({ run: null }));
      if (String(path).includes("scene-by-identity")) {
        return Promise.resolve({ ok: false, status: 404,
                                 json: async () => ({ detail: "gone",
                                                      kind: "scene_gone" }) });
      }
      return Promise.resolve(jsonOk({}));
    });

  await api.opener("run", "s1", "begin", () => {}, stop.signal)
    .catch(() => null);

  // The POST, the discovery, and the identity resolution: every one of them.
  expect(signals.length).toBeGreaterThanOrEqual(3);
  expect(signals.every((s) => s === stop.signal)).toBe(true);
});

test("a draft announces its run settled only once it is seen to end", async () => {
  const { onRunSettled } = await import("./memoEpoch");
  const heard = vi.fn();
  const off = onRunSettled(heard);
  try {
    globalThis.fetch = vi.fn().mockResolvedValue(
      draftRunResponse("landed", { result: { tagline: "x" } }));
    await api.generateCharacterTagline("realm", "mara");
    expect(heard).toHaveBeenCalledTimes(1);
  } finally {
    off();
  }
});

test("an abandoned draft wait announces nothing: the run is still going", async () => {
  const { onRunSettled } = await import("./memoEpoch");
  const heard = vi.fn();
  const off = onRunSettled(heard);
  vi.useFakeTimers();
  try {
    globalThis.fetch = vi.fn().mockResolvedValue(draftRunResponse("running"));
    const stop = new AbortController();
    const pending = api.generateCharacterTagline("realm", "mara", stop.signal);
    const settled = pending.catch((err: unknown) => err);
    await vi.advanceTimersByTimeAsync(1000);
    stop.abort();
    await vi.advanceTimersByTimeAsync(5000);
    expect((await settled as { name?: string }).name).toBe("AbortError");
    expect(heard).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
    off();
  }
});
