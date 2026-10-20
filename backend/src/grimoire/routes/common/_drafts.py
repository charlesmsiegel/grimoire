"""common: drafts responsibilities."""

from __future__ import annotations

from fastapi import HTTPException

from ... import inference as operations
from ... import store
from ...llm import LLMClient
from ...llm_errors import LLMError
from . import _deadlines, _errors, _inference


async def draft_completion(client: LLMClient, resolved: _inference.UsableInference,
                           messages: list[dict], task: str, shape, cid: str = "",
                           sid: str = "", *, schema: dict | None = None,
                           max_tokens: int | None = None) -> dict:
    """One metered non-stream generation, as the outcome a detached run reports.

    THE helper the twelve computing `draft` routes share, and the reason they
    are allowed to be three lines each. Every one of them was the same four
    steps written out again -- open a meter, call under the duration ceiling,
    turn an `LLMError` into an HTTP failure, parse the text into the caller's
    own shape -- and detaching them would have made it the same *seven*, with a
    reservation and a terminal outcome dict on either end. Thirteen slightly
    different implementations of that contract is the risk the issue named.

    Returns what `runner._guarded` understands: `{"state": ..., "result"?:,
    "error"?:}`. A failure is REPORTED rather than raised, because a raise here
    is recorded as an opaque `run_failed` and the client would lose the
    `missing_key` / `timeout` / `rate_limit` kind it acts on today.

    `shape` runs OUTSIDE the meter's block: it is parsing, not generation, and
    a parse that raises inside would file the model's successful call as a
    provider error in the usage ledger -- which is the one place that has to be
    able to say what the provider actually did.

    `resolved` is the route's resolution of `task` (`require_inference`, at
    the call site that names the task), which `inference.generate` refuses
    for any other task.

    `schema` and `max_tokens` are `inference.generate`'s (01f-C1: structured
    mode where the provider takes it, with the schema in `messages`; a
    per-call output cap), each passed only when given, so a draft that names
    neither sends exactly what it always sent.
    """
    asked: dict = {}
    if schema is not None:
        asked["schema"] = schema
    if max_tokens is not None:
        asked["max_tokens"] = max_tokens
    try:
        with store.usage.meter(task, campaign=cid, scene=sid) as m:
            text = await _deadlines._bounded_call(
                operations.generate(task, messages, client=client, resolved=resolved,
                                    usage=m.usage, stream=False, **asked),
                on_timeout=_deadlines._noting(client, resolved, m.usage))
    except LLMError as exc:
        return {"state": "failed", "error": _errors.run_error(_errors._llm_http_error(exc))}
    try:
        return {"state": "landed", "result": shape(text)}
    except HTTPException as exc:
        # A refusal the SHAPE chose -- an image whose bytes the model described
        # for a record that has since gone. Reported with its own status rather
        # than as the generic `run_failed` an escaping exception becomes.
        return {"state": "failed", "error": _errors.run_error(exc)}


def image_draft_prompt(path, subject: str,
                       cid: str = "") -> tuple[_inference.UsableInference, list[dict]]:
    """The resolution and the messages for one image-description draft.

    Everything `_draft_description` used to do BEFORE the provider call, split
    out so the route can do it synchronously -- while the request is still
    there to be told `no such image`, `that connection cannot read pictures`,
    `that file is too large` -- and detach only the part that is slow. A
    refusal the caller can act on must not arrive as a run state minutes later.

    `cid` is the CAMPAIGN whose routing applies (#142), and only the campaign
    library surface has one -- the other three describe a picture belonging to
    a world record, where the path's `cid` is a character id and naming a
    campaign would be a coincidence of spelling.
    """
    # The image route `requires` vision, so the seam refuses a connection that
    # cannot read pictures -- `claude_agent`, whose SDK path joins message
    # content as a string and would raise deep inside it, answers with
    # `image_drafts.UNSUPPORTED` exactly as this function's own kind check
    # used to; a model its catalog or the user says is blind, as `incapable`.
    # The one check, so no surface can drift from another.
    resolved = _inference.require_inference("image-description", cid)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    try:
        return resolved, store.image_drafts.build_prompt(path, subject)
    except store.image_drafts.ImageTooLargeError as exc:
        # Refused before the bytes are read, so this is an error rather than the
        # killed process an Android install would otherwise get. See MAX_BYTES.
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        # An externally-placed file with an extension we never accepted, so we
        # cannot label its bytes for the provider.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
