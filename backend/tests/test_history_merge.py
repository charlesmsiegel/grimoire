"""Round-aware admitted-only RRF, full-item callbacks and legacy scene keys."""

import json

from grimoire.store import config
from grimoire.store.history import coverage, model
from grimoire.store.history import merge_module as merge_mod
from tests.history_fixtures import cid as cid


def candidate(sid, signals=("lexical",), rank=1, identity=None):
    return model.Candidate(
        model.SceneRef(sid, identity),
        (model.Signals(lexical=model.LexicalSignal(1.0, ("ledger",))),),
        {(0, signal): rank for signal in signals},
        1,
        frozenset(signals),
    )


def evidence(candidates, items=()):
    return model.Evidence(
        tuple(items), tuple(candidates), coverage.verdict(candidates, 2, 10), 1200, "digest"
    )


def test_rrf_ignores_unadmitted_signal_ranks(cid):
    a = candidate("001--saltmarch", rank=1)
    b = candidate("002--saltmarch", rank=2)
    b = model.Candidate(
        b.scene, b.rounds, {(0, "lexical"): 2, (0, "semantic"): 1}, b.tier, b.admitted_by
    )
    assert [c.scene.sid for c in merge_mod.order([a, b])] == ["001--saltmarch", "002--saltmarch"]


def test_merge_keeps_round_ranks_and_expands_new_selection(cid):
    config.write_config(history_recall_depth="1")
    a = candidate("001--saltmarch")
    b = candidate("002--saltmarch", rank=2)
    c = candidate("002--saltmarch", rank=1)
    post = model.EvidencePost(7, "r-reply", 1, "Mara", "The ledger is here.")

    def expansion(cand):
        return model.EvidenceItem(
            cand.scene,
            "Saltmarch",
            summary="Mara returns.",
            excerpts=(model.Excerpt((post,), "**Mara:** The ledger is here."),),
        )

    result = merge_mod.merge(evidence([a, b]), evidence([c]), ceiling=1200, expand=expansion)
    assert result.items[0].scene.sid == "002--saltmarch"
    assert result.items[0].title == "Saltmarch" and result.items[0].summary == "Mara returns."
    assert result.items[0].excerpts[0].posts == (post,)
    assert result.items[0].evidence_ids == ("sid:002--saltmarch@r-reply",)
    assert result.candidates[0].ranks == {(0, "lexical"): 2, (1, "lexical"): 1}
    assert len(result.candidates[0].rounds) == 2
    json.dumps(result.detail())


def test_legacy_scenes_keep_distinct_keys_and_existing_items(cid):
    a, b = candidate("001--saltmarch"), candidate("002--saltmarch")
    item = model.EvidenceItem(a.scene, "Saltmarch", summary="Mara returns.")

    def expansion(c):
        return model.EvidenceItem(c.scene, "Realm", summary="Seraphine returns.")

    result = merge_mod.merge(evidence([a], [item]), evidence([b]), ceiling=1200, expand=expansion)
    assert {c.scene.key for c in result.candidates} == {"sid:001--saltmarch", "sid:002--saltmarch"}
    assert {i.scene.key for i in result.items} == {"sid:001--saltmarch", "sid:002--saltmarch"}
    assert next(i for i in result.items if i.scene == a.scene).summary == "Mara returns."


def test_merge_none_callback_does_not_spend_selection_slot(cid):
    config.write_config(history_recall_depth="1")
    a, b = candidate("002--saltmarch"), candidate("001--saltmarch", rank=2)
    result = merge_mod.merge(
        evidence([a, b]),
        evidence([]),
        ceiling=1200,
        expand=lambda c: (
            None
            if c.scene == a.scene
            else model.EvidenceItem(c.scene, "Realm", summary="Mara returns.")
        ),
    )
    assert result.items[0].scene == b.scene


def test_later_summary_only_round_preserves_previously_expanded_posts(cid):
    c = candidate("001--saltmarch")
    post = model.EvidencePost(7, "r-reply", 1, "Mara", "The ledger is here.")
    earlier = model.EvidenceItem(
        c.scene,
        "Saltmarch",
        summary="Mara returns.",
        excerpts=(model.Excerpt((post,), "**Mara:** The ledger is here."),),
    )
    later = model.EvidenceItem(c.scene, "Saltmarch", summary="Mara returns.")
    result = merge_mod.merge(
        evidence([c], [earlier]),
        evidence([c], [later]),
        ceiling=1200,
        expand=lambda candidate: None,
    )
    assert result.items[0].excerpts == earlier.excerpts
    assert result.items[0].excerpts[0].posts == (post,)
    assert result.items[0].evidence_ids == ("sid:001--saltmarch@r-reply",)
