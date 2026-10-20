"""characters: drafts responsibilities."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ... import inference as operations
from ... import store
from ...llm import LLMClient
from ...llm_errors import LLMError
from ...store import _plaintext
from .. import runs
from ..common import (
    _bounded_call,
    _card_data,
    _world_root_or_404,
    draft_completion,
    get_llm,
    require_inference,
)
from ..models import CharacterTextParse, CharacterTextRefine, TaglineSave, VoiceAnchorSave
from . import _framing
from ._router import router as router


@router.post("/worlds/{wid}/characters/parse-text")
def post_character_parse_text(wid: str, body: CharacterTextParse):
    """#136: an offline proposal. Cancel has no character write to undo."""
    _world_root_or_404(wid)
    try:
        return {"card": _plaintext.parse(body.text, body.filename)}
    except store.cards.CardParseError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post("/worlds/{wid}/characters/refine-text", status_code=202)
def post_character_refine_text(
        wid: str, body: CharacterTextRefine, request: Request,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Optional AI refinement uses the same detached preview contract (#136).

    Only the explicit action resolves a model or sends the text. No character
    is persisted here: the reviewed card goes through ordinary creation.
    """
    _world_root_or_404(wid)
    resolved = require_inference("character-text")
    try:
        messages = _plaintext.build_prompt(body.text, body.card)
    except store.cards.CardParseError as exc:
        raise HTTPException(422, detail=str(exc)) from exc

    def shape(text: str) -> dict:
        try:
            return {"card": _plaintext.parse_output(text)}
        except store.cards.CardParseError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    async def work():
        return await draft_completion(client, resolved, messages, "character-text", shape,
                                      schema=_plaintext.SCHEMA)

    return runs.run_draft(request.app, runs.world_subject(wid), "character-text",
                          x_grimoire_attempt, work)


@router.get("/worlds/{wid}/characters/{cid}/tagline")
def get_character_tagline(wid: str, cid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    return {"tagline": store.taglines.read(root, cid)}


@router.put("/worlds/{wid}/characters/{cid}/tagline")
def put_character_tagline(wid: str, cid: str, body: TaglineSave):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
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
    resolved = require_inference("tagline")
    try:
        ch = store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    card = store.characters.read_card(root, cid, ch["meta"]["default_version"])
    messages = store.taglines.build_prompt(_card_data(card))

    async def work():
        return await draft_completion(
            client, resolved, messages, "tagline",
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
    resolved = require_inference("tagline")
    # Off the event loop: `list_characters` stats every version and every image
    # of every character, which is ~200ms on a large world (see
    # `list_undescribed_images`). Eagerly, before the response is returned, so
    # `total` is known up front and a 404/409 is still an ordinary status code
    # rather than an error frame inside a 200.
    roster = await run_in_threadpool(store.characters.list_characters, root)
    targets = [c for c in roster if not c["tagline"]]

    async def event_stream():
        yield _framing._sse({"total": len(targets)})
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
                    yield _framing._sse({**frame, "error": {
                        "detail": "the storage location changed while this was running",
                        "kind": "store_moved"}})
                    break
                card, vid, skip = _tagline_target(root, cid)
                if card is None:
                    skipped += 1
                    yield _framing._sse({**frame, "skipped": skip})
                    continue
                messages = store.taglines.build_prompt(_card_data(card))
                try:
                    with store.usage.meter("tagline") as m:
                        text = await _bounded_call(operations.generate(
                            "tagline", messages, client=client, resolved=resolved,
                            usage=m.usage, stream=False))
                except LLMError as exc:
                    stopped = True
                    yield _framing._sse({**frame, "error": {"detail": exc.detail, "kind": exc.kind}})
                    break
                tagline = store.taglines.parse_output(text)
                if not tagline:
                    skipped += 1
                    yield _framing._sse({**frame, "skipped": "blank"})
                    continue
                skip = _commit_tagline(root, cid, vid, card, tagline)
                if skip:
                    skipped += 1
                    yield _framing._sse({**frame, "skipped": skip})
                    continue
                written += 1
                yield _framing._sse({**frame, "tagline": tagline})
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
            yield _framing._sse({**frame, "error": {"detail": str(exc), "kind": "tagline"}})
        summary = {"total": len(targets), "written": written,
                   "skipped": skipped, "stopped": stopped}
        yield _framing._sse({"summary": summary})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/worlds/{wid}/characters/{cid}/voice-anchor")
def get_character_voice_anchor(wid: str, cid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
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
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
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
    resolved = require_inference("voice-anchor")
    try:
        ch = store.characters.read_character(root, cid)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    card = store.characters.read_card(root, cid, ch["meta"]["default_version"])
    messages = store.voice_anchors.build_prompt(_card_data(card))

    async def work():
        return await draft_completion(
            client, resolved, messages, "voice-anchor",
            lambda text: {"voice_anchor": store.voice_anchors.parse_output(text)})

    return runs.run_draft(request.app, runs.world_subject(wid), "voice-anchor",
                          x_grimoire_attempt, work)
