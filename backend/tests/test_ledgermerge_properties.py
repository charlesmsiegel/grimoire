"""028's ledger merge over random offline play: symmetric, idempotent and never silently dropping.

Seeded random loops (the suite has no property-testing library): each trial plays a random mix of
payments, key handoffs, hand operations, hand edits and revocations on two copies of one synthetic
library, then merges. Whatever the merge says, both orders of the two sides must say it; a merge must
keep every event, relation, holder and operation either device had, leave a library 021 finds no
problem in, and come out unchanged when merged again.
"""

from __future__ import annotations

import random
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire.core import Moment
from grimoire.core.identity import AcceptedRef
from grimoire.effects import AcceptedEvent, AcceptedOperation, accepted_frontier
from grimoire.holdings import Line, Operation
from grimoire.ledgermerge import LedgerReview, Merged, Review, causal_order, device_tag
from grimoire.store import LibraryStore

from effects_fixtures import C, CHAT, INES, KEY1, KEY2, TAM, WREN, Handles, effects_library, give
from ledger_fixtures import COIN, OTHER, Device, build_base, file_bytes, merge, pay, read_ledger, write_back
from test_effects_model import event as model_event

TRIALS = 40
PEOPLE = (WREN, TAM, INES)


def play(device: Device, rng: random.Random, steps: int, home) -> None:
    """A random run of offline play; a step the device itself refuses is simply not taken.

    Each device mostly plays its own chat (``home``), so that most trials merge and some meet in one chat.
    """
    accepted: list[tuple[AcceptedRef, object]] = []
    sequence = 7
    for step in range(steps):
        sequence += rng.choice((0, 1, 1, 2))
        chat = home if rng.random() < 0.85 else rng.choice((CHAT, OTHER))
        roll = rng.random()
        try:
            if roll < 0.4:
                giver, to = rng.sample(PEOPLE, 2)
                proposal = pay(giver, to, rng.randint(1, 4), sequence=sequence, chat=chat)
                accepted.append((device.accept(proposal), proposal))
            elif roll < 0.55:
                item = rng.choice((KEY1, KEY2))
                holder, to = (WREN, rng.choice((TAM, INES)))
                proposal = give(item, holder, to, sequence=sequence, chat=chat, turn=f"t{sequence}",
                                summary=f"Wren hands over {item.instance_id}.")
                accepted.append((device.accept(proposal), proposal))
            elif roll < 0.8:
                device.hand_op(Operation(f"{device.name}-hand-{step}", (Line.add(rng.choice(PEOPLE), COIN,
                                                                                  rng.randint(1, 3)),)))
            elif roll < 0.86:
                device.hand_edit(rng.choice(PEOPLE), COIN, rng.randint(0, 20))
            elif accepted:
                ref, proposal = accepted.pop()
                device.revoke(ref, proposal)
        except (ValueError, KeyError):  # 021/023 refusing on the device (all ValueErrors), or no holder file yet
            continue


def inventory(store: LibraryStore) -> tuple[set, set, set, set]:
    """Every event, relation, holder and logged operation a library's ledger has."""
    ledger = read_ledger(store)
    ops = {(holder, entry.op) for holder, version in ledger.holdings.items() for entry in version.value.log}
    return set(ledger.events), set(ledger.relations), set(ledger.holdings), ops


def check_kept_everything(merged: Merged, base: LibraryStore, sides: dict[str, LibraryStore], tmp: Path) -> None:
    """Nothing either device had is missing from the merge, and the merged library reads clean."""
    shutil.copytree(sides["desktop"].root, tmp)
    target = LibraryStore.open(tmp)
    try:
        write_back(target, file_bytes(merged, base, sides))
        after = inventory(target)
        for store in (base, *sides.values()):
            for had, kept in zip(inventory(store), after, strict=True):
                assert had <= kept
        events = read_ledger(target).events
        assert sorted(v.value.ordinal for v in events.values()) == list(range(1, len(events) + 1))
        seen = effects_library(target, Handles()).continuity(C, as_of=10 ** 6, requested_by="director")
        assert seen.problems == (), seen.problems
    finally:
        target.close()


@pytest.mark.parametrize("seed", range(TRIALS))
def test_random_offline_play_merges_symmetrically_without_dropping_anything(seed, tmp_path):
    rng = random.Random(seed)
    base_path = build_base(tmp_path)
    devices = {name: Device(base_path, tmp_path, name) for name in ("desktop", "phone")}
    base = LibraryStore.open(base_path)
    try:
        for device, home in zip(devices.values(), (CHAT, OTHER), strict=True):
            play(device, rng, rng.randint(0, 8), home)
        sides = {name: device.store for name, device in devices.items()}
        flipped = dict(reversed(list(sides.items())))
        result = merge(base, sides)
        assert merge(base, flipped) == result  # symmetric: neither device's view of the merge differs

        candidate = result.candidate if isinstance(result, LedgerReview) else result
        if isinstance(result, Review):
            assert result.items  # a review always names why
        if not isinstance(candidate, Merged):
            return
        files = file_bytes(candidate, base, sides)
        assert file_bytes(candidate, base, flipped) == files
        check_kept_everything(candidate, base, sides, tmp_path / "check")
        if not isinstance(result, Merged):
            return

        # idempotent: both devices store the merge, and merging again changes nothing, also against a side
        # that has not yet taken the merge (the other device's copy before it synced)
        before = {}
        for name, store in sides.items():
            shutil.copytree(store.root, tmp_path / f"{name}-before")
            before[name] = LibraryStore.open(tmp_path / f"{name}-before")
        try:
            for device in devices.values():
                write_back(device.store, files)
            again = merge(base, sides)
            assert isinstance(again, Merged), again
            assert file_bytes(again, base, sides) == files and again.order == result.order
            for behind_name in sides:
                mixed = {name: before[name] if name == behind_name else sides[name] for name in sides}
                behind = merge(base, mixed)
                assert isinstance(behind, Merged), behind
                assert file_bytes(behind, base, mixed) == files
        finally:
            for store in before.values():
                store.close()
    finally:
        for device in devices.values():
            device.close()
        base.close()


# -- the merge order on its own: random acceptance histories --------------------------------------

def _history(rng: random.Random, size: int) -> dict[str, AcceptedEvent]:
    """Events as devices accept them: each after the frontier of what its device held, at a sequence never
    behind that device's own last one; devices now and then take in everything another holds (a merge)."""
    devices = ["a-tablet", "desktop", "phone"]
    held: dict[str, set[str]] = {device: set() for device in devices}
    floor = dict.fromkeys(devices, 1)
    counter = dict.fromkeys(devices, 0)
    events: dict[str, AcceptedEvent] = {}
    director = give(backed=None, agent=None, proposed_by="director")  # no endorsement pins the moment
    template = model_event(proposed_by="director", contributions=(), endorsements=(), controls=(),
                           operations=tuple(AcceptedOperation(op, "director") for op in director.operations))
    for _ in range(size):
        device = rng.choice(devices)
        if rng.random() < 0.3:
            other = rng.choice([d for d in devices if d != device])
            held[device] |= held[other]
            floor[device] = max(floor[device], floor[other])
            continue
        counter[device] += 1
        event_id = f"e-{counter[device]}-{device_tag(device)}"
        floor[device] += rng.choice((0, 0, 1, 2))
        mine = {event_id: events[event_id] for event_id in held[device]}
        after = accepted_frontier(mine.values())
        events[event_id] = replace(template, ref=AcceptedRef(C, event_id), moment=Moment(floor[device], "s1", "t1"),
                                   after=after)
        held[device].add(event_id)
    return events


def _closed(events: dict[str, AcceptedEvent], picked: set[str]) -> set[str]:
    found, stack = set(), list(picked)
    while stack:
        event_id = stack.pop()
        if event_id not in found:
            found.add(event_id)
            stack.extend(events[event_id].after or ())
    return found


@pytest.mark.parametrize("seed", range(60))
def test_the_merge_order_of_what_two_devices_hold_is_the_order_of_everything_restricted(seed):
    rng = random.Random(seed)
    events = _history(rng, rng.randint(1, 24))
    everything, stuck = causal_order(events)
    assert not stuck and sorted(everything) == sorted(events)
    place = {event_id: number for number, event_id in enumerate(everything)}
    for event in events.values():  # never before an event it was accepted after
        assert all(place[before] < place[event.event_id] for before in event.after or ())
    for _ in range(5):  # what any device (or two together) holds is closed under "after"
        subset = _closed(events, {event_id for event_id in events if rng.random() < 0.4})
        part, stuck = causal_order({event_id: events[event_id] for event_id in subset})
        assert not stuck and part == [event_id for event_id in everything if event_id in subset]
