"""Coptic & Ethiopian calendar engines.

Both share the same structure: 13 months — 12 of 30 days plus a small
intercalary month of 5 days (6 in leap years). Leap years: every 4
without exception (Julian rule). They differ only in epoch.

Coptic epoch: Toout 1, AM 1 = 29 August 284 CE (Julian) = JDN 1825030.
Ethiopian epoch: Meskerem 1, EE 1 = 29 August 8 CE (Julian) = JDN 1724221.

Month names differ:
  Coptic:    Toout, Paopi, Hathor, Koiak, Tobi, Meshir, Paremhat,
             Parmouti, Pashons, Paoni, Epip, Mesori, Pi Kogi Enavot.
  Ethiopian: Meskerem, Tekemt, Hedar, Tahsas, Tir, Yekatit, Megabit,
             Miyazya, Genbot, Sene, Hamle, Nehasse, Pagume.
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts

COPTIC_EPOCH = 1825030
ETHIOPIAN_EPOCH = 1724221

COPTIC_MONTH_NAMES = [
    "Thout",
    "Paopi",
    "Hathor",
    "Koiak",
    "Tobi",
    "Meshir",
    "Paremhat",
    "Parmouti",
    "Pashons",
    "Paoni",
    "Epip",
    "Mesori",
    "Pi Kogi Enavot",
]
ETHIOPIAN_MONTH_NAMES = [
    "Meskerem",
    "Tekemt",
    "Hedar",
    "Tahsas",
    "Tir",
    "Yekatit",
    "Megabit",
    "Miyazya",
    "Genbot",
    "Sene",
    "Hamle",
    "Nehasse",
    "Pagume",
]


def _is_leap(year: int) -> bool:
    return year % 4 == 3


def _to_jdn(epoch: int, year: int, month: int, day: int) -> int:
    days_before_year = (
        365 * (year - 1) + (year // 4) - (1 if year > 0 and (year - 1) % 4 == 3 else 0)
    )
    # Simpler: there are floor((year-1)/4) leap years before `year` (year
    # is leap when year % 4 == 3, so leap years are 3, 7, 11, ...; count
    # of those in [1, year-1] is floor((year-1+1)/4) = year//4).
    days_before_year = 365 * (year - 1) + year // 4
    days_before_month = 30 * (month - 1)
    return epoch + days_before_year + days_before_month + day - 1


def _from_jdn(epoch: int, jdn: int) -> tuple[int, int, int]:
    days = jdn - epoch
    # 4-year cycle = 4*365 + 1 = 1461 days
    cycles = days // 1461
    remainder = days % 1461
    if remainder == 1460:
        year_in_cycle = 3
        day_of_year = 365
    else:
        year_in_cycle = remainder // 365
        day_of_year = remainder % 365
    year = cycles * 4 + year_in_cycle + 1
    month = day_of_year // 30 + 1
    day = day_of_year % 30 + 1
    return year, month, day


class CopticEngine(CalendarEngine):
    system = "coptic"

    def to_jdn(self, date: DateParts) -> int:
        return _to_jdn(COPTIC_EPOCH, date.year, date.month, date.day)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d = _from_jdn(COPTIC_EPOCH, jdn)
        return DateParts(year=y, month=m, day=d)

    def format(self, parts: DateParts) -> str:
        return f"{parts.day} {COPTIC_MONTH_NAMES[parts.month - 1]} {parts.year} AM"

    def month_name(self, parts: DateParts) -> str:
        return COPTIC_MONTH_NAMES[parts.month - 1]


class EthiopianEngine(CalendarEngine):
    system = "ethiopian"

    def to_jdn(self, date: DateParts) -> int:
        return _to_jdn(ETHIOPIAN_EPOCH, date.year, date.month, date.day)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d = _from_jdn(ETHIOPIAN_EPOCH, jdn)
        return DateParts(year=y, month=m, day=d)

    def format(self, parts: DateParts) -> str:
        return f"{parts.day} {ETHIOPIAN_MONTH_NAMES[parts.month - 1]} {parts.year} EE"

    def month_name(self, parts: DateParts) -> str:
        return ETHIOPIAN_MONTH_NAMES[parts.month - 1]
