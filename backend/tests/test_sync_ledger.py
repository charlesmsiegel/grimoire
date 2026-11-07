"""028: a campaign's ledger syncs as one unit, merged (``grimoire.sync.units``), never file by file.

Two devices copy one agreed library, sync once, play offline and sync again. A clean merge leaves both
devices with the same ledger bytes whichever device starts the sync; a merge that stops is a
``merge_review`` conflict on the files the devices hold differently, and nothing of that ledger moves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.store import LibraryStore, etag
from grimoire.sync import (
    MERGE, ResolveRefused, SyncInterrupted, apply_sync, plan_sync, resolve, resolve_ledger, sync, sync_until_quiet,
)
from grimoire.sync.units import base_name

from effects_fixtures import C, CHAT, TAM, give
from ledger_fixtures import COIN, Device, build_base, pay, read_ledger
from store_helpers import OWNER
from sync_helpers import DESKTOP, PHONE, handles


def ledger_files(store: LibraryStore) -> dict[str, bytes]:
    found = {}
    for kind in ("accepted", "wrapups", "relations", "holdings"):
        root = store.root / "campaigns" / C / kind
        for path in sorted(root.rglob("*.json")) if root.exists() else ():
            found[path.relative_to(store.root).as_posix()] = path.read_bytes()
    return found


@pytest.fixture
def pair(tmp_path: Path):
    base = build_base(tmp_path)
    made = {name: Device(base, tmp_path, name) for name in (DESKTOP, PHONE)}
    first = sync(made[DESKTOP].store, made[PHONE].store, handles())
    assert first.conflicts == ()
    yield made
    for device in made.values():
        device.close()


def test_an_event_accepted_on_one_device_arrives_with_its_relations_and_holdings(pair):
    pair[DESKTOP].accept(give())
    report = sync_until_quiet(pair[DESKTOP].store, pair[PHONE].store, handles())
    assert report.conflicts == ()
    assert ledger_files(pair[DESKTOP].store) == ledger_files(pair[PHONE].store)
    assert len(read_ledger(pair[PHONE].store).events) == 1


@pytest.mark.parametrize("first", [DESKTOP, PHONE])
def test_events_accepted_on_both_devices_merge_to_one_ledger_whichever_starts(pair, first):
    pair[DESKTOP].accept(give())
    pair[PHONE].accept(pay())
    other = PHONE if first == DESKTOP else DESKTOP
    report = sync_until_quiet(pair[first].store, pair[other].store, handles())
    assert report.conflicts == (), report.conflicts
    files = ledger_files(pair[DESKTOP].store)
    assert files == ledger_files(pair[PHONE].store)
    assert len(read_ledger(pair[DESKTOP].store).events) == 2
    again = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    assert again.conflicts == () and not again.wrote


def test_a_holder_file_both_devices_change_again_merges_from_the_base_cache(pair):
    pair[DESKTOP].accept(give())
    pair[PHONE].accept(pay())
    sync_until_quiet(pair[DESKTOP].store, pair[PHONE].store, handles())
    cached = pair[PHONE].store.read_sync_state(base_name(DESKTOP, f"campaigns/{C}/holdings/character/tam.json"))
    assert cached is not None and cached == (pair[PHONE].store.root / f"campaigns/{C}/holdings/character/tam.json").read_bytes()
    pair[DESKTOP].accept(pay(sequence=12, chat=CHAT, attempt="pay-desk-12"))
    pair[PHONE].accept(pay(sequence=13, attempt="pay-phone-13"))  # both change Tam's and Ines's holder files
    report = sync_until_quiet(pair[PHONE].store, pair[DESKTOP].store, handles())
    assert report.conflicts == (), report.conflicts
    assert ledger_files(pair[DESKTOP].store) == ledger_files(pair[PHONE].store)
    assert read_ledger(pair[DESKTOP].store).holdings[TAM].value.count(COIN) == 4


def test_two_events_of_one_chat_are_a_merge_review_and_nothing_of_the_ledger_moves(pair):
    pair[DESKTOP].accept(pay(sequence=8, attempt="pay-desk"))
    pair[PHONE].accept(pay(sequence=9, attempt="pay-phone"))
    before = {name: ledger_files(device.store) for name, device in pair.items()}
    report = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    codes = {conflict.code for conflict in report.conflicts}
    assert codes == {"merge_review"}, report.conflicts
    assert all("same_chat" in {item.reason for item in conflict.review} for conflict in report.conflicts)
    assert {name: ledger_files(device.store) for name, device in pair.items()} == before
    assert pair[DESKTOP].store.conflicts() == () and pair[PHONE].store.conflicts() == ()  # nothing kept


def _review(pair):
    pair[DESKTOP].accept(pay(sequence=8, attempt="pay-desk"))
    pair[PHONE].accept(pay(sequence=9, attempt="pay-phone"))
    report = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    tokens = {conflict.token for conflict in report.conflicts}
    assert len(tokens) == 1
    return tokens.pop()


def test_resolve_ledger_merge_keeps_both_devices_events_and_ends_the_review(pair):
    token = _review(pair)
    resolve_ledger(pair[PHONE].store, pair[DESKTOP].store, C, MERGE, token, handles())
    assert ledger_files(pair[DESKTOP].store) == ledger_files(pair[PHONE].store)
    assert len(read_ledger(pair[DESKTOP].store).events) == 2
    again = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    assert again.conflicts == () and not again.wrote


def test_resolve_ledger_keeping_a_device_discards_only_for_an_owner(pair):
    pair[DESKTOP].accept(pay(sequence=8, attempt="pay-desk"))
    pair[PHONE].hand_edit(TAM, COIN, 12)  # no file only the phone holds: the desktop's ledger can be kept
    report = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    token = {conflict.token for conflict in report.conflicts}.pop() if report.conflicts else None
    if token is None:  # the merge took both: nothing to resolve
        pytest.skip("merged without review")
    with pytest.raises(ResolveRefused, match="only an owner"):
        resolve_ledger(pair[DESKTOP].store, pair[PHONE].store, C, DESKTOP, token, handles())
    resolve_ledger(pair[DESKTOP].store, pair[PHONE].store, C, DESKTOP, token, handles(), owners={PHONE: {OWNER}})
    assert ledger_files(pair[DESKTOP].store) == ledger_files(pair[PHONE].store)


def test_an_ordinary_sync_plan_never_takes_a_ledger_decision(pair):
    """Keeping one device's ledger discards the other's changes: only ``resolve_ledger``, with its owner
    and review-token checks, may decide it. ``plan_sync`` takes no decision at all."""
    pair[DESKTOP].accept(pay(sequence=8, attempt="pay-desk"))
    pair[PHONE].hand_edit(TAM, COIN, 12)
    report = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    assert "hand_edit_overlap" in {item.reason for conflict in report.conflicts for item in conflict.review}
    tam = pair[PHONE].store.root / f"campaigns/{C}/holdings/character/tam.json"
    edited = tam.read_bytes()
    with pytest.raises(TypeError):
        apply_sync(plan_sync(pair[DESKTOP].store, pair[PHONE].store, handles(), choices={C: DESKTOP}), handles())
    assert tam.read_bytes() == edited  # the phone's edit is never discarded unchecked
    again = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    assert {conflict.code for conflict in again.conflicts} == {"merge_review"} and not again.wrote


def test_a_device_named_merge_is_kept_only_as_a_device_with_the_discard_checks(tmp_path):
    """``"merge"`` is a valid device id: the string always names a device, so keeping it is a discard an
    owner decides. Taking the review's merge is ``MERGE``, which no device id can equal."""
    from sync_helpers import handle
    base = build_base(tmp_path)
    made = {name: Device(base, tmp_path, name) for name in ("merge", PHONE)}
    merge_device, phone = made["merge"].store, made[PHONE].store
    both = {"merge": handle(), PHONE: handle()}
    try:
        assert sync(merge_device, phone, both).conflicts == ()
        made["merge"].accept(pay(sequence=8, attempt="pay-merge"))
        made[PHONE].hand_edit(TAM, COIN, 12)
        report = sync(merge_device, phone, both)
        assert "hand_edit_overlap" in {item.reason for conflict in report.conflicts for item in conflict.review}
        token = {conflict.token for conflict in report.conflicts}.pop()
        tam = phone.root / f"campaigns/{C}/holdings/character/tam.json"
        edited = tam.read_bytes()
        with pytest.raises(ResolveRefused, match="only an owner"):  # keeping device "merge" discards the edit
            resolve_ledger(merge_device, phone, C, "merge", token, both)
        assert tam.read_bytes() == edited
        with pytest.raises(ResolveRefused, match="hand_edit_overlap"):  # no merge for a non-policy review
            resolve_ledger(merge_device, phone, C, MERGE, token, both)
        assert tam.read_bytes() == edited
        resolve_ledger(merge_device, phone, C, "merge", token, both, owners={PHONE: {OWNER}})
        assert ledger_files(merge_device) == ledger_files(phone) and tam.read_bytes() != edited
    finally:
        for device in made.values():
            device.close()


def test_keep_is_the_merge_sentinel_or_a_device_id_and_nothing_else(pair):
    token = _review(pair)
    for keep in ("merge", None, 0):  # no device is named "merge" here: the string is no request to merge
        with pytest.raises(ResolveRefused, match="keep must be"):
            resolve_ledger(pair[DESKTOP].store, pair[PHONE].store, C, keep, token, handles())


def test_resolve_ledger_refuses_a_stale_review_and_a_file_only_the_losing_device_holds(pair):
    token = _review(pair)
    with pytest.raises(ResolveRefused, match="revoke"):
        resolve_ledger(pair[DESKTOP].store, pair[PHONE].store, C, DESKTOP, token, handles(), owners={PHONE: {OWNER}})
    pair[DESKTOP].accept(pay(sequence=10, attempt="pay-desk-10"))
    with pytest.raises(ResolveRefused, match="changed"):
        resolve_ledger(pair[DESKTOP].store, pair[PHONE].store, C, MERGE, token, handles())


def test_resolve_refuses_a_ledger_file_and_points_to_resolve_ledger(pair):
    _review(pair)
    path = f"campaigns/{C}/holdings/character/tam.json"
    with pytest.raises(ResolveRefused, match="resolve_ledger"):
        resolve(pair[DESKTOP].store, pair[PHONE].store, path, "sha256:" + "0" * 64, {}, handles())


class _PowerLoss(Exception):
    pass


def _played(root: Path):
    root.mkdir()
    base = build_base(root)
    made = {name: Device(base, root, name) for name in (DESKTOP, PHONE)}
    sync(made[DESKTOP].store, made[PHONE].store, handles())
    made[DESKTOP].accept(give())
    made[PHONE].accept(pay())
    sync_until_quiet(made[DESKTOP].store, made[PHONE].store, handles())  # a merge, so a base cache, too
    made[DESKTOP].accept(pay(sequence=12, chat=CHAT, attempt="pay-desk-12"))
    made[PHONE].accept(pay(sequence=13, attempt="pay-phone-13"))
    return made


def _steps(made) -> list[str]:
    seen = []
    plan = plan_sync(made[DESKTOP].store, made[PHONE].store, handles())
    apply_sync(plan, handles(), observer=lambda step: seen.append(f"{step.kind}:{step.device}"))
    return seen


def test_a_merge_stopped_after_any_commit_converges_to_the_same_ledger(tmp_path):
    reference = _played(tmp_path / "reference")
    try:
        steps = _steps(reference)
        assert steps[:2] == [f"data:{DESKTOP}", f"data:{PHONE}"] and any(step.startswith("archive") for step in steps)
        expected = ledger_files(reference[DESKTOP].store)
        assert expected == ledger_files(reference[PHONE].store)
    finally:
        for device in reference.values():
            device.close()
    for stop in range(len(steps)):
        made = _played(tmp_path / f"stop-{stop}")
        try:
            done = []

            def observer(step, done=done, stop=stop):
                done.append(step)
                if len(done) > stop:
                    raise _PowerLoss(f"power lost after {step.kind} on {step.device}")
            plan = plan_sync(made[DESKTOP].store, made[PHONE].store, handles())
            with pytest.raises(_PowerLoss):
                apply_sync(plan, handles(), observer=observer)
            report = sync_until_quiet(made[PHONE].store, made[DESKTOP].store, handles())
            assert report.conflicts == (), (stop, report.conflicts)
            assert ledger_files(made[DESKTOP].store) == ledger_files(made[PHONE].store) == expected, stop
            assert not sync(made[DESKTOP].store, made[PHONE].store, handles()).wrote
        finally:
            for device in made.values():
                device.close()


def test_a_ledger_changed_during_the_sync_stops_the_merge_before_it_commits(pair, monkeypatch):
    from grimoire.sync import units
    pair[DESKTOP].accept(give())
    pair[PHONE].accept(pay())
    plan = plan_sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    real = units.changed

    def accept_meanwhile(plan, copies, published):  # an event accepted on the phone after the plan read it
        if not published:
            pair[PHONE].accept(pay(sequence=20, attempt="pay-phone-20"))
        return real(plan, copies, published)
    monkeypatch.setattr(units, "changed", accept_meanwhile)
    with pytest.raises(SyncInterrupted, match="changed on phone during the sync"):
        apply_sync(plan, handles())
    monkeypatch.undo()
    report = sync_until_quiet(pair[DESKTOP].store, pair[PHONE].store, handles())
    assert report.conflicts == () and ledger_files(pair[DESKTOP].store) == ledger_files(pair[PHONE].store)
    assert len(read_ledger(pair[DESKTOP].store).events) == 3


@pytest.mark.parametrize("order", ["same", "reversed"])
def test_archives_left_apart_by_a_crash_still_merge_later_changes_either_way(tmp_path, order):
    def later(made):  # each device accepts again after the sync that agreed on the merge
        made[DESKTOP].accept(pay(sequence=30, chat=CHAT, attempt="pay-desk-30"))
        made[PHONE].accept(pay(sequence=31, attempt="pay-phone-31"))

    reference = _played(tmp_path / "reference")
    try:
        sync_until_quiet(reference[DESKTOP].store, reference[PHONE].store, handles())
        later(reference)
        assert sync_until_quiet(reference[DESKTOP].store, reference[PHONE].store, handles()).conflicts == ()
        expected = ledger_files(reference[DESKTOP].store)
    finally:
        for device in reference.values():
            device.close()
    made = _played(tmp_path / "crashed")
    try:
        done = []

        def observer(step):
            done.append(step)
            if step.kind == "archive":  # one archive holds the new agreement, the other the old one
                raise _PowerLoss(f"power lost after archive on {step.device}")
        plan = plan_sync(made[DESKTOP].store, made[PHONE].store, handles())
        with pytest.raises(_PowerLoss):
            apply_sync(plan, handles(), observer=observer)
        later(made)
        pair = (made[DESKTOP].store, made[PHONE].store)
        report = sync_until_quiet(*(pair if order == "same" else pair[::-1]), handles())
        assert report.conflicts == (), report.conflicts
        assert ledger_files(made[DESKTOP].store) == ledger_files(made[PHONE].store) == expected
        assert any(step.kind == "archive" for step in done)
    finally:
        for device in made.values():
            device.close()


def test_a_ledger_review_and_a_keep_both_conflict_in_one_sync_end_each_on_its_own(pair):
    from grimoire.chatstore import ChatLibrary
    from grimoire.core.identity import ChatRef
    from chat_fixtures import Clock, Ids
    from grimoire.sync.documents import keeps_name, parse_keeps
    from sync_helpers import kept
    lighthouse = ChatRef(C, "t-3")
    path = f"campaigns/{C}/chats/{lighthouse.chat_id}.json"
    for name, label in ((DESKTOP, "At the desk"), (PHONE, "On the road")):  # one chat renamed on both devices
        chats = ChatLibrary(pair[name].store, pair[name].handles.app, clock=Clock(), new_id=Ids(), owner=OWNER)
        chats.set_label(lighthouse, label, expected=chats.open_chat(lighthouse).etag).commit()
    pair[DESKTOP].accept(pay(sequence=8, attempt="pay-desk"))  # and both devices' events in one chat: a review
    pair[PHONE].accept(pay(sequence=9, attempt="pay-phone"))
    report = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    tokens = {conflict.token for conflict in report.conflicts if conflict.code == "merge_review"}
    assert len(tokens) == 1
    token = tokens.pop()
    assert report.conflict(path).code == "concurrent"
    ours, theirs = (pair[DESKTOP].store.root / path).read_bytes(), (pair[PHONE].store.root / path).read_bytes()
    assert kept(pair[DESKTOP].store) == [theirs] and kept(pair[PHONE].store) == [ours]
    resolve_ledger(pair[DESKTOP].store, pair[PHONE].store, C, MERGE, token, handles())  # only the ledger
    for name, peer in ((DESKTOP, PHONE), (PHONE, DESKTOP)):  # the chat's conflict still stands in keeps.json
        record = parse_keeps(pair[name].store.read_sync_state(keeps_name(peer)), tuple(sorted((DESKTOP, PHONE))))
        assert record.entries[path].open is not None
    after = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    assert {(conflict.path, conflict.code) for conflict in after.conflicts} == {(path, "concurrent")}
    assert kept(pair[DESKTOP].store) == [theirs] and kept(pair[PHONE].store) == [ours]  # never duplicated
    assert ledger_files(pair[DESKTOP].store) == ledger_files(pair[PHONE].store)
    resolve(pair[DESKTOP].store, pair[PHONE].store, path, etag(ours), {DESKTOP: etag(ours), PHONE: etag(theirs)},
            handles())
    assert sync_until_quiet(pair[DESKTOP].store, pair[PHONE].store, handles()).conflicts == ()


def test_a_review_names_its_unit_once_however_many_of_its_files_differ(pair, monkeypatch):
    from grimoire.sync import units
    pair[DESKTOP].accept(pay(sequence=8, attempt="pay-desk"))
    pair[PHONE].accept(pay(sequence=9, attempt="pay-phone"))
    calls: list[str] = []
    real = units.token

    def counted(plan, campaign, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(campaign)
        return real(plan, campaign, *args, **kwargs)
    monkeypatch.setattr(units, "token", counted)
    report = sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    assert len({conflict.path for conflict in report.conflicts if conflict.code == "merge_review"}) > 1
    assert calls == [C]  # each token lists every file of the unit: once per unit, never once per file


@pytest.mark.parametrize("where", ["wrapups", "holdings/location"])
def test_a_ledger_folder_linked_away_during_the_sync_stops_the_merge_before_it_commits(pair, tmp_path, where):
    """The recheck under the sync's locks lists the unit again: a ledger folder (or one below) that became a
    link meanwhile hides what it holds, so the unit is no longer the one merged, and nothing of it lands."""
    pair[DESKTOP].accept(give())
    pair[PHONE].accept(pay())
    plan = plan_sync(pair[DESKTOP].store, pair[PHONE].store, handles())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "dock.json").write_bytes(b"{}")
    link = pair[PHONE].store.root / "campaigns" / C / where
    try:
        link.symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make a directory link")
    before = ledger_files(pair[PHONE].store)
    with pytest.raises(SyncInterrupted, match="changed on phone during the sync"):
        apply_sync(plan, handles())
    assert ledger_files(pair[PHONE].store) == before


def test_a_merged_event_that_pins_a_revision_only_its_own_device_holds_lands_on_both(pair):
    # each device makes a definition of its own and accepts an event that pins it (an envelope field); the
    # merge renumbers both, so each device receives its own event back, from a peer that lacks the pin
    from grimoire.core.identity import SourceRef
    from grimoire.store import RecordEnvelope, SourceManifest
    from grimoire.store.jsonfmt import render
    from sync_helpers import handle
    accepted = {name: set((device.store.root / "campaigns" / C / "accepted").rglob("*.json"))
                for name, device in pair.items()}
    pair[DESKTOP].accept(pay(sequence=8, chat=CHAT, attempt="pay-desk"))
    pair[PHONE].accept(pay())
    for name, world in ((DESKTOP, "deskworld"), (PHONE, "phoneworld")):
        store = pair[name].store
        ref = SourceRef(f"world:{world}", "character", "wren")
        txn = store.transaction()
        txn.put(RecordEnvelope(ref.at("r1"), {"bio": "a sailor"}, name="Wren"), None)
        txn.put(SourceManifest(f"world:{world}", OWNER, world, {"character/wren": "r1"}, ()), None)
        txn.commit(handle())
        (event,) = set((store.root / "campaigns" / C / "accepted").rglob("*.json")) - accepted[name]
        document = json.loads(event.read_bytes())
        document["x-provenance"] = str(ref.at("r1"))
        event.write_bytes(render(document))
    report = sync(pair[DESKTOP].store, pair[PHONE].store, handles())  # in one pass, not held for a second
    assert report.conflicts == (), report.conflicts
    assert ledger_files(pair[DESKTOP].store) == ledger_files(pair[PHONE].store)
    assert len(read_ledger(pair[PHONE].store).events) == 2
    for device in pair.values():  # each definition reached the device that lacked it
        for world in ("deskworld", "phoneworld"):
            assert (device.store.root / "sources" / "world" / world / "_source" / "records" / "character" / "wren"
                    / "r1.json").exists()

