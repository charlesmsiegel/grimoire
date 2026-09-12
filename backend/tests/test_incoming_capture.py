"""Capture the provider boundary, including fields no consumer recognizes."""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
import pytest

from grimoire import routes
from grimoire.llm import LLMClient
from grimoire.openai_compatible import OpenAICompatibleClient
from grimoire.openrouter import OpenRouterClient
from grimoire.store import logs, usage
from tests.test_claude_agent import install_fake_sdk

CONN = {"kind": "openai_compatible", "model": "m", "api_key": "secret-key",
        "base_url": "https://example.test/v1"}
FRAMES = [': keep-alive', '', 'event: extension',
          'data: {"choices":[{"delta":{"reasoning_content":"考える", "future":{"x":[null,false,0]}}}],"vendor":42}',
          '', 'data: {bad json', '',
          'data: {"choices":[{"delta":{"content":"Hello"}}]}', '',
          'data: {"usage":{"completion_tokens_details":{"reasoning_tokens":7}},"future_usage":true}',
          '', 'data: [DONE]']


def client_for(handler, sink, **kwargs):
    return LLMClient(openai_compatible=OpenAICompatibleClient(http=httpx.AsyncClient(
        transport=httpx.MockTransport(handler))),
        openrouter=OpenRouterClient(http=httpx.AsyncClient(
            transport=httpx.MockTransport(handler))),
        capture=lambda: sink, retries=0, **kwargs)


@pytest.mark.parametrize("kind", ["openai_compatible", "openrouter"])
async def test_all_lines_survive_before_content_and_usage_filtering(kind):
    events = []
    client = client_for(lambda r: httpx.Response(200, text="\n".join(FRAMES) + "\n"), events.append)
    try:
        assert await client.complete([], {**CONN, "kind": kind}) == "Hello"
    finally:
        await client.aclose()
    assert [e["payload"] for e in events if e["event"] == "sse_line"] == FRAMES
    assert events[0]["event"] == "start"
    assert events[-1]["payload"] == {"status": "complete"}
    assert len({e["call_id"] for e in events}) == 1
    assert [e["sequence"] for e in events] == list(range(len(events)))
    assert all(e["attempt"] == 1 and e["elapsed_ms"] >= 0 for e in events)
    assert "secret-key" not in json.dumps(events)


async def test_failed_attempt_and_fallback_keep_distinct_frames():
    events = []
    def handler(request):
        if json.loads(request.content)["model"] == "m":
            return httpx.Response(400, text='{"error":{"message":"bad","future":17}}')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n')
    client = client_for(handler, events.append, fallback={**CONN, "model": "fallback"})
    try:
        assert await client.complete([], CONN) == "ok"
    finally:
        await client.aclose()
    assert len({e["call_id"] for e in events}) == 1
    assert {e["attempt"] for e in events} == {1, 2}
    body, = [e for e in events if e["event"] == "http_error_body"]
    assert json.loads(body["payload"])["error"]["future"] == 17
    assert [e["payload"]["status"] for e in events if e["event"] == "end"] == ["error", "complete"]


async def test_capture_failure_cannot_fail_a_reply():
    def broken(event):
        raise OSError("disk full")
    client = client_for(lambda r: httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n'), broken)
    try:
        assert await client.complete([], CONN) == "ok"
    finally:
        await client.aclose()


async def test_close_records_interruption_and_keeps_partial_frames():
    events = []
    client = client_for(lambda r: httpx.Response(200, text="\n".join(FRAMES) + "\n"), events.append)
    stream = client.stream([], CONN)
    try:
        assert await anext(stream) == "Hello"
        await stream.aclose()
    finally:
        await client.aclose()
    assert any(e["event"] == "sse_line" for e in events)
    assert events[-1]["payload"] == {"status": "interrupted"}


async def test_sdk_fields_survive_before_text_block_filtering(monkeypatch):
    @dataclass
    class FutureMessage:
        thinking: str
        extra: dict
    message = FutureMessage("considering", {"future": [None, {"tokens": 3}]})
    install_fake_sdk(monkeypatch, replies=[message])
    events = []
    client = LLMClient(capture=lambda: events.append)
    try:
        assert await client.complete([], {"kind": "claude", "model": "m"}) == ""
    finally:
        await client.aclose()
    captured, = [e for e in events if e["event"] == "sdk_message"]
    assert captured["payload"] == {"type": "FutureMessage", "fields": {
        "thinking": "considering", "extra": {"future": [None, {"tokens": 3}]}}}


def test_debug_capture_chunks_large_values_without_clipping(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.forget_file_sizes()
    logs.apply_level("debug")
    try:
        payload = {"future": "考" * 9000, "unknown": [None, False, {}]}
        event = {"call_id": "call", "attempt": 1, "sequence": 2, "model": "m",
                 "provider": "openai_compatible", "elapsed_ms": 1.25,
                 "event": "sse_line", "payload": json.dumps(payload)}
        sink = logs.incoming_capture()
        sink(event)
        rows = [json.loads(line) for path in (tmp_path / "logs").glob("*.jsonl")
                for line in path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) > 1
        assert {r["parts"] for r in rows} == {len(rows)}
        assert [r["part"] for r in rows] == list(range(1, len(rows) + 1))
        assert json.loads("".join(r["payload"] for r in rows)) == event["payload"]
        assert all(r["kind"] == "llm_incoming" for r in rows)
        logs.apply_level("info")
        assert logs.incoming_capture() is None
    finally:
        logs.apply_level("info")
        logs.forget_file_sizes()


def test_application_connects_capture_to_existing_log_store():
    client = routes.common.build_llm()
    assert client._capture is logs.incoming_capture


def test_concurrent_capture_and_normal_logs_preserve_every_part(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.forget_file_sizes()
    logs.apply_level("debug")
    try:
        sink = logs.incoming_capture()
        payload = "thinking" * 900
        def capture(index):
            sink({"call_id": str(index), "attempt": 1, "sequence": 0,
                  "event": "sse_line", "payload": payload})
            logs.record("info", "worker", "ordinary row")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(capture, range(40)))
        rows = [json.loads(line) for path in (tmp_path / "logs").glob("*.jsonl")
                for line in path.read_text(encoding="utf-8").splitlines()]
        assert sum(r["message"] == "ordinary row" for r in rows) == 40
        for index in range(40):
            parts = sorted((r for r in rows if r.get("call_id") == str(index)), key=lambda r: r["part"])
            assert len(parts) == parts[0]["parts"]
            assert json.loads("".join(r["payload"] for r in parts)) == payload
    finally:
        logs.apply_level("info")
        logs.forget_file_sizes()


async def test_durable_capture_does_not_change_metered_accounting(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.apply_level("debug")
    body = ('data: {"choices":[{"delta":{"content":"Hello"}}]}\n'
            'data: {"usage":{"prompt_tokens":10,"completion_tokens":20,"cost":0.01},'
            '"new_field":{"reasoning":"private"}}\n')
    client = client_for(lambda r: httpx.Response(200, text=body), logs.incoming_capture())
    try:
        with usage.meter("chat") as meter:
            assert await client.complete([], CONN, usage=meter.usage) == "Hello"
        assert meter.row["completion_tokens"] == 20
        assert meter.row["cost_usd"] == 0.01
        rows = [json.loads(line) for path in (tmp_path / "logs").glob("*.jsonl")
                for line in path.read_text(encoding="utf-8").splitlines()]
        incoming = [json.loads(row["payload"]) for row in rows if row.get("event") == "sse_line"]
        assert any('"new_field":{"reasoning":"private"}' in line for line in incoming)
        assert "private" not in json.dumps(meter.row)
    finally:
        await client.aclose()
        logs.apply_level("info")
        logs.forget_file_sizes()
