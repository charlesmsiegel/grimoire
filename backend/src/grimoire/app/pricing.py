"""What booked model calls cost at the prices the person set (112): an estimate, never a bill.

The app's model configuration may hold a price per million prompt tokens and per million reply tokens (and,
optionally, per million cached prompt tokens) for its configured model. A cost is worked out by 048's
``grimoire.tracing`` cost model from the token counts 013 booked and those prices as they are **now**: no usage
record stores a cost, so changing a price re-prices every past call on that model. Nothing here is a float:
prices are ``Decimal``, counts are whole numbers, and a cost is given as decimal text.

Every state is said, as 048 says it: ``unpriced`` (no cost: no prices are set, or none of the calls went to the
priced model), ``priced`` (every count provider-reported), ``estimated`` (some count was not the provider's own)
and ``partial`` (a lower bound). ``cost_lower_bound`` is true whenever the number is a floor: a needed count went
unreported, a call was sent with nothing booked, or some attempts went to a model with no price (a fallback, or
the model configured before). No record holds a cost the provider itself reported; were one to, it would win.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext

from grimoire.core.inference import CallAttempt
from grimoire.providers.config import route_label
from grimoire.tracing import Price, PriceTable, usage_cost

from .config import ModelConfig

#: A cost is given to six decimal places (a millionth of the price's unit), then shown with at least two.
_STEP = Decimal("0.000001")
_UNPRICED = {"cost": None, "cost_state": "unpriced", "cost_lower_bound": False, "unpriced_attempts": 0}


def price_table(config: ModelConfig | None) -> PriceTable | None:
    """The price of ``config``'s model, by the route its calls are booked under; ``None`` until both the prompt
    and the reply price are set. Fallback models have no price: their attempts are counted as unpriced."""
    if config is None or not config.priced:
        return None
    price = Price(config.price_prompt, config.price_reply, config.price_cached)  # type: ignore[arg-type]
    return PriceTable({route_label(config.endpoint, config.model): price})


def cost_text(cost: Decimal, *, lower: bool = False) -> str:
    """``cost`` as decimal text, to six places and never fewer than two. A lower bound is rounded down, so it
    stays one; anything else to the nearest millionth (ties to even)."""
    with localcontext() as context:
        context.prec = 60
        text = format(cost.quantize(_STEP, rounding=ROUND_FLOOR if lower else ROUND_HALF_EVEN), "f")
    whole, _, places = text.partition(".")
    return f"{whole}.{places.rstrip('0').ljust(2, '0')}"


def cost_view(attempts: Iterable[CallAttempt], prices: PriceTable | None, *, unknown_calls: int = 0) -> dict:
    """The cost of ``attempts`` as a screen may show it: ``cost`` (decimal text, ``None`` when unpriced),
    ``cost_state``, ``cost_lower_bound`` and ``unpriced_attempts`` (sent attempts on a route with no price,
    which the cost leaves out). ``unknown_calls`` are calls sent with no attempt booked: what they used is
    unknown, so any cost beside them is a floor."""
    if prices is None:
        return dict(_UNPRICED)
    attempts = tuple(attempts)
    known = [each for each in attempts if each.outcome == "not_sent" or each.route in prices.prices]
    others = len(attempts) - len(known)
    if others and not any(each.outcome != "not_sent" for each in known):
        return {**_UNPRICED, "unpriced_attempts": others}
    with localcontext() as context:
        context.prec = 60  # counts times prices are summed exactly, whatever their size
        cost, state, exact = usage_cost(known, prices)
    lower = not exact or others > 0 or unknown_calls > 0
    if lower and state == "priced":
        state = "partial"
    return {"cost": cost_text(cost, lower=lower), "cost_state": state, "cost_lower_bound": lower,  # type: ignore[arg-type]
            "unpriced_attempts": others}


__all__ = ["cost_text", "cost_view", "price_table"]
