"""What one investigation may spend (spec 7; 12-C2a), per entry point.

Every number is structural -- a turn shape that fits the job, never a
measurement of anybody's library -- and is to be tuned through the eval gate
(spec 10). They live here and nowhere else.

- **E1** (`turn`, 12-S6) is a pre-generation step a player waits on: three
  turns (search, read, select), a small output cap (it writes a selection,
  not prose) and one named total deadline, `E1_WALL_S`, which is 02's
  `PLAN_CEILING_S` argued the same way (an optional step costs less waiting
  than the reply it shapes), capped by `llm_call_budget` when that is set.
- **E2** (`continuity`) and **E3** (`question`) are runs a reader started
  and waits on behind a 202: 01g's default turn and tool-call shape, and a
  wall of three and two `llm_call_budget`s.

No entry point reserves a finalize turn (`reserve_final=False`): every one
ends on a terminal tool, and a finalize turn's prose is dropped unread (an
implicit "insufficient evidence", spec 5), so reserving one would only take
a turn the model could have used to finish properly.

`investigation_spend_ceiling` is US dollars as 01g's estimator models a
call, unset by default: no spend axis, the run bounded by turns, tool calls,
wall clock and context. A campaign `over` its own budget is not refused --
campaign budgets warn, and measure a different column (01g open question 4).
"""

from __future__ import annotations

import math
from dataclasses import replace

from ... import tool_calls
from .. import config
from ..inference import limits
from ..inference import resolved as inference_resolved

ENTRIES: tuple[str, ...] = ("turn", "continuity", "question")

#: E1's total deadline, in seconds (02's `PLAN_CEILING_S`).
E1_WALL_S = 20.0

#: The prompt ceiling an unknown window falls back to, in tokens: small on
#: purpose, so a model whose window nobody stated is bounded by the turn and
#: result caps long before it could overflow (spec 7.3). To be raised once
#: evals show it binds.
FALLBACK_PROMPT_TOKENS = 8192

#: The config key holding the spend ceiling, in USD.
SPEND_CEILING_KEY = "investigation_spend_ceiling"

_SHAPES: dict[str, dict] = {
    "turn": {"max_turns": 3, "max_tool_calls": 4, "max_decisions": 0,
             "max_output_tokens": 512, "max_result_chars_total": 12_000},
    "continuity": {"max_turns": 6, "max_tool_calls": 12, "max_decisions": 2,
                   "max_output_tokens": 2048, "max_result_chars_total": 48_000},
    "question": {"max_turns": 5, "max_tool_calls": 10, "max_decisions": 1,
                 "max_output_tokens": 2048, "max_result_chars_total": 36_000},
}
_WALL_CALLS = {"continuity": 3, "question": 2}


def spend_ceiling(cfg: dict | None = None) -> float | None:
    """The configured ceiling in USD, or None: unset, blank, unparseable,
    not finite or not above zero all mean no spend axis."""
    cfg = config.read_config() if cfg is None else cfg
    raw = cfg.get(SPEND_CEILING_KEY)
    if raw in (None, ""):
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def wall_seconds(entry: str, call_budget: float) -> float:
    """The entry point's wall: E1's named deadline (capped by a positive
    `llm_call_budget`), else a multiple of it; `<= 0` is no wall."""
    if entry == "turn":
        return min(E1_WALL_S, call_budget) if call_budget > 0 else E1_WALL_S
    return _WALL_CALLS[entry] * call_budget if call_budget > 0 else 0.0


def for_entry(entry: str, cfg: dict | None = None) -> tool_calls.RunBudget:
    """The `RunBudget` an entry point hands the loop (spec 7.1). Reads the
    store: call it off the event loop."""
    if entry not in ENTRIES:
        raise ValueError(f"an investigation entry point is one of {', '.join(ENTRIES)}")
    cfg = config.read_config() if cfg is None else cfg
    return tool_calls.RunBudget(
        **_SHAPES[entry], wall_seconds=wall_seconds(entry, config.llm_call_budget()),
        spend_ceiling_usd=spend_ceiling(cfg), reserve_final=False, finalize_note=False)


def prompt_tokens(resolved: inference_resolved.ResolvedInference,
                  budget: tool_calls.RunBudget) -> int:
    """The context stop's ceiling (spec 7.3): 01i-C2's `prompt_ceiling` with a
    reserve sized for the turn's own output cap -- never `window -
    max_output` -- else `FALLBACK_PROMPT_TOKENS` when a window is unknown on
    any attempt the chain may send."""
    ceiling = limits.prompt_ceiling(resolved, max_tokens=budget.max_output_tokens)
    if ceiling.tokens is None:
        return FALLBACK_PROMPT_TOKENS
    # A window known on some attempts only bounds those: an attempt with none
    # stated (a riding fallback, say) gets the fallback's bound too.
    return ceiling.tokens if ceiling.complete else min(ceiling.tokens, FALLBACK_PROMPT_TOKENS)


def with_context(budget: tool_calls.RunBudget, tokens: int) -> tool_calls.RunBudget:
    """`budget` carrying the context stop."""
    return replace(budget, max_prompt_tokens=max(1, tokens))
