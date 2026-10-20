"""tracker: fences responsibilities."""

from __future__ import annotations

from ... import store
from ._router import router as router

# --- the update run ----------------------------------------------------------
#
# One `background` run per tracked post (`runs.reserve_background`): no
# exclusion key, so an update can neither refuse a turn with `run_in_flight`
# nor hold the scene against an edit, a cut or a rename. Play never waits on
# it, and nothing it does can fail the turn that scheduled it -- every entry
# point below swallows its own failures, because by the time any of them is
# called the post is already on disk and the player is owed it.
#
# Everything here is keyed on the scene's IDENTITY, not its `sid`. A run
# outlives its request, and a `sid` moves on rename: the `sid` a run was handed
# is only a hint, re-resolved (`_current_sid`) every time the run touches the
# store.

Mark = tuple[str, int]
"""A key `mark` set `pending`, with the generation that mark owns
(`records.GEN`). The run started for it carries both: a run whose generation
the entry has moved past is obsolete -- a newer run for the same key was marked
after it, or a person edited the record -- and is skipped or its result
discarded rather than allowed to land over whatever superseded it."""


def _current_sid(cid: str, hint: str, identity: str) -> str | None:
    """The scene's `sid` now, or `None` once it is deleted. The hint is tried
    first, so the ordinary case costs one header read rather than a scan."""
    if store.scenes.scene_identity(cid, hint) == identity:
        return hint
    return store.scenes.find_by_identity(cid, identity)


def _obsolete(cid: str, identity: str, key: str, gen: int | None) -> bool:
    """Whether a run owning generation `gen` has been superseded (`Mark`).
    Called under the campaign lock. `None` -- a caller with no generation to
    compare -- is never obsolete."""
    if gen is None:
        return False
    entry = store.tracker.records.read_index(cid, identity).get(key)
    return store.tracker.records.generation(entry) != gen


Base = tuple[str | None, int, int]
"""What an update's input stood on, as `_prepare` read it: the base record's
key (the newest `ok` record before the post, `walk.state_before`) with its
mark generation and flag sequence. `_commit` reads it again under the lock
that covers the write, and a result whose base has moved lands with
`upstream_changed`.

WHY, when `_scene_lock` already runs a scene's updates one at a time: that
lock is one process's. Two grimoire processes on one store
(docs/store-guarantees.md, "A second process on the same store") share the
campaign lock and nothing else, so each can run an adjacent post's update
while the other's is answering -- and the later one then reads a base the
earlier is about to replace. Nothing re-runs on its account (the spec's
rule: later records are flagged, and re-running is the person's call); the
flag is what keeps a record built on a superseded state from reading fresh.

The generation moves on every `mark_pending` and every hand edit, so a base
re-run or edited under the update is caught even when it lands back on the
same key; the sequence catches a flag raised on it. A base re-marked and not
yet landed is no longer `ok`, so the walk steps past it to an older key."""


def _base_mark(base_key: str | None, entries: dict) -> Base:
    entry = entries.get(base_key) if base_key else None
    records = store.tracker.records
    return base_key, records.generation(entry), records.flag_seq(entry)


def _base_unsettled(cid: str, sid: str, index: int, base_key: str | None,
                    entries: dict) -> bool:
    """Whether a post between the base and the post at `index` has an update
    still `pending` -- one that will land after this update read its base.

    In one process that cannot be an update queued AHEAD of this one, which
    `_scene_lock` has already let land; it is another process's update, one
    queued behind this one (a Retry of an earlier post), or one that died.
    In every case this result is built without that post's contribution. A
    `failed` post is not counted: its final state is "no result", and a Retry
    that gives it one flags every later record itself (`flag_later`). Nor is a
    post hidden from context, which `walk._latest_ok` steps over the same way:
    its update is never going to land, and nothing reads what it would say."""
    hidden = store.tracker.walk.hidden_keys(cid, sid)
    for i, key in reversed(store.tracker.walk.ordered_keys(cid, sid)):
        if i >= index:
            continue
        if key == base_key:
            return False
        if key in hidden:
            continue
        if (entries.get(key) or {}).get("status") == "pending":
            return True
    return False
