"""Roadmap 10-S3: the planning arms run offline inside the suite.

Recorded planner replies, the real facade and the production phase: the
figures say planning's mechanics work on synthetic recipes, never that a
model would plan this way (that is `evals/run.py --history --plan --live`)."""

import pytest


def test_the_planning_flag_dispatches_its_own_mode(monkeypatch):
    from evals import run
    seen = []
    monkeypatch.setattr(run, "run_history_plan", lambda args: seen.append(args.live) or 0)
    monkeypatch.setattr(run, "run_live", lambda *a, **kw: pytest.fail("ordinary live path"))
    assert run.main(["--history", "--plan"]) == 0
    assert seen == [False]


@pytest.mark.parametrize("flags", [["--plan"], ["--history", "--plan", "--long"]])
def test_the_planning_flag_refuses_what_it_cannot_combine(flags):
    from evals import run
    with pytest.raises(SystemExit):
        run.main(flags)


def test_the_recipes_cover_the_spec_cases_and_their_recordings():
    from evals.history import planning
    assert {c.shape for c in planning.CASES} == set(range(1, 10))
    assert {c.control for c in planning.CASES if c.control} == {"nothing", "found", "young"}
    for case in planning.CASES:
        assert planning.recording(case).get("plan"), case.id
    ids = {v["id"] for v in planning.validation_recordings()["replies"]}
    assert {"invented-handle", "bare-string", "fenced-object", "prose-no-object",
            "over-long-question", "maximal"} <= ids


def test_validation_replies_parse_as_documented():
    from evals.history import planning
    results = planning.run_validation()
    assert results and all(v.passed for v in results), [v for v in results if not v.passed]
    maximal = next(v for v in results if v.id == "maximal")
    assert 0 < maximal.tokens <= 1024


def test_offline_arms_report_recall_gain_and_quiet_controls():
    from evals.history import planning
    results, records = planning.run_all()
    validation = planning.run_validation()
    assert len(records) == len(planning.CASES) * len(planning.ARMS)
    assert planning.gate(records, validation)
    assert all(r.passed for r in results), [(r.case.id, r.failures) for r in results
                                            if not r.passed]
    totals = planning.aggregate(records)
    assert totals["plan+repair"]["recall3"] > totals["hybrid"]["recall3"]
    assert totals["plan"]["recall3"] > totals["hybrid"]["recall3"]
    # The controls, the young campaign included, never add a planned round
    # under the free gate; only the forced arm plans on them.
    for r in records:
        if r["control"] and r["arm"] in ("plan", "plan+repair"):
            assert not r["planned_round"], r
        if r["control"] == "young" and r["arm"] != "plan-always":
            assert r["terminal"] in ("", "not_run:exhausted"), r
        if r["arm"] == "hybrid":
            assert r["terminal"] == "" and r["planner"]["rows"] == 0
    # The repair hop runs on the recipes recorded insufficient, once.
    repaired = {r["case"] for r in records if r["arm"] == "plan+repair"
                and r["terminal"] == "repaired"}
    assert repaired == {"history-plan-2", "history-plan-6"}
    assert all(r["steps"] <= 3 for r in records)
    # Rows are the production meter's, copied into the run as eval rows; the
    # recorded provider reports no tokens, which reads as unreported, not 0.
    assert all(row.get("scope") == "eval" for result in results for row in (result.rows or ()))
    assert totals["plan+repair"]["tokens_per_turn"] is None
    assert totals["plan+repair"]["rows_per_turn"] > totals["plan"]["rows_per_turn"] > 0
    # Recall improves on the recorded replies, but the default gate also
    # needs an agreed cost bound and fully priced planning calls: the
    # recorded provider prices nothing, so the gate is not met even with one.
    assert planning.recall_gain(records)
    assert not planning.default_gate(records)
    assert not planning.default_gate(records, max_usd_per_planned_turn=1.0)
    # Money per arm, three columns apart; the recorded provider prices
    # nothing, so each column is unreported, never $0.
    assert totals["plan+repair"]["money"] == {"cost_usd": None, "estimated_usd": None,
                                              "modelled_usd": None}
    assert "money (USD, never added): billed n/a" in planning.summary(records, validation)


def test_the_money_columns_aggregate_apart():
    from evals.history import planning
    rows = [{"money": {"cost_usd": 0.25, "estimated_usd": None, "modelled_usd": 0.5}},
            {"money": {"cost_usd": None, "estimated_usd": None, "modelled_usd": 0.25}}]
    assert planning._column(rows, "cost_usd") == 0.25
    assert planning._column(rows, "estimated_usd") is None
    assert planning._column(rows, "modelled_usd") == 0.75



def _gate_records(plan_money, recall=(1.0, 0.0)):
    base = {"control": "", "triggered": True, "false_trigger": False, "valid_plans": 1,
            "plan_calls": 1, "drops": 0, "steps": 1, "terminal": "planned",
            "cheap_retrieval_failed": False, "wall_ms": 1.0, "unpriced_calls": 0,
            "planner": {"rows": 1, "prompt_tokens": 10, "completion_tokens": 5},
            "check": {"rows": 0, "prompt_tokens": None, "completion_tokens": None},
            "money": {"cost_usd": None, "estimated_usd": None, "modelled_usd": None},
            "recall1": 0.0, "recall5": 0.0, "excerpt_recall": 0.0, "gain": 1}
    return [{**base, "arm": "plan+repair", "recall3": recall[0], "planned_round": True,
             "plan_money": plan_money},
            {**base, "arm": "hybrid", "recall3": recall[1], "planned_round": False,
             "plan_money": {"cost_usd": None, "estimated_usd": None, "modelled_usd": None,
                            "unpriced_calls": 0}}]


def test_the_default_gate_holds_each_money_column_to_the_bound():
    from evals.history import planning
    cheap = {"cost_usd": 0.001, "estimated_usd": None, "modelled_usd": 0.002,
             "unpriced_calls": 0}
    assert planning.default_gate(_gate_records(cheap), max_usd_per_planned_turn=0.005)
    assert not planning.default_gate(_gate_records(cheap))            # no bound agreed
    assert not planning.default_gate(_gate_records(cheap), max_usd_per_planned_turn=0.0015)
    unpriced = {**cheap, "unpriced_calls": 1}
    assert not planning.default_gate(_gate_records(unpriced), max_usd_per_planned_turn=1.0)
    assert not planning.default_gate(_gate_records(cheap, recall=(0.5, 0.5)),
                                     max_usd_per_planned_turn=1.0)
    # A declined plan lands no round but was still called and charged: it
    # counts toward the cost per planned turn.
    declined = {**_gate_records(cheap)[0], "planned_round": False, "terminal": "declined",
                "plan_money": {**cheap, "cost_usd": 0.02}}
    assert not planning.default_gate([*_gate_records(cheap), declined],
                                     max_usd_per_planned_turn=0.005)
    free = {**declined, "planner": {"rows": 0, "prompt_tokens": None,
                                    "completion_tokens": None},
            "plan_money": {"cost_usd": None, "estimated_usd": None, "modelled_usd": None,
                           "unpriced_calls": 0}}
    assert planning.default_gate([*_gate_records(cheap), free],
                                 max_usd_per_planned_turn=0.005)


def test_the_cost_bound_flag_needs_the_planning_mode():
    from evals import run
    with pytest.raises(SystemExit):
        run.main(["--history", "--plan-cost-bound", "0.01"])
