# 01h-S5: Native async embed — Plan

**Goal:** `store.inference.embed.embed` stops being `to_thread(embed_sync)`
and becomes a native async door over a new `embeddings.AsyncEmbeddingsClient`:
one total deadline over the whole call (closing the header-drip hole the sync
client documents), a real cancel that files `aborted`, and the same checks,
row, failure record and capture line as `embed_sync`. One client per app,
built in `create_app`, closed by the lifespan, reached through
`routes.get_embeddings`. Both doors take `post=` and `run_id=`. Delivers
01h-C4b in full.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01h-embedding-options-design.md`
"Required by" (the consumer-facing API), §6.2, §9 (C4b), §10 (guards,
CLAUDE.md), §11 (Async door), Slices 01h-S5.

**Review gates:** not run (speed mode); the Codex gates are owed.

**Open questions (§13):** Q4 (native async client now, or keep `to_thread`):
the recommendation, native now, so 09's plan does not carry a transport
rewrite. 01g-S3 has landed, so `run_id` is filed on the row through
`usage.meter(run_id=)`; the capture line carries it too (passed explicitly,
since the line is written outside the meter).

## Design

- **`embeddings`.** The response handling both clients share moves into
  module functions: `_check_call` (the pre-send refusals), `_requests` (the
  `param` split and `BATCH`, as `(inputs, is_query)`), `_bounded` (the
  `MAX_BYTES` check), `_answer` (redirect, error status, JSON), `_verify`,
  `_headers`. The sync client keeps its own transport loop on them.
  `AsyncEmbeddingsClient.embed` runs every request under one
  `asyncio.timeout(deadline - now)`; its expiry is the sync client's error
  (`network`, `NOT_SENT` before any request, else `DEADLINE`). `_post`
  catches a `BaseException` (a cancel, or the deadline's) only to drop the
  sums of a batch in flight (`_Spend.lose`) and re-raise. httpx's own
  per-operation timeout gets a second of slack so the call's deadline is
  what fires. `aclose()` closes an owned pool and lets the next call reopen.
  No lock: one per app, touched only from its loop.
- **`store.inference.embed`.** Shared helpers both doors call: `_admit`
  (task, then `_sendable`), `_not_sent_if_spent`, `_meter` (with `post`,
  `run_id`), `_stamp`, `record_failure`, `estimate_prompt`, `_capture` (now
  with `run_id`). `embed_sync` refuses a client whose `embed` is a coroutine
  function, `embed` one whose `embed` is not (`TypeError`), so a sync client
  is never awaited and an async one never blocks. `embed` is never refused
  on the loop (it is the loop's door). A `CancelledError` around the client
  call marks the capture line `aborted`; `Meter.__exit__` already files a
  cancel `aborted` and writes no error row. The local prompt estimate runs
  in `asyncio.to_thread`.
- **The app.** `routes.common.build_embeddings` / `get_embeddings`, exported
  from `routes`; `create_app` sets `app.state.embeddings`, and the
  lifespan's close loop includes it (`test_llm_lifecycle`'s "every closable
  is closed" holds it).
- **Guards.** `test_usage_guard.py`'s embeddings check already matches any
  `.embed(` call; planted cases add an awaited async call with and without a
  holder. `test_operation_guard.py` already counts `embed` as an operation
  call; a planted case covers the module's own awaited client call, and
  `MIN_EMBED_CALLS` is unchanged (no production caller: 09 and 10).
- `CLAUDE.md`'s embedding paragraph names both doors and their clients.

## Files

- `backend/src/grimoire/embeddings.py`, `backend/src/grimoire/store/inference/embed.py`
- `backend/src/grimoire/routes/common.py`, `backend/src/grimoire/routes/__init__.py`,
  `backend/src/grimoire/main.py`
- `CLAUDE.md`
- Tests: new `test_embed_async.py`; `test_inference_embed.py`,
  `test_embed_off_loop.py` (the async door takes the async client),
  `test_usage_guard.py`, `test_operation_guard.py`.

## Tests

- Parity: plain, unreported counts (estimated), campaign/scene/post/run_id,
  prefix, `param`, a 400, a 503, a width mismatch -- the same rows, lines,
  results and request bodies from both doors.
- `post` and `run_id` on the row and the line; none of either is today's row.
- The wrong kind of client is a `TypeError` before anything is sent.
- A server that never finishes its headers is cut at the deadline
  (`DEADLINE`, well under the endpoint's delay, no sums kept).
- A budgeted deadline cut through the door is `aborted`, with no error row.
- A cancel mid-request: one `aborted` row, no counts, no error row, the
  line `error: "aborted"`.
- One client per app through `routes.get_embeddings`, its pool closed at
  lifespan exit.
