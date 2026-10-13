"""Synthetic gold is planted before retrieval and replay is offline."""
import importlib.util

import pytest


def test_history_corpus_exists():
    assert importlib.util.find_spec("evals.history") is not None


@pytest.mark.parametrize("flags,mode", [(["--history"], "offline"),
    (["--history", "--long"], "long"), (["--history", "--live"], "live")])
def test_history_flags_dispatch_own_mode(monkeypatch, flags, mode):
    from evals import run
    seen = []
    monkeypatch.setattr(run, "run_history", lambda ap, args: seen.append(
        "live" if args.live else "long" if args.long else "offline") or 0, raising=False)
    monkeypatch.setattr(run, "run_live", lambda *a, **kw: pytest.fail("ordinary live path"))
    assert run.main(flags) == 0
    assert seen == [mode]


def test_real_corpus_gold_and_all_offline_arms(monkeypatch, tmp_path):
    from evals.history import cases, runner, synth
    from grimoire.store import scenes
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert {c.shape for c in cases.CASES} == set(range(1, 11))
    planted = synth.build(cases.CASES[4])
    for gold in planted.gold:
        messages = scenes.read_scene(planted.cid, gold.sid)["messages"]
        assert planted.fact in " ".join(m["content"] for m in messages[gold.start:gold.end+1])
    results, records = runner.run_all()
    assert len(records) == 50
    assert all(r["hard_rules"] for r in records)
    assert all(r["added_tokens"] <= r["ceiling"] for r in records)
    # 12.3: retrieval's own time, split into provider time and the rest; the
    # case's wall (packing, any downstream generation) is reported apart.
    assert all(r["network_ms"] <= r["retrieval_ms"] <= r["wall_ms"] for r in records)
    assert all(r["network_ms"] == 0 for r in records
               if r["arm"] in {"context-only", "lexical", "structural+lexical"})
    assert any(r["network_ms"] > 0 for r in records if r["arm"] == "hybrid")
    assert runner.gate(records)
    assert any(r["arm"] == "context-only" and r["recall3"] > 0 for r in records)
    assert any(r["arm"] == "hybrid" and r["semantic_evaluated"] > 0 for r in records)
    assert all(row.get("scope") == "eval" for result in results for row in (result.rows or ()))


def test_grading_uses_planted_ranges_and_packed_text():
    from evals.history import graders, synth
    from grimoire.store.history import model
    gold = synth.Gold("001--saltmarch", "identity", 2, 2)
    post = model.EvidencePost(2, "p-post", None, "Mara", "amberseal")
    item = model.EvidenceItem(model.SceneRef(gold.sid, gold.identity), "Saltmarch",
        excerpts=(model.Excerpt((post,), "amberseal"),))
    assert graders.recalls([gold], [item])[3] == 1
    assert graders.excerpt_recall([gold], [item], "amberseal") == 1
    assert graders.excerpt_recall([gold], [item], "shed") == 0
    assert graders.consistent("The token is amberseal.", "amberseal")
    assert not graders.consistent("The token is not amberseal.", "amberseal")


def test_context_only_rules_read_scene_markers_not_the_shared_fact():
    """The planted fact is shared by gold and forbidden scenes, so the
    context-only arm is graded by each scene's own marker: a forbidden
    scene's line recalled outside the recap fails the rule, and a gold scene
    is credited only when its own line is there."""
    from evals.history import cases, runner, synth
    from grimoire.store.history import model
    case = cases.CASES[6]
    gold = synth.Gold("001--saltmarch", "g", 0, 0, "Tally 0.")
    planted = synth.Planted("realm", "009--saltmarch", (gold,), frozenset({"005--saltmarch"}),
                            "amberseal", case.shape, 3, None, frozenset({"Tally later."}))
    stats = {"network_ms": 0.0, "semantic_evaluated": 0, "retrieval_ms": 0.0}

    def grade(packed, recap=""):
        return runner._metrics(case, planted, "context-only", model.Evidence(ceiling=1200),
                               packed, "", {}, {**stats, "recap": recap}, 1.0, [])

    leaked = grade("Earlier scenes: amberseal. Tally later.")
    assert not leaked["hard_rules"] and leaked["recall1"] == 0
    assert grade("Story so far: Tally later.", recap="Story so far: Tally later.")["hard_rules"]
    assert grade("Earlier scenes: amberseal. Tally 0.")["recall1"] == 1
