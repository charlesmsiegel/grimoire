"""The adapter registry: one adapter per connection `kind`, sending a typed
target (`wire.Target`) through that kind's client.

Each adapter wraps one provider client and is the one place that knows how
its kind spells a call: which arguments its `stream` takes, how it lists its
models, how it is probed, and -- for a kind with a native decisions endpoint
(slice H) -- how one decision is asked of it. The facade (`llm.LLMClient`)
keeps everything that is not per kind: retries, the fallback, the idle bound,
image lowering, prefill tails and the usage stamp.

Each adapter also states what its kind can do, as flags the rest of the
gateway reads instead of keeping kind lists of its own:

- `carries_images`: its client can carry an image content part
  (`TEXT_ONLY_KINDS` is the kinds that cannot);
- `lists_models`: its provider can be asked for a model catalog
  (`LISTABLE_KINDS`);
- `embeds`: it serves embeddings -- held equal to the store's endpoint rule
  (`store.inference.resolve.embed_endpoint`). Embedding itself is not sent
  through here: it stays the caller-owned `EmbeddingsClient` door (slice D),
  because the store may not import the gateway;
- `decides_natively`: its kind has a native decisions endpoint;
- `calls_tools`: its kind can be sent tool definitions (01g) -- held equal
  to the presets' `never` (`store.inference.providers`); every kind can since
  01g-S8 fitted the Claude Agent SDK's own tool model to the loop.

A gateway module: it imports the provider clients and the gateway leaves,
and never the store (#239).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Protocol

from . import decisions, llm_sampling, openai_compatible, openrouter, tool_calls, wire
from .anthropic import AnthropicClient
from .claude_agent import ClaudeAgentClient
from .llm_errors import LLMError
from .openai_compatible import OpenAICompatibleClient
from .openrouter import OpenRouterClient

log = logging.getLogger(__name__)


class Adapter(Protocol):
    """What the facade asks of one kind's adapter."""

    kind: str
    #: Its client can carry an image content part.
    carries_images: bool
    #: Its provider can be asked for a model catalog.
    lists_models: bool
    #: It serves embeddings: `store.inference.resolve.embed_endpoint`'s rule
    #: for the kind, which a test holds equal.
    embeds: bool
    #: Its KIND has a native decisions endpoint (slice H) -- a question about
    #: the kind, which `generate`'s chain never asks. Not
    #: `store.inference.resolve.decides_natively(attempt)`, which shares the
    #: name and answers another question: whether one MODEL is not known
    #: unable to decide natively. A kind this is True for can still serve a
    #: model that cannot (the OpenAI preset's is the only
    #: `openai_compatible` one that may; every other lists `decide_native` in
    #: its `never`), and the resolver says so per attempt.
    decides_natively: bool
    #: Its kind can be sent tool definitions (01g).
    calls_tools: bool

    def generate(self, messages: list[dict], target: wire.Target, usage: dict | None,
                 *, schema: dict | None = None, tools: tuple[dict, ...] | None = None,
                 tool_choice: str | None = None) -> AsyncIterator[str]:
        """The provider stream for one attempt. `schema`, when given, asks for
        the provider's structured mode; the caller passes one only for a
        target flagged `structured`. `tools` and `tool_choice` (01g), when
        given, are sent in the kind's own spelling; absent, nothing changes."""
        ...

    async def models(self, target: wire.Target) -> list[dict]:
        """The provider's model catalog, normalized. Raises `LLMError`
        (`bad_response`) on a kind that `lists_models` is False for."""
        ...

    async def check(self, target: wire.Target) -> None:
        """Returns when the provider can serve; raises the `LLMError` a
        generation would on no."""
        ...

    async def decide(self, item: decisions.Item, target: wire.Target, usage: dict | None,
                     *, bound: float | None = None) -> decisions.ItemResult:
        """One item asked of the kind's native decisions endpoint, one POST
        (the facade retries). `bound` is the read timeout in seconds. Raises
        `LLMError` (`bad_response`) on a kind that `decides_natively` is
        False for."""
        ...

    @staticmethod
    def decision_body(item: decisions.Item, target: wire.Target) -> dict:
        """The body `decide` sends for `item`: pure -- it reads no client --
        and holding no key or URL, so a capture can record what was asked.
        Raises as `decide` does."""
        ...


def _no_catalog(kind: str) -> LLMError:
    return LLMError("bad_response", f"{kind} connections have no model catalog")


def _no_native(kind: str) -> LLMError:
    return LLMError("bad_response", f"{kind} connections have no native decisions endpoint")


def _offered(tools: tuple[dict, ...] | None, tool_choice: str | None) -> dict[str, Any]:
    """The client keywords for an offer of tools: both, only when there are
    tools, so a call without them is the call it always was."""
    if tools is None:
        return {}
    return {"tools": tools, "tool_choice": tool_choice}


def _own_state(messages: list[dict], target: wire.Target) -> list[dict]:
    """`messages` with any opaque provider state another provider or model
    wrote dropped (`tool_calls.provenanced`, 01g spec 3.11 rule 3): signed
    thinking and reasoning details go back only to the attempt's own
    `(kind, provider_id, model)`. The same list when none rides on it."""
    return tool_calls.provenanced(messages, target.kind, target.provider_id, target.model)


def _controls(target: wire.Target) -> tuple[dict, dict, dict]:
    """`(effective, applied, reasoning)` for one attempt: the controls
    `llm_sampling.effective` decides, the sampler half `split` sends, and the
    reasoning half -- decided per ATTEMPT against that attempt's own target,
    so a fallback of another kind is held to what its backend takes. What is
    dropped is logged at debug."""
    controls = llm_sampling.effective(target)
    applied, dropped = llm_sampling.split(target)
    if dropped:
        log.debug("sampler preset on %r: not sent %s", target.label,
                  ", ".join(f"{d['param']} ({d['reason']})" for d in dropped))
    return controls, applied, llm_sampling.reasoning_wire(controls)


class OpenRouterAdapter:
    """OpenRouter: chat completions with sampling and reasoning as body
    fields, a model catalog, and a native decisions endpoint (slice H)."""

    kind = "openrouter"
    carries_images = True
    lists_models = True
    embeds = True
    decides_natively = True
    calls_tools = True

    def __init__(self, client: OpenRouterClient) -> None:
        self._client = client

    def generate(self, messages: list[dict], target: wire.Target, usage: dict | None,
                 *, schema: dict | None = None, tools: tuple[dict, ...] | None = None,
                 tool_choice: str | None = None) -> AsyncIterator[str]:
        _, applied, reasoning = _controls(target)
        sampling = {**applied, **reasoning}
        # Each keyword only when there is something to send, so a call with no
        # preset, no schema and no tools is byte-for-byte the call it always was.
        extra: dict[str, Any] = _offered(tools, tool_choice)
        if sampling:
            extra["sampling"] = sampling
        if schema is not None:
            extra["schema"] = schema
        return self._client.stream(_own_state(messages, target), target.model, target.api_key,
                                   usage=usage, **extra)

    async def models(self, target: wire.Target) -> list[dict]:
        return await self._client.list_models(target.api_key)

    async def check(self, target: wire.Target) -> None:
        await self._client.probe(target.api_key)

    async def decide(self, item: decisions.Item, target: wire.Target, usage: dict | None,
                     *, bound: float | None = None) -> decisions.ItemResult:
        return await self._client.decide(item, target.model, target.api_key,
                                         usage=usage, bound=bound)

    @staticmethod
    def decision_body(item: decisions.Item, target: wire.Target) -> dict:
        return openrouter.decision_body(item, target.model)


class OpenAICompatibleAdapter:
    """Any OpenAI-compatible endpoint at the connection's own URL: strict
    post-processing on request, the reasoning effort as the client's own
    keyword (as the GLM setting always has travelled), and -- only the OpenAI
    API's preset ever resolves one native -- a decisions endpoint."""

    kind = "openai_compatible"
    carries_images = True
    lists_models = True
    embeds = True
    decides_natively = True
    calls_tools = True

    def __init__(self, client: OpenAICompatibleClient) -> None:
        self._client = client

    def generate(self, messages: list[dict], target: wire.Target, usage: dict | None,
                 *, schema: dict | None = None, tools: tuple[dict, ...] | None = None,
                 tool_choice: str | None = None) -> AsyncIterator[str]:
        _, applied, reasoning = _controls(target)
        effort = reasoning.get("reasoning_effort", "")
        extra: dict[str, Any] = {}
        if effort:
            extra["reasoning_effort"] = effort
        if applied:
            extra["sampling"] = applied
        if schema is not None:
            extra["schema"] = schema
        extra.update(_offered(tools, tool_choice))
        return self._client.stream(
            _own_state(messages, target), target.model, target.api_key, target.base_url,
            strict=target.post_process == "strict", usage=usage, **extra)

    async def models(self, target: wire.Target) -> list[dict]:
        return await self._client.list_models(target.base_url, target.api_key)

    async def check(self, target: wire.Target) -> None:
        await self._client.probe(target.base_url, target.api_key)

    async def decide(self, item: decisions.Item, target: wire.Target, usage: dict | None,
                     *, bound: float | None = None) -> decisions.ItemResult:
        # The endpoint's URL is the connection's, not the adapter's.
        return await self._client.decide(item, target.model, target.api_key,
                                         usage=usage, bound=bound, base_url=target.base_url)

    @staticmethod
    def decision_body(item: decisions.Item, target: wire.Target) -> dict:
        return openai_compatible.decision_body(item, target.model)


class AnthropicAdapter:
    """The Anthropic Messages API: the reasoning control's whole body share
    (`max_tokens` is always in it -- the API requires one -- and `thinking` /
    `output_config` are the reasoning control's), and a model catalog. No
    native decisions endpoint and no embeddings."""

    kind = "anthropic"
    carries_images = True
    lists_models = True
    embeds = False
    decides_natively = False
    calls_tools = True

    def __init__(self, client: AnthropicClient) -> None:
        self._client = client

    def generate(self, messages: list[dict], target: wire.Target, usage: dict | None,
                 *, schema: dict | None = None, tools: tuple[dict, ...] | None = None,
                 tool_choice: str | None = None) -> AsyncIterator[str]:
        controls, _applied, _reasoning = _controls(target)
        extra: dict[str, Any] = _offered(tools, tool_choice)
        if schema is not None:
            extra["schema"] = schema
        return self._client.stream(
            _own_state(messages, target), target.model, target.api_key, usage=usage,
            base_url=target.base_url, effective=controls["effective"], **extra)

    async def models(self, target: wire.Target) -> list[dict]:
        return await self._client.list_models(target.api_key, target.base_url)

    async def check(self, target: wire.Target) -> None:
        await self._client.probe(target.api_key, target.base_url)

    async def decide(self, item: decisions.Item, target: wire.Target, usage: dict | None,
                     *, bound: float | None = None) -> decisions.ItemResult:
        raise _no_native(self.kind)

    @staticmethod
    def decision_body(item: decisions.Item, target: wire.Target) -> dict:
        raise _no_native("anthropic")


class ClaudeAgentAdapter:
    """The Claude Agent SDK: the model only (an alias the SDK resolves; an
    unset one is the target's effective model), no sampling, no structured
    mode, no image parts -- it joins a message's content into one string --
    no catalog and no native decisions."""

    kind = "claude"
    carries_images = False
    lists_models = False
    embeds = False
    decides_natively = False
    #: 01g-S8: tools are declared through an in-process MCP server and every
    #: call is deferred back to the loop (`claude_agent`), never executed.
    calls_tools = True

    def __init__(self, client: ClaudeAgentClient) -> None:
        self._client = client

    def generate(self, messages: list[dict], target: wire.Target, usage: dict | None,
                 *, schema: dict | None = None, tools: tuple[dict, ...] | None = None,
                 tool_choice: str | None = None) -> AsyncIterator[str]:
        # Never structured: the SDK path has no structured mode to ask for.
        _controls(target)
        if tools is not None:
            return self._client.stream(messages, target.model, usage=usage, tools=tools,
                                       tool_choice=tool_choice)
        return self._client.stream(messages, target.model, usage=usage)

    async def models(self, target: wire.Target) -> list[dict]:
        # Its models are aliases the SDK resolves at request time, with no
        # endpoint to enumerate them.
        raise _no_catalog(self.kind)

    async def check(self, target: wire.Target) -> None:
        await self._client.probe(target.model)

    async def decide(self, item: decisions.Item, target: wire.Target, usage: dict | None,
                     *, bound: float | None = None) -> decisions.ItemResult:
        raise _no_native(self.kind)

    @staticmethod
    def decision_body(item: decisions.Item, target: wire.Target) -> dict:
        raise _no_native("claude")


#: Every connection kind, in the order the registry is built.
KINDS: tuple[str, ...] = ("openrouter", "openai_compatible", "anthropic", "claude")

_CLASSES: dict[str, Any] = {cls.kind: cls for cls in (
    OpenRouterAdapter, OpenAICompatibleAdapter, AnthropicAdapter, ClaudeAgentAdapter)}

#: Kinds whose client cannot carry OpenAI-style content PARTS: the Claude SDK
#: path joins a message's content into one string, so a multimodal message
#: raises deep inside it.
TEXT_ONLY_KINDS: frozenset[str] = frozenset(k for k in KINDS if not _CLASSES[k].carries_images)

#: Kinds whose provider can be asked for a model catalog (#149).
LISTABLE_KINDS: frozenset[str] = frozenset(k for k in KINDS if _CLASSES[k].lists_models)


def build(*, openrouter: OpenRouterClient, openai_compatible: OpenAICompatibleClient,
          anthropic: AnthropicClient, claude: ClaudeAgentClient) -> dict[str, Adapter]:
    """The registry: each kind's adapter around the client given for it."""
    clients = {"openrouter": openrouter, "openai_compatible": openai_compatible,
               "anthropic": anthropic, "claude": claude}
    return {kind: _CLASSES[kind](clients[kind]) for kind in KINDS}


def decides_natively(kind: str) -> bool:
    """Whether connections of `kind` have a native decisions endpoint: the
    registry's `decides_natively` flag, False for a kind it does not know.
    The KIND's answer -- whether one model may is
    `store.inference.resolve.decides_natively(attempt)`."""
    adapter = _CLASSES.get(kind)
    return adapter is not None and bool(adapter.decides_natively)


def calls_tools(kind: str) -> bool:
    """Whether connections of `kind` can be sent tool definitions: the
    registry's `calls_tools` flag, False for a kind it does not know."""
    adapter = _CLASSES.get(kind)
    return adapter is not None and bool(adapter.calls_tools)


def decision_body(item: decisions.Item, target: wire.Target) -> dict:
    """The body a native decision on `target` sends for `item`: its kind's
    `decision_body`, which needs no client, so a capture can build it. A kind
    with no native endpoint -- or one the registry does not know -- is
    refused, unsent, as `decide` refuses it."""
    if not decides_natively(target.kind):
        raise _no_native(target.kind)
    return _CLASSES[target.kind].decision_body(item, target)
