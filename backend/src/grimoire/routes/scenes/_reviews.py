"""Detached review preparation fans out independent phases over one captured snapshot."""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request

from ... import inference as operations
from ... import store
from ...llm import LLMClient
from ...llm_errors import LLMError
from ...store.absorb import membership as absorb_membership
from .. import runs
from ..common import (
    UsableInference,
    _campaign_root_or_404,
    _llm_http_error,
    _noting,
    _require_scene,
    _soft_inference,
    _soft_resolved,
    computes_only,
    get_llm,
    require_inference,
)
from . import _review_budget, _review_identity, _review_phases, _review_snapshot, _review_state
from ._router import router as router


@router.post("/campaigns/{cid}/scenes/{sid}/absorb", status_code=202)
@computes_only  # every edit here is staged; PUT /chronicle is what persists them
def post_absorb(cid: str, sid: str, request: Request, force: bool = False,
                client: LLMClient = Depends(get_llm)):
    """Start the end-of-scene review. Answers 202 and a run to poll.

    Not a stream, so there is no `tail_response` here: End Scene is a form the
    reviewer reads, edits and saves, and the client wants "started, here is a
    run id" followed by polling. The result lands in `store.pending_reviews`,
    which is what survives the phone locking mid-absorb -- the failure this
    route exists to fix, and by user impact the worst one in the app, because
    an absorb is the longest single generation in it and putting the phone down
    while it runs is exactly what people do.

    **Reserved before the snapshot**, which is the ordering the whole design
    turns on -- see `runs.reserve_review`. The pre-flight refusals below then
    release the reservation through `runs.reservation`, so a scene refused for
    having nothing to absorb is not left holding its own exclusion key.
    """
    _campaign_root_or_404(cid)
    _require_scene(cid, sid)
    resolved = require_inference("absorb", cid)
    # Before the reservation and before a token of the budget: a scene whose id
    # predates the cap can be one the review sidecar's longer name will not fit
    # beside, and finding that out in `publish` costs the whole generation and
    # then costs it again on every retry. Refused with what to do about it.
    if not store.pending_reviews.name_usable(cid, sid):
        raise HTTPException(
            status_code=409,
            detail={"kind": "scene_id_too_long",
                    "detail": "this scene's id is too long for its review file — "
                              "rename the scene to something shorter, then end it"})
    generation = uuid.uuid4().hex
    # One hold: a closed branch is refused, the review waits for every member
    # of the group (absorbing this one closes them), and nothing reserves a
    # turn in a sibling between those checks and this reservation.
    with store.locks.campaign_lock(cid):
        runs.require_scene_open(cid, sid)
        runs.require_group_free(request.app, cid, sid)
        run = runs.reserve_review(request.app, cid, sid, "absorb", generation)
    with runs.reservation(request.app, run):
        return _absorb_start(cid, sid, force, request, client, resolved, run, generation)


def _absorb_start(cid: str, sid: str, force: bool, request: Request,
                  client: LLMClient, resolved: UsableInference, run,
                  generation: str) -> dict:
    """Everything an absorb decides synchronously, then the handoff.

    Split out so `runs.reservation` can wrap every exit: the run is
    discoverable from the moment it is reserved, and a path that returns or
    raises in here without handing a producer to the runner would leave it
    `running` for the life of the process -- refusing every later turn and
    review on the scene.
    """
    with absorb_membership.held(cid):
        epoch, scene, tracked = _review_snapshot._absorb_snapshot(cid, sid, run.scene_identity)
        # Counted in context: an all-hidden scene has nothing a review could read,
        # and a director note left beside it is dropped from the transcript too.
        if not store.scenes.in_context(scene["messages"]):
            raise HTTPException(status_code=400, detail="nothing to absorb")
        # Absorb is not idempotent: lore edits append and plot movements add a beat,
        # so a second pass over the same scene duplicates both. Refuse by default
        # (before spending a token) and make the re-run an explicit choice (#235).
        if _review_snapshot._already_absorbed(scene) and not force:
            raise HTTPException(
                status_code=409,
                detail={"detail": "this scene has already been absorbed", "kind": "already_absorbed"})
        # The same snapshot the review is built from, digested. Validated again
        # when the review is retrieved and again at save: once this run lands its
        # exclusion slot is released and the composer re-enables, so the player can
        # keep playing while the review sits on disk -- and none of that advances
        # the commit epoch (`commits.reserve` is called from exactly one place),
        # so the token alone would let a summary of a transcript that has moved
        # save with every check returning green.
        facts = store.chronicle.scene_facts(cid, sid)
        player_label = store.appearances.player_label(cid, sid)
        # The prompt view, computed once: the transcript the model is shown and the
        # one its citations are judged against are the same text. The watermark
        # below stays on the raw scene -- it is about what is stored.
        shown = store.regex.view.view(scene["messages"], cid=cid, phase="prompt")
        transcript = store.chronicle.transcript_text(shown, player_label)
        groups = absorb_membership.capture(cid)
        prepared = _review_snapshot._Prepared(
            epoch=epoch, scene=scene, tracked=tracked, facts=facts, transcript=transcript,
            player_label=player_label, shown=shown, groups=groups,
            messages=store.absorb.build_prompt(
                transcript, facts,
                state_snapshot=store.absorb.state_snapshot(cid, sid),
                rel_snapshot=store.absorb.relationships_snapshot(cid, sid),
                plot_snapshot=store.absorb.plot_snapshot(cid),
                group_snapshot=store.absorb.group_snapshot(cid, facts.get("cast", ()), snapshot=groups.snapshot),
                commitment_snapshot=store.absorb.commitment_snapshot(cid),
                fact_snapshot=store.absorb.fact_snapshot(cid, sid),
                steering_snapshot=store.absorb.steering_snapshot(cid, sid),
                tracked_snapshot=tracked),
            watermark=store.pending_reviews.watermark(scene["messages"]))

    async def work():
        return await _absorb_work(cid, sid, client, resolved, run, generation, prepared)

    runs.start_computing(request.app, run, work)
    return _review_state._accepted(run, generation)


async def _absorb_work(cid: str, sid: str, client: LLMClient, resolved: UsableInference,
                       run, generation: str, prepared: _review_snapshot._Prepared) -> dict:
    """The four phases, and the durable write that is this run's whole value."""
    abandoned = _review_state._review_abandoned(run)
    budget = _review_budget._Budget(store.config.absorb_budget())
    try:
        # All four phases AT ONCE. Nothing here ever needed the one before it:
        # `_run_audit` re-reads the scene and transcript itself and never
        # touches `parsed`, and both per-NPC phases take only `transcript`,
        # captured from the snapshot above -- so what read as a pipeline was
        # only ever a fan-out written as a chain. The one real dependency --
        # the identity check reads the extraction's rows -- is chained inside
        # the first entry (`_extract_and_identify`) rather than added as a
        # step after the fan-out.
        #
        # The extraction is first in the list so it claims the first semaphore
        # slot: it is the one phase whose failure is fatal, so it must never be
        # the one left queued.
        #
        # Each phase resolves its OWN connection (#142): they are five
        # different routes -- extraction, the identity check, the per-NPC
        # dossier loop, voice drift and the mechanics audit -- and sharing one
        # resolution would make four of those five settings do nothing whenever an
        # absorb was what ran them.
        #
        # All four secondary ones resolved HERE, before the meter opens and before a single
        # coroutine is built. Resolved inline in the `_gather_phases(...)`
        # argument list instead, a failure from the third would leave the first
        # two coroutines created and never awaited.
        #
        # And resolved softly (`_soft_inference`, `_soft_resolved`), which
        # report instead of raising: these four promise never to fail an
        # absorb, so a route pointing one of them at a keyless connection has
        # to come back as that phase's status rather than as a 409 that
        # discards the extraction's result too. Voice drift and the identity
        # check keep the whole resolution, since `decide()` takes one (spec
        # 7.4).
        dossier_resolved, dossier_why = _soft_inference(
            lambda: require_inference("dossier", cid))
        voice_resolved, voice_why, _voice_kind = _soft_resolved(
            lambda: require_inference("voice-drift", cid, operation="decide"))
        audit_resolved, audit_why = _soft_inference(
            lambda: require_inference("audit", cid))
        ident_resolved, ident_why, _ident_kind = _soft_resolved(
            lambda: require_inference("continuity-identity", cid, operation="decide"))
        with store.usage.meter("absorb", campaign=cid, scene=sid) as m:
            # ONE race, around the whole fan-out, rather than a predicate
            # threaded into each phase. Two reasons, and the second is the one
            # that decided it:
            #
            # * it covers the extraction, which has no per-NPC loop to check in
            #   and is the phase most likely to be the one wedged;
            # * it changes nothing about how the phases are scheduled.
            #   `_watched` puts its subject on the loop as a task of its own,
            #   and wrapping each phase individually inserts a hop before its
            #   first statement -- long enough, with a tight budget, for a
            #   sibling to spend the clock the extraction was about to read.
            #
            # `_gather_phases` already cancels and detaches its children when
            # the task holding it is cancelled, which is exactly what
            # `_watched` does to it here.
            results = await _review_budget._watched(_review_budget._gather_phases(
                _review_identity._extract_and_identify(
                    budget.run(operations.generate("absorb", prepared.messages,
                                                   client=client, resolved=resolved,
                                                   usage=m.usage, stream=False,
                                                   schema=store.absorb.prompt.extraction_schema()),
                               on_timeout=_noting(client, resolved, m.usage)),
                    cid, sid, client, prepared, budget, ident_resolved, ident_why),
                _review_phases._stage_dossiers(cid, sid, prepared.transcript, client, dossier_resolved,
                                budget, unroutable=dossier_why),
                _review_phases._stage_voice_drift(cid, sid, prepared.transcript, client, voice_resolved,
                                   budget, unroutable=voice_why, abandoned=abandoned),
                _review_phases._run_audit(cid, sid, client, audit_resolved, budget, unroutable=audit_why),
                limit=store.config.absorb_concurrency()), abandoned)
        extraction, dossier_result, voice_result, audit_result = results
        if isinstance(extraction, BaseException):
            # Only the extraction is fatal, and a budget overrun on it is
            # included: nothing has been produced yet, so there is nothing to
            # degrade to. The other three never raise for absorb except to
            # report abandonment (each has its own failure boundary), so
            # anything else from one of them is a bug in that boundary rather
            # than a state to report -- `_phase_or_raise` says so.
            raise extraction
        parsed, identity_block = extraction

        def identity_staging_failed(exc: BaseException) -> None:
            # A defect in an identity-only staging pass reports on the
            # identity row; the rows stage without alternatives.
            identity_block["status"] = "failed"
            identity_block["reason"] = _review_identity._IDENTITY_STAGING_FAILED

        # The transcript the citations (#112) are judged against comes from the
        # SAME snapshot the model was shown: a reroll or an append landing while
        # the call was in flight would otherwise have them judged against text
        # it never saw.
        edits = store.absorb.materialize(cid, sid, parsed, prepared.shown,
                                         player_label=prepared.player_label,
                                         on_identity_error=identity_staging_failed,
                                         membership_capture=prepared.groups)
        # Unpacked in the order the phases were listed, not the order they
        # finished, so `edits` reads the same way every time.
        dossier_edits, dossiers = _review_budget._phase_or_raise(dossier_result)
        voice_edits, voice = _review_budget._phase_or_raise(voice_result)
        audit_edits, mechanics = _review_budget._phase_or_raise(audit_result)
    except _review_budget.Abandoned:
        # The reviewer cancelled while this was generating. The record is
        # already gone (the DELETE removed it under the lock that flagged this
        # run), so there is nothing to write and nothing to report but the
        # state.
        return {"state": "cancelled", "error": _review_state._cancelled_error()}
    except LLMError as exc:
        return {"state": "failed", "error": _review_state._run_error(_llm_http_error(exc))}
    edits += dossier_edits
    edits += voice_edits
    staged = edits + audit_edits
    review = {
        "one_line": parsed["one_line"], "summary": parsed["summary"],
        "keywords": parsed["keywords"], "timeline_events": parsed["timeline_events"],
        **prepared.facts, "edits": staged, "mechanics": mechanics,
        # Which staged rows a LATER scene has already answered differently
        # (#78). Empty for the ordinary case -- absorbing the newest scene,
        # which has no later scene to disagree with -- so the pass is
        # unconditional rather than a mode the caller has to know to ask
        # for, and it lights up exactly where it matters: a re-extraction
        # after a retcon of an old scene.
        "contradictions": _review_snapshot._contradictions(cid, sid, staged),
        "dossiers": dossiers, "voice": voice, "identity": identity_block,
        # One uniform row per step so a short absorb is legible as one
        # (see _phase_report) rather than as a model with nothing to say.
        "phases": _review_budget._phase_report(dossiers, voice, mechanics, identity_block),
        # Idempotency key for the save this review will become (#235): the
        # commit appends in six places, so a replay whose first response was
        # lost must return that result rather than apply it again. It also
        # carries the scene's commit epoch as captured at the TOP of this
        # run -- what tells a save that some OTHER review of the scene
        # committed while this one was being prepared or sat open (#271).
        "commit_token": store.commits.mint(prepared.epoch)}
    return await _review_state._persist_review(
        cid, sid, run, generation,
        lambda: store.pending_reviews.publish(cid, sid, generation, review, prepared.watermark))


@router.post("/campaigns/{cid}/scenes/{sid}/audit", status_code=202)
@computes_only  # a retry of absorb's audit step alone: same staged edits, same nothing written
def post_audit(cid: str, sid: str, request: Request,
               client: LLMClient = Depends(get_llm)):
    """Standalone audit retry: re-runs ONLY the audit step (never the prose
    absorb), returning fresh `expect` values on any resulting sheet edits.

    Detached like its absorb (#396), and merged into the stored review rather
    than returned whole: this answers `{mechanics, edits}`, a *part* of a
    review, and writing that part over the record would destroy the absorb's
    prose, its staged edits and its commit token -- the token being the piece
    nothing else can reconstruct.
    """
    _require_scene(cid, sid)
    resolved = require_inference("audit", cid)
    if store.modules.resolve(cid) is None:
        raise HTTPException(status_code=400, detail="no module resolved")

    def work_for(run, generation):
        async def work():
            # A retry gets its own budget — it never inherits the deadline of
            # whatever absorb ran out of time earlier.
            try:
                edits, mechanics = await _review_phases._run_audit(
                    cid, sid, client, resolved, _review_budget._Budget(store.config.absorb_budget()),
                    abandoned=_review_state._review_abandoned(run))
            except _review_budget.Abandoned:
                return {"state": "cancelled", "error": _review_state._cancelled_error()}
            result = {"mechanics": mechanics, "edits": edits}
            return await _review_state._persist_review(
                cid, sid, run, generation,
                lambda: store.pending_reviews.merge(
                    cid, sid, generation,
                    lambda review: store.pending_reviews.merge_audit(review, result)),
                result=result)
        return work

    return _review_state._retry_start(cid, sid, request, "audit", work_for)


@router.post("/campaigns/{cid}/scenes/{sid}/dossiers", status_code=202)
@computes_only  # a retry of absorb's dossier step alone: same staged edits, same nothing written
def post_dossiers(cid: str, sid: str, request: Request,
                  client: LLMClient = Depends(get_llm)):
    """Standalone dossier retry: re-runs ONLY the dossier phase (never the prose
    absorb), returning the same `dossiers` block and staged edits an absorb
    carries (#286).

    The audit has had this since #235 and the dossier phase has not, so a budget
    that ran out mid-flight left "End scene again" as the only way to get those
    dossiers -- and that re-runs every phase and replaces the review wholesale,
    discarding whatever the reviewer had already approved or typed.

    Every present NPC is re-run, not just the ones `skipped`/`failed` last time.
    That is deliberate: a per-NPC retry list would have to merge two dossier
    blocks in the client, and neither the status nor the reason is a merge of
    its parts. The re-run is also not the wasted work it looks like -- what
    starved the phase was sharing one budget with the extraction, voice and
    audit steps, and this hands the whole budget to dossiers alone, so the run
    that was cut short at NPC 6 of 8 typically now reaches all 8. A phase that
    cannot finish even on its own full budget genuinely needs a bigger one.

    Staged, never written (#235), for the same reason absorb stages: these land
    with the rest of the batch in PUT /chronicle, so a reviewer who hits Cancel
    leaves nothing behind.

    Detached like its absorb (#396), and folded into the stored review rather
    than replacing it: only the NPCs this run actually re-proposed are swapped,
    so a retry that failed for one of them cannot delete their perfectly good
    proposal from the first pass and put nothing in its place.
    """
    scene = _require_scene(cid, sid)
    resolved = require_inference("dossier", cid)
    if not store.scenes.in_context(scene["messages"]):
        # A dossier is a paragraph the model rewrites FROM the transcript, so an
        # empty one can only produce invention. The audit needs no equivalent
        # guard: with nothing to audit it simply finds nothing, where this would
        # stage a proposal to overwrite a real dossier with fiction. Hidden
        # posts and director notes do not count: the transcript below drops them.
        raise HTTPException(status_code=400, detail="nothing to build dossiers from")
    shown = store.regex.view.view(scene["messages"], cid=cid, phase="prompt")
    transcript = store.chronicle.transcript_text(
        shown, store.appearances.player_label(cid, sid))

    def work_for(run, generation):
        async def work():
            # A retry gets its own budget — it never inherits the deadline of
            # whatever absorb ran out of time earlier (post_audit's reason).
            try:
                edits, dossiers = await _review_phases._stage_dossiers(
                    cid, sid, transcript, client, resolved,
                    _review_budget._Budget(store.config.absorb_budget()),
                    abandoned=_review_state._review_abandoned(run))
            except _review_budget.Abandoned:
                return {"state": "cancelled", "error": _review_state._cancelled_error()}
            result = {"dossiers": dossiers, "edits": edits}
            return await _review_state._persist_review(
                cid, sid, run, generation,
                lambda: store.pending_reviews.merge(
                    cid, sid, generation,
                    lambda review: store.pending_reviews.merge_dossiers(review, result)),
                result=result)
        return work

    return _review_state._retry_start(cid, sid, request, "dossiers", work_for)
