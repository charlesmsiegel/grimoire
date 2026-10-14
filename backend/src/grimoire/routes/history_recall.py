"""One narrator history phase, outside the locks that freeze a turn (09-S3).

Theory: selected evidence belongs to a compose, then to its frozen snapshot.
Round drivers cache it by round id; NPCs, appended roll continuations and snapshot
replays never gather. An outside-lock replay peek can stale: only the locked
_prepare decision chooses whether to compose or replay, and replay ignores the
supplied evidence. The synchronous bridge tests the calling worker's lock before
crossing to the loop, whose own thread cannot observe that ownership. Errors
(including a closing portal after a player's append) degrade to empty evidence;
cancellation propagates. File stages may overrun the deadline; later network
stages must use its remaining monotonic time. No campaign lock is acquired here.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from functools import partial

from .. import inference as operations
from .. import store
from ..llm_errors import LLMError
from ..store import history_rerank as kit
from ..store.context import history as history_section
from ..store.context import layout
from ..store.history import budget, model, query, settings
from ..store.history import threads as history_threads
from . import common, decision_capture, streaming

HISTORY_PHASE_SECONDS = 4
HISTORY_PHASE_PLANNED_SECONDS = 12
RERANK_TOP = 8
RERANK_CEILING = 4
_log = logging.getLogger("grimoire")
_QUERY_POST = object()


def _error(cid, sid):
    # No exception text: a corrupt historical file can carry private prose.
    _log.warning("History retrieval failed for campaign %s scene %s", cid, sid)
    return model.Evidence(coverage=model.Coverage("error", semantic="off"))


def _on(cfg):
    """The settings when recall is on and its section is in the layout, else
    None: the one off check, so off reads nothing else (9.1)."""
    history_settings = settings.read(cfg)
    if history_settings is None or "history_recall" not in layout.enabled_ids(("history_recall",)):
        return None
    return history_settings


def enabled() -> bool:
    """Whether a turn would gather at all -- for a caller with reads of its
    own to skip (the round driver's replay peek), in a worker."""
    return _on(store.config.read_config()) is not None


def _prepare(cid, sid, seed, resolved):
    cfg = store.config.read_config()
    history_settings = _on(cfg)
    if history_settings is None:
        return None
    cap = budget.ceiling(cfg, resolved)
    if not cap:
        return None
    prepared = query.prepare(cid, sid, seed=seed)
    # The private legacy archive window includes director notes/image URLs;
    # the query's cleaned scan cannot stand in for it or be sent as its proxy.
    omissions = history_section.summary_omissions(cid, sid, prepared.archive_scan_text)
    # Cost follows the same player/director-note boundary as generation. The
    # query's scan boundary may end in an assistant reply on a legacy retry.
    answering = streaming._answering_post(store.scenes.read_scene(cid, sid)["messages"])
    return replace(prepared.query, turn_index=answering), cap, omissions, history_settings.rerank


def _kit_candidates(candidates, items):
    chosen = []
    for candidate in candidates[:RERANK_TOP]:
        item = items.get(candidate.scene.key)
        if item is None:
            continue
        # Reuse the authoritative header template, retaining only the first
        # prompt excerpt, or the summary fallback. Omitted summaries never
        # become a second way to leak archive prose into a decide prompt.
        payload = replace(item, excerpts=item.excerpts[:1],
                          summary="" if item.excerpts else item.summary)
        chosen.append(kit.Candidate(candidate.scene.key, budget.render_item(payload), item.date))
    return chosen


async def _rerank(cid, sid, q, candidates, items, *, client, deadline, run_id="", ranked=False):
    """09-S5: an opinion can move answered slots, never remove evidence.

    Ungraded slots keep their RRF positions. The kit and decide chain own
    unread replies, refusals and fallbacks; this adapter keeps only their
    ordinal answer and backend, never a distribution or a fused confidence.
    Rank extends the same slot rule with ascending positions and pointwise
    predicates, so an unplaced native candidate is simply ungraded.
    """
    if time.monotonic() >= deadline:
        return candidates, "skipped:deadline"
    try:
        chosen = await history_threads.run(_kit_candidates, candidates, items)
        if not chosen:
            return candidates, "skipped:empty"
        resolved, _why, kind = await history_threads.run(
            common._soft_resolved,
            lambda: common.require_inference("history-rerank", cid, operation="decide"))
        if resolved is None:
            return candidates, "skipped:" + (kind or "unresolvable")
        left = min(RERANK_CEILING, deadline - time.monotonic())
        if left <= 0:
            return candidates, "skipped:deadline"
        around = common.plan_deadline(left)
        ranked = ranked and len(chosen) > 1
        items_to_ask = await history_threads.run(
            lambda: (kit.build_items_ranked("\n".join(q.texts), chosen),) if ranked
            else kit.build_items("\n".join(q.texts), chosen))
        async with decision_capture.capturing(cid, sid, "history-rerank") as scope:
            decision = await operations.decide(
                "history-rerank", items_to_ask, client=client, resolved=resolved,
                campaign=cid, scene=sid, post=q.turn_index, run_id=run_id,
                capture=scope.hook(), around=around)
            grades = kit.grades_of(chosen, decision.items) if not ranked else {}
            positions = kit.positions_of(chosen, decision.items[0]) if ranked else {}
            signals = {}
            for i, candidate in enumerate(chosen):
                result = decision.items[0 if ranked else i]
                grade = grades[candidate.ref].level if not ranked else None
                signals[candidate.ref] = model.RerankSignal(
                    level=kit.LEVELS[grade] if grade is not None else None,
                    position=positions.get(candidate.ref), backend=result.backend)
            scope.note("", "post", q.turn_index)
            scope.note("", "rerank", {key: {"level": s.level, "position": s.position,
                                           "backend": s.backend} for key, s in signals.items()})
        ordered = [replace(c, rounds=(*c.rounds[:-1], replace(c.rounds[-1],
                           rerank=signals[c.scene.key]))) if c.scene.key in signals else c
                   for c in candidates]
        slots = [i for i, c in enumerate(ordered) if c.scene.key in signals
                 and (signals[c.scene.key].level is not None
                      or signals[c.scene.key].position is not None)]
        graded = [ordered[i] for i in slots]
        graded.sort(key=lambda c: (signals[c.scene.key].position if ranked
                                  else -kit.LEVELS.index(signals[c.scene.key].level)))
        for slot, candidate in zip(slots, graded, strict=True):
            ordered[slot] = candidate
        if slots:
            return tuple(ordered), "ok"
        # Nothing graded -- every answer abstained, was refused or unread, or
        # every call failed: RRF order stands, and the row says why.
        failures = [call.error_kind for call in decision.calls if call.error_kind]
        return tuple(ordered), "skipped:" + (failures[-1] if failures else "unanswered")
    except LLMError as exc:
        return candidates, "skipped:" + exc.kind
    except Exception:  # noqa: BLE001 -- an optional opinion never costs admitted file evidence
        return candidates, "skipped:error"


async def gather(app, cid, sid, *, seed, resolved, llm_client, embed_client, deadline=None,
                 post=_QUERY_POST, run_id=""):
    """Gather completed evidence before a turn's locked snapshot decision."""
    deadline = time.monotonic() + HISTORY_PHASE_SECONDS if deadline is None else deadline
    try:
        prepared = await history_threads.run(_prepare, cid, sid, seed, resolved)
        if prepared is None:
            return model.Evidence()
        q, cap, omissions, rerank_on = prepared
        if post is not _QUERY_POST:
            q = replace(q, turn_index=post)
        return await store.history.retrieve(
            cid, sid, q, ceiling=cap, embed_client=embed_client, deadline=deadline,
            summary_omissions=omissions, run_id=run_id,
            rerank=partial(_rerank, cid, sid, client=llm_client, deadline=deadline,
                           run_id=run_id) if rerank_on else None,
        )
    except Exception:  # noqa: BLE001 -- optional history cannot strand an appended player post
        return _error(cid, sid)


def gather_sync(app, cid, sid, *, seed, resolved, llm_client, embed_client, deadline=None,
                post=_QUERY_POST, run_id=""):
    """Bridge a def route; ownership is checked in its worker before the portal."""
    if store.locks.holds_campaign(cid):
        # Nothing was retrieved, which is not "nothing else exists": a planner
        # reading `error` never plans on it, and never mistakes it for exhausted.
        return model.Evidence(coverage=model.Coverage("error", semantic="skipped:locked"))
    try:
        return app.state.run_portal.call(partial(
            gather, app, cid, sid, seed=seed, resolved=resolved,
            llm_client=llm_client, embed_client=embed_client, deadline=deadline,
            post=post, run_id=run_id,
        ))
    except Exception:  # noqa: BLE001 -- shutdown can close (or cancel) the portal after the player's append
        # A worker thread is never itself cancelled: a portal future cancelled
        # at shutdown is the closing portal of spec 10.1, not this request's
        # cancellation, so it degrades like any other portal failure.
        return _error(cid, sid)
