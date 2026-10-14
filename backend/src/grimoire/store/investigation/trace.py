"""An investigation's trace (spec 8; 12-C2b): what it looked at before it
said what it said.

Refs, sizes and truncation for every step; the clipped query strings, the
only record of why a tool was called; the validated final payload's own
fields; and the run's usage in the three money columns, never added. No tool
result text (it is in the store already) and no reasoning or prose.

The query strings are model-written and may carry private prose drawn from
the store, so a trace is stored only where private campaign content already
lives (spec 8.2): a prompt-log capture, a candidate's proposal, or a draft
run's result.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from .. import usage
from ..continuity import candidates

VERSION = candidates.TRACE_VERSION
#: The longest string a trace keeps from a step's arguments.
ARG_CHARS = 200
#: The most refs a step keeps.
STEP_REFS = 12
#: The most steps a stored trace keeps (a run's budget bounds it well below).
MAX_STEPS = 32


def step(turn: int, tool: str, args: dict, *, ok: bool, refs: Iterable[str] = (),
         chars: int = 0, truncated: bool = False, error: str = "") -> dict:
    """One tool call's step: its turn, its tool, its arguments (strings
    clipped), and its result's refs, size and truncation -- or its error
    code. Never the result's text."""
    result: dict = {"ok": ok, "refs": list(refs)[:STEP_REFS], "chars": chars,
                    "truncated": truncated}
    if error:
        result["error"] = error
    return {"turn": turn, "tool": tool, "args": _clipped(args), "result": result}


def _clipped(args: dict) -> dict:
    out: dict = {}
    for key, value in args.items():
        if isinstance(value, str):
            out[key] = value[:ARG_CHARS]
        elif isinstance(value, list):
            out[key] = [v[:ARG_CHARS] if isinstance(v, str) else v for v in value][:STEP_REFS]
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
    return out


def _column(total: float, calls: int) -> float | None:
    """A money column: None where no call was priced that way -- a price
    nobody reported is never shown as zero."""
    return total if calls else None


def usage_of(rows: Iterable[dict], *, model_turns: int, tool_calls: int, decisions: int,
             seconds: float, rates: usage.Rates | None = None) -> dict:
    """The run's usage: its counts and wall time, and its ledger rows folded
    by the production fold (`usage.fold_rows`) into the three money columns,
    each None when no call landed in it, and `unpriced_calls`. `rates`
    models what no provider priced, as the Costs page does."""
    bucket = usage.fold_rows(rows, rates)
    billed = bucket["priced_calls"] - bucket["subscription_calls"]
    return {"model_turns": model_turns, "tool_calls": tool_calls, "decisions": decisions,
            "seconds": round(seconds, 2) if math.isfinite(seconds) else None,
            "cost": {"cost_usd": _column(bucket["cost_usd"], billed),
                     "estimated_usd": _column(bucket["estimated_usd"],
                                              bucket["subscription_calls"]),
                     "modelled_usd": _column(bucket["modelled_usd"], bucket["modelled_calls"])},
            "unpriced_calls": bucket["unpriced_calls"]}


def build(*, run: str, mode: str, task: str, model: str, started: str, outcome: str,
          limit: str, steps: list[dict], inspected: Iterable[str], final: dict | None,
          usage_block: dict) -> dict:
    """The stored shape (spec 8.1)."""
    return {"version": VERSION, "run": run, "mode": mode, "task": task, "model": model,
            "started": started, "outcome": outcome, "limit": limit,
            "steps": list(steps)[:MAX_STEPS],
            "inspected": [f"scene:{sid}" for sid in sorted(inspected)],
            "final": final, "usage": usage_block}


def valid(value: object) -> bool:
    """Whether `value` has the stored trace's shape: the check
    `continuity.candidates` reads a proposal's `investigation` field with,
    which owns it so a cache read needs nothing from this package."""
    return candidates.investigation_trace(value) is not None
