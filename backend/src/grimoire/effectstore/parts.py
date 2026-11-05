"""The holdings and ability parts of accept, revoke and continuity (Sprint 021, next slice).

Holdings are 023 files changed only through ``HoldingsLibrary.apply``: 021 plans the effect's one
operation (or, to revoke, its inverse) there and folds the holder files into its own transaction. These
helpers keep that out of the service's main flow. They read only under the caller's store lock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from grimoire.core.identity import InstanceRef
from grimoire.effects import (
    NEVER_APPLIED, AbilityCharge, AcceptedEvent, AcceptedHoldings, EffectError, EffectProblem, EffectProposal,
    accepted_token, holdings_failures, holdings_op_id, holdings_orphans, parse_token, revoked_token,
)
from grimoire.holdings import Holdings, HoldingsCodecError, HoldingsRefused
from grimoire.holdingstore import HoldingsChange, HoldingsLibrary
from grimoire.library import LibraryPathError
from grimoire.store import LibraryStore, StoreError

from .errors import InvalidEffect, refused

_UNREAD_BY_DESIGN = frozenset({"name_mismatch", "unrecognized"})  # as 023: never a holder file
#: the forward entry was never on that holder's file (restored from before it). "not logged" is a hole in the log, a
#: hand edit (023's ``beyond_history``): no evidence that nothing is in force.
_ABSENT = frozenset({"holder restored"})


def _unread(code: str) -> bool:
    """A store problem under ``holdings/`` that may hide a holder file (and so an id or an orphan)."""
    return code not in _UNREAD_BY_DESIGN


def read_holders(holdings: HoldingsLibrary, holders: Iterable[InstanceRef]) -> dict[InstanceRef, Holdings | str]:
    """Each holder's holdings as filed, or why the file cannot be read (damage, never a mismatch)."""
    found: dict[InstanceRef, Holdings | str] = {}
    for holder in sorted(set(holders), key=str):
        try:
            found[holder] = holdings.view(holder).holdings
        except HoldingsCodecError as error:
            found[holder] = f"undecodable ({error.reason})"
        except (StoreError, LibraryPathError, OSError) as error:
            found[holder] = f"unreadable ({type(error).__name__})"
    return found


def refuse_unreadable(failures: Iterable[tuple[InstanceRef, str, bool]]) -> None:
    unreadable = [str(holder) for holder, _, damaged in failures if damaged]
    if unreadable:
        raise InvalidEffect("a holder file cannot be read, so the holdings receipt cannot be checked",
                            "undecodable_holdings", unreadable)


def duplicate_holdings(event: AcceptedEvent, holdings: HoldingsLibrary) -> str | None:
    """Why an active duplicate's holdings receipt no longer holds (``None``: it does). Damage refuses."""
    if event.holdings is None:
        return None
    failures = holdings_failures(event, read_holders(holdings, event.holdings.holdings.holders))
    refuse_unreadable(failures)
    return None if not failures else f"holdings: {failures[0][0]}: {failures[0][1]}"


def holder_tokens(store: LibraryStore, campaign_id: str) -> tuple[frozenset[str], bool]:
    """Event ids named by an exact 021 token anywhere in the raw payload of a campaign holder file, and
    whether some holder file could not be read at all (so it may name more)."""
    found = store.campaign_holdings(campaign_id)
    unread = any(_unread(problem.code) for problem in found.problems)
    ids: set[str] = set()
    stack: list[object] = [stored.record.payload for stored in found.records.values()]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            token = parse_token(value)
            if token is not None:
                ids.add(token[1])
        elif isinstance(value, Mapping):
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return frozenset(ids), unread


def check_attempts(proposal: EffectProposal, events: Iterable[AcceptedEvent], holdings: HoldingsLibrary) -> None:
    """At most one event holds an attempt: one attempt is charged and applied once.

    An active event holding it refuses (``attempt_reused``). A revoked event holding it refuses a late retry
    of the very same effect, and any reuse of any of its attempts while that revocation left its holdings
    change in force (``attempt_revoked``): a revision would spend again what was never given back. That is
    a skipped undo, and an applied undo whose receipt no holder file confirms (a revocation that landed its
    event file but not every holder file).
    """
    wanted, fingerprint = proposal.attempts, proposal.fingerprint
    if not wanted:
        return
    for event in sorted(events, key=lambda e: e.event_id):
        shared = sorted(wanted & event.attempts)
        if not shared:
            continue
        if event.active:
            raise InvalidEffect(f"attempt {shared[0][:64]!r} is already charged by {event.ref}", "attempt_reused",
                                (event.event_id,))
        if event.fingerprint == fingerprint or event.revocation.holdings_left \
                or _undo_unconfirmed(event, holdings):  # type: ignore[union-attr]
            raise InvalidEffect(f"attempt {shared[0][:64]!r} was revoked with {event.ref}; a new acceptance needs "
                                "a fresh attempt", "attempt_revoked", (event.event_id,))


def _undo_unconfirmed(event: AcceptedEvent, holdings: HoldingsLibrary) -> bool:
    if event.holdings is None or not event.revocation.holdings_seqs:  # type: ignore[union-attr]
        return False
    holders = read_holders(holdings, event.holdings.holdings.holders)
    failures = holdings_failures(event, holders)
    refuse_unreadable(failures)
    forward = event.holdings.seqs  # a holder restored from before the forward change holds none of it either
    return any(not (isinstance(held, Holdings) and held.seq < forward[holder])
               for holder, _, _ in failures for held in (holders[holder],))


def charges(proposal: EffectProposal, domains: tuple[str, ...]) -> tuple[AbilityCharge, ...]:
    """The charge of each ability use, refusing one that is not permitted (``ability_not_permitted``)."""
    try:
        return tuple(AbilityCharge.of(assessment, domain) for assessment, domain in zip(proposal.abilities, domains))
    except EffectError as error:
        raise refused(error) from None


def check_permitted(proposal: EffectProposal) -> None:
    for assessment in proposal.abilities:
        if not assessment.permitted:  # only a permitted use is ever charged (025)
            raise InvalidEffect("an ability use is not permitted as assessed", "ability_not_permitted",
                                (assessment.classification,))


def plan_holdings(holdings: HoldingsLibrary, proposal: EffectProposal, events: Iterable[AcceptedEvent],
                  event_id: str, domains: tuple[str, ...]) -> tuple[HoldingsChange, AcceptedHoldings] | None:
    """The holdings change of an acceptance and its receipt; ``None`` without a holdings part.

    A touched holder logging an entry whose exact 021 cause names an event that does not record it is the
    landed half of an unrecorded acceptance (``orphan_effect``): repairing it is a human decision.
    """
    part = proposal.holdings
    if part is None:
        return None
    holders = read_holders(holdings, part.holders)
    refuse_unreadable((holder, why, True) for holder, why in holders.items() if isinstance(why, str))
    orphaned = holdings_orphans(events, {h: v for h, v in holders.items() if isinstance(v, Holdings)})
    if orphaned:
        raise InvalidEffect("a holder logs a change of an effect no event records", "orphan_effect",
                            sorted({p.detail.removeprefix("holdings ") for p in orphaned}))
    op_id = holdings_op_id(proposal.campaign_id, proposal.fingerprint)
    try:
        change = holdings.apply(part.operation(op_id, accepted_token(event_id)))
    except HoldingsCodecError:
        raise InvalidEffect("a holder file cannot be decoded", "undecodable_holdings") from None
    except HoldingsRefused as error:
        if error.reason == "op_id_reused":  # the id derives from this effect: a hand-edited entry of it, unrecorded
            raise InvalidEffect("a holder logs an altered copy of this effect's holdings change", "orphan_effect",
                                ("holdings",)) from None
        raise InvalidEffect(f"the holdings change is refused: {error.reason}", error.reason, ("holdings",)) from None
    except EffectError as error:
        raise refused(error) from None
    if change.outcome != "applied":  # already logged under this derived id, yet no event records it
        raise InvalidEffect("a holder already logs this effect's holdings change", "orphan_effect",
                            sorted(str(holder) for holder in change.entries))
    seqs = {holder: entry.seq for holder, entry in change.entries.items()}
    try:
        return change, AcceptedHoldings(part, domains, op_id, seqs)
    except EffectError as error:
        raise refused(error) from None


def plan_undo(holdings: HoldingsLibrary, event: AcceptedEvent, partial: bool
              ) -> tuple[HoldingsChange | None, dict[InstanceRef, int], str | None]:
    """The inverse holdings change of a revocation: ``(change, seqs, skipped)``.

    The forward entry must still be logged (strict: an evicted one cannot be told from another operation's).
    The inverse is planned with the receipt as 023's ``basis``. A refusal is ``has_dependents`` (the coins were
    spent since, a count was edited, a holder is gone or paired), or with ``partial`` the undo is skipped and
    recorded. An inverse already logged by this very revocation on every holder (an earlier publication whose
    event file did not land) is adopted; logged on some holders only, it is ``receipt_mismatch`` (030 completes it).
    """
    accepted = event.holdings
    if accepted is None:
        return None, {}, None
    holders = read_holders(holdings, accepted.holdings.holders)
    inverse = accepted.holdings.inverse(accepted.op_id, revoked_token(event.event_id))
    adopted = {holder: entry.seq for holder, held in holders.items() if isinstance(held, Holdings)
               for entry in held.log if entry.operation == inverse}
    if len(adopted) == len(accepted.seqs):  # this revocation's own inverse landed everywhere: adopt it, first, so an
        return None, adopted, None          # evicted forward entry cannot turn a landed undo into a skipped one
    if adopted:  # the inverse landed on some holders only: a partial publication, whose completion is 030's
        why = f"partial_publication: the undo is logged by {len(adopted)} of {len(accepted.seqs)} holders"
        if not partial:
            raise InvalidEffect(f"the holdings of {event.ref} were partly undone: {why}", "receipt_mismatch",
                                ("holdings",))
        return None, {}, why
    failures = holdings_failures(event, holders, strict=True, undoing=True)
    refuse_unreadable(failures)
    if failures:
        why = f"receipt: {failures[0][0]}: {failures[0][1]}"
        if len(failures) == len(accepted.seqs) and all(reason in _ABSENT for _, reason, _ in failures):
            why = f"{NEVER_APPLIED}: {why}"  # no holder logs the forward change: nothing to undo, nothing held
        if not partial:
            raise InvalidEffect(f"the holdings of {event.ref} do not match the holders: {why}", "receipt_mismatch",
                                ("holdings",))
        return None, {}, why
    try:
        change = holdings.apply(inverse, basis=dict(accepted.seqs))
    except HoldingsCodecError:
        raise InvalidEffect("a holder file cannot be decoded", "undecodable_holdings") from None
    except HoldingsRefused as error:
        why = f"has_dependents: {error.reason}"
        if not partial:
            raise InvalidEffect(f"the holdings of {event.ref} cannot be undone: {error.reason}", "has_dependents",
                                ("holdings",)) from None
        return None, {}, why
    seqs = {holder: entry.seq for holder, entry in change.entries.items()}
    return (change if change.outcome == "applied" else None), seqs, None


def campaign_holders(holdings: HoldingsLibrary, campaign_id: str, events: Iterable[AcceptedEvent]
                     ) -> tuple[dict[InstanceRef, Holdings | str], dict[InstanceRef, Holdings], list[EffectProblem]]:
    """Every holder file of the campaign: each holder's holdings or why it cannot be read, the decoded ones, and
    an ``undecodable_holdings`` problem for every holder file that cannot be read (it may hide an orphan).

    A holder an event's receipt names but with no file is read as accept and revoke read it (023's empty
    holdings), so all three name the same failure.
    """
    found = holdings.campaign_holdings(campaign_id)
    decoded = {holder: view.holdings for holder, view in found.views.items()}
    held: dict[InstanceRef, Holdings | str] = dict(decoded)
    damaged = []
    for problem in found.problems:  # the same problems ``holder_tokens`` counts as unread, so never a silent all-clear
        if problem.code == "undecodable" or (problem.code == "unrecognized" and _unread(problem.reason or "")):
            damaged.append(EffectProblem("undecodable_holdings", detail=f"holdings file {problem.path}: "
                                                                        f"{problem.reason}"))
        if problem.code == "undecodable" and problem.holder is not None:
            held[problem.holder] = f"undecodable ({problem.reason})"
    named = {holder for event in events if event.holdings is not None for holder in event.holdings.holdings.holders}
    held.update(read_holders(holdings, named - held.keys()))
    return held, decoded, damaged


def holdings_actors(proposal: EffectProposal) -> frozenset[InstanceRef]:
    """Every individual the proposal's holdings and ability uses name."""
    found = frozenset() if proposal.holdings is None else proposal.holdings.actors()
    return found | {attempt.actor for attempt in proposal.ability_attempts}


def acting_agents(proposal: EffectProposal) -> frozenset[InstanceRef]:
    """The agents of the proposal's holdings lines and its ability users: their instance files are pinned."""
    found = frozenset() if proposal.holdings is None else proposal.holdings.agents()
    return found | {attempt.actor for attempt in proposal.ability_attempts}


def recheck(changes: Iterable[HoldingsChange]) -> None:
    """Under the store lock: every holder still has the instance file it was planned against."""
    for change in changes:
        change.recheck()  # StaleWrite; HolderReplaced is never retried automatically
