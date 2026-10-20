"""tracker: records responsibilities."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from ... import store
from ...llm import LLMClient
from .. import runs
from ..common import _require_scene, get_llm
from ..models import TrackerEdit
from . import _scheduling, _transcript
from ._router import router as router

# --- scene records -------------------------------------------------------------
#
# What the disclosure reads and writes. Reads work whatever the setting, so a
# campaign that switched the tracker off can still look at what it recorded;
# the three writes refuse with `tracker_off`, because each of them either
# schedules a paid update or edits a record nothing will keep up to date.

INTERRUPTED = "interrupted"
"""The `error` a `pending` record with no run behind it is reported with."""


def _require_on(cid: str) -> None:
    if not store.tracker.settings.enabled(cid):
        raise HTTPException(status_code=409, detail="tracker_off")


def _require_key(key: str) -> None:
    # Checked before anything is read: a key becomes a file name.
    if not store.tracker.paths.valid_key(key):
        raise HTTPException(status_code=404, detail="tracker record not found")


def _update_live(app, cid: str, identity: str | None) -> bool:
    """Whether anything still running can settle this scene's `pending` records.

    A tracker update can, obviously. So can a TURN: it marks its keys `pending`
    inside the hold that writes each post, but starts their runs only after its
    terminal frames (`character_turns._start_tracking`) -- and a client that
    reads the summary on the `done` frame lands in exactly that gap. The turn
    run is still live there (it finishes when its generator does, after that
    `finally`), so counting it keeps a record that is about to start from
    flickering to "interrupted"."""
    if not identity:
        return False
    for run in app.state.runs.for_subject(("scene", cid, identity)):
        if run.terminal.is_set():
            continue
        if run.kind == "tracker-update" or run.cls == "turn":
            return True
    return False


def _reported(entry: dict, live: bool) -> dict:
    """The entry as a reader should see it.

    A `pending` record with nothing live behind it is one no run will ever
    settle: the process stopped between the mark and the result, or a start
    that failed could not even write `failed`. Read literally it says
    "updating..." for good and offers nothing; reported as failed it offers
    Retry, which is the one action that settles it. Reported, not rewritten --
    a GET takes no lock, and the next mark or save overwrites it anyway."""
    entry = {k: v for k, v in entry.items() if k not in store.tracker.records.INTERNAL}
    if entry.get("status") == "pending" and not live:
        return {**entry, "status": "failed", "error": INTERRUPTED}
    return entry


def _flags(entry: dict) -> dict:
    return {**dict.fromkeys(store.tracker.records.FLAGS, False), **(entry.get("flags") or {})}


def _mood_text(value) -> str:
    """A stored mood as the one line a cast tile shows. A layer may retype
    `visible_mood` to a list, so a list is its non-empty items joined; anything
    that is neither is no mood at all."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return ", ".join(v.strip() for v in value if isinstance(v, str) and v.strip())
    return ""


def _moods(snapshot: dict, fields: list[dict]) -> dict:
    """`{ref: visible_mood}` for the characters present at the tail, for the
    cast tiles -- always a string, whatever type the field's layers gave it.
    Nothing for a character with no mood yet, and nothing at all when the field
    is switched off for this scene."""
    if not any(f["key"] == "visible_mood" for f in store.tracker.fields.active(fields)):
        return {}
    out = {}
    for ref, ent in snapshot.items():
        text = _mood_text(((ent.get("fields") or {}).get("visible_mood") or {}).get("value"))
        if ent.get("present") and text:
            out[ref] = text
    return out


@router.get("/campaigns/{cid}/scenes/{sid}/tracker")
def get_scene_tracker(cid: str, sid: str, request: Request):
    """Every tracked post's key in transcript order, with its index entry --
    the disclosures' collapsed summaries in one read, no snapshot among them."""
    _require_scene(cid, sid)
    walk = store.tracker.walk
    ident = store.scenes.scene_identity(cid, sid)
    entries = store.tracker.records.read_index(cid, ident) if ident else {}
    live = _update_live(request.app, cid, ident)
    fields = store.tracker.fields.effective(cid, sid)
    _, current = walk.current(cid, sid)
    return {
        "enabled": store.tracker.settings.enabled(cid),
        "names": walk.roster(cid, sid),
        "keys": [{"index": i, "key": k} for i, k in walk.ordered_keys(cid, sid)],
        "entries": {k: _reported(e, live) for k, e in entries.items()},
        "moods": _moods(current, fields),
        # Switched-off fields included: a stored value still needs its label.
        "labels": {f["key"]: f["label"] for f in fields},
    }


def _record_body(app, cid: str, sid: str, identity: str, key: str) -> dict:
    entry = store.tracker.records.read_index(cid, identity).get(key)
    if entry is None:
        raise HTTPException(status_code=404, detail="tracker record not found")
    shown = _reported(entry, _update_live(app, cid, identity))
    body = store.tracker.records.read_snapshot(cid, identity, key)
    out = {
        "key": key,
        "status": shown.get("status"),
        "flags": _flags(shown),
        "snapshot": body["snapshot"] if body else None,
        "fields": store.tracker.fields.effective(cid, sid),
        "names": store.tracker.walk.roster(cid, sid),
    }
    if shown.get("error"):
        out["error"] = shown["error"]
    return out


def _identity_or_404(cid: str, sid: str) -> str:
    ident = store.scenes.scene_identity(cid, sid)
    if not ident:
        # A scene that never minted an identity has never stored a record.
        raise HTTPException(status_code=404, detail="tracker record not found")
    return ident


@router.get("/campaigns/{cid}/scenes/{sid}/tracker/records/{key}")
def get_tracker_record(cid: str, sid: str, key: str, request: Request):
    _require_scene(cid, sid)
    _require_key(key)
    return _record_body(request.app, cid, sid, _identity_or_404(cid, sid), key)


@router.put("/campaigns/{cid}/scenes/{sid}/tracker/records/{key}")
def put_tracker_record(cid: str, sid: str, key: str, body: TrackerEdit, request: Request):
    """A person's edit of one record's values. Nothing re-runs: every later
    record is flagged `upstream_changed`, and re-running is the person's call.

    Under one campaign-lock hold from the read to the flags, so the edit is
    applied to the snapshot it replaces and the flags land with it."""
    _require_scene(cid, sid)
    _require_key(key)
    _require_on(cid)
    records, walk = store.tracker.records, store.tracker.walk
    with store.locks.campaign_lock(cid):
        ident = _identity_or_404(cid, sid)
        entry = records.read_index(cid, ident).get(key)
        located = _transcript._locate(cid, sid, key) if entry is not None else None
        if entry is None or located is None:
            # No record, or one for a post that is gone (the next prune drops it).
            raise HTTPException(status_code=404, detail="tracker record not found")
        if entry.get("status") == "pending" and _update_live(request.app, cid, ident):
            # An update for this key is on its way, built on what the record
            # said before this edit; its save would replace the edit without a
            # trace. Refused rather than lost -- the person edits once it lands.
            raise HTTPException(status_code=409, detail="tracker_busy")
        stored = records.read_snapshot(cid, ident, key)
        # A record with no snapshot (failed before it ever landed) is edited
        # from the state its post started from -- what the disclosure shows.
        prev = stored["snapshot"] if stored else walk.state_before(cid, sid, located["index"])[1]
        fields = store.tracker.fields.effective(cid, sid)
        try:
            snapshot, changed = store.tracker.merge.apply_edit(prev, body.edits, fields)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # The post's own change list keeps what the update found, with the
        # person's changes laid over the same fields: it is the summary of what
        # changed AT this post, and the edit is one more thing that did.
        moved = {(c[0], c[1]) for c in changed}
        kept = ([c for c in entry.get("changed") or [] if (c[0], c[1]) not in moved]
                if entry.get("status") == "ok" else [])
        # Every pair the edit wrote, value or only awareness, joins the ones
        # earlier edits (or a re-run restoring them) left: what a re-run must
        # put back. `changed` cannot carry it -- it is the display's list, and
        # an awareness-only edit moves no value.
        touched = (records.pairs(entry.get(records.TOUCHED))
                   + store.tracker.merge.touched_by(prev, snapshot))
        # `keep_flags`: clearing is a re-run's job. A hand edit of one value
        # does not make the rest of the record agree with an edited post or an
        # earlier change, so whatever was raised stays. `bump_gen`: a run
        # already queued for this key read the record before this edit, and
        # must not land over it (`Mark`).
        records.save(cid, ident, key, snapshot, changed=kept + changed,
                     fields_digest=store.tracker.fields.digest(fields), model="user",
                     keep_flags=True, bump_gen=True, touched=touched)
        at = walk.index_of(cid, sid, key)
        if at is not None:
            # Only from a key ON the walk: an inactive variant is not what any
            # later record was built on.
            walk.flag_after(cid, sid, at)
    return _record_body(request.app, cid, sid, ident, key)


@router.post("/campaigns/{cid}/scenes/{sid}/tracker/records/{key}/retry")
def post_tracker_retry(cid: str, sid: str, key: str, request: Request,
                       client: LLMClient = Depends(get_llm)):
    """Run `key`'s update again. Its post must still be there; a record need
    not exist -- an untracked post is what Retry is offered on.

    When the record had no usable result (failed, interrupted, or never
    tracked), a success flags every later record: each was built without this
    post's contribution. A retry of an `ok` record leaves them alone.

    Refused on a closed branch: a paid update of a scene nobody can play."""
    _require_scene(cid, sid)
    runs.require_scene_open(cid, sid)
    _require_key(key)
    _require_on(cid)
    if _transcript._locate(cid, sid, key) is None:
        raise HTTPException(status_code=404, detail="tracker record not found")
    ident = store.scenes.scene_identity(cid, sid)
    entry = store.tracker.records.read_index(cid, ident).get(key) if ident else None
    # Captured BEFORE the mark, which overwrites the status with `pending`.
    status = (_reported(entry, _update_live(request.app, cid, ident))["status"]
              if entry else None)
    _scheduling.schedule(request.app, cid, sid, key, client,
             flag_later=status not in ("ok", "pending"))
    return get_scene_tracker(cid, sid, request)


@router.post("/campaigns/{cid}/scenes/{sid}/tracker/records/{key}/rerun-from")
def post_tracker_rerun_from(cid: str, sid: str, key: str, request: Request,
                            client: LLMClient = Depends(get_llm)):
    """Re-run `key` and every tracked post after it, in transcript order.

    Scheduled in that order, which is the order they run in (`_scene_lock`),
    so each starts from what the one before it just wrote. Each save clears its
    own flags; nothing needs flagging, because everything after is re-run.
    The first is started from its base record on purpose (`trust_base`): the
    person chose to re-run from here with that record's warning in view.
    Refused on a closed branch, as `post_tracker_retry` is."""
    _require_scene(cid, sid)
    runs.require_scene_open(cid, sid)
    _require_key(key)
    _require_on(cid)
    ordered = [k for _, k in store.tracker.walk.ordered_keys(cid, sid)]
    if key not in ordered:
        raise HTTPException(status_code=404, detail="tracker record not found")
    # A hidden post is scheduled nothing (`mark`), so the starting point the
    # person chose passes to the first post that does run.
    hidden = store.tracker.walk.hidden_keys(cid, sid)
    first = True
    for later in ordered[ordered.index(key):]:
        if later in hidden:
            continue
        _scheduling.schedule(request.app, cid, sid, later, client, trust_base=first)
        first = False
    return get_scene_tracker(cid, sid, request)
