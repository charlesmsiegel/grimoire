"""The escalation seam in `routes/` (roadmap 01d-S4; spec 01d §5.2, §9).

`routes.common.escalation_inference(task, cid)` is the one door a decide
call site's escalator stands on: the task's policy ROLE resolved with its
own preset, held to the base route's `requires`, and soft -- a refusal is
`(None, sentence)`. Each test resolves for real on an isolated format-2
store and answers through `llm_fakes.FakeLLM`; `TASK_POLICY` is empty in
the product, so a synthetic policy for `scene-break` is patched in.

Invented provider names and the codebase's placeholder names only.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import decisions, inference, llm
from grimoire.decisions import Answer, Item, ItemResult, Predicate
from grimoire.main import create_app
from grimoire.routes import common, decision_capture
from grimoire.store import routing
from grimoire.store.inference import resolve as inf
from grimoire.store.inference.resolved import ResolvedInference
from grimoire.store.routing import CALLER, TaskPolicy
from tests.llm_fakes import FakeLLM, decision_reply

from . import inference_fixtures as fx

TASK = "scene-break"
ROUTE = "scene_break"


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


def _policy(monkeypatch, **fields) -> TaskPolicy:
    fields.setdefault("escalate_to", "primary")
    fields.setdefault("question", "over")
    fields.setdefault("escalate_on", ("low_margin",))
    fields.setdefault("margins", (("openrouter", 0.2),))
    policy = TaskPolicy(**fields)
    monkeypatch.setitem(store.routing.TASK_POLICY, TASK, policy)
    return policy


def _base(client):
    """The Decision role natively on `vendor/decider` (no fallback), the
    Primary structured on `vendor/active`: the base resolution."""
    fx.decide_only(client, fallback=False)
    return inf.resolve(TASK, operation="decide")


def _item(context: str = "Mara closes the door behind her.") -> Item:
    return Item(context, (Predicate("over", "Is the scene over?"),))


def _p(prob: float) -> ItemResult:
    return ItemResult({"over": Answer(True, probability=prob)})


async def _seam():
    """The escalator a call site builds over the seam (minus the threadpool,
    which the TestClient's own store reads do not need here)."""
    return common.escalation_inference(TASK)


def _decide(fake, items, *, resolved, **kwargs) -> decisions.Decision:
    return asyncio.run(inference.decide(TASK, items, client=fake, resolved=resolved, **kwargs))


def _requires(monkeypatch, *caps: str) -> None:
    """The base route, patched to require `caps` (no decide route requires
    anything today)."""
    route = routing.route_by_key(ROUTE)
    monkeypatch.setitem(routing._BY_KEY, ROUTE, route._replace(requires=caps))


def _facts(client, model: str, overrides: dict) -> None:
    got = client.put("/api/llm-connections/openrouter/facts",
                     json={"model": model, "overrides": overrides})
    assert got.status_code == 200, got.text


# ---- a usable resolution ----
def test_the_seam_resolves_the_policy_role_and_a_hop_runs_on_it(client, monkeypatch):
    resolved = _base(client)
    _policy(monkeypatch)
    esc, why = common.escalation_inference(TASK)
    assert why == "" and esc is not None
    assert (esc.task, esc.operation, esc.role, esc.route) == (TASK, "decide", "primary", "")
    assert (esc.chain.primary.provider_id, esc.chain.primary.model) == (
        "openrouter", "vendor/active")
    fake = FakeLLM([[decision_reply({"over": False})]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved, escalation=_seam)
    (one,) = got.escalations
    assert (one.outcome, one.detail) == ("answered", "")
    assert got.items[0].answers["over"] == Answer(False)
    assert fake.requests[-1]["target"].account.hop == "escalation"


def test_the_hop_sends_the_role_s_own_preset_not_the_route_s(client, monkeypatch):
    resolved = _base(client)
    own = store.sampler_presets.create_preset("Warm", {"temperature": 0.8})
    routed = store.sampler_presets.create_preset("Cold", {"temperature": 0.2})
    fx.put_settings(client, {"roles": {"primary": {"selection": {
        "provider": "openrouter", "model": "vendor/active", "preset": own}}},
        "routes": {ROUTE: {"preset": routed}}})
    # The route's preset is real: the route's own resolution wears it.
    assert inf.resolve(TASK, operation="decide").attempts[0].preset_id == routed
    _policy(monkeypatch)
    esc, _why = common.escalation_inference(TASK)
    assert esc is not None and esc.attempts[0].preset_id == own
    fake = FakeLLM([[decision_reply({"over": False})]], decisions=[_p(0.55)])
    _decide(fake, [_item()], resolved=resolved, escalation=_seam)
    sampling = fake.requests[-1]["target"].sampling
    assert (sampling.preset_id, sampling.scope) == (own, "connection")


# ---- soft refusals ----
def test_a_missing_key_skips_every_candidate_with_the_sentence(client, monkeypatch):
    resolved = _base(client)
    got = client.post("/api/llm-connections", json={"kind": "openrouter", "name": "keyless"})
    assert got.status_code == 200, got.text
    fx.put_settings(client, {"roles": {"primary": {"selection": {
        "provider": got.json()["id"], "model": "vendor/keyless"}}}})
    _policy(monkeypatch)
    esc, why = common.escalation_inference(TASK)
    assert esc is None and why and "Primary role" in why
    fake = FakeLLM([["unused"]], decisions=[_p(0.55), _p(0.52)])
    got = _decide(fake, [_item(), _item("Winifred waits.")], resolved=resolved,
                  escalation=_seam)
    assert [(e.outcome, e.detail) for e in got.escalations] == [("skipped", why)] * 2
    assert fake.calls == 0 and len(fake.native_requests) == 2


def test_a_hop_lacking_the_route_s_requires_is_refused_naming_the_route(client, monkeypatch):
    resolved = _base(client)
    _facts(client, "vendor/active", {"vision": "no"})
    _requires(monkeypatch, "vision")
    _policy(monkeypatch)
    esc, why = common.escalation_inference(TASK)
    label = routing.label_for(ROUTE)
    assert esc is None
    assert why.startswith(f"The {label} route escalates to the Primary role (vendor/active on ")
    assert why.endswith("— choose another Primary model.")
    fake = FakeLLM([["unused"]], decisions=[_p(0.55)])
    got = _decide(fake, [_item()], resolved=resolved, escalation=_seam)
    assert [(e.outcome, e.detail) for e in got.escalations] == [("skipped", why)]
    assert fake.calls == 0


def test_a_route_requirement_the_hop_meets_is_no_refusal(client, monkeypatch):
    _base(client)
    _facts(client, "vendor/active", {"vision": "yes"})
    _requires(monkeypatch, "vision")
    _policy(monkeypatch)
    esc, why = common.escalation_inference(TASK)
    assert esc is not None and why == ""


def test_a_hop_that_can_do_neither_is_refused_naming_the_route(client, monkeypatch):
    _base(client)
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": "vendor/decider", "outputs": ["decisions"]},
                       {"id": fx.NEITHER[1], "outputs": ["image"]}],
        store.llm_connections.read_connection_raw("openrouter")["rev"])
    _facts(client, fx.NEITHER[1], {"decide_native": "no"})
    fx.put_settings(client, {"roles": {"primary": {"selection": {
        "provider": "openrouter", "model": fx.NEITHER[1]}}}})
    _policy(monkeypatch)
    esc, why = common.escalation_inference(TASK)
    assert esc is None
    assert why.startswith(f"The {routing.label_for(ROUTE)} route escalates to the Primary role")
    assert "generate text or make native decisions" in why


def test_the_refusal_is_pure_and_names_the_route():
    """`escalation_refusal` over a resolution with nothing selected says what
    `unusable` says; the route only enters a known capability gap."""
    empty = ResolvedInference(task=TASK, operation="decide", route="", role="primary",
                                  via="", scope="none", attempts=())
    assert inf.escalation_refusal(empty, TASK) == inf.unusable(empty)


# ---- the seam's own contract ----
@pytest.mark.parametrize("escalate_to", ["", CALLER])
def test_a_task_that_escalates_to_no_role_is_a_value_error(client, monkeypatch, escalate_to):
    _base(client)
    if escalate_to:
        _policy(monkeypatch, escalate_to=escalate_to)
    with pytest.raises(ValueError, match="no role"):
        common.escalation_inference(TASK)


# ---- skipped escalations reach the capture scope ----
def _escalation(outcome: str, detail: str, served=()) -> decisions.Escalation:
    return decisions.Escalation(0, "low_margin", 0.1, _p(0.55), outcome, detail, served)


def test_note_escalations_keeps_only_the_kind_of_a_failed_detail():
    scope = decision_capture.Scope(TASK, True)
    decision = decisions.Decision(items=(_p(0.55),), backend="native", escalations=(
        _escalation("failed", "rate_limit: Saltmarch provider says slow down",
                    ("openrouter", "openrouter", "vendor/active")),
        _escalation("skipped", "same_model"),
        _escalation("failed", "unreadable: NO_ITEM"),
        _escalation("answered", "")))
    scope.note_escalations("", decision)
    noted = scope.notes[""]["escalations"]
    assert [n["detail"] for n in noted] == ["rate_limit", "same_model", "unreadable", ""]
    assert noted[0] == {"index": 0, "trigger": "low_margin", "margin": 0.1,
                        "outcome": "failed", "detail": "rate_limit",
                        "served": ["openrouter", "openrouter", "vendor/active"]}
    assert "Saltmarch" not in repr(scope.notes)


def test_note_escalations_notes_nothing_without_escalations_or_capture():
    on = decision_capture.Scope(TASK, True)
    on.note_escalations("", decisions.Decision(items=(_p(0.9),), backend="native"))
    assert on.notes == {}
    off = decision_capture.Scope(TASK, False)
    off.note_escalations("", decisions.Decision(items=(_p(0.55),), backend="native",
                                                escalations=(_escalation("skipped", "cap"),)))
    assert off.notes == {}
