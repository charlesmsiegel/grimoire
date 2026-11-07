"""028's ledger merge order: causal, and the same for every pair of devices (synthetic names).

A library paired with two others merges with each in turn, each pair from its own agreed state. The order of
events must not depend on which pair is merging or on which side holds an event, or the pairs would renumber
the same events back and forth forever; and no event may go below one it was accepted after, or 021's undo
stack (latest first) could no longer take it back.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from itertools import combinations
from pathlib import Path

import pytest

from grimoire.effects import accepted_from_payload, accepted_schema, accepted_to_payload
from grimoire.holdings import Line, Operation
from grimoire.ledgermerge import Merged, Review
from grimoire.store import LibraryStore, ScopeRecord
from grimoire.store.envelope import scope_record_document
from grimoire.store.jsonfmt import render

from effects_fixtures import C, GUILD, INES, KEY1, KEY2, TAM, WREN, give
from ledger_fixtures import (
    CHAT, COIN, OTHER, THIRD, Device, build_base, file_bytes, merge, pay, path_of, read_ledger, write_back,
)
from test_ledgermerge import ledger_bytes, tag_of as tag

NAMES = ("a-tablet", "desktop", "phone")
HOMES = {"a-tablet": CHAT, "desktop": OTHER, "phone": THIRD}
# Each device pays from purses only it touches: three devices sharing one holder file cannot converge (below)
PURSES = {"a-tablet": (TAM, KEY1), "desktop": (WREN, KEY2), "phone": (INES, GUILD)}


@pytest.fixture
def base_path(tmp_path: Path) -> Path:
    return build_base(tmp_path)


class Pairs:
    """Three devices, and each pair's agreed state (a copy of the library as that pair last merged it)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        base = build_base(root)
        self.devices = {name: Device(base, root, name) for name in NAMES}
        self.bases = {pair: self._open(base, "-".join(pair)) for pair in combinations(NAMES, 2)}

    def _open(self, source: Path, name: str) -> LibraryStore:
        target = self.root / f"agreed-{name}"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        return LibraryStore.open(target)

    def sync(self, one: str, other: str) -> Merged:
        """Merge one pair from its agreed state, store the merge on both, and agree on it."""
        pair = tuple(sorted((one, other)))
        sides = {name: self.devices[name].store for name in pair}
        result = merge(self.bases[pair], sides)
        assert isinstance(result, Merged), (pair, result)
        files = file_bytes(result, self.bases[pair], sides)
        for name in pair:
            write_back(self.devices[name].store, files)
        self.bases[pair].close()
        self.bases[pair] = self._open(self.devices[pair[0]].store.root, "-".join(pair))
        return result

    def round(self) -> None:
        for one, other in combinations(NAMES, 2):
            self.sync(one, other)

    def close(self) -> None:
        for store in self.bases.values():
            store.close()
        for device in self.devices.values():
            device.close()


def ordinals(store: LibraryStore) -> dict[str, int]:
    return {ref.event_id: version.value.ordinal for ref, version in read_ledger(store).events.items()}


def test_three_devices_merging_round_robin_settle_on_one_numbering(tmp_path):
    pairs = Pairs(tmp_path)
    try:
        pairs.devices["phone"].hand_op(Operation("found-coins", (Line.add(INES, COIN, 5),)))
        for number in range(3):
            sequence = 8 + number
            for name, device in pairs.devices.items():  # every device plays its own chat at the same sequence
                giver, to = PURSES[name]
                device.accept(pay(giver, to, 1, sequence=sequence, chat=HOMES[name], attempt=f"{name}-{sequence}"))
            if number == 1:  # desktop takes back what it just accepted, before any merge carries it
                desktop = pairs.devices["desktop"]
                latest = max(ordinals(desktop.store).items(), key=lambda item: item[1])[0]
                desktop.revoke(next(ref for ref in read_ledger(desktop.store).events if ref.event_id == latest))
            pairs.round()
        pairs.round()  # nothing new: every device now holds every event

        held = {name: ledger_bytes(device.store) for name, device in pairs.devices.items()}
        assert held["a-tablet"] == held["desktop"] == held["phone"]
        numbered = ordinals(pairs.devices["desktop"].store)
        assert sorted(numbered.values()) == list(range(1, 10))
        pairs.round()  # settled: another round of every pair changes no byte
        assert {name: ledger_bytes(device.store) for name, device in pairs.devices.items()} == held
        for device in pairs.devices.values():
            seen = device.effects.continuity(C, as_of=100, requested_by="director")
            assert seen.problems == ()
            assert len(seen.events) == 8 and len(seen.revoked) == 1
            # one sequence after another, and at one sequence the devices in one fixed order
            assert [e.event_id for e in seen.events] == sorted(
                (e.event_id for e in seen.events), key=lambda event_id: numbered[event_id])
    finally:
        pairs.close()


def test_an_untagged_third_devices_event_keeps_its_place_once_the_other_side_holds_it(tmp_path):
    base_path = build_base(tmp_path)
    desktop, phone = Device(base_path, tmp_path, "desktop"), Device(base_path, tmp_path, "a-phone")
    tablet = Device(base_path, tmp_path, "tablet", identified=False)  # never identified: plain ids
    base = LibraryStore.open(base_path)
    behind = None
    try:
        own = desktop.accept(pay(sequence=8))
        theirs = tablet.accept(give(sequence=8, turn="t8"))
        # desktop synced with the tablet earlier: it holds the tablet's event, numbered after its own
        found = tablet.store.read(theirs)
        event = replace(accepted_from_payload(theirs, found.record.schema, found.record.payload), ordinal=2)
        record = ScopeRecord(theirs, accepted_schema(event), accepted_to_payload(event), owner=found.record.owner,
                             extra=found.record.extra)
        carried = {path_of(theirs): render(scope_record_document(record, None))}
        for ref in tablet.store.campaign_relations(C).records:
            carried[path_of(ref)] = tablet.store.root.joinpath(*path_of(ref).split("/")).read_bytes()
        write_back(desktop.store, carried)

        sides = {"a-phone": phone.store, "desktop": desktop.store}
        result = merge(base, sides)
        assert isinstance(result, Merged), result
        assert result.order == (theirs.event_id, own.event_id)  # an untagged id first at one sequence
        files = file_bytes(result, base, sides)
        shutil.copytree(desktop.store.root, tmp_path / "desktop-before")
        behind = LibraryStore.open(tmp_path / "desktop-before")
        write_back(phone.store, files)  # the phone took the merge; the desktop has not yet
        lagging = {"a-phone": phone.store, "desktop": behind}
        again = merge(base, lagging)
        assert isinstance(again, Merged) and again.order == result.order
        assert file_bytes(again, base, lagging) == files
        write_back(desktop.store, files)
        settled = merge(base, sides)
        assert isinstance(settled, Merged) and file_bytes(settled, base, sides) == files
    finally:
        if behind is not None:
            behind.close()
        base.close()
        for device in (desktop, phone, tablet):
            device.close()


def test_three_devices_sharing_one_holder_stop_on_a_diverged_log_rather_than_reorder_it(tmp_path):
    # Holder logs are numbered per pair: once the desktop/phone merge slots the phone's entry between two the
    # tablet and desktop agreed, the tablet/desktop pair no longer extends its agreed log. That is a review,
    # never a log rewritten in place (how three devices should share one holder is still open).
    pairs = Pairs(tmp_path)
    try:
        for name, device in pairs.devices.items():
            device.accept(pay(TAM, WREN, 1, sequence=8, chat=HOMES[name], attempt=f"{name}-8"))
        pairs.round()
        for name, device in pairs.devices.items():
            device.accept(pay(TAM, WREN, 1, sequence=9, chat=HOMES[name], attempt=f"{name}-9"))
        found = []
        for one, other in combinations(NAMES, 2):
            pair = (one, other)
            sides = {name: pairs.devices[name].store for name in pair}
            result = merge(pairs.bases[pair], sides)
            if isinstance(result, Merged):
                pairs.sync(one, other)
            else:
                found.append((pair, set(result.reasons)))
        assert found and all(reasons == {"log_diverged"} for _, reasons in found)
    finally:
        pairs.close()


def causally_numbered(store: LibraryStore) -> bool:
    """No event is numbered below one it was accepted after (021's undo stack depends on it)."""
    events = {ref.event_id: version.value for ref, version in read_ledger(store).events.items()}
    return all(events[before].ordinal < event.ordinal
               for event in events.values() for before in (event.after or ()) if before in events)


def latest(device: Device):
    """The latest active event by ordinal: the only one 021 lets a revocation take back."""
    events = {ref: version.value for ref, version in read_ledger(device.store).events.items() if version.value.active}
    return max(events, key=lambda ref: events[ref].ordinal)


def test_revokes_after_merges_keep_the_undo_stack_on_every_device(tmp_path):
    pairs = Pairs(tmp_path)
    try:
        pairs.devices["phone"].hand_op(Operation("found-coins", (Line.add(INES, COIN, 5),)))
        for sequence in (8, 9):
            for name, device in pairs.devices.items():
                giver, to = PURSES[name]
                device.accept(pay(giver, to, 1, sequence=sequence, chat=HOMES[name], attempt=f"{name}-{sequence}"))
            pairs.round()
            pairs.round()
            # every device holds every event: each takes back the latest, which is the same event everywhere,
            # and the next device then takes back the one before it
            for name in ("a-tablet", "phone"):
                undone = latest(pairs.devices[name])
                assert all(latest(device) == undone for device in pairs.devices.values()), name
                pairs.devices[name].revoke(undone)
                for other in NAMES:
                    if other != name:
                        pairs.sync(name, other)
        pairs.round()
        held = {name: ledger_bytes(device.store) for name, device in pairs.devices.items()}
        assert held["a-tablet"] == held["desktop"] == held["phone"]
        pairs.round()
        assert {name: ledger_bytes(device.store) for name, device in pairs.devices.items()} == held
        for device in pairs.devices.values():
            assert causally_numbered(device.store)
            seen = device.effects.continuity(C, as_of=100, requested_by="director")
            assert seen.problems == () and len(seen.revoked) == 4
    finally:
        pairs.close()


# -- causal order: never below an event it was accepted after ------------------------------------

def test_an_event_accepted_after_a_merged_one_stays_above_it_and_can_be_undone(base_path, tmp_path):
    desktop, phone = Device(base_path, tmp_path, "desktop"), Device(base_path, tmp_path, "phone")
    base = LibraryStore.open(base_path)
    agreed = None
    try:
        given = desktop.accept(pay(WREN, TAM, 5, sequence=8, chat=CHAT))  # Tam has 15
        sides = {"desktop": desktop.store, "phone": phone.store}
        first = merge(base, sides)
        assert isinstance(first, Merged), first
        files = file_bytes(first, base, sides)
        write_back(desktop.store, files)
        write_back(phone.store, files)
        shutil.copytree(phone.store.root, tmp_path / "agreed")
        agreed = LibraryStore.open(tmp_path / "agreed")
        # at the same moment, in another chat, the phone spends what the desktop's event gave; phone's tag sorts
        # before desktop's, but it was accepted after the desktop's event, so it stays after it
        spent = phone.accept(pay(TAM, INES, 14, sequence=8, chat=OTHER))
        event, _ = phone.effects.read(spent, requested_by="director")
        assert event.after == (given.event_id,)
        second = merge(agreed, sides)
        assert isinstance(second, Merged), second
        assert tag(spent) < tag(given) and second.order == (spent.event_id,)
        write_back(phone.store, file_bytes(second, agreed, sides))
        numbered = ordinals(phone.store)
        assert numbered[given.event_id] < numbered[spent.event_id]
        phone.revoke(spent)  # the latest, so the undo stack still takes it back
        assert phone.effects.continuity(C, as_of=100, requested_by="director").problems == ()
    finally:
        if agreed is not None:
            agreed.close()
        base.close()
        desktop.close()
        phone.close()


def test_events_from_before_028_keep_their_order_and_new_ones_follow_them(base_path, tmp_path):
    # an unidentified library accepted these (no ``after``): one at a later sequence, taken back, then one at an
    # earlier sequence. Their place is their ordinal, so the merge keeps them in that order, not by key
    legacy = Device(base_path, tmp_path, "legacy", identified=False)
    undone = legacy.accept(pay(TAM, WREN, 1, sequence=9, chat=CHAT, attempt="old-9"))
    legacy.revoke(undone)
    kept = legacy.accept(pay(TAM, WREN, 1, sequence=8, chat=CHAT, attempt="old-8"))
    legacy.close()
    agreed = tmp_path / "legacy"
    desktop, phone = Device(agreed, tmp_path, "desktop"), Device(agreed, tmp_path, "phone")
    base = LibraryStore.open(agreed)
    try:
        assert read_ledger(base).events[undone].value.after is None
        mine = desktop.accept(pay(WREN, KEY2, 1, sequence=10, chat=OTHER))
        theirs = phone.accept(pay(TAM, KEY1, 1, sequence=10, chat=THIRD))
        event, _ = desktop.effects.read(mine, requested_by="director")
        assert event.after == (kept.event_id,)  # the last of the legacy line, revoked one included before it
        sides = {"desktop": desktop.store, "phone": phone.store}
        result = merge(base, sides)
        assert isinstance(result, Merged), result
        write_back(desktop.store, file_bytes(result, base, sides))
        numbered = ordinals(desktop.store)
        assert numbered[undone.event_id] < numbered[kept.event_id] < min(numbered[mine.event_id],
                                                                          numbered[theirs.event_id])
        assert desktop.effects.continuity(C, as_of=100, requested_by="director").problems == ()
    finally:
        base.close()
        desktop.close()
        phone.close()


def test_a_causal_order_that_would_break_the_sequence_order_is_a_review(base_path, tmp_path):
    # desktop took back an event at sequence 12 and then accepted one at 8 after it (allowed: only active
    # events set the floor); the phone accepted one at 10 meanwhile. Causally 8 follows 12, so the merged
    # active events would run 10 then 8, which 021 calls inconsistent: the merge says so instead
    desktop, phone = Device(base_path, tmp_path, "desktop"), Device(base_path, tmp_path, "phone")
    base = LibraryStore.open(base_path)
    try:
        undone = desktop.accept(pay(WREN, KEY2, 1, sequence=12, chat=OTHER))
        desktop.revoke(undone)
        desktop.accept(pay(WREN, KEY2, 1, sequence=8, chat=OTHER, attempt="again"))
        phone.accept(pay(TAM, KEY1, 1, sequence=10, chat=THIRD))
        result = merge(base, {"desktop": desktop.store, "phone": phone.store})
        assert isinstance(result, Review) and set(result.reasons) == {"new_problem"}
        assert "inconsistent_ledger" in result.items[0].detail
    finally:
        base.close()
        desktop.close()
        phone.close()
