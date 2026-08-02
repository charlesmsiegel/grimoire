"""Scenes and the play loop: scene CRUD and suggestions, the generating
routes (chat / retry / regenerate), cast seating, scene location, datetime and
response scope, the chronicle, and the absorb/audit end-of-scene flow."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

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
def get_scene(cid: str, sid: str, limit: int | None = None, before: int | None = None):
    """The scene, whole by default. With `limit` the body is instead the last
    `limit` messages ending before index `before` (default: the tail), plus
    `offset`/`total`/`has_older` so the reader can page backwards — see
    `scenes.read_scene_window`. Omitting `limit` keeps the unwindowed shape
    every other caller of this route already reads.
    """
    if limit is not None and limit < 1:
        raise HTTPException(status_code=400, detail="limit must be at least 1")
    if before is not None and before < 0:
        raise HTTPException(status_code=400, detail="before must not be negative")
    try:
        if limit is None:
            return store.scenes.read_scene(cid, sid)
        return store.scenes.read_scene_window(cid, sid, limit, before)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound):
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found")


@router.put("/campaigns/{cid}/scenes/{sid}")
def put_scene(cid: str, sid: str, body: RenameScene):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    try:
        new_sid = store.scenes.rename_scene(cid, sid, title)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound):
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found")
    return {"id": new_sid, "title": title}


@router.delete("/campaigns/{cid}/scenes/{sid}")
def delete_scene(cid: str, sid: str):
    try:
        store.scenes.delete_scene(cid, sid)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound):
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found")
    return {"ok": True}


def _disown_dead_guidance(cid: str, sid: str) -> None:
    """Re-aim the reroll hint before streaming something that did not send it.

    A guided reroll whose stream died leaves its hint parked for "whatever lands
    next". Every other way of streaming into that empty slot — Retry, and the
    empty-send / director turn, which persist no player message — never sent
    that hint, so the take they produce must not be labelled with it.

    Only over an *empty* slot. Above a live reply these paths append a
    consecutive generation rather than replacing one, which moves the slot and
    retires the set anyway; archiving there would park a reply nobody asked to
    replace. A normal send needs none of this: it appends a player message,
    which moves the anchor and retires the set on its own.
    """
    parked = store.alternates.state(cid, sid)
    if parked["runs"] and parked["active"] is None:
        store.alternates.archive(cid, sid, "")


@router.post("/campaigns/{cid}/scenes/{sid}/chat")
def post_chat(cid: str, sid: str, turn: ChatTurn, client: LLMClient = Depends(get_llm)):
    _require_scene(cid, sid)
    conn = _require_connection()
    store.proposals.supersede(cid, sid)  # a new send retires any pending decision
    if store.scenes.is_pcless(cid, sid) or not turn.content.strip():
        # ephemeral turn, never stored: a director note steering one generation
        # (pcless), or — in any scene — an empty send meaning "next NPC round"
        note = turn.content.strip() or prompts.render("scene/director_note.j2")
        _disown_dead_guidance(cid, sid)
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
    posted_at = store.scenes.append_message(cid, sid, "user", content, speaker=speaker)
    messages = store.context.build_messages(cid, sid, turn=_turn_override(turn))
    # The post has to precede the stream — `build_messages` renders history out
    # of the transcript, so a turn the model never sees is a turn it cannot
    # answer — which is exactly what makes a failed generation able to strand
    # it. Hand `_chat_stream` the undo so the pair is transactional (#95): if
    # the turn produces nothing at all, the post comes back off. `posted_at`
    # travels with it because nothing holds a lock across the stream, so by the
    # time the undo runs the tail may belong to a different turn entirely.
    return _chat_stream(cid, sid, messages, conn, client,
                        undo_user_post=lambda: store.scenes.remove_trailing_user_post(
                            cid, sid, posted_at, content))


@router.post("/campaigns/{cid}/scenes/{sid}/retry")
def post_retry(cid: str, sid: str, body: RetryBody | None = None,
               client: LLMClient = Depends(get_llm)):
    scene = _require_scene(cid, sid)
    conn = _require_connection()
    store.proposals.supersede(cid, sid)  # a fresh generation retires the old decision
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to retry")
    _disown_dead_guidance(cid, sid)
    messages = store.context.build_messages(cid, sid, turn=_turn_override(body))
    return _chat_stream(cid, sid, messages, conn, client)


def _restore_reroll(cid: str, sid: str, removed: dict):
    """Put the outgoing reply back, and take the hint back with it.

    Two halves of one undo. The transcript half is #95's: a reroll that produces
    nothing must not cost the reply it deleted. The sidecar half is this PR's —
    `archive` recorded the guidance against a replacement that never landed, and
    with the original run live again `_resolve` would credit it with an
    instruction it was not generated from, then persist that on the next write.

    Best-effort on the second, deliberately: the reply is the artifact that
    cannot be regenerated, and a wrong label is not worth failing a restore for.
    """
    def restore() -> None:
        store.scenes.restore_trailing_assistant_run(cid, sid, removed)
        try:
            store.alternates.disown_guidance(cid, sid)
        except OSError:
            pass
    return restore


@router.post("/campaigns/{cid}/scenes/{sid}/regenerate")
def post_regenerate(cid: str, sid: str, body: RegenerateBody | None = None,
                    client: LLMClient = Depends(get_llm)):
    """Redo the most recent post: park the trailing assistant reply as an
    alternate, stream a fresh one."""
    _require_scene(cid, sid)
    conn = _require_connection()
    # Heal now, retire later. Healing is what can append a 🎲 line, and the
    # checks below have to judge the transcript that leaves behind — but
    # RETIRING the decision waits until the reroll has actually committed to
    # happening, or a failure cancels a decision whose narration is still
    # exactly what the reader sees.  `heal` is idempotent and `supersede` calls
    # it again itself.
    store.proposals.heal(cid, sid)
    guidance = (body.guidance or "").strip() if body else ""
    removed: dict | None = None   # set only when there is actually a reply to drop
    # ONE lock across the decision, the archive and the removal. A gap anywhere
    # in that span is a gap another writer's generation can land in — and the
    # removal would then take a reply the archive never saw, losing exactly what
    # the non-destructive guarantee promises to keep. Held only across a read
    # and two file writes; the stream starts after it is released.
    with store.locks.campaign_lock(cid):
        # Read AFTER the heal, which can append a 🎲 line the pre-heal snapshot
        # doesn't have. Judging the checks below on a stale snapshot let the
        # ROLL_SPEAKER guard pass and `remove_trailing_assistant_run` then
        # refuse (IndexError -> 500) — it never deletes a roll line, but the
        # caller deserves the 400 instead.
        msgs = _require_scene(cid, sid)["messages"]
        if not msgs:
            raise HTTPException(status_code=400, detail="nothing to regenerate")
        # Trailing scene transitions are stepped over, not consumed: reroll
        # targets the last generation BENEATH them, and every check below has to
        # look at that generation rather than at the transition line on top.
        core = msgs[:len(msgs) - store.scenes.trailing_transitions(msgs)]
        # An archived set whose slot is EMPTY means the previous reroll's stream
        # died before its replacement landed. Generations can sit back to back
        # (an empty send and a director turn persist no player message), so what
        # is exposed at the tail is then the generation BEFORE that slot —
        # removing it would delete a reply nobody asked to reroll and carry the
        # slot past the parked one, losing both. Stream into the empty slot.
        parked = store.alternates.state(cid, sid)
        replacing = (bool(core) and core[-1]["role"] == "assistant"
                     and not (parked["runs"] and parked["active"] is None))
        if replacing:
            if all(m["role"] == "assistant" for m in core):
                raise HTTPException(status_code=400,
                                    detail="cannot regenerate the opening post")
            if core[-1].get("speaker") in store.scenes.SYNTHETIC_SPEAKERS:
                # Enumerated from the same tuple `remove_trailing_assistant_run`
                # refuses on, not from one speaker name: only ROLL_SPEAKER is
                # reachable here (trailing transitions are already stripped
                # above), but a future synthetic speaker gets this 400 rather
                # than the bare IndexError (500) that refusal would surface.
                raise HTTPException(status_code=400,
                                    detail="cannot regenerate past a manual dice roll")
        # Archive BEFORE the removal, and let the replacement join the set on
        # its own when the next read reconciles: no callback has to fire deep
        # inside the stream's persist path, and a stream that dies between the
        # two leaves the outgoing reply recoverable rather than gone.
        #
        # Called even with nothing to remove. There is no run to keep then, but
        # the hint still has to be re-aimed at whatever lands next, or the
        # variant that does land is filed under the *previous* attempt's
        # guidance. `archive` writes nothing at all for a scene that has no set,
        # so the refusals above still leave the scene untouched.
        store.alternates.archive(cid, sid, guidance)
        if replacing:
            try:
                removed = store.scenes.remove_trailing_assistant_run(cid, sid)
            except Exception as exc:
                # The archive above recorded the hint against a replacement that
                # is now never going to land, and the reply it was meant to
                # replace is still live. Left there, the next resolution credits
                # that unchanged reply to an instruction it never received.
                #
                # Best-effort: whatever stopped the removal (a full disk, most
                # likely) may stop this too, and the original failure is the one
                # worth reporting.
                try:
                    store.alternates.disown_guidance(cid, sid)
                except OSError:
                    pass    # the disk that stopped the removal can stop this too
                if isinstance(exc, store.scenes.TurnSizesDesynced):
                    # Refusing beats guessing: the recorded turn boundaries don't
                    # fit the transcript, so any deletion could take blocks from
                    # an earlier generation. The transcript is untouched.
                    raise HTTPException(
                        status_code=400,
                        detail="this scene's recorded turn boundaries no longer match its "
                               "transcript — delete the last reply manually to regenerate") from exc
                raise
        # Built BEFORE the retirement below, not after: `supersede` writes
        # proposals.json and can fail, and until this exists there is nothing
        # holding the way back — the reply would be gone with the decision it
        # was derived from still pending and still acceptable.
        restore = _restore_reroll(cid, sid, removed) if removed else None
        # Everything that can refuse has refused and the removal is on disk, so
        # this reroll is committed: retire the decision the outgoing narration
        # was derived from, exactly as a fresh generation does anywhere else.
        try:
            store.proposals.supersede(cid, sid)
        except BaseException:
            if restore is not None:
                restore()
            raise
    # Everything from here to the `return` runs with the scene one reply short,
    # and until the stream exists there is nothing holding the way back: the
    # restore hooks live inside `_chat_stream`'s generator, so a raise here
    # would delete a reply and hand the caller a 500 with no trace of it.
    # Reachable without any race — `build_messages` reads the whole store, and
    # `prompts.render` compiles a template (review, #95).
    try:
        # rendered before the context build so its tokens can be reserved against
        # the context budget -- it is appended unconditionally, so the packer must
        # not fit the prompt to a ceiling this then pushes it over
        block = prompts.render("scene/regenerate_guidance.j2", guidance=guidance) if guidance else ""
        messages = store.context.build_messages(cid, sid, turn=_turn_override(body),
                                                reserve=(block,) if block else ())
        if block:
            messages.append({"role": "system", "content": block})
    except BaseException:
        if restore is not None:
            restore()
        raise
    # The old reply had to go before the context was built — the builders read
    # the transcript, so the model cannot be asked to replace something it can
    # still see. That leaves a window where the scene is one reply short and the
    # replacement does not exist yet, and a generation producing nothing would
    # end it there: a reply destroyed by a reroll the player stopped or that
    # never started (#95). Hand the stream the way back.
    #
    # The parked alternate is the other half of that guarantee and survives
    # either way: `archive` ran before the removal, so a stream that never
    # lands leaves the reply recoverable through the swipe control even if the
    # restore hook never fires.
    #
    # One window this does NOT close, deliberately, because closing it is a
    # redesign rather than a guard: between this return and the generator's
    # first step, the response body can be cancelled outright — uvicorn reports
    # ASGI spec 2.3, so Starlette races `stream_response` against
    # `listen_for_disconnect` in a task group, and an already-queued disconnect
    # cancels the former before it runs. An async generator that never started
    # runs none of its body on close, so no hook here can fire. The fix is to
    # stop deleting ahead of the replacement at all — remove the old run inside
    # `finalize`, under the same lock that writes the new reply — which is
    # tracked with the other transcript-identity work rather than bolted on.
    return _chat_stream(cid, sid, messages, conn, client, restore_removed=restore)


_PREVIEW_CHARS = 200


def _alternate(run: dict) -> dict:
    """One variant as the transcript would read it — the posts joined the way
    they render, clipped. The full text is never sent: the client's job is to
    pick a variant, and the one it picks arrives as transcript."""
    text = "\n\n".join(s["content"] for s in run["segments"])
    return {"id": store.alternates.variant_id(run),
            "created": run.get("created", ""), "guidance": run.get("guidance", ""),
            "posts": len(run["segments"]),
            "preview": text[:_PREVIEW_CHARS] + ("…" if len(text) > _PREVIEW_CHARS else "")}


@router.get("/campaigns/{cid}/scenes/{sid}/alternates")
def get_scene_alternates(cid: str, sid: str):
    """Every variant of the generation a reroll would replace. `active` is the
    one in the transcript, and null means the slot is empty — what a reroll
    whose stream died leaves behind."""
    _require_scene(cid, sid)
    state = store.alternates.state(cid, sid)
    return {"active": state["active"],
            "alternates": [_alternate(r) for r in state["runs"]]}


@router.post("/campaigns/{cid}/scenes/{sid}/alternates/{vid}")
def post_scene_alternate(cid: str, sid: str, vid: str):
    """Cycle (or pin) a stored variant into the transcript, parking the live one.

    Addressed by `variant_id`, not by position: retention shifts every index
    when a full set gains a variant, and a client snapshot from before that
    shift would otherwise name a different take than the one it previewed.
    """
    _require_scene(cid, sid)
    # One lock over the whole check-retire-swap span, so nothing lands between
    # the three and the request is all-or-nothing.
    with store.locks.campaign_lock(cid):
        # Resolve FIRST. The id comes from a client snapshot that another tab
        # may have moved on from, and retiring a decision is not something a
        # request that then 404s gets to do: a stale click would cancel a
        # proposal belonging to a turn it never swaps past. Position is safe
        # from here on — `promote` re-resolves under this same lock.
        state = store.alternates.state(cid, sid)
        index = next((i for i, r in enumerate(state["runs"])
                      if store.alternates.variant_id(r) == vid), None)
        if index is None:
            raise HTTPException(status_code=404, detail="alternate not found")
        if state["active"] == index:
            # Already showing. `promote` would return without touching the
            # transcript, so retiring anything below would be a side effect of a
            # request that changed nothing — and a delayed click for a variant
            # another tab has since promoted would cancel that tab's proposal.
            return {"ok": True}
        # Heal now, retire after. Healing is what can append a 🎲 line, and
        # `promote` has to reconcile against the transcript that leaves behind —
        # but a swap that fails must not take the decision with it. The sidecar
        # preflight is not enough on its own: `promote` writes the TRANSCRIPT
        # too, and that write can fail after the sidecar's has succeeded,
        # leaving the reader looking at the exact narration the proposal was
        # derived from with no way to resolve it. Same split as regenerate.
        store.proposals.heal(cid, sid)
        try:
            store.alternates.promote(cid, sid, index)
        except store.alternates.AlternateNotFound:
            # Reachable despite the check above only if that heal appended a
            # roll line and moved the slot -- in which case the transcript now
            # ends on a roll and there is nothing to swap.
            raise HTTPException(status_code=404, detail="alternate not found")
        except store.scenes.TurnSizesDesynced:
            # Same refusal regenerate makes, for the same reason: boundaries that
            # do not fit the transcript cannot authorize replacing a run.
            raise HTTPException(
                status_code=400,
                detail="this scene's recorded turn boundaries no longer match its "
                       "transcript — delete the last reply manually to swap alternates")
        # The transcript is now showing a different take, so the decision the
        # old narration produced is retired — and only now that it really is.
        # Accepting a proposal whose text is no longer on screen would continue
        # a mechanical decision nothing there asked for.
        store.proposals.supersede(cid, sid)
    return {"ok": True}


@router.get("/campaigns/{cid}/chronicle")
def get_chronicle(cid: str):
    _campaign_root_or_404(cid)
    return store.chronicle.recent(cid, 50)


# Indirection so tests can drive budget arithmetic off a fake clock instead of
# real waiting. Deliberately NOT time.time(): a wall-clock jump (NTP, DST,
# sleep/wake) must not expire or extend a running absorb.
_clock = time.monotonic
BUDGET_EXHAUSTED = "absorb time budget exhausted"


class BudgetRefused(LLMError):
    """The budget was already gone, so the call was never issued.

    An LLMError subclass, so every existing `except LLMError` still covers it —
    but a phase that cares can tell "never sent" from "sent, then cancelled".
    Only the first means the step was never attempted, and only the first is a
    step to report as skipped rather than failed."""


class _Budget:
    """A wall-clock ceiling on one absorb's whole LLM sequence (#243).

    Absorb awaits an extraction call, then one dossier call per present NPC,
    then one voice-drift call per present NPC that has a voice anchor, then an
    audit call, all inside a single HTTP request — the per-call idle
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

    async def run(self, coro, on_start=None):
        """Await `coro` under the remaining budget, reporting an overrun as the
        same LLMError kind an upstream stall raises — so every caller's existing
        LLM failure handling covers it with no new branch.

        `on_start` fires only if the call actually goes out, and `BudgetRefused`
        says it did not. Both exist because this is the only place that can
        decide it: a caller's own `spent()` check is already stale by the time
        the deadline is read below, however few statements sit in between.

        wait_for waits for the cancellation it requests to complete, so the
        real ceiling is the budget plus however long the call takes to unwind;
        that unwinding is itself hard-bounded in `llm` (grace-then-abandon),
        which is what keeps this a bound rather than a hope.
        """
        left = self.remaining()
        if left is None:
            if on_start:
                on_start()
            return await coro
        if left <= 0:
            # Handled here rather than left to wait_for, which cancels a task
            # before its first step and so reports a timeout for a request that
            # never left. `close()` retires the coroutine we are not going to
            # await, which would otherwise warn at collection.
            coro.close()
            raise BudgetRefused("timeout", BUDGET_EXHAUSTED)
        # Past this point wait_for runs the task's first step before its timer
        # can fire, so the call is issued even if the answer never arrives.
        if on_start:
            on_start()
        try:
            return await asyncio.wait_for(coro, left)
        except asyncio.TimeoutError as exc:
            # asyncio.TimeoutError is the builtin TimeoutError from 3.11 on, so
            # this also catches one raised *inside* the call. Only blame the
            # budget when the budget is actually gone.
            detail = BUDGET_EXHAUSTED if self.spent() else (str(exc) or "the call timed out")
            raise LLMError("timeout", detail) from exc


def _budget_overrun(exc: BaseException) -> bool:
    """Whether `exc` is this absorb's own clock running out rather than an
    upstream stall.

    `_Budget.run` deliberately reports both as the same LLMError kind so callers
    need no extra branch to *handle* them -- but a phase needs to *say* which
    happened, because only one of them is fixed by a larger budget. The detail
    is the sentinel `_Budget.run` sets, not a substring guess."""
    return isinstance(exc, LLMError) and exc.detail == BUDGET_EXHAUSTED


def _phase_report(dossiers: dict, voice: dict, mechanics: dict) -> list[dict]:
    """One row per LLM-backed step of this absorb, in run order: was it
    attempted, how did it end, and was the shared time budget what stopped it
    (#243/#236 follow-up).

    Without it, a slow-but-healthy extraction that eats the whole budget returns
    an absorb with fewer proposed edits and no way to tell that apart from a
    model that simply had nothing to suggest.

    Each row is *projected* from the block that already reports that step -- the
    `audit` row from `mechanics`, which is that step's block -- so `phases` is a
    uniform view rather than a second source of truth that can drift from what
    the review panel renders beside it.

    Extraction gets no block because it has no partial outcome: `post_absorb`
    raises 502 when it fails, so reaching this call already proves it succeeded.

    ("Phase" here means a step of one absorb run; the `Phase 2:`/`Phase 5:`
    comments elsewhere in this file are roadmap milestones, unrelated.)"""
    keys = ("status", "reason", "attempted", "budget_exhausted")
    return [{"name": "extraction", "status": "ok", "reason": None,
             "attempted": True, "budget_exhausted": False}] + \
           [{"name": name, **{k: block[k] for k in keys}}
            for name, block in (("dossiers", dossiers), ("voice", voice),
                                ("audit", mechanics))]


async def _run_audit(cid: str, sid: str, client: LLMClient, conn: dict,
                     budget: _Budget) -> tuple[list[dict], dict]:
    """(edits, mechanics) for the scene audit. Never raises; every failure is
    an explicit mechanics status (spec: audit visibility) so absorb stays
    intact even when the audit pipeline blows up.

    `attempted` says whether a request actually reached the model and
    `budget_exhausted` says whether this absorb's clock is why it did not --
    the two facts a bare `status: failed` cannot carry."""
    mech = {"status": "skipped", "reason": None, "warnings": [], "dropped": [],
            "attempted": False, "budget_exhausted": False}
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
        # `mech` is the accumulator every failure return below spreads, so the
        # callback reaches all of them -- and it fires only if the request goes
        # out, which is a fact only `run` holds.
        text = await budget.run(client.complete(messages, conn),
                                lambda: mech.__setitem__("attempted", True))
        parsed = store.audit.parse_output(text)
        edits, dropped = store.audit.materialize(cid, sid, parsed)
    except BudgetRefused:
        # Never asked, so there is no finding to doubt -- only work still owed.
        # Still `failed` (not `skipped`) and still paired with the POST /audit
        # retry the UI renders: a fresh budget is exactly what that retry gets.
        return [], {**mech, "status": "failed", "budget_exhausted": True,
                    "reason": "the absorb time budget ran out before the audit could run",
                    "dropped": excluded}
    except store.audit.AuditParseError as exc:
        return [], {**mech, "status": "failed", "reason": str(exc),
                    "dropped": excluded}
    except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
        # A budget overrun reaching *this* handler was cancelled mid-flight, so
        # the request did go out and `mech["attempted"]` stays true -- the never
        # -sent case is `BudgetRefused`, caught above.
        return [], {**mech, "status": "failed", "reason": f"audit failed: {exc}",
                    "dropped": excluded, "budget_exhausted": _budget_overrun(exc)}
    dropped = excluded + dropped
    status = "degraded" if dropped else "ok"
    reason = ("some sheets could not be audited" if excluded else
              "some findings could not be validated") if dropped else None
    return edits, {"status": status, "reason": reason,
                   "warnings": parsed["warnings"], "dropped": dropped,
                   "attempted": True, "budget_exhausted": False}


async def _stage_dossiers(cid: str, sid: str, transcript: str, client: LLMClient,
                          conn: dict, budget: _Budget) -> tuple[list[dict], dict]:
    """Propose a refreshed campaign dossier for every present NPC, reporting the
    outcome.

    The LLM call happens here; the WRITE does not (#235). Each dossier comes back
    as a StagedEdit that lands with the rest of the batch in PUT /chronicle, so an
    absorb that dies partway through this loop -- or a reviewer who hits Cancel --
    leaves nothing behind. `proposed` therefore names the NPCs whose dossier was
    generated, not written; an NPC whose paragraph came back unchanged is proposed
    with no edit to show for it.

    Never raises -- a dossier failure must not fail absorb -- but it is not silent
    either: failures (#236) and budget skips (#243) come back as a status the
    inspector renders, mirroring _run_audit's shape -- including `attempted` and
    `budget_exhausted`, the two flags a phase row is built from."""
    out: dict = {"status": "skipped", "reason": None,
                 "proposed": [], "failed": [], "skipped": [],
                 "attempted": False, "budget_exhausted": False}
    edits: list[dict] = []
    try:
        cast = store.appearances.scene_cast(cid, sid)
        croot = store.appearances.locked_actor_root(cid)   # cast actors are locked, so campaign-side
    except Exception as exc:  # noqa: BLE001 -- an unreadable cast is a failed phase, not a 500
        return [], {**out, "status": "failed", "reason": f"could not read the scene cast: {exc}"}
    def drop_tail(i: int) -> None:
        """Record the NPC at `i` and everyone after it as never reached.

        The extraction call is the part worth keeping, so the tail is dropped
        rather than run unbounded (#243) — but named, not silently, for the same
        reason failures are (#236)."""
        out["skipped"] = [b["id"] for b in cast[i:]
                          if b["kind"] == "characters" and b["role"] == "npc"]
        out["budget_exhausted"] = True

    for i, a in enumerate(cast):
        if a["kind"] != "characters" or a["role"] != "npc":
            continue  # dossiers feed the npc-only "Active elsewhere" tier; skip player cards
        if budget.spent():
            drop_tail(i)
            break
        try:
            name = store.characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            # Read ONCE, before the await. Re-reading after it to build the
            # staged `before` would record a paragraph another review wrote
            # while this call was in flight -- the conflict guard would then
            # pass and this stale output would overwrite that newer one (#235).
            prior = store.dossiers.read(croot, a["id"])
            msgs = store.dossiers.build_prompt(name, prior, transcript)
            # The loop's own check is stale by now -- the two reads and the
            # prompt build above are not free -- so the attempt is recorded by
            # `run`, which alone can decide it atomically with the deadline.
            d_text = await budget.run(client.complete(msgs, conn),
                                      lambda: out.__setitem__("attempted", True))
            parsed_dossier = store.dossiers.parse_output(d_text)
            # stage_edit returns None for an unchanged paragraph AND for a blank
            # reply; only the first is a success. Left conflated, a model that
            # answers "" for every NPC reports `ok` with nothing staged -- exactly
            # #236's symptom (dossiers quietly stop updating) wearing a status.
            if not parsed_dossier:
                out["failed"].append({"id": a["id"], "reason": "empty dossier reply"})
                continue
            edit = store.dossiers.stage_edit(a["id"], name, prior, parsed_dossier)
        except BudgetRefused:
            # Refused, not failed: nothing was sent, so this NPC is one more the
            # clock never reached — and so is everyone after them.
            drop_tail(i)
            break
        except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
            # Type-prefixed: a bare str() is useless for the store's own errors
            # (CharacterNotFound("aese") stringifies to just "aese").
            detail = str(exc).strip()
            out["budget_exhausted"] = out["budget_exhausted"] or _budget_overrun(exc)
            out["failed"].append({
                "id": a["id"],
                "reason": f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__})
        else:
            out["proposed"].append(a["id"])
            if edit:
                edits.append(edit)
    if not out["proposed"] and not out["failed"] and not out["skipped"]:
        return edits, {**out, "reason": "no npcs present"}
    if not out["proposed"]:
        # A budget that ran out before the first call is a different story from
        # calls that were made and went wrong; say which one happened.
        return edits, {**out, "status": "failed",
                       "reason": "no dossier could be prepared" if out["failed"] else
                                 "the absorb time budget ran out before any dossier "
                                 "could be prepared"}
    if out["failed"]:  # the more specific story when both happened; `skipped` still lists the rest
        return edits, {**out, "status": "degraded",
                       "reason": "some dossiers could not be prepared"}
    if out["skipped"]:
        return edits, {**out, "status": "degraded",
                       "reason": "the absorb time budget ran out before the rest "
                                 "could be prepared"}
    return edits, {**out, "status": "ok"}


async def _stage_voice_drift(cid: str, sid: str, transcript: str, client: LLMClient,
                             conn: dict, budget: _Budget) -> tuple[list[dict], dict]:
    """Judge every present NPC's dialogue in this scene against their voice
    anchor, proposing a drift flag (or a clear) for each.

    Runs ONLY for NPCs that actually have an anchor (#59). That is the cost
    control for the whole feature: a library with no anchors makes no extra LLM
    calls, and adding one is how a user opts a character in.

    The LLM call happens here; the WRITE does not, for _stage_dossiers' reason
    (#235) -- a flag that landed before the reviewer saved would survive a
    Cancel and go on correcting the model for a scene the chronicle never
    recorded.

    Never raises -- voice drift must not fail absorb -- but it is not silent
    either: failures and budget skips come back as a status the inspector
    renders, mirroring _stage_dossiers' shape -- including `attempted` and
    `budget_exhausted`, the two flags a phase row is built from."""
    out: dict = {"status": "skipped", "reason": None, "checked": [], "flagged": [],
                 "unjudged": [], "failed": [], "skipped": [],
                 "attempted": False, "budget_exhausted": False}
    edits: list[dict] = []
    try:
        cast = store.appearances.scene_cast(cid, sid)
        croot = store.appearances.locked_actor_root(cid)   # cast actors are locked, so campaign-side
        speakers = store.appearances.roster_names(cid)
    except Exception as exc:  # noqa: BLE001 -- an unreadable cast is a failed phase, not a 500
        return [], {**out, "status": "failed", "reason": f"could not read the scene cast: {exc}"}
    # The speaker labels this scene's transcript can carry. transcript.j2 labels
    # each line with the speaker's card name and nothing else, so an anchored NPC
    # sharing a label with ANY other speaker is unjudgeable -- the judge cannot
    # tell whose lines belong to its subject, and answers confidently regardless.
    #
    # Counted over the CAMPAIGN roster, not the present cast, because the present
    # cast is not the set of speakers: `scene_cast` drops an actor the moment it
    # leaves, while the transcript keeps every line it spoke, still wearing its
    # name. `_drift_roster` reaches for the same list against the same problem.
    # Nothing records which scene a departed actor spoke in, so this over-counts
    # a same-named actor who was never here -- the safe direction, since the
    # alternative is a corrective persisted against the wrong character.
    # Plus the role fallbacks transcript.j2 uses for a line carrying no speaker
    # stamp -- those are labels too. Whether this scene has any such line is not
    # recoverable from the rendered transcript, and an ambiguous judgment must
    # never be persisted, so a character wearing one of these names is
    # disqualified unconditionally rather than on a guess.
    speakers = [n for n in speakers if isinstance(n, str) and n.strip()]
    speakers += ["You", "Grimoire"]

    # Resolved up front so the budget check below covers only the actors that
    # will really cost a call: an anchorless NPC must not be reported as
    # "skipped for time" when it was never going to be judged at all.
    todo = []
    for a in cast:
        if a["kind"] != "characters" or a["role"] != "npc":
            continue   # a player character's voice is the user's to drift
        try:
            record = store.overlay.voice_anchor_record(cid, a["id"])
        except Exception as exc:  # noqa: BLE001 -- unreadable anchor: skip this actor, keep the phase
            out["failed"].append({"id": a["id"], "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if record["text"]:
            # `a["name"]` is the LOCKED VERSION's card name, which is what the
            # transcript labels this character's lines with. The container's
            # meta name can differ from it, and naming the judge a character the
            # transcript never mentions is how a multi-NPC scene gets judged
            # against the wrong lines (or comes back `not_enough`).
            #
            # Cards are stored as arbitrary dicts, so `data.name` can be a
            # number or an object -- import and version PUT both accept them.
            # Checked HERE, inside the per-actor boundary: everything below
            # treats the name as text, and one bad card must cost that actor its
            # voice check rather than 500 the whole absorb.
            # The RAW card name, not `a["name"]`: `_actor_name` substitutes the
            # actor id for a card that carries no usable one, and that id is a
            # display convenience, not something the transcript is known to
            # label anyone with. Judging against it means judging against a
            # name nobody agreed on.
            try:
                vid = store.appearances.locked_version(cid, "characters", a["id"])
                data = store.characters.read_card(croot, a["id"], vid).get("data")
            except Exception as exc:  # noqa: BLE001 -- unreadable card: skip this actor
                out["failed"].append({"id": a["id"], "reason": f"{type(exc).__name__}: {exc}"})
                continue
            name = data.get("name") if isinstance(data, dict) else None
            # `label_preserved`, not merely "is a nonblank string": the
            # serializer silently writes the generic role label instead of a
            # name it cannot form a marker from (one holding `*` or a newline,
            # or over 64 characters). Those lines land in the transcript as
            # "Grimoire", so pointing the judge at the card name hunts for a
            # speaker that cannot appear -- and risks charging generic
            # assistant prose to this character instead.
            if not isinstance(name, str) or not store.scenes.label_preserved(name):
                out["failed"].append({
                    "id": a["id"],
                    "reason": "the locked card has no name that can appear as a transcript "
                              "label, so the judge cannot be pointed at its lines"})
                continue
            # Report the clash instead of judging through it -- naming it is
            # actionable (rename a card), whereas judging is a coin flip
            # presented as a finding, and a wrong one persists a corrective
            # that nags a character for dialogue someone else spoke.
            # `scenes.confusable`, not a whole-name comparison: `match_name` is
            # what decided which cast member a written label meant when the
            # reply was stored, so it is what decides whether a label is
            # ambiguous. "Winifred Vance" and "Winifred Vale" are distinct
            # strings, but a block labelled "Winifred" belongs to neither.
            if store.scenes.confusable(name, speakers):
                out["failed"].append({
                    "id": a["id"],
                    "reason": f"another speaker in this scene can also be labelled {name!r}, "
                              "so their lines cannot be told apart in the transcript"})
                continue
            todo.append((a["id"], name, record))

    def drop_tail(i: int) -> None:
        """Record the anchored NPC at `i` and everyone after it as never
        reached -- named, not silently dropped, for _stage_dossiers' reason."""
        out["skipped"] = [b for b, _, _ in todo[i:]]
        out["budget_exhausted"] = True

    for i, (aid, name, record) in enumerate(todo):
        if budget.spent():
            drop_tail(i)
            break
        try:
            # Read ONCE, before the await -- dossiers' rule: re-reading after it
            # would record a flag another review wrote while this call was in
            # flight, and the conflict guard would then pass on stale output.
            # One snapshot, so the note and the provenance staged as `before`
            # always describe the same committed flag (voice_drift.read_record).
            flag = store.voice_drift.read_record(croot, aid)
            prior, prior_fp = flag["note"], flag["anchor"]
            msgs = store.voice_drift.build_prompt(name, record["text"], transcript)
            # The loop's own check is stale by now, so the attempt is recorded
            # by `run`, which alone can decide it atomically with the deadline.
            text = await budget.run(client.complete(msgs, conn),
                                    lambda: out.__setitem__("attempted", True))
            finding = store.voice_drift.parse_output(text)
            # An unreadable verdict is a FAILED call, not a quiet pass. Left
            # conflated with "in voice" it would stage a default-approved clear
            # of a standing flag on the strength of a garbled reply -- and a
            # model that answers nonsense for every NPC would report `ok` while
            # retiring the campaign's correctives one by one.
            if finding["verdict"] == store.voice_drift.UNKNOWN:
                out["failed"].append({"id": aid, "reason": "unreadable verdict from the voice judge"})
                continue
            # Both note checks are DRIFT-only, because only a drift verdict
            # stores a note: `stage_edit` writes `after=""` for IN_VOICE and
            # proposes nothing at all for NOT_ENOUGH, so their notes never reach
            # a prompt. Failing the call on an oversized note there would punish
            # a chatty explanation by leaving an obsolete corrective standing --
            # the clear is the whole point of a clean verdict, and a note nobody
            # stores cannot cost a single token.
            if finding["verdict"] == store.voice_drift.DRIFT:
                # No note is unusable: the note IS the corrective the next turn
                # gets. Report it rather than staging a flag that would say
                # nothing, or silently downgrading it to "fine".
                if not finding["note"]:
                    out["failed"].append({"id": aid, "reason": "drift reported with no corrective"})
                    continue
                # ...and the corrective is rendered into the post-history
                # message, which the packer reserves and cannot trim, so an
                # oversized one is charged against every later generation with
                # nothing able to give way.
                if len(finding["note"]) > store.voice_drift.MAX_NOTE:
                    out["failed"].append({
                        "id": aid,
                        "reason": f"the voice judge returned a corrective over "
                                  f"{store.voice_drift.MAX_NOTE} characters, too long to put "
                                  f"in front of every following turn"})
                    continue
            edit = store.voice_drift.stage_edit(aid, name, prior, finding,
                                                record["text"], record["id"], prior_fp)
        except BudgetRefused:
            # Refused, not failed: nothing was sent, so this NPC is one more the
            # clock never reached — and so is everyone after them.
            drop_tail(i)
            break
        except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
            detail = str(exc).strip()
            out["budget_exhausted"] = out["budget_exhausted"] or _budget_overrun(exc)
            out["failed"].append({
                "id": aid,
                "reason": f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__})
        else:
            out["checked"].append(aid)
            if finding["verdict"] == store.voice_drift.DRIFT:
                out["flagged"].append(aid)
            elif finding["verdict"] == store.voice_drift.NOT_ENOUGH:
                # A real judgment that produces no edit -- like a dossier that
                # came back unchanged. Named so "checked, not flagged" does not
                # read as "confirmed in voice" when nobody actually heard them.
                out["unjudged"].append(aid)
            if edit:
                edits.append(edit)
    if not out["checked"] and not out["failed"] and not out["skipped"]:
        return edits, {**out, "reason": "no anchored npcs present"}
    if not out["checked"]:
        return edits, {**out, "status": "failed",
                       "reason": "no voice check could be run" if out["failed"] else
                                 "the absorb time budget ran out before any voice check "
                                 "could be run"}
    if out["failed"]:  # the more specific story when both happened; `skipped` still lists the rest
        return edits, {**out, "status": "degraded",
                       "reason": "some voice checks could not be run"}
    if out["skipped"]:
        return edits, {**out, "status": "degraded",
                       "reason": "the absorb time budget ran out before the rest "
                                 "could be checked"}
    return edits, {**out, "status": "ok"}


def _already_absorbed(scene: dict) -> bool:
    """Whether THIS scene was absorbed, read from its own frontmatter.

    Deliberately not `sid in chronicle`: scene numbers are derived from the files
    on disk and `delete_scene` leaves the chronicle entry behind, so deleting the
    highest-numbered absorbed scene and remaking it under the same title hands the
    new scene the same id. A chronicle lookup would then refuse to absorb a
    brand-new scene. `done` is written only by mark_absorbed, into the scene file
    itself, so a recycled id starts clean."""
    return str(scene.get("meta", {}).get("done", "")).lower() == "true"


def _absorb_snapshot(cid: str, sid: str) -> tuple[int, dict]:
    """The scene and its commit epoch as of one instant, under one lock hold.

    Split out of `post_absorb` so it can be handed to a worker thread: the
    acquire blocks, and `post_absorb` runs on the event loop. Raises
    `_require_scene`'s 404 like any other handler code -- run_in_threadpool
    propagates it.
    """
    with store.locks.campaign_lock(cid):
        return store.commits.scene_epoch(cid, sid), _require_scene(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/absorb")
async def post_absorb(cid: str, sid: str, force: bool = False,
                      client: LLMClient = Depends(get_llm)):
    # Read before ANY of the scene state this review is built from (#271),
    # `meta.done` included. The token minted at the end of this handler is
    # stamped with this value, and the stamp has to date the snapshot, not the
    # response: a save landing while this handler runs advances the epoch, and a
    # stamp taken afterwards would match it -- letting a proposal built from
    # pre-save state pass its own supersession check, and letting the
    # already-absorbed guard below read a `done` that the save has since set.
    #
    # The campaign is validated first because this reads under campaign_root,
    # and an unusable cid has to surface as a 404 rather than a 500
    # (test_path_guard_store).
    #
    # Both reads under ONE hold, because `PUT /chronicle` advances the epoch in
    # `reserve()` and writes `meta.done` in `mark_absorbed()` several steps
    # later. Reading across that gap would pair the new epoch with a stale
    # `done` -- the already-absorbed guard below would wave the review through,
    # and its token would carry an epoch current enough to survive the
    # supersession check, so a second absorption of the same transcript could
    # save. The hold is two reads long and the commit holds the same lock for
    # its whole sequence, so this snapshot falls wholly before or wholly after.
    # Off the event loop, because this handler is async and the acquire is a
    # blocking one: a save holding the campaign lock would otherwise stall the
    # whole process for up to `locks.LOCK_TIMEOUT`, freezing every unrelated
    # request and open stream rather than just this campaign's absorb. Same
    # treatment `streaming.py` gives its blocking finalizers.
    _campaign_root_or_404(cid)
    epoch, scene = await run_in_threadpool(_absorb_snapshot, cid, sid)
    conn = _require_connection()
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to absorb")
    # Absorb is not idempotent: lore edits append and plot movements add a beat,
    # so a second pass over the same scene duplicates both. Refuse by default
    # (before spending a token) and make the re-run an explicit choice (#235).
    if _already_absorbed(scene) and not force:
        raise HTTPException(
            status_code=409,
            detail={"detail": "this scene has already been absorbed", "kind": "already_absorbed"})
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
    # Phase 2: propose each present NPC's refreshed campaign dossier -- staged, not
    # written (never raises -- see _stage_dossiers' own failure boundary).
    dossier_edits, dossiers = await _stage_dossiers(cid, sid, transcript, client, conn, budget)
    edits += dossier_edits
    # #59: judge each anchored NPC's dialogue against its voice anchor -- staged,
    # not written (never raises -- see _stage_voice_drift's own failure boundary).
    voice_edits, voice = await _stage_voice_drift(cid, sid, transcript, client, conn, budget)
    edits += voice_edits
    # Phase 5: audit the scene's mechanics against the sheeted cast (never
    # raises -- see _run_audit's own failure boundary).
    audit_edits, mechanics = await _run_audit(cid, sid, client, conn, budget)
    return {"one_line": parsed["one_line"], "summary": parsed["summary"],
            "keywords": parsed["keywords"], "timeline_events": parsed["timeline_events"],
            **facts, "edits": edits + audit_edits, "mechanics": mechanics,
            "dossiers": dossiers, "voice": voice,
            # One uniform row per step so a short absorb is legible as one
            # (see _phase_report) rather than as a model with nothing to say.
            "phases": _phase_report(dossiers, voice, mechanics),
            # Idempotency key for the save this review will become (#235): the
            # commit appends in six places, so a replay whose first response was
            # lost must return that result rather than apply it again. It also
            # carries the scene's commit epoch as captured at the TOP of this
            # handler -- what tells a save that some OTHER review of the scene
            # committed while this one was being prepared or sat open (#271).
            "commit_token": store.commits.mint(epoch)}


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
    # One hold across the whole persistence sequence (#234). These are four
    # independent writes; with a lock taken per write, contention arriving
    # partway returns 409 after the chronicle record and timeline events are
    # already durable -- and the retry that 409 invites appends the timeline
    # events a second time while the first attempt's approved edits were never
    # applied. Holding it here means a busy response is reported before the
    # first write, so a retry is safe. Reentrant, so the inner acquisitions
    # cost nothing.
    #
    # The same hold is what makes apply_edits' dossier branch safe (#235): it
    # compares the staged `before` with what is stored and then writes, and two
    # concurrent saves could otherwise both read a matching `before` before
    # either wrote -- a guard that stops neither.
    # The token names the attempt; the fingerprint names what it was for.
    fp = store.commits.fingerprint({
        "one_line": body.one_line, "summary": body.summary, "keywords": body.keywords,
        "timeline_events": body.timeline_events, "edits": body.edits})
    with store.locks.campaign_lock(cid):
        # Inside the lock: two saves racing on one token must not both miss.
        prior = store.commits.lookup(cid, body.commit_token)
        progress: dict = {}
        if prior is not None:
            if prior.get("sid") and prior["sid"] != sid:
                # The review panel survives a scene switch, so a retry can carry
                # scene A's token to scene B's route. The ledger is
                # campaign-scoped; without this, B's save would return A's
                # result and write nothing.
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "this save was already committed for a different "
                                      "scene — reopen that scene's review",
                            "kind": "commit_scene_mismatch"})
            if prior.get("fingerprint") and prior["fingerprint"] != fp:
                # The review stayed editable after the failed save, and this
                # retry carries different content. Returning the first result
                # would report success while discarding the edits made since.
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "this review changed after a save that already "
                                      "committed — reload the campaign and edit the "
                                      "records directly",
                            "kind": "commit_body_changed"})
            if prior["done"]:
                return prior["result"]
            if not prior["journalled"]:
                # Reserved before #271, so there is no account of what it did --
                # and it could have appended the timeline and applied any number
                # of edits before it died. Resuming it as fresh work would repeat
                # every one of them, so this keeps the pre-#271 refusal. Only
                # entries written by an older build can land here.
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "an earlier save of this review started and did "
                                      "not finish — reload the campaign to see what "
                                      "landed",
                            "kind": "commit_incomplete"})
            claimed = prior.get("claimed")
            if claimed is not None and store.commits.scene_epoch(cid, sid) > claimed:
                # The epoch this reservation's own claim produced, recorded by
                # `reserve`. Anything past it is a LATER commit for this scene --
                # the re-absorb that the wedge deliberately leaves room for, or
                # the scene's deletion. Resuming now would rewrite the chronicle
                # entry and the scene summary with this older review on top of
                # that one. Being stranded half-applied is the better of the two:
                # the newer save is the current record, this one is history.
                #
                # Read from the entry rather than derived from the token, so a
                # caller-minted key -- which carries no epoch and is a supported
                # thing to send -- is fenced exactly like a server-minted one.
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "a newer save of this scene completed while this "
                                      "one was unfinished — reload the campaign to see "
                                      "what landed",
                            "kind": "commit_incomplete"})
            # Reserved and never completed: some of the four writes landed and
            # nobody knows which. Its journal does, so this attempt RESUMES it
            # (#271) -- every step the journal accounts for is skipped, and a
            # step it marked attempted without confirming is reported rather
            # than repeated.
            progress = prior["progress"]
        else:
            epoch = store.commits.token_epoch(body.commit_token)
            if epoch is not None and epoch != store.commits.scene_epoch(cid, sid):
                # Two reviews of one scene carry different tokens, so the key
                # cannot order them: the second to save would append a second
                # set of timeline events and plot beats for the same scene. The
                # epoch stamped into the token is what the key is missing --
                # this review was prepared before some other save of this scene
                # completed, so it describes a state that no longer exists.
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "another review of this scene was saved after "
                                      "this one was prepared — re-absorb the scene to "
                                      "review it against what is now recorded",
                            "kind": "commit_superseded"})
            # Contradiction check (#111), before the first write and after the
            # replay branches above -- a replay of a save that already landed must
            # return its result, not be told the records it wrote now contradict it.
            # On a FRESH commit only, for the same reason taken one step further: a
            # resume's own earlier writes have already moved the records its
            # remaining edits were staged against, so this pass would refuse the
            # very commit it is trying to finish. Those edits still meet
            # `apply_edits`' per-edit pass, which reports them as conflict failures
            # rather than as a clean refusal.
            #
            # This is the REVIEWER'S check, not the guard: it runs ahead of every
            # write so a 409 leaves the chronicle untouched and this commit token
            # unspent, letting the panel offer keep/replace/merge on a review that is
            # still intact. The guard is `apply_edits`' own pass, which re-judges the
            # batch immediately before it writes -- so a target that moves between
            # here and there is still caught, as a conflict failure rather than a
            # clean refusal.
            #
            # Neither pass makes check-and-write atomic, and the campaign lock does
            # not either: `overlay` (and most of `locks.UNREVIEWED`) mutates without
            # taking it, and the lock is machine-local, so a synced store sees none
            # of it. Closing the remaining window means compare-and-swap in each
            # mutator -- what `sheets.write(expected=...)` already does, and what the
            # other seven would need -- which is the concurrency change `locks.py`
            # says those modules are waiting on, not something to bolt on here.
            drifted = store.absorb.check_conflicts(cid, body.edits)
            if drifted:
                raise HTTPException(
                    status_code=409,
                    detail={"detail": "some proposed changes no longer match what is "
                                      "stored — review them and save again",
                            "kind": "edit_conflicts", "conflicts": drifted})
        record = store.chronicle.absorb(cid, {
            "id": sid, "one_line": body.one_line, "summary": body.summary,
            "keywords": body.keywords, **facts})
        # The one step of the four that appends -- the other three overwrite (the
        # chronicle entry is keyed by scene id, the frontmatter below is a
        # rewrite, and each edit has its own slot in the journal). So it is the
        # one that has to be durably *attempted* before it is attempted: the
        # journal entry rides along on the reservation below, which is claimed
        # BEFORE any non-idempotent write. `progress` carries a resumed
        # attempt's journal forward so this one adds to that account rather than
        # opening a second one.
        timeline = progress.get("timeline")
        if timeline is None:
            progress["timeline"] = "pending"
        store.commits.reserve(cid, body.commit_token, fp, sid, progress)
        started: list[dict] = []
        if timeline is None:
            try:
                store.chronicle.append_timeline(cid, body.timeline_events)
            except Exception as exc:  # noqa: BLE001 — unreadable timeline, full disk, ...
                # An exception means the append did NOT publish: atomic.write_text
                # replaces by rename as its last act. So this is an ordinary
                # reported failure, and the mark comes back off -- "unconfirmed"
                # is for a process that died without returning, not for a call
                # that returned by raising.
                progress.pop("timeline", None)
                started.append({"id": "timeline", "kind": "error",
                                "reason": f"the timeline events could not be "
                                          f"recorded: {exc}"})
            else:
                progress["timeline"] = "done"
            # Settled straight away rather than left to apply_edits' first
            # checkpoint: a failure in mark_absorbed below would otherwise leave
            # an append that demonstrably landed reading as unconfirmed forever.
            store.commits.checkpoint(cid, body.commit_token, progress)
        elif timeline == "pending" and body.timeline_events:
            # An earlier attempt journalled the append and never confirmed it.
            # Repeating it would double the campaign's timeline, so say so
            # instead -- the events are in the review and in the response.
            started.append({"id": "timeline", "kind": "error",
                            "reason": store.absorb.UNCONFIRMED})
        store.scenes.mark_absorbed(cid, sid, body.one_line, body.summary)
        applied, failures = store.absorb.apply_edits(
            cid, body.edits, sid, progress=progress,
            checkpoint=lambda: store.commits.checkpoint(cid, body.commit_token, progress))
        result = {**record, "applied": applied, "failures": started + failures}
        store.commits.record(cid, body.commit_token, result, fp, sid)
    return result


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
    """The context breakdown, as packed. `total_tokens` is what was actually
    sent, measured the way the packer measures it — a section the packer
    dropped still ships here, with its text and `dropped: true`, so the
    inspector can show what was cut without that cut counting toward the total
    it was cut to fit. See `context.context_breakdown` for why the total is not
    the sum of the rows."""
    scene = _require_scene(cid, sid)
    return {"model": scene["meta"].get("model", ""),
            **store.context.context_breakdown(cid, sid)}


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
        # Same pairing as `_persist_reply`: every write to the transcript is
        # followed by reconciling the set, under one lock. An edit of the live
        # reply parks the pre-edit text as a variant — but that variant exists
        # only in the transcript until this runs, so a *second* edit would
        # overwrite the sole copy of the first and drop it from the set.
        with store.locks.campaign_lock(cid):
            store.scenes.edit_message(cid, sid, index, content)
            try:
                store.alternates.reconcile(cid, sid)
            except OSError:
                pass          # the edit is on disk; the sidecar is not a reason to fail it
    except IndexError:
        raise HTTPException(status_code=400, detail="message index out of range")
    except store.scenes.RollMessageImmutable:
        raise HTTPException(status_code=400, detail="a dice roll's transcript line can't be edited")
    return {"ok": True}
