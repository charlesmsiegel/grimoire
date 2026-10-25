"""tracker: update_run responsibilities."""

from __future__ import annotations

import anyio
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from ... import inference as operations
from ... import store
from ...llm import LLMClient
from ...llm_errors import LLMError
from ..common import _llm_http_error, require_inference, run_error
from . import _expression_selection, _locks, _persistence
from ._router import router as router


async def _update(app, cid: str, sid: str, key: str, gen: int | None, client: LLMClient,
                  identity: str, flag_later: bool = False, trust_base: bool = False) -> dict:
    """One post's update, as a run's outcome. `sid` is a hint (see above)."""
    async with _locks._scene_lock(app, cid, identity):
        try:
            return await _update_locked(cid, sid, key, gen, client, identity, flag_later,
                                        trust_base)
        except BaseException as exc:
            # Cancelled (a shutdown mid-call) or a bug: the record must not be
            # left `pending` with no run behind it, which reads as an update
            # still on its way, for good. Shielded, as `character_turns._rescue`
            # is, so the write survives the cancellation that brought us here.
            reason = (_text(exc) if isinstance(exc, Exception)
                      else "the update was interrupted")
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(_persistence._fail, cid, identity, sid, key, reason, gen)
            raise


async def _update_locked(cid: str, sid: str, key: str, gen: int | None, client: LLMClient,
                         identity: str, flag_later: bool = False,
                         trust_base: bool = False) -> dict:
    """`_update`'s body, under the scene's tracker lock. Every failure it
    expects is settled here and returned as the run's outcome."""
    try:
        # Inside the coroutine, so a campaign with no usable connection is
        # a failed record saying why rather than an exception out of the run.
        # In a worker: resolving a connection reads the campaign and the
        # connection files, and this runs on the lifespan loop.
        resolved = await run_in_threadpool(lambda: require_inference("tracker-update", cid))
    except HTTPException as exc:
        await run_in_threadpool(_persistence._fail, cid, identity, sid, key, _detail(exc), gen)
        return {"state": "failed", "error": run_error(exc)}
    try:
        prep = await run_in_threadpool(_persistence._prepare, cid, identity, sid, key, gen, trust_base)
    except Exception as exc:  # noqa: BLE001 -- the walk fails closed on a bad ledger
        await run_in_threadpool(_persistence._fail, cid, identity, sid, key, _text(exc), gen)
        return {"state": "failed", "error": {"kind": "run_failed", "detail": _text(exc)}}
    if prep is None:
        return {"state": "landed", "result": {"skipped": True}}
    fields = prep["fields"]
    messages = store.tracker.prompt.build_messages(
        fields, prep["prior"], prep["roster"], prep["present"], prep["newcomers"],
        prep["context_posts"], prep["post_msg"])
    try:
        with store.usage.meter("tracker-update", campaign=cid, scene=prep["sid"],
                               post=prep["post"], response_id=prep["rid"]) as m:
            text = await operations.generate("tracker-update", messages, client=client,
                                             resolved=resolved, usage=m.usage, stream=False)
        reply = store.tracker.merge.parse_reply(text)
    except LLMError as exc:
        await run_in_threadpool(_persistence._fail, cid, identity, prep["sid"], key, _text(exc), gen)
        return {"state": "failed", "error": run_error(_llm_http_error(exc))}
    except store.tracker.merge.TrackerReplyError as exc:
        await run_in_threadpool(_persistence._fail, cid, identity, prep["sid"], key, _text(exc), gen)
        return {"state": "failed", "error": {"kind": "bad_reply", "detail": _text(exc)}}
    snapshot, changed = store.tracker.merge.apply_reply(
        prep["prior"], reply, fields, prep["roster"], prep["present"],
        store.scenes.match_name)
    # A re-run or Retry rebuilds this record from the one before it; whatever
    # a person typed into it, and the reply did not itself move, is kept.
    snapshot, changed, restored = store.tracker.merge.keep_user_values(
        snapshot, prep["own"], prep["prior"], changed, prep["set_here"])
    snapshot, expressions, expression_summary = await _expression_selection.choose(
        cid, prep['sid'], identity, key, prep, snapshot, client)
    try:
        written = await run_in_threadpool(
            _persistence._commit, cid, identity, prep["sid"], key, snapshot, changed=changed,
            digest=store.tracker.fields.digest(fields), model=resolved.chain.primary.model,
            flag_later=flag_later, seen_seq=prep["seen_seq"], gen=gen,
            stale_base=prep["stale_base"], touched=restored, base=prep["base"],
            certificate=prep["provenance"], expressions=expressions, expression_prep=prep)
    except Exception as exc:  # noqa: BLE001 -- as `_prepare`: never leave it `pending`
        await run_in_threadpool(_persistence._fail, cid, identity, prep["sid"], key, _text(exc), gen)
        return {"state": "failed", "error": {"kind": "run_failed", "detail": _text(exc)}}
    if not written:
        return {"state": "landed", "result": {"skipped": True}}
    # Publication may clear one actor whose assets changed after selection.
    expression_summary.update(
        selected=sum(all(k in actor.get('fields', {}) for k in _expression_selection.KEYS)
                     for ref, actor in snapshot.items() if ref in prep['present']),
        failed=len(expressions['errors']))
    expression_summary['skipped'] = max(0, len(prep['present']) - expression_summary['selected']
                                         - expression_summary['failed'])
    return {"state": "landed", "result": {"changed": len(changed), "expressions": expression_summary}}


def _detail(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("detail") or detail.get("kind") or detail)
    return str(detail)


def _text(exc: Exception) -> str:
    return str(exc) or type(exc).__name__
