"""Threshold tooling for decision escalation (roadmap 01d-S5; spec 01d §6.3, §9).

The offline margin sweep is golden over a fixed recording
(`decide-continuity-identity.native-margins`); `--escalation POLICY_JSON` is
refused for a task off a decide route and held to the policy rules a JSON
value can break; `runner.escalator` hands its resolution over inside a
throwaway home and is refused by the tripwire against the real one; and a
live decide case under a policy runs the hop through `inference.decide`.

Invented provider names and the codebase's placeholder names only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import cases as case_mod  # noqa: E402
from evals import escalation, runner  # noqa: E402
from evals import run as run_mod  # noqa: E402
from grimoire import adapters, decisions  # noqa: E402
from grimoire.store import routing  # noqa: E402
from tests.llm_fakes import FakeLLM, decision_reply  # noqa: E402

IDENTITY = "decide-continuity-identity"
SCENE_BREAK = "decide-scene-break"
DECIDER, ACTIVE = "vendor/decider", "vendor/active"

#: The sweep over the identity case's native recordings, under the default
#: policy (the case's first question, every trigger). The margins recording
#: is the one that moves: item 1 is a partial report of exactly 0.6, whose
#: margin computes a hair under 0.2 and is NOT escalated at 0.20 -- the
#: sweep counts through `decisions.triggers`, MASS_TIE included.
GOLDEN = """\
escalation sweep: decide-continuity-identity (task continuity-identity, question 'decision', triggers low_margin, abstained, refused)
  native (openai_compatible): 3 item(s)
    item  answer                          margin
    0     existing:find-the-ledger        -
    1     new                             -
    2     new                             -
    threshold  escalates
    0.05       0/3: -
    0.10       0/3: -
    0.15       0/3: -
    0.20       0/3: -
    0.25       0/3: -
    0.30       0/3: -
    0.35       0/3: -
    0.40       0/3: -
    0.45       0/3: -
    0.50       0/3: -
  native-unknown-id (openai_compatible): 3 item(s)
    item  answer                          margin
    0     unreadable                      -
    1     new                             -
    2     new                             -
    threshold  escalates
    0.05       0/3: -
    0.10       0/3: -
    0.15       0/3: -
    0.20       0/3: -
    0.25       0/3: -
    0.30       0/3: -
    0.35       0/3: -
    0.40       0/3: -
    0.45       0/3: -
    0.50       0/3: -
  native-refused (openai_compatible): 3 item(s)
    item  answer                          margin
    0     refused                         -
    1     new                             -
    2     new                             -
    threshold  escalates
    0.05       1/3: 0 (refused)
    0.10       1/3: 0 (refused)
    0.15       1/3: 0 (refused)
    0.20       1/3: 0 (refused)
    0.25       1/3: 0 (refused)
    0.30       1/3: 0 (refused)
    0.35       1/3: 0 (refused)
    0.40       1/3: 0 (refused)
    0.45       1/3: 0 (refused)
    0.50       1/3: 0 (refused)
  native-margins (openai_compatible): 3 item(s)
    item  answer                          margin
    0     existing:find-the-ledger        +0.180
    1     new                             +0.200
    2     new                             +0.060
    threshold  escalates
    0.05       0/3: -
    0.10       1/3: 2 (low_margin)
    0.15       1/3: 2 (low_margin)
    0.20       2/3: 2 (low_margin), 0 (low_margin)
    0.25       3/3: 2 (low_margin), 0 (low_margin), 1 (low_margin)
    0.30       3/3: 2 (low_margin), 0 (low_margin), 1 (low_margin)
    0.35       3/3: 2 (low_margin), 0 (low_margin), 1 (low_margin)
    0.40       3/3: 2 (low_margin), 0 (low_margin), 1 (low_margin)
    0.45       3/3: 2 (low_margin), 0 (low_margin), 1 (low_margin)
    0.50       3/3: 2 (low_margin), 0 (low_margin), 1 (low_margin)"""


# ---- the offline sweep ----
def test_the_sweep_is_golden_over_the_margins_recording(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert escalation.sweep(case_mod.BY_ID[IDENTITY]) == GOLDEN


def test_the_sweep_reads_a_policy_s_filter(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    policy = escalation.parse(json.dumps({"escalate_on": ["low_margin"], "question": "decision",
                                          "escalate_answers": ["existing:"]}),
                              "continuity-identity", sweep=True)
    text = escalation.sweep(case_mod.BY_ID[IDENTITY], policy)
    assert "answers existing:" in text.splitlines()[0]
    tail = text[text.index("native-margins"):]
    assert "    0.15       0/3: -" in tail
    assert "    0.50       1/3: 0 (low_margin)" in tail
    # `refused` was not asked for: the refused recording escalates nothing.
    refused = text[text.index("native-refused"):text.index("native-margins")]
    assert "(refused)" not in refused


def test_the_sweep_says_a_case_has_no_native_recording(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    text = escalation.sweep(case_mod.BY_ID[SCENE_BREAK])
    assert text.splitlines()[1] == "  no native recording: a structured reply reports no margin"


def test_the_grid_and_the_native_kinds():
    assert escalation.GRID[0] == 0.05 and escalation.GRID[-1] == routing.MAX_MARGIN
    assert len(escalation.GRID) == 10
    assert set(escalation.NATIVE_KINDS) == set(runner.NATIVE_ADAPTERS)
    assert all(adapters.decides_natively(kind) for kind in escalation.NATIVE_KINDS.values())


def test_the_sweep_cli_prints_and_sends_nothing(capsys):
    assert run_mod.main(["--escalation-sweep", "--case", IDENTITY]) == 0
    out = capsys.readouterr().out
    assert out.strip() == GOLDEN


@pytest.mark.parametrize("argv", [
    ["--escalation-sweep"],                                      # no --case
    ["--escalation-sweep", "--case", "roll-fence"],              # not a decide route
    ["--escalation-sweep", "--case", IDENTITY, "--live"],        # offline only
    ["--escalation-sweep", "--case", IDENTITY, "--out", "x.json"],
])
def test_the_sweep_cli_refuses(argv):
    with pytest.raises(SystemExit) as raised:
        run_mod.main(argv)
    assert raised.value.code == 2


# ---- the policy override ----
def _policy(**over) -> str:
    body = {"escalate_to": "primary", "escalate_on": ["low_margin"], "question": "over",
            "margins": {"openrouter": 0.2}}
    body.update(over)
    return json.dumps(body)


def test_a_valid_policy_parses():
    got = escalation.parse(_policy(escalate_max=4, reads_declines=False), "scene-break")
    assert got == routing.TaskPolicy(escalate_to="primary", escalate_on=("low_margin",),
                                     question="over", margins=(("openrouter", 0.2),),
                                     escalate_max=4)


def test_escalation_is_refused_for_a_task_off_a_decide_route():
    with pytest.raises(escalation.PolicyError, match="not on a decide route"):
        escalation.parse(_policy(), "chat")
    assert escalation.decide_task(case_mod.BY_ID["roll-fence"])
    assert escalation.decide_task(case_mod.BY_ID[SCENE_BREAK]) is None
    with pytest.raises(SystemExit) as raised:
        run_mod.main(["--live", "--case", "roll-fence", "--escalation", _policy()])
    assert raised.value.code == 2


@pytest.mark.parametrize(("text", "why"), [
    ("[1]", "JSON object"),
    ("{", "not JSON"),
    (_policy(fallback="none"), "not fallback"),
    (_policy(escalate_to="caller"), "escalate_to is one of"),
    (_policy(escalate_on=[]), "escalate_on is some of"),
    (_policy(escalate_on=["shrug"]), "escalate_on is some of"),
    (_policy(question=""), "deciding question"),
    (_policy(margins={}), "needs a margin"),
    (_policy(margins={"openrouter": 0.6}), "in (0, 0.5]"),
    (_policy(margins={"openrouter": 0}), "in (0, 0.5]"),
    (_policy(margins={"anthropic": 0.2}), "no native decisions endpoint"),
    (_policy(escalate_on=["refused"], margins={}, escalate_answers=["true"]),
     "narrows low_margin only"),
    (_policy(escalate_max=9), "escalate_max is 1 to 8"),
    (_policy(escalate_max=True), "whole number"),
    (_policy(reads_declines="yes"), "true or false"),
])
def test_a_policy_breaking_a_rule_is_refused(text, why):
    with pytest.raises(escalation.PolicyError, match=re.escape(why)):
        escalation.parse(text, "scene-break")


def test_escalating_to_the_route_s_own_role_is_refused(monkeypatch):
    """Every decide route runs on the Decision role today, which no policy
    escalates to; a route on Primary would make a Primary hop the same model."""
    route = routing.route_by_key("scene_break")
    monkeypatch.setitem(routing._BY_KEY, "scene_break", route._replace(default_role="primary"))
    with pytest.raises(escalation.PolicyError, match="same model"):
        escalation.parse(_policy(), "scene-break")


def test_a_sweep_policy_needs_no_role_or_margins():
    got = escalation.parse(json.dumps({"escalate_on": ["low_margin"], "question": "over"}),
                           "scene-break", sweep=True)
    assert got.escalate_to == "" and got.margins == ()


@pytest.mark.parametrize("argv", [
    ["--case", SCENE_BREAK, "--escalation", _policy()],                      # no --live
    ["--live", "--escalation", _policy()],                                    # no --case
    ["--live", "--case", SCENE_BREAK, "--escalation", _policy(),
     "--decide-backend", "native"],                                           # forced
    ["--live", "--case", SCENE_BREAK, "--escalation", _policy(), "--record"],
    ["--gate", "--escalation", _policy()],
    ["--compare", "a.json", "--escalation", _policy()],
])
def test_the_live_flag_is_refused_where_it_means_nothing(argv):
    with pytest.raises(SystemExit) as raised:
        run_mod.main(argv)
    assert raised.value.code == 2


def test_overriding_puts_the_table_back():
    policy = routing.TaskPolicy(escalate_to="primary", escalate_on=("refused",),
                                question="over")
    with runner.overriding("scene-break", policy):
        assert routing.policy("scene-break") is policy
    assert "scene-break" not in routing.TASK_POLICY


# ---- the escalator and a live run ----
def _store(monkeypatch, home: Path) -> None:
    """A format-2 store: the Decision role natively on `vendor/decider`, the
    Primary structured on `vendor/active`, one keyed OpenRouter provider."""
    from grimoire.store import config, llm_connections

    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    config.read_config()
    llm_connections.update_connection("openrouter", api_key="sk-test")
    rev = llm_connections.read_connection_raw("openrouter")["rev"]
    llm_connections.set_cached_models(
        "openrouter", [{"id": DECIDER, "outputs": ["decisions"]},
                       {"id": ACTIVE, "outputs": ["text"]}], rev)
    config.write_config(role_primary_provider="openrouter", role_primary_model=ACTIVE,
                        role_decision_provider="openrouter", role_decision_model=DECIDER)


def _live_policy() -> routing.TaskPolicy:
    return escalation.parse(_policy(), "scene-break")


def test_the_escalator_hands_over_inside_an_isolate_and_trips_in_the_real_home(
        monkeypatch, tmp_path):
    real = tmp_path / "real"
    _store(monkeypatch, real)
    policy = _live_policy()
    case = case_mod.BY_ID[SCENE_BREAK]
    roles = runner.escalation_roles((case,), policy)
    resolved = roles["scene-break"]
    assert (resolved.role, resolved.chain.primary.model) == ("primary", ACTIVE)
    thunk = runner.escalator("scene-break", policy, resolved, real_home=real)
    # In the real home: refused, nothing handed over.
    assert asyncio.run(thunk()) == (None, runner.ISOLATE_ERROR)
    # In a throwaway one: the resolution, for the hop.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "iso"))
    assert asyncio.run(thunk()) == (resolved, "")


def test_the_escalator_refuses_a_mismatched_resolution(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path / "real")
    policy = _live_policy()
    resolved = runner.escalation_roles((case_mod.BY_ID[SCENE_BREAK],), policy)["scene-break"]
    with pytest.raises(ValueError, match="cannot escalate"):
        runner.escalator("voice-drift", policy, resolved)
    with pytest.raises(ValueError, match="escalates to a role"):
        runner.escalator("scene-break", policy._replace(escalate_to=routing.CALLER), resolved)


def test_escalation_roles_refuses_with_the_seam_s_sentence(monkeypatch, tmp_path):
    from grimoire.store import config, llm_connections

    _store(monkeypatch, tmp_path / "real")
    created = llm_connections.create_connection("openrouter", "keyless")
    config.write_config(role_primary_provider=created, role_primary_model="vendor/x")
    with pytest.raises(RuntimeError, match="scene-break: escalation: "):
        runner.escalation_roles((case_mod.BY_ID[SCENE_BREAK],), _live_policy())


@contextlib.contextmanager
def _isolate_at(monkeypatch, home: Path, real: Path):
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    try:
        yield home
    finally:
        monkeypatch.setenv("GRIMOIRE_HOME", str(real))


def test_a_live_decide_case_runs_the_hop_under_the_policy(monkeypatch, tmp_path):
    real = tmp_path / "real"
    _store(monkeypatch, real)
    case = case_mod.BY_ID[SCENE_BREAK]
    target = runner.resolve_connections((case,))[runner.conn_key(case)]
    policy = _live_policy()
    escalating = runner.Escalating(policy, runner.escalation_roles((case,), policy))
    unsure = decisions.ItemResult({"over": decisions.Answer(True, probability=0.55)})
    fake = FakeLLM([[decision_reply({"over": True}, rationales=["The debt is paid."])]],
                   decisions=[unsure])
    with _isolate_at(monkeypatch, tmp_path / "iso", real):
        result = runner.live(case, target, client=fake, real_home=real, run_id="r1",
                             escalating=escalating)
    assert "scene-break" not in routing.TASK_POLICY
    assert result.passed, result.error or result.failures
    assert [c.hop for c in result.calls] == ["", "escalation"]
    (record,) = result.items
    assert record["escalated"] and record["call"] == 1
    hops = [row for row in result.rows or () if row.get("hop") == "escalation"]
    assert len(hops) == 1 and hops[0]["role"] == "primary"
    assert fake.calls == 1 and len(fake.native_requests) == 1


def test_a_live_run_without_the_policy_never_escalates(monkeypatch, tmp_path):
    real = tmp_path / "real"
    _store(monkeypatch, real)
    case = case_mod.BY_ID[SCENE_BREAK]
    target = runner.resolve_connections((case,))[runner.conn_key(case)]
    unsure = decisions.ItemResult({"over": decisions.Answer(True, probability=0.55)},
                                  rationale="")
    fake = FakeLLM([["never sent"]], decisions=[unsure])
    with _isolate_at(monkeypatch, tmp_path / "iso", real):
        result = runner.live(case, target, client=fake, real_home=real, run_id="r1")
    assert [c.hop for c in result.calls] == [""] and fake.calls == 0
