"""028: a ledger merge that stopped part way, and the bookkeeping around it, never strands a library.

A merge's bytes are held by neither device before they land, so a sync stopped between its data commits
must merge again from the agreement both devices still share; the base cache must fit wherever the ledger
does; and one unreadable file must stop only what it belongs to.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from grimoire.holdings import Line, Operation
from grimoire.store import LibraryStore
from grimoire.store.guard import path_length
from grimoire.sync import MERGE, ResolveRefused, apply_sync, identify, plan_sync, resolve_ledger, sync, sync_until_quiet
from grimoire.sync.ledger_resolve import plan_resolution

from effects_fixtures import C, CHAT, TAM, WREN, give
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
        plan = plan_resolution(made[DESKTOP].store, made[PHONE].store, C, MERGE, tokens.pop(), handles())
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


def _reasons(report) -> list[str]:  # type: ignore[no-untyped-def]
    return sorted({item.reason for conflict in report.conflicts for item in conflict.review})


@pytest.mark.parametrize("stop", range(7))
def test_a_review_after_a_stop_at_any_commit_offers_a_merge_the_owner_can_take(tmp_path, stop):
    """Both devices play on in one chat after a merge stopped at any boundary: the review is a policy one
    (``same_chat``), it stays that review at every later sync, and ``resolve_ledger`` takes its merge. A
    file's own base may be uncertain there while its unit's is not; the bookkeeping must follow the unit."""
    made = _devices(tmp_path / "x")
    desktop, phone = made[DESKTOP].store, made[PHONE].store
    try:
        made[DESKTOP].accept(give())
        made[PHONE].accept(pay(sequence=8, chat=OTHER, attempt="pay-phone-8"))  # a clean merge, copies both ways
        observer, done = _stop_after(stop)
        try:
            apply_sync(plan_sync(desktop, phone, handles()), handles(), observer=observer)
        except _PowerLoss:
            pass
        made[DESKTOP].accept(pay(sequence=9, chat=OTHER, attempt="pay-desk-9"))
        made[PHONE].accept(pay(sequence=10, chat=OTHER, attempt="pay-phone-10"))
        first = sync(desktop, phone, handles())
        assert _reasons(first) == ["same_chat"], (done, first.conflicts)
        again = sync(desktop, phone, handles())
        assert _reasons(again) == ["same_chat"] and not again.wrote, done
        token = {conflict.token for conflict in again.conflicts if conflict.code == "merge_review"}.pop()
        resolve_ledger(desktop, phone, C, MERGE, token, handles())
        assert ledger_files(desktop) == ledger_files(phone)
        assert sync(desktop, phone, handles()).conflicts == ()
    finally:
        _close(made)


def test_a_review_sync_records_no_uncertainty_for_a_ledger_file_whose_unit_base_is_known(tmp_path):
    made = _devices(tmp_path / "x")
    desktop, phone = made[DESKTOP].store, made[PHONE].store
    try:
        made[PHONE].accept(pay(sequence=8, chat=OTHER, attempt="pay-phone-8"))  # only the phone plays
        observer, done = _stop_after(0)
        with pytest.raises(_PowerLoss):
            apply_sync(plan_sync(desktop, phone, handles()), handles(), observer=observer)
        assert done == [f"data:{DESKTOP}"]  # the desktop took the phone's ledger; nothing else landed
        made[DESKTOP].accept(pay(sequence=9, chat=OTHER, attempt="pay-desk-9"))
        made[PHONE].accept(pay(sequence=10, chat=OTHER, attempt="pay-phone-10"))
        assert _reasons(sync(desktop, phone, handles())) == ["same_chat"]
        for store, peer in ((desktop, PHONE), (phone, DESKTOP)):
            raw = store.read_sync_state(f"peers/{peer}/pending.json")
            entries = json.loads(raw)["entries"] if raw is not None else {}
            assert not any(entry.get("uncertain") for path, entry in entries.items() if f"campaigns/{C}/" in path)
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


@pytest.mark.parametrize("how", ["symlink", "busy"])
def test_an_unlistable_ledger_folder_is_a_review_even_when_every_file_seen_matches(tmp_path, monkeypatch, how):
    """What a folder that cannot be listed holds is unknown, so the unit is never read as agreed: here the
    desktop's ``wrapups`` (a directory link, or held by another program) may hold a wrap-up the phone lacks."""
    from grimoire.store import engine
    made = _devices(tmp_path / "unlistable")
    try:
        made[DESKTOP].accept(give())
        assert sync_until_quiet(made[DESKTOP].store, made[PHONE].store, handles()).conflicts == ()
        folder = made[DESKTOP].store.root / "campaigns" / C / "wrapups"
        assert not folder.exists()
        if how == "symlink":
            (tmp_path / "elsewhere").mkdir()
            try:
                folder.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
            except (OSError, NotImplementedError):
                pytest.skip("this system cannot make a directory link")
        else:
            folder.mkdir()
            real = engine.Access.list_dir

            def busy(self, parts):  # type: ignore[no-untyped-def]
                if tuple(parts) == ("campaigns", C, "wrapups") and self.root == made[DESKTOP].store.root:
                    raise PermissionError(13, "held by another program")
                return real(self, parts)
            monkeypatch.setattr(engine.Access, "list_dir", busy)
        report = sync(made[DESKTOP].store, made[PHONE].store, handles())
        reviews = {(item.reason, item.subject) for conflict in report.conflicts for item in conflict.review}
        assert ("unlistable", f"campaigns/{C}/wrapups") in reviews, report.conflicts
        assert {conflict.code for conflict in report.conflicts} == {"merge_review"}
        assert not report.copied
        if how == "symlink":
            folder.unlink()
        monkeypatch.undo()
        assert sync_until_quiet(made[DESKTOP].store, made[PHONE].store, handles()).conflicts == ()  # listable again
    finally:
        _close(made)


def test_an_agreed_relation_of_an_unknown_predicate_never_stops_its_ledger_and_is_kept_byte_for_byte(tmp_path):
    """A relation a newer version wrote (a predicate this one does not know) decodes as unsupported: the
    merge keeps it as it is (docs/LEDGER_MERGE.md), so it is no reason to review the campaign's ledger."""
    from grimoire.core.identity import RelationRef
    from grimoire.store import ScopeRecord
    from grimoire.store.envelope import scope_record_document
    from grimoire.store.jsonfmt import render
    from effects_fixtures import INES
    from store_helpers import OWNER
    root = tmp_path / "future"
    root.mkdir()
    base = build_base(root)
    record = ScopeRecord(RelationRef(C, "r-future-1"), "grimoire.relation/0",
                         {"predicate": "mentors", "subject": str(TAM), "object": str(INES), "provenance": "director",
                          "since": {"sequence": 1}}, owner=OWNER)
    path = f"campaigns/{C}/relations/r-future-1.json"
    (base / path).write_bytes(render(scope_record_document(record, None)))
    original = (base / path).read_bytes()
    made = {name: Device(base, root, name) for name in (DESKTOP, PHONE)}
    try:
        assert sync(made[DESKTOP].store, made[PHONE].store, handles()).conflicts == ()
        made[DESKTOP].accept(pay(sequence=8, chat=CHAT, attempt="pay-desk"))  # the relations join the unit
        report = sync_until_quiet(made[DESKTOP].store, made[PHONE].store, handles())
        assert report.conflicts == (), [(item.reason, item.subject) for c in report.conflicts for item in c.review]
        assert ledger_files(made[DESKTOP].store) == ledger_files(made[PHONE].store)
        assert len(read_ledger(made[PHONE].store).events) == 1
        assert (made[PHONE].store.root / path).read_bytes() == original == (made[DESKTOP].store.root / path).read_bytes()
    finally:
        _close(made)


@pytest.mark.parametrize("folder", ["accepted", "holdings", "wrapups", "Accepted", "campaign"])
def test_a_blocked_ledger_folder_is_a_review_though_the_campaign_has_no_ledger_file(tmp_path, folder):
    """Two libraries whose campaign holds no ledger file anywhere, and neither archive names one: a ledger
    folder (or the campaign folder) the desktop cannot list, or a look-alike spelling of one, may hold any
    ledger at all, so it is a folder-level ``unlistable`` review, never silence."""
    from sync_helpers import make_pair
    desktop, phone = make_pair(tmp_path / "pair")
    try:
        campaign = desktop.root / "campaigns" / "c1"
        assert not any((campaign / name).exists() for name in ("accepted", "holdings", "wrapups"))
        (tmp_path / "elsewhere").mkdir()
        if folder == "Accepted":  # a look-alike spelling: what it holds is never read
            (campaign / "Accepted").mkdir()
            if (campaign / "accepted").exists():
                pytest.skip("this file system does not tell the spellings apart")
            subject = "campaigns/c1/Accepted"
        else:
            if folder == "campaign":
                shutil.move(str(campaign), str(tmp_path / "elsewhere" / "c1"))
                link, subject = campaign, "campaigns/c1"
            else:
                link, subject = campaign / folder, f"campaigns/c1/{folder}"
            try:
                link.symlink_to(tmp_path / "elsewhere" / ("c1" if folder == "campaign" else ""), target_is_directory=True)
            except (OSError, NotImplementedError):
                pytest.skip("this system cannot make a directory link")
        report = sync(desktop, phone, handles())
        found = report.conflict(subject)
        assert found is not None and found.code == "merge_review", report.conflicts
        assert ("unlistable", subject) in {(item.reason, item.subject) for item in found.review}
        assert not sync(desktop, phone, handles()).wrote  # the review stands, and writes nothing again
        with pytest.raises(ResolveRefused):
            resolve_ledger(desktop, phone, "c1", MERGE, found.token, handles())
        if folder == "Accepted":  # listable again: the review ends by itself
            (campaign / "Accepted").rmdir()
        else:
            link.unlink()
            if folder == "campaign":
                shutil.move(str(tmp_path / "elsewhere" / "c1"), str(campaign))
        assert sync_until_quiet(desktop, phone, handles()).conflicts == ()
    finally:
        desktop.close()
        phone.close()
