"""`inference.decide`'s chain of stages (slice H, Task 4; spec 5.5, 7.4).

A native-only Decision model is resolved `"native"` by the resolver (Task 5),
so these tests stand on a real decide resolution on an isolated format-2
store. The resolver builds a native fallback too (behind a structured or a
native primary: `test_inference_decide.py`), unattached; a few tests here
lay a mode onto a real resolution's attempt with `dataclasses.replace`
instead -- a native fallback still attached behind its structured primary,
or an attempt both native and generating -- shapes the resolver does not
build, held here so `stages` answers them safely all the same. Every answer
comes from `llm_fakes`
(a `FakeLLM` scripted with `decisions=`), or from a real `LLMClient` over a
provider double where the facade's own retry and refusal rules are the
subject. Nothing reaches a provider.

Invented provider names and the codebase's placeholder names only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import decisions, inference, llm, routes, wire
from grimoire.decisions import Answer, Choice, Item, ItemResult, Option, Predicate
from grimoire.inference import NATIVE_CONCURRENCY, Stage
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.main import create_app
from grimoire.routes import common
from grimoire.store import pricing
from grimoire.store.inference import facts
from grimoire.store.inference import resolve as inf
from tests.llm_fakes import FakeLLM, decision_reply

from . import inference_fixtures as fx

NATIVE, STRUCTURED = decisions.NATIVE_BACKEND, decisions.STRUCTURED_BACKEND
#: The decide-only model `inference_fixtures.decide_only` puts on the
#: Decision role, and the generating fallback behind it.
DECIDER = ("openrouter", "vendor/decider")
SPARE = ("spare", "vendor/spare")


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


# ---- resolutions ----
def _resolved():
    return inf.resolve("scene-break", operation="decide")


def _native_resolution(client, *, fallback: bool):
    """A native-only Decision model (`inference_fixtures.decide_only`),
    resolved: its primary served natively, and -- when `fallback` -- the
    generating `spare` fallback behind it as a structured stage of its own."""
    fx.decide_only(client, fallback=fallback)
    resolved = _resolved()
    assert [a.decision_mode for a in resolved.attempts] == (
        [NATIVE, STRUCTURED] if fallback else [NATIVE])
    return resolved


def _structured_store(client, *, fallback: bool = True) -> None:
    """F's store: the Decision role on the generating `vendor/active`, with
    `spare` behind it when `fallback`."""
    fx.format2(client)
    fx.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/active"},
        "fallback": ({"provider": SPARE[0], "model": SPARE[1]} if fallback
                     else {"provider": ""})}}})


def _structured_resolution(client, *, fallback_mode: str = STRUCTURED):
    """F's resolution (a structured primary, the `spare` fallback attached),
    or -- `fallback_mode` native -- the same with that fallback served
    natively."""
    _structured_store(client)
    resolved = _resolved()
    if fallback_mode == STRUCTURED:
        return resolved
    return dataclasses.replace(resolved, attempts=(
        resolved.attempts[0], dataclasses.replace(resolved.attempts[1], decision_mode=NATIVE)))


def _catalog(conn_id: str, rows: list[dict]) -> None:
    rev = store.llm_connections.read_connection_raw(conn_id)["rev"]
    store.llm_connections.set_cached_models(conn_id, rows, rev)


def _flag(conn_id: str, model: str) -> None:
    """`model`'s catalog row on `conn_id` lists structured outputs."""
    _catalog(conn_id, [{"id": model, "params": ["temperature", "structured_outputs"]}])


# ---- items, answers, calls ----
def _item(context: str = "Mara closes the door behind her.") -> Item:
    return Item(context, (Predicate("over", "Is the scene over?"),))


def _items(count: int) -> list[Item]:
    return [_item(f"Winifred turns page {n}.") for n in range(count)]


def _yes(value: bool = True) -> ItemResult:
    return ItemResult({"over": Answer(value)})


def _decide(fake, items, *, resolved, **kwargs) -> decisions.Decision:
    return asyncio.run(inference.decide("scene-break", items, client=fake,
                                        resolved=resolved, **kwargs))


def _rows() -> list[dict]:
    return list(store.usage.calls(days=1))


def _refused_schema() -> LLMError:
    return LLMError("bad_response",
                    "response_format: json_schema strict mode is not supported", status=400)


#: A native entry that never answers: the item waits until it is cancelled.
HOLD = object()


class _Endpoint(FakeLLM):
    """A `FakeLLM` whose native answers are keyed by item context, and which
    yields to the loop before answering, so a stage's items really overlap.

    Each `decide_native` stamps the holder first -- the request is out --
    then yields; an entry is an `ItemResult` or `LLMError` (answered through
    `FakeLLM.decide_native`), another exception (raised as it is), or `HOLD`.
    `in_flight`'s high-water mark is `high` (and `full` is set once it
    reaches `NATIVE_CONCURRENCY`), every item a cancel reached is in
    `cancelled`, and `sent` counts the requests."""

    def __init__(self, turns, native: dict[str, object]):
        super().__init__(turns, decisions=[_yes()])
        self.native = native
        self.in_flight = self.high = self.sent = 0
        self.full = asyncio.Event()
        self.cancelled: list[str] = []

    async def decide_native(self, item, conn, usage=None, *, retries=None):
        self.sent += 1
        self.in_flight += 1
        self.high = max(self.high, self.in_flight)
        if self.in_flight == NATIVE_CONCURRENCY:
            self.full.set()
        try:
            self._stamp(usage, conn)
            for _ in range(3):
                await asyncio.sleep(0)
            entry = self.native[item.context]
            if entry is HOLD:
                await asyncio.Event().wait()
            if isinstance(entry, BaseException) and not isinstance(entry, LLMError):
                raise entry
            # The base fake answers its last scripted entry: this one.
            self.decisions = [entry]
            return await super().decide_native(item, conn, usage, retries=retries)
        except asyncio.CancelledError:
            self.cancelled.append(item.context)
            raise
        finally:
            self.in_flight -= 1


class _Wire:
    """One provider double under a real `LLMClient`: `stream` (the structured
    backend, scripted by `streams`, the last entry repeating: an exception to
    raise or a reply to yield) and `decide` (the native endpoint, scripted by
    `decides` the same way). Records each call's model."""

    def __init__(self, streams=(), decides=()):
        self.streams, self.decides = list(streams), list(decides)
        self.streamed: list[dict] = []
        self.decided: list[str] = []

    async def stream(self, messages, model="", *args, **kwargs):
        self.streamed.append({"model": model, "kwargs": kwargs})
        step = self.streams[min(len(self.streamed), len(self.streams)) - 1]
        if isinstance(step, BaseException):
            raise step
        yield step

    async def decide(self, item, model, key, *, usage=None, bound=None):
        self.decided.append(model)
        step = self.decides[min(len(self.decided), len(self.decides)) - 1]
        if isinstance(step, BaseException):
            raise step
        return step


def _real(wire: _Wire, *, retries: int = 2) -> LLMClient:
    return LLMClient(openrouter=wire, timeout=0, retries=retries)


# ---- stages ----
@pytest.mark.parametrize("shape", ["fallback_attached", "no_fallback"])
def test_stages_for_every_f_resolution_are_fs(client, shape):
    """Every structured resolution F builds is one structured stage on the
    chain F sends, its fallback and all -- the resolution's own targets, so
    nothing about F's call moves."""
    _structured_store(client, fallback=shape == "fallback_attached")
    resolved = _resolved()
    chain = inference.stages(resolved)
    assert chain == (Stage(STRUCTURED, resolved.chain, None),)
    assert chain[0].chain.primary is resolved.attempts[0].target
    assert (chain[0].chain.fallback is not None) == (shape == "fallback_attached")
    assert resolved.rides == (shape == "fallback_attached")


def test_stages_for_a_native_only_primary(client):
    resolved = _native_resolution(client, fallback=True)
    # As a resolver might hand it over: the fallback riding the primary.
    primary = resolved.attempts[0]
    resolved = dataclasses.replace(resolved, rides=True)
    assert resolved.chain is not None and resolved.chain.fallback is not None
    native, fallback = inference.stages(resolved)
    assert (native.mode, native.retries) == (NATIVE, None)
    assert native.chain.fallback is None
    assert native.chain == resolved.chain.alone() == wire.Chain(primary.target)
    assert (fallback.mode, fallback.retries) == (STRUCTURED, 0)
    assert fallback.chain == wire.Chain(resolved.attempts[1].target)
    # Built, never popped: the resolution still carries what it carried.
    assert resolved.rides
    assert resolved.chain.fallback is resolved.attempts[1].target


def test_stages_put_no_structured_stage_after_a_native_one(client):
    """An attempt that is native AND generates has one stage, its native one,
    then the fallback's: under ruling 1 no resolution builds it (a model
    that generates is served structured, C1), so a structured stage on it
    would be unreachable and is dropped. Native first on a model that also
    generates (01c §4.2) is a separate branch, on a STRUCTURED primary whose
    task's policy lists its kind (`resolve.native_first`); this hand-built
    native-and-generating attempt is still given no structured stage."""
    _structured_store(client)
    resolved = _resolved()
    both = dataclasses.replace(resolved, attempts=(
        dataclasses.replace(resolved.attempts[0], decision_mode=NATIVE), resolved.attempts[1]))
    assert inf.generates(both.attempts[0])
    chain = inference.stages(both)
    assert [(s.mode, s.retries) for s in chain] == [(NATIVE, None), (STRUCTURED, 0)]
    assert chain[1].chain == wire.Chain(both.attempts[1].target)


def test_stages_for_a_native_fallback(client):
    resolved = _structured_resolution(client, fallback_mode=NATIVE)
    structured, native = inference.stages(resolved)
    assert (structured.mode, structured.retries) == (STRUCTURED, None)
    assert structured.chain == resolved.chain.alone()
    assert (native.mode, native.retries) == (NATIVE, 0)
    assert native.chain == wire.Chain(resolved.attempts[1].target)


def test_a_fallback_known_incapable_is_no_stage(client):
    """A fallback in `fallback_missing` is never sent (spec 5.3): no stage."""
    resolved = dataclasses.replace(_native_resolution(client, fallback=True),
                                   fallback_missing=("generate",))
    assert [s.mode for s in inference.stages(resolved)] == [NATIVE]


def test_stages_copy_and_never_pop(client):
    """M2: deciding through a chain whose first stage is the primary alone
    leaves the resolution as it was: its attempts, whether its fallback
    rides, and its chain."""
    resolved = _structured_resolution(client, fallback_mode=NATIVE)
    before = deepcopy(resolved.attempts)
    rides = resolved.rides
    chain = resolved.chain
    _decide(FakeLLM([[decision_reply({"over": True})]], decisions=[_yes()]), [_item()],
            resolved=resolved)
    assert resolved.attempts == before
    assert resolved.rides == rides
    assert resolved.chain == chain


# ---- native first (spec 01c §4.2): stage shapes ----
def _native_first(monkeypatch, *kinds: str) -> None:
    """Plant `scene-break`'s policy at runtime, listing `kinds` in
    `native_first`. The code table lists no task (`test_task_policy.py`);
    this is how the chain answers a policy, not a policy."""
    monkeypatch.setitem(store.routing.TASK_POLICY, "scene-break",
                        store.routing.TaskPolicy(native_first=kinds))


def _shape(chain) -> list[tuple]:
    return [(s.mode, s.retries, s.isolated) for s in chain]


#: A decide-only model on `spare`, for a native fallback behind a
#: native-first primary.
SPARE_DECIDER = ("spare", "vendor/spare-decider")


@pytest.mark.parametrize("fallback", [True, False], ids=["rides", "none"])
def test_stages_for_a_native_first_primary(client, monkeypatch, fallback):
    """A listed kind on a `native_capable` primary that generates: its
    decisions endpoint first, isolated and with no retries, sent the primary
    alone and unflagged (it is sent no structured envelope); then today's
    structured stage, the resolution's own chain, its riding fallback and
    all. The resolution is left as it was."""
    fx.generates_and_decides(client, fallback=fallback)
    _native_first(monkeypatch, "openrouter")
    resolved = _resolved()
    before = deepcopy(resolved.chain)
    primary = resolved.attempts[0].target
    assert primary.structured is True
    chain = inference.stages(resolved)
    assert _shape(chain) == [(NATIVE, 0, True), (STRUCTURED, None, False)]
    assert chain[0].chain == wire.Chain(dataclasses.replace(primary, structured=False))
    assert chain[0].chain.fallback is None
    assert chain[1].chain == resolved.chain
    assert (chain[1].chain.fallback is not None) == fallback
    assert resolved.attempts[0].target is primary and primary.structured is True
    assert resolved.chain == before


def test_stages_for_a_native_first_primary_with_a_native_fallback(client, monkeypatch):
    fx.generates_and_decides(client, fallback=True, on=SPARE_DECIDER)
    _catalog("spare", [{"id": SPARE_DECIDER[1], "outputs": ["decisions"]}])
    _native_first(monkeypatch, "openrouter")
    resolved = _resolved()
    assert [a.decision_mode for a in resolved.attempts] == [STRUCTURED, NATIVE]
    chain = inference.stages(resolved)
    assert _shape(chain) == [(NATIVE, 0, True), (STRUCTURED, None, False),
                             (NATIVE, 0, False)]
    assert chain[1].chain == resolved.chain.alone()
    assert chain[2].chain == wire.Chain(resolved.attempts[1].target)


@pytest.mark.parametrize("shape", ["native_alone", "native_fallback", "unknown",
                                   "structured_native_fallback"])
def test_a_planted_policy_leaves_every_other_shape_as_today(client, monkeypatch, shape):
    """`native_first=("openrouter",)` planted on `scene-break` adds a stage
    only for a structured primary that is `native_capable`: every other
    resolution keeps the stages it has today, spelled out here."""
    _native_first(monkeypatch, "openrouter")
    if shape == "native_alone":
        resolved = _native_resolution(client, fallback=False)
        expected = [(NATIVE, None, False)]
        chains = [resolved.chain.alone()]
    elif shape == "native_fallback":
        resolved = _native_resolution(client, fallback=True)
        expected = [(NATIVE, None, False), (STRUCTURED, 0, False)]
        chains = [resolved.chain.alone(), wire.Chain(resolved.attempts[1].target)]
    elif shape == "unknown":
        resolved = _structured_resolution(client)
        assert resolved.attempts[0].capabilities["decide_native"].value == "unknown"
        expected = [(STRUCTURED, None, False)]
        chains = [resolved.chain]
        assert resolved.chain.fallback is not None
    else:
        resolved = _structured_resolution(client, fallback_mode=NATIVE)
        expected = [(STRUCTURED, None, False), (NATIVE, 0, False)]
        chains = [resolved.chain.alone(), wire.Chain(resolved.attempts[1].target)]
    chain = inference.stages(resolved)
    assert _shape(chain) == expected
    assert [s.chain for s in chain] == chains
    # The appended field defaults: a three-field Stage is the stage it was.
    assert all(Stage(s.mode, s.chain, s.retries) == s for s in chain)


def test_an_unlisted_kind_is_not_native_first(client, monkeypatch):
    fx.generates_and_decides(client, fallback=True)
    _native_first(monkeypatch, "openai_compatible")
    resolved = _resolved()
    assert _shape(inference.stages(resolved)) == [(STRUCTURED, None, False)]


def test_a_kind_with_no_decisions_endpoint_gets_todays_stages(client, monkeypatch):
    """An Anthropic primary the user says decides natively, under a policy
    planted with its kind (which the static rules refuse): its preset rules
    the endpoint out, an adapter-source `no` that outranks the user, so the
    chain is today's single structured stage."""
    fx.format2(client)
    got = client.post("/api/llm-connections", json={
        "kind": "anthropic", "name": "Realm Anthropic", "api_key": "sk-ant-test"})
    assert got.status_code == 200, got.text
    conn_id = got.json()["id"]
    fx.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": conn_id, "model": "claude-test-1"},
        "fallback": {"provider": ""}}}})
    got = client.put(f"/api/llm-connections/{conn_id}/facts",
                     json={"model": "claude-test-1", "overrides": {"decide_native": "yes"}})
    assert got.status_code == 200, got.text
    _native_first(monkeypatch, "anthropic")
    resolved = _resolved()
    cap = resolved.attempts[0].capabilities["decide_native"]
    assert (cap.value, cap.source) == ("no", "adapter")
    assert not inf.native_first(resolved)
    assert inference.stages(resolved) == (Stage(STRUCTURED, resolved.chain, None),)
    assert not inference.reports_distribution(resolved)


@pytest.mark.parametrize("case,expected", [
    ("native_only", True), ("native_first", True), ("no_policy", False),
    ("unknown", False), ("structured", False), ("generate", False), ("no_chain", False),
])
def test_reports_distribution(client, monkeypatch, case, expected):
    """Whether the first stage is native: a forecast read off `stages`, which
    sends nothing (spec 01c §4.2)."""
    if case == "native_only":
        resolved = _native_resolution(client, fallback=True)
    elif case in ("native_first", "no_policy"):
        fx.generates_and_decides(client, fallback=True)
        if case == "native_first":
            _native_first(monkeypatch, "openrouter")
        resolved = _resolved()
    elif case == "unknown":
        _native_first(monkeypatch, "openrouter")
        resolved = _structured_resolution(client)
    elif case == "structured":
        resolved = _structured_resolution(client)
    elif case == "generate":
        fx.generates_and_decides(client, fallback=True)
        _native_first(monkeypatch, "openrouter")
        resolved = inf.resolve("scene-break")
    else:
        resolved = dataclasses.replace(_native_resolution(client, fallback=False),
                                       attempts=())
        assert inference.stages(resolved) == ()
    assert inference.reports_distribution(resolved) is expected


def test_an_empty_chain_is_refused(client):
    _structured_store(client)
    with pytest.raises(ValueError):
        asyncio.run(inference.run_stages("scene-break", [_item()], (),
                                         client=FakeLLM([["x"]])))


# ---- the chain ----
def test_a_native_answer_is_final(client):
    """A native `refused` is an answer, and an answer never moves an item."""
    resolved = _native_resolution(client, fallback=True)
    fake = FakeLLM([[decision_reply({"over": True})]],
                   decisions=[ItemResult({"over": Answer(None, "refused")})])
    got = _decide(fake, [_item()], resolved=resolved)
    assert got.items[0].answers["over"] == Answer(None, "refused")
    assert got.items[0].backend == NATIVE and got.backend == NATIVE
    assert fake.calls == 0 and got.errors == ()
    # The primary stage runs on the facade's own retry budget.
    assert [retries for _i, _c, retries in fake.native_requests] == [None]


def test_a_native_failure_moves_to_the_fallback_stage(client):
    resolved = _native_resolution(client, fallback=True)
    fake = FakeLLM([[decision_reply({"over": True})]],
                   decisions=[LLMError("network", "connection reset")])
    got = _decide(fake, [_item()], resolved=resolved)
    assert got.items[0].answers["over"] == Answer(True)
    assert got.items[0].backend == STRUCTURED
    assert [(r["status"], r["decision_mode"]) for r in _rows()] == [
        ("error", NATIVE), ("ok", STRUCTURED)]
    # Answered on the fallback: the native failure is not the batch's.
    assert got.errors == ()
    # The fallback stage is one attempt (I3), and alone: nothing behind it.
    assert fake.retries == [0] and fake.requests[-1]["chain"].fallback is None


def test_native_items_fall_through_alone_and_keep_their_order(client):
    """Review Focus 4: only the item whose native call failed goes on to the
    fallback stage, and the results come back in input order."""
    resolved = _native_resolution(client, fallback=True)
    items = _items(5)
    native: dict[str, object] = {i.context: _yes() for i in items}
    native[items[2].context] = LLMError("bad_response", "unreadable", status=502)
    fake = _Endpoint([[decision_reply({"over": False})]], native)
    got = _decide(fake, items, resolved=resolved)
    assert [r.backend for r in got.items] == [NATIVE, NATIVE, STRUCTURED, NATIVE, NATIVE]
    assert [r.answers["over"].answer for r in got.items] == [True, True, False, True, True]
    assert fake.calls == 1
    sent = fake.messages[1]["content"]
    assert items[2].context in sent
    assert not any(i.context in sent for n, i in enumerate(items) if n != 2)


def test_native_concurrency_is_bounded(client):
    resolved = _native_resolution(client, fallback=False)
    items = _items(10)
    fake = _Endpoint([["unused"]], {i.context: _yes() for i in items})
    got = _decide(fake, items, resolved=resolved)
    assert all(r.backend == NATIVE for r in got.items)
    assert fake.high == NATIVE_CONCURRENCY      # bounded, and really concurrent
    assert fake.sent == 10 and len(_rows()) == 10


def test_a_cancelled_native_batch_files_aborted_rows_and_leaves_no_task(client):
    """I5: a cancel reaches every in-flight item, each opened meter files
    `aborted`, and no task `_native` started outlives the call."""
    resolved = _native_resolution(client, fallback=True)
    items = _items(6)
    fake = _Endpoint([["unused"]], {i.context: HOLD for i in items})

    async def main():
        task = asyncio.create_task(inference.decide("scene-break", items, client=fake,
                                                    resolved=resolved))
        await fake.full.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert asyncio.run(main()) == []
    assert sorted(fake.cancelled) == sorted(i.context for i in items[:NATIVE_CONCURRENCY])
    rows = _rows()
    assert len(rows) == NATIVE_CONCURRENCY
    assert {(r["status"], r["decision_mode"]) for r in rows} == {("aborted", NATIVE)}
    assert fake.calls == 0       # the fallback stage never ran


def test_a_cancel_under_an_abandoning_around_files_aborted_rows_and_leaves_no_task(
        client, monkeypatch):
    """Review Focus 4 under the continuity sweep's own hook: `_bounded_call`
    runs each call as a task of its own and, on a cancel, abandons it rather
    than waiting for it. Every opened meter still files `aborted`, every task
    `_native` created has ended when `decide` raises, and the abandoned calls
    unwind within a few turns of the loop."""
    resolved = _native_resolution(client, fallback=True)
    items = _items(6)
    fake = _Endpoint([["unused"]], {i.context: HOLD for i in items})
    children: list[asyncio.Task] = []
    create_task = asyncio.TaskGroup.create_task

    def recording(group, coro, **kwargs):
        task = create_task(group, coro, **kwargs)
        children.append(task)
        return task

    monkeypatch.setattr(asyncio.TaskGroup, "create_task", recording)

    async def main():
        task = asyncio.create_task(inference.decide(
            "scene-break", items, client=fake, resolved=resolved,
            around=lambda call, holder: common._bounded_call(call, ceiling=60)))
        await fake.full.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        ended = [child.done() for child in children]
        for _ in range(10):
            await asyncio.sleep(0)
        return ended, [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    ended, left = asyncio.run(main())
    assert len(ended) == len(items) and all(ended)
    assert left == []
    assert sorted(fake.cancelled) == sorted(i.context for i in items[:NATIVE_CONCURRENCY])
    rows = _rows()
    assert len(rows) == NATIVE_CONCURRENCY
    assert {(r["status"], r["decision_mode"]) for r in rows} == {("aborted", NATIVE)}
    assert fake.calls == 0


@pytest.mark.parametrize("stop", [False, True], ids=["alone", "after-a-stop"])
def test_an_item_that_raises_cancelled_error_itself_is_a_cancel(client, stop):
    """An item whose call raises `CancelledError` with no cancel behind it
    ends its task cancelled, which a TaskGroup does not propagate. It is
    still a cancel: raised as one, never an `IndexError` (alone), and never
    charged to an earlier connection-wide stop and sent on to the fallback
    as if it had been held back (after a stop)."""
    resolved = _native_resolution(client, fallback=True)
    items = _items(3)
    native: dict[str, object] = {i.context: _yes() for i in items}
    native[items[1].context] = asyncio.CancelledError()
    if stop:
        native[items[0].context] = LLMError("auth", "invalid key", status=401)
    fake = _Endpoint([[decision_reply({"over": True})]], native)
    with pytest.raises(asyncio.CancelledError):
        _decide(fake, items, resolved=resolved)
    assert fake.sent == 3 and fake.calls == 0


def test_an_unexpected_exception_cancels_the_other_native_items(client):
    """I5: only an `LLMError` is an item's failure. Anything else propagates
    as itself, and the items still in flight are cancelled with it."""
    resolved = _native_resolution(client, fallback=True)
    items = _items(3)
    fake = _Endpoint([["unused"]], {items[0].context: HOLD, items[1].context: HOLD,
                                    items[2].context: KeyError("lost")})

    async def main():
        with pytest.raises(KeyError):
            await inference.decide("scene-break", items, client=fake, resolved=resolved)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert asyncio.run(main()) == []
    assert sorted(fake.cancelled) == sorted(i.context for i in items[:2])
    assert fake.calls == 0


def test_nothing_answered_reraises_the_first_error_naming_the_fallbacks(client):
    """M3: the primary's error is raised, its kind and window kept, and the
    fallback stage's failure is named after it (`llm.routes_failed`)."""
    resolved = _native_resolution(client, fallback=True)
    first = LLMError("bad_response", "vendor/decider is not a valid model ID", status=404)
    then = LLMError("rate_limit", "slow down", retry_after=7.0, status=429)
    fake = FakeLLM([[""]], error=then, decisions=[first])
    with pytest.raises(LLMError) as exc:
        _decide(fake, [_item()], resolved=resolved)
    assert exc.value.kind == "bad_response" and exc.value.retry_after is None
    assert exc.value.detail == (
        "vendor/decider is not a valid model ID — and the fallback failed too: slow down")
    assert exc.value.words == (first, then)


def test_an_item_that_failed_on_every_stage_carries_both_failures(client):
    """Ruling 1: `errors` holds each failed item's FINAL error, both stages
    composed; the batch still answered, so it is no failure of the batch's."""
    resolved = _native_resolution(client, fallback=True)
    items = _items(2)
    first = LLMError("bad_response", "unreadable", status=502)
    then = LLMError("network", "connection reset")
    fake = _Endpoint([[""]], {items[0].context: _yes(), items[1].context: first})
    fake.error = then
    got = _decide(fake, items, resolved=resolved)
    assert got.items[0].backend == NATIVE
    assert got.items[1].answers["over"] == Answer(None, "error")
    (error,) = got.errors
    assert error.kind == "bad_response" and error.words == (first, then)
    assert common._decide_error(got, "over") is None


@pytest.mark.parametrize("mode", [NATIVE, STRUCTURED])
def test_a_fallback_stage_makes_one_request_on_a_429(client, mode):
    """I3: a fallback stage is one attempt, native or structured, however
    many retries the primary had."""
    busy = LLMError("rate_limit", "slow down", status=429)
    broken = LLMError("bad_response", "upstream exploded", status=500)
    if mode == NATIVE:
        resolved = _structured_resolution(client, fallback_mode=NATIVE)
        wire = _Wire(streams=[broken], decides=[busy])
    else:
        resolved = _native_resolution(client, fallback=True)
        wire = _Wire(streams=[busy], decides=[broken])
    with pytest.raises(LLMError):
        _decide(_real(wire, retries=3), [_item()], resolved=resolved)
    sent = wire.decided if mode == NATIVE else [s["model"] for s in wire.streamed]
    assert sent == [SPARE[1]]


def test_a_structured_stage_before_a_native_fallback_still_retries_without_the_mode(client):
    """M1: the structured primary stage is sent alone (its fallback is a stage
    of its own), so a refused schema is answered by `_ask`'s same-attempt
    re-send without the mode -- before the item could move on."""
    _structured_store(client)
    _flag("openrouter", "vendor/active")
    resolved = _resolved()
    resolved = dataclasses.replace(resolved, attempts=(
        resolved.attempts[0], dataclasses.replace(resolved.attempts[1], decision_mode=NATIVE)))
    assert resolved.chain.primary.structured is True
    wire = _Wire(streams=[_refused_schema(), decision_reply({"over": True})],
                 decides=[AssertionError("the native fallback was sent")])
    got = _decide(_real(wire, retries=0), [_item()], resolved=resolved)
    assert got.items[0].answers["over"] == Answer(True)
    assert got.items[0].backend == STRUCTURED and wire.decided == []
    first, second = wire.streamed
    assert first["model"] == second["model"] == "vendor/active"
    assert "schema" in first["kwargs"] and "schema" not in second["kwargs"]


def test_a_lone_structured_fallback_stage_gets_the_schema_refusal_retry(client):
    """M1: behind a native primary, the structured fallback stage is sent with
    no fallback riding it, so its schema refusal is re-sent without the mode."""
    fx.decide_only(client, fallback=True)
    _flag("spare", "vendor/spare")
    resolved = _resolved()
    assert resolved.attempts[1].target.structured is True
    wire = _Wire(streams=[_refused_schema(), decision_reply({"over": True})],
                 decides=[LLMError("network", "connection reset")])
    got = _decide(_real(wire, retries=0), [_item()], resolved=resolved)
    assert got.items[0].answers["over"] == Answer(True)
    assert got.items[0].backend == STRUCTURED
    first, second = wire.streamed
    assert first["model"] == second["model"] == "vendor/spare"
    assert "schema" in first["kwargs"] and "schema" not in second["kwargs"]


def test_a_preset_refusal_stops_the_chain(client):
    """Ruling 7 (M8): the structured primary's preset refused ends the chain,
    though the native fallback takes no sampling -- the preset is the user's
    to fix, as the facade's message says."""
    resolved = _structured_resolution(client, fallback_mode=NATIVE)
    refusal = llm.PresetRefusalError("bad_response", "temperature must be at most 1",
                                     status=400)
    fake = FakeLLM([[""]], error=refusal, decisions=[_yes()])
    with pytest.raises(LLMError) as exc:
        _decide(fake, [_item()], resolved=resolved)
    assert exc.value is refusal
    assert fake.native_requests == []


def test_an_unrepresentable_item_moves_to_the_fallback_with_its_reason(client):
    """A nullable 255-option choice is 256 native options: refused unsent
    (`decisions.native_gap`) and answered by the structured fallback. With no
    fallback the refusal is the caller's, files no row, and reaches the error
    store."""
    wide = Item("Seraphine weighs the roster.",
                (Choice("who", "Who steps forward?",
                        tuple(Option(f"o{n}", f"Candidate {n}") for n in range(255)),
                        allow_none=True),))
    gap = decisions.native_gap(wide)
    assert gap
    resolved = _native_resolution(client, fallback=True)
    fake = FakeLLM([[decision_reply({"who": "o3"})]], decisions=[_yes()])
    got = _decide(fake, [wide], resolved=resolved)
    assert got.items[0].answers["who"] == Answer("o3")
    assert got.items[0].backend == STRUCTURED
    assert fake.native_requests == []
    assert [r["decision_mode"] for r in _rows()] == [STRUCTURED]

    alone = dataclasses.replace(resolved, attempts=resolved.attempts[:1])
    before = store.errors.summary(days=1, module="scene-break")["total"]
    with pytest.raises(LLMError) as exc:
        _decide(FakeLLM([["unused"]], decisions=[_yes()]), [wide], resolved=alone)
    assert exc.value.code == "native_unrepresentable" and exc.value.detail == gap
    assert len(_rows()) == 1                       # no row for the unsent call
    # ...and exactly one failure recorded for it, this one.
    recorded = store.errors.summary(days=1, module="scene-break")
    assert recorded["total"] == before + 1
    newest = recorded["rows"][0]
    assert (newest["kind"], newest["message"]) == ("bad_response", gap)


#: A rank that names no `pointwise` question (01e §4.3, §4.4): a decisions
#: endpoint cannot order its candidates, so a native stage refuses it unsent.
BLIND_RANK = Item("Mara asks after the ledger she lost.", (decisions.Rank(
    "relevant", "Order these scenes by how much the turn needs them.",
    (Option("scene:ledger", "Mara loses the ledger."),
     Option("scene:market", "Winifred counts the stalls.")), top=1),))


def test_a_rank_without_pointwise_is_answered_by_the_structured_fallback(client):
    gap = decisions.native_gap(BLIND_RANK)
    assert "names no pointwise question" in gap
    resolved = _native_resolution(client, fallback=True)
    fake = FakeLLM([[decision_reply({"relevant": ["scene:ledger"]})]], decisions=[_yes()])
    got = _decide(fake, [BLIND_RANK], resolved=resolved)
    assert got.items[0].answers["relevant"] == Answer(decisions.Ranking(
        (("scene:ledger",),), rest=("scene:market",)))
    assert got.items[0].backend == STRUCTURED
    assert fake.native_requests == []
    assert [r["decision_mode"] for r in _rows()] == [STRUCTURED]
    (refused, answered) = got.calls
    assert (refused.mode, refused.row, refused.error_kind) == (NATIVE, None, "bad_response")
    assert (answered.mode, answered.items) == (STRUCTURED, (0,))


def test_a_rank_without_pointwise_and_no_fallback_is_native_unrepresentable(client):
    resolved = _native_resolution(client, fallback=False)
    # Beside an item the native stage answers, the batch comes back, and the
    # rank's one error is the refusal unsent.
    fake = FakeLLM([["unused"]], decisions=[_yes()])
    got = _decide(fake, [_item(), BLIND_RANK], resolved=resolved)
    assert got.items[0].answers["over"] == Answer(True)
    assert all(a.reason == "error" for a in got.items[1].answers.values())
    (error,) = got.errors
    assert (error.kind, error.code) == ("bad_response", "native_unrepresentable")
    assert error.detail == decisions.native_gap(BLIND_RANK)
    assert len(fake.native_requests) == 1                 # the answered item alone
    # Alone, the refusal is the caller's.
    with pytest.raises(LLMError) as exc:
        _decide(FakeLLM([["unused"]], decisions=[_yes()]), [BLIND_RANK], resolved=resolved)
    assert exc.value.code == "native_unrepresentable"


def test_around_runs_inside_each_native_meter(client):
    """`around` is handed each native call and its meter's live holder: a
    timeout it raises is that meter's `error/timeout` row, stamped native, and
    the item moves on to the fallback stage."""
    resolved = _native_resolution(client, fallback=True)
    fake = _Endpoint([[decision_reply({"over": True})]], {_item().context: HOLD})
    holders: list[dict] = []

    async def around(call, holder):
        holders.append(holder)
        try:
            return await asyncio.wait_for(call, 0.05)
        except TimeoutError:
            raise LLMError("timeout", "the call timed out") from None

    got = _decide(fake, [_item()], resolved=resolved, around=around)
    assert got.items[0].backend == STRUCTURED
    rows = _rows()
    assert [(r["status"], r.get("error", ""), r["decision_mode"]) for r in rows] == [
        ("error", "timeout", NATIVE), ("ok", "", STRUCTURED)]
    assert holders[0][llm.ATTEMPTED].model == DECIDER[1]


def test_native_rows_carry_operation_and_mode(client):
    resolved = _native_resolution(client, fallback=True)
    before = resolved.attempts[0].target
    fake = FakeLLM([["unused"]], decisions=[_yes()])
    _decide(fake, [_item()], resolved=resolved)
    (row,) = _rows()
    assert (row["operation"], row["decision_mode"]) == ("decide", NATIVE)
    sent = fake.native_requests[0][1]
    assert sent.account.decision_mode == NATIVE
    assert resolved.attempts[0].target.account.decision_mode == ""
    # E's no-mutation rule: the resolution's own target is as it was.
    assert resolved.attempts[0].target is before


def test_a_native_row_files_no_preset_it_never_sent(client):
    """Brutal reviews H (🟣3, P1): a native call sends no sampling (spec 8),
    so its ledger row names no `preset`, even with one on the role --
    `llm_usage.account` documents `preset` as the one actually sent. The
    resolution's own target still carries the preset it resolved."""
    fx.decide_only(client, fallback=False)
    pid = store.sampler_presets.create_preset("Warm", {"temperature": 0.8})
    fx.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": DECIDER[0], "model": DECIDER[1], "preset": pid}}}})
    resolved = _resolved()
    assert resolved.attempts[0].target.sampling.preset_id == pid
    fake = FakeLLM([["unused"]], decisions=[_yes()])
    _decide(fake, [_item()], resolved=resolved)
    (row,) = _rows()
    assert row["decision_mode"] == NATIVE and "preset" not in row
    assert fake.native_requests[0][1].sampling == wire.Sampling()


def test_a_native_row_still_files_decision_mode_native(client):
    """Slice I, 9c: the native stage's stamp moved from the dict's account
    block to its target's (`wire.Chain.with_account`). Through the REAL
    facade, the row a native call files still says `decision_mode: native`,
    so `usage._modellable` keeps it unpriced at chat rates; it names no
    `preset`, though the role has one; and a rate on file for exactly its
    model models nothing (`usage_rollup.VERSION` stays 6: no row field
    moved)."""
    fx.decide_only(client, fallback=False)
    pid = store.sampler_presets.create_preset("Warm", {"temperature": 0.8})
    fx.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": DECIDER[0], "model": DECIDER[1], "preset": pid}}}})
    rates = {"prompt_usd_per_1k": 0.01, "completion_usd_per_1k": 0.02}
    facts.state(DECIDER[0], DECIDER[1], rates=rates)
    pricing.write_pricing({DECIDER[1]: rates})
    resolved = _resolved()
    assert resolved.chain is not None and resolved.chain.primary.sampling.preset_id == pid

    class _Counted(_Wire):
        async def decide(self, item, model, key, *, usage=None, bound=None):
            result = await super().decide(item, model, key, usage=usage, bound=bound)
            usage.update({"prompt_tokens": 420, "completion_tokens": 3})
            return result

    wire_ = _Counted(decides=[_yes()])
    _decide(_real(wire_), [_item()], resolved=resolved)
    assert wire_.decided == [DECIDER[1]]
    (row,) = _rows()
    assert (row["operation"], row["decision_mode"]) == ("decide", NATIVE)
    assert "preset" not in row and row.get("modelled_usd") is None
    totals = store.usage.summary(days=1)["totals"]
    assert totals["modelled_usd"] == 0.0 and totals["modelled_calls"] == 0
    assert totals["unpriced_calls"] == 1
    from grimoire.store import usage_rollup
    assert usage_rollup.VERSION == 6


def test_the_facade_drops_the_preset_from_a_native_call_it_is_handed(client):
    """The same at the facade, for a caller that hands `decide_native` a
    target with a preset on it (the model test's probe): the holder it stamps,
    which the meter files, names none, and the attempt it names carries none."""
    fx.decide_only(client, fallback=False)
    target = dataclasses.replace(
        _resolved().attempts[0].target,
        sampling=wire.Sampling(preset_id="warm", preset_name="Warm", scope="global",
                               params={"temperature": 0.4}))
    holder: dict = {}
    sent = _Wire(decides=[_yes()])
    asyncio.run(_real(sent).decide_native(_item(), target, holder))
    assert holder["provider_id"] == DECIDER[0]     # the account was filed
    assert "preset" not in holder
    assert holder[llm.ATTEMPTED] == target.without_sampling()
    assert holder[llm.ATTEMPTED].sampling == wire.Sampling()
    assert target.sampling.preset_id == "warm"     # the caller's target is untouched


def test_decision_names_the_one_backend_and_route_that_answered(client):
    """`backend`, `provider` and `model` name the one stage that answered, and
    are empty when stages with different routes answered one batch; `served`
    names each, in the order it first answered."""
    resolved = _native_resolution(client, fallback=True)
    items = _items(2)
    alone = _decide(_Endpoint([["unused"]], {i.context: _yes() for i in items}), items,
                    resolved=resolved)
    assert (alone.backend, alone.provider, alone.model) == (NATIVE, *DECIDER)
    assert alone.served == (DECIDER,)

    fallen = _decide(FakeLLM([[decision_reply({"over": True}, {"over": False})]],
                             decisions=[LLMError("network", "connection reset")]),
                     items, resolved=resolved)
    assert (fallen.backend, fallen.provider, fallen.model) == (STRUCTURED, *SPARE)
    assert fallen.served == (SPARE,)

    mixed = _decide(_Endpoint([[decision_reply({"over": False})]],
                              {items[0].context: _yes(),
                               items[1].context: LLMError("network", "connection reset")}),
                    items, resolved=resolved)
    assert [r.backend for r in mixed.items] == [NATIVE, STRUCTURED]
    assert (mixed.backend, mixed.provider, mixed.model) == ("", "", "")
    assert mixed.served == (DECIDER, SPARE)


# ---- a failure every other item would meet (G's final review) ----
def _budget_refused() -> LLMError:
    return routes.scenes.BudgetRefused("timeout", routes.scenes.BUDGET_EXHAUSTED)


@pytest.mark.parametrize("stop", [
    LLMError("auth", "invalid key", status=401),
    LLMError("missing_key", "no API key"),
    LLMError("rate_limit", "slow down", retry_after=90.0, status=429),
    LLMError("bad_response", "insufficient credits", status=402),
    pytest.param("budget", id="budget-refused"),
], ids=["auth", "missing_key", "rate_limit", "spend", "budget-refused"])
def test_a_connection_wide_failure_starts_no_further_native_items(client, stop):
    """Ruling 4: once an item fails with what every other item on that
    connection would meet, no further item on the stage is started. Those in
    flight finish; the rest stay pending, are never sent and file no row, and
    each carries the failure that stopped it."""
    stop = _budget_refused() if stop == "budget" else stop
    resolved = _native_resolution(client, fallback=False)
    items = _items(10)
    native: dict[str, object] = {i.context: _yes() for i in items}
    native[items[0].context] = stop
    fake = _Endpoint([["unused"]], native)
    got = _decide(fake, items, resolved=resolved)
    assert fake.sent <= NATIVE_CONCURRENCY
    answered = [r for r in got.items if r.backend == NATIVE]
    assert len(answered) == fake.sent - 1
    assert len(got.errors) == 10 - len(answered)
    assert all(error is stop for error in got.errors)
    assert len(_rows()) == fake.sent           # nothing held back was sent


def test_items_a_connection_wide_failure_held_back_move_to_the_fallback(client):
    resolved = _native_resolution(client, fallback=True)
    items = _items(10)
    native: dict[str, object] = {i.context: _yes() for i in items}
    native[items[0].context] = LLMError("auth", "invalid key", status=401)
    fake = _Endpoint([[decision_reply(*({"over": False},) * 7)]], native)
    got = _decide(fake, items, resolved=resolved)
    assert fake.sent == NATIVE_CONCURRENCY
    assert [r.backend for r in got.items] == [STRUCTURED] + [NATIVE] * 3 + [STRUCTURED] * 6
    assert got.errors == () and fake.calls == 1


def test_an_item_failure_of_its_own_stops_nothing(client):
    resolved = _native_resolution(client, fallback=False)
    items = _items(10)
    native: dict[str, object] = {i.context: _yes() for i in items}
    native[items[0].context] = LLMError("bad_response", "vendor/decider refused", status=404)
    fake = _Endpoint([["unused"]], native)
    got = _decide(fake, items, resolved=resolved)
    assert fake.sent == 10 and len(got.errors) == 1


def _chunks(count: int = 3) -> list[Item]:
    """Enough items for `count` structured chunks."""
    return _items((count - 1) * decisions.MAX_ITEMS_PER_CALL + 1)


def test_a_connection_wide_failure_sends_no_further_structured_chunk(client):
    """The same stop on a structured stage: three chunks, the first meets a
    401, and only that one request is made. The two held back are never
    sent, file no row, and carry the 401; with nothing answered it is raised."""
    _structured_store(client, fallback=False)
    resolved = _resolved()
    unauthorised = LLMError("auth", "invalid key", status=401)
    fake = FakeLLM([[decision_reply({"over": True})]], error=unauthorised)
    with pytest.raises(LLMError) as exc:
        _decide(fake, _chunks(3), resolved=resolved)
    assert exc.value is unauthorised
    assert fake.calls == 1 and len(_rows()) == 1


def test_a_structured_chunk_held_back_has_no_call_record(client):
    """01a-S1: after a connection-wide stop on a structured stage, the
    chunks held back have no `CallRecord`; with a later answer the decision
    lists the sent chunk's record only for that stage."""
    resolved = _structured_resolution(client, fallback_mode=NATIVE)
    items = _chunks(3)
    unauthorised = LLMError("auth", "invalid key", status=401)
    fake = FakeLLM([[decision_reply({"over": True})]], error=unauthorised,
                   decisions=[_yes()])
    got = _decide(fake, items, resolved=resolved)
    first = [c for c in got.calls if c.stage == 0]
    assert len(first) == 1 and first[0].error_kind == "auth"
    assert first[0].items == tuple(range(decisions.MAX_ITEMS_PER_CALL))


def test_structured_chunks_held_back_move_to_the_next_stage(client):
    resolved = _structured_resolution(client, fallback_mode=NATIVE)
    items = _chunks(3)
    fake = FakeLLM([[decision_reply({"over": True})]],
                   error=LLMError("auth", "invalid key", status=401), decisions=[_yes()])
    got = _decide(fake, items, resolved=resolved)
    assert fake.calls == 1 and len(fake.native_requests) == len(items)
    assert all(r.backend == NATIVE for r in got.items) and got.errors == ()


@pytest.mark.parametrize(("then", "requests"), [
    (LLMError("auth", "invalid key", status=401), 2),
    (LLMError("network", "connection reset"), 6),
], ids=["both-routes-refused", "the-fallback-failed-its-own-way"])
def test_a_chunk_sent_with_a_fallback_stops_only_when_both_routes_would(client, then,
                                                                        requests):
    """A structured chunk whose primary meets a 401 and whose fallback (riding
    the facade) fails too stops the stage only when the fallback's failure is
    connection-wide as well: a fallback that failed for a reason of its own
    may serve the next chunk."""
    _structured_store(client)
    resolved = _resolved()
    assert resolved.rides
    provider = _Wire(streams=[LLMError("auth", "invalid key", status=401), then])
    with pytest.raises(LLMError):
        _decide(_real(provider, retries=0), _chunks(3), resolved=resolved)
    assert len(provider.streamed) == requests


def test_a_clock_refusal_ends_the_chain_and_keeps_its_type(client):
    """Brutal review H 🟡2: the absorb clock that refused the native stage
    refuses every later call too, so the chain stops there -- the fallback
    stage is never invoked -- and the refusal is raised as the
    `BudgetRefused` it is, never composed into a plain `LLMError` that a
    phase's `except BudgetRefused` would read as a provider failure."""
    resolved = _native_resolution(client, fallback=True)
    fake = FakeLLM([[decision_reply({"over": True})]], decisions=[_yes()])
    refused: list[LLMError] = []

    async def around(call, holder):
        call.close()           # the clock is out: nothing is sent
        refused.append(_budget_refused())
        raise refused[-1]

    with pytest.raises(routes.scenes.BudgetRefused) as exc:
        _decide(fake, [_item()], resolved=resolved, around=around)
    assert exc.value is refused[0] and len(refused) == 1
    assert fake.native_requests == [] and fake.calls == 0 and _rows() == []


def test_the_reviewers_two_stage_clock_script_raises_budget_refused(client):
    """The reviewer's own script: `run_stages` over (native, structured at 0
    retries) with an `around` that always refuses -- `BudgetRefused` comes out
    as itself, not as an `LLMError` composed of two refusals."""
    resolved = _native_resolution(client, fallback=True)
    chain = (Stage(NATIVE, wire.Chain(resolved.attempts[0].target), None),
             Stage(STRUCTURED, wire.Chain(resolved.attempts[1].target), 0))
    fake = FakeLLM([[decision_reply({"over": True})]], decisions=[_yes()])

    async def around(call, holder):
        call.close()
        raise _budget_refused()

    with pytest.raises(LLMError) as exc:
        asyncio.run(inference.run_stages("scene-break", [_item()], chain, client=fake,
                                         around=around))
    assert isinstance(exc.value, routes.scenes.BudgetRefused)
    assert exc.value.detail == routes.scenes.BUDGET_EXHAUSTED and exc.value.words == ()
    assert fake.calls == 0


def test_a_unit_refused_on_every_stage_is_the_first_refusal_itself():
    """Should a unit's failures all be the clock's refusal (a chain an eval
    builds by hand, say), its final error is the first of them as it is."""
    first, second = _budget_refused(), _budget_refused()
    assert inference._final([first, second]) is first
    composed = inference._final([LLMError("network", "connection reset"), second])
    assert not isinstance(composed, routes.scenes.BudgetRefused)
    assert composed.words[1] is second


# ---- a stop carried to a later stage on the same connection ----
@pytest.mark.parametrize("stop", [
    LLMError("auth", "invalid key", status=401),
    LLMError("missing_key", "no API key"),
    LLMError("rate_limit", "slow down", retry_after=90.0, status=429),
    LLMError("bad_response", "insufficient credits", status=402),
], ids=["auth", "missing_key", "rate_limit", "spend"])
def test_a_connection_wide_stop_skips_a_later_stage_on_the_same_connection(client, stop):
    """Brutal review H 🟣4: a native-only model with a generating fallback on
    the same OpenRouter account. The native stage stops on a failure every
    call on that connection would meet, so the structured stage on the same
    connection is never sent: its items keep the failures they have, as
    items held back inside a stage do."""
    fx.decide_only(client, fallback=True, on=fx.SAME_PROVIDER)
    resolved = _resolved()
    assert [(a.provider_id, a.decision_mode) for a in resolved.attempts] == [
        ("openrouter", NATIVE), ("openrouter", STRUCTURED)]
    items = _items(6)
    fake = _Endpoint([[decision_reply(*({"over": False},) * 6)]],
                     {i.context: stop for i in items})
    with pytest.raises(LLMError) as exc:
        _decide(fake, items, resolved=resolved)
    assert exc.value is stop
    assert fake.calls == 0
    assert fake.sent <= NATIVE_CONCURRENCY and len(_rows()) == fake.sent


def test_a_connection_wide_stop_still_runs_a_stage_on_another_connection(client):
    """The same stop with the fallback on another provider: that stage runs,
    and answers every item the native stage left."""
    resolved = _native_resolution(client, fallback=True)
    items = _items(6)
    fake = _Endpoint([[decision_reply(*({"over": False},) * 6)]],
                     {i.context: LLMError("auth", "invalid key", status=401) for i in items})
    got = _decide(fake, items, resolved=resolved)
    assert {r.backend for r in got.items} == {STRUCTURED} and got.errors == ()
    assert fake.calls == 1 and fake.requests[0]["target"].provider_id == SPARE[0]


def test_an_id_less_stage_is_never_the_stopped_stages_connection(client):
    """`_same_connection`: an empty provider id matches nothing, not even
    another empty one. A native stage on an id-less target that stops on a
    connection-wide failure (a refused key) therefore skips no later stage
    for being "the same connection": the id-less structured stage after it
    runs, and answers. (The resolver builds no id-less attempt; a hand-built
    chain, or an unbuilt target, has one.)"""
    assert not inference._same_connection("", "")
    assert inference._same_connection("openrouter", "openrouter")
    resolved = _native_resolution(client, fallback=True)
    native = dataclasses.replace(resolved.attempts[0].target, provider_id="")
    fallback = dataclasses.replace(resolved.attempts[1].target, provider_id="")
    assert native.kind == fallback.kind
    items = _items(2)
    fake = FakeLLM([[decision_reply({"over": False}, {"over": False})]],
                   decisions=[LLMError("auth", "invalid key", status=401)])
    got = asyncio.run(inference.run_stages(
        "scene-break", items, (Stage(NATIVE, wire.Chain(native), None),
                               Stage(STRUCTURED, wire.Chain(fallback), 0)), client=fake))
    assert fake.calls == 1
    assert {r.backend for r in got.items} == {STRUCTURED} and got.errors == ()


# ---- native first (spec 01c §4.2.1): the stage never kills the ones after it ----
def _both(client, monkeypatch, *, fallback: bool):
    """`vendor/both` on the Decision role, `scene-break` native-first on
    OpenRouter: its decisions endpoint, then its structured stage."""
    fx.generates_and_decides(client, fallback=fallback)
    _native_first(monkeypatch, "openrouter")
    resolved = _resolved()
    assert _shape(inference.stages(resolved))[0] == (NATIVE, 0, True)
    return resolved


def test_a_403_from_the_native_first_stage_falls_to_the_structured_stage(client, monkeypatch):
    """Review B1: OpenRouter maps 403 to `auth`, which is what a key without
    access to a decisions endpoint gets while it serves every chat call. With
    no fallback, the structured stage on the same provider is the only route
    left: only isolation (the stage never marks its provider dead) lets it
    run. A 403 is a native rejection, so health sees no failure."""
    resolved = _both(client, monkeypatch, fallback=False)
    wire = _Wire(streams=[decision_reply({"over": True})],
                 decides=[LLMError("auth", "forbidden", status=403)])
    observed: list[tuple] = []
    real = LLMClient(openrouter=wire, timeout=0, retries=2,
                     observer=lambda target, error: observed.append((target, error)))
    got = _decide(real, [_item()], resolved=resolved)
    assert wire.decided == [fx.BOTH[1]]
    assert [s["model"] for s in wire.streamed] == [fx.BOTH[1]]
    assert [r.backend for r in got.items] == [STRUCTURED] and got.errors == ()
    assert observed and all(error is None for _target, error in observed)


def test_a_rate_limit_from_the_native_first_stage_is_sent_once(client, monkeypatch):
    """`retries=0` (01c §4.2.2): a rate-limited decisions endpoint is not
    retried before the structured stage, which can answer, gets its turn."""
    resolved = _both(client, monkeypatch, fallback=False)
    wire = _Wire(streams=[decision_reply({"over": False})],
                 decides=[LLMError("rate_limit", "slow down", status=429, retry_after=1.0)])
    got = _decide(_real(wire, retries=2), [_item()], resolved=resolved)
    assert wire.decided == [fx.BOTH[1]]
    assert len(wire.streamed) == 1
    assert [r.backend for r in got.items] == [STRUCTURED]


@pytest.mark.parametrize("todays_dead_rule", [False, True], ids=["as-built", "isolation-only"])
def test_an_auth_from_native_first_leaves_the_riding_fallback(client, monkeypatch,
                                                              todays_dead_rule):
    """Review B1, the riding fallback: the native stage's `auth` does not
    skip the structured stage on its provider, nor the `spare` fallback
    riding it, which answers when the structured primary fails too. Before
    01c-S2, the dead rule skipped both. Run again with `_all_dead` put back
    to today's primary-only rule: the stage still runs, so it is isolation,
    not the all-routes rule (which `spare` alone would satisfy), that keeps
    it."""
    if todays_dead_rule:
        monkeypatch.setattr(inference, "_all_dead", lambda stage, dead: any(
            inference._same_connection(stage.chain.primary.provider_id, d) for d in dead))
    resolved = _both(client, monkeypatch, fallback=True)
    assert resolved.chain.fallback is not None
    wire = _Wire(streams=[LLMError("bad_response", "upstream exploded", status=500),
                          decision_reply({"over": True})],
                 decides=[LLMError("auth", "invalid key", status=401)])
    got = _decide(_real(wire, retries=0), [_item()], resolved=resolved)
    assert wire.decided == [fx.BOTH[1]]
    assert [s["model"] for s in wire.streamed] == [fx.BOTH[1], SPARE[1]]
    assert [r.backend for r in got.items] == [STRUCTURED] and got.errors == ()


def test_a_native_failure_is_answered_by_the_structured_stage_and_a_refusal_is_not_re_asked(
        client, monkeypatch):
    resolved = _both(client, monkeypatch, fallback=False)
    items = _items(3)
    fake = _Endpoint([[decision_reply({"over": False})]], {
        items[0].context: ItemResult({"over": Answer(None, "refused")}),
        items[1].context: LLMError("bad_response", "upstream exploded", status=500),
        items[2].context: _yes(),
    })
    got = _decide(fake, items, resolved=resolved)
    assert fake.calls == 1          # one chunk: item 1 alone
    assert [r.backend for r in got.items] == [NATIVE, STRUCTURED, NATIVE]
    assert got.items[0].answers["over"].reason == "refused"
    assert got.items[1].answers["over"].answer is False
    assert got.items[2].answers["over"].answer is True
    # Two backends answered; one route (the same model on both stages).
    assert got.backend == "" and got.served == ((fx.BOTH[0], fx.BOTH[1]),)
    # 01d-S2's per-item server: both stages stamp the same model, the native
    # one from its stage target, the structured one from the reply's holder.
    assert [r.served for r in got.items] == [("openrouter", *fx.BOTH)] * 3
    assert sorted((c.stage, c.mode) for c in got.calls) == [
        (0, NATIVE), (0, NATIVE), (0, NATIVE), (1, STRUCTURED)]


def test_a_both_failed_item_reports_the_structured_failure(client, monkeypatch):
    """D3: the native-first stage's 403 is not composed into the error of an
    item a later stage took -- the turn's failure is the chat endpoint's
    timeout, not a refused key. The 403 is still filed as its own row."""
    resolved = _both(client, monkeypatch, fallback=False)
    forbidden = LLMError("auth", "forbidden", status=403)
    timeout = LLMError("timeout", "the call timed out")
    wire = _Wire(streams=[timeout], decides=[forbidden])
    with pytest.raises(LLMError) as exc:
        _decide(_real(wire, retries=0), [_item()], resolved=resolved)
    assert exc.value.kind == "timeout"
    assert exc.value is not forbidden and forbidden not in exc.value.words
    native_rows = [r for r in _rows() if r.get("decision_mode") == NATIVE]
    assert len(native_rows) == 1 and native_rows[0]["status"] == "error"


def test_a_clock_refusal_at_the_native_first_stage_ends_the_chain(client, monkeypatch):
    """D5: isolation is about `dead`, not the caller's clock, which refuses
    every later call too."""
    resolved = _both(client, monkeypatch, fallback=False)
    fake = FakeLLM([[decision_reply({"over": True})]], decisions=[_yes()])
    refused: list[LLMError] = []

    async def around(call, holder):
        call.close()
        refused.append(_budget_refused())
        raise refused[-1]

    with pytest.raises(routes.scenes.BudgetRefused) as exc:
        _decide(fake, [_item()], resolved=resolved, around=around)
    assert exc.value is refused[0] and len(refused) == 1
    assert fake.calls == 0


@pytest.mark.parametrize("isolated", [True, False])
def test_a_structured_stage_stop_still_skips_a_later_stage_on_its_connection(
        client, monkeypatch, isolated):
    """Rule 1's contrast: an isolated stage's `auth` kills nothing, so the
    structured stage runs; its own `auth` still skips the stage after it on
    the same connection (01 §5.5). Not isolated, the first `auth` skips both."""
    resolved = _both(client, monkeypatch, fallback=False)
    target = resolved.attempts[0].target
    decider = dataclasses.replace(target, model="vendor/decider")
    refused = LLMError("auth", "invalid key", status=401)
    fake = FakeLLM([[decision_reply({"over": True})]], error=refused, decisions=[refused])
    chain = (Stage(NATIVE, wire.Chain(target), 0, isolated=isolated),
             Stage(STRUCTURED, wire.Chain(target), None),
             Stage(NATIVE, wire.Chain(decider), 0))
    with pytest.raises(LLMError):
        asyncio.run(inference.run_stages("scene-break", [_item()], chain, client=fake))
    assert len(fake.native_requests) == 1           # the third stage never ran
    assert fake.calls == (1 if isolated else 0)


def test_a_stage_with_a_live_riding_fallback_is_not_skipped_as_dead(client, monkeypatch):
    """Rule 2 (01c §4.2.1): a stage is skipped as dead only when every route
    it sends is dead. A later stage whose primary is on the stopped provider
    but whose riding fallback is not still runs, and its fallback answers."""
    resolved = _both(client, monkeypatch, fallback=True)
    primary, spare = resolved.attempts[0].target, resolved.attempts[1].target
    wire_ = _Wire(streams=[LLMError("auth", "invalid key", status=401),
                           LLMError("auth", "invalid key", status=401),
                           decision_reply({"over": True})])
    chain = (Stage(STRUCTURED, wire.Chain(primary), None),
             Stage(STRUCTURED, wire.Chain(primary, spare), 0))
    got = asyncio.run(inference.run_stages("scene-break", [_item()], chain,
                                           client=_real(wire_, retries=0)))
    assert [s["model"] for s in wire_.streamed] == [fx.BOTH[1], fx.BOTH[1], SPARE[1]]
    assert [r.backend for r in got.items] == [STRUCTURED]


def test_all_dead_counts_every_route_a_stage_sends(client, monkeypatch):
    resolved = _both(client, monkeypatch, fallback=True)
    primary, spare = resolved.attempts[0].target, resolved.attempts[1].target
    dead = [primary.provider_id]
    assert inference._all_dead(Stage(STRUCTURED, wire.Chain(primary), None), dead)
    assert not inference._all_dead(Stage(STRUCTURED, wire.Chain(primary, spare), 0), dead)
    assert inference._all_dead(Stage(STRUCTURED, wire.Chain(primary, spare), 0),
                               [*dead, spare.provider_id])
    # A native stage sends its primary alone, so only that counts there.
    assert inference._all_dead(Stage(NATIVE, wire.Chain(primary, spare), 0), dead)
    idless = dataclasses.replace(primary, provider_id="")
    assert not inference._all_dead(Stage(STRUCTURED, wire.Chain(idless), None), [""])
    assert not inference._all_dead(Stage(STRUCTURED, wire.Chain(primary), None), [])


# ---- a hung decisions endpoint (brutal review H 🟣5) ----
def _timed_out() -> LLMError:
    return LLMError("timeout", "the call timed out")


def test_a_run_of_native_timeouts_stops_the_stage(client):
    """A decisions endpoint that accepts connections and never answers: once
    `NATIVE_TIMEOUT_STOP` items in a row have timed out, no further item is
    started -- those already in flight finish -- so a hung endpoint costs
    about two waves of the ceiling, not one per `NATIVE_CONCURRENCY` items.
    The rest are never sent, file no row, and carry the timeout."""
    resolved = _native_resolution(client, fallback=False)
    items = _items(24)
    fake = _Endpoint([["unused"]], {i.context: _timed_out() for i in items})
    with pytest.raises(LLMError) as exc:
        _decide(fake, items, resolved=resolved)
    assert exc.value.kind == "timeout"
    assert NATIVE_CONCURRENCY <= fake.sent <= 2 * NATIVE_CONCURRENCY - 1
    assert len(_rows()) == fake.sent


def test_timeouts_broken_by_an_answer_stop_nothing(client):
    """One slow item says nothing of the next: a run of timeouts that an
    answer breaks resets the count, and every item is sent."""
    resolved = _native_resolution(client, fallback=False)
    items = _items(12)
    fake = _Endpoint([["unused"]], {
        i.context: (_yes() if n % NATIVE_CONCURRENCY == 0 else _timed_out())
        for n, i in enumerate(items)})
    got = _decide(fake, items, resolved=resolved)
    assert fake.sent == 12
    assert len(got.errors) == 12 - 12 // NATIVE_CONCURRENCY


def test_a_native_timeout_stop_does_not_skip_a_generating_stage_on_its_connection(client):
    """A hung decisions endpoint is not a hung chat endpoint: the timeout
    stop is the native stage's own, so the structured fallback on the same
    connection still answers the items it left."""
    fx.decide_only(client, fallback=True, on=fx.SAME_PROVIDER)
    resolved = _resolved()
    items = _items(10)
    fake = _Endpoint([[decision_reply(*({"over": False},) * 10)]],
                     {i.context: _timed_out() for i in items})
    got = _decide(fake, items, resolved=resolved)
    assert fake.sent < 10
    assert {r.backend for r in got.items} == {STRUCTURED} and got.errors == ()
    assert fake.calls == 2   # ten items, chunks of at most MAX_ITEMS_PER_CALL


# ---- the usage guard, end to end (Task 3, ruling 10) ----
def test_a_native_row_with_counts_and_a_rate_but_no_cost_is_unpriced(client):
    """A native stage whose provider reports counts and no cost files a
    `decision_mode: native` row, and a rate set for exactly that model prices
    none of it: the summary reports it unpriced, never modelled."""
    resolved = _native_resolution(client, fallback=False)
    rates = {"prompt_usd_per_1k": 0.01, "completion_usd_per_1k": 0.02}
    facts.state(DECIDER[0], DECIDER[1], rates=rates)
    pricing.write_pricing({DECIDER[1]: rates})

    class _Counting(FakeLLM):
        async def decide_native(self, item, conn, usage=None, *, retries=None):
            result = await super().decide_native(item, conn, usage, retries=retries)
            usage.update({"prompt_tokens": 420, "completion_tokens": 3})
            return result

    _decide(_Counting([["unused"]], decisions=[_yes()]), [_item()], resolved=resolved)
    (row,) = _rows()
    assert row["decision_mode"] == NATIVE and row.get("cost_usd") is None
    totals = store.usage.summary(days=1)["totals"]
    assert totals["unpriced_calls"] == 1
    assert totals["modelled_usd"] == 0.0 and totals["modelled_calls"] == 0
    assert totals["prompt_tokens"] == 420


# ---- the capture (spec 9.4, Task 6) ----
class _Captures(list):
    """A capture that keeps each `(messages, outcome, conn)` it is handed."""

    async def __call__(self, messages, outcome, conn):
        self.append((messages, outcome, conn))


def _choice_item() -> Item:
    return Item("Seraphine looks between the two of them.",
                (Choice("who", "Who speaks first?",
                        (Option("mara", "Mara"), Option("winifred", "Winifred"))),))


def test_capture_records_a_native_call_with_its_distribution(client):
    """A native item is one call, and its capture is the request as sent --
    the normalised body (`llm.native_body`), holding no key or URL -- with
    its normalised answer and the distribution the endpoint reported, on
    the stage's target without the sampler preset it never sent (M4)."""
    resolved = _native_resolution(client, fallback=False)
    own = resolved.attempts[0].target
    item = _choice_item()
    reported = ItemResult({"who": Answer("mara", distribution={"mara": 0.7,
                                                               "winifred": 0.3})})
    fake = FakeLLM([["unused"]], decisions=[reported])
    captured = _Captures()
    _decide(fake, [item], resolved=resolved, capture=captured)
    ((messages, outcome, conn),) = captured
    _item_sent, sent, _retries = fake.native_requests[0]
    assert messages == [{"role": "user", "content": json.dumps(
        llm.native_body(item, sent), indent=2, ensure_ascii=False)}]
    body = json.loads(messages[0]["content"])
    assert body["model"] == DECIDER[1] and body["state"] == item.context
    assert set(body["questions"]) == {"who"}
    key = resolved.attempts[0].target.api_key
    assert key and key not in messages[0]["content"]
    assert "http" not in messages[0]["content"]
    assert outcome == {"mode": NATIVE, "provider": DECIDER[0], "model": DECIDER[1],
                       "items": [{"backend": NATIVE, "answers": {"who": {
                           "answer": "mara",
                           "distribution": {"mara": 0.7, "winifred": 0.3}}}}],
                       "stage": 0, "at": [0]}
    assert conn.sampling == wire.Sampling()
    assert conn.account.decision_mode == NATIVE
    assert sent == conn
    # The resolution's own target keeps its preset.
    assert resolved.attempts[0].target is own


def test_each_native_item_is_one_capture_and_a_fallback_stage_its_own(client):
    resolved = _native_resolution(client, fallback=True)
    items = _items(3)
    native: dict[str, object] = {i.context: _yes() for i in items}
    native[items[1].context] = LLMError("network", "connection reset")
    fake = _Endpoint([[decision_reply({"over": False})]], native)
    captured = _Captures()
    _decide(fake, items, resolved=resolved, capture=captured)
    modes = sorted((o["mode"], o.get("error", "")) for _m, o, _c in captured)
    assert modes == [(NATIVE, ""), (NATIVE, ""), (NATIVE, "network: connection reset"),
                     (STRUCTURED, "")]
    (failed,) = [(m, o, c) for m, o, c in captured if "error" in o]
    # A failed call that was sent: its request, its route, its error.
    assert json.loads(failed[0][0]["content"])["state"] == items[1].context
    assert (failed[1]["provider"], failed[1]["model"]) == DECIDER
    (fallen,) = [(m, o) for m, o, _c in captured if o["mode"] == STRUCTURED]
    assert fallen[0] == fake.requests[0]["messages"]
    assert (fallen[1]["provider"], fallen[1]["model"]) == SPARE


def test_an_item_refused_unsent_is_captured_with_no_messages(client):
    """The ruling for a call nothing went out for -- an item the endpoint
    cannot represent (`decisions.native_gap`), refused before any stamp:
    it is a failed call, so it is captured with its error, but its messages
    are `[]`. `messages` is the request AS SENT, and none was; a body built
    for it would record a request no provider ever saw. The structured
    fallback that then answers it is captured as sent."""
    wide = Item("Seraphine weighs the roster.",
                (Choice("who", "Who steps forward?",
                        tuple(Option(f"o{n}", f"Candidate {n}") for n in range(255)),
                        allow_none=True),))
    resolved = _native_resolution(client, fallback=True)
    fake = FakeLLM([[decision_reply({"who": "o3"})]], decisions=[_yes()])
    captured = _Captures()
    _decide(fake, [wide], resolved=resolved, capture=captured)
    (refused, answered) = captured
    assert refused[0] == []
    assert refused[1] == {"mode": NATIVE, "provider": DECIDER[0], "model": DECIDER[1],
                          "error": f"bad_response: {decisions.native_gap(wide)}",
                          "stage": 0, "at": [0]}
    assert answered[0] == fake.requests[0]["messages"]
    assert answered[1]["mode"] == STRUCTURED


def test_a_call_the_clock_refused_unsent_is_captured_with_no_messages(client):
    """The same ruling for the structured backend: a chunk absorb's clock
    refused before it went out (`BudgetRefused`) sent nothing, so its
    capture carries no messages, only the refusal."""
    _structured_store(client, fallback=False)

    async def around(call, holder):
        call.close()
        raise _budget_refused()

    captured = _Captures()
    with pytest.raises(LLMError):
        _decide(FakeLLM([[decision_reply({"over": True})]]), [_item()],
                resolved=_resolved(), around=around, capture=captured)
    ((messages, outcome, _conn),) = captured
    assert messages == [] and outcome["error"].startswith("timeout: ")


def test_the_native_capture_renders_off_the_loop(client, monkeypatch):
    """The capture's body is built in a worker thread, as F's prompt is
    (`test_the_prompt_renders_off_the_event_loop`)."""
    import threading

    resolved = _native_resolution(client, fallback=False)
    built_on: list[int] = []
    real = llm.native_body

    def spy(item, conn):
        built_on.append(threading.get_ident())
        return real(item, conn)

    monkeypatch.setattr(llm, "native_body", spy)

    async def go():
        await inference.decide("scene-break", [_item()],
                               client=FakeLLM([["unused"]], decisions=[_yes()]),
                               resolved=resolved, capture=_Captures())
        return threading.get_ident()

    loop_thread = asyncio.run(go())
    assert len(built_on) == 1 and built_on[0] != loop_thread


@pytest.mark.parametrize("mode", [NATIVE, STRUCTURED])
def test_no_messages_are_built_without_a_capture(client, monkeypatch, mode):
    """Without a capture, nothing is built for one: no native request body
    and no outcome, on either backend. With one, each is built once per
    call -- the control that shows the spies are on the path."""
    if mode == NATIVE:
        resolved = _native_resolution(client, fallback=False)
    else:
        _structured_store(client, fallback=False)
        resolved = _resolved()
    built: list[str] = []

    def spy(name, real):
        def wrapper(*args, **kwargs):
            built.append(name)
            return real(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(inference, "_native_request",
                        spy("request", inference._native_request))
    monkeypatch.setattr(inference, "_outcome", spy("outcome", inference._outcome))

    def fake():
        return FakeLLM([[decision_reply({"over": True})]], decisions=[_yes()])

    got = _decide(fake(), [_item()], resolved=resolved)
    assert got.items[0].backend == mode and built == []
    _decide(fake(), [_item()], resolved=resolved, capture=_Captures())
    assert sorted(built) == (["outcome", "request"] if mode == NATIVE else ["outcome"])


class _Held(FakeLLM):
    """A gateway whose structured call goes out and never answers."""

    def __init__(self):
        super().__init__([["unused"]], decisions=[_yes()])
        self.out = asyncio.Event()

    async def complete(self, messages, conn, usage=None, *, schema=None, retries=None):
        self._stamp(usage, conn)
        self.out.set()
        await asyncio.Event().wait()


@pytest.mark.parametrize("mode", [NATIVE, STRUCTURED])
def test_a_cancelled_call_is_not_captured(client, mode):
    """A cancel is the player's own act, not a failure to diagnose: nothing
    is captured for a call it cut off, on either backend."""
    if mode == NATIVE:
        resolved = _native_resolution(client, fallback=True)
        items = _items(6)
        fake = _Endpoint([["unused"]], {i.context: HOLD for i in items})
        out = fake.full
    else:
        _structured_store(client, fallback=False)
        resolved, items = _resolved(), [_item()]
        fake = _Held()
        out = fake.out
    captured = _Captures()

    async def main():
        task = asyncio.create_task(inference.decide("scene-break", items, client=fake,
                                                    resolved=resolved, capture=captured))
        await out.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert captured == []
    assert {r["status"] for r in _rows()} == {"aborted"}


@pytest.mark.parametrize("mode", [NATIVE, STRUCTURED])
def test_a_raising_capture_leaves_the_decision_and_the_ok_row(client, mode, caplog):
    """I4: the capture runs outside the meter and is guarded, so a capture
    that raises neither fails the answered decision nor turns its `ok` row
    into an error; it is logged as a warning."""
    if mode == NATIVE:
        resolved = _native_resolution(client, fallback=False)
    else:
        _structured_store(client, fallback=False)
        resolved = _resolved()

    async def capture(messages, outcome, conn):
        raise RuntimeError("the prompt log is full")

    fake = FakeLLM([[decision_reply({"over": True})]], decisions=[_yes()])
    with caplog.at_level("WARNING", logger="grimoire.inference"):
        got = _decide(fake, [_item()], resolved=resolved, capture=capture)
    assert got.items[0].answers["over"] == Answer(True)
    assert got.items[0].backend == mode
    (row,) = _rows()
    assert (row["status"], row["decision_mode"]) == ("ok", mode)
    assert any("the prompt log is full" in r.getMessage() for r in caplog.records
               if r.levelname == "WARNING")


# ---- call records (01a-S1) ----
def test_each_native_item_is_one_call_record(client):
    """One `CallRecord` per native item, carrying its ledger row; the items
    settle concurrently, so the records are compared as a set."""
    resolved = _native_resolution(client, fallback=False)
    fake = FakeLLM([["unused"]], decisions=[_yes()])
    got = _decide(fake, _items(3), resolved=resolved)
    assert sorted(c.items for c in got.calls) == [(0,), (1,), (2,)]
    assert {(c.stage, c.mode, c.hop, c.error_kind) for c in got.calls} == {
        (0, NATIVE, "", "")}
    assert len(got.calls) == len(got.usage) == 3
    assert all(c.row in got.usage for c in got.calls)


def test_a_mixed_chain_names_each_calls_stage_and_batch_items(client):
    """Items a connection-wide failure held back are never sent and have no
    record; the fallback stage's one structured call carries them by their
    BATCH indices, and its stage is 1. The failed call keeps its kind and
    status, never the provider's words."""
    resolved = _native_resolution(client, fallback=True)
    items = _items(10)
    native: dict[str, object] = {i.context: _yes() for i in items}
    native[items[0].context] = LLMError("auth", "invalid key", status=401)
    fake = _Endpoint([[decision_reply(*({"over": False},) * 7)]], native)
    got = _decide(fake, items, resolved=resolved)
    first = [c for c in got.calls if c.stage == 0]
    assert len(first) == fake.sent == NATIVE_CONCURRENCY
    assert all(c.mode == NATIVE for c in first)
    (failed,) = [c for c in first if c.error_kind]
    assert (failed.items, failed.error_kind, failed.error_status) == ((0,), "auth", 401)
    assert "invalid key" not in repr(dataclasses.asdict(failed))
    (last,) = [c for c in got.calls if c.stage == 1]
    assert last.mode == STRUCTURED and last.items == (0, 4, 5, 6, 7, 8, 9)
    assert last.row is not None and last.row["decision_mode"] == STRUCTURED
    assert got.calls[-1] is last
    assert sorted(map(id, (c.row for c in got.calls))) == sorted(map(id, got.usage))


def test_a_native_item_refused_unsent_has_a_record_and_no_row(client):
    wide = Item("Seraphine weighs the roster.",
                (Choice("who", "Who steps forward?",
                        tuple(Option(f"o{n}", f"Candidate {n}") for n in range(255)),
                        allow_none=True),))
    resolved = _native_resolution(client, fallback=False)
    got = _decide(FakeLLM([["unused"]], decisions=[_yes()]), [_item(), wide],
                  resolved=resolved)
    (unsent,) = [c for c in got.calls if c.items == (1,)]
    assert unsent.row is None
    assert (unsent.error_kind, unsent.error_status) == ("bad_response", None)
    assert len(got.usage) == 1


# ---- capture outcomes name their stage and batch indices (01b-S1) ----


def test_a_fallback_stages_capture_names_batch_indices(client):
    """01b §3.2: a native stage's outcomes name each item's batch index, and
    the fallback stage's one structured call names stage 1 and the BATCH
    indices of the items it took over -- a failed item and the ones a
    connection-wide failure held back."""
    resolved = _native_resolution(client, fallback=True)
    items = _items(10)
    native: dict[str, object] = {i.context: _yes() for i in items}
    native[items[0].context] = LLMError("auth", "invalid key", status=401)
    fake = _Endpoint([[decision_reply(*({"over": False},) * 7)]], native)
    captured = _Captures()
    _decide(fake, items, resolved=resolved, capture=captured)
    first = sorted(o["at"] for _m, o, _c in captured if o["stage"] == 0)
    assert first == [[0], [1], [2], [3]]
    (last,) = [o for _m, o, _c in captured if o["stage"] == 1]
    assert last["mode"] == STRUCTURED and last["at"] == [0, 4, 5, 6, 7, 8, 9]


# ---- per-item provenance (01d-S2, spec 01d §5.6) ----
def _named(target: wire.Target) -> tuple[str, str, str]:
    return (target.kind, target.provider_id, target.model)


def test_each_native_item_names_its_server(client):
    resolved = _native_resolution(client, fallback=False)
    items = _items(2)
    got = _decide(_Endpoint([["unused"]], {i.context: _yes() for i in items}), items,
                  resolved=resolved)
    named = _named(resolved.chain.primary)
    assert named[1:] == DECIDER
    assert [r.served for r in got.items] == [named, named]


def test_a_native_items_scripted_server_is_overwritten(client):
    resolved = _native_resolution(client, fallback=False)
    fake = FakeLLM([["unused"]], decisions=[ItemResult(
        {"over": Answer(True)}, served=("anthropic", "elsewhere", "vendor/other"))])
    got = _decide(fake, [_item()], resolved=resolved)
    assert got.items[0].served == _named(resolved.chain.primary)


def test_items_a_mixed_chain_answered_name_each_stage(client):
    resolved = _native_resolution(client, fallback=True)
    items = _items(2)
    got = _decide(_Endpoint([[decision_reply({"over": False})]],
                            {items[0].context: _yes(),
                             items[1].context: LLMError("network", "connection reset")}),
                  items, resolved=resolved)
    primary, fallback = resolved.attempts[0].target, resolved.attempts[1].target
    assert [r.served for r in got.items] == [_named(primary), _named(fallback)]
    assert [r.served[1:] for r in got.items] == [DECIDER, SPARE]
    # The decision's own field keeps its two-part shape.
    assert got.served == (DECIDER, SPARE)


def test_an_item_a_native_fallback_stage_answered_names_the_fallback(client):
    """A structured primary fails; the native fallback stage answers on its
    own target (`named`), which the item names."""
    resolved = _structured_resolution(client, fallback_mode=NATIVE)
    wire = _Wire(streams=[LLMError("bad_response", "upstream exploded", status=500)],
                 decides=[_yes()])
    got = _decide(_real(wire, retries=0), [_item()], resolved=resolved)
    assert got.items[0].answers["over"] == Answer(True) and wire.decided == [SPARE[1]]
    assert got.items[0].served == _named(resolved.attempts[1].target)
    assert got.items[0].served[1:] == SPARE


def test_triggers_read_the_kind_decide_stamped(client):
    """01d-C2a end to end: `triggers` finds the low margin through the kind
    `decide` stamped on the item, and finds nothing for another kind."""
    resolved = _native_resolution(client, fallback=False)
    fake = FakeLLM([["unused"]], decisions=[
        ItemResult({"over": Answer(True, probability=0.55)}),
        ItemResult({"over": Answer(True, probability=0.95)})])
    got = _decide(fake, _items(2), resolved=resolved)
    kind = resolved.chain.primary.kind
    (found,) = decisions.triggers(got.items, question="over", escalate_on=("low_margin",),
                                  margins={kind: 0.2})
    assert (found.index, found.trigger, found.margin) == (0, "low_margin",
                                                          pytest.approx(0.1))
    assert decisions.triggers(got.items, question="over", escalate_on=("low_margin",),
                              margins={"openai_compatible": 0.2}) == ()
    assert len(fake.native_requests) == 2 and fake.calls == 0
