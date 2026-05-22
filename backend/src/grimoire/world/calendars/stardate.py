"""Star Trek TNG-era stardate.

Stardate is a decimal year offset. The canonical TNG-era anchor is
that stardate 41000.0 corresponds to early 2364 CE; later episodes
established a linear rate of ~1000 stardate units per Gregorian year.

We use the linear formula:
    stardate = (gregorian_year - 2323) * 1000 + day_of_year_fraction * 1000

So stardate 0.0 = 1 Jan 2323 CE; 41000.0 falls in early 2364, matching
the show's progression after the first season.

Native triple: (year=integer_part_of_stardate, month=1, day=1) — we
treat the integer stardate as "year" and ignore fractional days for the
two-way conversion (a calendar-style truncation). Display always shows
the fractional form.

This engine is unusual: stardates aren't a true day calendar, so JDN
conversion drops sub-day precision and `from_jdn` returns the integer
stardate matching the start of the day.
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts
from .gregorian import gregorian_from_jdn, gregorian_to_jdn, is_gregorian_leap

STARDATE_ANCHOR_YEAR = 2323  # CE year where stardate = 0.0


def _day_of_year(year: int, month: int, day: int) -> int:
    days_in_months = [
        31,
        29 if is_gregorian_leap(year) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ]
    return sum(days_in_months[: month - 1]) + day


def stardate_to_jdn(year_stardate: int, month: int, day: int) -> int:
    """Convert (stardate-year, m, d) -> JDN.

    `month`/`day` are interpreted as Gregorian month/day within the
    Gregorian year `STARDATE_ANCHOR_YEAR + year_stardate // 1000`.
    """
    gregorian_year = STARDATE_ANCHOR_YEAR + year_stardate // 1000
    return gregorian_to_jdn(gregorian_year, month, day)


def stardate_from_jdn(jdn: int) -> tuple[int, int, int]:
    gy, gm, gd = gregorian_from_jdn(jdn)
    days_in_year = 366 if is_gregorian_leap(gy) else 365
    doy = _day_of_year(gy, gm, gd)
    integer_stardate = (gy - STARDATE_ANCHOR_YEAR) * 1000 + ((doy - 1) * 1000) // days_in_year
    return integer_stardate, gm, gd


class StardateEngine(CalendarEngine):
    system = "stardate"

    def to_jdn(self, year: int, month: int, day: int) -> int:
        return stardate_to_jdn(year, month, day)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d = stardate_from_jdn(jdn)
        return DateParts(year=y, month=m, day=d)

    def format(self, parts: DateParts) -> str:
        # Show fractional stardate based on day-of-year.
        from .gregorian import is_gregorian_leap

        greg_year = STARDATE_ANCHOR_YEAR + parts.year // 1000
        days_in_year = 366 if is_gregorian_leap(greg_year) else 365
        doy = _day_of_year(greg_year, parts.month, parts.day)
        fractional = parts.year + ((doy - 1) * 10) / days_in_year
        return f"Stardate {fractional:.1f}"

    def month_name(self, parts: DateParts) -> str:
        return f"M{parts.month}"
