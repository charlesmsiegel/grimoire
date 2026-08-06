"""Transient per-turn NPC state: the ledger, its grammar, and the streaming
redactor that keeps the grammar off the player's screen (#120).

The model ends a reply with a fenced ``state`` block naming, per character it
voiced, a `mood` / `intent` / `posture` in a few words. `routes.streaming`
strips that block before the reply is split into posts, so it never enters a
transcript, and records what it said here. Provenance is the ``(sid, post
index)`` key; decay is `current()`'s window; `streaks()` is what #121 promotes
from.

Three things live here and nothing else does:

- **the grammar** (`split_block`, `StreamRedactor`) -- one definition, used by
  the persist path and by the stream so the two cannot disagree about what a
  tracker block is;
- **the ledger** at ``<campaign>/turnstate.json``, whose every read is
  defensive: a hand-edited or truncated file must omit the prompt section, not
  take a scene's generation down with it;
- **the two projections** the rest of the app asks for --- `current()` for the
  prompt, `streaks()` for promotion.

Deliberately ignorant of `scenes`: callers pass the transcript length in as
`tail`. That keeps this module out of the scene package's import
neighbourhood, and it is what lets `scenes.lifecycle` call `drop_scene` and
`scene_refs` call `repoint_scenes` without a cycle.

Mutators serialize on `locks.campaign_lock(cid)`: turnstate.json is rewritten
whole, so two unlocked read-modify-writes lose one of them. The lock is
reentrant, so `_persist_reply` holding it already costs nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import atomic, locks
from .campaigns import paths as campaigns_paths

#: The tracked fields, in prompt order. A key outside this tuple is dropped
#: rather than stored: the ledger feeds a prompt section and a promotion rule,
#: and neither can say anything useful about a field nobody declared.
FIELDS = ("mood", "intent", "posture")

#: Longest value kept. #121's implementation note asks for short values so
#: streaks are detectable at all; this is also what stops a model that decides
#: to narrate inside the block from quietly doubling the prompt section. Over
#: the cap the value is DROPPED, not truncated -- truncating manufactures
#: streak matches between two values that merely start alike.
MAX_VALUE = 80

#: Entries kept per scene, oldest dropped first. turnstate.json is
#: per-CAMPAIGN and is re-read and rewritten on every persisted turn, so a
#: scene's contribution to it has to be bounded by something — otherwise a long
#: campaign pays for every mood it has ever recorded on every subsequent turn.
#: Both readers only ever want the tail (`current` a window back from it,
#: `streaks` the final run), so dropping the oldest costs neither anything a
#: sane `turnstate_depth` or `promote_streak` could reach. Transient state is
#: the one store here where forgetting the distant past is the design, not a
#: compromise.
MAX_ENTRIES = 200

# The block, as a trailing fence only. `(?:^|\n)` rather than `re.MULTILINE`'s
# `^` so the newline that precedes the opener is eaten with it, leaving the
# narration without a dangling blank line.
_OPEN = re.compile(r"(?:^|\n)[ \t]*```[ \t]*state[ \t]*(?:\n|$)", re.IGNORECASE)
_CLOSE = re.compile(r"(?:^|\n)[ \t]*```[ \t]*(?:\n|$)")


def _norm(value: str) -> str:
    """The comparison form. Whitespace-collapsed, case-folded, and stripped of
    trailing sentence punctuation, because "Guarded." and "guarded" are the same
    mood written twice and a streak that breaks on a full stop never promotes.
    The STORED value keeps its original spelling -- this form is only ever
    compared, never written into a prompt or an edit."""
    return " ".join(value.split()).casefold().strip(" .,;:!?")


def _clean(fields) -> dict[str, str]:
    """One character's fields, keeping only declared keys with short, non-empty
    string values."""
    if not isinstance(fields, dict):
        return {}
    out = {}
    for f in FIELDS:
        v = fields.get(f)
        if not isinstance(v, str):
            continue
        v = " ".join(v.split())
        if v and len(v) <= MAX_VALUE:
            out[f] = v
    return out


def parse_block(body: str) -> dict[str, dict[str, str]]:
    """A block body as ``{character name: {field: value}}``.

    Tolerant in exactly one direction: anything unrecognized is dropped and the
    rest is kept. A malformed block is the model failing at bookkeeping in the
    middle of a reply that is otherwise fine, and the reply is the artifact
    worth keeping.

    Both the bare mapping and #120's ``{"state": {...}}`` envelope are accepted
    --- the instruction asks for the bare one, and a model that wraps it has
    still said what it meant.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if set(data) == {"state"} and isinstance(data["state"], dict):
        data = data["state"]
    out: dict[str, dict[str, str]] = {}
    for name, fields in data.items():
        if not isinstance(name, str) or not name.strip():
            continue
        got = _clean(fields)
        if got:
            out[name.strip()] = got
    return out


def split_block(text: str) -> tuple[str, dict[str, dict[str, str]]]:
    """``(narration, states)`` --- the reply with its trailing tracker block
    removed, and what the block said.

    **Only a trailing block is stripped.** A ``state`` fence with narration
    after it is left exactly where it is: deleting text from the middle of a
    reply is the one failure here that loses story, and a block in the wrong
    place is a misbehaving model the user should be able to see.

    A trailing *unterminated* opener is stripped with no data. That is what a
    turn cut off mid-block looks like, and by construction there is no
    narration after it to lose.
    """
    text = text or ""
    matches = list(_OPEN.finditer(text))
    if not matches:
        return text, {}
    m = matches[-1]
    rest = text[m.end():]
    close = _CLOSE.search(rest)
    if close is None:
        return text[:m.start()], {}
    if rest[close.end():].strip():
        return text, {}                      # not trailing: leave it alone
    return text[:m.start()], parse_block(rest[:close.start()])


def resolve(states: dict[str, dict[str, str]], cast: list[dict]) -> dict[str, dict[str, str]]:
    """Character names as the model wrote them -> ``characters:<id>`` tokens.

    Matched against the scene cast's display name, case-insensitively. The
    model is asked for the name it labels dialogue with, which is the name the
    character-description section showed it; asking for a store id instead
    would mean listing ids in the play prompt for it to mistype.

    NPCs only, and `characters` only --- the same scope `playstate` declares and
    `context.world_state._character_states` renders. A player's mood is not the
    narrator's to track.

    Two present NPCs sharing a display name collapse onto the FIRST in cast
    order (`scene_cast` sorts by kind then id, so the choice is at least
    stable). Not a resolvable case: the model labels their dialogue with the
    same string too, so the block it writes is ambiguous at the source.
    """
    by_name: dict[str, str] = {}
    for a in cast:
        if a.get("role") != "npc" or a.get("kind") != "characters":
            continue
        name = str(a.get("name") or "").strip().casefold()
        if name:
            by_name.setdefault(name, f"characters:{a['id']}")
    out: dict[str, dict[str, str]] = {}
    for name, fields in states.items():
        token = by_name.get(name.strip().casefold())
        if token:
            out.setdefault(token, {}).update(fields)
    return out


# ---- the stream ------------------------------------------------------------
# `_persist_reply` strips the block from what is STORED, but the deltas have
# already reached the browser by then. This is the display half.
#
# Composed OUTSIDE `fence.FenceWatcher` rather than folded into it. That class
# decides whether a roll proposal exists, its holdback is load-bearing for the
# proposal-before-narration guarantee, and it has no business also knowing
# about trackers. Feeding its output through this leaves `watcher.narration`
# --- and therefore the whole persistence path --- untouched.

_MAYBE_OPENER = re.compile(r"`{1,2}\Z|```[ \t]*(?:s(?:t(?:a(?:t(?:e)?)?)?)?)?\Z", re.IGNORECASE)
_IS_OPENER = re.compile(r"```[ \t]*state\b", re.IGNORECASE)


def _verdict(s: str) -> str:
    """Whether `s` (which starts with a backtick) opens a tracker block, could
    still grow into one, or cannot.

    An opener sitting exactly at the buffer end is "maybe", not "yes", for the
    reason `fence.feed` defers there too: its ``\\b`` was satisfied only by
    end-of-string, and the next delta could turn ``` ```state ``` into
    ``` ```statement ```.

    Deliberately looser than `_OPEN`, which additionally requires the info
    string to end the line: ``` ```state: ``` says "open" here and is not a
    block there. Over-triggering costs a pause; under-triggering leaks the
    block onto the screen. `finish` reconciles the difference by putting what
    was withheld through `split_block` itself, so the pause is all it ever
    costs.
    """
    m = _IS_OPENER.match(s)
    if m and m.end() < len(s):
        return "open"
    return "maybe" if _MAYBE_OPENER.fullmatch(s) else "no"


class StreamRedactor:
    """Withhold a tracker block from the deltas the client sees.

    Buffers from any backtick, releases the buffer the moment it cannot become
    an opener, and holds everything from an opener onward until `finish` can
    decide — with `split_block`, the same rule the stored reply is judged by —
    whether it really was one. Pure: it never looks at what was persisted.
    """

    def __init__(self) -> None:
        self._held = ""            # a prefix that could still grow into an opener
        self._tail: str | None = None   # everything from an opener on, or None

    def feed(self, chunk: str) -> str:
        if self._tail is not None:
            self._tail += chunk or ""
            return ""
        out = ""
        buf = self._held + (chunk or "")
        self._held = ""
        while buf:
            tick = buf.find("`")
            if tick < 0:
                out += buf
                break
            out += buf[:tick]
            rest = buf[tick:]
            verdict = _verdict(rest)
            if verdict == "open":
                self._tail = rest
                return out
            if verdict == "maybe":
                self._held = rest
                break
            # Not an opener after all. Release the backtick that started this
            # and rescan from the next character -- "`` ```state" holds a real
            # opener behind a run that is not one.
            out += rest[0]
            buf = rest[1:]
        return out

    def finish(self) -> str:
        """Resolve everything still withheld, at the point the stream ends —
        which is the first moment either question can be answered.

        A held *prefix* that never grew into an opener is just text; dropping it
        would eat the backticks off a code fence the narration ended on.

        A block that opened is put through `split_block`, the same rule the
        persisted reply is judged by. Withholding it outright would have been
        wrong the moment a model wrote narration AFTER the block: the transcript
        keeps that (a mid-reply block is never stripped) and the stream would
        have silently ended the reply early, so the two would disagree about
        what was said. Released in one burst rather than progressively, because
        "is this block trailing?" is not decidable until there is no more text.
        """
        if self._tail is not None:
            tail, self._tail = self._tail, None
            narration, _ = split_block(tail)
            return narration
        held, self._held = self._held, ""
        return held


# ---- the ledger ------------------------------------------------------------

def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "turnstate.json"


def read(cid: str) -> dict:
    """The raw file, or ``{}`` for anything unreadable.

    Never raises. #120 asks for exactly this: a garbled turnstate.json omits
    the section rather than crashing `_assemble`, because the prompt this feeds
    is assembled on the way to a paid generation.
    """
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def entries(cid: str, sid: str, tail: int | None = None) -> list[tuple[int, dict]]:
    """One scene's recorded entries as ``(post index, {token: {field: value}})``,
    oldest first, with every malformed level dropped.

    `tail` is the scene's current message count. Entries at or past it are
    dropped, which is what makes the index key survive the transcript shrinking
    under it: a reroll or a `trim_continuation` leaves entries describing posts
    that no longer exist, and the replacement reply then overwrites the entry at
    its own index. What this does NOT repair is an `edit_message` that changes
    how a message splits and shifts later indices by one --- see the design
    note; those entries misattribute until decay drops them.
    """
    scene = read(cid).get(sid)
    if not isinstance(scene, dict):
        return []
    out = []
    for key, actors in scene.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        if idx < 0 or (tail is not None and idx >= tail):
            continue
        if not isinstance(actors, dict):
            continue
        clean = {token: got for token, fields in actors.items()
                 if isinstance(token, str) and (got := _clean(fields))}
        if clean:
            out.append((idx, clean))
    return sorted(out, key=lambda e: e[0])


def _capped(scene: dict) -> dict:
    """The newest `MAX_ENTRIES` of a scene's entries. Unreadable keys are kept
    rather than counted — they sort nowhere sensible, `entries` already skips
    them, and a truncation rule is no place to start deleting a file's
    mysteries."""
    numbered = []
    for key in scene:
        try:
            numbered.append((int(key), key))
        except (TypeError, ValueError):
            continue
    if len(numbered) <= MAX_ENTRIES:
        return scene
    drop = {key for _, key in sorted(numbered)[:len(numbered) - MAX_ENTRIES]}
    return {k: v for k, v in scene.items() if k not in drop}


def record(cid: str, sid: str, index: int, states: dict[str, dict[str, str]]) -> None:
    """File one generation's tracker block at `index` --- the position of the
    LAST post it rode in on, so a fresh entry always sits at the transcript
    tail and the decay window measures from where the reply actually landed."""
    if not states:
        return
    with locks.campaign_lock(cid):
        data = read(cid)
        scene = data.get(sid)
        scene = {**(scene if isinstance(scene, dict) else {}),
                 str(index): {token: dict(fields) for token, fields in states.items()}}
        data[sid] = _capped(scene)
        _write(cid, data)


def supersede(cid: str, sid: str, floor: int) -> None:
    """Forget entries describing posts from `floor` on — the slots a landing
    generation is about to occupy.

    Called before every reply is appended, not only ones carrying a block, and
    that is the whole point. A reroll deletes the old reply and the replacement
    lands at the same index; if the replacement omits its tracker block, the
    DISCARDED variant's entry is still sitting there, and `entries` cannot tell
    it apart from a live one — the post it points at exists again. The scene
    then gets told a character is furious because a reply the player threw away
    said so.

    `entries`' tail filter does not cover this: it drops entries past the end of
    the transcript, and after the replacement lands this one is not past it.
    """
    with locks.campaign_lock(cid):
        data = read(cid)
        scene = data.get(sid)
        if not isinstance(scene, dict):
            return
        kept = {}
        for key, actors in scene.items():
            try:
                stale = int(key) >= floor
            except (TypeError, ValueError):
                stale = False       # unreadable key: `entries` skips it anyway
            if not stale:
                kept[key] = actors
        if len(kept) == len(scene):
            return
        data[sid] = kept
        _write(cid, data)


def current(cid: str, sid: str, tail: int, depth: int) -> dict[str, dict[str, str]]:
    """The live view: newest value per (actor, field) recorded within `depth`
    posts of the tail. That IS the decay --- an older value is not aged or
    weighted, it simply stops being in the window and drops out.

    `depth <= 0` disables the feature, mirroring `archive_depth`."""
    if depth <= 0 or tail <= 0:
        return {}
    floor = max(0, tail - depth)
    out: dict[str, dict[str, str]] = {}
    for idx, actors in entries(cid, sid, tail):
        if idx < floor:
            continue
        for token, fields in actors.items():
            out.setdefault(token, {}).update(fields)
    return out


def streaks(cid: str, sid: str, tail: int, need: int) -> dict[str, dict[str, str]]:
    """Values reinforced across at least `need` consecutive recorded entries
    (#121), as ``{token: {field: value}}``.

    The run measured is the FINAL one. `current_state` is standing state, and a
    mood held for four posts in the middle of a scene and abandoned before the
    end is precisely what the character is *not* now; promoting it would write
    the scene's discarded middle into its conclusion.

    An entry that does not mention an actor (or mentions her without this
    field) is SKIPPED, not streak-breaking. An NPC who did not act this turn
    has not changed her posture, and demanding literal index adjacency would
    make promotion unreachable in any scene with more than one NPC.
    """
    if need <= 0:
        return {}
    runs: dict[str, dict[str, tuple[str, int]]] = {}
    for _, actors in entries(cid, sid, tail):
        for token, fields in actors.items():
            for field, value in fields.items():
                held = runs.setdefault(token, {}).get(field)
                runs[token][field] = ((value, held[1] + 1)
                                      if held and _norm(held[0]) == _norm(value)
                                      else (value, 1))
    out = {}
    for token, fields in runs.items():
        kept = {f: v for f, (v, n) in fields.items() if n >= need}
        if kept:
            out[token] = kept
    return out


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids. Part of the `scene_refs.repoint` fan-out."""
    with locks.campaign_lock(cid):
        data = read(cid)
        if not any(sid in mapping for sid in data):
            return
        _write(cid, {mapping.get(sid, sid): scene for sid, scene in data.items()})


def drop_scene(cid: str, sid: str) -> None:
    """Forget a deleted scene's ledger.

    Scene ids are RECYCLED (`scenes.lifecycle.delete_scene` says so, and
    retires the commit ledger for the same reason): without this, the next
    scene to take the id inherits a dead one's moods, and the low indices the
    stale entries sit at are exactly the ones a young scene's decay window
    covers.
    """
    with locks.campaign_lock(cid):
        data = read(cid)
        if sid not in data:
            return
        del data[sid]
        _write(cid, data)
