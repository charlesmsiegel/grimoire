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

**That table is the second of two rate layers.** The first is a model's own
rates, stated on its provider's page and kept in that provider's model facts
(`store.inference.facts`, a `rates` entry per model, read by `provider_rates`).
`rate_for_call` is the one place precedence is decided: a price the provider
reported was never handed to this module at all, so it comes first by
construction; then the model's own rates (under the model asked for, then the
one that answered); then this table (each tier under the model that answered,
then the one asked for). The facts are read straight from their
JSON here rather than through `store.inference.facts`, because `store.usage`
imports this module and `store.inference` reaches `store.usage` again through
`campaigns` -- a runtime cycle the static import guard cannot see.

**Every rollup prices history at current rates.** Editing a rate re-prices
every row it covers, and so does deleting a provider: its facts file goes with
it, so the rows it served price from this table or read unpriced. A provider id
is its name's slug, so one created later under the same name prices those rows
at its own rates. Only `modelled_usd` moves in any of these -- no rate ever
touches a row a provider priced, so spend does not.

**Both base rates are required.** An entry carrying only one is dropped, not
half-applied: pricing a call's prompt at a rate and its completion at nothing
produces a figure that is confidently wrong, and on a call that generated
nothing it produces `$0.00` — the one thing this whole feature exists not to
say. The cache pair stays optional for the opposite reason: those tokens have
a rate either way (see below). **A zero rate is a rate**: both base rates at
zero say the model is free (a local endpoint), and its calls model to `$0`
rather than reading unpriced -- only a rate nobody stated is "unknown".

**What comes out of here is never spend.** An estimate is arithmetic over rates
somebody typed, against token counts a provider reported or this side counted
(`tokens_estimated`, see `store.usage`); it belongs in its own
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
from functools import partial
from pathlib import Path

from . import atomic, llm_connections, paths, statcache

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


class PricingUnreadableError(Exception):
    """The rate table is there but cannot be read or parsed.

    Named with the `Error` suffix ruff's N818 asks for, unlike its older sibling
    `response_presets.PresetUnreadable` — that one predates the rule's selection
    and is grandfathered in the lint baseline; a new one is not.

    Raised only for a caller that has asked for it (`read_pricing(strict=True)`).
    The distinction is the same one `PresetUnreadable` draws
    and exists for the same reason, sharpened by what this file is for: a
    *rollup* that cannot read the table should draw its report with no
    estimates in it, but an *editor* that cannot read the table must not offer
    an empty form, because saving that form replaces rates the user still has
    with the nothing this side managed to read.
    """


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


def entry(value: object) -> dict | None:
    """One table entry, keeping only the fields that are usable rates. Public
    because a model's own rates (`store.inference.facts`) are read by the same
    rule as a table row.

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


def check_entry(value: object) -> dict:
    """The editor's strict form of `entry`: the usable entry, or `ValueError`.

    `entry` drops what it cannot use, which is right for a reader and wrong for
    a writer -- a rate somebody typed that vanishes on the way in is a rate they
    never see again. Three refusals, checked in this order:

    1. not an object, or a key outside `FIELDS` (a misspelt `..._1K` would
       otherwise be dropped, leaving a half entry that is then refused as
       "needs both" with no hint of why);
    2. a present field that is not a non-negative finite number;
    3. both base rates are not there.
    """
    if not isinstance(value, dict):
        raise ValueError("rates must be an object of rate fields")
    unknown = sorted(str(k) for k in value if k not in FIELDS)
    if unknown:
        raise ValueError(f"unknown rate field(s): {', '.join(unknown)}")
    for field in FIELDS:
        if field in value and _rate(value[field]) is None:
            raise ValueError(f"{field} must be a non-negative number")
    kept = entry(value)
    if kept is None:
        raise ValueError("rates need both an input and an output rate")
    return kept


def read_pricing(strict: bool = False) -> dict[str, dict]:
    """The rate table, normalized. `{}` when there is none.

    **Fail-soft by default, and strict on request.** A rollup's whole job is to
    draw a report, so a hand-edited comma in `pricing.json` must cost the
    estimates rather than the page that would have shown the real spend beside
    them; a dropped entry is visible anyway, because its model reads "unpriced"
    again, which is what it read before anyone typed a rate.

    `strict=True` raises `PricingUnreadableError` instead, and the editor is the
    caller that needs it: a malformed file read as `{}` becomes an empty
    editable form, and one Save then replaces the user's real rates with the
    nothing this side could parse. Absent is still `{}` in both modes — there
    is nothing to lose in a file that does not exist.
    """
    path = pricing_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if strict:
            raise PricingUnreadableError(str(exc)) from exc
        return {}
    if not isinstance(data, dict):
        if strict:
            raise PricingUnreadableError("pricing.json is not an object")
        return {}
    table: dict[str, dict] = {}
    lost = 0
    for key, value in data.items():
        if not isinstance(key, str) or len(table) >= MAX_ENTRIES:
            lost += 1
            continue
        usable = entry(value)
        if usable is not None:
            table[key] = usable
        else:
            lost += 1
    # Dropping an entry is fine for a rollup -- that model reads "unpriced"
    # again, which is what it read before anyone typed a rate. It is NOT fine
    # for the editor: it saves by whole-table replacement, so a form filled in
    # from a shortened read deletes every entry that did not survive the read,
    # permanently, and reports a successful save. One hand-edited rate with a
    # missing half is enough to trigger it. Strict callers get the refusal.
    if lost and strict:
        raise PricingUnreadableError(
            f"{lost} entr{'y' if lost == 1 else 'ies'} in pricing.json could not be "
            f"read; refusing to hand back a table that is missing them")
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
        usable = entry(value)
        if usable is not None:
            kept[key] = usable
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
    return _table_rate(table, (model,))


def _prefixed(table: dict[str, dict], model: str) -> dict | None:
    """The longest ``prefix*`` entry `model` matches, or None."""
    best, best_len = None, -1
    for key, entry in table.items():
        if key.endswith("*") and model.startswith(key[:-1]) and len(key) > best_len:
            best, best_len = entry, len(key)
    return best


def _table_rate(table: dict[str, dict], names: tuple[object, ...]) -> dict | None:
    """`rate_for`'s three tiers over several names: every name's exact entry,
    then every name's longest ``prefix*``, then ``""`` -- so a narrower tier
    under a later name beats a wider one under an earlier name, and names
    within a tier are tried in the order given."""
    usable = [n for n in names if isinstance(n, str) and n]
    for name in usable:
        exact = table.get(name)
        if exact is not None:
            return exact
    for name in usable:
        best = _prefixed(table, name)
        if best is not None:
            return best
    return table.get(DEFAULT_KEY)


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
      zero), which is what makes the two distinguishable at all. **Both** counts
      are required, for the reason both rates are: half a call counted is half a
      call priced, and the other half valued at nothing.

    The cache pair is subtracted OUT of the prompt subtotal, not added beside
    it: both counts are slices of `prompt_tokens` (#148), so pricing them
    separately without removing them from the prompt would bill a cached prefix
    twice. Each is subtracted only when the entry names a rate for it —
    otherwise those tokens stay in the prompt subtotal, priced at the prompt
    rate, which is what a table with no cache rates is saying.
    """
    if not entry:
        return None
    # BOTH counts, not either — the exact mirror of the rate rule below, and
    # missing it left the same hole one level down. `from_openai_chunk` reads
    # `prompt_tokens` and `completion_tokens` independently, so a usage block
    # carrying only one is a shape a real provider can send; `int(None or 0)`
    # would then price the uncounted side at zero, mark the call modelled, take
    # it out of `unpriced_calls`, and render `$0.00` when the counted side is
    # empty. Nobody counted that half, so nobody can price the call.
    if prompt_tokens is None or completion_tokens is None:
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
    total /= 1000.0
    # A rate this module accepted as finite can still overflow once multiplied
    # by a real token count -- `1e308` times a few thousand is `inf`. That value
    # would reach every rollup response and `json.dumps` cannot write it, so one
    # absurd rate would 500 every cost endpoint until the file was edited by
    # hand. An estimate that overflowed is not an estimate: None, like every
    # other answer this function cannot compute.
    return total if total == total and total != float("inf") else None


#: `provider_rates`' memo, one entry per facts file signature. A pool of its own
#: like `usage._UNPRICED_POOL`: the shared `statcache` FIFO is the one the sync
#: sweeps fill with every entity and card hash. The rail reads this on every
#: navigation, so a stable file must cost a stat and not a parse. Each value is
#: a handful of model names; the stale signatures an edit leaves behind cost
#: nothing worth evicting early.
_PROVIDER_POOL: dict = {}
_PROVIDER_ENTRIES = 256


def _read_provider_file(path: Path) -> dict[str, dict]:
    """One facts file's usable rates as `{model: entry}`; `{}` for anything that
    cannot be read. Raises nothing, so the memo stores the empty answer too."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    rates: dict[str, dict] = {}
    for model, facts in raw.items():
        if not isinstance(model, str) or not isinstance(facts, dict):
            continue
        usable = entry(facts.get("rates"))
        if usable is not None:
            rates[model] = usable
    return rates


def provider_rates() -> dict[str, dict[str, dict]]:
    """Every provider's stated model rates, as `{provider_id: {model: entry}}`.

    Only usable entries (`entry`) appear, and a provider with none is omitted.
    **Never raises**: a facts file a sync or a hand mangled into anything
    contributes nothing, so its models read unpriced and fall back to the
    `pricing.json` table -- never `$0`, and never a failed rollup.

    Each file is remembered on its stat signature (`statcache`, in a pool of
    its own), so this is cheap on a path that runs every navigation. The answer
    is a fresh copy: a caller that edits it cannot edit the memo.

    **Deleting a provider re-prices its history.** Its facts file goes with it
    (`llm_connections.delete_connection`), so its rates leave this answer and
    every row it served prices from the table, or reads unpriced, on the next
    rollup (`usage.Rates`; the rail's aggregate re-prices on its fingerprint).
    A provider id is its name's slug, and a slug is reusable: a provider
    created later under the deleted one's name gets the same id, and its rates
    then price the old provider's rows. The ledger has nothing else to tell the
    two apart. Only `modelled_usd` is affected -- spend never moves, because no
    rate touches a row its provider priced.

    This reads the facts file's JSON shape itself (`{model: {"rates": ...}}`)
    rather than importing `store.inference.facts`, which writes it: `usage`
    imports this module, and `inference` reaches `usage` again through
    `campaigns`, so that import would be a runtime cycle the static guard
    cannot see. `tests/test_model_rates.py` writes through `facts.state` and
    reads back through here, which holds the two to one schema.
    """
    out: dict[str, dict[str, dict]] = {}
    try:
        files = llm_connections.facts_files()
    except Exception:   # noqa: BLE001 -- bookkeeping never fails a call
        return out
    for provider_id, path in files.items():
        try:
            sig = statcache.signature(path)
            rates = statcache.memo("pricing:provider", sig, partial(_read_provider_file, path),
                                   pool=_PROVIDER_POOL, max_entries=_PROVIDER_ENTRIES)
        except Exception:   # noqa: BLE001 -- one file never costs the rest
            continue
        if rates:
            out[provider_id] = {m: dict(e) for m, e in rates.items()}
    return out


def rate_for_call(table: dict[str, dict], providers: dict[str, dict[str, dict]], *,
                  provider_id: str = "", model: str = "",
                  requested_model: str = "") -> dict | None:
    """The entry that prices one call -- the one place precedence is decided.

    In order:

    1. the provider's rates for the model that was **asked for**
       (`providers[provider_id][requested_model]`), when both are set -- a
       provider may answer under a dated snapshot of what was requested, and
       the rates were stated under the request;
    2. the provider's rates for the model that **answered**;
    3. the table, by `rate_for`'s tiers -- an exact entry, then the longest
       `prefix*`, then `""` -- each tier tried under the model that
       **answered** and then the model **asked for**.

    A model's own rates are exact and take no wildcards. The table matches the
    *recorded* model first, so every existing table prices exactly what it
    priced before (no row filed before `requested_model` carries one). It also
    matches the name asked for, because that is the only name configuration
    knows: the Housekeeping chore (`inference.in_use.unpriced`) judges a
    configured model through this same function, so an entry under the name
    the user configured clears that chore AND prices the calls that answered
    as a dated snapshot of it -- the two chores cannot disagree about a model.
    A row that names no provider (every row filed before providers were
    recorded) has only the table.
    """
    mine = providers.get(provider_id) if provider_id else None
    if mine:
        for name in (requested_model, model):
            if name and name in mine:
                return mine[name]
    return _table_rate(table, (model, requested_model))
