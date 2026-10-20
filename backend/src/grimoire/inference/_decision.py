"""Stage planning, failed-call progression, and one post-answer escalation hop."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import NamedTuple

from .. import decisions, llm, store, wire
from ..llm import LLMClient
from ..llm_errors import LLMError
from ..store.inference import resolve
from ..store.inference.resolved import ResolvedInference
from . import _contracts, _decision_calls, _requests
from ._contracts import Around as Around
from ._contracts import Backend as Backend
from ._contracts import Capture as Capture
from ._contracts import Escalator as Escalator
from ._contracts import Resolver as Resolver
from ._contracts import Stage as Stage
from ._contracts import _Answered as _Answered
from ._contracts import _Call as _Call


def stages(resolved: ResolvedInference) -> tuple[Stage, ...]:
    """The decide chain of `resolved` (spec 5.5): the selection's one backend,
    then -- when it is a stage of its own -- the role fallback's, in one
    attempt. Pure: nothing in `resolved` is changed.

    The primary is served natively when its `decision_mode` says so, and
    structured when it `generates` (`resolve.generates`); its structured
    stage sends the resolution's chain as it is, so a structured fallback the
    resolver attached rides the facade, exactly as slice F sends it. The
    fallback is a stage of its own only when either attempt is native (a
    structured stage cannot carry a native attempt behind it, nor a native
    one anything), and then the primary is sent alone (`wire.Chain.alone`).
    A fallback known incapable (`fallback_missing`) is never a stage (spec
    5.3).

    No structured stage follows a native one on the same selection: a native
    attempt is one that cannot generate (ruling 1, C1), so that stage could
    never answer.

    Native first (01c §4.2): a structured primary whose task's policy lists
    its adapter kind, and which is known able to decide natively
    (`resolve.native_first`), is asked through its decisions endpoint first
    -- an isolated stage on the same model, with no retries, sent the
    primary alone and unflagged (no structured envelope goes to a decisions
    endpoint) -- and the structured stage after it, exactly today's, takes
    the items that stage failed. No task's policy lists a kind yet, so no
    chain built today has that stage."""
    whole = resolved.chain
    if whole is None:
        return ()
    primary = resolved.attempts[0]
    fallback = (resolved.attempts[1] if len(resolved.attempts) > 1
                and not resolved.fallback_missing else None)
    apart = (fallback is not None and fallback.decision_mode in decisions.BACKENDS
             and _contracts.NATIVE in (primary.decision_mode, fallback.decision_mode))
    chain: list[Stage] = []
    if primary.decision_mode == _contracts.NATIVE:
        chain.append(_contracts.Stage(_contracts.NATIVE, whole.alone(), None))
    elif resolve.generates(primary):
        if resolve.native_first(resolved):
            target = primary.target
            alone = replace(target, structured=False) if target.structured else target
            chain.append(_contracts.Stage(_contracts.NATIVE, wire.Chain(alone), 0, isolated=True))
        chain.append(_contracts.Stage(_contracts.STRUCTURED, whole.alone() if apart else whole, None))
    if apart and fallback is not None:
        chain.append(_contracts.Stage(fallback.decision_mode, wire.Chain(fallback.target), 0))
    return tuple(chain)


def reports_distribution(resolved: ResolvedInference) -> bool:
    """Whether `resolved`'s first decide stage is native (01c §4.2): a
    native-only primary, or a native-first one (`resolve.native_first`).
    Pure, and sends nothing. A forecast, not a guarantee: an item the native
    stage fails is answered by a structured stage with no distribution, so a
    sampling caller still goes through `draws.draw` per item."""
    planned = stages(resolved)
    return bool(planned) and planned[0].mode == _contracts.NATIVE


def _final(words: Sequence[LLMError]) -> LLMError:
    """One unit's final error from its failure on each stage it reached:
    `llm.routes_failed`'s composition -- except when every one of them is the
    caller's clock refusing the call unsent (`_refused_unsent`), which is the
    first of them as it is. A composed error is a plain `LLMError`, and a
    phase that tells "never sent" from "failed" by type (absorb's `except
    BudgetRefused`) would read the clock's refusal as a provider failure."""
    if all(_decision_calls._refused_unsent(word) for word in words):
        return words[0]
    return llm.routes_failed(words)


def _same_connection(provider_id: str, other: str) -> bool:
    """Whether two stages send to the same connection, by provider id --
    `llm._same_route`'s rule: an empty id never matches."""
    return bool(provider_id) and provider_id == other


def _all_dead(stage: Stage, dead: Sequence[str]) -> bool:
    """Whether every route `stage` sends -- its primary, and on a structured
    stage the fallback riding it -- is on a connection a stage before it
    stopped dead (01c §4.2.1). A native stage sends its primary alone
    (`_native` reads only `chain.primary`), so only that counts there. Today
    only a first stage carries a riding fallback, so for every chain built
    this asks whether the primary's connection is dead."""
    sent = (stage.chain.primary,) if stage.mode == _contracts.NATIVE else stage.chain.attempts
    return all(any(_same_connection(target.provider_id, d) for d in dead)
               for target in sent)


def _settle(got: _Answered, pending: list[int], results: list[decisions.ItemResult | None],
            words: dict[int, list[LLMError]]) -> list[tuple[tuple[int, ...], LLMError]]:
    """Lay one stage's answers over `results` (`pending` maps the stage's
    positions back to the batch's), add each failed unit's error to its
    items' `words`, and return the failed units in batch indices."""
    for position, result in enumerate(got.results):
        if result is not None:
            results[pending[position]] = result
    failed = [(tuple(pending[p] for p in unit), error) for unit, error in got.failed]
    for unit, error in failed:
        for index in unit:
            words.setdefault(index, []).append(error)
    return failed


#: Every backend a decide stage can dispatch to, by `Stage.mode`. Each fills
#: `_Answered.failed` with every unit whose items it leaves unanswered:
#: `run_stages` builds `Decision.errors` from it, and
#: `routes.common._decide_error` reports a batch's failure from the first, so
#: a unit left out would lose a rate limit's kind and window (spec 7.4,
#: "Result").
_BACKENDS: dict[str, Backend] = {_contracts.STRUCTURED: _decision_calls._structured, _contracts.NATIVE: _decision_calls._native}


async def run_stages(task: str, items: Sequence[decisions.Item], chain: Sequence[Stage], *,
                     client: LLMClient, explain: str = "",
                     campaign: str = "", scene: str = "", post: int | None = None,
                     round_id: str = "", capture: Capture | None = None,
                     around: Around | None = None, response_id: str = "",
                     run_id: str = "", loop_turn: int | None = None
                     ) -> decisions.Decision:
    """Answer `items` down `chain`, stage by stage (spec 5.5): each stage runs
    the items still pending through its own backend, and an item moves on
    only when its stage FAILED to answer it -- an answer, a `refused` or a
    `None` from a well-formed reply included, is final.

    An invalid request is a `decisions.DecideRequestError`, and an empty
    chain a `ValueError`, both before any meter opens. A
    `llm.PresetRefusalError` ends the chain where it is met, as it stops the
    facade (ruling 7): the preset is the user's to fix, and a native stage
    that takes no sampling would only hide it. So does the caller's clock
    refusing a call unsent (`_refused_unsent`, absorb's `BudgetRefused`):
    it refuses every later call too, so a later stage could only add another
    refusal, and the one it has keeps its type -- an isolated stage
    included. A stage that stopped on a failure every call on its connection
    would meet (`_connection_wide`: the key, the account's money, an
    exhausted rate limit) skips each later stage every route of which is on
    that SAME connection (`_all_dead`, `_same_connection`): its items keep
    the failures they have, as items held back inside a stage do.

    An isolated stage (native first, 01c §4.2.1) is a statement about its
    decisions endpoint only: it never marks its connection dead -- a key
    refused there may serve every chat call -- and its failures are set
    aside, composed into an item's error only when no later stage reached
    that item.

    When no item answered, the first failed unit's final error is raised
    (F's ruling 8): its failures on every stage it reached, composed
    (`llm.routes_failed`) -- the first stage's kind and window, the later
    stages' failures named after it (M3), and each kept in `words` -- or,
    when every one of them is the clock's refusal, the first of them as it
    is (`_final`), so a caller's `except BudgetRefused` still sees it.
    Otherwise each unanswered item is `None` with reason `error`, and
    `Decision.errors` holds each failed unit's final error.

    `decide` calls this with `stages(resolved)`; an eval that forces a
    backend builds its own chain."""
    items = tuple(items)
    decisions.validate(items)
    if not chain:
        raise ValueError(f"{task!r} has no decide stage to answer it")
    call = _contracts._Call(task=task, client=client, explain=explain,
                 campaign=campaign, scene=scene, post=post, round_id=round_id,
                 capture=capture, around=around, chain=chain[0].chain,
                 retries=chain[0].retries, response_id=response_id, run_id=run_id,
                 loop_turn=loop_turn)
    results: list[decisions.ItemResult | None] = [None] * len(items)
    #: Per item, the failure of each stage that failed it, in stage order.
    words: dict[int, list[LLMError]] = {}
    #: An isolated stage's failures, per item: an item's words only when no
    #: later stage reached it (01c §4.2.1).
    aside: dict[int, list[LLMError]] = {}
    rows: list[dict] = []
    records: list[decisions.CallRecord] = []
    served: dict[tuple[str, str], None] = {}     # ordered, each once
    failed: list[tuple[tuple[int, ...], LLMError]] = []
    pending = list(range(len(items)))
    #: The provider of each stage that stopped on a connection-wide failure.
    dead: list[str] = []
    for number, stage in enumerate(chain):
        if not pending:
            break
        if _all_dead(stage, dead):
            # Every route it sends would meet the failure that stopped a
            # stage before it.
            continue
        got = await _BACKENDS[stage.mode](
            tuple(items[i] for i in pending),
            replace(call, chain=stage.chain, retries=stage.retries, stage=number,
                    positions=tuple(pending)))
        rows.extend(got.rows)
        records.extend(got.calls)
        served.update(dict.fromkeys(got.served))
        failed = _settle(got, pending, results, aside if stage.isolated else words)
        pending = [i for i in pending if results[i] is None]
        if any(isinstance(error, llm.PresetRefusalError) or _decision_calls._refused_unsent(error)
               for _unit, error in failed):
            break
        if (got.stopped is not None and _decision_calls._connection_wide(got.stopped)
                and not stage.isolated):
            dead.append(stage.chain.primary.provider_id)
    # Each failed unit of the last stage its items reached, with the failures
    # of every stage before it: a chunk's earlier failures are its first
    # item's (a native stage before it failed each item on its own).
    # An isolated stage's failure counts only for an item no later stage
    # reached (the chain ended right after it): `words` is then empty for it.
    errors = tuple(_final(words.get(unit[0]) or aside[unit[0]]) for unit, _error in failed)
    # Each backend stamps the items it answered (`ItemResult.backend`).
    answered_by = list(dict.fromkeys(r.backend for r in results if r is not None))
    if not answered_by:
        # No item answered: the provider's error is the caller's (ruling 8).
        raise errors[0]
    for index in pending:
        results[index] = decisions.unanswered((items[index],), "error")[0]
    # One backend, and one route, answered: it is the decision's. Several
    # (the fallback stage took the items the primary failed): none is.
    backend = answered_by[0] if len(answered_by) == 1 else ""
    provider, model = next(iter(served)) if len(served) == 1 else ("", "")
    return decisions.Decision(items=tuple(r for r in results if r is not None),
                              backend=backend, provider=provider, model=model,
                              usage=tuple(rows), served=tuple(served), errors=errors,
                              calls=tuple(records))


async def decide(task: str, items: Sequence[decisions.Item], *, client: LLMClient,
                 resolved: ResolvedInference, explain: str = "", campaign: str = "",
                 scene: str = "", post: int | None = None, round_id: str = "",
                 capture: Capture | None = None,
                 around: Around | None = None,
                 escalation: Escalator | None = None, response_id: str = "",
                 run_id: str = "", loop_turn: int | None = None,
                 max_tokens: int | None = None) -> decisions.Decision:
    """Answer `items` (spec 7.4): one `ItemResult` per item, in input order.

    `resolved` is the call site's own resolution of `task` for the `decide`
    operation; a resolution of another task or operation, or one that resolved
    nothing, is a `ValueError`. An invalid request is a
    `decisions.DecideRequestError`. Both are raised before any meter opens, so
    neither files a ledger row.

    `explain` is the rationale instruction ("" asks for none). `campaign`,
    `scene`, `post`, `round_id` and `response_id` attribute each call's
    ledger row, and `run_id` / `loop_turn` the caller's run and its turn
    (01g-C3), which the call's incoming-response capture names too. `capture`
    is handed each call once it settles (`Capture`); `around` is handed each
    facade call and the meter's live holder, and returns what to await
    instead (a caller's time budget).

    `escalation` is handed in exactly when the task's code policy escalates
    (`routing.policy(task).escalate_to`; 01d §5.2): a mismatch, an item that
    does not ask the policy's deciding `question`, or a policy whose trigger
    arguments `decisions.triggers` refuses is a `ValueError` before any meter
    opens. It is awaited at most once, after the chain, and only when an item
    triggered: the items it hands over are answered once more by the
    escalation role's primary alone, or by the `Resolver` it returns, and
    what became of each is `Decision.escalations` (`_escalate`).

    `max_tokens` (01g-S7) caps each structured stage's output, held to each
    attempt's own maximum (`wire.Target.with_output_cap`, as `generate`'s
    `max_tokens=` is): what the decide tool's spend projection bounds. A
    native stage takes no sampling, so no cap; a value that is not a positive
    whole number is a `ValueError` before any meter opens.
    """
    items = tuple(items)
    if max_tokens is not None:
        _requests._check_cap(max_tokens)
    if resolved.task != task:
        raise ValueError(f"a resolution of {resolved.task!r} cannot decide {task!r}")
    if resolved.operation != "decide":
        raise ValueError(f"a {resolved.operation!r} resolution cannot decide")
    if resolved.chain is None:
        raise ValueError(f"{task!r} resolved to no connection")
    policy = _policy_for(task, items, escalation)
    chain = stages(resolved)
    if not chain:
        raise ValueError(f"no decision backend for mode {resolved.decision_mode!r}")
    if max_tokens is not None:
        chain = tuple(_capped_stage(stage, max_tokens) for stage in chain)
    base = await run_stages(task, items, chain, client=client,
                            explain=explain, campaign=campaign, scene=scene, post=post,
                            round_id=round_id, capture=capture, around=around,
                            response_id=response_id, run_id=run_id, loop_turn=loop_turn)
    if policy is None or escalation is None:
        return base
    found = decisions.triggers(base.items, question=policy.question,
                               escalate_on=policy.escalate_on,
                               margins=dict(policy.margins),
                               answers=policy.escalate_answers)
    if not found:
        return base
    # The hop's own call, built once, with the base's attribution: one
    # attempt (`retries=0`, 01d §11 Q4), after the chain's last stage, and
    # marked as the hop on every record and capture it makes.
    call = _contracts._Call(task=task, client=client, explain=explain, campaign=campaign,
                 scene=scene, post=post, round_id=round_id, capture=capture,
                 around=around, chain=chain[0].chain, retries=0, stage=len(chain),
                 hop=decisions.HOP_ESCALATION, response_id=response_id, run_id=run_id,
                 loop_turn=loop_turn)
    return await _escalate(call, items, base, found, policy, escalation, chain)


# ---- the escalation hop (roadmap 01d-C2b) ----

def _policy_for(task: str, items: tuple[decisions.Item, ...],
                escalation: Escalator | None) -> store.routing.TaskPolicy | None:
    """`task`'s policy when it escalates, else None -- after every check that
    must come before any meter opens (01d §5.2, S2's handoff): `escalation`
    is given exactly when the policy escalates, every item asks its deciding
    `question` (so `decisions.triggers` cannot raise after a paid call), and
    `triggers` accepts the policy's arguments (run here over no results)."""
    policy = store.routing.policy(task)
    if bool(policy.escalate_to) != (escalation is not None):
        raise ValueError(f"{task!r}'s policy escalates to {policy.escalate_to!r}, and the "
                         f"call {'passed' if escalation else 'passed no'} escalation=")
    if not policy.escalate_to:
        return None
    decisions.validate(items)
    missing = [index for index, item in enumerate(items)
               if all(q.id != policy.question for q in item.questions)]
    if missing:
        raise ValueError(f"items {missing} do not ask {task!r}'s deciding question "
                         f"{policy.question!r}")
    decisions.triggers((), question=policy.question, escalate_on=policy.escalate_on,
                       margins=dict(policy.margins), answers=policy.escalate_answers)
    return policy


class _Settled(NamedTuple):
    """One triggered item's escalation, and its merged result when the hop's
    answer replaced the base's (None otherwise)."""

    escalation: decisions.Escalation
    merged: decisions.ItemResult | None = None


class _HopDone(NamedTuple):
    """What a hop did: each triggered item's `_Settled`, keyed by batch
    index; the hop's ledger rows and `CallRecord`s; every `(provider id,
    model)` that answered one of its calls; and the backend of every result
    it returned."""

    settled: dict[int, _Settled]
    rows: tuple[dict, ...] = ()
    calls: tuple[decisions.CallRecord, ...] = ()
    served: tuple[tuple[str, str], ...] = ()
    backends: tuple[str, ...] = ()


def _skipped(found: Sequence[decisions.Trigger], base: decisions.Decision,
             detail: str) -> dict[int, _Settled]:
    """Each of `found` skipped, with `detail`: no hop ran for it."""
    return {t.index: _Settled(decisions.Escalation(t.index, t.trigger, t.margin,
                                                   base.items[t.index], "skipped", detail))
            for t in found}


async def _escalate(call: _Call, items: tuple[decisions.Item, ...], base: decisions.Decision,
                    found: tuple[decisions.Trigger, ...], policy: store.routing.TaskPolicy,
                    escalation: Escalator, chain: Sequence[Stage]) -> decisions.Decision:
    """The hop after the chain (01d §5.3): await the escalator once, then
    hand `found` to what it returned, and lay the result over `base`.

    The base is paid for, so nothing the escalator does loses it: a thunk
    that raises is every trigger skipped, `unresolved: <kind>` (its kind,
    never its text); `(None, why)` is every trigger skipped with `why`, for
    either kind of policy. Only a value of the wrong shape is a caller bug:
    a `Resolver` for a role policy or a resolution for a `CALLER` one is a
    `TypeError`, and a resolution of another task or operation a
    `ValueError` -- each raised before any hop call."""
    try:
        target, why = await escalation()
    except Exception as exc:  # noqa: BLE001 - the paid base answers must survive
        kind = exc.kind if isinstance(exc, LLMError) else type(exc).__name__
        return _escalated(base, found, _HopDone(_skipped(found, base, f"unresolved: {kind}")))
    if target is None:
        return _escalated(base, found, _HopDone(_skipped(found, base, why)))
    if policy.escalate_to == store.routing.CALLER:
        if isinstance(target, ResolvedInference) or not callable(target):
            raise TypeError(f"{call.task!r} escalates to a caller resolver, "
                            f"and the escalator returned {type(target).__name__}")
        return _escalated(base, found, await _caller_hop(call, items, base, found, policy, target))
    if not isinstance(target, ResolvedInference):
        raise TypeError(f"{call.task!r} escalates to the {policy.escalate_to!r} role, "
                        f"and the escalator returned {type(target).__name__}")
    if target.task != call.task or target.operation != "decide":
        raise ValueError(f"a {target.operation!r} resolution of {target.task!r} cannot "
                         f"escalate {call.task!r}")
    return _escalated(base, found, await _role_hop(call, items, base, found, policy, target, chain))


def _hop_stage(target: ResolvedInference) -> Stage | None:
    """The role hop's one stage (01d §5.3): the escalation primary ALONE --
    no fallback -- with no retries, its backend by its `decision_mode`, and
    its account marked as the hop. None when nothing resolved, or the
    primary can neither generate nor decide natively. Never native-first
    (01c): the stage is built here, not by `stages`."""
    chain = target.chain
    if chain is None or not target.attempts:
        return None
    mode = target.attempts[0].decision_mode
    if mode not in decisions.BACKENDS:
        return None
    return _contracts.Stage(mode, wire.Chain(chain.primary.with_account(hop=decisions.HOP_ESCALATION)), 0)


def _on_dead_connection(base: decisions.Decision, chain: Sequence[Stage],
                        provider_id: str) -> bool:
    """Whether the base met a failure every call on `provider_id`'s
    connection would meet (`_connection_wide`) and `provider_id` is the
    connection of one of the base's stages: a hop there would only meet it
    again."""
    if not any(isinstance(e, LLMError) and _decision_calls._connection_wide(e) for e in base.errors):
        return False
    return any(_same_connection(provider_id, target.provider_id)
               for stage in chain for target in stage.chain.attempts)


def _take(found: tuple[decisions.Trigger, ...], items: tuple[decisions.Item, ...], most: int,
          mode: str) -> tuple[tuple[decisions.Trigger, ...], tuple[decisions.Trigger, ...]]:
    """`(taken, capped)` of `found`, in its priority order (01d §5.3, S6):
    at most `most`, and on a structured hop only those `decisions.chunks`
    puts in its FIRST chunk -- so the hop is one structured call (plus at
    most one prompt-only re-send)."""
    taken = found[:most]
    if mode == _contracts.STRUCTURED and taken:
        taken = taken[:len(decisions.chunks([items[t.index] for t in taken])[0][1])]
    return taken, found[len(taken):]


def _merge(before: decisions.ItemResult, hop: decisions.ItemResult, question: str,
           reads_declines: bool) -> tuple[decisions.ItemResult | None, str]:
    """The item as the hop leaves it (01d §5.3), or None and why it stays.

    Replaced only when the hop's `question` answer is a value, or a decline
    (`abstained`, `refused`) on a task that reads one. The deciding answer,
    rationale, backend and server are then the hop's, and every other
    question keeps the base's answer unless the hop gave it a VALUE -- not
    merely one it `was_read`: a garbled or missing key reads as an
    `unreadable` that `was_read`, and it must not replace a good base answer
    (the plan's Decision 7; each question of an item stands alone)."""
    answer = hop.answers[question]
    if answer.answer is None:
        if answer.reason not in ("abstained", "refused"):
            return None, (f"{answer.reason}: {answer.detail}" if answer.detail
                          else answer.reason)
        if not reads_declines:
            return None, decisions.FAILED_DECLINED
    merged = {qid: (hop.answers[qid] if qid == question or hop.answers[qid].answer is not None
                    else old)
              for qid, old in before.answers.items()}
    return replace(hop, answers=merged), ""


def _settle_one(trigger: decisions.Trigger, before: decisions.ItemResult,
                result: decisions.ItemResult | None, error: LLMError | None,
                policy: store.routing.TaskPolicy,
                sent: tuple[()] | tuple[str, str, str]) -> _Settled:
    """One taken item's escalation, from the hop's `result` for it (None when
    its unit failed with `error`, or a caller gave none). `sent` names the
    target a failed role hop was sent (`()` for a caller)."""
    def record(outcome: str, detail: str,
               served: tuple[()] | tuple[str, str, str]) -> decisions.Escalation:
        return decisions.Escalation(trigger.index, trigger.trigger, trigger.margin, before,
                                    outcome, detail, served)

    if result is None:
        detail = (f"{error.kind}: {error.detail}" if error is not None
                  else decisions.FAILED_NO_RESULT)
        return _Settled(record("failed", detail, sent))
    merged, why = _merge(before, result, policy.question, policy.reads_declines)
    if merged is None:
        return _Settled(record("failed", why, result.served or sent))
    return _Settled(record("answered", "", merged.served), merged)


async def _role_hop(call: _Call, items: tuple[decisions.Item, ...], base: decisions.Decision,
                    found: tuple[decisions.Trigger, ...], policy: store.routing.TaskPolicy,
                    target: ResolvedInference, chain: Sequence[Stage]) -> _HopDone:
    """The hop to the policy's role (01d §5.3, §5.4): its one stage, sent
    the triggered items that are not on the model that answered them
    (`same_model`) and that fit the cap, through the stage's backend --
    the same dispatch `run_stages` makes, with a `_Call` whose `positions`
    are the batch indices taken, so its records and captures name the
    batch. A failed unit leaves its items' originals standing."""
    stage = _hop_stage(target)
    if stage is None:
        return _HopDone(_skipped(found, base, decisions.SKIPPED_INCAPABLE))
    primary = stage.chain.primary
    if _on_dead_connection(base, chain, primary.provider_id):
        return _HopDone(_skipped(found, base, decisions.SKIPPED_DEAD_CONNECTION))
    server = (primary.provider_id, primary.model)
    same = tuple(t for t in found if base.items[t.index].served[1:] == server)
    taken, capped = _take(tuple(t for t in found if t not in same), items,
                          policy.escalate_max, stage.mode)
    settled = {**_skipped(same, base, decisions.SKIPPED_SAME_MODEL),
               **_skipped(capped, base, decisions.SKIPPED_CAP)}
    if not taken:
        return _HopDone(settled)
    got = await _BACKENDS[stage.mode](
        tuple(items[t.index] for t in taken),
        replace(call, chain=stage.chain, positions=tuple(t.index for t in taken)))
    # `got.results` and `got.failed` are by SUBSET position p (the hop's own
    # item order): item p is the batch's `taken[p].index`. Only `got.calls`,
    # built through `call.positions`, already name batch indices.
    errors = {p: error for unit, error in got.failed for p in unit}
    sent = _decision_calls._server(primary)
    for p, trigger in enumerate(taken):
        settled[trigger.index] = _settle_one(trigger, base.items[trigger.index],
                                             got.results[p], errors.get(p), policy, sent)
    return _HopDone(settled, got.rows, got.calls, got.served,
                    tuple(r.backend for r in got.results if r is not None))


def _checked_result(result: object, item: decisions.Item) -> None:
    """A caller result is None, or an `ItemResult` from one of `BACKENDS`
    whose question ids are exactly `item`'s (so `_merge` reads every one)."""
    if result is None:
        return
    if not isinstance(result, decisions.ItemResult) or result.backend not in decisions.BACKENDS:
        raise ValueError(f"a caller resolver's result must be an ItemResult from one of "
                         f"{decisions.BACKENDS}, not {result!r}")
    if set(result.answers) != {q.id for q in item.questions}:
        raise ValueError(f"a caller resolver's result answers {sorted(result.answers)}, "
                         f"not the item's questions")


def _checked_reply(reply: object, taken: tuple[decisions.Trigger, ...],
                   items: tuple[decisions.Item, ...]) -> decisions.ResolverReply:
    """A caller resolver's reply, held to its contract (01d §5.2): one result
    per item handed over (`_checked_result`), and every row it filed marked
    as the hop's -- a row that does not say so would be counted as the
    chain's own call. A `ValueError` otherwise."""
    if not isinstance(reply, decisions.ResolverReply):
        raise ValueError(f"a caller resolver returns a ResolverReply, not {reply!r}")
    if len(reply.results) != len(taken):
        raise ValueError(f"a caller resolver was handed {len(taken)} items and returned "
                         f"{len(reply.results)} results")
    for trigger, result in zip(taken, reply.results, strict=True):
        _checked_result(result, items[trigger.index])
    if not all(isinstance(row, dict) and row.get("hop") == decisions.HOP_ESCALATION
               for row in reply.rows):
        raise ValueError("every row a caller resolver files carries hop: escalation")
    return reply


def _row_record(call: _Call, row: dict, taken: tuple[decisions.Trigger, ...]
                ) -> decisions.CallRecord:
    """A caller resolver's ledger row as a `CallRecord` of the hop: every item
    it was handed, its backend as the row says (else `structured`, which a
    tool loop ending in a generated answer stamps), and its error kind."""
    mode = row.get("decision_mode")
    return decisions.CallRecord(
        stage=call.stage, mode=mode if mode in decisions.BACKENDS else _contracts.STRUCTURED,
        items=tuple(t.index for t in taken), row=row,
        error_kind=str(row.get("error") or "") if row.get("status") == "error" else "",
        hop=decisions.HOP_ESCALATION)


async def _caller_hop(call: _Call, items: tuple[decisions.Item, ...], base: decisions.Decision,
                      found: tuple[decisions.Trigger, ...], policy: store.routing.TaskPolicy,
                      resolver: Resolver) -> _HopDone:
    """The hop to a caller's `Resolver` (01d §5.2): at most `escalate_max`
    items, once, with no same-model check (the resolver is not one model).
    An `LLMError` it raises leaves every original standing; a cancel, and
    anything else, passes through."""
    taken, capped = found[:policy.escalate_max], found[policy.escalate_max:]
    settled = _skipped(capped, base, decisions.SKIPPED_CAP)
    try:
        reply = await resolver(tuple(items[t.index] for t in taken), taken)
    except LLMError as exc:
        for trigger in taken:
            settled[trigger.index] = _settle_one(trigger, base.items[trigger.index], None,
                                                 exc, policy, ())
        return _HopDone(settled)
    reply = _checked_reply(reply, taken, items)
    for trigger, result in zip(taken, reply.results, strict=True):
        settled[trigger.index] = _settle_one(trigger, base.items[trigger.index], result,
                                             None, policy, ())
    answered = [r for r in reply.results if r is not None]
    return _HopDone(settled, reply.rows,
                    tuple(_row_record(call, row, taken) for row in reply.rows),
                    tuple((r.served[1], r.served[2]) for r in answered if r.served),
                    tuple(r.backend for r in answered))


def _escalated(base: decisions.Decision, found: tuple[decisions.Trigger, ...],
               hop: _HopDone) -> decisions.Decision:
    """`base` with the hop laid over it (01d §5.5): each replaced item, the
    hop's rows after the base's in `usage` and its records after the base's
    in `calls`, `served` the union in first-answered order of the base's
    and every server that answered a hop call (whether or not the merge kept
    its answer), and `Decision.escalations` in `found`'s priority order.
    `errors` stay the base's: a failed hop is not an unanswered unit.
    `provider`/`model` stand only when one route answered every call, and
    `backend` only when one backend did -- `run_stages`' own rule."""
    items = list(base.items)
    for index, done in hop.settled.items():
        if done.merged is not None:
            items[index] = done.merged
    served = dict.fromkeys(base.served)
    served.update(dict.fromkeys(hop.served))
    provider, model = base.provider, base.model
    if len(served) != len(base.served):
        provider, model = next(iter(served)) if len(served) == 1 else ("", "")
    backend = base.backend if all(b == base.backend for b in hop.backends) else ""
    return replace(base, items=tuple(items), backend=backend, provider=provider, model=model,
                   usage=base.usage + hop.rows, served=tuple(served),
                   calls=base.calls + hop.calls,
                   escalations=tuple(hop.settled[t.index].escalation for t in found))


def _capped_stage(stage: Stage, max_tokens: int) -> Stage:
    """A structured stage's chain, each attempt output-capped (01g-S7); a
    native stage as it is (it sends no sampling)."""
    if stage.mode != _contracts.STRUCTURED:
        return stage
    chain = stage.chain
    return stage._replace(chain=wire.Chain(
        _requests._capped(chain.primary, max_tokens),
        None if chain.fallback is None else _requests._capped(chain.fallback, max_tokens)))
