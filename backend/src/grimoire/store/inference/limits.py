"""A model's context window and output cap as a resolved fact (spec 01i).

`of` resolves both limits of one model on one provider from the two sources
the resolver already reads for every attempt -- the model's facts and its
provider's cached catalog row -- so the turn path gains no read. Each takes the
first source that states it: the user's own word (`context_window` /
`max_output` in the model's facts), then the catalog (`context` /
`max_output`), then unknown. Never 0 and never guessed from a name.

`prompt_ceiling` is the stated way a consumer turns a resolution into the
tokens a prompt may hold (01i-C2, spec section 5): the smallest window, less
the reply it reserves, over every target the call may be sent to. A consumer
never computes `window - max_output`, never takes its own minimum over
`resolved.attempts`, and never reads the catalog or the facts itself: each
would disagree with the facade about which targets a prompt reaches and how
much reply to hold back. `tokens is None` means "use your own cap"; `0` means
a known window with no room left.

Pure, and reads nothing: cheap enough to call per turn.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ... import wire
from . import resolved as resolved_mod

#: What a reply is held back for when nothing says how long it may be (no
#: per-call cap, no preset `max_tokens`): a shared window counts the reply, so
#: something must be reserved, and reasoning tokens count against it too. A
#: placeholder argued from that structure, to be tuned against real prompts
#: later -- never against a measured library.
DEFAULT_REPLY_RESERVE = 4096

#: The share of a window the default reserve may take at most (`window //
#: 4`), so a small local window stays usable rather than reserved whole.
_DEFAULT_SHARE = 4

#: The bound a stated limit must stay under: refuses only typos no int32
#: field on any wire could carry.
STATED_MAX = 2**31

def tokens(value: object) -> int | None:
    """A positive token count below `STATED_MAX`, else None: what a limit
    may be, stated or listed."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value < STATED_MAX:
        return value
    return None


def _limit(stated: object, listed: object) -> wire.Limit:
    user = tokens(stated)
    if user is not None:
        return wire.Limit(user, "user")
    catalog = tokens(listed)
    if catalog is not None:
        return wire.Limit(catalog, "catalog")
    return wire.UNKNOWN_LIMIT


def of(row: Mapping | None, model_facts: Mapping | None) -> wire.Limits:
    """`model_facts` (`facts.of`'s shape) and catalog `row` (or None) as one
    model's limits. Never raises: a malformed value contributes nothing."""
    stated = model_facts if isinstance(model_facts, Mapping) else {}
    listed = row if isinstance(row, Mapping) else {}
    return wire.Limits(window=_limit(stated.get("context_window"), listed.get("context")),
                       max_output=_limit(stated.get("max_output"), listed.get("max_output")))


def limit_body(limit: wire.Limit) -> dict:
    """One limit as a surface reads it: `{"value", "source"}`."""
    return {"value": limit.value, "source": limit.source}


def body(model_limits: wire.Limits) -> dict:
    """A model's limits as a surface reads them: `{"window", "max_output"}`,
    each `limit_body`'s shape."""
    return {"window": limit_body(model_limits.window),
            "max_output": limit_body(model_limits.max_output)}


@dataclass(frozen=True)
class Ceiling:
    """What a prompt may hold on a resolution (`prompt_ceiling`)."""

    #: The tokens a prompt may hold; None when no walked target's window is
    #: known (never 0 for unknown). 0 means a known window with no room left.
    tokens: int | None
    #: The window that bound it (None with `tokens`).
    window: int | None
    #: The reply reservation subtracted from that window (0 with no window).
    reserve: int
    #: Whether every target on the chain had a known window.
    complete: bool
    #: `(provider_id, model)` of the attempt that bound it -- a tuple, since a
    #: model id may itself hold `/`.
    binding: tuple[str, str] | None
    #: A sentence when `tokens == 0`, saying why; "" otherwise.
    reason: str


#: The ceiling of a resolution whose chain states no window, or is empty.
UNKNOWN = Ceiling(None, None, 0, False, None, "")


def _sent_max_tokens(attempt: resolved_mod.Attempt) -> int | None:
    """The `max_tokens` the attempt is sent from its preset, under whichever
    wire name its provider takes (`llm_sampling.effective`); None when it is
    sent none."""
    controls = attempt.controls if isinstance(attempt.controls, Mapping) else {}
    effective = controls.get("effective")
    if not isinstance(effective, Mapping):
        return None
    for name in ("max_tokens", "max_completion_tokens"):
        value = tokens(effective.get(name))
        if value is not None:
            return value
    return None


def _check(name: str, value: int | None, *, least: int) -> None:
    if value is not None and not (isinstance(value, int) and not isinstance(value, bool)
                                  and value >= least):
        raise ValueError(f"{name} must be a whole number of at least {least}, not {value!r}")


def _reserve(attempt: resolved_mod.Attempt, max_tokens: int | None) -> tuple[int, int, str]:
    """`(reserve, what was asked before the max-output cap, where it came
    from)`: "call", "preset" or "default"."""
    limits = attempt.target.limits
    if max_tokens is not None:
        want, source = max_tokens, "call"
    else:
        sent = _sent_max_tokens(attempt)
        if sent is not None:
            want, source = sent, "preset"
        else:
            window = limits.window.value
            want = (DEFAULT_REPLY_RESERVE if window is None
                    else min(DEFAULT_REPLY_RESERVE, window // _DEFAULT_SHARE))
            source = "default"
    cap = limits.max_output.value
    return (min(want, cap) if cap is not None else want), want, source


def reply_reserve(attempt: resolved_mod.Attempt, *, max_tokens: int | None = None) -> int:
    """The tokens a reply on `attempt` may take, to be held back from its
    window: the caller's per-call cap (`max_tokens`, which a caller sending
    one must pass), else the `max_tokens` its preset is sent, else
    `min(DEFAULT_REPLY_RESERVE, window // 4)` -- always capped at a known
    `max_output`, which is never itself the reserve."""
    _check("max_tokens", max_tokens, least=1)
    return _reserve(attempt, max_tokens)[0]


def _walked(res: resolved_mod.ResolvedInference) -> tuple[resolved_mod.Attempt, ...]:
    """The attempts `res.chain` is built from: the primary, and the fallback
    only when it rides (`ResolvedInference.chain`'s rule)."""
    if not res.attempts:
        return ()
    if res.rides and len(res.attempts) > 1:
        return res.attempts[:2]
    return res.attempts[:1]


def _binding(attempt: resolved_mod.Attempt) -> tuple[str, str]:
    target = attempt.target
    return (target.provider_id or attempt.provider_id, target.model or attempt.model)


def _reason(window: int, reserve: int, wanted: int, source: str, *,
            fallback: tuple[str, str] | None) -> str:
    """Why a ceiling is 0: what was asked for the reply (and the max-output
    cap that cut it, when one did), against the window that bound it -- named
    as the fallback's when the fallback, not the primary, bound it."""
    whose = "the preset" if fallback is None else "the fallback's preset"
    asked = {"call": "This call asks for", "preset": f"{whose[0].upper()}{whose[1:]} asks for",
             "given": "The caller reserves"}.get(source, "The default reserve is")
    capped = (f", capped at the model's max output of {reserve:,}" if reserve < wanted else "")
    model = ("this model's window" if fallback is None
             else f"the fallback's window ({fallback[1]} on {fallback[0]})")
    return (f"{asked} {wanted:,} reply tokens{capped}; {model} is {window:,}, "
            "which leaves no room for a prompt.")


def prompt_ceiling(resolved: resolved_mod.ResolvedInference, *, reserve: int | None = None,
                   max_tokens: int | None = None) -> Ceiling:
    """The tokens a prompt sent on `resolved` may hold: over the primary, and the
    fallback when it rides, the smallest `window - reserve` among those whose
    window is known, floored at 0 -- the facade may send the same messages to
    either, and a prompt sized for the primary overflows a smaller fallback.

    The reserve is `reserve` when given (outright, uncapped), else each
    attempt's own `reply_reserve(attempt, max_tokens=max_tokens)`. A fallback
    that does not ride, and a decide resolution's separate fallback stage, are
    never sent this prompt, so neither lowers the ceiling.
    """
    _check("reserve", reserve, least=0)
    _check("max_tokens", max_tokens, least=1)
    walked = _walked(resolved)
    best: tuple[int, int, int, int, resolved_mod.Attempt, str] | None = None
    for attempt in walked:
        window = attempt.target.limits.window.value
        if window is None:
            continue
        held, wanted, source = ((reserve, reserve, "given") if reserve is not None
                                else _reserve(attempt, max_tokens))
        tokens = max(0, window - held)
        if best is None or tokens < best[0]:
            best = (tokens, window, held, wanted, attempt, source)
    if best is None:
        return UNKNOWN
    tokens, window, held, wanted, attempt, source = best
    complete = all(a.target.limits.window.value is not None for a in walked)
    binding = _binding(attempt)
    reason = (_reason(window, held, wanted, source,
                      fallback=None if attempt is walked[0] else binding)
              if tokens == 0 else "")
    return Ceiling(tokens, window, held, complete, binding, reason)
