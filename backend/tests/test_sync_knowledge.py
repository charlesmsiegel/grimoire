"""028: two devices that both appended to one campaign's 020 knowledge log offline merge on sync.

Each device names its batches for itself, so the two offline appends are different files; sync copies
both create-only, and both devices end with one identical log that 020 reads whole and appends to again.
A union 020 would not read whole, or one where both devices ruled on one proposition offline, is held as
one unit, as two diverged logs always were. Synthetic fixtures only.
"""

from __future__ import annotations

import shutil

import pytest

from grimoire.core import DIRECTOR, Belief, Event, Perception, Proposition, Ruling, Subject, actor_knowledge
from grimoire.evidence import JOIN_BATCH_SCHEMA
from grimoire.evidencestore import EvidenceLibrary, EvidenceWriteError
from grimoire.store import LibraryStore, etag
from grimoire.sync import ResolveRefused, identify, resolve, sync
from grimoire.testing.fileops import snapshot

from evidence_fixtures import C, INES, MARA, NPC_CONTROLS, m
from store_helpers import OWNER, owner_handle
from sync_helpers import DESKTOP, PHONE, handles
from test_evidencestore_scenarios import seed_actors

SCOPES = ("sources", "campaigns", "kept")
E1 = Subject("event", "e1")


def evidence(store: LibraryStore) -> EvidenceLibrary:
    return EvidenceLibrary(store, owner_handle(*SCOPES), owner=OWNER)


def append(store: LibraryStore, records: list) -> None:
    evidence(store).append(C, records, controls=NPC_CONTROLS).commit()


def shared_log() -> list:
    """What both devices hold before they go offline: one event two actors saw, and a proposition about it."""
    return [
        Event(C, "e1", "a lantern goes out on the pier", m(1), "director"),
        Proposition(C, "p-1", "someone put the lantern out", m(1), "director", about=(E1,)),
        Perception(C, "v-mara", MARA, "e1", "saw", "the lantern dimmed", m(1), "director", confirmed=True),
        Perception(C, "v-ines", INES, "e1", "saw", "a shadow by the lantern", m(1), "director", confirmed=True),
    ]


def pair_with_log(root) -> tuple[LibraryStore, LibraryStore]:
    """An identified desktop and phone that agree on ``shared_log``."""
    desktop = LibraryStore.create(root / DESKTOP)
    seed_actors(desktop)
    phone = LibraryStore.create(root / PHONE)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    append(desktop, shared_log())
    assert sync(desktop, phone, handles()).conflicts == ()
    return desktop, phone


def knowledge_files(store: LibraryStore) -> dict[str, bytes]:
    prefix = f"campaigns/{C}/"
    return {path: data for path, data in store.scan().files.items()
            if path.startswith((prefix + "events/", prefix + "evidence/"))}


def batch_paths(store: LibraryStore) -> list[str]:
    return sorted(path for path in knowledge_files(store) if "/evidence/batch/" in path)


def read(store: LibraryStore):
    return evidence(store).read(C, requested_by=DIRECTOR)


def offline_appends(desktop: LibraryStore, phone: LibraryStore) -> None:
    """Different chains on each device: Mara believes and the director rules (desktop); Ines suspects (phone)."""
    append(desktop, [
        Belief(C, "b-mara-1", MARA, "p-1", "believed", m(3), DIRECTOR, ("v-mara",)),
        Event(C, "e-desk", "the tide turns", m(4), "director"),
        Ruling(C, "r-1", "p-1", "established", m(4), "director", grounds=(E1,)),
    ])
    append(phone, [
        Belief(C, "b-ines-1", INES, "p-1", "suspected", m(2), DIRECTOR, ("v-ines",)),
        Event(C, "e-phone", "a gull cries", m(2), "director"),
    ])


def test_two_offline_appends_merge_into_one_identical_log_both_devices_read_whole(tmp_path):
    desktop, phone = pair_with_log(tmp_path)
    try:
        offline_appends(desktop, phone)
        report = sync(desktop, phone, handles())
        assert report.conflicts == () and not any("/evidence/" in p or "/events/" in p
                                                  for skipped in report.skipped.values() for p in skipped)
        assert knowledge_files(desktop) == knowledge_files(phone)
        assert len(batch_paths(desktop)) == 3  # the shared batch and one per device, side by side
        for store in (desktop, phone):
            log = read(store)
            assert log.quarantined == () and log.problems == ()
            assert {e.event_id for e in log.knowledge.events} == {"e1", "e-desk", "e-phone"}
            assert {b.belief_id for b in log.knowledge.beliefs} == {"b-mara-1", "b-ines-1"}
        assert sync(desktop, phone, handles()).wrote is False  # converged
    finally:
        desktop.close()
        phone.close()


def test_the_merge_writes_the_same_bytes_whichever_device_starts_it(tmp_path):
    (tmp_path / "one").mkdir()
    desktop, phone = pair_with_log(tmp_path / "one")
    try:
        offline_appends(desktop, phone)
    finally:
        desktop.close()
        phone.close()
    shutil.copytree(tmp_path / "one", tmp_path / "two")
    for folder, order in (("one", (DESKTOP, PHONE)), ("two", (PHONE, DESKTOP))):
        stores = {name: LibraryStore.open(tmp_path / folder / name) for name in (DESKTOP, PHONE)}
        try:
            assert sync(stores[order[0]], stores[order[1]], handles()).conflicts == ()
        finally:
            for store in stores.values():
                store.close()
    for name in (DESKTOP, PHONE):
        one, two = snapshot(tmp_path / "one" / name), snapshot(tmp_path / "two" / name)
        assert {k: v for k, v in one.items() if not k.startswith(".txn")} == {
            k: v for k, v in two.items() if not k.startswith(".txn")}


def test_after_a_merge_each_device_appends_again_by_joining_both_heads(tmp_path):
    desktop, phone = pair_with_log(tmp_path)
    try:
        offline_appends(desktop, phone)
        sync(desktop, phone, handles())
        with pytest.raises(EvidenceWriteError) as caught:  # the merged high-water mark is the desktop's 4
            append(phone, [Event(C, "e-late", "too early", m(3), "director")])
        assert caught.value.reason == "backdated"
        append(phone, [Event(C, "e-p2", "the phone goes on", m(4), "director")])
        append(desktop, [Event(C, "e-d2", "the desktop goes on", m(5), "director")])
        joins = [path for path in batch_paths(phone) if path not in batch_paths(desktop)]
        assert len(joins) == 1 and JOIN_BATCH_SCHEMA.encode() in knowledge_files(phone)[joins[0]]
        assert sync(desktop, phone, handles()).conflicts == ()  # two joins, made offline again, merge too
        assert knowledge_files(desktop) == knowledge_files(phone)
        append(desktop, [Event(C, "e-d3", "one log again", m(6), "director")])
        assert sync(desktop, phone, handles()).conflicts == ()
        for store in (desktop, phone):
            assert read(store).quarantined == () and read(store).problems == ()
            assert {"e-p2", "e-d2", "e-d3"} <= {e.event_id for e in read(store).knowledge.events}
    finally:
        desktop.close()
        phone.close()


def test_merged_records_interleave_by_sequence_and_only_ever_add_knowledge(tmp_path):
    """Merged chronology: the phone's belief at sequence 2 lands in the desktop's past (as a delayed letter
    would), so the desktop's projections below its own mark gain it, and lose nothing."""
    desktop, phone = pair_with_log(tmp_path)
    try:
        offline_appends(desktop, phone)
        before = {as_of: actor_knowledge(read(desktop).knowledge, INES, as_of=as_of) for as_of in range(6)}
        sync(desktop, phone, handles())
        after = read(desktop).knowledge
        for as_of in range(6):
            view = actor_knowledge(after, INES, as_of=as_of)
            assert set(before[as_of].evidence) <= set(view.evidence)
            assert set(before[as_of].current) <= set(view.current)
        assert [b.belief_id for b in actor_knowledge(after, INES, as_of=2).current] == ["b-ines-1"]
        assert actor_knowledge(after, INES, as_of=1).current == ()
    finally:
        desktop.close()
        phone.close()


def test_both_devices_first_appends_merge_as_two_roots(tmp_path):
    desktop = LibraryStore.create(tmp_path / DESKTOP)
    seed_actors(desktop)
    phone = LibraryStore.create(tmp_path / PHONE)
    try:
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        sync(desktop, phone, handles())  # the campaign and its actors, no knowledge yet
        append(desktop, [Event(C, "e-d", "the desktop's first", m(1), "director")])
        append(phone, [Event(C, "e-p", "the phone's first", m(1), "director")])
        assert sync(desktop, phone, handles()).conflicts == ()
        assert read(desktop).quarantined == () and {e.event_id for e in read(phone).knowledge.events} == {"e-d", "e-p"}
        append(phone, [Event(C, "e-2", "joined", m(2), "director")])
        assert read(phone).problems == ()
    finally:
        desktop.close()
        phone.close()


# -- what is never merged ---------------------------------------------------------------------------

def _assert_held_as_one_unit(report, desktop, phone, before) -> None:
    """Nothing of either append crossed; each device's own log is still whole and appendable."""
    assert not [path for path, _ in report.copied if "/events/" in path or "/evidence/" in path]
    for store, device in ((desktop, DESKTOP), (phone, PHONE)):
        assert knowledge_files(store) == before[device]
        assert read(store).quarantined == () and read(store).problems == ()


def _diverge(desktop, phone, desk_records, phone_records):
    append(desktop, desk_records)
    append(phone, phone_records)
    return {DESKTOP: knowledge_files(desktop), PHONE: knowledge_files(phone)}


def test_both_devices_ruling_on_one_proposition_offline_is_a_conflict_never_a_merge(tmp_path):
    desktop, phone = pair_with_log(tmp_path)
    try:
        before = _diverge(desktop, phone, [Ruling(C, "r-d", "p-1", "established", m(2), "director", grounds=(E1,))],
                          [Ruling(C, "r-p", "p-1", "refuted", m(3), "director", grounds=(E1,))])
        report = sync(desktop, phone, handles())
        _assert_held_as_one_unit(report, desktop, phone, before)
        batches = sorted(set(batch_paths(desktop)) ^ set(batch_paths(phone)))
        assert len(batches) == 2
        for path in batches:
            conflict = report.conflict(path)
            assert conflict.code == "unapplicable" and "ruled offline on proposition:p-1" in conflict.detail
        assert all(len(store.conflicts()) == 1 for store in (desktop, phone))  # keep-both: the other's batch
        assert [c.code for c in sync(desktop, phone, handles()).conflicts] == ["unapplicable"] * 2  # stable
        path = next(path for path in batches if (desktop.root / path).exists())  # the desktop's own batch
        data = (desktop.root / path).read_bytes()
        with pytest.raises(ResolveRefused, match="cannot be merged"):
            resolve(desktop, phone, path, etag(data), {DESKTOP: etag(data), PHONE: None}, handles())
        assert knowledge_files(phone) == before[PHONE]
    finally:
        desktop.close()
        phone.close()


def test_rolling_one_devices_offline_append_back_ends_the_conflict(tmp_path):
    """020's documented repair: remove the newest batch and exactly the records it lists; then the
    other device's append syncs over as one device's appends always did."""
    desktop, phone = pair_with_log(tmp_path)
    try:
        _diverge(desktop, phone, [Ruling(C, "r-d", "p-1", "established", m(2), "director", grounds=(E1,))],
                 [Ruling(C, "r-p", "p-1", "refuted", m(3), "director", grounds=(E1,))])
        sync(desktop, phone, handles())
        for path in set(knowledge_files(phone)) - set(knowledge_files(desktop)):
            (phone.root / path).unlink()
        assert sync(desktop, phone, handles()).conflicts == ()
        assert knowledge_files(desktop) == knowledge_files(phone)
        assert [r.ruling_id for r in read(phone).knowledge.rulings] == ["r-d"]
    finally:
        desktop.close()
        phone.close()


def test_a_belief_chain_both_devices_extended_offline_is_held(tmp_path):
    desktop, phone = pair_with_log(tmp_path)
    try:
        append(desktop, [Belief(C, "b-1", MARA, "p-1", "believed", m(2), DIRECTOR, ("v-mara",))])
        sync(desktop, phone, handles())
        before = _diverge(desktop, phone,
                          [Belief(C, "b-d", MARA, "p-1", "rejected", m(3), DIRECTOR, ("v-mara",), supersedes="b-1")],
                          [Belief(C, "b-p", MARA, "p-1", "suspected", m(3), DIRECTOR, ("v-mara",), supersedes="b-1")])
        report = sync(desktop, phone, handles())
        _assert_held_as_one_unit(report, desktop, phone, before)
        assert {c.code for c in report.conflicts} == {"unapplicable"}
        assert all("would not read whole" in c.detail for c in report.conflicts)
    finally:
        desktop.close()
        phone.close()


def test_one_record_id_both_devices_used_for_different_bytes_is_a_conflict_of_its_own(tmp_path):
    desktop, phone = pair_with_log(tmp_path)
    try:
        before = _diverge(desktop, phone, [Event(C, "e-x", "the desktop's version", m(2), "director")],
                          [Event(C, "e-x", "the phone's version", m(2), "director")])
        report = sync(desktop, phone, handles())
        _assert_held_as_one_unit(report, desktop, phone, before)
        assert report.conflict(f"campaigns/{C}/events/e-x.json").code == "missing_ancestor"
        assert all(report.conflict(path) is None for path in batch_paths(desktop))  # the batches are skipped
    finally:
        desktop.close()
        phone.close()


def test_one_record_both_devices_wrote_identically_is_still_held(tmp_path):
    """The same bytes are one file, but each device's batch lists it: a merged log would list it twice."""
    desktop, phone = pair_with_log(tmp_path)
    try:
        same = [Event(C, "e-same", "both devices wrote this", m(2), "director")]
        before = _diverge(desktop, phone, same, same)
        report = sync(desktop, phone, handles())
        _assert_held_as_one_unit(report, desktop, phone, before)
        assert {c.code for c in report.conflicts} == {"unapplicable"}
    finally:
        desktop.close()
        phone.close()


def test_logs_both_devices_appended_before_tagged_batch_names_still_collide(tmp_path):
    """A build before 028's batch names wrote ``batch/0000000002.json`` on both devices: still a keep-both
    conflict on that batch, with the rest of each log held."""
    desktop, phone = pair_with_log(tmp_path)
    try:
        identities = {}
        for store in (desktop, phone):
            identities[store] = (store.root / ".sync" / "device.json").read_bytes()
            (store.root / ".sync" / "device.json").unlink()  # appends as a library with no device tag would
        before = _diverge(desktop, phone, [Event(C, "e-d", "desk", m(2), "director")],
                          [Event(C, "e-p", "phone", m(2), "director")])
        for store, data in identities.items():
            (store.root / ".sync" / "device.json").write_bytes(data)
        report = sync(desktop, phone, handles())
        _assert_held_as_one_unit(report, desktop, phone, before)
        assert report.conflict(f"campaigns/{C}/evidence/batch/0000000002.json").code == "missing_ancestor"
    finally:
        desktop.close()
        phone.close()


def test_a_diverged_log_that_cannot_be_seen_whole_merges_nothing(tmp_path, monkeypatch):
    from grimoire.store import engine
    desktop, phone = pair_with_log(tmp_path)
    try:
        offline_appends(desktop, phone)
        before = {DESKTOP: knowledge_files(desktop), PHONE: knowledge_files(phone)}
        real = engine.Access.list_dir

        def busy(self, listed):
            if self.root == phone.root and tuple(listed) == ("campaigns", C, "events"):
                raise PermissionError(13, "held by another program")
            return real(self, listed)
        monkeypatch.setattr(engine.Access, "list_dir", busy)
        report = sync(desktop, phone, handles())
        monkeypatch.undo()
        _assert_held_as_one_unit(report, desktop, phone, before)
    finally:
        desktop.close()
        phone.close()


def test_a_merge_one_device_may_not_write_still_lands_whole_on_the_other(tmp_path):
    """A denial drops the whole log unit to the denied device only; the other holds the union, whole."""
    desktop, phone = pair_with_log(tmp_path)
    try:
        offline_appends(desktop, phone)
        phone_only = knowledge_files(phone)
        report = sync(desktop, phone, {DESKTOP: owner_handle(*SCOPES), PHONE: owner_handle("sources", "kept")})
        assert {c.code for c in report.conflicts if "/evidence/" in c.path or "/events/" in c.path} == {"denied"}
        assert knowledge_files(phone) == phone_only
        assert set(phone_only) <= set(knowledge_files(desktop))
        for store in (desktop, phone):
            assert read(store).quarantined == () and read(store).problems == ()
        assert sync(desktop, phone, handles()).conflicts == ()
        assert knowledge_files(desktop) == knowledge_files(phone)
    finally:
        desktop.close()
        phone.close()


def test_diverged_records_no_batch_lists_are_held_too(tmp_path):
    """A record put beside the log on each device (a store-level put that bypassed 020) never crosses:
    the merged log would not read whole, and with no batch to blame every copy is the conflict."""
    from grimoire.evidence import encode
    from grimoire.store import KnowledgeRecord
    desktop, phone = pair_with_log(tmp_path)
    try:
        for store, event_id in ((desktop, "e-stray-d"), (phone, "e-stray-p")):
            txn = store.transaction()
            ref, schema, payload = encode(Event(C, event_id, "put beside the log", m(2), "director"))
            txn.put(KnowledgeRecord(ref, schema, payload), None)
            txn.commit(owner_handle(*SCOPES))
        before = {DESKTOP: knowledge_files(desktop), PHONE: knowledge_files(phone)}
        report = sync(desktop, phone, handles())
        assert not [path for path, _ in report.copied if "/events/" in path or "/evidence/" in path]
        assert {c.path.rsplit("/", 1)[1] for c in report.conflicts} == {"e-stray-d.json", "e-stray-p.json"}
        assert {c.code for c in report.conflicts} == {"unapplicable"}
        assert {DESKTOP: knowledge_files(desktop), PHONE: knowledge_files(phone)} == before
    finally:
        desktop.close()
        phone.close()
