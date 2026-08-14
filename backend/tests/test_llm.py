from grimoire.llm_errors import LLMError
from grimoire.openrouter import OpenRouterError


def test_openrouter_error_is_llm_error():
    err = OpenRouterError("auth", "bad key")
    assert isinstance(err, LLMError)
    assert err.kind == "auth"
    assert err.detail == "bad key"


def test_llm_error_detail_defaults_to_kind():
    assert LLMError("network").detail == "network"


from grimoire import llm  # noqa: E402 - deliberate late import; see the lines above
from grimoire.llm import LLMClient  # noqa: E402 - deliberate late import; see the lines above


class FakeProvider:
    def __init__(self, tag):
        self.tag = tag
        self.calls = []

    async def stream(self, messages, *args, **kwargs):
        self.calls.append((args, kwargs))
        yield self.tag


def _conn(kind, **fields):
    return {"kind": kind, "model": "m", "api_key": "k", "base_url": "", "post_process": "none", **fields}


async def test_dispatches_to_openrouter():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openrouter", model="or-model", api_key="sk-or-x")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["or"]
    assert op.calls == [(("or-model", "sk-or-x"), {})]
    assert cl.calls == [] and oc.calls == []


async def test_dispatches_to_claude():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("claude", model="opus")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["cl"]
    assert cl.calls == [(("opus",), {})]
    assert op.calls == [] and oc.calls == []


async def test_claude_missing_model_defaults_to_opus():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("claude", model="")
    [c async for c in client.stream([], conn)]
    assert cl.calls == [(("opus",), {})]


async def test_dispatches_to_openai_compatible_with_strict_flag():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", model="glm-4.6", api_key="sk-z",
                 base_url="https://api.z.ai/v4", post_process="strict")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["oc"]
    assert oc.calls == [(("glm-4.6", "sk-z", "https://api.z.ai/v4"), {"strict": True})]


async def test_openai_compatible_none_post_process_is_not_strict():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", post_process="none")
    [c async for c in client.stream([], conn)]
    assert oc.calls[0][1] == {"strict": False}


async def test_missing_kind_defaults_to_openrouter():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = {"model": "or-model", "api_key": "sk-or-x"}  # defensive: no kind key at all
    assert [c async for c in client.stream([], conn)] == ["or"]



# ---- idle timeout (#243) ----

import asyncio  # noqa: E402 - deliberate late import; see the lines above

import pytest  # noqa: E402 - deliberate late import; see the lines above


STALL = 2.0  # >> the 0.05s timeouts below, but bounded so an unguarded
             # regression fails the suite in seconds instead of hanging it


class StallingProvider:
    """Yields `before` chunks, then stalls — a wedged upstream."""

    def __init__(self, before=()):
        self.before = list(before)
        self.closed = False

    async def stream(self, messages, *args, **kwargs):
        try:
            for chunk in self.before:
                yield chunk
            await asyncio.sleep(STALL)
        finally:
            self.closed = True


def _timeout_client(provider, timeout):
    return LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                     timeout=timeout)


async def test_stalled_stream_raises_timeout_llm_error():
    client = _timeout_client(StallingProvider(), 0.05)
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], _conn("openrouter"))]
    assert exc.value.kind == "timeout"


async def test_deltas_before_the_stall_are_still_yielded():
    """A stall mid-stream must not discard what already arrived — the fence
    watcher and the partial-reply persist path both depend on those deltas."""
    client = _timeout_client(StallingProvider(["a", "b"]), 0.05)
    seen = []
    with pytest.raises(LLMError):
        async for delta in client.stream([], _conn("openrouter")):
            seen.append(delta)
    assert seen == ["a", "b"]


async def test_timeout_closes_the_underlying_generator():
    """Otherwise the provider's httpx stream leaks for the life of the process."""
    provider = StallingProvider()
    client = _timeout_client(provider, 0.05)
    with pytest.raises(LLMError):
        [c async for c in client.stream([], _conn("openrouter"))]
    assert provider.closed


class UnyieldingProvider:
    """A provider whose cleanup ignores the first cancellation — the case that
    makes `await`ing cleanup unsafe at any timeout."""

    def __init__(self):
        self.closing = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(STALL)
        return "never"

    async def aclose(self):
        self.closing = True
        try:
            await asyncio.sleep(0.4)
        except asyncio.CancelledError:
            await asyncio.sleep(0.4)  # resists being cancelled

    def stream(self, messages, *args, **kwargs):
        # Not `async def`: a provider's stream() is an async *generator*
        # function, so calling it hands back the iterator, not a coroutine.
        return self


async def test_cleanup_that_ignores_cancellation_cannot_wedge_the_caller(monkeypatch):
    """The timeout has to reach the caller even when closing the sick provider
    doesn't — otherwise the bound is only as good as the provider's manners."""
    from grimoire import llm as llm_mod
    monkeypatch.setattr(llm_mod, "_CLOSE_TIMEOUT", 0.05)
    provider = UnyieldingProvider()
    client = _timeout_client(provider, 0.05)

    async def consume():
        async for _ in client.stream([], _conn("openrouter")):
            pass

    # Watchdog well under the 0.4s the cleanup insists on taking: awaiting that
    # cleanup — however it is bounded — blows this, abandoning it does not.
    with pytest.raises(LLMError) as exc:
        await asyncio.wait_for(consume(), 0.25)
    assert exc.value.kind == "timeout" and provider.closing
    await asyncio.sleep(0.5)  # let the abandoned cleanup finish before teardown


class UncancellableProvider:
    """A provider whose *pull* ignores the first cancellation. Cancelling and
    then waiting for that cancellation to land is what turns a wedged upstream
    back into a wedged request — the failure #243 is about."""

    def __init__(self):
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            await asyncio.sleep(STALL)
        except asyncio.CancelledError:
            await asyncio.sleep(0.4)  # resists, then finally lets go
        return "never"

    async def aclose(self):
        self.closed = True

    def stream(self, messages, *args, **kwargs):
        return self


async def test_an_abandoned_pull_is_closed_once_it_finally_settles(monkeypatch):
    """Skipping the close of a still-running iterator is required (closing one
    mid-__anext__ raises) — but skipping it forever leaks the connection, so
    the close has to happen when the pull eventually lets go."""
    from grimoire import llm as llm_mod
    monkeypatch.setattr(llm_mod, "_CLOSE_TIMEOUT", 0.05)
    provider = UncancellableProvider()
    client = _timeout_client(provider, 0.05)
    with pytest.raises(LLMError):
        async for _ in client.stream([], _conn("openrouter")):
            pass
    assert not provider.closed  # still running: closing it now would raise
    await asyncio.sleep(0.5)    # the pull lets go
    assert provider.closed


async def test_a_pull_that_ignores_cancellation_cannot_wedge_the_caller(monkeypatch):
    from grimoire import llm as llm_mod
    monkeypatch.setattr(llm_mod, "_CLOSE_TIMEOUT", 0.05)
    provider = UncancellableProvider()
    client = _timeout_client(provider, 0.05)

    async def consume():
        async for _ in client.stream([], _conn("openrouter")):
            pass

    with pytest.raises(LLMError) as exc:
        await asyncio.wait_for(consume(), 0.25)  # << the 0.4s the pull insists on
    assert exc.value.kind == "timeout"
    await asyncio.sleep(0.5)  # let the abandoned pull finish before teardown


async def test_caller_side_close_reaches_the_provider():
    """An SSE client that disconnects mid-stream closes the guard; the provider
    (and its open httpx response) has to be closed with it."""
    provider = StallingProvider(["a"])
    client = _timeout_client(provider, 0)  # unbounded: only the close can end this
    agen = client.stream([], _conn("openrouter"))
    assert await agen.__anext__() == "a"
    await agen.aclose()
    assert provider.closed


class ReasoningProvider:
    """Streams liveness heartbeats for `beats` rounds — a model that is
    thinking, not one that is wedged — then produces its answer."""

    def __init__(self, beats):
        self.beats = beats

    async def stream(self, messages, *args, **kwargs):
        for _ in range(self.beats):
            await asyncio.sleep(0.02)
            yield ""
        yield "the answer"


async def test_heartbeats_hold_the_bound_open_and_never_reach_the_caller():
    """Total time here (~0.16s) is well past the 0.05s bound: only the *gap*
    between frames matters, and an empty frame is provider activity, not text."""
    client = _timeout_client(ReasoningProvider(8), 0.05)
    assert [c async for c in client.stream([], _conn("openrouter"))] == ["the answer"]


async def test_a_gap_between_heartbeats_still_times_out():
    """The heartbeat must not become a way to never time out."""
    client = _timeout_client(ReasoningProvider(1), 0.005)
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], _conn("openrouter"))]
    assert exc.value.kind == "timeout"


class SilentThenAnswers:
    """Says nothing at all for `quiet` seconds — not even a keep-alive — then
    answers. The model that is connecting, or thinking without streaming its
    reasoning: healthy, and indistinguishable from wedged without a tick."""

    def __init__(self, quiet, answer):
        self.quiet = quiet
        self.answer = answer

    async def stream(self, messages, *args, **kwargs):
        await asyncio.sleep(self.quiet)
        yield self.answer


async def test_the_facade_ticks_while_it_waits(monkeypatch):
    """A silent provider still tells the caller the stream is alive (#95).
    Empty strings, then the text: three ticks over a ~0.1s wait at a 0.03s
    interval, and no tick once the answer starts flowing."""
    monkeypatch.setattr(llm, "HEARTBEAT_INTERVAL", 0.03)
    client = _timeout_client(SilentThenAnswers(0.1, "the answer"), 0)  # no idle bound
    chunks = [c async for c in client.stream([], _conn("openrouter"))]
    assert chunks[-1] == "the answer"
    assert chunks[:-1] and set(chunks[:-1]) == {""}


async def test_ticking_does_not_extend_the_idle_bound(monkeypatch):
    """The bound counts provider activity, and a tick is not that. A heartbeat
    interval shorter than the timeout must not turn the timeout off."""
    monkeypatch.setattr(llm, "HEARTBEAT_INTERVAL", 0.01)
    client = _timeout_client(StallingProvider(), 0.05)
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], _conn("openrouter"))]
    assert exc.value.kind == "timeout"


async def test_a_chatty_silent_provider_cannot_suppress_the_tick(monkeypatch):
    """The regression review caught. Both adapters yield "" for every upstream
    SSE line, so a model streaming reasoning delivers frames far faster than the
    interval. A tick clock reset per pull never expires, and the caller's
    connection stays silent for the whole reasoning phase — the exact case the
    heartbeat exists for. The clock spans pulls and only text resets it, so
    ~0.16s of dense empty frames at a 0.03s interval has to produce ticks."""
    monkeypatch.setattr(llm, "HEARTBEAT_INTERVAL", 0.03)
    client = _timeout_client(ReasoningProvider(8), 0)  # frames every 0.02s
    chunks = [c async for c in client.stream([], _conn("openrouter"))]
    assert chunks[-1] == "the answer"
    assert chunks.count("") >= 2


async def test_text_resets_the_tick_clock(monkeypatch):
    """A stream that is actually producing prose needs no liveness signal —
    the prose is the signal."""
    monkeypatch.setattr(llm, "HEARTBEAT_INTERVAL", 0.05)
    client = _timeout_client(FakeProvider("or"), 0)
    assert [c async for c in client.stream([], _conn("openrouter"))] == ["or"]


async def test_a_provider_heartbeat_is_still_not_a_facade_tick(monkeypatch):
    """The two empties are different signals and only one is on a schedule the
    caller chose. A provider frame resets the bound and stops at the facade; if
    it were forwarded instead, this stream would emit eight of them."""
    monkeypatch.setattr(llm, "HEARTBEAT_INTERVAL", 0)  # ticking off
    client = _timeout_client(ReasoningProvider(8), 0.05)
    assert [c async for c in client.stream([], _conn("openrouter"))] == ["the answer"]


async def test_healthy_stream_is_untouched_by_the_guard():
    op = FakeProvider("or")
    client = _timeout_client(op, 0.05)
    assert [c async for c in client.stream([], _conn("openrouter"))] == ["or"]


async def test_zero_timeout_disables_the_bound():
    provider = StallingProvider(["a"])
    client = _timeout_client(provider, 0)
    agen = client.stream([], _conn("openrouter"))
    assert await agen.__anext__() == "a"
    with pytest.raises(asyncio.TimeoutError):  # hangs, unguarded, as configured
        await asyncio.wait_for(agen.__anext__(), 0.05)
    await agen.aclose()


async def test_complete_inherits_the_timeout():
    client = _timeout_client(StallingProvider(["partial"]), 0.05)
    with pytest.raises(LLMError) as exc:
        await client.complete([], _conn("openrouter"))
    assert exc.value.kind == "timeout"


async def test_a_resolver_is_consulted_per_call():
    """routes passes the config.md setting as a callable rather than a number,
    so a Configuration-page change lands without a restart — and so this module
    never has to import the store (see llm_errors' leaf rule)."""
    setting = [0.0]  # unbounded to start
    provider = StallingProvider(["a"])
    client = _timeout_client(provider, lambda: setting[0])
    agen = client.stream([], _conn("openrouter"))
    assert await agen.__anext__() == "a"
    await agen.aclose()

    setting[0] = 0.05  # the user tightens it mid-session
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], _conn("openrouter"))]
    assert exc.value.kind == "timeout"


async def test_a_client_given_no_timeout_uses_the_module_default():
    from grimoire import llm as llm_mod
    client = LLMClient(openrouter=FakeProvider("or"))
    assert client._timeout_seconds() == llm_mod.DEFAULT_TIMEOUT


# ---- retry with backoff, and the fallback route (#144) ----


class FlakyProvider:
    """Fails the first `failures` attempts with `kind`, then streams `reply`."""

    def __init__(self, failures: int, kind: str = "rate_limit", reply=("ok",)):
        self.failures = failures
        self.kind = kind
        self.reply = list(reply)
        self.attempts = 0
        self.models: list[str] = []

    async def stream(self, messages, model="", *args, **kwargs):
        self.attempts += 1
        self.models.append(model)
        if self.attempts <= self.failures:
            raise LLMError(self.kind, f"attempt {self.attempts}")
        for chunk in self.reply:
            yield chunk


class HalfwayProvider:
    """Yields `before`, then fails — the case a retry must NOT paper over."""

    def __init__(self, before=("half a sentence",), kind="network"):
        self.before = list(before)
        self.kind = kind
        self.attempts = 0

    async def stream(self, messages, *args, **kwargs):
        self.attempts += 1
        for chunk in self.before:
            yield chunk
        raise LLMError(self.kind, "died mid-stream")


def _retry_client(provider, retries=2, fallback=None, timeout=0):
    return LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                     timeout=timeout, retries=retries, fallback=fallback)


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    """Keep the schedule's shape (it is asserted on its own below) but stop the
    behavioural tests from actually sleeping through it."""
    from grimoire import llm as llm_mod
    monkeypatch.setattr(llm_mod, "RETRY_BASE", 0.0)


async def test_a_transient_failure_is_retried_and_then_succeeds():
    provider = FlakyProvider(failures=2)
    client = _retry_client(provider)
    assert [c async for c in client.stream([], _conn("openrouter"))] == ["ok"]
    assert provider.attempts == 3


async def test_retries_are_bounded():
    provider = FlakyProvider(failures=99)
    client = _retry_client(provider, retries=2)
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], _conn("openrouter"))]
    assert provider.attempts == 3          # the first attempt plus two retries
    assert exc.value.kind == "rate_limit"  # the provider's own error, not a new one


async def test_zero_retries_is_the_old_one_attempt_behaviour():
    provider = FlakyProvider(failures=99)
    client = _retry_client(provider, retries=0)
    with pytest.raises(LLMError):
        [c async for c in client.stream([], _conn("openrouter"))]
    assert provider.attempts == 1


@pytest.mark.parametrize("kind", ["auth", "missing_key", "bad_response",
                                  "missing_dependency", "timeout"])
async def test_non_transient_failures_are_not_retried(kind):
    """Retrying configuration errors is a slower way to show the same message,
    and retrying a timeout would multiply the one bound the user set."""
    provider = FlakyProvider(failures=99, kind=kind)
    client = _retry_client(provider, retries=3)
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], _conn("openrouter"))]
    assert provider.attempts == 1
    assert exc.value.kind == kind


async def test_a_failure_after_text_has_been_sent_is_never_retried():
    """The bytes are already on the wire; a fresh attempt would duplicate what
    the reader has seen. #144's explicitly-out-of-scope case."""
    provider = HalfwayProvider()
    client = _retry_client(provider, retries=3)
    seen = []
    with pytest.raises(LLMError):
        async for chunk in client.stream([], _conn("openrouter")):
            seen.append(chunk)
    assert seen == ["half a sentence"]
    assert provider.attempts == 1


async def test_a_heartbeat_already_sent_does_not_count_as_text(monkeypatch):
    """The facade's liveness signal reaches the caller as an empty chunk, which
    the routes turn into an SSE comment — framing, carrying no content. A fresh
    attempt after one duplicates nothing, so it must not disable the retry."""
    from grimoire import llm as llm_mod
    monkeypatch.setattr(llm_mod, "HEARTBEAT_INTERVAL", 0.01)
    provider = SlowFailingProvider(failures=1, stall=0.05)
    client = _retry_client(provider, retries=1)
    seen = [c async for c in client.stream([], _conn("openrouter"))]
    assert "" in seen, "no heartbeat fired: the test proves nothing"
    assert [c for c in seen if c] == ["ok"]
    assert provider.opened == 2


async def test_complete_gets_the_retries_too():
    provider = FlakyProvider(failures=1, reply=("some ", "prose"))
    client = _retry_client(provider)
    assert await client.complete([], _conn("openrouter")) == "some prose"
    assert provider.attempts == 2


async def test_a_malformed_retry_setting_falls_back_to_the_default():
    from grimoire import llm as llm_mod
    client = _retry_client(FlakyProvider(0), retries=lambda: "not a number")
    assert client._retry_count() == llm_mod.DEFAULT_RETRIES
    assert _retry_client(FlakyProvider(0), retries=lambda: -5)._retry_count() == 0


async def test_a_client_given_no_retry_resolver_uses_the_module_default():
    from grimoire import llm as llm_mod
    assert LLMClient(openrouter=FakeProvider("or"))._retry_count() == llm_mod.DEFAULT_RETRIES


async def test_the_retry_count_is_resolved_per_call():
    setting = [0]
    provider = FlakyProvider(failures=1)
    client = _retry_client(provider, retries=lambda: setting[0])
    with pytest.raises(LLMError):
        [c async for c in client.stream([], _conn("openrouter"))]
    assert provider.attempts == 1
    setting[0] = 2  # the user turns retries on mid-session
    assert [c async for c in client.stream([], _conn("openrouter"))] == ["ok"]


async def test_a_long_backoff_keeps_reporting_liveness(monkeypatch):
    """Between attempts there is no provider stream for `_guard` to time, so an
    unsliced sleep is a window where nothing crosses the caller's connection at
    all — and a proxy drops a silent SSE stream."""
    from grimoire import llm as llm_mod
    monkeypatch.setattr(llm_mod, "RETRY_BASE", 0.2)
    monkeypatch.setattr(llm_mod, "HEARTBEAT_INTERVAL", 0.02)
    provider = FlakyProvider(failures=1)
    client = _retry_client(provider, retries=1)
    seen = [c async for c in client.stream([], _conn("openrouter"))]
    assert seen.count("") >= 3          # sliced, not one long silence
    assert [c for c in seen if c] == ["ok"]


def test_backoff_grows_exponentially_capped_and_jittered(monkeypatch):
    from grimoire import llm as llm_mod
    monkeypatch.setattr(llm_mod, "RETRY_BASE", 0.5)  # undo the module's instant-backoff fixture
    for attempt in range(8):
        ceiling = min(llm_mod.RETRY_CAP, llm_mod.RETRY_BASE * (2 ** attempt))
        draws = {llm_mod._backoff_delay(attempt) for _ in range(50)}
        assert all(ceiling / 2 <= d <= ceiling for d in draws)
        assert len(draws) > 1, "unjittered: every caller would retry in lockstep"
    assert llm_mod._backoff_delay(0) < llm_mod._backoff_delay(6)
    assert llm_mod._backoff_delay(30) <= llm_mod.RETRY_CAP


# --- the fallback route ---


class RouteRecorder:
    """One provider standing in for several connections, remembering which
    model each attempt asked for so a fallback is visible in the record."""

    def __init__(self, failing: set[str], kind="rate_limit"):
        self.failing = failing
        self.kind = kind
        self.models: list[str] = []

    async def stream(self, messages, model="", *args, **kwargs):
        self.models.append(model)
        if model in self.failing:
            raise LLMError(self.kind, f"{model} is unavailable")
        yield f"from {model}"


def _route(id, model):
    return {"id": id, "name": f"conn-{id}", "kind": "openrouter", "model": model, "api_key": "k"}


async def test_the_fallback_answers_once_the_primary_is_exhausted():
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=1, fallback=lambda: _route("b", "backup"))
    chunks = [c async for c in client.stream([], _route("a", "primary"))]
    assert chunks == ["from backup"]
    # Two attempts on the primary (first + one retry), then exactly one on the
    # fallback -- #144's "tried once after the primary's retries are exhausted".
    assert provider.models == ["primary", "primary", "backup"]


async def test_the_fallback_is_tried_for_non_retryable_failures_too():
    """A repeat cannot fix a bad key, but a different connection can — that is
    the whole condition someone configures a fallback for."""
    provider = RouteRecorder(failing={"primary"}, kind="auth")
    client = _retry_client(provider, retries=3, fallback=lambda: _route("b", "backup"))
    assert [c async for c in client.stream([], _route("a", "primary"))] == ["from backup"]
    assert provider.models == ["primary", "backup"]  # no wasted retries


async def test_the_primary_error_survives_a_failing_fallback():
    provider = RouteRecorder(failing={"primary", "backup"})
    client = _retry_client(provider, retries=0, fallback=lambda: _route("b", "backup"))
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], _route("a", "primary"))]
    assert exc.value.detail == "backup is unavailable"
    assert provider.models == ["primary", "backup"]


async def test_no_fallback_configured_leaves_one_route():
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=0, fallback=lambda: None)
    with pytest.raises(LLMError):
        [c async for c in client.stream([], _route("a", "primary"))]
    assert provider.models == ["primary"]


async def test_a_fallback_pointing_at_the_active_connection_is_dropped():
    """Otherwise it is a third attempt wearing a different name, and it doubles
    how long the user waits to hear that the provider is down."""
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=0, fallback=lambda: _route("a", "primary"))
    with pytest.raises(LLMError):
        [c async for c in client.stream([], _route("a", "primary"))]
    assert provider.models == ["primary"]


async def test_a_resolver_that_raises_means_no_fallback():
    """A broken fallback must not be able to fail a generation the primary
    would have served."""
    def boom():
        raise OSError("store unreadable")

    provider = RouteRecorder(failing=set())
    client = _retry_client(provider, retries=0, fallback=boom)
    assert [c async for c in client.stream([], _route("a", "primary"))] == ["from primary"]


async def test_the_fallback_is_never_reached_when_the_primary_answers():
    provider = RouteRecorder(failing=set())
    client = _retry_client(provider, retries=2, fallback=lambda: _route("b", "backup"))
    assert [c async for c in client.stream([], _route("a", "primary"))] == ["from primary"]
    assert provider.models == ["primary"]


async def test_a_fallback_is_not_taken_after_text_has_been_sent():
    provider = HalfwayProvider()
    client = _retry_client(provider, retries=2, fallback=lambda: _route("b", "backup"))
    seen = []
    with pytest.raises(LLMError):
        async for chunk in client.stream([], _route("a", "primary")):
            seen.append(chunk)
    assert seen == ["half a sentence"] and provider.attempts == 1


async def test_falling_back_is_logged(caplog):
    """The user is not told which route answered — a stream has no room for it
    — so the operator record is the log line. Deliberately the honest, cheap
    surface; per-response reporting is not solved here."""
    import logging
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=0, fallback=lambda: _route("b", "backup"))
    with caplog.at_level(logging.WARNING, logger="grimoire.llm"):
        [c async for c in client.stream([], _route("a", "primary"))]
    assert "falling back" in caplog.text
    assert "conn-b" in caplog.text and "rate_limit" in caplog.text


class SlowFailingProvider:
    """Stalls, then fails `failures` times, then answers. The stall is what lets
    a heartbeat fire and a close be told from a leak."""

    def __init__(self, failures=0, stall=0.0, tail=0.0):
        self.failures = failures
        self.stall = stall
        self.tail = tail
        self.opened = 0
        self.closed = 0

    async def stream(self, messages, *args, **kwargs):
        self.opened += 1
        try:
            if self.stall:
                await asyncio.sleep(self.stall)
            if self.opened <= self.failures:
                raise LLMError("network", f"attempt {self.opened}")
            yield "ok"
            if self.tail:
                await asyncio.sleep(self.tail)
        finally:
            self.closed += 1


async def test_each_attempt_opens_its_own_provider_stream_and_closes_it():
    provider = SlowFailingProvider(failures=2)
    client = _retry_client(provider, retries=2)
    assert [c async for c in client.stream([], _conn("openrouter"))] == ["ok"]
    assert provider.opened == 3 and provider.closed == 3


async def test_closing_the_retried_stream_closes_the_provider():
    """The retry wrapper now sits between the caller and `_guard`, so it is what
    has to propagate a caller-side close — an SSE client disconnecting — down to
    httpx. Skip it and every cancelled turn strands a connection."""
    provider = SlowFailingProvider(tail=STALL)
    client = _retry_client(provider, retries=2, timeout=0)
    agen = client.stream([], _conn("openrouter"))
    assert await agen.__anext__() == "ok"   # suspended mid-generation
    await agen.aclose()                     # the caller goes away
    assert provider.opened == 1 and provider.closed == 1


import ast  # noqa: E402 - deliberate late import; see the lines above
from pathlib import Path  # noqa: E402 - deliberate late import; see the lines above

import grimoire  # noqa: E402 - deliberate late import; see the lines above

# The LLM gateway: the facade plus the three providers it dispatches to, and the
# error module they all share.
GATEWAY_MODULES = ("llm", "llm_errors", "openrouter", "claude_agent", "openai_compatible")
PROVIDERS = ("claude_agent", "openai_compatible", "openrouter")


def _sibling_imports(name: str) -> set[str]:
    """Names in the grimoire package that `name` imports.

    Deliberately counts function-scope imports too: deferring an import into a
    function is how an import cycle gets worked around, so a check that ignored
    them would call the workaround "acyclic" and let the cycle back in. Both
    spellings of an edge count — `from .llm import x` and `from grimoire.llm
    import x` reach the same module and cycle the same way.
    """
    src = Path(grimoire.__file__).with_name(f"{name}.py").read_text(encoding="utf-8")
    found: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module:  # from .llm import x
                found.add(node.module)
            elif node.level == 1:  # from . import llm
                found.update(a.name for a in node.names)
            elif node.level == 0 and node.module == "grimoire":  # from grimoire import llm
                found.update(a.name for a in node.names)
            elif node.level == 0 and (node.module or "").startswith("grimoire."):
                found.add(node.module.split(".", 1)[1])  # from grimoire.llm import x
        elif isinstance(node, ast.Import):  # import grimoire.llm
            found.update(a.name.split(".", 1)[1] for a in node.names
                         if a.name.startswith("grimoire."))
    return found


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    done: set[str] = set()

    def walk(node: str, path: list[str]) -> list[str] | None:
        if node in path:
            return path[path.index(node):] + [node]
        if node in done:
            return None
        for nxt in sorted(graph.get(node, ())):
            if cycle := walk(nxt, path + [node]):
                return cycle
        done.add(node)
        return None

    for start in graph:
        if cycle := walk(start, []):
            return cycle
    return None


def _reachable_graph() -> dict[str, set[str]]:
    """Everything the gateway can reach, not just the five gateway modules.

    Restricting the graph to the gateway would miss a cycle routed through a
    helper (provider → prompts → llm), so this walks outward from the gateway
    until it runs dry. Subpackages are not followed: `store/` carries its own
    known file-level cycle, and no gateway module imports it — if one ever
    does, that edge is simply not traced rather than failing this test for an
    unrelated reason.
    """
    pkg = Path(grimoire.__file__).parent
    graph: dict[str, set[str]] = {}
    queue = list(GATEWAY_MODULES)
    while queue:
        name = queue.pop()
        if name in graph:
            continue
        graph[name] = {m for m in _sibling_imports(name) if (pkg / f"{m}.py").is_file()}
        queue.extend(graph[name])
    return graph


def test_llm_gateway_imports_are_acyclic():
    """Regression for #239: LLMError lives in its own leaf module, so nothing
    the gateway reaches can import its way back into the gateway."""
    cycle = _find_cycle(_reachable_graph())
    assert cycle is None, "import cycle: " + " -> ".join(cycle or [])


def test_llm_errors_stays_a_leaf():
    """The whole fix rests on this module importing nothing from the package."""
    assert _sibling_imports("llm_errors") == set()


def test_llm_imports_its_providers_at_module_scope():
    """#239 asked for the deferred imports to go, not just for the cycle to be
    survivable: an acyclic graph is equally happy with them back inside
    __init__, so pin the module body itself."""
    tree = ast.parse(Path(grimoire.__file__).with_name("llm.py").read_text(encoding="utf-8"))
    body_imports = {node.module for node in tree.body
                    if isinstance(node, ast.ImportFrom) and node.level == 1}
    assert set(PROVIDERS) <= body_imports
