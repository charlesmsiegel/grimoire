from grimoire.llm_errors import LLMError
from grimoire.openrouter import OpenRouterError


def test_openrouter_error_is_llm_error():
    err = OpenRouterError("auth", "bad key")
    assert isinstance(err, LLMError)
    assert err.kind == "auth"
    assert err.detail == "bad key"


def test_llm_error_detail_defaults_to_kind():
    assert LLMError("network").detail == "network"


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
