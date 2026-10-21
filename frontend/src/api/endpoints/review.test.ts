// The `api` client against a stubbed `fetch`, for the end-of-scene
// review family (`./endpoints/review`).
import { api, ApiError } from "../client";
import { onShellChanged } from "../../appEvents";
import { fixture } from "../../testkit/fixture";
import { jsonOk } from "../../testkit/apiResponses";

// ---- the review family, detached (#396) ------------------------------------
//
// `absorbScene` is no longer one request that waits: the POST answers 202 the
// moment the run is reserved -- which is what makes a locked phone survivable
// -- and the client polls, then reads the review off the store. These pin the
// three parts of that a caller depends on: it waits, it answers with the
// stored review, and a run that failed raises the failure the synchronous
// route used to raise.

function runResponse(state: string, extra: Record<string, unknown> = {}) {
  return jsonOk({ run: { id: "r1", attempt_id: null, state, next_index: 0,
                         cls: "review", ...extra } });
}

test("absorbScene polls the run and answers with the stored review", async () => {
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(runResponse("running"))
      .mockResolvedValueOnce(runResponse("landed"))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "They met." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.absorbScene("run", "s1");
    // Two ticks of the poll, so the "still running" answer is really waited on
    // rather than the loop spinning through it.
    await vi.advanceTimersByTimeAsync(5000);
    const got = await pending;
    expect(got.generation).toBe("gen1");
    expect(got.review.one_line).toBe("They met.");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/campaigns/run/scenes/s1/absorb");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/campaigns/run/scenes/s1/runs/r1");
    expect(fetchMock.mock.calls[3][0])
      .toBe("/api/campaigns/run/scenes/s1/pending-review");
  } finally {
    vi.useRealTimers();
  }
});

test.each([false, true])("a landed absorb refreshes the shell before returning its review (recovered: %s)", async (recovered) => {
  vi.useFakeTimers();
  const changed = vi.fn();
  const off = onShellChanged(changed);
  try {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonOk({
      run: { id: "r1", state: recovered ? "running" : "landed", cls: "review",
             attempt_id: null, next_index: 0 }, generation: "gen1",
    }));
    if (recovered) fetchMock.mockResolvedValueOnce({ ok: false, status: 404,
      json: async () => ({ detail: "no such run", kind: "run_gone" }) });
    fetchMock.mockResolvedValueOnce(jsonOk({ review: { one_line: "They met." },
      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.absorbScene("run", "s1");
    expect(changed).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(5000);
    expect((await pending).review.one_line).toBe("They met.");
    expect(changed).toHaveBeenCalledTimes(1);
  } finally {
    off();
    vi.useRealTimers();
  }
});

test("an absorb whose review no longer matches does not announce a landed review", async () => {
  const changed = vi.fn();
  const off = onShellChanged(changed);
  try {
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "landed", cls: "review",
        attempt_id: null, next_index: 0 }, generation: "gen1" }))
      .mockResolvedValueOnce(jsonOk({ review: null, generation: null, stale: null }));
    await expect(api.absorbScene("run", "s1")).rejects.toMatchObject({ kind: "review_stale" });
    expect(changed).not.toHaveBeenCalled();
  } finally { off(); }
});

test("a failed run raises the failure the synchronous route used to raise", async () => {
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(runResponse("failed", {
        error: { kind: "timeout", detail: "absorb time budget exhausted", status: 504 } }));
    globalThis.fetch = fetchMock;
    const pending = api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); },
      (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);
    const err = await pending;
    // The status and kind travel with the run, so a caller that already knows
    // what to do with a 504 timeout needs no second shape to understand.
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(504);
    expect((err as ApiError).kind).toBe("timeout");
  } finally {
    vi.useRealTimers();
  }
});

test("a landed absorb whose record is gone is reported as stale, not as an empty review",
     async () => {
  // The window the watermark exists for, from the client's side: the run
  // landed and the scene moved on between the two calls. Handing the panel a
  // null would render as a review with nothing in it, which reads as a model
  // that had nothing to say.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "landed", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(jsonOk({ review: null, generation: "gen1",
                                      stale: { prepared_posts: 1, current_posts: 2 } }));
    globalThis.fetch = fetchMock;
    const err = await api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    expect((err as ApiError).kind).toBe("review_stale");
  } finally {
    vi.useRealTimers();
  }
});

test("discardReview names the generation, and only that", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk({ removed: true, stopped: 1 }));
  globalThis.fetch = fetchMock;
  await api.discardReview("run", "s1", "gen/1");
  expect(fetchMock.mock.calls[0][0])
    .toBe("/api/campaigns/run/scenes/s1/pending-review?generation=gen%2F1");
  expect(fetchMock.mock.calls[0][1]).toEqual(
    expect.objectContaining({ method: "DELETE" }));
});

test("liveReview ignores a live run that is not a review", async () => {
  // The newest run on a scene is as likely to be a chat turn, and adopting one
  // of those as a review would leave End Scene spinning over a reply.
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk(
    { run: { id: "r1", state: "running", cls: "turn", attempt_id: "a", next_index: 3 } }));
  globalThis.fetch = fetchMock;
  expect(await api.liveReview("run", "s1")).toBeNull();
});

test("liveReview answers with a review still being prepared", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonOk(
    { run: { id: "r1", state: "running", cls: "review", attempt_id: null,
             next_index: 0, review_generation: "gen1" } }));
  globalThis.fetch = fetchMock;
  expect((await api.liveReview("run", "s1"))?.review_generation).toBe("gen1");
});

test("an absorb whose run was reaped is recovered from the store", async () => {
  // The case this whole feature exists for, from the client's side: the tab was
  // suspended for longer than the run record lives, so the poll 404s on a run
  // whose review landed and is on disk. Failing there would report exactly the
  // loss the durable review was built to prevent.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(fixture({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) }))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "They met." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.absorbScene("run", "s1");
    await vi.advanceTimersByTimeAsync(5000);
    expect((await pending).review.one_line).toBe("They met.");
  } finally {
    vi.useRealTimers();
  }
});

test("a failed run does not hand back the review that was already there", async () => {
  // The fallback matches on GENERATION, not on something being stored. A scene
  // can be holding an earlier review the reader never saved, and handing that
  // back for a run that genuinely failed shows a stale summary as this
  // absorb's result -- a wrong answer presented as a right one.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen2" }))
      .mockResolvedValueOnce(runResponse("failed", {
        error: { kind: "run_failed", detail: "the extractor blew up", status: 409 } }))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "An older review." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const failed = api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);
    expect((await failed as ApiError).kind).toBe("run_failed");
  } finally {
    vi.useRealTimers();
  }
});

test("a landed run whose record belongs to another review is refused", async () => {
  // Discarded and re-absorbed from a second tab while this one waited. The
  // record on disk is a review, and it is not this one's.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "landed", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "Somebody else's." },
                                      generation: "gen2", stale: null }));
    globalThis.fetch = fetchMock;
    const failed = api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);
    expect((await failed as ApiError).kind).toBe("review_stale");
  } finally {
    vi.useRealTimers();
  }
});

test("a reaped run with nothing on disk still reports the failure", async () => {
  // The counterweight: the fallback must not swallow a real failure into
  // silence. Nothing stored means the absorb really is gone.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(fixture({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) }))
      .mockResolvedValueOnce(jsonOk({ review: null, generation: null, stale: null }));
    globalThis.fetch = fetchMock;
    const failed = api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);
    expect((await failed as ApiError).kind).toBe("run_gone");
  } finally {
    vi.useRealTimers();
  }
});

test("a failed poll is not a failed run", async () => {
  // The conditions this feature exists for -- a backgrounded WebView, a
  // suspended tab resuming into a dead socket -- all show up as a failed poll,
  // and ending the wait for one reports a failure for a run that is still
  // generating: a banner, a composer unlocked over a scene the server is still
  // holding, and nothing to open the review when it lands.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(runResponse("landed"))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "They met." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.absorbScene("run", "s1");
    await vi.advanceTimersByTimeAsync(20000);
    expect((await pending).review.one_line).toBe("They met.");
  } finally {
    vi.useRealTimers();
  }
});

test("a poll that keeps failing does give up", async () => {
  // The counterweight: riding out a transient failure must not become waiting
  // forever on a server that is gone.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockRejectedValue(new TypeError("Failed to fetch"));
    globalThis.fetch = fetchMock;
    const failed = api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(60000);
    expect(await failed).toBeInstanceOf(TypeError);
  } finally {
    vi.useRealTimers();
  }
});

test("an absorb whose 202 never arrived is adopted, not reported as failed", async () => {
  // The server can accept the POST, reserve the run and start generating, and
  // the 202 be lost on the way back -- a dropped link, a WebView backgrounded
  // in the same second. Reported as a failure, the caller clears its latch for
  // an absorb that is running: the scene is held for as long as it takes, End
  // scene answers `run_in_flight`, and there is no generation to offer a Stop
  // with, because `onStarted` never fired.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))        // the lost 202
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             kind: "absorb", attempt_id: null,
                                             next_index: 0,
                                             review_generation: "gen1" } }))
      .mockResolvedValueOnce(runResponse("landed"))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "They met." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const named: string[] = [];
    const pending = api.absorbScene("run", "s1", false, (g) => named.push(g));
    await vi.advanceTimersByTimeAsync(5000);

    expect((await pending).review.one_line).toBe("They met.");
    // ...and the Stop the panel needs is named, which is the half that makes
    // the adoption worth doing at all.
    expect(named).toEqual(["gen1"]);
  } finally {
    vi.useRealTimers();
  }
});

test("an absorb the server refused in words is not adopted", async () => {
  // Only a failure that carried NO reply is ambiguous. An `ApiError` means the
  // server answered -- `already_absorbed`, a missing key, a busy scene -- and
  // every one of those is the caller's to handle. Adopting a live run there
  // would swallow the confirmation prompt a re-absorb depends on.
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(fixture({ ok: false, status: 409,
                             json: async () => ({ detail: "already absorbed",
                                                  kind: "already_absorbed" }) }));
  globalThis.fetch = fetchMock;

  const failed = await api.absorbScene("run", "s1").then(
    () => { throw new Error("resolved"); }, (e: unknown) => e);

  expect((failed as ApiError).kind).toBe("already_absorbed");
  expect(fetchMock).toHaveBeenCalledTimes(1);       // no discovery attempt
});

test("a live retry of an OLDER review is not adopted as this absorb", async () => {
  // `review` is the class a whole review's runs share, so a scoped retry of
  // some earlier review's phase wears it too. Adopted here, it would install
  // that review's generation and hand its summary back as this End scene's
  // result -- a stale review presented as the one just asked for.
  const fetchMock = vi.fn<typeof fetch>()
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValueOnce(jsonOk({ run: { id: "r9", state: "running", cls: "review",
                                           kind: "audit", attempt_id: null,
                                           next_index: 0,
                                           review_generation: "gen-old" } }));
  globalThis.fetch = fetchMock;

  const failed = await api.absorbScene("run", "s1").then(
    () => { throw new Error("resolved"); }, (e: unknown) => e);

  expect(failed).toBeInstanceOf(TypeError);
});

test("an absorb that failed with no run to find still reports the failure", async () => {
  // The counterweight: a POST that never reached the server has nothing to
  // adopt, and silence there would leave the reader looking at a scene that
  // never started ending.
  const fetchMock = vi.fn<typeof fetch>()
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValueOnce(jsonOk({ run: null }));
  globalThis.fetch = fetchMock;

  const failed = await api.absorbScene("run", "s1").then(
    () => { throw new Error("resolved"); }, (e: unknown) => e);

  expect(failed).toBeInstanceOf(TypeError);
});

test("a reaped audit retry is read back off the stored review", async () => {
  // A retry is a detached run too, and it merges its phase into the durable
  // record before its own run record is anything but a receipt. A tab away
  // longer than the retention window polls a retry that landed perfectly well
  // and 404s: the panel keeps the phase it was retrying, and saving commits
  // those stale rows and clears the record. The completed retry is gone, and
  // nothing said so.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r2", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(fixture({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) }))
      .mockResolvedValueOnce(jsonOk({
        review: { mechanics: { status: "ok" },
                  edits: [{ id: "sheet:mara", kind: "sheet" },
                          { id: "lore:tea", kind: "lore" }] },
        generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.retryAudit("run", "s1");
    await vi.advanceTimersByTimeAsync(5000);

    const got = await pending;
    expect(got.mechanics.status).toBe("ok");
    // Only the rows this phase owns: the audit proposes the sheet edits and
    // proposes all of them, and the rest of the review is not its to hand back.
    expect(got.edits.map((e) => e.id)).toEqual(["sheet:mara"]);
  } finally {
    vi.useRealTimers();
  }
});

test("a reaped dossier retry hands back only the rows it re-proposed", async () => {
  // `proposed` is the phase's own list of who it prepared a dossier for -- the
  // same projection the server's merge kept the rows by -- so this reads back
  // exactly that retry's contribution and leaves the first pass's other NPCs
  // where they are.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r2", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(fixture({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) }))
      .mockResolvedValueOnce(jsonOk({
        review: { dossiers: { status: "ok", proposed: ["aese"] },
                  edits: [{ id: "d:aese", kind: "dossier", target: { id: "aese" } },
                          { id: "d:winifred", kind: "dossier",
                            target: { id: "winifred" } }] },
        generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.retryDossiers("run", "s1");
    await vi.advanceTimersByTimeAsync(5000);

    expect((await pending).edits.map((e) => e.id)).toEqual(["d:aese"]);
  } finally {
    vi.useRealTimers();
  }
});

test("a retry that FAILED is not answered with the phase it was retrying", async () => {
  // The narrowness is the point. A run that ended `failed` never merged
  // anything, so reading the record back hands the panel the PRE-retry phase
  // and presents it as this retry's result -- a wrong answer dressed as a right
  // one, and the reviewer never learns the retry did not run.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r2", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(runResponse("failed", {
        error: { kind: "run_failed", detail: "the auditor blew up", status: 409 } }))
      // The store has a perfectly good record for THIS review -- the pre-retry
      // one. Queued deliberately: the only thing that must stop it being
      // handed back is the failure being a failure and not a reaping.
      .mockResolvedValue(jsonOk({ review: { mechanics: { status: "failed" }, edits: [] },
                                  generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const failed = api.retryAudit("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);

    expect((await failed as ApiError).kind).toBe("run_failed");
  } finally {
    vi.useRealTimers();
  }
});

test("a reaped retry whose review has been replaced still reports the failure", async () => {
  // Matched on the generation, for `absorbScene`'s reason: a scene can be
  // holding a review this retry has nothing to do with.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r2", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockResolvedValueOnce(fixture({ ok: false, status: 404,
                               json: async () => ({ detail: "no such run",
                                                    kind: "run_gone" }) }))
      .mockResolvedValueOnce(jsonOk({ review: { mechanics: { status: "ok" }, edits: [] },
                                      generation: "gen2", stale: null }));
    globalThis.fetch = fetchMock;
    const failed = api.retryAudit("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);

    expect((await failed as ApiError).kind).toBe("run_gone");
  } finally {
    vi.useRealTimers();
  }
});

test("a transient failure on the read after a landed run is not a lost review", async () => {
  // This read is the answer to "did the absorb land", and every caller treats a
  // rejection as "it did not": the latch clears, the panel never opens, and the
  // adoption effect that would ask again does not re-run while the same scene
  // stays selected. A dropped fetch there loses a review that is on disk.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "landed", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "They met." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.absorbScene("run", "s1");
    await vi.advanceTimersByTimeAsync(5000);

    expect((await pending).review.one_line).toBe("They met.");
  } finally {
    vi.useRealTimers();
  }
});

test("a definite answer from the store is not asked for again", async () => {
  // Retrying is for the failures that clear on their own. `review_unreadable`
  // is an answer, and asking again only spends time before reporting it.
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValue(fixture({ ok: false, status: 409,
                         json: async () => ({ detail: "the stored review could not be read",
                                              kind: "review_unreadable" }) }));
  globalThis.fetch = fetchMock;

  const failed = await api.pendingReview("run", "s1").then(
    () => { throw new Error("resolved"); }, (e: unknown) => e);

  expect((failed as ApiError).kind).toBe("review_unreadable");
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test("a run still reported as running outlives a run of failed polls", async () => {
  // Out of patience is not a terminal state: nothing has observed the run stop,
  // only that one URL has not answered. Reported as a failure, the caller
  // clears its latch and its Stop goes with it, over a run the server may hold
  // for as long as an unbounded budget allows.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }));
    for (let i = 0; i <= 5; i++) fetchMock.mockRejectedValueOnce(new TypeError("gone"));
    fetchMock
      // ...the scene's own run endpoint says it is still going, so the wait
      // resumes rather than reporting a failure.
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 } }))
      .mockResolvedValueOnce(runResponse("landed"))
      .mockResolvedValueOnce(jsonOk({ review: { one_line: "They met." },
                                      generation: "gen1", stale: null }));
    globalThis.fetch = fetchMock;
    const pending = api.absorbScene("run", "s1");
    await vi.advanceTimersByTimeAsync(60000);

    expect((await pending).review.one_line).toBe("They met.");
  } finally {
    vi.useRealTimers();
  }
});

test("a run nothing can find any more still reports the failure", async () => {
  // The counterweight: when the rediscovery fails too, the network really is
  // gone and the original failure stands rather than looping forever.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             attempt_id: null, next_index: 0 },
                                      generation: "gen1" }))
      .mockRejectedValue(new TypeError("gone"));
    globalThis.fetch = fetchMock;
    const failed = api.absorbScene("run", "s1").then(
      () => { throw new Error("resolved"); }, (e: unknown) => e);
    await vi.advanceTimersByTimeAsync(60000);

    expect(await failed).toBeInstanceOf(TypeError);
  } finally {
    vi.useRealTimers();
  }
});

test("a scoped retry whose 202 was lost is adopted, not reported as failed", async () => {
  // Worse here than for an absorb: the run merges into the durable review while
  // the panel keeps the phase it was retrying, so saving commits those stale
  // rows over the completed retry.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))          // the lost 202
      .mockResolvedValueOnce(jsonOk({ run: { id: "r2", state: "running", cls: "review",
                                             kind: "audit", attempt_id: null,
                                             next_index: 0,
                                             review_generation: "gen1" } }))
      .mockResolvedValueOnce(runResponse("landed", {
        result: { mechanics: { status: "ok" }, edits: [] } }));
    globalThis.fetch = fetchMock;
    const pending = api.retryAudit("run", "s1");
    await vi.advanceTimersByTimeAsync(5000);

    expect((await pending).mechanics.status).toBe("ok");
  } finally {
    vi.useRealTimers();
  }
});

test("a retry does not adopt a live absorb as its own run", async () => {
  // They share the `review` class, so `kind` is the only thing that tells one
  // review run from another: a retry adopting an absorb would hand the panel a
  // whole review where it asked for one phase.
  const fetchMock = vi.fn<typeof fetch>()
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                           kind: "absorb", attempt_id: null,
                                           next_index: 0,
                                           review_generation: "gen1" } }));
  globalThis.fetch = fetchMock;

  const failed = await api.retryAudit("run", "s1").then(
    () => { throw new Error("resolved"); }, (e: unknown) => e);

  expect(failed).toBeInstanceOf(TypeError);
});

test("a transient failure discovering a live review is retried, not answered", async () => {
  // The adoption pass asks this first and treats a rejection as unknown rather
  // than as "nothing running" — but a single dropped fetch stranding the mount
  // until the reader changes scenes is a poor answer to a blip.
  vi.useFakeTimers();
  try {
    const fetchMock = vi.fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(jsonOk({ run: { id: "r1", state: "running", cls: "review",
                                             kind: "absorb", attempt_id: null,
                                             next_index: 0,
                                             review_generation: "gen1" } }));
    globalThis.fetch = fetchMock;
    const pending = api.liveReview("run", "s1");
    await vi.advanceTimersByTimeAsync(5000);

    expect((await pending)?.review_generation).toBe("gen1");
  } finally {
    vi.useRealTimers();
  }
});
