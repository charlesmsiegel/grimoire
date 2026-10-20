"""Campaign HTTP endpoints: sync domain."""


from __future__ import annotations

import contextlib
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, Response

from ... import store
from ...llm import LLMClient
from ...store import campaign_settings, compiled, overview
from ...store import paths as library_paths
from ...store.continuity import doc as continuity_doc
from ...store.continuity import effective as continuity_effective
from ...store.inference import settings as inference_settings
from .. import characters as character_routes
from .. import ledger as ledger_routes
from .. import runs
from ..common import (
    PC_HISTORY_MISSES,
    _campaign_root_or_404,
    _content_fields,
    _display_name_or_400,
    _dump,
    _page_of,
    _page_window,
    _response_body,
    _serve_image,
    _serve_image_file,
    _sheet_failure_status,
    _upload_image_ext,
    _with_descriptions,
    _write_response,
    computes_only,
    draft_completion,
    get_llm,
    image_draft_prompt,
    image_id_field,
    image_promote_guards,
    leaves_campaign_unchanged,
    pc_history_404,
    record_art_mutation,
    require_inference,
    stamps_campaign_write,
)
from ..models import (
    AdvanceTime,
    AvatarFocus,
    CalendarConfig,
    CampaignClimate,
    CharacterBirthdate,
    CharacterCreate,
    CharacterTags,
    CopyFromGreeting,
    DefaultVersion,
    ForkCampaign,
    GroupStateSave,
    ImageDescription,
    ImagePromote,
    NameBody,
    NewCampaign,
    NoticeMark,
    PCCreate,
    PCUpdate,
    PersonaVersionCreate,
    PersonaVersionUpdate,
    PickBody,
    PinRule,
    RefList,
    ResponseSettings,
    ScheduledEventCreate,
    ScheduledEventEdit,
    SyncPin,
    TaglineSave,
    VersionCreate,
    VersionUpdate,
    VoiceAnchorSave,
)
from ..overview_log import overview_read


from ._router import router
from ._names import _record_name

# ---- campaign sync ----
@router.get("/campaigns/{cid}/incoming")
def get_incoming(cid: str):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        return store.sync.incoming(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc


@router.post("/campaigns/{cid}/incoming/accept")
def post_accept(cid: str, body: RefList):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        store.sync.accept(cid, [_dump(r) for r in body.refs])
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return {"ok": True}


@router.post("/campaigns/{cid}/incoming/reject")
def post_reject(cid: str, body: RefList):
    try:
        store.campaigns.ensure_campaign_slim(cid)
        store.sync.reject(cid, [_dump(r) for r in body.refs])
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    return {"ok": True}


@router.get("/campaigns/{cid}/composition")
def get_composition(cid: str):
    """Every ref this campaign holds against the library, with its state and
    pin -- the join `/incoming`, `/diverged` and `/appearances` are projections
    of, which the composition panel used to assemble client-side (#71)."""
    try:
        store.campaigns.ensure_campaign_slim(cid)
        return {"rows": store.sync.composition(cid)}
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc


@router.put("/campaigns/{cid}/composition/pins")
def put_composition_pin(cid: str, body: SyncPin):
    """Pin one ref against the sync engine, or unpin it (#71). Pinned refs are
    not offered by `/incoming` and are dropped from accept/reject bodies; the
    base hash is never touched, so unpinning restores exactly what was
    waiting."""
    try:
        store.campaigns.ensure_campaign_slim(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    # Refused for a ref the engine never consults -- not in the manifest and
    # not a locked actor -- while somebody is there to read the refusal; a pin
    # on nothing would sit in the file meaning nothing. Unpinning is exempt: it
    # only ever removes, and the ref it names may have left the composition
    # since it was pinned.
    if body.pinned and not store.sync.is_composition_ref(cid, body.ref.kind, body.ref.id):
        raise HTTPException(status_code=404,
                            detail="ref is not part of this campaign's composition")
    return {"pinned": sorted(store.sync.set_pin(cid, body.ref.kind, body.ref.id, body.pinned))}




@router.get("/campaigns/{cid}/changes")
def get_changes(cid: str, limit: int | None = None, offset: int | None = None):
    """The rolling write-back deltas, one row per record, by kind then name.

    With `limit`/`offset` (either, or both) the body is a slice of that same
    listing; sending neither returns all of it, unchanged (#216).

    The slice is taken between naming the records and rendering their diffs,
    which is the only place it can be. The name is what the sort orders by, so
    every surviving row has to resolve before any row can be dropped -- but
    `line_diff` runs per FIELD, and a page that is not going to be sent has no
    reason to pay for it.
    """
    limit, offset = _page_window(limit, offset)
    _campaign_root_or_404(cid)
    data = store.changes.read(cid)
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    named: list[tuple[str, str, str, dict]] = []
    for ref, entry in data.items():
        kind, _, eid = ref.partition("/")
        name = _record_name(cid, kind, eid)
        if name is None:
            continue  # record deleted since the change was captured
        named.append((kind, name, eid, entry))
    named.sort(key=lambda r: r[:2])   # kind, then name
    out: list[dict] = []
    for kind, name, eid, entry in _page_of(named, limit, offset):
        sid = entry.get("scene", "")
        s, c = scenes_by_id.get(sid, {}), chron.get(sid, {})
        fields = [{"field": f.get("field", ""), "label": f.get("label", ""),
                   "diff": store.changes.line_diff(f.get("before", ""), f.get("after", ""))}
                  for f in entry.get("fields", [])]
        out.append({"ref": {"kind": kind, "id": eid}, "name": name,
                    "scene": {"id": sid, "title": s.get("title", sid), "date": c.get("date", "")},
                    "fields": fields})
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
#:
#: Still a cap after #216 gave the route above a `limit`/`offset`, and for the
#: reason that route did not have: this one was already bounded. What #216 paged
#: were the listings that returned EVERYTHING and grew with play. Reaching past
#: this cap is a different feature — the store retains by age and by bytes, so
#: what falls off the end is not something a bare offset can name.
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
    reason = entry.get("why", "") if isinstance(entry.get("why"), str) else ""
    target = plan.get("target") if isinstance(plan, dict) else None
    if (isinstance(target, dict) and target.get("w") in {"group_members", "membership"}
            and (target.get("retired") or "relocating" in target or target.get("kind") != "groups")):
        reversible = False
        reason = "This membership offer is retired, its move is incomplete, or its record is no longer a group."
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
        "why": reason,
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
    except store.undo.EntryNotFound as err:
        raise HTTPException(status_code=404, detail="that change is not in this "
                                                    "campaign's history") from err
    except store.undo.AlreadyUndone as err:
        raise HTTPException(status_code=409, detail="that change has already been undone") from err
    except store.undo.NotUndoable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except store.undo.UndoConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    return {"ok": True, "entry": _journal_row(cid, written, scenes_by_id, chron, {})}


