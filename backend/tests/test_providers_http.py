"""The adapter over real loopback HTTP: provider-reported usage and failure outcomes."""

import socket

import pytest

from provider_servers import (
    STREAM_HEAD, RawServer, base_url, raw_chunk, reset, sse_lines, sse_server, stream,
)
from grimoire.core.inference import (
    InferencePolicy, Measurement, TokenUsage, TurnUsageLedger, plan_turn,
)
from grimoire.providers import (
    ChatMessage, ModelRequest, ProviderEndpoint, ProviderRegistry, RouteChoice,
    StaticCredentials, Timeouts, open_envelope,
)
from grimoire.providers.adapter import ProviderAdapter
from grimoire.providers.meter import TurnMeter
from grimoire.providers.transport import HttpTransport
from grimoire.security import Authorizer, Grant, Permission, ResourcePath

KEY = "sk-loopback-SECRET-9"
FAST = Timeouts(connect=1.0, read=0.5, total=5.0)
USAGE = {"prompt_tokens": 50, "completion_tokens": 7,
         "prompt_tokens_details": {"cached_tokens": 30},
         "completion_tokens_details": {"reasoning_tokens": 2}}


def _run(url, *, attempts=1, timeouts=FAST, approvals=None):
    authz = Authorizer({"human:owner"})
    authz.add_grant(Grant("g", "app:runtime", frozenset({Permission.EXECUTE}),
                          ResourcePath(("providers",)), "human:owner"))
    adapter = ProviderAdapter(ProviderRegistry([ProviderEndpoint("local", url, timeouts=timeouts)]),
                              StaticCredentials({"local": KEY}), authz, HttpTransport())
    plan = plan_turn("camp", "t1", present_actor_ids=["npc"], speaking_npc_ids=["npc"],
                     policy=InferencePolicy(max_attempts_per_call=attempts))
    meter = TurnMeter(TurnUsageLedger(plan))
    result = adapter.execute(meter, plan.calls[0],
                             ModelRequest([ChatMessage("user", "hi")], max_output_tokens=16),
                             [RouteChoice("local", "synthetic-model")],
                             authz.handle_for("app:runtime", trusted=True), approvals=approvals)
    return result, meter


def test_a5_real_http_round_trip_is_provider_reported():
    authorized = []
    with sse_server(stream(sse_lines(usage=USAGE))) as server:
        result, meter = _run(base_url(server),
                             approvals=lambda label, n, env: authorized.append(env))
        path, headers, body = server.requests[0]
    assert (result.status, result.reply) == ("accepted", "Hello!")
    (attempt,) = result.attempts
    assert attempt.measurement == Measurement.PROVIDER_REPORTED
    assert attempt.usage == TokenUsage(50, 7, cache_read_tokens=30, reasoning_tokens=2)
    assert attempt.latency_measurement == "measured" and attempt.peer_loopback is True
    assert attempt.route.startswith("local@127.0.0.1:") and attempt.served_model == "synthetic-model"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert body == open_envelope(authorized[0])[1] and path == "/v1/chat/completions"
    assert meter.summary().provider_reported_only and meter.all_accepted


def test_a6_refused_port_is_not_sent():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    result, meter = _run(f"http://127.0.0.1:{port}/v1")
    assert result.status == "failed"
    assert [a.outcome for a in meter.ledger.attempts] == ["not_sent"]


def test_a6_silent_server_times_out_after_send():
    def never_answer(conn, server):
        server.read_request(conn)
        server.release.wait(10)
    with RawServer(never_answer) as server:
        result, meter = _run(server.url())
    assert (result.status, result.error.kind) == ("failed", "timeout")
    assert [a.outcome for a in meter.ledger.attempts] == ["failed"]
    assert meter.ledger.attempts[0].usage == TokenUsage(None, None)


def test_a6_close_after_request_is_failed():
    with RawServer(lambda conn, server: server.read_request(conn)) as server:
        result, meter = _run(server.url())
    assert result.status == "failed" and result.error.kind == "network"
    assert [a.outcome for a in meter.ledger.attempts] == ["failed"]


def test_a6_reset_before_finish_is_failed():
    def reset_early(conn, server):
        server.read_request(conn)
        conn.sendall(STREAM_HEAD + raw_chunk(sse_lines()[0]))
        reset(conn)
    with RawServer(reset_early) as server:
        result, meter = _run(server.url())
    assert result.status == "failed"
    assert [a.outcome for a in meter.ledger.attempts] == ["failed"]


def test_a6_close_after_finish_without_final_chunk_is_completed():
    lines = sse_lines(usage=None, done=False)

    def close_after_finish(conn, server):
        server.read_request(conn)
        conn.sendall(STREAM_HEAD + b"".join(raw_chunk(line) for line in lines))
        conn.shutdown(socket.SHUT_WR)
        server.release.wait(5)
    with RawServer(close_after_finish) as server:
        result, meter = _run(server.url())
    assert (result.status, result.reply) == ("accepted", "Hello!")
    assert meter.ledger.attempts[0].usage == TokenUsage(None, None)


def test_retry_over_http_after_a_server_error():
    attempts = []

    def flaky(handler):
        attempts.append(1)
        if len(attempts) == 1:
            handler.respond(503, b'{"error": {"message": "overloaded"}}')
        else:
            stream(sse_lines(usage=USAGE))(handler)
    with sse_server(flaky) as server:
        result, meter = _run(base_url(server), attempts=2)
    assert result.status == "accepted"
    assert [a.outcome for a in meter.ledger.attempts] == ["failed", "completed"]
