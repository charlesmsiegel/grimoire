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
from grimoire.anthropic import AnthropicClient  # noqa: E402 - deliberate late import
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
    assert op.calls == [(("or-model", "sk-or-x"), {"usage": None})]
    assert cl.calls == [] and oc.calls == []


async def test_dispatches_to_claude():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("claude", model="opus")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["cl"]
    assert cl.calls == [(("opus",), {"usage": None})]
    assert op.calls == [] and oc.calls == []


async def test_claude_missing_model_defaults_to_opus():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("claude", model="")
    [c async for c in client.stream([], conn)]
    assert cl.calls == [(("opus",), {"usage": None})]


async def test_dispatches_to_openai_compatible_with_strict_flag():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", model="glm-4.6", api_key="sk-z",
                 base_url="https://api.z.ai/v4", post_process="strict")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["oc"]
    assert oc.calls == [(("glm-4.6", "sk-z", "https://api.z.ai/v4"),
                        {"strict": True, "usage": None})]


async def test_openai_compatible_none_post_process_is_not_strict():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", post_process="none")
    [c async for c in client.stream([], conn)]
    assert oc.calls[0][1] == {"strict": False, "usage": None}


async def test_missing_kind_defaults_to_openrouter():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = {"model": "or-model", "api_key": "sk-or-x"}  # defensive: no kind key at all
    assert [c async for c in client.stream([], conn)] == ["or"]


async def test_dispatches_to_anthropic_with_the_effective_body_share():
    op, cl, oc, an = (FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc"),
                      FakeProvider("an"))
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc, anthropic=an)
    conn = _conn("anthropic", model="claude-test-1", api_key="test-key",
                 base_url="https://proxy.example.com")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["an"]
    assert an.calls == [(("claude-test-1", "test-key"),
                         {"usage": None, "base_url": "https://proxy.example.com",
                          "effective": {"max_tokens": 16000}})]
    assert op.calls == [] and cl.calls == [] and oc.calls == []


async def test_anthropic_gets_the_controls_effective_decides_capped_by_the_catalog():
    an = FakeProvider("an")
    client = LLMClient(openrouter=FakeProvider("or"), claude=FakeProvider("cl"),
                       openai_compatible=FakeProvider("oc"), anthropic=an)
    conn = _conn("anthropic", model="claude-test-1",
                 model_features={"max_tokens": 8192, "adaptive_thinking": True,
                                 "enabled_thinking": False, "effort": ["low", "medium", "high"]},
                 sampling={"preset_id": "p", "preset_name": "Terse", "scope": "connection",
                           "params": {"temperature": 0.7, "stop": ["END"],
                                      "reasoning_effort": "low"}})
    [c async for c in client.stream([], conn)]
    effective = an.calls[0][1]["effective"]
    assert effective == {"max_tokens": 8192, "stop_sequences": ["END"],
                         "thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}
    assert effective == llm.llm_sampling.effective(conn)["effective"]


def test_anthropic_is_a_listable_kind_that_carries_images():
    assert "anthropic" in llm.LISTABLE_KINDS
    assert "anthropic" not in llm.TEXT_ONLY_KINDS


async def test_aclose_closes_the_anthropic_client_too():
    closed = []

    class Closable:
        def __init__(self, tag):
            self.tag = tag

        async def aclose(self):
            closed.append(self.tag)

    client = LLMClient(openrouter=Closable("or"), claude=Closable("cl"),
                       openai_compatible=Closable("oc"), anthropic=Closable("an"))
    await client.aclose()
    assert "an" in closed and "or" in closed and "oc" in closed


def test_a_default_client_builds_the_real_anthropic_adapter():
    assert isinstance(LLMClient()._anthropic, AnthropicClient)


# ---- the catalog and the probe are dispatched by kind too (#146, #149) ----
from tests.llm_fakes import (  # noqa: E402 - see the late imports above
    RecordingProvider,
    RefusingProvider,
    SequencedProvider,
    carrying,
)


def _asking_client(**kinds):
    return LLMClient(openrouter=kinds.get("openrouter", RecordingProvider()),
                     claude=kinds.get("claude", RecordingProvider()),
                     openai_compatible=kinds.get("openai_compatible", RecordingProvider()),
                     anthropic=kinds.get("anthropic", RecordingProvider()))


async def test_list_models_asks_openrouter_for_an_openrouter_connection():
    """#149's gap in one assertion: the catalog comes from the connection's own
    provider, with its own key, rather than from a hardcoded URL."""
    op = RecordingProvider(models=[{"id": "a/b"}])
    client = _asking_client(openrouter=op)

    assert await client.list_models(_conn("openrouter", api_key="sk-or-x")) == [{"id": "a/b"}]
    assert op.listed == [("sk-or-x",)]


async def test_list_models_asks_the_endpoint_for_a_custom_connection():
    oc = RecordingProvider()
    client = _asking_client(openai_compatible=oc)

    await client.list_models(_conn("openai_compatible", base_url="https://x/v1", api_key="k"))

    assert oc.listed == [("https://x/v1", "k")]


async def test_list_models_asks_the_anthropic_api_for_an_anthropic_connection():
    an = RecordingProvider(models=[{"id": "claude-test-1"}])
    client = _asking_client(anthropic=an)

    got = await client.list_models(_conn("anthropic", api_key="test-key", base_url=""))

    assert got == [{"id": "claude-test-1"}]
    assert an.listed == [("test-key", "")]


async def test_list_models_refuses_the_kind_with_no_catalog():
    """A backstop: the route already refuses this, and what it must not do is
    reach a provider with no `list_models` and raise an AttributeError."""
    client = _asking_client()
    with pytest.raises(LLMError) as exc:
        await client.list_models(_conn("claude"))
    assert "catalog" in exc.value.detail


async def test_check_probes_the_connections_own_provider():
    op, cl, oc, an = (RecordingProvider(), RecordingProvider(), RecordingProvider(),
                      RecordingProvider())
    client = _asking_client(openrouter=op, claude=cl, openai_compatible=oc, anthropic=an)

    await client.check(_conn("openrouter", api_key="sk-or-x"))
    await client.check(_conn("claude", model=""))
    await client.check(_conn("openai_compatible", base_url="https://x/v1", api_key="k"))
    await client.check(_conn("anthropic", api_key="test-key",
                             base_url="https://proxy.example.com"))

    assert op.probed == [("sk-or-x",)]
    assert cl.probed == [("opus",)]        # the effective model, as a turn would run
    assert oc.probed == [("https://x/v1", "k")]
    assert an.probed == [("test-key", "https://proxy.example.com")]


async def test_a_failing_check_is_not_retried_or_fallen_back_to_another_provider():
    """"Is this connection healthy" answered by trying a different connection
    is not an answer, and a retry would report a rate-limited provider as
    healthy after waiting out the very window being asked about."""
    op = RecordingProvider(probe_error=LLMError("rate_limit", "slow down"))
    oc = RecordingProvider()
    client = LLMClient(openrouter=op, claude=RecordingProvider(), openai_compatible=oc,
                       retries=3)

    with pytest.raises(LLMError) as exc:
        # A fallback within reach: `check` must not take it.
        await client.check(carrying(_conn("openrouter", id="or"),
                                    _conn("openai_compatible", id="fb")))

    assert exc.value.kind == "rate_limit"
    assert len(op.probed) == 1 and oc.probed == []



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


def _retry_client(provider, retries=2, timeout=0):
    return LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                     timeout=timeout, retries=retries)


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


# --- Retry-After: the provider naming its own window (#144) ---


def _record_sleeps(monkeypatch):
    """Capture what the backoff asks to sleep for, without sleeping through it.

    The real `asyncio.sleep` is bound BEFORE patching: `llm` reaches it as
    `asyncio.sleep`, so the patch lands on the module `llm` shares with this
    test, and a replacement that called `asyncio.sleep` by name would call
    itself. Sleeps are recorded per slice (the wait is cut at the heartbeat
    interval), so assertions are on the sum.
    """
    real, slept = asyncio.sleep, []

    async def fake(seconds):
        slept.append(seconds)
        await real(0)

    monkeypatch.setattr(asyncio, "sleep", fake)
    return slept


class RateLimited:
    """429s `failures` times, naming `retry_after` seconds each time."""

    def __init__(self, failures, retry_after):
        self.failures = failures
        self.retry_after = retry_after
        self.attempts = 0

    async def stream(self, messages, *args, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise LLMError("rate_limit", "slow down", self.retry_after)
        yield "ok"


async def test_a_named_window_beats_our_own_backoff(monkeypatch):
    """Retrying before the provider's own window is a request it has already
    said it will reject."""
    slept = _record_sleeps(monkeypatch)
    provider = RateLimited(failures=1, retry_after=3.0)
    client = _retry_client(provider, retries=1)
    assert [c async for c in client.stream([], _conn("openrouter"))] == ["ok"]
    assert sum(slept) == pytest.approx(3.0)


async def test_our_backoff_wins_when_it_is_longer(monkeypatch):
    """`max`, not a replacement: a `Retry-After: 1` late in a sequence must not
    walk the backoff back down below the wait the attempt before it had."""
    from grimoire import llm as llm_mod
    monkeypatch.setattr(llm_mod, "RETRY_BASE", 4.0)
    slept = _record_sleeps(monkeypatch)
    provider = RateLimited(failures=1, retry_after=0.001)
    client = _retry_client(provider, retries=1)
    [c async for c in client.stream([], _conn("openrouter"))]
    assert sum(slept) >= 2.0   # the schedule's floor for attempt 0, not 0.001


async def test_a_window_longer_than_we_will_wait_stops_the_retries():
    """A multi-minute window is the provider saying it will not serve this
    soon. Sitting on it holds a scene hostage; the honest answer is to stop."""
    from grimoire import llm as llm_mod
    provider = RateLimited(failures=99, retry_after=llm_mod.RETRY_AFTER_CAP + 1)
    client = _retry_client(provider, retries=5)
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], _conn("openrouter"))]
    assert provider.attempts == 1
    assert exc.value.kind == "rate_limit"


async def test_a_window_we_will_not_wait_out_still_takes_the_fallback():
    """Stopping the retries must not also skip the route that could answer."""
    from grimoire import llm as llm_mod

    class Recorder:
        models = []

        async def stream(self, messages, model="", *args, **kwargs):
            Recorder.models.append(model)
            if model == "primary":
                raise LLMError("rate_limit", "come back later", llm_mod.RETRY_AFTER_CAP + 1)
            yield f"from {model}"

    Recorder.models = []
    client = _retry_client(Recorder(), retries=5)
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    assert [c async for c in client.stream([], chain)] == ["from backup"]
    assert Recorder.models == ["primary", "backup"]


async def test_an_error_that_names_no_window_uses_the_schedule():
    provider = RateLimited(failures=1, retry_after=None)
    client = _retry_client(provider, retries=1)
    assert [c async for c in client.stream([], _conn("openrouter"))] == ["ok"]
    assert provider.attempts == 2


def test_an_error_defaults_to_naming_no_window():
    """Every raise site that has no response to read from — and there are
    dozens — keeps working untouched."""
    assert LLMError("network", "reset").retry_after is None


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
    client = _retry_client(provider, retries=1)
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    chunks = [c async for c in client.stream([], chain)]
    assert chunks == ["from backup"]
    # Two attempts on the primary (first + one retry), then exactly one on the
    # fallback -- #144's "tried once after the primary's retries are exhausted".
    assert provider.models == ["primary", "primary", "backup"]


async def test_complete_retries_overrides_only_the_primary_count():
    """Slice H ruling 12: a decide chain's fallback STAGE is its own call, with
    the one attempt a fallback gets. `retries=` replaces the primary route's
    count and nothing else -- the fallback the dict carries is still tried
    once -- and without it `complete` makes exactly the requests it did."""
    both = {"primary", "backup"}
    provider = RouteRecorder(failing=both)
    client = _retry_client(provider, retries=3)
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    with pytest.raises(LLMError):
        await client.complete([], chain, retries=1)
    assert provider.models == ["primary", "primary", "backup"]

    provider.models.clear()
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    with pytest.raises(LLMError):
        await client.complete([], chain, retries=0)
    assert provider.models == ["primary", "backup"]

    provider.models.clear()
    with pytest.raises(LLMError):
        await client.complete([], carrying(_route("a", "primary"), _route("b", "backup")))
    assert provider.models == ["primary"] * 4 + ["backup"]


async def test_the_fallback_is_tried_for_non_retryable_failures_too():
    """A repeat cannot fix a bad key, but a different connection can — that is
    the whole condition someone configures a fallback for."""
    provider = RouteRecorder(failing={"primary"}, kind="auth")
    client = _retry_client(provider, retries=3)
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    assert [c async for c in client.stream([], chain)] == ["from backup"]
    assert provider.models == ["primary", "backup"]  # no wasted retries


async def test_when_both_routes_fail_the_message_names_both():
    """Neither error alone is the whole truth. Reporting only the fallback's
    sends someone off to debug an endpoint they were not using; reporting only
    the primary's leaves them fixing it and still getting nothing."""
    provider = RouteRecorder(failing={"primary", "backup"})
    client = _retry_client(provider, retries=0)
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], chain)]
    assert "primary is unavailable" in exc.value.detail
    assert "backup is unavailable" in exc.value.detail
    assert provider.models == ["primary", "backup"]


async def test_the_kind_is_the_primary_connections():
    """The `kind` is what the frontend branches on — a `missing_key` prompt for
    a key, say — so it has to describe the connection the user actually chose."""
    provider = RouteRecorder(failing={"primary", "backup"}, kind="rate_limit")

    async def stream(messages, model="", *args, **kwargs):
        provider.models.append(model)
        raise LLMError("rate_limit" if model == "primary" else "network", f"{model} down")
        yield  # unreachable; it is what makes this an async generator

    provider.stream = stream
    client = _retry_client(provider, retries=0)
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], chain)]
    assert exc.value.kind == "rate_limit"


async def test_a_single_route_failure_is_re_raised_untouched():
    """No fallback, no synthesis: the provider's own exception object reaches
    the caller, message and all."""
    original = LLMError("rate_limit", "slow down")

    class Raiser:
        async def stream(self, messages, *args, **kwargs):
            raise original
            yield  # unreachable; it is what makes this an async generator

    client = _retry_client(Raiser(), retries=0)
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], _route("a", "primary"))]
    assert exc.value is original


async def test_a_retried_route_does_not_report_a_fallback_nobody_configured():
    """The synthesized "and the fallback failed too" message is for the case
    where a SECOND connection was actually tried. A route that simply retried
    raises a different exception object each attempt, which is not the same
    question — and answering it that way told users with no fallback that their
    fallback had failed."""
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=2)
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], _route("a", "primary"))]
    assert "fallback" not in exc.value.detail
    assert provider.models == ["primary", "primary", "primary"]


async def test_the_primarys_retry_after_survives_a_failed_fallback():
    """The window travels with the kind, which is the primary's (#213): it
    reaches the caller as the `Retry-After` of a 429, and the fallback's window
    would name a connection the user is not using."""
    async def stream(messages, model="", *args, **kwargs):
        raise LLMError("rate_limit" if model == "primary" else "network",
                       f"{model} down", 90.0 if model == "primary" else 5.0)
        yield  # unreachable; it is what makes this an async generator

    provider = RouteRecorder(failing={"primary", "backup"})
    provider.stream = stream
    client = _retry_client(provider, retries=0)
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], chain)]
    assert (exc.value.kind, exc.value.retry_after) == ("rate_limit", 90.0)


async def test_no_fallback_configured_leaves_one_route():
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=0)
    with pytest.raises(LLMError):
        [c async for c in client.stream([], _route("a", "primary"))]
    assert provider.models == ["primary"]


async def test_a_fallback_pointing_at_the_active_connection_is_dropped():
    """Otherwise it is a third attempt wearing a different name, and it doubles
    how long the user waits to hear that the provider is down."""
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=0)
    chain = carrying(_route("a", "primary"), _route("a", "primary"))
    with pytest.raises(LLMError):
        [c async for c in client.stream([], chain)]
    assert provider.models == ["primary"]


async def test_the_fallback_is_never_reached_when_the_primary_answers():
    provider = RouteRecorder(failing=set())
    client = _retry_client(provider, retries=2)
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    assert [c async for c in client.stream([], chain)] == ["from primary"]
    assert provider.models == ["primary"]


async def test_a_fallback_is_not_taken_after_text_has_been_sent():
    provider = HalfwayProvider()
    client = _retry_client(provider, retries=2)
    seen = []
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    with pytest.raises(LLMError):
        async for chunk in client.stream([], chain):
            seen.append(chunk)
    assert seen == ["half a sentence"] and provider.attempts == 1


async def test_falling_back_is_logged(caplog):
    """The user is not told which route answered — a stream has no room for it
    — so the operator record is the log line. Deliberately the honest, cheap
    surface; per-response reporting is not solved here."""
    import logging
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=0)
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    with caplog.at_level(logging.WARNING, logger="grimoire.llm"):
        [c async for c in client.stream([], chain)]
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
            elif node.level == 1 or (node.level == 0 and node.module == "grimoire"):  # from . import llm
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


# --- usage accounting (#152) ---
class UsageProvider:
    """A provider that reports accounting the way a real one does: into the
    holder the facade threads down, on the frame after the last delta."""

    def __init__(self, block=None, fail=None):
        self.block = block if block is not None else {"prompt_tokens": 7,
                                                       "completion_tokens": 2}
        self.fail = fail
        self.seen: list[dict | None] = []

    async def stream(self, messages, *args, usage=None, **kwargs):
        self.seen.append(usage)
        yield "hi"
        if self.fail is not None:
            raise self.fail
        if usage is not None:
            usage.update(self.block)


async def test_the_facade_threads_the_usage_holder_down_to_the_provider():
    provider = UsageProvider()
    client = _retry_client(provider, retries=0)
    usage = {}
    assert [c async for c in client.stream([], _conn("openrouter"), usage=usage)] == ["hi"]
    assert usage["prompt_tokens"] == 7
    assert usage["completion_tokens"] == 2


async def test_complete_carries_the_usage_holder_too():
    provider = UsageProvider()
    client = _retry_client(provider, retries=0)
    usage = {}
    assert await client.complete([], _conn("openrouter"), usage=usage) == "hi"
    assert usage["completion_tokens"] == 2


async def test_the_facade_stamps_the_connection_that_actually_answered():
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=0)
    usage = {}
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    assert [c async for c in client.stream([], chain, usage=usage)] == [
        "from backup"]
    assert usage["model"] == "backup", "the ledger must name the route that served"
    assert usage["connection"] == "conn-b"
    assert usage["provider"] == "openrouter"


async def test_a_claude_route_is_stamped_with_the_model_it_will_really_run():
    provider = UsageProvider()
    client = _retry_client(provider, retries=0)
    usage = {}
    [c async for c in client.stream([], _conn("claude", model=""), usage=usage)]
    assert usage["model"] == llm.CLAUDE_DEFAULT_MODEL


async def test_a_provider_reported_model_wins_over_the_configured_one():
    provider = UsageProvider(block={"model": "realm/opus-2026-08"})
    client = _retry_client(provider, retries=0)
    usage = {}
    [c async for c in client.stream([], _conn("openrouter", model="realm/opus"), usage=usage)]
    assert usage["model"] == "realm/opus-2026-08"


async def test_the_row_counts_how_many_attempts_it_took():
    provider = FlakyProvider(failures=2)
    client = _retry_client(provider, retries=2)
    usage = {}
    assert [c async for c in client.stream([], _conn("openrouter"), usage=usage)] == ["ok"]
    assert usage["attempts"] == 3


async def test_an_abandoned_attempts_numbers_do_not_leak_into_the_next():
    """Each attempt starts the holder fresh, so the row describes the call that
    answered rather than a merge of it with one that died."""
    class Once:
        def __init__(self):
            self.n = 0

        async def stream(self, messages, *args, usage=None, **kwargs):
            self.n += 1
            if self.n == 1:
                if usage is not None:
                    usage.update({"prompt_tokens": 900, "cost_usd": 9.0,
                                  "cost_basis": "billed"})
                raise LLMError("network", "dropped after the usage frame")
            yield "ok"
            if usage is not None:
                usage.update({"prompt_tokens": 5})

    client = _retry_client(Once(), retries=1)
    usage = {}
    assert [c async for c in client.stream([], _conn("openrouter"), usage=usage)] == ["ok"]
    assert usage["prompt_tokens"] == 5
    assert "cost_usd" not in usage


async def test_a_call_that_never_succeeds_still_names_its_route():
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=0)
    usage = {}
    with pytest.raises(LLMError):
        [c async for c in client.stream([], _route("a", "primary"), usage=usage)]
    assert usage["model"] == "primary"
    assert usage["attempts"] == 1


async def test_a_client_asked_for_no_accounting_passes_none_down():
    """The overwhelmingly common shape outside routes; a holder allocated per
    call for nobody would be pure overhead."""
    provider = UsageProvider()
    client = _retry_client(provider, retries=0)
    [c async for c in client.stream([], _conn("openrouter"))]
    assert provider.seen == [None]


def _claude_route():
    return {"id": "b", "name": "conn-b", "kind": "claude", "model": "backup"}


async def test_a_multimodal_call_skips_a_fallback_that_cannot_carry_it():
    """An image draft is a message of content PARTS. The route layer refuses a
    PRIMARY whose client would flatten them, with a message the reader can act
    on; the fallback was never checked, so a primary failure sent the same
    parts down the SDK path -- which joins content as a string and raises. The
    reader was then told "and the fallback failed too" about a connection they
    had not chosen, instead of the real error from the one they had."""
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=0)
    parts = [{"role": "user", "content": [{"type": "text", "text": "what is this?"},
                                          {"type": "image_url", "image_url": {"url": "data:x"}}]}]
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream(parts, carrying(_route("a", "primary"), _claude_route()))]

    assert provider.models == ["primary"]              # the fallback was never asked
    assert "fallback failed too" not in exc.value.detail


async def test_an_ordinary_call_still_takes_that_same_fallback():
    """The pruning is about the message, not about the connection: plain text
    is exactly what a Claude fallback is there to serve."""
    provider = RouteRecorder(failing={"primary"})
    client = _retry_client(provider, retries=0)
    text = [{"role": "user", "content": "what is this?"}]
    chain = carrying(_route("a", "primary"), _claude_route())
    assert [c async for c in client.stream(text, chain)] == ["from backup"]
    assert provider.models == ["primary", "backup"]


# ---- sampler presets: split per attempt, fallback inheritance, refusals ----

def _sampled(conn, params, scope="connection", name="Warm"):
    return {**conn, "sampling": {"preset_id": name.lower(), "preset_name": name,
                                 "scope": scope, "params": params}}


async def test_no_preset_passes_no_sampling_kwarg():
    provider = RefusingProvider()
    client = _retry_client(provider)
    [c async for c in client.stream([], _route("a", "primary"))]
    assert "sampling" not in provider.calls[0][1]


async def test_the_applied_split_reaches_the_provider():
    provider = RefusingProvider()
    client = _retry_client(provider)
    conn = _sampled(_route("a", "primary"), {"temperature": 0.7, "min_p": 0.05})
    [c async for c in client.stream([], conn)]
    assert provider.calls[0][1]["sampling"] == {"temperature": 0.7, "min_p": 0.05}


async def test_a_claude_connection_is_sent_no_sampling():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    [c async for c in client.stream([], _sampled(_conn("claude"), {"temperature": 0.3}))]
    assert cl.calls == [(("m",), {"usage": None})]


async def test_a_standard_endpoint_is_sent_only_the_openai_params():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _sampled(_conn("openai_compatible", base_url="http://x"),
                    {"temperature": 0.3, "top_k": 40})
    [c async for c in client.stream([], conn)]
    assert oc.calls[0][1]["sampling"] == {"temperature": 0.3}


async def test_a_route_scoped_preset_follows_the_route_onto_the_fallback():
    provider = RefusingProvider(failing={"primary"})
    client = _retry_client(provider, retries=0)
    conn = _sampled(_route("a", "primary"), {"temperature": 0.2}, scope="global")
    chain = carrying(conn, _sampled(_route("b", "backup"), {"max_tokens": 300}))
    assert [c async for c in client.stream([], chain)] == ["from backup"]
    assert provider.calls[1] == ("backup", {"usage": provider.calls[1][1]["usage"],
                                            "sampling": {"temperature": 0.2}})


async def test_a_route_cleared_preset_clears_the_fallbacks_too():
    """The sentinel keeps a role-play cap off absorb; a 429 must not undo it."""
    provider = RefusingProvider(failing={"primary"})
    client = _retry_client(provider, retries=0)
    conn = _sampled(_route("a", "primary"), {}, scope="campaign")
    chain = carrying(conn, _sampled(_route("b", "backup"), {"max_tokens": 300}))
    [c async for c in client.stream([], chain)]
    assert "sampling" not in provider.calls[1][1]


async def test_a_connection_level_preset_stays_with_its_connection():
    provider = RefusingProvider(failing={"primary"})
    client = _retry_client(provider, retries=0)
    conn = _sampled(_route("a", "primary"), {"temperature": 0.2})
    chain = carrying(conn, _sampled(_route("b", "backup"), {"max_tokens": 300}))
    [c async for c in client.stream([], chain)]
    assert provider.calls[1][1]["sampling"] == {"max_tokens": 300}


@pytest.mark.parametrize("status", [400, 422])
async def test_a_refused_preset_is_not_handed_to_the_fallback(status):
    seen = []
    provider = RefusingProvider(failing={"primary"}, status=status)
    client = LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                       timeout=0, retries=2,
                       observer=lambda conn, err: seen.append((conn["id"], err)))
    conn = _sampled(_route("a", "primary"), {"temperature": 1.25})
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], carrying(conn, _route("b", "backup")))]
    assert [m for m, _ in provider.calls] == ["primary"]
    assert "Warm" in exc.value.detail and "temperature" in exc.value.detail
    assert "fallback connection was not tried" in exc.value.detail
    assert exc.value.kind == "bad_response" and exc.value.status == status
    # Typed, so a reader that must tell a refused setting from a refused
    # request (the model test call) asks the type rather than the prose.
    assert isinstance(exc.value, llm.PresetRefusalError)
    assert seen == []  # a refused setting is not a failing connection


async def test_a_400_with_no_preset_still_falls_back():
    provider = RefusingProvider(failing={"primary"}, status=400)
    client = _retry_client(provider, retries=0)
    chain = carrying(_route("a", "primary"), _route("b", "backup"))
    assert [c async for c in client.stream([], chain)] == ["from backup"]


async def test_a_500_with_a_preset_still_falls_back():
    provider = RefusingProvider(failing={"primary"}, status=500)
    client = _retry_client(provider, retries=0)
    conn = _sampled(_route("a", "primary"), {"temperature": 1.25})
    chain = carrying(conn, _route("b", "backup"))
    assert [c async for c in client.stream([], chain)] == ["from backup"]


async def test_a_400_whose_params_were_all_dropped_still_falls_back():
    """Nothing was sent, so nothing in the preset can be what was refused."""
    provider = RefusingProvider(failing={"primary"}, status=400)
    client = _retry_client(provider, retries=0)
    conn = {**_sampled(_route("a", "primary"), {"min_p": 0.1}), "model_params": ["temperature"]}
    chain = carrying(conn, _route("b", "backup"))
    assert [c async for c in client.stream([], chain)] == ["from backup"]



async def test_a_400_that_names_no_sent_param_still_falls_back():
    """A context overflow is a 400 too; a preset being attached must not turn it
    into a preset refusal that skips the fallback and the health verdict."""
    seen = []
    provider = RefusingProvider(failing={"primary"}, status=400,
                             why="maximum context length is 8192 tokens")
    client = LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                       timeout=0, retries=0,
                       observer=lambda conn, err: seen.append((conn["id"], err)))
    conn = _sampled(_route("a", "primary"), {"temperature": 1.25})
    chain = carrying(conn, _route("b", "backup"))
    assert [c async for c in client.stream([], chain)] == ["from backup"]
    assert seen[0][0] == "a" and seen[0][1] is not None


async def test_a_refusal_spelled_with_hyphens_is_still_recognized():
    provider = RefusingProvider(failing={"primary"}, status=400, why="unknown field: repeat_penalty")
    client = _retry_client(provider, retries=0)
    conn = {**_sampled(_conn("openai_compatible", id="a", model="primary", base_url="http://x"),
                       {"repetition_penalty": 1.1}), "sampler_support": "extended"}
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], carrying(conn, _route("b", "backup")))]
    assert "fallback connection was not tried" in exc.value.detail


async def test_a_fallback_that_refuses_the_preset_reports_both_failures():
    provider = RefusingProvider(failing={"primary", "backup"}, status=400)
    client = _retry_client(provider, retries=0)
    conn = _route("a", "primary")   # the primary sent nothing, so it falls back
    chain = carrying(conn, _sampled(_route("b", "backup"), {"temperature": 2}))
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], chain)]
    assert "and the fallback failed too" in exc.value.detail
    assert "not tried" not in exc.value.detail


@pytest.mark.parametrize("conn,expected", [
    ({"kind": "openrouter"}, False), ({"kind": "openrouter", "prefill": True}, True),
    ({"kind": "claude", "prefill": True}, True), ({"kind": "openrouter", "prefill": "true"}, False)])
def test_prefill_capable_reads_only_the_flag(conn, expected):
    assert llm.prefill_capable(conn) is expected


# ---- `single`: exactly one attempt, for the model test call (Task 9) ----


async def test_single_does_not_retry_a_rate_limit():
    provider = FlakyProvider(failures=1, kind="rate_limit")
    client = _retry_client(provider, retries=3)
    with pytest.raises(LLMError) as exc:
        await client.single([], _conn("openrouter"))
    assert exc.value.kind == "rate_limit"
    assert provider.attempts == 1
    # The control: the same provider and client through `stream` DO retry, so
    # the one attempt above is `single`'s doing, not this setup's.
    provider = FlakyProvider(failures=1, kind="rate_limit")
    client = _retry_client(provider, retries=3)
    assert await client.complete([], _conn("openrouter")) == "ok"
    assert provider.attempts == 2


async def test_single_never_calls_a_configured_fallback():
    provider = RouteRecorder(failing={"primary"}, kind="auth")
    client = _retry_client(provider, retries=2)
    with pytest.raises(LLMError) as exc:
        await client.single([], carrying(_route("a", "primary"), _route("b", "backup")))
    assert provider.models == ["primary"]
    assert "fallback" not in exc.value.detail


async def test_single_sends_no_degrade_sibling():
    """A request whose image the provider refused is not re-sent as text: the
    test is asking whether the model takes the image."""
    from grimoire import content_parts
    provider = SequencedProvider([LLMError("bad_response", "no images", status=400), ["ok"]])
    client = LLMClient(openrouter=provider, timeout=0, retries=2,
                       images=lambda _conn: 4,
                       load_image=lambda _c, _p: "data:image/png;base64,AA")
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "look"}, content_parts.ref("/img/a.png", "a", False)]}]
    with pytest.raises(LLMError):
        await client.single(messages, _route("a", "primary"))
    assert len(provider.requests) == 1
    # The control: the same refusal through `stream` IS re-sent as text.
    provider = SequencedProvider([LLMError("bad_response", "no images", status=400), ["ok"]])
    client = LLMClient(openrouter=provider, timeout=0, retries=0,
                       images=lambda _conn: 4,
                       load_image=lambda _c, _p: "data:image/png;base64,AA")
    assert await client.complete(messages, _route("a", "primary")) == "ok"
    assert len(provider.requests) == 2


async def test_single_counts_an_empty_completion_as_completed():
    provider = FlakyProvider(failures=0, reply=())
    client = _retry_client(provider, retries=2)
    assert await client.single([], _conn("openrouter")) == ""
    assert provider.attempts == 1


async def test_single_stamps_usage_as_stream_does():
    provider = FlakyProvider(failures=0)
    client = _retry_client(provider, retries=2)
    conn = _route("a", "primary")
    usage: dict = {}
    assert await client.single([], conn, usage) == "ok"
    assert {k: usage[k] for k in ("model", "connection", "provider", "attempts")} == {
        "model": "primary", "connection": "conn-a", "provider": "openrouter", "attempts": 1}
    assert usage[llm.ATTEMPTED] is conn


async def test_single_hands_anthropic_its_effective_body():
    """The 64-token cap travels in the connection's sampling, and `single`
    still runs it through `llm_sampling.effective` for the adapter."""
    an = FakeProvider("an")
    client = LLMClient(openrouter=FakeProvider("or"), claude=FakeProvider("cl"),
                       openai_compatible=FakeProvider("oc"), anthropic=an,
                       retries=3)
    conn = _conn("anthropic", model="claude-test-1",
                 sampling={"preset_id": "", "preset_name": "", "scope": "none",
                           "params": {"max_tokens": 64}})
    assert await client.single([], conn) == "an"
    assert an.calls[0][1]["effective"] == {"max_tokens": 64}


async def test_single_raises_a_provider_error_as_stream_does():
    provider = FlakyProvider(failures=1, kind="bad_response")
    client = _retry_client(provider, retries=0)
    with pytest.raises(LLMError) as exc:
        await client.single([], _conn("openrouter"))
    assert (exc.value.kind, exc.value.detail) == ("bad_response", "attempt 1")
