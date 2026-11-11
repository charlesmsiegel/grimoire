"""One test call to the configured model (112), sent only when the person asks for it.

It goes the way a turn's reply goes: through the same 013 adapter (so the same endpoint, key variable,
authorization, booking and redaction), over the same routes in the same order, under the configuration's own
attempts policy. What differs is what it carries, a fixed one-line prompt with a small reply bound, and whose
call it is: it is planned on a turn of its own that belongs to no chat, so nothing in the library is read or
written. Every attempt is booked on that turn's meter and handed to 048's tracer like a turn's calls, so the
device's recent calls show it with its tokens and its cost.

The whole call (every attempt and any wait between them) is cancelled after ``seconds``, and reported as a
timeout. The reply's text is never returned or kept, and neither is the key: a provider's own error text comes
back with the key redacted by the adapter.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable

from grimoire.core.inference import TurnUsageLedger, UsageTotals, plan_turn
from grimoire.core.inference.usage import TOKEN_FIELDS
from grimoire.play.tracing import record_calls, traced
from grimoire.providers.cancel import CancelToken
from grimoire.providers.errors import CallRefused
from grimoire.providers.meter import TurnMeter
from grimoire.providers.request import ChatMessage, ModelRequest
from grimoire.tracing import PriceTable

from .config import ModelConfig, ProviderAccess, policy_for
from .pricing import cost_view

#: Seconds the whole test call may take before it is cancelled.
TEST_CALL_SECONDS = 30.0
#: The most reply tokens a test call asks for: enough for one word, small enough to cost next to nothing.
TEST_OUTPUT_TOKENS = 32
#: All a test call sends. It names nothing from the library.
TEST_PROMPT = "This is a connection test. Reply with the single word: ready"
#: The turn a test call is planned on: its own, in no chat. Each call gets a fresh turn id.
TEST_CAMPAIGN = "app-test-call"
TEST_ACTOR = "test"
#: The trace operation a test call is recorded under.
OPERATION = "test_call"


class _Ended(Exception):
    """A test call that did not end with an answer: its ``reason`` becomes the trace's status."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Probe:
    """What 048's ``traced`` needs of a runner: its tracer and its clock."""

    def __init__(self, tracer, clock) -> None:  # type: ignore[no-untyped-def]
        self.tracer = tracer
        self.clock = clock

    @traced(OPERATION)
    def run(self, step: Callable[[], str]) -> None:
        status = step()
        if status != "ok":
            raise _Ended(status)


def send_test_call(config: ModelConfig, access: ProviderAccess, *, tracer=None,  # type: ignore[no-untyped-def]
                   clock: Callable[[], float] = time.monotonic, prices: PriceTable | None = None,
                   seconds: float = TEST_CALL_SECONDS) -> dict:
    """Send one test call and say how it ended. Never raises for a provider's failure: that is the answer."""
    plan = plan_turn(TEST_CAMPAIGN, "test-" + uuid.uuid4().hex, present_actor_ids=[TEST_ACTOR],
                     speaking_npc_ids=[TEST_ACTOR], policy=policy_for(config))
    call = plan.calls[0]
    meter = TurnMeter(TurnUsageLedger(plan))
    request = ModelRequest((ChatMessage("user", TEST_PROMPT),),
                           max_output_tokens=min(config.max_output_tokens, TEST_OUTPUT_TOKENS))
    cancel = CancelToken()
    expired = threading.Event()

    def expire() -> None:
        expired.set()
        cancel.cancel()

    found: dict = {}

    def step() -> str:
        try:
            result = access.adapter.execute(meter, call, request, access.routes, access.handle, cancel=cancel)
        except CallRefused as error:  # nothing was sent
            found.update(status="refused", reason=error.reason)
            return "refused"
        finally:
            record_calls(meter)
        error = result.error
        status, reason = result.status, None
        if status == "cancelled" and expired.is_set():
            status, reason = "timeout", "timeout"
        elif status == "refused":
            reason = result.refusal_reason
        elif status == "incomplete":
            reason = result.finish_reason or "incomplete"
        elif error is not None:
            reason = error.kind
        found.update(status=status, reason=reason, finish_reason=result.finish_reason)
        if error is not None:
            found.update(http_status=error.status, code=error.error_code or error.error_type, detail=error.diagnostic)
        return "ok" if status == "accepted" else status

    timer = threading.Timer(seconds, expire)
    timer.daemon = True
    started = time.monotonic()
    timer.start()
    try:
        _Probe(tracer, clock).run(step)
    except _Ended:
        pass
    finally:
        timer.cancel()
    taken = time.monotonic() - started
    attempts = meter.ledger.attempts
    reports = meter.reports(call.call_id)
    sent = [report for report in reports if report.outcome != "not_sent"]
    totals = UsageTotals.of(attempts)
    status = found.get("status", "failed")
    return {
        # an answer cut short by this call's own small reply bound still shows the model answers
        "ok": status == "accepted" or (status == "incomplete" and found.get("finish_reason") == "length"),
        "status": status, "reason": found.get("reason"), "http_status": found.get("http_status"),
        "code": found.get("code"), "detail": found.get("detail"),
        "sent": bool(sent), "local": config.endpoint.loopback_literal,
        "model": next((report.served_model for report in reversed(reports) if report.served_model), None),
        "route": reports[-1].route if reports else None,
        "seconds": round(taken, 3), "limit_seconds": seconds,
        "attempts": [{"attempt": report.attempt, "outcome": report.outcome, "route": report.route,
                      "measurement": report.measurement.value, "reason": report.error_kind,
                      "tokens": {name: getattr(report.usage, name) for name in TOKEN_FIELDS}} for report in reports],
        "tokens": {"sums": dict(totals.sums), "unreported": dict(totals.unreported)},
        "recorded": tracer is not None,
        **cost_view(attempts, prices),
    }


__all__ = ["OPERATION", "TEST_CALL_SECONDS", "TEST_OUTPUT_TOKENS", "TEST_PROMPT", "send_test_call"]
