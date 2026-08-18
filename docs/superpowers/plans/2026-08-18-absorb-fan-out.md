# Absorb fan-out (spec steps 1–3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ending a scene take one round of concurrent LLM calls instead of ten sequential ones, without turning a slow campaign into a 502.

**Architecture:** Three ordered changes to `routes/scenes.py` and its test harness. First the test fakes stop answering by call order (a precondition — nothing concurrent can be tested against an ordered script). Then the time budget is re-derived while absorb is still sequential, so its new failure semantics are provable in isolation. Only then do the phases run under one `asyncio.gather` bounded by a semaphore. No prompt template changes; no change to what `PUT /chronicle` writes.

**Tech Stack:** Python 3.11+/3.14, FastAPI, pytest, asyncio. Backend only — no frontend or Android work in this plan.

**Spec:** `docs/superpowers/specs/2026-08-18-absorb-performance-design.md`

## Scope

This plan implements **steps 1–3** of the spec's Staging section only. Steps 4–6 (batching the per-NPC phases, splitting extraction into three prompts, the citation contract and Android foreground promotion) each change model-visible behaviour and get their own plans. Steps 1–3 deliver the entire latency win and touch no prompt.

## Global Constraints

Copied verbatim from `CLAUDE.md` — every task's requirements implicitly include these.

- **Run the gate with `make check`.** Individually: `make check-py` (pytest), `check-lint` (ruff), `check-pydantic1`.
- **Never use a real world/campaign/character name** in a test fixture, commit message or doc — invented names only. Reuse the codebase's existing placeholders: Seraphine, Mara, Winifred, Realm, Saltmarch. (Existing fixtures contain names that predate this rule; do not propagate them into anything new.)
- **pydantic usage stays v1/v2-agnostic**: plain `BaseModel` fields only, dump via `routes.common._dump`. No `model_dump()`, `Field`, validators, `ConfigDict`.
- **Imports in `backend/src/grimoire/` are all at module scope and the module graph is acyclic** (`test_import_guard.py`).
- **Every store write goes through `store.atomic`** (`test_atomic_guard.py`). Nothing in this plan writes to the store — absorb is `@computes_only`.
- Guard markers are `# atomic-ok: <reason>` etc.; a marker with no reason fails deliberately.
- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Do **not** put a model identifier in a commit message or any committed artifact.

---

### Task 1: An inline cassette, so tests can answer by request shape

**Why first:** `FakeOpenRouterComplete([a, b])` answers by call order. Under a fan-out, call order is nondeterministic, so ~14 absorb tests would fail for reasons unrelated to the change under test. Cassettes answer by *what the request looks like*. `from_cassette(name)` only loads from a file, and most of these tests need their own one-off bodies — so they need an inline constructor.

**Files:**
- Modify: `backend/tests/llm_fakes.py` (add `from_entries`, export it)
- Test: `backend/tests/test_llm_fakes.py`

**Interfaces:**
- Consumes: `Cassette(data: dict, name: str)` and `FakeLLM(cassette=...)`, both already in `llm_fakes.py`.
- Produces: `from_entries(entries: list[dict], name: str = "<inline>") -> FakeLLM` — used by Tasks 2 and 3 and by the follow-on plans.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_llm_fakes.py`:

```python
async def test_from_entries_answers_by_shape_not_order():
    """An inline cassette is order-independent: the same two requests get the
    same two replies whichever way round they arrive."""
    entries = [
        {"when": {"system_contains": "absorbing a completed"}, "reply": "EXTRACTION"},
        {"when": {"system_contains": "auditing a completed"}, "reply": "AUDIT"},
    ]
    fake = llm_fakes.from_entries(entries)
    audit_first = await fake.complete([{"role": "system", "content": "auditing a completed scene"}], {})
    extraction_second = await fake.complete(
        [{"role": "system", "content": "absorbing a completed scene"}], {})
    assert audit_first == "AUDIT"
    assert extraction_second == "EXTRACTION"


async def test_from_entries_misses_loudly():
    """A request no entry covers raises rather than falling through to a
    default — the whole reason a cassette is safer than a script here."""
    fake = llm_fakes.from_entries([{"when": {"system_contains": "absorbing"}, "reply": "X"}])
    with pytest.raises(llm_fakes.CassetteMiss):
        await fake.complete([{"role": "system", "content": "something else"}], {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_llm_fakes.py -k from_entries -v`
Expected: FAIL with `AttributeError: module 'tests.llm_fakes' has no attribute 'from_entries'`

- [ ] **Step 3: Write minimal implementation**

In `backend/tests/llm_fakes.py`, beside `from_cassette`:

```python
def from_entries(entries: list[dict], name: str = "<inline>") -> FakeLLM:
    """A fake replaying entries given inline, for a test whose bodies are its
    own rather than the shared `campaign_flow` fixture's.

    The point is not brevity, it is order-independence. A scripted fake answers
    call 1 then call 2; absorb's calls run concurrently, so "call 1" names
    nothing. Matching on the prompt that OWNS each call keeps the assertion
    about which reply the code got, rather than about which order it asked in.
    """
    return FakeLLM(cassette=Cassette({"entries": entries}, name))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_llm_fakes.py -k from_entries -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/tests/llm_fakes.py backend/tests/test_llm_fakes.py
git commit -m "Answer the fake by request shape, not by call order

A scripted fake answers call 1 then call 2. Absorb is about to make its
calls concurrently, at which point \"call 1\" names nothing. from_entries
gives a test the cassette matching from_cassette already had, without
requiring a fixture file for bodies that belong to one test."
```

---

### Task 2: Migrate the order-dependent absorb tests

**Files:**
- Modify: `backend/tests/test_routes.py` — the 14 multi-element sites listed below
- Test: the same file (these *are* the tests)

**Interfaces:**
- Consumes: `from_entries` from Task 1.
- Produces: nothing new. Behaviour-preserving by construction — every assertion stays byte-identical; only the fake's dispatch changes.

The sites, all in `backend/tests/test_routes.py`:

| lines | current |
|---|---|
| 4349, 4760, 4781, 4802 | `FakeOpenRouterComplete([_EXTRACTION, _DOSSIER])` |
| 4550, 4580, 4604, 4675 | `FakeOpenRouterComplete([_EXTRACTION, _DOSSIER, _DOSSIER])` |
| 5704, 5766 | `FakeOpenRouterComplete([ABSORB_JSON, AUDIT_OK])` |
| 5740, 5752 | `FakeOpenRouterComplete([ABSORB_JSON, bad])` |
| 6424 | `FakeOpenRouterComplete([ABSORB_JSON, <dossier>, VOICE_OK, AUDIT_OK])` |

**Leave alone:** every single-element `FakeOpenRouterComplete([X])` (5716, 5728, 5777, 5784, 6709, 6773) — the last reply repeats, so those already answer every call the same way and are order-independent. `test_llm_fakes.py:42` deliberately tests ordered behaviour and must keep it.

- [ ] **Step 1: Add the shared matcher constants**

The four prompts absorb issues are identified by a phrase from the system prompt that owns each call. Confirm each against the live template before using it:

Run: `cd backend && head -1 ../templates/absorb/system.j2 ../templates/audit/system.j2 ../templates/dossier/system.j2 ../templates/voice_drift/system.j2`

Then add near the other absorb constants in `test_routes.py`:

```python
# The phrase from each system prompt that identifies which call a request is.
# Deliberately a phrase from the prompt that OWNS the call, not a keyword that
# might appear in a transcript: test_llm_fakes.py renders every real template
# and fails if one of these stops matching, which is the point.
_WHEN_EXTRACTION = {"system_contains": "You are absorbing a completed role-play scene"}
_WHEN_AUDIT = {"system_contains": "You are auditing a completed role-play scene"}
_WHEN_DOSSIER = {"system_contains": "You are updating a game master's dossier"}
_WHEN_VOICE = {"system_contains": "You are checking one character's dialogue"}
```

- [ ] **Step 2: Run the absorb tests to record the green baseline**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_routes.py -k "absorb or dossier or audit or voice" -q`
Expected: PASS. Write the count down — Step 5 must match it exactly.

- [ ] **Step 3: Migrate each site**

Mechanical, one site at a time. Each `FakeOpenRouterComplete([A, B])` becomes a `from_entries` whose entries carry the same bodies keyed by the call that should receive them. For example, line 4349:

```python
# before
fake = FakeOpenRouterComplete([_EXTRACTION, _DOSSIER])
# after
fake = from_entries([{"when": _WHEN_EXTRACTION, "reply": _EXTRACTION},
                     {"when": _WHEN_DOSSIER, "reply": _DOSSIER}])
```

and line 6424:

```python
# before
FakeOpenRouterComplete([ABSORB_JSON, "<a dossier paragraph>", VOICE_OK, AUDIT_OK])
# after
from_entries([{"when": _WHEN_EXTRACTION, "reply": ABSORB_JSON},
              {"when": _WHEN_DOSSIER, "reply": "Steady, and owed a debt at Saltmarch."},
              {"when": _WHEN_VOICE, "reply": VOICE_OK},
              {"when": _WHEN_AUDIT, "reply": AUDIT_OK}])
```

The `[_EXTRACTION, _DOSSIER, _DOSSIER]` sites collapse to **two** entries, not three: both dossier calls match the same entry and get the same reply, which is what the duplicated element meant. Where a test asserts different replies for two NPCs, add `user_contains` with that NPC's name to split them.

Import `from_entries` alongside the existing names at the top of the file.

- [ ] **Step 4: Update the `FakeOpenRouterComplete` docstring**

Its docstring currently sells the ordered list as the way to drive absorb. In `backend/tests/llm_fakes.py`:

```python
class FakeOpenRouterComplete(FakeLLM):
    """A completer whose reply is a single string (one-call tests) or a list
    consumed one-per-call, in order. The last reply repeats after the list runs
    out, so a single-element list answers every call the same way.

    A multi-element list is only correct where the caller's call ORDER is part
    of what the test asserts. Absorb's is not — its phases run concurrently —
    so absorb tests use `from_entries`/`from_cassette` and match on the prompt
    that owns each call.
    """
```

- [ ] **Step 5: Run the tests and confirm the same count passes**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_routes.py -k "absorb or dossier or audit or voice" -q`
Expected: PASS, with the identical count from Step 2. A `CassetteMiss` here names a call whose matcher is wrong — fix the matcher, never add a catch-all entry.

- [ ] **Step 6: Run the full gate**

Run: `make check-py && make check-lint`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/tests/test_routes.py backend/tests/llm_fakes.py
git commit -m "Drive the absorb tests by which call is asking, not by when

Fourteen sites scripted absorb's replies as an ordered list. The phases
are about to run concurrently, so the order is about to stop meaning
anything. Behaviour-preserving: same bodies, same assertions, matched on
the system prompt that owns each call instead of on position."
```

---

### Task 3: Re-derive the budget, while absorb is still sequential

**Why now and not with the fan-out:** `routes/common.py::_bounded_call` documents why absorb is excluded from `llm_call_budget` — `_Budget` "bounds a whole *sequence* and knows which of its steps are droppable". Concurrently nothing is droppable: every call shares one deadline, so an overrun takes **narrative** with it, and narrative failing is a 502 that loses the whole review. Today an overrun degrades. Landing this first means the new semantics are provable without concurrency in the picture.

**Files:**
- Modify: `backend/src/grimoire/routes/scenes.py` (`_Budget`, `post_absorb`)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `_clock` (the monotonic indirection tests drive off a fake), `BudgetRefused`, `_budget_overrun`, `BUDGET_EXHAUSTED` — all already in `routes/scenes.py`.
- Produces: `_Budget.run(coro, on_start=None, *, exempt: bool = False)` — `exempt=True` runs the coroutine under no overall deadline. Task 4 calls it that way for the extraction phase.

- [ ] **Step 1: Write the failing test**

```python
def test_budget_overrun_never_costs_the_extraction(client, monkeypatch):
    """An exhausted absorb budget degrades the droppable phases and leaves the
    review intact. The fan-out is about to make every call share one deadline;
    without the exemption that turns a slow campaign into a 502 and loses a
    review that today comes back with a summary and a reported failure."""
    _seed_scene_with_npc(client)
    monkeypatch.setattr(routes.scenes, "_clock", _expired_clock())
    client.app.dependency_overrides[routes.get_llm] = lambda: from_entries([
        {"when": _WHEN_EXTRACTION, "reply": ABSORB_JSON},
        {"when": _WHEN_DOSSIER, "reply": _DOSSIER},
        {"when": _WHEN_AUDIT, "reply": AUDIT_OK}])

    r = client.post(f"/campaigns/{CID}/scenes/{SID}/absorb")

    assert r.status_code == 200, "an exhausted budget must not 502 the review"
    body = r.json()
    assert body["one_line"], "extraction is exempt from the overall ceiling"
    phases = {p["name"]: p for p in body["phases"]}
    assert phases["extraction"]["status"] == "ok"
    assert phases["dossiers"]["budget_exhausted"] is True
    assert phases["dossiers"]["attempted"] is False, "refused, not failed mid-flight"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_routes.py -k budget_overrun_never_costs -v`
Expected: FAIL — 502, because today the extraction call is inside the shared budget and `post_absorb` raises `HTTPException(502)` on any extraction `LLMError`.

- [ ] **Step 3: Add the exemption to `_Budget.run`**

In `backend/src/grimoire/routes/scenes.py`:

```python
    async def run(self, coro, on_start=None, *, exempt: bool = False):
        """Await `coro` under the remaining budget.

        `exempt` runs it under no overall deadline at all. Exactly one caller
        needs it and the reason is structural: once the phases run
        concurrently they all share this clock, so an expiry that is meant to
        drop the optional work would take the extraction with it — and a
        failed extraction is a 502 and the loss of the whole review. The
        per-call ceiling (`common._bounded_call`) still bounds an exempt call;
        what it is exempt from is the SEQUENCE's clock, which exists to shed
        droppable work and cannot shed this.
        """
        if exempt:
            if on_start:
                on_start()
            return await coro
        ...  # the existing body, unchanged from here down
```

- [ ] **Step 4: Make the extraction call exempt**

In `post_absorb`, the extraction call becomes:

```python
        with store.usage.meter("absorb", campaign=cid, scene=sid) as m:
            # Exempt: see _Budget.run. The overall ceiling sheds droppable
            # phases; this is the one phase whose loss is a 502.
            text = await budget.run(client.complete(messages, conn, m.usage), exempt=True)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_routes.py -k budget_overrun_never_costs -v`
Expected: PASS

- [ ] **Step 6: Confirm no existing budget test regressed**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_routes.py -k "budget or absorb" -q`
Expected: PASS. If a test asserted a 502 on an exhausted budget at the extraction step, that assertion encoded the behaviour this task deliberately changes — update it and say so in the commit body.

- [ ] **Step 7: Update `DEFAULT_ABSORB_BUDGET`'s comment**

`backend/src/grimoire/store/config.py:70` still describes the calls as running "sequentially inside a single request". Correct it now so it is not stale between this task and the next:

```python
# Wall-clock ceiling on one absorb's DROPPABLE work — the dossier, voice and
# audit phases. Extraction is exempt (routes/scenes.py::_Budget.run): losing it
# is a 502 and the loss of the whole review, which is not a degradation. "0"
# means no ceiling at all.
DEFAULT_ABSORB_BUDGET = "600"
```

- [ ] **Step 8: Commit**

```bash
git add backend/src/grimoire/routes/scenes.py backend/src/grimoire/store/config.py backend/tests/test_routes.py
git commit -m "Exempt the extraction from the budget that sheds optional work

The absorb budget exists to drop the phases an absorb can do without.
Extraction is not one of them: it fails as a 502 and takes the review
with it. Sequentially that never bit, because extraction had already
returned before the clock mattered. Concurrently every call shares the
deadline, so without this the first slow campaign loses a whole review
where today it gets a summary and a reported failure.

Landed ahead of the fan-out so the new semantics are provable while the
calls still run one at a time."
```

---

### Task 4: Fan the phases out

**Files:**
- Modify: `backend/src/grimoire/routes/scenes.py` (`post_absorb`; new `_gather_phases`)
- Modify: `backend/src/grimoire/store/config.py` (new `absorb_concurrency`)
- Modify: `backend/src/grimoire/routes/models.py`, `backend/src/grimoire/routes/config.py` (expose the key)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `from_entries` (Task 1), `_Budget.run(..., exempt=True)` (Task 3), `_stage_dossiers`, `_stage_voice_drift`, `_run_audit`, `_watched`, `Abandoned` — all existing.
- Produces: `store.config.absorb_concurrency() -> int`; `_gather_phases(*coros, limit: int) -> list` returning results positionally, exceptions included.

- [ ] **Step 1: Write the failing concurrency test**

```python
def test_absorb_phases_run_concurrently(client, monkeypatch):
    """The four phases overlap. Recorded as an overlap rather than as a
    duration so the test does not measure the machine it runs on."""
    _seed_scene_with_npc(client)
    inflight, peak = set(), []

    class _Recording(FakeLLM):
        async def complete(self, messages, conn, usage=None):
            inflight.add(id(messages))
            peak.append(len(inflight))
            await asyncio.sleep(0)          # yield, so a sequential caller cannot overlap
            inflight.discard(id(messages))
            return await super().complete(messages, conn, usage)

    client.app.dependency_overrides[routes.get_llm] = lambda: _Recording(cassette=...)
    client.post(f"/campaigns/{CID}/scenes/{SID}/absorb")
    assert max(peak) > 1, "phases still run one at a time"


def test_absorb_concurrency_one_is_sequential(client, monkeypatch):
    """`absorb_concurrency = 1` restores today's behaviour exactly — the
    escape hatch a rate-limited provider needs, and the way this change is
    reversible without a revert."""
    _seed_scene_with_npc(client)
    _set_config(client, absorb_concurrency="1")
    ...  # same recorder
    assert max(peak) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_routes.py -k "phases_run_concurrently" -v`
Expected: FAIL — `assert 1 > 1`.

- [ ] **Step 3: Add the config key**

In `backend/src/grimoire/store/config.py`: add `"absorb_concurrency"` to `_CONFIG_KEYS`, `"absorb_concurrency": DEFAULT_ABSORB_CONCURRENCY` to `read_config`'s defaults, and:

```python
# How many of one absorb's LLM calls may be open at once. Its own key rather
# than a constant because a per-account rate limit is a fact no default knows,
# and because "1" has to remain available as an exact restoration of the
# sequential behaviour every version before the fan-out had.
DEFAULT_ABSORB_CONCURRENCY = "4"


def absorb_concurrency() -> int:
    """Concurrent LLM calls one absorb may have in flight. Clamped to at least
    1: zero would mean an absorb that never issues a call, which is not a
    setting anyone wants and is the reading `_count`'s 0-means-off convention
    would otherwise invite."""
    return max(1, _count("absorb_concurrency", DEFAULT_ABSORB_CONCURRENCY))
```

Then add `absorb_concurrency: str | None = None` to the config model in `routes/models.py` and the passthrough in `routes/config.py` beside `absorb_budget`.

- [ ] **Step 4: Write `_gather_phases`**

In `backend/src/grimoire/routes/scenes.py`:

```python
async def _gather_phases(*coros, limit: int) -> list:
    """Run the phase coroutines concurrently, at most `limit` in flight, and
    return their results positionally.

    `return_exceptions=True` is not a convenience. A bare `gather` propagates
    the first exception and leaves its siblings RUNNING — orphaned provider
    calls nobody will read and nobody is bounding. `Abandoned` and
    `BudgetRefused` both fly through this code, so that is the expected path
    here, not the exotic one. Results come back positionally so `edits` is
    assembled in a fixed order regardless of which call finished first.
    """
    sem = asyncio.Semaphore(limit)

    async def guarded(coro):
        async with sem:
            return await coro

    tasks = [asyncio.ensure_future(guarded(c)) for c in coros]
    try:
        return await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        # The REQUEST was cancelled. Detach rather than await, for `_watched`'s
        # reason: waiting for the cancellation you asked for hands the
        # unwinding the very control you were taking back.
        for t in tasks:
            t.cancel()
            t.add_done_callback(lambda x: None if x.cancelled() else x.exception())
        raise
```

- [ ] **Step 5: Restructure `post_absorb`'s middle**

Everything above `budget = _Budget(...)` and below the `return {...}` is unchanged. Replace the four sequential awaits with:

```python
    budget = _Budget(store.config.absorb_budget())
    with store.usage.meter("absorb", campaign=cid, scene=sid) as m:
        extraction = budget.run(client.complete(messages, conn, m.usage), exempt=True)
        results = await _gather_phases(
            extraction,
            _stage_dossiers(cid, sid, transcript, client, conn, budget),
            _stage_voice_drift(cid, sid, transcript, client, conn, budget),
            _run_audit(cid, sid, client, conn, budget),
            limit=store.config.absorb_concurrency())
    text, (dossier_edits, dossiers), (voice_edits, voice), (audit_edits, mechanics) = results
    if isinstance(text, BaseException):
        # Extraction alone still fails the absorb: there is no review without a
        # summary. The other three never raise for absorb (see each one's own
        # failure boundary), so they need no branch here.
        exc = text
        raise HTTPException(status_code=502,
                            detail={"detail": getattr(exc, "detail", str(exc)),
                                    "kind": getattr(exc, "kind", "error")})
    parsed = store.absorb.parse_output(text)
    edits = store.absorb.materialize(cid, sid, parsed, scene["messages"], turn_ledger=ledger)
    edits += dossier_edits + voice_edits
```

Note the `edits` order is `extraction → dossiers → voice`, then `+ audit_edits` in the existing `return`. That is today's order exactly; keep it, or the frozen-campaign snapshot moves for no reason.

- [ ] **Step 6: Move the disconnect watch to the gather**

`_watched` currently wraps individual calls inside `_stage_dossiers` and `_run_audit`. A disconnect abandons the whole review, not one phase, so wrap the gather instead and drop the `abandoned=` argument that `post_absorb` never passed anyway (the retry endpoints `post_audit` and `post_dossiers` still pass theirs and are untouched).

- [ ] **Step 7: Run the tests**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_routes.py -k "absorb or dossier or audit or voice or budget" -q`
Expected: PASS, including both new tests.

- [ ] **Step 8: Confirm the frozen campaign did not move**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_frozen_campaign.py -q`
Expected: PASS with **no** regeneration. This plan changes no rendered text, so a diff here means the edit order moved — fix the order, do not regenerate the snapshot.

- [ ] **Step 9: Run the full gate**

Run: `make check`
Expected: PASS. `check-pydantic1` matters here — the new config field must stay v1/v2-agnostic.

- [ ] **Step 10: Commit**

```bash
git add backend/src/grimoire/routes/scenes.py backend/src/grimoire/store/config.py \
        backend/src/grimoire/routes/models.py backend/src/grimoire/routes/config.py \
        backend/tests/test_routes.py
git commit -m "Run absorb's phases at once instead of one after another

The audit re-reads the scene itself and the per-NPC phases take only the
pre-extraction snapshot, so nothing here ever needed the one before it.
Ten calls back to back become one round: extraction, dossiers, voice and
the audit together, at most absorb_concurrency in flight.

gather takes return_exceptions because a bare one propagates the first
failure and leaves the rest running against the provider, and Abandoned
and BudgetRefused are the ordinary path here. Results come back
positionally so the staged edits keep the order they have today.

absorb_concurrency = 1 restores the sequential behaviour exactly."
```

---

## Self-Review

**Spec coverage (steps 1–3 only):**

| spec item | task |
|---|---|
| Migrate order-dependent fakes to cassettes | 1, 2 |
| Per-call ceiling / narrative exempt from the overall one | 3 |
| `absorb_budget = 0` still means no ceiling | 3 (untouched `_seconds` path) |
| Fan out under a semaphore | 4 |
| `absorb_concurrency`, with `1` restoring sequential | 4 |
| `return_exceptions=True` + explicit cancellation | 4 |
| `_watched` wraps the gather, not each phase | 4 |
| Deterministic `edits` order | 4 step 5 + step 8 |
| Frozen snapshot must come back unchanged | 4 step 8 |

Deferred to later plans, by design: batching the per-NPC phases, the silent-NPC filter, the extraction split, the citation contract, cache-friendly prompt ordering, Android promotion, `POST .../extract`.

**Known gap, deliberate:** the spec's "each phase gets its own per-call ceiling" is only half-done here. Task 3 exempts extraction from the *overall* clock, and `common._bounded_call` already provides a per-call ceiling — but absorb is still not routed through it. Wiring that in is a one-line change that only becomes meaningful once the phases are concurrent, and it belongs with the batching plan where the phase count stops being fixed. Flagged rather than silently dropped.

**Type consistency:** `from_entries(entries, name="<inline>") -> FakeLLM` (Task 1) is called with one positional argument in Tasks 2–4. `_Budget.run(coro, on_start=None, *, exempt=False)` (Task 3) is called with `exempt=True` in Task 4 step 5. `_gather_phases(*coros, limit)` (Task 4 step 4) is called with `limit=store.config.absorb_concurrency()` in step 5. `absorb_concurrency() -> int` matches `_count`'s return type.

**Placeholder scan:** no TBD/TODO. The one `...` in Task 4 step 1 is inside an illustrative test body where the recorder's construction depends on the test's own fixtures; the assertion — the part that defines the task — is written out.
