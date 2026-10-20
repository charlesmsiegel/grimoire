"""Typed target preparation and shared route vocabulary; no store dependency."""

from __future__ import annotations

import dataclasses
import logging
from typing import NamedTuple

from .. import adapters, content_parts, decisions, llm_sampling, model_guidance, wire

log = logging.getLogger('grimoire.llm')

# Fallback for a client constructed with no timeout of its own. The real value
# comes from config.md via the resolver routes injects (#243); this only covers
# callers that inject nothing.
DEFAULT_TIMEOUT = 120.0
# What the Claude path runs when its connection names no model. The SDK takes
# an alias, so an unconfigured Claude connection generates perfectly happily --
# unlike the other two kinds, whose empty model reaches the provider as an
# empty model. The store builds every target's `model` by the same rule
# (`store.config.DEFAULT_CLAUDE_MODEL`, restated there; a test holds the two
# equal), so a target arrives here already naming the model it runs.
CLAUDE_DEFAULT_MODEL = "opus"
#: Retries *after* the first attempt, for a client constructed with no resolver
#: of its own. The real value comes from config.md via routes, same as the
#: timeout. Two is enough to ride out a blip and few enough that a genuinely
#: down provider still reports quickly.
DEFAULT_RETRIES = 2

#: Where a sampler preset came from, for the scopes that are about the ROUTE
#: rather than the connection. A route-level answer follows the route onto
#: whichever connection serves it, the fallback included (`LLMClient._routes`).
ROUTE_SCOPES = frozenset({"campaign", "global"})


def _label(attempt: wire.Target) -> str:
    """How an attempt's connection is named in a log line and the ledger's
    `connection`: what the user called it, or whatever identifies it at all
    for one that has no name."""
    return attempt.provider_name or attempt.provider_id or attempt.kind or "?"


#: Connection kinds whose client cannot carry OpenAI-style content PARTS: the
#: Claude SDK path joins a message's content into one string, so a multimodal
#: message raises deep inside it. The registry's, derived from each adapter's
#: `carries_images`. `store.image_drafts.SUPPORTED_KINDS` states the same rule
#: positively; the ROUTE layer refuses such a connection as the PRIMARY with a
#: message the user can act on, through the `claude` provider preset's `never`
#: (`store.inference.providers`) at the inference seam;
#: `test_image_description_draft.py` pins the two halves to agree.
TEXT_ONLY_KINDS = adapters.TEXT_ONLY_KINDS

#: Connection kinds whose provider can be asked for a model catalog (#149):
#: the registry's, derived from each adapter's `lists_models`.
#:
#: `claude` is absent, and not as an oversight: that path's models are aliases
#: the SDK resolves at request time, with no endpoint to enumerate them, which
#: is why a Claude provider's model is typed (or picked from a fixed list)
#: rather than read off a live catalog. The route reads this to refuse a
#: catalog request the provider cannot serve *before* making one, so the reader gets "this kind has no catalog" instead
#: of a transport error from a URL that was never going to exist.
LISTABLE_KINDS = adapters.LISTABLE_KINDS

#: Key under which `_stamp` records the attempt the *current* one is running
#: on, in the usage holder the caller already threads down: its `Target`.
#:
#: For the one caller that has to file an outcome this facade cannot see — a
#: route whose ceiling cancels the call (`routes.common._noting`). Without it
#: that route can only name the connection it *started* with, and a generation
#: that failed over to the fallback and then overran would be recorded against
#: the primary, overwriting the primary's real failure with a timeout it did
#: not cause and leaving the fallback's health untouched.
#:
#: Safe to put here because `store.usage.Meter` copies named keys out of the
#: holder rather than dumping it, so this never reaches a ledger row.
ATTEMPTED = "_attempted_conn"


def prefill_capable(target: wire.Target) -> bool:
    """Whether a prompt ending in a partial assistant turn may be sent on `conn`
    as a prefill for the model to continue ("Keep writing", play controls IV).

    The connection's own opt-in and nothing else -- no kind check. Whether a
    trailing assistant message is continued depends on the model behind the
    route, not on the adapter: current Claude models refuse one, and most chat
    templates read it as history and write a fresh reply. So the default is
    the instruction, and the user says where prefill works: the target's
    `prefill`, its model's fact."""
    return target.prefill


def _carries_parts(messages: list[dict]) -> bool:
    """Does any message hold content parts this facade cannot LOWER to text?

    Image references (#377, `content_parts`) are lowered per route -- a route
    that cannot read images is sent their text -- so they are no reason to
    keep a text-only fallback away. An image-description draft's own
    `image_url` parts are."""
    return any(not isinstance(m.get("content", ""), str)
               and not content_parts.lowerable(m["content"]) for m in messages)


#: The HTTP statuses that mean "not this request" -- a provider refusing an
#: image for its format, size or content. `bad_response` alone cannot say this:
#: a 500 maps to it too, and re-sending as text on a server error would drop
#: the pictures from a turn a plain retry would have served. 404 because
#: OpenRouter answers "No endpoints found that support image input" with one
#: when no endpoint it may route to takes images; a 404 for a genuinely missing
#: model costs one extra text attempt that fails the same way.
REJECTED_STATUSES = frozenset({400, 404, 413, 415, 422})

#: The statuses a native decisions request answers when the ENDPOINT refused
#: that model, key or request (spec 7.4, ruling 11): `REJECTED_STATUSES` plus
#: 403. OpenRouter's reference documents 403 as "authenticated but insufficient
#: permissions", which is what a key without access to an alpha endpoint gets
#: while it serves every chat call -- so the connection answered, and is not
#: marked failing. A revoked key answers 401 and stays observed (M6).
NATIVE_REJECTED_STATUSES = REJECTED_STATUSES | {403}


class _Route(NamedTuple):
    """One attempt a generation may make, as the pair a route always was:
    the attempt's target and its retry budget (the primary's; 0 for a
    fallback or a degrade sibling). The target is what every callback, the
    `ATTEMPTED` stamp and a schema refusal are handed."""

    target: wire.Target
    retries: int

    def sent(self) -> wire.Target:
        """`target`, less the degrade marker: what an image budget, a tail
        chooser and a schema refusal are handed."""
        return dataclasses.replace(self.target, degrade=False)


def _degraded(route: _Route) -> _Route:
    """`route`'s degrade sibling (#377): the same attempt, sent its images as
    text, with no retries of its own."""
    return _Route(dataclasses.replace(route.target, degrade=True), 0)


def _with_degrades(routes: list[_Route]) -> list[_Route]:
    """Each route followed by its degrade sibling."""
    return [r for route in routes for r in (route, _degraded(route))]


def _chain_of(attempt: wire.Chain | wire.Target) -> wire.Chain:
    """What a generation is sent, as a chain: a `Chain` as it is, and a
    `Target` as a chain of one. Anything else -- a connection dict
    included -- is a `TypeError`: the facade sends typed targets only."""
    if isinstance(attempt, wire.Chain):
        return attempt
    return wire.Chain(_target_of(attempt))


def _target_of(attempt: wire.Target) -> wire.Target:
    """`attempt`, when it is one attempt's `Target`; a `TypeError` for
    anything else, a connection dict included."""
    if isinstance(attempt, wire.Target):
        return attempt
    raise TypeError(f"the facade sends a wire.Chain or wire.Target, not {type(attempt).__name__}")


def _unflagged(attempt: wire.Chain | wire.Target) -> wire.Chain | wire.Target:
    """`attempt` with every target unflagged for structured mode: what a call
    that sends no schema is sent (01f 3.4), so `wire.Target.structured` means
    "this attempt was sent the envelope" on every path, and
    `_structured_share` reads only an attempt that was. A target already
    unflagged is kept as the same object, so a call that never carried a
    flag sends exactly what it did."""
    if isinstance(attempt, wire.Target):
        return dataclasses.replace(attempt, structured=False) if attempt.structured else attempt
    chain = _chain_of(attempt)
    if not any(t.structured for t in chain.attempts):
        return chain
    primary = _unflagged(chain.primary)
    fallback = None if chain.fallback is None else _unflagged(chain.fallback)
    assert isinstance(primary, wire.Target)
    assert fallback is None or isinstance(fallback, wire.Target)
    return wire.Chain(primary, fallback)


def _may_send_refs(messages: list[dict]) -> bool:
    """Whether any route could be sent an image reference -- asked of every
    variant a prepared prompt holds, not only the primary's: a fallback packed
    for a larger window can keep a picture the primary's packing gave up, and
    without a sibling its refusal of that picture would end the turn."""
    if isinstance(messages, model_guidance.PreparedMessages):
        return messages.any_variant(content_parts.has_refs)
    return content_parts.has_refs(messages)


def _same_route(a: wire.Target, b: wire.Target) -> bool:
    """Whether two attempts would send the same request to the same place.

    Falling back to the connection that just failed is not a fallback: it is a
    third attempt wearing a different name, and it doubles the time a user
    waits to be told the provider is down. Identity first, then the provider
    id (a `Target` holds dicts, so it is never a key): an empty id never
    matches.
    """
    if a is b:
        return True
    return bool(a.provider_id) and a.provider_id == b.provider_id


def effective_model(target: wire.Target) -> str:
    """The model a generation on `target` will actually run on: its `model`,
    which the store built as the one it runs.

    Only the Claude path substitutes anything (`CLAUDE_DEFAULT_MODEL`, for a
    connection that names no model), so this differs from the stored model
    for exactly one kind -- but it is the difference between telling the
    reader "no model" and naming the one about to answer them.
    """
    return target.model


def _offer(tools: tuple[dict, ...] | None, tool_choice: str | None) -> dict:
    """The keywords an offer of tools (01g) travels as: both, only when there
    are tools, so a call without them reaches the adapter as it always did."""
    return {} if tools is None else {"tools": tools, "tool_choice": tool_choice}


#: The highest output cap a call is sent: the sampler parameter's own bound.
#: Above it `llm_sampling` reads the value as invalid and sends none (the
#: Anthropic API its default instead), so the cap would silently not be one;
#: a larger cap is held to it (`clamp_to_max_output`) rather than refused.
MAX_OUTPUT_CAP = int(next(p.high for p in llm_sampling.PARAMS if p.name == "max_tokens"))


def clamp_to_max_output(target: wire.Target, requested: int) -> int:
    """The output cap `target` is sent for a call that asked for `requested`
    (01f 3.9): the caller's cap, held to the model's own maximum output where
    that is known (01i-C1: `wire.Target.limits.max_output`, stated by the
    user or listed by the provider's catalog), and always to `MAX_OUTPUT_CAP`,
    the most a sampler `max_tokens` may carry. An unknown maximum holds
    nothing else back -- the cap applies as asked, and the Anthropic API's catalog
    limit still holds it there (`llm_sampling._anthropic_max_tokens`)."""
    bound = min(requested, MAX_OUTPUT_CAP)
    most = target.limits.max_output.value
    return bound if most is None else min(bound, most)


def fallback_sampling(primary: wire.Target, fallback: wire.Target) -> wire.Target:
    """`fallback` as it will be sent when it serves `primary`'s generation.

    A preset chosen for the ROUTE follows it onto the fallback; only a
    connection-level answer stays with its connection. Otherwise a route set to
    "no preset" -- to keep a role-play preset's token cap off absorb, say --
    would get that cap back the moment the primary rate-limited and a fallback
    carrying it took the call. Public because the prompt capture of a fallback
    attempt (`routes.common._record_prompt`) has to describe the same request.
    """
    if primary.sampling.scope not in ROUTE_SCOPES:
        return fallback
    sampling = primary.sampling
    if sampling.call_cap is None:
        return dataclasses.replace(fallback, sampling=sampling)
    # The call's own cap (01f 3.9) was held to the PRIMARY's maximum output;
    # the fallback is held to its own (01i-C1), so the preset follows with its
    # own `max_tokens` back and the cap is applied again.
    params = {k: v for k, v in sampling.params.items() if k != "max_tokens"}
    if sampling.preset_cap is not None:
        params["max_tokens"] = sampling.preset_cap
    uncapped = dataclasses.replace(fallback, sampling=dataclasses.replace(
        sampling, params=params, call_cap=None, preset_cap=None))
    return uncapped.with_output_cap(
        sampling.call_cap, most=clamp_to_max_output(fallback, sampling.call_cap))


def _native_route(target: wire.Target) -> _Route:
    """The one route a native decision makes: `target` alone and without its
    sampler preset, which a decision is never sent (spec 8) -- so neither the
    wire nor the ledger's `preset` names one."""
    return _Route(_target_of(target).without_sampling(), 0)


def native_body(item: decisions.Item, target: wire.Target) -> dict:
    """The body a native decision on `target` sends for `item`: its kind's
    adapter's `decision_body`, on the model the call runs on. Pure, and holds
    no key or URL, so a capture can record what was asked. A kind with no
    native endpoint is refused with the `bad_response` a call on it gets,
    unsent."""
    return adapters.decision_body(item, _target_of(target))
