"""Roadmap 12-S7: the adoption gate's offline arms and graders run in
`pytest backend` (spec 10; 14 "Eval").

The investigation arm's model is a canned script: these prove the loop, the
final-call checks and the graders over the synthetic corpus, never a model's
quality. The live arms are `evals/run.py --investigate MODE --live`.
"""

from __future__ import annotations

import asyncio

import pytest

from evals import run as eval_run
from evals.investigation import cases, graders, runner, synth


@pytest.fixture(scope="module")
def records():
    _results, found = runner.run_all()
    return found


def test_every_built_entry_point_runs_both_arms_over_the_corpus(records):
    seen = {(r["case"], r["arm"]) for r in records}
    for case in cases.CASES:
        for arm in cases.ARMS[case.mode]:
            assert (case.id, arm) in seen
    assert not [r for r in records if r["error"]]


def test_the_scripted_investigation_is_correct_and_the_offline_incumbent_abstains(records):
    for r in records:
        if r["arm"] == "investigation":
            assert r["grade"] == graders.CORRECT, r
            assert r["usage"]["model_turns"] >= 2
        else:
            assert r["grade"] == graders.ABSTAINED, r


def test_no_leakage_line_fires_and_the_hidden_route_is_clean(records):
    assert all(not r["text_leaks"] and not r["steered"] for r in records)
    hidden = [r for r in records if r["case"] == "unnamed-debt-resolution"]
    assert hidden and all(r["hidden_route"] is True for r in hidden)


def test_the_hidden_route_probe_sees_hidden_text_that_reached_the_index(monkeypatch):
    """The probe is not vacuous: had the hidden post been searchable, a search
    for its words would answer differently once they are gone."""
    with runner.isolated():
        monkeypatch.setattr(synth.scenes, "set_excluded", lambda *a, **k: None)
        planted = synth.debt_without_title()
        assert asyncio.run(runner._hidden_route(planted)) is False


def test_the_gate_reads_per_entry_point_and_e1_is_absent(records):
    verdict = runner.gate(records)
    assert verdict == {"rp": False, "continuity": True, "question": True}
    assert "absent until 12-S6" in runner.summary(records, live=False)


def test_every_case_runs_in_a_long_history_and_a_short_one_fails_the_gate(records):
    assert all(r["scenes"] >= cases.LONG_HISTORY for r in records)
    brief = [{**r, "scenes": 3} if r["case"] == "same-errand-duplicate" else r
             for r in records]
    assert runner.gate(brief)["continuity"] is False
    assert "same-errand-duplicate" in runner.summary(brief, live=False)


def test_a_run_that_came_back_failed_is_an_error(records, monkeypatch):
    """`investigate` returns a failed run rather than raising; the gate
    counts it as it counts a raise."""
    async def failing(case, planted, **_kw):
        return {"state": "failed", "error": "rate_limit", "result": None, "trace": {}}
    monkeypatch.setitem(runner.ARM_FNS, "investigation", failing)
    _results, ran = runner.run_all(selected=("question",))
    failed = [r for r in ran if r["arm"] == "investigation"]
    assert failed and all(r["error"] == "rate_limit" for r in failed)
    assert runner.gate(ran)["question"] is False


def test_an_incumbent_that_errored_is_no_baseline(records):
    broken = [{**r, "error": "LLMError", "grade": graders.ABSTAINED}
              if r["arm"] != "investigation" else r for r in records]
    assert runner.gate(broken) == {"rp": False, "continuity": False, "question": False}


def test_an_arms_wall_time_is_its_own(monkeypatch):
    """The hidden-route probe runs after the arm and is not its latency.
    The arm is stubbed to return at once, so the recorded time can only
    pass the probe's sleep if the probe was timed -- on any runner speed."""
    probed = []

    async def arm(case, planted, **_kw):
        return {"state": "completed", "result": None, "trace": {}}

    async def probe(planted, **_kw):
        probed.append(planted.cid)
        await runner.asyncio.sleep(PROBE_S)
    for name in cases.ARMS["question"]:
        monkeypatch.setitem(runner.ARM_FNS, name, arm)
    monkeypatch.setattr(runner, "_hidden_route", probe)
    monkeypatch.setattr(runner.cases, "CASES",
                        tuple(c for c in cases.CASES if c.id == "thread-change-question"))
    _results, timed = runner.run_all(selected=("question",))
    assert len(probed) == len(timed) == 2
    assert all(r["wall_ms"] < PROBE_S * 1000 for r in timed)


#: How long the probe sleeps: what an arm's time would hold if it were timed.
PROBE_S = 1.0


def test_the_gate_fails_without_the_margin(records):
    worse = [{**r, "grade": graders.ABSTAINED} if r["arm"] == "investigation" else r
             for r in records]
    assert runner.gate(worse)["continuity"] is False
    leaky = [{**r, "steered": ["scene:x"]} if r["arm"] == "investigation" else r
             for r in records]
    assert runner.gate(leaky)["question"] is False


def test_the_gate_refuses_a_run_that_did_not_cover_the_corpus(records):
    """Spec 10.2's items per entry point: a run missing one -- a case dropped,
    or never run in one arm -- cannot pass, however it scored."""
    assert all(c.covers in cases.COVERAGE[c.mode] for c in cases.CASES)
    assert set().union(*cases.COVERAGE.values()) >= {"continuation", "related",
                                                     "thread_change"}
    gap = [r for r in records if r["case"] != "errand-continuation"]
    assert runner.gate(gap)["continuity"] is False
    assert runner.uncovered(gap, "continuity") == {"continuation"}
    assert "corpus items not run: continuation" in runner.summary(gap, live=False)
    one_arm = [r for r in records
               if not (r["case"] == "thread-change-question" and r["arm"] == "retrieval")]
    assert runner.gate(one_arm)["question"] is False


def test_the_investigation_arm_runs_as_the_entry_point_does(monkeypatch):
    """The challenger is offered the production decide tool, and keeps the
    budget's wall when live (offline it has none: a script answers at once)."""
    seen = []
    real = runner.investigation.investigate

    async def spy(app, **kw):
        seen.append(kw)
        return await real(app, **kw)
    monkeypatch.setattr(runner.investigation, "investigate", spy)
    case = next(c for c in cases.CASES if c.mode == "question")
    with runner.isolated():
        planted = runner.synth.RECIPES[case.recipe]()
        client, resolved = runner._client(case, "investigation", planted, live=False,
                                          targets={})
        for live in (False, True):
            runner.asyncio.run(runner._investigate(
                case, planted, client=client, resolved=resolved, run_id="r", live=live,
                decider=runner.offline_resolution(runner.DECIDE_TASK, "decide")))
    offline, live = seen
    assert [s.name for s in offline["extra"]] == ["decide"]
    assert offline["budget"].wall_seconds == 0
    assert live["budget"].wall_seconds > 0


def test_an_arm_leaves_its_home_only_once_no_tool_thread_runs(monkeypatch):
    """A tool the loop abandoned still reads through `GRIMOIRE_HOME`: the arm
    waits for `tool_calls.idle` before its throwaway home is let go."""
    waited = []
    monkeypatch.setattr(runner.tool_calls, "idle", lambda timeout=None: waited.append(
        runner.os.environ.get("GRIMOIRE_HOME")) or True)
    monkeypatch.setattr(runner.cases, "CASES",
                        tuple(c for c in cases.CASES if c.id == "thread-change-question"))
    real = runner.os.environ.get("GRIMOIRE_HOME")
    runner.run_all(selected=("question",))
    assert len(waited) == 2 and all(h and h != real for h in waited)


def test_the_hidden_route_probe_searches_as_the_arms_do_and_costs_them_nothing(monkeypatch):
    """Live, the probe gets the arms' embeddings client -- semantic ranking is
    what could tell a hidden word from a missing one -- and files under its
    own run id, so its calls are never the arm's cost."""
    from grimoire.store import usage
    seen = []

    async def probe(planted, *, embed_client=None, run_id=""):
        seen.append((embed_client, run_id))
        usage.record(task="history-recall", campaign=planted.cid, run_id=run_id,
                     operation="embed", model="vendor/embed", provider="openrouter",
                     prompt_tokens=4, cost_usd=7.0)
        return True
    monkeypatch.setattr(runner, "_hidden_route", probe)
    monkeypatch.setattr(runner.cases, "CASES",
                        tuple(c for c in cases.CASES if c.id == "unnamed-debt-resolution"))
    client = object()
    monkeypatch.setattr(runner.embeddings, "AsyncEmbeddingsClient", lambda: client)

    async def arm(case, planted, **_kw):
        return {"state": "completed", "result": None, "trace": {}}
    for name in cases.ARMS["continuity"]:
        monkeypatch.setitem(runner.ARM_FNS, name, arm)
    monkeypatch.setattr(runner, "_close", lambda *a: runner.asyncio.sleep(0))
    monkeypatch.setattr(runner, "_client", lambda *a, **k: (None, None))
    _results, ran = runner.run_all(live=True, selected=("continuity",), run_id="r9",
                                   space={"space": "s"})
    assert [e for e, _ in seen] == [client, client]
    assert all(rid == runner.probe_run("r9") for _, rid in seen)
    assert all(r["probe_calls"] == 1 and r["money"]["cost_usd"] is None for r in ran)


def test_both_arms_search_with_the_embeddings_client_they_are_handed(monkeypatch):
    """Live, the run hands both arms an embeddings client (and the real
    store's space in the planted home's place), as production searches."""
    handed = []
    real = runner.inv_tools.toolset

    def toolset(run, *a, **k):
        handed.append(run.embed_client)
        return real(run, *a, **k)
    monkeypatch.setattr(runner.inv_tools, "toolset", toolset)
    client = object()
    case = next(c for c in cases.CASES if c.mode == "question")
    with runner.isolated():
        planted = runner.synth.RECIPES[case.recipe]()
        runner.asyncio.run(runner._retrieval(case, planted, client=None, resolved=None,
                                             run_id="r", live=False, decider=None,
                                             embed_client=client))
        fake, resolved = runner._client(case, "investigation", planted, live=False,
                                        targets={})
        runner.asyncio.run(runner._investigate(
            case, planted, client=fake, resolved=resolved, run_id="r", live=False,
            decider=None, embed_client=client))
    assert handed and all(h is client for h in handed)


# ---- the graders, on planted input ----
def test_a_directed_proposal_running_the_wrong_way_is_wrong():
    gold, way = {"s2"}, ("thread:b", "thread:a")
    right = {"decision": "continuation", "from": "thread:b", "to": "thread:a",
             "evidence_scenes": ["s2"]}
    assert graders.proposal("continuation", gold, right, way) == graders.CORRECT
    turned = {**right, "from": "thread:a", "to": "thread:b"}
    assert graders.proposal("continuation", gold, turned, way) == graders.WRONG
    assert graders.proposal("continuation", gold, turned) == graders.CORRECT


def test_the_incumbent_cites_only_what_it_was_shown():
    shown = ["scene:s1", "scene:s2"]
    reply = 'Here: {"text": "It burned.", "citations": ["scene:s2", "scene:s9", 3]}'
    assert runner._incumbent_answer(reply, shown) == {"text": "It burned.",
                                                      "citations": ["scene:s2"]}
    assert runner._incumbent_answer("It burned.", shown) is None
    assert graders.answer(("burn",), {"s2"}, runner._incumbent_answer(
        '{"text": "It burned.", "citations": []}', shown)) == (graders.WRONG, False)
def test_a_proposal_is_graded_with_uncertain_as_an_abstention():
    gold = {"s3"}
    assert graders.proposal("fulfilled", gold, None) == graders.ABSTAINED
    assert graders.proposal("fulfilled", gold, {"decision": "uncertain"}) == graders.ABSTAINED
    assert graders.proposal("fulfilled", gold,
                            {"decision": "fulfilled", "evidence_scenes": ["s3"]}) == graders.CORRECT
    assert graders.proposal("fulfilled", gold,
                            {"decision": "fulfilled", "evidence_scenes": ["s1"]}) == graders.WRONG
    assert graders.proposal("fulfilled", gold,
                            {"decision": "broken", "evidence_scenes": ["s3"]}) == graders.WRONG
    # The right word with nothing cited is a guess, not a reading.
    assert graders.proposal("fulfilled", gold,
                            {"decision": "fulfilled", "evidence_scenes": []}) == graders.WRONG


def test_an_answer_needs_its_claim_no_denial_and_valid_citations():
    gold, claim = {"s3"}, (("compass",), ("back",))
    assert graders.answer(claim, gold, {"text": "The compass came back.",
                                        "citations": ["scene:s3"]}) == (graders.CORRECT, True)
    assert graders.answer(claim, gold, {"text": "The compass came back.",
                                        "citations": ["scene:s1"]}) == (graders.WRONG, False)
    assert graders.answer(claim, gold, None) == (graders.ABSTAINED, True)
    # Every group must be met, and a denial turns the claim round.
    assert graders.answer(claim, gold, {"text": "The compass is lost.",
                                        "citations": ["scene:s3"]})[0] == graders.WRONG
    for denied in ("No, Mara never brought the compass back.",
                   "The compass came back, but that is unrelated."):
        assert graders.answer(claim, gold, {"text": denied,
                                            "citations": ["scene:s3"]})[0] == graders.WRONG


def test_each_case_refutes_the_inversions_its_claim_would_miss(records):
    """A case's own `refutes` turn its claim round where no general denial
    does; the live table also carries every answer's text to be read."""
    by_id = {c.id: c for c in cases.CASES}
    for text, case_id in (
            ("Mara kept the compass instead of giving it back.", "nickname-promise-question"),
            ("The fire left the warehouse and ledger untouched.", "thread-change-question")):
        case = by_id[case_id]
        found = {"text": text, "citations": [f"scene:{s}" for s in ("g",)]}
        grade, _ = graders.answer(case.claim, {"g"}, found,
                                  denials=(*graders.DENIALS, *case.refutes))
        assert grade == graders.WRONG, text
    answered = [r for r in records if r["mode"] == "question" and r["arm"] == "investigation"]
    assert answered and all(r["written"] for r in answered)


def _step(query, refs):
    return {"turn": 1, "tool": "search_history", "args": {"query": query},
            "result": {"ok": True, "refs": refs, "chars": 1, "truncated": False}}


def test_a_selection_only_a_forbidden_word_led_to_is_steered():
    trace = {"steps": [_step("quittance", ["scene:s2"])]}
    assert graders.steered(trace, ["s2"], ("quittance",)) == ["scene:s2"]
    also = {"steps": [_step("quittance", ["scene:s2"]), _step("boatman coins", ["scene:s2"])]}
    assert graders.steered(also, ["s2"], ("quittance",)) == []
    assert graders.steered(trace, ["s2"], ()) == []
    assert graders.text_leaks(["the quittance note"], ("Quittance",)) == ["Quittance"]


def test_the_cli_refuses_e1_until_it_lands(capsys):
    assert eval_run.main(["--investigate", "rp"]) == 1
    assert "absent until 12-S6" in capsys.readouterr().out
