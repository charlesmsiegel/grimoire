"""The scene tracker's view of a transcript: which posts are tracked, in what
order, and what state stood at a given point.

`tracker.records` stores by key and knows nothing about transcripts; this
module is where a `sid` and a message index are turned into keys, so it is the
half that may import `store.scenes` (`records` must not -- `delete_scene` imports
it). A key is derived from per-post metadata that survives edits, cuts and
renumbering (`post_id`, `response_id`), never from an index, so the walk is
recomputed from the transcript on every call rather than stored.

Two rules shape it:

- **A response is keyed at its last message.** A response split around a roll
  (`response_part`) is one response with one record, and the state it leaves
  behind is the state after its final part -- so that is where the key sits
  in the order, and a lookup for a state *before* the later part does not see
  the response's result early.
- **Only the active variant is on the walk, but every variant's record is
  kept.** Swiping to another variant changes which record is read, not which
  records exist; pruning an inactive variant would make swiping back cost a
  fresh run for a state that was already worked out.
"""

from __future__ import annotations

from .. import locks, responses
from ..appearances import cast
from ..appearances import paths as appearances_paths
from ..scenes import identity, read, serialize
from . import paths, records


def _tracked(m: dict) -> tuple[str, str] | None:
    """`("response", rid)` or `("post", key)` for a trackable message, else
    `None`. Synthetic lines (rolls, transitions, director notes) are never
    tracked, and a user post written before post ids existed has nothing to key
    a record to."""
    if m.get("speaker") in serialize.SYNTHETIC_SPEAKERS:
        return None
    if m.get("response_id"):
        return "response", m["response_id"]
    if m.get("role") == "user" and m.get("post_id"):
        return "post", paths.post_key(m["post_id"])
    return None


def _scan(messages: list[dict], variants: dict) -> tuple[list[tuple[int, str]], dict[int, str]]:
    """The tracked keys in transcript order, and which key owns each message
    index (every part of a response maps to that response's key)."""
    keys: list[tuple[int, str]] = []
    owner: dict[int, str] = {}
    parts: dict[str, list[int]] = {}
    for i, m in enumerate(messages):
        tracked = _tracked(m)
        if tracked is None:
            continue
        kind, value = tracked
        if kind == "response":
            parts.setdefault(value, []).append(i)
        elif paths.valid_key(value):
            keys.append((i, value))
            owner[i] = value
    for rid, indices in parts.items():
        active = variants.get(rid, (None, []))[0]
        if not active:
            continue                    # no ledger record, or nothing chosen yet
        key = paths.response_key(rid, active)
        if not paths.valid_key(key):
            continue
        keys.append((indices[-1], key))
        owner.update(dict.fromkeys(indices, key))
    keys.sort()
    return keys, owner


def _walk(cid: str, sid: str) -> tuple[list[tuple[int, str]], dict[int, str]]:
    messages = read.read_scene(cid, sid)["messages"]
    return _scan(messages, responses.variants_by_response(cid, sid))


def ordered_keys(cid: str, sid: str) -> list[tuple[int, str]]:
    """One `(message index, key)` per tracked post, in transcript order."""
    return _walk(cid, sid)[0]


def key_at(cid: str, sid: str, index: int) -> str | None:
    return dict(ordered_keys(cid, sid)).get(index)


def index_of(cid: str, sid: str, key: str) -> int | None:
    return next((i for i, k in ordered_keys(cid, sid) if k == key), None)


def _latest_ok(cid: str, sid: str, below: int | None) -> tuple[str | None, dict]:
    """The newest `ok` record at a message index below `below` (anywhere when
    `None`). Pending and failed keys are stepped over, so a turn whose update
    has not landed reads the state its predecessor left."""
    ident = identity.scene_identity(cid, sid)
    if not ident:
        return None, {}
    index = records.read_index(cid, ident)
    for i, key in reversed(ordered_keys(cid, sid)):
        if below is not None and i >= below:
            continue
        if index.get(key, {}).get("status") != "ok":
            continue
        body = records.read_snapshot(cid, ident, key)
        if body is not None:
            return key, body["snapshot"]
    return None, {}


def state_before(cid: str, sid: str, index: int) -> tuple[str | None, dict]:
    """The state a post at `index` starts from: the latest `ok` record at a
    message index strictly below it, or `(None, {})`."""
    return _latest_ok(cid, sid, index)


def current(cid: str, sid: str) -> tuple[str | None, dict]:
    """The state as of the transcript's tail."""
    return _latest_ok(cid, sid, None)


def present_at(cid: str, sid: str, index: int) -> set[str]:
    """Refs (`characters:<id>` / `pcs:<id>`) present at message `index`.

    Presence intervals are half-open. A cast member with no interval for this
    scene (an appearance recorded before intervals existed) has no boundary to
    test, so counts as present throughout. Read from the appearance record in
    one go rather than through `cast.scene_cast`, which is the same membership
    test plus a card read per actor for a name this does not need."""
    out: set[str] = set()
    for ref, rec in appearances_paths.record(cid).items():
        intervals = rec.get("presence", {}).get(sid)
        if intervals:
            if any(r["start"] <= index and (r.get("end") is None or index < r["end"])
                   for r in intervals):
                out.add(ref.replace("/", ":", 1))
        elif sid in rec.get("scenes", []):
            out.add(ref.replace("/", ":", 1))
    return out


def roster(cid: str, sid: str, *, departed: bool = True) -> dict[str, str]:
    """Ref to display name for everyone the scene's records may name: its
    current cast and, unless `departed` is False, everyone who has left it.

    Departed characters by default because a record describes a moment, not
    now: an earlier post's record names whoever was there then, an update
    for a post made just before a Leave is about someone who was present at
    it, and absorb reads the whole scene. Only what describes the scene AS IT
    IS -- the play prompt's Scene state -- asks for the current cast alone."""
    return {f"{a['kind']}:{a['id']}": a["name"]
            for a in cast.scene_cast(cid, sid, include_departed=departed)}


def _later(cid: str, sid: str, ident: str, index: int) -> list[str]:
    """Every key whose post sits after message `index`: each later player
    post, and EVERY variant of each later response -- not only the walk's.

    An inactive variant keeps its record (see the module docstring), and that
    record was built on the state before its response just as the active one
    was. Flagging only the walk would leave it fresh, and a swipe to it -- which
    flags only what comes after the response -- would make its pre-change
    state current with no warning. A response counts as later when its FIRST
    part does, so the response that owns `index` (an edit to an earlier part
    of a split response) is never its own successor."""
    messages = read.read_scene(cid, sid)["messages"]
    # The token already resolved, as `prune` passes it: a fail-soft second
    # lookup answering `{}` would quietly flag no response at all.
    variants = responses.variants_by_response(cid, sid, token=ident)
    out: list[str] = []
    first: dict[str, int] = {}
    for i, m in enumerate(messages):
        tracked = _tracked(m)
        if tracked is None:
            continue
        kind, value = tracked
        if kind == "response":
            first.setdefault(value, i)
        elif i > index and paths.valid_key(value):
            out.append(value)
    for rid, at in first.items():
        if at > index:
            out += [k for k in (paths.response_key(rid, vid)
                                for vid in variants.get(rid, (None, []))[1])
                    if paths.valid_key(k)]
    return out


def flag_edited(cid: str, sid: str, index: int) -> None:
    """The post at `index` was edited: its own record no longer matches its
    text, and every later record -- every variant of a later response
    included (`_later`) -- was built on top of it.

    An edit to an earlier part of a split response flags that response's key,
    which sits at its last part."""
    with locks.campaign_lock(cid):
        ident = identity.scene_identity(cid, sid)
        if not ident:
            return
        _, owner = _walk(cid, sid)
        own = owner.get(index)
        if own:
            records.set_flags(cid, ident, [own], "text_changed")
        records.set_flags(cid, ident, [k for k in _later(cid, sid, ident, index) if k != own],
                          "upstream_changed")


def flag_after(cid: str, sid: str, index: int) -> None:
    """Something at `index` changed that later records were built on (a cut, a
    reroll, a manual state edit): every record strictly after it is stale, each
    variant of a later response included (`_later`)."""
    with locks.campaign_lock(cid):
        ident = identity.scene_identity(cid, sid)
        if not ident:
            return
        records.set_flags(cid, ident, _later(cid, sid, ident, index), "upstream_changed")


def prune(cid: str, sid: str) -> int:
    """Discard every record whose post is gone from the transcript; returns how
    many. A response still in the transcript keeps a record for **each** of its
    variants, active or not (see the module docstring)."""
    with locks.campaign_lock(cid):
        ident = identity.scene_identity(cid, sid)
        if not ident:
            return 0
        messages = read.read_scene(cid, sid)["messages"]
        # The token already resolved, not a second fail-soft lookup: if the scene
        # file were unreadable on that one, the ledger would answer `{}` and
        # every response record would read as dead.
        variants = responses.variants_by_response(cid, sid, token=ident)
        live: set[str] = set()
        for m in messages:
            tracked = _tracked(m)
            if tracked is None:
                continue
            kind, value = tracked
            if kind == "response":
                live.update(paths.response_key(value, vid)
                            for vid in variants.get(value, (None, []))[1])
            else:
                live.add(value)
        dead = sorted(records.stored_keys(cid, ident) - live)
        if dead:
            records.discard(cid, ident, dead)
        return len(dead)
