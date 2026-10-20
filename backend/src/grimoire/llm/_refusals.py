"""Classify request-feature refusals and preserve composed route failures."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .. import llm_errors, llm_sampling, tool_calls, wire
from ..llm_errors import LLMError

#: HTTP statuses that, answering a request carrying sampler parameters, are read
#: as the PRESET being refused rather than the connection failing. 400 is what
#: OpenRouter and the OpenAI API send for a parameter they reject; 422 is what
#: FastAPI-based servers (vLLM among them) send for a field failing validation.
#: Such an attempt is not handed to the fallback -- see `_resilient`.
PRESET_REFUSAL_STATUSES = frozenset({400, 422})


class PresetRefusalError(LLMError):
    """An `LLMError` that is a sampler parameter THIS request sent being
    refused, rather than the request itself (`_preset_refusal`).

    A subclass rather than a reworded detail alone, so a reader that has to
    tell the two apart asks the type instead of matching prose: the model test
    call (`routes.config`) sends its own reply cap, and a provider refusing
    that cap has said nothing about whether the model can do what was asked.
    Every `except LLMError` still catches it, with the same kind and status."""


class CapRefusalError(PresetRefusalError):
    """A `PresetRefusalError` whose refused parameter is the output cap THIS
    CALL added (`wire.Target.with_output_cap`, `Sampling.call_cap`; 01f 3.9),
    not one the user's preset carried: worded as the call's, because there is
    no preset of the user's to blame or fix. Same kind, status and code, and
    the fallback is still not tried -- it would be sent the same cap -- and
    nothing re-sends the call without it: a caller under a ceiling cannot use
    an uncapped call. An endpoint that refuses `max_tokens` is the user's to
    fix with a provider setting (`sampler_support`)."""


#: Keys that select a variant rather than carry a setting: a refusal naming one
#: alone (a content block's `type`) is not about the control that sent it, and
#: one that IS names its parent (`thinking.type`, `thinking`) anyway.
_DISCRIMINATORS = frozenset({"type"})


def _wire_spellings(share: dict, prefix: str = "") -> set[str]:
    """Every name a provider could echo for the fields in `share`: each key, the
    dotted path of each nested key (`output_config.effort`) and the nested key
    itself (`effort`), lowercased -- a discriminator only as part of a path."""
    names: set[str] = set()
    for key, value in share.items():
        if not isinstance(key, str):
            continue
        path = f"{prefix}{key}".lower()
        names.add(path)
        if prefix and key not in _DISCRIMINATORS:
            names.add(key.lower())
        if isinstance(value, dict):
            names |= _wire_spellings(value, f"{path}.")
    return names


def _structured_share(target: wire.Target) -> dict:
    """The structured-output envelope this attempt puts on the wire, as a share
    `_wire_spellings` reads: `{}` unless the attempt is flagged
    (`wire.Target.structured`), and `{}` for `claude`, which never takes it.

    The schema itself is emptied: its property names are question ids, and a
    question id that happened to spell a sampler (`temperature`) would be
    subtracted from that sampler's spellings, so a refusal of the sampler
    would stop reading as the preset refused. Only the adapters' own envelope
    keys are named. Read from the flag rather than from whether a schema was
    sent, which is the same question by construction: a call that sends no
    schema sends every target unflagged (`_unflagged`, in `_streamed` and
    `single`; 01f 3.4), so a flagged target is one that was sent the
    envelope, whichever operation flagged it."""
    if not target.structured:
        return {}
    kind = target.kind
    if kind == "claude":
        return {}
    if kind == "anthropic":
        return {"output_config": {"format": {"type": None, "schema": None}}}
    # `openrouter` and `openai_compatible` -- and whatever else `_dispatch`
    # sends through the OpenRouter adapter.
    return {"response_format": {"type": None, "json_schema": None}}


def _preset_refusal(exc: LLMError, target: wire.Target) -> PresetRefusalError | None:
    """The error to raise in place of `exc` when it is a preset being refused.

    None when it is not: `exc` carries no refusal status, or this attempt sent
    no sampler parameters at all, in which case a 400 is the connection's own
    business and the ordinary fallback rule applies.

    Why this is not handed to the fallback: `_resilient` moves to the next route
    on any failure, which is right for a connection that cannot serve and wrong
    for a setting it refused. Every turn would quietly run on the fallback, the
    primary's health dot would go red over a temperature, and nothing would
    ever say the preset was the problem.
    """
    if exc.status not in PRESET_REFUSAL_STATUSES or llm_errors.account_limit(exc):
        # A spend limit's 400 is the account refused, whatever its prose says.
        return None
    shares = llm_sampling.sent_fields(target)
    sent = list(shares)
    # Only when the provider's message NAMES something that was sent. A 400 is
    # also what a context-length overflow or an unknown model id gets, and with
    # a preset attached those must still reach the fallback and the health
    # verdict exactly as they did before presets existed. Matched on every
    # spelling a provider might echo back: the canonical name and its
    # hyphen/space forms prose uses, and every field the control actually put
    # on the wire (`_wire_spellings`) -- `max_completion_tokens`,
    # `stop_sequences`, `repeat_penalty` beside `repetition_penalty`, and the
    # reasoning control's whole share: adaptive thinking is sent as `thinking`
    # AND `output_config.effort`, and the Anthropic API's refusal of the effort
    # names only the second.
    detail = (exc.detail or "").lower()
    # Less the structured envelope's own spellings (I3): adaptive effort and a
    # schema share the `output_config` parent on the Anthropic API, and a
    # refusal of `output_config.format` is the SCHEMA refused -- which leaves
    # the fallback, sent the same prompt without the mode, to be tried. A
    # refusal naming `output_config.effort` (or `effort`) still matches.
    # Known limit: only the envelope's own names are subtracted, so a
    # structured refusal whose message ALSO names a sent sampler ("response_format
    # is not supported with reasoning") still reads as a preset refusal.
    envelope = _wire_spellings(_structured_share(target))
    spellings = {name: {name, name.replace("_", "-"), name.replace("_", " "),
                        *_wire_spellings(shares[name])} - envelope
                 for name in sent}
    # llama.cpp's spelling, whichever one this endpoint was sent.
    spellings.get("repetition_penalty", set()).add("repeat_penalty")
    named = [n for n, forms in spellings.items() if any(form in detail for form in forms)]
    if not named:
        return None
    if named == ["max_tokens"] and _call_capped(target):
        return CapRefusalError(
            exc.kind,
            f"{exc.detail} — this call's output cap (max_tokens) was refused, so the "
            "fallback connection was not tried",
            exc.retry_after, status=exc.status, code=exc.code)
    name = target.sampling.preset_name or target.sampling.preset_id or "?"
    return PresetRefusalError(
        exc.kind,
        f"{exc.detail} — this request carried sampler preset “{name}” "
        f"({', '.join(sent)}) and the provider's refusal names one of them, so "
        "the fallback connection was not tried",
        exc.retry_after, status=exc.status, code=exc.code)


def _call_capped(target: wire.Target) -> bool:
    """Whether a refused `max_tokens` is the cap the CALL added rather than
    its preset's: the call capped its output (`Sampling.call_cap`) and the
    preset itself sent no `max_tokens` (`Sampling.preset_cap`; 01f 3.9). A
    preset that carried one keeps the preset's wording -- whatever the call
    or the model's maximum held it down to, the user's preset sent the
    field, and it is the user's to fix."""
    return target.sampling.call_cap is not None and target.sampling.preset_cap is None


class SchemaRefusalError(LLMError):
    """A call that failed on every route, at least one of which failed because
    the structured-output field THAT attempt was sent was refused
    (`_schema_refusal`).

    A subclass so `inference.decide` and `inference.generate` can ask the
    type: each re-sends each refusing attempt once without the mode (spec
    M-4, ruling 3; `inference._without_refused_mode`, 01f-C2). The schema
    always rides the prompt, so the reply still parses -- and the call that
    worked as a prompt-only call before slice F still works after it, whether
    the refusal came from the primary or its fallback, and whatever the other
    route then did. Every `except LLMError` still catches it.

    `words` is what each route the call ran failed with -- the primary, then
    the fallback when there was one -- and `attempts[i]` is route i's target
    as it was sent, when its failure was the refusal, else None. The error
    itself is `routes_failed(words)`: a lone route's own failure (with its
    kind and status), or the two composed."""

    def __init__(self, kind: str, detail: str = "", retry_after: float | None = None,
                 status: int | None = None, code: str | None = None, *,
                 attempts: tuple[wire.Target | None, ...] = (),
                 words: tuple[LLMError, ...] = ()):
        super().__init__(kind, detail, retry_after, status=status, code=code, words=words)
        self.attempts = attempts


class ToolsRefusalError(LLMError):
    """A call that offered tools (01g) and was refused them: a 400 or 422
    whose message names `tools`, `tool_choice` or `functions`, or an adapter
    whose kind cannot send them (`_tools_refusal`). Always `bad_response`
    with the code `tools_refused`.

    Unlike a schema, tools cannot be dropped and the call re-sent -- a loop
    without its tools is not the call that was asked for -- so nothing
    re-sends one. Every `except LLMError` still catches it."""

    def __init__(self, detail: str = "", *, status: int | None = None,
                 words: tuple[LLMError, ...] = ()):
        super().__init__("bad_response", detail, status=status, code=tool_calls.REFUSED,
                         words=words)


def routes_failed(words: Sequence[LLMError]) -> LLMError:
    """The one error a call raises when every route it ran failed: a lone
    route's own failure, or -- the primary and its fallback both failed -- the
    two composed.

    Composed, neither error alone is the whole truth. The kind stays the
    PRIMARY's, because that is the connection the user chose and the one the
    frontend branches on -- reporting a refused connection to a local fallback
    would send someone off to debug an endpoint they were not using while their
    real problem was a rate limit. But dropping the fallback's failure is just
    as misleading: it leaves them fixing the primary and still getting nothing.
    The window comes from the primary for the same reason the kind does. It is
    what reaches the caller as the `Retry-After` of a 429 (#213), and a
    fallback's window would say when a connection they are not using will be
    ready.

    Except when the primary's failure is its structured field refused
    (`SchemaRefusalError`): that is a mode the call can drop, not the
    connection's word, so the fallback's failure -- a rate limit, and the
    window it named -- is the one that says what actually stopped the call.
    Tools refused (`ToolsRefusalError`, 01g) read the same way: the feature
    was refused, the connection answered. When EVERY route refused tools the
    composed error is itself a `ToolsRefusalError`.

    Composed, it keeps the routes' own failures as `words`, so a fact the one
    sentence cannot carry -- a caller's clock having stopped one of them --
    is still there to be asked."""
    if len(words) == 1:
        return words[0]
    primary, fallback = words[0], words[-1]
    refusals = (SchemaRefusalError, ToolsRefusalError)
    word = (fallback if isinstance(primary, refusals)
            and not isinstance(fallback, refusals) else primary)
    detail = f"{primary.detail} — and the fallback failed too: {fallback.detail}"
    if all(isinstance(w, ToolsRefusalError) for w in words):
        return ToolsRefusalError(detail, words=tuple(words))
    return LLMError(word.kind, detail, word.retry_after, words=tuple(words))


def _said(exc: LLMError) -> str:
    """`exc.detail`, followed by what the upstream endpoint said when an
    aggregator relayed its error under a message of its own (an `upstream`
    attribute, `openrouter.OpenRouterError`'s)."""
    upstream = getattr(exc, "upstream", "")
    detail = exc.detail or ""
    return f"{detail}: {upstream}" if isinstance(upstream, str) and upstream else detail


def _schema_refusal(exc: LLMError, target: wire.Target) -> bool:
    """Whether `exc` is the structured envelope `target` was sent being refused:
    an attempt flagged for the mode, a refusal status, and a message that
    names one of the envelope's own spellings and nothing else that was sent.

    Only the envelope's specific spellings count (a dotted path or an
    underscored key -- `response_format`, `output_config.format`), never its
    bare words: "format" and "schema" are in plenty of 400s that are about
    something else. A message that also names a sent sampler is that
    sampler's business (`_preset_refusal` reads it first on a primary)."""
    envelope = _wire_spellings(_structured_share(target))
    if (not envelope or exc.status not in PRESET_REFUSAL_STATUSES
            or llm_errors.account_limit(exc)):
        return False
    # With what the upstream said, when an aggregator relayed its refusal
    # under a wrapper message of its own (`openrouter.OpenRouterError`): the
    # wrapper names nothing, and the endpoint's own sentence names the field.
    detail = _said(exc).lower()
    if not any(form in detail for form in envelope if "." in form or "_" in form):
        return False
    shares = llm_sampling.sent_fields(target)
    others = {form for name in shares
              for form in (name, name.replace("_", "-"), name.replace("_", " "),
                           *_wire_spellings(shares[name]))} - envelope
    return not any(form in detail for form in others)


#: How a refusal of the tools field is worded: the field names as whole
#: words, OpenAI's and its prose spelling of the choice, the legacy
#: `functions` field, and the prose "tool use" (OpenRouter's "no endpoints
#: found that support tool use") -- never `tool_use`, a block type a 400 about
#: the history's shape names.
_TOOLS_NAMED = re.compile(r"\b(?:tools|tool[_ ]choice|tool use|functions)\b")
#: How a context overflow is worded -- OpenAI's counts the `functions` it
#: measured, and Anthropic's can name the tools -- which is the request too
#: long, never tools refused.
_CONTEXT_OVERFLOW = re.compile(r"context[_ ]length|maximum context|context window"
                               r"|prompt is too long|reduce the length")


def _tools_refusal(exc: LLMError, offered: bool) -> bool:
    """Whether `exc` is an attempt's offer of tools (01g) being refused: an
    error an adapter raised with the code `tool_calls.REFUSED` (a kind that
    cannot send them), or -- for an attempt that WAS sent tools -- a refusal
    status, not an account limit, whose message names `tools`, `tool_choice`,
    `functions` or "tool use" as words (`tool_call_id`, `tool_calls` and
    `tool_use` are not: a 400 about the history's shape is a malformed
    request, not tools refused), and is not a context overflow
    (`_CONTEXT_OVERFLOW`, `context_length_exceeded`) that counts them.

    Modelled on `_schema_refusal`, but the decision is not the target's:
    whether this call offered tools is not on a `wire.Target`."""
    if exc.code == tool_calls.REFUSED:
        return True
    if (not offered or exc.status not in PRESET_REFUSAL_STATUSES
            or llm_errors.account_limit(exc) or exc.code == "context_length_exceeded"):
        return False
    said = _said(exc).lower()
    return bool(_TOOLS_NAMED.search(said)) and not _CONTEXT_OVERFLOW.search(said)
