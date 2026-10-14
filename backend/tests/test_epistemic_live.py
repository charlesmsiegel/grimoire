"""Spec 11 §8.2's live arm (roadmap 11-S5), offline: its graders on canned
replies, the corpus's questions, and the whole arm end to end with a fake
model -- one generation per asked NPC, each in its own throwaway home,
metered, harvested and graded, with the reply-leak line apart from recall."""

import re
import sys

import pytest

from evals import run as eval_run
from evals import runner as reports
from evals.epistemic import cases, graders, live
from tests.llm_fakes import FakeLLM

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
    from evals.cases import Case
    return reports.resolve_connections((Case(
        id="t", hypothesis="", build=dict, prompt=lambda ctx: [], grade=lambda c, o: [],
        recordings=(), task="chat"),))["chat"]


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
    fake = FakeLLM([["Mara recalls the AFTERNEEDLE1 tide and the OPENNEEDLE3 vigil."]])
    results, records = live.run(target, client=fake, real_home=tmp_path / "real",
                                run_id="r1", repeat=1)
    assert len(results) == len(records) == len(live.asked()) == fake.calls
    # The question is the last message, as Seraphine's line in the story.
    for request in fake.requests:
        last = request["messages"][-1]
        assert last["role"] == "user" and last["content"].startswith("Seraphine")
    # Each case's rows were harvested from its own home: one call each.
    assert all(r.rows is not None and len(r.rows) == 1 for r in results)
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

    def resolve(*a, **k):
        from grimoire.store import paths
        seen["home"] = paths.home()
        return {"chat": target}
    monkeypatch.setattr(reports, "resolve_connections", resolve)
    monkeypatch.setattr(live, "run", lambda target, **k: ([], []))
    # Nothing was asked (the arm is stubbed), so the gate fails: a live run
    # that asked nothing never passes.
    assert eval_run.main(["--epistemic", "--live", "--repeat", "1"]) == 1
    assert seen["home"] == tmp_path / "real"
