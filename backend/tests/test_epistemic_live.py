"""Spec 11 §8.2's live arm (roadmap 11-S5), offline: its graders on canned
replies, the corpus's questions, and the whole arm end to end with a fake
model -- one generation per asked NPC, each in its own throwaway home,
metered, harvested and graded, with the reply-leak line apart from recall."""

import re
import sys

import pytest

from evals import run as eval_run
from evals import runner as reports
from evals.epistemic import cases, decision, graders, live
from evals.graders import Check
from tests.llm_fakes import FakeLLM, decision_reply

RULE_WORDS = ("override", "witnessed", "unaware", "suspect", "narrator", "withheld",
              "grant", "R1", "R3", "R4", "R5")


def _store(monkeypatch, home):
    """A seeded throwaway store whose `chat` resolves to a local model."""
    from grimoire.store import config, llm_connections
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    config.read_config()
    llm_connections.create_connection("openai_compatible", "Mara Local",
                                      base_url="http://localhost:1234/v1")
    config.write_config(role_primary_provider="mara-local", role_primary_model="big")


def _target():
    return reports.resolve_connections((eval_run._probe("chat"),))["chat"]


def _judge():
    probe = eval_run._probe(decision.TASK, decide=True)
    return reports.resolve_connections((probe,))[reports.conn_key(probe)]


def _replies(reply: str, *judge_answers: bool | None) -> FakeLLM:
    """One reply per asked NPC, then the judge's answers, in call order."""
    verdict = decision_reply({f"fact{i}": a for i, a in enumerate(judge_answers)})
    return FakeLLM([[reply]] * len(live.asked()) + [[verdict]])


# ------------------------------------------------------------ the graders


def test_the_graders_read_planted_tokens_only():
    reply = "Mara mentions the AFTERNEEDLE1 tide, and the gallery."
    assert graders.tokens(reply) == {"AFTERNEEDLE1"}
    assert graders.reply_leaks(reply, ["AFTERNEEDLE1", "WHISPERNEEDLE3"]) == ["AFTERNEEDLE1"]
    assert graders.contamination("plain words", ["REVEALNEEDLE1"]) == []
    assert graders.recall(reply, ["AFTERNEEDLE1", "OPENNEEDLE3"]) == 0.5
    assert graders.recall(reply, []) is None
    # A title or heading phrase is something a reply can say honestly.
    assert graders.needles(("The Gallery", "Known to:", "SECRETNEEDLE8")) == ("SECRETNEEDLE8",)
    # Italics, code spans and a lower-cased spelling are still the token.
    assert graders.tokens("_WHISPERNEEDLE3_ and `aftERneedle1`.") == {"WHISPERNEEDLE3",
                                                                     "AFTERNEEDLE1"}


def test_case_3s_recall_never_rewards_the_whisper():
    found = live._facts(cases.Check("characters:mara", allowed=("OPENNEEDLE3", "WHISPERNEEDLE3"),
                                    live_forbidden=("WHISPERNEEDLE3",), ask="x"),
                        [{"content": "OPENNEEDLE3 WHISPERNEEDLE3"}])
    assert found["allowed"] == ["OPENNEEDLE3"] and found["exposed"] == ["WHISPERNEEDLE3"]


# ------------------------------------------------------------ the corpus


def test_every_question_is_innocent(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    items = live.asked()
    assert items and {i.case.id for i in items} >= {"1", "2", "3", "4", "8", "9"}
    for item in items:
        with live.offline._home():
            _items, checks = item.case.build(live.offline._cast())
        ask = checks[item.index].ask
        assert not graders.tokens(ask), item.id
        assert not any(re.search(rf"\b{w}", ask, re.IGNORECASE) for w in RULE_WORDS), (item.id, ask)
        assert item.case.label not in ask


def test_case_3_is_the_one_exposure(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    exposed = [i for i in live.asked()
               if any(c.live_forbidden for c in [_check(i)])]
    assert [i.case.id for i in exposed] == ["3"]


def _check(item):
    with live.offline._home():
        _items, checks = item.case.build(live.offline._cast())
    return checks[item.index]


# ------------------------------------------------------------ the arm, end to end


def test_the_arm_asks_each_npc_once_in_its_own_home_and_meters_it(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path / "real")
    target = _target()
    fake = _replies("Mara recalls the AFTERNEEDLE1 tide and the OPENNEEDLE3 vigil.", False)
    results, records = live.run(target, client=fake, real_home=tmp_path / "real",
                                run_id="r1", repeat=1, judge=_judge())
    # One reply per asked NPC, and one judge of the one exposed reply.
    assert len(records) == len(live.asked()) == fake.calls - 1
    assert len(results) == len(records) + 1
    [judged] = [r for r in records if r["exposed"]]
    assert judged["judge"] == "clean"
    # The question is the last message, as Seraphine's line in the story.
    for request in fake.requests[:-1]:
        last = request["messages"][-1]
        assert last["role"] == "user" and last["content"].startswith("Seraphine")
    # Each case's rows were harvested from its own home: one call each, the
    # judge's included, under its decide task.
    assert all(r.rows is not None and len(r.rows) == 1 for r in results)
    assert results[-1].rows[0]["task"] == decision.TASK
    # The judge was handed the reply and the secret as a sentence.
    assert cases.WHISPER_FACT in str(fake.requests[-1]["messages"])
    assert all(not r.error for r in results)
    # Every prompt was clean, every reply answered, none exposed leaked.
    assert all(r["answered"] and r["prompt_clean"] for r in records)
    assert live.gate(records, live=True, results=results)
    line = live.summary(records)
    assert "reply leaks: 0 of 1 exposed replies" in line
    assert "recall (not a leakage measure)" in line


def test_a_leaking_reply_is_on_the_leakage_line_and_fails_the_gate(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path / "real")
    fake = FakeLLM([["She heard the WHISPERNEEDLE3 and the WHISPERNEEDLE2, plainly."]])
    results, records = live.run(_target(), client=fake, real_home=tmp_path / "real",
                                repeat=2)
    leaked = [r for r in records if r["leaks"]]
    assert {r["case"] for r in leaked} == {"3"} and len(leaked) == 2
    assert not live.gate(records, live=True, results=results)
    assert "reply leaks: 2 of 2 exposed replies" in live.summary(records)
    # Case 2's prompt never held its whisper: the same token there is the
    # harness's fault, not a leak.
    assert any(r["case"] == "2" and r["contamination"] for r in records)
    failed = [r for r in results if r.case.id.startswith("epistemic-3-")]
    assert all(not r.passed for r in failed)


def test_an_empty_or_failed_reply_is_never_clean(monkeypatch, tmp_path):
    from grimoire.llm_errors import LLMError
    _store(monkeypatch, tmp_path / "real")
    _results, records = live.run(_target(), client=FakeLLM([[""]]),
                                 real_home=tmp_path / "real")
    assert not any(r["answered"] for r in records) and not live.gate(records)
    assert "answered: 0 of" in live.summary(records)
    failing = FakeLLM([["x"]], error=LLMError("rate_limit", "slow down"))
    results, records = live.run(_target(), client=failing, real_home=tmp_path / "real")
    assert all(r.error for r in results) and not live.gate(records)


def test_the_tripwire_refuses_the_real_home_before_planting(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path / "real")
    fake = FakeLLM([["x"]])
    monkeypatch.setattr(live.offline, "_home", _NotMoved)
    with pytest.raises(RuntimeError, match="throwaway"):
        live.run(_target(), client=fake, real_home=tmp_path / "real")
    assert fake.calls == 0


def test_the_tripwire_is_checked_again_before_the_harvest(monkeypatch, tmp_path):
    # Armed by default (the home active at entry), and asked twice per case:
    # before it is built, and before its rows are read.
    _store(monkeypatch, tmp_path / "real")
    target = _target()
    real = reports._is_real_home

    def trips_at_the_harvest(home):
        # The settle step is where the second check runs: trip only there.
        return sys._getframe(1).f_code.co_name == "_settle" or real(home)
    monkeypatch.setattr(reports, "_is_real_home", trips_at_the_harvest)
    results, records = live.run(target, client=FakeLLM([["x"]]))
    refused = [r for r in results if r.error == reports.ISOLATE_ERROR]
    # Every case was sent, and none was harvested: the check came after.
    assert len(refused) == len(results) and all(r.rows is None for r in refused)
    assert not live.gate(records, live=True, results=results)


def test_money_is_reported_never_zero_and_the_real_ledger_stays_empty(monkeypatch, tmp_path):
    from evals import costs
    from grimoire.store import usage
    _store(monkeypatch, tmp_path / "real")
    results, _records = live.run(_target(), client=FakeLLM([["Plain words."]]),
                                 real_home=tmp_path / "real")
    for r in results:
        assert r.bucket is not None and r.bucket.get("calls") == 1
        text = costs.cost_text(r.bucket)
        # A local model reports no price and no rate is set: not reported.
        assert "not reported" in text and "$0" not in text
    assert not [row for row in usage._read_rows(usage._today(), usage._today())
                if usage._is_call(row)]


def test_a_crash_fails_its_own_run_and_the_rest_carry_on(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path / "real")
    real = live.assemble.compose_turn
    seen = {"n": 0}

    def third_raises(*a, **k):
        seen["n"] += 1
        if seen["n"] == 3:
            raise RuntimeError("compose broke")
        return real(*a, **k)
    monkeypatch.setattr(live.assemble, "compose_turn", third_raises)
    fake = FakeLLM([["Plain words."]])
    results, records = live.run(_target(), client=fake, real_home=tmp_path / "real")
    assert len(results) == len(live.asked())
    assert sum(bool(r.error) for r in results) == 1
    assert fake.calls == len(results) - 1
    assert not live.gate(records, live=True, results=results)


def test_a_live_run_with_nothing_asked_never_passes():
    assert not live.gate([], [], live=True, results=[])
    assert live.gate([], [])


class _NotMoved:
    """A `_home` that leaves the active home where it is: the real one."""
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


# ------------------------------------------------------------ the mode


def test_offline_epistemic_never_builds_a_client(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(live, "run", lambda *a, **k: pytest.fail("offline ran the live arm"))
    assert eval_run.main(["--epistemic"]) == 0
    assert "prompt leaks (offline suite): 0 of" in capsys.readouterr().out


@pytest.mark.parametrize("flags", [["--epistemic", "--history"], ["--history", "--epistemic"],
                                   ["--epistemic", "--gate"], ["--epistemic", "--case", "x"],
                                   ["--epistemic", "--record", "--live"],
                                   ["--epistemic", "--repeat", "2"],
                                   ["--epistemic", "--live", "--repeat", "99"]])
def test_the_mode_refuses_what_it_does_not_take(monkeypatch, tmp_path, flags):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with pytest.raises(SystemExit) as stop:
        eval_run.main(flags)
    assert stop.value.code == 2


def test_live_resolves_before_any_home_moves(monkeypatch, tmp_path, capsys):
    _store(monkeypatch, tmp_path / "real")
    seen = {}
    target = _target()

    judge = _judge()

    def resolve(*a, **k):
        from grimoire.store import paths
        seen["home"] = paths.home()
        return {"chat": target, "epistemic-access (decide)": judge}
    monkeypatch.setattr(reports, "resolve_connections", resolve)
    monkeypatch.setattr(live, "run", lambda target, **k: ([], []))
    monkeypatch.setattr(decision, "run", lambda target, **k: ([], []))
    # Nothing was asked (the arm is stubbed), so the gate fails: a live run
    # that asked nothing never passes.
    assert eval_run.main(["--epistemic", "--live", "--repeat", "1"]) == 1
    assert seen["home"] == tmp_path / "real"


# ------------------------------------------------------------ the judge (11-S6)


def test_a_paraphrase_the_judge_sees_is_on_the_leakage_line(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path / "real")
    # No token at all: only the judge can see the chalice under the altar.
    fake = _replies("Mara murmurs that the silver cup lies under the altar stone.", True)
    results, records = live.run(_target(), client=fake, real_home=tmp_path / "real",
                                judge=_judge())
    [exposed] = [r for r in records if r["exposed"]]
    assert exposed["judge"] == "leak" and exposed["judged"] == ["WHISPERNEEDLE3"]
    assert not exposed["leaks"]
    assert "reply leaks: 1 of 1 exposed replies" in live.summary(records)
    assert not live.gate(records, live=True, results=results)


@pytest.mark.parametrize("why", ["unanswered", "failed", "no judge"])
def test_an_unjudged_exposed_reply_is_never_clean(monkeypatch, tmp_path, why):
    _store(monkeypatch, tmp_path / "real")
    if why == "failed":
        fake = FakeLLM([["Plain words."]] * len(live.asked()) + [["not a decision"]])
    else:
        fake = _replies("Plain words.", None)
    results, records = live.run(_target(), client=fake, real_home=tmp_path / "real",
                                judge=None if why == "no judge" else _judge())
    [exposed] = [r for r in records if r["exposed"]]
    assert exposed["judge"] == ("unjudged" if why == "no judge" else "failed")
    assert "judged: 0 of 1 exposed replies" in live.summary(records)
    assert not live.gate(records, live=True, results=results)


def test_every_exposure_states_its_secret():
    asked = [i for i in live.asked() if i.live_forbidden]
    assert asked and all(dict(i.facts).keys() == set(i.live_forbidden) for i in asked)


# ------------------------------------------------------------ the Decision gate


GATED = {("7", "TOLDNEEDLE7"): ("told", False), ("15", "KEPTNEEDLE15"): ("told", True),
         ("15b", "GONENEEDLE15"): ("stood", True), ("10", "LEGACYNEEDLE10"): ("stood", False),
         ("8", "SECRETNEEDLE8"): ("stood", True)}


def test_each_gated_part_is_one_production_would_ask(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    items = decision.gated()
    assert {(i.case.id, i.needle) for i in items} == set(GATED)
    for item in items:
        with live.offline._home():
            ctx = decision.plant(item)
        channel, negative = GATED[(item.case.id, item.needle)]
        assert ctx["part"].channel == channel and item.negative == negative
        # The item carries the part, and no basis code.
        context = ctx["items"][0].context
        assert item.needle in context
        assert not any(b in context for b in ("not_recorded_present", "scene_cast",
                                              "summary_needs_grant", "no_record"))


@pytest.mark.parametrize(("answer", "leaks", "correct"), [
    ("known", True, False), ("experienced", True, False), ("narrator_only", False, True),
    (None, False, True)])
def test_the_gate_grades_a_negative(monkeypatch, tmp_path, answer, leaks, correct):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    [item] = [i for i in decision.gated() if i.needle == "KEPTNEEDLE15"]
    with live.offline._home():
        ctx = decision.plant(item)
    checks = {c.name: c.ok for c in decision.grade(
        item, ctx, decision_reply({"access": answer}))}
    assert checks == {"gate.false_known": not leaks, "gate.gold": correct}


def test_the_gate_end_to_end_reports_false_known_apart(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path / "real")
    fake = FakeLLM([[decision_reply({"access": "known"})]])
    results, records = decision.run(_judge(), client=fake, real_home=tmp_path / "real")
    assert len(results) == len(GATED) == fake.calls
    assert all(r.rows is not None and r.rows[0]["task"] == decision.TASK for r in results)
    line = decision.summary(records)
    # Every negative answered `known`: each is a false known.
    assert "false known: 3 of 3 negative answers" in line
    assert "accuracy (not a leakage measure): 2 of 5 answered" in line
    assert not decision.gate(records)
    fake = FakeLLM([[decision_reply({"access": "narrator_only"})]])
    _results, records = decision.run(_judge(), client=fake, real_home=tmp_path / "real")
    assert decision.gate(records) and "false known: 0 of 3" in decision.summary(records)
    assert not decision.gate([])


def test_the_judge_and_the_gate_decide_on_the_epistemic_route():
    from grimoire.store import routing
    for task in (live.JUDGE_TASK, decision.TASK):
        route = routing.route(task)
        assert route is not None and route.key == "epistemic" and route.operation == "decide"
    assert live.TASK == "chat" and routing.route(live.TASK).operation == "generate"


def test_a_gated_part_no_call_answered_is_never_answered(monkeypatch, tmp_path):
    # Review finding 1: a failed call renders as a null, which reads as an
    # abstention -- so "answered" comes from the decision's own records.
    from grimoire.llm_errors import LLMError
    _store(monkeypatch, tmp_path / "real")
    fake = FakeLLM([["x"]], error=LLMError("rate_limit", "slow down"))
    _results, records = decision.run(_judge(), client=fake, real_home=tmp_path / "real")
    assert records and not any(r["answered"] for r in records)
    assert not any(r["false_known"] for r in records)
    assert not decision.gate(records)
    assert f"{len(records)} failed" in decision.summary(records)
    # A reply that never reaches the item is no answer either.
    fake = FakeLLM([["{}"]])
    _results, records = decision.run(_judge(), client=fake, real_home=tmp_path / "real")
    assert not any(r["answered"] for r in records) and not decision.gate(records)


def test_an_accuracy_miss_alone_does_not_fail_the_run(monkeypatch, tmp_path, capsys):
    # Review finding 2: the bar is no false known and every part answered.
    _store(monkeypatch, tmp_path / "real")
    clean = {"case": "1", "check": 0, "reader": "m", "repeat": 0, "answered": True,
             "error": "", "prompt_clean": True, "exposed": [], "leaks": [],
             "contamination": [], "recall": 1.0, "judge": "", "judged": []}
    missed = reports.Result(decision.as_case(decision.gated()[0]), "baseline",
                            [Check("gate.false_known", True, ""),
                             Check("gate.gold", False, "missed")], "")
    gate_record = {"case": "15", "needle": "KEPTNEEDLE15", "repeat": 0, "negative": True,
                   "answered": True, "error": "", "correct": False, "false_known": False}
    monkeypatch.setattr(eval_run, "_epistemic_live",
                        lambda args: ([], [missed], [clean], [gate_record]))
    assert eval_run.main(["--epistemic", "--live", "--repeat", "1"]) == 0
