"""077 commitment authority in one native campaign scope record, beside 055's calendar.

``CommitmentLibrary`` is trusted application code, director-facing, holding the outcome key: every offer
and step is a director decision committed as one compare-and-swap scope transaction that a human can
``preview`` and approve. A player-controlled obligor's own decision (accepting, refusing or giving up a
commitment) also needs the authorship key, as 021 asks for a player character's voluntary act: for
**every** obligor a player controls, that player's 003 contribution covering the ``commitment`` domain for
the step's scene and turn, and their endorsement of the exact step (``transition_fingerprint``). One
player never decides for another's character. Narration holds neither key.

Every step cites the 020 objective events it rests on; their support is re-read on every read, plan and
commit, so a revoked accepted event (a retcon) removes the steps it alone supported. An offer that names a
deadline links to a 055 clock, and may schedule it in the same write: the two receipts land together or not
at all. No wall clock, model or actor knowledge is involved; commitment records are canon for the director,
never an NPC's memory.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from grimoire.calendars import (
    CALENDAR_KEY, CalendarError, CalendarState, Deadline, Schedule, apply, state_from_payload, state_to_payload,
)
from grimoire.calendars import Moment as ClockMoment
from grimoire.chats import scope_from_payload
from grimoire.commitments import (
    COMMITMENTS_KEY, DECISIONS, NEXT, Authorship, ClockTime, Commitment, CommitmentError, CommitmentLedger, CommitmentState,
    DeadlineState, Offer, Transition, Void, fold, fold_one, ledger_from_payload, ledger_to_payload, transition_fingerprint,
    void_fingerprint,
)
from grimoire.commitments.model import STATUSES, cites_of, text
from grimoire.core import DIRECTOR
from grimoire.core.authority import CONTRIBUTION_DOMAINS, AuthorityError, PlayerContribution, controller_of
from grimoire.core.identity import ScopeRef
from grimoire.core.knowledge import Moment
from grimoire.core.model import ControlAssignment
from grimoire.effectstore import Endorsement
from grimoire.peersync.pairing import pairing_ended
from grimoire.security.policy import is_human
from grimoire.store import LibraryStore, ScopeRecord, StaleWrite
from grimoire.store.jsonfmt import thaw
from grimoire.store.layout import address_parts
from grimoire.sync.turns import open_conflicts

from .support import Support, read_support


def _clock(moment: ClockMoment | None) -> ClockTime | None:
    return None if moment is None else ClockTime(moment.fixed_day, moment.minute)


def _values(value: object, label: str) -> tuple:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise CommitmentError(f"{label} must be a collection")
    return tuple(value)


def deadlines_of(calendar: CalendarState | None) -> dict[str, DeadlineState]:
    """Each deadline the 055 calendar holds now, as the pure fold reads it (none without a calendar)."""
    if calendar is None:
        return {}
    return {d.deadline_id: DeadlineState(d.deadline_id, True, _clock(d.due), d.label, d.triggered_by is not None,
                                         d.triggered_by) for d in calendar.deadlines}


@dataclass(frozen=True)
class Due:
    """A deadline to schedule in the campaign's 055 calendar with the offer: a native date, minute and label."""
    native_date: str
    label: str
    minute: int = 0


@dataclass(frozen=True)
class CommitmentView:
    """Director-only: the ledger, the calendar it reads deadlines from, and each commitment's state.

    ``commitments`` is as of ``as_of`` (``None``: everything recorded). ``problems`` names whatever made a
    citation unreadable; a state with ``certain=False`` rests on it.
    """
    scope: ScopeRef
    etag: str
    as_of: int | None
    ledger: CommitmentLedger | None
    calendar: CalendarState | None
    commitments: tuple[CommitmentState, ...]
    problems: tuple[str, ...]

    def commitment(self, commitment_id: str) -> CommitmentState | None:
        return next((state for state in self.commitments if state.commitment_id == commitment_id), None)


@dataclass(frozen=True)
class CommitmentChange:
    """An immutable plan. ``operation`` is what was (or, on a retry, had already been) recorded.

    On a retry ``already_applied`` is true and ``preview`` is empty. ``commit`` returns the current view.
    An abandoned plan reserves nothing.
    """
    library: CommitmentLibrary = field(repr=False, compare=False)
    scope: ScopeRef
    operation: Offer | Transition | Void
    already_applied: bool
    _envelope: ScopeRecord | None = field(repr=False)
    _expected: str = field(repr=False)

    def _transaction(self):
        txn = self.library.store.transaction()
        if self._envelope is not None:
            txn.put(self._envelope, self._expected)
        return txn

    def preview(self) -> dict[str, bytes]:
        return {} if self.already_applied else self._transaction().preview()

    def commit(self, approvals: Mapping[str, str] | None = None) -> CommitmentView:
        library, op = self.library, self.operation
        with library.store.lock:
            # Recheck even a no-write retry: a conflict or a replaced history may have arrived since planning.
            current, record, ledger, calendar = library._read_scope(self.scope)
            old = None if ledger is None else ledger.operation(op.operation_id)
            if old is not None:
                if old.request() != op.request():
                    raise CommitmentError("operation changed since planning", "operation_mismatch")
                return library.read(self.scope, requested_by=DIRECTOR)
            if self.already_applied:
                raise CommitmentError("operation no longer belongs to the retained history", "history_changed")
            # A concurrent write is a stale plan, never a retcon: report it as one before rechecking support.
            library._fresh(self.scope, current, self._expected)
            # The scope's compare-and-swap guards the ledger; the citations live elsewhere, so a retcon that
            # landed after planning must still refuse this step rather than record it on undone fiction.
            planned = CommitmentLedger(self.scope.campaign_id, (ledger.operations if ledger else ()) + (op,))
            if not isinstance(op, Void):
                support = library._support(planned)
                state = fold_one(planned, op.commitment_id, support.values, deadlines_of(calendar))
                assert state is not None
                step = state.offer if isinstance(op, Offer) else next(s for s in state.steps if s.operation is op)
                if step.support != "supported" or step.outcome != "applied" or not state.certain:
                    raise CommitmentError("a cited event's support changed since planning", "citation_changed")
            self._transaction().commit(library.handle, approvals=approvals)
            return library.read(self.scope, requested_by=DIRECTOR)


class CommitmentLibrary:
    """Trusted application interface over an open V2 store, holding the store's trusted apply handle.

    ``issued`` is the authorizer's check that a handle is one it made: the outcome key and each player's
    endorsement handle must pass it (as in 021). Reads are director-only and create nothing.
    """

    def __init__(self, store: LibraryStore, handle: object, *, issued: Callable[[object], bool]) -> None:
        if not callable(issued) or not (issued(handle) and getattr(handle, "trusted", False) is True):
            raise AuthorityError("the outcome key must be a trusted handle issued by this library's authorizer")
        self.store = store
        self.handle = handle
        self._issued = issued

    # -- reading ------------------------------------------------------------------------------

    def _read_scope(self, scope: ScopeRef):
        if type(scope) is not ScopeRef:
            raise CommitmentError("commitment scope must be a ScopeRef")
        path = "/".join(address_parts(scope))
        found, complete = open_conflicts(self.store, path, (path,),
                                         ended=lambda peer: pairing_ended(self.store, peer))
        if found or not complete:
            raise CommitmentError("campaign scope has unresolved or unreadable sync conflicts", "scope_conflict")
        stored = self.store.read(scope)
        record = stored.record
        scope_from_payload(scope, record.schema, record.payload)
        ledger = None if COMMITMENTS_KEY not in record.payload else ledger_from_payload(
            scope.campaign_id, record.payload[COMMITMENTS_KEY])
        try:
            calendar = None if CALENDAR_KEY not in record.payload else state_from_payload(
                scope.campaign_id, record.payload[CALENDAR_KEY])
        except CalendarError as error:
            raise CommitmentError(f"the campaign calendar cannot be read: {error.reason}", "calendar_unreadable") from None
        return stored.etag, record, ledger, calendar

    def _support(self, ledger: CommitmentLedger | None, extra: Iterable[str] = ()) -> Support:
        cited = {event_id for op in (ledger.operations if ledger else ()) for event_id in getattr(op, "cites", ())}
        return read_support(self.store, ledger.campaign_id if ledger else "", cited | set(extra),
                            requested_by=DIRECTOR) \
            if ledger is not None else Support({}, {}, ())

    def read(self, scope: ScopeRef, *, requested_by: str, as_of: int | None = None) -> CommitmentView:
        """Director-only. Every commitment offered by ``as_of`` and where it stands, with its deadline."""
        if requested_by != DIRECTOR:
            raise AuthorityError("commitments are the director's record; an actor reads what it perceived")
        if as_of is not None and (type(as_of) is not int or as_of < 0):
            raise CommitmentError("as_of must be a non-negative int or None")
        with self.store.lock:
            etag, _, ledger, calendar = self._read_scope(scope)
            support = self._support(ledger)
            states = fold(ledger, support.values, deadlines_of(calendar), as_of=as_of)
            return CommitmentView(scope, etag, as_of, ledger, calendar, states, support.problems)

    # -- planning -----------------------------------------------------------------------------

    def offer(self, scope: ScopeRef, operation_id: str, commitment: Commitment, *, cites: Iterable[str],
              expected: str, due: Due | None = None) -> CommitmentChange:
        """Open ``commitment`` where the cited events offered it, optionally scheduling its deadline now."""
        if type(commitment) is not Commitment:
            raise CommitmentError("offer takes a Commitment")
        if due is not None and type(due) is not Due:
            raise CommitmentError("due must be a Due or None")
        cites = _values(cites, "cites")
        with self.store.lock:
            etag, record, ledger, calendar = self._read_scope(scope)
            probe = Offer(operation_id, commitment, cites, Moment(0), schedules=due is not None)
            retry = self._retry(scope, ledger, operation_id, probe.request())
            if retry is not None:
                if due is not None and not self._scheduled(calendar, operation_id, commitment, due):
                    raise CommitmentError("operation id already belongs to a different deadline", "operation_mismatch")
                return retry
            self._fresh(scope, etag, expected)
            if commitment.campaign_id != scope.campaign_id:
                raise CommitmentError("the commitment belongs to another campaign", "campaign_mismatch")
            if ledger is not None and ledger.offer(commitment.commitment_id) is not None:
                raise CommitmentError("commitment id already exists", "duplicate_commitment")
            known = set(self.store.campaign_instances(scope.campaign_id))
            if not set(commitment.owed_by + commitment.owed_to) <= known:
                raise CommitmentError("a party is not an instance of this campaign", "unknown_party")
            calendar_after = self._deadline(calendar, operation_id, commitment, due)
            at, support = self._cited(scope.campaign_id, ledger, probe.cites)
            op = Offer(operation_id, commitment, probe.cites, at, None if calendar is None else _clock(calendar.now),
                       due is not None)
            return self._plan(scope, record, etag, ledger, op, calendar_after if due is not None else None)

    def record(self, scope: ScopeRef, operation_id: str, commitment_id: str, status: str, *,
               cites: Iterable[str], expected: str, note: str = "", controls: Iterable[ControlAssignment] = (),
               contributions: Iterable[PlayerContribution] = (),
               endorsements: Iterable[Endorsement] = ()) -> CommitmentChange:
        """Record the next step of a commitment (see ``commitments.NEXT``).

        A decision (``DECISIONS``) needs ``controls``, the campaign's current 003 control assignments, and,
        for every obligor a player controls, that player's contribution and endorsement.
        """
        cites = cites_of(_values(cites, "cites"))
        if type(status) is not str or status not in STATUSES:
            raise CommitmentError("unknown commitment status")
        text(note, "step note", empty=True)
        controls, contributions = _values(controls, "controls"), _values(contributions, "contributions")
        endorsements = _values(endorsements, "endorsements")
        with self.store.lock:
            etag, record, ledger, calendar = self._read_scope(scope)
            probe = Transition(operation_id, commitment_id, status, cites, Moment(0), note) \
                if status not in DECISIONS else None
            request = ("transition", commitment_id, status, cites, note)
            retry = self._retry(scope, ledger, operation_id, request)
            if retry is not None:
                return retry
            self._fresh(scope, etag, expected)
            offer = None if ledger is None else ledger.offer(commitment_id)
            if offer is None:
                raise CommitmentError("no such commitment", "unknown_commitment")
            at, support = self._cited(scope.campaign_id, ledger, cites)
            state = fold_one(ledger, commitment_id, support.values, deadlines_of(calendar))
            assert state is not None  # offered, and as_of is everything
            if state.status in ("unsupported", "voided"):
                raise CommitmentError(f"the commitment is {state.status}", "unsupported_commitment")
            if not state.certain:
                raise CommitmentError("the commitment's history cannot be fully checked", "uncertain_history")
            if status not in NEXT.get(state.status, frozenset()):
                raise CommitmentError(f"{state.status} cannot become {status}", "illegal_transition")
            if at.sequence < state.moment.sequence:
                raise CommitmentError("a step cannot be earlier than the commitment's last step", "out_of_order")
            clock = None if calendar is None else _clock(calendar.now)
            if probe is not None:
                if controls or contributions or endorsements:
                    raise CommitmentError("an adjudicated outcome takes no player backing", "unexpected_backing")
                op = Transition(operation_id, commitment_id, status, cites, at, note, clock)
            else:
                pairs = self._controls(scope.campaign_id, offer.commitment, controls)
                fingerprint = transition_fingerprint(scope.campaign_id, commitment_id, status, cites, note)
                needed = {actor: who for actor, who in pairs if who != DIRECTOR}
                authorship = self._backing(scope.campaign_id, needed, at, contributions, endorsements, fingerprint)
                op = Transition(operation_id, commitment_id, status, cites, at, note, clock, pairs, authorship)
            return self._plan(scope, record, etag, ledger, op)

    def void(self, scope: ScopeRef, operation_id: str, target: str, *, reason: str, expected: str,
             endorsements: Iterable[Endorsement] = ()) -> CommitmentChange:
        """Withdraw an offer or step recorded in error. Every player who backed that step must endorse
        ``void_fingerprint``. Voiding an offer withdraws the commitment, and with it every player decision
        on it not already voided, so it needs those players' endorsements; its deadline is reported orphaned."""
        text(reason, "void reason")
        endorsements = _values(endorsements, "endorsements")
        with self.store.lock:
            etag, record, ledger, calendar = self._read_scope(scope)
            retry = self._retry(scope, ledger, operation_id, ("void", target, reason))
            if retry is not None:
                return retry
            self._fresh(scope, etag, expected)
            found = None if ledger is None else ledger.operation(target)
            if found is None or isinstance(found, Void) or target in ledger.voids():  # type: ignore[union-attr]
                raise CommitmentError("nothing voidable has that operation id", "invalid_void")
            backers = ledger.backers(target)  # type: ignore[union-attr]
            needed = {a.contribution_id: a.submitted_by for a in backers}
            fingerprint = void_fingerprint(scope.campaign_id, target, reason)
            given = {e.contribution_id: e for e in endorsements if type(e) is Endorsement}
            if len(given) != len(endorsements) or set(given) != {a.contribution_id for a in backers}:
                raise CommitmentError("a void needs the endorsement of every player who backed the step",
                                      "not_endorsed")
            authorship = []
            for item in backers:
                endorsement = given[item.contribution_id]
                self._check_handle(endorsement, needed[item.contribution_id], fingerprint)
                authorship.append(Authorship(item.contribution_id, item.actor_id, item.submitted_by, item.kind,
                                             item.scene_id, item.turn_id, item.text, fingerprint))
            op = Void(operation_id, target, reason, tuple(authorship))
            return self._plan(scope, record, etag, ledger, op)

    # -- helpers ------------------------------------------------------------------------------

    def _retry(self, scope: ScopeRef, ledger: CommitmentLedger | None, operation_id: str, request: tuple):
        old = None if ledger is None else ledger.operation(operation_id)
        if old is None:
            return None
        if old.request() != request:
            raise CommitmentError("operation id already belongs to a different request", "operation_mismatch")
        return CommitmentChange(self, scope, old, True, None, "")

    def _fresh(self, scope: ScopeRef, etag: str, expected: str) -> None:
        if expected != etag:
            raise StaleWrite([("/".join(address_parts(scope)), "campaign changed; reread before planning")])

    def _deadline(self, calendar: CalendarState | None, operation_id: str, commitment: Commitment,
                  due: Due | None) -> CalendarState | None:
        if commitment.deadline_id is None:
            if due is not None:
                raise CommitmentError("a due date needs the commitment's deadline id", "no_deadline_id")
            return calendar
        if calendar is None:
            raise CommitmentError("campaign calendar is not initialized", "uninitialized")
        exists = any(d.deadline_id == commitment.deadline_id for d in calendar.deadlines)
        if due is None:
            if not exists:
                raise CommitmentError("no such deadline in the campaign calendar", "unknown_deadline")
            return calendar
        try:
            deadline = Deadline(commitment.deadline_id, ClockMoment(calendar.calendar.parse(due.native_date),
                                                                    due.minute), due.label)
            after, _, already = apply(calendar.campaign_id, calendar, Schedule(f"commitment-{operation_id}", deadline))
        except CalendarError as error:
            raise CommitmentError(f"the deadline cannot be scheduled: {error}", error.reason) from None
        if already:  # never adopt a calendar operation this write did not make as the offer's schedule
            raise CommitmentError("the calendar already has an operation with this id", "operation_mismatch")
        return after

    def _scheduled(self, calendar: CalendarState | None, operation_id: str, commitment: Commitment,
                   due: Due) -> bool:
        """Whether this offer's own calendar receipt scheduled exactly ``due`` (a retry must repeat it)."""
        receipt = None if calendar is None else calendar.receipt(f"commitment-{operation_id}")
        if receipt is None or commitment.deadline_id is None:
            return False
        try:
            wanted = Deadline(commitment.deadline_id, ClockMoment(calendar.calendar.parse(due.native_date),  # type: ignore[union-attr]
                                                                  due.minute), due.label)
        except (CalendarError, TypeError, ValueError):
            return False
        return receipt.operation == Schedule(f"commitment-{operation_id}", wanted)

    def _cited(self, campaign_id: str, ledger: CommitmentLedger | None, cites: tuple[str, ...]):
        support = self._support(ledger, cites) if ledger is not None else read_support(self.store, campaign_id, cites,
                                                                                     requested_by=DIRECTOR)
        for event_id in cites:
            value = support.values[event_id]
            if value != "supported":
                raise CommitmentError(f"cited event {event_id} is {value}", f"citation_{value}")
        latest = max((support.events[event_id] for event_id in cites),
                     key=lambda event: (event.moment.sequence, event.event_id))
        return latest.moment, support

    def _controls(self, campaign_id: str, commitment: Commitment, controls: tuple) -> tuple[tuple[str, str], ...]:
        """Each obligor's current controller (``director`` for none), from the caller's 003 assignments."""
        if not all(type(c) is ControlAssignment for c in controls):
            raise CommitmentError("controls must be 003 ControlAssignment values")
        pairs = []
        for ref in commitment.owed_by:
            controller = controller_of(campaign_id, ref.instance_id, controls)  # ambiguity: 003's AuthorityError
            controller = DIRECTOR if controller is None else controller
            if controller != DIRECTOR and not is_human(controller):
                raise CommitmentError("an obligor's controller is not a human", "non_human_controller")
            pairs.append((ref.instance_id, controller))
        return tuple(sorted(pairs))

    def _backing(self, campaign_id: str, needed: Mapping[str, str], at: Moment, contributions: tuple,
                 endorsements: tuple, fingerprint: str) -> tuple[Authorship, ...]:
        """One contribution and one endorsement per player-controlled obligor, and nothing else."""
        if not all(type(c) is PlayerContribution for c in contributions) \
                or not all(type(e) is Endorsement for e in endorsements):
            raise CommitmentError("contributions and endorsements must be 003/021 values")
        if not needed:
            if contributions or endorsements:
                raise CommitmentError("no obligor is a player's: the decision takes no backing", "unexpected_backing")
            return ()
        if at.scene_id is None or at.turn_id is None:
            raise CommitmentError("a player's decision must cite an event of a scene turn", "no_turn")
        by_actor: dict[str, PlayerContribution] = {}
        for contribution in contributions:
            if (contribution.campaign_id, contribution.scene_id, contribution.turn_id) != (
                    campaign_id, at.scene_id, at.turn_id) or contribution.actor_id not in needed \
                    or contribution.submitted_by != needed[contribution.actor_id] \
                    or "commitment" not in CONTRIBUTION_DOMAINS[contribution.kind] \
                    or contribution.actor_id in by_actor:
                raise CommitmentError("a contribution is not one obligor's own commitment for this scene and "
                                      "turn", "pc_authorship")
            by_actor[contribution.actor_id] = contribution
        if set(by_actor) != set(needed):
            raise CommitmentError("every player-controlled obligor must back the decision", "pc_authorship")
        given = {e.contribution_id: e for e in endorsements}
        wanted = {c.contribution_id for c in by_actor.values()}
        if len(given) != len(endorsements) or set(given) != wanted:
            raise CommitmentError("each backing contribution needs exactly its player's endorsement", "not_endorsed")
        records = []
        for actor, contribution in sorted(by_actor.items()):
            self._check_handle(given[contribution.contribution_id], needed[actor], fingerprint)
            records.append(Authorship(contribution.contribution_id, actor, contribution.submitted_by,
                                      contribution.kind, contribution.scene_id, contribution.turn_id,
                                      contribution.text, fingerprint))
        return tuple(records)

    def _check_handle(self, endorsement: Endorsement, principal: str, fingerprint: str) -> None:
        handle = endorsement.handle
        if not (self._issued(handle) and getattr(handle, "trusted", False) is True and is_human(principal)
                and getattr(handle, "principal", None) == principal and endorsement.fingerprint == fingerprint):
            raise CommitmentError("an endorsement is not valid for this step", "invalid_endorsement")

    def _plan(self, scope: ScopeRef, record: ScopeRecord, etag: str, ledger: CommitmentLedger | None,
              op: Offer | Transition | Void, calendar: CalendarState | None = None) -> CommitmentChange:
        """``calendar`` is the calendar this write schedules a deadline into; ``None`` leaves it as stored."""
        after = (ledger or CommitmentLedger(scope.campaign_id, ())).appended(op)
        payload = thaw(record.payload)
        payload[COMMITMENTS_KEY] = ledger_to_payload(after)
        if calendar is not None:
            payload[CALENDAR_KEY] = state_to_payload(calendar)
        envelope = ScopeRecord(scope, record.schema, payload, owner=record.owner, extra=record.extra)
        change = CommitmentChange(self, scope, op, False, envelope, etag)
        # Preview enforces the native size/encoding limits now. Never drop an old operation to fit: a
        # forgotten operation id would let an old retry record a step twice.
        change.preview()
        return change

