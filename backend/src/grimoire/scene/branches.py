"""How one chat's turns hang together across devices (028's turn merge): its line, its forks, what is set aside.

Two paired devices that play one chat offline each write its next turn; once sync brings both into one
library the chat is **forked** at that number. Every turn names the turn it was played after (``follows``), so
the turns form a tree, and the chat's line is read from the root: at each number the turns played after the
line's last turn are its candidates. One candidate: the line goes on. Several: a fork, which waits until
someone chooses which branch goes on; nothing is ever chosen here. A choice is a ``set_aside`` mark on every
other branch head of that fork, naming the chosen head, and sets aside that head and every turn played after
it (a turn arriving later from a third device included): kept on disk, heard by no one, outside the chat's
numbering. Marks that do not name exactly one unmarked candidate (two devices chose differently offline, or
a mark names a turn that is itself set aside or absent) choose nothing: the fork is a ``conflict``, every
branch shown, and play waits for a new choice. Pure: no store, no clock.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256

from .model import SceneRefused, TurnRecord

#: How many alternative tags one device may use for one turn number (see ``replay_tag``).
MAX_REPLAYS = 10_000


@dataclass(frozen=True)
class Branch:
    """One branch of a fork: its head (a turn of the fork's number), the device tag that played it (``None``
    untagged), every turn of it from the fork's number on (ids, in order), and whether a valid mark set it aside."""

    head: str
    device: str | None
    turns: tuple[str, ...]
    set_aside: bool


@dataclass(frozen=True)
class Fork:
    """Where the chat's line splits: the number, the turn every branch follows (``None`` at turn 1), and its
    branches in the chat's order. ``open``: no valid choice yet. ``conflict``: marks that choose nothing."""

    number: int
    after: str | None
    state: str
    branches: tuple[Branch, ...]

    def describe(self, mine: Callable[[str | None], bool] = lambda device: False) -> str:
        parts = []
        for branch in self.branches:
            who = branch.device or "untagged"
            notes = [f"{len(branch.turns)} turn{'' if len(branch.turns) == 1 else 's'}"]
            if mine(branch.device):
                notes.append("this device")
            if branch.set_aside:
                notes.append("set aside")
            parts.append(f"{branch.head} ({who}, from {self.number}: {', '.join(notes)})")
        return f"turn {self.number}{'' if self.state == 'open' else ' (choices conflict)'}: " + "; ".join(parts)


@dataclass(frozen=True)
class Lineage:
    """``line``: the chat's turns as play reads them (up to a fork, then every branch not set aside, in the
    chat's order). ``set_aside``: turns a valid choice set aside. ``fork``: the first fork, or ``None``.
    ``orphans``: turns whose predecessor is missing or unknown (shown, and the chat is incomplete)."""

    line: tuple[TurnRecord, ...]
    set_aside: tuple[TurnRecord, ...]
    fork: Fork | None
    orphans: tuple[TurnRecord, ...]


def order(record: TurnRecord) -> tuple[int, str]:
    """One order on every device: by number, then an untagged turn before tagged ones, then by device tag."""
    return record.number, record.device or ""


def parent(record: TurnRecord, by_number: dict[int, list[TurnRecord]]) -> str | None:
    """The id of the turn ``record`` was played after, or ``None`` (turn 1, or no way to tell). A turn written
    before ``follows`` existed is placed after the turn before it of its own device, else the untagged one,
    else the only one: a library could not fork before then except by hand, and nothing is guessed past that."""
    if record.follows is not None or record.number == 1:
        return record.follows
    before = by_number.get(record.number - 1, [])
    for wanted in (lambda turn: turn.device == record.device, lambda turn: turn.device is None):
        found = [turn for turn in before if wanted(turn)]
        if found:
            return found[0].turn_id
    return before[0].turn_id if len(before) == 1 else None


def arrange(records: Iterable[TurnRecord]) -> Lineage:
    """The chat's line, its first fork and what is set aside, from all of one chat's readable turns."""
    turns = sorted(records, key=order)
    by_id = {turn.turn_id: turn for turn in turns}
    by_number: dict[int, list[TurnRecord]] = {}
    for turn in turns:
        by_number.setdefault(turn.number, []).append(turn)
    children: dict[str | None, list[TurnRecord]] = {}
    orphans: list[TurnRecord] = []
    for turn in turns:
        above = parent(turn, by_number)
        if turn.number > 1 and above not in by_id:
            orphans.append(turn)  # its predecessor is missing or unknown: never placed on any branch
        else:
            children.setdefault(above, []).append(turn)

    def subtree(head: TurnRecord) -> list[TurnRecord]:
        found, stack = [], [head]
        while stack:
            turn = stack.pop()
            found.append(turn)
            stack.extend(children.get(turn.turn_id, ()))
        return sorted(found, key=order)

    line: list[TurnRecord] = []
    set_aside: list[TurnRecord] = []
    fork = None
    after: str | None = None
    while True:
        level = children.get(after, [])
        if not level:
            break
        unmarked = [turn for turn in level if turn.set_aside is None]
        marked = [turn for turn in level if turn.set_aside is not None]
        named = {turn.turn_id for turn in unmarked}
        valid = bool(unmarked) and all(turn.set_aside.chosen in named for turn in marked)  # type: ignore[union-attr]
        if valid:
            for turn in marked:
                set_aside.extend(subtree(turn))
        if valid and len(unmarked) == 1:
            line.append(unmarked[0])
            after = unmarked[0].turn_id
            continue
        state = "open" if valid else "conflict"
        heads = unmarked if valid else level
        fork = Fork(level[0].number, after, state, tuple(
            Branch(head.turn_id, head.device, tuple(turn.turn_id for turn in subtree(head)),
                   valid and head.set_aside is not None) for head in level))
        for head in heads:
            line.extend(subtree(head))
        break
    for turn in orphans:  # shown as they are, with what follows them, so nothing silently disappears
        line.extend(subtree(turn))
    seen: set[str] = set()
    shown = [turn for turn in sorted(line, key=order) if not (turn.turn_id in seen or seen.add(turn.turn_id))]
    return Lineage(tuple(shown), tuple(sorted(set_aside, key=order)), fork, tuple(orphans))


def replay_tag(tag: str | None, k: int) -> str:
    """The ``k``-th alternative tag (k >= 1) a device with ``tag`` (``None``: no identity) uses for a turn number it
    played already, on a branch now set aside: the first eight hex digits of the SHA-256 of ``<tag>.<k>``."""
    return sha256(f"{tag or ''}.{k}".encode()).hexdigest()[:8]


def tag_for(tag: str | None, taken: Iterable[str], spell: Callable[[str | None], str]) -> str | None:
    """The device tag a new turn takes: this device's own (``None``: untagged), unless that id is taken (by a turn
    set aside), then the first free ``replay_tag``. ``spell`` gives the turn id for a tag; ``taken`` are the chat's
    ids. Refused ``too_many_turns`` past ``MAX_REPLAYS``."""
    used = set(taken)
    if spell(tag) not in used:
        return tag
    for k in range(1, MAX_REPLAYS + 1):
        candidate = replay_tag(tag, k)
        if spell(candidate) not in used:
            return candidate
    raise SceneRefused("too_many_turns", f"this turn number was played on this device {MAX_REPLAYS} times")


def is_mine(tag: str | None, device: str | None, replays: int) -> bool:
    """Whether a branch tagged ``device`` was played by this device (``tag``): its own tag or one of its first
    ``replays`` alternative tags."""
    if device == tag:
        return True
    return device is not None and any(replay_tag(tag, k) == device for k in range(1, replays + 1))


__all__ = ["Branch", "Fork", "Lineage", "MAX_REPLAYS", "arrange", "is_mine", "order", "parent", "replay_tag",
           "tag_for"]
