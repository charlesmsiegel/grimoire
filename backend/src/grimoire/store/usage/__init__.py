"""What each LLM call spent, and what a window of them adds up to (#152).

Nothing used to be persisted about a generation once its text landed: how many
tokens it took, what it cost, how long it ran, whether it failed. The local
tokenizer in ``store.context`` counts what grimoire *composed*, which is an
estimate of the prompt and says nothing at all about the reply or the bill. This
module records what the provider reported, per call -- and, where it reported
no counts, what this side counted, flagged ``tokens_estimated``.

Layout, home-scoped rather than campaign-scoped::

    <home>/usage/YYYY-MM.jsonl      one JSON object per line, append-only

Home-scoped because not every call belongs to a campaign — a tagline is
generated against a world, and a connection test against nothing at all — and a
per-campaign ledger would either drop those or invent a campaign for them.
Month-per-file because the only query this serves is "the last N days", so a
30-day window reads at most two files however long the library has been played.

**Rollups are computed on read.** There is no index and no database: ``summary``
scans the one or two month files in the window and buckets in memory. A heavy
day is a few hundred rows; a heavy year is tens of thousands, which is
milliseconds to parse and keeps the store what the rest of grimoire promises —
files a human can open and read.

The one exception is ``store.usage_rollup``, and it is an exception to the
*cost* rather than to the rule: it keeps a byte bookmark into each month file
so an all-time total can be asked for on every navigation without re-reading
the library's whole history. It is derived, deletable and never authoritative —
these files are still the ledger — and it folds rows in through this module's
own ``_add``, so it cannot come to a different opinion about what a call cost.

Pricing has three sources, and the difference between them is the point:

- ``cost_usd`` with ``cost_basis: "billed"`` is money an account was charged,
  reported by the provider (OpenRouter returns ``usage.cost`` in credits, which
  are USD).
- ``cost_usd`` with ``cost_basis: "equivalent"`` is what the call *would* have
  cost at API rates but did not, because it billed against a subscription
  instead — the Claude Agent path, whose auth is the host's Claude Code login
  (see ``claude_agent``). Summing that into a spend figure would tell someone
  they had spent money they had not.

  So the two never share a total: buckets carry ``cost_usd`` (billed only) and
  ``estimated_usd`` (equivalent only).
- Neither, when a provider reports no price at all — every ``openai_compatible``
  endpoint today, and any OpenRouter reply whose usage block came without a
  cost. The field is then **absent**, not zero, which is what lets a re-pricing
  pass over the ledger tell "free" from "unknown".

  ``store.pricing`` (#158) is that pass, over two rate layers the user
  states: a model's own rates, kept in its provider's model facts and edited
  on the provider's page, and the ``pricing.json`` table. A reported price
  always wins -- no rate touches a row that has one -- and between the two
  layers ``pricing.rate_for_call`` decides, the one precedence function: the
  model's own rates, then the table. A rollup prices those rows at what it
  picks and reports the figure as ``modelled_usd`` — a third column, never
  added to the other two, because it is arithmetic this side did rather than
  a number a provider sent. A subscription row nobody priced is modelled the
  same way and lands there too, counted again in
  ``modelled_subscription_calls``. A zero rate is a price: a model stated free
  models to zero rather than reading unpriced.
  ``unpriced_calls`` then counts only what is left: calls with no reported
  price and no rate to model one, which is what makes a total's incompleteness
  visible rather than quietly understated.

  The counts a rate multiplies may be the provider's or this side's (the
  estimate flag, below); ``estimated_token_calls`` says how many rows in a
  bucket rest on counts this side made.

The token counts follow the same absent-not-zero rule, so a row never claims a
call used no tokens when nobody counted them. A bucket's token total is
therefore a floor whenever ``unmetered_calls`` is non-zero -- calls that
arrived with neither a price nor the counts a rate would need -- and a total
that rests on counts this side estimated says so through
``estimated_token_calls``.

**A count the provider did not report is estimated, and the row says so**
(spec 9.1). The facade (``llm._resilient``) counts it locally with
``store.tokens``, on a worker thread, for an attempt whose stream ended on its
own -- never one the caller broke out of, closed or that failed, since nobody
knows what such a call billed. It fills only the missing count, and the row
carries ``tokens_estimated: true``. Reasoning text counts as completion. An
``embed`` row never gets an estimated completion: an embedding generates
nothing. Images are not estimated: there is no portable per-image count, and
the row's ``images`` already says they rode along. ``Meter.done`` counts
nothing and loads no encoder; it reads what the facade wrote.

The facade is not the only counter. An ``embed`` call that returned without a
prompt count is estimated by ``inference.embed.estimate_prompt``
(``tokens.count_if_loaded``: an encoder that has already loaded, else the
characters/4 heuristic, and never a load), synchronously on the caller's
thread with no ``llm.COUNT_TIMEOUT_S``. Every caller is off the event loop: the
embed operation is reached from threadpool workers, and the model test's probe
hands its count to a worker.

**The cache pair is a breakdown, not a component** (#148).
``cache_read_tokens`` and ``cache_write_tokens`` are slices of
``prompt_tokens`` — the part of the prompt that was already cached, and the
part this call paid to put there — so they are summed into columns of their own
and deliberately left out of ``total_tokens``. Folding them in would count a
cached prefix twice. They are what turns "this month cost X" into "this month
cost X, and caching is doing Y about it": a read is billed at a fraction of a
fresh token, a write at a small premium, and without the split a rollup cannot
tell a library whose prompts cache well from one whose prompts thrash. Absent
whenever a provider says nothing about caching, which includes every
``openai_compatible`` endpoint that reports no usage at all.

Retention is deliberately none. A row is ~250 bytes, so a year of heavy play is
single-digit megabytes, and the month files are trivially deletable by hand —
which is a better answer than a pruner that silently destroys the history
somebody enabled this feature to have.
"""

from __future__ import annotations

import calendar as calendar
import json as json
import re as re
import time as time
from collections.abc import Iterable as Iterable
from datetime import date as date
from datetime import timedelta as timedelta
from functools import partial as partial
from pathlib import Path as Path

from ... import decisions as decisions
from .. import atomic as atomic
from .. import errors as errors
from .. import paths as paths
from .. import pricing as pricing
from .. import statcache as statcache
from ._budget import (
    MONTHLY as MONTHLY,
)
from ._budget import (
    OFF as OFF,
)
from ._budget import (
    OK as OK,
)
from ._budget import (
    OVER as OVER,
)
from ._budget import (
    PERIODS as PERIODS,
)
from ._budget import (
    TOTAL as TOTAL,
)
from ._budget import (
    WARN as WARN,
)
from ._budget import (
    WARN_FRACTION as WARN_FRACTION,
)
from ._budget import (
    budget as budget,
)
from ._budget import (
    normalize_limit as normalize_limit,
)
from ._budget import (
    normalize_period as normalize_period,
)
from ._budget import (
    period_window as period_window,
)
from ._clock import (
    _DEFAULT_DAYS as _DEFAULT_DAYS,
)
from ._clock import (
    _SESSION_START as _SESSION_START,
)
from ._clock import (
    MAX_DAYS as MAX_DAYS,
)
from ._clock import (
    _now as _now,
)
from ._clock import (
    _scan_since as _scan_since,
)
from ._clock import (
    _today as _today,
)
from ._clock import (
    _valid_day as _valid_day,
)
from ._clock import (
    month_bounds as month_bounds,
)
from ._folding import (
    Rates as Rates,
)
from ._folding import (
    _add as _add,
)
from ._folding import (
    _add_unpriced as _add_unpriced,
)
from ._folding import (
    _bump as _bump,
)
from ._folding import (
    fold_rows as fold_rows,
)
from ._ledger import (
    _MONTH as _MONTH,
)
from ._ledger import (
    _append as _append,
)
from ._ledger import (
    _differs as _differs,
)
from ._ledger import (
    _estimated as _estimated,
)
from ._ledger import (
    _is_call as _is_call,
)
from ._ledger import (
    _positive as _positive,
)
from ._ledger import (
    _read_rows as _read_rows,
)
from ._ledger import (
    _row as _row,
)
from ._ledger import (
    _run_fields as _run_fields,
)
from ._ledger import (
    _window_files as _window_files,
)
from ._ledger import (
    calls as calls,
)
from ._ledger import (
    ledger_dir as ledger_dir,
)
from ._ledger import (
    month_path as month_path,
)
from ._ledger import (
    record as record,
)
from ._ledger import (
    repoint_scenes as repoint_scenes,
)
from ._meter import (
    Meter as Meter,
)
from ._meter import (
    meter as meter,
)
from ._reports import (
    _MAX_RENAMES as _MAX_RENAMES,
)
from ._reports import (
    CAMPAIGN_SCENES as CAMPAIGN_SCENES,
)
from ._reports import (
    NO_SCENE as NO_SCENE,
)
from ._reports import (
    REROLL_TASKS as REROLL_TASKS,
)
from ._reports import (
    SCENE_ORDERS as SCENE_ORDERS,
)
from ._reports import (
    SCENE_TURNS as SCENE_TURNS,
)
from ._reports import (
    _projected as _projected,
)
from ._reports import (
    _rename_trail as _rename_trail,
)
from ._reports import (
    _scene_now as _scene_now,
)
from ._reports import (
    _sort_scenes as _sort_scenes,
)
from ._reports import (
    _turn as _turn,
)
from ._reports import (
    available_months as available_months,
)
from ._reports import (
    campaign_scenes as campaign_scenes,
)
from ._reports import (
    lifetime_since as lifetime_since,
)
from ._reports import (
    monthly_campaigns as monthly_campaigns,
)
from ._reports import (
    scene_usage as scene_usage,
)
from ._reports import (
    summary as summary,
)
from ._rows import (
    _CENTS as _CENTS,
)
from ._rows import (
    _ZERO as _ZERO,
)
from ._rows import (
    EMBED_OPERATION as EMBED_OPERATION,
)
from ._rows import (
    ENDED_KEY as ENDED_KEY,
)
from ._rows import (
    ESTIMATE_KEY as ESTIMATE_KEY,
)
from ._rows import (
    ESTIMATED as ESTIMATED,
)

# Explicit aliases retain the former module surface; shared values have one owner.
from ._rows import (
    KIND_LLM as KIND_LLM,
)
from ._rows import (
    KIND_RENAME as KIND_RENAME,
)
from ._rows import (
    NOT_A_FAILURE as NOT_A_FAILURE,
)
from ._rows import (
    PRE_SEND_KEYS as PRE_SEND_KEYS,
)
from ._rows import (
    REASONING_KEY as REASONING_KEY,
)
from ._rows import (
    RUN_KEY as RUN_KEY,
)
from ._rows import (
    TOOL_CALLS_KEY as TOOL_CALLS_KEY,
)
from ._rows import (
    _completion_count as _completion_count,
)
from ._rows import (
    _count as _count,
)
from ._rows import (
    _counts as _counts,
)
from ._rows import (
    _float as _float,
)
from ._rows import (
    _int as _int,
)
from ._rows import (
    _label as _label,
)
from ._rows import (
    _metered as _metered,
)
from ._rows import (
    _modellable as _modellable,
)
from ._rows import (
    _money as _money,
)
from ._rows import (
    _post as _post,
)
from ._rows import (
    _ranked as _ranked,
)
from ._rows import (
    _rounded as _rounded,
)
from ._rows import (
    _text as _text,
)
from ._rows import (
    sent as sent,
)
from ._unpriced import (
    _UNPRICED_ENTRIES as _UNPRICED_ENTRIES,
)
from ._unpriced import (
    _UNPRICED_POOL as _UNPRICED_POOL,
)
from ._unpriced import (
    _month_unpriced as _month_unpriced,
)
from ._unpriced import (
    unpriced_models as unpriced_models,
)

# Preserve deferred module annotations; values and their types have one owner.
__annotations__ = {
    '_UNPRICED_POOL': 'dict',
}

__all__ = [
    'CAMPAIGN_SCENES',
    'EMBED_OPERATION',
    'ENDED_KEY',
    'ESTIMATED',
    'ESTIMATE_KEY',
    'KIND_LLM',
    'KIND_RENAME',
    'MAX_DAYS',
    'MONTHLY',
    'NOT_A_FAILURE',
    'NO_SCENE',
    'OFF',
    'OK',
    'OVER',
    'PERIODS',
    'PRE_SEND_KEYS',
    'REASONING_KEY',
    'REROLL_TASKS',
    'RUN_KEY',
    'SCENE_ORDERS',
    'SCENE_TURNS',
    'TOOL_CALLS_KEY',
    'TOTAL',
    'WARN',
    'WARN_FRACTION',
    'Iterable',
    'Meter',
    'Path',
    'Rates',
    'annotations',
    'atomic',
    'available_months',
    'budget',
    'calendar',
    'calls',
    'campaign_scenes',
    'date',
    'decisions',
    'errors',
    'fold_rows',
    'json',
    'ledger_dir',
    'lifetime_since',
    'meter',
    'month_bounds',
    'month_path',
    'monthly_campaigns',
    'normalize_limit',
    'normalize_period',
    'partial',
    'paths',
    'period_window',
    'pricing',
    're',
    'record',
    'repoint_scenes',
    'scene_usage',
    'sent',
    'statcache',
    'summary',
    'time',
    'timedelta',
    'unpriced_models',
]
