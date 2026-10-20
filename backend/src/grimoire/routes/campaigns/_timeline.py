"""Campaign HTTP endpoints: timeline domain."""


from __future__ import annotations

from fastapi import (
    HTTPException,
)

from ... import store
from ..common import (
    _campaign_root_or_404,
)
from ..models import (
    PinRule,
)
from ._names import _record_name
from ._router import router


@router.get("/campaigns/{cid}/timeline")
def get_timeline(cid: str):
    """The play timeline (#198): every scene in play order, with the plot beats
    that landed in it and the threads to filter them by.

    Thin on purpose — the join, the tolerance and the lock all live in
    ``store.timeline``, whose docstring carries the argument for each. Same
    split as ``get_scene_briefing``, and for the same reason: this is a read
    that fans across three files with one failure policy, which is a store
    question rather than an HTTP one.

    The ledger's own ``timeline`` section is a different view of a subset — the
    last few chronicle one-liners as table rows — and stays where it is. This
    route reaches the scenes that were never absorbed, which is most of the ones
    a campaign in progress has, and the per-scene beats nothing else reads back.
    """
    _campaign_root_or_404(cid)
    return store.timeline.build(cid)


# ---- user pins & excludes (#129) ----
#
# Campaign-scoped, with the scene as a query parameter rather than a path
# segment, because one rule set holds both scopes: a scene-scoped rule and the
# campaign-wide default it overrides are read, listed and removed together, and
# hanging the scene ones off /scenes/{sid} would split that in two.
def _pin_name(cid: str, kind: str, eid: str, pc_names) -> str | None:
    """Display name for a pinned target, or None when the campaign no longer has
    it. Rules outlive what they name -- a pinned character can be deleted -- and
    a dangling rule is inert rather than an error (see store/pins.py), so this
    reports the absence instead of hiding the row.

    `pc_names` is the campaign's PC list, resolved once by the caller and only
    when a row needs it: PCs are read as a LIST rather than one at a time
    because a PC the campaign has not materialized is still inherited from the
    world and still castable, and reading the campaign copy of one would report
    it as deleted. `_record_name`'s `read_character` resolves the same way for
    the other actor kind, one id at a time, so only this half needs hoisting.
    """
    if kind == "pcs":
        return pc_names().get(eid)
    return _record_name(cid, kind, eid)


def _pin_row(cid: str, rule: dict, pc_names) -> dict:
    """One stored rule, plus what a panel needs to draw it. The ONE projection:
    a POST answers with the same shape its GET will hand back, so the client can
    render what it just wrote without a second read disagreeing with it."""
    kind, _sep, eid = rule["ref"].partition(":")
    name = _pin_name(cid, kind, eid, pc_names)
    return {**rule, "kind": kind, "id": eid, "name": name or eid, "missing": name is None}


def _pc_name_reader(cid: str):
    """A memoized `{pid: name}` reader. Memoized rather than eager because most
    rule sets name no PC at all, and the list costs a directory walk over the
    campaign's PCs and the world's."""
    cache: dict[str, str] = {}
    loaded = False

    def pc_names() -> dict[str, str]:
        nonlocal loaded
        if not loaded:
            cache.update({p["id"]: p["name"] for p in store.overlay.list_pcs(cid)})
            loaded = True
        return cache

    return pc_names


def _pin_rows(cid: str, sid: str, posts: int) -> list[dict]:
    pc_names = _pc_name_reader(cid)
    return [_pin_row(cid, r, pc_names) for r in store.pins.records(cid, sid, posts)]


def _pin_posts(cid: str, sid: str) -> int:
    """The transcript length a TTL counts against, or 0 with no scene named.

    404s on a scene this campaign does not have: a rule filed against a
    mistyped id would never apply to anything and never be visible anywhere.
    """
    if not sid:
        return 0
    try:
        return len(store.scenes.read_scene(cid, sid)["messages"])
    except store.SceneNotFound as exc:
        raise HTTPException(status_code=404, detail="scene not found") from exc


@router.get("/campaigns/{cid}/pins")
def get_pins(cid: str, sid: str = ""):
    """Every rule in force for `sid` — that scene's, then the campaign's own.

    Spent rules are already gone from this list (`pins.records`), so `remaining`
    is always a live countdown or None for a standing rule.
    """
    _campaign_root_or_404(cid)
    return {"pins": _pin_rows(cid, sid, _pin_posts(cid, sid))}


@router.post("/campaigns/{cid}/pins")
def post_pin(cid: str, body: PinRule):
    """Pin or exclude one target, replacing whatever rule this scope held for it.

    The post count the TTL is measured from is read HERE rather than sent by the
    client: it is the length of the transcript this rule is about, and a client
    that guessed it wrong would set a window that expires at the wrong turn.
    """
    _campaign_root_or_404(cid)
    # Only a scene rule is measured against a scene, so only a scene rule is
    # refused for naming one that is gone. A client that keeps its scene id in
    # the same field for both scopes (ours does) would otherwise be unable to
    # file a CAMPAIGN rule from a scene that has just been renamed underneath
    # it -- over an id the campaign rule does not use and does not store.
    posts = _pin_posts(cid, body.sid) if body.scope == "scene" else 0
    try:
        rec = store.pins.set_rule(cid, body.ref, body.mode, scope=body.scope, sid=body.sid,
                                  ttl_posts=body.ttl_posts, posts=posts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "pin": _pin_row(cid, rec, _pc_name_reader(cid))}


@router.delete("/campaigns/{cid}/pins")
def delete_pin(cid: str, ref: str, scope: str = "scene", sid: str = ""):
    """Lift one rule. 404 when this scope had none for that ref — removing a
    rule that is not there is a client working from a stale list, and reporting
    it as done would leave the panel showing a row nothing will clear."""
    _campaign_root_or_404(cid)
    try:
        removed = store.pins.remove(cid, ref, scope=scope, sid=sid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="pin not found")
    return {"ok": True}


# ---- campaign cast & suggestions ----
def _absorbed_cast_refs(cid: str) -> set[str]:
    """Actor refs preserved by absorbed scenes, for pre-presence campaigns."""
    try:
        chronicle = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — a garbled chronicle loses only legacy evidence
        return set()
    if not isinstance(chronicle, dict):
        return set()
    refs: set[str] = set()
    for scene in chronicle.values():
        # One read, so the isinstance check narrows the value that is iterated.
        cast = scene.get("cast") if isinstance(scene, dict) else None
        if isinstance(cast, list):
            refs.update(ref for ref in cast if isinstance(ref, str))
    return refs


