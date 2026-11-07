"""028's ledger merge, end to end over two synthetic device libraries (no real library).

Each test copies one agreed library to two devices, plays something offline on each and merges the two
ledgers. A clean merge must give both devices the same bytes, leave a library 021 finds no problem in,
and be idempotent; anything it cannot settle must come back as a review naming why, never as a merge
that dropped something.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire.core.identity import AcceptedRef, HoldingsRef, RelationRef
from grimoire.holdings import Holdings, Line, Operation, plan
from grimoire.ledgermerge import Kept, LedgerReview, Merged, Review, ReviewItem, Version, merge_holdings, merge_ledger
from grimoire.store import LibraryStore, ScopeRecord
from grimoire.store.envelope import scope_record_document
from grimoire.store.jsonfmt import render

from effects_fixtures import C, CHAT, INES, KEY1, TAM, WREN, give, moment
from holdings_fixtures import ZeroSizer
from ledger_fixtures import (
    COIN, OTHER, Device, StoreSizer, build_base, file_bytes, merge, pay, path_of, read_ledger, write_back,
)


@pytest.fixture
def base_path(tmp_path: Path) -> Path:
    return build_base(tmp_path)


@pytest.fixture
def base(base_path: Path):
    store = LibraryStore.open(base_path)
    yield store
    store.close()


@pytest.fixture
def devices(base_path: Path, tmp_path: Path):
    made = {name: Device(base_path, tmp_path, name) for name in ("desktop", "phone")}
    yield made
    for device in made.values():
        device.close()


def stores(devices) -> dict[str, LibraryStore]:
    return {name: device.store for name, device in devices.items()}


def tag_of(ref) -> str:
    return ref.event_id.rsplit("-", 1)[1]


def reasons(result) -> set[str]:
    assert isinstance(result, Review), result
    return set(result.reasons)


def ledger_bytes(store: LibraryStore) -> dict[str, bytes]:
    """Every ledger file of the campaign, by path: what the merge owns."""
    found = {}
    for kind in ("accepted", "wrapups", "relations", "holdings"):
        root = store.root / "campaigns" / C / kind
        for path in sorted(root.rglob("*.json")) if root.exists() else ():
            found[path.relative_to(store.root).as_posix()] = path.read_bytes()
    return found


def swapped(sides: dict) -> dict:
    return dict(reversed(list(sides.items())))


def agreed_base(base_path: Path, tmp_path: Path, *proposals) -> Path:
    """The base library with ``proposals`` already accepted (an untagged library: both devices agree)."""
    agreed = Device(base_path, tmp_path, "agreed", identified=False)
    for proposal in proposals:
        agreed.accept(proposal)
    agreed.close()
    return tmp_path / "agreed"


# -- a clean merge ------------------------------------------------------------------------------

def test_two_chats_offline_merge_to_identical_bytes_and_a_clean_library(base, devices, tmp_path):
    desktop, phone = devices["desktop"], devices["phone"]
    gave = desktop.accept(give())  # Wren gives Tam the iron key, in the first chat
    paid = phone.accept(pay())  # Tam pays Ines two coins, in the second chat, at the same sequence

    result = merge(base, stores(devices))
    assert isinstance(result, Merged), result
    # same sequence: by the accepting device's tag (phone's sorts before desktop's), whichever side is first
    assert tag_of(paid) < tag_of(gave) and result.order == (paid.event_id, gave.event_id)
    flipped = merge(base, dict(reversed(list(stores(devices).items()))))
    assert flipped == result
    files = file_bytes(result, base, stores(devices))
    assert file_bytes(flipped, base, dict(reversed(list(stores(devices).items())))) == files

    for device in devices.values():
        write_back(device.store, files)
    assert ledger_bytes(desktop.store) == ledger_bytes(phone.store)

    for device in devices.values():
        seen = device.effects.continuity(C, as_of=100, requested_by="director")
        assert seen.problems == ()
        assert sorted(event.ordinal for event in seen.events) == [1, 2]
        assert {event.event_id for event in seen.events} == {gave.event_id, paid.event_id}
    holdings = read_ledger(desktop.store).holdings
    assert holdings[TAM].value.count(COIN) == 8 and holdings[INES].value.count(COIN) == 2
    assert holdings[WREN].value.count(COIN) == 10
    carried = [v.value for v in read_ledger(desktop.store).relations.values()]
    assert [r.subject for r in carried if r.object == KEY1 and r.ending is None] == [TAM]

    # the merged library goes on: the next acceptance takes the next ordinal, and it can be revoked
    nxt = desktop.accept(pay(TAM, INES, 1, sequence=9, chat=CHAT))
    event, _ = desktop.effects.read(nxt, requested_by="director")
    assert event.ordinal == 3
    desktop.revoke(nxt)
    assert desktop.effects.continuity(C, as_of=100, requested_by="director").problems == ()


def test_a_device_that_did_nothing_takes_the_other_ones_files_unchanged(base, devices):
    devices["phone"].accept(pay())
    result = merge(base, stores(devices))
    assert isinstance(result, Merged)
    assert all(isinstance(out, Kept) for out in result.files.values())  # nothing to rewrite: phone's own bytes
    assert file_bytes(result, base, stores(devices)) == ledger_bytes(devices["phone"].store)


def test_holdings_receipts_are_rewritten_from_the_replayed_seqs(base, devices):
    first = devices["desktop"].accept(pay(TAM, INES, 3, chat=CHAT))
    second = devices["phone"].accept(pay(TAM, WREN, 4))
    result = merge(base, stores(devices))
    assert isinstance(result, Merged), result
    files = file_bytes(result, base, stores(devices))
    write_back(devices["desktop"].store, files)
    seen = devices["desktop"].effects.continuity(C, as_of=100, requested_by="director")
    assert seen.problems == ()
    tam = read_ledger(devices["desktop"].store).holdings[TAM].value
    assert tam.count(COIN) == 3 and [entry.seq for entry in tam.log][-2:] == [2, 3]
    receipts = {e.event_id: e.holdings.seqs[TAM] for e in seen.events}  # type: ignore[union-attr]
    # one sequence: phone's event first by its device tag, so its payment is Tam's second entry
    assert receipts == {second.event_id: 2, first.event_id: 3}


def test_hand_operations_on_both_devices_both_count(base, devices):
    devices["desktop"].hand_op(Operation("found-coins", (Line.add(INES, COIN, 3),)))
    devices["phone"].hand_op(Operation("won-coins", (Line.add(INES, COIN, 2),)))
    result = merge(base, stores(devices))
    assert isinstance(result, Merged), result
    files = file_bytes(result, base, stores(devices))
    assert file_bytes(merge(base, dict(reversed(list(stores(devices).items())))), base, stores(devices)) == files
    write_back(devices["desktop"].store, files)
    ines = read_ledger(devices["desktop"].store).holdings[INES].value
    assert ines.count(COIN) == 5 and [entry.seq for entry in ines.log] == [1, 2]


# -- what stops a merge -------------------------------------------------------------------------

def test_one_chat_on_both_devices_is_a_policy_review_with_the_merge_it_would_make(base, devices):
    devices["desktop"].accept(give())
    devices["phone"].accept(pay(chat=CHAT, sequence=8))
    result = merge(base, stores(devices))
    assert isinstance(result, LedgerReview)
    assert reasons(result) == {"same_chat"}
    assert result.items == (ReviewItem("same_chat", str(CHAT), "new events on both devices"),)
    assert isinstance(result.candidate, Merged) and len(result.candidate.order) == 2


def test_spending_the_same_coins_twice_is_refused_by_replay(base, devices):
    devices["desktop"].accept(pay(TAM, INES, 6, chat=CHAT))
    devices["phone"].accept(pay(TAM, WREN, 6))
    result = merge(base, stores(devices))
    assert reasons(result) == {"insufficient"}
    assert isinstance(result, LedgerReview) and result.candidate is None


def test_giving_away_one_key_on_both_devices_ends_one_relation_twice(base, devices):
    devices["desktop"].accept(give())
    devices["phone"].accept(give(KEY1, WREN, INES, chat=OTHER, summary="Wren gives Ines the iron key."))
    result = merge(base, stores(devices))
    assert isinstance(result, LedgerReview) and result.candidate is None
    assert result.items == (ReviewItem("both_changed", "campaign:c#relation/r-seed-1",
                                       "a relation changed on both devices"),)


def test_the_same_effect_accepted_on_both_devices_is_named(base, devices):
    devices["desktop"].accept(pay(chat=CHAT))
    devices["phone"].accept(pay(chat=OTHER, attempt="pay-t-1-7"))  # a different chat: not one fingerprint
    assert reasons(merge(base, stores(devices))) == {"attempt_reused"}


def test_one_proposal_accepted_twice_is_same_effect(base, devices):
    devices["desktop"].accept(give())
    devices["phone"].accept(give())
    found = reasons(merge(base, stores(devices)))
    assert "same_effect" in found and "same_chat" in found


def test_untagged_devices_collide_on_event_ids(base_path, tmp_path):
    desktop = Device(base_path, tmp_path, "desktop", identified=False)
    phone = Device(base_path, tmp_path, "phone", identified=False)
    base = LibraryStore.open(base_path)
    try:
        assert desktop.accept(give()) == phone.accept(pay())  # both "e-1": why 028 tags new ids
        assert "id_collision" in reasons(merge(base, {"desktop": desktop.store, "phone": phone.store}))
    finally:
        for store in (desktop, phone):
            store.close()
        base.close()


def test_one_operation_id_logged_differently_on_two_holders_no_other_device_touched_is_a_collision(base, devices):
    devices["desktop"].hand_op(Operation("offline-op-1", (Line.add(TAM, COIN, 3),)))
    devices["phone"].hand_op(Operation("offline-op-1", (Line.add(WREN, COIN, 4),)))  # each file taken whole
    assert reasons(merge(base, stores(devices))) == {"id_collision"}


def test_one_operation_id_on_a_file_taken_whole_and_a_replayed_one_is_a_collision(base, devices):
    devices["desktop"].hand_op(Operation("offline-op-1", (Line.add(TAM, COIN, 3),)))
    devices["desktop"].hand_op(Operation("found-coins", (Line.add(INES, COIN, 1),)))
    devices["phone"].hand_op(Operation("won-coins", (Line.add(INES, COIN, 2),)))  # Ines replayed, Tam taken whole
    devices["phone"].hand_op(Operation("offline-op-1", (Line.add(INES, COIN, 5),)))
    assert "id_collision" in reasons(merge(base, stores(devices)))


def _restore(device: Device, base: LibraryStore, name: str) -> None:
    """Put the agreed bytes of one holder file back on a device (a restored backup of that one file)."""
    agreed = next((base.root / "campaigns" / C / "holdings").rglob(name))
    write_back(device.store, {agreed.relative_to(base.root).as_posix(): agreed.read_bytes()})


def test_an_operation_one_holder_file_taken_whole_logs_but_its_peer_does_not_is_partial(base, devices):
    devices["desktop"].hand_op(Operation("offline-op-1", (Line.move(TAM, WREN, COIN, 3),)))
    _restore(devices["desktop"], base, "wren.json")  # Tam's file keeps the transfer, Wren's is the agreed one
    devices["phone"].hand_op(Operation("found-coins", (Line.add(INES, COIN, 1),)))  # Tam and Wren: taken whole
    for sides in (stores(devices), swapped(stores(devices))):
        result = merge(base, sides)
        assert isinstance(result, LedgerReview) and result.candidate is None
        assert result.items == (ReviewItem("partial_op", "offline-op-1", f"desktop does not log it on {WREN}"),)


def test_a_peer_that_evicted_an_operation_a_file_taken_whole_still_logs_is_not_partial(base, devices):
    devices["desktop"].hand_op(Operation("offline-op-1", (Line.move(TAM, WREN, COIN, 3),)))
    devices["phone"].hand_op(Operation("found-coins", (Line.add(INES, COIN, 1),)))
    ledgers = {name: read_ledger(store) for name, store in stores(devices).items()}
    wren = ledgers["desktop"].holdings[WREN]
    # Wren's file evicted the transfer's entry (a full file, say); Tam's still logs it
    evicted = replace(wren, value=replace(wren.value, log=(), evicted_through=wren.value.seq),
                      etag="sha256:" + "2" * 64)
    ledgers["desktop"] = replace(ledgers["desktop"], holdings={**ledgers["desktop"].holdings, WREN: evicted})
    kwargs = {"instances": set(base.campaign_instances(C)), "sizer": StoreSizer()}
    result = merge_ledger(read_ledger(base), ledgers, **kwargs)
    assert isinstance(result, Merged), result
    assert merge_ledger(read_ledger(base), swapped(ledgers), **kwargs) == result


def test_revoking_an_agreed_event_while_the_other_device_goes_on(base_path, tmp_path):
    agreed = agreed_base(base_path, tmp_path, give())
    desktop, phone = Device(agreed, tmp_path, "desktop"), Device(agreed, tmp_path, "phone")
    base = LibraryStore.open(agreed)
    try:
        ref = AcceptedRef(C, "e-1")
        desktop.revoke(ref, give())
        phone.accept(pay())
        result = merge(base, {"desktop": desktop.store, "phone": phone.store})
        assert reasons(result) == {"revoked_vs_extended"}
        assert isinstance(result, LedgerReview) and isinstance(result.candidate, Merged)
        write_back(desktop.store, file_bytes(result.candidate, base, {"desktop": desktop.store,
                                                                       "phone": phone.store}))
        assert desktop.effects.continuity(C, as_of=100, requested_by="director").problems == ()
    finally:
        for store in (desktop, phone):
            store.close()
        base.close()


def test_revoking_one_event_differently_on_both_devices_is_both_changed(base_path, tmp_path):
    agreed = agreed_base(base_path, tmp_path, give())
    desktop, phone = Device(agreed, tmp_path, "desktop"), Device(agreed, tmp_path, "phone")
    base = LibraryStore.open(agreed)
    try:
        desktop.revoke(AcceptedRef(C, "e-1"), give(), reason="revised")
        phone.revoke(AcceptedRef(C, "e-1"), give(), reason="mistaken")
        assert "both_changed" in reasons(merge(base, {"desktop": desktop.store, "phone": phone.store}))
    finally:
        for store in (desktop, phone):
            store.close()
        base.close()


def test_revoking_one_event_identically_on_both_devices_merges(base_path, tmp_path):
    agreed = agreed_base(base_path, tmp_path, give())
    desktop, phone = Device(agreed, tmp_path, "desktop"), Device(agreed, tmp_path, "phone")
    base = LibraryStore.open(agreed)
    try:
        desktop.revoke(AcceptedRef(C, "e-1"), give())
        phone.revoke(AcceptedRef(C, "e-1"), give())
        result = merge(base, {"desktop": desktop.store, "phone": phone.store})
        assert isinstance(result, Merged), result
    finally:
        for store in (desktop, phone):
            store.close()
        base.close()


def test_an_attempt_one_device_accepted_and_revoked_in_full_may_be_spent_on_the_other(base, base_path, devices,
                                                                                      tmp_path):
    first = pay(TAM, INES, 2)
    revised = pay(TAM, INES, 3, chat=CHAT, attempt=first.attempt_id)  # a revised effect under the same attempt
    one = Device(base_path, tmp_path, "sequential")  # one device may do exactly this in turn
    try:
        one.revoke(one.accept(first))
        one.accept(revised)
    finally:
        one.close()
    paid = devices["desktop"].accept(first)
    devices["desktop"].revoke(paid)
    devices["phone"].accept(revised)
    result = merge(base, stores(devices))
    assert isinstance(result, Merged), result
    assert merge(base, swapped(stores(devices))) == result
    files = file_bytes(result, base, stores(devices))
    write_back(devices["desktop"].store, files)
    assert devices["desktop"].effects.continuity(C, as_of=100, requested_by="director").problems == ()
    held = read_ledger(devices["desktop"].store).holdings
    assert held[TAM].value.count(COIN) == 7 and held[INES].value.count(COIN) == 3


def test_a_relation_of_an_unknown_predicate_is_kept_byte_for_byte(base_path, tmp_path):
    agreed = tmp_path / "agreed"
    shutil.copytree(base_path, agreed)
    ref = RelationRef(C, "r-future-1")  # written by a newer version: a predicate this one does not know
    record = ScopeRecord(ref, "grimoire.relation/0", {"predicate": "mentors", "subject": str(TAM), "object": str(INES),
                                                      "provenance": "director", "since": {"sequence": 1}},
                         owner="human:owner")
    path = path_of(ref)
    agreed.joinpath(*path.split("/")).write_bytes(render(scope_record_document(record, None)))
    desktop, phone = Device(agreed, tmp_path, "desktop"), Device(agreed, tmp_path, "phone")
    base = LibraryStore.open(agreed)
    try:
        sides = {"desktop": desktop.store, "phone": phone.store}
        idle = merge(base, sides)  # nothing done on either device
        assert isinstance(idle, Merged), idle
        assert all(isinstance(out, Kept) for out in idle.files.values())
        desktop.accept(give())
        phone.accept(pay())
        result = merge(base, sides)
        assert isinstance(result, Merged), result
        assert merge(base, swapped(sides)) == result
        files = file_bytes(result, base, sides)
        assert files[path] == agreed.joinpath(*path.split("/")).read_bytes()
    finally:
        for device in (desktop, phone):
            device.close()
        base.close()


def test_a_hand_edit_on_one_device_is_kept_on_top_of_the_others_operations(base, devices):
    devices["desktop"].hand_edit(WREN, COIN, 15)  # an owner's edit of the file: five more, no log entry
    devices["phone"].accept(pay(TAM, INES, 2))
    result = merge(base, stores(devices))
    assert isinstance(result, Merged), result
    write_back(devices["phone"].store, file_bytes(result, base, stores(devices)))
    held = read_ledger(devices["phone"].store).holdings
    assert held[WREN].value.count(COIN) == 15 and held[TAM].value.count(COIN) == 8


def test_a_hand_edit_of_a_count_the_other_device_changed_is_an_overlap(base, devices):
    devices["desktop"].hand_edit(TAM, COIN, 12)
    devices["phone"].accept(pay(TAM, INES, 2))
    result = merge(base, stores(devices))
    assert reasons(result) == {"hand_edit_overlap"}


def test_the_same_hand_edit_on_both_devices_is_one_edit(base, devices):
    devices["desktop"].hand_edit(TAM, COIN, 12)
    devices["phone"].hand_edit(TAM, COIN, 12)
    result = merge(base, stores(devices))
    assert isinstance(result, Merged), result


def test_a_file_one_device_deleted_is_never_dropped_silently(base, devices):
    devices["desktop"].accept(give())
    path = devices["phone"].store.root / "campaigns" / C / "holdings"
    victim = next(path.rglob("wren.json"))
    victim.unlink()
    result = merge(base, stores(devices))
    assert "deleted" in reasons(result)


def test_a_log_that_does_not_extend_the_agreed_one_is_diverged(base, devices):
    ledgers = {name: read_ledger(store) for name, store in stores(devices).items()}
    tam = ledgers["phone"].holdings[TAM]
    # a side whose log goes back past the agreed one is no extension of it (a restored backup, say)
    shrunk = replace(tam, value=replace(tam.value, seq=0, log=()), etag="sha256:" + "1" * 64)
    phone = replace(ledgers["phone"], holdings={**ledgers["phone"].holdings, TAM: shrunk})
    result = merge_ledger(read_ledger(base), {"desktop": ledgers["desktop"], "phone": phone},
                          instances=set(base.campaign_instances(C)), sizer=StoreSizer())
    assert reasons(result) == {"log_diverged"}


# -- idempotence ---------------------------------------------------------------------------------

def test_a_merge_merged_again_with_itself_or_a_side_is_unchanged(base, base_path, devices, tmp_path):
    desktop, phone = devices["desktop"], devices["phone"]
    desktop.accept(give())
    desktop.hand_op(Operation("found-coins", (Line.add(INES, COIN, 3),)))
    phone.accept(pay())
    shutil.copytree(phone.store.root, tmp_path / "phone-before")
    before = LibraryStore.open(tmp_path / "phone-before")
    try:
        result = merge(base, stores(devices))
        assert isinstance(result, Merged), result
        files = file_bytes(result, base, stores(devices))
        for device in devices.values():
            write_back(device.store, files)
        again = merge(base, stores(devices))
        assert isinstance(again, Merged) and again.order == result.order
        assert file_bytes(again, base, stores(devices)) == files
        assert all(isinstance(out, Kept) for out in again.files.values())
        mixed = {"desktop": desktop.store, "phone": before}
        once_more = merge(base, mixed)
        assert isinstance(once_more, Merged), once_more
        assert file_bytes(once_more, base, mixed) == files
        # and with the merged library as the new base, nothing is left to merge
        settled = merge(desktop.store, stores(devices))
        assert isinstance(settled, Merged) and file_bytes(settled, desktop.store, stores(devices)) == files
    finally:
        before.close()


def test_one_scene_wrapped_up_differently_on_both_devices_is_both_changed(base, devices):
    for name, summary in (("desktop", "The key changes hands."), ("phone", "A quiet night.")):
        devices[name].effects.wrap_up(CHAT, "s1", through=moment(7), summary=summary, cites=[], written_by="director",
                                      requested_by="director").commit()
    result = merge(base, stores(devices))
    assert isinstance(result, LedgerReview) and result.candidate is None
    assert reasons(result) == {"both_changed"}


def test_a_scene_wrapped_up_on_one_device_merges_with_the_others_play(base, devices):
    devices["desktop"].effects.wrap_up(CHAT, "s1", through=moment(7), summary="Nothing happened.", cites=[],
                                       written_by="director", requested_by="director").commit()
    devices["phone"].accept(pay())
    result = merge(base, stores(devices))
    assert isinstance(result, Merged), result
    write_back(devices["phone"].store, file_bytes(result, base, stores(devices)))
    seen = devices["phone"].effects.continuity(C, as_of=100, requested_by="director")
    assert seen.problems == () and len(seen.wrapups) == 1


# -- the merge's own arguments ---------------------------------------------------------------------

def test_a_device_called_base_merges_like_any_other(base, devices):
    # "base" is a valid device id; the merge's word for the agreed state must never collide with it
    devices["desktop"].accept(give())
    devices["phone"].accept(pay())
    named = stores(devices)
    renamed = {"base": named["desktop"], "phone": named["phone"]}  # "base" ranks before "phone", as "desktop" does
    expected, result = merge(base, named), merge(base, renamed)
    assert isinstance(result, Merged), result
    assert result.order == expected.order
    assert file_bytes(result, base, renamed) == file_bytes(expected, base, named)


def test_a_merge_takes_exactly_two_devices_of_the_bases_campaign(base, devices):
    ledgers = {name: read_ledger(store) for name, store in stores(devices).items()}
    agreed = read_ledger(base)
    kwargs = {"instances": set(base.campaign_instances(C)), "sizer": StoreSizer()}
    with pytest.raises(ValueError, match="exactly two"):
        merge_ledger(agreed, {"desktop": ledgers["desktop"]}, **kwargs)
    with pytest.raises(ValueError, match="campaign"):
        merge_ledger(agreed, {"desktop": replace(ledgers["desktop"], campaign_id="d"), "phone": ledgers["phone"]},
                     **kwargs)
    with pytest.raises(ValueError, match="identifier"):
        ReviewItem("not a reason", "x", "y")
    with pytest.raises(ValueError):
        Review(())


def test_a_wrap_up_that_misses_the_other_devices_event_is_a_new_problem(base, devices):
    # desktop wraps the scene up with nothing to cite; phone accepted an event in that scene meanwhile
    devices["desktop"].effects.wrap_up(CHAT, "s1", through=moment(7), summary="A quiet night.", cites=[],
                                       written_by="director", requested_by="director").commit()
    devices["phone"].accept(pay(chat=CHAT, sequence=5))
    result = merge(base, stores(devices))
    assert isinstance(result, LedgerReview) and result.candidate is None
    assert reasons(result) == {"new_problem"}  # a wrap-up is not an event: only 021's check catches this
    assert "wrapup" in result.items[0].detail or "cite" in result.items[0].detail


def test_a_merge_evicts_as_any_write_would_and_stays_put_once_both_devices_hold_it(base, devices):
    for name, device in devices.items():
        for number in range(5):
            device.hand_op(Operation(f"{name}-{number}", (Line.add(INES, COIN, 1),)))
    size = len(next((devices["desktop"].store.root / "campaigns" / C / "holdings").rglob("ines.json")).read_bytes())
    tight = size + 600  # room for about six entries of the ten
    result = merge(base, stores(devices), max_file=tight)
    assert isinstance(result, Merged), result
    files = file_bytes(result, base, stores(devices))
    shutil.copytree(devices["phone"].store.root, devices["phone"].store.root.parent / "phone-before")
    for device in devices.values():
        write_back(device.store, files)
    ines = read_ledger(devices["desktop"].store).holdings[INES].value
    assert ines.count(COIN) == 10 and ines.seq == 10 and ines.evicted_through > 0
    again = merge(base, stores(devices), max_file=tight)
    assert isinstance(again, Merged) and file_bytes(again, base, stores(devices)) == files
    # a device that missed the merge cannot be replayed against a log that no longer shows those entries
    before = LibraryStore.open(devices["phone"].store.root.parent / "phone-before")
    try:
        lagging = merge(base, {"desktop": devices["desktop"].store, "phone": before}, max_file=tight)
        assert reasons(lagging) == {"log_diverged"}
    finally:
        before.close()


def test_a_third_devices_events_keep_their_place_however_many_sides_hold_them(base_path, tmp_path):
    # desktop accepted its own event, then merged a tablet's (same sequence); the phone did nothing
    made = {name: Device(base_path, tmp_path, name) for name in ("a-phone", "desktop", "tablet")}
    base = LibraryStore.open(base_path)
    try:
        own = made["desktop"].accept(give(sequence=8, turn="t8"))
        theirs = made["tablet"].accept(pay(sequence=8))
        first = merge(base, {"desktop": made["desktop"].store, "tablet": made["tablet"].store})
        assert isinstance(first, Merged), first
        write_back(made["desktop"].store, file_bytes(first, base, {"desktop": made["desktop"].store,
                                                                    "tablet": made["tablet"].store}))
        pair = {"a-phone": made["a-phone"].store, "desktop": made["desktop"].store}
        result = merge(base, pair)
        assert isinstance(result, Merged), result
        # by tag alone, whichever pair merges: the tablet's tag sorts before the desktop's
        assert tag_of(theirs) < tag_of(own) and result.order == (theirs.event_id, own.event_id)
        files = file_bytes(result, base, pair)
        write_back(made["a-phone"].store, files)  # the phone took the merge; the desktop has not yet
        lagging = merge(base, pair)
        assert isinstance(lagging, Merged) and lagging.order == result.order
        assert file_bytes(lagging, base, pair) == files
        write_back(made["desktop"].store, files)
        settled = merge(base, pair)
        assert isinstance(settled, Merged) and settled.order == result.order
        assert file_bytes(settled, base, pair) == files
    finally:
        for device in made.values():
            device.close()
        base.close()


def test_a_holder_file_rewritten_for_hand_edits_alone_is_sized_and_evicts_or_is_too_large(base, devices):
    devices["desktop"].hand_edit(WREN, "apple", 1)  # no operation on either device: only the residuals rewrite it
    devices["phone"].hand_edit(WREN, "pear", 1)
    roomy = file_bytes(merge(base, stores(devices)), base, stores(devices))
    path = next(path for path in roomy if path.endswith("wren.json"))
    result = merge(base, stores(devices), max_file=len(roomy[path]) - 1)  # only the agreed seed entry makes room
    assert isinstance(result, Merged), result
    files = file_bytes(result, base, stores(devices))
    assert len(files[path]) < len(roomy[path])
    assert reasons(merge(base, stores(devices), max_file=len(files[path]) - 1)) == {"too_large"}  # nothing left
    for device in devices.values():
        write_back(device.store, files)
    wren = read_ledger(devices["desktop"].store).holdings[WREN].value
    assert wren.log == () and wren.evicted_through == 1 and dict(wren.stacks) == {COIN: 10, "apple": 1, "pear": 1}
    again = merge(base, stores(devices), max_file=len(roomy[path]) - 1)
    assert isinstance(again, Merged) and file_bytes(again, base, stores(devices)) == files


def test_an_empty_agreed_holder_hand_edited_on_both_devices_past_the_bound_is_too_large():
    empty = Holdings.empty(WREN)
    sides = {name: {WREN: Version(replace(empty, stacks={item: 1}), f"sha256:{name}", "human:owner")}
             for name, item in (("desktop", "apple"), ("phone", "pear"))}
    agreed = {WREN: Version(empty, "sha256:agreed", "human:owner")}
    roomy = merge_holdings(agreed, sides, exists=lambda holder: True, sizer=StoreSizer())
    size = StoreSizer().document(roomy.values[WREN], "human:owner", {}, "sha256:agreed")
    assert dict(roomy.values[WREN].stacks) == {"apple": 1, "pear": 1}
    refused = merge_holdings(agreed, sides, exists=lambda holder: True, sizer=StoreSizer(), max_file=size - 1)
    assert isinstance(refused, Review) and refused.reasons == {"too_large"}


def _logged(holdings: Holdings, op: Operation) -> Holdings:
    return plan(op, {holdings.holder: holdings}, "2026-11-07T00:00:00Z", exists=lambda holder: True,
                size=ZeroSizer()).values[holdings.holder]


def test_a_file_taken_whole_whose_holder_the_merged_library_lacks_is_missing_holder():
    empty = Holdings.empty(WREN)
    credited = _logged(empty, Operation("found-coin", (Line.add(WREN, COIN, 1),)))
    agreed = {WREN: Version(empty, "sha256:agreed", "human:owner")}
    sides = {"desktop": {WREN: Version(credited, "sha256:desktop", "human:owner")},
             "phone": {WREN: Version(empty, "sha256:agreed", "human:owner")}}  # unchanged: desktop's taken whole
    result = merge_holdings(agreed, sides, exists=lambda holder: False, sizer=StoreSizer())
    assert isinstance(result, Review) and result.reasons == {"missing_holder"}
    kept = merge_holdings(agreed, sides, exists=lambda holder: True, sizer=StoreSizer())  # merges once it exists
    assert not isinstance(kept, Review) and kept.files[HoldingsRef.of(WREN)] == Kept("sha256:desktop", "desktop")


def test_a_seq_far_past_the_log_is_a_gap_and_no_range_is_built():
    empty = Holdings.empty(WREN)
    agreed = {WREN: Version(empty, "sha256:agreed", "human:owner")}
    for other in (empty, replace(empty, stacks={"pear": 1})):  # taken whole, or replayed against a hand edit
        sides = {"desktop": {WREN: Version(replace(empty, seq=10 ** 15), "sha256:desktop", "human:owner")},
                 "phone": {WREN: Version(other, "sha256:phone", "human:owner")}}
        result = merge_holdings(agreed, sides, exists=lambda holder: True, sizer=StoreSizer())
        assert isinstance(result, Review) and result.reasons == {"log_diverged"}
