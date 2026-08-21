# Detached Runs — Phase 1: the run core and scene turns

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a scene turn survive the phone locking — the generation keeps
running server-side, the client re-attaches when it comes back, and Android
posts a notification when it lands.

**Architecture:** A per-app run registry owns LLM jobs independently of the
requests that started them. Runs are started inside the lifespan `anyio` task
group (bridged from synchronous handlers), each behind its own failure
boundary. Streaming runs tee absolute-indexed SSE frames into an append-only
buffer, so a foregrounded client tails live exactly as today and a returning
one replays from its own consumed index. On Android, a foreground service
keeps the process alive while any run is live.

**Tech Stack:** FastAPI + anyio + uvicorn (backend), React + vitest
(frontend), Kotlin + Chaquopy (Android shell).

**Spec:** `docs/superpowers/specs/2026-08-20-detached-turn-runs-design.md`

## Phasing — read this before starting

The spec covers all 25 LLM routes. That is too much for one plan that produces
working software, so it is split into three. **This plan is Phase 1** and is
complete and shippable on its own:

- **Phase 1 (this plan)** — the run core, the five scene-turn routes, the
  frontend run registry, the Android foreground service and notification.
  Delivers the actual reported problem: a locked phone no longer loses a turn.
- **Phase 2** — the absorb family: `store/pending_reviews.py`, durable review
  results, the 202/poll contract, retry merges, the transcript watermark.
- **Phase 3** — the remaining `draft` and `background` routes (19 of them) and
  the shared start/poll/unwrap client helper.

Phase 1 deliberately leaves `post_absorb`, `post_audit`, `post_dossiers`,
`post_rolling_summary`, `post_scene_break` and every `draft` route **exactly as
they are today**. They keep working unchanged; they simply do not yet get
detachment. Do not partially migrate them here.

## Global Constraints

Copied verbatim from `CLAUDE.md` and the spec. Every task's requirements
implicitly include this section.

- **Run the gate with `make check`.** Individually: `make check-py`,
  `check-web`, `check-lint`, `check-mypy`, `check-eslint`, `check-templates`,
  `check-pydantic1`.
- **Lint gates are ratcheted.** Resolving a finding makes the recorded count
  stale, so run `make baseline` and commit the smaller `lint-baselines/*.json`
  with the fix.
- **Imports in `backend/src/grimoire/` are all at module scope and the module
  graph is acyclic** (`test_import_guard.py`). Inside `store/`, a cross-package
  import binds a *submodule*: `from ..campaigns import read` then
  `read.world_refs()`.
- **`routes/runs.py` must not import `routes.streaming` or `routes.scenes`** —
  they import it. This is the edge that would close a cycle.
- **pydantic stays v1/v2-agnostic**: plain `BaseModel` fields only, dump via
  `routes.common._dump`. No `Field`, validators, or `ConfigDict`.
- **Every store write goes through `store.atomic`** (`test_atomic_guard.py`).
- **Filesystem access goes through the resolvers** (`test_paths_guard.py`).
- **One campaign lock per run, ever.** No `ExitStack`, no lock carried around a
  loop (`test_lock_order_guard.py`).
- **Fake the LLM with `backend/tests/llm_fakes.py`**, injected at
  `app.dependency_overrides[routes.get_llm]`. Never a new inline fake.
- **Run vitest from `frontend/`**, not via `npx --prefix`.
- **In a frontend test, `await` means the page has SETTLED.** See
  `src/test-setup.ts` and `settle.test.tsx`.
- **Shared frontend test scaffolding goes in `frontend/src/testkit/`**, which
  the coverage config excludes.
- **Never use a real world/campaign/character name** in a test fixture or
  commit message. Reuse the codebase's placeholders: Seraphine, Mara,
  Winifred, Realm, Saltmarch.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `backend/src/grimoire/routes/runs.py` | The `Run` record, the `RunRegistry` type, subject/class definitions, and the run routes. Imports from `store` only — never from `streaming` or `scenes`. |
| `backend/src/grimoire/runner.py` | Starting a run: the thread-safe bridge onto the lifespan loop, the per-run failure boundary, the reaper. Separate from `runs.py` so the registry stays a data structure with no scheduling concerns. |
| `backend/tests/test_runs_registry.py` | Registry unit tests: subject/exclusion indexes, attempt idempotency, reaping. |
| `backend/tests/test_runs_routes.py` | The four run routes, including replay-from-offset and cancel. |
| `backend/tests/test_runs_detach.py` | The behavioral heart: a dropped subscriber does not cancel a run. |
| `frontend/src/runs/RunRegistryProvider.tsx` | React context above the router holding in-flight runs by id, with the consumed-frame cursor per run. |
| `frontend/src/runs/useSceneRun.ts` | The hook a scene view uses to start, attach and cancel. |
| `frontend/src/runs/RunRegistryProvider.test.tsx` | Provider tests: survives navigation, no cross-scene render. |
| `frontend/src/testkit/runMocks.tsx` | Shared `vi.mock` factories for the run API. |
| `android/app/src/main/java/app/grimoire/RunNotifier.kt` | Notification channels, posting, suppression while foregrounded, tap intent. |

**Modified:**

| File | Change |
|---|---|
| `backend/src/grimoire/main.py:157` | Stash the lifespan task group and a `RunRegistry` on `app.state`; start the reaper. |
| `backend/src/grimoire/routes/streaming.py` | `_fence_stream` returns an explicit terminal outcome; `_chat_stream`'s hooks move to the runner unchanged. |
| `backend/src/grimoire/routes/scenes.py` | `post_chat`, `post_retry`, `post_regenerate`, `post_replay_turn` start runs; reserve before the first mutator. |
| `backend/src/grimoire/routes/mechanics.py:110` | `post_roll_proposal` likewise, for `_continuation_stream`. |
| `backend/src/grimoire/store/scenes/` | Scene identity field + backfill. |
| `backend/src/grimoire/store/migrations.py` | The identity backfill pass. |
| `frontend/src/api/client.ts:186` | `streamPost` retains the error body so a 409 carries `run_id`. |
| `frontend/src/routes/CampaignView.tsx` | Composer disabled on exclusion key; run adopted from the provider. |
| `android/.../MainActivity.kt`, `ServerService.kt`, `AndroidManifest.xml` | Foreground service, permissions, tap routing. |

---

## A note on test helpers

Several tests below call small local helpers -- `_strip_identity_from_disk`,
`_read_stream`, `_finish_run`, `_wait_terminal`, `_scene_bytes`,
`_hold_a_run`, `_release`. **Each is written in the test
module that uses it**, in the same commit; none is shared machinery:

- `_strip_identity_from_disk(cid, sid)` -- rewrite the scene file's frontmatter
  without the `identity` key, simulating a record written before this feature.
- `_read_stream(client, run, frm)` -- drive the SSE route, return decoded events.
- `_finish_run(cid, sid, attempt)` -- start a run with that attempt id and mark
  it terminal without going through a route.
- `_wait_terminal(app, run_id, timeout=5)` -- poll the registry until the run
  leaves `running`; fail on timeout rather than hang.
- `_scene_bytes(cid, sid)` -- the raw scene file, for byte-identical assertions.
- `_sse(payload)` -- encode one SSE data frame, `f"data: {json.dumps(payload)}\n\n"`.
  The tests build frames with it because `append_frame` takes the wire format;
  do not import `streaming._sse`, which would couple a run test to the module
  Task 2 is forbidden to depend on.
- `_hold_a_run(cid, sid, cls="turn")` / `_release(cid, sid)` -- take and drop an
  exclusion key directly.

**One fixture is the exception and does go in `conftest.py`: `live_server`.**
Task 3's disconnect test and Task 5's cancel tests both need a real socket,
which `TestClient` cannot give them (the reasoning is at that test). It starts
uvicorn on an ephemeral port once per module against the same app and
`GRIMOIRE_HOME`, and exposes `.url`, `.app`, `.campaign_scene`, `.two_scenes`,
`.same_sid_in_two_campaigns` — two campaigns each holding a first scene, so
both carry the id `0001--…`, which is the point — and
`.hold_provider(scene=None, campaign=None, reply=None)`, the last wrapping the
`llm_fakes` provider so it blocks after its first delta until the test releases
it, optionally scripting a distinct reply per scene *or per campaign* so two
concurrent runs are distinguishable. Two tasks needing it is what makes it shared rather than
local; nothing else here is.

**Anything asserting that two things happen AT ONCE belongs here, not on
`client`.** `TestClient` buffers a streaming response to completion, so two
sequential calls through it never overlap no matter how the test is worded —
the first run is terminal before the second is reserved. That is what made the
disconnect test vacuous, and it is the same reason the cross-scene isolation
test uses this fixture: concurrency that the harness cannot express is
concurrency the test cannot check.

**There is no `app` fixture.** `conftest.client` returns `TestClient(app)` and
nothing else, so tests reach the registry through **`client.app.state.runs`**.
An early draft of this plan declared `app` as a test parameter, which pytest
rejects at collection with "fixture 'app' not found" before a single assertion
runs.

**And that bare `TestClient` never runs the lifespan.** `TestClient(app)`
without a `with` block does not emit startup, which this tree already knows and
writes down: `test_frozen_campaign.py:264` runs the migration by hand for
exactly this reason, and `create_app` builds the gateway clients outside
`_lifespan` with the comment "so the dependency resolves for a `TestClient`
that never runs one" (`main.py:270`). So if `app.state.runs` were created only
inside `_lifespan`, every test in this plan that touches `client.app.state.runs`
would die on `AttributeError` before reaching its assertion — and so would
every migrated route, since the handler reserves against a registry that is
not there.

**Split the install, following the precedent the gateway clients already set.**
The registry is a dict and a lock — no async resources, nothing to close — so
it is constructed in `create_app()` and is present for any client, lifespan or
not. Only the parts that need a running loop (the portal, the task group, the
reaper) are installed in `_lifespan`. Task 3's interface list says this
explicitly.

Which then splits the tests in two, and the split is not cosmetic:

- Tests that only *inspect* the registry — reserve, read state, assert an
  exclusion key is held, assert a 409 — take the ordinary `client`.
- Tests where a run must actually **execute** take a lifespan-entered client,
  `run_client`, a new `conftest` fixture that is `client`'s body ending in
  `with TestClient(app) as c: yield c`.

And `runner.start` raises a clear `RuntimeError("no run portal; the app's
lifespan is not running")` when the portal is absent, rather than
`AttributeError` on `app.state`. A test that picks the wrong fixture should say
so in one line, not send its author into `main.py`.

**The rule for classifying a test:** does anything here call a route that
*produces* a run? If yes, `run_client`. If the test only reserves through
`_hold_a_run` or `_finish_run` and then checks what some other route does about
it, plain `client`. By that rule Task 5's whole freeze suite stays on `client`
— every one of those tests holds a key directly and never lets a runner start —
while Task 3's three end-to-end turn tests take `run_client`. When in doubt,
`run_client` is never wrong, only slower.

That same fixture builds a `TestClient`, which is why Task 5's disconnect test
needs its own live-server harness rather than this one -- see the note there.

**Use the existing `backend/tests/conftest.py` fixtures rather than building a
store by hand.** `client` gives an app over a throwaway store with the gateway
faked; `cid_with_sheet` and `scene_with_sheeted_cast` show the real creation
pattern, which is `wid = worlds.create_world("Realm")` then
`campaigns.create_campaign("Saltmarch", wid)`. Note that `create_campaign` takes
`world_id` as a **required positional** and returns the **cid string**, not a
dict -- an early draft of this plan called it `create_campaign(name,
world=None)["id"]`, which fails twice over.

Frontend helpers go in `frontend/src/testkit/runMocks.tsx` (Task 6), which the
coverage config excludes -- not in the suite files, per `CLAUDE.md`.

## Task 1: The scene identity field and its backfill

Everything downstream compares this, so it lands first. A run captures it at
start and refuses to publish if it changed — which is what stops an old run
writing onto a *different* scene that recycled its `sid`
(`serialize.py:_numbering` derives the next number from files on disk with no
stored counter, so deleting the highest scene frees its number).

**Files:**
- Modify: `backend/src/grimoire/store/scenes/serialize.py` (frontmatter round-trip)
- Modify: `backend/src/grimoire/store/scenes/lifecycle.py` (`_create_scene`)
- Modify: `backend/src/grimoire/store/migrations.py` (backfill pass)
- Modify: `backend/src/grimoire/main.py:145` (register the pass)
- Test: `backend/tests/test_scene_identity.py`
- Regenerate: `backend/tests/fixtures/frozen_campaign/snapshot.json`

**Interfaces:**
- Produces: `store.scenes.scene_identity(cid, sid) -> str` — an opaque 32-hex
  value, stable for the life of a scene record.

  **Deliberately NOT exposed through `read_scene`.** That function returns
  `{"meta": {"id": sid, **frontmatter}, "messages": [...]}` (`read.py:63`), so
  a new frontmatter key would surface at `read_scene(...)["meta"]["identity"]`
  and land in the frozen-campaign snapshot — which snapshots the whole payload
  (`sweep.py:188`). A freshly minted `uuid4()` per scene would then make
  `snapshot.json` **different on every regeneration**, destroying the fixture's
  whole purpose. Identity is an internal correctness token, not scene content,
  so `read_scene` filters it out of `meta` and a dedicated accessor reads it.
- Produces: `migrations.backfill_scene_identities() -> None` — idempotent,
  per-campaign, under `campaign_lock`.
- Produces: `store.scenes.find_by_identity(cid, identity) -> str | None` — the
  **reverse** lookup: current `sid` for an identity, or `None` if the scene is
  gone. The notification tap needs this and nothing else provides it. Taps
  carry the identity precisely because a `sid` goes stale on rename, so without
  an inverse the intent can only keep the stale id or fall back to the campaign
  unnecessarily. Exercise both the renamed and the deleted case.

  **The store function alone is not enough** — Kotlin cannot call it. Task 4
  adds `GET /api/campaigns/{cid}/scenes/by-identity/{identity}` returning the
  current `sid` or 404, and Task 7 adds the `android_entry.py` resolver plus
  the `ServerRuntime` method `MainActivity` invokes when handling the intent.
  Without that concrete path the reverse lookup exists and nothing can reach
  it, so taps after a rename still open a stale route.
- Produces: `store.scenes.ensure_identity(cid, sid) -> str` — the lazy path,
  under `campaign_lock`; assigns one if absent and returns it either way.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_scene_identity.py
def test_created_scene_has_a_stable_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    first = store.scenes.scene_identity(cid, sid)
    assert first and len(first) == 32
    # Stable across reads and across an unrelated mutation.
    store.scenes.append_message(cid, sid, "user", "hello")
    assert store.scenes.scene_identity(cid, sid) == first


def test_identity_is_not_in_the_read_scene_payload(tmp_path, monkeypatch):
    # It would land in the frozen snapshot, which snapshots the whole payload
    # -- and a fresh uuid4 per scene would move snapshot.json on every
    # regeneration.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    assert "identity" not in store.scenes.read_scene(cid, sid)["meta"]


def test_recycled_sid_gets_a_different_identity(tmp_path, monkeypatch):
    """The whole point: `_numbering` frees the top number on delete, so a
    recreate can land on the same sid. Identity must still differ."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    old = store.scenes.scene_identity(cid, sid)
    store.scenes.delete_scene(cid, sid)
    again = store.scenes.create_scene(cid, "Mara")
    assert again == sid, "precondition: the sid really is recycled"
    assert store.scenes.scene_identity(cid, again) != old


def test_backfill_is_idempotent_and_assigns_to_legacy_scenes(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    _strip_identity_from_disk(cid, sid)          # simulate a pre-feature scene
    assert store.scenes.scene_identity(cid, sid) is None
    migrations.backfill_scene_identities()
    got = store.scenes.scene_identity(cid, sid)
    assert got
    migrations.backfill_scene_identities()
    assert store.scenes.scene_identity(cid, sid) == got


def test_a_run_lazily_backfills_a_campaign_the_migration_skipped(client, tmp_path, monkeypatch):
    # The startup pass skips a campaign that was locked ("skipping beats
    # failing to boot"). Without a lazy path those scenes stay identity-less
    # until the next restart, and two missing values compare EQUAL -- which is
    # the corruption the identity exists to prevent.
    cid, sid = _campaign_whose_migration_was_skipped(tmp_path, monkeypatch)
    assert store.scenes.scene_identity(cid, sid) is None
    store.scenes.ensure_identity(cid, sid)          # the lazy path, directly
    assert store.scenes.scene_identity(cid, sid)
```

**Task 1 tests `ensure_identity` directly, and cannot do otherwise.** There is
no registry until Task 2, nothing installs it until Task 3, and no route
produces a run until Task 5 — so a `_start_a_run` helper here would have
nothing to call, and Task 1's own "run tests, expect PASS" checkpoint could
never be met. The assertion that actually starting a run triggers the lazy
backfill belongs in **Task 5**, where a run can exist; it is listed there.
Keeping it here would mean either a task that cannot go green or a helper that
fakes the very integration it claims to prove.

**Task 5 must actually carry it, and an earlier revision of this plan claimed
it did without adding it.** The assertion is not decoration: startup skips a
campaign whose lock is contended, so a legacy campaign can still be
identity-less when its first run starts. If reservation does not call
`ensure_identity`, that run captures nothing, and the identity fence -- the
whole defence against a recycled `sid` publishing onto a replacement scene --
degrades silently to comparing `None` with `None`, which always matches. An
implementation that adds `ensure_identity` and never calls it passes every
other test in this plan.

So Task 5's suite includes: strip a scene's identity from disk, POST a real
turn, then assert both that the scene has one on disk **and** that the run
record captured that same value. Asserting only the first passes against a
backfill that ran somewhere harmless while the run captured `None`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_scene_identity.py -v`
Expected: FAIL with `KeyError: 'identity'`

- [ ] **Step 3: Add the field at creation**

In `lifecycle.py:_create_scene`, mint `uuid.uuid4().hex` and write it into the
scene's frontmatter alongside the existing keys. In `serialize.py`, carry
`identity` through the frontmatter round-trip so it survives every
read-modify-write. Do **not** derive it from number, title or date — all three
are mutable by design (rename, `repad`, the front door's date slug).

- [ ] **Step 4: Add the backfill**

```python
# backend/src/grimoire/store/migrations.py
def backfill_scene_identities() -> None:
    """Give every pre-feature scene an identity. Idempotent: a scene that has
    one is skipped, so re-running costs a read per scene.

    Assigning only at creation would be worse than nothing -- an old scene and
    a replacement that recycled its sid would both present the same absent
    value, so the identity check would pass and the corruption it exists to
    prevent would be untouched, while reading as solved.
    """
    log = logging.getLogger(__name__)
    for c in campaigns_read.list_campaigns():
        # Per campaign, not per pass. The startup hook's own handler catches
        # StoreBusy around the whole step, so letting it out of this loop
        # abandons every campaign after the contended one -- they would wait
        # for another startup or a scene-specific lazy repair, while the log
        # said one campaign was skipped. Contention on one campaign says
        # nothing about the next.
        try:
            _backfill_campaign(c["id"])
        except locks.StoreBusy as exc:
            log.warning("identity backfill skipped for %s -- %s; it will be "
                        "retried", c["id"], exc)
```

`_backfill_campaign` takes `locks.campaign_lock(cid)` for the whole pass, the
way `_migrate_campaign` does, and writes through `store.atomic`.

Test it with two campaigns where the first is held: the second must come back
with identities, which fails against a loop that lets the exception out.

- [ ] **Step 5: Register it in the startup hook**

In `main.py`, add `migrations.backfill_scene_identities` to the existing
`for step in (...)` tuple. It inherits that loop's `StoreBusy` handling —
"skipping beats failing to boot" — and the lazy path in Task 4 covers whatever
a busy campaign skipped.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_scene_identity.py -v`
Expected: PASS

- [ ] **Step 7: Confirm the frozen-campaign snapshot does NOT move**

Because identity is filtered out of `read_scene`'s `meta`, the snapshotted
payload is unchanged. Verify rather than assume:

```bash
cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py -v
git diff --exit-code backend/tests/fixtures/frozen_campaign/snapshot.json
```

Expected: PASS, and an empty diff. **If the snapshot moves, stop** — it means
identity leaked into the payload, and regenerating would bake a random UUID
into the fixture that the next regeneration would change again. Fix the filter
instead. Never regenerate `home/`.

Note for anyone tempted to add the backfill to the sweep: `_write_snapshot`
runs only `store.migrations.migrate_scene_ids()` (`sweep.py:208`), not the
startup tuple, so registering a migration in `_lifespan` does not affect it.
Nothing here needs it to.

- [ ] **Step 8: Commit**

```bash
make check-py check-lint check-mypy && make baseline
git add backend/src/grimoire/store/scenes backend/src/grimoire/store/migrations.py \
        backend/src/grimoire/main.py backend/tests/test_scene_identity.py lint-baselines
git commit -m "Give every scene an immutable identity, and backfill it

Scene ids are recycled: serialize.py:_numbering derives the next number from
the files on disk with no stored counter, so deleting the highest-numbered
scene frees its number and the next create with the same title slug produces
the identical sid. A detached run that checked only whether its scene still
EXISTS would pass against the replacement and publish onto it.

Backfilled rather than assigned at creation only. Every existing scene
predates the field, and comparing two absent values passes -- which would
leave the corruption untouched while reading as solved."
```

---

## Task 2: The run record and registry

A pure data structure with no scheduling in it. Scheduling is Task 3.

**Files:**
- Create: `backend/src/grimoire/routes/runs.py`
- Test: `backend/tests/test_runs_registry.py`

**Interfaces:**
- Produces: `Subject` — `("scene", cid, sid)` / `("campaign", cid)` /
  `("world", wid)` / `("global",)`. A plain tuple, so it is hashable and
  `_dump`-able without pydantic.
- Produces: `RunClass` — `"turn" | "review" | "background" | "draft"`.
- Produces: `Run` with fields `id, subject, cls, kind, attempt_id,
  scene_identity, labels, state, frames, result, error, started_at,
  ended_at`, plus
  `finish(state: str, at: float | None = None) -> None` (defaults `at` to the
  current clock; tests pass it explicitly so reaping is deterministic) and
  `append_frame(frame: str) -> int` returning the absolute index assigned.

  **The parameter is a raw SSE frame — the exact bytes the producer yields —
  not a payload dict.** The producer is `event_stream`, which yields
  already-encoded strings, and one of them is `_HEARTBEAT` (`": heartbeat\n\n"`,
  `streaming.py:51`), a *comment* frame with no JSON payload at all. Typed as
  `dict`, the buffer can only take heartbeats by rejecting them, dropping them,
  or re-encoding every other frame back out of a decode it should never have
  done — and dropping them is the one that looks harmless and is not, because
  a heartbeat occupies an index and a client resuming at `consumed + 1` would
  then be off by the number of heartbeats it never saw. Store the frame
  verbatim, index it, and let replay be a byte-for-byte concatenation.

  Every registry test appends strings, and one of them appends a heartbeat
  between two deltas and reconnects across it, asserting the resumed text
  equals the uninterrupted text. That test is what makes the type real; a
  suite that only ever appends `{"delta": ...}` passes just as well against
  the wrong signature.

  **`labels` is `{"campaign": str, "scene": str}`, captured at start** by the
  producing route and carried on the run. The Android terminal notification
  reads it; resolving the names at terminal time would find nothing for a scene
  deleted mid-run, which is exactly the case the *error* notification exists to
  report.

  **`labels` is a REQUIRED positional on `start_or_existing`, not an optional
  keyword.** Left optional, an implementation can omit it everywhere, pass
  every test in this plan, and ship an Android build whose notifications have
  no campaign or scene text — a feature silently absent behind a green suite.
  Each of the five producing routes gets a test asserting the captured display
  names, **taken before the scene is deleted**, since reading them afterwards
  is the failure this exists to prevent.
- Produces: `RunRegistry` with
  `start_or_existing(subject, cls, kind, attempt_id, scene_identity, labels) -> tuple[Run, bool]`
  -- `(run, True)` for a new run, `(run, False)` when `attempt_id` already has
  one, and **raises `RunInFlight(run_id=...)`** when the class declares an
  exclusion key that a `running` run holds,
  `get(run_id, subject) -> Run | None`, `for_subject(subject) -> list[Run]`,
  `live_for_key(key) -> Run | None`, `reap(now) -> int`.
- Produces: `exclusion_key(subject, cls) -> str | None` — the scene key for
  `turn` and `review`, `None` otherwise.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_runs_registry.py
from grimoire.routes import runs

SCENE = ("scene", "saltmarch", "0001--mara")
OTHER = ("scene", "saltmarch", "0002--winifred")
TWIN  = ("scene", "realm", "0001--mara")     # SAME sid, different campaign
WORLD = ("world", "realm")
LABELS = {"campaign": "Saltmarch", "scene": "Mara"}


def test_turn_and_review_share_one_exclusion_key_per_scene():
    assert runs.exclusion_key(SCENE, "turn") == runs.exclusion_key(SCENE, "review")
    assert runs.exclusion_key(SCENE, "turn") != runs.exclusion_key(OTHER, "turn")
    # Scene ids are CAMPAIGN-LOCAL: `_numbering` derives the next number from
    # the files in that campaign's own directory (serialize.py:261), so
    # `0001--mara` exists in every campaign that has a first scene. A key or
    # lookup built from `sid` alone passes every other test here and then
    # either rejects a turn in campaign B because campaign A has one live, or
    # -- worse -- routes B's reply onto A's scene.
    assert runs.exclusion_key(SCENE, "turn") != runs.exclusion_key(TWIN, "turn")


def test_background_and_draft_declare_no_key():
    assert runs.exclusion_key(SCENE, "background") is None
    assert runs.exclusion_key(WORLD, "draft") is None


def test_second_turn_on_a_busy_scene_is_refused():
    r = runs.RunRegistry()
    first, started = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert started
    assert r.live_for_key(runs.exclusion_key(SCENE, "turn")) is first
    # A different attempt on the same busy scene does not get a run.
    with pytest.raises(runs.RunInFlight) as exc:
        r.start_or_existing(SCENE, "turn", "chat", "a2", "ident", LABELS)
    assert exc.value.run_id == first.id


def test_drafts_overlap_on_one_subject_and_both_stay_discoverable():
    """The bug a most-recent pointer would ship: the second start hides the
    first, which then has no discovery path at all."""
    r = runs.RunRegistry()
    a, _ = r.start_or_existing(WORLD, "draft", "image-description", "a1", None, LABELS)
    b, _ = r.start_or_existing(WORLD, "draft", "image-description", "a2", None, LABELS)
    assert {run.id for run in r.for_subject(WORLD)} == {a.id, b.id}


def test_repeated_attempt_id_returns_the_existing_run_even_when_terminal():
    r = runs.RunRegistry()
    first, started = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert started
    first.finish("landed")
    again, started_again = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert again is first and not started_again


def test_get_refuses_a_run_id_from_another_subject():
    r = runs.RunRegistry()
    run, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    assert r.get(run.id, SCENE) is run
    assert r.get(run.id, OTHER) is None


def test_reap_drops_terminal_runs_past_the_window_and_keeps_live_ones():
    r = runs.RunRegistry()
    done, _ = r.start_or_existing(SCENE, "turn", "chat", "a1", "ident", LABELS)
    done.finish("landed", at=1000.0)
    live, _ = r.start_or_existing(OTHER, "turn", "chat", "a2", "ident", LABELS)
    assert r.reap(now=1000.0 + runs.REAP_SECONDS + 1) == 1
    assert r.get(done.id, SCENE) is None
    assert r.get(live.id, OTHER) is live
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_runs_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'grimoire.routes.runs'`

- [ ] **Step 3: Write the module**

`REAP_SECONDS = 600`. The registry holds three dicts —
`_runs: dict[str, Run]`, `_by_subject: dict[Subject, list[str]]`,
`_by_attempt: dict[tuple[Subject, str], str]`, `_by_key: dict[str, str]` — all
mutated under one `threading.Lock`, get-or-create style, for the reason
`locks.campaign_lock` documents: a plain check-then-act hands two concurrent
first callers different answers.

`start_or_existing` in order: (1) return the existing run if `attempt_id`
matches; (2) raise `RunInFlight(run_id=...)` if the class has a key and it is
held by a `running` run; (3) create, index, return.

**One representation, and it must hold heartbeats too.** The producer yields
raw SSE *strings* — including the literal comment `": heartbeat\n\n"`
(`streaming.py:51`) that keeps a long provider pause from looking like a dead
connection. Subscribers read only from the buffer, so a buffer of `dict`
payloads forces an implementer to either drop heartbeats (losing the keepalive
exactly when it matters) or invent an undocumented parse-and-re-encode step
that can shift frame indices and corrupt replay.

So a frame is `{"index": int, "raw": str}` — the wire text verbatim, comments
included, with its absolute index alongside.

**And the index goes ON THE WIRE, not only in the server-side buffer.** Storing
it beside `raw` and then replaying `raw` verbatim leaves the client exactly
where it started: `parseSSEChunk` discards heartbeat comments without a
callback, so a cursor advanced per decoded event still lags the server's frame
position, and `consumed + 1` after a heartbeat replays deltas already
rendered. Every frame carries its index in the protocol — an SSE `id:` line is
the natural place, since it survives the comment/data distinction — and
`parseSSEChunk` is extended to surface it. Cover a reconnect whose offset lands
**across a heartbeat**; that is the case that fails silently, by duplicating
text mid-reply.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_runs_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
make check-py check-lint check-mypy && make baseline
git add backend/src/grimoire/routes/runs.py backend/tests/test_runs_registry.py lint-baselines
git commit -m "A run registry: subject, class, and the indexes they need

Addressed by id, never by (cid, sid) -- a subscriber that resolved a frame
stream by scene could, between one run ending and the next starting, attach a
view showing scene B to frames produced for scene A.

The subject index is a COLLECTION, not a most-recent pointer, and holds
terminal runs. Both matter: draft and background declare no exclusion key so
they legitimately overlap on one subject, and a terminal run must stay
findable or a run that finished while nobody was attached leaves the client
unable to tell 'post landed' from 'post never sent'."
```

---

## Task 3: Starting a run — the bridge, the boundary, the reaper

The mechanism the whole feature rests on, and the one the spec found could not
work as first written.

**Files:**
- Create: `backend/src/grimoire/runner.py`
- Modify: `backend/src/grimoire/main.py:157`
- Test: `backend/tests/test_runner.py`

**Fixtures this task's tests need** — written in `test_runner.py` itself:
`app_with_lifespan` (an app whose `_lifespan` has been entered, so the portal
and registry exist on `app.state`) and `app_with_lifespan_factory` (the same as
a context manager, so a test can *exit* the lifespan and observe shutdown).
The `SCENE` / `OTHER` / `WORLD` subject constants from Task 2's test module are
re-declared here rather than imported across suites — three tuples, and a
cross-suite import couples two files for nothing. Remember `import pytest` and
`import anyio` where the snippets use them.

**Interfaces:**
- Consumes: `runs.RunRegistry`, `runs.Run` (Task 2).
- Consumes: `runs.install_registry(app) -> None` — **lives in Task 2's
  `routes/runs.py`**, not here, because it constructs the registry and nothing
  else; it is listed here because this is the task that makes `main.py` call
  it. Called from **`create_app`**, not the lifespan: the registry is pure data
  and needs no running loop. See the fixture note above for why it cannot wait
  for startup.
- Produces: `runner.install(app, tg) -> None` — called from `_lifespan` inside
  the task group; stashes `app.state.run_portal` and starts the reaper. It does
  **not** create the registry; it attaches the machinery that needs a loop.
- Produces: `runner.start(app, run, factory) -> None` — thread-safe from a
  synchronous handler. `factory` is a zero-arg callable returning the
  coroutine to run.
- Produces: `Run.cancel_scope` — an `anyio.CancelScope` the producer runs
  inside, plus `Run.terminal: anyio.Event` set once the abort hook has
  finished. `runner.cancel(app, run) -> None` cancels the scope; the cancel
  route awaits `run.terminal` before responding, which is what makes "the slot
  stays held until `on_abort` returns" true rather than aspirational.

  **Construct `ready` and `terminal` through the portal, not with a bare
  `anyio.Event()` on the handler thread.** The reasoning is worth writing down
  because the naive version *appears* to work:

  - On anyio ≥ 4.2, `Event.__new__` catches `NoEventLoopError` and hands back
    an `EventAdapter` that binds to a backend lazily — so constructing one off
    the loop does not raise, and a review claiming `AsyncLibraryNotFoundError`
    here is wrong for the version this tree resolves today (checked against
    4.14).
  - But `anyio` is **unpinned**. It arrives transitively through
    `fastapi>=0.110` (`pyproject.toml:11`, mirrored in
    `android/app/build.gradle.kts:82`), whose own floor is anyio 3.7.1 — and on
    anyio 3.x `Event()` goes straight through `sniffio` and *does* raise off
    the loop. `check-pydantic1` resolves the Android dependency set separately,
    so "it worked on my machine" and "it works in that job" are different
    claims.
  - And the adapter's lazy binding is itself unsynchronized: `_event` checks
    `_internal_event is None` and then assigns, with no lock, so a `set()` from
    the handler thread racing a `wait()` on the loop can bind twice and leave a
    waiter parked on an object the setter never touches.

  One portal `call(anyio.Event)` per reservation costs a round-trip and removes
  all three questions. It also composes with the rule already stated below —
  that every `set()` is marshalled through the portal — into one simple
  invariant: **these events are created and mutated only on the loop.** Do not
  "simplify" the construction back to a bare call because it passes locally;
  leave the reason in a comment.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_runner.py
def test_start_works_from_a_synchronous_handler_thread(app_with_lifespan):
    """The six streaming routes are `def`, not `async def`, so FastAPI runs
    them in a threadpool worker. `start_soon` is not thread-safe; this is the
    test that would have caught the whole design failing at runtime."""
    app = app_with_lifespan
    done = threading.Event()

    async def work():
        done.set()

    def from_worker_thread():
        run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
        runner.start(app, run, lambda: work())

    threading.Thread(target=from_worker_thread).start()
    assert done.wait(timeout=5), "the run never reached the lifespan loop"


def test_one_runner_raising_does_not_cancel_its_siblings(app_with_lifespan):
    """anyio cancels all siblings and propagates out of _lifespan, so without
    a per-run boundary one malformed scene would abort every other live run
    and stop the backup ticker."""
    app = app_with_lifespan

    async def boom():
        raise RuntimeError("one bad turn")

    async def fine():
        await anyio.sleep(0.05)

    bad, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    good, _ = app.state.runs.start_or_existing(OTHER, "turn", "chat", "a2", "i", LABELS)
    runner.start(app, bad, lambda: boom())
    runner.start(app, good, lambda: fine())
    # NOT `survived.wait()`: `fine` sets that flag before it returns, and
    # `_guarded` writes `landed` only after `await factory()` returns. Waking on
    # the flag and asserting the state is a race that goes green on an idle
    # machine and red on a loaded CI runner -- in this test, which is about
    # isolation and would then be blamed for a defect it does not have.
    _wait_terminal(app, bad.id)
    _wait_terminal(app, good.id)
    assert bad.state == "failed"
    assert good.state == "landed"


def test_shutdown_cancels_live_runs_and_they_flush(app_with_lifespan_factory):
    flushed = []

    async def slow():
        try:
            await anyio.sleep(30)
        finally:
            flushed.append("partial")

    with app_with_lifespan_factory() as app:
        run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
        runner.start(app, run, lambda: slow())
    assert flushed == ["partial"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'grimoire.runner'`

- [ ] **Step 3: Implement the bridge**

`_lifespan` enters a `BlockingPortal` and stores it on `app.state`. From an
external thread, `runner.start` calls **`portal.start_task_soon(...)`**; on the
loop thread it uses the task group directly.

Do **not** reach for `anyio.from_thread.run_sync` here. That is a different
API — a handoff for code already running inside an AnyIO *worker* thread — and
it does not run "against" a portal. FastAPI's threadpool worker is not an
AnyIO worker-thread context, and neither is the plain `threading.Thread` the
test below uses, so `from_thread.run_sync` would fail at the first run rather
than bridging anything.

`runner.start` therefore checks which thread it is on and picks the right
door.

- [ ] **Step 4: Add the cancellation handle**

```python
async def _guarded(run, factory):
    with anyio.CancelScope() as scope:
        run.cancel_scope = scope
        ...
    run.terminal.set()          # AFTER the abort hook, never before
```

Without a scope on the run there is nothing for the cancel route to interrupt,
and without `terminal` the route cannot wait for the abort hook — so a fast
re-send could race the partial-persist, which is the thing the ordering exists
to prevent.

**Every pre-start exit must set the handshake events too.** The readiness
event covers the window from `start` to the task beginning — but the run is
indexed by `start_or_existing` *before* that, while the route is still doing
synchronous setup, and Task 5 explicitly allows that setup to return early and
release the reservation without ever scheduling `_guarded`. A discovery or
cancel request landing in that window finds a real run and then waits forever
on events no task will ever set. So a pre-start release marks the run terminal
and sets both `ready` and `terminal`, and there is a test for **cancel racing
an early-exit route**, not only cancel against a runner that started normally.

**And an early exit that reported an error must buffer that error, or give up
its attempt index.** These two rules collide otherwise. The setup path returns
an SSE error frame *directly* — a `check_error`, say — without the run ever
buffering a frame; the release marks it terminal; and `start_or_existing`
returns an existing run even when terminal, which is what makes retries
idempotent. So a client whose response was lost re-POSTs with the same attempt
id, adopts that terminal record, streams it, and receives **nothing** — an
empty terminal stream where the first attempt got a specific, actionable
error. The retry looks like a turn that silently did nothing.

Pick one, consistently: either the early error is written into the run's frame
buffer and finished with the matching structured outcome — so replay reproduces
what the first caller saw — or the run is released *without* registering its
attempt id, so a retry is a genuinely fresh attempt rather than the adoption of
an empty one. Buffering is the better shape, because it keeps one rule ("an
attempt id names a run whose outcome can be replayed") instead of two. Test the
retry: same attempt id after a lost early-error response reproduces the same
error frame.

**Those event updates must be marshalled onto the lifespan loop.** The
pre-start cleanup runs in FastAPI's synchronous handler thread while a
concurrent cancel awaits `ready`/`terminal` on the loop — and once an
`anyio.Event` has an async waiter, calling `set()` directly from a worker
thread is not thread-safe on the asyncio backend and may simply fail to wake
it. The cancel-versus-early-exit race would then hang exactly as before, with
the fix appearing to be in place. Route the updates through the
`BlockingPortal`, or use a handshake that is explicitly thread-safe.

**`runner.start` must not expose the run before its scope is installed.** The
scope is assigned inside `_guarded`, which does not begin until the scheduled
task is picked up — so a Stop arriving immediately after `start` returns would
find no scope to cancel and then wait forever on `run.terminal` while the
provider ran happily on. Add a readiness handshake: `start` waits for an
`anyio.Event` the task sets once the scope is in place, or `cancel` waits for
that event before acting. A test must cancel *in the same breath* as starting,
not after a sleep, or it will not exercise this.

- [ ] **Step 5: Implement the failure boundary**

```python
async def _guarded(run, factory):
    """Everything that is not a shutdown cancellation is contained here.

    An anyio task group cancels all siblings and propagates the moment any
    child raises, so without this one malformed scene would abort every other
    live run, stop the backup ticker, and take the exception out through
    `_lifespan` itself -- a single bad turn ending the process.
    """
    try:
        outcome = await factory()          # an Outcome dict, or None
        # UNPACK it. `finish` takes a state STRING; handing it the dict leaves
        # run.state as a dict and never copies Outcome.error into Run.error,
        # which silently breaks polling and the terminal notification.
        run.error = (outcome or {}).get("error")
        run.finish((outcome or {}).get("state", "landed"))
    except anyio.get_cancelled_exc_class():
        run.finish("cancelled")
        raise                      # shutdown must still propagate
    except Exception:              # noqa: BLE001 - the containment IS the point
        log.exception("run %s failed", run.id)
        run.finish("failed")
```

- [ ] **Step 6: Wire it into the lifespan and start the reaper**

In `main.py`, call `runs.install_registry(app)` from `create_app`, beside the
gateway clients and for the same reason they are there. Then, inside the
existing `async with anyio.create_task_group() as tg:`, call
`runner.install(app, tg)` before `tg.start_soon(_backup_ticker)`, and start the
reaper on the same group.

The registry instance goes on `app.state`, **not** at module scope: this repo
builds and tears down `create_app()` repeatedly in one process, and a module
global's terminal records, attempt ids and exclusion keys would leak into the
next app.

Cover the split itself: one test asserts `create_app().state.runs` exists with
no lifespan entered, and one asserts `runner.start` on that app raises the
explicit "no run portal" error rather than `AttributeError`. Both fail against
an implementation that puts the registry in the lifespan, which is the shape
this plan had until it was checked against `conftest`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_runner.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
make check-py check-lint check-mypy && make baseline
git add backend/src/grimoire/runner.py backend/src/grimoire/main.py \
        backend/tests/test_runner.py lint-baselines
git commit -m "Start runs on the lifespan loop, from either kind of thread

post_chat, post_retry, post_regenerate, post_replay_turn, post_opener and
post_roll_proposal are all synchronous `def`, so FastAPI runs them in a
threadpool worker -- and start_soon on an anyio task group is not safe to
call from another thread. Reaching for the stashed group directly would have
failed on every run, immediately. A blocking portal is the supported door.

Each runner is contained: anyio cancels siblings and propagates on the first
raise, so one bad turn would otherwise abort every other live run, stop the
backup ticker and leave through _lifespan."
```

---

## Task 4: The run routes

**Files:**
- Modify: `backend/src/grimoire/routes/runs.py`
- Modify: `backend/src/grimoire/routes/__init__.py` (registration order)
- Test: `backend/tests/test_runs_routes.py`

**Interfaces:**
- Produces: `GET /api/campaigns/{cid}/scenes/{sid}/run[?attempt=]` — with
  `attempt`, an exact match and nothing else. **Without it, the subject's most
  recently STARTED run**, which is a decision and not an implementation detail:
  `_by_subject` routinely holds several, because a terminal run stays readable
  for the whole `REAP_SECONDS` window while a new one is already live. Return
  an arbitrary or first match and mount discovery hands the client the *older,
  terminal* record — it settles, skips attachment, and misses the live reply
  entirely, which is the exact failure this endpoint exists to prevent. Test it
  with a terminal run and a newer live run on one scene, and assert the live
  one comes back.
- Produces: `GET /api/campaigns/{cid}/scenes/{sid}/runs/{run_id}/stream?from=N`
- Produces: `GET /api/campaigns/{cid}/scenes/{sid}/runs/{run_id}`
- Produces: `POST /api/campaigns/{cid}/scenes/{sid}/runs/{run_id}/cancel`
- Produces: `GET /api/campaigns/{cid}/scenes/by-identity/{identity}` — the
  reverse lookup Task 1 exists for, over `store.scenes.find_by_identity`.
  Returns `{"id": sid}`, or 404 when the identity names no live scene.
  Registration order matters, though not against `/scenes/{sid}` — that
  pattern is a segment shorter, so it cannot shadow this one. The collision
  is a *crossing* with the entity catch-all: `GET
  /api/campaigns/{cid}/{kind}/{eid}/images` matches
  `/api/campaigns/{cid}/scenes/by-identity/images` too, so which handler runs
  is decided by include order alone. `test_route_order` computes both
  shadowing and crossings from the live route table, so it will fail on this
  pair until the winner is recorded in `CROSSING_PAIRS` with the reason the
  others carry — "scenes" is not an entity kind, so the catch-all can never
  legitimately claim a URL under it, and `runs` is included before `entities`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_runs_routes.py
def test_from_is_inclusive_and_a_reconnect_reproduces_the_reply_once(client, live_run):
    """`from=N` sends frame N itself, so a client that consumed through N must
    ask for N+1. Getting this backwards duplicates a delta in the middle of a
    reply -- invisible until someone reads the text."""
    # Raw SSE frames, exactly what `event_stream` yields -- see `append_frame`
    # in Task 2. A dict here would test a re-encoding path production never
    # takes, and could not express the heartbeat case below at all.
    live_run.append_frame(_sse({"delta": "Wind off the "}))   # index 0
    live_run.append_frame(_sse({"delta": "water."}))          # index 1
    live_run.finish("landed")
    whole = _read_stream(client, live_run, frm=0)
    assert "".join(e["delta"] for e in whole) == "Wind off the water."
    resumed = _read_stream(client, live_run, frm=1)
    assert "".join(e["delta"] for e in resumed) == "water."


def test_a_reconnect_across_a_heartbeat_does_not_lose_or_repeat_text(client, live_run):
    """The heartbeat occupies an index like any other frame.

    NOTE what this does and does not prove. It hard-codes `frm=1`, so it proves
    only that the SERVER honours an offset that lands on a heartbeat. The bug
    this scheme exists to prevent is on the CLIENT -- deriving the cursor by
    counting decoded data events, which `parseSSEChunk` strips heartbeats from.
    An implementation with exactly that defect passes this test and then, having
    consumed frames 0, 1 and 2, records cursor 1 and resumes at 2, replaying the
    final delta. Task 6 carries the test that catches it; this one is its
    server-side half and is not sufficient alone."""
    live_run.append_frame(_sse({"delta": "Wind off the "}))   # index 0
    live_run.append_frame(": heartbeat\n\n")                  # index 1
    live_run.append_frame(_sse({"delta": "water."}))          # index 2
    live_run.finish("landed")
    resumed = _read_stream(client, live_run, frm=1)
    assert "".join(e["delta"] for e in resumed) == "water."


def test_from_past_the_buffer_tails_rather_than_erroring(client, live_run):
    # A LIVE run's stream stays open and tails; it does not return empty. An
    # assertion of `== []` against a live run either hangs or silently tests a
    # helper that stopped reading early.
    live_run.finish("landed")
    assert _read_stream(client, live_run, frm=99) == []


def test_from_past_the_buffer_on_a_live_run_receives_later_frames(client, live_run):
    reader = _read_stream_async(client, live_run, frm=99)
    live_run.append_frame(_sse({"delta": "later"}))
    live_run.finish("landed")
    assert [e["delta"] for e in reader.result()] == ["later"]


def test_a_reaped_or_unknown_run_id_is_run_gone(client, campaign_scene):
    cid, sid = campaign_scene
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/nope")
    assert r.status_code == 404 and r.json()["kind"] == "run_gone"


def test_a_run_id_from_another_scene_is_run_gone(client, two_scenes, live_run):
    # `Run` has `subject`, not `cid` -- the campaign id is the subject's second
    # element. Reaching for `live_run.cid` raises AttributeError before the
    # request goes out, so the isolation this test names would go unproven
    # while the suite still went red somewhere confusing.
    _, cid, _ = live_run.subject
    _, other_sid = two_scenes
    r = client.get(f"/api/campaigns/{cid}/scenes/{other_sid}/runs/{live_run.id}")
    assert r.status_code == 404 and r.json()["kind"] == "run_gone"


def test_attempt_lookup_answers_for_a_run_that_finished_unattended(client, campaign_scene):
    """#95: the response was lost after the post was appended. 'A run exists
    recently' is not proof -- an unrelated run finishing on this scene would
    satisfy it -- so recovery asks by attempt id."""
    cid, sid = campaign_scene
    _finish_run(cid, sid, attempt="mine")
    _finish_run(cid, sid, attempt="someone-elses")
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/run?attempt=mine").json()
    assert got["attempt_id"] == "mine"
    missing = client.get(f"/api/campaigns/{cid}/scenes/{sid}/run?attempt=never-sent")
    assert missing.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_runs_routes.py -v`
Expected: FAIL with 404s from unregistered routes

- [ ] **Step 3: Implement the routes**

Every handler resolves the run with `registry.get(run_id, subject_from_path)`
and returns `404 {"detail": {"kind": "run_gone"}}` on a miss — the subject
comparison is what stops scene A's run being streamed or cancelled through
scene B's URL.

**Validate `from` as non-negative and reject it with a 400.** `-1` on a natural
list slice starts from the tail and silently drops every earlier frame,
rendering only the last delta while looking like a successful replay. Add a
route test for the rejected value.

The `cancel` route does **not** get `@computes_only`: cancelling a streaming
run drives `on_abort`, whose job is to persist the partial, and that is a
write. The state moves to `cancelled` only after the abort hook returns, and
the response does not resolve before it — freeing the slot at request time
would let a fast re-send race the partial-persist.

- [ ] **Step 4: Register in an order `test_route_order.py` accepts**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_route_order.py -v`
Expected: PASS

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_runs_routes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
make check-py check-lint check-mypy && make baseline
git add backend/src/grimoire/routes lint-baselines \
        backend/tests/test_runs_routes.py backend/tests/test_route_order.py
git commit -m "Run routes: discover, stream from an offset, poll, cancel

`from` is inclusive, so a client that consumed through N asks for N+1.
Recovery asks by attempt id rather than recency: a run that finished eight
minutes ago is inside the reap window, so 'a run exists' would report an
unrelated run as proof that THIS send landed -- turning an honest 'I don't
know' into a confident wrong answer."
```

---

## Task 5: Detach the scene turns

**Files:**
- Modify: `backend/src/grimoire/routes/streaming.py`
- Modify: `backend/src/grimoire/routes/scenes.py:359,419,491,3212`
- Modify: `backend/src/grimoire/routes/mechanics.py:110`
- Test: `backend/tests/test_runs_detach.py`

**Interfaces:**
- Consumes: `runner.start`, `runs.RunRegistry` (Tasks 2–3).
- Produces: `_fence_stream(...) -> tuple[AsyncIterator[str], Callable[[], Outcome]]`
  — the frames, and a terminal-outcome getter.
- Produces: `Outcome` — `{"state": "landed" | "failed", "error": {...} | None}`,
  where `error` carries `{status, detail, kind, retry_after, post_returned}`.

  **`post_returned` is not optional.** When a provider fails before producing
  narration, `_chat_stream.on_error` takes the player's appended post back off
  and the SSE frame says so (`streaming.py:545`; typed at `stream.ts:14`), so
  the client can restore the text to the composer. A client that detached
  before that frame must learn the same fact from `Run.error` — otherwise the
  server rolls the post back, the client never hears, and the player's words
  vanish. Test it through the poll route, not only the stream.

  **A bare `"failed"` is not enough.** A client that detached before the
  provider failed never saw the buffered SSE error frame, and Task 6 forbids
  replaying a terminal run's frames on a fresh mount — so polling would report
  failure with no provider detail and no retry metadata, which is the one
  situation where the detail changes what the player should do next. The
  structured outcome is what populates `Run.error`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_runs_detach.py
def test_a_dropped_subscriber_does_not_cancel_the_run(live_server):
    """The inverse of today's behavior, and the single most important test in
    this plan. Disconnect used to mean cancel; now it detaches a subscriber."""
    cid, sid = live_server.campaign_scene
    # A held provider makes "mid-generation" a defined moment rather than a
    # sleep: it has emitted one delta and will not emit the next until the test
    # releases it, so the disconnect below lands squarely inside the stream.
    held = live_server.hold_provider()
    with httpx.stream("POST", f"{live_server.url}/api/campaigns/{cid}/scenes/{sid}/chat",
                      json={"content": "Mara steps onto the dock."}) as r:
        run_id = _first_run_frame(r)["run"]["id"]
        held.await_first_delta()
        r.close()                       # a real socket close, mid-generation
    held.release()
    _wait_terminal(live_server.app, run_id)
    run = live_server.app.state.runs.get(run_id, ("scene", cid, sid))
    assert run.state == "landed"
    assert "dock" in store.scenes.read_scene(cid, sid)["messages"][-1]["content"].lower()


def test_a_dropped_subscriber_does_not_cancel_a_roll_continuation(live_server):
    """The same guarantee, through the OTHER kind of producer.

    `post_roll_proposal` streams `_continuation_stream` (mechanics.py:193), not
    `_chat_stream`, and it lives in a different module. An implementation that
    migrates `post_chat` and leaves the mechanics handler returning its
    continuation directly passes every other detach test here while locking the
    phone during an accepted roll still cancels and drops the narration -- and
    a roll is exactly when a player looks away."""
    cid, sid = live_server.campaign_scene
    pid = _stage_a_proposal(live_server, cid, sid)
    held = live_server.hold_provider(reply="The lamps gutter, then hold.")
    with httpx.stream("POST", f"{live_server.url}/api/campaigns/{cid}"
                      f"/scenes/{sid}/roll-proposal",
                      json={"proposal": pid, "action": "accept"}) as r:
        run_id = _first_run_frame(r)["run"]["id"]
        held.await_first_delta()
        r.close()
    held.release()
    _wait_terminal(live_server.app, run_id)
    assert live_server.app.state.runs.get(run_id, ("scene", cid, sid)).state == "landed"
    assert "gutter" in store.scenes.read_scene(cid, sid)["messages"][-1]["content"].lower()


def test_a_provider_failure_ends_failed_not_landed(run_client, campaign_scene):
    """_fence_stream catches LLMError, emits an SSE error frame and returns
    NORMALLY. A runner inferring success from 'did not raise' would mark the
    turn landed and fire the success notification."""
    cid, sid = campaign_scene
    run_id = _drive_failing_turn(run_client, cid, sid)
    run = run_client.app.state.runs.get(run_id, ("scene", cid, sid))
    assert run.state == "failed"


def test_a_rejected_send_leaves_the_transcript_byte_identical(run_client, campaign_scene):
    """The slot is reserved before the FIRST mutator, not just before the
    append: retry heals and supersedes proposals, regenerate archives a reply,
    replay stages posts. A 409 after any of those tells the player nothing
    happened when something did."""
    cid, sid = campaign_scene
    _start_and_hold_a_turn(run_client, cid, sid)
    before = _scene_bytes(cid, sid)
    r = run_client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "second"})
    assert r.status_code == 409 and r.json()["kind"] == "run_in_flight"
    assert _scene_bytes(cid, sid) == before


def test_two_scenes_in_one_campaign_do_not_cross_contaminate(live_server):
    """The runs must genuinely OVERLAP, which is why this is on `live_server`
    and not `run_client`.

    `TestClient` buffers a streaming response to completion -- the same
    property that made the disconnect test vacuous. Two sequential
    `_start_turn` calls through it therefore run one after the other: A is
    already terminal before B is reserved, and a shared mutable producer that
    cross-contaminates genuinely concurrent scenes passes anyway. The isolation
    this test names would be untested while reading as covered.

    So: both providers are held behind a barrier, both requests are in flight
    on real sockets before either is released, and only then does either
    finish."""
    cid, (a, b) = live_server.two_scenes
    held_a = live_server.hold_provider(scene=a, reply="Seraphine waits.")
    held_b = live_server.hold_provider(scene=b, reply="Winifred does not.")
    with _in_flight(live_server, cid, a, "Seraphine?") as ra, \
         _in_flight(live_server, cid, b, "Winifred?") as rb:
        held_a.await_first_delta()
        held_b.await_first_delta()   # both live at once, which is the point
        held_a.release()
        held_b.release()
    _wait_terminal(live_server.app, ra); _wait_terminal(live_server.app, rb)
    assert "seraphine" in _last_reply(cid, a).lower()
    assert "winifred" in _last_reply(cid, b).lower()


def test_the_same_sid_in_two_campaigns_does_not_collide(live_server):
    """The case one campaign cannot express, and the likeliest one in real use.

    Scene ids are campaign-local, so `0001--...` exists in every campaign with
    a first scene -- and a phone user with two campaigns open hits this on
    their first turn in each. An implementation that keys the registry on `sid`
    alone passes the same-campaign test above and then either 409s the second
    campaign's turn or publishes its reply onto the first campaign's scene."""
    (cid_a, sid), (cid_b, twin) = live_server.same_sid_in_two_campaigns
    assert sid == twin                      # the premise, asserted not assumed
    held_a = live_server.hold_provider(campaign=cid_a, reply="Seraphine waits.")
    held_b = live_server.hold_provider(campaign=cid_b, reply="Winifred does not.")
    with _in_flight(live_server, cid_a, sid, "Seraphine?") as ra, \
         _in_flight(live_server, cid_b, twin, "Winifred?") as rb:
        held_a.await_first_delta()
        held_b.await_first_delta()   # neither reservation excluded the other
        held_a.release(); held_b.release()
    _wait_terminal(live_server.app, ra); _wait_terminal(live_server.app, rb)
    assert "seraphine" in _last_reply(cid_a, sid).lower()
    assert "winifred" in _last_reply(cid_b, twin).lower()
```

`_in_flight(server, cid, sid, text)` is a context manager local to this module:
it issues the POST on its own thread, yields the run id off the first frame,
and leaves the socket open until the block exits. `live_server.hold_provider`
takes a `scene=` so the two runs get distinguishable scripted replies —
without that, "no cross-contamination" cannot be told apart from "both scenes
got the same text".

**The disconnect test must not use `TestClient`.** Starlette's in-process
client buffers a streaming response while driving the ASGI app, so the `with`
block is not entered at the first SSE frame while the producer is still live —
it becomes observable only after the stream completes. Exiting the block
therefore simulates nothing, and **an implementation that still cancels on a
real socket close would pass the most important test in this plan.**

So this one test does **not** take the `client` fixture. It takes
`live_server` — a uvicorn instance on an ephemeral port, started per module,
sharing the app and its `GRIMOIRE_HOME` — and talks to it with a real `httpx`
client whose `close()` puts a FIN on the wire. The alternative, if starting a
server in-process proves unstable on CI, is an ASGI harness that sends an
explicit `{"type": "http.disconnect"}` into the running app; either produces
the event the feature is about, and `TestClient` produces neither.

`live_server.hold_provider()` wraps the `llm_fakes` provider so it blocks
after its first delta until released. Without it the test has no way to name a
moment that is after the stream started and before it finished, and a sleep
long enough to be safe is also long enough to let the whole turn complete —
which passes vacuously, exactly like the version this replaces.

Build `live_server` as a fixture in `conftest.py` alongside `client`, not
inline in this module: Task 5's cancel tests need the same real socket.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_runs_detach.py -v`
Expected: FAIL — the run frame is absent and the turn aborts on disconnect

- [ ] **Step 3: Return an explicit terminal outcome from `_fence_stream`**

Its `except LLMError` branch already emits an error frame and returns; record
`"failed"` there and `"landed"` on the clean path. The runner reads that rather
than guessing from whether the coroutine raised.

**`LLMError` is not the only branch that ends badly and returns normally.**
`_fence_stream` has a second one at `streaming.py:422`: `finalize` raising
`store.locks.StoreBusy`, which emits a `busy` error frame and returns. That
path is reached *after* the provider's clean EOF, so an implementation that
sets `"landed"` on "the `async for` completed" marks the run landed, fires the
success notification, and tells the player a turn is waiting — when the reply
did not finish persisting and the subscriber that was still attached got an
error. `_persist_reply`'s own `StoreBusy`/`SceneNotFound` handler at `:367` is
the same shape: it swallows so the stream can still report the upstream
failure, and it means the write was lost.

So the rule is not "record failure in the `LLMError` branch." It is: **every
branch that emits a terminal error frame records a failed outcome, and the
clean outcome is recorded only where nothing was swallowed.** Write the
outcome where the frame is emitted, so the two cannot drift apart. Test the
`StoreBusy`-during-finalize path specifically and assert the run is `failed` —
it is the one an implementer will not think of, because from inside the
generator it looks like the turn succeeded.

- [ ] **Step 4: Move the persist hooks onto the runner**

`finalize`, `on_error` and `on_abort` never touch the socket and only *return*
frames, so they move to the runner unchanged in that respect. The turn-token
claim, the `owned_tail` read and the restore/undo transactionality come along
as-is.

**But do not believe the "already under one campaign-lock hold" shorthand when
adding the identity comparison.** `_persist_reply` reaches `store.scenes.
append_reply`, which is `@_serialized` and therefore acquires the lock
*itself*, after the hook has already begun. A comparison made before that
acquisition leaves a window in which the scene is deleted and its `sid`
recycled, and the old run still publishes onto the replacement — the exact
corruption the identity was introduced to stop, surviving because the check and
the write were not one atomic step.

**When the fence refuses, the run is `failed` — say so explicitly, because
neither implementation says it by itself.** Both shapes suppress the write and
return; the provider already reached a clean EOF, so a runner reading "the
coroutine did not raise" records `landed` and fires the success notification for
a reply that was deliberately discarded. The player gets *New Post in …*, opens
the scene, and finds nothing — which is worse than the corruption the fence
prevents, because it is silent.

This is the same defect as the `StoreBusy`-during-finalize branch, and it has
the same rule: the outcome is written where the decision is made, not inferred
downstream. An identity mismatch produces a failed outcome whose error names
the mismatch.

Test it as an integration, not a unit: hold generation, delete the scene,
create a replacement that recycles the `sid`, release, and then assert both
halves — the replacement transcript is untouched **and** the run is `failed`.
Asserting only the first passes against an implementation that discards the
write and reports success.

So either hold `campaign_lock(cid)` continuously across the comparison **and**
every terminal mutation, or push the expected identity down into the locked
persistence call so the store refuses the write itself. The second is the more
robust shape, because it cannot be undone by a future caller who forgets the
outer hold.

- [ ] **Step 5: Branch on an already-known attempt BEFORE any setup**

`start_or_existing` returning `(existing, False)` means this exact POST already
ran. The route must return or replay that run **immediately**, before any heal,
supersede, append, archive, stage or check-resolution — otherwise a duplicate
delivery after a lost response performs the destructive setup a second time,
and the registry handing back the original run does nothing to undo it.
Recording an attempt id makes recovery queryable; this branch is what makes the
POST idempotent.

- [ ] **Step 6: Reserve before the first mutator in each of the five routes**

`post_chat` (before the heal/sidecar block at `scenes.py:364`), `post_retry`,
`post_regenerate` (before the archive-and-remove), `post_replay_turn` (before
staging), `post_roll_proposal` (before the check resolves).

**The byte-identical rejection test covers all five routes, not `/chat`
alone.** `/chat`'s setup is the *least* destructive of the five and therefore
the least informative: a late reservation there heals a sidecar, while a late
reservation in `post_regenerate` archives and removes a reply, in
`post_replay_turn` stages posts, and in `post_roll_proposal` resolves a check
and mutates the proposal record. An implementation that reserves correctly in
`post_chat` and late everywhere else passes a one-route test and still tells
the player "nothing happened" after destroying a reply.

Parameterize it over the five, and compare the right artifact for each: scene
bytes for the transcript mutators, the proposal record for `post_roll_proposal`.
Each must be byte-identical across a rejected request.

**Release the reservation on every early exit, not only on a raise.**
`post_roll_proposal` catches check-resolution failures and *returns* an SSE
error frame rather than raising, so a release wired only to exception unwinding
would leave a never-started run holding that scene's exclusion key for the full
reap window — the scene wedged with nothing running. Wrap each route's setup so
both paths release.

- [ ] **Step 7: Emit the leading `run` frame and accept an attempt id**

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`
Expected: PASS, including the existing streaming suites unchanged

- [ ] **Step 9: Commit**

```bash
make check-py check-lint check-mypy check-pydantic1 && make baseline
git add backend/src lint-baselines backend/tests/test_runs_detach.py \
        backend/tests/conftest.py
git commit -m "Scene turns outlive their request

A closed socket now detaches a subscriber and nothing else; cancellation is
explicit. The persist hooks move to the runner unchanged -- they already ran
under the campaign lock, never touched the socket, and only returned frames,
which is why this is tractable at all.

The slot is reserved before the FIRST mutator in each route, not just before
the append: retry heals and supersedes proposals, regenerate archives and
removes a reply, replay stages posts, roll-proposal resolves a check."
```

---

## Task 5b: Freeze the scene's shape while a run holds it

The spec's rule — *while a `turn` or `review` holds a scene, that scene's shape
does not change* — has three separate doors, and closing one is not closing the
others. Reserving before the snapshot (Task 5) closes the race **before** a
run; this closes the races **during** one.

**Files:**
- Modify: `backend/src/grimoire/routes/scenes.py` (rename, edit, retcon, cut)
- Modify: `backend/src/grimoire/store/scenes/lifecycle.py` (`_create_scene`)
- Test: `backend/tests/test_scene_freeze.py`

**Interfaces:**
- Consumes: `runs.RunRegistry.live_for_key`, `runs.exclusion_key` (Task 2).
- Produces: `common._require_scene_free(app, cid, sid) -> None`, raising
  `HTTPException(409, {"kind": "scene_busy", "run_id": ...})`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_scene_freeze.py
def test_edit_retcon_and_cut_are_refused_while_a_run_holds_the_scene(client, held_scene):
    # saveEdit (CampaignView.tsx:2163) and saveRetcon (:2204) gate on
    # `rolling`, not sceneLocked, and their routes know nothing of a registry
    # -- so a ten-minute absorb would spend its whole budget on a review its
    # watermark then refuses.
    cid, sid = held_scene
    # NOTE the methods: these are NOT all POSTs. Getting that wrong makes the
    # test fail with 405 and look like the guard works when it does not.
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    calls = [
        ("put",    base,                          {"title": "Seraphine"}),
        ("put",    f"{base}/messages/0",          {"content": "edited"}),
        ("delete", f"{base}/messages/0",          None),
        ("post",   f"{base}/messages/0/retcon",   {"content": "retconned"}),
    ]
    for method, path, body in calls:
        r = getattr(client, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 409, f"{method} {path}"
        assert r.json()["kind"] == "scene_busy", f"{method} {path}"


def test_rename_is_refused_while_a_run_holds_the_scene(client, held_scene):
    cid, sid = held_scene
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}", json={"title": "Seraphine"})
    assert r.status_code == 409 and r.json()["kind"] == "scene_busy"


def test_width_crossing_create_refused_while_any_key_in_campaign_held(client, campaign_at_999):
    # 999 -> 1000, NOT 99 -> 100. scene_ids.MIN_WIDTH is 3 and `_numbering`
    # never reports a width below it, so scene 100 is written as `100--...`
    # with no repad at all -- a test built on 99 could never go green.
    # _create_scene crossing 999 -> 1000 calls lifecycle.repad, which renames
    # EVERY scene in the campaign and repoints their sidecars, consulting no
    # run registry. Refusing the explicit rename route never covered this.
    cid, busy_sid = campaign_at_999
    _hold_a_run(cid, busy_sid)
    r = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Winifred"})
    assert r.status_code == 409 and r.json()["kind"] == "scene_busy"


def test_all_of_these_are_allowed_once_the_run_is_terminal(client, held_scene):
    cid, sid = held_scene
    _release(cid, sid)
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}", json={"title": "Seraphine"})
    assert r.status_code == 200


def test_stop_persists_the_partial_and_holds_the_slot_until_it_is_written(live_server):
    """The cancel path end to end, which nothing else here covers.

    Task 3's shutdown test cancels a bare coroutine: it proves the task group
    unwinds, not that `_chat_stream`'s `on_abort` ran, kept the player's post,
    persisted the partial narration, and held the exclusion key until those
    writes finished. An implementation that marks the run terminal before the
    abort hook, or drops the partial, passes every other test in this plan and
    loses narration the player watched arrive.

    On `live_server` because the POST must still be in flight when the cancel
    lands -- a buffering TestClient cannot express that."""
    cid, sid = live_server.campaign_scene
    held = live_server.hold_provider(reply="The lamps are already lit.")
    with _in_flight(live_server, cid, sid, "Mara waits.") as run_id:
        held.await_first_delta()
        r = httpx.post(f"{live_server.url}/api/campaigns/{cid}"
                       f"/scenes/{sid}/runs/{run_id}/cancel")
        assert r.status_code == 200
    # The route answered only after `terminal`, so the writes are already done
    # -- no polling here on purpose: a `_wait_terminal` would hide a cancel
    # that returns early, which is precisely the defect this guards.
    run = live_server.app.state.runs.get(run_id, ("scene", cid, sid))
    assert run.state == "cancelled"
    msgs = store.scenes.read_scene(cid, sid)["messages"]
    assert msgs[-2]["content"] == "Mara waits."      # the post is KEPT, not rolled back
    assert "lamps" in msgs[-1]["content"].lower()    # the partial is persisted
    # And the slot is free the instant the cancel returned, so a re-send cannot
    # race the partial-persist.
    again = httpx.post(f"{live_server.url}/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "again"})
    assert again.status_code != 409


def test_a_background_run_does_not_freeze_the_scene(client, campaign_scene):
    # background declares no exclusion key; if it froze the scene, every turn
    # would be followed by a window where the player could not edit anything.
    cid, sid = campaign_scene
    _hold_a_run(cid, sid, cls="background")
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}", json={"title": "Seraphine"})
    assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_scene_freeze.py -v`
Expected: FAIL — every mutator currently returns 200

- [ ] **Step 3: Add the guard helper**

In `routes/common.py`, add `_require_scene_free(app, cid, sid)` that raises the
409 when `registry.live_for_key(runs.exclusion_key(("scene", cid, sid), "turn"))`
returns a run. It keys on the **exclusion key**, so a `background` or `draft`
run does not freeze anything.

The four handlers to guard, by name and method. They are **not all POSTs**, and
guessing costs a debugging cycle:

| Handler | Route | Location |
|---|---|---|
| `put_scene` (rename) | `PUT /campaigns/{cid}/scenes/{sid}` | `scenes.py:314` |
| `put_scene_message` (edit) | `PUT .../messages/{index}` | `scenes.py:3021` |
| `delete_scene_messages_from` (cut) | `DELETE .../messages/{index}` | `scenes.py:3056` |
| `post_scene_retcon` | `POST .../messages/{index}/retcon` | `scenes.py:3098` |
| `post_scene_roll` | `POST .../roll` | `mechanics.py:34` |
| `post_scene_check` | `POST .../check` | `mechanics.py:203` |
| `post_scene_cast` / `_batch` / `post_emergent_cast` / `post_dismiss` | `POST .../cast`, `.../cast/batch`, `.../emergent-cast`, `.../dismiss` | `scenes.py:2729`+ |
| `delete_scene_cast` | `DELETE .../cast/{kind}/{id}` | `scenes.py:2739` |
| `set_location` / `set_datetime` setters | the scene's location and clock routes | `store/scenes/moment.py` |
| `post_replay` / `post_replay_cancel` | `POST .../replay`, `.../replay/cancel` | `scenes.py:3186`, `:3264` |
| `post_scene_alternate` | `POST .../alternates/{vid}` | `scenes.py:703` |
| `put_chronicle` (review save) | `PUT .../chronicle` | `scenes.py:2483` |
| `post_first_post` | `POST .../first-post` | `greetings.py:328` |
| `post_start_from_greeting` | `POST .../start-from-greeting` | `greetings.py:306` |

`post_replay` calls `store.replay.begin`, which **cuts** the transcript;
`post_replay_cancel` can restore the cut posts; and `post_scene_alternate`
promotes a different assistant run into the transcript. Each rewrites history
underneath a live run's snapshot.

**`delete_scene_cast` is the one that was missing, and it is the most
destructive of the cast routes.** The inventory listed `post_dismiss`, which
only hides a suggestion and touches no transcript — so the entry that looked
like cast-removal coverage was the one route in the group that needs it least.
`delete_scene_cast` (`scenes.py:2739`) calls `store.appearances.leave`, which
removes the actor **and appends a "leaves the scene" transition** when the
transcript is non-empty. A second tab can therefore rewrite both the cast and
the transcript under a live run, and every listed guard and test still passes.

**The cast and moment routes belong here too**, even though the UI already
disables them with `sceneLocked`: a second tab or a direct API call is not
bound by the UI. `appear`/`leave` and `set_location` append transition lines to
the transcript, and the first `set_datetime` **renames the scene** — so these
do not merely change context the live run is generating from, they can trip the
identity check and discard a completed result. Each gets the guard under the
same lock hold as its mutation, and a route test.

**The two greeting routes are the ones the inventory kept missing, because
they live in a different module.** Every guard and test listed here is in
`scenes.py` and `mechanics.py`; `greetings.py` was simply never looked at, so
its routes pass the whole suite untouched. Both mutate a live scene:

- `post_first_post` (`greetings.py:328`) persists an adopted opener as the
  scene's first assistant message. Its existing guard — 409 if
  `read_scene(...)["messages"]` is non-empty — does *not* help here, because
  the case is an **empty** scene: an opener or director turn holds it, the
  scene is still empty at check time, the stale creation tab's adoption
  succeeds, and the live run then appends its reply after a first post that
  appeared from nowhere.
- `post_start_from_greeting` (`greetings.py:306`) is worse: `start_from_greeting`
  returns a **new sid**, so it changes cast, appends, stamps metadata and moves
  the scene out from under a run mid-generation — the identity fence's exact
  trigger, reached by a route the fence's own task never listed.

Both get the same under-lock scene-free check, and a stale-tab test each.

**`put_chronicle` is the subtlest entry, and the one with no fallback.** The
review panel survives a scene switch and a stale tab, so a review prepared
before a new turn began can be saved after it: the save writes a summary of the
*old* transcript and marks the scene absorbed, and the live turn then appends a
reply to a scene already declared finished. That reply is in no chronicle and
never will be.

The commit-token fence does not catch it. `store.commits.scene_epoch` advances
on chronicle save, not on an ordinary turn append, so the token a stale tab
holds still matches and `lookup` finds its own attempt exactly as if nothing
had changed underneath. The registry check is the only thing that sees the live
turn. `put_chronicle` already holds one `campaign_lock(cid)` across its whole
four-write sequence (`scenes.py:2504`, #234), so the guard goes *inside* that
hold, before the first write — which is also where its existing 409 is
reported from, so the retry-safety argument in that function's comment carries
over unchanged. Test a stale-tab save issued while a turn holds the scene.

**The last two are easy to miss and belong in the same guard.** Both call
`store.scenes.append_message(cid, sid, "assistant", line, ...)`
(`mechanics.py:54` and `:221`), so a manual roll or check from a second tab
appends narration to the transcript underneath a live run — letting a review
spend its budget on a snapshot that will be rejected, or a turn append
narration generated from history that predates the roll.

**The check must run under the same `campaign_lock(cid)` hold as the mutation
it guards, not as a separate step at the top of the route.** Checked and
released first, it is a check-then-act with a real gap: the edit sees no run,
a turn then reserves and snapshots the scene, and the edit finally takes the
lock and rewrites the transcript underneath the live run — the exact race the
guard exists to close, reintroduced by where it was placed. Since
`scenes/locking.py:_serialized` already wraps every scene mutator in that lock,
the check belongs inside that hold.

**And that alone still does not close it, because the other side of the race
never takes the lock at all.** `start_or_existing` serializes on the registry's
own `threading.Lock`, which orders reservations against each other and against
nothing else. Two locks that never overlap impose no order between them, so
this interleaving survives a guard that is perfectly placed:

1. the edit takes `campaign_lock(cid)` and finds no live run — correct, there
   is none;
2. the turn reserves (registry lock only) and reads the scene, unlocked,
   catching the transcript **mid-edit**;
3. the edit publishes and releases.

The run then generates from history that never existed, and no participant did
anything wrong. Registry membership and transcript state have to become
observable in one order, which means **reservation and the initial scene
snapshot happen inside `campaign_lock(cid)` too** — the same lock, so the two
sides genuinely serialize. Then either the edit wins (the turn snapshots the
edited transcript) or the turn wins (the edit's guard sees the run and 409s),
and there is no third outcome.

The lock is reentrant, so the handler taking it around reserve-and-snapshot
costs nothing where a mutator takes it again underneath.

**Keep the hold to the reservation and the synchronous scene writes — release
it before composing the prompt.** "Not across the provider call" is too weak a
line, because the expensive part starts earlier: `store.context.compose_turn`
can issue an **external embeddings request** when semantic recall is on. Held
under `campaign_lock(cid)`, a slow embeddings endpoint stalls the campaign's
cross-process write lock until its network deadline — every other mutation in
that campaign queues behind it or 409s, and the user sees a campaign that has
seized up because one turn is thinking.

Releasing early is safe precisely because of what the reservation already did:
once it is published, the guarded mutators refuse to touch the scene, so the
snapshot the run composes from cannot move underneath it. The lock establishes
the ordering; the reservation sustains it. Holding longer buys nothing and
costs the whole campaign.

**Do not add `routes/runs.py` to `store.locks.DOMAIN_MODULES`.** An earlier
revision of this plan said to classify it, which is wrong and would fail the
backend gate: `_survey()` collects modules that mutate the campaign *store*,
`routes/runs.py` maintains an in-memory registry and mutates nothing on disk,
so declaring it makes it a phantom and
`test_the_declaration_has_no_phantom_modules` fails naming it. The lock is
taken by the **handler**, which is where it belongs — the domain lists describe
store mutators, and the registry is not one.

Test it as an interleaving, not a sequence: hold the edit inside its lock,
attempt the reservation from another thread, and assert the reservation blocks
until the edit completes and then snapshots the edited transcript.

- [ ] **Step 3b: One wire contract for the attempt id, across all five routes**

Nothing so far says how the id actually travels, and the four request models
these routes use — `ChatTurn`, `RetryBody`, `RegenerateBody`, `ProposalAction`
(`routes/models.py:481, 144, 139, 171`) — have no field for it. Without a
single contract the id cannot reach `start_or_existing` on a retry, and the
idempotency this entire plan rests on is decoration: a client that lost the
run frame re-POSTs, gets a *new* attempt, and both the duplicate-suppression
and the "did my send land?" recovery fail at once.

**Send it as a header: `X-Grimoire-Attempt`.** A body field would mean editing
four pydantic models (one of which, `RetryBody`, is otherwise empty) and would
still not cover a route whose body shape changes later; the header is one
`Header(default=None)` parameter, identical on all five, and it survives the
pydantic-v1/v2 constraint untouched because it is not a model field at all.

Rules, all five routes alike:
- Absent or malformed → generate one server-side and proceed. Older clients and
  `curl` must keep working; they simply get no idempotency, which is what they
  have today.
- Present → it is the attempt id, verbatim. The server never rewrites it.
- The run frame echoes it, so a client that generated one can confirm the
  server agreed rather than assuming.

Test the transport end to end on at least two of the five, and specifically
test that **the same header twice yields one run**, not two — that is the
property, and it cannot be observed from either route in isolation.

- [ ] **Step 4: Guard the width-crossing create — in the ROUTE, not the store**

`_create_scene` is a store-layer function with no `app` and no registry, and
the registry deliberately lives on `app.state`. Importing route state into
`store/scenes/lifecycle.py` would invert the dependency direction and cannot
pick the right app instance anyway — `AGENTS.md` lists exactly that shape among
its tripwires.

So the check belongs in `post_scene` (`scenes.py:92`): take
`campaign_lock(cid)`, consult `request.app.state.runs` for **any** held
exclusion key in that campaign, and call the (re-entrant) store mutation from
inside that hold. Refuse only when the create would cross the width boundary —
`serialize._numbering(cid)` reports `number` and `width`, and
`len(str(number)) > width` is the branch that calls `repad` — with
`MIN_WIDTH = 3` that first bites at 999 → 1000, not 99 → 100, which renames
every scene in the campaign rather than only the busy one.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
make check-py check-lint check-mypy && make baseline
git add backend/src backend/tests/test_scene_freeze.py lint-baselines
git commit -F - <<'MSG'
While a run holds a scene, that scene's shape does not change

Reserving before the snapshot closed the race before a run; this closes the
ones during it. Edits, retcons and cuts gate on `rolling`, not sceneLocked,
and their routes know nothing of a registry -- so they could rewrite the
transcript underneath a ten-minute absorb, which would then spend its entire
budget producing a review its watermark refuses.

The width-crossing create is refused too: _create_scene crossing 99 -> 100
calls repad, which renames EVERY scene in the campaign. Refusing the explicit
rename route never covered that second, automatic path.
MSG
```

---

## Task 6: The frontend run registry

**Files:**
- Create: `frontend/src/runs/RunRegistryProvider.tsx`, `useSceneRun.ts`,
  `RunRegistryProvider.test.tsx`
- Create: `frontend/src/testkit/runMocks.tsx`
- Modify: `frontend/src/api/client.ts:186`
- Modify: `frontend/src/main.tsx` — wrap `BrowserRouter` in
  `RunRegistryProvider`. **Without this the feature does not exist in
  production**: the provider tested in isolation is not above the real router,
  so `useSceneRun` has no provider and navigation preserves nothing.
- Modify: `frontend/src/routes/CampaignView.tsx`

**Interfaces:**
- Consumes: the four run routes (Task 4).
- Produces: `useSceneRun(cid, sid) -> { run, frames, busy, start, cancel }`.
- Produces: `ApiError.body` — the decoded response, so a 409 carries `run_id`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/runs/RunRegistryProvider.test.tsx
it("keeps a run alive across navigation and re-attaches to the right one", async () => {
  const { user } = renderCampaign();
  await startTurn("scene-a", "Mara waits.");
  await user.click(screen.getByRole("button", { name: /winifred/i }));
  // Narration generated while we are LOOKING AT ANOTHER SCENE. This is the
  // text the whole feature is about, and it is the only text that proves
  // replay happened: asserting on "Mara waits." would prove nothing, because
  // that is our own submitted post and the scene refetch carries it back
  // whether or not a single replayed delta was applied. An implementation that
  // requests the right offset and then drops every frame it receives passed
  // the earlier version of this test.
  await emitWhileAway("scene-a", "The lamps are already lit.");
  await user.click(screen.getByRole("button", { name: /mara/i }));
  expect(await screen.findByText(/The lamps are already lit\./)).toBeInTheDocument();
});

it("derives the resume cursor from the wire index, not the event count", async () => {
  // The defect this catches: `parseSSEChunk` discards comment frames, so a
  // provider counting the events it surfaced undercounts by every heartbeat
  // and resumes one frame early, duplicating text mid-reply. Drive the REAL
  // parser and provider -- a test helper that recomputes the cursor its own
  // way would agree with a broken implementation and prove nothing.
  const { user } = renderCampaign();
  await startTurn("scene-a", "Mara waits.");
  await emitFrames("scene-a", [
    { index: 0, frame: sse({ delta: "The lamps " }) },
    { index: 1, frame: ": heartbeat\n\n" },
    { index: 2, frame: sse({ delta: "are already lit." }) },
  ]);
  await user.click(screen.getByRole("button", { name: /winifred/i }));
  await user.click(screen.getByRole("button", { name: /mara/i }));
  expect(lastStreamRequest().from).toBe(3);
  expect(await screen.findByText(/The lamps are already lit\./)).toBeInTheDocument();
  expect(screen.queryByText(/are already lit\.are already lit\./)).toBeNull();
});

it("disables the composer while the scene has a live turn, and re-enables after", async () => {
  renderCampaign();
  await startTurn("scene-a", "Mara waits.");
  expect(screen.getByPlaceholderText(/speak your intent/i)).toBeDisabled();
  await landRun("scene-a");
  expect(screen.getByPlaceholderText(/speak your intent/i)).toBeEnabled();
});

it("does not disable the composer for a background run", async () => {
  // rolling summary and scene break fire after EVERY turn; if they took the
  // scene's slot the app would lock itself.
  renderCampaign();
  await landRun("scene-a");
  await fireBackgroundRun("scene-a", "rolling-summary");
  expect(screen.getByPlaceholderText(/speak your intent/i)).toBeEnabled();
});

it("does not render one scene's frames in another scene's view", async () => {
  renderCampaign();
  await startTurn("scene-a", "Seraphine.");
  await startTurn("scene-b", "Winifred.");
  await user.click(screen.getByRole("button", { name: /mara/i }));
  expect(screen.queryByText(/Winifred/)).not.toBeInTheDocument();
});
```

The composer's placeholder is **`"Speak your intent…"`**
(`CampaignView.tsx:3533`), or `"Direct the scene (optional)…"` when the scene
is pcless. Query the real copy; do not invent a placeholder and do not edit
production copy to match a test.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/runs/RunRegistryProvider.test.tsx`
Expected: FAIL — the module does not exist

- [ ] **Step 3: Make `streamPost` retain the error body**

`client.ts:186` currently builds `new ApiError(res.status, data.detail, data.kind)`
and drops the decoded body, so `run_id` is lost and `kind === "run_in_flight"`
cannot attach to anything. Keep the payload on the error.

**The body is FLAT, not nested — check this against `main.py` rather than
against FastAPI's documented default.** This app installs its own
`HTTPException` handler (`main.py:294`) whose whole job is to unwrap a dict
detail: `content = exc.detail if isinstance(exc.detail, dict) else {"detail":
exc.detail}`. So `raise HTTPException(409, detail={"kind": "run_in_flight",
"run_id": ...})` puts `kind` and `run_id` at the **top level** of the response
body, and `data.kind` is exactly right. Every structured error already in this
tree is asserted that way — `test_llm_error_status.py:205` and
`test_retcon_routes.py:121` both read `r.json()["kind"]`.

An earlier revision of this plan claimed the opposite and prescribed
`data.detail?.kind ?? data.kind` normalization. That was wrong, and it was
wrong in the expensive direction: the "normalization" would have read
`data.detail` — a string or undefined — found no `kind` on it, fallen through
to `data.kind`, and worked by accident, leaving a plausible-looking defensive
line that documents a wire format this app does not produce.

So: keep the decoded body on the error, read `kind` and `run_id` from its top
level, and write the frontend fixture by copying a real response body out of a
backend test rather than hand-authoring one.

- [ ] **Step 4: A 409 is busy-state, not an adoption**

A rejected send never had its prompt appended -- that is what reserving before
the first mutator buys. So the loser of two concurrent tabs must **not** attach
to the winner's stream: it would render another tab's reply while its own
prompt went nowhere, which is exactly the "did my send land?" ambiguity the
attempt id exists to end. On `run_in_flight`, compare the returned `run_id`'s
attempt against our own: re-attach only on a match, otherwise keep the text in
the composer and show the scene as busy.

- [ ] **Step 5: Write the provider and mount it in `main.tsx`**

Above `BrowserRouter`, not inside it — a provider under the router remounts on
navigation, which is the thing this exists to prevent. Cover the real entry
composition, not only an isolated render.

It holds `Map<runId, {subject, cls, frames, consumed}>` **plus a
`Map<subject, attemptId>` of sends that have not yet resolved to a run id.**

**The attempt id goes into provider state BEFORE the POST is issued.** Creating
the entry when the leading `run` frame arrives is too late, and it fails in the
one case the mechanism exists for: if the server accepts the request and
mutates the scene but the response is lost before that frame, the attempt id
lives only in hook state — which unmounts on navigation or backgrounding. On
recovery the client then cannot call `GET .../run?attempt=...`, which is the
only unambiguous way to learn whether *its* send landed, and #95's ambiguity is
back. Test a response lost before the first frame, then a remount.

**And it must actually be SENT.** Storing the id and never putting it on the
wire is the failure this whole mechanism dies of quietly: the server, seeing no
`X-Grimoire-Attempt`, generates its own, so the id the provider is holding
names nothing. `GET .../run?attempt=` then reports no run for a turn that ran
perfectly, and the "did my send land?" recovery answers *no* when the truth is
yes — after which a retry submits the turn a second time. Every symptom points
at the backend and none of it is there.

`streamPost` (`client.ts:186`) and the five producing wrappers cannot carry a
header today; their signatures have to grow one. Do it in one place — an
options argument threaded through `streamPost` — rather than five ad-hoc
parameters, and default it to absent so nothing else in the app changes.

Assert it on the **real outgoing request**, not on a wrapper's arguments: the
test reads the header off the intercepted fetch and compares it to the id in
provider state. A test that checks "we passed the id to the wrapper" passes
against a wrapper that drops it, which is precisely the bug.

**Keep the submitted prompt text alongside the attempt**, until the outcome
proves it durable. The id alone cannot honour `post_returned`: if the response
is lost before the run frame, the provider then fails, and the backend takes
the player's post back off the transcript, recovery finds a failed run whose
error says the post was returned — and the text exists nowhere. Not in the
transcript, not in the unmounted component. Held in provider state, it goes
back in the composer. Test terminal recovery with `post_returned: true` after
a remount.

The consumed index is
persisted **per run as frames are read** and resume asks for `consumed + 1`.
Never resume from `next_index` — that is the live tail, and using it drops
everything generated while the client was away.

- [ ] **Step 6: Adopt on mount and on `visibilitychange`**

**Three outcomes, not two: live, terminal, and `run_gone`.** The third is the
one an implementation forgets, and it is reachable by ordinary use. A turn
finishes while the phone is locked; nobody attaches; `REAP_SECONDS` passes; the
tab comes back. Discovery, poll and stream all now 404 with `run_gone` for an
id the still-mounted component is holding — and unlike a cold mount, its
transcript predates the landed reply. Surfacing the 404 as an error would
report a lost turn that in fact landed perfectly; ignoring it leaves the
composer locked over a stale transcript.

So `run_gone` **refetches the scene and settles**, exactly like terminal
discovery. The spec requires this branch for turns; it is the same handler, and
the two cases differ only in that one of them has no run record left to read.
Test a scene hidden longer than the reap window with a turn that landed while
it was away, and assert the new message is on screen and the composer is live.

**But "settle" must not mean "discard", and for one case it would.** The branch
above reasons from a run that *landed*. A run can also have **failed with
`post_returned: true`** — the backend took the player's post back off the
transcript — and then been reaped before the tab returned. Now: the structured
outcome that would have said so is gone with the record, and the refetched
transcript is *correctly* missing the post. An unconditional settle drops the
provider's held text, which at that moment is the only copy in existence. The
player watched their words vanish, and every artifact agrees nothing was ever
sent.

The refetch cannot distinguish the two cases on its own — "the post is absent"
means both "it was rolled back" and "it never landed", which is exactly #95's
ambiguity resurfacing after the evidence expired.

**Matching the submitted text against the transcript does not resolve it, and
an earlier revision of this plan said it did.** Two ways that fails, and both
are ordinary:

- *A player repeats themselves.* "I wait." submitted twice in a session means
  the tail matches an **earlier** turn, so the newer attempt is declared
  durable and its text discarded — the exact loss the rule exists to prevent,
  now triggered by the remedy.
- *A landed turn does not end with the post.* It ends with assistant
  narration, so "the tail matches the submitted text" is false for precisely
  the case that should settle.

Text is not an identifier. **The attempt id has to outlive the run record**, so
correlation survives the reap: when the post is appended, the attempt is
recorded durably alongside it — `store.commits` is the existing shape for this
(`reserve`/`lookup`, already used for chronicle idempotency at
`scenes.py:2629`), and it survives both the reap window and a restart, which
in-memory run state does not. Recovery then asks a question with a definite
answer — *was attempt X ever appended to this scene?* — instead of guessing
from prose.

**And where the answer is not definite, keep the text.** Any outcome other than
a clear "this attempt is durable" — no record, an unreadable one, a scene that
no longer exists — restores the prompt to the composer and leaves the attempt
unresolved. Choosing wrongly in that direction costs the player one duplicate
they can see and delete; choosing wrongly the other way costs them their words
with no trace. Ambiguity resolves toward the recoverable error, always.

Test the hidden-past-`REAP_SECONDS` case for a **failed** turn as well as a
landed one, and assert the composer holds the text again.

Attach only to a **live** run. A terminal run is reported for state so the view
can settle rather than spin, and never replayed: a fresh mount has no cursor
and its scene fetch already contains the persisted reply, so replaying from 0
renders it twice.

**Discovering a terminal run must refetch the scene**, not merely unlock the
composer. The two ways a client learns of a terminal run differ in what the
client already holds. On a cold mount the scene fetch happens anyway and
already carries the reply. But on `visibilitychange`, and on a live stream
that ends, the component has been mounted the whole time holding a transcript
from *before* the run — the reply was persisted by the backend while the tab
was hidden or after the last frame, and nothing in the client has fetched it.
Unlocking alone leaves a settled composer above a transcript missing its
newest turn, which reads exactly like the turn was lost. Refetch on every
terminal discovery; the cold-mount case is then merely redundant, which is
cheap, rather than the only case that works. Test a hidden-then-visible
transition across a run that completed while hidden, and assert the new
message is on screen.

- [ ] **Step 6b: Rewire Stop to the cancel endpoint**

Today's Stop button aborts the `fetch` — which, once the run outlives the
request, stops the *client's view* of a run that keeps generating, keeps
holding the scene's exclusion key, and keeps the composer locked. The button
would then read as broken in the one situation a user reaches for it.

Point it at `POST .../runs/{run_id}/cancel` and let the terminal frame (or the
terminal state the poll discovers) do the settling, rather than settling
locally on abort. Detaching the local reader stays as the second half — the
request is aborted *after* the cancel POST is issued, not instead of it.

Cancel is a request, not a guarantee: a run inside a provider call ends when
that call unwinds. Leave the button disabled and the state `cancelling` from
the POST until a terminal frame arrives, so a second press cannot stack
cancels, and the UI never claims the run stopped before the backend says so.

Test: Stop issues the cancel POST; the composer stays locked until the
terminal frame lands, and unlocks then.

- [ ] **Step 7: Extend `sceneLocked` to the exclusion key**

Key on `turn`/`review` specifically, not "any run for this scene."

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd frontend && npm run test:coverage`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
make check-web check-eslint && make baseline
git add frontend/src lint-baselines
git commit -m "A run registry above the router, so navigation does not kill a turn

The stream lived in the scene view's state, so leaving unmounted and aborted
it. Runs are addressed by id and resumed from the client's OWN consumed index
+ 1: next_index is the live tail, and resuming there would drop every frame
generated while the client was away."
```

---

## Task 7: Android — foreground service and notification

**Files:**
- Create: `android/app/src/main/java/app/grimoire/RunNotifier.kt`
- Modify: `ServerService.kt`, `MainActivity.kt`, `AndroidManifest.xml`
- Modify: `backend/src/grimoire/runner.py` (terminal hook)
- Modify: `backend/src/grimoire/routes/runs.py` (**reservation** hook — see
  below; the runner alone cannot emit the live-count callback this task needs)

**Files (additional):**
- Modify: `android/app/src/main/python/android_entry.py` — accept and forward
  the run callbacks.
- Modify: `android/app/src/main/java/app/grimoire/ServerRuntime.kt` — pass them
  in alongside the existing `PortCallback`.

**Interfaces:**
- Consumes: `runner`'s terminal transition (Task 3).
- Produces: `ServerRuntime.onRunsChanged(live: Int)` and
  `ServerRuntime.onRunTerminal(runId, state, campaignName, sceneTitle, cid, sceneIdentity)`.

**Both callbacks are invoked after run bookkeeping, each inside its own
fail-soft boundary.** A cross-language call can raise — foreground promotion
refused, notification construction failing — and neither outcome may touch the
run. Inside Task 3's guarded block it would flip a successfully persisted run
from `landed` to `failed`; outside it, the exception escapes into the lifespan
task group and cancels sibling runs. So: bookkeeping first, then each callback
wrapped in its own try/except that logs and swallows. A notification is the
least important thing a terminal run does.

**These have to be plumbed, not just declared.** `android_entry.start_server`
today takes only a `PortCallback` and builds `create_app()` without retaining
any Kotlin callback, so nothing in the backend can reach `ServerRuntime`. As
written in the first draft, the runner had no path to either method and
foreground promotion and completion notifications would simply never fire on
device — with every unit test still green, because none of them run the
Android entry point. `start_server` gains the two callbacks and stashes them
where `runner` can find them (`app.state`, beside the registry).

- [ ] **Step 1: Register notification channels before anything posts**

On Android 8.0+ — every supported device at `minSdk 26` — posting to an
unregistered channel suppresses a completion notification and makes the
**foreground** notification invalid, which would defeat the process-lifetime
guarantee this whole feature rests on. Two channels created at service start:
an ongoing one (low importance, no sound) and a completions one.

- [ ] **Step 2: Add the manifest entries**

`FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_DATA_SYNC`, `POST_NOTIFICATIONS`,
and `android:foregroundServiceType="dataSync"` on the service. `dataSync` is
the honest type; `shortService` caps at three minutes, which a turn can exceed
and an absorb routinely does.

- [ ] **Step 2b: Request `POST_NOTIFICATIONS` at runtime**

Declaring it in the manifest is not enough on Android 13+ at `targetSdk 34`:
on a fresh install it is denied until the activity asks. Without the request,
completion notifications are permanently off for every new user and the
"degraded mode" below stops being a user choice and becomes the default.
`MainActivity` requests it once, on first launch, via
`registerForActivityResult(RequestPermission())`.

- [ ] **Step 3: Promote and demote**

Promote when the registry gains its first live run, demote when it has none.

**"Gains a live run" means at reservation, in `start_or_existing` — not when
the runner starts.** The registry goes live before the handler builds its
prompt, and that setup is not always fast: context construction can involve
semantic recall. A phone locking during it would find the service unpromoted
and the process reclaimable before the detached runner ever began — losing the
turn in precisely the window this feature exists to protect. The matching
demotion belongs on the pre-start release path too. Test with a deliberately
held setup.

**Which means `onRunsChanged` cannot be emitted from `runner.py`**, and the
file list above says so. The runner is not entered until after the handler's
synchronous setup, and a run reserved by a route that then early-exits — a
validation failure, a 409 discovered late — is released without the runner ever
being entered at all. A callback living only where the runner can reach it
therefore misses both transitions this step exists for: it promotes too late,
and on the early-release path it never demotes, leaving a foreground service
pinned by a run that no longer exists.

So the live-count callback belongs to the **registry**, which owns every
transition into and out of live: `start_or_existing` when the map gains its
first live run, and the release path — both the pre-start release and the
terminal one — when it drops to none. `runs.py` grows a `on_live_change`
sink that the app sets at startup and that Task 3's runner does not touch.
`onRunTerminal` stays on the runner, where the outcome is known. Both keep
their own fail-soft boundary as described above.

Test the held-setup case directly: reserve, do not start the runner, assert the
promotion callback already fired; then release without starting, and assert the
demotion fired. That pair fails against any implementation that hangs the
callback off the runner, which is the whole point of writing it.
**If `POST_NOTIFICATIONS` is denied, still promote** — the service runs, its
notification simply is not shown, and runs survive backgrounding as designed.
Only the completion notification is lost. A denied permission is not a reason
to skip promotion.

- [ ] **Step 4: Post the terminal notification**

Text: `New Post in <Campaign>: <Scene>` / `Error on <Campaign>: <Scene>`.
Labels are captured at **run start**, not at terminal — a scene deleted under a
live run is supported, and resolving the title afterwards would find nothing in
exactly the case that most needs the error notification. A `cancelled` run
posts **nothing**: only success and error text exist, so a deliberate Stop
would otherwise report an error for something the player chose to stop.
Suppress while the activity is resumed. The notification id derives from the
run id so two completions cannot collapse.

- [ ] **Step 5: Route the tap by identity**

The intent carries `cid` and the scene **identity**, resolved to a current
`sid` when the tap is handled, falling back to the campaign when the scene is
gone. A notification outlives the moment it was posted; a stored `sid` opens a
dead route after a delete or rename.

- [ ] **Step 6: Build and verify by hand**

Run: `make apk`
Then on device: start a turn, lock the phone, confirm the ongoing notification,
unlock and confirm the reply landed and the completion notification appeared.

**Then confirm the ongoing notification is GONE.** Promotion is the half that
gets tested because it is the half you notice; demotion is the half that
breaks. `onRunsChanged(0)` has to reach the Kotlin side and actually call
`stopForeground` — and if it does not, `make check-apk` passes, every automated
test passes, this procedure passes, and the user is left with a permanent
"grimoire is running" notification over a process that never gets reclaimed.
That is a worse daily experience than the bug this whole feature fixes. Watch
it disappear when the run lands, and again after a **cancelled** run, which
takes the other release path.

**Then tap one — twice, in the two cases Step 5's routing contract exists
for.** Confirming a notification *appears* proves nothing about where it goes,
and tap routing is the half with no automated coverage at all: a missing
intent extra, a stored `sid` instead of an identity, or a cold-start handler
that drops the payload all leave a notification that looks perfect and opens
the wrong thing.

1. Start a turn, background the app, **let it finish** and post its
   notification. *Then* rename the scene, then tap. The renamed scene must open
   — the rename minted a new `sid`, which is what resolving by identity buys.

   The order matters and an earlier revision of this plan got it backwards, in
   a way that contradicted its own Task 5b: renaming *while the run holds the
   scene* is exactly what `put_scene`'s freeze guard now refuses with a 409, so
   that procedure could never have been carried out and would have "verified"
   routing by never exercising it. Rename after the run is terminal and the
   slot is released.
2. Start a turn, background, let it finish, **delete the scene**, tap the
   notification. The campaign must open — not a crash, not an empty scene view.
   (Deletion is likewise refused while the run holds the scene.)

Do both from a **cold start** (swipe the app away first), which is the path
that actually breaks: a warm activity often has the state a cold one has to
reconstruct from the intent.

There is no automated coverage for any of this beyond `make check-apk`; say so
rather than implying otherwise.

- [ ] **Step 7: Commit**

```bash
make check-apk
git add android backend/src/grimoire/runner.py backend/src/grimoire/routes/runs.py
git commit -m "Keep the process alive while a run is live, and say when it lands

Channels are registered before anything posts: on 8.0+ an unregistered
channel makes the FOREGROUND notification invalid, which would defeat the
process-lifetime guarantee. A denied POST_NOTIFICATIONS still promotes -- only
the completion notification is lost.

Labels are captured at run start and taps carry the scene identity, because a
notification outlives the moment it was posted and a deleted scene is exactly
when the error notification fires."
```

---

## Task 8: Documentation the change invalidates

- [ ] **Step 1: Update `docs/android-architecture.md`**

§4 says foreground promotion during generation is unimplemented Phase 3, and
risk 6 (process killed mid-stream → reply lost) is unmitigated. Both are now
false. `ServerService.kt`'s docstring says the same — it was rewritten in Task
7. Neither file is in `test_docs_guard.py`'s `DOCS` tuple, so nothing fails if
they are forgotten, which is exactly why they are named here.

- [ ] **Step 2: Add a `CLAUDE.md` working note**

One paragraph: runs live on `app.state`, not module scope; disconnect no longer
cancels; and **the five scene-turn handlers** — `post_chat`, `post_retry`,
`post_regenerate`, `post_replay_turn`, `post_roll_proposal` — are synchronous
and start runs through the portal.

Say five, not six. `post_opener` is the sixth synchronous streaming handler but
it is a `draft`, and Phase 1 leaves every draft route unchanged. Writing "all
six" would misstate production behavior the day it lands and mislead whoever
picks up Phase 3.

- [ ] **Step 2b: Amend the merged spec where this plan overruled it**

The spec (merged in #393) says at its §"frozen campaign" that the compatibility
sweep runs the identity backfill and that **`snapshot.json` will move, and that
is expected**, because identity would appear in the scene record. This plan
deliberately does the opposite: identity is filtered out of `read_scene`'s
`meta`, and the snapshot must **not** move.

The plan is right and the spec is stale. Identity is a fresh `uuid4()` per
scene, so if it reached the snapshot the frozen fixture would produce different
bytes on every regeneration — destroying the one property that fixture exists
for, which is being old and stable. Filtering it out keeps the sweep meaningful
and makes an unchanged `snapshot.json` a real assertion rather than a chore.

But leaving both documents standing is the actual hazard, and it is procedural
rather than technical: `CLAUDE.md`'s last gate reviews the diff **against the
originating spec**, asking whether the change implements it. With the spec
unamended, that gate reads a deliberate decision as drift and either reopens a
settled question or, worse, gets talked into "fixing" the implementation to
match the stale text.

So amend the spec in place — the paragraph asserting the snapshot moves becomes
one recording that identity is internal-only and the snapshot must not move,
with the uuid reasoning above and a pointer to this plan. Keep it short and
keep it dated; the point is that one document is authoritative, not that the
history is erased.

- [ ] **Step 3: Run the full gate and commit**

```bash
make check
git add docs CLAUDE.md
git commit -m "Document what detached runs changed"
```

---

## Out of scope for Phase 1

State plainly rather than leaving it implied:

- `post_absorb`, `post_audit`, `post_dossiers` keep today's synchronous
  behavior. Phase 2.
- `post_rolling_summary` / `post_scene_break` keep their client trigger.
  Phase 3 adds the server-side one *in addition* — `askAfterPost` has eight
  call sites and only one is the end of a generated turn.
- The 19 `draft` routes keep returning their results directly. Phase 3.
- **`test_usage_guard.py`** recognizes an LLM call by the receiver name
  `client` inside `routes/`. Phase 1 leaves the meter at its call sites, so the
  guard still sees everything. Phase 3 moves enough that the guard must be
  re-examined; a guard that fails open over the token ledger is worse than no
  guard, and it must be shown failing on a deliberately unmetered run.
