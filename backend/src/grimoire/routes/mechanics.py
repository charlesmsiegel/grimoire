"""Mechanics at play time (#162): dice rolls, the roll-proposal lifecycle and
its narrated continuations, manual checks, the roll log, and the campaign's
bound module and sheets."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .. import prompts, store
from ..llm import LLMClient
from . import runs
from .common import (
    _campaign_root_or_404,
    _record_prompt,
    _require_connection,
    _require_scene,
    computes_only,
    get_llm,
)
from .models import (
    CheckBody,
    ModuleSetting,
    ProposalAction,
    RollBody,
    SheetAdvanceBody,
    SheetBody,
    SheetBulkBody,
    SheetCreationBody,
)
from .streaming import StreamOutcome, _continuation_stream, _sse

router = APIRouter()


@router.post("/campaigns/{cid}/scenes/{sid}/roll")
def post_scene_roll(cid: str, sid: str, body: RollBody, request: Request):
    """Manual dice roll: resolve, log to <campaign>/rolls.json, and write the
    result into the scene transcript as a narrator line."""
    _require_scene(cid, sid)
    try:
        result = store.dice.roll(body.notation)
    except store.dice.DiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Collapse newlines so a hostile label can't fake a blank-line boundary
    # followed by a marker like "**You:**" and get split into a forged
    # message by scenes._markers on the next read.
    label = " ".join((body.label or "").split()) or None
    line = store.dice.format_roll(result, label)
    # One lock across BOTH writes (#234). They each take it anyway, but
    # separately: contention arriving between them returns 409 with the roll
    # already durable and no transcript line, so the retry the 409 invites logs
    # a *second* roll while the first stays invisible forever. Reentrant, so the
    # inner acquisitions are free.
    with store.locks.campaign_lock(cid):
        # A manual roll APPENDS to the transcript, which makes it a shape change
        # like any other: a detached turn composed its prompt without this line
        # and would append its reply after it, so the roll reads as something
        # the narration ignored. Inside the hold, so a send cannot reserve
        # between the check and the append.
        runs.require_scene_free(request.app, cid, sid)
        entry = store.rolls.append(cid, sid, label, result)
        store.scenes.append_message(cid, sid, "assistant", line,
                                    speaker=store.scenes.ROLL_SPEAKER)
    return {"ok": True, "roll": entry, "message": line}


def _continuation_rule_bodies(cid: str, resolution: dict) -> tuple[list[str], list[str]]:
    """Bodies of every `on_roll` rules doc plus the check's linked `rules:`
    docs (the continuation's mechanical grounding)."""
    on_roll_docs: list[str] = []
    check_docs: list[str] = []
    with store.locks.campaign_lock(cid):
        mid = store.modules.resolve(cid)
        if mid is None:
            return on_roll_docs, check_docs
        pack = store.modules.load_pack(mid)
        for doc in pack.get("rules", []):
            if doc.get("on_roll"):
                rule = store.modules.read_rule(mid, doc["id"])
                if rule is not None:
                    on_roll_docs.append(rule["body"].strip())
        cd = pack["checks"] if isinstance(pack["checks"], dict) else {}
        check = cd.get(resolution.get("check"))
        if isinstance(check, dict):
            for rid in (check.get("rules") or []):
                rule = store.modules.read_rule(mid, rid)
                if rule is not None:
                    check_docs.append(rule["body"].strip())
        return on_roll_docs, check_docs


def _continuation_messages(cid: str, sid: str, resolution: dict) -> tuple[list[dict], dict | None]:
    # The roll block is rendered first and named in `appended`: a check can drag
    # in several on-roll rule documents, which is exactly the kind of mandatory
    # bulk that would otherwise be packed around and then appended anyway.
    on_roll_docs, check_docs = _continuation_rule_bodies(cid, resolution)
    block = prompts.render("scene/roll_result.j2", resolution=resolution,
                           on_roll_docs=on_roll_docs, check_docs=check_docs)
    return store.context.compose_turn(
        cid, sid, appended=(("Roll result", "system", block),),
        describe=store.prompt_log.capturing())


def _declined_continuation_messages(cid: str, sid: str) -> tuple[list[dict], dict | None]:
    block = prompts.render("scene/roll_declined.j2")
    return store.context.compose_turn(
        cid, sid, appended=(("Roll declined", "system", block),),
        describe=store.prompt_log.capturing())


@router.get("/campaigns/{cid}/scenes/{sid}/roll-proposal")
def get_roll_proposal(cid: str, sid: str):
    """Recovery endpoint: the scene's current proposal record (or null)."""
    _require_scene(cid, sid)
    return {"record": store.proposals.get(cid, sid)}


@router.post("/campaigns/{cid}/scenes/{sid}/roll-proposal")
def post_roll_proposal(cid: str, sid: str, body: ProposalAction, request: Request,
                       client: LLMClient = Depends(get_llm),
                       x_grimoire_attempt: str | None = Header(default=None)):
    """Adjudicate a roll proposal (accept / decline). Idempotent by proposal
    id, keyed to the scene's current record. Every state change is a CAS; a
    lost transition means someone else moved the record (a new send
    superseded it, another accept won the claim) — we stop dead: no
    projection, no continuation, 409."""
    replay = runs.replay_attempt(request.app, cid, sid, x_grimoire_attempt)
    if replay is not None:
        return replay
    _require_scene(cid, sid)
    conn = _require_connection("continuation", cid)
    # RESERVED BEFORE THE FIRST CAS. Every transition below writes the proposal
    # record, so a 409 raised after one would report that nothing happened over
    # a record that has already moved. The exits that answer without generating
    # -- an already-narrated proposal, a lost race -- return through the guard
    # too, which ends their reservation rather than stranding it.
    # Defaulted to the PROPOSAL, not to a fresh id. This route is documented
    # idempotent by proposal id, and one-run-per-scene would otherwise turn
    # that into a refusal: two clients accepting the same proposal at once --
    # a double tap, a retry over a slow link -- would race, one would reserve
    # and the other would be told a turn was already running. Sharing the
    # attempt makes the second ADOPT the first's run and tail its frames, so
    # both get the same answer, the narration persists once, and the provider
    # is called once instead of twice.
    run, fresh = runs.reserve_turn(
        request.app, cid, sid, "continuation",
        x_grimoire_attempt or f"proposal:{body.proposal}",
        # A client's own id is a promise that this is the same logical request,
        # so its outcome is replayed however it ended. The proposal-derived one
        # is only a dedupe key for concurrent duplicates -- nobody promised
        # anything -- so a later retry of an adjudication that FAILED has to be
        # allowed to actually retry, rather than being handed the failure for
        # the rest of the retention window.
        adopt_terminal=x_grimoire_attempt is not None)
    if not fresh:
        return runs.tail_response(run, 0, lead=runs.lead_frame(run))
    with runs.reservation(request.app, run):
        return _roll_proposal_run(cid, sid, body, request, client, conn, run)


def _roll_proposal_run(cid: str, sid: str, body: ProposalAction, request: Request,
                       client: LLMClient, conn: dict, run):
    """The body of an adjudication, once the scene is reserved -- see
    `scenes._chat_run` for why every exit from it is wrapped."""
    pid = body.proposal
    rec = store.proposals.get(cid, sid)
    if rec is None or rec.get("id") != pid:
        raise HTTPException(status_code=409, detail="proposal is stale")
    if rec.get("status") == "superseded":
        # A same-id superseded record that already resolved still owes its roll
        # + 🎲 line to the transcript (the roll stands as history per spec). If
        # a crash landed between the roll append and the line write, no other
        # path heals it, so a stale client's retry becomes the recovery path:
        # project idempotently (pure file I/O), then still 409 — no
        # continuation is ever offered for a superseded record. A record
        # superseded while still pending/resolving has no resolution to
        # project; it stays a plain 409.
        if isinstance(rec.get("resolution"), dict):
            store.proposals.project(cid, sid, pid)
        raise HTTPException(status_code=409, detail="proposal is stale")
    status = rec["status"]

    if status == "narrated":
        return runs.answer_without_running(request.app, run, [_sse({"done": True})])
    if status == "resolving":
        raise HTTPException(status_code=409, detail="adjudication in progress")

    if status == "pending":
        if body.action == "decline":
            if not store.proposals.transition(cid, sid, pid, ("pending",), "declined"):
                raise HTTPException(status_code=409, detail="proposal is stale")
        else:  # accept
            if not store.proposals.claim(cid, sid, pid):
                raise HTTPException(status_code=409, detail="adjudication in progress")
            p = rec["payload"]
            try:
                resolution = store.checks.resolve_check(
                    cid, body.check or p.get("check"), body.actor or p.get("actor"),
                    body.difficulty if body.difficulty is not None else p.get("difficulty"),
                    body.modifier if body.modifier is not None else (p.get("modifier") or 0))
            except store.locks.StoreBusy:
                # Contention is not a check failure and must not be dressed up
                # as one (#234). Revert exactly as the broad path does, then
                # let the 409 handler answer. The revert can itself contend; if
                # it does the record stays "resolving", which needs no new
                # machinery -- that is in proposals.NON_TERMINAL, so the next
                # send's supersede() retires it, and until then this route
                # answers 409 "adjudication in progress", which is accurate.
                try:
                    store.proposals.transition(cid, sid, pid, ("resolving",), "pending")
                except store.locks.StoreBusy:
                    pass
                raise
            except Exception as exc:  # noqa: BLE001 — any failure reverts cleanly
                store.proposals.transition(cid, sid, pid, ("resolving",), "pending")
                detail = (str(exc) if isinstance(exc, store.checks.CheckError)
                          else "the check could not be resolved")
                # `failed`, not the default `landed`: nothing was generated
                # and nothing was persisted. Recorded `landed`, a poll would
                # read success while the stream carried an error, and the
                # phone would announce a reply that does not exist.
                return runs.answer_without_running(request.app, run, [
                    _sse({"error": {"detail": detail, "kind": "check_error"}})],
                    state="failed")
            if not store.proposals.transition(cid, sid, pid, ("resolving",), "resolved", resolution):
                # superseded mid-resolve: the pure roll result is discarded unlogged
                raise HTTPException(status_code=409, detail="proposal was superseded")
        status = store.proposals.get(cid, sid)["status"]

    if status == "resolved":
        resolution = store.proposals.project(cid, sid, pid)
        if resolution is None:
            # Another actor won the scene's record in the window between our
            # pre-stream status read and the projection lock (a supersede +
            # brand-new fence/send). Nothing was projected — stop dead, same
            # as any other lost-race case, with a clean done frame.
            return runs.answer_without_running(request.app, run, [_sse({"done": True})])
        messages, breakdown = _continuation_messages(cid, sid, resolution)
    elif status == "declined":
        messages, breakdown = _declined_continuation_messages(cid, sid)
    else:  # defensive: a race moved the record out from under us
        raise HTTPException(status_code=409, detail="proposal is stale")
    _record_prompt(cid, sid, "continuation", breakdown)
    outcome = StreamOutcome()
    # DETACHED like every other scene turn. The plan singles this producer out:
    # it is `_continuation_stream`, in a different module, so a migration that
    # did the chat path and stopped would pass every other detach test while
    # locking the phone during an accepted roll still cancelled it and dropped
    # the narration -- and a roll is exactly when a player looks away.
    stream = _continuation_stream(cid, sid, pid, messages, conn, client,
                                  identity=run.scene_identity, outcome=outcome)
    runs.start_detached(request.app, run, lambda: stream.body_iterator,
                        outcome=outcome.result)
    return runs.tail_response(run, 0, lead=runs.lead_frame(run))


@router.get("/campaigns/{cid}/scenes/{sid}/checks")
def get_scene_checks(cid: str, sid: str):
    _require_scene(cid, sid)
    return {"actors": store.checks.available_checks(cid, sid)}


@router.post("/campaigns/{cid}/scenes/{sid}/check")
def post_scene_check(cid: str, sid: str, body: CheckBody, request: Request):
    """Manual check: run the pure resolver, log the roll (no proposal tag), and
    append the 🎲 line — the same resolution path an accepted proposal takes."""
    _require_scene(cid, sid)
    try:
        resolution = store.checks.resolve_check(
            cid, body.check, body.actor, body.difficulty, body.modifier or 0)
    except store.checks.CheckError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # One lock across both writes, for the same reason as post_scene_roll above
    # (#234): a 409 landing between them strands a durable roll outside the
    # transcript and the retry logs a duplicate.
    with store.locks.campaign_lock(cid):
        # Appends the 🎲 line, so the same refusal as the manual roll above.
        runs.require_scene_free(request.app, cid, sid)
        entry = store.rolls.append(cid, sid, store.checks.roll_label(resolution),
                                   resolution["result"],
                                   tier=resolution.get("tier"))
        resolution = {**resolution, "roll_id": entry["id"]}
        line = store.checks.format_check_roll(resolution)
        store.scenes.append_message(cid, sid, "assistant", line,
                                    speaker=store.scenes.ROLL_SPEAKER)
    return {"ok": True, "resolution": resolution, "roll": entry, "message": line}


@router.get("/campaigns/{cid}/rolls")
def get_rolls(cid: str):
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")
    return list(reversed(store.rolls.read(cid)))


@router.post("/campaigns/{cid}/rolls/{rid}/replay")
@computes_only
def post_roll_replay(cid: str, rid: str):
    try:
        return {"ok": True, **store.rolls.replay(cid, rid)}
    except (store.rolls.RollNotFound, store.campaigns.CampaignNotFound):
        # rolls.json is under campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="roll not found")


@router.get("/campaigns/{cid}/module")
def get_campaign_module(cid: str):
    try:
        meta = store.campaigns.read_campaign(cid)["meta"]
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    setting = (meta.get("module") or "").strip()
    resolved = store.modules.resolve(cid)
    source = None
    if resolved is not None:
        source = "campaign" if setting and setting != "none" else "world"
    return {"setting": setting, "resolved": resolved, "source": source}


@router.put("/campaigns/{cid}/module")
def put_campaign_module(cid: str, body: ModuleSetting):
    try:
        with store.locks.campaign_lock(cid):
            store.modules.set_campaign_module(cid, body.module.strip())
            store.audit.clear_baselines(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    return {"ok": True}


@router.get("/campaigns/{cid}/sheets")
def get_campaign_sheets(cid: str):
    _campaign_root_or_404(cid)
    return {"coverage": store.sheets.coverage(cid),
            "refs": store.sheets.list_refs(cid)}


# Four segments, so it can neither shadow nor be shadowed by the five-segment
# `/sheets/{kind}/{eid}` routes below. What it does CROSS is the generic entity
# read `/campaigns/{cid}/{kind}/{eid}`, which `routes.__init__` includes last so
# this one wins; `tests/test_route_order.py` pins that decision.
@router.get("/campaigns/{cid}/sheets/roster")
def get_campaign_sheet_roster(cid: str):
    """The cast, one row per member, saying who has a sheet and who does not.

    Separate from `GET /sheets` rather than folded into it: the mechanics panel
    reads that endpoint every time it opens and only ever wants the tallies, and
    a campaign whose world carries a few hundred characters should not pay for
    the whole roster to render `12/40`."""
    _campaign_root_or_404(cid)
    return {"roster": store.sheets.roster(cid)}


@router.post("/campaigns/{cid}/sheets/create-missing")
def post_campaign_sheets_create_missing(cid: str, body: SheetBulkBody):
    """Create a default sheet for every cast member lacking one, in one request
    -- the alternative being N round-trips driven by the client, which is N
    chances to stop half-way and no single answer about what happened."""
    _campaign_root_or_404(cid)
    try:
        return store.sheets.create_missing(cid, body.types)
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/campaigns/{cid}/sheets/{kind}/{eid}")
def get_campaign_sheet(cid: str, kind: str, eid: str):
    _campaign_root_or_404(cid)
    return {"sheet": store.sheets.read(cid, kind, eid)}


@router.put("/campaigns/{cid}/sheets/{kind}/{eid}")
def put_campaign_sheet(cid: str, kind: str, eid: str, body: SheetBody):
    _campaign_root_or_404(cid)
    try:
        store.sheets.write(cid, kind, eid, body.sheet_type, body.fields, expected=body.expected)
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/campaigns/{cid}/sheets/{kind}/{eid}")
def delete_campaign_sheet(cid: str, kind: str, eid: str, gen: str | None = None):
    _campaign_root_or_404(cid)
    try:
        return {"ok": store.sheets.delete(cid, kind, eid, expected_gen=gen)}
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/campaigns/{cid}/sheets/{kind}/{eid}/creation")
def put_campaign_sheet_creation(cid: str, kind: str, eid: str, body: SheetCreationBody):
    _campaign_root_or_404(cid)
    try:
        store.sheets.write_creation(cid, kind, eid, body.sheet_type, body.spends,
                                    expected=body.expected)
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound, store.entities.EntityNotFound):
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"sheet": store.sheets.read(cid, kind, eid)}


@router.post("/campaigns/{cid}/sheets/{kind}/{eid}/advance")
def post_sheet_advance(cid: str, kind: str, eid: str, body: SheetAdvanceBody):
    _campaign_root_or_404(cid)
    try:
        return {"sheet": store.sheets.advance(cid, kind, eid, body.field)}
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound, store.entities.EntityNotFound):
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
