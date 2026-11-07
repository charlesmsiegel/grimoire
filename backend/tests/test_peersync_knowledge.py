"""028 over the peer transport: two paired devices' offline knowledge-log appends merge, and a merge 020
could not read whole is held, exactly as a local sync decides (test_sync_knowledge.py).

Every library here is synthetic and lives under the test's temporary directory; every server listens on
loopback.
"""

from __future__ import annotations

from grimoire.core import DIRECTOR, Belief, Event, Ruling
from grimoire.evidencestore import EvidenceLibrary
from grimoire.peersync import resolve_conflict, transfer
from grimoire.store import RecordEnvelope, etag

from evidence_fixtures import ACTORS, C, INES, MARA, NPC_CONTROLS, m
from peersync_helpers import PHONE, Device, paired, serving
from store_helpers import OWNER
from test_sync_knowledge import E1, shared_log


def _evidence(device: Device) -> EvidenceLibrary:
    return EvidenceLibrary(device.store, device.handle, owner=OWNER)


def _append(device: Device, records: list) -> None:
    _evidence(device).append(C, records, controls=NPC_CONTROLS).commit()


def _knowledge(device: Device) -> dict[str, bytes]:
    prefix = f"campaigns/{C}/"
    return {path: data for path, data in device.store.scan().files.items()
            if path.startswith((prefix + "events/", prefix + "evidence/"))}


def _read(device: Device):
    return _evidence(device).read(C, requested_by=DIRECTOR)


def _paired_with_log(root):  # type: ignore[no-untyped-def]
    desktop, phone, _ = paired(root)
    txn = desktop.store.transaction()
    for actor in ACTORS:
        txn.put(RecordEnvelope(actor, {}), None)
    txn.commit(desktop.handle)
    _append(desktop, shared_log())
    return desktop, phone


def test_offline_appends_on_both_devices_merge_over_the_connection(tmp_path):
    desktop, phone = _paired_with_log(tmp_path)
    try:
        with serving(phone) as server:
            assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
            _append(desktop, [Belief(C, "b-mara-1", MARA, "p-1", "believed", m(3), DIRECTOR, ("v-mara",)),
                              Event(C, "e-desk", "the tide turns", m(4), "director")])
            _append(phone, [Belief(C, "b-ines-1", INES, "p-1", "suspected", m(2), DIRECTOR, ("v-ines",)),
                            Event(C, "e-phone", "a gull cries", m(2), "director")])
            report = transfer(desktop.store, desktop.authority, PHONE, server.address)
            assert report.status == "synced", report
            assert _knowledge(desktop) == _knowledge(phone)
            for device in (desktop, phone):
                log = _read(device)
                assert log.quarantined == () and log.problems == ()
                assert {e.event_id for e in log.knowledge.events} == {"e1", "e-desk", "e-phone"}
            _append(phone, [Event(C, "e-after", "the phone joins both heads", m(4), "director")])
            assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
        assert _knowledge(desktop) == _knowledge(phone) and _read(desktop).problems == ()
    finally:
        desktop.close()
        phone.close()


def test_rulings_both_devices_made_offline_are_held_and_cannot_be_resolved_into_one_log(tmp_path):
    desktop, phone = _paired_with_log(tmp_path)
    try:
        with serving(phone) as server:
            assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
            _append(desktop, [Ruling(C, "r-d", "p-1", "established", m(2), "director", grounds=(E1,))])
            _append(phone, [Ruling(C, "r-p", "p-1", "refuted", m(3), "director", grounds=(E1,))])
            before = {"desktop": _knowledge(desktop), "phone": _knowledge(phone)}
            report = transfer(desktop.store, desktop.authority, PHONE, server.address)
            assert report.status == "conflicts", report
            codes = {c.code for c in report.conflicts}
            assert codes == {"unapplicable"}
            assert _knowledge(desktop) == before["desktop"] and _knowledge(phone) == before["phone"]
            mine = sorted(set(_knowledge(desktop)) - set(_knowledge(phone)))
            batch = next(path for path in mine if "/evidence/batch/" in path)
            data = (desktop.store.root / batch).read_bytes()
            resolved = resolve_conflict(desktop.store, desktop.authority, PHONE, server.address, batch, etag(data),
                                        {"desktop": etag(data), PHONE: None})
            assert resolved.status == "refused" and "cannot be merged" in resolved.reason
        assert _knowledge(phone) == before["phone"]
        for device in (desktop, phone):
            assert _read(device).quarantined == () and _read(device).problems == ()
    finally:
        desktop.close()
        phone.close()
