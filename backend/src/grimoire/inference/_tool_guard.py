"""What refuses a tool run's next send unsent: its context stop and spend ceiling."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from .. import tool_calls, wire
from . import _pricing, _requests
from ._pricing import Price


class _SendGuard:
    """The checks a turn passes before it is sent (spec 3.9): the caller's
    context stop (roadmap 12 §7.3) and the spend axis (01g-S5), which counts
    the decide tool's own calls too (01g-S7). A guard, never accounting: it
    prices counts at the prices read at entry, never a reported cost."""

    def __init__(self, budget: tool_calls.RunBudget, defs: Sequence[dict],
                 prices: dict[tuple[str, str], Price | None]):
        self.budget = budget
        self.defs = defs
        #: The spend axis (01g-S5): each attempt's price, read at entry, each
        #: sent turn's `(projection, row)`, and the prompt count the next
        #: projection builds on.
        self.prices = prices
        self.spends: list[tuple[float, dict | None]] = []
        self.last_prompt: float | None = None
        self.next_prompt = 0.0
        #: What the decide tool's calls cost as the guard prices them (01g-S7).
        self.charged = 0.0

    async def over_context(self, sending: list[dict]) -> bool:
        """Whether `sending` holds more than the run's `max_prompt_tokens`."""
        cap = self.budget.max_prompt_tokens
        return cap is not None and await asyncio.to_thread(
            _pricing._prompt_count, list(sending), self.defs) > cap

    async def projected(self, chain: wire.Chain, sending: list[dict],
                        unsent: list[dict]) -> float | None:
        """What the next send could cost, as the guard prices it: None with no
        ceiling. Prompt tokens are counted in a worker thread with the margin
        -- the whole prompt on the first send, else what was appended since
        the last (`unsent`), on top of that send's reported (else projected)
        count -- and the completion is the turn's cap; the maximum over the
        attempts that could serve it."""
        cap = self.budget.max_output_tokens
        if self.budget.spend_ceiling_usd is None or cap is None:
            # `RunBudget` refuses an uncapped run beside a ceiling.
            return None
        if self.last_prompt is None:
            counted = await asyncio.to_thread(_pricing._prompt_count, list(sending), self.defs)
            prompt = counted * tool_calls.PROJECTION_MARGIN
        else:
            counted = await asyncio.to_thread(_pricing._prompt_count, unsent, ())
            prompt = self.last_prompt + counted * tool_calls.PROJECTION_MARGIN
        self.next_prompt = prompt
        costs = [_pricing._cost(self.prices.get((t.provider_id, t.model)), prompt,
                       _requests.clamp_to_max_output(t, cap))
                 for t in chain.attempts]
        known = [c for c in costs if c is not None]
        # Every attempt was priced at entry (`tool_run_refusal`); one that
        # still is not counts as the dearest known, never as free.
        return max(known) if known else float("inf")

    def over_ceiling(self, projection: float | None) -> bool:
        """Whether a send projected at `projection` would carry the run past
        its ceiling; never with no projection."""
        return projection is not None and self.spent() + projection > (
            self.budget.spend_ceiling_usd or 0.0)

    def room(self) -> float | None:
        """The spend left under the ceiling; None with no ceiling."""
        ceiling = self.budget.spend_ceiling_usd
        return None if ceiling is None else ceiling - self.spent()

    def spent(self) -> float:
        """Every sent turn's cost as the guard prices it: its row's counts at
        its attempt's price, else that turn's projection. Never a reported
        figure (`cost_usd`) -- the guard keeps one unit."""
        total = self.charged
        for projection, row in self.spends:
            cost = self._row_cost(row)
            total += cost if cost is not None else projection
        return total

    def _row_cost(self, row: dict | None) -> float | None:
        if not isinstance(row, dict):
            return None
        prompt, completion = row.get("prompt_tokens"), row.get("completion_tokens")
        if not (isinstance(prompt, int) and isinstance(completion, int)):
            return None
        provider = row.get("provider_id", "")
        price = (self.prices.get((provider, row.get("requested_model", "")))
                 or self.prices.get((provider, row.get("model", ""))))
        return _pricing._cost(price, prompt, completion)

    def note(self, projection: float | None, row: dict | None) -> None:
        """A sent turn's projection and its ledger row, for `spent` and the
        next projection; nothing with no ceiling."""
        if projection is None:
            return
        self.spends.append((projection, row))
        prompt = row.get("prompt_tokens") if isinstance(row, dict) else None
        self.last_prompt = (float(prompt) if isinstance(prompt, int)
                            and not isinstance(prompt, bool) else self.next_prompt)
