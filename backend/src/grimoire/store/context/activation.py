"""World-info activation as a function of the transcript: per-post key
bitmaps, per-entry windows, key logic, and the sticky/cooldown machine.

Pure -- it reads no store and writes none. It is handed the posts the scan
window reads (`(transcript_index, text)`, director notes included) and the
turn's seed, and answers which entries the keys select and why.

## The bitmap is exact

Today's rule (`world_state.keyword_hit`) searches the window's posts joined by
newlines, plus the seed. This module searches each post once per key instead
and keeps the answers as a per-key bitmap, so a window at any depth and any
boundary is "any set bit in a range" -- which is what lets §5.3 replay every
boundary of a scene for the cost of one search per (key, post).

The two agree on every (keys, window), not approximately: a key is escaped and
single-line, so it cannot match across the newline that joins two posts, and
the newline itself is a non-word character exactly as the start or end of a
string is, so `\\b` decides the same at a post's edge whether a neighbour is
joined on or not. The surrounding `.strip()` only removes whitespace at the
very ends, which no `\\b`-anchored key can use.

## Slots and boundaries

Slot `i < len(posts)` is post `i`; when there is a seed it is the last slot.
A boundary `b` covers slots `[0, b)`, and the current turn is boundary `n`.
An entry's window at `b` is its last `depth` post slots before `b`, plus the
seed slot only at `b == n` -- the seed belongs to the current turn alone.

## The timed machine: age, then match

`timed_state` replays an entry's direct key rule at every boundary from the
start of the scene (a shorter replay is not exact: an activation just before
its start could still be sticky or cooling inside it). It starts `ready`, and
at each boundary it first AGES the state and only then considers a MATCH, so an
exhausted sticky cools before a new match is weighed:

- age: `active` with sticky carry left spends one; `active` with none cools
  for `cooldown` boundaries (or goes straight to `ready`); `cooling` entered
  earlier counts down and is `ready` at zero.
- match: only a `ready` entry with a direct match becomes `active`, carrying
  `sticky` more boundaries. A match while active does not refresh it, and a
  match while cooling is ignored.

Only a direct key match starts sticky, so the machine is a function of the
transcript and the entry alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from .. import lore_fields

_NO_CONTROLS = lore_fields.Controls()


def _compile(key: str) -> re.Pattern[str]:
    """`keyword_hit`'s pattern for one key, compiled once per index."""
    return re.compile(rf"\b{re.escape(key)}\b", re.IGNORECASE)


def controls(entry: dict) -> lore_fields.Controls:
    """An entry's controls; an entry that carries none has the defaults."""
    found = entry.get("controls")
    return found if isinstance(found, lore_fields.Controls) else _NO_CONTROLS


class KeyIndex:
    """Per-key, per-slot match bitmaps over one turn's posts and seed."""

    def __init__(self, posts: Sequence[tuple[int, str]], seed: str) -> None:
        self._indices = [i for i, _ in posts]
        self._texts = [t for _, t in posts] + ([seed] if seed else [])
        self._posts = len(posts)
        self._hits: dict[str, list[bool]] = {}

    @property
    def n(self) -> int:
        return len(self._texts)

    def hits(self, key: str) -> list[bool]:
        found = self._hits.get(key)
        if found is None:
            pattern = _compile(key)
            found = [pattern.search(t) is not None for t in self._texts]
            self._hits[key] = found
        return found

    def _window(self, upto: int, depth: int | None) -> list[int]:
        """The slots an entry reads at boundary `upto`, newest first."""
        end = min(upto, self._posts)
        start = 0 if depth is None else max(0, end - depth)
        slots = list(range(end - 1, start - 1, -1))
        if upto == self.n and self.n > self._posts:
            slots.insert(0, self._posts)
        return slots

    def newest(self, keys: Iterable[str], upto: int,
               depth: int | None) -> tuple[str, int] | None:
        """The newest `(key, slot)` hit in the window at boundary `upto`; on a
        slot more than one key hits, the first key in `keys` order."""
        keys = list(keys)
        for slot in self._window(upto, depth):
            for key in keys:
                if self.hits(key)[slot]:
                    return key, slot
        return None

    def post_index(self, slot: int) -> int | None:
        """The transcript index of a post slot; None for the seed slot."""
        return self._indices[slot] if slot < self._posts else None

    def age(self, slot: int) -> int:
        """How recent a slot is, on the transcript's scale: a post's own
        index, and for the seed one past the last post (0 with no posts)."""
        if slot < self._posts:
            return self._indices[slot]
        return self._indices[-1] + 1 if self._indices else 0


def _secondary(logic: str, keys: tuple[str, ...], idx: KeyIndex, upto: int,
               depth: int) -> tuple[bool, str | None]:
    """Does the secondary rule pass, and which secondary key does it cite?"""
    found = [hit for k in keys if (hit := idx.newest([k], upto, depth))]
    newest = max(found, key=lambda h: h[1])[0] if found else None
    if logic == "and_all":
        return len(found) == len(keys), newest
    if logic == "not_any":
        return not found, None
    if logic == "not_all":
        return len(found) < len(keys), None
    return bool(found), newest  # and_any


def direct_match(entry: dict, idx: KeyIndex, upto: int, depth: int) -> dict | None:
    """The entry's key rule against its window at boundary `upto`:
    `{"key", "secondary", "slot"}` for the newest primary hit, or None. A
    keyless entry is not a key match (it is always-on, which is the caller's
    rule). `depth` is the global scan depth; the entry's own overrides it."""
    keys = entry.get("keys") or []
    if not keys:
        return None
    c = controls(entry)
    d = depth if c.scan_depth is None else c.scan_depth
    primary = idx.newest(keys, upto, d)
    if primary is None:
        return None
    secondary = None
    if c.secondary_keys:
        ok, secondary = _secondary(c.key_logic, c.secondary_keys, idx, upto, d)
        if not ok:
            return None
    return {"key": primary[0], "secondary": secondary, "slot": primary[1]}


def timed_state(entry: dict, idx: KeyIndex, depth: int) -> dict:
    """The sticky/cooldown machine's state at the current boundary, replayed
    from the scene's first post (§5.3). `remaining` counts boundaries,
    including the current one: still active, or still blocked."""
    c = controls(entry)
    state, carry, cd, from_slot, fresh = "ready", 0, 0, None, False
    if not entry.get("keys"):
        return _report(state, fresh, from_slot, 0)
    for b in range(1, idx.n + 1):
        fresh = False
        # Age first, so an exhausted active state cools before a match counts.
        if state == "active" and carry > 0:
            carry -= 1
        elif state == "active":
            state, cd, from_slot = "cooling" if c.cooldown else "ready", c.cooldown, None
        elif state == "cooling":
            cd -= 1
            if cd == 0:
                state = "ready"
        if state == "ready":
            hit = direct_match(entry, idx, b, depth)
            if hit is not None:
                state, carry, from_slot, fresh = "active", c.sticky, hit["slot"], True
    remaining = carry + 1 if state == "active" else cd if state == "cooling" else 0
    return _report(state, fresh, from_slot, remaining)


def _report(state: str, fresh: bool, from_slot: int | None, remaining: int) -> dict:
    return {"state": state, "fresh": fresh, "from_slot": from_slot,
            "remaining": remaining}
