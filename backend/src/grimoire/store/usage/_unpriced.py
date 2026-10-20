"""The single per-month memo of unpriced ledger model occurrences."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

from .. import pricing, statcache
from . import _ledger, _rows

#: `unpriced_models`' memo: per month file, how many calls of each call shape
#: a rate COULD price. A pool of its own rather than the shared `statcache`
#: FIFO, which the sync sweeps fill with every entity and card hash. The
#: budget covers the two months read plus the signatures the current month
#: leaves behind as it is appended to -- each is a handful of model names, so
#: the stale ones cost nothing worth evicting early.
_UNPRICED_POOL: dict = {}
_UNPRICED_ENTRIES = 64


def unpriced_models(months: int = 2) -> list[dict]:
    """Models whose calls could be priced but are not, newest months first.

    The gap this closes: a pricing table is opt-in and matched by model string,
    so an entry whose key does not match what the ledger actually recorded
    prices nothing, silently. Nothing anywhere said which strings the ledger
    holds, so the only way to notice was to compare a rollup against a table by
    eye and conclude that the feature was broken.

    One entry per call shape, `{"model", "facts_model", "provider_id",
    "calls"}`: `model` is the recorded string, which a `pricing.json` key must
    match; `facts_model` is the key a model's own rates are stated under on
    `provider_id` (the model that was asked for, when the answer named a dated
    snapshot of it). One model served by two providers is two entries, because
    each would be priced by its own provider's rates. A row that names no
    provider has `provider_id` "" and only the table can price it.

    Only calls that a rate *could* have priced are counted -- no `cost_usd`, and
    both token counts present (an embed row's completion is 0, `_counts`). A
    call nobody metered cannot be rescued by a rate (rates times nothing is
    zero), so listing its model here would send the reader to write an entry
    that changes nothing. Nor is a native decision row, which no rate models
    (`_modellable`).

    Bounded to the newest `months` ledger files rather than the whole history:
    this backs a chore and a hint, both opened casually, and `lifetime_since`
    is the read reserved for the all-time view. Two months is enough to name
    the models in current use, which is the question being asked.

    **Counted per file and remembered; judged fresh every time.** The chore is
    asked on every shell read, and re-parsing two month files to answer it cost
    more than the rest of that read. A month's counts are memoized on the
    file's stat signature, so a past month is parsed once per process and the
    current one whenever it has actually grown -- `statcache` refuses to keep
    anything inside its racy window, so a row appended a moment ago is never
    missed. The rates are deliberately applied AFTER the memo, through
    `pricing.rate_for_call` as every rollup applies them: they are the user's
    to edit at any moment, and a memo holding verdicts would keep listing a
    model for as long as its month file happened not to change.
    """
    root = _ledger.ledger_dir()
    try:
        files = sorted((p for p in root.iterdir() if p.suffix == ".jsonl"),
                       reverse=True)[:max(1, months)]
    except OSError:
        return []
    table = pricing.read_pricing()
    providers = pricing.provider_rates()
    counts: dict[tuple[str, str, str], int] = {}
    for path in files:
        sig = statcache.signature(path)
        if sig is None:
            continue
        try:
            month = statcache.memo("usage:unpriced", sig, partial(_month_unpriced, path),
                                   pool=_UNPRICED_POOL, max_entries=_UNPRICED_ENTRIES)
        except OSError:
            # Raised by the compute and so never stored: a month a sync client
            # has locked costs this read, not every read until the file next
            # changes -- which for a past month is never.
            continue
        for shape, n in month:
            counts[shape] = counts.get(shape, 0) + n
    return [{"model": model, "facts_model": facts_model, "provider_id": provider_id,
             "calls": n}
            for (provider_id, facts_model, model), n
            in sorted(counts.items(), key=lambda kv: -kv[1])
            if pricing.rate_for_call(table, providers, provider_id=provider_id,
                                     model=model, requested_model=facts_model) is None]


def _month_unpriced(path: Path) -> tuple[tuple[tuple[str, str, str], int], ...]:
    """One month file's rate-priceable calls, as
    ``((provider_id, requested_model or model, model), calls)`` pairs in order
    of first appearance -- the order `unpriced_models` breaks ties in.

    A tuple, because the value lives in a memo other requests read. Raises
    OSError rather than answering empty, so a file that could not be opened is
    never remembered as one with nothing in it. Bytes that are not UTF-8 end
    the read where the decoder meets them, the tolerance `_read_rows` has: a
    list drawn short rather than a chore that raises."""
    counts: dict[tuple[str, str, str], int] = {}
    with open(path, encoding="utf-8") as f:
        try:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or _rows._float(row.get("cost_usd")) is not None:
                    continue
                # A native decision row is never modelled, so naming its
                # model here would send the reader to set a rate that prices
                # nothing (`_modellable`).
                if not _rows._metered(row) or not _rows._modellable(row):
                    continue
                model = str(row.get("model") or "")
                if model:
                    shape = (_rows._text(row.get("provider_id")),
                             _rows._text(row.get("requested_model")) or model, model)
                    counts[shape] = counts.get(shape, 0) + 1
        except ValueError:      # UnicodeDecodeError, surfacing mid-file
            pass
    return tuple(counts.items())
