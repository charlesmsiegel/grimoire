"""Duplicate identity checking may degrade an extraction, never discard it."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from ... import inference as operations
from ... import store
from ...llm import LLMClient
from ...store.continuity import identity as continuity_identity
from ...store.continuity import similarity as continuity_similarity
from .. import decision_capture
from ..common import UsableInference, _decide_error, _noting
from . import _review_budget, _review_snapshot
from ._router import router as router

#: What the identity phase reports when the embeddings provider could not be
#: used and lexical and structural matching stood in (spec §26).
_SEMANTIC_UNAVAILABLE = "semantic matching unavailable — basic matching used"
#: The same, when it was this absorb's own clock that cut the embed short --
#: `EmbeddingsClient` reports a passed deadline as a network error, and the
#: deadline may be the budget's.
_SEMANTIC_BUDGET = ("the absorb time budget ran out during semantic matching — "
                    "basic matching used")
_IDENTITY_REFUSED = "the absorb time budget ran out before the duplicate check could run"
#: The same clock, after the check had sent at least one chunk: it ran partly,
#: and the rows it did not answer are unchecked.
_IDENTITY_CUT_SHORT = ("the absorb time budget ran out partway through the duplicate check; "
                       "the rows it did not answer are unchecked")
_IDENTITY_STAGING_FAILED = ("the duplicate check's staging step failed; "
                            "rows staged without alternatives")


def _embedding_reason(cid: str, sid: str, exam: continuity_identity.Examination,
                      budget: _review_budget._Budget, block: dict) -> str:
    """Why semantic matching did not stand, or "" when it did (or was off).

    A failed embed under a spent budget is this absorb's clock, not the
    provider -- the line `BudgetRefused.llm_call_failed` and `_budget_overrun`
    draw for LLM calls -- so it writes no error row. Any other failure writes
    one counts-only row, so a dead endpoint reaches the error store (#156):
    the detail is a constant, never a title, beat or identity text.

    The reason is also written to `block["fallback"]`, which no later outcome
    overwrites: `reason` names one thing, and a partial or failed resolver
    outranks the embed there, while the possible-match lists are still the
    word-and-cast ones the reviewer has to be told about."""
    if exam.embedding != "failure":
        return ""
    if budget.spent():
        block.update(budget_exhausted=True, fallback=_SEMANTIC_BUDGET)
        return _SEMANTIC_BUDGET
    store.errors.record("continuity-identity", exam.embedding_error or "network",
                        "semantic matching failed; basic matching used",
                        campaign=cid, scene=sid, task="continuity-identity")
    block["fallback"] = _SEMANTIC_UNAVAILABLE
    return _SEMANTIC_UNAVAILABLE


def _identity_status(exam: continuity_identity.Examination,
                     unavailable: str) -> tuple[str, str | None]:
    """(status, reason) once the resolver's decisions are in. A partial
    answer is a partial failure, not `ok`."""
    if exam.all_unchecked():
        return "degraded", "the duplicate check answered none of the rows"
    if exam.counts()["unchecked"] > 0:
        return "degraded", "the duplicate check left some rows unanswered"
    if unavailable:
        return "degraded", unavailable
    return "ok", None


async def _resolve_identity(cid: str, sid: str, client: LLMClient,
                            resolved: UsableInference | None, why: str,
                            exam: continuity_identity.Examination,
                            budget: _review_budget._Budget, block: dict) -> None:
    """Decide `exam`'s rows, recording the outcome in `block`: no rows, no
    resolution, or `decide()` over one item per examined row (spec 7.4),
    chunked at `decisions.MAX_ITEMS_PER_CALL` with one metered call per
    chunk. Raises what `decide` raises when no chunk answered (an `LLMError`,
    `BudgetRefused` among them) or the request is refused before anything is
    sent (`decisions.DecideRequestError`); `_identify` turns either into the
    phase's status."""
    unavailable = _embedding_reason(cid, sid, exam, budget, block)
    if not exam.rows:
        block.update(status="degraded" if unavailable else "skipped",
                     reason=unavailable or "no close existing records")
        return
    if resolved is None:
        reason = why or "no connection"
        exam.hint_only(reason)
        block.update(status="failed", reason=reason)
        return
    rows = exam.prompt_rows()
    # Both render templates, so both run off the loop (the loader stats files).
    items = await run_in_threadpool(continuity_identity.build_items, rows, exam.live)
    explain = await run_in_threadpool(continuity_identity.explain)
    # The absorb budget runs INSIDE each chunk's meter (`around`), as it ran
    # inside this phase's own meter before: an overrun is that meter's
    # `error/timeout` row, a chunk the spent budget refuses is never sent, and
    # `_noting` reads the live holder, so a fallback that had taken over is the
    # connection told. The attempt is recorded by `run`, which alone can decide
    # it atomically with the deadline. One decision scope (roadmap 01b): every
    # chunk's call is one record of one prompt-log entry.
    async with decision_capture.capturing(cid, sid, "continuity-identity") as scope:
        decision = await operations.decide(
            "continuity-identity", items, client=client, resolved=resolved,
            explain=explain, campaign=cid, scene=sid, capture=scope.hook(),
            around=lambda call, holder: budget.run(
                call, lambda: block.__setitem__("attempted", True),
                on_timeout=_noting(client, resolved, holder)))
    if error := _decide_error(decision, continuity_identity.DECISION_ID):
        # A chunk failed and no chunk was read: the failure is the phase's
        # (M12), by the failing chunk's own kind -- never the unreadable reply
        # the garbled chunk beside it was.
        if budget.spent():
            # The clock is why. A chunk that went out means the check ran
            # partly (M3); only a check that never sent one "could not run".
            reason = _IDENTITY_CUT_SHORT if block["attempted"] else _IDENTITY_REFUSED
            exam.hint_only(reason)
            block.update(status="failed", budget_exhausted=True, reason=reason)
        else:
            reason = f"duplicate check failed: {error.kind}"
            exam.hint_only(reason)
            block.update(status="failed", reason=reason)
        return
    if not continuity_identity.take(exam, continuity_identity.answers_of(rows, decision.items)):
        block.update(status="failed", reason=continuity_identity.UNREADABLE)
        return
    block["status"], block["reason"] = _identity_status(exam, unavailable)
    # A row a refused or failed chunk never reached is `unchecked`; when the
    # clock is why, the phase says so.
    block["budget_exhausted"] = block["budget_exhausted"] or (
        budget.spent() and exam.counts()["unchecked"] > 0)


def _identity_outcome(cid: str, sid: str, exam: continuity_identity.Examination | None,
                      parsed: dict, block: dict) -> dict:
    """Steps 9-10: the rewritten rows, the counts and the log row. The log
    row carries counts and modes only -- no titles, beats, texts or reasons."""
    result = exam.rewritten(parsed) if exam is not None else parsed
    block["counts"] = exam.counts() if exam is not None else {}
    store.logs.record("info", 'grimoire.routes.scenes', "continuity identity check",
                      kind="continuity-identity", campaign=cid, scene=sid,
                      status=block["status"], matching=block["matching"],
                      embedding=exam.embedding if exam is not None else "",
                      embedding_error=exam.embedding_error if exam is not None else "",
                      **block["counts"])
    return result


async def _identify(cid: str, sid: str, client: LLMClient, resolved: UsableInference | None,
                    why: str, parsed: dict, prepared: _review_snapshot._Prepared,
                    budget: _review_budget._Budget) -> tuple[dict, dict]:
    """The identity phase (spec §10.4): `(parsed_for_materialize, block)`.

    Examines the extraction's proposed-new threads and commitments against
    the stored same-type records and, when any has a plausible neighbour,
    asks `decide()` which are the same business: one item per examined row,
    one metered call per chunk of them (`_resolve_identity`). Never raises
    but for `Abandoned` and cancellation: every failure -- the examination,
    the embeddings provider, the routed call, its reply, the rewrite -- is
    this phase's status, and the extraction's rows still stage. `matching`
    is set on every path from the one definition the continuity read uses;
    `fallback` is non-empty when semantic matching was configured but did not
    stand this run, whatever `status` and `reason` came to.
    """
    block = {"status": "skipped", "reason": "no new records proposed", "attempted": False,
             "budget_exhausted": False, "matching": continuity_similarity.matching(),
             "fallback": "", "counts": {}}
    if not continuity_identity.has_proposals(parsed):
        return parsed, block
    exam: continuity_identity.Examination | None = None
    # With no resolution the check can never run, so the step is
    # deterministic neighbours only (§10.4): no deadline embeds nothing, and
    # vectors already cached still score. Embedding would send campaign prose
    # to the provider, on the extraction's critical path, for no decision.
    embed_deadline = (None if resolved is None
                      else continuity_similarity.deadline(budget.remaining()))
    try:
        exam = await run_in_threadpool(
            continuity_identity.examine, cid, sid, parsed, prepared.facts,
            embed_deadline=embed_deadline)
        block["matching"] = exam.matching
        if exam.proposed == 0:
            # Every row moves a stored record or was dropped: nothing was
            # proposed-new (§10.2), so this is the empty extraction's skip --
            # nothing embedded, nothing to rewrite, and (§29) no log row.
            return parsed, block
        await _resolve_identity(cid, sid, client, resolved, why, exam, budget, block)
    except _review_budget.Abandoned:
        raise
    except _review_budget.BudgetRefused:
        # Never asked: the rows the examination found keep their hints.
        if exam is not None:
            exam.hint_only(_IDENTITY_REFUSED)
        block.update(status="failed", budget_exhausted=True, reason=_IDENTITY_REFUSED)
    except Exception as exc:  # noqa: BLE001 -- LLMError, EmbeddingsError, store errors: a failed phase, never a failed absorb
        # Deliberately covers `EmbeddingsError` (an `LLMError`) too, so it can
        # never reach `_absorb_work`'s fatal `except LLMError`.
        store.errors.record_exception(exc, "continuity-identity", campaign=cid, scene=sid)
        overrun = _review_budget._budget_overrun(exc)
        # The clock's reason, worded as `_resolve_identity` words it for a
        # batch: a failure composed from routes the clock stopped would
        # otherwise read as the provider's, the sentinel twice over.
        reason = ((_IDENTITY_CUT_SHORT if block["attempted"] else _IDENTITY_REFUSED)
                  if overrun else f"duplicate check failed: {exc}")
        if exam is not None:
            exam.hint_only(reason)
        block.update(status="failed", budget_exhausted=overrun, reason=reason)
    try:
        return _identity_outcome(cid, sid, exam, parsed, block), block
    except Exception as exc:  # noqa: BLE001 -- a defect in the rewrite, counts or log row: a failed phase, the rows staged as extracted
        store.errors.record_exception(exc, "continuity-identity", campaign=cid, scene=sid)
        block.update(status="failed", reason=f"duplicate check failed: {exc}", counts={})
        return parsed, block


async def _extract_and_identify(extraction, cid: str, sid: str, client: LLMClient,
                                prepared: _review_snapshot._Prepared, budget: _review_budget._Budget,
                                ident_resolved: UsableInference | None,
                                ident_why: str) -> tuple[dict, dict]:
    """The extraction, then the identity phase chained onto it.

    `extraction` is the already-built extraction awaitable: its
    `operations.generate(...)` is spelled in `_absorb_work`, under the absorb
    meter, so the usage guard sees it metered. Its failure stays fatal; the
    identity phase never raises for absorb."""
    text = await extraction
    return await _identify(cid, sid, client, ident_resolved, ident_why,
                           store.absorb.parse_output(text), prepared, budget)
