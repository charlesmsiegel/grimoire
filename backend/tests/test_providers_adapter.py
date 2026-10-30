"""ProviderAdapter through the scripted transport: planned, authorized, fully booked."""

import json
import traceback

import pytest

from grimoire.core.inference import (
    InferencePolicy, Measurement, PlannedCall, TokenUsage, TurnUsageLedger, UnplannedCallError,
    plan_turn,
)
from grimoire.providers import (
    CallRefused, ChatMessage, ModelRequest, ProviderEndpoint, ProviderError, ProviderRegistry,
    RouteChoice, StaticCredentials, Timeouts, TransportError, open_envelope,
)
from grimoire.providers.adapter import CallResult, ProviderAdapter
from grimoire.providers.meter import TurnMeter
from grimoire.providers.transport import HttpTransport
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.testing.providers import (
    CancelAt, ConnectFail, Garbage, Interrupt, ReadFail, Reply, ScriptedClock, ScriptedTransport,
    Status, Truncate, WriteFail,
)

KEY = "sk-primary-SECRET-111"
KEY2 = "sk-backup-SECRET-222"
PROMPT = "PROMPT-SENTINEL-zq9"
REPLY_TEXT = ("REPLY-", "SENTINEL-", "kx7")
PRIMARY = RouteChoice("primary", "model-a")
BACKUP = RouteChoice("backup", "model-b")


def registry():
    return ProviderRegistry([
        ProviderEndpoint("primary", "https://primary.example/v1"),
        ProviderEndpoint("backup", "https://backup.example/v1"),
        ProviderEndpoint("local", "http://127.0.0.1:11434/v1", needs_key=False),
        ProviderEndpoint("nokey", "https://nokey.example/v1"),
    ])


def authority(principal="app:runtime", grant=True):
    authz = Authorizer({"human:owner"})
    if grant:
        authz.add_grant(Grant("g1", principal, frozenset({Permission.EXECUTE}),
                              ResourcePath(("providers",)), "human:owner"))
    return authz


class World:
    def __init__(self, steps, *, attempts=1, speakers=("npc-a",), grant=True, trusted=True,
                 credentials=None, clock=None):
        self.transport = ScriptedTransport(steps)
        self.authz = authority(grant=grant)
        self.handle = self.authz.handle_for("app:runtime", trusted=trusted)
        self.clock = clock or ScriptedClock()
        self.adapter = ProviderAdapter(
            registry(), credentials or StaticCredentials({"primary": KEY, "backup": KEY2}),
            self.authz, self.transport, clock=self.clock, wait=self.clock.wait)
        self.plan = plan_turn("camp", "t1", present_actor_ids=["npc-a", "npc-b", "npc-c", "npc-d"],
                              speaking_npc_ids=speakers,
                              policy=InferencePolicy(max_attempts_per_call=attempts))
        self.meter = TurnMeter(TurnUsageLedger(self.plan))
        self.call = self.plan.calls[0]

    def run(self, routes=(PRIMARY,), call=None, **options):
        options.setdefault("handle", self.handle)
        return self.adapter.execute(self.meter, call or self.call, request(), list(routes),
                                    **options)


def request(text=PROMPT):
    return ModelRequest([ChatMessage("user", text)], max_output_tokens=32)


def reply(**kw):
    kw.setdefault("deltas", REPLY_TEXT)
    return Reply(**kw)


# -- acceptance -------------------------------------------------------------


def test_a1_two_speakers_two_calls():
    world = World([reply(), reply()], speakers=("npc-a", "npc-b"))
    results = [world.run(call=call) for call in world.plan.calls]
    assert [r.status for r in results] == ["accepted", "accepted"]
    assert [r.reply for r in results] == ["".join(REPLY_TEXT)] * 2
    assert len(world.transport.sends) == 2
    summary = world.meter.summary()
    assert summary.baseline.attempts == 2 and summary.optional.attempts == 0
    assert summary.complete and world.meter.all_accepted
    assert summary.provider_reported_only is False
    assert summary.measurements == {Measurement.SYNTHETIC}
    assert summary.total.sums["input_tokens"] == 40


def test_a2_outage_falls_back_visibly():
    world = World([Status(503), reply()], attempts=2)
    result = world.run(routes=(PRIMARY, BACKUP))
    assert result.status == "accepted"
    first, second = result.attempts
    assert (first.outcome, first.error_kind, first.route) == (
        "failed", "server_error", "primary@primary.example/model-a")
    assert (second.outcome, second.route) == ("completed", "backup@backup.example/model-b")
    assert first.usage == TokenUsage(None, None)
    summary = world.meter.summary()
    assert summary.total.attempts == 2 and summary.total.unreported["input_tokens"] == 1
    assert [s.url for s in world.transport.sends] == [
        "https://primary.example/v1/chat/completions", "https://backup.example/v1/chat/completions"]


def test_a3_outage_without_fallback_fails_visibly():
    world = World([Status(503)])
    result = world.run()
    assert (result.status, result.reply, result.error.kind) == ("failed", None, "server_error")
    assert result.error.status == 503
    assert len(world.meter.ledger.attempts) == 1
    assert world.meter.status(world.call.call_id) == "failed"


def test_a4_completed_call_is_never_redone():
    world = World([Status(503), reply()], attempts=2)
    world.run(routes=(PRIMARY, BACKUP))
    with pytest.raises(CallRefused, match="completed"):
        world.run(routes=(PRIMARY,))
    assert len(world.transport.connects) == 2


# -- authorization ----------------------------------------------------------


def test_f1_denials_send_nothing_and_book_nothing():
    for world, expected in (
            (World([reply()], grant=False), "not_granted"),
            (World([reply()], trusted=False), "approval_required")):
        result = world.run()
        assert (result.status, result.refusal_reason) == ("refused", expected)
        assert world.transport.connects == [] and world.meter.ledger.attempts == ()
        assert world.meter.status(world.call.call_id) == "refused"


@pytest.mark.parametrize("kind", ["foreign", "hand_built", "duck"])
def test_f1_unissued_handles_are_refused(kind):
    from grimoire.security import Decision, Reason, WorkerHandle
    world = World([reply()])

    class Duck:
        def authorize(self, *args, **kwargs):
            return Decision(True, Reason.GRANTED, ResourcePath(("providers", "primary")), "g1")
    handle = {"foreign": authority().handle_for("app:runtime", trusted=True),
              "hand_built": WorkerHandle(world.authz, "app:runtime", True),
              "duck": Duck()}[kind]
    with pytest.raises(CallRefused, match="handle"):
        world.run(handle=handle)
    assert world.transport.connects == []


def _approve(world, envelope_bytes, approval_id="a1", principal="app:untrusted"):
    world.authz.register_approval(Approval(
        approval_id, principal, Permission.EXECUTE, ResourcePath(("providers", "primary")),
        payload_digest(envelope_bytes), "human:owner"))
    return approval_id


def _untrusted(world):
    world.authz.add_grant(Grant("g2", "app:untrusted", frozenset({Permission.EXECUTE}),
                                ResourcePath(("providers",)), "human:owner"))
    return world.authz.handle_for("app:untrusted")


def test_f1_approval_for_another_url_or_body_is_refused():
    for change in ("url", "body"):
        world = World([reply()])
        handle = _untrusted(world)
        seen = []

        def approvals(label, attempt, envelope_bytes):
            url, body = open_envelope(envelope_bytes)
            other = json.dumps({"body": (body + b" ").decode() if change == "body" else body.decode(),
                                "url": "https://evil.example/v1/chat/completions" if change == "url"
                                else url}, separators=(",", ":"), sort_keys=True).encode()
            seen.append(label)
            return _approve(world, other, f"a-{change}")
        result = world.run(handle=handle, approvals=approvals)
        assert (result.status, result.refusal_reason) == ("refused", "approval_required")
        assert world.transport.connects == [] and seen == ["primary@primary.example/model-a"]


def test_f1_later_approval_allows_a_fresh_execute():
    world = World([reply()])
    handle = _untrusted(world)
    assert world.run(handle=handle).status == "refused"
    result = world.run(handle=handle,
                       approvals=lambda label, attempt, env: _approve(world, env, "a-ok"))
    assert result.status == "accepted"
    assert world.authz.pending_approvals() == ()  # consumed by exactly one send


def test_approvals_callback_receives_envelope_bytes():
    world = World([reply()])
    handle = _untrusted(world)
    got = []

    def approvals(label, attempt, envelope_bytes):
        got.append((label, attempt, open_envelope(envelope_bytes)))
        return _approve(world, envelope_bytes)
    world.run(handle=handle, approvals=approvals)
    ((label, attempt, (url, body)),) = got
    assert (label, attempt, url) == ("primary@primary.example/model-a", 1,
                                     "https://primary.example/v1/chat/completions")
    assert world.transport.sends[0].body == body


def test_sent_bytes_come_from_the_authorized_envelope(monkeypatch):
    import grimoire.providers.adapter as adapter_module
    world = World([reply()])
    handle = _untrusted(world)
    authorized = []

    def approvals(label, attempt, envelope_bytes):
        authorized.append(envelope_bytes)
        monkeypatch.setattr(adapter_module, "request_body", lambda *a, **k: b'{"evil":true}')
        return _approve(world, envelope_bytes)
    assert world.run(handle=handle, approvals=approvals).status == "accepted"
    assert world.transport.sends[0].body == open_envelope(authorized[0])[1]


def test_approvals_callback_that_raises_sends_and_books_nothing():
    world = World([reply()])

    def boom(*args):
        raise RuntimeError("approval UI crashed")
    with pytest.raises(RuntimeError):
        world.run(handle=_untrusted(world), approvals=boom)
    assert world.transport.connects == [] and world.meter.ledger.attempts == ()
    assert world.meter.status(world.call.call_id) is None
    assert world.run().status == "accepted"  # the meter's lock was released


def test_f2_unplanned_call_sends_nothing():
    world = World([reply()])
    forged = PlannedCall(world.call.call_id, "a different purpose")
    with pytest.raises(UnplannedCallError):
        world.run(call=forged)
    other = plan_turn("camp", "t9", present_actor_ids=["x"], speaking_npc_ids=["x"]).calls[0]
    with pytest.raises(UnplannedCallError):
        world.run(call=other)
    assert world.transport.connects == []


def test_static_route_errors_are_valueerrors():
    world = World([reply()], attempts=1)
    with pytest.raises(ValueError):
        world.run(routes=())
    with pytest.raises(ValueError):
        world.run(routes=(RouteChoice("unknown", "m"),))
    with pytest.raises(ValueError):
        world.run(routes=(PRIMARY, BACKUP))  # two routes, one attempt
    assert world.transport.connects == []


# -- failures and retries ---------------------------------------------------


def test_f4_timeouts_before_and_after_delivery():
    world = World([ConnectFail("timeout"), reply()], attempts=2)
    result = world.run()
    assert result.status == "accepted"
    assert [a.outcome for a in result.attempts] == ["not_sent", "completed"]
    assert result.attempts[0].usage == TokenUsage(0, 0)
    assert result.attempts[0].measurement == Measurement.ESTIMATED

    world = World([ReadFail(0, "timeout"), reply()], attempts=2)
    assert world.run().status == "accepted"

    world = World([ReadFail(2, "timeout"), reply()], attempts=2)
    seen = []
    result = world.run(on_delta=seen.append)
    assert (result.status, result.reply, result.error.kind) == ("failed", None, "timeout")
    assert seen == ["Hel", "lo"] and world.transport.remaining == 1
    assert result.attempts[0].delivered is True


def test_f5_truncated_stream_never_accepted():
    world = World([Truncate(2), reply()], attempts=2)
    result = world.run()
    assert result.status == "accepted"
    assert [a.error_kind for a in result.attempts] == ["truncated", None]
    world = World([Truncate(2)], attempts=1)
    result = world.run()
    assert (result.status, result.reply) == ("failed", None)
    world = World([reply(done=False, finish_reason=None, usage=None)])
    assert world.run().status == "failed"


def test_finish_then_drop_is_completed():
    for fault in (ReadFail(4, "network"), Truncate(4), ReadFail(4, "timeout")):
        world = World([fault], attempts=2)
        result = world.run()
        assert result.status == "accepted" and result.attempts[0].outcome == "completed"
        assert result.attempts[0].usage == TokenUsage(None, None)  # usage chunk never arrived
        assert world.transport.remaining == 0


def test_f6_cancel_before_send_mid_stream_and_during_wait():
    from grimoire.providers import CancelToken
    token = CancelToken()
    token.cancel()
    world = World([reply()])
    assert world.run(cancel=token).status == "cancelled"
    assert world.transport.connects == [] and world.meter.ledger.attempts == ()

    world = World([CancelAt(2), reply()], attempts=2)
    result = world.run()
    assert result.status == "cancelled"
    assert [a.outcome for a in result.attempts] == ["cancelled"]
    assert world.transport.remaining == 1 and world.transport.closed == 1

    token = CancelToken()
    clock = ScriptedClock()
    world = World([Status(429, retry_after="5"), reply()], attempts=2, clock=clock)
    world.clock.wait = lambda tok, seconds: (token.cancel(), True)[1]
    world.adapter._wait = world.clock.wait
    assert world.run(cancel=token).status == "cancelled"
    assert world.transport.remaining == 1


def test_cancel_after_stop_is_accepted():
    world = World([CancelAt(4)])
    assert world.run().status == "accepted"


def test_f7_length_finish_is_incomplete():
    world = World([reply(finish_reason="length"), reply()], attempts=2)
    result = world.run()
    assert (result.status, result.reply, result.finish_reason) == ("incomplete", None, "length")
    assert result.attempts[0].outcome == "completed" and world.transport.remaining == 1
    assert world.meter.status(world.call.call_id) == "incomplete"


@pytest.mark.parametrize("code,kind", [
    (401, "auth"), (403, "auth"), (402, "account_limit"), (408, "timeout"), (504, "timeout"),
    (400, "bad_request"), (422, "bad_request"), (429, "rate_limit"), (500, "server_error"),
    (503, "server_error"), (301, "bad_response"), (101, "bad_response"), (204, "bad_response"),
])
def test_status_mapping(code, kind):
    world = World([Status(code)])
    result = world.run()
    assert (result.status, result.error.kind, result.error.status) == ("failed", kind, code)


def test_f8_auth_is_terminal_even_with_fallbacks():
    world = World([Status(401), reply()], attempts=2)
    assert world.run(routes=(PRIMARY, BACKUP)).error.kind == "auth"
    assert world.transport.remaining == 1


@pytest.mark.parametrize("header,waits", [("2", [2.0]), ("9999", [30.0]), ("-1", []),
                                          ("nan", []), ("Wed, 21 Oct 2015 07:28:00 GMT", []),
                                          ("1" * 5000, [])])
def test_f8_retry_after_parsing(header, waits):
    world = World([Status(429, retry_after=header), reply()], attempts=2)
    assert world.run().status == "accepted"
    assert world.clock.waits == waits


def test_f9_usage_discrepancy_noted_and_missing_usage_incomplete():
    world = World([reply(usage={"prompt_tokens": 100, "completion_tokens": 3,
                                "prompt_tokens_details": {"cached_tokens": 160}})])
    (attempt,) = world.run().attempts
    assert attempt.usage == TokenUsage(100, 3) and "cache" in attempt.usage_note
    world = World([reply(usage={"prompt_tokens": 100, "completion_tokens": 3,
                                "prompt_tokens_details": {"cached_tokens": 60}})])
    assert world.run().attempts[0].usage.cache_read_tokens == 60
    world = World([reply(usage=None)])
    assert world.run().status == "accepted"
    assert world.meter.summary().complete is False


def test_f10_missing_key_refused_unbooked():
    world = World([reply()])
    result = world.run(routes=(RouteChoice("nokey", "m"),))
    assert (result.status, result.refusal_reason) == ("refused", "missing_key")
    assert world.transport.connects == [] and world.meter.ledger.attempts == ()
    world = World([reply()])
    assert world.run(routes=(RouteChoice("local", "llama3"),)).status == "accepted"
    assert world.transport.sends[0].keyed is False


def test_credentials_that_raise_are_refused_unbooked():
    class Broken:
        def api_key(self, provider_id):
            raise ValueError("bad env value")
    world = World([reply()], credentials=Broken())
    result = world.run()
    assert (result.status, result.refusal_reason) == ("refused", "bad_key")
    assert world.transport.connects == []


def test_f11_consumer_exception_and_keyboardinterrupt_are_booked():
    world = World([reply(), reply()], attempts=2)

    def explode(delta):
        raise RuntimeError("UI crashed")
    with pytest.raises(RuntimeError):
        world.run(on_delta=explode)
    (attempt,) = world.meter.ledger.attempts
    assert attempt.outcome == "failed"
    assert world.meter.status(world.call.call_id) == "failed"
    assert world.meter.delivered(world.call.call_id)

    world = World([Interrupt(2), reply()], attempts=2)
    with pytest.raises(KeyboardInterrupt):
        world.run()
    assert [a.outcome for a in world.meter.ledger.attempts] == ["failed"]
    assert world.transport.closed == 1


def test_delta_counts_as_delivered_even_if_on_delta_raised():
    world = World([reply(), reply()], attempts=2)
    with pytest.raises(RuntimeError):
        world.run(on_delta=lambda d: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(CallRefused, match="delivered"):
        world.run()
    assert world.run(redo_after_delivery=True).status == "accepted"


def test_consumer_exception_after_finish_books_completed_and_status_failed():
    world = World([reply(), reply()], attempts=2)
    calls = []

    def late(delta):
        calls.append(delta)

    class Boom(Exception):
        pass
    original = world.adapter._accept

    def accept_then_fail(state):
        raise Boom()
    world.adapter._accept = accept_then_fail
    with pytest.raises(Boom):
        world.run(on_delta=late)
    world.adapter._accept = original
    (attempt,) = world.meter.ledger.attempts
    assert attempt.outcome == "completed"
    assert world.meter.status(world.call.call_id) == "failed"
    with pytest.raises(CallRefused, match="completed"):
        world.run(redo_after_delivery=True)


def test_f12_oversized_non_sse_and_in_stream_errors():
    world = World([Garbage(1), reply()], attempts=2)
    result = world.run()
    assert result.attempts[0].error_kind == "bad_response" and result.status == "accepted"

    world = World([Status(200, b'{\n "error": {"code": 400, "message": "bad model"}\n}\n'),
                   reply()], attempts=2)
    result = world.run()
    assert (result.status, result.error.kind) == ("failed", "provider_error")
    assert world.transport.remaining == 1

    world = World([Status(200, b"<html>proxy page</html>\n"), reply()], attempts=2)
    assert world.run().attempts[0].error_kind == "bad_response"

    error_chunk = b'data: {"error": {"code": 502, "message": "upstream"}}\n'
    world = World([Status(200, error_chunk), reply()], attempts=2)
    assert world.run().status == "accepted"  # 5xx in-stream errors are retryable

    huge = Reply(deltas=("x" * 3000,))
    world = World([huge])
    world.adapter._limits = type(world.adapter._limits)(max_line_bytes=1000)
    assert world.run().error.kind == "bad_response"


def test_total_deadline_stops_a_trickling_stream():
    clock = ScriptedClock(step=40.0)  # every clock read is 40 s later
    world = World([reply(deltas=("a",) * 50)], clock=clock)
    result = world.run(routes=(PRIMARY,))
    assert (result.status, result.error.kind) == ("failed", "timeout")


def test_last_route_is_reused():
    world = World([Status(503), Status(503), reply()], attempts=3)
    result = world.run(routes=(PRIMARY, BACKUP))
    assert [a.route.split("@")[0] for a in result.attempts] == ["primary", "backup", "backup"]


def test_empty_stop_reply_is_accepted():
    world = World([Reply(deltas=())])
    result = world.run()
    assert (result.status, result.reply) == ("accepted", "")


def test_reentrant_execute_from_on_delta_is_busy():
    world = World([reply(), reply()], speakers=("npc-a", "npc-b"))
    errors = []

    def reenter(delta):
        try:
            world.run(call=world.plan.calls[1])
        except CallRefused as error:
            errors.append(error.reason)
    assert world.run(on_delta=reenter).status == "accepted"
    assert set(errors) == {"busy"} and len(world.transport.connects) == 1


def test_redo_after_delivery_allows_retry_until_delivery():
    world = World([ReadFail(1), ConnectFail("network"), reply()], attempts=3)
    first = world.run(on_delta=lambda d: None)
    assert first.status == "failed"
    second = world.run(on_delta=lambda d: None, redo_after_delivery=True)
    assert second.status == "accepted"
    assert [a.outcome for a in second.attempts] == ["not_sent", "completed"]


def test_connections_are_always_closed():
    steps = [Status(503), ReadFail(1), Truncate(0), Garbage(0), reply(), CancelAt(1), WriteFail()]
    world = World(steps, speakers=("npc-a", "npc-b", "npc-c"), attempts=3)
    for call in world.plan.calls:
        try:
            world.run(call=call)
        except CallRefused:
            pass
    assert world.transport.closed == len(world.transport.connects)


def test_scripted_measurement_is_synthetic():
    world = World([reply()])
    assert world.run().attempts[0].measurement == Measurement.SYNTHETIC
    assert world.meter.summary().provider_reported_only is False


def test_httptransport_subclass_is_synthetic():
    class Sneaky(HttpTransport):
        pass
    adapter = ProviderAdapter(registry(), StaticCredentials({}), authority(), Sneaky())
    assert adapter._measurement() == Measurement.SYNTHETIC
    real = ProviderAdapter(registry(), StaticCredentials({}), authority(), HttpTransport())
    assert real._measurement() == Measurement.PROVIDER_REPORTED


def test_new_errors_are_not_os_errors():
    for error in (CallRefused("x"), ProviderError("network"), TransportError("network", "read")):
        assert not isinstance(error, OSError)


# -- privacy ----------------------------------------------------------------


def test_prompt_and_reply_text_absent_from_reprs():
    echo = json.dumps({"error": {"code": 400, "message": f"bad input {PROMPT} with {KEY} " * 20}})
    world = World([reply(), Status(400, echo.encode())], speakers=("npc-a", "npc-b"))
    ok = world.run(call=world.plan.calls[0])
    bad = world.run(call=world.plan.calls[1])
    texts = [repr(ok), str(ok), repr(bad), str(bad), repr(bad.error), str(bad.error),
             *map(repr, ok.attempts + bad.attempts), repr(world.meter), repr(world.meter.summary()),
             repr(world.meter.ledger), repr(world.authz.audit()), repr(world.adapter)]
    for text in texts:
        for sentinel in (PROMPT, "SENTINEL-kx7", KEY, KEY2):
            assert sentinel not in text
    assert ok.reply == "".join(REPLY_TEXT)
    assert len(bad.error.diagnostic) <= 300 and KEY not in bad.error.diagnostic
    assert "<redacted>" in bad.error.diagnostic


def test_secret_absent_from_results_reprs_audit_and_tracebacks():
    world = World([reply()])

    def explode(delta):
        raise RuntimeError("consumer failed")
    with pytest.raises(RuntimeError) as caught:
        world.run(on_delta=explode)
    formatted = "".join(traceback.TracebackException.from_exception(
        caught.value, capture_locals=True).format())
    assert KEY not in formatted
    assert all(KEY not in repr(record) for record in world.authz.audit())
