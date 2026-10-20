"""characters: record responsibilities."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ... import store
from ...store import _plaintext
from ..common import _display_name_or_400, _world_root_or_404
from ..models import (
    CharacterBirthdate,
    CharacterCreate,
    CharacterTags,
    DefaultVersion,
    NameBody,
    VersionCreate,
    VersionUpdate,
)
from ._router import router as router


@router.get("/worlds/{wid}/characters")
def get_world_characters(wid: str):
    root = _world_root_or_404(wid)
    # The badge count agrees with the character page's Greetings tab, which
    # lists world greetings featuring the character beneath the card's own.
    rows = store.greetings.add_featuring_counts(
        store.characters.list_characters(root), store.greetings.list_greetings(root))
    # Rendered as it stands rather than through FastAPI's `jsonable_encoder`,
    # which walks every value of a list that is already plain JSON -- strs,
    # ints, bools, None, lists and str-keyed dicts, nothing it would convert --
    # and on a large cast that walk cost more than building the rows from the
    # listing memo. The bytes are the ones the default path renders, which is
    # this same class (`test_world_list_fast.py` compares them).
    return JSONResponse(rows)


@router.post("/worlds/{wid}/characters")
def post_world_character(wid: str, body: CharacterCreate):
    # The folder is named from the slug, and folding can grow a name past what
    # a filesystem allows (`İ` lowercases to two code points); a name is also
    # one line. Refuse either here rather than as a 500 from mkdir (#136).
    if problem := _plaintext.name_problem(body.name):
        raise HTTPException(422, detail=problem)
    with store.locks.world_actor_lock(wid):
        try:
            store.actor_names.require_unique(body.name, scope="world", scope_id=wid)
            if body.card:
                store.actor_names.require_unique(store.actor_names.card_name(body.card),
                                                 scope="world", scope_id=wid)
        except store.actor_names.ActorNameError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        cid, vid = store.characters.create_character(
            _world_root_or_404(wid), body.name, body.version_name, body.card
        )
    return {"character": cid, "version": vid}


@router.get("/worlds/{wid}/characters/{cid}")
def get_world_character(wid: str, cid: str):
    try:
        return store.characters.read_character(_world_root_or_404(wid), cid)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc


@router.put("/worlds/{wid}/characters/{cid}")
def put_world_character(wid: str, cid: str, body: DefaultVersion):
    try:
        with store.locks.world_actor_lock(wid):
            root = _world_root_or_404(wid)
            selected = store.characters.read_card(root, cid, body.default_version)
            store.actor_names.require_unique(store.actor_names.card_name(selected),
                                             scope="world", scope_id=wid,
                                             actor_ref=f"characters:{cid}")
            store.characters.set_default_version(root, cid, body.default_version)
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    except store.characters.VersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return {"ok": True}


@router.put("/worlds/{wid}/characters/{cid}/name")
def put_world_character_name(wid: str, cid: str, body: NameBody):
    """Rename the container (#13). The card's own `data.name` is saved with the
    card; this is the name the grid, the cast panel and the `meta.name` prompt
    sections read, and the two used to be unable to agree."""
    name = _display_name_or_400(body.name)
    try:
        store.actor_names.require_unique(name, scope="world", scope_id=wid,
                                         actor_ref=f"characters:{cid}")
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    try:
        store.characters.set_name(_world_root_or_404(wid), cid, name)
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    return {"ok": True}


@router.put("/worlds/{wid}/characters/{cid}/birthdate")
def put_world_character_birthdate(wid: str, cid: str, body: CharacterBirthdate):
    try:
        store.characters.set_birthdate(_world_root_or_404(wid), cid, body.birthdate)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    return {"ok": True}


@router.put("/worlds/{wid}/characters/{cid}/tags")
def put_world_character_tags(wid: str, cid: str, body: CharacterTags):
    """The tags this character carries when cast as the player (#18). Ids from
    the world's vocabulary, checked under the same actor lock the PC route
    holds, so a tag deleted mid-save cannot be written onto a character."""
    root = _world_root_or_404(wid)
    try:
        with store.locks.world_actor_lock(wid):
            for t in body.tags:
                if not store.tags.has_tag(root, t):
                    raise HTTPException(status_code=400, detail=f"unknown tag: {t}")
            store.characters.set_tags(root, cid, body.tags)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    return {"ok": True}


@router.delete("/worlds/{wid}/characters/{cid}")
def delete_world_character(wid: str, cid: str):
    root = _world_root_or_404(wid)
    with store.overlay.world_identity_change(root, record=("characters", cid)) as cids:
        try:
            store.characters.delete_character(root, cid)
        except store.characters.CharacterNotFound as exc:
            raise HTTPException(status_code=404, detail="character not found") from exc
        store.overlay.forget_world_record(root, "characters", cid, cids=cids)   # #225
        return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/versions")
def post_world_version(wid: str, cid: str, body: VersionCreate):
    try:
        store.actor_names.require_unique(store.actor_names.card_name(body.card),
                                         scope="world", scope_id=wid,
                                         actor_ref=f"characters:{cid}")
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    try:
        vid = store.characters.create_version(_world_root_or_404(wid), cid, body.name, body.card)
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    return {"version": vid}


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}")
def put_world_version(wid: str, cid: str, vid: str, body: VersionUpdate):
    try:
        store.actor_names.require_unique(store.actor_names.card_name(body.card),
                                         scope="world", scope_id=wid,
                                         actor_ref=f"characters:{cid}")
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    try:
        store.characters.update_version(_world_root_or_404(wid), cid, vid, body.card)
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    except store.characters.VersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    return {"ok": True}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}")
def delete_world_version(wid: str, cid: str, vid: str):
    try:
        store.characters.delete_version(_world_root_or_404(wid), cid, vid)
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    except store.characters.VersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
