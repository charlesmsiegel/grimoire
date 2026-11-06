"""Sprint 042: the guards between a rebase or canon review and its commit, and grants on world ids.

Synthetic Wren of a synthetic harbor world; no user content.
"""

from __future__ import annotations

import pytest
from chat_fixtures import Clock, Ids
from ledger_helpers import fixed_clock
from store_helpers import owner_handle
from rebase_fixtures import (  # noqa: F401  (fixtures)
    OWNER, R2, SALTMARCH, WREN, chats, edit_instance, envelope, hand_edit_description, publish, put, rebase, reason,
    start, store, wren,
)

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core.entity import EntityDefinition
from grimoire.core.facets import Agent, Durable, FacetSet, Physical, Portable
from grimoire.core.identity import SourceRef
from grimoire.core.portrayal import compose
from grimoire.effective import chat_snapshot, explainer
from grimoire.ledgers import DurableAuthority, security_source_named
from grimoire.rebasestore import CanonPick, CanonProposer, SourceRebase
from grimoire.rebasestore.rebase import _reader
from grimoire.security import Approval, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import LinkSpec, SourceManifest, StaleWrite

GRANTEE = "app:importer"


# -- canon under an approval-gated policy ----------------------------------------------------------

def test_a_gated_canon_proposal_commits_with_its_own_approvals(store, chats):
    started, actor = start(chats)
    authority = DurableAuthority.open(store, [OWNER], clock=fixed_clock())
    try:
        authority.add_grant(Grant("gs", GRANTEE, frozenset({Permission.APPLY}), ResourcePath.parse("sources"), OWNER))
        proposal = CanonProposer(store, authority.handle_for(GRANTEE), owner=OWNER).propose(
            started.chat, SALTMARCH, [CanonPick(actor, "wren")])
        approvals = {}
        for number, (address, data) in enumerate(sorted(proposal.preview().items())):
            authority.register_approval(Approval(f"ok-{number}", GRANTEE, Permission.APPLY,
                                                 ResourcePath.parse(address), payload_digest(data), OWNER))
            approvals[address] = f"ok-{number}"
        proposal.commit(approvals)
        assert store.check_source(SALTMARCH) != "missing_source"
    finally:
        authority.close()


def test_a_grant_left_on_a_world_id_keeps_it_taken(store, chats):
    started, actor = start(chats)
    authority = DurableAuthority.open(store, [OWNER], clock=fixed_clock())
    try:
        authority.add_grant(Grant("gw", GRANTEE, frozenset({Permission.APPLY}),
                                  ResourcePath.parse("sources/world/saltmarch"), OWNER))
        authority.add_grant(Grant("gn", GRANTEE, frozenset({Permission.APPLY}),
                                  ResourcePath.parse("sources/world"), OWNER))
    finally:
        authority.close()
    proposer = CanonProposer(store, owner_handle(), owner=OWNER)
    assert reason(lambda: proposer.propose(started.chat, SALTMARCH, [CanonPick(actor, "wren")])) == "world_exists"
    # a grant on the whole namespace names no world, so another id is free
    other = proposer.propose(started.chat, "world:tidewater", [CanonPick(actor, "wren")])
    assert other.report.world == "world:tidewater"
    assert reason(lambda: proposer.propose(started.chat, "world:salt:marsh",
                                           [CanonPick(actor, "wren")])) == "not_a_world"


def test_a_grant_names_a_source_by_its_folder_prefix(store):
    authority = DurableAuthority.open(store, [OWNER], clock=fixed_clock())
    try:
        authority.add_grant(Grant("g1", GRANTEE, frozenset({Permission.APPLY}),
                                  ResourcePath.parse("sources/world/salt"), OWNER))
        authority.add_grant(Grant("g2", GRANTEE, frozenset({Permission.APPLY}),
                                  ResourcePath.parse("sources/catalog/cards/_source"), OWNER))
    finally:
        authority.close()
    assert security_source_named(store, "world:salt")
    assert security_source_named(store, "world:salt:marsh")  # under the granted folder
    assert security_source_named(store, "catalog:cards")
    assert not security_source_named(store, "world:saltmarch")
    assert not security_source_named(store, "catalog:other")


# -- canon: a base that changes, and an individual's sections ---------------------------------------

def test_a_canon_proposal_is_stale_when_its_base_is_edited(store, chats):
    started, actor = start(chats)
    proposal = CanonProposer(store, owner_handle(), owner=OWNER).propose(started.chat, SALTMARCH,
                                                                          [CanonPick(actor, "wren")])
    hand_edit_description(store, WREN.at("r1"), "A ferry captain.")
    with pytest.raises(StaleWrite):
        proposal.commit()
    assert store.check_source(SALTMARCH) == "missing_source"


def test_an_individualized_template_actor_keeps_its_sections_as_a_standalone_individual(store):
    sailor = SourceRef("catalog:types", "character", "sailor", "r1")
    put(store, SourceManifest("catalog:types", OWNER, "Types", {"character/sailor": "r1"}),
        envelope(EntityDefinition(sailor, "Sailor", "template", FacetSet([Physical(), Agent()]), "A deckhand.",
                                  {"sections": {"voice-evidence": "Swears in three tongues."}})))
    library = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)
    started = library.start_chat(Launch("character", sailor)).commit()
    [actor] = started.actors
    edit_instance(store, actor, local_name="Moss", individuality={
        "template": str(sailor), "decision_id": "d-1", "given_name": "Moss", "decided_by": OWNER, "scene_id": "s-1"})
    proposal = CanonProposer(store, owner_handle(), owner=OWNER).propose(started.chat, SALTMARCH,
                                                                          [CanonPick(actor, "moss")])
    [entry] = proposal.report.entries
    assert entry.base is None and entry.definition.nature == "individual"
    proposal.commit()
    view = store.load()
    effective = compose(SourceRef(SALTMARCH, "character", "moss", "r1"), view.index, _reader(view), explainer(view))
    assert effective.name == "Moss" and effective.section("voice-evidence") == "Swears in three tongues."
    assert effective.description == "A deckhand."


# -- rebase: what moves between review and commit --------------------------------------------------

def test_a_new_head_since_the_review_of_a_following_instance_is_stale(store, chats, rebase):
    publish(store, wren(R2, facets=(Physical(), Agent(), Durable(5))))
    started, actor = start(chats, WREN)  # follows the head
    edit_instance(store, actor, added=[{"facet": "portable", "bulk": "light"}])
    publish(store, wren(WREN.at("r3"), facets=(Physical(), Agent(), Durable(5), Portable("heavy"))))
    change = rebase.apply(rebase.review(actor), {"facet:portable": "keep"})  # kept as a replaced trait
    publish(store, wren(WREN.at("r4"), facets=(Physical(), Agent(), Durable(5))))  # drops portable again
    with pytest.raises(StaleWrite):
        change.commit()
    assert chat_snapshot(store.load(), started.chat).snapshot.entity(actor).facets.by_name("portable") == Portable(
        "light")


def test_an_edited_base_layer_of_a_derived_upstream_is_stale(store):
    card = SourceRef("catalog:cards", "character", "wren", "r1")
    put(store, SourceManifest("catalog:cards", OWNER, "Cards", {"character/wren": "r1"}),
        envelope(EntityDefinition(card, "Wren", "individual", FacetSet([Physical(), Agent()]), "Card text.", {})))
    port = SourceRef("world:port", "character", "wren")

    def portrayal(ref, facets=()):
        return EntityDefinition(ref, "Wren", "individual", FacetSet(list(facets)), "",
                                {"derivation": {"base": str(card)}})

    links = (LinkSpec(port.at("r1"), "derived_variant", card),)
    put(store, SourceManifest("world:port", OWNER, "Port", {"character/wren": "r1"}, links),
        envelope(portrayal(port.at("r1"))))
    library = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)
    [actor] = library.start_chat(Launch("character", port.at("r1"))).commit().actors
    put(store, SourceManifest("world:port", OWNER, "Port", {"character/wren": "r2"},
                              (*links, LinkSpec(port.at("r2"), "derived_variant", card))),
        envelope(portrayal(port.at("r2"), (Physical(), Durable(5)))))
    rebase = SourceRebase(store, owner_handle(), clock=Clock())
    review = rebase.review(actor)
    change = rebase.apply(review, {row.field: "take" for row in review.rows if row.needs_decision})
    hand_edit_description(store, card, "Edited card text.")
    with pytest.raises(StaleWrite):
        change.commit()

