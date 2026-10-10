"""A model's size as a resolved fact (01i), and the prompt ceiling a
resolution allows.

`of` resolves a model's window and output cap (`wire.Limits`), each from the
first source that states it: the user's word about the model on this
provider (its facts, `context_window` / `max_output`), then the provider's own
catalog row as cached for the connection's current rev (`context`,
`max_output`), then unknown. The user's word outranks the listing for the
reason it does for a capability (`capabilities`): it is a statement about
this model on this provider -- "this llama.cpp server was started with `-c
8192`" -- which the user is placed to know and no catalog field can tell. A
smaller prompt BY CHOICE is `context_budget`'s job, not this.

The resolver calls `of` with the row and facts it already read for an attempt
(`resolve._typed`), so the turn path gains no read, and the result rides the
attempt's target (`Attempt.limits`).

`prompt_ceiling` is the stated way a consumer turns a resolution into the
token count a prompt may hold (spec section 5): the smallest window, less the
reply it reserves, over the attempts the facade may send the same messages
to. A consumer must not compute `window - max_output` itself, take its own
minimum over `attempts`, or read the catalog or the facts: each of those
disagrees with the facade about which targets a prompt reaches and how much
reply to hold back. `tokens is None` means "use your own cap"; `0` means
nothing more fits.

Pure, and reads nothing. Imports only `wire` and `resolved`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ... import wire
from . import resolved

#: What a reply is held back when nothing says how long it may be: no per-call
#: cap and no `max_tokens` sent. A shared window counts the reply, and an unset
#: `max_tokens` says nothing of its length (nor of reasoning tokens, which
#: count against it where they exist), so something must be reserved; a
#: quarter of the window caps it, so a small local window stays usable.
#: Both are argued structurally, as placeholders to be tuned against real
#: prompts later -- never against a measured library.
DEFAULT_REPLY_RESERVE = 4096

#: A stated limit no int32 field on any wire could carry is a typo, not a model.
_CEILING = 2**31


def _stated(value: object) -> int | None:
    """A stated limit as the facts module writes one: a positive int (never a
    bool) below `2**31`, else None. `facts.stated_limit`'s rule, restated so
    this module imports nothing of the store."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value < _CEILING:
        return value
    return None


def _listed(value: object) -> int | None:
    """A catalog value: a positive whole number (`catalog._whole`'s rule, so a
    `16000.0` a sidecar holds still counts), below `2**31`, else None."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _stated(value)


def _limit(model_facts: Mapping, row: Mapping, stated: str, listed: str) -> wire.Limit:
    value = _stated(model_facts.get(stated))
    if value is not None:
        return wire.Limit(value, "user")
    value = _listed(row.get(listed))
    if value is not None:
        return wire.Limit(value, "catalog")
    return wire.UNKNOWN_LIMIT


def of(row: object, model_facts: object) -> wire.Limits:
    """The model's window and output cap, each with its source: the user's
    word in `model_facts` (`facts.of`'s shape: `context_window`, `max_output`),
    then the catalog `row` (`catalog.entry`'s shape: `context`, `max_output`,
    or None for none read), then unknown. Never raises, and never 0: a
    malformed value -- in either, or either not a mapping -- contributes
    nothing."""
    facts_view = model_facts if isinstance(model_facts, Mapping) else {}
    listing = row if isinstance(row, Mapping) else {}
    return wire.Limits(window=_limit(facts_view, listing, "context_window", "context"),
                       max_output=_limit(facts_view, listing, "max_output", "max_output"))


def limit_body(limit: wire.Limit) -> dict:
    """One limit as every surface spells it: `{"value", "source"}` (the
    facts panel, a breakdown's `model_window`, the Models readout)."""
    return {"value": limit.value, "source": limit.source}


def body(sizes: wire.Limits) -> dict:
    """`{"window": limit_body, "max_output": limit_body}`."""
    return {"window": limit_body(sizes.window), "max_output": limit_body(sizes.max_output)}


@dataclass(frozen=True)
class Ceiling:
    """What a prompt may hold on a resolution (`prompt_ceiling`)."""

    #: The tokens a prompt may hold: None when no walked attempt's window is
    #: known (use your own cap), 0 when a known window has no room left after
    #: the reserve (nothing more fits) -- never 0 for unknown.
    tokens: int | None
    #: The window that bound it (None with `tokens`).
    window: int | None
    #: The reply reservation subtracted from that window (0 when none bound).
    reserve: int
    #: Whether every walked attempt's window was known. False with nothing
    #: resolved.
    complete: bool
    #: `(provider_id, model)` of the attempt that bound it -- a tuple, since an
    #: OpenRouter model id holds `/` and a joined string could not be split.
    binding: tuple[str, str] | None
    #: "" or, when `tokens == 0`, a sentence saying why.
    reason: str


def _positive(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _sent_max_tokens(controls: Mapping) -> int | None:
    """The `max_tokens` the attempt's preset sends, under its wire name
    (`max_tokens`, `max_completion_tokens` at OpenAI): None when none is sent.
    The Anthropic API is always sent one (`llm_sampling.effective`)."""
    table = controls.get("controls")
    entry = table.get("max_tokens") if isinstance(table, Mapping) else None
    name = entry.get("wire") if isinstance(entry, Mapping) else None
    sent = controls.get("effective")
    if not isinstance(name, str) or not name or not isinstance(sent, Mapping):
        return None
    return _positive(sent.get(name))


def _reserve(attempt: resolved.Attempt, max_tokens: int | None) -> tuple[int, str]:
    """`(reserve, origin)`: `reply_reserve`'s figure and which rule gave it --
    "call", "preset" or "default" -- for a ceiling's `reason`."""
    known = attempt.limits
    window = known.window.value
    asked = _positive(max_tokens)
    if asked is not None:
        want, origin = asked, "call"
    else:
        sent = _sent_max_tokens(attempt.controls)
        if sent is not None:
            want, origin = sent, "preset"
        else:
            want = (DEFAULT_REPLY_RESERVE if window is None
                    else min(DEFAULT_REPLY_RESERVE, window // 4))
            origin = "default"
    cap = known.max_output.value
    return (want if cap is None else min(want, cap)), origin


def reply_reserve(attempt: resolved.Attempt, *, max_tokens: int | None = None) -> int:
    """The tokens a reply on `attempt` may take, held back from its window:
    the per-call cap the caller will send (`max_tokens`: `generate`'s, or a
    loop's per-turn cap -- not in `attempt.controls`, so the caller must pass
    it), else the `max_tokens` its preset sends, else
    `min(DEFAULT_REPLY_RESERVE, window // 4)` (the default alone when the
    window is unknown). Always capped at a known `max_output`, which bounds
    what may be asked and is never itself the reserve: reserving it whole
    would refuse every prompt on a model that lists a cap near its window.
    A `max_tokens` that is not a positive int is no cap."""
    return _reserve(attempt, max_tokens)[0]


def _why_full(origin: str, held: int, window: int, model: str) -> str:
    """The sentence a ceiling of 0 carries."""
    if origin == "reserve":
        return f"A reply reserve of {held:,} tokens fills {model}'s window of {window:,}."
    who = {"call": "The call", "preset": "The preset"}.get(origin, "The default")
    return f"{who} asks for {held:,} reply tokens; {model}'s window is {window:,}."


def prompt_ceiling(resolution: resolved.ResolvedInference, *, reserve: int | None = None,
                   max_tokens: int | None = None) -> Ceiling:
    """The token count a prompt may hold on `resolution` (01i-C2).

    Walks the attempts its `chain` is built from -- the primary, and the
    fallback only when it `rides` -- since the facade may send the same
    messages to either. A fallback that does not ride is never sent, and a
    decide resolution's separate stage is not on the chain (its prompt is the
    decide template's), so neither lowers the ceiling. For each attempt with a
    known window: `window - reserve` (the caller's `reserve` outright, else
    `reply_reserve(attempt, max_tokens=max_tokens)` by that attempt's own
    controls), floored at 0. The smallest wins; a tie keeps the primary.

    `tokens` is None when no walked window is known, and 0 (with a `reason`)
    when a known window has no room left after the reserve.
    """
    walked = resolution.attempts[:2] if resolution.rides else resolution.attempts[:1]
    complete = bool(walked)
    #: `(tokens, window, reserve, origin, attempt)` of the attempt binding so far.
    best: tuple[int, int, int, str, resolved.Attempt] | None = None
    for attempt in walked:
        window = attempt.limits.window.value
        if window is None:
            complete = False
            continue
        if reserve is not None:
            held, origin = max(reserve, 0), "reserve"
        else:
            held, origin = _reserve(attempt, max_tokens)
        tokens = max(window - held, 0)
        if best is None or tokens < best[0]:
            best = (tokens, window, held, origin, attempt)
    if best is None:
        return Ceiling(tokens=None, window=None, reserve=0, complete=complete, binding=None,
                       reason="")
    tokens, window, held, origin, bound = best
    model = bound.target.model or bound.model
    return Ceiling(tokens=tokens, window=window, reserve=held, complete=complete,
                   binding=(bound.provider_id, model),
                   reason="" if tokens else _why_full(origin, held, window, model))
