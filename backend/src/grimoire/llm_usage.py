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
    cache_read_tokens                   how much of the prompt was a cache HIT
    cache_write_tokens                  how much of it was written to the cache
    cost_usd + cost_basis               money, when the provider names a price
    model                               what actually answered, which can differ
                                        from what was asked for (a provider
                                        routing an alias, or a fallback route)

**The two cache counts are slices OF `prompt_tokens`, never additions to it**
(#148), and every consumer downstream depends on that: `store.usage` sums them
into their own columns and deliberately leaves `total_tokens` alone. A cached
prefix is billed at a fraction of a fresh one, so these are what say whether
caching is working at all -- the ledger without them can report what a month
cost but not what it saved, and cannot show a hit rate.

Getting the containment backwards is the one mistake here that corrupts a
number rather than losing it, and the wire shapes invite it: OpenAI's
`prompt_tokens` already counts the cache hit inside it, while Anthropic's
`input_tokens` counts only what was neither read nor written. The two adapters
therefore arrive at `prompt_tokens` differently -- `from_openai_chunk` takes it
as given, and `claude_agent` sums its three prompt keys -- and both end up
meaning the same thing before anything here records a cache count beside it.

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


def cache_written(block: dict) -> int | None:
    """Tokens this call wrote to the prompt cache, or None if it did not say.

    Exported for the same reason `tokens` and `money` are: `claude_agent` asks
    the identical question of a different wire shape, and the answer has two
    spellings that must not be added together. `cache_creation_input_tokens` is
    the flat total; `cache_creation` is that same total split per TTL tier
    (`ephemeral_5m_input_tokens`, `ephemeral_1h_input_tokens`, ...), which
    Anthropic added without removing the flat field. So the flat one wins
    whenever it is there, and the split is summed only as the fallback for a
    provider that sends the breakdown alone -- summing both would report every
    cache write twice.

    A split whose every entry is unusable answers None rather than 0, the rule
    `tokens` sets: "nobody counted" and "wrote nothing" are different claims.
    """
    flat = tokens(block.get("cache_creation_input_tokens"))
    if flat is not None:
        return flat
    split = block.get("cache_creation")
    if not isinstance(split, dict):
        return None
    counted = [n for n in (tokens(v) for v in split.values()) if n is not None]
    return sum(counted) if counted else None


def cache_read(block: dict) -> int | None:
    """Tokens this call read from the prompt cache, or None if it did not say.

    Two spellings again, and this time from two different providers rather than
    two eras of one: Anthropic reports `cache_read_input_tokens` at the top of
    the block, while OpenAI-shaped endpoints (OpenRouter included) nest it as
    `prompt_tokens_details.cached_tokens`. Both name the same quantity -- the
    part of the prompt that was already in the cache -- so both map to one
    column rather than to a pair a reader would have to know to add.
    """
    flat = tokens(block.get("cache_read_input_tokens"))
    if flat is not None:
        return flat
    details = block.get("prompt_tokens_details")
    return tokens(details.get("cached_tokens")) if isinstance(details, dict) else None


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
    # Beside `prompt_tokens`, never added to it (#148): on this wire shape the
    # prompt count already includes the cache hit, and the detail says how much
    # of it was free.
    for key, count in (("cache_read_tokens", cache_read(block)),
                       ("cache_write_tokens", cache_written(block))):
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
