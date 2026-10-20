"""Campaign HTTP endpoints: actors domain."""


from __future__ import annotations

from fastapi import (
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse

from ... import store
from ...llm import LLMClient
from .. import runs
from ..common import (
    _campaign_root_or_404,
    _display_name_or_400,
    _serve_image,
    _upload_image_ext,
    computes_only,
    draft_completion,
    get_llm,
    image_id_field,
    image_promote_guards,
    record_art_mutation,
    require_inference,
)
from ..models import (
    AvatarFocus,
    CharacterBirthdate,
    CharacterCreate,
    CharacterTags,
    CopyFromGreeting,
    DefaultVersion,
    ImageDescription,
    ImagePromote,
    NameBody,
    PCCreate,
    TaglineSave,
    VersionCreate,
    VersionUpdate,
    VoiceAnchorSave,
)
from ._router import router
from ._timeline import _absorbed_cast_refs


@router.get("/campaigns/{cid}/appearances")
def get_appearances(cid: str):
    """The appearance record, each row with its portrait's `avatar_v`.

    The token is added here rather than in `appearances.roster`, which drift
    measurement and sync also walk and which has no use for art. The play view
    draws every portrait off these rows -- the cast grid, the speaker plates,
    the inspector -- and a URL without `?v=` is never served immutable, so a
    reopened scene revalidated one image per actor on it.
    """
    _campaign_root_or_404(cid)
    v = store.overlay.view(cid)       # one for every row
    records = store.appearances.record(cid)
    absorbed = _absorbed_cast_refs(cid)
    out = []
    for r in store.appearances.roster(cid):
        ref = f"{r['kind']}/{r['id']}"
        record = records.get(ref, {})
        appeared = bool(r["scenes"] or record.get("presence") or ref in absorbed)
        out.append({
            **r,
            "appeared": appeared,
            "avatar_v": store.overlay.avatar_v(
                cid, r["id"], r["version"], r["kind"], v=v),
        })
    return out


@router.get("/campaigns/{cid}/pcs")
def get_campaign_pcs(cid: str):
    _campaign_root_or_404(cid)
    return store.overlay.list_pcs(cid)


@router.post("/campaigns/{cid}/pcs")
def post_campaign_pc(cid: str, body: PCCreate):
    # Campaign-local PC overlay: tags are free strings (no world-vocabulary check).
    _campaign_root_or_404(cid)
    try:
        with store.locks.campaign_lock(cid):
            store.actor_names.require_unique(body.name, scope="campaign", scope_id=cid)
            if body.persona:
                store.actor_names.require_unique(store.actor_names.persona_name(body.persona),
                                                 scope="campaign", scope_id=cid)
            pid, vid = store.overlay.create_pc(cid, body.name, body.tags,
                                               body.version_name, body.persona)
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return {"pc": pid, "version": vid}


def _campaign_char_version_or_404(cid: str, char: str, vid: str):
    """The campaign root, once `char`/`vid` are known to name a real character
    version.

    Resolved through `overlay.char_root`, not the campaign root: on a thin
    campaign the character's card files are still the world's, so a croot-only
    check would 404 every inherited character -- which is all of them. The root
    this *returns* is always the campaign's, because that is where a write has
    to land. A character the campaign has tombstoned resolves to the campaign
    root, where there is no `character.md`, so it is refused for the same
    reason a typo is; so is a version `pick_version` purged.

    Why gate at all -- and why the reads on this surface are left ungated
    here too -- see `common._world_char_version_or_404` (#360).
    """
    root = _campaign_root_or_404(cid)
    try:
        store.characters.require_version(store.overlay.char_root(cid, char), char, vid)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    except store.characters.VersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    return root


@router.get("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def get_campaign_image(cid: str, char: str, vid: str, name: str, request: Request):
    _campaign_root_or_404(cid)
    return _serve_image(store.overlay.image_root(cid, char, vid, name), char, vid, name, request=request)


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
async def put_campaign_image(cid: str, char: str, vid: str, name: str, file: UploadFile = File(...)):
    root = _campaign_char_version_or_404(cid, char, vid)
    data = await file.read()
    ext = _upload_image_ext(data)  # the bytes name the type, not `file.filename` (#321)
    try:
        stored = store.assets.put_image(root, char, vid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": name, "ext": stored,
            **image_id_field(store.assets.image_id(root, char, vid, name))}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def delete_campaign_image(cid: str, char: str, vid: str, name: str, expected_v: str | None = None):
    # tombstone so a still-materialized world image doesn't show back through
    # the overlaid read the moment the campaign's own copy is gone (get_campaign_image).
    _campaign_char_version_or_404(cid, char, vid)
    return record_art_mutation("campaign", cid, "characters", char, vid, name,
                               "delete", expected_v=expected_v)


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}/promote")
def promote_campaign_image(cid: str, char: str, vid: str, name: str, body: ImagePromote | None = None):
    _campaign_char_version_or_404(cid, char, vid)
    return record_art_mutation("campaign", cid, "characters", char, vid, name,
                               "promote", **image_promote_guards(body))


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/avatar/focus")
def put_campaign_avatar_focus(cid: str, char: str, vid: str, body: AvatarFocus):
    # a thin campaign may only have this avatar through the inherited world
    # character, so the existence gate must check the overlay union, not croot alone
    # the write always lands campaign-side; overlay.read_focus then finds this
    # campaign crop record (an avatar placement -- image-less over an inherited
    # avatar -- or a focus.json beside a legacy campaign avatar) and treats the
    # campaign as authoritative going forward
    _campaign_char_version_or_404(cid, char, vid)
    return record_art_mutation("campaign", cid, "characters", char, vid, "avatar",
                               "focus", focus=body.focus, expected_v=body.expected_v)


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}/description")
def put_campaign_image_description(cid: str, char: str, vid: str, name: str,
                                   body: ImageDescription):
    _campaign_char_version_or_404(cid, char, vid)
    return record_art_mutation("campaign", cid, "characters", char, vid, name,
                               "description", description=body.description, expected_v=body.expected_v,
                               expected_description_rev=body.expected_description_rev,
                               cleanup_only=body.cleanup_only)


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/copy-from-greeting")
def post_copy_campaign_image_from_greeting(cid: str, char: str, vid: str, body: CopyFromGreeting):
    root = _campaign_char_version_or_404(cid, char, vid)
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
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail="source image not found") from err
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    # Same rule as the world roster: the badge counts the world greetings the
    # character is present at, read through the overlay so a campaign's own
    # and deleted greetings are respected.
    # Rendered as it stands, not through `jsonable_encoder`: already plain
    # JSON, and the same bytes -- see the world roster's route.
    return JSONResponse(store.greetings.add_featuring_counts(
        store.overlay.list_characters(cid), store.overlay.list_greetings(cid)))


@router.post("/campaigns/{cid}/characters")
def post_campaign_character(cid: str, body: CharacterCreate):
    """A character who exists only in this campaign — an NPC who walked on
    mid-scene and was never in the library (#60).

    The world side has had this route all along; the campaign side had every
    piece except the door. `overlay.create_character` writes no `sync.md` ref
    and the world has no counterpart, and that *absence* is the whole "emergent"
    signal: nothing in `sync.incoming` enumerates the record, so it can never
    surface as a phantom world change. `POST .../{kind}/{eid}/promote` is what
    ends that state.
    """
    _campaign_root_or_404(cid)
    try:
        with store.locks.campaign_lock(cid):
            store.actor_names.require_unique(body.name, scope="campaign", scope_id=cid)
            if body.card:
                store.actor_names.require_unique(store.actor_names.card_name(body.card),
                                                 scope="campaign", scope_id=cid)
            aid, vid = store.overlay.create_character(cid, body.name, body.version_name, body.card)
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return {"character": aid, "version": vid}


@router.get("/campaigns/{cid}/diverged")
def get_diverged(cid: str):
    """Every campaign copy that no longer matches the library record it came
    from — the inverse of `/incoming`, and what `push` can act on (#53)."""
    # `_campaign_root_or_404` has already answered for a campaign that is not
    # there; past it, `diverged` reads sync.md, which is absent-means-empty.
    _campaign_root_or_404(cid)
    return store.sync.diverged(cid)


@router.get("/campaigns/{cid}/characters/{char}")
def get_campaign_character(cid: str, char: str):
    _campaign_root_or_404(cid)
    try:
        return store.overlay.read_character(cid, char)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc


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
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
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
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    store.overlay.set_voice_anchor(cid, char, body.voice_anchor)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/voice-anchor/generate", status_code=202)
@computes_only
def post_campaign_voice_anchor_generate(
        cid: str, char: str, request: Request,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Start a drafted anchor from the character's card, resolved through the
    overlay so a campaign-local character (which has no world copy) can use it
    too. Preview only — the caller persists with PUT."""
    _campaign_root_or_404(cid)
    resolved = require_inference("voice-anchor", cid)
    root = store.overlay.char_root(cid, char)
    try:
        ch = store.characters.read_character(root, char)
        card = store.characters.read_card(root, char, ch["meta"]["default_version"])
    except (store.characters.CharacterNotFound, store.characters.VersionNotFound) as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    # See the world-side route: `{}` and `{"data": ["speech"]}` are both
    # supported card state, and the template renders "(none)" per field.
    data = card.get("data")
    messages = store.voice_anchors.build_prompt(data if isinstance(data, dict) else {})

    async def work():
        return await draft_completion(
            client, resolved, messages, "voice-anchor",
            lambda text: {"voice_anchor": store.voice_anchors.parse_output(text)},
            cid=cid)

    return runs.run_draft(request.app, runs.campaign_subject(cid), "voice-anchor",
                          x_grimoire_attempt, work)


#: Messages before a recorded placement that the capsule excerpt includes: the
#: turn that introduced the character lands before the placement is written.
_ENTRANCE_LEAD = 2


def _first_scene_transcript(cid: str, char: str) -> str:
    """The transcript of the first scene `char` was placed in, as the prompt
    reads it, or "" when the character has never appeared in one (#62).

    The first key is the first placement: the record keeps scenes in the order
    they were entered. The view is the prompt phase, the same as every
    other LLM reader of transcript text.
    """
    rec = store.appearances.record(cid).get(f"characters/{char}") or {}
    # The history, not current membership: `leave` drops a scene from `scenes`
    # but keeps its interval under `presence`, and the scene a character walked
    # on in is the one this reads even after they have walked off it. Records
    # written before `presence` existed fall back to `scenes`.
    presence = rec.get("presence") or {}
    scenes = list(presence) or list(rec.get("scenes") or [])
    if not scenes:
        return ""
    sid = scenes[0]
    try:
        messages = store.scenes.read_scene(cid, sid)["messages"]
    except store.SceneNotFound:
        return ""
    # The interval's `start` is the message count when the character was placed,
    # which is after the turn that introduced them, so the excerpt begins a
    # couple of messages earlier. A scene that runs long before they walk on still
    # gives the model the entrance and what follows it.
    arrived = [i.get("start") for i in presence.get(sid) or [] if i.get("start") is not None]
    begin = max(0, min(arrived, default=0) - _ENTRANCE_LEAD)
    shown = store.regex.view.view(messages, cid=cid, phase="prompt")
    return store.chronicle.transcript_text(shown[begin:],
                                           store.appearances.player_label(cid, sid))


@router.post("/campaigns/{cid}/characters/{char}/capsule/generate", status_code=202)
@computes_only
def post_campaign_capsule_generate(
        cid: str, char: str, request: Request,
        version: str | None = None,
        client: LLMClient = Depends(get_llm),
        x_grimoire_attempt: str | None = Header(default=None)):
    """Start a drafted capsule and tags for a character, read from its card and
    the first scene it appears in (#62). `version` is the version the editor has
    open, since the tags it returns are saved onto that version; the default one
    when omitted. Preview only: the capsule is saved through PUT .../tagline and
    the tags through the version PUT, both on Save.
    """
    _campaign_root_or_404(cid)
    resolved = require_inference("capsule", cid)
    root = store.overlay.char_root(cid, char)
    try:
        ch = store.characters.read_character(root, char)
        card = store.characters.read_card(root, char, version or ch["meta"]["default_version"])
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    except store.characters.VersionNotFound as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    data = card.get("data")
    messages = store.taglines.build_capsule_prompt(
        data if isinstance(data, dict) else {}, _first_scene_transcript(cid, char))

    async def work():
        return await draft_completion(
            client, resolved, messages, "capsule",
            store.taglines.parse_capsule_output, cid=cid)

    return runs.run_draft(request.app, runs.campaign_subject(cid), "capsule",
                          x_grimoire_attempt, work)


@router.get("/campaigns/{cid}/characters/{char}/tagline")
def get_campaign_character_tagline(cid: str, char: str):
    """The capsule as this campaign sees it: its own sentence, else the world's
    (`overlay.tagline`). An emergent character has no world copy, so its
    sentence is simply its own."""
    _campaign_root_or_404(cid)
    try:
        store.characters.read_character(store.overlay.char_root(cid, char), char)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    return {"tagline": store.overlay.tagline(cid, char)}


def _shown_capsule(cid: str, char: str) -> str:
    """The capsule the campaign shows once a save has landed. The write is
    already published when this reads, so an unreadable library sentence answers
    blank rather than failing a save that happened."""
    try:
        return store.overlay.tagline(cid, char)
    except (OSError, ValueError):
        return ""


@router.put("/campaigns/{cid}/characters/{char}/tagline")
def put_campaign_character_tagline(cid: str, char: str, body: TaglineSave):
    """Write the campaign's own capsule for a character.

    The sidecar is written straight under the campaign root, and nothing
    materializes the actor: `tagline.md` overlays per file (`overlay.tagline`
    reads the campaign's copy first), so an inherited character keeps its cards
    inherited and its sync base untouched. The existence check runs inside the
    lock hold that writes, so a delete cannot land between the two.

    A blank save writes a blank campaign copy, which `overlay.tagline` reads as
    "no sentence here", so the library's shows through again -- unlike a voice
    anchor, a capsule has no opt-out to preserve.
    """
    _campaign_root_or_404(cid)
    with store.locks.campaign_lock(cid):
        try:
            store.characters.read_character(store.overlay.char_root(cid, char), char)
        except store.characters.CharacterNotFound as exc:
            raise HTTPException(status_code=404, detail="character not found") from exc
        store.overlay.set_tagline(cid, char, body.tagline)
        shown = _shown_capsule(cid, char)
    return {"ok": True, "tagline": shown}


@router.put("/campaigns/{cid}/characters/{char}")
def put_campaign_character(cid: str, char: str, body: DefaultVersion):
    _campaign_root_or_404(cid)
    try:
        with store.locks.campaign_lock(cid):
            selected = store.characters.read_card(store.overlay.char_root(cid, char),
                                                  char, body.default_version)
            store.actor_names.require_unique(store.actor_names.card_name(selected),
                                             scope="campaign", scope_id=cid,
                                             actor_ref=f"characters:{char}")
            root = store.overlay.ensure_actor_writable(cid, "characters", char)
            store.characters.set_default_version(root, char, body.default_version)
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    except store.characters.VersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/campaigns/{cid}/characters/{char}")
def delete_campaign_character(cid: str, char: str):
    """Remove a character from this campaign (#60).

    The counterpart to the create route above, and it had been missing: the
    world side has had a delete all along, version-delete refuses the last
    version, and so an NPC invented by mistake could not be removed at all
    (Codex review). `overlay.delete_actor` decides what the delete MEANS here
    — tombstone an inherited actor, drop a campaign copy, or remove an
    emergent one outright.
    """
    _campaign_root_or_404(cid)
    try:
        store.overlay.delete_actor(cid, "characters", char)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    return {"ok": True}


@router.delete("/campaigns/{cid}/pcs/{pid}")
def delete_campaign_pc(cid: str, pid: str):
    """The same, for a campaign-scoped PC — `POST /campaigns/{cid}/pcs` has
    existed longer than the character one and had no delete either."""
    _campaign_root_or_404(cid)
    try:
        store.overlay.delete_actor(cid, "pcs", pid)
    except store.pcs.PCNotFound as exc:
        raise HTTPException(status_code=404, detail="pc not found") from exc
    return {"ok": True}


@router.put("/campaigns/{cid}/characters/{char}/name")
def put_campaign_character_name(cid: str, char: str, body: NameBody):
    """This campaign's own name for the character (#13). Materializes the actor
    copy-on-write like every other campaign-side character write, so the world's
    name is left alone."""
    _campaign_root_or_404(cid)
    name = _display_name_or_400(body.name)
    try:
        with store.locks.campaign_lock(cid):
            store.actor_names.require_unique(name, scope="campaign", scope_id=cid,
                                             actor_ref=f"characters:{char}")
            root = store.overlay.ensure_actor_writable(cid, "characters", char)
            store.characters.set_name(root, char, name)
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    return {"ok": True}


@router.put("/campaigns/{cid}/characters/{char}/birthdate")
def put_campaign_character_birthdate(cid: str, char: str, body: CharacterBirthdate):
    """Save on the campaign copy, including characters created only here."""
    _campaign_root_or_404(cid)
    try:
        # Materialization and the metadata read-modify-write are one edit.
        with store.locks.campaign_lock(cid):
            root = store.overlay.ensure_actor_writable(cid, "characters", char)
            store.characters.set_birthdate(root, char, body.birthdate)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    return {"ok": True}


@router.put("/campaigns/{cid}/characters/{char}/tags")
def put_campaign_character_tags(cid: str, char: str, body: CharacterTags):
    """This campaign's own tags for the character when cast as the player (#18).
    Free strings, as a campaign PC's are: the world-vocabulary check belongs to
    the world route. Materializes the copy play reads, so the world is left alone."""
    _campaign_root_or_404(cid)
    try:
        with store.locks.campaign_lock(cid):
            root = store.overlay.ensure_actor_writable(cid, "characters", char)
            store.characters.set_tags(root, char, body.tags)
    except store.characters.CharacterNotFound as exc:
        raise HTTPException(status_code=404, detail="character not found") from exc
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions")
def post_campaign_character_version(cid: str, char: str, body: VersionCreate):
    _campaign_root_or_404(cid)
    try:
        with store.locks.campaign_lock(cid):
            store.actor_names.require_unique(store.actor_names.card_name(body.card),
                                             scope="campaign", scope_id=cid,
                                             actor_ref=f"characters:{char}")
            if store.appearances.locked_version(cid, "characters", char) is not None:
                raise HTTPException(status_code=409, detail="character is locked to one version")
            root = store.overlay.ensure_actor_writable(cid, "characters", char)
            vid = store.characters.create_version(root, char, body.name, body.card)
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    return {"version": vid}


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}")
def put_campaign_character_version(cid: str, char: str, vid: str, body: VersionUpdate):
    _campaign_root_or_404(cid)
    try:
        with store.locks.campaign_lock(cid):
            store.actor_names.require_unique(store.actor_names.card_name(body.card),
                                             scope="campaign", scope_id=cid,
                                             actor_ref=f"characters:{char}")
            root = store.overlay.ensure_actor_writable(cid, "characters", char)
            store.characters.update_version(root, char, vid, body.card)
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    except store.characters.VersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    return {"ok": True}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}")
def delete_campaign_character_version(cid: str, char: str, vid: str):
    _campaign_root_or_404(cid)
    try:
        root = store.overlay.ensure_actor_writable(cid, "characters", char)
        store.characters.delete_version(root, char, vid)
    except store.characters.CharacterNotFound as err:
        raise HTTPException(status_code=404, detail="character not found") from err
    except store.characters.VersionNotFound as err:
        raise HTTPException(status_code=404, detail="version not found") from err
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


