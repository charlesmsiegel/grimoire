"""Reading a provider's own accounting off the wire (#152).

The two OpenAI-shaped adapters (`openrouter`, `openai_compatible`) already
duplicate their error helpers, and this could have been a third pair of copies.
It is one module instead because unlike those, this one is a *mapping*: it
decides which wire field becomes which ledger column, and two copies of a
mapping drift into two different ledgers written by the same app.

What it fills is the plain dict `LLMClient.stream` threads down to the adapter
— see `store.usage.Meter` for why the numbers come back through a holder rather
than a return value. Keys, all optional:

    prompt_tokens / completion_tokens   ints, as the provider counted them
    cost_usd + cost_basis               money, when the provider names a price
    model                               what actually answered, which can differ
                                        from what was asked for (a provider
                                        routing an alias, or a fallback route)

Nothing here raises. A usage block is trailing metadata on a reply that has
already been delivered, and a provider sending a shape we did not expect must
cost the statistic, never the turn — so every value is type-checked on the way
in and anything unrecognized is simply not recorded.
"""

from __future__ import annotations

#: Money reported by a provider that charges per call. The other basis
#: (`equivalent`) is `claude_agent`'s, whose calls bill against a subscription;
#: `store.usage` keeps the two out of one total and says why.
BILLED = "billed"


def _int(value: object) -> int | None:
    """A token count, or None for anything that is not one. `bool` is excluded
    deliberately -- it is an `int` subclass, so `True` would otherwise be
    recorded as a prompt of one token."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _money(value: object) -> float | None:
    """A price, or None. Accepts an int (a provider reporting exactly 0) and
    rejects NaN/inf, which would poison every total downstream of it."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if value != value or abs(value) == float("inf") or value < 0:
        return None
    return value


def from_openai_chunk(obj: object, usage: dict | None) -> None:
    """Fold one SSE chunk's `usage` block (and `model`) into `usage`, if it has
    them.

    Called for **every** chunk rather than only the last, because which chunk
    carries the block is a provider's choice: OpenRouter attaches it to the
    final one, an OpenAI-compatible server with `include_usage` sends a whole
    extra chunk after the last delta, and some send a null `usage` on every
    chunk until the end. Recognizing the block wherever it lands is cheaper than
    modelling each of those.

    `usage is None` means the caller did not ask for accounting -- the common
    case for a client constructed outside the facade -- and short-circuits
    before any parsing.
    """
    if usage is None or not isinstance(obj, dict):
        return
    model = obj.get("model")
    if isinstance(model, str) and model:
        usage["model"] = model
    block = obj.get("usage")
    if not isinstance(block, dict):
        return
    for key in ("prompt_tokens", "completion_tokens"):
        count = _int(block.get(key))
        if count is not None:
            usage[key] = count
    # OpenRouter's `cost` is denominated in credits, which are USD one-for-one.
    # A plain `usage` block with no cost at all is the ordinary case for every
    # other endpoint, and leaving the key absent is what lets a rollup count the
    # call as unpriced instead of as free.
    cost = _money(block.get("cost"))
    if cost is not None:
        usage["cost_usd"] = cost
        usage["cost_basis"] = BILLED
