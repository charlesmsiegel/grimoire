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

`tokens` and `money` are exported rather than private because `claude_agent`
uses them too. Its wire shape is entirely different (an SDK object, not an SSE
chunk), but "what counts as a token count" and "what counts as a price" are the
same two questions, and two adapters answering them separately is the drift this
module exists to prevent -- it was written with a private copy in the Claude
path and immediately grew a different answer about infinity.

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


def tokens(value: object) -> int | None:
    """A token count, or None for anything that is not one.

    None rather than 0, and the distinction is load-bearing all the way to the
    ledger: `store.usage` omits an absent count from the row rather than writing
    zero, because a row saying zero tokens is a row saying the call used none.
    A provider that sends `"input_tokens": "lots"` has not said that.

    `bool` is excluded deliberately -- it is an `int` subclass, so `True` would
    otherwise be recorded as a prompt of one token."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def money(value: object) -> float | None:
    """A price, or None. Accepts an int (a provider reporting exactly 0) and
    rejects NaN/inf, which would poison every total downstream of it -- and
    which `json.dumps` happily writes as `Infinity`, producing a ledger line no
    strict JSON reader can parse."""
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
        count = tokens(block.get(key))
        if count is not None:
            usage[key] = count
    # OpenRouter's `cost` is denominated in credits, which are USD one-for-one.
    # A plain `usage` block with no cost at all is the ordinary case for every
    # other endpoint, and leaving the key absent is what lets a rollup count the
    # call as unpriced instead of as free.
    cost = money(block.get("cost"))
    if cost is not None:
        usage["cost_usd"] = cost
        usage["cost_basis"] = BILLED
