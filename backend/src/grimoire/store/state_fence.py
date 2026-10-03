"""The trailing ``state`` fence: recognised, parsed and kept off the page.

A model may still end a reply with a fenced ``state`` block -- the old per-turn
NPC mood/intent/posture ledger asked for one, a card or a long chat history may
still show one, and a provider has no way to be told it was retired. Nothing
reads what such a block says any more, but it must still never reach the
transcript or the player's screen, so the grammar survives here on its own.

Two things live here and nothing else does:

- **the grammar** (`split_block`, `parse_block`) -- one definition, used by the
  persist path and by the stream so the two cannot disagree about what a
  state block is;
- **the stream redactor** (`StreamRedactor`), the display half: it withholds
  the block from the deltas the client sees, judging by `split_block` itself.

Pure functions over text: no store access, no lock, nothing persisted.
"""

from __future__ import annotations

import json
import re

#: The fields a block may carry per character, in the order the old prompt
#: listed them. A key outside this tuple is dropped by `parse_block`.
FIELDS = ("mood", "intent", "posture")

#: Longest value kept. Over the cap the value is DROPPED, not truncated --
#: truncating manufactures matches between two values that merely start alike.
MAX_VALUE = 80

# The block, as a trailing fence only. `(?:^|\n)` rather than `re.MULTILINE`'s
# `^` so the newline that precedes the opener is eaten with it, leaving the
# narration without a dangling blank line.
#
# `\r` is in the trailing class on both, because a provider that returns CRLF
# line endings otherwise matches NEITHER boundary -- and the failure is silent
# and total: the block is persisted into the transcript as narration. The
# leading `\r` of a CRLF pair stays on the narration side, where `split_reply`
# strips it like any other trailing space.
_OPEN = re.compile(r"(?:^|\n)[ \t]*```[ \t]*state[ \t\r]*(?:\n|$)", re.IGNORECASE)
_CLOSE = re.compile(r"(?:^|\n)[ \t]*```[ \t\r]*(?:\n|$)")


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

    Both the bare mapping and the ``{"state": {...}}`` envelope are accepted
    --- the retired instruction asked for the bare one, and a model that wraps
    it has still said what it meant.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # The envelope, told apart from a CHARACTER called "state" by the shape one
    # level down: an envelope's values are per-character field maps (dicts), and
    # a character's are field values (strings). Unwrapping on the key alone lost
    # every block for such a character outright -- `{"state": {"mood": "calm"}}`
    # became a cast of one named `mood`, whose "fields" were the string "calm".
    if (set(data) == {"state"} and isinstance(data["state"], dict)
            and all(isinstance(v, dict) for v in data["state"].values())):
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
    """``(narration, states)`` --- the reply with its trailing ``state`` block
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


# ---- the stream ------------------------------------------------------------
# `_persist_reply` strips the block from what is STORED, but the deltas have
# already reached the browser by then. This is the display half.
#
# Composed OUTSIDE `fence.FenceWatcher` rather than folded into it. That class
# decides whether a roll proposal exists, its holdback is load-bearing for the
# proposal-before-narration guarantee, and it has no business also knowing
# about state fences. Feeding its output through this leaves `watcher.narration`
# --- and therefore the whole persistence path --- untouched.

_MAYBE_OPENER = re.compile(r"`{1,2}\Z|```[ \t]*(?:s(?:t(?:a(?:t(?:e)?)?)?)?)?\Z", re.IGNORECASE)
_IS_OPENER = re.compile(r"```[ \t]*state\b", re.IGNORECASE)


def _verdict(s: str) -> str:
    """Whether `s` (which starts with a backtick) opens a ``state`` block, could
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
    """Withhold a trailing ``state`` block from the deltas the client sees.

    Buffers from any backtick, releases the buffer the moment it cannot become
    an opener, and holds everything from an opener onward until `finish` can
    decide — with `split_block`, the same rule the stored reply is judged by —
    whether it really was one. Pure: it never looks at what was persisted.
    """

    def __init__(self) -> None:
        self._held = ""            # a prefix that could still grow into an opener
        self._tail: str | None = None   # everything from an opener on, or None
        # Whether everything emitted since the last newline is blank, i.e.
        # whether the next character sits where `_OPEN`'s `(?:^|\n)[ \t]*` could
        # start. Start of stream counts.
        self._blank = True

    def _advance(self, text: str) -> None:
        if not text:
            return
        _, sep, after = text.rpartition("\n")
        self._blank = (after.strip(" \t") == "") if sep else (
            self._blank and text.strip(" \t") == "")

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
                self._advance(buf)
                break
            out += buf[:tick]
            self._advance(buf[:tick])
            rest = buf[tick:]
            # Only a fence at a line boundary can be a block, because that is
            # what `_OPEN` requires. Withholding an INLINE one detached it from
            # the context that disqualified it: `finish` hands the suffix to
            # `split_block`, which sees it starting at `^` and strips it as a
            # trailing block -- while persistence, judging the whole reply,
            # keeps the fence because "Use " precedes it. The client lost the
            # rest of the reply until a refresh put it back.
            verdict = _verdict(rest) if self._blank else "no"
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
            self._advance(rest[0])
            buf = rest[1:]
        return out

    def finish(self) -> str:
        """Resolve everything still withheld, at the point the stream ends —
        which is the first moment either question can be answered.

        Both cases go through `split_block`, the same rule the persisted reply
        is judged by, which is the only way the two can be made to agree:

        - A block that OPENED must not simply be dropped. The moment a model
          wrote narration after it, the transcript keeps the lot (a mid-reply
          block is never stripped) and dropping would silently end the streamed
          reply early. Released in one burst rather than progressively, because
          "is this block trailing?" is not decidable until there is no more text.
        - A HELD prefix is usually just text -- dropping it would eat the
          backticks off a code fence the narration ended on -- but not always.
          A stream that stops exactly after ``` ```state ``` with no newline
          leaves a *complete* opener held, and `split_block` strips that as an
          unterminated trailing block. Emitting it here would show the player an
          opener the transcript does not contain, and on a reroll would show it
          in place of the reply the server just restored.

        `split_block` returns anything that is not a trailing block untouched,
        so the ordinary held prefix comes back whole.
        """
        pending = self._tail if self._tail is not None else self._held
        self._tail, self._held = None, ""
        narration, _ = split_block(pending)
        return narration
