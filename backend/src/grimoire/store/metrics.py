"""How fast the app has been, how often it failed, and which way both are
moving (#154).

Every ingredient was already being written down. `store.usage` has recorded a
`duration_ms` and a `status` per LLM call since #152, and `store.errors` now
records failures per module (#156). What was missing is the *shape* of those
numbers: a ledger can say a month of chat calls took 41 minutes in total, and
that is the one summary of a latency distribution that tells you nothing. A
mean hides the tail, and the tail is the entire experience -- "usually four
seconds, but one turn in ten takes twenty" is a p90 fact that no total or
average can express.

So this module takes percentiles, and it takes them **on read**, out of the
same rows: no index, no second ledger, no aggregate maintained at write time.
That is the call `store.usage` already made for its rollups, and it holds here
for the same reason -- a heavy day is a few hundred rows, a heavy year tens of
thousands, and a full scan is milliseconds against a file a human can still
open in an editor. A maintained percentile would need a sketch (t-digest and
friends) whose whole value is not having to keep the raw data, in a store whose
whole value is keeping it.

**Where each number comes from, because two of them look like the same
number.** A failed LLM call is in the usage ledger (as `status: "error"`) and
in the error store (as a row `Meter.done` recorded). Both are used, for
different questions:

- `by_task` / `by_model` / `by_day` count errors from **the ledger**, because
  an error *rate* needs the calls that succeeded as its denominator, and only
  the ledger has those.
- `errors` comes from **the error store**, because "what has been going wrong"
  includes the failures that were never a call: a dossier that came back empty,
  a provider that was never configured, anything the logging bridge caught.

The two totals therefore differ, and are meant to. Each is labelled with its
source rather than reconciled into one number that would answer neither
question.

Latency is the **client-side** duration -- `Meter`'s own monotonic clock around
the whole call, retries included (`store.usage.Meter`). It is not the
provider's service time and must not be read as one: it contains queueing, the
network, and any `llm._resilient` backoff between attempts. That is the right
measurement for this app, because it is what the person waiting experienced,
but it means a p99 spike can be a provider's `Retry-After` being honoured
exactly as asked.
"""

from __future__ import annotations

import random
import time

from . import errors, logs, usage

#: The percentiles reported for every distribution. p99 is included knowing
#: full well that it is noise under ~100 calls -- `count` is beside every one
#: of them so a reader can see that, which is a better answer than hiding the
#: column until some threshold and leaving them wondering where it went.
PERCENTILES = (50, 90, 99)

DEFAULT_DAYS = 30

#: Values one distribution keeps. A percentile needs the values, not a sum, so
#: this is the one place a scan holds something per call -- capped so a library
#: with a million calls in the window cannot turn a dashboard request into a
#: memory event. Past the cap it becomes a uniform random sample of the window
#: (`_Series.add`), and `sampled` says so.
MAX_SAMPLES = 50_000

#: The sampler's seed. Fixed, so the same ledger produces the same percentiles
#: twice: a dashboard whose numbers wobble on refresh with nothing underneath
#: having changed is a dashboard nobody trusts. It costs nothing -- the sample
#: is uniform either way, and this is not a security decision.
SAMPLE_SEED = 20260821

#: Distinct labels a breakdown will open a bucket for. `MAX_SAMPLES` bounds
#: what ONE bucket holds; this bounds how many there are, and both are needed:
#: `task` and `model` are strings out of a file a human can edit, and a ledger
#: whose model column had drifted (a per-request id landing where a model name
#: belongs, say) would open a `_Series` -- with its own `random.Random` -- for
#: every row in the window. Two hundred is far above any real library: this app
#: has a dozen tasks and a handful of models, and the frontend ranks and shows
#: the top few.
#:
#: Past the cap, further new labels are counted under `OVERFLOW`, so a capped
#: breakdown holds `MAX_BUCKETS` named buckets plus that one and its totals
#: still add up to the calls that were read. The numbers stay true and only the
#: naming gets coarse, which is the right way round for a bound nobody with a
#: real ledger will ever reach.
MAX_BUCKETS = 200

#: Where labels past `MAX_BUCKETS` are counted. A bucket genuinely called
#: "other" would merge with it rather than be shadowed by it, which keeps the
#: arithmetic honest and costs only the label -- the trade the `""` key on
#: `totals` avoids because it can, and this cannot.
OVERFLOW = "other"


class _Series:
    """One bucket's durations, plus the counts that go beside them.

    Past `MAX_SAMPLES` this switches to **reservoir sampling** (Vitter's R):
    each later value replaces a uniformly-chosen slot with probability
    `MAX_SAMPLES/n`, which leaves the kept values a uniform sample of
    everything seen. The obvious alternatives are both wrong in ways that
    matter here -- keeping the first N reports the percentiles of whenever the
    window started, which for a 30-day window is a month-old answer presented
    as today's, and always overwriting one fixed slot (which is what this did
    first) keeps the first N with a single rotating hole, i.e. the same stale
    answer wearing a `sampled` flag.

    `min` and `max` are tracked exactly, outside the reservoir. They are the
    two numbers a sample is least likely to hold and the two a reader is most
    likely to act on -- "Slowest" understating the worst call in the window
    would be a reading that quietly excuses the thing being investigated.
    """

    def __init__(self) -> None:
        self.values: list[int] = []
        self.calls = 0
        self.errors = 0
        self.sampled = False
        self.low: int | None = None
        self.high: int | None = None
        self._rng = random.Random(SAMPLE_SEED)

    def add(self, ms: int, failed: bool) -> None:
        self.calls += 1
        if failed:
            self.errors += 1
        self.low = ms if self.low is None else min(self.low, ms)
        self.high = ms if self.high is None else max(self.high, ms)
        if len(self.values) < MAX_SAMPLES:
            self.values.append(ms)
            return
        self.sampled = True
        # `randrange(self.calls)` over the count SEEN, not over the reservoir:
        # that is what makes the keep probability MAX_SAMPLES/n rather than 1.
        slot = self._rng.randrange(self.calls)
        if slot < MAX_SAMPLES:
            self.values[slot] = ms

    def report(self, key: str) -> dict:
        out = {"key": key, "calls": self.calls, "errors": self.errors,
               "error_rate": round(self.errors / self.calls, 4) if self.calls else 0.0,
               "sampled": self.sampled}
        out.update(percentiles(self.values))
        # The exact ends win over the sample's, always -- see the class
        # docstring. Equal when nothing was sampled, so this is a no-op on
        # every real library.
        # `is not None`, not `or`: a bucket whose fastest call genuinely took
        # 0ms is not a bucket with no data, and `or` cannot tell them apart.
        out["min"] = self.low if self.low is not None else 0
        out["max"] = self.high if self.high is not None else 0
        return out


def percentile(values: list[int], q: float) -> int:
    """The ``q``th percentile of ``values``, in whatever unit they are in.

    Linear interpolation between the two order statistics around rank
    ``(n-1)·q``: the "inclusive" method, which is what `statistics.quantiles`
    computes and what every chart library assumes. Implemented here rather than
    called, for two reasons that are both about the edges this actually sees --
    `quantiles` raises on a single sample, and a library with one call in the
    window is the normal state of a fresh install; and it returns all 99 cut
    points, so asking it for three means computing ninety-six that get thrown
    away on every request.

    ``values`` must be sorted. Rounded to a whole millisecond, because that is
    the resolution the ledger stores and a p90 of 4210.4 is false precision --
    and rounded HALF UP rather than through `round`, whose banker's rounding
    turns a p50 of exactly 50.5 into 50 and the same distribution shifted by
    one into 52. Durations are non-negative, so `int(x + 0.5)` is the whole of
    it, and a reader comparing the number against a hand-worked one gets the
    answer they expected.
    """
    if not values:
        return 0
    if len(values) == 1:
        return int(values[0])
    rank = (len(values) - 1) * max(0.0, min(float(q), 100.0)) / 100.0
    low = int(rank)
    high = min(low + 1, len(values) - 1)
    exact = values[low] + (values[high] - values[low]) * (rank - low)
    return int(exact + 0.5)


def percentiles(values: list[int]) -> dict:
    """`PERCENTILES` of ``values``, keyed ``p50``/``p90``/``p99``, plus the two
    ends. Sorts a copy: the caller's list is a bucket that is still being
    filled elsewhere in the same pass."""
    ordered = sorted(values)
    out = {f"p{q}": percentile(ordered, q) for q in PERCENTILES}
    out["min"] = ordered[0] if ordered else 0
    out["max"] = ordered[-1] if ordered else 0
    return out


def performance(days: int = DEFAULT_DAYS, campaign: str = "") -> dict:
    """Latency, failures and their trend over the last ``days`` (UTC, inclusive
    of today).

    ``days`` is clamped rather than rejected -- this backs a dashboard control,
    and a silly number there should draw a chart, not a 422; the same call
    `usage.summary` makes.

    ``by_day`` is the trend: one bucket per day, with that day's own
    percentiles rather than a running average, so a slow Tuesday is visible as
    a slow Tuesday. Chronological, unlike the two ranked breakdowns, because a
    time series is read left to right.
    """
    span = max(1, min(int(days or 1), logs.MAX_DAYS))
    # Resolved ONCE, here, and handed to both readers. Each used to work the
    # window out from its own clock -- `usage.calls` from `_today`,
    # `errors.summary` from `logs.span`, and the reported pair from
    # `logs.window` -- three calls to `time.gmtime` in one report. A report
    # generated across UTC midnight therefore quoted a latency distribution
    # from one window beside an error count from another, with the header
    # naming a third.
    since, until = logs.span("", "", span)
    overall = _Series()
    by_task: dict[str, _Series] = {}
    by_model: dict[str, _Series] = {}
    by_day: dict[str, _Series] = {}
    for row in usage.calls(span, campaign, since=since, until=until):
        ms = _int(row.get("duration_ms"))
        failed = row.get("status") == "error"
        overall.add(ms, failed)
        _label_series(by_task, row.get("task"), MAX_BUCKETS).add(ms, failed)
        _label_series(by_model, row.get("model"), MAX_BUCKETS).add(ms, failed)
        _label_series(by_day, str(row.get("ts", ""))[:10], 0).add(ms, failed)
    return {
        "days": span, "since": since, "until": until, "campaign": campaign,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "percentiles": list(PERCENTILES),
        # `key` is "" rather than a label: this bucket is every call, and
        # naming it "all" would collide with a task genuinely called that.
        "totals": overall.report(""),
        "by_task": _ranked(by_task),
        "by_model": _ranked(by_model),
        "by_day": [by_day[day].report(day) for day in sorted(by_day)],
        # From the error STORE, not the ledger -- see the module docstring on
        # why the two totals differ and why that is the point.
        "errors": errors.summary(span, campaign=campaign,
                                 since=since, until=until),
    }


def _label_series(buckets: dict[str, _Series], key: object, cap: int) -> _Series:
    """The bucket for ``key``, created if new. A hand-edited ledger row can
    hold anything where a task name belongs, and a dict arriving at the
    frontend as a key would take the panel down -- so a non-string is
    "unknown", the same narrowing `usage._label` does.

    Bounded at ``cap`` distinct labels; see `MAX_BUCKETS` for why a per-label
    `_Series` is not something a read of a user-editable file gets to open
    without a ceiling. Passed rather than defaulted, so the constant is read at
    call time -- a default argument would bind it once at import and quietly
    ignore a test that moved it.

    ``cap`` of 0 is no bound, which is right for exactly one caller: `by_day`'s
    labels come from the row's own `ts`, and `usage.calls` has already dropped
    every row outside the window -- so that breakdown is bounded by the window
    itself, at `MAX_DAYS`, which is *larger* than `MAX_BUCKETS`. Capping it
    here would fold the far end of a year-long trend into a bucket called
    "other" and draw it as a day.
    """
    name = key if isinstance(key, str) and key else "unknown"
    if cap and name not in buckets and len(buckets) >= cap:
        name = OVERFLOW
    return buckets.setdefault(name, _Series())


def _ranked(buckets: dict[str, _Series]) -> list[dict]:
    """Buckets as a list, busiest first, ties broken by name so two reads of
    the same window agree on the order."""
    return sorted((series.report(key) for key, series in buckets.items()),
                  key=lambda b: (-b["calls"], b["key"]))


def _int(value: object) -> int:
    """A duration from a row, defensively. A row is a line from a file a human
    can edit; a string where a number belongs costs that row's contribution to
    the distribution, not the report."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if value != value or value in (float("inf"), float("-inf")):
        return 0
    return int(max(0, value))
