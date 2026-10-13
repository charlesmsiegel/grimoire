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
