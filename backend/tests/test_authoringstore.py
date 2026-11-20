"""World authoring (087): review three suggested linked towns and a guild, accept two towns, store nothing else."""

from __future__ import annotations

import pytest

from grimoire.authoring import AuthoringError, AuthoringRequest, Candidate, Decision, Link, Proposal
from grimoire.authoringstore import AuthoringLibrary
from grimoire.changesetstore import ChangeSetConflict
from grimoire.core import DIRECTOR
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_from_payload
from grimoire.core.facets import Spatial
from grimoire.core.identity import SourceRef
from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest, WriteDenied
from grimoire.testing.fileops import snapshot
from grimoire.topology import Place, Topology
from grimoire.topology.codec import encode_topology
from grimoire.topologystore import TopologyLibrary

from authoring_fixtures import COAST, GUILD, LIGHT, MANIFEST, MAP, REALM, entity, seed
from store_helpers import OWNER, owner_handle


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed(opened)
        yield opened


def request(max_candidates: int = 8) -> AuthoringRequest:
    return AuthoringRequest("coast-towns", REALM, "Three linked fishing towns on the Saltmarch Coast and a guild",
                            ("towns sit within the Saltmarch Coast", "roads follow the shore"), max_candidates)


def towns(*, coast: SourceRef = COAST, extra=()) -> Proposal:
    """Three linked towns on the coast, a guild the world already has, and a loose brainstorm."""
    candidates = (
        Candidate("brinewick", "location", "Brinewick", "individual", "A harbor town of smokehouses."),
        Candidate("gullhaven", "location", "Gullhaven", "individual", "A cliff town above the shoals."),
        Candidate("tidemoor", "location", "Tidemoor", "individual", "A marsh town reached at low tide."),
        Candidate("lamplighters", "organization", "The Lamplighters' Guild", "individual", "Keepers of the coast lights."),
        Candidate("bell", "location", "A drowned bell tower", "brainstorm", "Maybe a tower that rings under the sea."),
    ) + tuple(extra)
    links = (
        Link("shore-road", "road", "brinewick", "gullhaven"),
        Link("marsh-road", "road", "gullhaven", "tidemoor"),
        Link("brinewick-in", "within", "brinewick", coast),
        Link("gullhaven-in", "within", "gullhaven", coast),
        Link("tidemoor-in", "within", "tidemoor", coast),
        Link("bell-road", "road", "bell", "tidemoor"),
    )
    return Proposal("p1", request(), candidates, links, rationale="The shore needs towns to visit.",
                    origin="model:planning")


def accept(*keys: str, distinct=()) -> Decision:
    return Decision(tuple(keys), "human:owner", tuple(distinct))


def lib(store) -> AuthoringLibrary:
    return AuthoringLibrary(store, owner_handle())


def written(before: dict, after: dict) -> set[str]:
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
            and (before.get(path) or after.get(path)) is not None}


def record(ref: SourceRef) -> str:
    return f"sources/world/realm/_source/records/{ref.kind}/{ref.entity_id}/{ref.revision}.json"


# -- the proving scenario ---------------------------------------------------------------------------

def test_two_accepted_towns_are_written_linked_the_third_is_not_and_the_duplicate_guild_is_surfaced(store):
    authoring = lib(store)
    review = authoring.review(towns())

    # The guild is surfaced as the world's existing Lamplighters Guild, not as a new record.
    assert [(d.candidate, d.existing, d.existing_name) for d in review.duplicates] == [
        ("lamplighters", GUILD, "Lamplighters Guild")]
    assert review.problems == ()

    with pytest.raises(AuthoringError, match="duplicate_unacknowledged"):
        authoring.draft(review, accept("brinewick", "gullhaven", "lamplighters"))
    draft = authoring.draft(review, accept("brinewick", "gullhaven"))

    brinewick, gullhaven = SourceRef(REALM, "location", "brinewick", "r1"), SourceRef(REALM, "location", "gullhaven", "r1")
    new_map = SourceRef(REALM, "topology", "map", "r1")
    assert dict(draft.records) == {"brinewick": brinewick, "gullhaven": gullhaven}
    assert draft.topology == new_map
    assert [c.key for c in draft.selection.discarded] == ["tidemoor", "lamplighters", "bell"]
    assert [(link.link_id, why) for link, why in draft.selection.dropped] == [
        ("marsh-road", "rejected_endpoint"), ("tidemoor-in", "rejected_endpoint"), ("bell-road", "rejected_endpoint")]
    assert [d.candidate for d in draft.selection.surfaced] == ["lamplighters"]
    assert draft.applicable and not draft.changes.needs_approval
    assert [(item.action, item.path) for item in draft.changes.files] == [
        ("create", record(brinewick)), ("create", record(gullhaven)), ("create", record(new_map)),
        ("manifest", MANIFEST)]

    before = snapshot(store.root)
    accepted = authoring.apply(draft)
    after = snapshot(store.root)
    # Exactly the two towns, the new map and the manifest: Tidemoor, the guild and the brainstorm are not stored.
    assert {path for path in written(before, after) if not path.startswith(".")} == {
        record(brinewick), record(gullhaven), record(new_map), MANIFEST}
    assert accepted.records == {"brinewick": brinewick, "gullhaven": gullhaven} and accepted.topology == new_map

    view = store.load()
    names = sorted(d.name for d in view.index.definitions() if d.ref.source == REALM and d.ref.kind != "topology")
    assert names == ["Brinewick", "Gullhaven", "Lamplighters Guild", "Old Light", "Saltmarch Coast"]
    stored = view.index.resolve(brinewick)
    assert stored.envelope.extra["schema"] == ENTITY_SCHEMA and stored.envelope.owner == "human:owner"
    town = definition_from_payload(brinewick, ENTITY_SCHEMA, stored.envelope.payload)
    assert town.nature == "individual" and town.traits.has(Spatial)
    assert town.description == "A harbor town of smokehouses."
    assert dict(town.extras)["authoring"] == {"proposal": "p1", "request": "coast-towns",
                                              "suggested_by": "model:planning", "accepted_by": "human:owner"}

    # The links are correct: a road joins the two towns, and both lie within the coast.
    index = TopologyLibrary(store, owner_handle()).view(new_map, requested_by=DIRECTOR).index
    route = index.route(brinewick, gullhaven, requested_by=DIRECTOR)
    assert route is not None and index.route(gullhaven, brinewick, requested_by=DIRECTOR) is not None
    assert set(index.children(COAST, requested_by=DIRECTOR)) == {brinewick, gullhaven}
    assert not index.missing


def test_a_flagged_duplicate_kept_as_distinct_gets_its_own_identity(store):
    authoring = lib(store)
    review = authoring.review(towns())
    draft = authoring.draft(review, accept("lamplighters", distinct=("lamplighters",)))
    created = draft.records["lamplighters"]
    assert created == SourceRef(REALM, "organization", "the-lamplighters-guild", "r1")
    assert draft.topology is None  # an organization joins no road
    authoring.apply(draft)
    assert store.load().index.resolve(GUILD).name == "Lamplighters Guild"  # the existing guild is untouched
    with pytest.raises(AuthoringError, match="distinct_not_flagged"):
        authoring.draft(authoring.review(towns()), accept("brinewick", distinct=("brinewick",)))


def test_a_brainstorm_is_never_storable(store):
    authoring = lib(store)
    with pytest.raises(AuthoringError, match="brainstorm_not_storable"):
        authoring.draft(authoring.review(towns()), accept("brinewick", "bell"))


def test_candidates_sharing_a_name_are_both_flagged(store):
    twin = Candidate("brinewick-2", "location", "brinewick", "individual")
    review = lib(store).review(towns(extra=(twin,)))
    assert {(d.candidate, d.other) for d in review.duplicates if d.other} == {
        ("brinewick", "brinewick-2"), ("brinewick-2", "brinewick")}


# -- staleness and conflicts ------------------------------------------------------------------------

def add_town(store, name: str, entity_id: str, kind: str = "location") -> None:
    ref = SourceRef(REALM, kind, entity_id, "r1")
    manifest = store.read_source(REALM)
    heads = dict(manifest.manifest.heads)
    heads[f"{kind}/{entity_id}"] = "r1"
    txn = store.transaction()
    txn.put(entity(ref, name, spatial=kind == "location"), None)
    txn.put(SourceManifest(REALM, OWNER, "Realm", heads), manifest.etag)
    txn.commit(owner_handle())


def test_a_namesake_added_after_review_makes_the_draft_stale(store):
    authoring = lib(store)
    review = authoring.review(towns())
    add_town(store, "Brinewick", "brinewick-town")
    with pytest.raises(AuthoringError, match="stale_review"):
        authoring.draft(review, accept("brinewick", "gullhaven"))
    fresh = authoring.review(towns())
    assert ("brinewick", SourceRef(REALM, "location", "brinewick-town", "r1")) in {
        (d.candidate, d.existing) for d in fresh.duplicates}


def test_a_world_edited_between_draft_and_apply_writes_nothing(store):
    authoring = lib(store)
    draft = authoring.draft(authoring.review(towns()), accept("brinewick", "gullhaven"))
    add_town(store, "Saltgate", "saltgate")
    before = snapshot(store.root)
    with pytest.raises(ChangeSetConflict) as refused:
        authoring.apply(draft)
    assert [c.reason for c in refused.value.conflicts] == ["manifest_changed"]
    assert written(before, snapshot(store.root)) == set()


def test_an_id_taken_by_another_kind_is_never_reused(store):
    add_town(store, "Brinewick Writ", "brinewick", kind="lore")
    authoring = lib(store)
    draft = authoring.draft(authoring.review(towns()), accept("brinewick"))
    assert draft.records["brinewick"] == SourceRef(REALM, "location", "brinewick-2", "r1")
    assert draft.topology == SourceRef(REALM, "topology", "map", "r1")  # it still lies within the coast


# -- an existing map, and links the world can no longer carry -----------------------------------------

@pytest.fixture
def mapped(tmp_path):
    with LibraryStore.create(tmp_path / "mapped") as opened:
        seed(opened, with_map=True)
        yield opened


def test_an_existing_map_gets_a_new_revision_keeping_its_places(mapped):
    authoring = lib(mapped)
    proposal = towns()
    road = Link("light-road", "road", "brinewick", LIGHT)
    proposal = Proposal("p2", proposal.request, proposal.candidates, proposal.links + (road,), origin="human:owner")
    draft = authoring.draft(authoring.review(proposal), accept("brinewick", "gullhaven"))
    assert draft.topology == MAP.at("r2")
    assert [item.action for item in draft.changes.files] == ["create", "create", "revise", "manifest"]
    authoring.apply(draft)

    brinewick = SourceRef(REALM, "location", "brinewick", "r1")
    index = TopologyLibrary(mapped, owner_handle()).view(MAP.at("r2"), requested_by=DIRECTOR).index
    assert index.route(LIGHT, SourceRef(REALM, "location", "gullhaven", "r1"), requested_by=DIRECTOR) is not None
    assert set(index.children(COAST, requested_by=DIRECTOR)) >= {LIGHT, brinewick}
    old = TopologyLibrary(mapped, owner_handle()).view(MAP, requested_by=DIRECTOR).index
    assert set(old.children(COAST, requested_by=DIRECTOR)) == {LIGHT}  # r1 is kept as it was
    assert mapped.read_source(REALM).manifest.heads["topology/coast-map"] == "r2"


def test_two_maps_need_the_reviewer_to_name_one(mapped):
    graph = Topology(SourceRef(REALM, "topology", "sea-map", "r1"), (Place(COAST),), (), "another")
    stored = mapped.read_source(REALM)
    heads = dict(stored.manifest.heads, **{"topology/sea-map": "r1"})
    txn = mapped.transaction()
    txn.put(RecordEnvelope(graph.ref, encode_topology(graph), name="Sea map"), None)
    txn.put(SourceManifest(REALM, OWNER, "Realm", heads), stored.etag)
    txn.commit(owner_handle())
    authoring = lib(mapped)
    review = authoring.review(towns())
    with pytest.raises(AuthoringError, match="ambiguous_topology"):
        authoring.draft(review, accept("brinewick", "gullhaven"))
    assert authoring.draft(review, accept("brinewick", "gullhaven"), topology="sea-map").topology == graph.ref.at("r2")


def test_links_to_missing_moved_or_unplaced_records_are_dropped_with_their_reason(store):
    ghost = SourceRef(REALM, "location", "nowhere", "r1")
    moved = COAST.at("r0")
    proposal = towns()
    links = (Link("a", "within", "brinewick", ghost), Link("b", "within", "gullhaven", moved),
             Link("c", "road", "tidemoor", GUILD), Link("d", "road", "brinewick", "gullhaven"))
    proposal = Proposal("p3", proposal.request, proposal.candidates, links)
    authoring = lib(store)
    review = authoring.review(proposal)
    assert [(p.link_id, p.reason) for p in review.problems] == [
        ("a", "missing_endpoint"), ("b", "missing_endpoint"), ("c", "not_a_place")]
    draft = authoring.draft(review, accept("brinewick", "gullhaven", "tidemoor"))
    assert [(link.link_id, why) for link, why in draft.selection.dropped] == [
        ("a", "missing_endpoint"), ("b", "missing_endpoint"), ("c", "not_a_place")]
    assert [link.link_id for link in draft.selection.links] == ["d"]


def test_a_link_to_an_endpoint_whose_head_moved_is_dropped(store):
    stored = store.read_source(REALM)
    txn = store.transaction()
    txn.put(entity(COAST.at("r2"), "Saltmarch Coast", spatial=True), None)
    txn.put(SourceManifest(REALM, OWNER, "Realm", dict(stored.manifest.heads, **{"location/saltmarch-coast": "r2"})),
            stored.etag)
    txn.commit(owner_handle())
    review = lib(store).review(towns())
    assert {p.reason for p in review.problems} == {"endpoint_moved"}


def test_reject_writes_nothing_and_ends_the_draft(store):
    authoring = lib(store)
    draft = authoring.draft(authoring.review(towns()), accept("brinewick", "gullhaven"))
    before = snapshot(store.root)
    authoring.reject(draft)
    assert written(before, snapshot(store.root)) == set() and draft.changes.state == "rejected"
    with pytest.raises(Exception):
        authoring.apply(draft)


def test_context_offers_the_world_for_a_proposer_without_its_maps(mapped):
    context = lib(mapped).context(REALM)
    assert context.title == "Realm" and not context.truncated
    assert [(r.kind, r.name) for r in context.records] == [
        ("location", "Old Light"), ("location", "Saltmarch Coast"), ("organization", "Lamplighters Guild")]
    assert lib(mapped).context(REALM, limit=1).truncated
    with pytest.raises(AuthoringError, match="missing_source"):
        lib(mapped).context("world:elsewhere")


def test_an_untrusted_author_needs_a_human_approval_of_every_written_file(store):
    authority = Authorizer([OWNER])
    realm = ResourcePath.parse("sources/world/realm")
    authority.add_grant(Grant("g-owner", OWNER, frozenset({Permission.APPLY}), realm, OWNER))
    authority.add_grant(Grant("g-worker", "app:world-author", frozenset({Permission.APPLY}), realm, OWNER))
    authoring = AuthoringLibrary(store, authority.handle_for("app:world-author"))
    draft = authoring.draft(authoring.review(towns()), accept("brinewick", "gullhaven"))
    assert set(draft.changes.needs_approval) == {item.path for item in draft.changes.files}
    before = snapshot(store.root)
    with pytest.raises(WriteDenied):
        authoring.apply(draft)
    assert snapshot(store.root) == before
    counter = iter(range(1, 100))
    approvals = authoring.approve(draft, authority, approved_by=OWNER, new_id=lambda: f"ap-{next(counter)}")
    authoring.apply(draft, approvals)
    assert store.load().index.resolve(SourceRef(REALM, "location", "brinewick")).name == "Brinewick"
