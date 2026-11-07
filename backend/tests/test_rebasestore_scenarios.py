"""Sprint 042 through a real native store: reviewed rebase of a campaign actor and a chat's canon
proposed as a new reusable world. Synthetic Wren of a synthetic harbor world; no user content.
"""

from __future__ import annotations

import pytest
from rebase_fixtures import (  # noqa: F401  (fixtures)
    NAMESAKE, OWNER, R1, R2, SALTMARCH, SECTIONS, VOICE, WREN, chats, edit_instance, envelope, hand_edit, publish, put,
    rebase, reason, start, store, wren,
)
from store_helpers import owner_handle

from grimoire.chats import Launch
from grimoire.core.facets import Agent, Durable, Physical, Portable
from grimoire.core.identity import AcceptedRef, InstanceRef, RelationRef, SourceRef
from grimoire.core.portrayal import compose
from grimoire.effective import chat_snapshot, explainer
from grimoire.rebasestore import ACTOR_REBOUND, ANCESTOR_UNVERIFIED, HISTORY_REVIEW, CanonPick, CanonProposer
from grimoire.rebasestore.rebase import _reader
from grimoire.store import RecordEnvelope, ScopeRecord, SourceManifest, StaleWrite
from grimoire.core.entity_codec import ENTITY_SCHEMA
from grimoire.testing.fileops import snapshot as files_of


def campaign_files(store, campaign):
    return {path: data for path, data in files_of(store.root).items() if path.startswith(f"campaigns/{campaign}/")}


def history(store, campaign, actor):
    put(store, ScopeRecord(RelationRef(campaign, "r-1"), "test.relation/1",
                           {"subject": str(actor), "object": "campaign:elsewhere/character/gull", "kind": "owes"}),
        ScopeRecord(AcceptedRef(campaign, "e-1"), "test.accepted/1", {"actor": str(actor), "summary": "Saved a ferry"}))


# -- scenario 1: the proof --------------------------------------------------------------------------

def test_campaign_b_takes_a_corrected_biography_and_campaign_a_keeps_r1(store, chats, rebase):
    a, actor_a = start(chats)
    b, actor_b = start(chats)
    history(store, b.scope.campaign_id, actor_b)
    publish(store, wren(R2, sections={**SECTIONS, "biography": "Born in the harbor town."}))
    roster_before = chats.read_chat(b.chat)[0].roster
    a_before = campaign_files(store, a.scope.campaign_id)
    b_before = campaign_files(store, b.scope.campaign_id)

    review = rebase.review(actor_b)
    assert [(row.field, row.status) for row in review.rows] == [("section:biography", "upstream")]
    assert review.ancestor.ref == R1 and review.upstream.ref == R2
    assert review.repinned == (b.chat,) and review.following == ()
    assert review.history.chats == (b.chat,)
    assert review.history.relations == (RelationRef(b.scope.campaign_id, "r-1"),)
    assert review.history.accepted == (AcceptedRef(b.scope.campaign_id, "e-1"),)
    assert review.notices == (HISTORY_REVIEW,)

    change = rebase.apply(review, {"section:biography": "take"})
    instance_path = f"campaigns/{b.scope.campaign_id}/instances/character/{actor_b.instance_id}.json"
    chat_path = f"campaigns/{b.scope.campaign_id}/chats/{b.chat.chat_id}.json"
    assert set(change.preview()) == {instance_path, chat_path}
    change.commit()

    assert campaign_files(store, a.scope.campaign_id) == a_before
    b_after = campaign_files(store, b.scope.campaign_id)
    assert {path for path in b_before if b_before[path] != b_after.get(path)} == {instance_path, chat_path}
    stored = store.read(actor_b).envelope
    assert stored.payload["source"] == str(R2) and stored.base == str(R2)
    assert stored.base_etag == store.read(R2).etag and stored.derived_from == str(R2)

    record, _ = chats.read_chat(b.chat)
    assert record.roster == roster_before
    assert [(binding.ref, binding.active) for binding in record.bindings] == [(R1, False), (R2, True)]
    b_view = chat_snapshot(store.load(), b.chat).snapshot
    a_view = chat_snapshot(store.load(), a.chat).snapshot
    assert b_view.entity(actor_b).ref == actor_b
    assert b_view.effective(R2).section("biography") == "Born in the harbor town."
    assert a_view.effective(R1).section("biography") == "Born at sea."
    assert rebase.review(actor_a).upstream.ref == R2  # A can still review later; nothing was decided for it


# -- scenario 2 and 3: conflicts --------------------------------------------------------------------

def test_a_local_name_against_an_upstream_rename(store, chats, rebase):
    _, actor = start(chats)
    edit_instance(store, actor, local_name="Captain Wren")
    publish(store, wren(R2, name="Wren Tidewater"))
    review = rebase.review(actor)
    [row] = review.rows
    assert (row.field, row.status) == ("name", "conflict")
    kept = rebase.apply(review, {"name": "keep"})
    assert kept.instance.local_name == "Captain Wren"
    taken = rebase.apply(review, {"name": "take"})
    assert taken.instance.local_name is None
    taken.commit()
    assert "local_name" not in store.read(actor).envelope.payload


def test_facet_rows_through_the_store(store, chats, rebase):
    publish(store, wren(R2, facets=(Physical(), Agent(), VOICE, Durable(5), Portable())))
    _, actor = start(chats, R2)
    edit_instance(store, actor, replaced=[{"facet": "durable", "max_integrity": 9}])
    publish(store, wren(WREN.at("r3"), facets=(Physical(), Agent(), VOICE, Durable(7))))
    review = rebase.review(actor)
    assert [(row.field, row.status) for row in review.rows] == [("facet:durable", "conflict"),
                                                                 ("facet:portable", "upstream")]
    change = rebase.apply(review, {"facet:durable": "keep", "facet:portable": "keep"})
    change.commit()
    payload = store.read(actor).envelope.payload
    assert [dict(facet) for facet in payload["replaced"]] == [{"facet": "durable", "max_integrity": 9}]
    assert [dict(facet) for facet in payload["added"]] == [{"facet": "portable", "bulk": "normal"}]


# -- scenario 4: refusals and staleness -------------------------------------------------------------

def test_a_namesake_is_never_a_rebase_target(store, chats, rebase):
    _, actor = start(chats)
    assert reason(lambda: rebase.review(actor, NAMESAKE)) == "other_entity"
    assert reason(lambda: rebase.review(actor)) == "up_to_date"
    assert reason(lambda: rebase.review(actor, WREN.at("r9"))) == "missing_target"


def test_a_hand_edited_ancestor_is_refused(store, chats, rebase):
    _, actor = start(chats)
    publish(store, wren(R2, name="Wren Tidewater"))
    hand_edit(store, R1, biography="Born on a ferry.")
    assert reason(lambda: rebase.review(actor)) == "ancestor_edited"


def test_undecided_and_not_overridable(store, chats, rebase):
    _, actor = start(chats)
    publish(store, wren(R2, name="Wren Tidewater", sections={**SECTIONS, "biography": "Born in the harbor town."}))
    review = rebase.review(actor)
    assert review.decisions_needed == ("name", "section:biography")
    assert reason(lambda: rebase.apply(review, {"name": "take"})) == "undecided"
    assert reason(lambda: rebase.apply(review, {"name": "take", "section:biography": "keep"})) == "not_overridable"


def test_an_instance_edited_after_review_is_stale(store, chats, rebase):
    _, actor = start(chats)
    publish(store, wren(R2, name="Wren Tidewater"))
    change = rebase.apply(rebase.review(actor), {"name": "take"})
    edit_instance(store, actor, local_name="Wren the Younger")
    before = files_of(store.root)
    with pytest.raises(StaleWrite):
        change.commit()
    assert files_of(store.root) == before


def test_a_chat_that_binds_the_actor_after_review_is_stale(store, chats, rebase):
    """A new chat pinning the old revision would be left behind, so the commit re-checks every chat."""
    started, actor = start(chats)
    publish(store, wren(R2, name="Wren Tidewater"))
    change = rebase.apply(rebase.review(actor), {"name": "take"})
    key = ("character", R1.entity_key)
    chats.start_chat(Launch("character", R1), scope=started.scope, reuse_actors={key: actor}).commit()
    with pytest.raises(StaleWrite):
        change.commit()


def test_a_redefinition_between_review_and_commit_is_stale(store, chats, rebase):
    _, actor = start(chats)
    publish(store, wren(R2, name="Wren Tidewater"))
    change = rebase.apply(rebase.review(actor), {"name": "take"})
    hand_edit(store, R2, biography="Born on a ferry.")
    with pytest.raises(StaleWrite):
        change.commit()


def test_a_rebound_actor_uses_the_revision_its_chat_plays(store, chats, rebase):
    """107 rebinding leaves the instance at r1 while the chat pins r2: r2 is the common ancestor."""
    started, actor = start(chats)
    publish(store, wren(R2, name="Wren Tidewater"))
    _, etag = chats.read_chat(started.chat)
    chats.detach_source(started.chat, R1, "character", expected=etag).commit()
    _, etag = chats.read_chat(started.chat)
    chats.attach_source(started.chat, R2, "character", expected=etag, actor=actor).commit()
    publish(store, wren(WREN.at("r3"), name="Wren Tidewater", sections={**SECTIONS, "biography": "Born ashore."}))
    review = rebase.review(actor)
    assert review.ancestor.ref == R2 and [row.field for row in review.rows] == ["section:biography"]
    assert review.notices == (ACTOR_REBOUND, ANCESTOR_UNVERIFIED, HISTORY_REVIEW)
    rebase.apply(review, {"section:biography": "take"}).commit()
    assert store.read(actor).envelope.payload["source"] == str(WREN.at("r3"))


def test_chats_that_pin_different_revisions_must_be_resolved_first(store, chats, rebase):
    started, actor = start(chats)
    publish(store, wren(R2, name="Wren Tidewater"))
    key = ("character", R1.entity_key)
    chats.start_chat(Launch("character", R2), scope=started.scope, reuse_actors={key: actor}).commit()
    assert reason(lambda: rebase.review(actor)) == "binding_disagrees"


def test_a_chat_that_follows_the_head_is_reported_and_not_rewritten(store, chats, rebase):
    started, actor = start(chats, WREN, follow=True)  # the explicit opt-in (or a chat stored by an earlier build)
    publish(store, wren(R2, name="Wren Tidewater"))
    review = rebase.review(actor)
    assert review.ancestor.ref == R1 and review.repinned == () and review.following == (started.chat,)
    change = rebase.apply(review, {"name": "take"})
    assert set(change.preview()) == {f"campaigns/{actor.campaign_id}/instances/character/{actor.instance_id}.json"}
    change.commit()
    stored = store.read(actor).envelope
    assert stored.payload["source"] == str(WREN) and stored.base == str(R2)


def test_a_campaign_original_has_nothing_to_rebase(store, rebase):
    actor = InstanceRef("q-9", "character", "gull")
    put(store, RecordEnvelope(actor, {"local_name": "Gull", "added": [{"facet": "agent"}]}, name="Gull", owner=OWNER,
                              extra={"schema": ENTITY_SCHEMA}))
    assert reason(lambda: rebase.review(actor)) == "campaign_original"
    assert reason(lambda: rebase.review(InstanceRef("q-9", "character", "nobody"))) == "missing_actor"


# -- scenario 5: chat canon as a new world ----------------------------------------------------------

def test_a_chat_proposes_its_renamed_actor_as_a_new_world(store, chats):
    started, actor = start(chats)
    edit_instance(store, actor, local_name="Captain Wren", added=[{"facet": "durable", "max_integrity": 6}])
    other, stranger = start(chats)
    before = files_of(store.root)
    proposer = CanonProposer(store, owner_handle(), owner=OWNER)
    proposal = proposer.propose(started.chat, SALTMARCH, [CanonPick(actor, "wren")], title="Saltmarch")
    [entry] = proposal.report.entries
    new = SourceRef(SALTMARCH, "character", "wren", "r1")
    assert entry.definition.ref == new and entry.base == R1
    assert entry.same_name == ()  # the namesake is "Wren", not "Captain Wren"
    assert set(proposal.preview()) == set(proposal.report.paths)
    assert all(path.startswith("sources/world/saltmarch/") for path in proposal.preview())
    proposal.commit()

    after = files_of(store.root)
    assert {path: data for path, data in after.items() if not path.startswith("sources/world/saltmarch")} == before
    view = store.load()
    effective = compose(new, view.index, _reader(view), explainer(view))
    assert effective.name == "Captain Wren" and effective.section("biography") == "Born at sea."
    assert effective.traits.by_name("durable") == Durable(6)
    assert store.read(new).envelope.payload["promoted_from"] == {"chat": str(started.chat), "actor": str(actor)}

    assert reason(lambda: proposer.propose(started.chat, SALTMARCH, [CanonPick(actor, "wren")])) == "world_exists"
    assert reason(lambda: proposer.propose(started.chat, "world:tidewater",
                                           [CanonPick(stranger, "wren")])) == "not_in_chat"
    assert reason(lambda: proposer.propose(started.chat, "catalog:tidewater",
                                           [CanonPick(actor, "wren")])) == "not_a_world"

    _, etag = chats.read_scope(started.scope)
    chats.attach_world(started.scope, SALTMARCH, expected=etag).commit()
    captured = chat_snapshot(store.load(), started.chat)
    assert [entry.actor for entry in captured.chat.roster] == [actor]  # ids unchanged
    assert captured.snapshot.entity(actor).name == "Captain Wren"


def test_a_canon_proposal_is_stale_when_its_actor_changes(store, chats):
    started, actor = start(chats)
    proposal = CanonProposer(store, owner_handle(), owner=OWNER).propose(started.chat, SALTMARCH,
                                                                          [CanonPick(actor, "wren")])
    edit_instance(store, actor, local_name="Wren the Elder")
    with pytest.raises(StaleWrite):
        proposal.commit()
    assert store.check_source(SALTMARCH) == "missing_source"


def test_a_namesake_is_reported_never_merged(store, chats):
    started, actor = start(chats)
    put(store, SourceManifest("world:pier", OWNER, "Pier"), envelope(wren(SourceRef("world:pier", "character", "w",
                                                                                     "r1"), name="Gull")))
    edit_instance(store, actor, local_name="Gull")
    proposal = CanonProposer(store, owner_handle(), owner=OWNER).propose(started.chat, SALTMARCH,
                                                                          [CanonPick(actor, "gull")])
    [entry] = proposal.report.entries
    assert entry.same_name == (SourceRef("world:pier", "character", "w"),)
    assert all(link.relation == "derived_variant" and link.object == str(R1)
               for link in proposal.puts[0].links)
