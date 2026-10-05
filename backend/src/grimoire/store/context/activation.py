"""World-info activation as a function of the transcript: per-post key
bitmaps, per-entry windows, key logic, and the sticky/cooldown machine.

Pure -- it reads no store and writes none. It is handed the posts the scan
window reads (`(transcript_index, text)`, director notes included) and the
turn's seed, and answers which entries the keys select and why.

## The bitmap is exact

Today's rule (`world_state.keyword_hit`) searches the window's posts joined by
newlines, plus the seed. This module searches each post once per key instead
and keeps the answers per key and slot, so a window at any depth and any
boundary is "any hit in a range". The searches are lazy (`KeyIndex`): an
untimed entry pays for its window and no more, as it did under the joined
text, and only a timed entry -- which §5.3 replays at every boundary of the
scene -- has its keys searched in every post, once, for all the boundaries.

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

A seed is not a turn of its own: it is the current turn's text (an opener's
prompt, a director's note), added to the posts the turn already reads. So with
a seed the seeded boundary `n` REPLACES boundary `P = len(posts)` rather than
following it -- the replay is `1..P-1` and then `n` (`KeyIndex.boundaries`).
Replaying both would count the newest post's match twice and age every timed
entry one step too far.

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

## One turn, in levels (`run`)

The gates keep `world_state.activate`'s order and its guarantees: `gm-only` is
dropped first (a pin included), an excluded ref next (and never offered to
recall), then a pin activates with no key or owner having to agree -- and a
pin beats a cooldown for the same reason it beats the owner gate. Everything
else passes the owner gate against the present set, then its keys.

Level 0 decides every candidate against `structural_presence` (§7.1): the
keys against its window, through the timed machine when it has one, so the
current state is a key match, a sticky carry, a cooldown (held back, and then
unreachable by any path but a pin -- once its owner gate is open, since an
absent owner's lore is not even listed) or nothing. Each later level first makes
the items, groups and creatures the level before activated present (§7.2) and
re-closes the structural fixed point over the grown set -- an activated group
makes the item it holds present too -- then re-checks what has not activated:
an entry whose owner just arrived is decided on its window, and up to
`recursion_depth` levels an entry may also be pulled by the bodies of the level
before. There are at most
`recursion_depth + 1` such levels, so the extra one that lets an activated
group unlock its owned lore runs even with recursion off.

A gm-only or excluded record confers no presence, structural or activated:
it is not in the prompt by any path, so it may not open a gate for one either.
A negative `recursion_depth` reads as 0.

Recall comes last, over the keyed entries nothing activated or held back whose
owner gate the final present set passes. Its hits are terminal: no presence,
no pulling.

A hit's reason is JSON-safe, for the inspector and never for a prompt. Its age
is §6's: the transcript index of the newest matching post, one past the last
post for the seed, the post that started a sticky carry, and -1 for anything
with no match of its own (keyless, a pin, recursion, an owner arriving late).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from .. import entities, lore_fields

_NO_CONTROLS = lore_fields.Controls()
# §7.1's rules, per kind, in the order they are tried: (structural field,
# presence reason, whether the field is read against the present set or
# against the current location). These are also the kinds whose own entry can
# be present by activating (§7.2): a character or a location is present by
# being in the scene, never by being mentioned.
_STRUCTURAL: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "items": (("holder", "held_by", True),),
    "groups": (("leader", "led_by", True), ("headquarters", "headquarters", False)),
    "creatures": (("habitat", "habitat", False),),
}
_ACTIVATED = {"type": "activated", "via": None}


def _compile(key: str) -> re.Pattern[str]:
    """`keyword_hit`'s pattern for one key, compiled once per index."""
    return re.compile(rf"\b{re.escape(key)}\b", re.IGNORECASE)


def controls(entry: dict) -> lore_fields.Controls:
    """An entry's controls; an entry that carries none has the defaults."""
    found = entry.get("controls")
    return found if isinstance(found, lore_fields.Controls) else _NO_CONTROLS


class KeyIndex:
    """Per-key, per-slot matches over one turn's posts and seed, searched
    lazily: a `(key, slot)` is searched the first time anything reads it and
    never again.

    An untimed entry reads only its window, so it costs at most one search
    per key per slot of that window -- a long scene costs what a short one
    does. A timed entry replays every boundary of the scene (§5.3), so its
    keys are searched in every slot once (`hits`), and each boundary's window
    is then answered from per-key `last` tables rather than walked again."""

    def __init__(self, posts: Sequence[tuple[int, str]], seed: str) -> None:
        self._indices = [i for i, _ in posts]
        self._texts = [t for _, t in posts] + ([seed] if seed else [])
        self._posts = len(posts)
        self._patterns: dict[str, re.Pattern[str]] = {}
        self._slots: dict[str, list[bool | None]] = {}
        self._hits: dict[str, list[bool]] = {}
        # `_last[key][e]` is the newest post slot before `e` the key hits, or -1.
        self._last: dict[str, list[int]] = {}

    @property
    def n(self) -> int:
        return len(self._texts)

    def hit(self, key: str, slot: int) -> bool:
        """Does `key` match the text in `slot`? Searched once, then memoised."""
        memo = self._slots.get(key)
        if memo is None:
            memo = self._slots[key] = [None] * self.n
        found = memo[slot]
        if found is None:
            pattern = self._patterns.get(key)
            if pattern is None:
                pattern = self._patterns[key] = _compile(key)
            found = memo[slot] = pattern.search(self._texts[slot]) is not None
        return found

    def hits(self, key: str) -> list[bool]:
        """The key's whole bitmap, every slot searched (once, shared by every
        entry with the key), with its `last` table beside it."""
        found = self._hits.get(key)
        if found is None:
            found = self._hits[key] = [self.hit(key, s) for s in range(self.n)]
            last, newest = [-1], -1
            for slot in range(self._posts):
                if found[slot]:
                    newest = slot
                last.append(newest)
            self._last[key] = last
        return found

    def _bounds(self, upto: int, depth: int | None) -> tuple[int, int, bool]:
        """The window at boundary `upto`: post slots `[start, end)`, and
        whether the seed slot is read too."""
        end = min(upto, self._posts)
        start = 0 if depth is None else max(0, end - depth)
        return start, end, upto == self.n and self.n > self._posts

    def newest(self, keys: Iterable[str], upto: int, depth: int | None, *,
               full: bool = False) -> tuple[str, int] | None:
        """The newest `(key, slot)` hit in the window at boundary `upto`; on a
        slot more than one key hits, the first key in `keys` order.

        By default this walks the window newest first, searching only what it
        reads. `full` reads each key's whole bitmap instead, which a timed
        entry has paid for anyway: then a window costs O(1) per key."""
        keys = list(keys)
        start, end, seeded = self._bounds(upto, depth)
        if full:
            best: tuple[str, int] | None = None
            for key in keys:
                self.hits(key)
                slot = self._posts if seeded and self.hit(key, self._posts) else -1
                if slot < 0 and self._last[key][end] >= start:
                    slot = self._last[key][end]
                if slot >= 0 and (best is None or slot > best[1]):
                    best = key, slot
            return best
        slots = range(end - 1, start - 1, -1)
        for slot in ([self._posts, *slots] if seeded else slots):
            for key in keys:
                if self.hit(key, slot):
                    return key, slot
        return None

    def boundaries(self) -> list[int]:
        """The boundaries a replay visits, in order, ending at the current one.
        With a seed, the seeded boundary `n` stands in for boundary `P`: both
        read the same posts, and the seed only adds to the current turn."""
        if self.n > self._posts:
            return [*range(1, self._posts), self.n]
        return list(range(1, self.n + 1))

    def post_index(self, slot: int) -> int | None:
        """The transcript index of a post slot; None for the seed slot."""
        return self._indices[slot] if slot < self._posts else None

    def age(self, slot: int) -> int:
        """How recent a slot is, on the transcript's scale: a post's own
        index, and for the seed one past the last post (0 with no posts)."""
        if slot < self._posts:
            return self._indices[slot]
        return self._indices[-1] + 1 if self._indices else 0


def _logic_ok(logic: str, found: int, total: int) -> bool:
    """The secondary rule, given how many of `total` secondary keys hit."""
    if logic == "and_all":
        return found == total
    if logic == "not_any":
        return found == 0
    if logic == "not_all":
        return found < total
    return found > 0  # and_any


def _secondary(logic: str, keys: tuple[str, ...], idx: KeyIndex, upto: int,
               depth: int, full: bool) -> tuple[bool, str | None]:
    """Does the secondary rule pass, and which secondary key does it cite?"""
    found = [hit for k in keys if (hit := idx.newest([k], upto, depth, full=full))]
    newest = max(found, key=lambda h: h[1])[0] if found else None
    cites = logic in ("and_any", "and_all")
    return _logic_ok(logic, len(found), len(keys)), newest if cites else None


def direct_match(entry: dict, idx: KeyIndex, upto: int, depth: int, *,
                 full: bool = False) -> dict | None:
    """The entry's key rule against its window at boundary `upto`:
    `{"key", "secondary", "slot"}` for the newest primary hit, or None. A
    keyless entry is not a key match (it is always-on, which is the caller's
    rule). `depth` is the global scan depth; the entry's own overrides it.
    `full` is `KeyIndex.newest`'s: the same answer, read from whole-scene
    bitmaps -- what a replay over every boundary wants."""
    keys = entry.get("keys") or []
    if not keys:
        return None
    c = controls(entry)
    d = depth if c.scan_depth is None else c.scan_depth
    primary = idx.newest(keys, upto, d, full=full)
    if primary is None:
        return None
    secondary = None
    if c.secondary_keys:
        ok, secondary = _secondary(c.key_logic, c.secondary_keys, idx, upto, d, full)
        if not ok:
            return None
    return {"key": primary[0], "secondary": secondary, "slot": primary[1]}


def timed_state(entry: dict, idx: KeyIndex, depth: int) -> dict:
    """The sticky/cooldown machine's state at the current boundary, replayed
    from the scene's first post (§5.3) over `idx.boundaries()`. `remaining`
    counts boundaries, including the current one: still active, or still
    blocked."""
    c = controls(entry)
    state, carry, cd, from_slot, fresh = "ready", 0, 0, None, False
    if not entry.get("keys"):
        return _report(state, fresh, from_slot, 0)
    for b in idx.boundaries():
        fresh = False
        # Age first, so an exhausted active state cools before a match counts.
        if state == "active" and carry > 0:
            carry -= 1
        elif state == "active":
            state, cd, from_slot = "cooling" if c.cooldown > 0 else "ready", c.cooldown, None
        elif state == "cooling":
            cd -= 1
            if cd <= 0:
                state = "ready"
        if state == "ready":
            hit = direct_match(entry, idx, b, depth, full=True)
            if hit is not None:
                state, carry, from_slot, fresh = "active", c.sticky, hit["slot"], True
    remaining = carry + 1 if state == "active" else cd if state == "cooling" else 0
    return _report(state, fresh, from_slot, remaining)


def _report(state: str, fresh: bool, from_slot: int | None, remaining: int) -> dict:
    return {"state": state, "fresh": fresh, "from_slot": from_slot,
            "remaining": remaining}


# --- one turn, in levels (§5.2, §7) ------------------------------------------

@dataclass(frozen=True)
class Hit:
    ref: str
    entry: dict
    level: int
    reason: dict
    direct: bool
    age: int


@dataclass(frozen=True)
class Held:
    ref: str
    entry: dict
    reason: dict


@dataclass(frozen=True)
class Result:
    keyword: list[Hit]
    recalled: list[Hit]
    held_back: list[Held]
    present: dict[str, dict]


def _ref(entry: dict) -> str:
    """An entry's ref, spelled as `world_state._ref` and `store/pins.py` do."""
    return f"{entry.get('kind')}:{entry.get('id')}"


def _structural_refs(entry: dict, field: str) -> list[str]:
    """The refs an entry names in one structural field; anything malformed is
    none, since these come from hand-editable frontmatter."""
    refs = entry.get("refs")
    found = refs.get(field) if isinstance(refs, Mapping) else None
    if not isinstance(found, (list, tuple)):
        return []
    return [r for r in found if isinstance(r, str)]


def _structural_reason(entry: dict, present: Mapping[str, dict],
                       here: str | None) -> dict | None:
    """Why the entry's own ref is present by §7.1's rules, or None."""
    for field, why, reads_present in _STRUCTURAL.get(str(entry.get("kind")), ()):
        for ref in _structural_refs(entry, field):
            if ref in present if reads_present else ref == here:
                return {"type": why, "via": ref}
    return None


def structural_presence(entries: list[dict], base: Mapping[str, dict],
                        current_location: str | None) -> dict[str, dict]:
    """`base` grown by what it structurally implies, to a fixed point: an item
    whose holder is present, a group whose leader is present or whose
    headquarters is here, a creature whose habitat is here.

    Each pass reads the set as the previous pass left it, so the result does
    not depend on the order of `entries`: a ref is added with the first rule
    (in its entry's field order) that the previous pass satisfies. A reason
    already in the set is never replaced. Each pass adds a ref or stops, so
    this ends within as many passes as there are entries.
    """
    present = dict(base)
    here = f"locations:{current_location}" if current_location else None
    while True:
        before = dict(present)
        added: dict[str, dict] = {}
        for entry in entries:
            ref = _ref(entry)
            if ref in present or ref in added:
                continue
            reason = _structural_reason(entry, before, here)
            if reason is not None:
                added[ref] = reason
        if not added:
            return present
        present.update(added)


def _owner_gate(entry: dict, present: Mapping[str, dict]) -> dict | None:
    """None when the gate is shut; otherwise what the reason gains -- nothing
    for an unowned entry, else the first present owner in the entry's order."""
    owners = entry.get("owners") or []
    if not owners:
        return {}
    for owner in owners:
        if owner in present:
            return {"owner": owner, "owner_presence": dict(present[owner])}
    return None


def _window_hit(entry: dict, idx: KeyIndex, depth: int) -> tuple[dict, int] | None:
    """The entry against its own window at the current boundary: `(reason,
    age)`, a `cooldown` reason (age unused), or None when it does not fire.
    Keyless is always on and never timed."""
    if not entry.get("keys"):
        return {"type": "keyless"}, -1
    if controls(entry).timed():
        state = timed_state(entry, idx, depth)
        if state["state"] == "cooling":
            return {"type": "cooldown", "remaining": state["remaining"]}, -1
        if state["state"] != "active":
            return None
        if not state["fresh"]:
            slot = state["from_slot"]
            return ({"type": "sticky", "from_post": idx.post_index(slot),
                     "remaining": state["remaining"]}, idx.age(slot))
    hit = direct_match(entry, idx, idx.n, depth, full=controls(entry).timed())
    if hit is None:
        return None
    post = idx.post_index(hit["slot"])
    return ({"type": "key", "key": hit["key"], "secondary": hit["secondary"],
             "post": post, "seed": post is None}, idx.age(hit["slot"]))


class _Bodies:
    """Key searches over the bodies of activated entries, memoised per
    (puller, key) so a recursion level costs at most one search for each."""

    def __init__(self) -> None:
        self._patterns: dict[str, re.Pattern[str]] = {}
        self._hits: dict[tuple[int, str], bool] = {}

    def hit(self, pos: int, body: str, key: str) -> bool:
        found = self._hits.get((pos, key))
        if found is None:
            pattern = self._patterns.get(key)
            if pattern is None:
                pattern = self._patterns[key] = _compile(key)
            found = self._hits[(pos, key)] = pattern.search(body) is not None
        return found

    def pulls(self, entry: dict, pos: int, body: str) -> str | None:
        """The primary key by which `body` pulls `entry`, under its key logic
        evaluated against that body alone; None when it does not."""
        primary = next((k for k in entry.get("keys") or [] if self.hit(pos, body, k)), None)
        if primary is None:
            return None
        c = controls(entry)
        if c.secondary_keys:
            found = sum(self.hit(pos, body, k) for k in c.secondary_keys)
            if not _logic_ok(c.key_logic, found, len(c.secondary_keys)):
                return None
        return primary


class _Turn:
    """`run`'s working state: the hits and holds so far, the growing present
    set, and each entry's window decision, made at most once. `scene` is what
    structural presence re-closes over, which may be wider than `entries`."""

    def __init__(self, entries: list[dict], idx: KeyIndex, depth: int,
                 present: dict[str, dict], scene: list[dict],
                 current_location: str | None) -> None:
        self.entries = entries
        self.idx = idx
        self.depth = depth
        self.present = present
        self.scene = scene
        self.here = current_location
        self.bodies = _Bodies()
        self.hits: dict[int, Hit] = {}
        self.held: dict[int, Held] = {}
        self._windows: dict[int, tuple[dict, int] | None] = {}

    def window(self, pos: int) -> tuple[dict, int] | None:
        if pos not in self._windows:
            self._windows[pos] = _window_hit(self.entries[pos], self.idx, self.depth)
        return self._windows[pos]

    def cooling(self, pos: int) -> bool:
        """Is the entry cooling? If so it is held back, here and for good."""
        found = self.window(pos)
        if found is None or found[0]["type"] != "cooldown":
            return False
        self.held[pos] = Held(_ref(self.entries[pos]), self.entries[pos], found[0])
        return True

    def make_present(self, positions: Iterable[int]) -> None:
        """Activated items, groups and creatures are present (§7.2), and so is
        whatever they structurally imply: the fixed point is re-closed over the
        grown set, so an activated group makes the item it holds present too."""
        grew = False
        for pos in positions:
            ref = self.hits[pos].ref
            if str(self.entries[pos].get("kind")) in _STRUCTURAL and ref not in self.present:
                self.present[ref] = dict(_ACTIVATED)
                grew = True
        if grew:
            self.present = structural_presence(self.scene, self.present, self.here)

    def decide(self, pos: int, level: int, pullers: list[int]) -> bool:
        """Activate the entry at `level` if it may; True when it did."""
        entry = self.entries[pos]
        gate = _owner_gate(entry, self.present)
        if gate is None or self.cooling(pos):
            return False  # owned with no owner here (never leak), or held back
        found = self.window(pos)
        if found is not None:
            reason, age = found
            direct = level == 0
            self.hits[pos] = Hit(_ref(entry), entry, level, {**reason, **gate}, direct,
                                 age if direct else -1)
            return True
        if not entry.get("keys") or not controls(entry).pulled_ok():
            return False
        for puller in pullers:
            body = str(self.entries[puller].get("body") or "")
            key = self.bodies.pulls(entry, puller, body)
            if key is not None:
                reason = {"type": "recursion", "via": _ref(self.entries[puller]), "key": key}
                self.hits[pos] = Hit(_ref(entry), entry, level, {**reason, **gate}, False, -1)
                return True
        return False

    def levels(self, pending: list[int], recursion_depth: int) -> list[int]:
        """Run levels 0 to `recursion_depth + 1`; the entries left pending."""
        last = sorted(self.hits)  # the pins, activated at level 0
        for level in range(recursion_depth + 2):
            if level:
                if not last:
                    break
                self.make_present(last)
            pullers = ([p for p in last if controls(self.entries[p]).pulls_ok()]
                       if 1 <= level <= recursion_depth else [])
            added = [p for p in pending if self.decide(p, level, pullers)]
            last = sorted(last + added) if level == 0 else added
            pending = [p for p in pending if p not in self.hits and p not in self.held]
        self.make_present(last)
        return pending

    def recall(self, pending: list[int], recall: Callable[[list[dict], str],
               list[tuple[dict, float]]] | None, text: str) -> list[Hit]:
        """Recall over the keyed entries still pending whose owner gate the
        final present set opens. Terminal: its hits change nothing else."""
        gates: dict[int, dict] = {}
        for pos in pending:
            gate = _owner_gate(self.entries[pos], self.present)
            if gate is not None and self.entries[pos].get("keys") and not self.cooling(pos):
                gates[pos] = gate
        if recall is None or not gates:
            return []
        by_id = {id(self.entries[p]): p for p in gates}
        out = []
        for entry, score in recall([self.entries[p] for p in gates], text):
            at = by_id.get(id(entry))
            if at is not None:
                reason = {"type": "recall", "score": float(score), **gates[at]}
                out.append(Hit(_ref(entry), entry, 0, reason, False, -1))
        return out


def run(entries: list[dict], posts: Sequence[tuple[int, str]], seed: str,
        base_present: Mapping[str, dict], *, scan_depth: int, recursion_depth: int,
        current_location: str | None, pinned_refs: frozenset = frozenset(),
        excluded_refs: frozenset = frozenset(),
        recall: Callable[[list[dict], str], list[tuple[dict, float]]] | None = None,
        recall_text: str = "", scene_entries: list[dict] | None = None) -> Result:
    """One turn's world-info activation (§5.2), with every hit's reason.

    `entries` are the candidates: what may activate, pull, become present by
    activating or take a recall slot. `scene_entries`, when given, is the
    wider set structural presence reads -- an NPC's call activates only what
    that NPC knows, but what is in the room is the scene's (§8.1), so an item
    held by someone present is present whether or not this NPC knows of it.
    None reads `entries` for both.

    `keyword` is in input order whatever level an entry came from, so prompt
    bytes never depend on discovery order; `recalled` is in the order `recall`
    answered. See the module docstring for the levels.
    """
    # gm-only and excluded records are not in the prompt by any path, so they
    # confer no presence either: they are left out of the structural fixed
    # point here, and never activate, so never become present by activating.
    def shows(entry: dict) -> bool:
        return (entities.normalize_secrecy(entry.get("secrecy")) != entities.GM_ONLY
                and _ref(entry) not in excluded_refs)

    visible = [pos for pos, entry in enumerate(entries) if shows(entry)]
    scene = [e for e in (entries if scene_entries is None else scene_entries) if shows(e)]
    turn = _Turn(entries, KeyIndex(posts, seed), scan_depth,
                 structural_presence(scene, base_present, current_location),
                 scene, current_location)
    pending: list[int] = []
    for pos in visible:
        entry = entries[pos]
        ref = _ref(entry)
        if ref in pinned_refs:
            turn.hits[pos] = Hit(ref, entry, 0, {"type": "pinned"}, True, -1)
        else:
            pending.append(pos)
    pending = turn.levels(pending, max(0, recursion_depth))
    recalled = turn.recall(pending, recall, recall_text)
    return Result(keyword=[turn.hits[p] for p in sorted(turn.hits)], recalled=recalled,
                  held_back=[turn.held[p] for p in sorted(turn.held)],
                  present=turn.present)
