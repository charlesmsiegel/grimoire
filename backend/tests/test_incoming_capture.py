"""Capture the provider boundary, including fields no consumer recognizes."""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

import httpx
import pytest

from grimoire import llm, llm_capture, routes, wire
from grimoire.llm import LLMClient
from grimoire.openai_compatible import OpenAICompatibleClient
from grimoire.openrouter import OpenRouterClient
from grimoire.store import logs, usage
from tests.test_claude_agent import install_fake_sdk

CONN = wire.Target(provider_id="", kind="openai_compatible", model="m", requested_model="m",
                   api_key="secret-key", base_url="https://example.test/v1")
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
        assert await client.complete([], replace(CONN, kind=kind)) == "Hello"
    finally:
        await client.aclose()
    assert [e["payload"] for e in events if e["event"] == "sse_line"] == FRAMES
    assert events[0]["event"] == "start"
    assert events[-1]["payload"] == {"status": "complete"}
    assert len({e["call_id"] for e in events}) == 1
    assert [e["sequence"] for e in events] == list(range(len(events)))
    assert all(e["attempt"] == 1 and e["elapsed_ms"] >= 0 for e in events)
    assert "secret-key" not in json.dumps(events)
    assert not any("run_id" in e for e in events), "no run, no run id"


async def test_failed_attempt_and_fallback_keep_distinct_frames():
    events = []
    def handler(request):
        if json.loads(request.content)["model"] == "m":
            return httpx.Response(400, text='{"error":{"message":"bad","future":17}}')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n')
    client = client_for(handler, events.append)
    chain = wire.Chain(CONN, replace(CONN, model="fallback", requested_model="fallback"))
    try:
        assert await client.complete([], chain) == "ok"
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
        assert await client.complete([], wire.Target(provider_id="", kind="claude", model="m",
                                                     requested_model="m")) == ""
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


# ---- run attribution (01g-S3) ----
def test_stamp_keeps_the_run_key_and_clears_the_rest():
    holder = {llm_capture.RUN_KEY: "run-1", "prompt_tokens": 99, "cost_usd": 0.5}
    llm._stamp(holder, CONN, 1)
    assert holder[llm_capture.RUN_KEY] == "run-1"
    assert "prompt_tokens" not in holder and "cost_usd" not in holder
    holder = {"prompt_tokens": 99}
    llm._stamp(holder, CONN, 1)
    assert llm_capture.RUN_KEY not in holder


async def test_the_run_id_rides_every_attempt_of_a_call(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)
    answers = iter([httpx.Response(429, text='{"error":{"message":"slow down"}}'),
                    httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n')])
    events: list[dict] = []
    client = LLMClient(openai_compatible=OpenAICompatibleClient(http=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: next(answers)))),
        capture=lambda: events.append, retries=1)
    try:
        with usage.meter("chat", run_id="run-1", loop_turn=1) as m:
            assert await client.complete([], CONN, m.usage) == "ok"
    finally:
        await client.aclose()
    assert {e["attempt"] for e in events} == {1, 2}
    assert all(e["run_id"] == "run-1" for e in events)
    assert m.row["run_id"] == "run-1" and m.row["attempts"] == 2


def _declared_holder_keys() -> dict[str, str]:
    """Every holder key the gateway declares: a module-level `KEY`, `*_KEY`
    or `ATTEMPTED` string beginning with `_`, in a top-level module."""
    import importlib
    import pkgutil

    import grimoire

    found: dict[str, str] = {}
    for info in pkgutil.iter_modules(grimoire.__path__):
        if info.ispkg:
            continue
        module = importlib.import_module(f"grimoire.{info.name}")
        for name, value in vars(module).items():
            if ((name == "KEY" or name.endswith("_KEY") or name == "ATTEMPTED")
                    and isinstance(value, str) and value.startswith("_")):
                found[f"{info.name}.{name}"] = value
    return found


def test_the_keys_stamp_keeps_are_the_pre_send_keys():
    """`llm._stamp` clears the holder per attempt and keeps a few keys a caller
    placed before the call. Those, and only those, are what `store.usage.sent`
    must not count as a request sent (`PRE_SEND_KEYS`), or a holder refused
    before sending files a model-less row. A key the gateway declares without
    a value here fails first, so a new kept key cannot skip the comparison."""
    from grimoire import llm_reasoning, llm_usage, tool_calls

    values = {
        llm_capture.RUN_KEY: "run-1",
        llm_capture.KEY: llm_capture.Capture(lambda event: None, "c", 1, "m", "p"),
        llm_reasoning.KEY: llm_reasoning.Buffer(),
        tool_calls.KEY: tool_calls.Collector(),
        llm_usage.ESTIMATE_KEY: llm_usage.Estimate([]),
        llm_usage.ENDED_KEY: True,
        llm.ATTEMPTED: CONN,
    }
    declared = _declared_holder_keys()
    missing = {name for name, key in declared.items() if key not in values}
    assert not missing, f"holder keys with no candidate value here: {missing}"
    fresh: dict = {}
    llm._stamp(fresh, CONN, 1)
    holder = dict(values)
    llm._stamp(holder, CONN, 1)
    kept = {key for key in holder if key not in fresh}
    assert kept == set(usage.PRE_SEND_KEYS)


async def test_the_run_id_rides_a_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    events: list[dict] = []

    def handler(request):
        if json.loads(request.content)["model"] == "m":
            return httpx.Response(400, text='{"error":{"message":"bad"}}')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n')
    client = client_for(handler, events.append)
    chain = wire.Chain(CONN, replace(CONN, model="fallback", requested_model="fallback"))
    try:
        with usage.meter("chat", run_id="run-1") as m:
            assert await client.complete([], chain, m.usage) == "ok"
    finally:
        await client.aclose()
    assert {e["attempt"] for e in events} == {1, 2}
    assert all(e["run_id"] == "run-1" for e in events)
    assert m.row["model"] == "fallback" and m.row["run_id"] == "run-1"
