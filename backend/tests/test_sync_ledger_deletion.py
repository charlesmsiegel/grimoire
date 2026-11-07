"""028: deletion propagation (row 9) never reaches into a campaign's ledger unit (docs/SYNC.md, "Ledgers").

A ledger file (an accepted event, a wrap-up, a holder file, or a relation of a campaign with an accepted
event) is merged with its campaign's unit, never decided alone, so a one-sided removal of one is never
propagated: the ledger merge never merges a deletion, and the unit stops for review (``deleted``) with
nothing removed, archived as a tombstone or kept. A relation of a campaign with no accepted event is an
ordinary record and its deletion propagates; its tombstone then stands beside the unit once the campaign
has events. A deletion of a record a ledger file still names is held back (``delete_dependency``).
Every name is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.store import LibraryStore
from grimoire.sync import MERGE, ResolveRefused, apply_sync, plan_sync, resolve, resolve_ledger, sync, sync_until_quiet
from grimoire.sync.ledger_resolve import plan_resolution

from effects_fixtures import C, KEY2, give
from ledger_fixtures import Device, build_base, pay, read_ledger
from store_helpers import OWNER
from sync_helpers import DESKTOP, PHONE, handles
from test_sync_ledger import ledger_files


@pytest.fixture
def pair(tmp_path: Path):
    base = build_base(tmp_path)
    made = {name: Device(base, tmp_path, name) for name in (DESKTOP, PHONE)}
    assert sync(made[DESKTOP].store, made[PHONE].store, handles()).conflicts == ()
    yield made
    for device in made.values():
        device.close()


def _relations(store: LibraryStore) -> dict[str, bytes]:
    root = store.root / "campaigns" / C / "relations"
    return {path.relative_to(store.root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*.json"))}


def _relation_to(store: LibraryStore, item) -> str:
    """The path of the seed relation ``carries`` Wren to ``item``."""
    for path, data in _relations(store).items():
        if item.instance_id in data.decode("utf-8"):
            return path
    raise AssertionError(f"no relation names {item}")


def _archive(store: LibraryStore, peer: str) -> dict:
    return json.loads(store.read_sync_state(f"peers/{peer}/archive.json"))["entries"]


def _synced_with_an_event(pair) -> None:
    pair[DESKTOP].accept(give())
    report = sync_until_quiet(pair[DESKTOP].store, pair[PHONE].store, handles())
    assert report.conflicts == ()


@pytest.mark.parametrize("kind", ["relation", "holdings", "accepted"])
def test_a_ledger_file_removed_on_one_device_is_never_deleted_on_the_other(pair, kind):
    _synced_with_an_event(pair)
    desktop, phone = pair[DESKTOP].store, pair[PHONE].store
    path = {"relation": lambda: _relation_to(desktop, KEY2), "holdings": lambda: f"campaigns/{C}/holdings/character/tam.json",
            "accepted": lambda: next(p.relative_to(desktop.root).as_posix()
                                     for p in (desktop.root / "campaigns" / C / "accepted").rglob("*.json"))}[kind]()
    original = (phone.root / path).read_bytes()
    agreed = _archive(phone, DESKTOP)[path]
    (desktop.root / path).unlink()
    report = sync(desktop, phone, handles())
    assert report.deleted == ()
    assert (phone.root / path).read_bytes() == original  # never removed, and nothing kept for it
    assert phone.conflicts() == () and desktop.conflicts() == ()
    found = {conflict.path: conflict for conflict in report.conflicts}
    assert found[path].code == "merge_review"
    assert "deleted" in {item.reason for item in found[path].review}
    assert {conflict.code for conflict in report.conflicts} == {"merge_review"}
    for store, peer in ((desktop, PHONE), (phone, DESKTOP)):  # no tombstone: the pair still agrees on the file
        assert _archive(store, peer)[path] == agreed
    with pytest.raises(ResolveRefused, match="resolve_ledger"):  # never resolved toward the deletion either
        resolve(desktop, phone, path, None, {DESKTOP: None, PHONE: agreed["agreed"]}, handles())
    again = sync(desktop, phone, handles())
    assert {(conflict.path, conflict.code) for conflict in again.conflicts} == {(path, "merge_review")}
    assert not again.wrote
    # keeping the device that still holds it puts it back (the other device's owner discards the removal)
    resolve_ledger(desktop, phone, C, PHONE, found[path].token, handles(), owners={DESKTOP: {OWNER}})
    assert (desktop.root / path).read_bytes() == original
    assert sync(desktop, phone, handles()).conflicts == ()


def test_resolve_ledger_cannot_keep_the_device_that_removed_a_ledger_file(pair):
    _synced_with_an_event(pair)
    desktop, phone = pair[DESKTOP].store, pair[PHONE].store
    path = _relation_to(desktop, KEY2)
    (desktop.root / path).unlink()
    token = {conflict.token for conflict in sync(desktop, phone, handles()).conflicts}.pop()
    with pytest.raises(ResolveRefused, match="revoke"):  # 021 and 023 have no delete: nothing is removed
        resolve_ledger(desktop, phone, C, DESKTOP, token, handles(), owners={PHONE: {OWNER}})
    assert (phone.root / path).exists()


def test_a_relation_deleted_before_its_campaign_had_events_propagates_and_its_tombstone_stays_quiet(pair):
    desktop, phone = pair[DESKTOP].store, pair[PHONE].store
    path = _relation_to(desktop, KEY2)  # the campaign has no accepted event yet: an ordinary record
    original = (desktop.root / path).read_bytes()
    (desktop.root / path).unlink()
    report = sync(desktop, phone, handles())
    assert report.conflicts == () and (path, PHONE) in report.deleted
    tombstone = _archive(phone, DESKTOP)[path]
    assert "deleted" in tombstone
    pair[PHONE].accept(give())  # now the campaign has a ledger unit, its relations in it
    report = sync_until_quiet(desktop, phone, handles())
    assert report.conflicts == (), report.conflicts
    assert _relations(desktop) == _relations(phone) and path not in _relations(phone)
    assert len(read_ledger(desktop).events) == 1
    for store, peer in ((desktop, PHONE), (phone, DESKTOP)):
        assert _archive(store, peer)[path] == tombstone  # the unit leaves the tombstone as it is
    (phone.root / path).write_bytes(original)  # written again at the tombstoned path: never a silent copy
    report = sync(desktop, phone, handles())
    found = {conflict.path: conflict for conflict in report.conflicts}
    assert found[path].code == "merge_review" and "no_base" in {item.reason for item in found[path].review}
    assert not (desktop.root / path).exists() and not report.wrote


def test_a_record_a_holder_file_names_is_never_deleted_from_under_it(pair):
    _synced_with_an_event(pair)
    desktop, phone = pair[DESKTOP].store, pair[PHONE].store
    path = f"campaigns/{C}/instances/character/tam.json"
    original = (phone.root / path).read_bytes()
    (desktop.root / path).unlink()  # Tam's holder file (and the event that gave Tam the key) still name Tam
    report = sync(desktop, phone, handles())
    assert report.deleted == ()
    assert {(conflict.path, conflict.code) for conflict in report.conflicts} >= {(path, "delete_dependency")}
    assert (phone.root / path).read_bytes() == original
    assert (phone.root / f"campaigns/{C}/holdings/character/tam.json").exists()


class _PowerLoss(Exception):
    pass


def test_a_relation_deletion_stopped_before_its_archives_is_no_ledger_deletion_once_the_campaign_plays(pair):
    desktop, phone = pair[DESKTOP].store, pair[PHONE].store
    path = _relation_to(desktop, KEY2)  # no accepted event yet: an ordinary record, its deletion propagates
    (desktop.root / path).unlink()

    def stop(step):  # type: ignore[no-untyped-def]
        if step.kind == "data":
            raise _PowerLoss()
    with pytest.raises(_PowerLoss):
        apply_sync(plan_sync(desktop, phone, handles()), handles(), observer=stop)
    assert not (phone.root / path).exists()  # the deletion landed on the phone; no archive records it yet
    pair[PHONE].accept(give())  # the campaign's first event: its relations join the unit
    report = sync(desktop, phone, handles())
    assert report.conflicts == (), report.conflicts  # absent on both, its removal marker landed: deleted
    for store, peer in ((desktop, PHONE), (phone, DESKTOP)):
        assert "deleted" in _archive(store, peer)[path]
    assert sync(desktop, phone, handles()).conflicts == ()


# -- resolve_ledger writes only its campaign's ledger -------------------------------------------------

def _review(pair) -> str:  # type: ignore[no-untyped-def]
    pair[DESKTOP].accept(pay(sequence=8, attempt="pay-desk"))
    pair[PHONE].accept(pay(sequence=9, attempt="pay-phone"))
    report = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    return {conflict.token for conflict in report.conflicts if conflict.code == "merge_review"}.pop()


def test_resolve_ledger_never_carries_out_a_deletion_outside_its_unit(pair):
    desktop, phone = pair[DESKTOP].store, pair[PHONE].store
    token = _review(pair)
    chat = f"campaigns/{C}/chats/t-3.json"
    assert (phone.root / chat).exists()
    (desktop.root / chat).unlink()  # after the review, the owner removes an unrelated chat on the desktop
    steps: list[tuple[str, str, tuple[str, ...]]] = []
    plan = plan_resolution(desktop, phone, C, MERGE, token, handles())
    apply_sync(plan, handles(), observer=lambda step: steps.append(
        (step.kind, step.device, tuple(gone.path for gone in step.deletes))))
    assert (phone.root / chat).exists()  # that deletion waits for the next sync
    assert all(not gones for _, _, gones in steps) and "holds" not in {kind for kind, _, _ in steps}
    assert ledger_files(desktop) == ledger_files(phone)
    report = sync(desktop, phone, handles())  # the next sync carries the deletion as any sync does
    assert (chat, PHONE) in report.deleted and report.conflicts == ()


ZED = f"campaigns/{C}/instances/character/zed.json"
CHAT3 = f"campaigns/{C}/chats/t-3.json"


def _write(root: Path, path: str, value: dict) -> None:  # type: ignore[type-arg]
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def test_resolve_ledger_is_not_stopped_by_a_deletion_that_depends_on_a_copy_it_leaves_out(tmp_path):
    base = build_base(tmp_path)
    _write(base, ZED, {"address": f"campaign:{C}/character/zed", "format": "grimoire.record/1", "owner": OWNER,
                       "payload": {"local_name": "Zed"}, "previous": None, "schema": "grimoire.entity/0"})
    chat = json.loads((base / CHAT3).read_text())
    chat["payload"]["launch"]["premise"] = f"campaign:{C}/character/zed"  # the chat names Zed
    _write(base, CHAT3, chat)
    made = {name: Device(base, tmp_path, name) for name in (DESKTOP, PHONE)}
    desktop, phone = made[DESKTOP].store, made[PHONE].store
    try:
        assert sync(desktop, phone, handles()).conflicts == ()
        token = _review(made)
        edited = json.loads((desktop.root / CHAT3).read_text())  # the owner takes Zed out of the chat ...
        edited["payload"]["launch"]["premise"] = "The lighthouse at dusk."
        edited["previous"] = None
        _write(desktop.root, CHAT3, edited)
        (desktop.root / ZED).unlink()  # ... and deletes Zed: a sync takes both, since the edited chat lands too
        resolve_ledger(desktop, phone, C, MERGE, token, handles())
        assert ledger_files(desktop) == ledger_files(phone)
        assert (phone.root / ZED).exists() and (phone.root / CHAT3).read_bytes() != (desktop.root / CHAT3).read_bytes()
        report = sync(desktop, phone, handles())
        assert (ZED, PHONE) in report.deleted and report.conflicts == ()
    finally:
        for device in made.values():
            device.close()


def test_resolve_ledger_refuses_until_a_record_the_merged_ledger_names_is_on_the_receiver(pair):
    """The merge pays Zed, who is only on the desktop: resolving writes only the ledger, so it must not land
    Zed's holder file and event on the phone without Zed. It is refused until a sync brings Zed over."""
    from grimoire.core.identity import InstanceRef
    from grimoire.sync import ResolveRefused
    from store_helpers import owner_handle
    desktop, phone = pair[DESKTOP].store, pair[PHONE].store
    _write(desktop.root, ZED, {"address": f"campaign:{C}/character/zed", "format": "grimoire.record/1", "owner": OWNER,
                               "payload": {"local_name": "Zed"}, "previous": None, "schema": "grimoire.entity/0"})
    pair[DESKTOP].accept(pay(to=InstanceRef(C, "character", "zed"), sequence=8, attempt="pay-desk"))
    pair[PHONE].accept(pay(sequence=9, attempt="pay-phone"))  # one chat on both devices: a review
    report = sync(desktop, phone, {DESKTOP: handles()[DESKTOP], PHONE: owner_handle("sources", "kept")})
    assert report.conflict(ZED).code == "denied" and not (phone.root / ZED).exists()
    token = {conflict.token for conflict in report.conflicts if conflict.code == "merge_review"}.pop()
    zed_holdings = f"campaigns/{C}/holdings/character/zed.json"
    before = ledger_files(phone)
    with pytest.raises(ResolveRefused, match="zed"):  # the handles now suffice, but Zed is not on the phone
        resolve_ledger(desktop, phone, C, MERGE, token, handles())
    assert ledger_files(phone) == before and not (phone.root / zed_holdings).exists()
    after = sync(desktop, phone, handles())  # Zed lands; the review still stands, on the same files
    assert (phone.root / ZED).exists() and {conflict.code for conflict in after.conflicts} == {"merge_review"}
    resolve_ledger(desktop, phone, C, MERGE, token, handles())
    assert ledger_files(desktop) == ledger_files(phone) and (phone.root / zed_holdings).exists()
    assert sync(desktop, phone, handles()).conflicts == ()
