"""Spend forecasts for bounded runs, distinct from reported ledger accounting."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .. import decisions, store, tool_calls
from ..store.inference import probes
from ..store.inference.resolved import Attempt, ResolvedInference
from . import _contracts, _decision, _decision_calls, _requests


def decide_projection(resolved: ResolvedInference, item: decisions.Item, max_tokens: int,
                      prices: dict[tuple[str, str], Price | None]) -> float | None:
    """What one decide call over `item` could cost, as the spend guard prices
    it (01g-S7; spec 3.9): the rendered decide prompt's tokens with the margin
    and the call's output cap, at each attempt's price, summed over every call
    the decision could make -- each stage's attempts, and each one's
    prompt-only re-send. None when any stage is native (a native call is
    never modelled) or any attempt is unpriceable or uncapped. Renders a
    template: run it off the event loop."""
    counted = _prompt_count(_decision_calls.structured_messages([item], explain=""), ())
    prompt = counted * tool_calls.PROJECTION_MARGIN
    total = 0.0
    for stage in _decision.stages(resolved):
        if stage.mode != _contracts.STRUCTURED:
            return None
        for target in stage.chain.attempts:
            sent = _requests._capped(target, max_tokens)
            cost = (_cost(prices.get((target.provider_id, target.model)), prompt,
                          _requests.clamp_to_max_output(target, max_tokens)) if _requests.cap_sent(sent) else None)
            if cost is None:
                return None
            total += 2 * cost
    return total


def rows_cost(rows: Sequence[dict], prices: dict[tuple[str, str], Price | None]
              ) -> float | None:
    """`rows` priced as the spend guard prices a row (its counts at its
    attempt's price), or None when any one cannot be. Never a reported
    figure."""
    total = 0.0
    for row in rows:
        prompt, completion = row.get("prompt_tokens"), row.get("completion_tokens")
        if not (isinstance(prompt, int) and isinstance(completion, int)):
            return None
        provider = row.get("provider_id", "")
        price = (prices.get((provider, row.get("requested_model", "")))
                 or prices.get((provider, row.get("model", ""))))
        cost = _cost(price, prompt, completion)
        if cost is None:
            return None
        total += cost
    return total


@dataclass(frozen=True)
class Price:
    """What one attempt costs per token, as the spend guard prices it (spec
    3.9): its catalog's per-token prices (`basis` "catalog"), or the user's
    rates entry (`basis` "rates", priced by `pricing.estimate`)."""
    basis: Literal["catalog", "rates"]
    prompt: float = 0.0
    completion: float = 0.0
    entry: dict | None = None


def price_for(attempt: Attempt) -> Price | None:
    """`attempt`'s price, in the model-test preview's order: its cached
    catalog row's per-token prices (a stated 0 is a free model), else -- only
    for a provider that does not report its own price -- the user's rates
    (`pricing.rate_for_call`: the model's own, then `pricing.json`), else None
    (unpriceable). For a provider that reports its price the ledger never
    prices its calls from the user's rates, so a `"": 0` default meant for
    local models must not project a billed turn at nothing. Reads the store:
    run it off the event loop."""
    row = store.llm_connections.cached_row(attempt.provider_id, attempt.model)
    if isinstance(row, dict):
        prompt, completion = probes._rate(row.get("prompt")), probes._rate(row.get("completion"))
        if prompt is not None and completion is not None:
            return Price("catalog", prompt=prompt, completion=completion)
    preset = store.inference.providers.PRESETS.get(attempt.provider_preset)
    if preset is not None and preset.reports_price:
        return None
    entry = store.pricing.rate_for_call(store.pricing.read_pricing(),
                                        store.pricing.provider_rates(),
                                        provider_id=attempt.provider_id, model=attempt.model)
    if entry is None or store.pricing.estimate(entry, prompt_tokens=1,
                                               completion_tokens=1) is None:
        return None
    return Price("rates", entry=entry)


def prices_for(resolved: ResolvedInference) -> dict[tuple[str, str], Price | None]:
    """Every attempt's `price_for`, keyed by `(provider_id, model)` -- both
    the attempt's and the model its target sends. Reads the store."""
    out: dict[tuple[str, str], Price | None] = {}
    for attempt in resolved.attempts:
        price = price_for(attempt)
        out[(attempt.provider_id, attempt.model)] = price
        out[(attempt.target.provider_id, attempt.target.model)] = price
    return out


def _cost(price: Price | None, prompt: float, completion: float) -> float | None:
    """`prompt` and `completion` tokens at `price`, or None unpriced."""
    if price is None:
        return None
    if price.basis == "catalog":
        return prompt * price.prompt + completion * price.completion
    return store.pricing.estimate(price.entry, prompt_tokens=math.ceil(prompt),
                                  completion_tokens=math.ceil(completion))


def _prompt_count(messages: list[dict], defs: Sequence[dict]) -> int:
    """`messages` and `defs` counted as the guard counts a prompt
    (`tokens.count_if_loaded`, which never starts an encoder load)."""
    text = "\n".join([json.dumps(m, ensure_ascii=False, default=str) for m in messages]
                     + [json.dumps(d, ensure_ascii=False) for d in defs])
    return store.tokens.count_if_loaded(text)


def tool_run_refusal(task: str, resolved: ResolvedInference, budget: tool_calls.RunBudget,
                     prices: dict[tuple[str, str], Price | None] | None = None
                     ) -> tool_calls.RunRefused | None:
    """Why a tool run on `resolved` would be refused before anything is sent
    (spec 3.9), or None: with a spend ceiling, an attempt with no price this
    side knows (`price_for`) or whose output cap is not sent (`cap_sent`:
    without it the completion has no bound). The same check `run_tools`
    makes at entry, as a preflight a `def` route runs BEFORE it reserves, so
    an unpriceable run is a 409 rather than a 202 that fails. A fallback the
    loop never sends (`_tool_chain`) is not priced. `prices`, when not given,
    are read from the store."""
    if budget.spend_ceiling_usd is None:
        return None
    chain, _ = _requests._tool_chain(resolved, _requests.call_chain(resolved, max_tokens=budget.max_output_tokens))
    known = prices if prices is not None else prices_for(resolved)
    for target in chain.attempts:
        name = f"{target.model} on {target.label}"
        if known.get((target.provider_id, target.model)) is None:
            return tool_calls.RunRefused(
                "unpriceable", f"{task}: {name} has no price this side knows -- set rates "
                               "for it, or run without a spend ceiling")
        if not _requests.cap_sent(target):
            return tool_calls.RunRefused(
                "unpriceable", f"{task}: {name} cannot be sent an output cap, so a turn "
                               "has no price bound -- run without a spend ceiling")
    return None
