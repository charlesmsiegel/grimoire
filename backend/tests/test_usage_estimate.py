"""A count the provider did not report is counted locally, and the row says so
(slice E, Task 3; spec 9.1 step 3).

The facade counts, never the meter: `llm._resilient` is the one place that
knows an attempt's stream ended on its own, and it is async, so it hands the
count to a worker thread under `COUNT_TIMEOUT_S`. The counter arrives as
`LLMClient(count_tokens=)` because the gateway imports no store. `Meter.done`
only reads what the facade wrote -- the counts, `tokens_estimated` and the
`ENDED_KEY` mark -- and never counts or loads an encoder.

A call that did not end on its own (an early break, a close, a failure) gets
no estimate: nobody knows what such a call billed.

Invented names, fake keys and `vendor/model-*` models only.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from grimoire import content_parts, llm, llm_reasoning, llm_usage
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.openai_compatible import OpenAICompatibleClient
from grimoire.store import tokens, usage
from tests.llm_fakes import ScriptedProvider

MODEL_A = "vendor/model-a"
CONN = {"kind": "openai_compatible", "model": MODEL_A,
        "base_url": "https://saltmarch.test/v1", "api_key": "sk-fake"}
PROMPT = [{"role": "user", "content": "Mara asks the ferryman about the tide."}]
PROMPT_TEXT = "Mara asks the ferryman about the tide."
CHUNKS = ["The ferryman ", "points at the ", "drowned stair."]


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)
    monkeypatch.setattr(llm, "_backoff_delay", lambda attempt: 0)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def _client(provider, counter=tokens.count_tokens, **kwargs) -> LLMClient:
    return LLMClient(openai_compatible=provider, count_tokens=counter, **kwargs)


def _rows() -> list[dict]:
    return list(usage.calls(days=1))


def _one_row() -> dict:
    rows = _rows()
    assert len(rows) == 1, rows
    return rows[0]


def _filed(client: LLMClient, messages=PROMPT, conn=CONN) -> tuple[str, dict, usage.Meter]:
    """One `complete` through the real facade under a meter: the text, the row
    and the meter."""
    async def go():
        with usage.meter("chat") as m:
            text = await client.complete(messages, conn, usage=m.usage)
        return text, m
    text, m = asyncio.run(go())
    return text, _one_row(), m


def _no_estimate(row: dict) -> None:
    assert "prompt_tokens" not in row
    assert "completion_tokens" not in row
    assert "tokens_estimated" not in row


# ---- the estimate ----
def test_a_provider_that_reports_no_counts_gets_local_counts_and_the_flag(home):
    text, row, _m = _filed(_client(ScriptedProvider(CHUNKS)))
    assert text == "".join(CHUNKS)
    assert row["prompt_tokens"] == tokens.count_tokens(PROMPT_TEXT)
    assert row["completion_tokens"] == tokens.count_tokens("".join(CHUNKS))
    assert row["tokens_estimated"] is True


def test_reported_counts_are_never_replaced(home):
    provider = ScriptedProvider(CHUNKS, usage={"prompt_tokens": 7, "completion_tokens": 3})
    _text, row, _m = _filed(_client(provider))
    assert row["prompt_tokens"] == 7
    assert row["completion_tokens"] == 3
    assert "tokens_estimated" not in row


def test_only_the_missing_count_is_estimated(home):
    counted: list[str] = []

    def counter(text: str) -> int:
        counted.append(text)
        return tokens.count_tokens(text)
    provider = ScriptedProvider(CHUNKS, usage={"prompt_tokens": 7})
    _text, row, _m = _filed(_client(provider, counter))
    assert row["prompt_tokens"] == 7
    assert row["completion_tokens"] == tokens.count_tokens("".join(CHUNKS))
    assert row["tokens_estimated"] is True
    # The reported prompt is not encoded again: only the missing half is counted.
    assert counted == ["".join(CHUNKS)]


# ---- only a call that ended on its own ----
def _broken_early(provider) -> tuple[dict, usage.Meter]:
    """A consumer that takes one chunk, breaks out (stop-after-fence), and
    files the call `ok`."""
    client = _client(provider)

    async def go():
        m = usage.meter("chat")
        async for _chunk in client.stream(PROMPT, CONN, m.usage):
            break
        m.done()
        return m
    m = asyncio.run(go())
    assert m.row is not None
    return m.row, m


@pytest.mark.parametrize("provider", [
    ScriptedProvider(CHUNKS),
    ScriptedProvider(CHUNKS, usage={"prompt_tokens": 7, "completion_tokens": 3}),
], ids=["no-usage", "trailing-usage-skipped"])
def test_a_consumer_that_breaks_early_gets_no_estimate(home, provider):
    row, m = _broken_early(provider)
    assert row["status"] == "ok"
    _no_estimate(row)
    assert llm_usage.ESTIMATE_KEY not in m.usage
    assert llm_usage.ENDED_KEY not in m.usage


def test_an_aborted_or_failed_call_is_not_estimated(home):
    async def closed():
        m = usage.meter("chat")
        agen = _client(ScriptedProvider(CHUNKS)).stream(PROMPT, CONN, m.usage)
        assert await agen.__anext__() == CHUNKS[0]
        await agen.aclose()
        m.done("aborted")

    async def failed():
        provider = ScriptedProvider(CHUNKS, error=LLMError("bad_response", "the stair flooded"))
        with pytest.raises(LLMError), usage.meter("chat") as m:
            await _client(provider).complete(PROMPT, CONN, usage=m.usage)

    asyncio.run(closed())
    asyncio.run(failed())
    _broken_early(ScriptedProvider(CHUNKS))
    rows = _rows()
    assert [r["status"] for r in rows] == ["aborted", "error", "ok"]
    for row in rows:
        _no_estimate(row)


# ---- never on the loop, never fatal ----
def test_counting_runs_off_the_event_loop(home):
    seen: list[int] = []

    def counter(text: str) -> int:
        seen.append(threading.get_ident())
        return len(text)

    async def go():
        loop_thread = threading.get_ident()
        with usage.meter("chat") as m:
            await _client(ScriptedProvider(CHUNKS), counter).complete(PROMPT, CONN, usage=m.usage)
        return loop_thread
    loop_thread = asyncio.run(go())
    assert seen, "the counter never ran"
    assert loop_thread not in seen
    assert _one_row()["tokens_estimated"] is True


def test_meter_done_never_loads_the_encoder(home, monkeypatch):
    loads: list[int] = []

    def cold_load():
        loads.append(threading.get_ident())
        raise RuntimeError("offline: the encoder cannot be fetched")
    monkeypatch.setattr(tokens._loader, "get", cold_load)

    async def go():
        loop_thread = threading.get_ident()
        with usage.meter("chat") as m:
            await _client(ScriptedProvider(CHUNKS)).complete(PROMPT, CONN, usage=m.usage)
        return loop_thread
    loop_thread = asyncio.run(go())
    row = _one_row()
    # The cold load fell back to the heuristic, on a worker thread.
    assert row["prompt_tokens"] == -(-len(PROMPT_TEXT) // 4)
    assert row["completion_tokens"] == -(-len("".join(CHUNKS)) // 4)
    assert row["tokens_estimated"] is True
    assert loads and loop_thread not in loads

    # And the meter itself never counts, whatever the holder carries.
    fired: list[str] = []

    def never(text: str) -> int:
        fired.append(text)
        raise AssertionError("Meter.done counted")
    monkeypatch.setattr(tokens, "count_tokens", never)
    m = usage.Meter("chat")
    m.usage.update({"model": MODEL_A, "attempts": 1,
                    llm_usage.ENDED_KEY: True,
                    llm_usage.ESTIMATE_KEY: llm_usage.Estimate(list(PROMPT))})
    filed = m.done()
    assert filed is not None and filed["model"] == MODEL_A
    _no_estimate(filed)
    assert fired == []
    assert len(_rows()) == 2


def test_a_counter_that_raises_costs_the_estimate_not_the_call(home):
    def broken(text: str) -> int:
        raise RuntimeError("the tokenizer is broken")
    text, row, _m = _filed(_client(ScriptedProvider(CHUNKS), broken))
    assert text == "".join(CHUNKS)
    assert row["status"] == "ok"
    _no_estimate(row)


def test_a_slow_count_costs_the_estimate_not_the_reply(home, monkeypatch):
    """The reply returns at `COUNT_TIMEOUT_S`, not when the count would have
    finished: timed inside the coroutine, against a counter that would block
    for seconds (released once measured, so the counting thread is free for
    the next test)."""
    monkeypatch.setattr(llm, "COUNT_TIMEOUT_S", 0.05)
    release = threading.Event()
    blocked_for = 5.0

    def slow(text: str) -> int:
        release.wait(blocked_for)
        return 1
    client = _client(ScriptedProvider(CHUNKS), slow)

    async def go():
        started = time.monotonic()
        with usage.meter("chat") as m:
            text = await client.complete(PROMPT, CONN, usage=m.usage)
        return text, time.monotonic() - started
    try:
        text, elapsed = asyncio.run(go())
    finally:
        release.set()
    assert text == "".join(CHUNKS)
    assert elapsed < 1.0 < blocked_for
    row = _one_row()
    assert row["status"] == "ok"
    _no_estimate(row)


def test_the_counting_thread_never_blocks_process_exit(home):
    """A count parked behind an encoder download with no timeout of its own
    must not hold the interpreter open at exit: the counting thread is a
    daemon, which a `ThreadPoolExecutor` worker never is (its workers are
    joined at exit)."""
    names: list[str] = []

    def counter(text: str) -> int:
        names.append(threading.current_thread().name)
        return len(text)

    async def go():
        with usage.meter("chat") as m:
            await _client(ScriptedProvider(CHUNKS), counter).complete(PROMPT, CONN, usage=m.usage)
    asyncio.run(go())
    assert names
    workers = [t for t in threading.enumerate() if t.name in set(names)]
    assert workers and all(t.daemon for t in workers)


def test_a_cancelled_count_never_runs(home):
    """A count still queued when its `COUNT_TIMEOUT_S` runs out is cancelled
    unstarted, so a hung count leaves no backlog behind it."""
    executor = llm._count_executor()
    release = threading.Event()
    ran: list[str] = []
    first = executor.submit(release.wait, 5.0)
    second = executor.submit(ran.append, "queued")
    assert second.cancel()
    release.set()
    assert first.result(5.0) is True
    assert executor.submit(ran.append, "after").result(5.0) is None
    assert ran == ["after"]


def test_a_stalled_counter_warns_once_per_stall_not_per_call(home, caplog):
    """While the encoder load is stuck or broken every call that needed a
    count would otherwise log its own warning; one per stall says the same.
    A count that works ends the stall, so the next failure is news again."""
    state = {"broken": True}

    def flaky(text: str) -> int:
        if state["broken"]:
            raise RuntimeError("the tokenizer is broken")
        return len(text)

    def warnings() -> int:
        return sum(1 for r in caplog.records
                   if r.name == "grimoire.llm" and "could not count" in r.getMessage())

    def call() -> None:
        async def go():
            with usage.meter("chat") as m:
                await _client(ScriptedProvider(CHUNKS), flaky).complete(
                    PROMPT, CONN, usage=m.usage)
        asyncio.run(go())

    caplog.set_level("WARNING", logger="grimoire.llm")
    llm._count_stall.worked()   # a stall an earlier test left open is not this one's
    for _ in range(3):
        call()
    assert warnings() == 1

    state["broken"] = False
    call()
    state["broken"] = True
    call()
    call()
    assert warnings() == 2
    assert [r.get("tokens_estimated") for r in _rows()] == [None] * 3 + [True] + [None] * 2


def test_counting_never_uses_the_default_executor(home):
    """A hung encoder download must not park a default-executor worker, which
    `_lowered`'s picture loads and httpx's DNS lookups need: counts run on the
    facade's own single counting thread."""
    submitted: list[object] = []
    names: list[str] = []

    class Recording(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            submitted.append(fn)
            return super().submit(fn, *args, **kwargs)

    def counter(text: str) -> int:
        names.append(threading.current_thread().name)
        return len(text)

    async def go():
        default = Recording(max_workers=1)
        asyncio.get_running_loop().set_default_executor(default)
        with usage.meter("chat") as m:
            await _client(ScriptedProvider(CHUNKS), counter).complete(PROMPT, CONN, usage=m.usage)
    asyncio.run(go())
    assert names and all(n.startswith("grimoire-count") for n in names)
    assert submitted == []
    assert _one_row()["tokens_estimated"] is True


@pytest.mark.parametrize("content", [
    None,
    [{"type": "text", "text": "the tide"}, "a stray string", 7],
    # Beside a part no route lowers, so the facade hands it on as it is and
    # the estimate is what meets the bad `text`.
    [{"type": "text", "text": 5},
     {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}],
], ids=["none-content", "non-dict-part", "non-str-text"])
def test_malformed_messages_never_fail_the_estimate(home, content):
    """The estimate reads past each shape. Not a claim about the whole call:
    the `non-str-text` shape sits beside an `image_url` part because text
    lowering (`content_parts.text_of`) still raises on it, a separate bug."""
    messages = [{"role": "system", "content": "Be brief."},
                {"role": "user", "content": content}]
    text, row, _m = _filed(_client(ScriptedProvider(CHUNKS)), messages)
    assert text == "".join(CHUNKS)
    assert row["status"] == "ok"


def test_the_estimate_reads_past_every_malformed_shape():
    """The same shapes, and a non-dict message, straight at `Estimate`."""
    messages = ["not a message", None, {"role": "user", "content": None},
                {"role": "user", "content": [None, "x", {"type": "text", "text": 5},
                                             {"type": "text", "text": "kept"},
                                             {"type": "image_url", "image_url": {}}]},
                {"role": "user", "content": 42},
                {"role": "user", "content": "also kept"}]
    est = llm_usage.Estimate(messages)
    assert est.prompt_text() == "kept\nalso kept"
    est.add("one")
    est.add("")
    est.add(None)  # type: ignore[arg-type]
    est.add("two")
    assert est.completion_text() == "onetwo"
    assert est.count(len, completion=True) == (len("kept\nalso kept"), len("onetwo"))
    assert est.count(len, completion=False) == (len("kept\nalso kept"), None)
    assert est.count(len, prompt=False, completion=True) == (None, len("onetwo"))


def test_the_estimate_keeps_a_reference_to_the_prompt():
    messages = [{"role": "user", "content": "a"}]
    est = llm_usage.Estimate(messages)
    messages.append({"role": "user", "content": "b"})
    assert est.prompt_text() == "a\nb"


def test_the_estimate_is_dropped_from_the_holder(home):
    client = _client(ScriptedProvider(CHUNKS))

    async def go():
        m = usage.meter("chat")
        await client.complete(PROMPT, CONN, usage=m.usage)
        before = dict(m.usage)
        m.done()
        return before, m
    before, m = asyncio.run(go())
    assert llm_usage.ESTIMATE_KEY not in before  # the facade let go already
    assert before[llm_usage.ENDED_KEY] is True
    assert llm_usage.ESTIMATE_KEY not in m.usage
    assert llm_usage.ENDED_KEY not in m.usage
    assert _one_row()["tokens_estimated"] is True


def test_image_parts_are_not_counted_as_text(home):
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "Describe the gull."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 4000}}]}]
    _text, row, _m = _filed(_client(ScriptedProvider(CHUNKS)), messages)
    assert row["prompt_tokens"] == tokens.count_tokens("Describe the gull.")


def test_a_text_lowered_prompt_counts_what_was_sent(home):
    """M10: a carrier is dropped by text lowering, so its label is not sent and
    must not be counted."""
    label = content_parts.CARRIED_LABEL.format(alt="a gull over Saltmarch harbour")
    messages = [{"role": "user", "content": "What did you see?"},
                {"role": "user", content_parts.CARRIER: True,
                 "content": [{"type": "text", "text": label},
                             content_parts.ref("/img/gull", "a gull", True)]}]
    provider = ScriptedProvider(CHUNKS)
    _text, row, _m = _filed(_client(provider), messages)
    sent = provider.requests[0]["messages"]
    assert [m["content"] for m in sent] == ["What did you see?"]
    assert row["prompt_tokens"] == tokens.count_tokens("What did you see?")


# ---- reasoning, through the real OpenAI-compatible adapter ----
THOUGHT = "Seraphine weighs whether the ferryman is lying."
PROSE = "He is not."


def _reasoning_client(counter=tokens.count_tokens, handler=None) -> LLMClient:
    def body(request):
        events = [{"choices": [{"delta": {"reasoning_content": THOUGHT}}]},
                  {"choices": [{"delta": {"content": PROSE}}]}]
        return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n"
                                                for e in events) + "data: [DONE]\n\n")
    return LLMClient(openai_compatible=OpenAICompatibleClient(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler or body))),
        retries=0, count_tokens=counter)


def test_reasoning_counts_toward_completion_without_a_display_buffer(home):
    client = _reasoning_client()

    async def go():
        try:
            with usage.meter("chat") as m:
                assert llm_reasoning.KEY not in m.usage
                text = await client.complete(PROMPT, CONN, usage=m.usage)
        finally:
            await client.aclose()
        return text
    assert asyncio.run(go()) == PROSE
    row = _one_row()
    assert row["completion_tokens"] == tokens.count_tokens(THOUGHT + PROSE)
    assert row["prompt_tokens"] == tokens.count_tokens(PROMPT_TEXT)
    assert row["tokens_estimated"] is True


def test_reasoning_is_counted_once_with_a_display_buffer(home):
    client = _reasoning_client()

    async def go():
        try:
            with usage.meter("chat") as m:
                events = [e async for e in llm_reasoning.stream(
                    m.usage, lambda: client.stream(PROMPT, CONN, m.usage))]
        finally:
            await client.aclose()
        return events
    events = asyncio.run(go())
    assert "".join(e.get("thinking_delta", "") for e in events) == THOUGHT
    assert _one_row()["completion_tokens"] == tokens.count_tokens(THOUGHT + PROSE)


def test_a_retried_attempt_counts_only_the_attempt_that_answered(home):
    calls: list[object] = []

    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (b'data: {"choices":[{"delta":{"reasoning_content":'
                   b'"A long thought the first attempt never finished"}}]}\n\n')
            raise httpx.ReadError("disconnected")

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, stream=Broken())
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Yes."}}]}\n\n')

    client = LLMClient(openai_compatible=OpenAICompatibleClient(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler))),
        retries=1, count_tokens=tokens.count_tokens)

    async def go():
        try:
            with usage.meter("chat") as m:
                return await client.complete(PROMPT, CONN, usage=m.usage)
        finally:
            await client.aclose()
    assert asyncio.run(go()) == "Yes."
    row = _one_row()
    assert len(calls) == 2 and row["attempts"] == 2
    assert row["completion_tokens"] == tokens.count_tokens("Yes.")
    assert row["prompt_tokens"] == tokens.count_tokens(PROMPT_TEXT)


# ---- embed rows ----
def test_an_embed_operation_never_gets_a_completion_estimate(home):
    conn = llm_usage.with_account(CONN, operation="embed")
    _text, row, _m = _filed(_client(ScriptedProvider(CHUNKS)), conn=conn)
    assert row["operation"] == "embed"
    assert row["prompt_tokens"] == tokens.count_tokens(PROMPT_TEXT)
    assert "completion_tokens" not in row
    assert row["tokens_estimated"] is True

    holder = {"operation": "embed"}
    llm_usage.fill(holder, 5, 3)
    assert holder == {"operation": "embed", "prompt_tokens": 5,
                      llm_usage.ESTIMATED: True}


def test_fill_writes_only_what_is_missing():
    holder = {"prompt_tokens": 7, "completion_tokens": 3}
    llm_usage.fill(holder, 50, 30)
    assert holder == {"prompt_tokens": 7, "completion_tokens": 3}
    holder = {"completion_tokens": 3}
    llm_usage.fill(holder, 50, None)
    assert holder == {"completion_tokens": 3, "prompt_tokens": 50, llm_usage.ESTIMATED: True}


def test_a_client_without_a_counter_estimates_nothing(home):
    client = LLMClient(openai_compatible=ScriptedProvider(CHUNKS))
    text, row, _m = _filed(client)
    assert text == "".join(CHUNKS)
    _no_estimate(row)


def test_note_helpers_are_no_ops_without_a_holder_or_an_estimate():
    llm_usage.note_prompt(None, PROMPT)
    llm_usage.note_reply(None, "x")
    holder: dict = {}
    llm_usage.note_reply(holder, "x")
    assert holder == {}
    llm_usage.note_prompt(holder, PROMPT)
    first = holder[llm_usage.ESTIMATE_KEY]
    llm_usage.note_prompt(holder, PROMPT)
    assert holder[llm_usage.ESTIMATE_KEY] is not first
    llm_usage.note_reply(holder, 3)  # type: ignore[arg-type]
    llm_usage.note_reply(holder, "")
    assert holder[llm_usage.ESTIMATE_KEY].completion_text() == ""


# ---- the seams ----
def test_build_llm_wires_a_late_bound_counter(monkeypatch):
    from grimoire import routes
    client = routes.common.build_llm()
    monkeypatch.setattr(tokens, "count_tokens", lambda text: 4242)
    assert client._count_tokens("anything") == 4242


def test_usage_imports_no_counter():
    source = pathlib.Path(usage.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            assert "tokens" not in (node.module or "").split(".")
            assert "tokens" not in {alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            assert not any("tokens" in alias.name.split(".") for alias in node.names)


def test_estimate_keys_are_one_spelling():
    assert usage.ESTIMATE_KEY == llm_usage.ESTIMATE_KEY == "_estimate"
    assert usage.ENDED_KEY == llm_usage.ENDED_KEY == "_ended"
    assert usage.ESTIMATED == llm_usage.ESTIMATED == "tokens_estimated"
