"""The continuity capstone's reviewed decisions over plot threads and commitments
(spec §21): aliases (a duplicate merged into its canonical), links (a reviewed
relation between two records), and the read that shows both beside what the
effective view made of them.

The read is pure -- no model, no embedding -- and answers whatever shape the
files are in, because it is what a reader opens to find out what is wrong:
records it cannot interpret are reported with a reason rather than raised on,
and a ledger that will not parse makes existence unknown rather than turning
every merge into it into a "broken" row.

The drivers read (`GET .../continuity/drivers`, spec §14) is
`continuity.drivers.snapshot`, taken without a lock like the suggestion
snapshot it will feed (Decision 14).

The writes are `continuity.review`'s, journalled as ``manual`` and undoable from
the play view's Changes panel. A refusal is a `review.RefusedError`, answered as
``{"kind", "detail", ...}`` so the client can act on the kind.

**The reconciliation sweep** (spec §11.1) is a `background` run on the
campaign subject, kind ``continuity-reconcile``, at most one live per campaign:
`POST .../continuity/reconcile` starts a full sweep (202 and the run to poll),
and End Scene an incremental one through `schedule_reconcile`. The run
discovers, persists what it found, asks the ``continuity-reconcile``
connection about the findings with no proposal -- one call, when a connection
resolves -- and persists the proposals. Discovery and both persists live in
`store.continuity.reconcile`; the routed call, its meter and the run's work
live here, because `test_routing_guard` and `test_usage_guard` read only
`routes/`. Nothing the run does writes a ledger (§11.5).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .. import store
from ..llm import LLMClient
from ..llm_errors import LLMError
from ..store.continuity import doc, drivers, effective, reconcile, review
from . import runs
from .common import (
    _bounded_call,
    _llm_http_error,
    _noting,
    _require_connection,
    _soft_connection,
    computes_only,
    get_llm,
    run_error,
)
from .models import ContinuityAliasCreate, ContinuityLinkCreate

router = APIRouter()
log = logging.getLogger(__name__)

#: Whether End Scene starts the incremental sweep (Decision 20). The suite turns
#: it off unless a test asks (`conftest._auto_reconcile_off_unless_asked`):
#: every `PUT /chronicle` test would otherwise start a lifespan-hosted run that
#: races fakes scripted by call order and writes after the response.
AUTO_RECONCILE = True

#: The review persist's numbers (`scenes._PERSIST_ATTEMPTS`): `campaign_lock`
#: already waits `LOCK_TIMEOUT`, so a retry is a second full wait. Worth it
#: because a sweep's findings are not written down anywhere else.
_PERSIST_ATTEMPTS = 3
_PERSIST_BACKOFF = 0.5

#: The run kind, and the subject's one live background run of it.
_KIND = "continuity-reconcile"
#: A ledger edit's kind -> the ref prefix of the record it moves.
_TOUCHED_PREFIX = {"plot": "thread", "commitment": "commitment"}
_UNDECODABLE = "the reconciliation check returned no readable answer"
#: Every decision word once, in vocabulary order: the log row counts each.
_WORDS = tuple(dict.fromkeys(w for words in reconcile.DECISIONS.values() for w in words))


def _campaign_or_404(cid: str) -> None:
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")


def _refusal(e: review.RefusedError) -> HTTPException:
    return HTTPException(status_code=e.status,
                         detail={"kind": e.kind, "detail": e.detail, **e.extra})


def _text(value) -> str:
    return value if isinstance(value, str) else ""


@router.get("/campaigns/{cid}/continuity")
def get_continuity(cid: str):
    _campaign_or_404(cid)
    with store.locks.best_effort_campaign_lock(cid):
        data = doc.read(cid)
        ledgers = effective.Ledgers.load(cid)
        live = effective.live_canon(cid, ledgers)
        diagnostics = effective.diagnostics(cid, ledgers)
        kept = effective.links(cid, ledgers)
        malformed = doc.malformed(cid)
    dangling = {d["ref"]: d["reason"] for d in diagnostics["dangling_aliases"]}
    aliases = []
    for ref in sorted(k for k in data["aliases"] if isinstance(k, str)):
        record = data["aliases"][ref]
        to = _text(record.get("to")) if isinstance(record, dict) else ""
        meta = record if isinstance(record, dict) else {}
        aliases.append({
            "ref": ref, "to": to, "canonical": live.get(ref, ref),
            "title": review.describe(cid, ref, ledgers),
            "to_title": review.describe(cid, to, ledgers) if to else "",
            "created": _text(meta.get("created")), "source": _text(meta.get("source")),
            "note": _text(meta.get("note")),
            "dangling": ref in dangling, "reason": dangling.get(ref, ""),
        })
    links = [{**link, "a_title": review.describe(cid, link["a"], ledgers),
              "b_title": review.describe(cid, link["b"], ledgers)} for link in kept]
    states = {d["id"]: ("broken", d["reason"]) for d in diagnostics["broken_links"]}
    states.update({d["id"]: ("hidden", d["reason"]) for d in diagnostics["hidden_links"]})
    raw_links = []
    for lid in sorted(k for k in data["links"] if isinstance(k, str)):
        record = data["links"][lid]
        meta = record if isinstance(record, dict) else {}
        state, reason = states.get(lid, ("ok", ""))
        raw_links.append({"id": lid, **{k: _text(meta.get(k)) for k in
                                        ("a", "b", "relation", "scene", "note", "created")},
                          "state": state, "reason": reason})
    suppressions = []
    for fp in sorted(k for k in data["suppressions"] if isinstance(k, str)):
        meta = data["suppressions"][fp]
        meta = meta if isinstance(meta, dict) else {}
        refs = meta.get("refs")
        suppressions.append({
            "fingerprint": fp, "kind": _text(meta.get("kind")),
            "refs": [r for r in refs if isinstance(r, str)] if isinstance(refs, list) else [],
            "decision": _text(meta.get("decision")), "created": _text(meta.get("created"))})
    return {"aliases": aliases, "links": links, "raw_links": raw_links,
            "suppressions": suppressions, "diagnostics": diagnostics,
            "malformed": malformed, "unreadable": diagnostics["unreadable"],
            "matching": drivers.matching()}


@router.get("/campaigns/{cid}/continuity/drivers")
def get_drivers(cid: str, offscreen: bool = False):
    _campaign_or_404(cid)
    return drivers.snapshot(cid, offscreen=offscreen)


@router.post("/campaigns/{cid}/continuity/aliases")
def post_alias(cid: str, body: ContinuityAliasCreate):
    _campaign_or_404(cid)
    try:
        return review.create_alias(cid, body.ref, body.to, replace=bool(body.replace),
                                   accept_status_change=bool(body.accept_status_change),
                                   note=body.note or "")
    except review.RefusedError as e:
        raise _refusal(e) from e


@router.delete("/campaigns/{cid}/continuity/aliases")
def delete_alias(cid: str, ref: str):
    """By query (`?ref=`), not by path: refs contain ``:`` and a model-written
    plot id may contain ``/``."""
    _campaign_or_404(cid)
    try:
        return review.remove_alias(cid, ref)
    except review.RefusedError as e:
        raise _refusal(e) from e


@router.post("/campaigns/{cid}/continuity/links")
def post_link(cid: str, body: ContinuityLinkCreate):
    _campaign_or_404(cid)
    try:
        return review.create_link(cid, body.a, body.b, body.relation,
                                  scene=body.scene or "", note=body.note or "")
    except review.RefusedError as e:
        raise _refusal(e) from e


@router.delete("/campaigns/{cid}/continuity/links/{lid}")
def delete_link(cid: str, lid: str):
    _campaign_or_404(cid)
    try:
        return review.remove_link(cid, lid)
    except review.RefusedError as e:
        raise _refusal(e) from e


# ------------------------------------------------------ reconciliation sweep


def _persist_failed(run, kind: str, exc: Exception, status: int) -> dict:
    log.warning("continuity sweep %s could not persist -- %s", run.id, type(exc).__name__)
    return {"written": False,
            "error": {"kind": kind, "detail": str(exc) or type(exc).__name__,
                      "status": status}}


async def _persist(run, call: Callable[[], dict]) -> dict:
    """One persist, off the event loop, waiting out a contended campaign lock
    (`StoreBusy`) up to `_PERSIST_ATTEMPTS` times. A final `StoreBusy` or an
    `OSError` comes back as ``{"written": False, "error": {...}}`` rather than
    raising, so the run can say it failed and which sweep it was."""
    left = _PERSIST_ATTEMPTS
    while True:
        left -= 1
        try:
            return await run_in_threadpool(call)
        except store.locks.StoreBusy as exc:
            if left <= 0:
                return _persist_failed(run, "busy", exc, 409)
            await asyncio.sleep(_PERSIST_BACKOFF)
        except OSError as exc:
            return _persist_failed(run, "io", exc, 500)


def _blank_result(full: bool, sweep: reconcile.Sweep) -> dict:
    return {"sweep": "full" if full else "incremental", "matching": sweep.matching,
            "embedding": sweep.embedding, "llm": "off", "reason": "", "candidates": 0,
            "adjudicated": 0, "pairs_capped": sweep.pairs_capped, "superseded": False,
            "follow_on": False}


def _failed(result: dict, error: dict) -> dict:
    """A failed outcome. Its `error` carries the sweep too: a polling client
    keeps only `error` for a run that did not land, and a Refresh that adopted
    an incremental sweep needs to know to ask again (Decision 24)."""
    return {"state": "failed", "error": {**error, "sweep": result["sweep"]}, "result": result}


def _stopped(persisted: dict, result: dict) -> dict | None:
    """The outcome a persist ends the pass with, or None to carry on: a
    failed write fails the run, a stopped or forgotten run is ``cancelled``, and
    a superseded run or a deleted campaign lands with nothing more to do."""
    if persisted.get("error"):
        return _failed(result, persisted["error"])
    result.update(candidates=persisted["candidates"], superseded=persisted["superseded"])
    if persisted["cancelled"]:
        return {"state": "cancelled", "result": result}
    if persisted["superseded"] or persisted["gone"]:
        return {"state": "landed", "result": result}
    return None


async def _adjudicate(run, cid: str, client: LLMClient, sweep: reconcile.Sweep,
                      result: dict, stillborn: Callable[[], bool]) -> tuple[dict, dict]:
    """Steps 4-6: the one routed call over `select`'s findings, and persist 2.
    ``(outcome, proposals)``. No connection is ``llm: "off"`` with the reason;
    nothing to ask is ``"skipped"``. A provider failure or an undecodable
    reply fails the run, and persist 1's findings stand (§26)."""
    conn, why = await run_in_threadpool(
        _soft_connection, lambda: _require_connection("continuity-reconcile", cid))
    selected = await run_in_threadpool(reconcile.select, cid, sweep)
    if conn is None:
        result.update(llm="off", reason=why)
        return {"state": "landed", "result": result}, {}
    if not selected:
        result["llm"] = "skipped"
        return {"state": "landed", "result": result}, {}
    payload = await run_in_threadpool(reconcile.build_payload, cid, selected)
    try:
        with store.usage.meter("continuity-reconcile", campaign=cid) as m:
            text = await _bounded_call(
                client.complete(reconcile.build_prompt(payload), conn, m.usage),
                on_timeout=_noting(client, conn, m.usage))
    except LLMError as exc:
        result["llm"] = "failed"
        return _failed(result, run_error(_llm_http_error(exc))), {}
    proposals = reconcile.parse_output(text, payload)
    if proposals is None:
        result["llm"] = "failed"
        return _failed(result, {"kind": "undecodable", "detail": _UNDECODABLE,
                                "status": 502}), {}
    result.update(llm="ok", adjudicated=len(proposals))
    second = await _persist(run, lambda: reconcile.persist_proposals(
        cid, sweep, proposals, stillborn=stillborn))
    return _stopped(second, result) or {"state": "landed", "result": result}, proposals


def _log_pass(cid: str, sweep: reconcile.Sweep, result: dict, proposals: dict) -> None:
    """Step 7: counts and modes only (§29) -- never a title, a beat, a reason or
    a prompt. The model's reply is captured at Debug level by the adapters,
    under the Settings disclosure; this row is what an info-level log keeps."""
    vias = [record["signals"].get("via") for record in sweep.discovered.values()]
    words = Counter(p.get("decision") for p in proposals.values())
    decided: dict[str, Any] = {w: words.get(w, 0) for w in _WORDS}
    store.logs.record(
        "info", __name__, "continuity reconcile", kind="continuity-reconcile",
        campaign=cid, sweep=result["sweep"], matching=result["matching"],
        embedding=result["embedding"], embedding_error=sweep.embedding_error,
        llm=result["llm"], candidates=result["candidates"],
        deterministic=sum(via != "semantic" for via in vias),
        semantic=sum(via == "semantic" for via in vias),
        adjudicated=result["adjudicated"], pairs_capped=result["pairs_capped"],
        superseded=result["superseded"], **decided)


async def _sweep_pass(run, cid: str, client: LLMClient, *, full: bool,
                      touched: tuple[str, ...]) -> dict:
    """Steps 1-7 of one pass (§11.1), as the run's outcome."""
    sweep = await run_in_threadpool(reconcile.discover, cid,
                                    stamp=reconcile.generation(run.id),
                                    full=full, touched=touched)
    result = _blank_result(full, sweep)

    def stillborn() -> bool:
        return run.cancel_requested or run.forgotten

    first = await _persist(run, lambda: reconcile.persist_found(cid, sweep,
                                                                stillborn=stillborn))
    proposals: dict = {}
    outcome = _stopped(first, result)
    if outcome is None:
        outcome, proposals = await _adjudicate(run, cid, client, sweep, result, stillborn)
    if outcome["state"] != "cancelled":
        _log_pass(cid, sweep, result, proposals)
    return outcome


def _take_touched(app, cid: str) -> set[str]:
    return app.state.runs.take_touched(runs.campaign_subject(cid))


async def _passes(app, run, cid: str, client: LLMClient, *, full: bool,
                  touched: tuple[str, ...]) -> dict:
    """The pass, then one more for the refs adopters left (Decision 6).

    An incremental pass starts by taking what earlier adopters left. A full one
    leaves them: discovery re-checks nothing as touched on a full sweep, so
    taking them first would lose them. After a landed pass -- unless the run
    was stopped or its campaign deleted -- whatever the set holds gets one
    incremental pass inside this run, since a live run can neither adopt a
    successor nor be told it finished. Its result keeps the first pass's
    `sweep` (a Refresh that absorbed an End Scene still reads ``full``) with
    `follow_on` and the last pass's counts."""
    refs = set(touched) if full else set(touched) | _take_touched(app, cid)
    outcome = await _sweep_pass(run, cid, client, full=full, touched=tuple(sorted(refs)))
    if outcome["state"] != "landed" or run.cancel_requested or run.forgotten:
        return outcome
    more = _take_touched(app, cid)
    if not more:
        return outcome
    first = outcome["result"]["sweep"]
    follow = await _sweep_pass(run, cid, client, full=False, touched=tuple(sorted(more)))
    out = {**follow, "result": {**(follow.get("result") or {}), "sweep": first,
                                "follow_on": True}}
    if follow.get("error"):
        out["error"] = {**follow["error"], "sweep": first}
    return out


async def _reconcile_work(app, run, cid: str, client: LLMClient, *, full: bool,
                          touched: tuple[str, ...]) -> dict:
    """The run's work: `_passes`, with an unexpected failure reported as the
    runner would report it plus the sweep it was, for `_failed`'s reason."""
    try:
        return await _passes(app, run, cid, client, full=full, touched=touched)
    except Exception as exc:  # the runner's own boundary, plus the sweep a polling client needs
        log.exception("continuity sweep %s failed", run.id)
        return {"state": "failed",
                "error": {"kind": "run_failed", "detail": str(exc) or type(exc).__name__,
                          "status": 500, "sweep": "full" if full else "incremental"}}


def start_reconcile(app, cid: str, client: LLMClient, *, full: bool,
                    attempt_id: str | None = None,
                    touched: tuple[str, ...] = ()) -> tuple[runs.Run, bool] | None:
    """Reserve the campaign's reconcile and start it, or adopt the live one.

    ``(run, fresh)``, or None while the store is moving. A fresh run is handed
    to the runner inside `runs.reservation`, so a start that raises leaves no
    run ``running``; an adopted run already has its producer and is not
    started again."""
    reserved = runs.reserve_campaign_background(app, cid, _KIND, attempt_id)
    if reserved is None:
        return None
    run, fresh = reserved
    if fresh:
        with runs.reservation(app, run):
            runs.start_computing(app, run, lambda: _reconcile_work(
                app, run, cid, client, full=full, touched=touched))
    return run, fresh


def _touched_ref(edit, slot) -> str:
    """The ref one applied plot or commitment edit moved, or ``""``. The
    target the commit journal says it wrote wins over the staged one (a row
    staged as new can be reallocated to a free id)."""
    if not (isinstance(slot, dict) and slot.get("state") == "applied"
            and isinstance(edit, dict) and isinstance(edit.get("kind"), str)
            and isinstance(edit.get("id"), str)):
        return ""
    prefix = _TOUCHED_PREFIX.get(edit["kind"])
    target = slot.get("target") if isinstance(slot.get("target"), dict) else edit.get("target")
    pid = target.get("id") if isinstance(target, dict) else None
    return f"{prefix}:{pid}" if prefix and isinstance(pid, str) and pid else ""


def _touched_refs(edits, progress) -> set[str]:
    """The records a commit's applied plot and commitment edits moved.

    Walked by POSITION through the commit journal's ``edits`` slots, as
    `absorb.apply_edits` writes them -- ids are client-supplied and need not be
    unique, so the applied-id list cannot say which of two edits landed. An
    edit or slot of the wrong shape is skipped: the save has already landed."""
    slots = progress.get("edits") if isinstance(progress, dict) else None
    if not isinstance(slots, dict) or not isinstance(edits, list):
        return set()
    return {ref for i, edit in enumerate(edits) if (ref := _touched_ref(edit, slots.get(str(i))))}


def schedule_reconcile(app, cid: str, client: LLMClient, *, edits: list | None = None,
                       progress: dict | None = None, touched: tuple[str, ...] = ()) -> None:
    """End Scene's incremental sweep, or nothing at all. Never raises (§11.1:
    the save response never depends on it).

    A trigger that adopts a live sweep leaves its refs on the registry for that
    run to take after its pass (Decision 6). A run reserved fresh whose start
    raised has already been released by `runs.reservation`; a reservation that
    raised left nothing to release; an adopted run is someone else's."""
    if not AUTO_RECONCILE:
        return
    try:
        refs = set(touched) | _touched_refs(edits or [], progress or {})
        reserved = start_reconcile(app, cid, client, full=False, touched=tuple(sorted(refs)))
        if reserved is not None and not reserved[1] and refs:
            app.state.runs.pend_touched(runs.campaign_subject(cid), refs)
    except Exception as exc:  # noqa: BLE001 -- the save already landed; a sweep is never its failure
        log.warning("could not start the continuity sweep for %s -- %s", cid,
                    type(exc).__name__)


@router.post("/campaigns/{cid}/continuity/reconcile", status_code=202)
@computes_only  # 202 before anything is written; each persist stamps for itself
def post_reconcile(cid: str, request: Request, client: LLMClient = Depends(get_llm),
                   x_grimoire_attempt: str | None = Header(default=None)):
    """Refresh: a full sweep (§11.1), answered 202 with the run to poll. It works
    with no connection, and the run's result says ``llm: "off"``. A sweep
    already live is returned rather than doubled, and this request's attempt id
    is recorded on it, so a lost 202 can be re-found."""
    _campaign_or_404(cid)
    reserved = start_reconcile(request.app, cid, client, full=True,
                               attempt_id=x_grimoire_attempt or uuid.uuid4().hex)
    if reserved is None:
        raise HTTPException(status_code=409, detail={
            "kind": "busy", "detail": "the storage location is being changed; try again"})
    return {"run": runs.run_payload(reserved[0])}
