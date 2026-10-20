"""One rate snapshot and the shared three-column accounting fold."""

from __future__ import annotations

from collections.abc import Iterable

from .. import pricing
from . import _rows


class Rates:
    """The rates a rollup prices against, resolved once per call shape instead
    of per row.

    Two sources, read once at construction: the user's `pricing.json` table
    (#158) and every provider's stated model rates (`pricing.provider_rates`,
    model facts). `pricing.rate_for_call` decides between them -- the one
    precedence function (spec 9.1): the provider's rates for the model asked
    for, then for the model that answered, then the table. A row that names no
    `provider_id` (every row filed before that field) prices by the table
    alone, exactly as it did.

    A rollup asks "what would this have cost?" for every unpriced row it walks,
    and `pricing.rate_for` scans the wildcard entries each time it is asked. A
    heavy month is tens of thousands of rows across a handful of models, so the
    answer is memoized per (provider, model, requested model).

    Read once also means a rollup is drawn against ONE set of rates: a file
    could otherwise be saved mid-scan and half a report would be priced at the
    old rates and half at the new, with nothing saying so.

    **A provider's rates live with the provider.** Deleting a provider deletes
    its facts file (`llm_connections.delete_connection`), so every row it
    served falls back to the table, or to unpriced, on the next rollup: its
    history is re-priced, as editing a rate re-prices it. The id is a reusable
    slug, so a provider created later under the same name inherits those rows
    and prices them at its own rates (`pricing.provider_rates`). Either way
    only `modelled_usd` moves; spend (`cost_usd`) never does, because no rate
    ever touches a row a provider priced.

    `off()` is the no-estimates table, and it is not the same thing as an empty
    one: a caller that must not model anything (the budget, which measures money
    actually owed) says so rather than relying on the user's files being empty.
    """

    def __init__(self, table: dict[str, dict] | None,
                 providers: dict[str, dict[str, dict]] | None = None):
        self.table = table or {}
        self.providers = providers or {}
        self._seen: dict[tuple[str, str, str], dict | None] = {}

    @classmethod
    def current(cls) -> Rates:
        """The rates as they are on disk right now. Never raises -- both readers
        fail-soft (`read_pricing` to `{}`, `provider_rates` per file), so a
        broken file costs the estimates and not the report they sit beside."""
        return cls(pricing.read_pricing(), pricing.provider_rates())

    @classmethod
    def off(cls) -> Rates:
        return cls(None, None)

    def entry(self, model: object, *, provider_id: object = "",
              requested_model: object = "") -> dict | None:
        if not self.table and not self.providers:
            return None
        key = (_rows._text(provider_id), _rows._text(model), _rows._text(requested_model))
        if key not in self._seen:
            self._seen[key] = pricing.rate_for_call(
                self.table, self.providers, provider_id=key[0], model=key[1],
                requested_model=key[2])
        return self._seen[key]

    def estimate(self, row: dict) -> float | None:
        """What this row would have cost at the user's rates, or None.

        None for a row nothing prices AND for a row nobody counted -- see
        `pricing.estimate`, which is where that second case is decided. The
        counts are read raw rather than through `_int` on purpose: `_int` turns
        an absent count into 0, and 0 is exactly the value that must stay
        distinguishable from "not counted" here. The one exception is
        structural rather than a guess: an embed row's completion (`_counts`).

        None, too, for a native decision row (`_modellable`), whatever its
        counts and whatever the rates: every read that models a row comes
        through here, so this one check is the whole of that rule.
        """
        if not _rows._modellable(row):
            return None
        entry = self.entry(row.get("model"), provider_id=row.get("provider_id"),
                           requested_model=row.get("requested_model"))
        if entry is None:
            return None
        prompt, completion = _rows._counts(row)
        return pricing.estimate(
            entry, prompt_tokens=prompt, completion_tokens=completion,
            cache_read_tokens=_rows._count(row.get("cache_read_tokens")),
            cache_write_tokens=_rows._count(row.get("cache_write_tokens")))


def _add(bucket: dict, row: dict, rates: Rates | None = None) -> None:
    """Fold one row into a bucket. Reads defensively — a row is a line from a
    file a human can edit, so a string where a number belongs must cost that
    field rather than the whole report.

    `rates` is the user's per-token table (#158). It only ever touches the rows
    that arrived with no price at all: a modelled figure lands in `modelled_usd`
    and its own count, and the row stops being `unpriced` — it is now priced by
    an estimate, which is a different and weaker claim, made in a different
    column. `None` (the default) models nothing, which is what a caller
    measuring money actually owed passes.
    """
    bucket["calls"] += 1
    # Only `error`, not "anything that is not ok". A cancelled turn is recorded
    # as `aborted` -- the player pressed Cancel or navigated away -- and folding
    # that into an error rate would make a user who reads a lot of half-answers
    # and stops look like a provider that is failing them. Both are still
    # `calls`, because both were generated and both may have been billed.
    if row.get("status") == "error":
        bucket["errors"] += 1
    prompt = _rows._int(row.get("prompt_tokens"))
    completion = _rows._int(row.get("completion_tokens"))
    bucket["prompt_tokens"] += prompt
    bucket["completion_tokens"] += completion
    bucket["total_tokens"] += prompt + completion
    # NOT folded into `total_tokens`, and this is the line to read twice: a
    # cache read is part of the prompt the provider already counted, so adding
    # it would bill a cached prefix to the total twice over (#148). They get
    # their own columns because they answer a question the totals cannot --
    # how much of what was sent was already there, which is the whole measure
    # of whether prompt caching is working.
    bucket["cache_read_tokens"] += _rows._int(row.get("cache_read_tokens"))
    bucket["cache_write_tokens"] += _rows._int(row.get("cache_write_tokens"))
    bucket["duration_ms"] += _rows._int(row.get("duration_ms"))
    # Only once a row carries one: `_ZERO` has no `images`, so the empty
    # summary, the persisted rollup and the shell's money block keep their shape
    # and a bucket written before #377 folds a new row without a KeyError.
    if images := _rows._int(row.get("images")):
        bucket["images"] = bucket.get("images", 0) + images
    # The breakdown counts below are counts, never money, and lazy like
    # `images` for the same reason. Each sits INSIDE a count `_ZERO` already
    # has (the cache-pair precedent): `estimated_token_calls` inside `calls`,
    # the two subscription counts inside `modelled_calls` and `unpriced_calls`.
    # There is no subscription dollar figure -- a fourth one would be one more
    # thing to add to the other three.
    if row.get("tokens_estimated") is True:
        _bump(bucket, "estimated_token_calls")
    subscription = row.get("billing") == "subscription"
    cost = _rows._float(row.get("cost_usd"))
    if cost is None:
        modelled = rates.estimate(row) if rates is not None else None
        if modelled is None:
            _add_unpriced(bucket, row, subscription)
        else:
            bucket["modelled_calls"] += 1
            bucket["modelled_usd"] += modelled
            if subscription:
                _bump(bucket, "modelled_subscription_calls")
        return
    # `cost_basis` alone moves a figure (spec 9.1, ruling 4). `billing` is a
    # label: a provider tagged subscription that reported a BILLED price said
    # it charged, so that figure is spend and counts against a budget.
    bucket["priced_calls"] += 1
    if row.get("cost_basis") == "equivalent":
        bucket["subscription_calls"] += 1
        bucket["estimated_usd"] += cost
    else:
        bucket["cost_usd"] += cost


def fold_rows(rows: Iterable[dict], rates: Rates | None = None) -> dict:
    """`rows` -- a caller's own rows, such as one tool run's -- folded by
    `_add` into one rounded bucket: the three money columns kept apart, each
    with the count of calls priced that way. `rates` models what no provider
    priced; None models nothing."""
    bucket = dict(_rows._ZERO)
    for row in rows:
        if isinstance(row, dict):
            _add(bucket, row, rates)
    return _rows._rounded(bucket)


def _add_unpriced(bucket: dict, row: dict, subscription: bool) -> None:
    """`_add`'s share for a row nothing priced: `unpriced_calls`, and the
    slices of it that say why."""
    bucket["unpriced_calls"] += 1
    if subscription:
        _bump(bucket, "unpriced_subscription_calls")
    # Counted whether or not a rate exists, and read straight off the row
    # rather than from the estimator's verdict: the question is "could ANY
    # rate have priced this". A native decision never could (`_modellable`),
    # whatever its counts, and is counted apart so a view can say so in its
    # own words; otherwise the answer is no whenever a count is missing -- see
    # `pricing.estimate`, which requires both, and `_counts`, which knows an
    # embedding completes nothing.
    if not _rows._modellable(row):
        _bump(bucket, "unpriced_native_calls")
    elif not _rows._metered(row):
        bucket["unmetered_calls"] += 1


def _bump(bucket: dict, key: str) -> None:
    """Add one to a lazy breakdown count -- one `_ZERO` does not carry."""
    bucket[key] = bucket.get(key, 0) + 1
