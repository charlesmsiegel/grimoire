"""`inference.decide` over structured generation (slice F, Task 3; spec 7.4, 9.3).

Every test resolves for real on an isolated store and answers through
`llm_fakes`: a `FakeLLM` scripted with `decision_reply` at the gateway seam, or
a real `LLMClient` over provider doubles where the fallback is the subject.
They resolve `scene-break` -- the first decide route (Task 6) -- with
`operation="decide"` directly, rather than through its call site.

Invented provider names and the codebase's placeholder names only.
"""

from __future__ import annotations

import asyncio
import dataclasses
from copy import deepcopy

import pytest
from fastapi import HTTPException

import grimoire.store as store
from grimoire import decisions, inference, llm, llm_usage, prompts, routes, wire
from grimoire.decisions import Choice, Item, Option, Predicate, Score
from grimoire.llm import ATTEMPTED, LLMClient
from grimoire.llm_errors import LLMError
from grimoire.routes import common
from grimoire.store.inference import capabilities, facts, migrate, settings
from grimoire.store.inference import resolve as inf
from tests.llm_fakes import (
    FailingOpenRouter,
    FakeLLM,
    SequencedProvider,
    decision_reply,
    from_entries,
)

from . import inference_baseline as base
from . import inference_fixtures as fx


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)


@pytest.fixture
def client(tmp_path):
    with base.client_at(tmp_path) as c:
        yield c


def _settings(client, body: dict) -> None:
    got = client.put("/api/inference/settings", json=body)
    assert got.status_code == 200, got.text


def _store(client, *, fallback: bool = True) -> None:
    """A format-2 store: the seeded `openrouter` provider at `vendor/active`
    and a `spare` one, the Decision role on the first with `spare` as its
    fallback (or none), and the scene-break route on the Decision role."""
    base._fresh(client)
    base._spare(client)
    assert migrate.ensure().state == "done"
    role = {"selection": {"provider": "openrouter", "model": "vendor/active"},
            "fallback": ({"provider": "spare", "model": "vendor/spare"} if fallback
                         else {"provider": ""})}
    _settings(client, {"roles": {"decision": role},
                       "routes": {"scene_break": {"use": "decision"}}})


def _catalog(conn_id: str, rows: list[dict]) -> None:
    rev = store.llm_connections.read_connection_raw(conn_id)["rev"]
    store.llm_connections.set_cached_models(conn_id, rows, rev)


def _resolved(operation: str = "decide"):
    return inf.resolve("scene-break", operation=operation)


def _rows() -> list[dict]:
    return list(store.usage.calls(days=1))


def _item(context: str = "Mara closes the door behind her.") -> Item:
    return Item(context, (Predicate("over", "Is the scene over?"),))


def _decide(fake, items, **kwargs) -> decisions.Decision:
    kwargs.setdefault("resolved", _resolved())
    return asyncio.run(inference.decide("scene-break", items, client=fake, **kwargs))


def _enum_values(schema: object) -> int:
    """Every enum value anywhere in a JSON Schema."""
    if isinstance(schema, dict):
        own = len(schema["enum"]) if isinstance(schema.get("enum"), list) else 0
        return own + sum(_enum_values(v) for k, v in schema.items() if k != "enum")
    if isinstance(schema, list):
        return sum(_enum_values(v) for v in schema)
    return 0


# ---- the operation ----
def test_decide_returns_one_result_per_item_in_order(client):
    _store(client)
    items = [
        _item(),
        Item("Tobin looks to Seraphine.",
             (Choice("next", "Who speaks next?",
                     (Option("seraphine", "Seraphine"), Option("mara", "Mara")),
                     allow_none=True),)),
        Item("Winifred slams the ledger.",
             (Score("tone", "How tense is it?", ("calm", "uneasy", "tense")),))]
    fake = FakeLLM([[decision_reply({"over": True}, {"next": "mara"}, {"tone": 2})]])
    got = _decide(fake, items)
    assert [r.answers for r in got.items] == [
        {"over": decisions.Answer(True)}, {"next": decisions.Answer("mara")},
        {"tone": decisions.Answer(2)}]
    assert got.backend == "structured"
    assert (got.provider, got.model) == ("openrouter", "vendor/active")
    assert len(got.usage) == 1 and got.usage[0]["task"] == "scene-break"
    assert fake.calls == 1


def test_decide_sends_the_schema_and_the_prompt_carries_it(client):
    _store(client)
    items = [_item()]
    fake = FakeLLM([[decision_reply({"over": False})]])
    _decide(fake, items)
    schema = decisions.schema(items, explain=False)
    assert fake.schemas[-1] == schema
    shown = prompts._env().from_string("{{ s | tojson(indent=2) }}").render(s=schema)
    assert fake.messages[0]["role"] == "system" and shown in fake.messages[0]["content"]
    assert fake.messages == inference.structured_messages(items)
    assert "Mara closes the door behind her." in fake.messages[1]["content"]


def test_decide_asks_for_a_rationale_only_when_explain_is_given(client):
    _store(client)
    fake = FakeLLM([[decision_reply({"over": True}, rationales=["The door is shut."])]])
    got = _decide(fake, [_item()], explain="Say in one sentence what resolved.")
    assert got.items[0].rationale == "The door is shut."
    assert fake.schemas[-1] == decisions.schema([_item()], explain=True)
    assert "Say in one sentence what resolved." in fake.messages[1]["content"]


def test_decide_refuses_a_generate_resolution_and_a_mismatched_task(client):
    _store(client)
    fake = FakeLLM([[decision_reply({"over": True})]])
    with pytest.raises(ValueError, match="cannot decide"):
        _decide(fake, [_item()], resolved=_resolved("generate"))
    with pytest.raises(ValueError, match="cannot decide"):
        asyncio.run(inference.decide("rolling-summary", [_item()], client=fake,
                                     resolved=_resolved()))
    with pytest.raises(ValueError, match="no connection"):
        _decide(fake, [_item()], resolved=dataclasses.replace(_resolved(), attempts=()))
    assert fake.calls == 0 and _rows() == []


def test_an_invalid_request_files_no_row(client):
    _store(client)
    fake = FakeLLM([[decision_reply({"over": True})]])
    for items in ([], [Item("x", ())], [Item("x", (Predicate("answers", "?"),))]):
        with pytest.raises(decisions.DecideRequestError):
            _decide(fake, items)
    assert fake.calls == 0 and _rows() == []


def test_each_chunk_is_its_own_metered_row(client):
    _store(client)
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    fake = FakeLLM([[decision_reply(*[{"over": False}] * 8)],
                    [decision_reply({"over": True})]])
    got = _decide(fake, items)
    assert fake.calls == 2
    assert [r.answers["over"].answer for r in got.items] == [False] * 8 + [True]
    rows = [r for r in _rows() if r["task"] == "scene-break"]
    assert len(rows) == 2 and len(got.usage) == 2
    assert fake.schemas == [decisions.schema(items[:8], explain=False),
                            decisions.schema(items[8:], explain=False)]
    # Both chunks answered by the primary: it is the decision's selection.
    assert got.served == (("openrouter", "vendor/active"),)
    assert (got.provider, got.model) == ("openrouter", "vendor/active")


def test_chunks_answered_by_different_routes_name_every_one(client):
    """A batch's chunks each run down the attempt chain on their own: the
    primary answers the first chunk, fails on the second, and its fallback
    answers that one. `served` names both, in the order they first answered,
    and `provider`/`model` name neither -- the last chunk's route did not
    answer the first chunk's items."""
    _store(client)
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    provider = SequencedProvider([[decision_reply(*[{"over": False}] * 8)],
                                  LLMError("network", "connection reset"),
                                  [decision_reply({"over": True})]])
    got = _decide(LLMClient(openrouter=provider, timeout=0, retries=0), items)
    assert [r.answers["over"].answer for r in got.items] == [False] * 8 + [True]
    assert [r["model"] for r in provider.requests] == [
        "vendor/active", "vendor/active", "vendor/spare"]
    assert got.served == (("openrouter", "vendor/active"), ("spare", "vendor/spare"))
    assert (got.provider, got.model) == ("", "")
    assert len(got.usage) == 2


def test_a_failed_chunk_names_nothing_in_served(client):
    """A chunk that failed answered nothing: beside an answered one, the
    decision names only the selection that answered, and that one is its."""
    _store(client)
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    fake = FakeLLM([[decision_reply(*[{"over": True}] * 8)]],
                   error=LLMError("network", "connection reset"), fail_after=1)
    got = _decide(fake, items)
    assert got.served == (("openrouter", "vendor/active"),)
    assert (got.provider, got.model) == ("openrouter", "vendor/active")


def test_no_chunk_exceeds_the_enum_value_budget(client):
    """OpenAI strict mode's documented cap is 1000 enum values across a
    schema: a batch is chunked under it, not only under eight items."""
    _store(client)
    wide = tuple(Option(f"o{n}", f"Option {n}") for n in range(decisions.MAX_OPTIONS))
    items = [Item(f"Winifred weighs lot {n}.", (Choice("pick", "Which lot?", wide),))
             for n in range(7)]
    fake = FakeLLM([[decision_reply(*[{"pick": "o1"}] * 3)],
                    [decision_reply(*[{"pick": "o1"}] * 3)],
                    [decision_reply({"pick": "o1"})]])
    got = _decide(fake, items)
    assert decisions.MAX_ENUM_VALUES == 1000
    assert fake.calls == 3
    assert all(_enum_values(s) <= decisions.MAX_ENUM_VALUES for s in fake.schemas)
    assert [_enum_values(s) for s in fake.schemas] == [765, 765, 255]
    assert [r.answers["pick"].answer for r in got.items] == ["o1"] * 7
    # One item that alone exceeds the budget could never be sent: refused.
    over = Item("Winifred weighs every lot.",
                tuple(Choice(f"c{n}", "Which lot?", wide) for n in range(4)))
    with pytest.raises(decisions.DecideRequestError, match="enum values"):
        _decide(fake, [over])
    assert fake.calls == 3


def test_chunks_bound_items_and_enum_values_together():
    def scored(n: int) -> Item:
        return Item(str(n), (Score("s", "?", tuple("abcdefghij")),))     # 10 values each
    items = [scored(n) for n in range(20)]
    assert [len(c) for _, c in decisions.chunks(items)] == [8, 8, 4]
    assert [len(c) for _, c in decisions.chunks(items, budget=25)] == [2] * 10
    assert [o for o, _ in decisions.chunks(items, budget=25)] == list(range(0, 20, 2))
    # An item over the budget alone still gets a chunk of its own.
    assert [len(c) for _, c in decisions.chunks(items[:3], budget=5)] == [1, 1, 1]


def test_decide_writes_nothing_into_the_holder(client, monkeypatch):
    """Ruling 9: once the call returns, the holder holds what the facade's
    stamp wrote and nothing `decide` added.

    The stamp is made blind to the account block, so the only way an
    `operation` or `decision_mode` can reach the holder is a direct write --
    which is exactly what this must catch, whatever value it writes."""
    real_account = llm_usage.account

    def blind(usage, target):
        real_account(usage, dataclasses.replace(target, account=wire.Account()))

    monkeypatch.setattr(llm_usage, "account", blind)
    _store(client)
    holders: list[dict] = []

    async def around(call, holder):
        holders.append(holder)        # inspected after `decide` returns
        return await call

    fake = FakeLLM([[decision_reply({"over": True})]])
    _decide(fake, [_item()], around=around)
    (holder,) = holders
    sent = fake.requests[-1]["chain"]
    assert holder[ATTEMPTED] is sent.primary
    stamped: dict = {}
    asyncio.run(FakeLLM([["x"]]).complete([], sent, stamped))
    assert holder == stamped
    assert "operation" not in holder and "decision_mode" not in holder


def test_a_decide_row_files_its_operation_and_mode(client):
    """I6: the row says `decide` and `structured`, and so does the fallback's
    row when the primary fails."""
    _store(client)
    _decide(FakeLLM([[decision_reply({"over": True})]]), [_item()])
    row = _rows()[-1]
    assert (row["operation"], row["decision_mode"]) == ("decide", "structured")

    provider = SequencedProvider([LLMError("network", "connection reset"),
                                  [decision_reply({"over": False})]])
    real = LLMClient(openrouter=provider, timeout=0, retries=0)
    got = _decide(real, [_item()])
    assert got.items[0].answers["over"].answer is False
    assert [r["model"] for r in provider.requests] == ["vendor/active", "vendor/spare"]
    row = _rows()[-1]
    assert row["connection"] == "spare" and got.provider == "spare"
    assert (row["operation"], row["decision_mode"]) == ("decide", "structured")


def test_the_mode_is_stamped_per_call_on_copied_blocks(client):
    """I7: the mode rides new targets; the resolution's own targets are left
    exactly as the resolver wrote them."""
    _store(client)
    resolved = _resolved()
    before = deepcopy(resolved.attempts)
    chain = resolved.chain
    assert chain is not None and chain.fallback is not None
    fake = FakeLLM([[decision_reply({"over": True})]])
    _decide(fake, [_item()], resolved=resolved)
    sent = fake.requests[-1]["chain"]
    # Per field, so the account's other fields (slice E's billing and role)
    # may ride beside these.
    for target in sent.attempts:
        assert (target.account.operation, target.account.decision_mode) == (
            "decide", "structured")
    assert sent.primary is not chain.primary and sent.fallback is not chain.fallback
    # The stamp changes the mode and nothing else.
    assert sent == chain.with_account(decision_mode="structured")
    # Nothing of the resolution moved: same targets, no mode.
    assert resolved.chain == chain
    assert all(t.account.decision_mode == "" for t in chain.attempts)
    assert resolved.attempts == before
    for target in chain.attempts:
        assert target.account.operation == "decide"
    assert resolved.chain.fallback is resolved.attempts[1].target


def test_a_generate_resolution_carries_only_its_operation(client):
    _store(client)
    resolved = _resolved("generate")
    for attempt in resolved.attempts:
        assert attempt.target.account.operation == "generate"
        assert attempt.target.account.decision_mode == ""


# ---- failures ----
def test_a_failed_only_chunk_raises_the_llm_error(client):
    _store(client)
    with pytest.raises(LLMError) as exc:
        _decide(FailingOpenRouter(kind="rate_limit", message="slow down"), [_item()])
    assert exc.value.kind == "rate_limit"
    (row,) = _rows()
    assert (row["status"], row["error"]) == ("error", "rate_limit")
    assert (row["operation"], row["decision_mode"]) == ("decide", "structured")


def test_a_failed_chunk_beside_an_answered_one_is_marked_error(client):
    _store(client)
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    # The second chunk fails after the first answered.
    fake = FakeLLM([[decision_reply(*[{"over": True}] * 8)]],
                   error=LLMError("network", "connection reset"), fail_after=1)
    got = _decide(fake, items)
    assert [r.answers["over"] for r in got.items] == (
        [decisions.Answer(True)] * 8 + [decisions.Answer(None, "error")])
    assert [r["status"] for r in _rows()] == ["ok", "error"]
    assert len(got.usage) == 2

    # The first chunk fails and a later one answers: not raised either.
    items[0] = _item("Winifred waits at the gate.")
    fake = from_entries([
        {"when": {"user_contains": "Winifred waits at the gate."},
         "error": {"kind": "network", "message": "connection reset"}},
        {"when": {}, "reply": decision_reply({"over": False})}])
    got = _decide(fake, items)
    assert [r.answers["over"] for r in got.items] == (
        [decisions.Answer(None, "error")] * 8 + [decisions.Answer(False)])


def test_the_first_error_is_raised_after_every_chunk_was_tried(client):
    _store(client)
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    items[0] = _item("Winifred waits at the gate.")
    fake = from_entries([
        {"when": {"user_contains": "Winifred waits at the gate."},
         "error": {"kind": "auth", "message": "first"}},
        {"when": {}, "error": {"kind": "network", "message": "second"}}])
    with pytest.raises(LLMError) as exc:
        _decide(fake, items)
    assert exc.value.kind == "auth" and fake.calls == 2


class _Captures(list):
    """A capture (spec 9.4) that keeps each `(messages, outcome, target)` it
    is handed, with how many calls `fake` had made by then."""

    def __init__(self, fake=None):
        super().__init__()
        self.fake = fake

    async def __call__(self, messages, outcome, conn):
        self.append((messages, outcome, conn, getattr(self.fake, "calls", None)))


def test_capture_records_each_structured_call_once_it_settles(client):
    """One capture per chunk, handed over after that chunk's call has
    returned: the messages it sent, its mode and normalised answers
    (`decisions.outcome`), and the stage's account-stamped target it was sent
    on."""
    _store(client)
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    fake = FakeLLM([[decision_reply(*[{"over": False}] * 8)],
                    [decision_reply({"over": True})]])
    captured = _Captures(fake)
    got = _decide(fake, items, capture=captured)
    assert [calls for *_, calls in captured] == [1, 2]
    assert [m for m, *_ in captured] == [r["messages"] for r in fake.requests]
    # The target the facade stamped as sent (`llm.ATTEMPTED`): this fake
    # stamps the primary of the chain it was handed.
    assert all(conn is r["chain"].primary
               for (_m, _o, conn, _c), r in zip(captured, fake.requests, strict=True))
    for _m, _o, conn, _c in captured:
        assert conn.account.decision_mode == "structured"
        assert (conn.provider_id, conn.model) == ("openrouter", "vendor/active")
    first, second = (outcome for _m, outcome, _conn, _c in captured)
    assert first == decisions.outcome("structured", "openrouter", "vendor/active",
                                      got.items[:8])
    assert first["mode"] == second["mode"] == "structured"
    assert first["items"][0] == {"backend": "structured",
                                 "answers": {"over": {"answer": False}}}
    assert second["items"] == [{"backend": "structured",
                                "answers": {"over": {"answer": True}}}]


def test_a_schema_refusal_retry_is_one_capture(client):
    """M9: a chunk whose provider refused the structured field and which
    `_ask` sent once more without the mode is one call to the capture, with
    the final outcome -- the answer the re-send got."""
    _store(client, fallback=False)
    _catalog("openrouter", [{"id": "vendor/active",
                             "params": ["temperature", "structured_outputs"]}])
    provider = SequencedProvider([_refused_schema(), [decision_reply({"over": True})]])
    captured = _Captures()
    _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()],
            capture=captured)
    assert len(provider.requests) == 2
    ((messages, outcome, conn, _calls),) = captured
    assert messages == provider.requests[1]["messages"]
    # Named as re-sent: the same attempt, without the structured mode.
    assert isinstance(conn, wire.Target) and not conn.structured
    assert (conn.provider_id, conn.model) == ("openrouter", "vendor/active")
    assert outcome == {"mode": "structured", "provider": "openrouter",
                       "model": "vendor/active",
                       "items": [{"backend": "structured",
                                  "answers": {"over": {"answer": True}}}]}


def test_a_capture_names_the_fallback_that_answered(client):
    """The facade's fallback answered: the capture's target is the one it was
    sent (`llm.ATTEMPTED`), so the prompt log's model, kind and preset name
    the fallback, as the outcome does -- and a preset scoped to the
    primary's connection, which stayed with it, is not reported."""
    _store(client)
    resolved = _resolved()
    warm = {"preset_id": "warm", "preset_name": "Warm", "scope": "connection",
            "params": {"temperature": 0.9}}
    primary = dataclasses.replace(
        resolved.attempts[0],
        target=dataclasses.replace(resolved.attempts[0].target,
                                   sampling=wire.Sampling(**warm)))
    resolved = dataclasses.replace(resolved, attempts=(primary, *resolved.attempts[1:]))
    assert resolved.chain.fallback.provider_id == "spare"
    provider = SequencedProvider([LLMError("network", "connection reset"),
                                  [decision_reply({"over": True})]])
    captured = _Captures()
    _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()],
            resolved=resolved, capture=captured)
    assert [r["model"] for r in provider.requests] == ["vendor/active", "vendor/spare"]
    ((messages, outcome, conn, _calls),) = captured
    assert messages == provider.requests[1]["messages"]
    assert (outcome["provider"], outcome["model"]) == ("spare", "vendor/spare")
    assert (conn.provider_id, conn.model) == ("spare", "vendor/spare")
    assert isinstance(conn, wire.Target)
    assert conn.sampling.preset_id != "warm"
    assert conn.account.decision_mode == "structured"


def test_capture_records_a_failed_call_with_its_error(client):
    _store(client, fallback=False)
    fake = FakeLLM([[""]], error=LLMError("rate_limit", "slow down"))
    captured = _Captures(fake)
    with pytest.raises(LLMError):
        _decide(fake, [_item()], capture=captured)
    ((messages, outcome, conn, calls),) = captured
    assert calls == 1 and messages == fake.requests[0]["messages"]
    assert outcome == {"mode": "structured", "provider": "openrouter",
                       "model": "vendor/active", "error": "rate_limit: slow down"}
    assert conn is fake.requests[0]["chain"].primary


def test_a_timed_out_pick_is_captured_with_its_error(client):
    """A caller's budget (`around`) that cuts the call off raises inside the
    call, so the call settles failed and is captured with the timeout."""
    _store(client)

    async def budget(call, holder):
        await call
        raise LLMError("timeout", "the decision ran past its budget")

    fake = FakeLLM([[decision_reply({"over": True})]])
    captured = _Captures(fake)
    with pytest.raises(LLMError):
        _decide(fake, [_item()], around=budget, capture=captured)
    ((messages, outcome, _conn, _calls),) = captured
    assert messages == fake.requests[0]["messages"]
    assert outcome["error"] == "timeout: the decision ran past its budget"
    assert "items" not in outcome


def test_the_prompt_renders_off_the_event_loop(client, monkeypatch):
    """`decide` is awaited by detached turns (the speaker pick), which must not
    block the loop on the template loader's file reads: each chunk's prompt is
    rendered in a worker thread, never on the loop thread that awaits it."""
    import threading

    _store(client)
    rendered_on: list[int] = []
    real = inference.structured_messages

    def spy(items, **kwargs):
        rendered_on.append(threading.get_ident())
        return real(items, **kwargs)

    monkeypatch.setattr(inference, "structured_messages", spy)
    fake = FakeLLM([[decision_reply({"over": True})]])

    async def go():
        loop_thread = threading.get_ident()
        await inference.decide("scene-break", [_item()], client=fake, resolved=_resolved())
        return loop_thread

    loop_thread = asyncio.run(go())
    assert len(rendered_on) == 1 and rendered_on[0] != loop_thread


def test_around_runs_inside_the_meter(client):
    """I2: a budget that cuts the call off is filed as one error/timeout row."""
    _store(client)

    async def budget(call, holder):
        await call
        raise LLMError("timeout", "the decision ran past its budget")

    with pytest.raises(LLMError) as exc:
        _decide(FakeLLM([[decision_reply({"over": True})]]), [_item()], around=budget)
    assert exc.value.kind == "timeout"
    (row,) = _rows()
    assert (row["status"], row["error"], row["task"]) == ("error", "timeout", "scene-break")


# ---- the resolver ----
def test_decision_mode_is_structured_on_every_decide_attempt(client):
    _store(client)
    decide = _resolved()
    assert [a.decision_mode for a in decide.attempts] == ["structured", "structured"]
    assert decide.decision_mode == "structured"
    generate = _resolved("generate")
    assert [a.decision_mode for a in generate.attempts] == ["", ""]
    assert generate.decision_mode is None
    # The replaced attempts keep their targets: the chain still names them.
    assert decide.chain.fallback is decide.attempts[1].target


def test_a_fallback_without_structured_mode_still_answers(client):
    """Review Focus 2: the primary is flagged and fails; the fallback, sent no
    structured mode, answers with the JSON in prose; the answers parse."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/active",
                             "params": ["temperature", "structured_outputs"]}])
    resolved = _resolved()
    assert resolved.chain.primary.structured is True
    assert resolved.chain.fallback.structured is False
    reply = f"Here is my answer: {decision_reply({'over': True})} Hope that helps."
    provider = SequencedProvider([LLMError("network", "connection reset"), [reply]])
    got = _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()],
                  resolved=resolved)
    assert got.items[0].answers["over"] == decisions.Answer(True)
    first, second = provider.requests
    assert first["kwargs"]["schema"] == decisions.schema([_item()], explain=False)
    assert "schema" not in second["kwargs"]
    assert first["messages"] == second["messages"]


def _refused_schema() -> LLMError:
    return LLMError("bad_response",
                    "response_format: json_schema strict mode is not supported", status=400)


def test_a_refused_schema_with_nowhere_to_fall_is_retried_once_without_the_mode(client):
    """Spec M-4 (ruling 3): a flagged attempt whose provider refuses the
    structured field, with no other attempt to fall to, is sent once more as
    the same attempt without the mode -- the schema is in the prompt, so the
    reply still parses. The retry is its own metered call, and the call that
    worked before slice F (prompt-only) still works after it."""
    _store(client, fallback=False)
    _catalog("openrouter", [{"id": "vendor/active",
                             "params": ["temperature", "structured_outputs"]}])
    resolved = _resolved()
    assert resolved.chain.primary.structured is True and resolved.chain.fallback is None
    provider = SequencedProvider([_refused_schema(), [decision_reply({"over": True})]])
    got = _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()],
                  resolved=resolved)
    assert got.items[0].answers["over"] == decisions.Answer(True)
    first, second = provider.requests
    assert first["kwargs"]["schema"] == decisions.schema([_item()], explain=False)
    assert "schema" not in second["kwargs"]
    assert first["messages"] == second["messages"] and first["model"] == second["model"]
    rows = _rows()
    assert [(r["task"], r["status"]) for r in rows] == [("scene-break", "error"),
                                                       ("scene-break", "ok")]
    assert [r["decision_mode"] for r in rows] == ["structured", "structured"]
    assert len(got.usage) == 2
    # The resolution's own target still says what the resolver decided.
    assert resolved.chain.primary.structured is True


def test_a_refused_schema_is_retried_once_only(client):
    _store(client, fallback=False)
    _catalog("openrouter", [{"id": "vendor/active",
                             "params": ["temperature", "structured_outputs"]}])
    second = LLMError("bad_response", "still no", status=400)
    provider = SequencedProvider([_refused_schema(), second, [decision_reply({"over": True})]])
    with pytest.raises(LLMError) as exc:
        _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()])
    assert exc.value is second and len(provider.requests) == 2


def test_another_refusal_is_not_retried_without_the_mode(client):
    """Only the structured field refused earns the retry: any other 400 (an
    unknown model, an overflow) is the attempt's failure, as it always was."""
    _store(client, fallback=False)
    _catalog("openrouter", [{"id": "vendor/active",
                             "params": ["temperature", "structured_outputs"]}])
    refused = LLMError("bad_response", "vendor/active is not a valid model ID", status=400)
    provider = SequencedProvider([refused, [decision_reply({"over": True})]])
    with pytest.raises(LLMError):
        _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()])
    assert len(provider.requests) == 1


def test_a_refused_schema_with_a_fallback_falls_and_is_not_retried(client):
    """With a fallback to fall to, the fallback is the retry (spec 7.2, I3):
    the primary is not re-sent without the mode."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/active",
                             "params": ["temperature", "structured_outputs"]}])
    provider = SequencedProvider([_refused_schema(), [decision_reply({"over": True})]])
    got = _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()])
    assert got.items[0].answers["over"] == decisions.Answer(True)
    assert [r["model"] for r in provider.requests] == ["vendor/active", "vendor/spare"]
    assert len(_rows()) == 1


def _flagged(*, spare: bool = False) -> None:
    """The primary's catalog lists structured outputs; the fallback's too, when
    `spare`."""
    rows = [{"id": "vendor/active", "params": ["temperature", "structured_outputs"]}]
    _catalog("openrouter", rows)
    if spare:
        _catalog("spare", [{"id": "vendor/spare",
                            "params": ["temperature", "structured_outputs"]}])


def _busy() -> LLMError:
    return LLMError("rate_limit", "slow down", retry_after=30.0, status=429)


def test_a_refused_primary_whose_fallback_fails_is_retried_without_the_mode(client):
    """Brutal-1 #1: a refused schema with a failing fallback used to end the
    decision, and the primary -- which answers prompt-only -- was never asked.
    It is re-sent once, alone and without the mode, after the fallback fails."""
    _store(client)
    _flagged()
    seen: list[tuple[str, str | None]] = []
    provider = SequencedProvider([_refused_schema(), _busy(),
                                  [decision_reply({"over": True})]])
    fake = LLMClient(openrouter=provider, timeout=0, retries=0,
                     observer=lambda target, err: seen.append(
                         (target.model, err.kind if err else None)))
    got = _decide(fake, [_item()])
    assert got.items[0].answers["over"] == decisions.Answer(True)
    assert [(r["model"], "schema" in r["kwargs"]) for r in provider.requests] == [
        ("vendor/active", True), ("vendor/spare", False), ("vendor/active", False)]
    assert provider.requests[2]["messages"] == provider.requests[0]["messages"]
    assert (got.provider, got.model) == ("openrouter", "vendor/active")
    # The chain is one row, filed with the fallback's rate limit -- the
    # refused field is a mode, not the primary's word -- and the re-send its own.
    rows = _rows()
    assert [(r["status"], r.get("error")) for r in rows] == [("error", "rate_limit"),
                                                            ("ok", None)]
    assert [r["decision_mode"] for r in rows] == ["structured", "structured"]
    assert len(got.usage) == 2
    # The refusal marks nothing; the fallback's 429 and the answer are observed.
    assert seen == [("vendor/spare", "rate_limit"), ("vendor/active", None)]


def test_a_chain_whose_primary_refused_reports_the_fallbacks_failure(client):
    """The facade's own error, before `decide` re-sends anything: the
    fallback's rate limit and the window it named, not the primary's refusal."""
    _store(client)
    _flagged()
    provider = SequencedProvider([_refused_schema(), _busy()])
    fake = LLMClient(openrouter=provider, timeout=0, retries=0)
    with pytest.raises(llm.SchemaRefusalError) as exc:
        asyncio.run(fake.complete([{"role": "user", "content": "x"}], _resolved().chain,
                                  schema=decisions.schema([_item()], explain=False)))
    assert (exc.value.kind, exc.value.retry_after) == ("rate_limit", 30.0)
    assert "json_schema strict mode" in exc.value.detail and "slow down" in exc.value.detail
    primary, fallback = exc.value.attempts
    # The refused attempt, as the target it was sent: one attempt, no chain.
    assert isinstance(primary, wire.Target) and primary.model == "vendor/active"
    assert primary.structured is True
    assert fallback is None
    assert [w.kind for w in exc.value.words] == ["bad_response", "rate_limit"]


def test_a_refused_primary_whose_retry_fails_too_reports_every_route(client):
    """Everything fails: the primary's word is now what it said prompt-only,
    composed with the fallback's failure as any both-failed call is."""
    _store(client)
    _flagged()
    down = LLMError("network", "connection reset")
    provider = SequencedProvider([_refused_schema(), _busy(), down])
    with pytest.raises(LLMError) as exc:
        _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()])
    assert not isinstance(exc.value, llm.SchemaRefusalError)
    assert exc.value.kind == "network"
    assert exc.value.detail == "connection reset — and the fallback failed too: slow down"
    assert len(provider.requests) == 3
    assert [(r["status"], r.get("error")) for r in _rows()] == [("error", "rate_limit"),
                                                               ("error", "network")]


def test_a_refused_fallback_is_retried_without_the_mode(client):
    """Brutal-2 #2: the primary fails on the network, the fallback refuses the
    structured field. The fallback -- not the primary -- is re-sent once,
    alone and without the mode, and answers."""
    _store(client)
    _flagged(spare=True)
    resolved = _resolved()
    assert resolved.chain.fallback.structured is True
    seen: list[tuple[str, str | None]] = []
    provider = SequencedProvider([LLMError("network", "connection reset"),
                                  _refused_schema(), [decision_reply({"over": False})]])
    fake = LLMClient(openrouter=provider, timeout=0, retries=0,
                     observer=lambda target, err: seen.append(
                         (target.model, err.kind if err else None)))
    got = _decide(fake, [_item()], resolved=resolved)
    assert got.items[0].answers["over"] == decisions.Answer(False)
    assert [(r["model"], "schema" in r["kwargs"]) for r in provider.requests] == [
        ("vendor/active", True), ("vendor/spare", True), ("vendor/spare", False)]
    assert (got.provider, got.model) == ("spare", "vendor/spare")
    rows = _rows()
    assert [(r["status"], r.get("error")) for r in rows] == [("error", "network"),
                                                            ("ok", None)]
    assert [(r["decision_mode"], r["model"]) for r in rows] == [
        ("structured", "vendor/spare"), ("structured", "vendor/spare")]
    assert seen == [("vendor/active", "network"), ("vendor/spare", None)]


def test_a_failed_chunks_error_is_its_composed_last_word(client):
    """A chunk whose refused primary is re-sent and fails again reports what
    `decide` would raise for it alone -- every route's failure composed --
    on `Decision.errors`, never the re-send's bare error; and that is what a
    batch with nothing read reports (`common._decide_error`)."""
    _store(client)
    _flagged()
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    provider = SequencedProvider([_refused_schema(), _busy(),
                                  LLMError("network", "connection reset"), ["no json"]])
    got = _decide(LLMClient(openrouter=provider, timeout=0, retries=0), items)
    assert [r.answers["over"].reason for r in got.items] == ["error"] * 8 + ["unreadable"]
    (error,) = got.errors
    assert isinstance(error, LLMError) and not isinstance(error, llm.SchemaRefusalError)
    assert (error.kind, error.detail) == (
        "network", "connection reset — and the fallback failed too: slow down")
    assert common._decide_error(got, "over") is error


def test_a_chunk_answered_on_its_second_re_send_did_not_fail(client):
    """Both routes refused the first chunk: the primary's re-send fails and the
    fallback's answers (garbled), so that chunk failed nothing. The second
    chunk fails on both routes. With nothing read, the batch's error is the
    second chunk's -- not the first chunk's failed re-send on the way to its
    answer (M1)."""
    _store(client)
    _flagged(spare=True)
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    provider = SequencedProvider([_refused_schema(), _refused_schema(),
                                  LLMError("network", "connection reset"), ["no json"],
                                  _busy(), _busy()])
    got = _decide(LLMClient(openrouter=provider, timeout=0, retries=0), items)
    assert len(provider.requests) == 6
    assert [r.answers["over"].reason for r in got.items] == ["unreadable"] * 8 + ["error"]
    (error,) = got.errors
    assert error.kind == "rate_limit"
    assert common._decide_error(got, "over") is error
    assert got.served == (("spare", "vendor/spare"),)


def test_the_first_failed_chunks_error_is_the_batchs(client):
    """Review 2 #5: three chunks -- a network failure, a garbled reply, a rate
    limit. `errors` holds the two failures in chunk order, and with nothing
    read the batch reports the FIRST, which is what `decide` itself raises
    when no chunk answers -- never the last one, nor the garbled chunk.

    Network first, not the rate limit: since slice H a rate limit the facade
    gave up on stops the stage's later chunks (they would meet it too), so a
    rate limit first would leave nothing after it to be reported over."""
    _store(client, fallback=False)
    items = [_item(f"Mara counts to {n}.") for n in range(2 * decisions.MAX_ITEMS_PER_CALL + 1)]
    provider = SequencedProvider([LLMError("network", "connection reset"), ["no json"],
                                  _busy()])
    got = _decide(LLMClient(openrouter=provider, timeout=0, retries=0), items)
    assert len(provider.requests) == 3
    assert [e.kind for e in got.errors] == ["network", "rate_limit"]
    error = common._decide_error(got, "over")
    assert error is got.errors[0]
    assert (error.kind, error.retry_after) == ("network", None)
    # The rate limit is still the chunk's own, window and all.
    assert (got.errors[1].kind, got.errors[1].retry_after) == ("rate_limit", 30.0)


def _clock_after_the_first_call(monkeypatch):
    """An absorb budget (`routes.scenes._Budget`) whose clock runs out the
    moment the first call it ran returns: every later call is refused unsent."""
    clock = [0.0]
    monkeypatch.setattr(routes.scenes, "_clock", lambda: clock[0])
    budget = routes.scenes._Budget(60)

    async def around(call, holder):
        try:
            return await budget.run(call)
        finally:
            clock[0] = 1e6
    return around


@pytest.mark.parametrize("second", ["refused", "server"])
def test_a_re_send_the_clock_stopped_is_the_clocks_however_it_is_composed(
        client, monkeypatch, second):
    """Review 2 #1: the primary refused the structured field, and the fallback
    refused it too (or failed with a 500); the absorb clock then refused each
    prompt-only re-send. The routes' failures compose into one sentence, which
    is no longer the clock's sentinel -- but they are kept (`words`), so the
    absorb still reads its own clock as the cause."""
    _store(client)
    _flagged(spare=True)
    provider = SequencedProvider([
        _refused_schema(),
        _refused_schema() if second == "refused" else LLMError("server", "oops", status=500)])
    with pytest.raises(LLMError) as exc:
        _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()],
                around=_clock_after_the_first_call(monkeypatch))
    assert len(provider.requests) == 2               # the re-sends never left
    assert routes.scenes.BUDGET_EXHAUSTED in exc.value.detail
    assert exc.value.detail != routes.scenes.BUDGET_EXHAUSTED
    assert any(isinstance(w, routes.scenes.BudgetRefused) for w in exc.value.words)
    assert routes.scenes._budget_overrun(exc.value)


def test_a_re_send_cut_off_then_one_refused_is_the_clocks(client):
    """The reviewer's probe, word for word: both routes refuse the field, the
    primary's re-send is cut off by the clock as it runs, and the fallback's
    is refused before it is sent. Before the routes were kept, the absorb read
    this as a provider failure (`_budget_overrun` False)."""
    _store(client)
    _flagged(spare=True)
    provider = SequencedProvider([_refused_schema(), _refused_schema()])
    calls: list[object] = []

    async def around(call, holder):
        calls.append(call)
        if len(calls) == 1:
            return await call
        call.close()
        if len(calls) == 2:
            raise LLMError("timeout", routes.scenes.BUDGET_EXHAUSTED)
        raise routes.scenes.BudgetRefused("timeout", routes.scenes.BUDGET_EXHAUSTED)

    with pytest.raises(LLMError) as exc:
        _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()],
                around=around)
    assert len(calls) == 3
    assert exc.value.detail == (f"{routes.scenes.BUDGET_EXHAUSTED} — and the fallback "
                                f"failed too: {routes.scenes.BUDGET_EXHAUSTED}")
    assert [type(w).__name__ for w in exc.value.words] == ["LLMError", "BudgetRefused"]
    assert routes.scenes._budget_overrun(exc.value)


def test_a_composed_failure_nobodys_clock_stopped_is_not_an_overrun(client):
    """The routes kept are asked, not assumed: two provider failures composed
    are not the absorb's clock."""
    composed = llm.routes_failed([LLMError("network", "connection reset"), _busy()])
    assert [w.kind for w in composed.words] == ["network", "rate_limit"]
    assert not routes.scenes._budget_overrun(composed)
    assert llm.routes_failed([_busy()]).words == ()


def test_a_batch_whose_every_chunk_answered_carries_no_error(client):
    _store(client)
    got = _decide(FakeLLM([[decision_reply({"over": True})]]), [_item()])
    assert got.errors == ()
    assert common._decide_error(got, "over") is None


def test_each_refusing_route_is_retried_once_only(client):
    """Both routes refused: each is re-sent once, in route order, and the
    failure composed from their re-sends is the primary's prompt-only word."""
    _store(client)
    _flagged(spare=True)
    provider = SequencedProvider([_refused_schema(), _refused_schema(),
                                  LLMError("server", "oops", status=500), _busy(),
                                  [decision_reply({"over": True})]])
    with pytest.raises(LLMError) as exc:
        _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()])
    assert [(r["model"], "schema" in r["kwargs"]) for r in provider.requests] == [
        ("vendor/active", True), ("vendor/spare", True),
        ("vendor/active", False), ("vendor/spare", False)]
    assert (exc.value.kind, exc.value.retry_after) == ("server", None)
    assert exc.value.detail == "oops — and the fallback failed too: slow down"
    assert len(_rows()) == 3


# ---- a model that cannot generate is answered natively (slice H, Task 5) ----
def _yes() -> decisions.ItemResult:
    return decisions.ItemResult({"over": decisions.Answer(True)})


def _row_of(view: dict, key: str) -> dict:
    (row,) = [r for r in view["routes"] if r["key"] == key]
    return row


def test_resolve_serves_a_decide_only_primary_natively(client):
    """Ruling 1: a Decision model whose catalog says it only decides is
    served by its provider's decisions endpoint -- nothing missing, nothing
    refused -- and the generating fallback on another provider rides nothing:
    it is a stage of its own."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/active", "outputs": ["decisions"]}])
    resolved = _resolved()
    assert resolved.decision_mode == "native"
    assert resolved.missing == () and inf.refusal(resolved) is None
    assert settings._problem(resolved) is None
    assert resolved.chain.primary is resolved.attempts[0].target
    assert routes.common._usable(resolved).chain.primary is resolved.attempts[0].target
    # The fallback is attached to nothing, and is its own stage.
    fallback = resolved.attempts[1]
    assert (fallback.provider_id, fallback.decision_mode) == ("spare", "structured")
    assert resolved.fallback_missing == () and resolved.fallback_problem is None
    assert resolved.chain.fallback is None and not resolved.rides
    assert inference.stages(resolved) == (
        inference.Stage("native", resolved.chain, None),
        inference.Stage("structured", wire.Chain(fallback.target), 0))
    # The settings view reads it the same way: no problem on the route row or
    # on the Decision card, and both say the model is answered natively.
    view = client.get("/api/inference/settings").json()
    row, card = _row_of(view, "scene_break"), view["roles"]["decision"]
    assert row["problem"] is None and card["problem"] is None
    assert row["decision_mode"] == card["decision_mode"] == "native"
    # What decide sends is the native request, and nothing is completed.
    fake = FakeLLM([["unused"]], decisions=[_yes()])
    got = _decide(fake, [_item()], resolved=resolved)
    assert got.items[0].answers["over"] == decisions.Answer(True)
    assert got.backend == "native" and fake.calls == 0
    [(_item_sent, conn, retries)] = fake.native_requests
    assert (conn.provider_id, conn.model, retries) == ("openrouter", "vendor/active", None)
    # A generate resolution of the same role is unchanged: refused.
    generate = _resolved("generate")
    assert generate.missing == ("generate",) and generate.decision_mode is None
    assert inf.refusal(generate)[1]["kind"] == "incapable"
    # Without a fallback it is still served: natively, and alone.
    _settings(client, {"roles": {"decision": {"fallback": {"provider": ""}}}})
    alone = _resolved()
    assert alone.decision_mode == "native" and alone.missing == ()
    assert inf.refusal(alone) is None
    assert inference.stages(alone) == (inference.Stage("native", alone.chain, None),)


def test_an_openrouter_non_text_model_on_decision_resolves_native(client):
    """I8: an OpenRouter catalog row whose `outputs` lacks `text` (here an
    embedding model) is `generate: no`, and says nothing of `decide_native`
    -- a list without `decisions` is unknown, not `no`. So it resolves
    `native` with no refusal: the deliberate consequence of spec 5.3's
    "unknown is allowed". The native request it makes may fail, and then the
    role fallback answers; refusing it here would refuse on a guess."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/active", "outputs": ["embeddings"]}])
    resolved = _resolved()
    caps = resolved.attempts[0].capabilities
    assert (caps["generate"].value, caps["generate"].source) == ("no", "catalog")
    assert caps["decide_native"].value == "unknown"
    assert resolved.decision_mode == "native"
    assert resolved.missing == () and inf.refusal(resolved) is None


def test_an_unknown_native_capability_is_not_refused(client):
    """`generate` known `no` -- here the user's own word, with no catalog --
    and `decide_native` unknown: native, and not refused (spec 5.3)."""
    _store(client)
    got = client.put("/api/llm-connections/openrouter/facts",
                     json={"model": "vendor/active", "overrides": {"generate": "no"}})
    assert got.status_code == 200, got.text
    resolved = _resolved()
    caps = resolved.attempts[0].capabilities
    assert (caps["generate"].value, caps["generate"].source) == ("no", "user")
    assert caps["decide_native"].value == "unknown"
    assert inf.native_only(caps)
    assert resolved.decision_mode == "native"
    assert resolved.missing == () and inf.refusal(resolved) is None


def test_a_decide_only_model_with_a_same_provider_fallback_keeps_it(client):
    """The single-provider store -- one OpenRouter account, a decide-only
    Decision model and one of its generating models behind it -- keeps its
    fallback: behind a primary that cannot generate it is a stage of its own
    on another backend, never a retry of the call that failed (`_apart`)."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/decider", "outputs": ["decisions"]},
                            {"id": "vendor/active", "outputs": ["text"]}])
    _settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/decider"},
        "fallback": {"provider": "openrouter", "model": "vendor/active"}}}})
    resolved = _resolved()
    assert [a.decision_mode for a in resolved.attempts] == ["native", "structured"]
    assert resolved.fallback_problem is None and resolved.fallback_missing == ()
    assert inf.refusal(resolved) is None
    primary, fallback = resolved.attempts
    assert (fallback.provider_id, fallback.model) == ("openrouter", "vendor/active")
    assert resolved.chain.fallback is None
    assert inference.stages(resolved) == (inference.Stage("native", wire.Chain(primary.target), None),
                                          inference.Stage("structured", wire.Chain(fallback.target), 0))
    # Everywhere else a same-provider fallback is still a retry, and dropped:
    # on a generate resolution of the same role (refused)...
    generate = _resolved("generate")
    assert len(generate.attempts) == 1 and generate.missing == ("generate",)
    assert generate.fallback_missing == ()
    assert generate.fallback_problem == inf.SAME_PROVIDER
    # ...and behind a decide primary that can generate.
    _settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/active"},
        "fallback": {"provider": "openrouter", "model": "vendor/decider"}}}})
    capable = _resolved()
    assert len(capable.attempts) == 1 and capable.chain.fallback is None
    assert capable.fallback_problem == inf.SAME_PROVIDER
    # The Decision card, which now reads its role as a decision, says so too.
    card = client.get("/api/inference/settings").json()["roles"]["decision"]
    assert card["fallback_problem"] == inf.SAME_PROVIDER and card["problem"] is None


def test_a_same_provider_fallback_that_can_do_neither_is_reported(client):
    """Kept behind a primary that cannot generate, a same-provider fallback
    that cannot serve either backend is reported in `fallback_missing` like
    any other incapable fallback, and is no stage (spec 5.3)."""
    fx.neither(client)
    _catalog("openrouter", [{"id": "vendor/decider", "outputs": ["decisions"]},
                            {"id": fx.NEITHER[1], "outputs": ["image"]}])
    _settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/decider"},
        "fallback": {"provider": fx.NEITHER[0], "model": fx.NEITHER[1]}}},
        "routes": {"scene_break": {"use": "decision"}}})
    resolved = _resolved()
    assert len(resolved.attempts) == 2 and resolved.fallback_problem is None
    assert resolved.fallback_missing == ("generate", "decide_native")
    assert resolved.attempts[1].decision_mode == ""
    assert inf.refusal(resolved) is None
    assert inference.stages(resolved) == (inference.Stage("native", resolved.chain, None),)


def test_a_fallback_that_cannot_generate_either_is_a_native_stage(client):
    """F refused this store (nothing could answer either model). Under H a
    fallback that cannot generate is served natively like the primary: its
    own stage, one attempt, attached to nothing."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/active", "outputs": ["decisions"]}])
    _catalog("spare", [{"id": "vendor/spare", "outputs": ["embeddings"]}])
    resolved = _resolved()
    assert [a.decision_mode for a in resolved.attempts] == ["native", "native"]
    assert resolved.missing == () and resolved.fallback_missing == ()
    assert inf.refusal(resolved) is None and resolved.chain.fallback is None
    assert inference.stages(resolved) == (
        inference.Stage("native", resolved.chain, None),
        inference.Stage("native", wire.Chain(resolved.attempts[1].target), 0))


def test_a_generating_primary_with_a_decide_only_fallback_falls_to_a_native_stage(client):
    """Rule 3's one permitted change to a structured primary's resolution: a
    fallback that cannot generate but may decide natively was F's
    `fallback_missing == ("generate",)`, never sent. Now it lacks nothing: it
    rides nothing (the primary's chain is F's, with no fallback) and is
    a native stage of its own, one attempt, which a failed primary reaches."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/active", "outputs": ["text"]}])
    _catalog("spare", [{"id": "vendor/spare", "outputs": ["decisions"]}])
    resolved = _resolved()
    primary, fallback = resolved.attempts
    assert [a.decision_mode for a in resolved.attempts] == ["structured", "native"]
    assert resolved.missing == () and resolved.fallback_missing == ()
    assert resolved.chain.fallback is None
    assert inference.stages(resolved) == (inference.Stage("structured", wire.Chain(primary.target), None),
                                          inference.Stage("native", wire.Chain(fallback.target), 0))
    fake = FakeLLM([[""]], error=LLMError("network", "connection reset"), decisions=[_yes()])
    got = _decide(fake, [_item()], resolved=resolved)
    assert got.items[0].answers["over"] == decisions.Answer(True)
    assert got.items[0].backend == "native"
    [(_item_sent, conn, retries)] = fake.native_requests
    assert (conn.provider_id, conn.model, retries) == ("spare", "vendor/spare", 0)
    assert [(r["status"], r["decision_mode"], r["model"]) for r in _rows()] == [
        ("error", "structured", "vendor/active"), ("ok", "native", "vendor/spare")]


def test_operation_capability_is_the_pickers_needs():
    """The seam's `OPERATION_CAPABILITY` and the picker's `capabilities.NEEDS`
    are one table: a decision needs `decide_native` or `generate` in both."""
    assert inf.OPERATION_CAPABILITY == {"generate": ("generate",), "embed": ("embed",),
                                        "decide": ("decide_native", "generate")}
    assert all(inf.OPERATION_CAPABILITY[op] == capabilities.NEEDS[op]
               for op in inf.OPERATION_CAPABILITY)


def test_a_native_failure_falls_to_a_same_provider_generating_fallback(client):
    """Review Focus 1, end to end: the decide-only model's native endpoint
    answers 404, and the generating model on the same provider answers in its
    place -- one attempt, structured, alone."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/decider", "outputs": ["decisions"]},
                            {"id": "vendor/active", "outputs": ["text"]}])
    _settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/decider"},
        "fallback": {"provider": "openrouter", "model": "vendor/active"}}}})
    fake = FakeLLM([[decision_reply({"over": True})]], decisions=[
        LLMError("bad_response", "no decisions endpoint", status=404)])
    got = _decide(fake, [_item()], resolved=_resolved())
    assert got.items[0].answers["over"] == decisions.Answer(True)
    assert got.items[0].backend == "structured"
    assert [t.model for _i, t, _r in fake.native_requests] == ["vendor/decider"]
    sent = fake.requests[-1]["chain"]
    assert (sent.primary.provider_id, sent.primary.model) == ("openrouter", "vendor/active")
    assert sent.fallback is None and fake.retries == [0]
    assert [(r["status"], r["decision_mode"], r["model"]) for r in _rows()] == [
        ("error", "native", "vendor/decider"), ("ok", "structured", "vendor/active")]


def _dual_capable(how: str) -> None:
    """`vendor/active` made `decide_native: yes` (spec 16's §16 guard): from
    the OpenRouter catalog (`outputs: ["text", "decisions"]`), or from a
    passed probe."""
    if how == "catalog":
        _catalog("openrouter", [{"id": "vendor/active", "outputs": ["text", "decisions"],
                                 "params": ["temperature", "structured_outputs"]}])
        return
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    assert facts.record_verified("openrouter", "vendor/active", rev,
                                                 {"decide_native": {"ok": True}})


@pytest.mark.parametrize("how", ["catalog", "probe"])
def test_a_dual_capable_primary_stays_structured(client, how):
    """C1: a model that can generate stays on structured generation whatever
    its `decide_native` says, until native wins on evals (spec 16). Its
    resolution is F's, field for field -- the chain, its riding fallback and
    the structured flag -- and deciding on it sends no native request."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/active", "outputs": ["text"],
                             "params": ["temperature", "structured_outputs"]}])
    before = _resolved().chain
    assert before.primary.structured is True and before.fallback is not None
    _dual_capable(how)
    resolved = _resolved()
    caps = resolved.attempts[0].capabilities
    assert (caps["decide_native"].value, caps["decide_native"].source) == (
        "yes", "catalog" if how == "catalog" else "test")
    assert resolved.decision_mode == "structured"
    assert resolved.chain == before
    assert resolved.chain.fallback is resolved.attempts[1].target
    assert resolved.chain.primary.structured is True
    fake = FakeLLM([[decision_reply({"over": True})]], decisions=[_yes()])
    _decide(fake, [_item()], resolved=resolved)
    assert fake.native_requests == [] and fake.calls == 1


def test_a_structured_primary_is_unchanged(client):
    """A generating primary's decide resolution is F's, field for field: the
    generate resolution's targets with the operation stamped `decide` and the
    structured flag where the model takes it -- on the primary and on the
    fallback it carries -- and one structured stage that sends it whole."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/active",
                             "params": ["temperature", "structured_outputs"]}])
    decide, generate = _resolved(), _resolved("generate")

    def as_decided(target: wire.Target, flagged: bool) -> wire.Target:
        return dataclasses.replace(target.with_account(operation="decide"),
                                   structured=flagged)

    assert decide.chain == wire.Chain(as_decided(generate.chain.primary, True),
                                      as_decided(generate.chain.fallback, False))
    assert [a.decision_mode for a in decide.attempts] == ["structured", "structured"]
    assert inference.stages(decide) == (inference.Stage("structured", decide.chain, None),)


def test_a_model_that_neither_generates_nor_decides_is_refused(client):
    """I8: both capabilities known `no` -- the catalog's `generate` and the
    user's `decide_native` -- is the one decide resolution refused, 409
    `incapable` with both phrases composed, on the Decision card and on the
    route row alike."""
    fx.neither(client)
    _settings(client, {"routes": {"scene_break": {"use": "decision"}}})
    resolved = _resolved()
    assert resolved.missing == ("generate", "decide_native")
    assert resolved.decision_mode is None
    status, body = inf.refusal(resolved)
    assert (status, body["kind"]) == (409, "incapable")
    assert body["detail"] == (
        "The Scene-break checks route runs on the Decision role (vendor/neither on "
        "OpenRouter), which cannot generate text or make native decisions — choose "
        "another Decision model or pin this route.")
    view = client.get("/api/inference/settings").json()
    assert _row_of(view, "scene_break")["problem"] == body["detail"]
    assert view["roles"]["decision"]["problem"] == (
        "This decision runs on the Decision role (vendor/neither on OpenRouter), which "
        "cannot generate text or make native decisions — choose another Decision model.")
    with pytest.raises(HTTPException) as exc:
        common.require_inference("scene-break", operation="decide")
    assert (exc.value.status_code, exc.value.detail) == (409, body)


# ---- call records (01a-S1) ----
def test_each_structured_chunk_is_one_call_record(client):
    _store(client)
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    fake = FakeLLM([[decision_reply(*[{"over": True}] * 8)],
                    [decision_reply({"over": False})]])
    got = _decide(fake, items)
    assert [(c.stage, c.mode, c.items, c.hop, c.error_kind) for c in got.calls] == [
        (0, "structured", tuple(range(8)), "", ""), (0, "structured", (8,), "", "")]
    assert [c.row for c in got.calls] == list(got.usage) == _rows()


def test_a_schema_refusal_re_send_is_its_own_call_record(client):
    _store(client, fallback=False)
    _catalog("openrouter", [{"id": "vendor/active",
                             "params": ["temperature", "structured_outputs"]}])
    provider = SequencedProvider([_refused_schema(), [decision_reply({"over": True})]])
    got = _decide(LLMClient(openrouter=provider, timeout=0, retries=0), [_item()])
    refused, resent = got.calls
    assert (refused.items, refused.error_kind, refused.error_status) == (
        (0,), "bad_response", 400)
    assert refused.row is not None and refused.row["status"] == "error"
    assert (resent.items, resent.error_kind, resent.row["status"]) == ((0,), "", "ok")


def test_a_failed_call_record_carries_kind_and_status_but_no_detail(client):
    _store(client)
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    fake = FakeLLM([[decision_reply(*[{"over": True}] * 8)]],
                   error=LLMError("network", "connection reset"), fail_after=1)
    got = _decide(fake, items)
    ok, failed = got.calls
    assert ok.error_kind == "" and failed.items == (8,)
    assert (failed.error_kind, failed.error_status) == ("network", None)
    assert "connection reset" not in repr(dataclasses.asdict(failed))


# --- 01e: the new kinds' prompts -------------------------------------------------

SCENES = (Option("scene:ledger", "Mara loses the ledger at the pier"),
          Option("scene:market", "Winifred counts the stalls"),
          Option("scene:storm", "Seraphine waits out the storm"))
RANK = decisions.Rank("relevant", "Which scenes matter most to this turn?", SCENES,
                      top=2, allow_none=True, pointwise="Does this scene bear on the ledger?")
#: Each 01e kind's system-prompt bullet, as it opens.
NEW_BULLETS = {"rank": "- a ranking is answered with a list of candidate ids",
               "select": "- a selection is answered with a list of option ids"}
WITNESSES = (Option("characters:mara", "Mara"), Option("characters:winifred", "Winifred"))


def test_a_rank_renders_its_line_candidates_and_bullet():
    system, user = (m["content"] for m in inference.structured_messages(
        [Item("Mara asks after the ledger.", (RANK,))]))
    assert ("- relevant (ranking, best first, at least the top 2, or null if they cannot "
            "be ordered): Which scenes matter most to this turn?") in user
    for opt in SCENES:
        assert f"\n  - {opt.id}: {opt.description}" in user
    # The pointwise question is a native stage's alone.
    assert RANK.pointwise not in user and RANK.pointwise not in system
    assert NEW_BULLETS["rank"] in system
    plain = decisions.Rank("relevant", "Order them.", SCENES)
    (_, user) = (m["content"] for m in inference.structured_messages(
        [Item("ctx", (plain,))]))
    assert "- relevant (ranking, best first): Order them." in user


def test_a_select_renders_its_line_bounds_options_and_bullet():
    select = decisions.MultiSelect("saw", "Who saw Seraphine take the key?", WITNESSES,
                                   min=1, max=2, allow_none=True)
    zero = decisions.MultiSelect("helped", "Who helped her?", WITNESSES, max=0)
    plain = decisions.MultiSelect("heard", "Who heard it?", WITNESSES)
    system, user = (m["content"] for m in inference.structured_messages(
        [Item("Seraphine palms the harbour key.", (select, zero, plain))]))
    assert ("- saw (selection, at least 1, at most 2, or null if it cannot be said): "
            "Who saw Seraphine take the key?") in user
    # A max of 0 is a bound, and is said; an unset min and max say nothing.
    assert "- helped (selection, at most 0): Who helped her?" in user
    assert "- heard (selection): Who heard it?" in user
    assert "\n  - characters:winifred: Winifred" in user
    assert NEW_BULLETS["select"] in system and "an empty list means none of them apply" in system
    assert NEW_BULLETS["rank"] not in system


def test_an_old_kind_batch_carries_no_new_bullet():
    """Today's call sites ask predicates, choices and scores: the bullets 01e
    adds render only beside their own kind, so those prompts do not move."""
    old = [Item("Mara closes the door.", (
        Predicate("over", "Is the scene over?"),
        Choice("next", "Who speaks next?", (Option("mara", "Mara"), Option("winifred", "W")),
               allow_none=True),
        Score("tone", "How tense?", ("calm", "tense"))))]
    system = inference.structured_messages(old, explain="Why?")[0]["content"]
    for bullet in NEW_BULLETS.values():
        assert bullet not in system
    assert ("- a choice is answered with the id of one of its options, exactly as listed, "
            "or null where the schema allows it;\n- a scale is answered") in system


def test_decide_answers_a_rank_on_a_structured_stage(client):
    _store(client, fallback=False)
    fake = FakeLLM([[decision_reply({"relevant": ["scene:storm", "scene:ledger"]})]])
    got = _decide(fake, [Item("Mara asks after the ledger.", (RANK,))])
    answer = got.items[0].answers["relevant"]
    assert answer == decisions.Answer(decisions.Ranking(
        (("scene:storm",), ("scene:ledger",)), rest=("scene:market",)))
    assert got.items[0].backend == "structured"
    assert fake.schemas[-1]["properties"]["0"]["properties"]["answers"]["properties"][
        "relevant"]["anyOf"][0]["type"] == "array"


def test_decide_answers_a_select_on_a_structured_stage(client):
    _store(client, fallback=False)
    select = decisions.MultiSelect("saw", "Who saw it?", WITNESSES)
    fake = FakeLLM([[decision_reply({"saw": ["characters:winifred", "characters:mara"]},
                                    {"saw": []})]])
    got = _decide(fake, [Item("Seraphine palms the key.", (select,)),
                         Item("Nobody is on the pier.", (select,))])
    assert got.items[0].answers["saw"] == decisions.Answer(
        ("characters:mara", "characters:winifred"))
    assert got.items[1].answers["saw"] == decisions.Answer(())
