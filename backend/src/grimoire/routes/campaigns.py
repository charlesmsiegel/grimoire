"""Campaign-scoped routes: the campaign record, exports, the world->campaign
sync inbox, calendar and climate settings, group state, the campaign's own
copies of characters and PCs, change review (the rolling per-record delta, the
append-only journal behind it, and undo), and the continuity ledger.

Scenes, weather, mechanics and greetings have their own modules; the generic
``/campaigns/{cid}/{kind}`` entity surface lives in ``entities``.
"""

from __future__ import annotations


from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .. import store
from ..llm import LLMClient
from ..llm_errors import LLMError
from .common import (computes_only, _bounded_call, _campaign_root_or_404, _content_fields,
                     _dump, _require_connection, _response_body, get_llm,
                     _serve_image, _serve_image_file, _write_response)
from .models import (AvatarFocus, CalendarConfig, CampaignClimate, CopyFromGreeting,
                     DefaultVersion, GroupStateSave, NameBody, NewCampaign, PCCreate,
                     PCUpdate, PersonaVersionCreate, PersonaVersionUpdate, PickBody,
                     RefList, ResponseSettings, VersionCreate, VersionUpdate,
                     VoiceAnchorSave)

router = APIRouter()


# ---- group state (#47): campaign-local, not covered by generic entity CRUD
# (path shape groups/{gid}/state cannot collide with /{kind}/{eid} or its
# /images sub-paths, so order relative to the generic routes doesn't matter)
@router.get("/campaigns/{cid}/groups/{gid}/state")
def get_group_state(cid: str, gid: str):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    try:
        store.overlay.read_entity(cid, "groups", gid)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="group not found")
    st = store.groupstate.read_state(store.campaigns.campaign_root(cid), gid)
    if st is None:
        return {**{k: "" for k in store.groupstate.FIELDS}, "updated": ""}
    return st


@router.put("/campaigns/{cid}/groups/{gid}/state")
def put_group_state(cid: str, gid: str, body: GroupStateSave):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    try:
        store.overlay.read_entity(cid, "groups", gid)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="group not found")
    values = {"goals": body.goals, "resources": body.resources, "focus": body.focus,
              "public_perception": body.public_perception, "secrets": body.secrets}
    name = _record_name(cid, "groups", gid) or gid
    with store.undo.journalled(cid, {"w": "group_state", "id": gid},
                               kind="group_state", ref={"kind": "groups", "id": gid},
                               field="body", label=f"{name} — group state"):
        store.groupstate.write_state(store.campaigns.campaign_root(cid), gid,
                                     store.groupstate.compose_body(values))
    return {"ok": True}


@router.get("/campaigns/{cid}/climate")
def get_campaign_climate(cid: str):
    """The campaign's default climate id, and the resolved document."""
    _campaign_root_or_404(cid)
    resolved = store.campaign_climate.resolve_default(cid)
    return {"default_climate": resolved["id"], "climate": resolved}


@router.put("/campaigns/{cid}/climate")
def put_campaign_climate(cid: str, body: CampaignClimate):
    """Change the default after creation.

    Without this the create-time write is the only thing that ever sets a
    campaign's default climate, so it would be immutable short of hand-editing
    the store or tagging every location individually.
    """
    _campaign_root_or_404(cid)
    try:
        store.campaign_climate.write_default(cid, body.default_climate)
    except store.climates.ClimateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not write climate: {e}")
    return {"ok": True, "default_climate": body.default_climate}


# ---- campaigns ----
@router.get("/campaigns")
def get_campaigns():
    out = []
    for c in store.campaigns.list_campaigns():
        scene_list = store.scenes.list_scenes(c["id"])
        # `updated` is campaign.md's, which only metadata writes advance --
        # playing a scene touches the scene file and nothing else, so ordering
        # by it ranks a campaign renamed months ago above one played into last
        # night. `activity` is the whole campaign's high-water mark, for
        # anything answering "what was I last working on".
        #
        # Through `best_stamp` rather than a bare `max`, and over EVERY scene
        # rather than `scene_list[0]`. The fold is lexical, so one unparseable
        # or far-future value anywhere in it outranks every genuine timestamp
        # and then blocks its own replacement -- and `list_scenes` sorts by the
        # very field that may be the bad one, so element zero is only the
        # newest if the sort key can be trusted. The list is already in memory
        # for the count; validating it costs a strptime per scene.
        # The newest scene carrying an absorb mark, which is how far the
        # chronicle, the ledger and every dossier are caught up to. It is not
        # the same question as "how many scenes are there" and a campaign
        # answers them differently the moment you play one scene ahead of the
        # absorb -- which is the normal state of a campaign being played.
        absorbed = next((s["title"] for s in scene_list if s.get("done")), "")
        out.append({**c, "scenes": len(scene_list),
                    "cover": store.covers.cover_version(c["id"]),
                    "last_scene": scene_list[0]["title"] if scene_list else "",
                    "absorbed_through": absorbed,
                    "activity": store.campaigns.best_stamp(
                        c["updated"], store.campaigns.read_activity(c["id"]),
                        *(s["updated"] for s in scene_list))})
    return out


@router.get("/campaigns/{cid}/calendar")
def get_calendar_config(cid: str):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    return store.calendars.read_calendar(store.campaigns.campaign_root(cid))


@router.put("/campaigns/{cid}/calendar")
def put_calendar_config(cid: str, body: CalendarConfig):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = {"primary": body.primary, "secondary": body.secondary, "confirmed": body.confirmed}
    try:
        store.calendars.validate_calendar(cfg)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    store.calendars.write_calendar(store.campaigns.campaign_root(cid), cfg)
    return {"ok": True}


@router.get("/campaigns/{cid}/calendar/months")
def get_calendar_months(cid: str, year: int):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.calendars.read_calendar(store.campaigns.campaign_root(cid))
    try:
        return {"months": store.calendars.get_provider(cfg["primary"]).months(year)}
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/campaigns")
def post_campaign(body: NewCampaign):
    try:
        cid = store.campaigns.create_campaign(body.name, body.world,
                                              body.region, body.calendar,
                                              body.module, body.climate)
        store.config.mark_setup_done()   # see routes/worlds.post_world (#194)
        return {"id": cid}
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=400, detail="world not found")
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except store.climates.ClimateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")


@router.get("/campaigns/{cid}")
def get_campaign(cid: str):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        out = store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    # embedded so the frontend needn't chain a world fetch after this one
    wid = out["meta"].get("world", "")
    out["meta"]["world_name"] = store.worlds.world_name(wid) or wid
    # Derived, like world_name above -- nothing about the cover is written into
    # campaign.md, whose unlocked read-modify-writers would race it.
    out["meta"]["cover"] = store.covers.cover_version(cid)
    return out


@router.get("/campaigns/{cid}/export.epub")
def export_campaign_epub(cid: str):
    try:
        blob, filename = store.epub.build_epub(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="application/epub+zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.md.zip")
def export_campaign_markdown(cid: str):
    try:
        blob, filename = store.export.build_markdown_bundle(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.html")
def export_campaign_html(cid: str):
    try:
        blob, filename = store.export.build_html(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.txt")
def export_campaign_text(cid: str):
    try:
        blob, filename = store.export.build_text(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/campaigns/{cid}/export.json")
def export_campaign_json(cid: str):
    try:
        blob, filename = store.export.build_json(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---- the campaign's cover image (store/covers.py) --------------------------
# Declared here, in `campaigns`, which `routes/__init__` includes BEFORE
# `entities` -- `/campaigns/{cid}/{kind}` would otherwise capture `cover`.
@router.get("/campaigns/{cid}/cover")
def get_campaign_cover(cid: str, request: Request):
    _campaign_root_or_404(cid)
    p = store.covers.cover_path(cid)
    if p is None:
        raise HTTPException(status_code=404, detail="cover not found")
    return _serve_image_file(p, request)


@router.put("/campaigns/{cid}/cover")
async def put_campaign_cover(cid: str, file: UploadFile = File(...)):
    _campaign_root_or_404(cid)
    try:
        # Check the size BEFORE reading. `read()` below materializes the whole
        # upload as a single `bytes` object, and that allocation -- not the
        # receipt -- is what `MAX_BYTES` exists to bound: the backend is packaged
        # verbatim into the Android app (Chaquopy), where a 300 MB image would
        # OOM the process before a 413 could be composed. Starlette spools the
        # body to disk above 1 MB and populates `UploadFile.size`, so this costs
        # nothing and the bytes are already on disk by the time we look.
        #
        # `covers.validate` still re-checks `len(data)`, and must: `size` is
        # Optional in the ASGI contract, so a client (or a future transport)
        # that leaves it None would otherwise buy an unbounded read.
        if file.size is not None and file.size > store.covers.MAX_BYTES:
            raise store.covers.CoverTooLarge(store.covers.TOO_LARGE)
        data = await file.read()
        # `validate` also names the extension to store under, from the format it
        # decoded -- `file.filename` is not consulted at all, so a JPEG uploaded
        # as `cover.png` cannot be served (or manifested in the EPUB) as PNG.
        ext = store.covers.validate(data)
    except store.covers.CoverTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except store.covers.CoverInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        stored = store.covers.put_cover(cid, data, ext)
    except ValueError as exc:
        # Nothing `validate` returns is an extension `put_in` refuses today, so
        # this is unreachable from here -- kept because what is storable is the
        # store's decision to make, not this route's to assume.
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ext": stored, "v": store.covers.cover_version(cid)}


@router.delete("/campaigns/{cid}/cover")
def delete_campaign_cover(cid: str):
    _campaign_root_or_404(cid)
    try:
        store.covers.delete_cover(cid)
    except OSError:
        # `delete_cover` confirms the removal rather than swallowing a failed
        # unlink, so this is a cover that is genuinely still there -- a held
        # file on Windows, a read-only store. Reporting 200 would be a lie.
        raise HTTPException(status_code=500, detail="cover could not be removed")
    return {"ok": True}


@router.put("/campaigns/{cid}")
def put_campaign(cid: str, body: NameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        store.campaigns.rename_campaign(cid, name)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"id": cid, "name": name}


@router.delete("/campaigns/{cid}")
def delete_campaign(cid: str):
    try:
        store.campaigns.delete_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


@router.get("/campaigns/{cid}/response")
def get_campaign_response(cid: str):
    try:
        campaign_meta = store.campaigns.read_campaign(cid)["meta"]
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.read_config()
    return _response_body({}, campaign_meta, cfg, campaign_meta)


@router.put("/campaigns/{cid}/response")
def put_campaign_response(cid: str, body: ResponseSettings):
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    try:
        _write_response(lambda f: store.campaigns.set_campaign_response(cid, f), fields)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


# ---- campaign sync ----
@router.get("/campaigns/{cid}/incoming")
def get_incoming(cid: str):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        return store.sync.incoming(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.post("/campaigns/{cid}/incoming/accept")
def post_accept(cid: str, body: RefList):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        store.sync.accept(cid, [_dump(r) for r in body.refs])
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/incoming/reject")
def post_reject(cid: str, body: RefList):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        store.sync.reject(cid, [_dump(r) for r in body.refs])
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


def _record_name(cid: str, kind: str, eid: str) -> str | None:
    """Display name for a campaign record (materialized or still inherited
    from the world), or None if it no longer exists anywhere."""
    try:
        if kind == "characters":
            return store.overlay.read_character(cid, eid)["meta"].get("name", eid)
        if kind in store.entities.ENTITY_KINDS:
            return store.overlay.read_entity(cid, kind, eid)["meta"].get("name", eid)
    except (store.characters.CharacterNotFound, store.entities.EntityNotFound):
        return None
    return None


@router.get("/campaigns/{cid}/changes")
def get_changes(cid: str):
    _campaign_root_or_404(cid)
    data = store.changes.read(cid)
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    out: list[dict] = []
    for ref, entry in data.items():
        kind, _, eid = ref.partition("/")
        name = _record_name(cid, kind, eid)
        if name is None:
            continue  # record deleted since the change was captured
        sid = entry.get("scene", "")
        s, c = scenes_by_id.get(sid, {}), chron.get(sid, {})
        fields = [{"field": f.get("field", ""), "label": f.get("label", ""),
                   "diff": store.changes.line_diff(f.get("before", ""), f.get("after", ""))}
                  for f in entry.get("fields", [])]
        out.append({"ref": {"kind": kind, "id": eid}, "name": name,
                    "scene": {"id": sid, "title": s.get("title", sid), "date": c.get("date", "")},
                    "fields": fields})
    out.sort(key=lambda r: (r["ref"]["kind"], r["name"]))
    return out


# ---- the append-only change journal (#31) ----------------------------------
#
# `changes` above is a rolling upsert: one entry per record, replaced by the next
# write-back. These two routes read the history it cannot keep, and reverse an
# entry in it.

#: How many entries a listing returns, newest first. A cap rather than paging:
#: the panel is a "what just happened, and can I take it back" view, and each
#: row carries a rendered diff, so returning everything the store retains would
#: be a much larger response than anyone reads. The store keeps more than this
#: (`journal.RETENTION` / `journal.MAX_BYTES`) and an older entry is still
#: undoable by id — it is simply not in the list.
JOURNAL_PAGE = 100


def _journal_row(cid: str, entry: dict, scenes_by_id: dict, chron: dict,
                 names: dict) -> dict:
    """One journal entry as the panel reads it: the record's current display
    name when it still resolves, the scene's label, and the server-side line
    diff `changes` already renders for the rolling view.

    Every field is coerced, because journal.json is hand-editable and read by a
    bare `json.loads` -- the same rule `plot._field` states. A non-string `label`
    handed to React blanks the whole panel, and a non-string before/after would
    raise out of `line_diff`'s `.splitlines()`.
    """
    ref = entry.get("ref")
    ref = ref if isinstance(ref, dict) else {}
    kind = ref.get("kind") if isinstance(ref.get("kind"), str) else ""
    eid = ref.get("id") if isinstance(ref.get("id"), str) else ""
    key = f"{kind}/{eid}"
    if key not in names:
        names[key] = _record_name(cid, kind, eid) if kind and eid else None
    sid = entry.get("scene") if isinstance(entry.get("scene"), str) else ""
    s, c = scenes_by_id.get(sid, {}), chron.get(sid, {})
    undone = entry.get("undone")
    plan = entry.get("undo")
    # The same predicate `undo.undo` refuses on, not merely "has an `undo` key":
    # journal.json is hand-editable, and a row whose plan lost its target would
    # otherwise render an enabled button that 400s.
    reversible = isinstance(plan, dict) and isinstance(plan.get("target"), dict)
    return {
        "id": entry.get("id", "") if isinstance(entry.get("id"), str) else "",
        "ts": entry.get("ts", "") if isinstance(entry.get("ts"), str) else "",
        "source": entry.get("source", "") if isinstance(entry.get("source"), str) else "",
        "kind": entry.get("kind", "") if isinstance(entry.get("kind"), str) else "",
        "ref": {"kind": kind, "id": eid},
        "name": names[key] or "",
        "label": entry.get("label", "") if isinstance(entry.get("label"), str) else "",
        "field": entry.get("field", "") if isinstance(entry.get("field"), str) else "",
        "scene": {"id": sid, "title": s.get("title", sid), "date": c.get("date", "")},
        "diff": store.changes.line_diff(
            entry.get("before", "") if isinstance(entry.get("before"), str) else "",
            entry.get("after", "") if isinstance(entry.get("after"), str) else ""),
        # `undoable` is the server's answer, never the client's inference: the
        # button must not offer what the store would refuse.
        "undoable": reversible and not undone,
        "why": entry.get("why", "") if isinstance(entry.get("why"), str) else "",
        "undone": undone if isinstance(undone, dict) else None,
    }


@router.get("/campaigns/{cid}/journal")
def get_journal(cid: str):
    _campaign_root_or_404(cid)
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    names: dict = {}
    entries = store.journal.read(cid)[-JOURNAL_PAGE:]
    return [_journal_row(cid, e, scenes_by_id, chron, names) for e in reversed(entries)]


@router.post("/campaigns/{cid}/journal/{jid}/undo")
def post_journal_undo(cid: str, jid: str):
    """Put one journalled change back.

    409 rather than 200 when the record moved since: the reversal is a
    compare-and-swap (`store/undo.py`), and a reader undoing one edit has not
    asked to discard whatever landed on that record afterwards.
    """
    _campaign_root_or_404(cid)
    try:
        written = store.undo.undo(cid, jid)
    except store.undo.EntryNotFound:
        raise HTTPException(status_code=404, detail="that change is not in this "
                                                    "campaign's history")
    except store.undo.AlreadyUndone:
        raise HTTPException(status_code=409, detail="that change has already been undone")
    except store.undo.NotUndoable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except store.undo.UndoConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    return {"ok": True, "entry": _journal_row(cid, written, scenes_by_id, chron, {})}


# The recent-facts tier of the ledger. Shorter than GET /chronicle's 50: that
# route feeds the per-scene recap, where the reader is looking for one entry,
# while this is a standing overview meant to be read top to bottom.
LEDGER_RECENT = 20

#: `relationships.set_feeling` writes 0–5; the ledger draws the number back.
FEELING_AXES = ("trust", "affection", "tension")


def _ledger_text(value, fallback: str = "") -> str:
    """A projected field as text. The ledger renders these directly, and React
    refuses an object as a child -- so a hand-edited record with a dict-valued
    `title` would blank the whole view rather than showing one odd row. The
    per-section tolerance below cannot catch that: the read SUCCEEDS and the
    failure happens in the browser. `commitments.open_commitments` normalizes
    its own rows; `plot.open_threads` does not, and hardening `plot` is one of
    the pre-existing items flagged on that PR, so the coercion sits here where
    the ledger owns the projection.

    Module level rather than nested in `get_ledger` because the relationships
    projection below needs the same rule, and two copies of it is two places
    for one of them to stop being applied.
    """
    return value.strip() if isinstance(value, str) else fallback


def _ledger_relationships(cid: str) -> list[dict]:
    """Who stands where with whom, as flat rows the ledger can put in a table.

    relationships.json holds two shapes and both belong on this view. A
    *feeling* is directed and metered -- A's trust, affection and tension
    toward B, which B does not return by construction -- and a *bond* is
    symmetric, named ("kin", "sworn") and dated to the scene it formed in. One
    list with a `kind` rather than two sections: the reader's question is what
    stands between two people, and answering it in two tables makes them read
    both to find out.

    Names are resolved once per token and cached. `relationships.actor_name`
    reads a card per call, and a campaign with a dozen actors carries upwards of
    a hundred directed pairs, so the uncached version is that many file reads
    for a dozen answers. A token whose card will not parse falls back to its own
    id rather than emptying the section -- `casefile.build` makes the same trade
    for a cast member whose card is broken, and for the same reason: a name is
    the least of what this row says.

    The meters are clamped rather than trusted, like `casefile._pips`: the file
    is hand-editable, the client draws five pips, and a stored 9 would draw four
    that do not exist. A non-integer reads as 0 -- visibly nothing, rather than
    a crash.
    """
    data = store.relationships.read(cid)
    feelings = data.get("feelings") if isinstance(data.get("feelings"), dict) else {}
    bonds = data.get("bonds") if isinstance(data.get("bonds"), dict) else {}
    names: dict[str, str] = {}

    def _name(token: str) -> str:
        if token not in names:
            try:
                names[token] = store.relationships.actor_name(cid, token)
            except Exception:  # noqa: BLE001 — an unreadable card costs a name, not the section
                names[token] = token.partition(":")[2] or token
        return names[token]

    def _meter(value) -> int:
        return min(5, max(0, value)) if isinstance(value, int) and not isinstance(value, bool) else 0

    rows: list[dict] = []
    for key, f in feelings.items():
        if not isinstance(key, str) or not isinstance(f, dict):
            continue
        a, _, b = key.partition("->")
        rows.append({"id": key, "kind": "feeling", "a": a, "b": b,
                     "a_name": _name(a), "b_name": _name(b),
                     **{axis: _meter(f.get(axis)) for axis in FEELING_AXES},
                     "note": _ledger_text(f.get("note")), "type": "", "since_scene": ""})
    for key, b_rec in bonds.items():
        if not isinstance(key, str) or not isinstance(b_rec, dict):
            continue
        a, _, b = key.partition("|")
        rows.append({"id": key, "kind": "bond", "a": a, "b": b,
                     "a_name": _name(a), "b_name": _name(b),
                     **dict.fromkeys(FEELING_AXES, 0),
                     "note": "", "type": _ledger_text(b_rec.get("type")),
                     "since_scene": _ledger_text(b_rec.get("since_scene"))})
    # Bonds after feelings, each group by its own key, so the table has a stable
    # order across reads -- a row that moves between two identical-looking loads
    # is a row the reader stops trusting.
    rows.sort(key=lambda r: (r["kind"] == "bond", r["id"]))
    return rows


@router.get("/campaigns/{cid}/provenance")
def get_provenance(cid: str):
    """Why each continuity line is there: the quote, speaker and certainty
    behind every edit that landed, keyed `"<kind>/<id>#<field>"`.

    `absorb/parse.py` has always asked the extractor to cite itself and
    `absorb/routing.py` has always weighed those citations into a band — and
    both were thrown away the moment the edit applied, so the citation existed
    for exactly as long as the review row you judged it on. Keeping it costs
    disk and no tokens.

    One flat read of one file: unlike the ledger this joins nothing, so it needs
    neither the lock nor the per-section tolerance. `store.provenance.read` is
    itself tolerant of a garbled file — this backs display markers, and one bad
    byte must cost the markers rather than the page.

    A field with no entry is the normal case and always will be: the later
    absorb phases rest on no transcript citation, records written before this
    existed have none, and a hand-edited record has none either. The client
    renders those as uncited rather than hiding them.
    """
    _campaign_root_or_404(cid)
    data = store.provenance.read(cid)
    if not data:
        return data
    # The recording scene is labelled here rather than at write time, for the
    # reason `get_ledger` labels its own: a scene can be renamed, and a title
    # frozen into the citation would then name a scene that no longer exists.
    # Tolerant — an unreadable scene list costs the labels, not the markers.
    try:
        titles = {s["id"]: s["title"] for s in store.scenes.list_scenes(cid)}
    except Exception:  # noqa: BLE001 — labels degrade, the citations do not
        titles = {}
    return {k: {**v, "scene_title": titles.get(v.get("scene"), v.get("scene", ""))}
            if isinstance(v, dict) else v
            for k, v in data.items()}


@router.get("/campaigns/{cid}/ledger")
def get_ledger(cid: str):
    """The continuity ledger (#117): what the campaign still owes, in one read.

    Six sections, and one route rather than six, because they are read together
    and share one failure policy — a garbled plot.json, commitments.json,
    facts.json or relationships.json costs its own section and nothing else, the
    same tolerance `plot.render_open` and `get_changes` already apply. Splitting
    them would put that policy in six places and make the page reason about six
    loading states to render one view.

    Each thread, commitment and standing fact carries the scene it came from,
    resolved the same way `get_changes` resolves its labels: the title from the
    scene list, the in-fiction date from the chronicle. For a thread or a
    commitment that is the scene that last moved it; for a fact it is the scene
    that recorded it, since a fact's text never changes after that (#114) — a
    fact that stopped being true is retired rather than rewritten.

    `retired` is that other half, and it is the reason this route grew: a
    retired fact and its `superseded_by` pointer have been on disk since #114
    and never left the server, so the one thing the ledger keeps that a snapshot
    cannot — the chain saying WHEN a truth stopped being true and what replaced
    it — was unreadable from the client. Its rows carry two resolved scenes, the
    one that recorded the fact and the one that ended it, because a retired row
    is dated twice and both dates are the point of it.

    Contradictions are the section this view is named for and are absent until
    #111 gives them a record; commitment aging (overdue/stale) is #103's, which
    reads the `due` and `last_scene` this already returns.
    """
    _campaign_root_or_404(cid)

    def _tolerant(read):
        try:
            return read()
        except Exception:  # noqa: BLE001 — a garbled file empties its section, not the view
            return []

    # All seven sources are read under ONE campaign lock, because they are seven
    # files and a save writes them one after another: `put_chronicle` holds this
    # same lock while it records the chronicle and then applies the absorb's
    # plot, commitment, fact and relationship edits. Reading without it can catch
    # that sequence half done and return a new fact beside the still-open
    # commitment the very same save fulfilled — or, since #114, beside the
    # standing fact that same save retired -- and the page keeps that
    # contradictory snapshot until something else bumps its revision. A
    # continuity view that contradicts itself is worse than one that is a moment
    # stale, which is the whole reason the writer takes the lock across the
    # sequence rather than per file.
    #
    # The two halves of the fact ledger are read here for a sharper version of
    # the same reason: `facts.record` retires a fact and files its replacement in
    # ONE write, so an unlocked pair of reads is exactly where a supersession
    # chain shows up with both ends standing, or with neither.
    #
    # It is a read, so it holds the lock only for the reads: the projections
    # below work on data already in hand.
    with store.locks.campaign_lock(cid):
        scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
        try:
            chron = store.chronicle.read_chronicle(cid)
        except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
            chron = {}
        open_threads = _tolerant(lambda: store.plot.open_threads(cid))
        owed = _tolerant(lambda: store.commitments.open_commitments(cid))
        standing = _tolerant(lambda: store.facts.active(cid))
        ended = _tolerant(lambda: store.facts.retired(cid))
        # The whole projection sits inside `_tolerant`, not just the read: it
        # resolves a name per actor off the cards, so a broken card empties this
        # section rather than 500ing the view around it.
        bonds = _tolerant(lambda: _ledger_relationships(cid))
    # Unparseable is not the only way that file can be wrong. `read_chronicle`
    # is a bare `json.loads`, so a chronicle.json holding `[]` -- valid JSON of
    # the wrong shape -- returns a list and raises nothing, and the `.get` below
    # would then 500 the whole view for any campaign with one open thread. The
    # shape is checked where it is used rather than trusted from the read.
    if not isinstance(chron, dict):
        chron = {}

    # The text coercion these projections run on now lives at module scope
    # (`_ledger_text`), because the relationships projection needs the same rule
    # and it has to be the same rule. Bound to the local name the projections
    # below already use.
    _txt = _ledger_text

    def _scene(sid) -> dict:
        # The row's OWN scene id is the third place a wrong shape can arrive,
        # and the one the two checks above do not reach: these projections run
        # outside `_tolerant`, so a commitment whose `last_scene` is `[]` reaches
        # `scenes_by_id.get` as an unhashable key and 500s the view. Coerced per
        # row rather than guarded per section, so one malformed record loses its
        # scene label instead of emptying the section around it.
        #
        # The label's own fields go through `_txt` for the reason the row fields
        # do: `LedgerPanel.sceneNote` interpolates them, so a non-string `date`
        # in a hand-edited chronicle reaches the panel and renders as
        # `[object Object]`. This nested projection feeds the plot and
        # commitment sections, which the recent-facts coercion never touched.
        if not isinstance(sid, str):
            sid = ""
        s = scenes_by_id.get(sid, {})
        c = chron.get(sid)
        c = c if isinstance(c, dict) else {}   # a per-scene entry can be wrong too
        return {"id": sid, "title": _txt(s.get("title"), sid), "date": _txt(c.get("date"))}

    threads = [{**t, "id": _txt(t.get("id")), "title": _txt(t.get("title"), _txt(t.get("id"))),
                "status": _txt(t.get("status"), "open"),
                "last_scene": _txt(t.get("last_scene")),
                "latest_beat": _txt(t.get("latest_beat"))}
               for t in open_threads if isinstance(t, dict)]
    # Derived from `chron` rather than `chronicle.recent`, which sorts on the raw
    # `id` of every record: one list-valued id makes that comparison raise and
    # `_tolerant` empties the entire recent-facts section, losing every good fact
    # to one bad row. Sorting on a coerced key keeps the rest. It also saves a
    # second read of the same file.
    recent = sorted((r for r in chron.values() if isinstance(r, dict)),
                    key=lambda r: _txt(r.get("id")))[-LEDGER_RECENT:]
    return {
        "plot": [{**t, "scene": _scene(t["last_scene"])} for t in threads],
        "commitments": [{**c, "scene": _scene(c["last_scene"])} for c in owed],
        # The fact ledger (#114). `facts.active` normalizes its own rows, like
        # `open_commitments` does, so only the scene label is resolved here.
        "facts": [{**f, "scene": _scene(f["scene"])} for f in standing],
        # The other half of the same file: the facts that stopped being true.
        # BOTH scenes are resolved and neither is redundant -- `scene` is still
        # the one that RECORDED the fact, so a retired row keeps its dated place
        # in the ledger, and `retired_scene` is the one that ended it, which is
        # the only thing on the row saying when it stopped. `superseded_by` stays
        # a bare fact id: it points INTO this same response, and inlining the
        # replacement's text here would ship one sentence twice and let the two
        # copies disagree the moment either is hand-edited.
        "retired": [{**f, "scene": _scene(f["scene"]),
                     "retired_scene": _scene(f["retired_scene"])} for f in ended],
        # `since_scene` is a bond's only date and is blank for every feeling,
        # which resolves to the empty label -- the same row shape the sections
        # above carry, so one renderer serves all of them.
        "relationships": [{**r, "scene": _scene(r["since_scene"])} for r in bonds],
        # Newest first: `chronicle.recent` returns the tail in ascending order,
        # which is right for a recap read forward and backwards for a ledger.
        # `one_line or summary`, the fallback every other chronicle consumer
        # uses (`context/story.py:_story_entries`): a save may leave `one_line`
        # empty, and a row with only its scene metadata is a blank line in the
        # panel rather than a fact.
        # `_scene` carries the same id coercion for the two sections above; a
        # chronicle record's `id` needs it for the same reason, and its text
        # needs `_txt` for the same reason the plot rows above do.
        "chronicle": [{**_scene(r.get("id")),
                       "one_line": _txt(r.get("one_line")) or _txt(r.get("summary")),
                       "date": _txt(r.get("date"))}
                      for r in reversed(recent)],
    }


# ---- campaign cast & suggestions ----
@router.get("/campaigns/{cid}/appearances")
def get_appearances(cid: str):
    _campaign_root_or_404(cid)
    return store.appearances.roster(cid)


@router.get("/campaigns/{cid}/pcs")
def get_campaign_pcs(cid: str):
    _campaign_root_or_404(cid)
    return store.overlay.list_pcs(cid)


@router.post("/campaigns/{cid}/pcs")
def post_campaign_pc(cid: str, body: PCCreate):
    # Campaign-local PC overlay: tags are free strings (no world-vocabulary check).
    _campaign_root_or_404(cid)
    pid, vid = store.overlay.create_pc(cid, body.name, body.tags, body.version_name, body.persona)
    return {"pc": pid, "version": vid}


@router.get("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def get_campaign_image(cid: str, char: str, vid: str, name: str, request: Request):
    _campaign_root_or_404(cid)
    return _serve_image(store.overlay.image_root(cid, char, vid, name), char, vid, name, request=request)


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
async def put_campaign_image(cid: str, char: str, vid: str, name: str, file: UploadFile = File(...)):
    root = _campaign_root_or_404(cid)
    data = await file.read()
    fn = file.filename or ""
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    try:
        stored = store.assets.put_image(root, char, vid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def delete_campaign_image(cid: str, char: str, vid: str, name: str):
    _campaign_root_or_404(cid)
    # tombstone so a still-materialized world image doesn't show back through
    # the overlaid read the moment the campaign's own copy is gone (get_campaign_image).
    store.overlay.delete_image(cid, char, vid, name)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}/promote")
def promote_campaign_image(cid: str, char: str, vid: str, name: str):
    _campaign_root_or_404(cid)
    try:
        store.overlay.promote_image(cid, char, vid, name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    except ValueError as exc:
        # an externally-placed file whose extension we never accepted for upload
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/avatar/focus")
def put_campaign_avatar_focus(cid: str, char: str, vid: str, body: AvatarFocus):
    root = _campaign_root_or_404(cid)
    # a thin campaign may only have this avatar through the inherited world
    # character, so the existence gate must check the overlay union, not croot alone
    names = {i["name"] for i in store.overlay.list_images(cid, char, vid)}
    if store.assets.AVATAR not in names:
        raise HTTPException(status_code=404, detail="image not found")
    # the write always lands campaign-side; overlay.read_focus then finds this
    # campaign focus.json and treats the campaign as authoritative going forward
    store.assets.write_focus(root, char, vid, body.focus)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/copy-from-greeting")
def post_copy_campaign_image_from_greeting(cid: str, char: str, vid: str, body: CopyFromGreeting):
    root = _campaign_root_or_404(cid)
    # the source greeting image may still be inherited (unmaterialized) in a thin
    # campaign; resolve its root through the overlay so the copy still finds it,
    # while the destination character write always targets the campaign root
    src_root = store.overlay.image_root(cid, body.gid, "default", body.name, base="greetings")
    # the free gallery_N slot must account for inherited world gallery images too,
    # or a campaign-side copy can reuse a name that shadows one of them
    taken_names = {i["name"] for i in store.overlay.list_images(cid, char, vid)}
    try:
        # overlay-ok: the two things this reads are handed in already resolved --
        # src_root from overlay.image_root and taken_names from the overlay union
        # above; `root` is only the destination the copy writes to
        stored = store.image_subjects.copy_to_character(root, body.gid, body.name, char, vid, body.slot,
                                                         src_root=src_root, taken_names=taken_names)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="source image not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # overlay-ok: reads back the file copy_to_character just wrote campaign-side,
    # only to report its extension — the overlay union would answer a different
    # question (which image wins), not "what did this call just store"
    p = store.assets.image_path(root, char, vid, stored)
    return {"name": stored, "ext": p.suffix.lstrip(".").lower() if p else ""}


def _campaign_wroot(cid: str):
    return store.campaigns.world_root_of(cid)


@router.get("/campaigns/{cid}/characters")
def get_campaign_characters(cid: str):
    _campaign_root_or_404(cid)
    return store.overlay.list_characters(cid)


@router.get("/campaigns/{cid}/characters/{char}")
def get_campaign_character(cid: str, char: str):
    _campaign_root_or_404(cid)
    try:
        return store.overlay.read_character(cid, char)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")


@router.get("/campaigns/{cid}/characters/{char}/voice-anchor")
def get_campaign_voice_anchor(cid: str, char: str):
    """The anchor as this campaign sees it — its own copy if it has one, else
    the world's (`overlay.voice_anchor_record`).

    A campaign-local character has no world counterpart at all: an NPC accepted
    from an absorb `new_character` proposal exists only here. Without these
    routes such a character could never be given an anchor, so absorb would skip
    its voice check forever -- the one class of character the feature most
    obviously wants to cover (#59/#61)."""
    _campaign_root_or_404(cid)
    try:
        store.characters.read_character(store.overlay.char_root(cid, char), char)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"voice_anchor": store.overlay.voice_anchor_record(cid, char)["text"]}


@router.put("/campaigns/{cid}/characters/{char}/voice-anchor")
def put_campaign_voice_anchor(cid: str, char: str, body: VoiceAnchorSave):
    """Write the anchor campaign-side, which is how the per-file overlay records
    a divergence — the same shape as any other campaign edit to an inherited
    record. A blank body opts the character out of voice checks in this campaign
    WITHOUT touching the world's anchor: see `overlay.set_voice_anchor` for why
    that cannot simply delete the local copy."""
    _campaign_root_or_404(cid)
    try:
        store.characters.read_character(store.overlay.char_root(cid, char), char)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    store.overlay.set_voice_anchor(cid, char, body.voice_anchor)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/voice-anchor/generate")
@computes_only
async def post_campaign_voice_anchor_generate(cid: str, char: str,
                                              client: LLMClient = Depends(get_llm)):
    """Draft an anchor from the character's card, resolved through the overlay so
    a campaign-local character (which has no world copy) can use it too. Preview
    only — the caller persists with PUT."""
    _campaign_root_or_404(cid)
    conn = _require_connection()
    root = store.overlay.char_root(cid, char)
    try:
        ch = store.characters.read_character(root, char)
        card = store.characters.read_card(root, char, ch["meta"]["default_version"])
    except (store.characters.CharacterNotFound, store.characters.VersionNotFound):
        raise HTTPException(status_code=404, detail="character not found")
    try:
        # See the world-side route: `{}` and `{"data": ["speech"]}` are both
        # supported card state, and the template renders "(none)" per field.
        data = card.get("data")
        text = await _bounded_call(client.complete(
            store.voice_anchors.build_prompt(data if isinstance(data, dict) else {}), conn))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    return {"voice_anchor": store.voice_anchors.parse_output(text)}


@router.put("/campaigns/{cid}/characters/{char}")
def put_campaign_character(cid: str, char: str, body: DefaultVersion):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        store.characters.set_default_version(root, char, body.default_version)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions")
def post_campaign_character_version(cid: str, char: str, body: VersionCreate):
    _campaign_root_or_404(cid)
    if store.appearances.locked_version(cid, "characters", char) is not None:
        raise HTTPException(status_code=409, detail="character is locked to one version")
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        vid = store.characters.create_version(root, char, body.name, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"version": vid}


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}")
def put_campaign_character_version(cid: str, char: str, vid: str, body: VersionUpdate):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        store.characters.update_version(root, char, vid, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}")
def delete_campaign_character_version(cid: str, char: str, vid: str):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        store.characters.delete_version(root, char, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.get("/campaigns/{cid}/pcs/{pid}")
def get_campaign_pc(cid: str, pid: str):
    _campaign_root_or_404(cid)
    try:
        return store.pcs.read_pc(store.overlay.pc_root(cid, pid), pid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")


@router.put("/campaigns/{cid}/pcs/{pid}")
def put_campaign_pc(cid: str, pid: str, body: PCUpdate):
    # Campaign tags are free strings: no world-vocabulary check on this side.
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        if body.tags is not None:
            store.pcs.set_tags(root, pid, body.tags)
        if body.default_version is not None:
            store.pcs.set_default_version(root, pid, body.default_version)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/pcs/{pid}/versions")
def post_campaign_pc_version(cid: str, pid: str, body: PersonaVersionCreate):
    _campaign_root_or_404(cid)
    if store.appearances.locked_version(cid, "pcs", pid) is not None:
        raise HTTPException(status_code=409, detail="pc is locked to one version")
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        vid = store.pcs.create_version(root, pid, body.name, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    return {"version": vid}


@router.put("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def put_campaign_pc_version(cid: str, pid: str, vid: str, body: PersonaVersionUpdate):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        store.pcs.update_version(root, pid, vid, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/campaigns/{cid}/pcs/{pid}/versions/{vid}")
def delete_campaign_pc_version(cid: str, pid: str, vid: str):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "pcs", pid)
        store.pcs.delete_version(root, pid, vid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{aid}/pick-version")
def post_pick_version(cid: str, kind: str, aid: str, body: PickBody):
    _campaign_root_or_404(cid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    if store.appearances.locked_version(cid, kind, aid) is not None:
        # checked before existence: the sibling versions were purged by the pick
        raise HTTPException(status_code=409, detail=f"{kind}/{aid} is already locked")
    if store.appearances.actor_hash(store.overlay.actor_root(cid, kind, aid), kind, aid, body.version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in campaign")
    try:
        store.appearances.pick_version(cid, kind, aid, body.version)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/{aid}/import-version")
def post_import_version(cid: str, kind: str, aid: str, body: PickBody):
    _campaign_root_or_404(cid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    if store.appearances.actor_hash(_campaign_wroot(cid), kind, aid, body.version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in world")
    try:
        store.appearances.import_version(cid, kind, aid, body.version)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}")
def post_campaign_instantiate(cid: str, kind: str, mid: str, content_id: str):
    _campaign_root_or_404(cid)
    try:
        content = store.modules.read_content(mid, kind, content_id)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ContentNotFound:
        raise HTTPException(status_code=404, detail="content not found")
    try:
        eid = store.overlay.create_entity(cid, kind, content["name"], content["body"],
                                          content.get("keys", ""), "",
                                          fields=_content_fields(kind, content))
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    if content.get("sheet_type"):
        try:
            store.sheets.write(cid, kind, eid, content["sheet_type"], content.get("fields"),
                              expected=None)
        except (store.modules.ModuleNotFound, store.sheets.SheetError) as e:
            # The entity was just created campaign-side via overlay.create_entity
            # with no world counterpart, so overlay.delete_entity removes the
            # campaign file cleanly (no tombstone) -- roll it back so a failed
            # instantiate leaves no sheetless orphan.
            store.overlay.delete_entity(cid, kind, eid)
            raise HTTPException(status_code=400, detail=str(e))
    return {"id": eid}
