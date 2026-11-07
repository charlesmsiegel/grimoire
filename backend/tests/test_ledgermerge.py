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

from grimoire.core.identity import AcceptedRef
from grimoire.holdings import Line, Operation
from grimoire.ledgermerge import Kept, LedgerReview, Merged, Review, ReviewItem, merge_ledger
from grimoire.store import LibraryStore

from effects_fixtures import C, CHAT, INES, KEY1, TAM, WREN, give, moment
from ledger_fixtures import (
    COIN, OTHER, Device, StoreSizer, build_base, file_bytes, merge, pay, read_ledger, write_back,
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


def reasons(result) -> set[str]:
    assert isinstance(result, Review), result
    return set(result.reasons)


def ledger_bytes(store: LibraryStore) -> dict[str, bytes]:
    """Every ledger file of the campaign, by path: what the merge owns."""
    found = {}
    for kind in ("accepted", "wrapups", "relations", "holdings"):
        root = store.root / "campaigns" / C / kind
        for path in sorted(root.rglob("*.json")) if root.exists() else ():
            found[str(path.relative_to(store.root))] = path.read_bytes()
    return found


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
    # same sequence: the lower device id first, whichever side the caller lists first
    assert result.order == (gave.event_id, paid.event_id)
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
    assert receipts == {first.event_id: 2, second.event_id: 3}


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

def test_a_merge_takes_exactly_two_devices_of_the_bases_campaign(base, devices):
    ledgers = {name: read_ledger(store) for name, store in stores(devices).items()}
    agreed = read_ledger(base)
    kwargs = {"instances": set(base.campaign_instances(C)), "sizer": StoreSizer()}
    with pytest.raises(ValueError, match="exactly two"):
        merge_ledger(agreed, {"desktop": ledgers["desktop"]}, **kwargs)
    with pytest.raises(ValueError, match="agreed state"):
        merge_ledger(agreed, {"base": ledgers["desktop"], "phone": ledgers["phone"]}, **kwargs)
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
        assert result.order == (own.event_id, theirs.event_id)  # a device neither side is comes after both
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
