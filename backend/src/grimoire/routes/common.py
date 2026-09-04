"""Shared helpers for the route modules.

Dependency-injection providers, the pydantic-version shim, the response-scope
read/write pair, image serving, the 404 guards every domain module reuses, the
opt-in page window the growing list routes share (#216), the stale-write
precondition every record editor shares (#35), and the LLM error-status
taxonomy every non-stream generation route answers with (#213). This module
holds no routes and imports no sibling route module, so it is always safe to
import from one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .. import llm, store
from ..health import ProviderHealth
from ..llm import LLMClient, effective_model
from ..llm_errors import LLMError
from ..openai_compatible import OpenAICompatibleClient

log = logging.getLogger(__name__)


def _fresh_or_409(expected: str | None, current: str | None) -> None:
    """Refuse a write whose base is no longer what is on disk (#35).

    The store is a folder of markdown files the user is invited to point at
    Dropbox or Syncthing, so "somebody else changed this while you had it open"
    is an ordinary event here rather than an exotic one -- and until now it
    resolved as last-writer-wins, silently. A save that carries the rev it read
    turns that into a 409 the editor can act on.

    An empty or absent `expected` means the caller opted out (see
    `EntityUpdate.rev`); a client that has no rev to offer must not be turned
    into one that can never write. `current is None` cannot happen for a record
    that exists, so it only reaches here for one that has been deleted
    underneath -- also a conflict, and one whose 404-shaped alternative would be
    a worse answer: the record is exactly as gone as the user's unsaved edit is
    real.

    Two limits, both narrower than the window this closes and neither of them
    closed by it:

    - **Check and write are not atomic.** Nothing holds a lock across the two,
      so a writer landing in that microsecond still wins silently. What the
      precondition removes is the *human*-scale window -- the minutes a record
      sits open in an editor -- which is the one a sync client or a second
      device actually lands in. Two of this app's own tabs saving the same
      record in the same instant can still both pass. Closing that needs the
      compare and the write under one lock, and entity writes take no lock
      today (`store/locks.py`: entities are outside the campaign domain).
    - **As sharp as `statcache`, and no sharper.** `current` comes from a hash
      memoized on `(path, mtime_ns, size)`, so an external write landing with
      the same size inside the filesystem's timestamp granularity is invisible
      here exactly as it is to every other reader in the app. `statcache`'s
      one-second racy window narrows that to a case a synced folder is unlikely
      to produce; closing it outright would mean re-reading every record on
      every request, which is the cost that cache exists to avoid.
    """
    if not expected or expected == current:
        return
    raise HTTPException(status_code=409, detail={
        "kind": "stale_record", "rev": current,
        "detail": "This record changed on disk since you opened it — "
                  "reload to see the current version before saving."})


# ---- the LLM error taxonomy (#213) ----
#: The HTTP status each `LLMError.kind` answers with.
#:
#: Every non-stream LLM route used to answer 502 for all of them. The `kind` was
#: in the body the whole time, but a status code is the part a browser, a proxy,
#: a retry helper and a log reader all understand without knowing this app's
#: vocabulary -- and 502 told every one of them the same wrong thing: that the
#: provider had misbehaved, when it had in fact answered clearly.
#:
#: Streaming routes are deliberately not covered. By the time a provider fails
#: there, the reply is already a `200 text/event-stream`, so their errors stay
#: in-band SSE events carrying the same `kind` (`streaming.py`). No status code
#: can be sent after the headers are gone, and inventing one for the body would
#: be a second, disagreeing taxonomy.
_LLM_STATUS = {
    # Not 401. This API has no authentication of its own, so 401 would be a
    # claim about the *caller's* credentials that is simply untrue -- and RFC
    # 9110 requires a `WWW-Authenticate` on one, which there is no honest value
    # for. An upstream that refused our key is a gateway that could not serve
    # the request, which is what 502 says. The `kind` still tells the frontend
    # which gateway failure this is.
    "auth": 502,
    # Setup rather than failure: nothing can be sent until the user changes a
    # connection. 409 because `_require_connection` already answers exactly that
    # for exactly this, and two codes for one condition would be worse than
    # either alone. `missing_dependency` is the same condition with a different
    # piece missing -- a `claude` connection whose SDK is not installed sails
    # past `_require_connection`, which checks a key and a base URL, neither of
    # which that kind has -- so it gets the same answer rather than a second one.
    "missing_key": 409,
    "missing_dependency": 409,
    # The one this issue is named for. A caller that can tell "slow down" from
    # "the provider is broken" can wait and try again; one reading 502 cannot.
    "rate_limit": 429,
    # Upstream never finished, or our own ceiling expired waiting on it
    # (`_bounded_call`). Both are 504 rather than 502: the gateway reached the
    # provider fine, it just never got an answer back.
    "timeout": 504,
    # Could not talk to the provider at all, which includes httpx's own connect
    # and read timeouts -- the providers raise `network` for every `HTTPError`.
    # That looks like a `timeout` in the making and is deliberately left alone:
    # `network` is retryable and `timeout` is not (`llm.RETRYABLE_KINDS` says
    # why), so reclassifying a refused connection would stop it being retried.
    # 502 is also the right answer for it -- there was no answer to be late.
    "network": 502,
    "bad_response": 502,
}
#: What a kind with no entry above answers with. It cannot happen today -- a
#: test holds `_LLM_STATUS`'s keys to `llm_errors.KINDS` -- but this runs on the
#: failure path, where a KeyError would replace the provider's error with our
#: own, and 502 is the answer every kind gave before this map existed.
_LLM_STATUS_FALLBACK = 502


def _retry_after_header(seconds: float | None) -> str | None:
    """A provider-named wait as a `Retry-After` value, or None for no header.

    Rounded *up* to whole seconds: the header's delta-seconds form is an
    integer, and rounding down would advise a retry fractionally inside a window
    the provider already said it would reject.

    Non-finite and non-positive both answer None. `llm_errors.retry_after_seconds`
    already rejects those where a header is parsed, but a provider client is free
    to construct an `LLMError` with any float it likes, and `Retry-After: inf` is
    worse than no header at all.
    """
    if seconds is None or not math.isfinite(seconds) or seconds <= 0:
        return None
    return str(math.ceil(seconds))


def _llm_http_error(exc: LLMError) -> HTTPException:
    """The HTTP failure one non-stream LLM error becomes (#213).

    One function rather than a raise at each of the ten call sites, because the
    reason those sites were all wrong is that they each spelled the answer out
    themselves. Callers `raise _llm_http_error(exc) from exc`: the provider failure
    IS the cause, and dropping it leaves a traceback that starts at the `raise`
    with nothing saying which call failed or why.

    A rate limit carries the provider's own `Retry-After` when it named one.
    Gated on the *kind* rather than on the status being 429, because the kind is
    what makes the window mean anything: `retry_after` is set from an error
    response's header, and only a rate-limiter's is advice about when this
    request will be served. Reading the status instead would tie the header to a
    number that could be revisited, and silently drop it when it was.

    It can be absent even for a provider that did name one -- `llm._resilient`
    waits such a window out and re-raises whatever the *last* attempt said -- so
    the header is a bonus, never a promise, which is exactly what RFC 9110 makes
    it.
    """
    status = _LLM_STATUS.get(exc.kind, _LLM_STATUS_FALLBACK)
    retry_after = _retry_after_header(exc.retry_after) if exc.kind == "rate_limit" else None
    return HTTPException(
        status_code=status,
        detail={"detail": exc.detail, "kind": exc.kind},
        headers={"Retry-After": retry_after} if retry_after else None)


def run_error(exc: HTTPException) -> dict:
    """One refusal as the error a POLLING client reads off a detached run.

    Carries the HTTP status the same failure would have had when these routes
    answered synchronously, so a client's existing handling of `409
    missing_key` or `504 timeout` needs no second shape to understand -- the
    only thing that moved is where it reads it from. That equivalence is the
    whole migration contract for a computing route: what the client sees must
    not depend on whether the work was detached.

    Here rather than in `runs`, which the `draft` routes' modules already reach
    through this one, and which may not import anything that would let it build
    an `HTTPException` from an `LLMError` (`_llm_http_error` lives here).

    **`retry_after` moves into the BODY, because a header cannot survive
    detachment.** A rate limit's window is advice about when this work can be
    tried again, and the response that would have carried it as `Retry-After`
    went out as a 202 before the provider was even called -- the same thing
    that has always been true of a streamed failure, whose errors are SSE
    events for exactly this reason. Dropping it instead would silently lose the
    one number a client needs to schedule its retry.
    """
    detail = exc.detail
    if isinstance(detail, dict):
        out = {"kind": detail.get("kind", "refused"),
               "detail": str(detail.get("detail", "")),
               "status": exc.status_code}
    else:
        out = {"kind": "refused", "detail": str(detail), "status": exc.status_code}
    window = (exc.headers or {}).get("Retry-After")
    return {**out, "retry_after": window} if window else out


async def draft_completion(client: LLMClient, conn: dict, messages: list[dict],
                           task: str, shape, cid: str = "", sid: str = "") -> dict:
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
    """
    try:
        with store.usage.meter(task, campaign=cid, scene=sid) as m:
            text = await _bounded_call(client.complete(messages, conn, m.usage),
                                       on_timeout=_noting(client, conn, m.usage))
    except LLMError as exc:
        return {"state": "failed", "error": run_error(_llm_http_error(exc))}
    try:
        return {"state": "landed", "result": shape(text)}
    except HTTPException as exc:
        # A refusal the SHAPE chose -- an image whose bytes the model described
        # for a record that has since gone. Reported with its own status rather
        # than as the generic `run_failed` an escaping exception becomes.
        return {"state": "failed", "error": run_error(exc)}


def image_draft_prompt(path, subject: str, cid: str = "") -> tuple[dict, list[dict]]:
    """The connection and the messages for one image-description draft.

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
    conn = _require_connection("image-description", cid)
    if conn.get("kind") not in store.image_drafts.SUPPORTED_KINDS:
        # A refusal the user can act on, rather than a 500 out of the SDK path:
        # `claude_agent` joins message content as a string, so a multimodal
        # message raises deep inside it. See `store/image_drafts.py`.
        raise HTTPException(status_code=409, detail=store.image_drafts.UNSUPPORTED)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    try:
        return conn, store.image_drafts.build_prompt(path, subject)
    except store.image_drafts.ImageTooLargeError as exc:
        # Refused before the bytes are read, so this is an error rather than the
        # killed process an Android install would otherwise get. See MAX_BYTES.
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        # An externally-placed file with an extension we never accepted, so we
        # cannot label its bytes for the provider.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _connection_problem(conn: dict) -> str | None:
    """Why this connection cannot send, or None if it can.

    The credential check `_require_connection` turns into a 409 and
    `_fallback_connection` turns into "there is no fallback" — one function,
    because a fallback that is silently unusable is exactly the failure a
    fallback exists to prevent, and two copies of this rule would drift.
    """
    if conn["kind"] == "openrouter" and not conn.get("api_key"):
        return "OpenRouter key not set"
    if conn["kind"] == "openai_compatible" and not conn.get("base_url"):
        return "Endpoint base URL not set"
    return None


def _fallback_connection() -> dict | None:
    """The connection a generation falls back to on exhaustion (#144), or None.

    Resolved per generation rather than at import, so repointing it on the
    Configuration page lands without a restart — the same contract the timeout
    resolver has.

    Every way of not having a usable one answers None, and none of them raise:
    an unset key, a fallback pointing at a connection that has since been
    deleted, an unreadable store, and — the one worth spelling out — a
    connection missing the credential it needs to send. Surfacing *that* as an
    error would replace the primary's real failure ("OpenRouter is rate
    limiting you") with a confusing second one about a connection the user was
    not using, on exactly the request where they need the first message. So a
    misconfigured fallback is no fallback, and the primary's error stands.
    """
    try:
        fid = store.read_config().get("fallback_connection_id", "")
        if not fid:
            return None
        conn = store.llm_connections.read_connection_raw(fid)
    except (store.llm_connections.ConnectionNotFound, store.locks.StoreBusy, OSError):
        return None
    return None if _connection_problem(conn) else conn


def build_llm(health: ProviderHealth | None = None) -> LLMClient:
    """The gateway client for one app (#215).

    Built per app rather than once per process because it owns an
    `httpx.AsyncClient` connection pool that `main._lifespan` closes on the way
    out; a module-level singleton had nowhere to be closed from. Construction
    opens nothing — the pool is created on the first call — so an app that
    never generates pays nothing for holding one.

    The idle bound is passed as a resolver, not a number: llm.py must not import
    the store (#239), and reading config.md per call is what lets a
    Configuration-page change land without a restart (#243). The retry count and
    the fallback route (#144) ride the same seam for the same two reasons.

    `health` is that app's registry (#146), passed as the observer every attempt
    reports its outcome to. Optional so a caller that only wants to generate —
    tests, mostly — need not build one; the facade treats a missing observer as
    "nobody is listening" rather than as an error.
    """
    return LLMClient(timeout=store.config.llm_timeout,
                     retries=store.config.llm_retries,
                     fallback=_fallback_connection,
                     observer=health.record if health is not None else None)


def build_openai_compatible_client() -> OpenAICompatibleClient:
    """The model-listing client for one app, owned and closed like `build_llm`'s.

    A second instance rather than the one `LLMClient` holds for generation:
    `list_models` is not part of the gateway's surface, which dispatches by
    connection kind, and reaching through `LLMClient` for the inner client
    would make a private attribute part of the route's contract.
    """
    return OpenAICompatibleClient()


def get_health(request: Request) -> ProviderHealth:
    """The app's health registry, built in `main.create_app` (#146).

    On `app.state` rather than at module scope for the reason every other piece
    of per-app state is: a `TestClient` builds an app per test, and a shared
    module-level registry would carry one test's recorded provider failures
    into the next.
    """
    return request.app.state.health


def get_openai_compatible_client(request: Request) -> OpenAICompatibleClient:
    return request.app.state.openai_compatible


def _dump(model: BaseModel) -> dict:
    """model_dump() on pydantic v2, dict() on v1. The Android build may pin the
    pure-python pydantic 1.x wheel (docs/android-architecture.md §7); this is
    the only v2-specific API the codebase uses."""
    dump = getattr(model, "model_dump", None)
    return dump() if dump is not None else model.dict()


def _turn_override(body) -> dict | None:
    """A request body's one-shot per-turn response override as a plain dict.

    The wire type is `ResponseSettings`, like every other response write path,
    so a malformed payload is rejected at the boundary instead of reaching
    response_presets.resolve mid-generation. Unset fields are dropped: a scope
    dict means "these fields have an opinion", and a None would read as one.
    """
    if body is None or getattr(body, "response", None) is None:
        return None
    return {k: v for k, v in _dump(body.response).items() if v is not None}


def _record_prompt(cid: str, sid: str, task: str, breakdown: dict | None,
                   *, model: str | None = None) -> None:
    """Freeze what this turn's model is about to see (#157).

    Called with the breakdown from the SAME `context.compose_*` call that
    produced the messages being sent — see `store.prompt_log`. The scene's
    stamped model rides along so a snapshot still names its provider after the
    scene is repointed at another one.

    `model` overrides that stamp for the one caller that KNOWS the turn did not
    run on it: a reroll carrying a per-call route override (#77). **None and ""
    are different answers**, which review caught this defaulting away: None is
    "I have nothing to say, use the scene's stamp", and "" is "this ran on a
    route that names no model at all" — a custom endpoint with none configured,
    which generates perfectly well on the provider's own default. Collapsing
    the two filed such a reroll under the campaign's model, which is the exact
    mislabel #77's third bullet exists to prevent. Keyword-only and defaulted
    to None, so every other caller keeps the shared-inaccuracy rule
    `store.prompt_log`'s docstring argues for — the frozen panel and the live
    one agreeing about a scene matters more than either being exactly right.
    That argument is about a *drifted* stamp, though, and it does not cover a
    turn the user deliberately sent somewhere else: there the live panel is
    describing the next turn and this one is describing a turn that happened,
    and they are simply about different things.

    Called once the turn is committed to happening — after the stream object
    exists, so the pre-stream claim has already succeeded — but NOT from inside
    the stream itself. The finalizers carry delicate turn-ownership and abort
    semantics that a debug write has no business joining, and a turn the
    provider *failed* is one of the turns whose prompt is most worth having.
    `prompt_log.record` swallows its own storage failures and never waits on a
    lock, so this cannot cost the turn either way.
    """
    # None means the caller composed with `describe=False` because capture is
    # off. Nothing to record, and nothing was built to record.
    if breakdown is None:
        return
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
            # `is None`, not `or`: see the docstring. "" is a real answer.
            try:
                store.prompt_log.record(
                    cid, sid, task, breakdown,
                    model=meta.get("model", "") if model is None else model)
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


def _abandon(task: asyncio.Task) -> None:
    """Ask an overrun call to stop, then stop waiting on it.

    Retrieving the exception in a callback is what keeps asyncio from logging
    the abandoned task as never-retrieved (`llm._swallow`'s job, kept local:
    routes does not reach into that module's privates). Cancellation is not
    awaited here on purpose -- awaiting it is the very thing that lets the
    ceiling be overrun.
    """
    task.cancel()
    task.add_done_callback(lambda t: None if t.cancelled() else t.exception())


async def _bounded_call(coro, ceiling: float | None = None, on_timeout=None):
    """Await one non-streaming generation under a total-duration ceiling (#272).

    The facade's own bound is an *idle* one -- the gap between deltas -- which is
    the right shape for streamed prose (cutting a healthy long generation off
    mid-sentence is worse than letting it finish) but leaves an upstream that
    emits a frame every `llm_timeout - 1` seconds holding its request forever.
    The one-shot generation routes have no partial output to protect: nothing is
    visible until the call returns, and a truncated one costs only a retry. So
    they get a stopwatch, and `stream` deliberately does not.

    Absorb is not routed through here. It carries `_Budget`, which bounds a whole
    *sequence* and knows which of its steps are droppable -- and whose `0` means
    "no ceiling at all, however long the calls take". Folding this ceiling into
    the facade would silently narrow that escape hatch for every absorb step, so
    the ceiling stays where the policy is: the routes that opt into it.

    An overrun is raised as the same `LLMError("timeout", ...)` an upstream stall
    already raises, so every caller's existing `except LLMError` covers it with
    no new branch -- and it reaches the client as the 504 that kind maps to
    (#213), which is what it is. `llm_call_budget <= 0` disables the ceiling.

    `ceiling` overrides that setting for a caller whose wait is not a
    generation and must not inherit the "no ceiling at all" escape hatch --
    the health check (#146), which is a button somebody is watching rather
    than a model they are waiting on.

    `on_timeout` is called with the overrun error before it is raised, for the
    caller that knows which connection was being held. `_noting` builds one.

    `asyncio.wait_for` is deliberately NOT used, for the reason `llm._settle`
    spells out: it cancels the call and then waits for that cancellation to
    finish, so the ceiling is only as hard as the unwinding underneath it. Here
    that unwinding is `_guard`'s `finally`, which grants the pull `_CLOSE_TIMEOUT`
    to settle and the provider another to close -- so a stalled upstream can
    hold the request ~10s past a ceiling that promised to give up at `seconds`,
    and a client that swallows cancellation holds it for good. Waiting is
    therefore capped here and the cancelled call is left to unwind on its own.
    A detached task is a leak we can live with; a wedged request is not.
    """
    seconds = store.config.llm_call_budget() if ceiling is None else ceiling
    if seconds <= 0:
        return await coro
    task = asyncio.ensure_future(coro)
    try:
        done, _ = await asyncio.wait({task}, timeout=seconds)
    except asyncio.CancelledError:
        # The caller went away (SSE disconnect, shutdown). `wait_for` propagated
        # that inward for free; `asyncio.wait` does not, and an uncancelled task
        # here would outlive the request that wanted it.
        _abandon(task)
        raise
    if not done:
        _abandon(task)
        overrun = LLMError(
            "timeout", f"the reply did not finish within {seconds:g}s — giving up")
        # The one failure the facade cannot see for itself: cancelling the call
        # unwinds `_resilient` through `GeneratorExit`, not through its
        # `except LLMError`, so without this the connection that just held a
        # request past its ceiling keeps whatever verdict it had — a green dot
        # over a 504 (#146). Only the holder of the ceiling can tell this from
        # a caller who simply walked away, which is why the facade does not try.
        if on_timeout is not None:
            on_timeout(overrun)
        raise overrun
    try:
        return task.result()
    except TimeoutError as exc:
        # asyncio.TimeoutError IS the builtin TimeoutError from 3.11 on, so an
        # upstream that gives up on its own lands in the same handler as an
        # expired ceiling. It keeps its own message: blaming a setting that had
        # nothing to do with it would send the user to tune the wrong knob.
        raise LLMError("timeout", str(exc) or "the call timed out") from exc


def _noting(client: LLMClient, conn: dict, usage: dict | None = None):
    """`on_timeout` for a bounded generation: file the overrun against the
    connection that was actually running when the ceiling fired (#146).

    A function rather than a lambda at each call site, because the thing worth
    reading at those sites is the generation, not the bookkeeping.

    `conn` is the route's connection and `usage` is the holder the facade
    stamps per attempt, which is the more accurate of the two: a generation
    that failed over is being served by the *fallback* by the time it overruns,
    and blaming the primary would both overwrite its real failure with a
    timeout it did not cause and leave the connection that did cause one
    looking healthy. The route's own connection is the fallback for a caller
    that threads no holder.
    """
    def note(exc: LLMError) -> None:
        # Guarded for the same reason `llm._observe` is, and it took a broken
        # test suite to prove the point: this runs on the failure path, and an
        # exception here does not merely lose a status update — it REPLACES the
        # error the caller is about to raise. A gateway double with no
        # `note_outcome` turned "the budget stopped this phase" into an
        # AttributeError, and the phase stopped reporting why it died.
        try:
            client.note_outcome((usage or {}).get(llm.ATTEMPTED) or conn, exc)
        except Exception as exc2:  # noqa: BLE001 - see above
            log.warning("could not record a ceiling timeout: %s", exc2)

    return note


def get_llm(request: Request) -> LLMClient:
    """The app's gateway client, built in `main.create_app` and closed by its
    lifespan (#215). `app.dependency_overrides` replaces this callable whole,
    which is the seam `tests/llm_fakes.py` is injected at."""
    return request.app.state.llm


# ---- response bundle (scope endpoints) ----
def _response_body(scene_meta: dict, campaign_meta: dict, cfg: dict, own: dict) -> dict:
    """The shape every scope returns. `own` is that scope's raw frontmatter."""
    resolved = store.response_presets.resolve(
        scene_meta=scene_meta, campaign_meta=campaign_meta, config=cfg)
    fields = {k: own.get(k, "") for k in store.scenes.RESPONSE_FIELDS}
    # Global stores the style as default_style_id; normalize so the picker sees
    # one spelling at every scope. The on-disk key is deliberately unchanged.
    if not fields["style_id"]:
        fields["style_id"] = own.get("default_style_id", "")
    return {**fields,
            "effective": {k: resolved[k] for k in ("style_id",) + store.lengths.KNOBS},
            "provenance": resolved["provenance"]}


# ---- routing bundle (#142) ----
def _routing_body(scope: str, campaign_meta: dict) -> dict:
    """What a routing picker renders, at either scope.

    `/response`'s shape and its reason: the picker offers "inherit", so it has
    to be able to say what inheriting currently gets you -- and for routing that
    answer can be a connection two scopes away.

    The connection list rides along because every value in the bundle is an
    opaque id: without it the picker would have to fetch the whole connection
    list (keys, base URLs and all) to render ten option labels.
    """
    conns = store.llm_connections.list_connections()
    known = {c["id"] for c in conns}
    bundle = store.routing.bundle(campaign_meta=campaign_meta, cfg=store.read_config(),
                                  exists=lambda conn_id: conn_id in known, scope=scope)
    active = store.llm_connections.get_active()  # routing-ok: names the cascade's base
    return {**bundle, "scope": scope,
            "active_connection_id": active["id"] if active else "",
            "catalog": [{"key": r.key, "label": r.label, "hint": r.hint,
                         "tasks": list(r.tasks)}
                        for r in store.routing.routes_for(scope)],
            "connections": [{"id": c["id"], "name": c["name"], "kind": c["kind"],
                             "model": c.get("model", ""),
                             # Whether it can send AT ALL. Routing a job to a
                             # keyless connection is a 409 on every call of that
                             # kind, and the picker is where that is cheap to
                             # notice. `_connection_problem` is the same rule
                             # `_require_connection` refuses with, asked of a
                             # masked record -- which is why `key_set` stands in
                             # for the key it deliberately does not carry.
                             "usable": _connection_problem(
                                 {**c, "api_key": "x" if c.get("key_set") else ""}) is None}
                            for c in conns]}


def _routing_fields(scope: str, body) -> dict:
    """The `{route: connection_id}` map a PUT means, validated for this scope.

    A route the scope cannot set is a 400 rather than a stored key: written
    silently it would look applied on the page and route nothing at all, which
    is the failure mode `store/routing.py`'s `campaign_scoped` flag exists to
    make impossible.
    """
    routes = _dump(body).get("routes") or {}
    if not isinstance(routes, dict):
        raise HTTPException(status_code=400, detail="routes must be an object")
    fields = {store.routing.config_key(str(k)): str(v or "").strip() for k, v in routes.items()}
    refused = store.routing.refused(scope, fields)
    if refused:
        raise HTTPException(
            status_code=400,
            detail=f"not routable at this scope: {sorted(k[len('route_'):] for k in refused)}")
    # A value naming no connection is refused HERE and tolerated on READ, which
    # looks inconsistent and is the whole point: a write is a decision, made
    # against the list this same response carries, so a typo has to fail rather
    # than be stored as a setting that quietly routes nothing. A stored value
    # that stops naming a connection LATER is a deletion, which cannot reach
    # into every campaign's frontmatter -- so resolution walks past it instead
    # of failing a turn (`routing._opinion`).
    unknown = sorted({v for v in fields.values() if v and not _connection_exists(v)})
    if unknown:
        raise HTTPException(status_code=400, detail=f"no such connection: {unknown}")
    return fields


def _connection_exists(conn_id: str) -> bool:
    """Whether this id names a connection, for the WRITE path only.

    Narrower than the read path's predicate on purpose, and the difference is
    which failures are allowed to look like a typo. A busy store or an
    unreadable file is not "no such connection": answering False would report a
    transient condition as a mistake in what the user typed, and the app already
    turns `StoreBusy` into a 409 that says what actually happened. Resolution
    swallows both because degrading to the next scope is better than failing a
    turn; a settings write has no turn to protect.
    """
    try:
        store.llm_connections.read_connection_raw(conn_id)
    except store.llm_connections.ConnectionNotFound:
        return False
    return True


def _write_response(setter, fields: dict, style_key: str = "style_id") -> None:
    """Map the picker's style_id back onto the scope's own spelling."""
    out = dict(fields)
    if style_key != "style_id" and "style_id" in out:
        out[style_key] = out.pop("style_id")
    setter(out)


# ---- image serving ----
_IMAGE_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp"}


def _with_descriptions(images: list[dict], descriptions: dict[str, str]) -> list[dict]:
    """One image listing, each entry carrying what it depicts.

    The descriptions ride with the listing the editors already fetch rather
    than through a second round trip -- the same choice the per-version payloads
    make for `avatar_focus`.

    `described` is separate from `description` and is not redundant: absent and
    `""` are different states in the sidecar (never reviewed vs reviewed and
    deliberately undescribed), and collapsing both to an empty string here would
    throw that away at the one boundary where the UI still needs it.
    """
    return [{**i,
             "description": descriptions.get(i["name"], ""),
             "described": i["name"] in descriptions}
            for i in images]


def _upload_image_ext(data: bytes) -> str:
    """The extension an uploaded record image is stored under, from its bytes (#321).

    The client's filename is not asked. Every consumer names a media type from
    the stored suffix -- the EPUB manifest, an export's data URIs, the
    Content-Type `_serve_image_file` sets -- so a JPEG uploaded as `avatar.png`
    used to be stored as `.png` and declared `image/png` by all three, which
    epubcheck reports as an error and some readers refuse to render. The
    extension allowlist never closed this: it was only ever checked against the
    filename, which is the thing that lied. `store.covers` settled it for
    campaign covers; this is the same rule for character avatars and galleries,
    greeting art, and location and lore images.

    Magic bytes rather than the PIL decode `covers.validate` runs (it also has
    to bound the raster it is about to thumbnail, which a signature cannot):
    the export names every packed image with the very same detector, and using
    one at both ends is what makes a stored image's suffix the suffix the packer
    derives, rather than two rules that merely happen to agree today. The two
    ends differ only in what they do with an answer of "no format I can name" --
    this refuses the upload, `export.Images` drops the image from the book.

    Bytes in no format we can label are refused rather than stored under a name
    that lies about them: an AVIF uploaded as `avatar.png` renders in a browser
    today only because browsers sniff, and it has never been packable.
    """
    ext = store.fetch.sniff_ext(data)
    if ext is None:
        raise HTTPException(status_code=400, detail="unsupported image type")
    return ext


#: The width a gallery tile requests through `?w=`, which `store.thumbs` honours
#: with an on-the-fly WebP downscale. Tiles render at 96-154px and 320 covers
#: retina. Here rather than in each listing route because two routes now build
#: thumbnail URLs -- the greeting tagger's and the world gallery's -- and a
#: width that drifts between them is two cache keys for one picture.
THUMB_W = 320


def _serve_image(root, cid: str, vid: str, name: str, base: str = "characters",
                 request: Request | None = None):
    p = store.assets.image_path(root, cid, vid, name, base)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    return _serve_image_file(p, request)


def _serve_image_file(p: Path, request: Request | None = None) -> Response:
    """Serve one image file with the app's caching contract.

    Bare URLs are no-cache: promotions swap file contents under stable URLs,
    so the browser must revalidate — with an ETag that's a 304, not a
    re-download. A `?v=` URL (built from list responses' version tokens) names
    one exact content state, so it caches immutable: zero requests on later
    renders.

    A `FileNotFoundError` reading the file is a 404, not a 500: an image can be
    replaced or removed between the caller resolving its path and this reading
    it, and that is a missing image rather than a server fault. That applies to
    every image route, not only covers — a deliberate widening, since a 500 was
    never the right answer for a file that went away mid-request.

    Only that one, though. Catching `OSError` whole would swallow a
    `PermissionError`, a Windows sharing violation, an exhausted file-descriptor
    table or a disk read error — cases where the image is still there — and
    report a real operational fault to the user as missing data, with the
    frontend dutifully marking a valid cover broken. Those surface as a 500,
    which is what they are.
    """
    try:
        st = p.stat()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
    versioned = request is not None and "v" in request.query_params
    cache = "public, max-age=31536000, immutable" if versioned else "no-cache"
    headers = {"Cache-Control": cache, "ETag": etag}
    if request is not None and etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    # ?w= asks for a downscaled variant — tiles shouldn't pull multi-MB originals.
    # An undecodable source just serves the original bytes.
    if request is not None and (w := request.query_params.get("w", "")).isdigit():
        tp = store.thumbs.thumbnail(p, max(16, min(1024, int(w))))
        if tp is not None:
            try:
                thumb = tp.read_bytes()
            except OSError:
                thumb = None  # cache entry swept between generation and read
            if thumb is not None:
                return Response(content=thumb, media_type="image/webp", headers=headers)
    try:
        content = p.read_bytes()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    # The bytes name the type; the stored suffix answers only for bytes that
    # sniff as nothing (#321). A new upload can no longer be misnamed, but a
    # store already on disk holds files that are, and serving one as
    # `image/png` works only because browsers sniff too.
    #
    # Serving keeps that fallback where the export drops the image instead: a
    # response has to carry some type, the app has to render what the user put
    # in it, and a browser sniffs past a wrong one. A book gets neither -- an
    # EPUB reader validates the manifest and may refuse the image outright.
    ext = store.fetch.sniff_ext(content) or p.suffix.lstrip(".").lower()
    return Response(content=content,
                    media_type=_IMAGE_MEDIA.get(ext, "application/octet-stream"),
                    headers=headers)


# ---- uploaded archives ----
@contextlib.asynccontextmanager
async def _spooled_upload(request: Request, cap: int, too_large: str):
    """Stream the request body to a temp file, yield its path, then remove it.

    Both zip-import routes (module packs, world bundles) need the same three
    things and get them here rather than each writing their own: the
    declared-length pre-check that refuses an oversized upload before a byte is
    read, the running count that refuses one whose Content-Length lied, and the
    unlink on every exit path. A world bundle runs to a gigabyte, so the body
    is never held in memory.
    """
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > cap:
        raise HTTPException(status_code=413, detail=too_large)
    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    try:
        total = 0
        # atomic-ok: a system temp file for the uploaded archive, not a store
        # record; read by the importer and unlinked in the finally below
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                total += len(chunk)
                if total > cap:
                    raise HTTPException(status_code=413, detail=too_large)
                f.write(chunk)
        yield Path(tmp_name)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _display_name_or_400(name: str) -> str:
    """A trimmed, single-line display name, or a 400.

    `dump_frontmatter` writes one `key: value` line per field and the parser
    reads them back a line at a time, so a value carrying a newline stores a
    mangled name AND leaves a stray line in the record that a later parse
    could read as a field. Tabs and other control characters round-trip no
    better. Printability is the test rather than ASCII: accents, CJK and
    symbols are ordinary names, and a library is not English.
    """
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="name is required")
    if not cleaned.isprintable():
        raise HTTPException(status_code=400, detail="name must be a single line")
    return cleaned


# ---- opt-in paging on the list routes that grow with play (#216) ----
def _page_window(limit: int | None, offset: int | None) -> tuple[int | None, int]:
    """An opt-in `limit`/`offset` pair, range-checked and normalized, or a 400.

    Both are optional and both default to "the whole listing", so a route that
    adopts this returns exactly what it returned before to every caller that
    sends neither -- which is what lets these land without touching `client.ts`.

    What this shares between the routes is the RANGE CHECK and its wording, not
    the meaning of `offset`: it sees two integers and nothing about the listing
    they will be applied to. Two of the three routes read `offset` as "skip that
    many from the front of what I print"; `GET /campaigns/{cid}/chronicle`,
    whose window was already anchored at the newest end, reads it as "skip that
    many of the newest". A helper cannot close that gap -- only the route
    docstrings can, and each says which it is.

    Hand-checked rather than `Query(ge=1)`, which would be shorter and would put
    the bound in the OpenAPI schema: FastAPI answers a violated `ge` with a 422
    and its own error body, and `GET /campaigns/{cid}/scenes/{sid}` -- the
    windowed route these follow -- already answers 400 with this wording. Either
    way one pair of inputs answers inconsistently, so the choice is which pair:
    `ge` would have split `limit=0` from the windowed route it was copied from,
    where this splits `limit=0` from `limit=abc`. A client sends the first by
    arithmetic and the second only by writing a bug.

    Checked BEFORE the route looks for its campaign, matching that same route.
    FastAPI validates the query TYPES ahead of the handler, so `?limit=abc` is a
    422 whatever the campaign is; a hand-written range check that deferred to
    the 404 would make `limit=abc` and `limit=0` answer differently for the same
    request.
    """
    if limit is not None and limit < 1:
        raise HTTPException(status_code=400, detail="limit must be at least 1")
    if offset is not None and offset < 0:
        raise HTTPException(status_code=400, detail="offset must not be negative")
    return limit, offset or 0


def _page_of(rows: list, limit: int | None, offset: int) -> list:
    """`rows` narrowed to the window `_page_window` returned. `limit` None: no cap.

    Takes the built list, because every one of these listings is sorted before
    it is paged and a sort has to see every row. What a page bounds is the
    response -- and, where per-row work outlives the sort (`GET /changes`
    renders a diff per field), whatever the route does after this call.
    """
    return rows[offset:] if limit is None else rows[offset:offset + limit]


# ---- 404 guards and other lookups shared by worlds and campaigns ----
def _world_root_or_404(wid: str):
    if not store.worlds.world_exists(wid):
        raise HTTPException(status_code=404, detail="world not found")
    return store.worlds.world_root(wid)


def _world_char_version_or_404(wid: str, cid: str, vid: str):
    """The world root, once `cid`/`vid` are known to name a real character version.

    Every *write* on the character image surface goes through this (#360).
    `assets.put_image` creates the directory it writes into, so an unchecked id
    turned a typo into `characters/<typo>/assets/<vid>/avatar.png`: bytes no
    listing shows (`list_characters` needs `character.md`, `read_character`
    only reports images for versions it can resolve) and no delete route can
    name, reported to the caller as a successful upload.

    The reads are deliberately left ungated, which is the one place this
    departs from `worlds._world_pc_version_or_404`: they create nothing, they
    already answer "no image" for an id that names nothing, and
    `GET .../images/avatar` is hit once per portrait per rendered grid.

    Honest about its reach: this refuses an id that names nothing *now*. A
    version deleted between the check and the write still strands the upload,
    because no lock spans the two -- it is a guard against a typo, not against
    a race.
    """
    root = _world_root_or_404(wid)
    try:
        store.characters.require_version(root, cid, vid)   # two stats, no read
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return root


def _card_data(card: dict) -> dict:
    """`store.cards.card_data`, under the name the prompt-building routes use.

    Reading `card["data"]` blind is a KeyError on a card that has none -- `{}`
    is what version PUT stores for one -- and a non-object `data` raises one
    attribute access into the prompt template. Either way a 500 before the model
    is ever called, where the templates already render "(none)" for a missing
    field, which is a far better answer for a draft the user edits anyway.
    """
    return store.cards.card_data(card)


def _campaign_root_or_404(cid: str):
    try:
        store.campaigns.ensure_campaign_slim(cid)  # lazy slim of pre-overlay campaigns
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return store.campaigns.campaign_root(cid)


def _content_fields(kind: str, content: dict) -> dict:
    """The declared fields a module content entry carries, checked.

    The check is the point, and it was missing: both instantiate routes handed
    these straight to `create_entity`, while the ordinary create/update routes
    ran `invalid_values` on the identical dict. A pack naming a bare, malformed
    or wrong-kind `holder` therefore instantiated a record that `EntityEditor`
    could never save again -- every later save resends the field and the save
    boundary rejects it. That is the same trap a reclassify used to set, one
    door over.

    A 400 rather than a silent drop: the value came from a file somebody wrote,
    and naming the field is what lets them fix it. Module *pack* validation
    (`store/modules/validate.py`) does not look at these fields at all, so this
    is the only boundary that can say anything, and it checks everything
    `invalid_values` covers -- the location weather fields had the identical
    hole beside the new ref ones.
    """
    fields = {k: content[k] for k in store.entity_schema.field_keys(kind) if k in content}
    bad = store.entity_schema.invalid_values(kind, fields)
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"module content has invalid values for {kind}: {', '.join(bad)}")
    return fields


def _sheet_failure_status(exc: Exception) -> int:
    """The status a rejected sheet write answers with: 409 for a CAS conflict,
    400 for everything else the writer refuses.

    Every sheet route in `mechanics.py` and `worlds.py` splits the two this
    way. The instantiate routes catch the base class in one clause (their
    rollback runs for both), and used to answer 400 for the subclass too --
    an id freshly minted by `create_entity` cannot carry a sheet yet, so the
    conflict is unreachable there today, but the mapping is one rule, not
    one per route.
    """
    return 409 if isinstance(exc, store.sheets.SheetConflict) else 400


def _campaign_routing_meta(cid: str) -> dict:
    """A campaign's frontmatter, for the routing walk -- {} for anything unreadable.

    Never raises. A missing or damaged `campaign.md` is a 404 (or a 500) on the
    routes that need the campaign itself, and every one of them has already said
    so by the time a connection is resolved; re-deciding it here would let a
    stale read turn "this campaign routes elsewhere" into a failed generation.
    """
    if not cid:
        return {}
    try:
        return store.campaigns.read_campaign(cid)["meta"]
    except (store.campaigns.CampaignNotFound, store.locks.StoreBusy,
            OSError, UnicodeDecodeError):
        return {}


def _routed_connection(task: str, cid: str) -> tuple[dict | None, dict]:
    """The connection `task` is routed to, and how that was decided (#142).

    `(None, resolution)` means no route applies and the ACTIVE connection is the
    answer -- which is every task on an install that has never set a route, and
    what makes this change invisible until someone asks for it.

    At most two connection files are read: `exists` caches, and the cascade
    consults at most one id per scope.
    """
    seen: dict[str, dict | None] = {}

    def exists(conn_id: str) -> bool:
        if conn_id not in seen:
            try:
                seen[conn_id] = store.llm_connections.read_connection_raw(conn_id)
            except (store.llm_connections.ConnectionNotFound, store.locks.StoreBusy,
                    OSError, UnicodeDecodeError):
                seen[conn_id] = None
        return seen[conn_id] is not None

    resolution = store.routing.resolve(
        task, campaign_meta=_campaign_routing_meta(cid),
        cfg=store.read_config(), exists=exists)
    return seen.get(resolution["connection_id"]), resolution


def _standing_connection(task: str, cid: str) -> tuple[dict | None, dict, bool]:
    """Where `task` would run, how that was decided, and whether a route chose it.

    `_require_connection`'s resolution half, without its refusals, and the only
    place in `routes/` that reads the active connection. Two callers need
    exactly this and differ only in what they do when it is unusable: the seam
    below refuses, and `_override_connection` still has a question to answer --
    "is this override somewhere the turn would not have gone anyway?" has an
    answer when the standing route is broken, and refusing there would reject
    the reroll that FIXES the session.
    """
    conn, resolution = _routed_connection(task, cid)
    routed = conn is not None
    if conn is None:
        conn = store.llm_connections.get_active()  # routing-ok: this IS the seam
    return conn, resolution, routed


def _usable_or_409(conn: dict | None, resolution: dict, routed: bool) -> dict:
    """The 409 that says why this connection cannot send, or the connection.

    Shared by the seam and by `_override_connection`, which resolves the
    standing route itself to answer a second question and must refuse it on
    exactly the same terms -- including the routed-connection wording below,
    which its own inline copy of these checks used to lose.
    """
    if conn is None:
        raise HTTPException(
            status_code=409, detail={"detail": "No LLM connection selected", "kind": "missing_key"})
    problem = _connection_problem(conn)
    if problem is not None and routed:
        # A ROUTED connection that cannot send is reported, not walked past.
        # The walk skips a route naming a connection that no longer exists --
        # a delete cannot reach into every campaign's frontmatter, so that
        # reference is stale rather than meant. This one is meant: the user
        # pointed this task at this connection and it has no key. Falling back
        # to the active connection would generate a scene on a model they did
        # not choose and never say so.
        raise HTTPException(status_code=409, detail={
            "detail": f"{problem} ({conn.get('name') or conn['id']}, routed for "
                      f"{store.routing.label_for(resolution['route']).lower()})",
            "kind": "missing_key"})
    if problem is not None:
        # Deliberately *before* the facade, so a configured fallback does not
        # rescue this and the 409 still fires. The two look inconsistent — the
        # facade happily falls back on `auth` — and the distinction is real: a
        # key the provider rejected is a runtime failure, worth routing around
        # silently, while no key at all is a setup mistake. Quietly serving it
        # from the fallback would leave someone playing for weeks on the wrong
        # connection, wondering why the model they picked never sounds right.
        raise HTTPException(status_code=409, detail={"detail": problem, "kind": "missing_key"})
    return conn


def _require_connection(task: str = "", cid: str = "") -> dict:
    """The connection this generation runs on, or a 409 saying why there is none.

    `task` is the same string the call site meters under (`store.usage.meter`),
    and `store/routing.py` maps it to a route; `cid` lets a campaign override
    that route. Both default to "unrouted", which resolves to the active
    connection -- but `test_routing_guard.py` fails a call site in `routes/`
    that leaves `task` off, so the default covers callers outside the route
    layer rather than being an option for one inside it.
    """
    return _usable_or_409(*_standing_connection(task, cid))


def _override_connection(body, task: str = "", cid: str = "") -> tuple[dict, bool]:
    """Where ONE call runs, and whether that is somewhere it would not have gone
    anyway (#77).

    Returns `(connection, routed)` — always the resolved connection, never a
    sentinel the caller has to re-resolve:

        conn, routed = _override_connection(body)

    A tuple rather than "None means no override", which is what this was and
    which review broke in two ways at once. It could not say *both* "the caller
    named something" and "that something differs from the standing route", so
    the second meaning silently took over the first; and answering None for a
    named-but-identical connection made the caller re-read the active one,
    opening a window where another tab repointing it between the two reads
    served an explicitly pinned reroll from somewhere else entirely — the exact
    invisible substitution the refusals below exist to prevent.

    `routed` is compared on the EFFECTIVE model, not the stored one. They
    differ for exactly one kind: a `claude` connection with no model configured
    still runs `llm.CLAUDE_DEFAULT_MODEL`, so naming that model — which is what
    the picker now advertises in its placeholder — is naming the standing route,
    not leaving it.

    The two fields compose. `connection_id` alone runs the named connection at
    its own model; `model` alone runs the ACTIVE connection at that model; both
    is the named connection driven at the named model. Neither expresses the
    other's case, which is why there are two: a bare model id cannot reach a
    different provider (the credentials, base URL and prompt post-processing
    that makes possible live on a connection), and a bare connection id cannot
    say "the same provider, its bigger model".

    Four refusals, and which one fires matters to the caller:

    - a model id longer than `alternates.MAX_MODEL_CHARS` is a **400**. Bounded
      where it arrives rather than at each place it is recorded: the same body
      string reaches the alternates sidecar, `prompt_log`'s index (read on every
      listing) and the usage ledger, and the latter two cannot be clamped
      downstream without changing what is actually sent.
    - an id naming no connection is a **400**, not a 404. The routes that take
      an override are scene routes whose 404 already means "this scene is gone"
      and is acted on as such by the client (it stops the turn and re-reads the
      rail); spending the same status on a bad body field would send it
      hunting for a scene that is fine. An id that is not a safe path segment
      lands here too — `llm_connections` refuses it rather than joining it onto
      a path (#240), so it simply names no connection.
    - a connection that cannot send is the same **409/missing_key**
      `_require_connection` raises, because it is the same setup mistake and
      the frontend already routes that kind to the Connections page. Checked
      through the shared `_connection_problem`, so an override is held to
      exactly the standard the active connection is.
    - an override is NOT rescued by falling back to the active connection when
      it is unusable. Quietly serving "reroll this on the local endpoint" from
      OpenRouter is the failure mode the explicit 409 exists to prevent, and it
      would be invisible: the reply reads like any other.

    The active connection is deliberately not required when `connection_id`
    names another one. Rerolling onto a working local endpoint is exactly the
    thing to do while the OpenRouter key is missing, and requiring the active
    one first would refuse the request that fixes the session. A `model`-only
    override does still require it — that is the connection it is overriding.

    What this does NOT change is the configured fallback (#144). An override
    picks which connection is *primary*; the fallback is standing policy about
    what happens when a primary is exhausted, and silently suspending it for
    one call would make a reroll the one turn a rate limit can simply lose.
    `llm._same_route` already drops a fallback that resolves to the override
    itself, so "reroll this on the fallback" does not double up.
    """
    conn_id = (getattr(body, "connection_id", None) or "").strip() if body else ""
    model = (getattr(body, "model", None) or "").strip() if body else ""
    # No early return for "neither field set": that case IS this function's
    # main path with an empty override -- it resolves the standing route,
    # refuses it on the same terms, and compares it against itself to report
    # `routed=False`. A short-circuit through `_require_connection` would only
    # be a second spelling of the same thing, and it took a task this helper
    # does not have.
    if len(model) > store.alternates.MAX_MODEL_CHARS:
        raise HTTPException(
            status_code=400,
            detail="That is too long to be a model id — check it and try again.")
    # The STANDING ROUTE for this task (#142), which is the active connection
    # only when nothing routes it elsewhere. Read ONCE and used for both roles
    # below: resolving a `model`-only override, and deciding whether the result
    # differs from where this turn would have gone. Review caught the first draft of this claiming exactly that while
    # the `model`-only branch went through `_require_connection()`, which reads
    # again — the same two-read window the tuple return was introduced to
    # close, left open in the one branch the fix did not reach. A repoint
    # landing between the two sent "the same provider, its bigger model" to a
    # different provider entirely, and computed `routed` against a connection
    # that never served the turn.
    active, resolution, routed = _standing_connection(task, cid)
    if not conn_id:
        # The seam's refusals, raised against the connection already in hand
        # rather than by reading it a second time -- and through the seam's own
        # helper, so a routed connection that cannot send says so here too.
        conn = _usable_or_409(active, resolution, routed)
    else:
        try:
            conn = store.llm_connections.read_connection_raw(conn_id)
        except store.llm_connections.ConnectionNotFound as exc:
            # Written for the banner it lands in, not for a log. `errorText`
            # renders `detail` verbatim, and the reader's next move is the one
            # worth naming: the connection they picked is gone (deleted in
            # another tab, most likely), and the reroll is still available.
            raise HTTPException(
                status_code=400,
                detail="That connection no longer exists — pick another, "
                       "or reroll on the campaign's.") from exc
        problem = _connection_problem(conn)
        if problem is not None:
            # NAMED, unlike `_require_connection`'s copy of this. There the
            # connection is the one the whole app is using and needs no
            # introduction; here it is one the reader picked for a single
            # reroll, and the bare "OpenRouter key not set" reads as though
            # the connection they have been playing on has broken.
            raise HTTPException(
                status_code=409,
                detail={"detail": f"{conn['name']}: {problem}", "kind": "missing_key"})
    # Last, so it applies to the active connection and to a named one alike.
    # A copy, never a mutation: `conn` is the dict the store handed back, and
    # writing through it would be a per-call override editing shared state.
    resolved = {**conn, "model": model} if model else conn
    same = (active is not None and resolved["id"] == active["id"]
            and effective_model(resolved) == effective_model(active))
    return resolved, not same


def _require_scene(cid: str, sid: str) -> dict:
    try:
        return store.scenes.read_scene(cid, sid)
    except (store.scenes.SceneNotFound, store.campaigns.CampaignNotFound):
        # a scene path is built from campaign_root, so an unusable campaign id
        # surfaces here as CampaignNotFound -- still a 404, not a 500
        raise HTTPException(status_code=404, detail="scene not found")


def computes_only(fn):
    """Mark a campaign-scoped POST that persists nothing.

    POST is not a synonym for write. These routes compute and return -- a
    generated voice anchor for the user to accept or discard, scene
    suggestions, a roll replayed against its stored inputs -- so treating them
    as campaign activity moves a campaign up the recents rail for merely being
    *looked* at.

    Declared at the route rather than as a path list in the middleware,
    deliberately: a path list sits far from the thing it describes and goes
    stale silently, which is how the activity sweep leaked for six rounds. The
    next preview route's author sees this on its neighbours.
    """
    fn.grimoire_computes_only = True
    return fn


def leaves_campaign_unchanged(fn):
    """Mark a campaign-scoped write that does not write to the campaign it names.

    There is exactly one, and its whole design rests on it:
    `POST /campaigns/{cid}/fork` copies `cid` into a *second* campaign and
    touches the source not at all (`store/fork.py`). It is still activity in the
    source -- a fork is something that happened there, and the recents rail
    should say so -- but it must not move the source's revision token (#409),
    which answers "has this campaign changed since I priced against it?". A fork
    that answered yes would break the one composition the token exists for:
    checkpoint this campaign, then advance it against the state the checkpoint
    was taken from.

    A sibling of `computes_only` rather than a second meaning bolted onto it,
    because the two say different things -- "nothing was written" and "something
    was written, elsewhere" -- and the middleware acts on them differently.
    Declared at the route for the same reason: a path list in the middleware
    sits far from what it describes and goes stale in silence.
    """
    fn.grimoire_leaves_campaign = True
    return fn
