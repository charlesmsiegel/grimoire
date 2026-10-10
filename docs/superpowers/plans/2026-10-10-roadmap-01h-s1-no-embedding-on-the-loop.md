# 01h-S1: No embedding on the event loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** nothing calls `embed_sync` on the app's lifespan loop thread. The
greeting opener, the one caller that does today, composes (and records its
prompt) in a worker. A runtime guard in `embed_sync` refuses a call made on
a registered app loop thread as `network`/`on_loop`, so the caller degrades to
keyword rather than freezing every other request, and files one error row
naming the refusal. Delivers 01h-C4a in full.

> **Amended at the review gate:** the registry holds loop OBJECTS, not thread
> idents (`app.state.run_loop`), the row's trace is 16 compact frames, and the
> opener checkpoints after its compose. The task text below is the plan as
> gated; "Review and final gate" at the end records what changed and why.

**Architecture:** a small refcounted registry of app loop thread idents in
`store/inference/embed.py` (`mark_app_loop`, `unmark_app_loop`,
`app_loop_marked`). `runner.install`, which already runs on the lifespan loop
and already takes `threading.get_ident()` as `loop_thread`, marks that ident
and keeps it on `app.state.run_loop_thread`. A new `runner.uninstall(app)`,
called first in `main._lifespan`'s outer `finally`, unmarks it. `embed_sync`
checks `_on_app_loop()` (the current ident is marked **and** an asyncio loop is
running in this thread) after the empty-input return and before the
spent-deadline check and any meter. On refusal it writes one error row through
`store.errors` (module = the task, kind `network`, message `on_loop`, the
caller's stack as the trace), marks the exception recorded, and raises
`EmbeddingsError("network", ..., code=ON_LOOP)`. `routes/greetings._opener_frames`
moves `compose_opener` and `_record_prompt` into one module-level helper,
`_compose_opener_call`, awaited through `run_in_threadpool`.

**Tech Stack:** Python 3.11+, `threading`, `asyncio`, anyio's
`BlockingPortal` (via Starlette's `TestClient.portal`), Starlette's
`run_in_threadpool`, pytest, the shared fakes (`FakeLLM`, `FakeEmbeddings`).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01h-embedding-options-design.md`
§1 ("One caller embeds on the event loop"), §6.1 (C4a), §9 (01h-C4a), §10
(CLAUDE.md, Detached runs), §11 (Loop guard), §13 open question 3, Slices
(01h-S1).

**Open question 3 (loop-guard behaviour):** not marked decided in the spec, so
this plan adopts the spec's recommendation: **degrade with an error row**. The
refusal is an `LLMError` of kind `network`, which every embed caller already
catches and treats as "endpoint unavailable, do not retry" (recall,
`semantic._embed`; search, `semsearch`; art, `art._semantic_scores`), so the
turn proceeds on keyword activation. It is never a hard error that fails a
turn.

## Current state (read against `wt/01h-s1` at `c2509b8`)

- `store/inference/embed.py`: `embed_sync` raises `ValueError` for a
  non-embed task, returns `[]` for empty input, raises
  `EmbeddingsError("network", ..., code=embeddings.NOT_SENT)` for a spent
  deadline (outside any meter), then opens `usage.meter`, `_stamp`s, calls
  `client.embed`, and writes one Debug capture line (`_capture`). `embed` is
  `asyncio.to_thread(embed_sync, ...)`, so it always runs `embed_sync` in a
  worker. There is no thread check.
- `runner.install(app, tg)` runs on the loop and computes
  `loop_thread = threading.get_ident()` for `_PortalEvent`; it does not keep
  it. `main._lifespan` calls `runner.install(app, tg)` inside the
  task-group/portal block and closes clients in its outer `finally`.
- `routes/greetings._opener_frames.frames` is an `async def` generator, pumped
  on the lifespan loop by `runs.start_detached`. It calls
  `store.context.compose_opener(...)` and `_record_prompt(...)` synchronously,
  per speaker. `compose_opener` → `_assemble` → `world_state._world_info` →
  `semantic.recall_scored` (embed) and `art.catalogue` → `art.rank` →
  `_semantic_scores` (embed). The opener's prompt seeds the scan window
  (`assemble.py:356-357`), so recall and art both embed on an opener.
- `store/errors.py`: `record(module, kind, detail, *, campaign, scene, task,
  trace)` writes the ERROR row; `mark_recorded(exc)` stops a later
  `record_exception(exc, ...)` from writing a second one (what `Meter.done`
  does).
- `tests/conftest.py`'s `client` fixture enters the lifespan
  (`with TestClient(app) as c`), so `client.portal` is the lifespan's portal
  and `client.portal.call(f)` runs `f` on the registered loop thread.

## Global Constraints

- **Nothing changes off the loop.** A call from a worker, a CLI, the async
  `embed` door (a `to_thread` worker) or a private `asyncio.run` loop files the
  same row and line it files today. `test_inference_embed.py`,
  `test_context_semantic.py`, `test_context_art.py`, `test_semsearch_store.py`
  and `test_continuity_similarity.py` pass untouched.
- **Order inside `embed_sync`:** task check (`ValueError`) → empty input
  (`[]`) → loop guard → spent deadline (`NOT_SENT`) → meter. So an empty call
  returns `[]` wherever it is made, and a refused call opens no meter, files no
  usage row and writes no capture line.
- **The refusal is a named exception to "a call that sends nothing files
  nothing"**: exactly one ERROR row, written by the guard, and the exception is
  marked recorded so an outer `errors.record_exception` adds none. The row
  carries the task, the kind and the code, the `campaign` and `scene` when the
  call passed them (as every other embed error row does), and a bounded stack
  of the caller as `trace` (`traceback.format_stack(limit=8)`: file names and
  source lines only, no locals, never text, a provider message or a URL).
  Spec §6.1's "task and code only" is read as "no provider text" (plan gate,
  ruling b).
- **Scoped to the app's loop.** Refused only when the current thread ident is
  marked by a live lifespan **and** an asyncio loop is running in the current
  thread. The second half is what makes a stale ident harmless: a worker
  thread that inherits a dead loop thread's ident (Python reuses idents) runs
  no loop, so it is never refused. It is not "is any loop running": an
  unmarked private loop is never refused.
- **Registry lifetime.** Marked in `runner.install`; unmarked in
  `runner.uninstall`, the first statement of `_lifespan`'s outer `finally`, so
  every way out of the lifespan unmarks it before the loop thread can end. The
  registry is a refcount (`dict[int, int]`) under a `threading.Lock`, so two
  apps on one loop (or a re-entered lifespan) cannot unmark each other.
  `uninstall` clears `app.state.run_loop_thread` after unmarking, so a second
  call, or a lifespan whose startup failed before `install`, unmarks nothing.
- **The fix and the guard land together** (spec, Slices): the guard alone
  would degrade every opener's recall and art.
- **Imports at module scope, no cycle.** `runner.py` gains
  `from .store.inference import embed as embed_operation` (it is outside
  `store/`, and `store.inference.embed` imports nothing of `runner`);
  `store/inference/embed.py` gains `errors` in its existing
  `from .. import ...` line, plus `threading` and `traceback`.
  `routes/greetings.py` gains `from starlette.concurrency import
  run_in_threadpool`, as `routes/worlds.py` and `routes/streaming.py` import it.
- **No new ratchet findings, no baseline raised.** The guard is a helper
  (`_refuse_on_loop`), so `embed_sync`'s branch count rises by one at most;
  `frames()` loses two calls and gains one await.
- Placeholder names only in tests (Seraphine, Mara, Realm, Saltmarch,
  Sablewrought as an invented lore key).

## Review Focus

- The guard's position: after `[]` for empty input, before the spent-deadline
  `NOT_SENT` and before `usage.meter` opens. A refused call files no usage row
  and no capture line.
- One error row exactly, and none doubled: `errors.mark_recorded(exc)` after
  `errors.record`, so a caller that records the exception again adds nothing.
- The two-part test (`app_loop_marked(ident)` and a running loop) and that it
  still refuses a sync function `portal.call`ed onto the app loop (spec §6.1)
  while never refusing `run_in_threadpool`, `asyncio.to_thread`, a CLI or an
  unmarked `asyncio.run` loop.
- Unmarking on every lifespan exit path, before any `await` in the `finally`.
- The opener: both `compose_opener` and `_record_prompt` run in the worker, the
  per-speaker loop and its frames are unchanged, and `prior` is the parts list
  as it stood when that speaker was composed.
- A cancel while the worker composes: `run_in_threadpool` is not cancellable,
  so the compose (and any embed in it) completes and files an ordinary row,
  and the cancellation lands at the await (spec §10, Detached runs). No new
  code path handles it; the existing `except BaseException: raise` stands.

---

### Task 1: the registry and the guard in `embed_sync`

**Files:**
- Modify: `backend/src/grimoire/store/inference/embed.py`
  (`ON_LOOP`, `_APP_LOOPS`, `_APP_LOOPS_LOCK`, `mark_app_loop`,
  `unmark_app_loop`, `app_loop_marked`, `_on_app_loop`, `_refuse_on_loop`;
  the registry's docstring says why it is module state rather than
  `app.state`: the spec prescribes it, the guard has no app in hand six frames
  below a route, and the refcount is what keeps per-test apps from unmarking
  each other;
  `embed_sync` calls `_refuse_on_loop(task, campaign=campaign, scene=scene)`; module docstring gains a
  "Never on the app's loop" paragraph)
- Create: `backend/tests/test_embed_off_loop.py`

**Interfaces:**
- Produces:
  - `embed.ON_LOOP: str = "on_loop"`;
  - `embed.mark_app_loop(ident: int) -> None` (increments);
  - `embed.unmark_app_loop(ident: int) -> None` (decrements, deletes at 0,
    a no-op for an unmarked ident);
  - `embed.app_loop_marked(ident: int) -> bool` (registry only);
  - `embed._on_app_loop() -> bool`: `app_loop_marked(threading.get_ident())`
    and `asyncio.get_running_loop()` does not raise `RuntimeError`;
  - `embed._refuse_on_loop(task: str, *, campaign: str, scene: str) -> None`:
    when `_on_app_loop()`, builds `exc = embeddings.EmbeddingsError("network",
    "embedding refused on the event loop", code=ON_LOOP)`, writes
    `errors.record(task, "network", ON_LOOP, campaign=campaign, scene=scene,
    task=task, trace="".join(traceback.format_stack(limit=8)))`, calls
    `errors.mark_recorded(exc)` and raises `exc`.

- [ ] **Step 1: Failing tests** (`test_embed_off_loop.py`; the `_home`,
  `space` and `_client` helpers copied in shape from `test_inference_embed.py`
  -- an `openai_compatible` provider named "Saltmarch Vectors", the role on
  `embed-1`, an `httpx.MockTransport` client recording requests. A local
  helper `_on_marked_loop(fn)` runs `asyncio.run` of a coroutine that marks
  `threading.get_ident()`, calls `fn()`, and unmarks in `finally`.)
  - `test_a_call_on_a_marked_loop_is_refused_before_anything_is_sent`:
    `_on_marked_loop(lambda: embed.embed_sync("semantic-recall", ["x"], ...))`
    raises `EmbeddingsError` with `kind == "network"` and
    `code == embed.ON_LOOP`; no request was seen; `usage.calls(days=1)` is
    empty; with `logs.apply_level("debug")`, no `module == "embed"` capture
    line; exactly one ERROR row, `module == "semantic-recall"`,
    `kind == "network"`, `message == "on_loop"`, `task == "semantic-recall"`,
    the `campaign` and `scene` the call passed, and a `trace` naming this test
    file; the input text appears nowhere in the log file.
  - `test_the_refusal_is_recorded_once`: catching the refusal and calling
    `errors.record_exception(exc, "semantic-recall")` writes no second row.
  - `test_an_empty_input_is_free_even_on_the_loop`: `[]`, no request, no row,
    no error row.
  - `test_the_refusal_comes_before_a_spent_deadline`: with a deadline already
    past, the marked-loop call raises `code == embed.ON_LOOP`, not
    `NOT_SENT`.
  - `test_an_unmarked_private_loop_is_not_refused`: `asyncio.run` of a
    coroutine calling `embed_sync` while a different ident is marked → the
    vector, one usage row, no error row.
  - `test_a_worker_under_a_marked_loop_is_not_refused`: inside the marked
    loop, `await asyncio.to_thread(embed.embed_sync, ...)` and
    `await embed.embed(...)` both answer, two usage rows, no error row.
  - `test_a_marked_ident_with_no_running_loop_is_not_refused` (the CLI, and a
    stale ident inherited by a worker): mark the test thread's own ident, call
    `embed_sync` plainly → answers, one row; unmark in `finally`.
  - `test_the_registry_counts_marks`: mark twice, unmark once →
    `app_loop_marked` true; unmark again → false; unmarking an unmarked ident
    is a no-op.
- [ ] **Step 2: Run** → fail:
  `cd /home/user/wt/01h-s1/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_embed_off_loop.py`
- [ ] **Step 3: Implement** in `embed.py` as above; `embed_sync` calls
  `_refuse_on_loop(task, campaign=campaign, scene=scene)` on the line after `if not texts: return []`. The
  docstring of `embed_sync` gains one sentence ("Raises
  `EmbeddingsError(..., code=ON_LOOP)` on the app's loop thread; see the module
  docstring"), and the module docstring a paragraph stating the rule, the
  two-part test, and the error row as the named exception to "nothing sent,
  nothing filed".
- [ ] **Step 4: Run** → pass, plus
  `tests/test_inference_embed.py tests/test_context_semantic.py tests/test_context_art.py tests/test_semsearch_store.py tests/test_continuity_similarity.py tests/test_import_guard.py tests/test_operation_guard.py tests/test_usage_guard.py`.

### Task 2: register the lifespan loop, unregister on exit

**Files:**
- Modify: `backend/src/grimoire/runner.py` (`install` marks the ident and
  stores `app.state.run_loop_thread`; new `uninstall(app)`; the module
  docstring's list names the registration)
- Modify: `backend/src/grimoire/main.py` (`_lifespan`'s outer `finally` calls
  `runner.uninstall(app)` first, before the client closes)
- Test: `backend/tests/test_embed_off_loop.py`

**Interfaces:**
- Produces: `runner.uninstall(app) -> None`: reads
  `getattr(app.state, "run_loop_thread", None)`; when an int, calls
  `embed_operation.unmark_app_loop(it)` and sets the attribute to `None`.

- [ ] **Step 1: Failing tests** (route level; the conftest `client` fixture,
  which enters the lifespan; the space a hand-built dict
  `{"space": "s", "model": "m", "key": "k", "base_url": "u"}` as the art tests
  use, and `FakeEmbeddings` from `tests.llm_fakes`):
  - `test_the_lifespan_marks_its_loop_thread`:
    `ident = client.portal.call(threading.get_ident)`;
    `embed.app_loop_marked(ident)` and
    `client.app.state.run_loop_thread == ident`.
  - `test_a_sync_call_portal_called_onto_the_app_loop_is_refused`:
    `client.portal.call(functools.partial(embed.embed_sync, "semantic-recall",
    ["x"], space=SPACE, client=fake))` raises `EmbeddingsError` with
    `code == embed.ON_LOOP`; `fake.calls == []`; no usage row.
  - `test_a_worker_reached_from_the_app_loop_is_not_refused`:
    `client.portal.call(run_in_threadpool, functools.partial(embed.embed_sync,
    ...))` answers; one usage row; no error row.
  - `test_a_thread_calling_through_the_portal_is_not_refused`: from the test
    thread (not the loop), while the app's loop is marked, a plain
    `embed_sync` answers; and a function that itself calls
    `client.portal.call(threading.get_ident)` before embedding (a thread
    calling *through* the portal) answers too.
  - `test_the_lifespan_exit_unmarks_its_loop_thread`: its own app over
    `tmp_path` (`monkeypatch.setenv("GRIMOIRE_HOME", ...)`,
    `importlib.reload(store)`, `create_app()`, as the conftest fixture builds
    one); inside `with TestClient(app) as c:` take the ident and see it marked;
    after the block, `not embed.app_loop_marked(ident)` and
    `app.state.run_loop_thread is None`.
  - `test_uninstall_unmarks_once`: no app needed. Mark an ident twice (two
    owners), hand `runner.uninstall` a stub
    (`types.SimpleNamespace(state=types.SimpleNamespace(run_loop_thread=ident))`)
    twice → the ident is still marked once (the second call is a no-op, and
    the other owner's mark survives) and `stub.state.run_loop_thread is None`;
    the test unmarks the remaining mark in `finally`.
- [ ] **Step 2: Run** → fail.
- [ ] **Step 3: Implement.** `install`: after `loop_thread = ...`,
  `app.state.run_loop_thread = loop_thread` FIRST, then
  `embed_operation.mark_app_loop(loop_thread)`, so a mark never exists
  without the attribute that undoes it. `uninstall` as above. In
  `main._lifespan`, the outer `finally` opens with `runner.uninstall(app)` and a
  comment: it runs on the loop thread, on every way out, before the first
  `await` and before the thread can end, which is what keeps a reused ident
  from naming a dead loop.
- [ ] **Step 4: Run** → pass, plus `tests/test_llm_lifecycle.py
  tests/test_draft_runs.py tests/test_runner.py tests/test_runs_detach.py
  tests/test_runs_registry.py tests/test_maintenance_runs.py` (the lifespan's
  other users; `run_loop_thread` is an int, not a closable, so
  `test_llm_lifecycle`'s closable sweep is unaffected).

### Task 3: the opener composes in a worker

**Files:**
- Modify: `backend/src/grimoire/routes/greetings.py`
  (`_compose_opener_call`; `_opener_frames.frames` awaits it through
  `run_in_threadpool`)
- Modify: `backend/tests/llm_fakes.py` (`FakeEmbeddings.threads: list[int]`,
  the `threading.get_ident()` of each `embed` call)
- Test: `backend/tests/test_embed_off_loop.py`

**Interfaces:**
- Produces: `greetings._compose_opener_call(cid, sid, prompt, actor, prior,
  resolved, adapt) -> list[dict]`: `store.context.compose_opener(cid, sid,
  prompt, actor_ref=actor["actor_ref"], prior=prior,
  describe=store.prompt_log.capturing(), model=primary.model, adapt=adapt)`,
  then `_record_prompt(cid, sid, "opener", breakdown, model=primary.model,
  kind=primary.kind, messages=messages, conn=resolved.chain)`, returning the
  messages (`primary = resolved.chain.primary`). In `frames()`:
  `messages = await run_in_threadpool(_compose_opener_call, cid, sid, prompt,
  actor, list(parts), resolved, adapt)`.

- [ ] **Step 1: Failing test** `test_an_opener_with_recall_and_art_embeds_off_the_loop`
  (the conftest `client`; a world "Realm" and a campaign "Saltmarch" made
  through the API, as `test_draft_runs.py`'s fixtures do; a scene "Arrival";
  in the campaign root a lore entry "Sablewrought", body "The sword her
  mother left her.", keyed `sablewrought`; a campaign library image
  `coastline` described "A hand-drawn map of the northern coastline."
  (`campaign_images.put_image`, `image_descriptions.set_in`); an
  `openai_compatible` provider and the Embedding role on it through
  `tests.inference_fixtures.embedding`; `config.write_config(
  semantic_recall_depth="1", semantic_recall_threshold="0.4")`; two
  `FakeEmbeddings` patched into `semantic._CLIENT` and `art._CLIENT`, each
  with `vector_for=lambda t: [1.0, 0.0] if ("blade" in t or "sword" in t)
  else [0.0, 1.0]`; `FakeLLM(turns=[["A quiet road."]])` as `get_llm`; the
  Primary keyed with `PUT /api/llm-connections/openrouter {"api_key": ...}`
  as `test_routes.py` does, or the opener answers 409; no NPC in the scene's
  cast, because art runs only for the Grimoire speaker.
  POST the opener with prompt "She drew the blade her mother left her."):
  - the run lands (`speaker_done` and `done` frames);
  - recall was applied: "The sword her mother left her." is in the first
    generation request's messages (the keyword rule cannot reach it: nobody
    said "sablewrought");
  - both fakes were called (each one's `threads` is non-empty), and the
    loop's ident (`client.portal.call(threading.get_ident)`) is in neither's
    `threads`;
  - no ERROR row has `message == "on_loop"`;
  - the opener's prompt capture is still filed
    (`GET /api/campaigns/{cid}/scenes/{sid}/prompts` has an entry for the
    `opener` task), so `_record_prompt` ran in the worker.
  With Task 1 and 2 in and this task not, the test fails on recall (the
  guard refuses the query embed, the body is absent) and on the `on_loop`
  row, which is the "fix is live" half of the acceptance.
- [ ] **Step 2: Run** → fail (for that reason, checked by reading the
  failure).
- [ ] **Step 3: Implement** as above. The per-speaker loop, its frames, the
  meter around `operations.generate` and both error paths are unchanged.
- [ ] **Step 4: Run** → pass, plus `tests/test_draft_runs.py
  tests/test_draft_suppression.py tests/test_greetings_store.py
  tests/test_authors_note_turns.py tests/test_character_turns.py
  tests/test_prompt_log_routes.py tests/test_context.py tests/test_revision.py
  tests/test_routes.py tests/test_usage_routes.py tests/test_routing_routes.py
  tests/test_tracker_context.py tests/test_model_guidance.py` (every opener
  test; `test_revision.py`'s
  `test_an_opener_that_captured_a_prompt_stamps_for_that` pins that
  `_record_prompt`'s stamp survives the move to the worker).

### Task 4: what the docs say

**Files:**
- Modify: `CLAUDE.md` (the "Adding an embedding call site?" paragraph)
- Modify: `backend/src/grimoire/embeddings.py` (the module docstring's
  "while a recall is in flight the event loop is blocked" sentence, the
  header-drip paragraph's "the async rewrite named above", which would
  dangle, and the `TIMEOUT` comment's "this call blocks the event loop")
- Modify: `backend/src/grimoire/store/context/assemble.py` (`_assemble`'s
  docstring: the opener "is composed inside an async generator on the event
  loop"; it is composed in a worker now, and skipping the tracker read stays,
  because its section is `except_opener`)
- Modify: `backend/src/grimoire/store/context/semantic.py` (the `WARM_LIMIT`
  and `_CLIENT` comments' "event loop blocked"/"blocking the event loop":
  a held worker, not the loop)

- [ ] **Step 1:** In CLAUDE.md, after "A call that sends nothing files nothing
  -- no row, no error, no capture line -- whether its input was empty or its
  deadline lapsed before the first request.", add the named exception: never
  on the app's loop thread -- `runner.install` marks the lifespan loop's
  thread and the lifespan's exit unmarks it, and `embed_sync` called there
  (with that loop running) is refused before any meter as
  `network`/`on_loop`, so the caller degrades as it does for an unreachable
  endpoint, and it writes ONE error row (task, kind, code and the caller's
  stack): a programming error rather than a call, recorded so it can be found.
  A worker, a CLI, the `embed` door and a private loop are never refused. An
  `async def` caller reaches a compose or an embed through
  `run_in_threadpool`, as the opener does.
- [ ] **Step 2:** `embeddings.py`: the client is still synchronous, but it runs
  in a worker thread: every caller is a `def` handler, a
  `run_in_threadpool`, or `embed`'s `to_thread`, and `store.inference.embed`
  refuses the app's loop thread. `TIMEOUT` bounds the worker it holds. The
  header-drip paragraph names the fix directly (an async client whose whole
  call is one deadline, 01h-C4b) instead of "the async rewrite named above".
  `assemble._assemble` and `semantic.py`'s two comments are corrected to say
  a worker is held, not the loop.
- [ ] **Step 3: Run** `tests/test_docs_guard.py` → pass.

### Task 5: gates and commit

- [ ] **Step 1:** the targeted runs of Tasks 1-4 together, then the guards:
  `tests/test_import_guard.py tests/test_operation_guard.py
  tests/test_usage_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py
  tests/test_lock_domain_guard.py tests/test_docs_guard.py`.
- [ ] **Step 2:** ratchets and templates from the worktree root:
  `make check-lint check-mypy check-templates PY=/home/user/grimoire/backend/.venv/bin/python`
  → at baseline, no baseline file changed.
- [ ] **Step 3:** the whole backend once:
  `make check-py PY=/home/user/grimoire/backend/.venv/bin/python` → pass.
- [ ] **Step 4: Commit** `01h-S1: no embedding on the event loop`, body naming
  the opener fix, the guard and the CLAUDE.md exception, with the two
  attribution lines from the slice brief.

## Acceptance traced (spec §11, Loop guard; Slices, 01h-S1)

| Acceptance item | Test |
|---|---|
| A thread calling through a portal is not refused | `test_a_thread_calling_through_the_portal_is_not_refused` |
| `on_loop` on a registered thread, no usage row, one error row | `test_a_call_on_a_marked_loop_is_refused_before_anything_is_sent`, `test_a_sync_call_portal_called_onto_the_app_loop_is_refused`, `test_the_refusal_is_recorded_once` |
| Fine from a worker | `test_a_worker_under_a_marked_loop_is_not_refused`, `test_a_worker_reached_from_the_app_loop_is_not_refused` |
| Fine from a CLI | `test_a_marked_ident_with_no_running_loop_is_not_refused` (and every existing `test_inference_embed.py` case) |
| Fine from a private `asyncio.run` loop | `test_an_unmarked_private_loop_is_not_refused` |
| `[]` for empty input on the loop | `test_an_empty_input_is_free_even_on_the_loop` |
| An opener with recall and art completes with recall applied and no `on_loop` row | `test_an_opener_with_recall_and_art_embeds_off_the_loop` |
| `runner.install` registers, the lifespan exit unregisters | `test_the_lifespan_marks_its_loop_thread`, `test_the_lifespan_exit_unmarks_its_loop_thread`, `test_uninstall_unmarks_once` |
| CLAUDE.md names the exception | Task 4 |

## Choices beyond the spec's letter (for the plan gate)

- **The running-loop half of the test.** The spec says "refuses only when
  `threading.get_ident()` is a registered app loop thread". Adding "and an
  asyncio loop is running in this thread" refuses nothing the spec refuses
  (the app loop thread runs loop callbacks only while it serves) and makes an
  ident left marked by a lifespan that never exited (or reused after one did)
  harmless.
- **A stack trace, and the campaign and scene, on the error row.** See the
  plan gate's ruling (b) below.
- **The guard precedes the spent-deadline check.** The spec fixes only "after
  the empty-input return, before any meter". A loop caller with a spent
  deadline is still the programming error worth recording.
- **`runner.uninstall`** is a new name; the spec says only "the lifespan's exit
  unregisters it".

## Plan gate (substitute review, 2026-10-10)

No Codex CLI in this environment: an independent reviewer agent ran the
adversarial plan review against the spec and the code, and the coordinator
ruled. Verdict: pass with should-fixes. Each item, and how it was folded:

- **(a) Running-loop half of the guard:** accepted as written.
- **(b) The error row.** The draft was inconsistent (it carried a trace but
  dropped campaign and scene). Ruling: carry `campaign` and `scene` when the
  call passed them, as every other embed error row does and as CLAUDE.md's
  "those ids make a failure findable" asks, and keep the trace bounded
  (`traceback.format_stack(limit=8)`, frames only: no locals, no provider
  text, like `errors._frames`). This is a deliberate reading of spec §6.1's
  "task and code only" as "no provider text". Folded into Global Constraints,
  `_refuse_on_loop`'s signature, and the pinned test, which now asserts the
  campaign and scene are present and the input text is absent from the log.
- **(c) Guard before the deadline check:** accepted.
- **(d) `runner.uninstall`:** accepted. NIT folded: `install` sets
  `app.state.run_loop_thread` before `mark_app_loop`, so a mark never exists
  without the attribute that undoes it.
- **(e) `available_art` by default:** confirmed: `layout.apply` returns the
  whole catalog unless `prompt_layout_enabled == "on"` (off by default). Art
  runs only for the Grimoire speaker, so the opener test's scene has no NPC.
- **1 [SHOULD] Comments this slice makes false:** `assemble._assemble`'s
  docstring, `semantic.py`'s `WARM_LIMIT` and `_CLIENT` comments, and the
  `embeddings.py` header-drip paragraph's "async rewrite named above"
  reference. Folded into Task 4.
- **2 [SHOULD] Task 3's run missed opener tests:** `test_revision.py`,
  `test_routes.py`, `test_usage_routes.py`, `test_routing_routes.py`,
  `test_tracker_context.py` and `test_model_guidance.py` added to Task 3
  Step 4.
- **3 [NIT] The opener test keys the Primary** (`PUT
  /api/llm-connections/openrouter`) and asserts each fake's `threads` is
  non-empty. Folded into Task 3.
- **4 [NIT] A thread calling through a portal:** a test of its own added
  to Task 2 (`test_a_thread_calling_through_the_portal_is_not_refused`).
- **5 [NIT] Why the registry is module state:** stated in its docstring
  (Task 1 Files).

Found while implementing: the suite's first name, `test_embed_loop_guard.py`,
matched `test_docs_guard.test_contributing_names_every_guard_test`'s
`test_*guard*.py` pattern, which is reserved for the AST guards in
CONTRIBUTING's table. A runtime-behaviour suite is not one of them, so it is
`test_embed_off_loop.py` instead.

## Review and final gate (substitute review, 2026-10-10)

No Codex CLI in this environment: an independent reviewer agent stood in for
`/codex:review` against the diff and for the final spec gate (the diff against
spec 01h §6.1, §9 C4a, §11 Loop guard and the 01h-S1 slice entry), and the
coordinator ruled. Verdict: PASS with should-fixes; no correctness bug found.
The spec is a dated record and is not edited. Each finding, and how it was
folded:

- **SHOULD 1: the trace missed the caller on the recall path.**
  `traceback.format_stack(limit=8)` ended at `assemble._assemble`, so a row
  could not tell the opener from a chat turn. The row now keeps the caller's
  innermost `ON_LOOP_FRAMES = 16` frames, each a compact
  `dir/file.py:line in function` line with no source text or locals
  (`embed._caller_frames`). That keeps the row far inside `logs.MAX_TRACE`, so
  `logs._clip`, which keeps a trace's tail, never cuts off the caller's end.
  Pinned by `test_a_compose_on_the_loop_names_its_route_in_the_row`: a
  recall-path compose `portal.call`ed onto the app loop files a row whose trace
  names `routes/greetings.py` and `_compose_opener_call`. That test fails at
  the old limit of 8, which was checked.
- **SHOULD 2: comments this slice made false.** `store/locks.py`'s
  `store.authors_notes` entry and `store/authors_notes.py`'s "Reads are
  lenient and lock-free" both said the opener composes on the event loop.
  Both now say a worker.
- **NIT 3: mark the loop object.** Done. `_APP_LOOPS` is
  `dict[AbstractEventLoop, int]`. `runner.install` stores
  `asyncio.get_running_loop()` on `app.state.run_loop` before marking it, and
  `uninstall` unmarks that object. `_on_app_loop` is now
  `asyncio.get_running_loop()` (none is never refused) and `in` the registry.
  Thread-ident reuse is out of the argument entirely: a loop object is never
  reused. The two-part test and its stale-ident docstring are gone, and the
  "stale ident" test is replaced by
  `test_a_cli_call_is_not_refused_while_an_app_loop_is_marked`.
- **NIT 4: caller-level degrade tests.** Two were added.
  - `test_recall_on_the_loop_falls_back_to_keyword_and_does_not_retry`:
    `semantic.recall_scored` on a marked loop returns `[]`, the client is
    never reached, and there is exactly one `on_loop` row (a query-only retry
    would have filed a second).
  - `test_art_on_the_loop_keeps_the_keyword_ranking`: `art._semantic_scores`
    returns `None`, so the caller keeps its keyword scores, with one row.
- **NIT 5: the Stop-during-compose test**
  (`test_a_stop_during_the_compose_lets_its_embed_finish_and_file_an_ordinary_row`).
  A `FakeEmbeddings` blocks the compose's recall on an event, then
  `runner.cancel` runs, then the event is released. The test found a real
  gap. `run_in_threadpool` shields its wait, so the Stop was delivered only
  at the next suspending await. With a fake that never suspends, the run
  landed. With a real provider, the Stop would land inside the generation,
  after `speaker_start` and possibly after the request went out. The opener
  now runs `await anyio.lowlevel.checkpoint()` right after the compose,
  which is `runner._guarded`'s own argument for its checkpoint. The test pins
  that the run ends `cancelled`, there is no `speaker_start` frame, no
  generation is sent, and exactly one ordinary (`ok`, not `aborted`)
  `semantic-recall` row is filed. Without the checkpoint it fails (`landed`).
- **NIT 6: spec drift, recorded here rather than in the spec.** The
  deliberate readings of §6.1 this slice ships:
  - **The error row carries `campaign` and `scene`** when the call passed
    them, plus a bounded trace of the caller's frames. "Task and code only"
    is read as "no provider text" (plan-gate ruling b): every other embed
    error row carries those ids, and CLAUDE.md says they make a failure
    findable.
  - **The registry is a refcount of loop objects**, not "a set" of thread ids.
    The refcount keeps two apps on one loop, or a re-entered lifespan, from
    unmarking each other. The loop object keeps a reused ident from naming a
    dead loop.
  - **The check is "the running loop is a marked one"**, which refuses exactly
    the app loop's own callbacks, a sync function `portal.call`ed onto it
    included. A private `asyncio.run` loop, a worker, a CLI and a thread
    calling through a portal are never refused.
  - **The guard runs before the spent-deadline check** (plan-gate ruling c),
    and the opener checkpoints after its compose (NIT 5 above).

Re-run after folding:
- `tests/test_embed_off_loop.py`: 19 passed.
- The targeted set (embed, semantic, art, search, similarity, the run and
  lifespan suites and every opener test) and the AST and docs guards:
  2338 passed.
- `make check-lint check-mypy check-templates`: at baseline.
