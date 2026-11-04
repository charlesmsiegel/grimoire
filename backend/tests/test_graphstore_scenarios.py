"""The Sprint 017 proving scenario, persisted: the director sees a membership; nobody learns it.

A1-A6 of the spec. Relations are written through ``RelationLibrary`` (preview, approve, commit),
the store is reopened, and the graph is rebuilt from canonical files. Actor views come only from
012 projections. All fixtures are synthetic.
"""

from __future__ import annotations

import inspect
import json

import pytest

from grimoire.core import DIRECTOR, Knowledge, Moment, Proposition, Report, Belief, Subject
from grimoire.core import actor_knowledge, knowledge_about
from grimoire.graph import ClaimSet, ClaimShape, claim_matches, claims_about
from grimoire.graphstore import RelationLibrary
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import LibraryStore
from grimoire.testing.fileops import snapshot

from graph_fixtures import (
    C, GUILD, HARBOR, INES, ORRIN, TOMAS, WREN, instance_record, relation_library, seed_campaign,
)
from store_helpers import OWNER


@pytest.fixture
def path(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed_campaign(store)
    return root


def members(library: RelationLibrary, as_of: int) -> dict:
    index = library.graph(C, requested_by=DIRECTOR).index
    found = index.traverse(GUILD, as_of=as_of, requested_by=DIRECTOR, direction="in", predicates={"member_of"})
    return dict(found.reached)


def _memberships(path) -> None:
    with LibraryStore.open(path) as store:
        library = relation_library(store)
        change = library.add(C, "member_of", WREN, GUILD, since=Moment(3), provenance="director")
        preview = change.preview()
        assert list(preview) == ["campaigns/c/relations/r-1.json"]
        document = json.loads(preview["campaigns/c/relations/r-1.json"])
        assert document["schema"] == "grimoire.relation/0" and document["address"] == "campaign:c#relation/r-1"
        assert document["payload"]["subject"] == str(WREN) and document["payload"]["since"] == {"sequence": 3}
        change.commit()
        library.add(C, "member_of", TOMAS, GUILD, since=Moment(1), provenance="director").commit()
        library.add(C, "has_met", INES, WREN, since=Moment(2), provenance="director").commit()


def rumour_knowledge() -> Knowledge:
    def about(*refs):
        return tuple(Subject("instance", str(ref)) for ref in refs)

    return Knowledge(C, propositions=[
        Proposition(C, "p-1", "Ines is in the Ashen Guild", Moment(4), "rumour", about(INES, GUILD)),
        Proposition(C, "p-2", "Wren belongs to the Ashen Guild", Moment(4), "director", about(WREN, GUILD)),
    ], evidence=[
        Report(C, "e-1", TOMAS, "p-1", "told", Moment(5), "director", confirmed=True, sender=WREN,
               attributed_to=ORRIN),
    ], beliefs=[Belief(C, "b-1", TOMAS, "p-1", "suspected", Moment(5), "director", ("e-1",))])


def rumour_claims(knowledge: Knowledge) -> ClaimSet:
    return ClaimSet(knowledge, [ClaimShape(C, "p-1", "member_of", INES, GUILD),
                                ClaimShape(C, "p-2", "member_of", WREN, GUILD)])


def test_a1_director_sees_membership(path):
    _memberships(path)
    with LibraryStore.open(path) as store:  # reopened: rebuilt from canonical files
        library = relation_library(store)
        assert members(library, 9) == {GUILD: 0, WREN: 1, TOMAS: 1}
        assert members(library, 2) == {GUILD: 0, TOMAS: 1}


def test_a2_connectivity_is_not_knowledge(path):
    _memberships(path)
    knowledge = rumour_knowledge()
    claims = rumour_claims(knowledge)
    with LibraryStore.open(path) as store:
        index = relation_library(store).graph(C, requested_by=DIRECTOR).index
        assert index.recorded("member_of", WREN, GUILD, as_of=9, requested_by=DIRECTOR)
        assert index.recorded("member_of", TOMAS, GUILD, as_of=9, requested_by=DIRECTOR)  # a fellow member
        assert index.recorded("has_met", WREN, INES, as_of=9, requested_by=DIRECTOR)
    for who in (TOMAS, INES):
        view = actor_knowledge(knowledge, who, as_of=9)
        assert claims_about(view, claims, WREN).items == ()
        about_wren = knowledge_about(knowledge, who, Subject("instance", str(WREN)), as_of=9)
        assert (about_wren.evidence, about_wren.sent, about_wren.current, about_wren.history) == ((), (), (), ())


def test_a3_false_membership_persisted_graph_unchanged(path):
    _memberships(path)
    knowledge = rumour_knowledge()
    claims = rumour_claims(knowledge)
    found = claims_about(actor_knowledge(knowledge, TOMAS, as_of=9), claims, INES)
    assert [(i.proposition_id, i.position, i.stance) for i in found.items] == [("p-1", "subject", "suspected")]
    assert found.items[0].heard[0].attributed_to == ORRIN and str(WREN) not in repr(found)  # not the true sender
    with LibraryStore.open(path) as store:
        index = relation_library(store).graph(C, requested_by=DIRECTOR).index
        assert claim_matches(index, claims, "p-1", as_of=9, requested_by=DIRECTOR) == ()
        assert claim_matches(index, claims, "p-2", as_of=9, requested_by=DIRECTOR) == ("r-1",)
        assert not index.recorded("member_of", INES, GUILD, as_of=9, requested_by=DIRECTOR)
        assert set(index.traverse(GUILD, as_of=9, requested_by=DIRECTOR, direction="in",
                                  predicates={"member_of"}).reached) == {GUILD, WREN, TOMAS}


def test_a4_end_and_retract(path):
    _memberships(path)
    with LibraryStore.open(path) as store:
        library = relation_library(store)
        tomas = library.graph(C, requested_by=DIRECTOR).index.relations(
            TOMAS, as_of=9, requested_by=DIRECTOR, predicates={"member_of"})[0].ref
        _, etag = library.read(tomas)
        library.end(tomas, until=Moment(12), provenance="director: left the guild", expected=etag).commit()
        mistake = library.add(C, "owns", WREN, HARBOR, since=Moment(1), provenance="import").commit().relation
        _, etag = library.read(mistake)
        library.retract(mistake, provenance="director: recorded in error", expected=etag).commit()
    payload = json.loads((path / "campaigns/c/relations" / f"{tomas.relation_id}.json").read_bytes())["payload"]
    assert payload["provenance"] == "director" and payload["since"] == {"sequence": 1}
    assert payload["ending"]["until"] == {"sequence": 12}
    assert payload["ending"]["provenance"] == "director: left the guild"
    assert payload["recorded_at"] != payload["ending"]["at"]
    assert (path / "campaigns/c/relations" / f"{mistake.relation_id}.json").exists()
    with LibraryStore.open(path) as store:
        library = relation_library(store)
        assert members(library, 11) == {GUILD: 0, WREN: 1, TOMAS: 1}
        assert members(library, 12) == {GUILD: 0, WREN: 1}
        index = library.graph(C, requested_by=DIRECTOR).index
        assert not any(index.recorded("owns", WREN, HARBOR, as_of=s, requested_by=DIRECTOR) for s in (0, 1, 5, 99))
        flagged = [(e.ref, e.retracted) for e in index.history(HARBOR, requested_by=DIRECTOR)]
        assert flagged == [(mistake, True)]


def test_a5_rebuild_equal_and_no_derived_files(path):
    _memberships(path)
    with LibraryStore.open(path) as store:
        before = relation_library(store).graph(C, requested_by=DIRECTOR).index.to_json(requested_by=DIRECTOR)
    files = snapshot(path)
    with LibraryStore.open(path) as store:
        after = relation_library(store).graph(C, requested_by=DIRECTOR).index.to_json(requested_by=DIRECTOR)
    assert before == after
    assert snapshot(path) == files  # building the index wrote nothing
    relation_files = {key for key, data in files.items() if "relations/" in key and data is not None}
    assert relation_files == {f"campaigns/c/relations/r-{n}.json" for n in (1, 2, 3)}


def test_a6_pc_endpoint_is_world_fact(path):
    assert "controls" not in inspect.signature(RelationLibrary.add).parameters
    with LibraryStore.open(path) as store:
        library = relation_library(store)
        # Wren is player-controlled elsewhere; the relation is still an editorial world fact
        library.add(C, "kin_of", WREN, INES, since=Moment(0), provenance="session zero").commit()
        assert library.graph(C, requested_by=DIRECTOR).index.recorded(
            "kin_of", INES, WREN, as_of=0, requested_by=DIRECTOR)


def test_an_approval_gated_relation_write_commits_once_approved(path):
    authority = Authorizer([OWNER])
    authority.add_grant(Grant("g", "app:graph", frozenset({Permission.APPLY}), ResourcePath.parse("campaigns"), OWNER))
    with LibraryStore.open(path) as store:
        library = relation_library(store, handle=authority.handle_for("app:graph"))
        change = library.add(C, "located_in", WREN, HARBOR, since=Moment(1), provenance="director")
        address, data = next(iter(change.preview().items()))
        authority.register_approval(Approval("a1", "app:graph", Permission.APPLY, ResourcePath.parse(address),
                                             payload_digest(data), OWNER))
        change.commit({address: "a1"})
        assert library.read(change.relation)[0].predicate == "located_in"


def test_instance_fixture_is_a_real_campaign_original(path):
    with LibraryStore.open(path) as store:
        assert store.read(WREN).envelope == instance_record(WREN)
