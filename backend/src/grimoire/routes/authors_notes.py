"""Author's notes (play controls V): read and save the standing instructions
the context builder inserts into the history at a chosen depth.

The store keys scene notes by identity (`store.authors_notes`); this surface
speaks sids, resolving every identity in one pass on the way out, so a client
never handles a token. A note whose scene no longer exists is not reported.

Every save answers with the whole document, as the GET does, so the panel can
reload from the response. Validation is here, in the route, as a 400 per field
(the request body is a plain `BaseModel`, so it stays pydantic-v1/v2-agnostic):
the store's own `normalize` clamps rather than refuses, which is right for a
hand-edited file and wrong for a request a person can be told about.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store
from . import runs
from .common import _campaign_root_or_404, _require_scene
from .models import AuthorsNote

router = APIRouter()


def _validate(body: AuthorsNote) -> dict | None:
    """The note to store, None for empty text (which clears), or a 400."""
    if len(body.text) > store.authors_notes.MAX_TEXT:
        raise HTTPException(status_code=400, detail="text_too_long")
    if not 0 <= body.depth <= store.authors_notes.DEPTH_MAX:
        raise HTTPException(status_code=400, detail="depth_out_of_range")
    if not 1 <= body.every <= store.authors_notes.EVERY_MAX:
        raise HTTPException(status_code=400, detail="every_out_of_range")
    if not body.text.strip():
        return None
    return {"text": body.text, "depth": body.depth, "every": body.every}


def _payload(cid: str) -> dict:
    data = store.authors_notes.read(cid)
    by_identity = {store.scenes.scene_identity(cid, s["id"]): s["id"]
                   for s in store.scenes.list_scenes(cid)}
    by_identity.pop(None, None)
    return {"campaign": data["campaign"], "characters": data["characters"],
            "scenes": {by_identity[ident]: note for ident, note in data["scenes"].items()
                       if ident in by_identity}}


@router.get("/campaigns/{cid}/authors-notes")
def get_authors_notes(cid: str):
    """`{"campaign": note | None, "characters": {ref: note}, "scenes": {sid: note}}`."""
    _campaign_root_or_404(cid)
    return _payload(cid)


@router.put("/campaigns/{cid}/authors-notes/campaign")
def put_campaign_authors_note(cid: str, body: AuthorsNote):
    _campaign_root_or_404(cid)
    store.authors_notes.set_campaign(cid, _validate(body))
    return _payload(cid)


@router.put("/campaigns/{cid}/authors-notes/characters/{ref}")
def put_character_authors_note(cid: str, ref: str, body: AuthorsNote):
    """`ref` is the full actor ref (`characters:<id>`), URL-encoded, and must
    name one of the campaign's characters -- a note for nobody would be stored
    and never reach a prompt."""
    _campaign_root_or_404(cid)
    note = _validate(body)
    if ref not in {f"characters:{c['id']}" for c in store.overlay.list_characters(cid)}:
        raise HTTPException(status_code=404, detail="character_not_found")
    store.authors_notes.set_character(cid, ref, note)
    return _payload(cid)


@router.put("/campaigns/{cid}/scenes/{sid}/authors-note")
def put_scene_authors_note(cid: str, sid: str, body: AuthorsNote):
    """Refused on a closed branch: its note steers turns nobody can play."""
    _campaign_root_or_404(cid)
    note = _validate(body)
    _require_scene(cid, sid)
    with store.locks.campaign_lock(cid):
        runs.require_scene_open(cid, sid)
        try:
            ident = store.scenes.ensure_identity(cid, sid)
        except store.scenes.SceneNotFound as exc:
            raise HTTPException(status_code=404, detail="scene not found") from exc
        store.authors_notes.set_scene(cid, ident, note)
    return _payload(cid)


@router.get("/campaigns/{cid}/scenes/{sid}/authors-notes/next")
def get_next_authors_notes(cid: str, sid: str):
    """Which notes apply to the scene's NEXT turn (N + 1), for the panel's
    header count: `{"turn", "count", "notes": [{"level", "depth", "every",
    "applies", "ref"?, "name"?}]}`.

    A character note is listed per NPC in the scene's cast that has one, and
    applies "when that character speaks" -- whether they will is the turn's
    decision, so `count` counts every entry due on that turn, character entries
    included. This describes the turn to come; the live inspector describes the
    turn just composed, which is why the two can differ by a cadence step."""
    scene = _require_scene(cid, sid)
    data = store.authors_notes.read(cid)
    turn = store.context.authors_note.note_turn(scene["messages"]) + 1
    applies = store.authors_notes.applies
    notes: list[dict] = []
    ident = store.scenes.scene_identity(cid, sid)
    for level, note in (("campaign", data["campaign"]),
                        ("scene", data["scenes"].get(ident) if ident else None)):
        if note:
            notes.append({"level": level, "depth": note["depth"], "every": note["every"],
                          "applies": applies(note, turn)})
    for actor in store.appearances.scene_cast(cid, sid):
        ref = f"{actor['kind']}:{actor['id']}"
        note = data["characters"].get(ref)
        if actor["role"] == "npc" and note:
            notes.append({"level": "character", "depth": note["depth"], "every": note["every"],
                          "applies": applies(note, turn), "ref": ref, "name": actor["name"]})
    return {"turn": turn, "count": sum(1 for n in notes if n["applies"]), "notes": notes}
