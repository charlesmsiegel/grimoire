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

The Story Graph read (`GET .../continuity/graph`, spec §19.7) is
`continuity.graph.build`: one uncapped payload, best-effort locked inside the
store function, calendar plugin code outside the hold.

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
from ..store.continuity import (
    candidates,
    doc,
    drivers,
    effective,
    graph,
    pending,
    reconcile,
    review,
)
from . import ledger as ledger_routes
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
from .models import ContinuityAliasCreate, ContinuityDismiss, ContinuityLinkCreate

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
_MALFORMED = "continuity.json is malformed; nothing this sweep found was saved"
#: The same refusal at persist 2, when persist 1's findings already stand.
_MALFORMED_PROPOSALS = "continuity.json is malformed; the model's suggestions were not saved"
#: The same refusal before a follow-on pass's persist 1: the first pass's stand.
_MALFORMED_FOLLOW_ON = "continuity.json is malformed; nothing the follow-on pass found was saved"
#: Every decision word once, in vocabulary order: the log row counts each.
_WORDS = tuple(dict.fromkeys(w for words in reconcile.DECISIONS.values() for w in words))


def _campaign_or_404(cid: str) -> None:
    if not store.campaigns.campaign_exists(cid):
        raise HTTPException(status_code=404, detail="campaign not found")


def _refusal(e: review.RefusedError) -> HTTPException:
    return HTTPException(status_code=e.status,
                         detail={"kind": e.kind, "detail": e.detail, **e.extra})


def _stale_refusal(cid: str, e: review.RefusedError) -> HTTPException:
    """`_refusal`, with a ``stale_candidate``'s rows given their pressure.

    §22 step 5's rows are the read's rows (§12.2), pressure included, so the
    detail laid over from them still says what is overdue. The refusal is built
    under the campaign lock, and pressure can run calendar-plugin code that
    §11.1 keeps out of every hold, so it joins here, after the hold is gone --
    and only on a 409, so an apply that lands never pays for it."""
    rows = e.extra.get("current", {}).get("records") if e.kind == "stale_candidate" else None
    if rows:
        by_ref = reconcile.pressure_by_ref(cid)
        for row in rows:
            if not row.get("gone"):              # as `pending.rows` leaves one
                row["pressure"] = by_ref.get(row["ref"])
    return _refusal(e)


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _suppresses(current: pending.Current | None, fp: str, kind: str, refs: list[str]) -> bool:
    """Whether a stored dismissal still names the records as they are (§12.7):
    its fingerprint recomputed now. One for records that have since changed
    suppresses nothing, and a record of the wrong shape is never live."""
    if current is None or kind not in candidates.KINDS or not refs:
        return False
    try:
        return pending.fingerprint(current, kind, refs) == fp
    except (AttributeError, IndexError, TypeError, ValueError):
        return False


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
        # Built only when there is a dismissal to judge, and from the ledgers
        # already loaded: a read with none costs what it always did.
        current = (pending.Current.load(cid, ledgers=ledgers)
                   if data["suppressions"] else None)
    dangling = {d["ref"]: d["reason"] for d in diagnostics["dangling_aliases"]}
    aliases = []
    for ref in sorted(k for k in data["aliases"] if isinstance(k, str)):
        record = data["aliases"][ref]
        to = _text(record.get("to")) if isinstance(record, dict) else ""
        meta = record if isinstance(record, dict) else {}
        aliases.append({
            "ref": ref, "to": to, "canonical": live.get(ref, ref),
            "title": review.reader_name(cid, ref, ledgers),
            "to_title": review.reader_name(cid, to, ledgers) if to else "",
            "created": _text(meta.get("created")), "source": _text(meta.get("source")),
            "note": _text(meta.get("note")),
            "dangling": ref in dangling, "reason": dangling.get(ref, ""),
        })
    # Every title this read hands a review surface is `reader_name`'s (§12.8,
    # §30): a record that is gone, untitled, or in a ledger that will not read
    # is named by its kind in words, never by its ref -- only the server can
    # tell an untitled record from a missing one.
    links = [{**link, "a_title": review.reader_name(cid, link["a"], ledgers),
              "b_title": review.reader_name(cid, link["b"], ledgers)} for link in kept]
    states = {d["id"]: ("broken", d["reason"]) for d in diagnostics["broken_links"]}
    states.update({d["id"]: ("hidden", d["reason"]) for d in diagnostics["hidden_links"]})
    raw_links = []
    for lid in sorted(k for k in data["links"] if isinstance(k, str)):
        record = data["links"][lid]
        meta = record if isinstance(record, dict) else {}
        state, reason = states.get(lid, ("ok", ""))
        row = {k: _text(meta.get(k)) for k in ("a", "b", "relation", "scene", "note", "created")}
        # Titled as effective links are (§30): a broken link's ends are the
        # ones a reader most needs named.
        raw_links.append({"id": lid, **row, "state": state, "reason": reason,
                          "a_title": review.reader_name(cid, row["a"], ledgers)
                          if row["a"] else "",
                          "b_title": review.reader_name(cid, row["b"], ledgers)
                          if row["b"] else ""})
    suppressions = []
    for fp in sorted(k for k in data["suppressions"] if isinstance(k, str)):
        meta = data["suppressions"][fp]
        meta = meta if isinstance(meta, dict) else {}
        refs = meta.get("refs")
        refs = [r for r in refs if isinstance(r, str)] if isinstance(refs, list) else []
        kind = _text(meta.get("kind"))
        suppressions.append({
            "fingerprint": fp, "kind": kind, "refs": refs,
            "decision": _text(meta.get("decision")), "created": _text(meta.get("created")),
            "live": _suppresses(current, fp, kind, refs),
            "titles": [review.reader_name(cid, ref, ledgers) for ref in refs]})
    return {"aliases": aliases, "links": links, "raw_links": raw_links,
            "suppressions": suppressions, "diagnostics": diagnostics,
            "malformed": malformed, "unreadable": diagnostics["unreadable"],
            "matching": drivers.matching()}


@router.get("/campaigns/{cid}/continuity/drivers")
def get_drivers(cid: str, offscreen: bool = False):
    _campaign_or_404(cid)
    return drivers.snapshot(cid, offscreen=offscreen)


@router.get("/campaigns/{cid}/continuity/graph")
def get_graph(cid: str):
    _campaign_or_404(cid)
    return graph.build(cid)


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


# --------------------------------------------------------- the review read
#
# `GET .../continuity/candidates` (§12.2): every cached finding joined, at read
# time, to the records it names as they are now. Pure -- no model, no
# embedding, no run, no write. The one definition of what a finding means now
# is `pending`'s, so this read, Todo, apply and both persists cannot disagree.


def _scene_rows(cid: str) -> list[dict] | None:
    """The scenes that exist, or None when the list cannot be read (nothing can
    then be said about a proposal's evidence either way)."""
    try:
        return store.scenes.list_scenes(cid)
    except OSError:
        return None


def _strings(value) -> list[str]:
    return [v for v in value if isinstance(v, str) and v] if isinstance(value, list) else []


def _stale_reason(verdict: str, record: dict, scene_ids: set[str] | None) -> str | None:
    """Why a visible finding cannot be acted on as it stands: its records moved
    (``records``), or its proposal cites a scene that is gone (``evidence``) --
    in the order apply checks them, so the read and apply's 409 agree."""
    if verdict == "stale":
        return "records"
    if scene_ids is not None and not pending.evidence_ok(record["proposal"], scene_ids):
        return "evidence"
    return None


def _candidate(current: pending.Current, key: str, record: dict, verdict: str,
               titles: dict[str, str], scene_ids: set[str] | None, pressure: dict) -> dict:
    reason = _stale_reason(verdict, record, scene_ids)
    return {"id": key, "kind": record["kind"], "group": pending.GROUP_OF[record["kind"]],
            "refs": list(record["refs"]), "fingerprint": record["fingerprint"],
            "stale": reason is not None, "stale_reason": reason,
            "signals": record["signals"], "proposal": record["proposal"],
            "created": record["created"],
            "records": pending.rows(current, record["refs"],
                                    scene_title=lambda sid: titles.get(sid, ""),
                                    pressure=pressure)}


def _actor_name(cid: str, actor: str) -> str:
    """The actor's name, or "" when nobody can say: `actor_name` answers an
    unresolved token with its bare id, which is not a name."""
    _kind, _, aid = actor.partition(":")
    try:
        name = store.relationships.actor_name(cid, actor) or ""
    except Exception:  # noqa: BLE001 -- a label must never be the thing that fails the read
        return ""
    return "" if name in (aid, actor) else name


def _mentioned(found: list[dict]) -> tuple[set[str], set[str], set[str]]:
    """The scene ids, actor refs and event refs the listed findings mention."""
    scenes: set[str] = set()
    actors: set[str] = set()
    events: set[str] = set()
    for c in found:
        for row in c["records"]:
            scenes.add(row["last_scene"]["id"])
            scenes.update(b["scene"] for b in row["beats"])
        scenes.update(_strings((c["proposal"] or {}).get("evidence_scenes")))
        scenes.update(_strings(c["signals"].get("shared_scenes")))
        actors.update(_strings(c["signals"].get("shared_actors")))
        events.update(_strings(c["signals"].get("shared_anchors")))
    scenes.discard("")
    return scenes, actors, events


def _names(cid: str, current: pending.Current, found: list[dict],
           titles: dict[str, str]) -> dict[str, str]:
    """A display name for every scene, actor and event the response mentions
    (Decision 23), so the detail never shows a scene filename or an actor ref.
    One it cannot name -- a scene that is gone or has no title, an actor nobody
    resolves, an event `describe` could only answer with its ref -- gets no
    entry, so the detail leaves it out rather than show the id as its name."""
    scenes, actors, events = _mentioned(found)
    named = [(sid, titles.get(sid, "")) for sid in sorted(scenes)]
    named += [(a, _actor_name(cid, a)) for a in sorted(actors)]
    named += [(e, review.describe(cid, e, current.ledgers)) for e in sorted(events)]
    return {key: name for key, name in named if name and name != key}


def _live_reconcile(request: Request, cid: str) -> dict | None:
    """The newest running sweep on the campaign, so the section can follow one
    already running when it loads (End Scene's, typically)."""
    live = [r for r in request.app.state.runs.for_subject(runs.campaign_subject(cid))
            if r.kind == _KIND and r.state == "running"]
    return runs.run_payload(live[-1]) if live else None


def _diagnostics(cid: str, ledgers: effective.Ledgers) -> dict:
    return {**effective.diagnostics(cid, ledgers), "malformed": doc.malformed(cid),
            "cache_malformed": candidates.malformed(cid)}


@router.get("/campaigns/{cid}/continuity/candidates")
def get_candidates(cid: str, request: Request):
    _campaign_or_404(cid)
    cache = candidates.read(cid)
    if not cache["records"]:
        # Nothing cached -- no sweep yet, one that found nothing, or a cache
        # that will not parse -- so there is nothing to join (Todo's cost rule,
        # `pending.findings`). The Ledger mounts this read for every section
        # and re-reads it on every write, so it answers without the pressure
        # pass, the current view or the scene list: `scenes` and `names` exist
        # to label findings, and only an open finding's detail reads them.
        # What the top level says with no findings still answers.
        with store.locks.best_effort_campaign_lock(cid):
            ledgers = effective.Ledgers.load(cid)
        return {"generated": cache["generated"], "matching": drivers.matching(),
                "diagnostics": _diagnostics(cid, ledgers),
                "run": _live_reconcile(request, cid), "names": {}, "scenes": [],
                "candidates": []}
    # Outside the hold: the scene list, and pressure, which can run user
    # calendar-plugin code that §11.1 keeps out of every lock hold -- a slow
    # plugin must not hold up a save or an apply behind a read.
    rows = _scene_rows(cid)
    # An untitled scene has no name, not its id for one (Decision 23).
    titles = {r["id"]: store.fieldtext.text(r.get("title")) for r in rows or []}
    scene_ids = None if rows is None else set(titles)
    pressure = reconcile.pressure_by_ref(cid)
    with store.locks.best_effort_campaign_lock(cid):
        current = pending.Current.load(cid)
        found = [_candidate(current, key, record, verdict, titles, scene_ids, pressure)
                 for key, record, verdict in pending.findings(cid, current)
                 if verdict in pending.VISIBLE]
    diagnostics = _diagnostics(cid, current.ledgers)
    scenes = [{"id": r["id"], "title": titles[r["id"]]}
              for r in reversed(reconcile.play_order(rows or []))]
    return {"generated": candidates.read(cid)["generated"], "matching": drivers.matching(),
            "diagnostics": diagnostics, "run": _live_reconcile(request, cid),
            "names": _names(cid, current, found, titles), "scenes": scenes,
            "candidates": found}


# --------------------------------------------------- acting on one finding
#
# Apply, dismiss and restore (§12.3, §12.4, §12.7, §22). Each runs in one
# campaign-lock hold; every refusal is a `review.RefusedError`. A write that
# lands part-way answers 500 naming the parts that landed -- and stamps the
# write token itself, because the activity middleware stamps only a 2xx.


def _closure_label(current: pending.Current, ref: str, status: str) -> str:
    return f"{pending.label(current, [ref])} — {status}"


def _write_status(cid: str, checked: dict, plan: dict) -> None:
    """A closure's or a resolution's status, on the canonical physical record,
    with the optional beat under the reader's evidence scene and `last_scene`
    never moved backwards (§12.4)."""
    ref = checked["record"]["refs"][0]
    move = (ledger_routes.move_thread if ref.startswith("thread:")
            else ledger_routes.move_commitment)
    move(cid, plan["target"], status=plan["status"], beat=plan["beat"],
         scene=plan["scene"] or None, keep_later_scene=True,
         label=_closure_label(checked["current"], ref, plan["status"]))


def _copy_due(cid: str, checked: dict, plan: dict) -> None:
    """The explicit due copy (§5.1): its own journalled ledger row, before the
    merge, through the ledger's helper."""
    canonical = plan["alias"]["to"]
    source = review.reader_name(cid, plan["alias"]["ref"], checked["current"].ledgers)
    move_label = (f"{pending.label(checked['current'], [canonical])} — "
                  f"due copied from {source}")
    ledger_routes.move_commitment(cid, plan["target"], due=plan["copy_due"],
                                  label=move_label)


def _apply_plan(cid: str, key: str, checked: dict, plan: dict, landed: list[str]) -> dict:
    """Decision 16's write order: status, the due copy, the alias or link, then
    the cache cleanup (`review.settle`). `landed` grows as each part lands, so
    a failure part-way can say what did."""
    out: dict = {}
    if "status" in plan:
        _write_status(cid, checked, plan)
        landed.append("status")
    if "copy_due" in plan:
        _copy_due(cid, checked, plan)
        landed.append("due")
    if "alias" in plan:
        alias = plan["alias"]
        made = review.create_alias(cid, alias["ref"], alias["to"],
                                   accept_status_change=alias["accept"], source="review",
                                   replace_broken=True)
        landed.append("alias")
        out["alias"] = made["alias"]
        if "dues" in made:
            out["dues"] = made["dues"]
    if "link" in plan:
        link = plan["link"]
        out["link"] = review.create_link(cid, link["a"], link["b"], link["relation"],
                                         scene=plan["scene"])["link"]
        landed.append("link")
    landed.extend(review.settle(cid, key, checked["record"], checked["fingerprint"],
                                plan.get("decision")))
    return out


def _expected(body: dict) -> str | None:
    expect = body.get("expect_fingerprint")
    if expect is not None and not isinstance(expect, str):
        raise review.RefusedError(400, "bad_body", "'expect_fingerprint' must be a str")
    return expect


def _partial_apply(cid: str, exc: Exception, landed: list[str]) -> HTTPException:
    """The 500 for an apply an I/O error stopped part-way (§22 "partial
    writes"), naming the parts that landed -- a `keep_open`'s settle reports
    its own. The token is bumped here: the write may have landed, and a non-2xx
    answer is invisible to the activity middleware."""
    if isinstance(exc, review.PartialSettleError):
        landed.extend(part for part in exc.landed if part not in landed)
    store.revision.bump(cid)
    return HTTPException(status_code=500, detail={
        "kind": "partial_apply", "landed": landed,
        "detail": "the finding was only partly applied: "
                  f"{', '.join(landed) or 'nothing'} landed ({type(exc).__name__}); "
                  "each part that did can be undone"})


@router.post("/campaigns/{cid}/continuity/candidates/{candidate_id}/apply")
def post_apply(cid: str, candidate_id: str, body: dict):
    """Act on one finding (§21's flat body, a `dict` because a key is named
    ``from`` -- Decision 15). Every part is validated before the first write."""
    _campaign_or_404(cid)
    landed: list[str] = []
    try:
        with store.locks.campaign_lock(cid):
            checked = review.check_candidate(cid, candidate_id, _expected(body))
            plan = review.plan_apply(cid, checked, body)
            out = _apply_plan(cid, candidate_id, checked, plan, landed)
    except review.RefusedError as e:
        raise _stale_refusal(cid, e) from e
    except doc.ContinuityError as e:
        # continuity.json refused a write under the hold that checked it well
        # formed. Nothing landed is a plain refusal; otherwise it is partial.
        if not landed:
            raise _refusal(review.RefusedError(409, "malformed", str(e))) from e
        raise _partial_apply(cid, e, landed) from e
    except OSError as e:
        raise _partial_apply(cid, e, landed) from e
    return {"ok": True, "applied": landed, **out}


@router.post("/campaigns/{cid}/continuity/candidates/{candidate_id}/dismiss")
def post_dismiss(cid: str, candidate_id: str, body: ContinuityDismiss):
    """Set a finding aside (Decision 17): a suppression keyed by its current
    fingerprint, then the cache drop."""
    _campaign_or_404(cid)
    try:
        return review.dismiss(cid, candidate_id, body.decision or "dismiss",
                              body.expect_fingerprint)
    except review.RefusedError as e:
        raise _stale_refusal(cid, e) from e
    except review.PartialSettleError as e:
        # The suppression landed (so the finding is hidden) and the drop did
        # not: a write answered non-2xx, which the middleware cannot see.
        store.revision.bump(cid)
        raise HTTPException(status_code=500, detail={
            "kind": "partial_dismiss", "landed": e.landed,
            "detail": "the finding was set aside but could not be removed from the "
                      "review cache; the next refresh clears it"}) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail={
            "kind": "io", "detail": f"the finding could not be set aside ({type(e).__name__})"},
        ) from e


@router.delete("/campaigns/{cid}/continuity/suppressions/{fingerprint}")
def delete_suppression(cid: str, fingerprint: str):
    """Restore a dismissed finding (§12.7); it returns at the next refresh."""
    _campaign_or_404(cid)
    try:
        return review.restore_suppression(cid, fingerprint)
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
            "continuity": sweep.continuity, "follow_on": False}


def _failed(result: dict, error: dict) -> dict:
    """A failed outcome. Its `error` carries the sweep too: a polling client
    keeps only `error` for a run that did not land, and a Refresh that adopted
    an incremental sweep needs to know to ask again (Decision 24)."""
    return {"state": "failed", "error": {**error, "sweep": result["sweep"]}, "result": result}


def _malformed(result: dict, detail: str = _MALFORMED) -> dict:
    """continuity.json is malformed, so the cache was left as it is (Decision
    2): the persist saved nothing, and the run says so rather than landing."""
    result["continuity"] = "malformed"
    return _failed(result, {"kind": "malformed", "detail": detail, "status": 409})


def _stopped(persisted: dict, result: dict, malformed: str = _MALFORMED) -> dict | None:
    """The outcome a persist ends the pass with, or None to carry on: a
    failed write fails the run, a stopped or forgotten run is ``cancelled``, a
    persist refused by a malformed continuity.json fails the run (`malformed`
    is what that refusal says it lost), and a superseded run or a deleted
    campaign lands with nothing more to do."""
    if persisted.get("error"):
        return _failed(result, persisted["error"])
    result.update(candidates=persisted["candidates"], superseded=persisted["superseded"])
    if persisted["cancelled"]:
        return {"state": "cancelled", "result": result}
    if persisted.get("continuity") == "malformed":
        return _malformed(result, malformed)
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
    return (_stopped(second, result, _MALFORMED_PROPOSALS)
            or {"state": "landed", "result": result}), proposals


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
        llm=result["llm"], continuity=result["continuity"], candidates=result["candidates"],
        deterministic=sum(via != "semantic" for via in vias),
        semantic=sum(via == "semantic" for via in vias),
        adjudicated=result["adjudicated"], pairs_capped=result["pairs_capped"],
        superseded=result["superseded"], **decided)


async def _sweep_pass(run, cid: str, client: LLMClient, *, full: bool,
                      touched: tuple[str, ...], progress: dict,
                      budget: reconcile.EmbedBudget,
                      malformed: str = _MALFORMED) -> dict:
    """Steps 1-7 of one pass (§11.1), as the run's outcome. Sets
    ``progress["saved"]`` once persist 1 has landed: from then on the section
    lists what this sweep found, whatever the rest of the run does (§26).
    `malformed` is what a refusal before persist 1 says it lost. `budget` is
    the run's embedding allowance, which every pass draws on (§9.4)."""
    sweep = await run_in_threadpool(reconcile.discover, cid,
                                    stamp=reconcile.generation(run.id),
                                    full=full, touched=touched, budget=budget)
    result = _blank_result(full, sweep)

    def stillborn() -> bool:
        return run.cancel_requested or run.forgotten

    proposals: dict = {}
    if sweep.continuity == "malformed":
        outcome: dict | None = _malformed(result, malformed)
    else:
        first = await _persist(run, lambda: reconcile.persist_found(cid, sweep,
                                                                    stillborn=stillborn))
        outcome = _stopped(first, result, malformed)
    if outcome is None:
        progress["saved"] = True
        outcome, proposals = await _adjudicate(run, cid, client, sweep, result, stillborn)
    if outcome["state"] != "cancelled":
        _log_pass(cid, sweep, result, proposals)
    return outcome


def _take_touched(app, cid: str) -> set[str]:
    return app.state.runs.take_touched(runs.campaign_subject(cid))


async def _passes(app, run, cid: str, client: LLMClient, *, full: bool,
                  touched: tuple[str, ...], progress: dict) -> dict:
    """The pass, then one more for the refs adopters left (Decision 6).

    An incremental pass starts by taking what earlier adopters left. A full one
    leaves them: discovery re-checks nothing as touched on a full sweep, so
    taking them first would lose them. After a landed pass -- unless the run
    was stopped or its campaign deleted -- whatever the set holds gets one
    incremental pass inside this run, since a live run can neither adopt a
    successor nor be told it finished. Its result keeps the first pass's
    `sweep` (a Refresh that absorbed an End Scene still reads ``full``) with
    `follow_on` and the last pass's counts. A failure from then on sets
    ``progress["follow_on"]``: the first pass's findings stand whatever it
    was, so the failure is the follow-on pass's alone. Both passes draw on one
    `reconcile.EmbedBudget`: `RECONCILE_WARM_LIMIT` and the embedding window
    are the run's (§9.4), so the follow-on embeds only what the first left."""
    refs = set(touched) if full else set(touched) | _take_touched(app, cid)
    budget = reconcile.EmbedBudget()
    outcome = await _sweep_pass(run, cid, client, full=full, touched=tuple(sorted(refs)),
                                progress=progress, budget=budget)
    if outcome["state"] != "landed" or run.cancel_requested or run.forgotten:
        return outcome
    more = _take_touched(app, cid)
    if not more:
        return outcome
    first = outcome["result"]["sweep"]
    progress["follow_on"] = True
    follow = await _sweep_pass(run, cid, client, full=False, touched=tuple(sorted(more)),
                               progress=progress, budget=budget,
                               malformed=_MALFORMED_FOLLOW_ON)
    out = {**follow, "result": {**(follow.get("result") or {}), "sweep": first,
                                "follow_on": True}}
    if follow.get("error"):
        out["error"] = {**follow["error"], "sweep": first}
    return out


async def _reconcile_work(app, run, cid: str, client: LLMClient, *, full: bool,
                          touched: tuple[str, ...]) -> dict:
    """The run's work: `_passes`, with an unexpected failure reported as the
    runner would report it plus the sweep it was, for `_failed`'s reason.

    Every failed outcome's `error` also carries `saved`: whether persist 1
    landed, so the findings listed are this sweep's. The kind alone cannot say
    it -- `busy`, `io` and `malformed` each come from either persist -- and the
    Refresh note must not claim findings a refused persist never wrote. And
    `follow_on`: whether it was the follow-on pass that failed, after the first
    pass landed -- then nothing about the failure is the first pass's."""
    progress = {"saved": False, "follow_on": False}
    try:
        outcome = await _passes(app, run, cid, client, full=full, touched=touched,
                                progress=progress)
    except Exception as exc:  # the runner's own boundary, plus the sweep a polling client needs
        log.exception("continuity sweep %s failed", run.id)
        outcome = {"state": "failed",
                   "error": {"kind": "run_failed", "detail": str(exc) or type(exc).__name__,
                             "status": 500, "sweep": "full" if full else "incremental"}}
    if outcome["state"] == "failed":
        outcome["error"] = {**outcome["error"], "saved": progress["saved"],
                            "follow_on": progress["follow_on"]}
    return outcome


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
    raised left nothing to release; an adopted run is someone else's. A
    reservation the store move refused comes back as None rather than raising,
    and is logged as the skipped sweep it is (§26)."""
    if not AUTO_RECONCILE:
        return
    try:
        refs = set(touched) | _touched_refs(edits or [], progress or {})
        reserved = start_reconcile(app, cid, client, full=False, touched=tuple(sorted(refs)))
        if reserved is None:
            log.warning("skipped the continuity sweep for %s -- the storage location "
                        "is being changed", cid)
        elif not reserved[1] and refs:
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
