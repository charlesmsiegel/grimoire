"""Issue one planned model call through an authorized route, booking every attempt.

The adapter never decides that a call should happen: the turn plan does. It
refuses calls the plan lacks, sends only bytes its authorizer allowed to the
endpoint trusted configuration names, books each attempt on the turn's meter
the moment it ends (whatever ended it), and accepts a reply only when the
provider said it stopped normally. After any text reached a consumer, a
failure ends the call: nothing is silently redone. See ``docs/PROVIDERS.md``.
"""

from __future__ import annotations

import json
import re
import threading
import time
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from grimoire.core.inference import CallAttempt, Measurement, PlannedCall, TokenUsage
from grimoire.security import Authorizer, Decision, Permission, WorkerHandle

from .cancel import CancelToken
from .config import (
    AdapterLimits, Credentials, ProviderEndpoint, ProviderRegistry, RouteChoice, Secret,
    provider_resource, route_label,
)
from .errors import RETRYABLE, CallRefused, ProviderError, TransportError
from .meter import AttemptReport, CallStatus, TurnMeter
from .request import ModelRequest, envelope, open_envelope, request_body, request_digest
from .sse import (
    DIAGNOSTIC_LIMIT, StreamState, classify_non_sse, error_from_object, feed_line, finished,
    normalize_usage,
)
from .transport import HttpTransport, Transport

ERROR_BODY_LIMIT = 64 * 1024
ResultStatus = Literal["accepted", "incomplete", "failed", "cancelled", "refused"]
Approvals = Callable[[str, int, bytes], "str | None"]


@dataclass(frozen=True, slots=True)
class CallResult:
    """How one ``execute`` ended. ``reply`` is set only when ``status == "accepted"``."""

    status: ResultStatus
    reply: str | None = field(default=None, repr=False)
    finish_reason: str | None = None
    error: ProviderError | None = None
    refusal_reason: str | None = None
    attempts: tuple[AttemptReport, ...] = ()


@dataclass
class _Attempt:
    outcome: str = "not_sent"
    error: ProviderError | None = None
    state: StreamState = field(default_factory=StreamState)
    booking: str = "precise"
    report: AttemptReport | None = None


def _status_kind(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 402:
        return "account_limit"
    if status in (408, 504):
        return "timeout"
    if status == 429:
        return "rate_limit"
    if 400 <= status <= 499:
        return "bad_request"
    if 500 <= status <= 599:
        return "server_error"
    return "bad_response"


def _retry_after(value: str | None) -> int | None:
    """Delta-seconds only; dates, negatives and junk are ignored."""
    if value is not None and re.fullmatch(r"[0-9]{1,9}", value.strip()):
        return int(value.strip())
    return None


def _redact(text: str | None, key: Secret | None) -> str | None:
    """Redact the key from the whole text, then truncate (never the other way round)."""
    if text is None:
        return None
    if key is not None:
        text = text.replace(key.reveal(), "<redacted>")
    return text[:DIAGNOSTIC_LIMIT]


def _sanitize(error: ProviderError, key: Secret | None) -> ProviderError:
    """Provider-chosen fields must not carry the key into ``repr`` or reports."""
    secret = key.reveal() if key is not None else None
    for name in ("error_code", "error_type"):
        if secret is not None and secret in (getattr(error, name) or ""):
            setattr(error, name, None)
    error.diagnostic = _redact(error.diagnostic, key)
    return error


def _default_wait(token: CancelToken, seconds: float) -> bool:
    return token.wait(seconds)


class ProviderAdapter:
    """Trusted configuration (endpoints, keys, authorizer) is fixed here, not per call."""

    def __init__(self, registry: ProviderRegistry, credentials: Credentials,
                 authorizer: Authorizer, transport: Transport, *,
                 clock: Callable[[], float] = time.monotonic,
                 wait: Callable[[CancelToken, float], bool] = _default_wait,
                 limits: AdapterLimits = AdapterLimits()) -> None:
        if not isinstance(registry, ProviderRegistry):
            raise ValueError("registry must be a ProviderRegistry")
        if not isinstance(authorizer, Authorizer):
            raise ValueError("authorizer must be an Authorizer")
        self._registry = registry
        self._credentials = credentials
        self._authorizer = authorizer
        self._transport = transport
        self._clock = clock
        self._wait = wait
        self._limits = limits
        self._authorize_lock = threading.Lock()  # approval consumption is not thread-safe
        # One meter per turn: a second meter would let a call be issued twice.
        self._meters: dict[tuple[str, str], weakref.ref[TurnMeter]] = {}

    def __repr__(self) -> str:
        return f"ProviderAdapter({self._registry!r}, transport={type(self._transport).__name__})"

    # Small hooks, so the fault explorer can check that each invariant can fail.

    def _measurement(self) -> Measurement:
        """Provider-reported only over the real HTTP transport (exact type)."""
        if type(self._transport) is HttpTransport:
            return Measurement.PROVIDER_REPORTED
        return Measurement.SYNTHETIC

    def _retryable(self, kind: str | None, delivered: bool) -> bool:
        return kind in RETRYABLE and not delivered

    def _accept(self, state: StreamState) -> bool:
        return state.finish_reason == "stop"

    # -- public -------------------------------------------------------------

    def execute(self, meter: TurnMeter, call: PlannedCall, request: ModelRequest,
                routes: Sequence[RouteChoice], handle: WorkerHandle, *,
                approvals: Approvals | None = None,
                on_delta: Callable[[str], None] | None = None,
                cancel: CancelToken | None = None,
                redo_after_delivery: bool = False) -> CallResult:
        """Run one planned call; see the module docstring and ``docs/PROVIDERS.md``."""
        if not isinstance(meter, TurnMeter):
            raise ValueError("execute needs the turn's TurnMeter")
        if not isinstance(request, ModelRequest):
            raise ValueError("request must be a ModelRequest")
        routes = tuple(routes)
        if not routes or not all(isinstance(route, RouteChoice) for route in routes):
            raise ValueError("routes must be a nonempty sequence of RouteChoice")
        for route in routes:
            if route.provider_id not in self._registry:
                raise ValueError(f"unknown provider {route.provider_id!r}")
        if type(handle) is not WorkerHandle or not self._authorizer.issued(handle):
            raise CallRefused("handle")
        plan = meter.ledger.plan
        turn = (plan.campaign_id, plan.turn_id)
        known = self._meters.get(turn)
        if known is not None and known() is not None and known() is not meter:
            raise CallRefused("second meter for this turn")
        self._meters = {key: ref for key, ref in self._meters.items() if ref() is not None}
        self._meters[turn] = weakref.ref(meter)
        cancel = cancel if cancel is not None else CancelToken()
        digest = request_digest(request)
        booked_before = len(meter.ledger.attempts)
        status: CallStatus | None = None
        remaining = meter._begin(call, digest, redo_after_delivery, len(routes))
        try:
            result = self._run(meter, call, request, routes, handle, approvals, on_delta,
                               cancel, remaining)
            status = result.status
            return result
        finally:
            if status is None and len(meter.ledger.attempts) > booked_before:
                status = "failed"  # an exception escaped after an attempt was booked
            meter._end(call.call_id, status)  # None: nothing happened, status unchanged

    # -- internals ----------------------------------------------------------

    def _run(self, meter: TurnMeter, call: PlannedCall, request: ModelRequest,
             routes: tuple[RouteChoice, ...], handle: WorkerHandle,
             approvals: Approvals | None, on_delta: Callable[[str], None] | None,
             cancel: CancelToken, remaining: int) -> CallResult:
        reports: list[AttemptReport] = []

        def result(status: ResultStatus, **kw) -> CallResult:
            return CallResult(status, attempts=tuple(reports), **kw)

        for index in range(remaining):
            choice = routes[min(index, len(routes) - 1)]
            endpoint = self._registry.get(choice.provider_id)
            label = route_label(endpoint, choice.model)
            number = meter.next_attempt(call.call_id)
            if cancel.cancelled:
                return result("cancelled")
            authorized = envelope(endpoint.completions_url,
                                  request_body(request, endpoint, choice.model))
            approval_id = approvals(label, number, authorized) if approvals else None
            resource = provider_resource(endpoint.provider_id)
            with self._authorize_lock:
                decision = handle.authorize(Permission.EXECUTE, str(resource), payload=authorized,
                                            approval_id=approval_id)
            if (type(decision) is not Decision or decision.allowed is not True
                    or decision.resource != resource):
                return result("refused", refusal_reason=str(getattr(decision, "reason", "denied")))
            url, body = open_envelope(authorized)  # send exactly what was authorized
            key = None
            if endpoint.needs_key:
                try:
                    key = self._credentials.api_key(endpoint.provider_id)
                except Exception:
                    return result("refused", refusal_reason="bad_key")
                if key is None:
                    return result("refused", refusal_reason="missing_key")
                if not isinstance(key, Secret):
                    return result("refused", refusal_reason="bad_key")

            attempt = self._attempt(meter, call, number, label, endpoint, url, body, key,
                                    on_delta, cancel)
            reports.append(attempt.report)
            state = attempt.state
            if attempt.booking != "precise":
                return result("failed", error=attempt.error,
                              refusal_reason="booking_failed")
            if attempt.outcome == "completed":
                if self._accept(state):
                    return result("accepted", reply=state.text, finish_reason=state.finish_reason)
                return result("incomplete", finish_reason=state.finish_reason)
            if attempt.outcome == "cancelled" or cancel.cancelled:
                return result("cancelled", error=attempt.error)
            error = attempt.error
            if (index == remaining - 1 or error is None
                    or not self._retryable(error.kind, meter.delivered(call.call_id))):
                return result("failed", error=error)
            seconds = _retry_after(error.retry_after) if error.kind == "rate_limit" else None
            if seconds and self._wait(cancel, min(seconds, self._limits.max_retry_wait)):
                return result("cancelled", error=error)
        return result("failed")  # unreachable: the loop returns on its last attempt

    def _attempt(self, meter: TurnMeter, call: PlannedCall, number: int, label: str,
                 endpoint: ProviderEndpoint, url: str, body: bytes, key: Secret | None,
                 on_delta: Callable[[str], None] | None, cancel: CancelToken) -> _Attempt:
        """One attempt. Whatever ends it (including an exception), it is booked."""
        attempt = _Attempt()
        state = attempt.state
        clock, limits, timeouts = self._clock, self._limits, endpoint.timeouts
        start = clock()
        first_byte = None
        peer = None
        sent = False
        delivered = False
        connection = None
        try:
            try:
                connection = self._transport.connect(url, timeouts, cancel)
            except TransportError as error:
                attempt.error = ProviderError(error.kind)
                return attempt
            peer = connection.peer_loopback
            if cancel.cancelled:  # cancelled while connecting: close without sending
                return attempt
            sent = True  # everything after connect() may have reached the provider
            attempt.outcome = "failed"
            try:
                response = connection.send(body, {"Content-Type": "application/json",
                                                  "Accept": "text/event-stream"}, key)
            except TransportError as error:
                attempt.error = ProviderError(
                    self._deadline_kind(start, timeouts.total, error.kind))
                if cancel.cancelled:
                    attempt.outcome = "cancelled"
                return attempt
            first_byte = clock() - start
            if response.status != 200:
                attempt.error = self._status_error(response, key)
                return attempt
            received = 0
            while True:
                left = timeouts.total - (clock() - start)
                if finished(state):
                    attempt.outcome = "completed"
                    if state.done or left <= 0 or cancel.cancelled:
                        break
                    timeout = min(limits.usage_grace, left)
                else:
                    if cancel.cancelled:
                        attempt.outcome = "cancelled"
                        break
                    if left <= 0:
                        attempt.error = ProviderError("timeout")
                        break
                    timeout = min(timeouts.read, left)
                try:
                    line = response.read_line(limits.max_line_bytes, timeout)
                except TransportError as error:
                    if not finished(state):
                        attempt.error = ProviderError(
                    self._deadline_kind(start, timeouts.total, error.kind))
                        attempt.outcome = "cancelled" if cancel.cancelled else "failed"
                    break
                if not finished(state) and clock() - start >= timeouts.total:
                    attempt.error = ProviderError("timeout")  # the deadline cut this line short
                    break
                if not line:
                    if not finished(state):
                        attempt.outcome = "cancelled" if cancel.cancelled else "failed"
                        if attempt.outcome == "failed":
                            attempt.error = ProviderError("truncated")
                    break
                received += len(line)
                if received > limits.max_stream_bytes:
                    if not finished(state):
                        attempt.error = ProviderError("bad_response")
                    break
                try:
                    delta = feed_line(state, line)
                except ProviderError as error:
                    if not finished(state):
                        if error.error_type == "not_sse" and not state.saw_sse:
                            error = classify_non_sse(line + response.error_body(ERROR_BODY_LIMIT))
                        attempt.error = _sanitize(error, key)
                    break
                if delta is not None and on_delta is not None:
                    meter._delivered(call.call_id)
                    delivered = True
                    on_delta(delta)
                if state.done and not finished(state):
                    attempt.error = ProviderError("truncated")  # [DONE] with no finish reason
                    break
        except BaseException:
            if attempt.outcome != "completed" and not finished(state):
                attempt.outcome = "failed" if sent else "not_sent"
            elif finished(state):
                attempt.outcome = "completed"
            # A consumer callback (or a broken transport) raised; it is re-raised below.
            attempt.error = attempt.error or ProviderError("consumer_error")
            raise
        finally:
            if key is not None and key.reveal() in (state.served_model or ""):
                state.served_model = None
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            self._book(meter, call, number, label, attempt, sent, peer, delivered,
                       first_byte, clock() - start)
        return attempt

    def _deadline_kind(self, start: float, total: float, kind: str) -> str:
        """A read cut short by the attempt's hard deadline is a timeout."""
        return "timeout" if self._clock() - start >= total else kind

    def _status_error(self, response, key: Secret | None) -> ProviderError:
        kind = _status_kind(response.status)
        detail = error_from_object(_json_error(response.error_body(ERROR_BODY_LIMIT)))
        return _sanitize(ProviderError(kind, status=response.status,
                                       retry_after=response.headers.get("retry-after"),
                                       error_type=detail.error_type, error_code=detail.error_code,
                                       diagnostic=detail.diagnostic), key)

    def _book(self, meter: TurnMeter, call: PlannedCall, number: int, label: str,
              attempt: _Attempt, sent: bool, peer: bool | None, delivered: bool,
              first_byte: float | None, total: float) -> None:
        state = attempt.state
        measurement = self._measurement() if sent else Measurement.ESTIMATED
        kind = attempt.error.kind if attempt.error is not None else None

        def build() -> tuple[CallAttempt, AttemptReport]:
            if sent:
                usage, note = normalize_usage(state.usage_block)
            else:
                usage, note = TokenUsage(0, 0), None
            booked = CallAttempt(call.call_id, number, usage, measurement, attempt.outcome, label)
            report = AttemptReport(
                call.call_id, number, label, attempt.outcome, usage, measurement,
                error_kind=kind, finish_reason=state.finish_reason,
                served_model=state.served_model, delivered=delivered, peer_loopback=peer,
                first_byte_s=first_byte, total_s=total,
                latency_measurement="measured" if (
                    self._measurement() is Measurement.PROVIDER_REPORTED
                    and self._clock is time.monotonic) else "synthetic",
                usage_note=note)
            return booked, report

        attempt.booking = meter._book(call.call_id, number, label, sent=sent,
                                      measurement=measurement, error_kind=kind, build=build)
        reports = meter.reports(call.call_id)
        attempt.report = reports[-1] if reports and reports[-1].attempt == number else \
            AttemptReport(call.call_id, number, label, "unbooked", TokenUsage(None, None),
                          measurement, error_kind=kind)


def _json_error(body: bytes) -> object:
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return value.get("error") if isinstance(value, dict) else None
