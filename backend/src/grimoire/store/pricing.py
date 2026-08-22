"""User-supplied per-token rates, for the calls whose provider names no price (#158).

The ledger (`store.usage`) records `cost_usd` only when the provider itself
reported one. OpenRouter does; every `openai_compatible` endpoint does not, and
the Claude Agent path reports a price it did not charge. So a rollup over a
mixed library has three kinds of call in it, and only one of them is money:

- **billed** — the provider said what it charged.
- **subscription** — the provider said what it *would* have charged
  (`cost_basis: "equivalent"`), against auth that bills a flat fee instead.
- **unpriced** — nobody said anything.

This module is what turns the third kind into a number. It holds a table of
per-token rates the user maintains, `<home>/pricing.json`::

    {
      "meta-llama/llama-3.1-70b": {"prompt_usd_per_1k": 0.0004,
                                   "completion_usd_per_1k": 0.0004},
      "anthropic/*":              {"prompt_usd_per_1k": 0.003,
                                   "completion_usd_per_1k": 0.015,
                                   "cache_read_usd_per_1k": 0.0003,
                                   "cache_write_usd_per_1k": 0.00375},
      "":                         {"prompt_usd_per_1k": 0.001,
                                   "completion_usd_per_1k": 0.002}
    }

A sibling file rather than a `config.md` key, for the reason #158 gives: config
frontmatter is flat string-scalar, and a per-model map is not.

**Both base rates are required.** An entry carrying only one is dropped, not
half-applied: pricing a call's prompt at a rate and its completion at nothing
produces a figure that is confidently wrong, and on a call that generated
nothing it produces `$0.00` — the one thing this whole feature exists not to
say. The cache pair stays optional for the opposite reason: those tokens have
a rate either way (see below).

**What comes out of here is never spend.** An estimate is arithmetic over rates
somebody typed, against token counts a provider reported; it belongs in its own
column (`modelled_usd`) beside `cost_usd`, never summed into it, and never
counted against a budget. The whole reason `store.usage` writes an *absent*
price rather than a zero one is so this pass can tell "free" from "unknown" —
spending that distinction to make a total look complete would undo it.

Rates are ``$ per 1,000 tokens``, the shape #158 specified. Providers publish
per-million these days, so the config UI shows the per-million equivalent
beside each box; the file keeps the documented unit.

**The two cache rates are optional, and their absence is not zero.** Cache
counts are slices OF `prompt_tokens` (#148), so a table that names no cache
rate has already priced those tokens at the prompt rate — which is the right
answer for a provider that does not discount them, and a small over-estimate
for one that does. Naming a cache rate moves those tokens out of the prompt
subtotal and onto their own; see `estimate`.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, paths

#: The key an entry uses to mean "every model with no entry of its own".
DEFAULT_KEY = ""

#: The rate fields an entry may carry, in the order `estimate` applies them.
#: `prompt`/`completion` are the pair that makes an entry usable at all; the
#: cache pair is optional and refines the first (see the module docstring).
PROMPT = "prompt_usd_per_1k"
COMPLETION = "completion_usd_per_1k"
CACHE_READ = "cache_read_usd_per_1k"
CACHE_WRITE = "cache_write_usd_per_1k"
FIELDS = (PROMPT, COMPLETION, CACHE_READ, CACHE_WRITE)

#: How many entries the table may hold. This is a hand-maintained file, and the
#: cap is on what a rollup will build a lookup out of rather than on trust: the
#: wildcard scan below is linear per distinct model, so an accidental dump of a
#: whole catalog into it would be paid for on every summary.
MAX_ENTRIES = 500


def pricing_path() -> Path:
    return paths.home() / "pricing.json"


def _rate(value: object) -> float | None:
    """One rate as a non-negative finite float, or None for anything that is
    not one.

    Takes a string as readily as a number: this file is hand-editable, and a
    rate typed as ``"0.002"`` is a rate. Negative, infinite, NaN and
    unparseable all answer None rather than 0.0 — the distinction `store.usage`
    rests on is between a price and no price, and a bad entry that silently
    priced a model at zero would report a library as free.
    """
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if value != value or abs(value) == float("inf") or value < 0:
        return None
    return value


def _entry(value: object) -> dict | None:
    """One table entry, keeping only the fields that are usable rates.

    An entry without BOTH base rates is dropped rather than kept partial: the
    pair is what makes an entry able to price a call, and a surviving one would
    shadow the `""` default for that model — an entry that silently turns
    pricing OFF, or worse prices half a call at nothing, for exactly the model
    somebody tried to price.
    """
    if not isinstance(value, dict):
        return None
    kept = {}
    for field in FIELDS:
        rate = _rate(value.get(field))
        if rate is not None:
            kept[field] = rate
    # BOTH base rates, not either. A completion-only entry would price every
    # prompt token at zero -- and, on a call that generated nothing, would
    # report `$0.00` for a call nobody priced, which is exactly the claim this
    # feature exists not to make. An entry that cannot price both halves of a
    # call cannot price the call, so it is dropped like any other unusable one.
    # The cache pair stays optional: those tokens have a rate either way, the
    # prompt rate, which is what a table naming no cache rate is saying.
    return kept if PROMPT in kept and COMPLETION in kept else None


def read_pricing() -> dict[str, dict]:
    """The rate table, normalized. `{}` when there is none, or when the file
    cannot be read or parsed.

    Fail-soft, unlike a record read: this runs inside rollups whose whole job is
    to draw a report, and a hand-edited comma in `pricing.json` must cost the
    estimates rather than the page that would have shown the real spend beside
    them. A dropped entry is visible — its model reads "unpriced" again, which
    is what it read before anyone typed a rate.
    """
    path = pricing_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    table: dict[str, dict] = {}
    for key, value in data.items():
        if not isinstance(key, str) or len(table) >= MAX_ENTRIES:
            continue
        entry = _entry(value)
        if entry is not None:
            table[key] = entry
    return table


def write_pricing(table: object) -> dict[str, dict]:
    """Replace the table with `table`, normalized, and return what was stored.

    A PUT of the whole table rather than a patch, like the budget route: a rate
    entry is removed by sending a table without it, and there is no partial
    shape whose meaning a reader could have to guess. Raises `ValueError` on a
    table that is not a mapping, and on one over `MAX_ENTRIES` — an entry that
    would be silently dropped on the way in is a rate somebody typed and would
    never see again.
    """
    if not isinstance(table, dict):
        raise ValueError("pricing must be an object keyed by model id")
    if len(table) > MAX_ENTRIES:
        raise ValueError(f"pricing holds at most {MAX_ENTRIES} entries")
    kept: dict[str, dict] = {}
    for key, value in table.items():
        if not isinstance(key, str):
            raise ValueError("every pricing key must be a model id")
        entry = _entry(value)
        if entry is not None:
            kept[key] = entry
    # `ensure_home`, like every other first-write path (`config.write_config`,
    # `fork`): `atomic.write_text` creates its temp file BESIDE the target, so a
    # store root that does not exist yet is a `FileNotFoundError` rather than a
    # created directory. This endpoint stands alone -- it is reachable as the
    # very first call a fresh install makes -- so it cannot assume something
    # else has been there first.
    paths.ensure_home()
    atomic.write_text(pricing_path(),
                      json.dumps(kept, indent=2, sort_keys=True) + "\n")
    return kept


def rate_for(table: dict[str, dict], model: object) -> dict | None:
    """The entry that prices `model`, or None when nothing does.

    Three tiers, most specific first:

    1. the model's exact id;
    2. the longest ``prefix*`` entry it matches — a provider publishes one price
       sheet per family, and ``anthropic/*`` is how a reader says that once
       rather than per model id;
    3. the ``""`` default.

    Longest-prefix rather than first-match, because a table naturally holds both
    ``openai/*`` and ``openai/gpt-5*`` and the narrower one is the one that was
    typed second on purpose. A model this ledger recorded as ``"unknown"``
    (`store.usage._label`, for a row with no model at all) matches only the
    default, which is correct: nobody knows what answered, so only a rate that
    claims to cover everything can price it.
    """
    if not isinstance(model, str) or not model:
        return table.get(DEFAULT_KEY)
    exact = table.get(model)
    if exact is not None:
        return exact
    best, best_len = None, -1
    for key, entry in table.items():
        if key.endswith("*") and model.startswith(key[:-1]) and len(key) > best_len:
            best, best_len = entry, len(key)
    return best if best is not None else table.get(DEFAULT_KEY)


def estimate(entry: dict | None, *, prompt_tokens: int | None,
             completion_tokens: int | None, cache_read_tokens: int | None = None,
             cache_write_tokens: int | None = None) -> float | None:
    """What a call of this shape would cost at these rates, or None.

    None — never 0.0 — whenever the answer would be a guess dressed as a
    figure. Two ways that happens, and both are ordinary:

    - **no entry prices this model.** The table is opt-in and starts empty.
    - **nobody counted the tokens.** An `openai_compatible` endpoint that sends
      no usage block at all is the common case, and it is exactly the case a
      rate cannot rescue: rates times nothing is zero, and a scene of those
      rendered as `$0.00` is the claim this whole feature exists not to make.
      Absent counts are `None` here (the ledger omits them rather than writing
      zero), which is what makes the two distinguishable at all.

    The cache pair is subtracted OUT of the prompt subtotal, not added beside
    it: both counts are slices of `prompt_tokens` (#148), so pricing them
    separately without removing them from the prompt would bill a cached prefix
    twice. Each is subtracted only when the entry names a rate for it —
    otherwise those tokens stay in the prompt subtotal, priced at the prompt
    rate, which is what a table with no cache rates is saying.
    """
    if not entry:
        return None
    if prompt_tokens is None and completion_tokens is None:
        return None
    # Belt and braces with `_entry`'s rule, because `_entry` is not the only
    # door in: this is a public function, and a caller handing it a half-entry
    # would otherwise have its unpriced half silently valued at zero.
    if entry.get(PROMPT) is None or entry.get(COMPLETION) is None:
        return None
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    total = 0.0
    for count, field in ((cache_read_tokens, CACHE_READ),
                         (cache_write_tokens, CACHE_WRITE)):
        rate = entry.get(field)
        if rate is None:
            continue
        # Clamped to what is left of the prompt: the two slices cannot exceed
        # the whole they are slices of, and a hand-edited row (or a provider
        # double-reporting) that says they do must not drive the prompt
        # subtotal negative and *subtract* from the estimate.
        taken = min(max(0, int(count or 0)), prompt)
        prompt -= taken
        total += taken * rate
    total += prompt * entry[PROMPT]
    total += completion * entry[COMPLETION]
    return total / 1000.0
