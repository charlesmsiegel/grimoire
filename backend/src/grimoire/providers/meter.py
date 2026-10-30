"""One turn's provider bookkeeping: the only path by which attempts are booked.

``TurnUsageLedger`` is an immutable value; a meter holds the turn's current
value, so an attempt is recorded the moment it ends and a failure later in the
same call cannot lose it. Use exactly one meter per turn. A meter is
single-threaded: a second ``execute`` while one is running is refused, not
queued.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from grimoire.core.inference import (
    CallAttempt, Measurement, OptionalRequest, PlannedCall, TokenUsage, TurnInferencePlan,
    TurnUsageLedger, TurnUsageSummary, UnplannedCallError,
)
from grimoire.core.inference.plan import CallId

from .errors import CallRefused

CallStatus = Literal["accepted", "incomplete", "failed", "cancelled", "refused", "poisoned"]
BookingResult = Literal["precise", "fallback", "poisoned"]
FALLBACK_NOTE = "booking fell back to unknown usage"


@dataclass(frozen=True, slots=True)
class AttemptReport:
    """What one attempt did, for people and for 015's comparisons. No prompt or reply text."""

    call_id: CallId
    attempt: int
    route: str
    outcome: str
    usage: TokenUsage
    measurement: Measurement
    error_kind: str | None = None
    finish_reason: str | None = None
    served_model: str | None = None
    delivered: bool = False
    peer_loopback: bool | None = None
    first_byte_s: float | None = None
    total_s: float | None = None
    latency_measurement: Literal["measured", "synthetic"] = "synthetic"
    usage_note: str | None = None


@dataclass
class _CallState:
    reports: list[AttemptReport] = field(default_factory=list)
    status: CallStatus | None = None
    request_digest: str | None = None
    delivered: bool = False
    poisoned: bool = False


class TurnMeter:
    """Holds a turn's ledger and, per call, its reports, status and delivery."""

    def __init__(self, ledger: TurnUsageLedger, *,
                 _fail_booking: Callable[[str], bool] | None = None) -> None:
        if not isinstance(ledger, TurnUsageLedger):
            raise ValueError("a meter needs a TurnUsageLedger")
        self._ledger = ledger
        self._calls: dict[CallId, _CallState] = {}
        self._busy = threading.Lock()
        self._pending_digest: str | None = None
        self._fail_booking = _fail_booking  # tests only: force a booking stage to fail

    # -- public view --------------------------------------------------------

    @property
    def ledger(self) -> TurnUsageLedger:
        return self._ledger

    def summary(self) -> TurnUsageSummary:
        return self._ledger.summary()

    def reports(self, call_id: CallId) -> tuple[AttemptReport, ...]:
        state = self._calls.get(call_id)
        return tuple(state.reports) if state else ()

    def status(self, call_id: CallId) -> CallStatus | None:
        state = self._calls.get(call_id)
        if state is None:
            return None
        return "poisoned" if state.poisoned else state.status

    def delivered(self, call_id: CallId) -> bool:
        state = self._calls.get(call_id)
        return bool(state and state.delivered)

    @property
    def all_accepted(self) -> bool:
        """Every planned call ended with an accepted reply (015's evidence rule)."""
        return all(self.status(call.call_id) == "accepted" for call in self._ledger.plan.calls)

    def next_attempt(self, call_id: CallId) -> int:
        return 1 + sum(1 for each in self._ledger.attempts if each.call_id == call_id)

    def extend(self, plan: TurnInferencePlan) -> None:
        self._ledger = self._ledger.extend(plan)

    def add_phase(self, phase: str, *, speaking_npc_ids: Iterable[str] = (),
                  optional_requests: Iterable[OptionalRequest] = ()) -> None:
        self._ledger = self._ledger.add_phase(
            phase, speaking_npc_ids=speaking_npc_ids, optional_requests=optional_requests)

    def __repr__(self) -> str:
        plan = self._ledger.plan
        return (f"TurnMeter({plan.campaign_id}/{plan.turn_id}, calls={len(plan.calls)}, "
                f"attempts={len(self._ledger.attempts)})")

    # -- adapter protocol ---------------------------------------------------

    def _begin(self, call: object, request_digest: str, redo_after_delivery: bool,
               routes_count: int) -> int:
        """Claim ``call`` for one execution; return the attempts it may still make."""
        if not self._busy.acquire(blocking=False):
            raise CallRefused("busy")
        try:
            if not isinstance(call, PlannedCall) or call not in self._ledger.plan.calls:
                raise UnplannedCallError(f"not a planned call of this turn: {call!r}")
            state = self._calls.setdefault(call.call_id, _CallState())
            if state.poisoned:
                raise CallRefused("poisoned")
            previous = [a for a in self._ledger.attempts if a.call_id == call.call_id]
            if any(a.outcome == "completed" for a in previous):
                raise CallRefused("completed")
            remaining = self._ledger.plan.policy.max_attempts_per_call - len(previous)
            if remaining < 1:
                raise CallRefused("exhausted")
            if state.request_digest is not None and state.request_digest != request_digest:
                raise CallRefused("request_changed")
            if state.delivered:
                if not redo_after_delivery:
                    raise CallRefused("delivered")
                state.delivered = False
            if routes_count > remaining:
                raise ValueError(f"{routes_count} routes but only {remaining} attempts left")
        except BaseException:
            self._busy.release()
            raise
        self._pending_digest = request_digest
        return remaining

    def _delivered(self, call_id: CallId) -> None:
        self._calls.setdefault(call_id, _CallState()).delivered = True

    def _book(self, call_id: CallId, attempt_no: int, route: str, *, sent: bool,
              measurement: Measurement, error_kind: str | None,
              build: Callable[[], tuple[CallAttempt, AttemptReport]]) -> BookingResult:
        """Record one attempt. Write-ahead: the call is poisoned until a record succeeds.

        If the precise attempt cannot be built or recorded, a fallback with
        unknown usage is booked instead. An interrupt leaves the call poisoned.
        """
        state = self._calls.setdefault(call_id, _CallState())
        state.poisoned = True
        result: BookingResult = "precise"
        try:
            if self._fail_booking and self._fail_booking("precise"):
                raise RuntimeError("forced precise booking failure")
            attempt, report = build()
            self._ledger = self._ledger.record(attempt)
        except Exception:
            result = "fallback"
            try:
                if self._fail_booking and self._fail_booking("fallback"):
                    raise RuntimeError("forced fallback booking failure")
                usage = TokenUsage(None, None) if sent else TokenUsage(0, 0)
                attempt = CallAttempt(call_id, attempt_no, usage,
                                      measurement if sent else Measurement.ESTIMATED,
                                      "failed" if sent else "not_sent", route)
                report = AttemptReport(call_id, attempt_no, route, attempt.outcome, usage,
                                       attempt.measurement, error_kind=error_kind,
                                       usage_note=FALLBACK_NOTE)
                self._ledger = self._ledger.record(attempt)
            except Exception:
                return "poisoned"
        state.reports.append(report)
        state.request_digest = self._pending_digest
        state.poisoned = False
        return result

    def _end(self, call_id: CallId, status: CallStatus | None) -> None:
        """Release the meter; ``None`` leaves the call's status as it was."""
        state = self._calls.setdefault(call_id, _CallState())
        if status is not None and not state.poisoned:
            state.status = status
        self._busy.release()
