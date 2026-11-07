"""028: a ledger merge that stopped part way, and the bookkeeping around it, never strands a library.

A merge's bytes are held by neither device before they land, so a sync stopped between its data commits
must merge again from the agreement both devices still share; the base cache must fit wherever the ledger
does; and one unreadable file must stop only what it belongs to.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from grimoire.holdings import Line, Operation
from grimoire.store import LibraryStore
from grimoire.store.guard import path_length
from grimoire.sync import apply_sync, identify, plan_sync, sync, sync_until_quiet
from grimoire.sync.ledger_resolve import plan_resolution

from effects_fixtures import C, CHAT, TAM, WREN
from ledger_fixtures import COIN, OTHER, Device, build_base, pay, read_ledger
from sync_helpers import DESKTOP, PHONE, handles
from test_sync_ledger import ledger_files


class _PowerLoss(Exception):
    pass


def _devices(root: Path) -> dict[str, Device]:
    root.mkdir()
    base = build_base(root)
    made = {name: Device(base, root, name) for name in (DESKTOP, PHONE)}
    assert sync(made[DESKTOP].store, made[PHONE].store, handles()).conflicts == ()
    return made


def _both_pay_ines(made: dict[str, Device]) -> None:
    """Each device pays Ines, who held nothing when they last agreed: both create her holder file anew."""
    made[DESKTOP].accept(pay(sequence=8, chat=CHAT, attempt="pay-desk"))
    made[PHONE].accept(pay(sequence=9, chat=OTHER, attempt="pay-phone"))


def _stop_after(stop: int):  # type: ignore[no-untyped-def]
    done: list[str] = []

    def observer(step) -> None:  # type: ignore[no-untyped-def]
        done.append(f"{step.kind}:{step.device}")
        if len(done) > stop:
            raise _PowerLoss(done[-1])
    return observer, done


def _close(made: dict[str, Device]) -> None:
    for device in made.values():
        device.close()


@pytest.mark.parametrize("first", [DESKTOP, PHONE])
def test_a_sync_stopped_after_its_first_data_commit_merges_again_to_the_same_ledger(tmp_path, first):
    reference = _devices(tmp_path / "reference")
    try:
        _both_pay_ines(reference)
        assert sync_until_quiet(reference[DESKTOP].store, reference[PHONE].store, handles()).conflicts == ()
        expected = ledger_files(reference[DESKTOP].store)
    finally:
        _close(reference)
    made = _devices(tmp_path / "stopped")
    try:
        _both_pay_ines(made)
        other = PHONE if first == DESKTOP else DESKTOP
        observer, done = _stop_after(0)
        with pytest.raises(_PowerLoss):
            apply_sync(plan_sync(made[first].store, made[other].store, handles()), handles(), observer=observer)
        assert len(done) == 1 and done[0].startswith("data:")  # only one device took the merge
        report = sync_until_quiet(made[first].store, made[other].store, handles())
        assert report.conflicts == (), [(item.reason, item.detail) for c in report.conflicts for item in c.review]
        assert ledger_files(made[DESKTOP].store) == ledger_files(made[PHONE].store) == expected
    finally:
        _close(made)


@pytest.mark.parametrize("stop", [0, 1])  # after one device's data commit, or after both
def test_a_device_that_plays_on_after_the_stop_still_merges_with_no_decision_needed(tmp_path, stop):
    made = _devices(tmp_path / "stopped")
    try:
        _both_pay_ines(made)
        observer, _ = _stop_after(stop)
        with pytest.raises(_PowerLoss):
            apply_sync(plan_sync(made[DESKTOP].store, made[PHONE].store, handles()), handles(), observer=observer)
        made[PHONE].accept(pay(sequence=10, chat=OTHER, attempt="pay-phone-10"))  # the phone plays on
        report = sync_until_quiet(made[DESKTOP].store, made[PHONE].store, handles())
        assert report.conflicts == (), [(item.reason, item.detail) for c in report.conflicts for item in c.review]
        assert ledger_files(made[DESKTOP].store) == ledger_files(made[PHONE].store)
        assert len(read_ledger(made[DESKTOP].store).events) == 3
    finally:
        _close(made)


@pytest.mark.parametrize("stop", range(8))
def test_a_merge_decision_stopped_after_any_commit_needs_no_second_decision(tmp_path, stop):
    made = _devices(tmp_path / "decided")
    try:
        made[DESKTOP].accept(pay(sequence=8, attempt="pay-desk"))  # one chat on both devices: a review
        made[PHONE].accept(pay(sequence=9, attempt="pay-phone"))
        tokens = {conflict.token for conflict in sync(made[DESKTOP].store, made[PHONE].store, handles()).conflicts}
        assert len(tokens) == 1
        plan = plan_resolution(made[DESKTOP].store, made[PHONE].store, C, "merge", tokens.pop(), handles())
        observer, done = _stop_after(stop)
        try:
            apply_sync(plan, handles(), observer=observer)
        except _PowerLoss:
            pass
        report = sync_until_quiet(made[PHONE].store, made[DESKTOP].store, handles())
        assert report.conflicts == (), (done, [(c.path, c.code) for c in report.conflicts])
        assert ledger_files(made[DESKTOP].store) == ledger_files(made[PHONE].store)
        assert len(read_ledger(made[DESKTOP].store).events) == 2
    finally:
        _close(made)


def _longest_library_path(roots) -> int:  # type: ignore[no-untyped-def]
    return max(path_length(path) for root in roots for path in root.rglob("*")
               if ".sync" not in path.relative_to(root).parts and ".txn" not in path.relative_to(root).parts)


def test_a_library_that_fits_its_path_limit_syncs_however_long_its_base_cache_names_are(tmp_path):
    base = build_base(tmp_path)
    roots = {name: tmp_path / name for name in (DESKTOP, PHONE)}
    for root in roots.values():
        shutil.copytree(base, root)
    limit = _longest_library_path(roots.values()) + 12  # every library file fits, with room to spare
    stores = {name: LibraryStore.open(root, limits=lambda directory: (limit, 255)) for name, root in roots.items()}
    try:
        for name, store in stores.items():
            identify(store, name)
        report = sync(stores[DESKTOP], stores[PHONE], handles())
        assert report.conflicts == (), [(c.path, c.code, c.detail) for c in report.conflicts]
        assert not sync(stores[DESKTOP], stores[PHONE], handles()).wrote
        assert ledger_files(stores[DESKTOP]) == ledger_files(stores[PHONE])
    finally:
        for store in stores.values():
            store.close()


def test_base_cache_files_in_an_older_layout_are_never_read(tmp_path):
    made = _devices(tmp_path / "older")
    try:
        made[DESKTOP].accept(pay(sequence=8, chat=CHAT, attempt="pay-desk"))
        made[PHONE].accept(pay(sequence=9, chat=OTHER, attempt="pay-phone"))
        assert sync_until_quiet(made[DESKTOP].store, made[PHONE].store, handles()).conflicts == ()
        tam = f"campaigns/{C}/holdings/character/tam.json"
        for name, peer in ((DESKTOP, PHONE), (PHONE, DESKTOP)):  # a cache named by its ledger path, wrong bytes
            planted = made[name].store.root / ".sync" / "peers" / peer / "bases" / Path(tam)
            planted.parent.mkdir(parents=True, exist_ok=True)
            planted.write_bytes(b'{"planted": true}\n')
        made[DESKTOP].accept(pay(sequence=12, chat=CHAT, attempt="pay-desk-12"))
        made[PHONE].accept(pay(sequence=13, chat=OTHER, attempt="pay-phone-13"))
        report = sync_until_quiet(made[PHONE].store, made[DESKTOP].store, handles())
        assert report.conflicts == (), [(c.path, c.code) for c in report.conflicts]
        assert ledger_files(made[DESKTOP].store) == ledger_files(made[PHONE].store)
        assert len(read_ledger(made[DESKTOP].store).events) == 4
    finally:
        _close(made)


def _corrupt_a_relation(device: Device) -> str:
    relations = sorted((device.store.root / "campaigns" / C / "relations").rglob("*.json"))
    assert relations
    relations[0].write_bytes(b"{not json")
    return relations[0].relative_to(device.store.root).as_posix()


def test_one_unreadable_relation_outside_any_ledger_unit_is_one_blocked_conflict(tmp_path):
    made = _devices(tmp_path / "unreadable")
    try:
        path = _corrupt_a_relation(made[PHONE])  # the campaign holds no accepted event: relations sync alone
        made[DESKTOP].hand_op(Operation("hand-1", (Line.move(TAM, WREN, COIN, 1),)))  # a holdings-only change
        report = sync_until_quiet(made[DESKTOP].store, made[PHONE].store, handles())
        assert {(conflict.path, conflict.code) for conflict in report.conflicts} == {(path, "blocked")}
        holdings = {name: data for name, data in ledger_files(made[DESKTOP].store).items() if "/holdings/" in name}
        assert holdings == {name: data for name, data in ledger_files(made[PHONE].store).items() if "/holdings/" in name}
    finally:
        _close(made)


def test_one_unreadable_relation_inside_a_ledger_unit_stops_only_that_merge_for_review(tmp_path):
    made = _devices(tmp_path / "unreadable")
    try:
        path = _corrupt_a_relation(made[PHONE])
        made[DESKTOP].accept(pay(sequence=8, chat=CHAT, attempt="pay-desk"))  # now the relations are the ledger's
        report = sync_until_quiet(made[DESKTOP].store, made[PHONE].store, handles())
        reviews = {(item.reason, item.subject) for conflict in report.conflicts for item in conflict.review}
        assert ("unreadable", path) in reviews, reviews
        assert {conflict.code for conflict in report.conflicts} <= {"merge_review", "blocked"}
    finally:
        _close(made)
