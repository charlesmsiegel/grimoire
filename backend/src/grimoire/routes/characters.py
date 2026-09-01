"""World characters: versions, taglines, voice anchors, Chub links, card
import/export, localization and per-version images."""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import store
from ..llm import LLMClient
from ..llm_errors import LLMError
from . import runs
from .common import (
    THUMB_W,
    _bounded_call,
    _campaign_root_or_404,
    _card_data,
    _display_name_or_400,
    _require_connection,
    _serve_image,
    _upload_image_ext,
    _world_char_version_or_404,
    _world_root_or_404,
    draft_completion,
    get_llm,
    image_draft_prompt,
)
from .models import (
    AvatarFocus,
    CharacterBirthdate,
    CharacterCreate,
    ChubImportBody,
    ChubSourceBody,
    DefaultVersion,
    ImageDescription,
    NameBody,
    TaglineSave,
    VersionCreate,
    VersionUpdate,
    VoiceAnchorSave,
)

router = APIRouter()


def _sse(payload: dict) -> str:
    """One SSE data frame. Local, like `runs.sse` and `streaming._sse`: this
    module is not allowed to import either (`test_import_guard`), and the four
    streaming routes here were each spelling the frame out inline."""
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/worlds/{wid}/characters")
def get_world_characters(wid: str):
    return store.characters.list_characters(_world_root_or_404(wid))


@router.post("/worlds/{wid}/characters")
def post_world_character(wid: str, body: CharacterCreate):
    cid, vid = store.characters.create_character(
        _world_root_or_404(wid), body.name, body.version_name, body.card
    )
    return {"character": cid, "version": vid}


@router.get("/worlds/{wid}/characters/chub-unlinked")
def get_world_characters_chub_unlinked(wid: str):
    return {"versions": store.characters.find_unlinked_versions(_world_root_or_404(wid))}


@router.get("/worlds/{wid}/characters/{cid}")
def get_world_character(wid: str, cid: str):
    try:
        return store.characters.read_character(_world_root_or_404(wid), cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")


@router.put("/worlds/{wid}/characters/{cid}")
def put_world_character(wid: str, cid: str, body: DefaultVersion):
    try:
        store.characters.set_default_version(_world_root_or_404(wid), cid, body.default_version)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.put("/worlds/{wid}/characters/{cid}/name")
def put_world_character_name(wid: str, cid: str, body: NameBody):
    """Rename the container (#13). The card's own `data.name` is saved with the
    card; this is the name the grid, the cast panel and the `meta.name` prompt
    sections read, and the two used to be unable to agree."""
    name = _display_name_or_400(body.name)
    try:
        store.characters.set_name(_world_root_or_404(wid), cid, name)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"ok": True}


@router.put("/worlds/{wid}/characters/{cid}/birthdate")
def put_world_character_birthdate(wid: str, cid: str, body: CharacterBirthdate):
    try:
        store.characters.set_birthdate(_world_root_or_404(wid), cid, body.birthdate)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-source")
def post_world_character_chub_source(wid: str, cid: str, vid: str, body: ChubSourceBody):
    root = _world_root_or_404(wid)
    url = store.chub.normalize_link(body.url)
    if url is None:
        raise HTTPException(status_code=400, detail="not a valid URL")
    try:
        store.characters.set_chub_source(root, cid, vid, url)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"chub_source": url}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-source")
def delete_world_character_chub_source(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.clear_chub_source(root, cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"chub_source": ""}


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-gallery")
def post_world_character_chub_gallery(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        node = store.characters.resolve_chub_node(root, cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except store.chub.ChubFetchError:
        raise HTTPException(status_code=404, detail="could not fetch from chub.ai")

    def event_stream():
        for ev in store.characters.download_chub_gallery_stream(root, cid, vid, node):
            yield _sse(ev)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/chub-lorebooks")
def post_world_character_chub_lorebooks(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        return store.characters.download_chub_lorebooks(root, cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except store.chub.ChubFetchError:
        raise HTTPException(status_code=404, detail="could not fetch from chub.ai")


@router.delete("/worlds/{wid}/characters/{cid}")
def delete_world_character(wid: str, cid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.delete_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    store.overlay.forget_world_record(root, "characters", cid)   # #225
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/versions")
def post_world_version(wid: str, cid: str, body: VersionCreate):
    try:
        vid = store.characters.create_version(_world_root_or_404(wid), cid, body.name, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"version": vid}


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}")
def put_world_version(wid: str, cid: str, vid: str, body: VersionUpdate):
    try:
        store.characters.update_version(_world_root_or_404(wid), cid, vid, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}")
def delete_world_version(wid: str, cid: str, vid: str):
    try:
        store.characters.delete_version(_world_root_or_404(wid), cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.get("/worlds/{wid}/characters/{cid}/tagline")
def get_character_tagline(wid: str, cid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"tagline": store.taglines.read(root, cid)}


@router.put("/worlds/{wid}/characters/{cid}/tagline")
def put_character_tagline(wid: str, cid: str, body: TaglineSave):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    store.taglines.write(root, cid, body.tagline)
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/tagline/generate", status_code=202)
def post_character_tagline_generate(
        wid: str, cid: str, request: Request,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Start a drafted tagline for this character. 202 and a run to poll.

    Preview only — the caller persists via PUT on Save, so Generate-then-cancel
    (e.g. the import popup's Skip) leaves nothing written, and the run's result
    is reaped rather than stored. That is the right durability for a sentence
    nobody has agreed to yet.
    """
    root = _world_root_or_404(wid)
    conn = _require_connection("tagline")
    try:
        ch = store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    card = store.characters.read_card(root, cid, ch["meta"]["default_version"])
    messages = store.taglines.build_prompt(_card_data(card))

    async def work():
        return await draft_completion(
            client, conn, messages, "tagline",
            lambda text: {"tagline": store.taglines.parse_output(text)})

    return runs.run_draft(request.app, runs.world_subject(wid), "tagline",
                          x_grimoire_attempt, work)


def _tagline_target(root, cid: str) -> tuple[dict | None, str, str]:
    """The card to derive `cid`'s tagline from, or `(None, "", reason)` when
    there is nothing to derive (#57).

    Read now rather than taken from the roster scan, because a run walks that
    scan for as long as a provider call per character takes, and everything it
    recorded can move. The default version most of all: changing it leaves the
    old card in place, so a stale id still READS -- it just answers with the
    wrong card, and the tagline written from it is no longer blank for a later
    run to correct.

    The blank is checked here, before the call and not only after it: a target
    somebody else filled in the meantime is a generation whose answer this route
    would throw away, and it is billed either way.
    """
    try:
        ch = store.characters.read_character(root, cid)
        vid = ch["meta"]["default_version"]
        card = store.characters.read_card(root, cid, vid)
    except (store.characters.CharacterNotFound, store.characters.VersionNotFound):
        # Deleted or unreadable since the scan. One unusable card is not a
        # reason to abandon the rest of the roster.
        return None, "", "unreadable card"
    if store.taglines.read(root, cid):
        return None, "", "already set"
    return card, vid, ""


def _commit_tagline(root, cid: str, vid: str, card: dict, tagline: str) -> str:
    """Write the derived sentence, or say why it was not written (#57).

    Fenced on the card it was derived from being still on disk and still the
    same bytes -- one check rather than a list of the ways the character could
    have moved while the model was answering, because it covers all of them:

    - **Deleted.** `taglines.write` creates the parent directory, so writing to
      a character who is gone rebuilds `characters/<cid>/` holding nothing but
      tagline.md -- invisible to every listing (they need character.md) and
      still enough to make `create_character` suffix the id of the next
      character given that name.
    - **Deleted and recreated under the same name.** The slug is free again the
      moment the directory goes, so the replacement takes it, and no existence
      check can tell the two apart. Its card can -- unless it is byte-identical,
      which is deliberately not distinguished: a sentence derived from exactly
      the text the new card holds describes it exactly as well.
    - **Edited.** The sentence describes text that is no longer there. Leaving
      it blank is what lets a re-run derive it from what the card says now.
    - **Re-pointed to another default version.** This one the card comparison
      does NOT catch on its own, and that is why the default is checked
      separately: re-pointing leaves the old version file untouched, so the
      bytes still match while the character it describes has moved. A tagline
      is character-wide, so writing the old version's sentence would put it on
      a character whose card now says something else -- and no later run would
      fix it, because it is no longer blank.

    And the blank once more, for the same reason it is checked before the call:
    a sentence saved by hand during the generation is not this run's to replace.
    """
    try:
        if store.characters.read_character(root, cid)["meta"]["default_version"] != vid:
            return "changed"
        if store.characters.read_card(root, cid, vid) != card:
            return "changed"
    except (store.characters.CharacterNotFound, store.characters.VersionNotFound):
        return "gone"
    if store.taglines.read(root, cid):
        return "already set"
    store.taglines.write(root, cid, tagline)
    return ""


@router.post("/worlds/{wid}/characters/taglines/generate")
async def post_world_taglines_generate(wid: str, client: LLMClient = Depends(get_llm)):
    """Derive a tagline for every character in this world that has none (#57).

    The per-character route above is a *preview*: it hands the sentence back and
    writes nothing, so the import popup's Skip leaves no trace (#59). This one
    writes as it goes, and the difference is deliberate. A world imported in
    bulk arrives with a roster of blank taglines, and the thing that made that
    unfixable was reviewing them one modal at a time; asking for the roster to
    be derived IS the review, and every sentence it writes stays editable in the
    character editor afterwards.

    What keeps that safe is the one rule this route never breaks: **it only ever
    fills a blank**. A character who already has a tagline -- hand-written or
    generated -- is not a target, and the blank is re-checked immediately before
    each write, so a sentence saved by hand *during* the run survives it too.
    `taglines.py` says a hand-written tagline must not silently expire; a batch
    that overwrote one would be exactly that, arriving all at once.

    That re-check has the reach every other check here has, and no more: it sees
    what is on disk at the moment it looks, so a save landing between it and the
    write is still lost. Nothing holds a lock across the two. The window it
    closes is the minutes this run takes; the one it leaves open is instructions
    wide, and closing that would need a lock this world-scoped write has never
    had.

    Sequential, one call at a time -- not because concurrency is unsafe here
    (`store.usage`'s ledger is append-only and says so), but because it buys
    less than it costs. A roster's worth of calls issued at once is what a
    provider's rate limiter exists to refuse, nothing here would throttle them,
    and "stop at the first failure" is not a rule that means anything with
    twenty in flight. Every other bulk path in this app works the same way.

    The first provider failure stops the run rather than spending three hundred
    calls learning the same thing three hundred times. Stopping costs nothing,
    because a re-run targets whatever is still blank: the work already written
    is not re-done, and the work that never happened is exactly what comes back.
    A client that disconnects mid-run gets the same deal -- the generator stops,
    and what landed stays landed.

    Which is also why this is NOT a detached run (`app.state.runs`). That
    machinery exists because a scene turn cannot simply be run again -- its
    output is a transcript nobody can regenerate. This can be run again, and a
    second pass costs only the calls the first one never made, so resumability
    here is a property of the work rather than something built around it. The
    one thing that buys elsewhere and not here: `PUT /config/data-dir` is
    refused while a *run* is live, and this is not one, so nothing stops the
    storage location moving mid-derive. What that would cost is not symmetric
    -- `root` is captured here, so taglines would keep landing in the tree this
    scanned, while `store.usage` resolves the ledger's home per row and would
    file the cost against the newly chosen library. So the loop notices instead:
    a root that no longer matches stops the run. That is narrower than the
    refusal a run gets (the move still succeeds) but it ends the spending, and
    the idempotence covers the rest -- those characters are still blank in the
    new root, and deriving there fills them.
    """
    root = _world_root_or_404(wid)
    conn = _require_connection("tagline")
    # Off the event loop: `list_characters` stats every version and every image
    # of every character, which is ~200ms on a large world (see
    # `list_undescribed_images`). Eagerly, before the response is returned, so
    # `total` is known up front and a 404/409 is still an ordinary status code
    # rather than an error frame inside a 200.
    roster = await run_in_threadpool(store.characters.list_characters, root)
    targets = [c for c in roster if not c["tagline"]]

    async def event_stream():
        yield _sse({"total": len(targets)})
        written = skipped = 0
        stopped = False
        frame: dict = {}
        try:
            for done, c in enumerate(targets, start=1):
                cid = c["id"]
                frame = {"done": done, "character": cid, "name": c["name"]}
                if store.worlds.world_root(wid) != root:
                    # The storage location moved under the run (see above).
                    # Checked per character rather than once, because the whole
                    # point is to stop paying into a library nobody is looking
                    # at, and the next call is where that money goes.
                    stopped = True
                    yield _sse({**frame, "error": {
                        "detail": "the storage location changed while this was running",
                        "kind": "store_moved"}})
                    break
                card, vid, skip = _tagline_target(root, cid)
                if card is None:
                    skipped += 1
                    yield _sse({**frame, "skipped": skip})
                    continue
                messages = store.taglines.build_prompt(_card_data(card))
                try:
                    with store.usage.meter("tagline") as m:
                        text = await _bounded_call(client.complete(messages, conn, m.usage))
                except LLMError as exc:
                    stopped = True
                    yield _sse({**frame, "error": {"detail": exc.detail, "kind": exc.kind}})
                    break
                tagline = store.taglines.parse_output(text)
                if not tagline:
                    skipped += 1
                    yield _sse({**frame, "skipped": "blank"})
                    continue
                skip = _commit_tagline(root, cid, vid, card, tagline)
                if skip:
                    skipped += 1
                    yield _sse({**frame, "skipped": skip})
                    continue
                written += 1
                yield _sse({**frame, "tagline": tagline})
        except Exception as exc:  # noqa: BLE001 — surface a stream error like the localize route
            # A failed write, or anything else this loop did not anticipate. The
            # response is already a 200, so the only way to say so is a frame --
            # and a summary still follows it, because the caller's report is
            # "what did this run manage", which is a question a crash also has
            # an answer to. A hang-up is not caught here and must not be: both
            # `GeneratorExit` and `asyncio.CancelledError` are BaseExceptions,
            # so a client that stopped the run unwinds instead of being sent an
            # error frame nobody is reading.
            stopped = True
            yield _sse({**frame, "error": {"detail": str(exc), "kind": "tagline"}})
        summary = {"total": len(targets), "written": written,
                   "skipped": skipped, "stopped": stopped}
        yield _sse({"summary": summary})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/worlds/{wid}/characters/{cid}/voice-anchor")
def get_character_voice_anchor(wid: str, cid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"voice_anchor": store.voice_anchors.read(root, cid)}


@router.put("/worlds/{wid}/characters/{cid}/voice-anchor")
def put_character_voice_anchor(wid: str, cid: str, body: VoiceAnchorSave):
    """Set (or, with a blank body, remove) the anchor absorb judges drift against.

    Removing it is a real operation, not a no-op: an anchorless character is not
    judged at all, so clearing the text is how a user opts a character back out
    of drift detection."""
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    store.voice_anchors.write(root, cid, body.voice_anchor)
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/voice-anchor/generate", status_code=202)
def post_character_voice_anchor_generate(
        wid: str, cid: str, request: Request,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Start a drafted voice anchor for this character. 202 and a run to poll.

    Preview only, like tagline/generate — the caller persists via PUT on Save,
    so an anchor is never written without review (#59).
    """
    root = _world_root_or_404(wid)
    conn = _require_connection("voice-anchor")
    try:
        ch = store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    card = store.characters.read_card(root, cid, ch["meta"]["default_version"])
    messages = store.voice_anchors.build_prompt(_card_data(card))

    async def work():
        return await draft_completion(
            client, conn, messages, "voice-anchor",
            lambda text: {"voice_anchor": store.voice_anchors.parse_output(text)})

    return runs.run_draft(request.app, runs.world_subject(wid), "voice-anchor",
                          x_grimoire_attempt, work)


_EXPORT_MEDIA = {"json": "application/json", "png": "image/png", "charx": "application/zip"}


@router.post("/worlds/{wid}/characters/import")
async def post_character_import(wid: str, file: UploadFile = File(...),
                                format: str = Form(...), into: str | None = Form(None),
                                name: str | None = Form(None),
                                version_name: str | None = Form(None)):
    """Import a card. `into` adds it as a version of an existing character;
    `name` names a new character and `version_name` names the version, which is
    otherwise derived from the card and so is the same for every version."""
    root = _world_root_or_404(wid)
    data = await file.read()
    try:
        cid, vid = store.characters.import_card(root, data, format, into_cid=into, name=name,
                                                version_name=version_name)
    except store.cards.CardParseError as exc:
        raise HTTPException(status_code=400, detail=f"could not parse card: {exc}")
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"character": cid, "version": vid}


@router.post("/worlds/{wid}/characters/import/chub")
def post_character_import_chub(wid: str, body: ChubImportBody):
    root = _world_root_or_404(wid)
    try:
        return store.characters.import_from_chub(root, body.url, into_cid=body.into,
                                                 into_vid=body.into_version,
                                                 version_name=body.version_name)
    except store.chub.ChubParseError:
        raise HTTPException(status_code=400, detail="not a valid URL")
    except store.chub.ChubFetchError:
        raise HTTPException(status_code=404, detail="could not fetch a character card from that URL")
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/export")
def get_character_export(wid: str, cid: str, vid: str, format: str = "json"):
    root = _world_root_or_404(wid)
    if format not in _EXPORT_MEDIA:
        raise HTTPException(status_code=400, detail="unknown format")
    try:
        blob, filename = store.characters.export_card(root, cid, vid, format)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    # Named like the campaign exports: the frontend offers these as plain
    # download links, and without the header the browser saves them as "export".
    return Response(content=blob, media_type=_EXPORT_MEDIA[format],
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
def post_character_localize(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        card = store.characters.read_card(root, cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")

    def event_stream():
        # localize_card rewrites `card` in place field-by-field; save whenever any
        # rewrite landed, including on a mid-stream error or client disconnect
        # (GeneratorExit skips the except but runs the finally).
        state = {"changed": False, "saved": False}

        def save():
            if state["changed"] and not state["saved"]:
                store.characters.update_version(root, cid, vid, card)
                state["saved"] = True

        try:
            for ev in store.localize.localize_card(card, root, cid, vid, wid):
                if ev.get("applied") or ("summary" in ev and ev["summary"].get("localized")):
                    state["changed"] = True
                yield _sse(ev)
            save()  # normal completion: a failed save surfaces as an error frame
        except Exception as exc:  # noqa: BLE001 — surface a stream error like the chat routes
            yield _sse({"error": {"detail": str(exc), "kind": "localize"}})
        finally:
            try:
                save()
            except Exception:  # noqa: BLE001 — a disconnected client can't be told
                pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/lorebook/import")
def post_character_lorebook_import(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        card = store.characters.read_card(root, cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    book = card.get("data", {}).get("character_book") or {}
    created = store.lorebook.commit(root, store.lorebook.from_character_book(book))
    return {"created": created}


@router.get("/worlds/{wid}/images/undescribed")
def list_undescribed_images(wid: str):
    """Every stored image in this world with NO description entry — the backlog
    `DescribeQueue` steps through.

    Key ABSENT, never merely empty: an image reviewed and deliberately left
    undescribed is finished, and re-offering it is how a queue never empties.

    Registered here rather than in `entities.py` because `/{kind}/{eid}` would
    otherwise swallow it — `entities.router` is included last precisely so a
    named route in another module wins, the same arrangement
    `/worlds/{wid}/subjects/untagged` relies on.

    World-scoped. A campaign reaches most of its art through its world, so
    describing it here describes it once; a campaign that has diverged an image
    describes that one in its own editor.

    The cost is a directory walk over every record of every base, and it is
    paid whenever the character page mounts, because the rail button shows the
    count. Measured at ~200ms for a 300-character world with 900 undescribed
    images — an outlier by some way, and one that shrinks as the queue is
    worked. Worth knowing before adding anything else to this loop.
    """
    root = _world_root_or_404(wid)
    out = []
    # One name lookup per RECORD, not per image. A character with a gallery
    # contributes an entry per picture and `_record_display_name` opens a card
    # file, so the naive loop re-read one card once per image in it: 395ms for a
    # 300-character world, on a route that fires whenever the character page
    # mounts. The same mistake `context.art._keyword_scores` makes it easy to
    # make twice.
    # One read per RECORD, yielding both what to call it and which versions it
    # still has. Two separate memos, or a version check outside the memo, is the
    # same per-image re-read this exists to avoid -- which is exactly how it
    # crept back in when the version check was added.
    seen: dict[tuple[str, str], tuple[str, set[str]] | None] = {}
    for base in ("characters", store.pcs.ASSET_BASE, *store.entities.ENTITY_KINDS):
        for item in store.image_descriptions.undescribed(root, base):
            key = (base, item["id"])
            if key not in seen:
                seen[key] = _record_name_and_versions(root, base, item["id"])
            found = seen[key]
            name = found[0] if found else None
            if name is None or (found is not None and found[1] and item["vid"] not in found[1]):
                # An asset folder whose record -- or whose VERSION -- is gone.
                # Not listed: the queue would offer an image no route can
                # describe, and the PUT it issues is a 404 by design, so the
                # entry could never be cleared and would be re-offered forever.
                continue
            out.append({"kind": base, "id": item["id"], "vid": item["vid"],
                        "name": item["name"], "record_name": name,
                        "url": _undescribed_url(wid, base, item)})
    return out


#: Every base a whole-world image sweep walks: `list_undescribed_images`' list,
#: plus greetings. `ENTITY_KINDS` is spread rather than spelled out, so a sixth
#: entity kind reaches the gallery by existing.
#:
#: The describe backlog above does not walk greetings; this does. Greeting art is
#: real, browsable art, and the sidecar that governs it is `image_subjects` (who
#: is in the picture) rather than `image_descriptions` (what it depicts) -- so a
#: greeting image is something the gallery has to show and something the describe
#: queue has nothing to ask about.
GALLERY_BASES = ("characters", store.pcs.ASSET_BASE, *store.entities.ENTITY_KINDS, "greetings")


@router.get("/worlds/{wid}/gallery")
def list_world_gallery(wid: str):
    """Every image this world holds, in one response — the browser #200 asks for.

    A route rather than a client-side fan-out because art hangs off a record AND
    a version: a gallery assembled in the browser is one request per character,
    per PC, per entity of five kinds and per greeting, and then one more per
    version of each actor. The sweep is the same directory walk
    `/images/undescribed` already pays for (see its note on the cost), over one
    more base.

    Registered here, beside that route, for the same route-ordering reason:
    `entities.router` is included last so `/worlds/{wid}/{kind}` cannot swallow
    a named path in another module.

    World-scoped. A campaign reaches most of its art through its world, and the
    art it has diverged is listed in its own editors — the same split
    `/images/undescribed` draws.
    """
    root = _world_root_or_404(wid)
    out = []
    # One record read per RECORD, not per image, and one subjects read per
    # GREETING -- the mistake `list_undescribed_images` documents right above,
    # which a listing over every image in the world would pay for even harder.
    seen: dict[tuple[str, str], tuple[str, set[str]] | None] = {}
    subjects: dict[str, dict[str, list[str]]] = {}
    answered: dict[str, set[str]] = {}
    # The world's character ids, enumerated ONCE. `read_subjects` filters
    # deleted characters out of every entry it returns, and without this it
    # rescans the character directory to learn them per greeting -- an
    # O(greetings x characters) walk on a store that may sit in a synced folder.
    # Its own docstring asks sweeps to pass this; the gallery is a sweep.
    known_cids: set[str] | None = None
    for base in GALLERY_BASES:
        for item in store.image_descriptions.catalog(root, base):
            key = (base, item["id"])
            if key not in seen:
                seen[key] = _record_name_and_versions(root, base, item["id"])
            found = seen[key]
            if found is None or (found[1] and item["vid"] not in found[1]):
                # An asset folder whose record -- or whose VERSION -- is gone.
                # Skipped for `list_undescribed_images`' reason turned around:
                # there is no route that serves these bytes, so a tile over them
                # is a broken image the reader cannot clear.
                continue
            row = {"kind": base, "id": item["id"], "vid": item["vid"],
                   "name": item["name"], "ext": item["ext"], "record_name": found[0],
                   "described": item["described"], "description": item["description"],
                   **_gallery_urls(wid, base, item)}
            if base == "greetings":
                # Who is in the picture, which for greeting art is the sidecar
                # that governs it. Carried here so the gallery can say which
                # tiles the tagging queue is still going to ask about, without a
                # second sweep of the same tree.
                #
                # ANSWERED is key presence (`reviewed_names`), the same test the
                # queue offers by; the LIST is the filtered read, which drops an
                # entry whose value is not a list. Asking one function both
                # questions is what let a hand-edited sidecar read as untagged
                # here while the queue considered it done -- an unfinished tile
                # with no way to resolve it. Null means "not answered"; `[]`
                # means "answered: nobody", including when what was stored was
                # not a list this can render.
                if item["id"] not in subjects:
                    if known_cids is None:
                        known_cids = set(store.characters.character_refs(root))
                    subjects[item["id"]] = store.image_subjects.read_subjects(
                        root, item["id"], known_cids=known_cids)
                    answered[item["id"]] = store.image_subjects.reviewed_names(root, item["id"])
                row["subjects"] = (subjects[item["id"]].get(item["name"], [])
                                   if item["name"] in answered[item["id"]] else None)
            out.append(row)
    return out


@router.get("/campaigns/{cid}/gallery")
def list_campaign_gallery(cid: str):
    """Every image this CAMPAIGN sees, world-inherited and its own alike.

    The world gallery one route up is world-scoped, and says so: a campaign
    reaches most of its art through its world. What that leaves out is the art
    the campaign has of its own -- a diverged avatar, a gallery the campaign
    added -- which is invisible in a world sweep because it is not in the world
    root. A reader who came here from a campaign is asking about the pictures
    that campaign actually renders, so those are the ones this answers with.

    Resolution is `overlay`'s, not a merge written again here. Every row comes
    from `overlay.list_images` and `overlay.read_descriptions`, which is what
    the app serves and describes from -- so a tombstoned image is absent, a
    detached record shows only its own art, a campaign copy shadows the world's
    under the same name whatever it is stored as, and a description written
    campaign-side about inherited art is the one reported. A hand-rolled merge
    here would be a second set of rules to keep in step with those, and the
    first thing to drift would be exactly the case this route exists for.

    URLs are campaign-scoped for EVERY row, inherited ones included. The
    campaign serve route resolves through the same overlay, so one URL shape is
    correct for both origins and no row has to carry where its bytes live.

    Costlier than the world sweep, knowingly: the two roots are both catalogued
    to find the version directories, and then each is resolved through the
    overlay, which reads a campaign sidecar and a world sidecar per version.
    That is the price of an answer that matches what the campaign renders; the
    world route is still there, unchanged, for the world's own question.
    """
    _campaign_root_or_404(cid)
    croot = store.campaigns.campaign_root(cid)
    wroot = store.overlay.wroot_of(cid)
    out = []
    names: dict[tuple[str, str], tuple[str, set[str]] | None] = {}
    subjects = _GreetingSubjects(wroot)
    for base in GALLERY_BASES:
        # Which (record, version) folders exist on EITHER side. The campaign's
        # own art is the point of this route, and a version the world never had
        # is only in the campaign's catalog.
        pairs: set[tuple[str, str]] = set()
        for root in (croot, wroot):
            for item in store.image_descriptions.catalog(root, base):
                pairs.add((item["id"], item["vid"]))
        for rid, vid in sorted(pairs):
            key = (base, rid)
            if key not in names:
                names[key] = _overlay_name_and_versions(cid, base, rid)
            found = names[key]
            # Same gate as the world sweep: an asset folder whose record -- or
            # whose version -- is gone serves no bytes, so a tile over it is a
            # broken image the reader cannot clear.
            if found is None or (found[1] and vid not in found[1]):
                continue
            imgs = store.overlay.list_images(cid, rid, vid, base)
            if not imgs:
                continue   # every image here was tombstoned campaign-side
            desc = store.overlay.read_descriptions(cid, rid, vid, base)
            for item in imgs:
                base_url = _actor_image_url(f"/api/campaigns/{quote(cid, safe='')}",
                                            base, rid, vid, item["name"])
                v = quote(item["v"], safe="")
                row = {"kind": base, "id": rid, "vid": vid, "name": item["name"],
                       "ext": item["ext"], "record_name": found[0],
                       # Key PRESENCE, the distinction the sidecar turns on: an
                       # image reviewed and left blank is described.
                       "described": item["name"] in desc,
                       "description": desc.get(item["name"], ""),
                       "url": f"{base_url}?v={v}",
                       "thumb": f"{base_url}?w={THUMB_W}&v={v}"}
                if base == "greetings":
                    row["subjects"] = subjects.lookup(rid, item["name"])
                out.append(row)
    return out


class _GreetingSubjects:
    """Who is in each greeting picture, read WORLD-side and cached per greeting.

    World-side because that is where the sidecar is written -- it is what the
    tagging queue walks -- so a campaign asking who is in a greeting picture is
    asking the world's answer, not one of its own.

    A small object rather than three dicts threaded through the loop: the
    caching, the one-time character enumeration and the answered/list split are
    one concern, and inlining them is what pushed the route over the complexity
    gate. The split itself is the world sweep's, verbatim: ANSWERED is key
    presence and the LIST is the filtered read, so a hand-edited sidecar cannot
    read as untagged here while the queue considers it done. `None` means "not
    answered"; `[]` means "answered: nobody".
    """

    def __init__(self, wroot):
        self._wroot = wroot
        self._subjects: dict[str, dict[str, list[str]]] = {}
        self._answered: dict[str, set[str]] = {}
        self._known: set[str] | None = None

    def lookup(self, gid: str, name: str) -> list[str] | None:
        if gid not in self._subjects:
            if self._known is None:
                # Once for the whole sweep: `read_subjects` otherwise rescans
                # the character directory per greeting.
                self._known = set(store.characters.character_refs(self._wroot))
            self._subjects[gid] = store.image_subjects.read_subjects(
                self._wroot, gid, known_cids=self._known)
            self._answered[gid] = store.image_subjects.reviewed_names(self._wroot, gid)
        if name not in self._answered[gid]:
            return None
        return self._subjects[gid].get(name, [])


def _overlay_name_and_versions(cid: str, base: str, rid: str) -> tuple[str, set[str]] | None:
    """`_record_name_and_versions` through the overlay: the campaign's record if
    it has one, the world's if it inherits, and None when neither can be read.

    Split from the world-rooted one rather than parameterised because the reads
    differ by more than a root -- `overlay.read_character` applies tombstones
    and detachment, which a bare root read cannot see.
    """
    try:
        if base == "characters":
            d = store.overlay.read_character(cid, rid)
            return str(d["meta"]["name"]), {v["id"] for v in d["versions"]}
        if base == store.pcs.ASSET_BASE:
            d = store.overlay.read_pc(cid, rid)
            return str(d["meta"]["name"]), {v["id"] for v in d["versions"]}
        if base == "greetings":
            g = store.overlay.read_greeting(cid, rid)
            return str(g.get("name") or rid), set()
        e = store.overlay.read_entity(cid, base, rid)
        return str(e["meta"].get("name") or rid), set()
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound,
            store.entities.EntityNotFound, store.greetings.GreetingNotFound,
            KeyError, OSError, UnicodeDecodeError):
        # The same set the world-rooted helper catches, and for the same reason:
        # skip the folder rather than 500 a whole gallery over one record
        # somebody deleted by hand.
        return None


def _actor_image_url(prefix: str, base: str, rid: str, vid: str, name: str) -> str:
    """One tile's serving URL under `prefix` (`/api/worlds/x` or
    `/api/campaigns/y`). An actor's art is per version; an entity's is keyed on
    a fixed `default`, so its URL has no version segment to carry.

    Quoted segment by segment, like `_undescribed_url`'s: `assets.storable`
    accepts names URL syntax owns.
    """
    if base in ("characters", store.pcs.ASSET_BASE):
        return (f"{prefix}/{base}/{quote(rid, safe='')}"
                f"/versions/{quote(vid, safe='')}"
                f"/images/{quote(name, safe='')}")
    return f"{prefix}/{base}/{quote(rid, safe='')}/images/{quote(name, safe='')}"


def _gallery_urls(wid: str, base: str, item: dict) -> dict:
    """Full and thumbnail URLs for one gallery tile, both carrying the `?v=`
    token the catalog resolved.

    Versioned on purpose: a bare URL is served `no-cache` and revalidates, which
    for a grid of every image in a world is a request per tile on every render.
    The thumb is the `?w=` downscale `store.thumbs` does on the fly — a gallery
    of full-resolution art is tens of megabytes for pictures drawn at 154px.
    """
    base_url = _undescribed_url(wid, base, item)
    v = quote(item["v"], safe="")
    return {"url": f"{base_url}?v={v}", "thumb": f"{base_url}?w={THUMB_W}&v={v}"}


def _record_name_and_versions(root, base: str, rid: str) -> tuple[str, set[str]] | None:
    """What to call the record an undescribed image hangs off, and which version
    ids it still has — or None when there is no such record any more.

    An empty version set means "this kind has no versions": entity art is keyed
    on a fixed `default`, so the record existing is the whole question there.

    The versions matter because an asset directory can outlive its version.
    Uploading campaign-side art to a locked actor and then importing a different
    world version leaves the old version's folder behind
    (`appearances.import_version` removes the card, not the folder), and an
    image queued from it can never be described: every PUT 404s on the version
    gate, so the entry would be re-offered forever.
    """
    try:
        if base == "characters":
            d = store.characters.read_character(root, rid)
            return str(d["meta"]["name"]), {v["id"] for v in d["versions"]}
        if base == store.pcs.ASSET_BASE:
            d = store.pcs.read_pc(root, rid)
            return str(d["meta"]["name"]), {v["id"] for v in d["versions"]}
        # Greeting art hangs off the greeting, which has no versions -- the same
        # fixed `default` an entity's art uses, so the empty set says the same
        # thing here: the record existing is the whole question.
        if base == "greetings":
            return str(store.greetings.read_greeting(root, rid)["meta"]["name"]), set()
        return str(store.entities.read_entity(root, base, rid)["meta"]["name"]), set()
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound,
            store.entities.EntityNotFound, store.greetings.GreetingNotFound,
            KeyError, OSError, UnicodeDecodeError):
        return None


def _undescribed_url(wid: str, base: str, item: dict) -> str:
    """The world-scoped serving URL for one queued image. An actor's art is per
    version; an entity's is keyed on a fixed `default`, so its URL has no
    version segment to carry.

    Quoted segment by segment, like the campaign backlog's. `assets.storable`
    accepts names URL syntax owns -- `a#b` truncates at the fragment, a literal
    `%` can decode to another name -- so a raw URL here showed a broken preview
    for exactly the images whose (encoded) description PUT would have worked.
    """
    if base in ("characters", store.pcs.ASSET_BASE):
        return (f"/api/worlds/{wid}/{base}/{quote(item['id'], safe='')}"
                f"/versions/{quote(item['vid'], safe='')}"
                f"/images/{quote(item['name'], safe='')}")
    return (f"/api/worlds/{wid}/{base}/{quote(item['id'], safe='')}"
            f"/images/{quote(item['name'], safe='')}")


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/images")
def list_world_images(wid: str, cid: str, vid: str):
    return store.assets.list_images(_world_root_or_404(wid), cid, vid)


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
def get_world_image(wid: str, cid: str, vid: str, name: str, request: Request):
    return _serve_image(_world_root_or_404(wid), cid, vid, name, request=request)


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
async def put_world_image(wid: str, cid: str, vid: str, name: str, file: UploadFile = File(...)):
    root = _world_char_version_or_404(wid, cid, vid)
    data = await file.read()
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.assets.put_image(root, cid, vid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
def delete_world_image(wid: str, cid: str, vid: str, name: str):
    # Gated on the character and the version, not on the image: removing an
    # image that is already gone is the caller getting what they asked for, but
    # removing one from a character that does not exist is a typo worth reporting.
    store.assets.delete_image(_world_char_version_or_404(wid, cid, vid), cid, vid, name)
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/promote")
def promote_world_image(wid: str, cid: str, vid: str, name: str):
    root = _world_char_version_or_404(wid, cid, vid)
    try:
        store.assets.promote_image(root, cid, vid, name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    except ValueError as exc:
        # an externally-placed file whose extension we never accepted for upload
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/avatar/focus")
def put_world_avatar_focus(wid: str, cid: str, vid: str, body: AvatarFocus):
    root = _world_char_version_or_404(wid, cid, vid)
    if store.assets.image_path(root, cid, vid, store.assets.AVATAR) is None:
        raise HTTPException(status_code=404, detail="image not found")
    store.assets.write_focus(root, cid, vid, body.focus)
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/description/draft",
             status_code=202)
def post_world_image_description_draft(
        wid: str, cid: str, vid: str, name: str, request: Request,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Start a model-drafted first pass at what this character's picture shows.

    World-side only, and that is not an oversight -- it is true of all four
    surfaces. A description drafted from the bytes is a claim about the bytes,
    and a campaign reaches most of its art through its world, so the draft
    belongs where the art does. A campaign that has diverged an image can still
    describe it by hand; what it cannot do is spend a model call to caption a
    picture its world already captioned.
    """
    root = _world_char_version_or_404(wid, cid, vid)
    try:
        subject = store.characters.read_character(root, cid)["meta"]["name"]
    except store.characters.CharacterNotFound:
        subject = ""
    conn, messages = image_draft_prompt(
        store.assets.image_path(root, cid, vid, name), subject)

    async def work():
        return await draft_completion(
            client, conn, messages, "image-description",
            lambda text: {"description": store.image_drafts.parse_output(text)})

    return runs.run_draft(request.app, runs.world_subject(wid),
                          "image-description", x_grimoire_attempt, work)


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/description")
def put_world_image_description(wid: str, cid: str, vid: str, name: str,
                                body: ImageDescription):
    root = _world_char_version_or_404(wid, cid, vid)
    try:
        store.image_descriptions.set_description(root, cid, vid, name, body.description)
    except ValueError:
        # The strict-write rule as a status code: describing an image this
        # version does not hold is a 404, never a silently-kept orphan entry.
        # `from None` -- the ValueError is the store's detail, not the caller's.
        raise HTTPException(status_code=404, detail="image not found") from None
    return {"ok": True}
