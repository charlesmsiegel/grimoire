"""Messages as a phase sees them: the one door callers outside the package use.

`view` is what every LLM reader of transcript text goes through (the `prompt`
phase) and what the scene read and the exports go through (`display`). It never
writes: the stored transcript stays raw, and turning a rule off restores every
later prompt and every screen at once. `store_phase` is the other direction --
the opt-in rewrite of text as it lands, which is the only phase that changes
what is stored.

Each message is run against the effective list for the connection that
produced it, so a list is built once per distinct `connection` value rather
than once per message. When no rule could apply in the phase for any of them,
the messages come back as plain copies without a pattern being run: a store
with no rule files must build exactly the prompt it built before rules existed.
"""

from __future__ import annotations

import logging

from ..scenes import serialize as scenes_serialize
from . import apply, layers

log = logging.getLogger(__name__)


def _live(entries: list[apply.Entry], phase: str) -> list[apply.Entry]:
    """The entries that could apply in `phase` to some message. Role and depth
    are still checked per message by `apply.run`; this only decides whether
    there is anything to run at all."""
    out = []
    for e in entries:
        rule = e["rule"]
        if not rule["enabled"] or e["off"]:
            continue
        if rule["rewrite_stored"] if phase == "store" else phase in rule["applies"]:
            out.append(e)
    return out


def connection_of(message: dict) -> str:
    """The connection that produced `message`, "" for none. Only a non-empty
    string is an id: the transcript reader already drops anything else, and a
    caller's own dict is held to the same rule rather than trusted as a key."""
    conn = message.get("connection")
    return conn if isinstance(conn, str) else ""


def view(messages: list[dict], *, cid: str | None, phase: str, offset: int = 0,
         total: int | None = None) -> list[dict]:
    """Copies of `messages` with `content` run through the `phase` rules.

    `messages` may be a window of a longer transcript: `offset` is the index of
    its first message in the whole, and `total` the whole's length, so depth --
    0 for the newest message -- counts over the full transcript rather than
    over the slice. A line no rule may touch (a roll, a transition, a director
    note) is copied unchanged."""
    out = [dict(m) for m in messages]
    lists: dict[str, list[apply.Entry]] = {}
    for m in out:
        conn = connection_of(m)
        if conn not in lists:
            lists[conn] = _live(layers.effective(cid=cid, connection=conn), phase)
    if not any(lists.values()):
        return out
    n = total if total is not None else len(messages)
    for i, m in enumerate(out):
        entries = lists[connection_of(m)]
        role = apply.role_of(m)
        if not entries or role is None or not isinstance(m.get("content"), str):
            continue
        m["content"] = apply.run(m["content"], entries, role=role, phase=phase,
                                 depth=n - 1 - (offset + i))
    return out


def annotate_shown(messages: list[dict], *, cid: str | None, offset: int = 0,
                   total: int | None = None) -> list[dict]:
    """Copies of `messages`, each carrying `shown` only where the display phase
    changed its text. `content` stays raw: it is what an edit starts from."""
    shown = view(messages, cid=cid, phase="display", offset=offset, total=total)
    out = []
    for m, s in zip(messages, shown, strict=True):
        copy = dict(m)
        if s.get("content") != m.get("content"):
            copy["shown"] = s["content"]
        out.append(copy)
    return out


def _posts_started(text: str) -> int:
    """How many new posts `text` would start once stored: a speaker marker
    after a blank line is where the transcript reader begins the next message.
    One at the very start of the (stripped) text sits behind the post's own
    marker on the same line, so it starts nothing."""
    return sum(1 for m in scenes_serialize._markers(text.strip()) if m.start() > 0)


def store_phase(text: str, *, cid: str, role: str,
                connection: str = "") -> tuple[str, list[str]]:
    """`text` through the rules that rewrite stored text, and the ids of the
    rules that changed it (a rule that matched nothing, or replaced a match
    with itself, is not one of them). When the text the landing path would
    store -- every one strips it -- is what arrived, no rule fired at all,
    whatever happened on the way: rules that undid each other, or a change to
    boundary whitespace only, leave no record and no Restore to offer.

    A rewrite that would leave nothing (empty or whitespace only) is not
    applied: the text comes back exactly as it arrived, with no rule fired, so
    no seam stores an empty post or loses the only copy of what was written.
    Nor is one that would start a post of its own -- a speaker marker after a
    blank line, which the next read splits off as another message, breaking
    turn counts, ids and Restore. Logged by rule id only -- the text is
    private prose."""
    entries = _live(layers.effective(cid=cid, connection=connection), "store")
    if not entries:
        return text, []
    before = text
    fired: list[str] = []
    for step in apply.trace(text, entries, role=role, phase="store", depth=0):
        if step["applied"] and step["text_after"] != text:
            fired.append(step["rule_id"])
        text = step["text_after"]
    if fired and text.strip() == before.strip():
        # Rules that undid each other, or moved only the boundary whitespace
        # every landing path strips: what would be stored is what arrived, so
        # there is no rewrite to record and no original to restore.
        return before, []
    if fired and not text.strip():
        log.warning("store phase: not applying a rewrite that empties the text (rules %s)",
                    ", ".join(fired))
        return before, []
    if fired and _posts_started(text) > _posts_started(before):
        log.warning("store phase: not applying a rewrite that starts a new post (rules %s)",
                    ", ".join(fired))
        return before, []
    return text, fired
