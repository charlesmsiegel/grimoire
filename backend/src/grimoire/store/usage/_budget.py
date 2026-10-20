"""Billed-only period budgets and user-facing warning states."""

from __future__ import annotations

from datetime import date, timedelta

from . import _clock, _folding, _ledger, _rows

# ---- budgets ----
#: The share of a budget at which the warning starts. A budget that only speaks
#: once it has already been broken is a receipt, not a warning -- the point is to
#: be told while the session can still be ended.
WARN_FRACTION = 0.8

#: The two periods a campaign budget can be declared over. `MONTHLY` is the
#: current UTC calendar month, which is how providers themselves bill; `TOTAL`
#: is what the campaign has cost over the ledger's whole scannable history.
MONTHLY, TOTAL = "monthly", "total"
PERIODS = (MONTHLY, TOTAL)

#: What `budget` reports. `OFF` is a campaign with no budget set, and is
#: deliberately not `OK`: "you are within your budget" and "you have not asked
#: for one" are different answers, and a banner that fires on the second is a
#: banner people turn off.
OFF, OK, WARN, OVER = "off", "ok", "warn", "over"


def normalize_period(period: object) -> str:
    """A period this module understands. Anything else is `MONTHLY`, which is
    the safer default of the two: a month's cap read as an all-time one would
    fire the warning for spend the user had already accepted.

    Case- and space-insensitive, because the value reaches here from a
    hand-edited `campaign.md` as readily as from the form -- and reading
    ``Total`` as ``monthly`` would be a silent, invisible downgrade of what
    somebody plainly wrote."""
    if not isinstance(period, str):
        return MONTHLY
    period = period.strip().lower()
    return period if period in PERIODS else MONTHLY


def normalize_limit(limit: object) -> float:
    """A budget as a positive number of dollars, or 0.0 for "no budget".

    Takes a string as readily as a number: campaign frontmatter is
    string-scalar (`store.frontmatter`), so the stored value arrives here as
    ``"12.5"`` and a hand edit can make it ``"twelve"``. Zero, negative,
    infinite and unparseable all mean the same thing — there is no budget —
    because the alternative is a campaign pinned permanently to `OVER` by a
    typo, warning about every call it ever makes.
    """
    if isinstance(limit, str):
        try:
            limit = float(limit.strip() or 0)
        except ValueError:
            return 0.0
    value = _rows._float(limit)
    return value if value is not None and value > 0 else 0.0


def period_window(period: str, until: str = "") -> tuple[str, str]:
    """The ``[since, until]`` a period covers, both ``YYYY-MM-DD``.

    `TOTAL` is `MAX_DAYS` back rather than genuinely all time, and that bound is
    the same one `summary` takes: this store answers windows, and a query whose
    cost grows with the library's age is the thing `_window_files` exists to
    avoid. A campaign older than a year reports the last year of its spend, and
    the window comes back in the payload so the view can say so.

    **The month is UTC, not the reader's.** Every stamp in this ledger is
    (`_now`), so a monthly budget rolls over at UTC midnight on the 1st, which
    is up to a day off from the month a provider's invoice or the user's own
    calendar would draw. Stated rather than fixed: the fix is a timezone this
    store has no way to know -- the backend serves a browser that never tells it
    one -- and inventing the host's would make the same library report different
    spend on two machines.
    """
    until = _clock._valid_day(until) or _clock._today()
    if normalize_period(period) == TOTAL:
        return (date.fromisoformat(until) - timedelta(days=_clock.MAX_DAYS - 1)).isoformat(), until
    return until[:8] + "01", until


def budget(campaign: str, limit_usd: object, period: object = "") -> dict:
    """Where a campaign stands against its budget (#153).

    ``limit_usd`` and ``period`` come straight off ``campaign.md``'s
    frontmatter, unparsed — normalizing them is this function's job, because it
    is the only place that knows what an unusable one has to mean (see
    `normalize_limit`).

    **A campaign with no budget is not scanned at all.** It reports
    ``{"level": "off"}`` and no spend fields, rather than a zero: nobody asked
    what this campaign cost, and a `spent_usd: 0.0` in the payload is a number a
    view will render as a fact. That also keeps the common case — most campaigns
    never set a budget — free of a file scan on every campaign load.

    Only ``cost_usd`` counts against the cap. Subscription-billed calls
    (``estimated_usd``) are reported beside it and never summed in, for the
    reason the module docstring gives: charging a budget for money nobody paid
    would have a Claude Agent user hitting their cap on their first evening.
    ``unpriced_calls`` is what says the figure is a floor.
    """
    limit = normalize_limit(limit_usd)
    period = normalize_period(period)
    if not limit:
        return {"limit_usd": 0.0, "period": period, "level": OFF,
                "warn_fraction": WARN_FRACTION}
    since, until = period_window(period)
    totals = dict(_rows._ZERO)
    # `Rates.off()`, said out loud rather than left to the default: a budget
    # measures money owed, and an estimate over rates the user typed is not
    # that. Charging a cap for a modelled figure would have somebody hit their
    # limit on arithmetic nobody was invoiced for. `unpriced_calls` still says
    # the figure is a floor, which is the honest version of the same warning.
    rates = _folding.Rates.off()
    for row in _ledger._read_rows(since, until, campaign):
        if not _ledger._is_call(row):
            continue
        _folding._add(totals, row, rates)
    spent = round(totals["cost_usd"], _rows._CENTS)
    fraction = spent / limit
    level = OVER if spent >= limit else WARN if fraction >= WARN_FRACTION else OK
    return {"limit_usd": limit, "period": period, "since": since, "until": until,
            "spent_usd": spent, "estimated_usd": round(totals["estimated_usd"], _rows._CENTS),
            "unpriced_calls": totals["unpriced_calls"], "calls": totals["calls"],
            # Rounded like the money it is derived from, and for the same
            # reason: 0.7999999999999999 renders as 79.99999999999999%.
            "fraction": round(fraction, 4), "level": level,
            "warn_fraction": WARN_FRACTION}
