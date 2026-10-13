"""Caller-directed source scope stays separate from turn retrieval."""
import asyncio
import time

import pytest

from grimoire.store import campaigns, scenes, worlds
from grimoire.store.history import budget, coverage, model, retrieval
from grimoire.store.searchdocs import live
from tests.history_fixtures import absorbed, actor, appear, played
from tests.history_fixtures import cid as cid


def recall(cid, sid, sources, query=None):
    return asyncio.run(retrieval.retrieve(
        cid, sid, query or model.Query(("silver ledger",), terms=("silver", "ledger"), tier_limit=3),
        source_campaigns=sources, origin="search", ceiling=1200, embed_client=None,
        deadline=time.monotonic()+4))


@pytest.mark.parametrize("anchor_history", [False, True])
def test_named_sources_are_enumerated_before_anchor_exhaustion(cid, anchor_history):
    if anchor_history:
        old = played(cid)
        absorbed(cid, old)
    current = played(cid)
    source = campaigns.create_campaign("Realm", worlds.create_world("Realm"))
    old = played(source, text="The silver ledger records amber seal.")
    absorbed(source, old, "The silver ledger records amber seal.")
    evidence = recall(cid, current, (cid, source))
    assert evidence.coverage.eligible == 1 + int(anchor_history)
    item = next(i for i in evidence.items if i.scene.campaign == source)
    assert item.scene.key.startswith(f"campaign:{source}/")
    assert "amber seal" in item.excerpts[0].text
    assert f"[{source}]" in budget.render_item(item)


def test_copied_and_legacy_identities_do_not_collapse(cid):
    current = played(cid)
    sources = []
    for name in ("Realm", "Saltmarch"):
        source = campaigns.create_campaign(name, worlds.create_world("Realm"))
        old = played(source)
        absorbed(source, old)
        sources.append(source)
    paths = [campaigns.campaign_root(c)/"scenes"/"001--saltmarch.md" for c in sources]
    # Both legacy and copied identity shapes are authored independently of retrieval.
    for identity in ("", "copied-identity"):
        for path in paths:
            text = path.read_text(encoding="utf-8")
            lines = [line for line in text.splitlines() if not line.startswith("identity:")]
            lines.insert(1, "identity: " + identity)
            path.write_text("\n".join(lines)+"\n", encoding="utf-8")
        evidence = recall(cid, current, tuple(sources))
        assert len(evidence.candidates) == 2
        assert len({c.scene.key for c in evidence.candidates}) == 2
        assert {i.scene.campaign for i in evidence.items} == set(sources)


def test_unknown_source_is_not_exhausted(cid, monkeypatch):
    old = played(cid)
    absorbed(cid, old)
    current = played(cid)
    original = live.scene_documents
    def unreadable(source, **kwargs):
        if source == "realm":
            raise PermissionError("unreadable")
        return original(source, **kwargs)
    monkeypatch.setattr(live, "scene_documents", unreadable)
    assert recall(cid, current, (cid, "realm")).coverage.verdict == "thin"
    assert recall(cid, current, ("realm",)).coverage.verdict == "empty"
    assert recall(cid, current, ("missing",)).coverage.verdict == "exhausted"
    assert coverage.verdict([], 3, 0, sources_complete=False).verdict == "empty"


def test_unrelated_same_slug_actors_do_not_structurally_join(cid):
    current = played(cid)
    actor(cid, current, "Mara")
    actor(cid, current, "Seraphine")
    source = campaigns.create_campaign("Realm", worlds.create_world("Realm"))
    old = played(source, text="The room is quiet.")
    actor(source, old, "Mara")
    actor(source, old, "Seraphine")
    absorbed(source, old, "The room is quiet.")
    evidence = recall(cid, current, (source,), model.Query(("Mara Seraphine",), tier_limit=3))
    assert evidence.candidates == ()


@pytest.mark.parametrize("tier,origin,sources", [(3,"turn",("realm",)), (3,"search",()),
                                                (2,"search",("realm",)), (2,"turn",("realm",))])
def test_source_contract_refuses_invalid_combinations(cid, tier, origin, sources):
    with pytest.raises(ValueError):
        asyncio.run(retrieval.retrieve(cid, played(cid), model.Query(("",), tier_limit=tier),
            origin=origin, source_campaigns=sources, ceiling=1200, embed_client=None,
            deadline=time.monotonic()+1))


def test_default_scene_key_spelling_is_preserved():
    assert model.SceneRef("001--saltmarch", None).key == "sid:001--saltmarch"
    assert model.SceneRef("001--saltmarch", "identity").key == "identity"
