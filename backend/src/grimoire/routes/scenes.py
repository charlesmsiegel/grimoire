"""Scenes and the play loop: scene CRUD and suggestions, the generating
routes (chat / retry / regenerate), cast seating, scene location, datetime and
response scope, the chronicle, and the absorb/audit end-of-scene flow."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException

from .. import prompts, store
from ..llm import LLMClient
from ..llm_errors import LLMError
from .common import (_campaign_root_or_404, _dump, _require_connection, _require_scene,
                     _response_body, _turn_override, _write_response, get_llm)
from .models import (Appear, AppearBatch, ChatTurn, ChronicleSave, Dismiss, EditMessage,
                     NewScene, RegenerateBody, RenameScene, ResponseSettings, RetryBody,
                     SceneDatetime, SceneLocation)
from .streaming import _chat_stream

router = APIRouter()


@router.get("/campaigns/{cid}/scenes")
def get_scenes(cid: str):
    try:
        return store.scenes.list_scenes(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.post("/campaigns/{cid}/scenes")
def post_scene(cid: str, body: NewScene):
    title = body.title or "New scene"
    try:
        return {"id": store.scenes.create_scene(cid, title, body.suggested_date,
                                                pcless=body.pcless)}
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


def _resolve_cast(cid: str, tokens: list[str]) -> list[dict]:
    out = []
    for tok in tokens:
        kind, _, aid = tok.partition(":")
        try:
            if kind == "pcs":
                name = store.pcs.read_pc(store.overlay.pc_root(cid, aid), aid)["meta"].get("name", aid)
            else:
                name = store.characters.read_character(
                    store.overlay.char_root(cid, aid), aid)["meta"].get("name", aid)
        except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
            name = aid
        out.append({"kind": kind, "id": aid, "name": name})
    return out


@router.post("/campaigns/{cid}/scene-suggestions")
async def post_scene_suggestions(cid: str, after: str | None = None, offscreen: bool = False,
                                 client: LLMClient = Depends(get_llm)):
    try:
        store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    conn = _require_connection()
    # with >2 startable greetings the same call also ranks them for the chooser
    candidates = store.suggest.greeting_candidates(cid, after, pcless=offscreen)
    messages = store.suggest.build_prompt(store.suggest.build_snapshot(cid, offscreen=offscreen),
                                          candidates, offscreen=offscreen)
    try:
        text = await client.complete(messages, conn)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    loc_names = {e["id"]: e.get("name", e["id"]) for e in store.overlay.list_entities(cid, "locations")}
    out = []
    for s in store.suggest.parse_output(text, cid, offscreen=offscreen):
        loc = {"id": s["location"], "name": loc_names.get(s["location"], s["location"])} if s["location"] else None
        out.append({"title": s["title"], "premise": s["premise"], "date": s["date"],
                    "cast": _resolve_cast(cid, s["cast"]), "location": loc})
    picks = (store.suggest.parse_greeting_picks(text, {c["id"] for c in candidates})
             if candidates else [])
    return {"suggestions": out, "greeting_picks": picks,
            "next_date": store.suggest.parse_next_date(text, cid)}


@router.get("/campaigns/{cid}/scenes/{sid}")
def get_scene(cid: str, sid: str):
    try:
        return store.scenes.read_scene(cid, sid)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")


@router.put("/campaigns/{cid}/scenes/{sid}")
def put_scene(cid: str, sid: str, body: RenameScene):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    try:
        new_sid = store.scenes.rename_scene(cid, sid, title)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")
    return {"id": new_sid, "title": title}


@router.delete("/campaigns/{cid}/scenes/{sid}")
def delete_scene(cid: str, sid: str):
    try:
        store.scenes.delete_scene(cid, sid)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/scenes/{sid}/chat")
def post_chat(cid: str, sid: str, turn: ChatTurn, client: LLMClient = Depends(get_llm)):
    _require_scene(cid, sid)
    conn = _require_connection()
    store.proposals.supersede(cid, sid)  # a new send retires any pending decision
    if store.scenes.is_pcless(cid, sid) or not turn.content.strip():
        # ephemeral turn, never stored: a director note steering one generation
        # (pcless), or — in any scene — an empty send meaning "next NPC round"
        note = turn.content.strip() or prompts.render("scene/director_note.j2")
        messages = store.context.build_director_messages(
            cid, sid, note, turn=_turn_override(turn))
        return _chat_stream(cid, sid, messages, conn, client)
    names = store.appearances.player_names(cid, sid)
    speaker = names[0] if len(names) == 1 else None
    if speaker:
        store.scenes.stamp_user_speaker(cid, sid, speaker)
    # Macros resolved once at persist time (#137): a player's {{roll:1d20}}
    # must not re-roll on every later context build (retry, next turn, ...).
    content = store.context.expand_macros(
        turn.content, store.context.scene_substitutions(cid, sid), cid, sid)
    store.scenes.append_message(cid, sid, "user", content, speaker=speaker)
    messages = store.context.build_messages(cid, sid, turn=_turn_override(turn))
    return _chat_stream(cid, sid, messages, conn, client)


@router.post("/campaigns/{cid}/scenes/{sid}/retry")
def post_retry(cid: str, sid: str, body: RetryBody | None = None,
               client: LLMClient = Depends(get_llm)):
    scene = _require_scene(cid, sid)
    conn = _require_connection()
    store.proposals.supersede(cid, sid)  # a fresh generation retires the old decision
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to retry")
    messages = store.context.build_messages(cid, sid, turn=_turn_override(body))
    return _chat_stream(cid, sid, messages, conn, client)


@router.post("/campaigns/{cid}/scenes/{sid}/regenerate")
def post_regenerate(cid: str, sid: str, body: RegenerateBody | None = None,
                    client: LLMClient = Depends(get_llm)):
    """Redo the most recent post: drop a trailing assistant reply, stream a fresh one."""
    _require_scene(cid, sid)
    conn = _require_connection()
    store.proposals.supersede(cid, sid)  # regenerating retires the old decision
    # Re-read AFTER the retire: superseding heals the record it retires, which
    # can append a 🎲 line the pre-retire snapshot doesn't have. Judging the
    # checks below on the stale snapshot let the ROLL_SPEAKER guard pass and
    # `remove_trailing_assistant_run` then refuse (IndexError -> 500) — it
    # never deletes a roll line, but the caller deserves the 400 instead.
    msgs = _require_scene(cid, sid)["messages"]
    if not msgs:
        raise HTTPException(status_code=400, detail="nothing to regenerate")
    # Trailing scene transitions are stepped over, not consumed: reroll targets
    # the last generation BENEATH them, and every check below has to look at
    # that generation rather than at the transition line sitting on top of it.
    core = msgs[:len(msgs) - store.scenes.trailing_transitions(msgs)]
    if core and core[-1]["role"] == "assistant":
        if all(m["role"] == "assistant" for m in core):
            raise HTTPException(status_code=400, detail="cannot regenerate the opening post")
        if core[-1].get("speaker") in store.scenes.SYNTHETIC_SPEAKERS:
            # Enumerated from the same tuple `remove_trailing_assistant_run`
            # refuses on, not from one speaker name: only ROLL_SPEAKER is
            # reachable here (trailing transitions are already stripped
            # above), but a future synthetic speaker gets this 400 rather than
            # the bare IndexError (500) that refusal would otherwise surface.
            raise HTTPException(status_code=400, detail="cannot regenerate past a manual dice roll")
        try:
            store.scenes.remove_trailing_assistant_run(cid, sid)
        except store.scenes.TurnSizesDesynced:
            # Refusing beats guessing: the recorded turn boundaries don't fit
            # the transcript, so any deletion could take blocks from an earlier
            # generation. Nothing has been changed on disk.
            raise HTTPException(
                status_code=400,
                detail="this scene's recorded turn boundaries no longer match its "
                       "transcript — delete the last reply manually to regenerate")
    messages = store.context.build_messages(cid, sid, turn=_turn_override(body))
    guidance = (body.guidance or "").strip() if body else ""
    if guidance:
        messages.append({
            "role": "system",
            "content": prompts.render("scene/regenerate_guidance.j2", guidance=guidance),
        })
    return _chat_stream(cid, sid, messages, conn, client)


@router.get("/campaigns/{cid}/chronicle")
def get_chronicle(cid: str):
    _campaign_root_or_404(cid)
    return store.chronicle.recent(cid, 50)


# Indirection so tests can drive budget arithmetic off a fake clock instead of
# real waiting. Deliberately NOT time.time(): a wall-clock jump (NTP, DST,
# sleep/wake) must not expire or extend a running absorb.
_clock = time.monotonic
BUDGET_EXHAUSTED = "absorb time budget exhausted"


class _Budget:
    """A wall-clock ceiling on one absorb's whole LLM sequence (#243).

    Absorb awaits an extraction call, then one dossier call per present NPC,
    then an audit call, all inside a single HTTP request — the per-call idle
    timeout in `llm` bounds each *stall*, but nothing bounds the total. This
    does, and it is deliberately absorb's policy rather than the LLM facade's:
    only the caller knows which of its steps are droppable.

    `seconds <= 0` means no ceiling at all (config's escape hatch), in which
    case every method degrades to a plain await.
    """

    def __init__(self, seconds: float):
        self._deadline = None if seconds <= 0 else _clock() + seconds

    def remaining(self) -> float | None:
        return None if self._deadline is None else self._deadline - _clock()

    def spent(self) -> bool:
        left = self.remaining()
        return left is not None and left <= 0

    async def run(self, coro):
        """Await `coro` under the remaining budget, reporting an overrun as the
        same LLMError kind an upstream stall raises — so every caller's existing
        LLM failure handling covers it with no new branch.

        wait_for waits for the cancellation it requests to complete, so the
        real ceiling is the budget plus however long the call takes to unwind;
        that unwinding is itself hard-bounded in `llm` (grace-then-abandon),
        which is what keeps this a bound rather than a hope.
        """
        left = self.remaining()
        if left is None:
            return await coro
        try:
            return await asyncio.wait_for(coro, left)
        except asyncio.TimeoutError as exc:
            # asyncio.TimeoutError is the builtin TimeoutError from 3.11 on, so
            # this also catches one raised *inside* the call. Only blame the
            # budget when the budget is actually gone.
            detail = BUDGET_EXHAUSTED if self.spent() else (str(exc) or "the call timed out")
            raise LLMError("timeout", detail) from exc


async def _run_audit(cid: str, sid: str, client: LLMClient, conn: dict,
                     budget: _Budget) -> tuple[list[dict], dict]:
    """(edits, mechanics) for the scene audit. Never raises; every failure is
    an explicit mechanics status (spec: audit visibility) so absorb stays
    intact even when the audit pipeline blows up."""
    mech = {"status": "skipped", "reason": None, "warnings": [], "dropped": []}
    excluded: list = []
    try:
        if store.modules.resolve(cid) is None:
            mech["reason"] = "no module"
            return [], mech
        # ONE failure boundary around the ENTIRE audit pipeline (spec:
        # never-fail-absorb) — sheet_blocks, read_scene, transcript,
        # roll_lines, build_prompt, complete, parse AND materialize. Any
        # exception anywhere here is a failed audit, never a 500 absorb.
        blocks, excluded = store.audit.sheet_blocks(cid, sid)
        if not blocks and not excluded:
            mech["reason"] = "no sheeted scope"
            return [], mech
        if not blocks:
            return [], {**mech, "status": "failed",
                        "reason": "all scoped sheets invalid", "dropped": excluded}
        scene = store.scenes.read_scene(cid, sid)
        transcript = store.chronicle.transcript_text(scene["messages"])
        messages = store.audit.build_prompt(transcript, blocks,
                                            store.audit.roll_lines(cid, sid))
        # An exhausted budget fails this instantly (never calling the model),
        # landing in the catch-all below as a failed audit — the status the UI
        # already renders with a POST /audit retry beside it.
        text = await budget.run(client.complete(messages, conn))
        parsed = store.audit.parse_output(text)
        edits, dropped = store.audit.materialize(cid, sid, parsed)
    except store.audit.AuditParseError as exc:
        return [], {**mech, "status": "failed", "reason": str(exc), "dropped": excluded}
    except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
        return [], {**mech, "status": "failed", "reason": f"audit failed: {exc}",
                    "dropped": excluded}
    dropped = excluded + dropped
    status = "degraded" if dropped else "ok"
    reason = ("some sheets could not be audited" if excluded else
              "some findings could not be validated") if dropped else None
    return edits, {"status": status, "reason": reason,
                   "warnings": parsed["warnings"], "dropped": dropped}


async def _refresh_dossiers(cid: str, sid: str, transcript: str,
                            client: LLMClient, conn: dict, budget: _Budget) -> dict:
    """Refresh every present NPC's campaign dossier from this scene, reporting
    the outcome. Never raises -- a dossier failure must not fail absorb -- but
    it is no longer silent either: failures come back as a status the inspector
    renders, mirroring _run_audit's shape, so a user whose dossiers quietly
    stopped updating sees it on the very first absorb rather than never."""
    out: dict = {"status": "skipped", "reason": None,
                 "refreshed": [], "failed": [], "skipped": []}
    try:
        cast = store.appearances.scene_cast(cid, sid)
        croot = store.appearances.locked_actor_root(cid)   # cast actors are locked, so campaign-side
    except Exception as exc:  # noqa: BLE001 -- an unreadable cast is a failed phase, not a 500
        return {**out, "status": "failed", "reason": f"could not read the scene cast: {exc}"}
    for i, a in enumerate(cast):
        if a["kind"] != "characters" or a["role"] != "npc":
            continue  # dossiers feed the npc-only "Active elsewhere" tier; skip player cards
        if budget.spent():
            # The extraction call is the part worth keeping, so the tail is
            # dropped rather than run unbounded (#243) — but named, not
            # silently, for the same reason failures are (#236).
            out["skipped"] = [b["id"] for b in cast[i:]
                              if b["kind"] == "characters" and b["role"] == "npc"]
            break
        try:
            name = store.characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            msgs = store.dossiers.build_prompt(name, store.dossiers.read(croot, a["id"]), transcript)
            d_text = await budget.run(client.complete(msgs, conn))
            store.dossiers.write(croot, a["id"], store.dossiers.parse_output(d_text))
        except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
            # Type-prefixed: a bare str() is useless for the store's own errors
            # (CharacterNotFound("aese") stringifies to just "aese").
            detail = str(exc).strip()
            out["failed"].append({
                "id": a["id"],
                "reason": f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__})
        else:
            out["refreshed"].append(a["id"])
    if not out["refreshed"] and not out["failed"] and not out["skipped"]:
        return {**out, "reason": "no npcs present"}
    if not out["refreshed"]:
        # A budget that ran out before the first call is a different story from
        # calls that were made and went wrong; say which one happened.
        return {**out, "status": "failed",
                "reason": "no dossier could be refreshed" if out["failed"] else
                          "the absorb time budget ran out before any dossier could be refreshed"}
    if out["failed"]:  # the more specific story when both happened; `skipped` still lists the rest
        return {**out, "status": "degraded", "reason": "some dossiers could not be refreshed"}
    if out["skipped"]:
        return {**out, "status": "degraded",
                "reason": "the absorb time budget ran out before the rest could be refreshed"}
    return {**out, "status": "ok"}


@router.post("/campaigns/{cid}/scenes/{sid}/absorb")
async def post_absorb(cid: str, sid: str,
                      client: LLMClient = Depends(get_llm)):
    scene = _require_scene(cid, sid)
    conn = _require_connection()
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to absorb")
    facts = store.chronicle.scene_facts(cid, sid)
    transcript = store.chronicle.transcript_text(scene["messages"])
    messages = store.absorb.build_prompt(
        transcript, facts,
        store.absorb.state_snapshot(cid, sid), store.absorb.relationships_snapshot(cid, sid),
        store.absorb.plot_snapshot(cid), store.absorb.group_snapshot(cid))
    budget = _Budget(store.config.absorb_budget())
    try:
        text = await budget.run(client.complete(messages, conn))
    except LLMError as exc:
        # Including a budget overrun on this first call: nothing has been
        # produced yet, so there is nothing to degrade to.
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    parsed = store.absorb.parse_output(text)
    edits = store.absorb.materialize(cid, sid, parsed)
    # Phase 2: refresh each present NPC's campaign dossier from this scene
    # (never raises -- see _refresh_dossiers' own failure boundary).
    dossiers = await _refresh_dossiers(cid, sid, transcript, client, conn, budget)
    # Phase 5: audit the scene's mechanics against the sheeted cast (never
    # raises -- see _run_audit's own failure boundary).
    audit_edits, mechanics = await _run_audit(cid, sid, client, conn, budget)
    return {"one_line": parsed["one_line"], "summary": parsed["summary"],
            "keywords": parsed["keywords"], "timeline_events": parsed["timeline_events"],
            **facts, "edits": edits + audit_edits, "mechanics": mechanics,
            "dossiers": dossiers}


@router.post("/campaigns/{cid}/scenes/{sid}/audit")
async def post_audit(cid: str, sid: str, client: LLMClient = Depends(get_llm)):
    """Standalone audit retry: re-runs ONLY the audit step (never the prose
    absorb), returning fresh `expect` values on any resulting sheet edits."""
    _require_scene(cid, sid)
    conn = _require_connection()
    if store.modules.resolve(cid) is None:
        raise HTTPException(status_code=400, detail="no module resolved")
    # A retry gets its own budget — it never inherits the deadline of whatever
    # absorb ran out of time earlier.
    edits, mechanics = await _run_audit(cid, sid, client, conn,
                                        _Budget(store.config.absorb_budget()))
    return {"mechanics": mechanics, "edits": edits}


@router.put("/campaigns/{cid}/scenes/{sid}/chronicle")
def put_chronicle(cid: str, sid: str, body: ChronicleSave):
    _require_scene(cid, sid)
    facts = store.chronicle.scene_facts(cid, sid)
    record = store.chronicle.absorb(cid, {
        "id": sid, "one_line": body.one_line, "summary": body.summary,
        "keywords": body.keywords, **facts})
    store.chronicle.append_timeline(cid, body.timeline_events)
    store.scenes.mark_absorbed(cid, sid, body.one_line, body.summary)
    applied, sheet_failures = store.absorb.apply_edits(cid, body.edits, sid)
    return {**record, "applied": applied, "sheet_failures": sheet_failures}


@router.get("/campaigns/{cid}/scenes/{sid}/cast")
def get_scene_cast(cid: str, sid: str):
    _require_scene(cid, sid)
    return store.appearances.scene_cast(cid, sid)


def _seat_cast_member(cid: str, sid: str, body: Appear) -> None:
    """Validate + resolve one cast addition and record it. Raises HTTPException
    (404 unknown, 400 bad role) or store.appearances.AppearError (already cast)."""
    if body.kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    role = "player" if body.kind == "pcs" else (body.role or "npc")
    if role not in ("player", "npc"):
        raise HTTPException(status_code=400, detail="role must be player or npc")
    if role == "player" and store.scenes.is_pcless(cid, sid):
        raise HTTPException(status_code=400, detail="cannot seat a player in an offscreen scene")
    version = body.version
    try:
        if version is None:
            if body.kind == "characters":
                version = store.characters.read_character(
                    store.overlay.char_root(cid, body.id), body.id)["meta"]["default_version"]
            else:
                version = store.pcs.read_pc(
                    store.overlay.pc_root(cid, body.id), body.id)["meta"]["default_version"]
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
        raise HTTPException(status_code=404, detail="actor not found")
    # A first appearance locks lazily by copying from the world when the campaign
    # lacks the version; validate a supplied version against the campaign-visible
    # actor first so a purged/tombstoned one can't be revived. An already-cast
    # actor skips this — appear() reports the lock conflict (409), no revival.
    if store.appearances.locked_version(cid, body.kind, body.id) is None and store.appearances.actor_hash(
            store.overlay.actor_root(cid, body.kind, body.id), body.kind, body.id, version) is None:
        raise HTTPException(status_code=404, detail="actor or version not found in campaign")
    store.appearances.appear(cid, sid, body.kind, body.id, version, role)


@router.post("/campaigns/{cid}/scenes/{sid}/cast")
def post_scene_cast(cid: str, sid: str, body: Appear):
    _require_scene(cid, sid)
    try:
        _seat_cast_member(cid, sid, body)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.delete("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}")
def delete_scene_cast(cid: str, sid: str, kind: str, id: str):
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    store.appearances.leave(cid, sid, kind, id)
    return {"ok": True}


@router.post("/campaigns/{cid}/scenes/{sid}/cast/batch")
def post_scene_cast_batch(cid: str, sid: str, body: AppearBatch):
    """Seat a whole suggestion cast in one request. Already-cast members are
    skipped (the per-member 409), matching what the chooser's serial loop
    tolerated; unknown actors still 404 the request."""
    _require_scene(cid, sid)
    added, skipped = 0, []
    for ref in body.refs:
        try:
            _seat_cast_member(cid, sid, ref)
            added += 1
        except store.appearances.AppearError:
            skipped.append(f"{ref.kind}/{ref.id}")
    return {"ok": True, "added": added, "skipped": skipped}


@router.get("/campaigns/{cid}/scenes/{sid}/suggestions")
def get_scene_suggestions(cid: str, sid: str):
    _require_scene(cid, sid)
    return store.appearances.suggestions(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/suggestions/dismiss")
def post_dismiss(cid: str, sid: str, body: Dismiss):
    _require_scene(cid, sid)
    store.scenes.add_dismissed(cid, sid, body.character)
    return {"ok": True}


@router.get("/campaigns/{cid}/scenes/{sid}/location")
def get_scene_location(cid: str, sid: str):
    _require_scene(cid, sid)
    history = store.scenes.get_location_history(cid, sid)

    def ref(eid: str) -> dict:
        try:
            name = store.overlay.read_entity(cid, "locations", eid)["meta"].get("name", eid)
        except store.entities.EntityNotFound:
            name = eid
        return {"id": eid, "name": name}

    return {"current": ref(history[-1]) if history else None,
            "visited": [ref(e) for e in history[:-1]]}


@router.put("/campaigns/{cid}/scenes/{sid}/location")
def put_scene_location(cid: str, sid: str, body: SceneLocation):
    _require_scene(cid, sid)
    try:
        result = store.scenes.set_location(cid, sid, body.location)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="location not found")
    return {"ok": True, **result}


@router.get("/campaigns/{cid}/scenes/{sid}/datetime")
def get_scene_datetime(cid: str, sid: str):
    _require_scene(cid, sid)
    history = store.scenes.get_time_history(cid, sid)
    current = None
    suggested = None
    if history:
        cfg = store.calendars.read_calendar(store.campaigns.campaign_root(cid))
        native = history[-1]
        try:
            current = {"native": native, **store.calendars.today_facts(cfg, native),
                       "cast": store.context.cast_datetime_facts(cid, sid, native)}
        except store.calendars.CalendarError:
            current = None  # misconfigured calendar — surface "no date" rather than 500
    else:
        # dateless: offer a pre-fill — the creation-time hint, else where the story left off
        hint = store.scenes.get_suggested_date(cid, sid)
        if not hint:
            try:
                recent = store.chronicle.recent(cid, 1)
                hint = recent[-1].get("date", "") if recent else ""
            except Exception:  # noqa: BLE001 — garbled chronicle.json
                hint = ""
        if hint:
            suggested = store.calendars.split_native(hint)[0]
    return {"current": current, "history": history, "suggested": suggested}


@router.put("/campaigns/{cid}/scenes/{sid}/datetime")
def put_scene_datetime(cid: str, sid: str, body: SceneDatetime):
    _require_scene(cid, sid)
    # Captured before the write: the sweep needs the moment being left, and
    # set_datetime appends to the same history it would read back.
    history = store.scenes.get_time_history(cid, sid)
    previous = history[-1] if history else None
    try:
        result = store.scenes.set_datetime(cid, sid, body.datetime)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Names the transitions for the advance digest. Generation is pure, so the
    # changes happen either way; without this they are simply never reported.
    weather_changes = store.weather.sweep(cid, result.get("id", sid), previous, body.datetime)
    return {"ok": True, **result, "weather_changes": weather_changes}


@router.get("/campaigns/{cid}/scenes/{sid}/response")
def get_scene_response(cid: str, sid: str):
    scene = _require_scene(cid, sid)
    try:
        campaign_meta = store.campaigns.read_campaign(cid)["meta"]
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.read_config()
    return _response_body(scene["meta"], campaign_meta, cfg, scene["meta"])


@router.put("/campaigns/{cid}/scenes/{sid}/response")
def put_scene_response(cid: str, sid: str, body: ResponseSettings):
    _require_scene(cid, sid)
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    _write_response(lambda f: store.scenes.set_response(cid, sid, f), fields)
    return {"ok": True}


@router.get("/campaigns/{cid}/scenes/{sid}/context")
def get_scene_context(cid: str, sid: str):
    scene = _require_scene(cid, sid)
    sections = []
    total = 0
    for s in store.context.context_sections(cid, sid):
        tokens = store.context.count_tokens(s["text"])
        total += tokens
        sections.append({"label": s["label"], "text": s["text"], "tokens": tokens})
    return {"model": scene["meta"].get("model", ""), "total_tokens": total, "sections": sections}


@router.get("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}")
def get_cast_detail(cid: str, sid: str, kind: str, id: str):
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    try:
        return store.appearances.cast_detail(cid, sid, kind, id)
    except (store.appearances.AppearError, store.characters.CharacterNotFound,
            store.characters.VersionNotFound, store.pcs.PCNotFound, store.pcs.PCVersionNotFound):
        raise HTTPException(status_code=404, detail="actor not found")


@router.put("/campaigns/{cid}/scenes/{sid}/messages/{index}")
def put_scene_message(cid: str, sid: str, index: int, body: EditMessage):
    _require_scene(cid, sid)
    # Macros resolved once at persist time (#137), same as a fresh send.
    content = store.context.expand_macros(
        body.content, store.context.scene_substitutions(cid, sid), cid, sid)
    try:
        store.scenes.edit_message(cid, sid, index, content)
    except IndexError:
        raise HTTPException(status_code=400, detail="message index out of range")
    except store.scenes.RollMessageImmutable:
        raise HTTPException(status_code=400, detail="a dice roll's transcript line can't be edited")
    return {"ok": True}
