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

Roadmap 10 plans inside the same phase: on thin or empty coverage (or a forced
gather) `plan_and_repair` asks one structured planner call, merges one planned
round, and -- only on an `insufficient` sufficiency check -- one repair plan,
never another. Every step draws from the phase deadline; every failure returns
at worst the first retrieval, and the `PlanTrace` rides the evidence.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from functools import partial

from .. import inference as operations
from .. import store
from ..llm_errors import LLMError
from ..store import history_rerank as kit
from ..store.context import epistemic, layout
from ..store.context import history as history_section
from ..store.continuity import involvement
from ..store.history import budget, model, query, retrieval, settings, sufficiency
from ..store.history import plan as history_plan
from ..store.history import threads as history_threads
from ..store.scenes import identity as scenes_identity
from . import common, decision_capture, streaming

HISTORY_PHASE_SECONDS = 4
#: The phase's deadline when planning may run (`history_plan` on, or a forced
#: gather): planning has no deadline of its own, and every step draws
#: `min(own ceiling, remaining)` from this one (roadmap 10, section 8).
HISTORY_PHASE_PLANNED_SECONDS = 12
RERANK_TOP = 8
RERANK_CEILING = 4
#: One short structured reply (10 section 5.3), never `llm_call_budget`, whose
#: 0 means no ceiling at all.
PLAN_CEILING = 6
#: The planner's output cap (01f-C1's per-call `max_tokens`): a maximal valid
#: plan is a few hundred tokens; the rest is a reasoning model's allowance.
PLAN_MAX_TOKENS = 1024
#: Steps one planned phase may take, counted before each (10-C2): a plan, a
#: check and the repair plan; one more with `final_check`. The 01d hop sits
#: inside a check, not beside it.
MAX_PLAN_STEPS = 3
#: The verdicts the free gate plans on (09-C2). `exhausted` and `error` never
#: plan: nothing more exists, or retrieval itself failed.
PLAN_ON = frozenset({"thin", "empty"})
PLAN_TASK = "history-query-plan"
#: One facade call of the sufficiency check (10-C3): a one-item, no-rationale
#: decision. The whole check -- its fallback stage, its re-sends -- starts no
#: call once the turn-phase deadline has passed.
CHECK_CEILING = 4
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


def _actor(actor_ref):
    """The NPC a step is written for, None for the narrator."""
    return None if actor_ref in (None, "grimoire", "narrator") else actor_ref


def enabled() -> bool:
    """Whether a turn would gather at all -- for a caller with reads of its
    own to skip (the round driver's replay peek), in a worker."""
    return _on(store.config.read_config()) is not None


_Catalog = query.Catalog


@dataclass(frozen=True)
class Turn:
    """What 09's query step already read, for the planner (10 section 5.1):
    the turn's query (its window, its FILTERED seeds and its post), the
    catalog behind them, and the retrieval settings the planned rounds
    share with the first."""

    query: model.Query
    catalog: _Catalog
    depth: int
    omissions: dict
    repair: bool = True


@dataclass(frozen=True)
class _Gathering:
    query: model.Query
    cap: int
    omissions: dict
    rerank: bool
    plan: str
    turn: Turn


def _prepare(cid, sid, seed, resolved, actor_ref=None):
    cfg = store.config.read_config()
    history_settings = _on(cfg)
    if history_settings is None:
        return None
    cap = budget.ceiling(cfg, resolved)
    if not cap:
        return None
    prepared = query.prepare(cid, sid, seed=seed, actor_ref=actor_ref)
    # The private legacy archive window includes director notes/image URLs;
    # the query's cleaned scan cannot stand in for it or be sent as its proxy.
    # The archive and recap are the narrator's: an actor omits nothing (11-S3).
    omissions = ({} if _actor(actor_ref) else
                 history_section.summary_omissions(cid, sid, prepared.archive_scan_text))
    # Cost follows the same player/director-note boundary as generation. The
    # query's scan boundary may end in an assistant reply on a legacy retry.
    answering = streaming._answering_post(store.scenes.read_scene(cid, sid)["messages"])
    q = replace(prepared.query, turn_index=answering)
    return _Gathering(q, cap, omissions, history_settings.rerank, history_settings.plan,
                     Turn(q, prepared.catalog, history_settings.depth, omissions,
                          history_settings.repair))


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


def _slot_sorted(ordered, signals, ranked):
    """09-S5's slot rule: graded candidates are sorted among the slots graded
    candidates hold; every ungraded slot keeps its RRF position. Returns the
    order and whether any slot was graded."""
    ordered = list(ordered)
    slots = [i for i, c in enumerate(ordered) if c.scene.key in signals
             and (signals[c.scene.key].level is not None
                  or signals[c.scene.key].position is not None)]
    graded = [ordered[i] for i in slots]
    graded.sort(key=lambda c: (signals[c.scene.key].position if ranked
                              else -kit.LEVELS.index(signals[c.scene.key].level)))
    for slot, candidate in zip(slots, graded, strict=True):
        ordered[slot] = candidate
    return ordered, bool(slots)


def _reapplied(ordered):
    """The first retrieval's rerank opinion, applied to a merged order (10
    section 3): a planned turn pays one rerank at most, so the grades `E0`
    paid for stand over the final merge, and a scene only a planned round
    found is ungraded and keeps its RRF slot. Pure: no call."""
    signals = {}
    for c in ordered:
        found = next((r.rerank for r in c.rounds if r.rerank is not None
                      and (r.rerank.level is not None or r.rerank.position is not None)), None)
        if found is not None:
            signals[c.scene.key] = found
    if not signals:
        return list(ordered)
    ranked = any(s.position is not None for s in signals.values())
    if ranked:
        signals = {k: v for k, v in signals.items() if v.position is not None}
    else:
        signals = {k: v for k, v in signals.items() if v.level in kit.LEVELS}
    return _slot_sorted(ordered, signals, ranked)[0]


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
        ordered, moved = _slot_sorted(ordered, signals, ranked)
        if moved:
            return tuple(ordered), "ok"
        # Nothing graded -- every answer abstained, was refused or unread, or
        # every call failed: RRF order stands, and the row says why.
        failures = [call.error_kind for call in decision.calls if call.error_kind]
        return tuple(ordered), "skipped:" + (failures[-1] if failures else "unanswered")
    except LLMError as exc:
        return candidates, "skipped:" + exc.kind
    except Exception:  # noqa: BLE001 -- an optional opinion never costs admitted file evidence
        return candidates, "skipped:error"


def _ms(started):
    return int((time.monotonic() - started) * 1000)


def _trace(terminal, first, **kwargs):
    return model.PlanTrace(terminal, e0=model.RoundRecord.of(first.items), **kwargs)


def _involving_cast(cid, turn):
    """The seeded threads and commitments a present actor stood in, which
    the planner's input lists first. An unreadable ledger costs only the
    ordering."""
    threads = tuple(r for r in turn.query.subjects
                    if r.partition(":")[0] in ("thread", "commitment"))
    if not threads:
        return frozenset()
    try:
        touched = involvement.of(cid, threads)
    except Exception:  # noqa: BLE001 -- advisory ordering only
        return frozenset()
    present = turn.catalog.present
    return frozenset(ref for ref, value in touched.items()
                     if present.intersection(str(a) for a in value.get("actors", [])))


async def _plan_step(cid, sid, turn, evidence, *, client, resolved, deadline, tried=None,
                     offered_before=None, run_id=""):
    """One planner call (10 section 5.3): `(plan, why, step, offered)`. The
    input is built off the loop, the call is metered on the post it served
    and bounded by `min(PLAN_CEILING, remaining)` with `_noting`, and the
    reply is read after the meter's block, so a parse never changes the row."""
    started = time.monotonic()

    def build():
        # The query's perspective is carried, never assumed: `build_input`
        # refuses an actor's until 11-C1 says what that actor may know.
        inp = history_plan.build_input(
            turn.query.texts[0] if turn.query.texts else "", turn.catalog, turn.query.subjects,
            evidence, depth=turn.depth,
            tried=history_plan.tried_of(tried, offered_before) if tried is not None else None,
            perspective=turn.query.perspective, involved=_involving_cast(cid, turn))
        offered = history_plan.offered(inp)
        return history_plan.build_messages(inp), offered, history_plan.schema_for(
            offered, turn.depth)

    messages, offered, schema = await history_threads.run(build)
    left = min(PLAN_CEILING, deadline - time.monotonic())
    if left <= 0:
        return None, "deadline", model.Step(PLAN_TASK, "skipped:deadline", _ms(started)), offered
    rows = 0
    try:
        with store.usage.meter("history-query-plan", campaign=cid, scene=sid,
                               post=turn.query.turn_index, run_id=run_id) as m:
            try:
                text = await common._bounded_call(
                    operations.generate("history-query-plan", messages, client=client, resolved=resolved,
                                        usage=m.usage, schema=schema,
                                        max_tokens=PLAN_MAX_TOKENS, stream=False),
                    ceiling=left, on_timeout=common._noting(client, resolved, m.usage))
            finally:
                rows = 1 if m.usage.get("attempts") else 0
    except LLMError as exc:
        return None, exc.kind, model.Step(PLAN_TASK, exc.kind, _ms(started), rows), offered
    got, why = history_plan.read(text, offered, turn.depth, perspective=turn.query.perspective)
    return got, why, model.Step(PLAN_TASK, why, _ms(started), rows), offered


async def _planned_round(cid, sid, turn, evidence, got, *, ceiling, embed_client, deadline,
                         run_id):
    """One planned round: 09's own retrieval of the plan's `Query`, no rerank,
    merged into the evidence so far by scene identity (09-C1). The merged
    selection is at most the setting's depth under the same ceiling: a round
    changes which scenes the section carries, never how many or how large."""
    q = history_plan.query_of(got, turn.query.turn_index)
    found = await store.history.retrieve(
        cid, sid, q, ceiling=ceiling, embed_client=embed_client, deadline=deadline,
        summary_omissions=turn.omissions, run_id=run_id, rerank=None)
    anchor = replace(q, terms=tuple(dict.fromkeys((*turn.query.terms, *q.terms))),
                     max_scenes=None)
    expand = retrieval.expander(cid, sid, anchor, ceiling, summary_omissions=turn.omissions)
    merged = await history_threads.run(lambda: store.history.merge(
        evidence, found, ceiling=ceiling, expand=expand, rerank=_reapplied))
    # The merge builds a fresh coverage; the first retrieval's rerank -- the
    # one opinion `_reapplied` carries -- keeps its status on the result.
    merged = replace(merged, coverage=replace(merged.coverage, rerank=evidence.coverage.rerank))
    return merged, model.RoundRecord.of(merged.items, questions=got.questions,
                                        terms=got.terms, subjects=got.subjects), \
        found.coverage.semantic


class _Phase:
    """One planned phase's bookkeeping: the steps taken, the plans read and
    the trace each exit builds."""

    def __init__(self, first, trigger, final_check):
        self.first, self.trigger = first, trigger
        self.steps: list[model.Step] = []
        self.plans: list[dict] = []
        self.rounds: list[model.RoundRecord] = []
        self.semantic: list[str] = [first.coverage.semantic]
        self.cap = MAX_PLAN_STEPS + (1 if final_check else 0)
        self.before = {i.scene.key for i in first.items}

    def capped(self):
        return len(self.steps) >= self.cap

    def done(self, terminal, evidence, **kwargs):
        gain = tuple(i.scene.key for i in evidence.items if i.scene.key not in self.before)
        return evidence, _trace(terminal, self.first, trigger=self.trigger,
                                rounds=tuple(self.rounds), steps=tuple(self.steps),
                                plans=tuple(self.plans), gain=gain,
                                semantic=tuple(self.semantic), **kwargs)


async def _planner(cid, phase, deadline):
    """The planner's resolution, or None and the `skipped:` state that says
    why no plan is asked: a spent deadline, an unresolvable route, the cap."""
    if time.monotonic() >= deadline:
        return None, "skipped:deadline"
    resolved, _why, kind = await history_threads.run(
        common._soft_resolved, lambda: common.require_inference("history-query-plan", cid))
    if resolved is None:
        return None, "skipped:" + (kind or "unresolvable")
    if phase.capped():
        return None, "skipped:step_cap"
    return resolved, ""


async def plan_and_repair(app, cid, sid, turn, first, *, client, ceiling, deadline, force=False,
                          final_check=False, embed_client=None, run_id=""):
    """10-C2: plan when 09's first retrieval is thin or empty (or `force`),
    retrieve once more with the plan, and merge; on an `insufficient` check,
    take ONE repair plan -- never another after it. Never raises into the
    turn; the worst case returns `first` unchanged. `(evidence, PlanTrace)`.

    `turn` is a `Turn`. Every step takes `min(own ceiling, remaining)` of
    the turn-phase `deadline`; at most `MAX_PLAN_STEPS` (one more with
    `final_check`) are taken, counted before each. No campaign lock is held
    or taken here."""
    verdict = first.coverage.verdict
    if verdict == "error" or (verdict not in PLAN_ON and not force):
        # The free gate: nothing more exists, retrieval failed, or it found enough.
        return first, _trace("not_run:" + verdict, first)
    phase = _Phase(first, "forced" if force else verdict, final_check)
    resolved, skipped = await _planner(cid, phase, deadline)
    if resolved is None:
        return phase.done(skipped, first)
    got, why, step, offered = await _plan_step(cid, sid, turn, first, client=client,
                                               resolved=resolved, deadline=deadline,
                                               run_id=run_id)
    phase.steps.append(step)
    if why == "deadline":
        return phase.done("skipped:deadline", first)
    if got is None:
        return phase.done("unusable", first)
    phase.plans.append(history_plan.projection(got, offered.names))
    if not got.needs_history:
        return phase.done("declined", first)
    if time.monotonic() >= deadline:
        # A round's file stages run to completion once started, so a round
        # begun past the deadline would overrun it by the archive's read.
        return phase.done("skipped:deadline", first)
    evidence, record, status = await _planned_round(cid, sid, turn, first, got, ceiling=ceiling,
                                            embed_client=embed_client, deadline=deadline,
                                            run_id=run_id)
    phase.rounds.append(record)
    phase.semantic.append(status)
    if not turn.repair or phase.capped():
        return phase.done("planned", evidence)
    return await _repair(phase, cid, sid, turn, evidence, got, offered, client=client,
                         resolved=resolved, ceiling=ceiling, deadline=deadline,
                         final_check=final_check, embed_client=embed_client, run_id=run_id)


async def _repair(phase, cid, sid, turn, evidence, got, offered, *, client, resolved, ceiling,
                  deadline, final_check, embed_client, run_id):
    """The check before the repair hop, and the hop itself (10-C2, 10-C3)."""
    window = turn.query.texts[0] if turn.query.texts else ""
    said, source, step = await _sufficiency(cid, sid, window, evidence, client=client,
                                            deadline=deadline, post=turn.query.turn_index,
                                            run_id=run_id)
    phase.steps.append(step)
    if said != sufficiency.INSUFFICIENT:
        # Sufficient, or unknown: a non-answer never buys the repair hop.
        return phase.done("planned", evidence, sufficiency=said, sufficiency_source=source)
    asked = {"sufficiency": said, "sufficiency_source": source}
    if phase.capped() or time.monotonic() >= deadline:
        return phase.done("unrepaired", evidence, **asked)
    repair, why, step, offered = await _plan_step(cid, sid, turn, evidence, client=client,
                                                  resolved=resolved, deadline=deadline,
                                                  tried=got, offered_before=offered,
                                                  run_id=run_id)
    phase.steps.append(step)
    if repair is None or why == "deadline":
        return phase.done("unrepaired", evidence, **asked)
    phase.plans.append(history_plan.projection(repair, offered.names))
    if not repair.needs_history or repair.adds_nothing_to(got):
        return phase.done("repair_redundant", evidence, **asked)
    if time.monotonic() >= deadline:
        return phase.done("unrepaired", evidence, **asked)
    evidence, record, status = await _planned_round(cid, sid, turn, evidence, repair, ceiling=ceiling,
                                            embed_client=embed_client, deadline=deadline,
                                            run_id=run_id)
    phase.rounds.append(record)
    phase.semantic.append(status)
    # Never another plan. One more check only when the caller will act on its
    # answer (12's RP mode): it is what `cheap_retrieval_failed` reads.
    if not final_check or phase.capped():
        return phase.done("repaired", evidence)
    said, source, step = await _sufficiency(cid, sid, window, evidence, client=client,
                                            deadline=deadline, post=turn.query.turn_index,
                                            run_id=run_id)
    phase.steps.append(step)
    return phase.done("repaired", evidence, sufficiency=said, sufficiency_source=source,
                      cheap_retrieval_failed=said == sufficiency.INSUFFICIENT)


def _check_around(deadline, started=None):
    """`decide`'s `around` for the sufficiency check: each facade call bounded
    by `min(CHECK_CEILING, remaining)` of the turn-phase deadline, and a call
    that would start after it refused unsent (`DeadlineRefused`, which is the
    app's clock, never the provider failing). `started`, when given, counts
    the calls it let start: each runs under its own meter and files a row,
    which is how a decide that raises still reports what it filed."""
    async def bound(call, _holder):
        left = deadline - time.monotonic()
        if left <= 0:
            close = getattr(call, "close", None)
            if close is not None:
                close()   # a coroutine never awaited would warn at collection
            raise common.DeadlineRefused(
                "timeout", "the history phase's deadline passed before this call")
        if started is not None:
            started.append(1)
        return await common._bounded_call(call, ceiling=min(CHECK_CEILING, left))
    return bound


async def _sufficiency(cid, sid, window, evidence, *, client, deadline, post=None, run_id=""):
    """10-C3's call: `(verdict, source, step)`. Resolved in a worker, bounded
    by `_check_around`, captured to the prompt log (01b), and mapped by
    `sufficiency.verdict_of`; every failure is `unknown`."""
    started = time.monotonic()

    def unknown(outcome, rows=0):
        return (sufficiency.UNKNOWN, sufficiency.UNKNOWN,
                model.Step(sufficiency.TASK, outcome, _ms(started), rows))
    if time.monotonic() >= deadline:
        return unknown("skipped:deadline")
    try:
        resolved, _why, kind = await history_threads.run(
            common._soft_resolved,
            lambda: common.require_inference("history-sufficiency", cid, operation="decide"))
        if resolved is None:
            return unknown("skipped:" + (kind or "unresolvable"))
        if time.monotonic() >= deadline:
            return unknown("skipped:deadline")
        item = await history_threads.run(sufficiency.item, window, evidence)
        sent: list[int] = []
        async with decision_capture.capturing(cid, sid, "history-sufficiency") as scope:
            decision = await operations.decide(
                "history-sufficiency", [item], client=client, resolved=resolved,
                campaign=cid, scene=sid, post=post, run_id=run_id, capture=scope.hook(),
                around=_check_around(deadline, sent))
            said, source = sufficiency.verdict_of(decision)
            scope.note("", "post", post)
            scope.note("", "sufficiency", said)
    except LLMError as exc:
        # The chain's meters filed a row per call it started before raising.
        return unknown(exc.kind, len(sent))
    rows = len(decision.usage)
    if said == sufficiency.UNKNOWN:
        failures = [call.error_kind for call in decision.calls if call.error_kind]
        return unknown(failures[-1] if failures else "unanswered", rows)
    return said, source, model.Step(sufficiency.TASK, said, _ms(started), rows)


async def sufficient(app, cid, sid, turn_window, evidence, *, client, deadline, post=None,
                     run_id=""):
    """10-C3, public: whether `evidence` holds what the next reply needs --
    `sufficient`, `insufficient` or `unknown` (a non-answer, a failed call,
    an unresolvable route or a spent deadline; never `insufficient`). 12's RP
    entry asks it of the final evidence when the trace says `not_asked`."""
    said, _source, _step = await _sufficiency(cid, sid, turn_window, evidence, client=client,
                                              deadline=deadline, post=post, run_id=run_id)
    return said


async def _gather(cid, sid, *, seed, resolved, llm_client, embed_client, deadline, post,
                  run_id, actor_ref, force=False, final_check=False):
    """The evidence, or None when recall is off for this turn.

    With `history_plan` on (or `force`, for evals and a later explicit
    control) the phase's deadline is `HISTORY_PHASE_PLANNED_SECONDS`, and a
    narrator step's thin or empty first retrieval is handed to
    `plan_and_repair` (10). An actor's step never plans: the planner is
    shown the narrator's seeds, and 10 plans for the narrator only."""
    started = time.monotonic()
    planning = False
    try:
        prepared = await history_threads.run(_prepare, cid, sid, seed, resolved, actor_ref)
        if prepared is None:
            return None
        q = prepared.query
        if post is not _QUERY_POST:
            q = replace(q, turn_index=post)
        planning = (prepared.plan == "auto" or force) and not _actor(actor_ref)
        if deadline is None:
            deadline = started + (HISTORY_PHASE_PLANNED_SECONDS if planning
                                  else HISTORY_PHASE_SECONDS)
        first = await store.history.retrieve(
            cid, sid, q, ceiling=prepared.cap, embed_client=embed_client, deadline=deadline,
            summary_omissions=prepared.omissions, run_id=run_id,
            rerank=partial(_rerank, cid, sid, client=llm_client, deadline=deadline,
                           run_id=run_id) if prepared.rerank and not _actor(actor_ref) else None,
        )
    except Exception:  # noqa: BLE001 -- optional history cannot strand an appended player post
        failed = _error(cid, sid)
        # A planned phase still says why it never planned: retrieval failed.
        return replace(failed, plan=_trace("not_run:error", failed)) if planning else failed
    if not planning:
        return first
    try:
        evidence, trace = await plan_and_repair(
            None, cid, sid, replace(prepared.turn, query=q), first, client=llm_client,
            ceiling=prepared.cap, deadline=deadline, force=force, final_check=final_check,
            embed_client=embed_client, run_id=run_id)
    except Exception:  # noqa: BLE001 -- planning is optional: at worst, what 09 alone found
        # Ids only: a plan's text is model-written and stays out of the log.
        _log.warning("History planning failed for campaign %s scene %s", cid, sid)
        evidence, trace = first, _trace("skipped:error", first)
    return replace(evidence, plan=trace)


async def gather(app, cid, sid, *, seed, resolved, llm_client, embed_client, deadline=None,
                 post=_QUERY_POST, run_id="", actor_ref=None, force=False, final_check=False):
    """Gather completed evidence before a turn's locked snapshot decision.

    `actor_ref` names the step's perspective (11-S3): an NPC's query is its
    own, and is never reranked -- a rerank grades rendered items, summaries
    included, which is the narrator's material steering an actor's pick.
    `force` and `final_check` are `plan_and_repair`'s (10)."""
    evidence = await _gather(cid, sid, seed=seed, resolved=resolved, llm_client=llm_client,
                             embed_client=embed_client, deadline=deadline, post=post,
                             run_id=run_id, actor_ref=actor_ref, force=force,
                             final_check=final_check)
    return model.Evidence() if evidence is None else evidence


def _view(cid, sid, evidence, actor_ref, identity):
    """Classify one step's evidence (11-C2), in a worker. The narrator's
    roster is everyone on stage. A retrieval that failed, or a classification
    that did, leaves the narrator 09's unannotated items and an actor no
    history at all: failure narrows, never widens."""
    actor = _actor(actor_ref)
    try:
        if identity is None:
            identity = scenes_identity.scene_identity(cid, sid)
        if evidence.coverage.verdict == "error":
            if actor is not None:
                return None
            return epistemic.narrator_view(cid, sid, evidence, identity=identity)
        present = epistemic.present_roster(cid, sid)
        perspective = epistemic.Perspective.of(cid, sid, actor, present, identity=identity)
        return epistemic.classify_evidence(perspective, evidence)
    except Exception:  # noqa: BLE001 -- classification failing narrows, never widens
        _log.warning("History classification failed for campaign %s scene %s", cid, sid)
        if actor is not None:
            return None
        return epistemic.narrator_view(cid, sid, evidence, identity=identity)


async def gather_view(app, cid, sid, *, seed, resolved, llm_client, embed_client,
                      deadline=None, post=_QUERY_POST, run_id="", actor_ref=None,
                      identity=None):
    """One step's history as its perspective may have it (11-S3): `gather`
    for that perspective, then `classify`, both off the loop. None when recall
    is off, and for an actor whose retrieval or classification failed.
    `identity` is the run's scene identity, so a view never disagrees with the
    compose it is handed to."""
    evidence = await _gather(cid, sid, seed=seed, resolved=resolved, llm_client=llm_client,
                             embed_client=embed_client, deadline=deadline, post=post,
                             run_id=run_id, actor_ref=actor_ref)
    if evidence is None:
        return None
    return await history_threads.run(_view, cid, sid, evidence, actor_ref, identity)


def _sent(status: str) -> bool:
    return status == "ok" or status.startswith("failed:")


def retrievals(view) -> int:
    """How many retrievals produced `view`'s evidence: one, and one more per
    planned round (10), which `costed` charges to the round."""
    plan = view.evidence.plan if view is not None else None
    return 0 if view is None else 1 + (len(plan.rounds) if plan is not None else 0)


def embeds(view) -> int:
    """How many query embeddings the retrievals behind `view` sent: each
    planned round's own, read off the trace -- a merged coverage reads
    `mixed` -- and `embedded` for a phase that did not plan."""
    if view is None:
        return 0
    plan = view.evidence.plan
    if plan is not None and plan.semantic:
        return sum(_sent(status) for status in plan.semantic)
    return int(embedded(view))


def embedded(view) -> bool:
    """Whether the retrieval behind `view` sent a query embedding: its
    semantic stage ran ("ok") or failed after trying, never one that was off
    or skipped before any request."""
    status = view.evidence.coverage.semantic if view is not None else "off"
    return status == "ok" or status.startswith("failed:")


def costed(view, retrievals: int, embeds: int):
    """`view` carrying its round's history cost so far (spec 11 §14 Q7), for
    the history row to show what recall per speaker costs."""
    if view is None:
        return None
    return replace(view, round_cost={"retrievals": retrievals, "embeds": embeds})


def _bridge(app, cid, sid, coroutine, failed, **kwargs):
    """Run a gather from a def route; ownership is checked in its worker
    before the portal, whose own thread cannot observe it."""
    if store.locks.holds_campaign(cid):
        # Nothing was retrieved, which is not "nothing else exists": a planner
        # reading `error` never plans on it, and never mistakes it for exhausted.
        return failed(model.Evidence(coverage=model.Coverage("error", semantic="skipped:locked")))
    try:
        return app.state.run_portal.call(partial(coroutine, app, cid, sid, **kwargs))
    except Exception:  # noqa: BLE001 -- shutdown can close (or cancel) the portal after the player's append
        # A worker thread is never itself cancelled: a portal future cancelled
        # at shutdown is the closing portal of spec 10.1, not this request's
        # cancellation, so it degrades like any other portal failure.
        return failed(_error(cid, sid))


def gather_sync(app, cid, sid, *, seed, resolved, llm_client, embed_client, deadline=None,
                post=_QUERY_POST, run_id="", actor_ref=None):
    """`gather` from a def route."""
    return _bridge(app, cid, sid, gather, lambda evidence: evidence, seed=seed,
                   resolved=resolved, llm_client=llm_client, embed_client=embed_client,
                   deadline=deadline, post=post, run_id=run_id, actor_ref=actor_ref)


def gather_view_sync(app, cid, sid, *, seed, resolved, llm_client, embed_client,
                     deadline=None, post=_QUERY_POST, run_id="", actor_ref=None,
                     identity=None):
    """`gather_view` from a def route: a legacy door's one-step round, its
    cost carried as such. A gather that could not run is the narrator's error
    row and an actor's nothing, as `_view` decides."""
    view = _bridge(app, cid, sid, gather_view,
                   lambda evidence: _view(cid, sid, evidence, actor_ref, identity),
                   seed=seed, resolved=resolved, llm_client=llm_client,
                   embed_client=embed_client, deadline=deadline, post=post, run_id=run_id,
                   actor_ref=actor_ref, identity=identity)
    return costed(view, retrievals(view), embeds(view))
