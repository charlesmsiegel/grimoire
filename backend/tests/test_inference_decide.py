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

import grimoire.store as store
from grimoire import decisions, inference, llm, llm_usage, prompts, routes
from grimoire.decisions import Choice, Item, Option, Predicate, Score
from grimoire.llm import ATTEMPTED, FALLBACK_KEY, LLMClient
from grimoire.llm_errors import LLMError
from grimoire.routes import common
from grimoire.store.inference import migrate, settings
from grimoire.store.inference import resolve as inf
from tests.llm_fakes import (
    FailingOpenRouter,
    FakeLLM,
    SequencedProvider,
    decision_reply,
    from_entries,
)

from . import inference_baseline as base

ACCOUNT = llm_usage.ACCOUNT_KEY


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

    def blind(usage, conn):
        real_account(usage, {k: v for k, v in conn.items() if k != ACCOUNT})

    monkeypatch.setattr(llm_usage, "account", blind)
    _store(client)
    holders: list[dict] = []

    async def around(call, holder):
        holders.append(holder)        # inspected after `decide` returns
        return await call

    fake = FakeLLM([[decision_reply({"over": True})]])
    _decide(fake, [_item()], around=around)
    (holder,) = holders
    assert holder[ATTEMPTED] is fake.conn
    stamped: dict = {}
    asyncio.run(FakeLLM([["x"]]).complete([], fake.conn, stamped))
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
    """I7: the mode rides a copy of each attempt's block; the resolution's own
    blocks are left exactly as the resolver wrote them."""
    _store(client)
    resolved = _resolved()
    before = deepcopy(resolved.conn)
    primary_block = resolved.conn[ACCOUNT]
    fallback_block = resolved.attempts[1].conn[ACCOUNT]
    fake = FakeLLM([[decision_reply({"over": True})]])
    _decide(fake, [_item()], resolved=resolved)
    sent = fake.conn
    # Per key, so the block's other fields (slice E's billing and role) may
    # ride beside these.
    for block in (sent[ACCOUNT], sent[FALLBACK_KEY][ACCOUNT]):
        assert (block["operation"], block["decision_mode"]) == ("decide", "structured")
    assert sent[ACCOUNT] is not primary_block
    assert sent[FALLBACK_KEY][ACCOUNT] is not fallback_block
    # The copies change the mode and nothing else.
    for copy, own in ((sent[ACCOUNT], before[ACCOUNT]),
                      (sent[FALLBACK_KEY][ACCOUNT], before[FALLBACK_KEY][ACCOUNT])):
        assert {k: v for k, v in copy.items() if k != "decision_mode"} == own
    # Nothing of the resolution moved: same blocks, same contents, no mode.
    assert resolved.conn == before
    assert resolved.conn[ACCOUNT] is primary_block
    assert resolved.attempts[1].conn[ACCOUNT] is fallback_block
    for block in (primary_block, fallback_block):
        assert block["operation"] == "decide" and "decision_mode" not in block
    assert resolved.conn[FALLBACK_KEY] is resolved.attempts[1].conn


def test_a_generate_resolution_carries_only_its_operation(client):
    _store(client)
    resolved = _resolved("generate")
    for attempt in resolved.attempts:
        assert attempt.conn[ACCOUNT]["operation"] == "generate"
        assert "decision_mode" not in attempt.conn[ACCOUNT]
    assert resolved.conn[ACCOUNT] is not resolved.attempts[1].conn[ACCOUNT]


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


def test_capture_sees_each_chunks_messages_before_it_is_sent(client):
    _store(client)
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    fake = FakeLLM([[decision_reply(*[{"over": False}] * 8)],
                    [decision_reply({"over": True})]])
    captured: list[tuple[list[dict], int]] = []

    async def capture(messages):
        captured.append((messages, fake.calls))

    _decide(fake, items, capture=capture)
    assert [calls for _, calls in captured] == [0, 1]
    assert [m for m, _ in captured] == [r["messages"] for r in fake.requests]


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
    # The replaced attempts keep their dicts: the attach still names them.
    assert decide.conn[FALLBACK_KEY] is decide.attempts[1].conn


def test_a_fallback_without_structured_mode_still_answers(client):
    """Review Focus 2: the primary is flagged and fails; the fallback, sent no
    structured mode, answers with the JSON in prose; the answers parse."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/active",
                             "params": ["temperature", "structured_outputs"]}])
    resolved = _resolved()
    assert resolved.conn[llm.STRUCTURED_KEY] is True
    assert llm.STRUCTURED_KEY not in resolved.conn[FALLBACK_KEY]
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
    assert resolved.conn[llm.STRUCTURED_KEY] is True and FALLBACK_KEY not in resolved.conn
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
    # The resolution's own dict still says what the resolver decided.
    assert resolved.conn[llm.STRUCTURED_KEY] is True


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
                     observer=lambda conn, err: seen.append(
                         (conn["model"], err.kind if err else None)))
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
        asyncio.run(fake.complete([{"role": "user", "content": "x"}], _resolved().conn,
                                  schema=decisions.schema([_item()], explain=False)))
    assert (exc.value.kind, exc.value.retry_after) == ("rate_limit", 30.0)
    assert "json_schema strict mode" in exc.value.detail and "slow down" in exc.value.detail
    primary, fallback = exc.value.attempts
    assert primary is not None and primary["model"] == "vendor/active"
    assert primary[llm.STRUCTURED_KEY] is True and FALLBACK_KEY not in primary
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
    assert resolved.conn[FALLBACK_KEY][llm.STRUCTURED_KEY] is True
    seen: list[tuple[str, str | None]] = []
    provider = SequencedProvider([LLMError("network", "connection reset"),
                                  _refused_schema(), [decision_reply({"over": False})]])
    fake = LLMClient(openrouter=provider, timeout=0, retries=0,
                     observer=lambda conn, err: seen.append(
                         (conn["model"], err.kind if err else None)))
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


def test_resolve_skips_a_decide_only_primary_for_a_generating_fallback(client):
    """I5: a Decision model that only decides natively is skipped for a role
    fallback that generates, until slice H can answer it natively."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/active", "outputs": ["decisions"]}])
    resolved = _resolved()
    assert resolved.skipped == ("generate",) and resolved.missing == ()
    assert inf.refusal(resolved) is None
    assert resolved.conn is resolved.attempts[1].conn and resolved.conn["id"] == "spare"
    assert resolved.fallback is None and FALLBACK_KEY not in resolved.attempts[0].conn
    assert resolved.decision_mode == "structured"
    assert resolved.attempts[0].decision_mode == ""
    assert routes.common._usable(resolved).conn is resolved.attempts[1].conn
    text = inf.skip_text(resolved)
    assert text == (
        "The Scene-break checks route runs on the Decision role (vendor/active on "
        f"{resolved.attempts[0].conn['name']}), which cannot generate; until native "
        "decisions arrive it is answered by the fallback (vendor/spare on spare).")
    assert settings._problem(resolved) == text
    # What decide sends is the fallback, and nothing behind it.
    fake = FakeLLM([[decision_reply({"over": True})]])
    _decide(fake, [_item()], resolved=resolved)
    assert fake.conn["id"] == "spare" and FALLBACK_KEY not in fake.conn
    # A generate resolution of the same role is unchanged: refused, not skipped.
    generate = _resolved("generate")
    assert generate.skipped == () and generate.missing == ("generate",)
    assert generate.conn is generate.attempts[0].conn
    assert inf.refusal(generate)[1]["kind"] == "incapable"
    assert inf.skip_text(generate) is None

    # Without a fallback the 409 stands.
    _settings(client, {"roles": {"decision": {"fallback": {"provider": ""}}}})
    alone = _resolved()
    assert alone.skipped == () and alone.missing == ("generate",)
    status, body = inf.refusal(alone)
    assert status == 409 and body["kind"] == "incapable"
    assert settings._problem(alone) == body["detail"]


def test_a_same_provider_fallback_answers_for_a_decide_only_primary(client):
    """Spec I-1: the same-provider drop (#144: a second attempt on the
    primary's provider is a retry) is about not sending one connection twice
    after a failure. A decide-only primary is never sent, so a generating
    fallback on its own provider is the only call, and the skip reaches it --
    one OpenRouter account with a decide-only Decision model and one of its
    generating models behind it is the likeliest shape of this store."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/decider", "outputs": ["decisions"]},
                            {"id": "vendor/active", "outputs": ["text"]}])
    _settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/decider"},
        "fallback": {"provider": "openrouter", "model": "vendor/active"}}}})
    resolved = _resolved()
    assert resolved.skipped == ("generate",) and resolved.missing == ()
    assert inf.refusal(resolved) is None
    assert (resolved.conn["id"], resolved.conn["model"]) == ("openrouter", "vendor/active")
    assert resolved.fallback is None and FALLBACK_KEY not in resolved.conn
    assert resolved.decision_mode == "structured"
    # The same-provider reason is lifted exactly where the drop is: the row the
    # skip lands on names no problem with the fallback that answers it.
    assert resolved.fallback_problem is None
    fake = FakeLLM([[decision_reply({"over": True})]])
    _decide(fake, [_item()], resolved=resolved)
    assert (fake.conn["id"], fake.conn["model"]) == ("openrouter", "vendor/active")

    # Everywhere else a same-provider fallback is still a retry, and dropped:
    # on a generate resolution of the same role (refused, not skipped)...
    generate = _resolved("generate")
    assert len(generate.attempts) == 1 and generate.missing == ("generate",)
    assert generate.fallback_missing == ()
    assert generate.fallback_problem == inf.SAME_PROVIDER
    # ...and behind a decide primary that can generate.
    _settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/active"},
        "fallback": {"provider": "openrouter", "model": "vendor/decider"}}}})
    capable = _resolved()
    assert len(capable.attempts) == 1 and capable.skipped == ()
    assert FALLBACK_KEY not in capable.conn
    assert capable.fallback_problem == inf.SAME_PROVIDER


def test_the_decision_card_says_the_fallback_answers_its_decide_routes(client):
    """Brutal-2 #1: the card reads the role as a generation, which drops a
    same-provider fallback as a retry -- and so said it "is never tried", right
    above the decide routes it answers. Read as `decide` too, the card says
    what those routes do, the same sentence their rows show, and names no
    problem with the fallback that answers them."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/decider", "outputs": ["decisions"]},
                            {"id": "vendor/active", "outputs": ["text"]}])
    _settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/decider"},
        "fallback": {"provider": "openrouter", "model": "vendor/active"}}}})
    view = client.get("/api/inference/settings").json()
    card = view["roles"]["decision"]
    assert card["fallback_problem"] is None
    assert card["decide_skip"] == (
        "This decision runs on the Decision role (vendor/decider on OpenRouter), which "
        "cannot generate; until native decisions arrive it is answered by the fallback "
        "(vendor/active on OpenRouter).")
    # The generate reading stands: a generate route using Decision is refused.
    assert "cannot generate text" in card["problem"]
    (row,) = [r for r in view["routes"] if r["key"] == "scene_break"]
    assert row["problem"].endswith(
        "until native decisions arrive it is answered by the fallback "
        "(vendor/active on OpenRouter).")
    assert row["fallback_problem"] is None
    # No skip, no sentence, and the drop is reported as it always was.
    _settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/active"},
        "fallback": {"provider": "openrouter", "model": "vendor/decider"}}}})
    card = client.get("/api/inference/settings").json()["roles"]["decision"]
    assert card["decide_skip"] is None
    assert card["fallback_problem"] == inf.SAME_PROVIDER
    # Every other role carries the field, empty.
    roles = client.get("/api/inference/settings").json()["roles"]
    assert all(roles[r]["decide_skip"] is None for r in ("primary", "fast"))


def test_a_same_provider_fallback_that_cannot_generate_is_still_dropped(client):
    """Admitted only for the skip: one that cannot answer either is dropped as
    it always was (not reported), and the primary's 409 stands."""
    _store(client)
    _catalog("openrouter", [{"id": "vendor/decider", "outputs": ["decisions"]},
                            {"id": "vendor/embedder", "outputs": ["embeddings"]}])
    _settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/decider"},
        "fallback": {"provider": "openrouter", "model": "vendor/embedder"}}}})
    resolved = _resolved()
    assert len(resolved.attempts) == 1
    assert resolved.skipped == () and resolved.missing == ("generate",)
    assert resolved.fallback_missing == ()
    # Dropped for the reason it always was, and said so.
    assert resolved.fallback_problem == inf.SAME_PROVIDER
    assert inf.refusal(resolved)[1]["kind"] == "incapable"


def test_a_fallback_that_cannot_generate_either_skips_nothing(client):
    _store(client)
    _catalog("openrouter", [{"id": "vendor/active", "outputs": ["decisions"]}])
    _catalog("spare", [{"id": "vendor/spare", "outputs": ["embeddings"]}])
    resolved = _resolved()
    assert resolved.skipped == () and resolved.missing == ("generate",)
    assert resolved.fallback_missing == ("generate",)
    assert inf.refusal(resolved)[1]["kind"] == "incapable"
    assert resolved.decision_mode is None
