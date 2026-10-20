"""common: prompts responsibilities."""

from __future__ import annotations

from collections.abc import Callable

from ... import llm, llm_sampling, model_guidance, store, wire
from ...store.inference import limits as inference_limits


def _sampling_report(target: wire.Target | None) -> dict | None:
    """`llm_sampling.report(target)`, plus what the CALL added to the attempt
    (01f 3.7, `inference.call_chain`): `structured` when it was sent its
    provider's structured mode, and `call_cap` when the call capped its
    output (the capped `max_tokens` is already in `applied`). Neither key is
    there otherwise, so a capture of a plain call is what it always was."""
    report = llm_sampling.report(target)
    if report is None or target is None:
        return report
    if target.structured:
        report = {**report, "structured": True}
    if target.sampling.call_cap is not None:
        report = {**report, "call_cap": target.sampling.call_cap}
    return report


def model_window(target: wire.Target | None) -> dict:
    """The window of the model a breakdown names (01i), `{value, source}`:
    the target's own resolved limit (`wire.Target.limits`), unknown when
    nothing resolved. What every breakdown that names a `model` carries, so
    the inspector measures it against the window the backend knows rather
    than looking the model up in a catalog it may not have."""
    return inference_limits.limit_body(
        target.limits.window if target is not None else wire.UNKNOWN_LIMIT)


def _record_prompt(cid: str, sid: str, task: str, breakdown: dict | None,
                   *, model: str | None = None, kind: str = "",
                   messages: list[dict] | None = None,
                   conn: wire.Chain | wire.Target | None = None,
                   operation: str = "",
                   still: Callable[[], bool] | None = None) -> None:
    """Freeze what this turn's model is about to see (#157).

    Called with the breakdown from the SAME `context.compose_*` call that
    produced the messages being sent — see `store.prompt_log`. The requested
    model rides along so a snapshot still names that attempt after the standing
    connection changes.

    Scene callers pass the resolved requested model, including per-call
    overrides. None retains the legacy scene-stamp default for other callers;
    an empty string means the endpoint's unnamed default and stays empty.

    `kind` is the connection kind the model is reached through, which is what
    lets the snapshot say whether its counts are the model's own
    (`tokens.counting`). A fallback attempt is recorded without one -- the
    variant callback is handed a model id only -- so its counts read as
    estimates, which is the safe direction.

    `conn` is what this call is sent on -- a generation's chain, or one
    attempt's target -- and what the snapshot records about its sampler
    preset is its first attempt's (`llm_sampling.report`, the same split the
    facade sends), so a past turn says what it was sent WITH, and what its
    backend could not take. Its window rides along too (`model_window`, the
    same target's), so a past turn is measured against the window it was sent
    into.

    `messages` binds an optional best-effort capture for a distinct fallback
    attempt. The prepared prompt owns frozen variants; this callback only files
    their existing breakdown, never recomposes campaign context. It is bound
    here, after the claim, so an unclaimed turn cannot record a fallback either.

    Called once the turn is committed to happening — after the stream object
    exists, so the pre-stream claim has already succeeded — but NOT from inside
    the stream itself. The finalizers carry delicate turn-ownership and abort
    semantics that a debug write has no business joining, and a turn the
    provider *failed* is one of the turns whose prompt is most worth having.
    `prompt_log.record` swallows its own storage failures and never waits on a
    lock, so this cannot cost the turn either way.

    `operation` is handed to `prompt_log.record` (a decision capture's
    `prompt_log.DECIDE`, which files the entry in a retention pool of its
    own). `still` is a decision capture's fence (`routes.decision_capture`):
    asked INSIDE the hold, after the scene read and before the write, and a
    False answer skips the write -- and with it the revision bump, since
    nothing was written. A `still` that raises leaves this function: the
    one caller that passes it turns that into its own warning.
    """
    # None means the caller composed with `describe=False` because capture is
    # off. Nothing to record, and nothing was built to record.
    if breakdown is None:
        return
    chain = conn if isinstance(conn, wire.Chain) else None
    sent = conn.primary if isinstance(conn, wire.Chain) else conn
    if sent is not None and "model_window" not in breakdown:
        # A caller that names another attempt's model (a decision capture's
        # answering call) has stamped that attempt's window already.
        breakdown = {**breakdown, "model_window": model_window(sent)}
    report = _sampling_report(sent)
    if report is not None:
        breakdown = {**breakdown, "sampling": report}
    if isinstance(messages, model_guidance.PreparedMessages):
        def on_variant(selected: str, variant: dict | None) -> None:
            # The fallback as the facade sends it: the one this call carries
            # (`wire.Chain.fallback`), with the route's preset carried onto it
            # under the same rule.
            fallback = chain.fallback if chain is not None else None
            _record_prompt(cid, sid, task, variant, model=selected,
                           conn=llm.fallback_sampling(chain.primary, fallback)
                           if chain is not None and fallback is not None else None)
        messages.on_variant = on_variant
    # The scene check and the append are ONE critical section, on the same lock
    # `record` uses. Another client can rename or delete the scene between the
    # composition and this call, and its cleanup (`repoint_scenes` /
    # `forget_scene`) will already have run -- so a row appended afterwards under
    # the obsolete id is one nothing will ever repoint or remove, waiting for the
    # id to be recycled and shown as the replacement scene's own prompt. Checking
    # outside the lock would only narrow that window; checking inside closes it.
    #
    # Non-blocking, and skipping on contention, for the reason `record` is: this
    # runs on the generating path (see `store.prompt_log.record`). The lock is
    # reentrant, so `record`'s own acquisition inside this one is free.
    #
    # The `with` is INSIDE the try, not around it: acquiring takes a file lock,
    # so entering the context manager can raise OSError on its own (the
    # machine-local lock directory gone or unwritable) -- outside any guard,
    # that aborted the route over a debug side effect. Most visible on the
    # opener, where no stream is constructed to fail into.
    try:
        with store.locks.campaign_lock_nowait(cid) as got:
            if not got:
                return
            # Frontmatter only. `read_scene` would re-parse the whole transcript
            # for one field, on a path the turn is already about to pay for
            # several times over.
            # Read even when `model` was supplied: the frontmatter read is what
            # proves the scene is still here, which is the check this whole
            # critical section exists for.
            meta = store.scenes.read_scene_meta(cid, sid)
            # The same scene, not merely a scene under this id: a decision
            # capture's fence, checked in the hold that covers the write.
            if still is not None and not still():
                return
            # `is None`, not `or`: see the docstring. "" is a real answer.
            try:
                store.prompt_log.record(
                    cid, sid, task, breakdown,
                    model=meta.get("model", "") if model is None else model,
                    kind=kind, operation=operation)
            finally:
                # A capture IS a campaign write (`prompts/index.json`), and
                # the one route that reaches this while persisting nothing else
                # is `post_opener` -- a draft, and now `@computes_only` so a
                # discarded one cannot invalidate somebody's clock price. That
                # marker would have taken this write's stamp with it, so the
                # write stamps for itself (#409).
                #
                # AFTER the record and INSIDE the hold, which is the whole of
                # the ordering. Stamped before the write, the new token is
                # readable while the capture is still landing, so a preview
                # taken in that gap is handed a token that outlives a write it
                # never saw -- and `/advance` then passes a check against it,
                # which is the one thing the token exists to refuse (Codex
                # review). Here, no reader can hold this token until the write
                # it records is on disk.
                #
                # In a `finally` because `record` writes an index and a body and
                # can fail between them; the returns and the `except` around
                # this block are the rule from the other end, and both are
                # reached without `record` having been entered at all -- a
                # capture that was skipped, contended, or whose scene had gone
                # wrote nothing, so it stamps nothing.
                #
                # Every other caller here is a turn that stamps at its terminal
                # points anyway; a second bump costs a caller holding an older
                # token nothing it was not already going to be told.
                store.revision.bump(cid)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound,
            store.locks.StoreBusy, OSError):
        return   # gone, contended, or unreadable: capture nothing, cost nothing


def _record_campaign_prompt(cid: str, task: str, breakdown: dict, *, model: str = "",
                            kind: str = "", conn: wire.Target | None = None,
                            operation: str = "",
                            still: Callable[[], bool] | None = None) -> None:
    """`_record_prompt`'s sibling for a capture filed with no scene
    (`prompt_log.NO_SCENE`, roadmap 01b §3.6): today the continuity reconcile
    sweep's, a campaign-scoped background run.

    `_record_prompt` cannot take that scene, because what it proves is that a
    SCENE still exists. This proves the CAMPAIGN does (`read_campaign` raising
    `CampaignNotFound`), asks `still` -- the site's own fence, which is what
    tells a run forgotten by a campaign delete from one that still owns the
    campaign -- and writes, all inside the same non-blocking hold, and bumps
    the write token after the write, in the hold, for `_record_prompt`'s
    reasons. Contention, a gone campaign, a failed fence or an unreadable
    store cost the capture and nothing else, and a capture that filed no
    entry stamps nothing. A `still` that raises leaves this function, as it does
    `_record_prompt`.

    The residual, stated rather than hidden: between a campaign's store delete
    and `runs.forget_subject`, `read_campaign` is all that stands, and it
    passes once a same-named campaign has been recreated -- the one way left
    for a campaign-level capture to land in the wrong campaign.
    """
    if conn is not None and "model_window" not in breakdown:
        breakdown = {**breakdown, "model_window": model_window(conn)}
    # What the call added (`structured`, `call_cap`) as `_record_prompt`
    # reports it, so a campaign-level capture reads like a scene-level one.
    report = _sampling_report(conn)
    if report is not None:
        breakdown = {**breakdown, "sampling": report}
    try:
        with store.locks.campaign_lock_nowait(cid) as got:
            if not got:
                return
            store.campaigns.read_campaign(cid)
            if still is not None and not still():
                return
            eid = store.prompt_log.record(cid, store.prompt_log.NO_SCENE, task,
                                          breakdown, model=model, kind=kind,
                                          operation=operation)
            # After the record and inside the hold: `_record_prompt`'s
            # ordering, for its reason. `record` swallows its own I/O errors
            # and answers None for a capture no reader can see (a payload the
            # index never named), so only an entry it filed stamps -- nothing
            # else here is a write a token holder could miss.
            if eid is not None:
                store.revision.bump(cid)
    except (store.campaigns.CampaignNotFound, store.locks.StoreBusy, OSError):
        return   # gone, contended, or unreadable: capture nothing, cost nothing
