"""Agentic investigations (082): review a bounded read-only world audit over the native store.

The proving scenario: an audit of a synthetic world finds a missing guild link and a possible duplicate,
cites the exact revisions and words for both, and edits nothing, even when it is cancelled part way.
"""

from __future__ import annotations

import itertools

import pytest
from changeset_fixtures import hand_write
from grimoire.core.identity import SourceRef
from grimoire.investigations import (
    Budget,
    Citation,
    ConsistencyAuditor,
    Finding,
    Finish,
    InvestigationError,
    InvestigationSpec,
    ReadRecord,
    Report,
)
from grimoire.investigationstore import InvestigationService, WorldToolbox
from grimoire.ledgers import DurableAuthority
from grimoire.ledgers.codec import decode_grant
from grimoire.providers.cancel import CancelToken
from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest
from grimoire.testing.fileops import snapshot
from ledger_helpers import fixed_clock
from store_helpers import HARBOR, OWNER, owner_handle

GUILD_REF = "world:harbor/organization/ashen-guild"
WORLD = {
    ("organization", "ashen-guild"): ("Ashen Guild", {"description": "Lamplighters who keep the quay lit.",
                                                      "seat": "world:harbor/location/old-hall"}),
    ("location", "old-hall"): ("Old Hall", {"description": "A burnt-out hall above the quay."}),
    # Wren's text names the guild, but neither record refers to the other: the missing guild link.
    ("character", "wren"): ("Wren", {"description": "A sailor sworn to the Ashen Guild since the fire."}),
    # Mara names the guild too, and refers to it: linked, nothing to report.
    ("character", "mara"): ("Mara", {"description": "Keeps the Ashen Guild's ledgers.", "member_of": GUILD_REF}),
    # Two characters whose names differ only in case and a leading article: a possible duplicate.
    ("character", "seraphine"): ("Seraphine", {"description": "A harbor pilot."}),
    ("character", "seraphine-2"): ("the seraphine", {"description": "Pilots ships past the breakwater."}),
}


def seed(store: LibraryStore, world=WORLD) -> None:
    txn = store.transaction()
    txn.put(SourceManifest(HARBOR, OWNER, "Harbor", {f"{kind}/{entity_id}": "r1" for kind, entity_id in world}),
            None)
    for (kind, entity_id), (name, payload) in world.items():
        txn.put(RecordEnvelope(_ref(kind, entity_id), payload, name=name), None)
    txn.commit(owner_handle())


def _ref(kind: str, entity_id: str) -> SourceRef:
    return SourceRef(HARBOR, kind, entity_id, "r1")


@pytest.fixture
def library(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store)
        authority = DurableAuthority.open(store, [OWNER], clock=fixed_clock())
        yield store, authority, tmp_path / "lib"
        authority.close()


def service(store, authority) -> InvestigationService:
    ids = itertools.count(1)
    return InvestigationService(store, authority, owner=OWNER, new_id=lambda: f"inv-{next(ids)}")


def spec(job_id="audit-1", **budget) -> InvestigationSpec:
    return InvestigationSpec(job_id, HARBOR, "Check the harbor for missing links and duplicates.", Budget(**budget))


def world_files(root) -> dict:
    """Every file of the library a world edit could touch (the authority's own ledger excluded)."""
    return {path: data for path, data in snapshot(root).items() if not path.startswith(("authority", ".txn"))}


# -- the proving scenario ------------------------------------------------------------------------------------

def test_an_audit_finds_a_missing_guild_link_and_a_possible_duplicate_with_cited_evidence(library):
    store, authority, root = library
    before = world_files(root)

    report = service(store, authority).run(spec())

    assert report.status == "finished" and report.rejected == ()
    found = {item.finding.category: item.finding for item in report.findings}
    assert set(found) == {"missing_link", "possible_duplicate"}

    link = found["missing_link"]
    assert link.summary == 'character "Wren" names organization "Ashen Guild", but neither record refers to the other'
    wren, guild = link.citations
    assert (wren.kind, wren.entity_id, wren.revision, wren.field, wren.quote) == (
        "character", "wren", "r1", "/payload/description", "Ashen Guild")
    assert (guild.kind, guild.entity_id, guild.field, guild.quote) == ("organization", "ashen-guild", "/name",
                                                                      "Ashen Guild")
    # each citation is the exact revision the job read, by etag
    assert wren.etag == store.read(_ref("character", "wren")).etag
    assert guild.etag == store.read(_ref("organization", "ashen-guild")).etag

    duplicate = found["possible_duplicate"]
    assert [(c.entity_id, c.quote) for c in duplicate.citations] == [("seraphine", "Seraphine"),
                                                                     ("seraphine-2", "the seraphine")]
    # Mara refers to the guild and the guild to its hall: neither pair is reported
    assert not any(c.entity_id in ("mara", "old-hall") for f in found.values() for c in f.citations)
    # every entity was read, and nothing was written to the library
    assert report.unread == () and len(report.reads) == len(WORLD)
    assert world_files(root) == before
    lines = report.describe()
    assert lines[0] == "investigation audit-1 of world:harbor: finished"
    assert '  cites character/wren@r1 /payload/description "Ashen Guild"' in lines


def test_a_cancelled_audit_keeps_its_cited_findings_and_edits_nothing(library):
    store, authority, root = library
    before = world_files(root)
    cancel = CancelToken()

    class CancelAfterReads(ConsistencyAuditor):
        def next_action(self, observation):
            action = super().next_action(observation)
            if len(self._seen) == 4:
                cancel.cancel()  # a person presses cancel while the worker is choosing its next action
            return action

    report = service(store, authority).run(spec(), CancelAfterReads(HARBOR), cancel=cancel)

    assert report.status == "cancelled"
    # sorted order reads the four characters first: Mara, Seraphine, the seraphine and Wren
    assert [item.finding.category for item in report.findings] == ["possible_duplicate"]
    assert sorted(report.unread) == [("location", "old-hall"), ("organization", "ashen-guild")]
    assert world_files(root) == before
    # the job's read-only grant is gone however it ended
    assert not any(grant.principal == "investigator:audit-1" for grant in authority.active_grants())


def test_every_read_is_an_audited_inspect_decision_and_the_grant_is_revoked(library):
    store, authority, _root = library
    service(store, authority).run(spec())
    mine = [record for record in authority.audit() if record.principal == "investigator:audit-1"]
    assert mine and all(record.permission is Permission.INSPECT and record.allowed and not record.trusted
                        for record in mine)
    assert {record.resource for record in mine} >= {"sources/world/harbor/_source",
                                                    "sources/world/harbor/_source/records/character/wren"}
    assert authority.active_grants() == ()
    # the grant was inspect only, over harbor's own files, from the owner
    assert any("investigator:audit-1 may inspect in sources/world/harbor/_source (granted by human:owner)" in line
               for line in _policy_history(authority))


def test_an_owner_who_revokes_the_job_mid_run_stops_its_reads_and_still_gets_the_report(library):
    store, authority, root = library
    before = world_files(root)

    class RevokedAfterTwoReads(ConsistencyAuditor):
        def next_action(self, observation):
            if len(self._seen) == 2 and authority.active_grants():
                authority.revoke_grant(authority.active_grants()[0].grant_id)  # the owner takes it back
            return super().next_action(observation)

    report = service(store, authority).run(spec(), RevokedAfterTwoReads(HARBOR))

    assert report.status == "finished"
    # the list and three reads ran before the revocation took effect; every read after it was refused
    later_reads = [step.outcome for step in report.steps[4:] if step.tool == "read"]
    assert len(report.reads) == 3 and later_reads and set(later_reads) == {"denied"}
    assert authority.active_grants() == () and world_files(root) == before


def _policy_history(authority):
    """Grant descriptions from the ledger (the grant is revoked, so describe_policy no longer lists it)."""
    ledger = authority.read_ledger("security")
    for entry in ledger.entries:
        if entry.get("kind") == "grant_added":
            yield decode_grant(entry["grant"]).describe()


def test_a_job_handle_reads_nothing_outside_its_source(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        _outside_the_grant(store)


def _outside_the_grant(store):
    seed(store)
    txn = store.transaction()
    txn.put(SourceManifest("world:saltmarch", OWNER, "Saltmarch", {"character/winifred": "r1"}), None)
    txn.put(RecordEnvelope(SourceRef("world:saltmarch", "character", "winifred", "r1"), {"secret": "x"},
                           name="Winifred"), None)
    txn.commit(owner_handle())
    kernel = Authorizer([OWNER])
    kernel.add_grant(Grant("g", "investigator:x", frozenset({Permission.INSPECT}),
                           ResourcePath.parse("sources/world/harbor/_source"), OWNER))
    handle = kernel.handle_for("investigator:x")
    other = WorldToolbox(store, handle, "world:saltmarch")
    assert other.list().outcome == "denied" and other.read("character", "winifred").outcome == "denied"
    # and the grant gives no write: an apply or propose on harbor is refused
    assert not handle.authorize(Permission.APPLY, "sources/world/harbor/_source/manifest.json", b"{}").allowed
    assert not handle.authorize(Permission.PROPOSE, "sources/world/harbor/_source/manifest.json", b"{}").allowed
    with pytest.raises(ValueError, match="untrusted"):
        WorldToolbox(store, kernel.handle_for(OWNER, trusted=True), HARBOR)


def test_a_worker_cannot_cite_what_it_did_not_read_or_quote_words_that_are_not_there(library):
    store, authority, _root = library
    real = store.read(_ref("character", "wren")).etag

    class Liar:
        def __init__(self):
            self.script = iter([
                ReadRecord("character", "wren"),
                # cites the guild, which it never read
                Report(Finding("missing_link", "x", (Citation("organization", "ashen-guild", "r1", real),))),
                # quotes words Wren's record does not contain
                Report(Finding("rumor", "Wren betrayed the guild",
                               (Citation("character", "wren", "r1", real, "/payload/description", "betrayed"),))),
                # claims another revision of a record it did read
                Report(Finding("rumor", "y", (Citation("character", "wren", "r9", real),))),
                # a field the record does not have
                Report(Finding("rumor", "z", (Citation("character", "wren", "r1", real, "/payload/allegiance"),))),
                Finish(),
            ])

        def next_action(self, observation):
            return next(self.script)

    report = service(store, authority).run(spec(), Liar())
    assert report.status == "finished" and report.findings == ()
    assert [item.reason for item in report.rejected] == ["unread_evidence", "quote_not_found", "revision_mismatch",
                                                         "unknown_field"]


def test_review_marks_a_finding_stale_when_its_evidence_changed_since_the_run(library):
    store, authority, root = library
    svc = service(store, authority)
    report = svc.run(spec())
    assert svc.review(report).stale == ()

    # Wren's head file is edited by hand after the run: the missing-link finding rests on old bytes
    path = "sources/world/harbor/_source/records/character/wren/r1.json"
    hand_write(root, path, (root / path).read_bytes().replace(b"since the fire", b"since the flood"))
    review = svc.review(report)
    link = next(item.finding_id for item in report.findings if item.finding.category == "missing_link")
    assert review.stale == (link,)
    change = next(item for item in review.findings if item.finding.finding_id == link).changes
    assert [(c.citation.entity_id, c.reason) for c in change] == [("wren", "edited")]
    assert f"{link} is stale: character/wren edited" in review.describe()


def test_a_missing_source_is_refused_before_anything_is_granted(library):
    store, authority, _root = library
    with pytest.raises(InvestigationError) as refused:
        service(store, authority).run(InvestigationSpec("a", "world:nowhere", "audit"))
    assert refused.value.reason == "missing_source"
    assert authority.active_grants() == ()


def test_a_read_budget_ends_the_audit_with_what_it_found_and_what_it_left_unread(library):
    store, authority, _root = library
    report = service(store, authority).run(spec(max_reads=3))
    assert (report.status, report.reason) == ("budget_exhausted", "reads")
    assert len(report.reads) == 3 and len(report.unread) == len(WORLD) - 3


def test_a_dangling_reference_cites_the_value_that_names_a_missing_entity(tmp_path):
    world = dict(WORLD)
    world[("item", "lantern")] = ("Lantern", {"maker": "world:harbor/character/winifred",
                                              "kept_at": "world:harbor/location/old-hall@r7"})
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store, world)
        authority = Authorizer([OWNER])
        report = service(store, authority).run(spec())
    dangling = [item.finding for item in report.findings if item.finding.category == "dangling_reference"]
    assert [(f.citations[0].field, f.citations[0].quote) for f in dangling] == [
        ("/payload/kept_at", "world:harbor/location/old-hall@r7"),
        ("/payload/maker", "world:harbor/character/winifred"),
    ]
    assert dangling[0].summary == ('item "Lantern" refers to revision r7 of location/old-hall, which does not '
                                   "exist")


def test_an_entity_load_drops_is_never_read_as_evidence(library):
    store, authority, root = library
    # one id reused across two kinds: load() drops both, so the audit may not cite either
    for kind, name in (("character", "Pell"), ("item", "Pell's Oar")):
        hand_write(root, f"sources/world/harbor/_source/records/{kind}/pell/r1.json",
                   ('{"format": "grimoire.record/1", "name": "%s", "payload": '
                    '{"description": "Named for the Ashen Guild."}}\n' % name).encode())
    assert store.check_definition(SourceRef(HARBOR, "character", "pell")) == "entity_dropped"
    audits = service(store, authority)
    report = audits.run(spec())
    assert not {("character", "pell"), ("item", "pell")} & set(report.reads)
    assert {("character", "pell"), ("item", "pell")} <= set(report.unread)
    assert all(citation.entity_id != "pell" for item in report.findings for citation in item.finding.citations)
    handle_report = audits.run(spec("audit-2"), _ReadOne("character", "pell"))
    assert [step.outcome for step in handle_report.steps if step.tool == "read"] == ["entity_dropped"]


def test_review_marks_evidence_stale_when_load_stops_admitting_it(library):
    store, authority, root = library
    audits = service(store, authority)
    report = audits.run(spec())
    assert any(c.record == ("character", "wren") for f in report.findings for c in f.finding.citations)
    # after the audit, a hand edit reuses Wren's id for an item: load() now drops character/wren
    hand_write(root, "sources/world/harbor/_source/records/item/wren/r1.json",
               b'{"format": "grimoire.record/1", "name": "Wren\'s Knife", "payload": {}}\n')
    review = audits.review(report)
    assert review.stale
    assert any(change.citation.record == ("character", "wren") and change.reason == "entity_dropped"
               for reviewed in review.findings for change in reviewed.changes)


def test_a_field_too_deep_to_cite_is_skipped_and_the_audit_goes_on(tmp_path):
    world = dict(WORLD)
    world[("item", "chest")] = ("Chest", {"k" * 600: "world:harbor/character/nobody"})
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store, world)
        report = service(store, Authorizer([OWNER])).run(spec())
    assert report.status == "finished" and "not checked" in report.note
    assert {item.finding.category for item in report.findings} == {"missing_link", "possible_duplicate"}


class _ReadOne:
    def __init__(self, kind, entity_id):
        self.actions = iter((ReadRecord(kind, entity_id), Finish()))

    def next_action(self, observation):
        return next(self.actions)


def test_listing_shows_an_entity_with_no_head_and_the_audit_reports_it_unread(library):
    store, authority, root = library
    # a stray entity with two revisions and no declared head: listed, never read
    for revision in ("r1", "r2"):
        hand_write(root, f"sources/world/harbor/_source/records/character/ghost/{revision}.json",
                   b'{"format": "grimoire.record/1", "name": "Ghost", "payload": {}}\n')
    report = service(store, authority).run(spec())
    assert ("character", "ghost") in report.unread
    listed = [s for s in report.steps if s.tool == "list"]
    assert [s.outcome for s in listed] == ["ok"]
