"""The Decision stage of spec 11 (§5, roadmap 11-S6): helpers, no router.

An NPC's compose step has its recalled history classified deterministically
(`store/context/epistemic.py`, 11-S3). A part the record cannot settle stays
`unknown`, and an actor is shown nothing `unknown`. Where the actor had a
structural way to have learned such a part (`store/context/epistemic_items`),
this stage asks a Decision model, once per contribution, whether it could know
it -- and folds the answers back into the view the step composes from.

The rules it keeps, each one a reason it exists as its own step:

- **Off is free.** With `epistemic_decide` off (the default, and hidden from
  Settings until its play gate is ratified) nothing past the switch is read,
  resolved or called, and the view is returned as it came.
- **Capped and bounded.** At most `EPISTEMIC_DECIDE_ITEMS` parts per step and
  `EPISTEMIC_ROUND_ITEMS` per round (carried on the round record), and one
  total deadline, `EPISTEMIC_CEILING_S`, across every call `decide` makes --
  a fallback stage and a schema re-send included (the capture is filed after,
  outside it). No `_noting`: a slow Decision model is not marked failing
  because of this stage.
- **Fail-soft and fail-closed.** Every failure -- a route that does not
  resolve, a refused or failed call, a spent deadline, a crash -- leaves every
  eligible part `unknown`, which is withheld, and the turn plays on. A cancel
  is not an `Exception`, so Stop passes through.
- **A model never grants presence or withholding.** `witnessed` is structural
  and `narrator_only` is the user's word; an answer can make a part `known`
  or `suspected`, or leave it `unknown`.
- **Nothing derived is authoritative.** The answers are for this
  contribution's compose: they ride its snapshot, its response record and
  the capture, and the user may accept one as an override. Nothing caches
  them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

from starlette.concurrency import run_in_threadpool

from .. import decisions, store
from .. import inference as operations
from ..llm_errors import LLMError
from ..store import epistemic_access
from ..store.context import epistemic
from ..store.context import epistemic_items as items_mod
from . import decision_capture
from .common import _soft_resolved, plan_deadline, require_inference

log = logging.getLogger("grimoire")

#: The task, on the `epistemic` route (02-C5a's kit).
TASK = epistemic_access.TASK

#: Seconds the epistemic Decision pass may hold one actor step before its
#: stream starts, across every call `decide` makes (a chunk, a schema
#: re-send, a fallback stage; the task does not escalate, and the 01b capture
#: is filed after the call, outside it). Argued
#: structurally: it is optional, it sits before the first token beside 09's
#: rerank and 10's planning phase, and a step should not wait longer for it
#: than for 02's own pre-generation decision. Below `PLAN_CEILING_S` (20 s)
#: for that reason. To be tuned against 01a's latency reports.
EPISTEMIC_CEILING_S = 10.0
#: Parts asked about per actor step: one structured chunk
#: (`decisions.MAX_ITEMS_PER_CALL`). Argued from the chunk size alone; to be
#: tuned against real prompts later.
EPISTEMIC_DECIDE_ITEMS = 8
#: Parts asked about across a round's steps, first come first served: two
#: chunks. Carried on the round record (`epistemic_asked`), so a resumed
#: round does not reset it. A part is charged once it is handed to `decide`,
#: even when a spent deadline then refuses it unsent: the cap errs on the
#: side of asking less.
EPISTEMIC_ROUND_ITEMS = 16
#: The round record's field.
ROUND_FIELD = "epistemic_asked"

#: How an answer reads (§5.3): (access, basis).
_READING = {"known": ("known", "decision"), "experienced": ("known", "decision"),
            "suspected": ("suspected", "decision"), "narrator_only": ("unknown", "decision")}
_ABSTAINED = ("unknown", "decision_abstained")
_FAILED = ("unknown", "decision_failed")
_SKIPPED = ("unknown", "decision_skipped")


def ceiling() -> float:
    """`EPISTEMIC_CEILING_S`, or the user's `llm_call_budget` when that is
    lower (`<= 0` is "no ceiling" there, which never lifts this one). Reads
    the store: a worker's."""
    budget = store.config.llm_call_budget()
    return EPISTEMIC_CEILING_S if budget <= 0 else min(EPISTEMIC_CEILING_S, budget)


def asked_in(round_record: dict) -> int:
    """How many parts this round's steps have asked about so far."""
    value = round_record.get(ROUND_FIELD, 0)
    return value if type(value) is int and value >= 0 else 0


@dataclass(frozen=True)
class Refined:
    """What one step's refine produced. `record` is None when the stage did
    not run (off, the narrator, no view); `sent` is how many parts went to a
    model, which the round's budget is charged."""
    view: epistemic.EpistemicView | None
    record: dict | None = None
    response_id: str = ""
    sent: int = 0


def _switched_on(cid: str) -> bool:
    try:
        return store.play_decisions.epistemic_decide(cid)
    except Exception as exc:  # noqa: BLE001 - an optional step that cannot read its switch asks nothing
        log.error("epistemic switch unreadable for %s: %s", cid, type(exc).__name__)
        return False


def _actor_name(cid: str, sid: str, actor: str) -> str:
    entry = next((a for a in store.appearances.scene_cast(cid, sid)
                  if f"{a['kind']}:{a['id']}" == actor), None)
    return str((entry or {}).get("name") or "")


def _questions(cid: str, sid: str, actor: str, parts):
    name = _actor_name(cid, sid, actor)
    qs = items_mod.questions(parts, actor_ref=actor, actor_name=name,
                             state=items_mod.own_state(cid, actor))
    return qs, epistemic_access.build_items(qs)


def _final(view: epistemic.EpistemicView, decided: dict, parts) -> epistemic.EpistemicView:
    """`view` classified again with the answers (§5.3), carrying the
    retrieval, the round's cost and the eligible parts. A classification that
    fails keeps the deterministic view: every asked part stays withheld."""
    try:
        final = epistemic.classify(view.perspective, view.evidence.items, decided=decided)
    except Exception as exc:  # noqa: BLE001 - the deterministic view is the safe side
        log.error("epistemic re-classification failed: %s", type(exc).__name__)
        final = view
    return replace(final, evidence=view.evidence, round_cost=view.round_cost,
                   pending=tuple(p.ident for p in parts))


def _record(parts, decided: dict, skipped: str, sent: int) -> dict:
    return {"task": TASK, "asked": sent, "skipped": skipped,
            "parts": [{"ref": items_mod.ref_of(p), "channel": p.channel,
                       "access": decided.get(p.ident, _SKIPPED)[0],
                       "basis": decided.get(p.ident, _SKIPPED)[1]} for p in parts]}


@dataclass
class _Call:
    """One step's call, as it goes: each part's reading, why nothing was read,
    how many parts were sent, and the response id it was metered under."""
    response_id: str
    decided: dict = field(default_factory=dict)
    skipped: str = ""
    sent: int = 0


async def _ask(call: _Call, actor: str, parts, ask, *, cid, sid, client, post,
               round_id) -> None:
    """Resolve, build and decide `ask` (§5.2), filling `call`. A route that
    does not resolve skips; a raise is the caller's to read (§5.3)."""
    resolved, _why, kind = await run_in_threadpool(
        _soft_resolved, lambda: require_inference("epistemic-access", cid, operation="decide"))
    if resolved is None:
        call.skipped = kind or "unresolved"
        call.decided.update({p.ident: _SKIPPED for p in ask})
        # Nothing is sent, but the skip is said where a capture is looked
        # for (§5.4).
        async with decision_capture.capturing(cid, sid, TASK) as scope:
            scope.note("", "skipped", call.skipped)
            scope.note("", "parts", _record(parts, call.decided, "", 0)["parts"])
        return
    limit = await run_in_threadpool(ceiling)
    questions, items = await run_in_threadpool(_questions, cid, sid, actor, ask)
    call.response_id = call.response_id or store.responses.mint_id()
    call.decided.update({p.ident: _FAILED for p in ask})
    async with decision_capture.capturing(cid, sid, TASK) as scope:
        call.sent = len(ask)
        try:
            decision = await operations.decide(
                "epistemic-access", list(items), client=client, resolved=resolved,
                campaign=cid, scene=sid, post=post, round_id=round_id,
                response_id=call.response_id, capture=scope.hook(),
                around=plan_deadline(limit))
        except LLMError as exc:
            # Said in the capture too, before `refine` reads the raise.
            scope.note("", "skipped", exc.kind or "error")
            raise
        scope.note_escalations("", decision)
        access = epistemic_access.access_of(questions, decision)
        for q, p in zip(questions, ask, strict=True):
            call.decided[p.ident] = _READING.get(access[(q.actor_ref, q.evidence_ref)],
                                                 _ABSTAINED)
        if all(call.decided[p.ident] == _ABSTAINED for p in ask):
            # Nothing read -- every item unanswered, or every call failed:
            # say why, as the rerank's row does.
            failures = [c.error_kind for c in decision.calls if c.error_kind]
            call.skipped = failures[-1] if failures else ""
        scope.note("", "parts", _record(parts, call.decided, "", call.sent)["parts"])
        if call.skipped:
            scope.note("", "skipped", call.skipped)


async def refine(view: epistemic.EpistemicView | None, *, cid: str, sid: str, client,
                 post: int | None, round_id: str, asked: int,
                 response_id: str = "") -> Refined:
    """One actor step's view with the Decision stage's answers folded in
    (§5), never raising an `Exception`. `asked` is how many parts the round
    has asked about already; `response_id` the contribution's, minted here
    when the stage calls and none was handed in."""
    if view is None or view.perspective.actor is None:
        return Refined(view, response_id=response_id)
    if not await run_in_threadpool(_switched_on, cid):
        return Refined(view, response_id=response_id)
    parts: tuple[items_mod.Part, ...] = ()
    call = _Call(response_id)
    try:
        parts = await run_in_threadpool(items_mod.eligible_for, view)
        if not parts:
            return Refined(view, _record((), {}, "", 0), response_id)
        room = max(0, min(EPISTEMIC_DECIDE_ITEMS, EPISTEMIC_ROUND_ITEMS - asked))
        # Beyond either cap, in 09's rank order: skipped, never asked.
        call.decided.update({p.ident: _SKIPPED for p in parts[room:]})
        if room:
            await _ask(call, view.perspective.actor, parts, parts[:room], cid=cid, sid=sid,
                       client=client, post=post, round_id=round_id)
        else:
            call.skipped = "round_cap"
    except LLMError as exc:
        call.skipped = exc.kind or "error"
    except decisions.DecideRequestError:
        call.skipped = "invalid_request"
    except Exception as exc:  # noqa: BLE001 - an optional step never fails a turn (§5.4)
        log.error("epistemic decision for %s/%s skipped: %s", cid, sid, type(exc).__name__)
        call.skipped = f"error:{type(exc).__name__}"
        call.decided.update({p.ident: _FAILED for p in parts if p.ident not in call.decided})
    if not parts:
        return Refined(view, _record((), {}, call.skipped, 0), response_id)
    final = await run_in_threadpool(_final, view, call.decided, parts)
    return Refined(final, _record(parts, call.decided, call.skipped, call.sent),
                   call.response_id, call.sent)
