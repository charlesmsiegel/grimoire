"""`inference.decide`'s chain of stages (slice H, Task 4; spec 5.5, 7.4).

A native-only Decision model is resolved `"native"` by the resolver (Task 5),
so these tests stand on a real decide resolution on an isolated format-2
store; where a shape no resolver builds is the subject (a native fallback,
an attempt both native and generating), `dataclasses.replace` lays the mode
onto a real resolution's attempt. Every answer comes from `llm_fakes`
(a `FakeLLM` scripted with `decisions=`), or from a real `LLMClient` over a
provider double where the facade's own retry and refusal rules are the
subject. Nothing reaches a provider.

Invented provider names and the codebase's placeholder names only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import decisions, inference, llm, llm_usage, routes
from grimoire.decisions import Answer, Choice, Item, ItemResult, Option, Predicate
from grimoire.inference import NATIVE_CONCURRENCY, Stage
from grimoire.llm import FALLBACK_KEY, LLMClient
from grimoire.llm_errors import LLMError
from grimoire.main import create_app
from grimoire.routes import common
from grimoire.store import pricing
from grimoire.store.inference import facts
from grimoire.store.inference import resolve as inf
from tests.llm_fakes import FakeLLM, decision_reply

from . import inference_fixtures as fx

NATIVE, STRUCTURED = decisions.NATIVE_BACKEND, decisions.STRUCTURED_BACKEND
ACCOUNT = llm_usage.ACCOUNT_KEY
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
            self._stamp(usage, llm._without_fallback(conn))
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
    dict F sends, `FALLBACK_KEY` and all -- the same object, so nothing about
    F's call moves."""
    _structured_store(client, fallback=shape == "fallback_attached")
    resolved = _resolved()
    chain = inference.stages(resolved)
    assert chain == (Stage(STRUCTURED, resolved.conn, None),)
    assert chain[0].conn is resolved.conn
    assert (FALLBACK_KEY in chain[0].conn) == (shape == "fallback_attached")


def test_stages_for_a_native_only_primary(client):
    resolved = _native_resolution(client, fallback=True)
    # As a resolver might hand it over: the fallback attached to the primary.
    primary = dataclasses.replace(resolved.attempts[0], conn={
        **resolved.attempts[0].conn, FALLBACK_KEY: resolved.attempts[1].conn})
    resolved = dataclasses.replace(resolved, attempts=(primary, resolved.attempts[1]))
    native, fallback = inference.stages(resolved)
    assert (native.mode, native.retries) == (NATIVE, None)
    assert FALLBACK_KEY not in native.conn
    assert native.conn == {k: v for k, v in primary.conn.items() if k != FALLBACK_KEY}
    assert (fallback.mode, fallback.retries) == (STRUCTURED, 0)
    assert fallback.conn == resolved.attempts[1].conn
    # Copied, never popped: the resolution still carries what it carried.
    assert primary.conn[FALLBACK_KEY] is resolved.attempts[1].conn


def test_stages_put_no_structured_stage_after_a_native_one(client):
    """An attempt that is native AND generates has one stage, its native one,
    then the fallback's: under ruling 1 no resolution builds it (a model
    that generates is served structured, C1), so a structured stage on it
    would be unreachable and is dropped. Trying native first on a model that
    also generates is a later user decision, made after `evals/run.py --live
    --decide-backend` compares the two backends (spec 16); that decision
    adds the structured stage back beside `decision_mode`."""
    _structured_store(client)
    resolved = _resolved()
    both = dataclasses.replace(resolved, attempts=(
        dataclasses.replace(resolved.attempts[0], decision_mode=NATIVE), resolved.attempts[1]))
    assert inf.generates(both.attempts[0])
    chain = inference.stages(both)
    assert [(s.mode, s.retries) for s in chain] == [(NATIVE, None), (STRUCTURED, 0)]
    assert chain[1].conn == both.attempts[1].conn


def test_stages_for_a_native_fallback(client):
    resolved = _structured_resolution(client, fallback_mode=NATIVE)
    structured, native = inference.stages(resolved)
    assert (structured.mode, structured.retries) == (STRUCTURED, None)
    assert structured.conn == {k: v for k, v in resolved.conn.items() if k != FALLBACK_KEY}
    assert (native.mode, native.retries) == (NATIVE, 0)
    assert native.conn == resolved.attempts[1].conn


def test_a_fallback_known_incapable_is_no_stage(client):
    """A fallback in `fallback_missing` is never sent (spec 5.3): no stage."""
    resolved = dataclasses.replace(_native_resolution(client, fallback=True),
                                   fallback_missing=("generate",))
    assert [s.mode for s in inference.stages(resolved)] == [NATIVE]


def test_stages_copy_and_never_pop(client):
    """M2: deciding through a chain whose first stage is the primary alone
    leaves the resolution's own dict as it was, `FALLBACK_KEY` included."""
    resolved = _structured_resolution(client, fallback_mode=NATIVE)
    before = deepcopy(resolved.conn)
    _decide(FakeLLM([[decision_reply({"over": True})]], decisions=[_yes()]), [_item()],
            resolved=resolved)
    assert resolved.conn == before
    assert resolved.conn[FALLBACK_KEY] is resolved.attempts[1].conn


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
    assert fake.retries == [0] and FALLBACK_KEY not in fake.conn


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
    assert resolved.conn[llm.STRUCTURED_KEY] is True
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
    no `FALLBACK_KEY`, so its schema refusal is re-sent without the mode."""
    fx.decide_only(client, fallback=True)
    _flag("spare", "vendor/spare")
    resolved = _resolved()
    assert resolved.attempts[1].conn[llm.STRUCTURED_KEY] is True
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
    assert holders[0][llm.ATTEMPTED]["model"] == DECIDER[1]


def test_native_rows_carry_operation_and_mode(client):
    resolved = _native_resolution(client, fallback=True)
    before = deepcopy(resolved.attempts[0].conn)
    block = resolved.attempts[0].conn[ACCOUNT]
    fake = FakeLLM([["unused"]], decisions=[_yes()])
    _decide(fake, [_item()], resolved=resolved)
    (row,) = _rows()
    assert (row["operation"], row["decision_mode"]) == ("decide", NATIVE)
    sent = fake.native_requests[0][1]
    assert sent[ACCOUNT] is not block and sent[ACCOUNT]["decision_mode"] == NATIVE
    # E's no-mutation rule: the resolution's own block is as it was.
    assert resolved.attempts[0].conn == before
    assert resolved.attempts[0].conn[ACCOUNT] is block and "decision_mode" not in block


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
    assert FALLBACK_KEY in resolved.conn
    provider = _Wire(streams=[LLMError("auth", "invalid key", status=401), then])
    with pytest.raises(LLMError):
        _decide(_real(provider, retries=0), _chunks(3), resolved=resolved)
    assert len(provider.streamed) == requests


def test_a_budget_refused_native_stage_is_still_the_clocks_through_a_failed_fallback(client):
    """Ruling 3: the absorb's clock refused the native stage, and the fallback
    stage then failed for its own reason. The two compose into one error,
    whose sentence is no longer the clock's -- but the stages are kept as
    `words`, so G's identity call site (`routes.scenes._budget_overrun`)
    still reads its own clock as the cause."""
    resolved = _native_resolution(client, fallback=True)
    fake = FakeLLM([[""]], error=LLMError("network", "connection reset"), decisions=[_yes()])
    calls: list[object] = []

    async def around(call, holder):
        calls.append(call)
        if len(calls) == 1:
            call.close()       # the native call: the clock refuses it unsent
            raise _budget_refused()
        return await call

    with pytest.raises(LLMError) as exc:
        _decide(fake, [_item()], resolved=resolved, around=around)
    assert fake.native_requests == []
    assert exc.value.kind == "timeout"
    assert exc.value.detail != routes.scenes.BUDGET_EXHAUSTED
    assert isinstance(exc.value.words[0], routes.scenes.BudgetRefused)
    assert routes.scenes._budget_overrun(exc.value)


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
