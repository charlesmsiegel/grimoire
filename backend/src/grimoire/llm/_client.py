"""Provider dispatch with instance-owned clients and late-bound caller callbacks."""

from __future__ import annotations

import asyncio
import uuid

from .. import (
    adapters,
    content_parts,
    decisions,
    llm_capture,
    llm_usage,
    model_guidance,
    tool_calls,
    wire,
)
from ..anthropic import AnthropicClient
from ..claude_agent import ClaudeAgentClient
from ..llm_errors import LLMError
from ..openai_compatible import OpenAICompatibleClient
from ..openrouter import OpenRouterClient
from . import _accounting, _retry, _routes
from ._post_images import _PostImages
from ._routes import _Route as _Route


class LLMClient:
    """Dispatches each call to the resolved connection's kind."""

    def __init__(self, *, openrouter=None, claude=None, openai_compatible=None, timeout=None,
                 retries=None, observer=None, capture=None,
                 images=None, load_image=None, anthropic=None, count_tokens=None):
        # Every argument by keyword: there are eleven, all optional, and a
        # positional caller would be one reordering away from a silent swap.
        self._openrouter = openrouter if openrouter is not None else OpenRouterClient()
        self._claude = claude if claude is not None else ClaudeAgentClient()
        self._openai_compatible = (openai_compatible if openai_compatible is not None
                                    else OpenAICompatibleClient())
        self._anthropic = anthropic if anthropic is not None else AnthropicClient()
        #: One adapter per kind (`adapters`), around the clients above, which
        #: stay attributes: they are the test seams.
        self._adapters = adapters.build(
            openrouter=self._openrouter, openai_compatible=self._openai_compatible,
            anthropic=self._anthropic, claude=self._claude)
        # A number, or a callable returning one. Callable is how routes hands
        # over the config.md setting without this module importing the store —
        # the gateway's imports are kept acyclic and store-free on purpose
        # (#239) — and resolving per call is also what lets a Configuration-page
        # change land without a restart.
        self._timeout = timeout
        # Same contract, for the same reason: the retry count is a config.md
        # setting. The fallback (#144) is not the client's: every call carries
        # its own, the resolver's per-call choice (`wire.Chain.fallback`).
        self._retries = retries
        #: Called with `(conn, error_or_None)` as each attempt settles, so the
        #: health registry learns what the provider actually did without
        #: anything having to poll it (#146). A callable rather than the
        #: registry itself, for the third time on this constructor and the same
        #: reason: the registry lives on `app.state` and this module may not
        #: reach into the app any more than it may reach into the store.
        self._observer = observer
        # Resolver returns a sink, or None when capture is off. Like timeout,
        # this keeps runtime configuration in the store and out of the gateway.
        self._capture = capture
        # Post images (#377), the same contract once more: `images(attempt)` is
        # how many a prompt for that attempt may carry right now (0 = none) and
        # `load_image(campaign, part)` turns one reference into a data URI.
        # Both are store lookups, so both arrive as callables (`_PostImages`).
        self._post_images = _PostImages(images, load_image)
        # Once more: `count_tokens(text)` counts what a provider did not report (spec
        # 9.1). It is the store's tokenizer, so it arrives as a callable. None
        # counts nothing -- a hand-built client files exactly what it did.
        self._count_tokens = count_tokens

    def _timeout_seconds(self) -> float:
        if self._timeout is None:
            return _routes.DEFAULT_TIMEOUT
        return float(self._timeout() if callable(self._timeout) else self._timeout)

    def _retry_count(self) -> int:
        """Retries after the first attempt. Never negative, and never an
        exception: a malformed setting must not take generation down with it,
        which is the same posture `store.config` takes on every other knob."""
        if self._retries is None:
            return _routes.DEFAULT_RETRIES
        try:
            return max(0, int(self._retries() if callable(self._retries) else self._retries))
        except (TypeError, ValueError):
            return _routes.DEFAULT_RETRIES

    def _adapter(self, kind: str) -> adapters.Adapter:
        """The adapter that sends a target of `kind`. A kind the registry does
        not know is sent through OpenRouter's, as the facade always sent one."""
        return self._adapters.get(kind) or self._adapters["openrouter"]

    def _native_adapter(self, kind: str) -> adapters.Adapter:
        """The adapter that sends a native decision on `kind`, or the
        `bad_response` a kind with no native endpoint is refused with,
        unsent (`adapters.decides_natively`)."""
        if not adapters.decides_natively(kind):
            raise LLMError("bad_response", f"{kind} connections have no native decisions endpoint")
        return self._adapters[kind]

    def _routes(self, attempt: wire.Chain | wire.Target,
                retries: int | None = None) -> list[_Route]:
        """The attempts one generation may make, in order.

        The primary with its retry budget -- `retries` when the call names
        one (`complete(retries=)`), else the client's -- then the fallback
        the CALL carries (`wire.Chain.fallback`, the resolver's own per-call
        choice) with a single attempt, sent the route's preset when the
        primary's came from a route scope (`fallback_sampling`).

        The fallback is dropped when it is the connection that is already
        primary -- see `_same_route`.
        """
        chain = _routes._chain_of(attempt)
        routes = [_routes._Route(chain.primary,
                         self._retry_count() if retries is None else max(0, retries))]
        fallback = chain.fallback
        if fallback is not None and not _routes._same_route(chain.primary, fallback):
            routes.append(_routes._Route(_routes.fallback_sampling(chain.primary, fallback), 0))
        return routes

    def _usable_routes(self, messages: list[dict], attempt: wire.Chain | wire.Target,
                       retries: int | None = None) -> list[_Route]:
        """`_routes`, minus a FALLBACK that cannot carry these messages.

        An image description is drafted from a multimodal message, and the
        route layer refuses a primary connection whose client would flatten it
        (`store.image_drafts`). The fallback was never checked: with an
        OpenRouter primary and a Claude fallback, a primary failure sent those
        same content parts down the SDK path, which joins content as a string
        and raises -- so the user was shown "and the fallback failed too" about
        a connection they had not chosen for this call, in place of the real
        error from the one they had (PR review).

        The PRIMARY is never dropped here, even when it cannot carry them: the
        route above returns a 409 the reader can act on, and answering "no
        route at all" from this layer would replace that with something worse.
        """
        routes = self._routes(attempt, retries)
        if _routes._carries_parts(messages):
            routes = routes[:1] + [r for r in routes[1:]
                                   if r.target.kind not in _routes.TEXT_ONLY_KINDS]
        # A pure check: no resolver runs while routes are built, so nothing reads
        # a catalog sidecar on the event loop. Whether a sibling is attempted is
        # `_resilient`'s question, asked after the attempt before it.
        return _routes._with_degrades(routes) if _routes._may_send_refs(messages) else routes

    def _dispatch(self, messages: list[dict], route: _Route, usage: dict | None = None,
                  schema: dict | None = None, *, tools: tuple[dict, ...] | None = None,
                  tool_choice: str | None = None):
        # Read before selecting: `for_model` returns a plain list.
        campaign = getattr(messages, "campaign", "")
        target = route.target
        sent = route.sent()
        # Select per ATTEMPT: retries retain the frozen prompt, while a
        # fallback repacks that same context with its own model's guidance.
        # Ordinary message lists (JSON extraction, judges, drafts) stay ordinary.
        if isinstance(messages, model_guidance.PreparedMessages):
            # Per attempt, not `for_model`: a prompt with per-attempt tails
            # ("Keep writing") ends the way THIS attempt can take -- a
            # fallback that cannot continue a prefill is sent the instruction
            # instead. Untailed prompts are `for_model`.
            messages = messages.for_target(sent)
        if usage is not None:
            usage["images"] = 0
        # After `_stamp` cleared the holder, so each attempt counts only itself
        # -- the tool definitions it is sent included (01g).
        llm_usage.note_prompt(usage, messages, tools)
        offered = _routes._offer(tools, tool_choice)
        if not content_parts.needs_lowering(messages):
            return self._generate(messages, target, usage, schema, **offered)
        return self._parts_lowered(messages, route, usage, campaign, schema, **offered)

    def _generate(self, messages: list[dict], target: wire.Target, usage: dict | None,
                  schema: dict | None, *, tools: tuple[dict, ...] | None = None,
                  tool_choice: str | None = None):
        """One attempt's provider stream, through its kind's adapter.

        Structured output is decided per ATTEMPT like the preset (spec 7.2):
        only a target its resolver flagged capable (`structured`) is asked
        for it, and the adapter passes the keyword only when there is
        something to send, so every other call is the call it was before
        slice F. The prompt carries the schema either way; this adds the
        provider's mode. The adapter decides the sampler controls against
        that attempt's own target (`llm_sampling.effective`), so a fallback
        of another kind is held to what ITS backend takes. Tools (01g) reach
        the adapter only when offered."""
        return self._adapter(target.kind).generate(
            messages, target, usage, schema=schema if target.structured else None,
            **_routes._offer(tools, tool_choice))

    async def _parts_lowered(self, messages: list[dict], route: _Route, usage: dict | None,
                             campaign: str, schema: dict | None = None, *,
                             tools: tuple[dict, ...] | None = None,
                             tool_choice: str | None = None):
        """`messages` lowered for `route`, then streamed (#377).

        Lowering resolves the image budget (a catalog sidecar read) and loads
        pictures (a decode, cached), so it runs off the event loop. It runs
        inside the attempt, so its time counts against the first-delta idle
        bound: a pathological stall here is recorded as that connection's
        timeout -- accepted, since a warm picture is a cache hit and a cold one
        is one bounded decode. The provider stream is closed in `finally`, so a
        caller's close or a timeout still reaches httpx exactly as `_guard` and
        `_resilient` intend."""
        lowered, sent = await asyncio.to_thread(self._post_images.lower, messages, route, campaign)
        if usage is not None:
            usage["images"] = sent
        # What was sent, not what was asked: text lowering drops carriers (M10).
        llm_usage.note_prompt(usage, lowered, tools)
        inner = self._generate(lowered, route.target, usage, schema,
                               **_routes._offer(tools, tool_choice))
        try:
            async for chunk in inner:
                yield chunk
        finally:
            aclose = getattr(inner, "aclose", None)
            if aclose is not None:
                await aclose()

    def _sink(self):
        """This call's capture sink, or None when capture is off -- or when
        resolving it raised: failed diagnostic setup must not stop the call."""
        try:
            return self._capture() if self._capture is not None else None
        except Exception:  # noqa: BLE001 - failed diagnostic setup must not stop generation
            return None

    def stream(self, messages: list[dict], chain: wire.Chain | wire.Target,
               usage: dict | None = None, *, schema: dict | None = None,
               tools: tuple[dict, ...] | None = None, tool_choice: str | None = None):
        """Every provider stream leaves the facade idle-bounded — the one place
        the bound is provider-independent (the Claude SDK has no httpx client
        to configure at all) — and retried-then-fallen-back, which for the same
        reason can only be decided here: `_resilient` is what knows whether a
        delta has already reached the caller (#144).

        The dispatch is deliberately deferred into a lambda rather than built
        once: each attempt needs its *own* provider stream, aimed at whichever
        route it is running.

        `usage`, when given, is a plain dict this fills in place with what the
        call cost (#152) -- tokens, money where the provider names it, and which
        route answered. A holder rather than a return value because this IS the
        return value: an async generator has nowhere to put a summary, and the
        numbers arrive on the provider's last frame anyway, after the caller has
        consumed every delta. `store.usage.Meter` owns one and files it.

        `schema`, when given, asks for JSON matching that JSON Schema (spec
        7.2): each target flagged `structured` is sent its provider's
        structured mode, and every other attempt -- a fallback without the
        mode included -- is sent the call it would have been sent anyway. The
        caller puts the schema in the prompt too, so an unflagged attempt can
        still answer it.

        `chain` is the primary and the fallback it may fail over to (a lone
        `Target` is a chain of one). A connection dict is a `TypeError`:
        the facade sends typed targets only.

        `tools` and `tool_choice` (01g) offer neutral tool definitions,
        checked (`tool_calls.check`, a `ValueError` before anything is
        stamped) and sent in each attempt's kind's own spelling; the calls a
        reply makes are read from a `tool_calls.Collector` the caller put in
        `usage`, which `_stamp` empties per attempt. A call is not text: the
        stream yields only prose. An attempt refused its tools is a
        `ToolsRefusalError`, never observed and never re-sent without them.
        Absent, the call is the call it always was.
        """
        return self._streamed(messages, chain, usage, schema, None,
                              tools=tools, tool_choice=tool_choice)

    def _streamed(self, messages: list[dict], chain: wire.Chain | wire.Target, usage: dict | None,
                  schema: dict | None, retries: int | None, *,
                  tools: tuple[dict, ...] | None = None, tool_choice: str | None = None):
        """`stream`'s body, with the primary's retry count `complete` may
        name (`_routes`)."""
        if tools is not None or tool_choice is not None:
            tool_calls.check(tools, tool_choice)
        offered = _routes._offer(tools, tool_choice)
        sink = self._sink()
        if schema is None:
            # No envelope goes out, so no target says it did (01f 3.4).
            chain = _routes._unflagged(chain)
        return _retry._resilient(lambda route, holder: self._dispatch(messages, route, holder, schema,
                                                               **offered),
                          self._usable_routes(messages, chain, retries), self._timeout_seconds(),
                          usage=usage, observer=self._observer, capture=sink,
                          counter=self._count_tokens, offered=tools is not None)

    async def complete(self, messages: list[dict], chain: wire.Chain | wire.Target,
                       usage: dict | None = None, *, schema: dict | None = None,
                       retries: int | None = None, tools: tuple[dict, ...] | None = None,
                       tool_choice: str | None = None) -> str:
        """`stream`, joined. `schema` is `stream`'s; slice F's only caller is
        `decide` (spec 7.2's `generate(schema=)` until slice I).

        `retries`, when given, is the primary route's retry count in place of
        the client's, and nothing else changes: a decide chain's fallback
        STAGE is sent as a call of its own, and gets the one attempt a
        fallback gets (spec 5.4, slice H ruling 12). Absent, the call is
        exactly `stream`'s. `tools` and `tool_choice` are `stream`'s."""
        return "".join([chunk async for chunk in self._streamed(
            messages, chain, usage, schema, retries, tools=tools, tool_choice=tool_choice)])

    async def single(self, messages: list[dict], target: wire.Target,
                     usage: dict | None = None, *, tools: tuple[dict, ...] | None = None,
                     tool_choice: str | None = None) -> str:
        """Exactly one attempt on `target`, joined: the model test call's way in.

        No retry, no fallback route and no degrade sibling -- the route list is
        `target` with zero retries and nothing else, so `_resilient` makes one
        attempt and raises what it raised, exactly as `stream` would have from
        that attempt. A test is a question about ONE model on ONE provider, and
        each further attempt is money spent answering a different question:
        a fallback's success would be recorded against a model that never
        answered, a retry would pay twice for a 429, and a text-only re-send
        would call a model that refused the picture one that read it.

        Everything else is `stream`'s, through the same `_resilient` and
        `_dispatch`: the idle bound, the per-attempt `usage` stamp a
        `store.usage.Meter` files, the incoming-response capture, and the
        adapter's `llm_sampling.effective` -- which is how the probe's reply
        cap reaches the Anthropic API as its required `max_tokens`.

        Not reported to the health observer. A probe's refusal is about the
        model (this one reads no images), not about whether the connection
        serves, and a status dot turned red by a vision probe would send the
        reader to fix a connection that works.

        `tools` and `tool_choice` (01g) offer tool definitions, for the
        `tools` probe: checked (`tool_calls.check`) before anything is
        stamped, and sent in the attempt's kind's spelling. Whether the reply
        called one is read from a `tool_calls.Collector` the caller put in
        `usage` (`_stamp` keeps it). Absent, the call is what it always was.
        """
        if tools is not None or tool_choice is not None:
            tool_calls.check(tools, tool_choice)
        sink = self._sink()
        # Never a schema, so never a flag (01f 3.4).
        sent = _routes._unflagged(_routes._target_of(target))
        assert isinstance(sent, wire.Target)
        offered = _routes._offer(tools, tool_choice)
        agen = _retry._resilient(lambda route, holder: self._dispatch(messages, route, holder,
                                                               **offered),
                          [_routes._Route(sent, 0)],
                          self._timeout_seconds(),
                          usage=usage, capture=sink, counter=self._count_tokens,
                          offered=tools is not None)
        return "".join([chunk async for chunk in agen])

    async def decide_native(self, item: decisions.Item, target: wire.Target,
                            usage: dict | None = None, *,
                            retries: int | None = None) -> decisions.ItemResult:
        """Ask `item` of `target`'s native decisions endpoint (spec 7.4), through
        its kind's adapter: one attempt, retried, and never fallen back -- the
        chain of stages is `inference.decide`'s.

        Refused unsent, before any stamp (so a meter files no row, and still
        records the failure): a kind with no native endpoint
        (`adapters.decides_natively`), and an item the endpoint cannot
        represent (`decisions.native_gap`, ruling 25), as a `bad_response`
        with the code `native_unrepresentable`.

        Retries are `_resilient`'s rule: only `RETRYABLE_KINDS`, never past a
        `Retry-After` over `RETRY_AFTER_CAP`, after the longer of the backoff
        and the provider's window. `retries` overrides the client's count.
        Each attempt is stamped and captured like a generation's; none is
        estimated (no `Estimate`, no `note_prompt`), because a native row's
        billing unit is not a chat prompt, and none is sent sampling, which a
        decision takes none of (spec 8) -- nor stamped with it: the target is
        sent `without_sampling`, so the row files no `preset`. A status in
        `NATIVE_REJECTED_STATUSES` is not reported to the observer.
        """
        route = _routes._native_route(target)
        attempt = route.target
        adapter = self._native_adapter(attempt.kind)
        gap = decisions.native_gap(item)
        if gap:
            raise LLMError("bad_response", gap, code="native_unrepresentable")
        sink = self._sink()
        if usage is None and sink is not None:
            usage = {}  # the adapter receives its recorder through the holder
        call_id = uuid.uuid4().hex if sink is not None else ""
        attempts = 1 + (self._retry_count() if retries is None else max(0, retries))
        last: LLMError | None = None
        for tries in range(1, attempts + 1):
            if last is not None:
                await asyncio.sleep(max(_retry._backoff_delay(tries - 2), last.retry_after or 0.0))
            _accounting._stamp(usage, route, tries)
            if sink is not None and usage is not None:
                usage[llm_capture.KEY] = llm_capture.Capture(
                    sink, call_id, tries, attempt.model, attempt.kind,
                    run_id=llm_capture.run_id(usage))
                llm_capture.emit(usage, "start", None)
            outcome = "interrupted"
            try:
                result = await adapter.decide(item, attempt, usage,
                                              bound=self._timeout_seconds())
                outcome = "complete"
            except LLMError as exc:
                outcome = "error"
                if exc.status not in _routes.NATIVE_REJECTED_STATUSES:
                    _accounting._observe(self._observer, attempt, exc)
                retryable = (exc.kind in _retry.RETRYABLE_KINDS
                             and not (exc.retry_after or 0.0) > _retry.RETRY_AFTER_CAP)
                if not retryable or tries == attempts:
                    raise
                last = exc
                continue
            finally:
                llm_capture.emit(usage, "end", {"status": outcome})
            _accounting._observe(self._observer, attempt, None)
            return result
        raise AssertionError("unreachable: the last attempt returns or raises")

    def note_outcome(self, target: wire.Target, error: LLMError | None) -> None:
        """File an outcome this facade did not itself observe (#146).

        There is exactly one such outcome, and it is the reason this is public.
        A one-shot generation runs under a total-duration ceiling imposed by the
        route (`routes.common._bounded_call`), and an overrun *cancels* the
        stream from outside — so `_resilient` unwinds through `GeneratorExit`
        rather than through its `except LLMError`, and the attempt that
        provoked the ceiling would be the one failure the registry never hears
        about. The reader would then be shown a 504 and a green dot.

        Cancellation cannot be read as a failure from inside `_resilient`,
        which is why it is not: a caller walking away (an SSE client
        disconnecting) unwinds identically and is nobody's fault. Only the
        holder of the ceiling knows which of the two just happened, so only it
        can say.

        Filed for the attempt alone, as it is: a target names no fallback.
        """
        _accounting._observe(self._observer, _routes._target_of(target), error)

    async def list_models(self, target: wire.Target) -> list[dict]:
        """The catalog `target`'s provider offers, normalized (#149).

        On the facade because the answer depends on the connection's kind, and
        dispatching by kind is the one thing this class is. Before #149 the
        catalog was a second seam of its own that only knew how to ask an
        OpenAI-compatible endpoint — so the picker showed a *custom* endpoint
        its own models and showed every other connection OpenRouter's, fetched
        from the browser against a hardcoded URL whichever provider was
        configured.

        Deliberately **not** retried and **not** fallen back. `_resilient`
        exists so a scene survives a blip; a catalog is a question about one
        named connection, and answering it from a different provider's models
        would hand the reader a list of ids their connection cannot run.
        """
        attempt = _routes._target_of(target)
        adapter = self._adapters.get(attempt.kind)
        if adapter is None or not adapter.lists_models:
            # Unreachable through the API — the route refuses these before it
            # gets here, with a message about the kind rather than a transport
            # failure. Kept as a backstop so a future caller that forgets the
            # check gets an error rather than an AttributeError from a provider
            # with no `list_models`.
            raise LLMError("bad_response", f"{attempt.kind} connections have no model catalog")
        return await adapter.models(attempt)

    async def check(self, target: wire.Target) -> None:
        """Ask `target`'s provider whether it can serve. Returns on yes, raises
        the same `LLMError` a generation would on no (#146).

        One vocabulary, not two: the health report a reader sees is the `kind`
        and `detail` of the error their next turn would have failed with, so a
        check that says `auth` and a scene that says `auth` are saying the same
        thing about the same connection.

        Not retried and not fallen back, for a sharper version of
        `list_models`' reason: "is this connection healthy" answered by trying
        a *different* connection is not an answer, and a retry would report a
        rate-limited provider as healthy after waiting out the window the
        reader is asking about.
        """
        attempt = _routes._target_of(target)
        await self._adapter(attempt.kind).check(attempt)

    async def aclose(self) -> None:
        await self._openrouter.aclose()
        await self._openai_compatible.aclose()
        await self._anthropic.aclose()
