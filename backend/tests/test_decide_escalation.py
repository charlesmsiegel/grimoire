"""The escalation hop in `inference.decide` (roadmap 01d-S3; spec 01d §5.2-5.7).

Every test resolves for real on an isolated format-2 store and answers
through `llm_fakes.FakeLLM` -- `decisions=` for a native call, `turns=` with
`decision_reply` for a structured one. The base chain is the Decision role
(`inference_fixtures.decide_only`: a decisions-only `vendor/decider`, served
natively, so its answers carry the margins the triggers read), and the hop
goes to the Primary role (`vendor/active`, structured) unless a test moves
it. `TASK_POLICY` is empty in the product; each test patches a synthetic
policy for `scene-break` in.

Invented provider names and the codebase's placeholder names only.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import decisions, inference, llm
from grimoire.decisions import Answer, Choice, Item, ItemResult, Option, Predicate
from grimoire.llm_errors import LLMError
from grimoire.main import create_app
from grimoire.routes import common
from grimoire.routes.scenes import BudgetRefused
from grimoire.store.inference import resolve as inf
from grimoire.store.routing import CALLER, TaskPolicy
from tests.llm_fakes import FakeLLM, decision_reply

from . import inference_fixtures as fx

TASK = "scene-break"
ACTIVE = ("openrouter", "vendor/active")
SECOND = "vendor/second"


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


# ---- stores and resolutions ----
def _catalog(rows: list[dict]) -> None:
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models("openrouter", rows, rev)


def _native_base(client):
    """The Decision role natively on `vendor/decider`, no fallback; the
    Primary structured on `vendor/active`. The base resolution."""
    fx.decide_only(client, fallback=False)
    return inf.resolve(TASK, operation="decide")


def _native_primary(client) -> None:
    """The Primary role on a second decisions-only model, so the hop is
    native too."""
    _catalog([{"id": "vendor/decider", "outputs": ["decisions"]},
              {"id": SECOND, "outputs": ["decisions"]},
              {"id": "vendor/active", "outputs": ["text"]}])
    fx.put_settings(client, {"roles": {"primary": {
        "selection": {"provider": "openrouter", "model": SECOND}}}})


def _kind(resolved) -> str:
    return resolved.chain.primary.kind


def _policy(monkeypatch, **fields) -> TaskPolicy:
    fields.setdefault("escalate_to", "primary")
    fields.setdefault("question", "over")
    policy = TaskPolicy(**fields)
    monkeypatch.setitem(store.routing.TASK_POLICY, TASK, policy)
    return policy


def _low(monkeypatch, resolved, **fields) -> TaskPolicy:
    """A policy escalating low margins under the starting threshold."""
    fields.setdefault("escalate_on", ("low_margin",))
    return _policy(monkeypatch, margins=((_kind(resolved), 0.2),), **fields)


class _Escalator:
    """An escalator thunk over the real resolver (01d-S4's seam stands in
    front of it in production), counting how often it was awaited."""

    def __init__(self, role: str = "primary", task: str = TASK,
                 answer: Callable[[], tuple] | None = None):
        self.role, self.task, self.answer, self.calls = role, task, answer, 0

    async def __call__(self):
        self.calls += 1
        if self.answer is not None:
            return self.answer()
        return inf.resolve(self.task, operation="decide", role=self.role), ""


def _item(context: str = "Mara closes the door behind her.") -> Item:
    return Item(context, (Predicate("over", "Is the scene over?"),))


def _pick(context: str = "Seraphine looks to the door.") -> Item:
    return Item(context, (Choice("pick", "Who speaks next?",
                                 (Option("mara", "Mara"), Option("seraphine", "Seraphine")),
                                 allow_none=True),))


def _p(prob: float) -> ItemResult:
    return ItemResult({"over": Answer(True, probability=prob)})


def _decide(fake, items, *, resolved, **kwargs) -> decisions.Decision:
    return asyncio.run(inference.decide(TASK, items, client=fake, resolved=resolved, **kwargs))


def _rows() -> list[dict]:
    return list(store.usage.calls(days=1))


# ---- Task 3: the hop mark on a call ----
def test_a_hop_call_records_and_captures_its_mark(client):
    resolved = _native_base(client)
    esc = inf.resolve(TASK, operation="decide", role="primary")
    seen: list = []

    async def capture(messages, outcome, target):
        seen.append(outcome)

    fake = FakeLLM([[decision_reply({"over": True})]])
    for hop in ("escalation", ""):
        seen.clear()
        call = inference._Call(task=TASK, client=fake, explain="", campaign="", scene="",
                               post=None, round_id="", capture=capture, around=None,
                               chain=esc.chain.alone(), retries=0, stage=1, positions=(3,),
                               hop=hop)
        got = asyncio.run(inference._BACKENDS[inference.STRUCTURED]((_item(),), call))
        assert [(c.hop, c.stage, c.items) for c in got.calls] == [(hop, 1, (3,))]
        assert seen[0]["stage"] == 1 and seen[0]["at"] == [3]
        assert seen[0].get("hop") == (hop or None)
    assert resolved.chain is not None


# ---- the call site and its policy ----
def test_no_policy_returns_the_base_decision(client, monkeypatch):
    resolved = _native_base(client)
    returned: list = []
    real = inference.run_stages

    async def recording(*args, **kwargs):
        returned.append(await real(*args, **kwargs))
        return returned[-1]

    monkeypatch.setattr(inference, "run_stages", recording)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved)
    assert got is returned[0] and got.escalations == ()
    assert fake.calls == 0 and len(fake.native_requests) == 1


def test_a_policy_without_an_escalator_is_refused_before_any_meter(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    with pytest.raises(ValueError, match="escalat"):
        _decide(fake, [_item()], resolved=resolved)
    assert fake.native_requests == [] and fake.calls == 0 and _rows() == []


def test_an_escalator_without_a_policy_is_refused_before_any_meter(client):
    resolved = _native_base(client)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    with pytest.raises(ValueError, match="escalat"):
        _decide(fake, [_item()], resolved=resolved, escalation=_Escalator())
    assert fake.native_requests == [] and _rows() == []


def test_an_item_without_the_deciding_question_is_refused_first(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    tone = Item("Winifred slams the ledger.", (Predicate("tense", "Is it tense?"),))
    with pytest.raises(ValueError, match="deciding question"):
        _decide(fake, [_item(), tone], resolved=resolved, escalation=_Escalator())
    assert fake.native_requests == [] and _rows() == []


def test_a_policy_with_an_unknown_trigger_is_refused_first(client, monkeypatch):
    resolved = _native_base(client)
    _policy(monkeypatch, escalate_on=("bored",))
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    with pytest.raises(ValueError, match="unknown triggers"):
        _decide(fake, [_item()], resolved=resolved, escalation=_Escalator())
    assert fake.native_requests == [] and _rows() == []


# ---- nothing to hand over ----
def test_no_trigger_returns_the_base_and_never_resolves(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    escalator = _Escalator()
    fake = FakeLLM([["unused"]], decisions=[_p(0.95)])
    got = _decide(fake, [_item(), _item()], resolved=resolved, escalation=escalator)
    assert got.escalations == () and escalator.calls == 0 and fake.calls == 0


def test_a_failed_base_raises_and_never_resolves(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    escalator = _Escalator()
    fake = FakeLLM([["unused"]], decisions=[LLMError("bad_response", "garbled")])
    with pytest.raises(LLMError, match="garbled"):
        _decide(fake, [_item()], resolved=resolved, escalation=escalator)
    assert escalator.calls == 0


# ---- the role hop answers ----
def test_a_low_margin_item_is_handed_to_the_primary(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([[decision_reply({"over": False})]], decisions=[_p(0.55), _p(0.95)])
    got = _decide(fake, [_item(), _item("Winifred waits.")], resolved=resolved,
                  escalation=_Escalator())
    esc = inf.resolve(TASK, operation="decide", role="primary")
    hop_server = (esc.chain.primary.kind, *ACTIVE)
    assert got.items[0].answers["over"] == Answer(False)
    assert got.items[0].backend == "structured" and got.items[0].served == hop_server
    assert got.items[1].answers["over"].probability == 0.95
    (one,) = got.escalations
    assert (one.index, one.trigger, one.outcome, one.detail, one.served) == (
        0, "low_margin", "answered", "", hop_server)
    assert one.margin == pytest.approx(0.1)
    assert one.before.answers["over"].probability == 0.55
    assert got.served == (("openrouter", "vendor/decider"), ACTIVE)
    assert (got.provider, got.model, got.backend) == ("", "", "")
    assert got.errors == ()
    assert fake.retries == [0] and fake.requests[-1]["chain"].fallback is None
    assert [c.hop for c in got.calls] == ["", "", "escalation"]
    assert got.calls[-1].items == (0,) and got.calls[-1].stage == 1


def test_a_hop_sends_the_escalation_resolution_s_own_target(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([[decision_reply({"over": False})]], decisions=[_p(0.55)])
    _decide(fake, [_item()], resolved=resolved, escalation=_Escalator())
    esc = inf.resolve(TASK, operation="decide", role="primary")
    assert fake.requests[-1]["target"] == esc.chain.primary.with_account(
        hop="escalation", decision_mode="structured")
    assert fake.requests[-1]["target"].model != resolved.chain.primary.model


def test_a_native_hop_answers_on_its_own_endpoint(client, monkeypatch):
    resolved = _native_base(client)
    _native_primary(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55), _p(0.9)])
    got = _decide(fake, [_item()], resolved=resolved, escalation=_Escalator())
    (_base, (_item_sent, target, retries)) = fake.native_requests
    assert target.model == SECOND and retries == 0 and target.account.hop == "escalation"
    assert got.items[0].served[2] == SECOND and got.escalations[0].outcome == "answered"
    assert fake.calls == 0


def test_an_escalated_answer_is_never_escalated_again(client, monkeypatch):
    resolved = _native_base(client)
    _native_primary(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55), _p(0.51)])
    got = _decide(fake, [_item()], resolved=resolved, escalation=_Escalator())
    assert len(fake.native_requests) == 2
    assert [e.outcome for e in got.escalations] == ["answered"]
    assert got.items[0].answers["over"].probability == 0.51


# ---- skips ----
def test_the_same_model_is_skipped_and_costs_no_call(client, monkeypatch):
    fx.format2(client)
    fx.put_settings(client, {"roles": {"decision": {
        "selection": {"provider": ACTIVE[0], "model": ACTIVE[1]}, "fallback": {"provider": ""}}}})
    resolved = inf.resolve(TASK, operation="decide")
    _policy(monkeypatch, question="pick", escalate_on=("abstained",))
    fake = FakeLLM([[decision_reply({"pick": None})]])
    got = _decide(fake, [_pick()], resolved=resolved, escalation=_Escalator())
    assert [(e.outcome, e.detail) for e in got.escalations] == [("skipped", "same_model")]
    assert fake.calls == 1 and got.items[0].answers["pick"].reason == "abstained"


def test_an_unresolved_hop_skips_every_trigger_with_its_sentence(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55), _p(0.52)])
    got = _decide(fake, [_item(), _item()], resolved=resolved,
                  escalation=_Escalator(answer=lambda: (None, "Saltmarch has no key.")))
    assert [(e.index, e.outcome, e.detail) for e in got.escalations] == [
        (1, "skipped", "Saltmarch has no key."), (0, "skipped", "Saltmarch has no key.")]
    assert fake.calls == 0 and len(_rows()) == 2


def test_a_none_escalator_under_a_caller_policy_skips_every_trigger(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved, escalate_to=CALLER)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved,
                  escalation=_Escalator(answer=lambda: (None, "no loop today")))
    assert [(e.outcome, e.detail) for e in got.escalations] == [("skipped", "no loop today")]


def test_an_escalator_that_raises_skips_every_trigger_by_kind(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)

    def boom():
        raise RuntimeError("secret words")

    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved, escalation=_Escalator(answer=boom))
    (one,) = got.escalations
    assert (one.outcome, one.detail) == ("skipped", "unresolved: RuntimeError")
    assert got.items[0].answers["over"].probability == 0.55 and fake.calls == 0


def test_a_hop_that_can_do_neither_is_skipped_incapable(client, monkeypatch):
    resolved = _native_base(client)
    _catalog([{"id": "vendor/decider", "outputs": ["decisions"]},
              {"id": fx.NEITHER[1], "outputs": ["image"]},
              {"id": "vendor/active", "outputs": ["text"]}])
    got = client.put("/api/llm-connections/openrouter/facts",
                     json={"model": fx.NEITHER[1], "overrides": {"decide_native": "no"}})
    assert got.status_code == 200, got.text
    fx.put_settings(client, {"roles": {"primary": {
        "selection": {"provider": "openrouter", "model": fx.NEITHER[1]}}}})
    resolved = inf.resolve(TASK, operation="decide")
    _low(monkeypatch, resolved)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved, escalation=_Escalator())
    assert [(e.outcome, e.detail) for e in got.escalations] == [("skipped", "incapable")]
    assert fake.calls == 0 and len(fake.native_requests) == 1


def test_a_hop_on_a_dead_connection_is_skipped(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55), LLMError("auth", "key refused")])
    got = _decide(fake, [_item(), _item()], resolved=resolved, escalation=_Escalator())
    assert [(e.outcome, e.detail) for e in got.escalations] == [("skipped", "dead_connection")]
    assert fake.calls == 0 and [e.kind for e in got.errors] == ["auth"]


# ---- the cap ----
def test_the_cap_takes_refusals_then_abstentions_then_the_lowest_margins(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved, escalate_on=("refused", "abstained", "low_margin"))
    refused = ItemResult({"over": Answer(None, "refused")})
    abstained = ItemResult({"over": Answer(None, "abstained")})
    script = [refused, _p(0.52), abstained, _p(0.51), refused, abstained, _p(0.58),
              abstained, _p(0.55), _p(0.53)]
    fake = FakeLLM([[decision_reply(*[{"over": False}] * 8)]], decisions=script)
    got = _decide(fake, [_item(f"Winifred turns page {n}.") for n in range(10)],
                  resolved=resolved, escalation=_Escalator())
    assert fake.calls == 1
    assert got.calls[-1].hop == "escalation"
    assert got.calls[-1].items == (0, 4, 2, 5, 7, 3, 1, 9)
    assert [(e.index, e.outcome, e.detail) for e in got.escalations[-2:]] == [
        (8, "skipped", "cap"), (6, "skipped", "cap")]


def test_a_structured_hop_takes_only_its_first_chunk(client, monkeypatch):
    resolved = _native_base(client)
    _policy(monkeypatch, question="pick", escalate_on=("abstained",))
    options = tuple(Option(f"o{n}", f"Option {n}") for n in range(200))
    items = [Item(f"Saltmarch ledger {n}.", (Choice("pick", "Which entry?", options,
                                                     allow_none=True),))
             for n in range(8)]
    fits = len(decisions.chunks(items)[0][1])
    assert 1 <= fits < 8
    abstained = ItemResult({"pick": Answer(None, "abstained")})
    fake = FakeLLM([[decision_reply(*[{"pick": "o1"}] * fits)]], decisions=[abstained])
    got = _decide(fake, items, resolved=resolved, escalation=_Escalator())
    assert fake.calls == 1
    assert [e.outcome for e in got.escalations] == ["answered"] * fits + ["skipped"] * (8 - fits)
    assert all(e.detail == "cap" for e in got.escalations[fits:])


# ---- the merge ----
def _pick_low() -> ItemResult:
    return ItemResult({"pick": Answer("mara", distribution={"mara": 0.55, "seraphine": 0.45})})


def test_a_hop_decline_on_a_task_that_does_not_read_one_fails(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved, question="pick")
    fake = FakeLLM([[decision_reply({"pick": None})]], decisions=[_pick_low()])
    got = _decide(fake, [_pick()], resolved=resolved, escalation=_Escalator())
    (one,) = got.escalations
    assert (one.outcome, one.detail) == ("failed", "declined")
    assert got.items[0].answers["pick"].answer == "mara"


def test_served_names_a_hop_whose_answer_the_merge_rejected(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved, question="pick")
    fake = FakeLLM([[decision_reply({"pick": None})]], decisions=[_pick_low()])
    got = _decide(fake, [_pick()], resolved=resolved, escalation=_Escalator())
    assert got.served == (("openrouter", "vendor/decider"), ACTIVE)
    assert (got.provider, got.model) == ("", "")
    assert got.backend == ""


def test_a_hop_decline_replaces_on_a_reads_declines_task(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved, question="pick", reads_declines=True)
    fake = FakeLLM([[decision_reply({"pick": None})]], decisions=[_pick_low()])
    got = _decide(fake, [_pick()], resolved=resolved, escalation=_Escalator())
    assert got.escalations[0].outcome == "answered"
    assert got.items[0].answers["pick"].reason == "abstained"


def test_a_garbled_hop_fails_and_the_original_stands(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([["I would rather not say."]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved, escalation=_Escalator())
    (one,) = got.escalations
    assert (one.outcome, one.detail) == ("failed", "unreadable: no_object")
    assert got.items[0].answers["over"].probability == 0.55


@pytest.mark.parametrize("evidence, kept", [(None, True), ("nowhere", True), ("s2", False)])
def test_the_merge_keeps_a_question_the_hop_garbled(client, monkeypatch, evidence, kept):
    resolved = _native_base(client)
    _policy(monkeypatch, question="decision", escalate_on=("abstained",))
    item = Item("Mara and Mara Vell may be one person.", (
        Choice("decision", "Same person?", (Option("same", "Same"), Option("new", "New")),
               allow_none=True),
        Choice("evidence_scene", "Which scene shows it?",
               (Option("s1", "Scene one"), Option("s2", "Scene two")))))
    base_evidence = Answer("s1")
    base = ItemResult({"decision": Answer(None, "abstained"), "evidence_scene": base_evidence})
    reply = {"decision": "same"} if evidence is None else {"decision": "same",
                                                           "evidence_scene": evidence}
    fake = FakeLLM([[decision_reply(reply)]], decisions=[base])
    got = _decide(fake, [item], resolved=resolved, escalation=_Escalator())
    assert got.items[0].answers["decision"] == Answer("same")
    if kept:
        assert got.items[0].answers["evidence_scene"] is base_evidence
    else:
        assert got.items[0].answers["evidence_scene"] == Answer("s2")


def test_a_hop_decline_never_turns_a_read_batch_into_a_failure(client, monkeypatch):
    """The review's S1 counterexample: A read at a low margin, B failed, the
    hop abstains on A on a task that does not read declines."""
    resolved = _native_base(client)
    _low(monkeypatch, resolved, question="pick")
    fake = FakeLLM([[decision_reply({"pick": None})]],
                   decisions=[_pick_low(), LLMError("bad_response", "garbled")])
    got = _decide(fake, [_pick(), _pick("Mara turns.")], resolved=resolved,
                  escalation=_Escalator())
    assert got.escalations[0].outcome == "failed"
    assert got.items[1].answers["pick"].reason == "error"
    assert common._decide_error(got, "pick") is None


# ---- the caller resolver ----
def _caller(monkeypatch, resolved) -> None:
    _low(monkeypatch, resolved, escalate_to=CALLER)


def _resolving(reply_or_error):
    seen: list = []

    async def resolver(items, triggers):
        seen.append((items, triggers))
        if isinstance(reply_or_error, Exception):
            raise reply_or_error
        return reply_or_error

    return resolver, seen


HOP_ROW = {"task": TASK, "hop": "escalation", "decision_mode": "structured"}
TOOL = ("openrouter", "openrouter", "vendor/tool")


def test_a_caller_resolver_answers_and_its_rows_and_server_join_the_decision(client,
                                                                            monkeypatch):
    resolved = _native_base(client)
    _caller(monkeypatch, resolved)
    reply = decisions.ResolverReply(
        (ItemResult({"over": Answer(False)}, backend="structured", served=TOOL),),
        rows=(HOP_ROW,))
    resolver, seen = _resolving(reply)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved,
                  escalation=_Escalator(answer=lambda: (resolver, "")))
    assert got.items[0].answers["over"] == Answer(False)
    assert got.usage[-1] is HOP_ROW
    assert (got.calls[-1].hop, got.calls[-1].items, got.calls[-1].row) == (
        "escalation", (0,), HOP_ROW)
    assert ("openrouter", "vendor/tool") in got.served
    (items, triggers), = seen
    assert items == (_item(),) and [t.trigger for t in triggers] == ["low_margin"]


@pytest.mark.parametrize("reply", [
    decisions.ResolverReply((ItemResult({"over": Answer(False)}),)),
    decisions.ResolverReply((ItemResult({"over": Answer(False)}, backend="structured"),),
                            rows=({"task": TASK},)),
    decisions.ResolverReply(()),
    decisions.ResolverReply((ItemResult({"tense": Answer(False)}, backend="structured"),)),
], ids=["backend", "row-without-hop", "length", "missing-question"])
def test_a_malformed_caller_reply_is_refused(client, monkeypatch, reply):
    resolved = _native_base(client)
    _caller(monkeypatch, resolved)
    resolver, _seen = _resolving(reply)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    with pytest.raises(ValueError):
        _decide(fake, [_item()], resolved=resolved,
                escalation=_Escalator(answer=lambda: (resolver, "")))


def test_a_caller_resolver_that_raises_leaves_every_original(client, monkeypatch):
    resolved = _native_base(client)
    _caller(monkeypatch, resolved)
    resolver, _seen = _resolving(LLMError("timeout", "slow"))
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved,
                  escalation=_Escalator(answer=lambda: (resolver, "")))
    assert [(e.outcome, e.detail) for e in got.escalations] == [("failed", "timeout: slow")]
    assert got.items[0].answers["over"].probability == 0.55
    assert len(got.usage) == 1 and got.errors == ()


def test_a_caller_none_result_leaves_the_original(client, monkeypatch):
    resolved = _native_base(client)
    _caller(monkeypatch, resolved)
    resolver, _seen = _resolving(decisions.ResolverReply((None,)))
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved,
                  escalation=_Escalator(answer=lambda: (resolver, "")))
    assert [(e.outcome, e.detail) for e in got.escalations] == [("failed", "no_result")]


def test_a_resolver_for_a_role_policy_is_a_type_error(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    resolver, _seen = _resolving(decisions.ResolverReply((None,)))
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    with pytest.raises(TypeError):
        _decide(fake, [_item()], resolved=resolved,
                escalation=_Escalator(answer=lambda: (resolver, "")))


def test_a_resolution_for_a_caller_policy_is_a_type_error(client, monkeypatch):
    resolved = _native_base(client)
    _caller(monkeypatch, resolved)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    with pytest.raises(TypeError):
        _decide(fake, [_item()], resolved=resolved, escalation=_Escalator())


def test_a_resolution_of_another_task_is_refused(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    with pytest.raises(ValueError, match="cannot escalate"):
        _decide(fake, [_item()], resolved=resolved,
                escalation=_Escalator(task="voice-drift"))
    assert fake.calls == 0


# ---- failures, budgets, cancellation, the ledger and the capture ----
def test_a_rate_limited_hop_leaves_the_original_and_the_errors(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)],
                   error=LLMError("rate_limit", "slow down", status=429))
    got = _decide(fake, [_item()], resolved=resolved, escalation=_Escalator())
    (one,) = got.escalations
    assert one.outcome == "failed" and one.detail.startswith("rate_limit: ")
    assert got.errors == () and common._decide_error(got, "over") is None
    assert got.calls[-1].error_kind == "rate_limit" and got.calls[-1].hop == "escalation"
    assert got.usage[-1]["status"] == "error" and got.usage[-1]["hop"] == "escalation"


def test_a_hop_the_budget_refuses_is_failed_and_files_no_row(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    seen = [0]

    async def around(pending, holder):
        seen[0] += 1
        if seen[0] > 1:            # the base's one native call, then the hop
            pending.close()
            raise BudgetRefused("timeout", "budget spent")
        return await pending

    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved, escalation=_Escalator(), around=around)
    assert [(e.outcome, e.detail) for e in got.escalations] == [
        ("failed", "timeout: budget spent")]
    assert len(_rows()) == 1 and fake.calls == 0
    assert got.calls[-1].row is None and got.calls[-1].hop == "escalation"


def test_hop_rows_carry_the_hop_and_the_role(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([[decision_reply({"over": False})]], decisions=[_p(0.55)])
    _decide(fake, [_item()], resolved=resolved, escalation=_Escalator(),
            campaign="saltmarch", scene="s1", post=3, round_id="r1")
    base_row, hop_row = _rows()
    assert "hop" not in base_row
    assert (hop_row["hop"], hop_row["role"], hop_row["operation"],
            hop_row["decision_mode"], hop_row["task"]) == (
        "escalation", "primary", "decide", "structured", TASK)
    assert (hop_row["campaign"], hop_row["scene"], hop_row["post"],
            hop_row["round_id"]) == ("saltmarch", "s1", 3, "r1")


def test_hop_calls_reach_the_capture_marked(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    outcomes: list = []

    async def capture(messages, outcome, target):
        outcomes.append(outcome)

    fake = FakeLLM([[decision_reply({"over": False})]], decisions=[_p(0.95), _p(0.55)])
    _decide(fake, [_item(), _item()], resolved=resolved, escalation=_Escalator(),
            capture=capture)
    assert ["hop" in o for o in outcomes[:2]] == [False, False]
    assert (outcomes[2]["hop"], outcomes[2]["stage"], outcomes[2]["at"]) == (
        "escalation", 1, [1])


def test_a_capture_that_raises_fails_nothing(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)

    async def capture(messages, outcome, target):
        if outcome.get("hop"):
            raise RuntimeError("capture broke")

    fake = FakeLLM([[decision_reply({"over": False})]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved, escalation=_Escalator(),
                  capture=capture)
    assert got.escalations[0].outcome == "answered"


def test_a_cancel_during_the_hop_propagates_and_files_aborted(client, monkeypatch):
    resolved = _native_base(client)
    _low(monkeypatch, resolved)
    fake = FakeLLM([[decision_reply({"over": False})]], decisions=[_p(0.55)], stall=True)

    async def go() -> None:
        task = asyncio.create_task(inference.decide(TASK, [_item()], client=fake,
                                                    resolved=resolved,
                                                    escalation=_Escalator()))
        for _ in range(3000):            # until the hop has stamped its holder
            if fake.calls:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    rows = _rows()
    assert rows[-1]["hop"] == "escalation" and rows[-1]["status"] == "aborted"
