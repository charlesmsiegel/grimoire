"""11-S3 phase A: each compose step retrieves for its own perspective.

An NPC's query is built from what it was present for and its own seeds; the
narrator's is 09's, unchanged. `gather_view` classifies what came back, and a
failure narrows: the narrator keeps 09's items, an actor gets nothing.
"""

import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from grimoire import store
from grimoire.routes import history_recall
from grimoire.store import relationship_history, relationships, scenes
from grimoire.store.context import epistemic
from grimoire.store.history import model, query, retrieval, structural
from tests.history_fixtures import absorbed, actor, played
from tests.history_fixtures import cid as cid


def _gather_view(cid, sid, **kwargs):
    return asyncio.run(history_recall.gather_view(
        None, cid, sid, seed="", resolved=None, llm_client=None, embed_client=None, **kwargs))


# ------------------------------------------------------------ the perspective


def test_actor_of_accepts_the_narrator_and_a_character_only():
    assert model.actor_of("narrator") is None
    assert model.actor_of("actor:characters:mara") == "characters:mara"
    for bad in ("actor", "Mara", "actor:pcs:mara", "actor:characters:", "actor:characters:../x",
                "characters:mara", "actor:characters:a/b"):
        with pytest.raises(ValueError):
            model.actor_of(bad)


def test_an_actor_query_validates_and_tier_three_stays_the_narrators(cid):
    q = model.Query(("silver",), perspective="actor:characters:mara")
    retrieval.validate(q)
    with pytest.raises(ValueError):
        retrieval._validate_scope(replace(q, tier_limit=3), "admin", ("other",))


# ------------------------------------------------------------ the actor query


def _stage(cid):
    """Posts 0 and 1 before Mara arrives (1 hidden), post 2 after. The scene
    is newer than any `played` before it, and asks after the silver ledger."""
    store.config.write_config(recap_depth="0")
    sid = played(cid, title="Pier", text="Winifred mentions the BEFOREWORD harbour.")
    scenes.append_message(cid, sid, "user", "A HIDDENWORD aside.")
    scenes.set_excluded(cid, sid, 1, True)
    mara, _ = actor(cid, sid, "Mara")
    scenes.append_message(cid, sid, "user", "Seraphine speaks of the AFTERWORD lantern and the silver ledger.")
    return sid, mara


def test_an_actor_query_reads_only_its_observed_window(cid):
    sid, mara = _stage(cid)
    narrator = query.build(cid, sid)
    assert "beforeword" in narrator.terms and "afterword" in narrator.terms
    own = query.build(cid, sid, actor_ref=f"characters:{mara}")
    assert own.perspective == f"actor:characters:{mara}"
    assert "afterword" in own.terms and "AFTERWORD" in own.texts[0]
    # Windowed before the hidden post is filtered: post 1 still counts, so the
    # interval starts at post 2, not at the arrival line's shifted index.
    assert not {"beforeword", "hiddenword", "harbour"} & set(own.terms)
    assert "BEFOREWORD" not in own.texts[0] and "HIDDENWORD" not in own.texts[0]


def test_a_legacy_presence_record_reads_from_the_last_player_post(cid, monkeypatch):
    sid, mara = _stage(cid)
    monkeypatch.setattr(query.appearances_paths, "record", lambda c: {})
    own = query.build(cid, sid, actor_ref=f"characters:{mara}")
    assert "afterword" in own.terms and "beforeword" not in own.terms


def test_an_actor_query_carries_no_thread_or_commitment(cid, monkeypatch):
    sid, mara = _stage(cid)
    scenes.append_message(cid, sid, "user", "They swear by the Ember Compact.")
    real = query._thread_catalog

    def ledger(c, kind):
        if kind == "thread":
            return {"thread:ember": ("ember compact",)}, {"thread:ember": {"id": "ember"}}
        if kind == "commitment":
            return {"commitment:oath": ("ember compact",)}, {"commitment:oath": {"id": "oath"}}
        return real(c, kind)
    monkeypatch.setattr(query, "_thread_catalog", ledger)
    narrator = query.prepare(cid, sid)
    assert "thread:ember" in narrator.query.subjects
    own = query.prepare(cid, sid, actor_ref=f"characters:{mara}")
    assert not [s for s in own.query.subjects if s.startswith(("thread:", "commitment:"))]
    assert "thread" not in own.query.scopes
    assert not own.catalog.threads and own.catalog.actor == f"characters:{mara}"
    assert "ember compact" not in own.weights
    # And a passed query cannot reintroduce one: `subjects` keeps an actor's
    # own seeds, filtered by the catalog it may search with.
    assert query.subjects(cid, own.catalog, "", ("thread:ember", f"characters:{mara}")) == (
        f"characters:{mara}",)


def test_an_actors_seeds_are_its_own(cid):
    sid, mara = _stage(cid)
    winifred, _ = actor(cid, sid, "Winifred")
    ilse, _ = actor(cid, sid, "Ilse")
    relationships.set_feeling(cid, f"characters:{mara}", f"characters:{winifred}",
                              1, 0, 2, "wary")
    relationships.set_feeling(cid, f"characters:{ilse}", f"characters:{winifred}",
                              3, 3, 0, "fond")
    own = query.prepare(cid, sid, actor_ref=f"characters:{mara}").query
    assert f"characters:{winifred}" in own.subjects and f"characters:{mara}" in own.subjects
    # No group or involvement join: those are inferences over the ledger.
    assert not [s for s in own.subjects if s.startswith("groups:")]


def test_an_actor_reads_only_relationship_pairs_it_is_in(cid, monkeypatch):
    asked = []
    monkeypatch.setattr(relationship_history, "for_pair",
                        lambda c, a, b: asked.append({a, b}) or [])
    present = frozenset({"characters:mara", "characters:winifred", "characters:ilse"})
    structural._relationships(cid, present, "characters:mara")
    assert asked and all("characters/mara" in pair for pair in asked)
    asked.clear()
    structural._relationships(cid, present)
    assert {"characters/winifred", "characters/ilse"} in asked


def test_two_others_relationship_history_never_moves_an_actors_candidates(cid):
    old = played(cid, text="Mara and Winifred quarrel at the pier.")
    absorbed(cid, old)
    sid, mara = _stage(cid)
    winifred, _ = actor(cid, sid, "Winifred")
    ilse, _ = actor(cid, sid, "Ilse")
    own = query.prepare(cid, sid, actor_ref=f"characters:{mara}").query
    q = replace(own, scopes=frozenset({"relationship"}))

    narrator = replace(q, perspective="narrator")

    def candidates(query_):
        docs = retrieval._eligible(cid, sid)
        return [c.scene.sid for c in structural.candidates(cid, sid, query_, docs)]
    before = candidates(q)
    relationship_history.append(cid, [relationship_history.row(
        "bond", f"characters/{winifred}", f"characters/{ilse}", label="rivals", before="",
        after="rivals", scene=old)])
    assert candidates(q) == before == []
    assert candidates(narrator) == [old]  # the control: the narrator does rank by it


# ------------------------------------------------------------ gather_view


def test_off_gives_no_view_and_reads_nothing(cid, monkeypatch):
    sid = played(cid)
    store.config.write_config(history_recall_depth="0")
    monkeypatch.setattr(query, "prepare", lambda *a, **k: pytest.fail("off prepared history"))
    assert _gather_view(cid, sid) is None
    assert _gather_view(cid, sid, actor_ref="characters:mara") is None


def test_the_narrator_view_classifies_over_everyone_present(cid):
    old = played(cid)
    absorbed(cid, old)
    sid, mara = _stage(cid)
    view = _gather_view(cid, sid)
    assert view.perspective.actor is None
    assert f"characters:{mara}" in view.perspective.present
    assert view.perspective.identity == store.scenes.identity.scene_identity(cid, sid)
    assert [c.item.scene.sid for c in view.items] == [old]
    assert view.evidence.items and view.evidence.ceiling


def test_an_actor_view_is_built_for_that_actor_and_never_reranked(cid, monkeypatch):
    store.config.write_config(history_recall_rerank="on")
    old = played(cid, text="The AFTERWORD lantern was lit.")
    absorbed(cid, old, summary="The AFTERWORD lantern was lit.")
    sid, mara = _stage(cid)

    async def no_rerank(*a, **k):
        pytest.fail("an actor's retrieval was reranked")
    monkeypatch.setattr(history_recall, "_rerank", no_rerank)
    view = _gather_view(cid, sid, actor_ref=f"characters:{mara}")
    assert view.perspective.actor == f"characters:{mara}"
    assert view.evidence.coverage.rerank == "off"
    # Mara was in no earlier scene: her view holds nothing she could see.
    assert view.visible() == ()


def test_an_actor_omits_no_summary(cid, monkeypatch):
    sid, mara = _stage(cid)
    seen = []
    real = store.history.retrieve

    async def capture(*args, **kwargs):
        seen.append(kwargs["summary_omissions"])
        return await real(*args, **kwargs)
    monkeypatch.setattr(store.history, "retrieve", capture)
    monkeypatch.setattr(history_recall.history_section, "summary_omissions",
                        lambda *a: pytest.fail("an actor computed the narrator's omissions"))
    _gather_view(cid, sid, actor_ref=f"characters:{mara}")
    assert seen == [{}]


def test_a_failed_retrieval_narrows(cid, monkeypatch):
    sid, mara = _stage(cid)

    async def fail(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(store.history, "retrieve", fail)
    assert _gather_view(cid, sid, actor_ref=f"characters:{mara}") is None
    view = _gather_view(cid, sid)
    assert view.perspective.actor is None and view.evidence.coverage.verdict == "error"
    assert view.items == ()


def test_a_failed_classification_narrows(cid, monkeypatch):
    old = played(cid)
    absorbed(cid, old)
    sid, mara = _stage(cid)
    monkeypatch.setattr(epistemic, "classify_evidence",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _gather_view(cid, sid, actor_ref=f"characters:{mara}") is None
    view = _gather_view(cid, sid)
    # 09's items, unannotated: the narrator loses its annotations only.
    assert [c.state for c in view.items] == ["unattributed"]
    assert view.visible()[0].slices


def test_the_sync_bridge_refuses_under_a_lock_and_narrows(cid):
    sid, mara = _stage(cid)
    portal = SimpleNamespace(call=lambda *a, **k: pytest.fail("locked bridge crossed portal"))
    app = SimpleNamespace(state=SimpleNamespace(run_portal=portal))
    with store.locks.campaign_lock(cid):
        actor_view = history_recall.gather_view_sync(
            app, cid, sid, seed="", resolved=None, llm_client=None, embed_client=None,
            actor_ref=f"characters:{mara}")
        narrator = history_recall.gather_view_sync(
            app, cid, sid, seed="", resolved=None, llm_client=None, embed_client=None)
    assert actor_view is None
    assert narrator.evidence.coverage.semantic == "skipped:locked"


def test_the_sync_bridge_classifies_through_the_portal(cid):
    old = played(cid)
    absorbed(cid, old)
    sid, _mara = _stage(cid)
    portal = SimpleNamespace(call=lambda fn: asyncio.run(fn()))
    app = SimpleNamespace(state=SimpleNamespace(run_portal=portal))
    view = history_recall.gather_view_sync(app, cid, sid, seed="", resolved=None,
                                           llm_client=None, embed_client=None,
                                           deadline=time.monotonic() + 4)
    assert [c.item.scene.sid for c in view.items] == [old]


def test_present_roster_drops_the_excluded(cid):
    sid, mara = _stage(cid)
    winifred, _ = actor(cid, sid, "Winifred")
    store.pins.set_rule(cid, f"characters:{winifred}", "exclude", sid=sid)
    assert epistemic.present_roster(cid, sid) == (f"characters:{mara}",)


def test_narrator_view_passes_evidence_through():
    item = model.EvidenceItem(model.SceneRef("001--pier", "a" * 32), "Pier", summary="S",
                              excerpts=(model.Excerpt((model.EvidencePost(0, "", None, "Mara",
                                                                          "x"),), "X"),))
    ev = model.Evidence((item,), ceiling=10)
    view = epistemic.narrator_view("c", "002--x", ev, ("characters:mara",))
    assert view.evidence is ev and view.perspective.present == ("characters:mara",)
    assert [s.text for s in view.items[0].slices] == ["X", "S"]
