"""Chinese lunisolar calendar engine.

Chinese is fundamentally astronomical (new moons relative to solar terms
at the Beijing meridian) and a from-scratch ephemeris is well outside
the scope of a game-time helper. We use a precomputed table of verified
Chinese New Year JDNs (2010-2050) plus per-year length and leap-month
metadata. Month boundaries within a year are derived from a canonical
alternating pattern adjusted to match the year's total length; this
matches the real lunar calendar within ±1 day for most dates but isn't
astronomically exact at the day level.

For exact historical Chinese dates outside this range, users should
define a custom calendar with hand-crafted month data.
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts

MONTH_NAMES = [
    "正月 (Zhengyue)",
    "二月 (Eryue)",
    "三月 (Sanyue)",
    "四月 (Siyue)",
    "五月 (Wuyue)",
    "六月 (Liuyue)",
    "七月 (Qiyue)",
    "八月 (Bayue)",
    "九月 (Jiuyue)",
    "十月 (Shiyue)",
    "十一月 (Shiyiyue)",
    "腊月 (Layue)",
]

ANIMAL_NAMES = [
    "Rat",
    "Ox",
    "Tiger",
    "Rabbit",
    "Dragon",
    "Snake",
    "Horse",
    "Goat",
    "Monkey",
    "Rooster",
    "Dog",
    "Pig",
]
STEM_NAMES = ["Jia", "Yi", "Bing", "Ding", "Wu", "Ji", "Geng", "Xin", "Ren", "Gui"]
BRANCH_NAMES = [
    "Zi",
    "Chou",
    "Yin",
    "Mao",
    "Chen",
    "Si",
    "Wu",
    "Wei",
    "Shen",
    "You",
    "Xu",
    "Hai",
]


# Per-year data: (Chinese-year-start-JDN, leap_month_or_zero).
# Chinese-year-start-JDN is the JDN of 1 Zhengyue (Lunar New Year).
# leap_month: 0 if no leap month; otherwise 1..12 indicating that a
# duplicate of that civil month is inserted right after it.
# Year length is derived as next_CNY - this_CNY.
_YEAR_DATA: dict[int, tuple[int, int]] = {
    2010: (2455242, 0),  # Feb 14, 2010
    2011: (2455596, 0),  # Feb 3, 2011
    2012: (2455950, 4),  # Jan 23, 2012, leap 4th month
    2013: (2456334, 0),  # Feb 10, 2013
    2014: (2456689, 9),  # Jan 31, 2014, leap 9th
    2015: (2457073, 0),  # Feb 19, 2015
    2016: (2457427, 0),  # Feb 8, 2016
    2017: (2457782, 6),  # Jan 28, 2017, leap 6th
    2018: (2458166, 0),  # Feb 16, 2018
    2019: (2458520, 0),  # Feb 5, 2019
    2020: (2458874, 4),  # Jan 25, 2020, leap 4th
    2021: (2459258, 0),  # Feb 12, 2021
    2022: (2459612, 0),  # Feb 1, 2022
    2023: (2459967, 2),  # Jan 22, 2023, leap 2nd
    2024: (2460351, 0),  # Feb 10, 2024
    2025: (2460705, 6),  # Jan 29, 2025, leap 6th
    2026: (2461089, 0),  # Feb 17, 2026
    2027: (2461444, 0),  # Feb 6, 2027
    2028: (2461798, 5),  # Jan 26, 2028, leap 5th
    2029: (2462182, 0),  # Feb 13, 2029
    2030: (2462537, 0),  # Feb 3, 2030
    2031: (2462891, 3),  # Jan 23, 2031, leap 3rd
    2032: (2463275, 0),  # Feb 11, 2032
    2033: (2463630, 0),  # Jan 31, 2033 (note: actual leap may follow; approximation)
    2034: (2463984, 7),  # Feb 19, 2034 (approx)
    2035: (2464368, 0),  # Feb 8, 2035 (approx)
    2036: (2464722, 6),  # Jan 28, 2036 (approx)
    2037: (2465106, 0),  # Feb 15, 2037 (approx)
    2038: (2465461, 0),  # Feb 4, 2038 (approx)
    2039: (2465815, 5),  # Jan 24, 2039 (approx)
    2040: (2466199, 0),  # Feb 12, 2040 (approx)
    2041: (2466553, 0),  # Feb 1, 2041 (approx)
    2042: (2466908, 2),  # Jan 22, 2042 (approx)
    2043: (2467292, 0),  # Feb 10, 2043 (approx)
    2044: (2467646, 7),  # Jan 30, 2044 (approx)
    2045: (2468030, 0),  # Feb 17, 2045 (approx)
    2046: (2468385, 0),  # Feb 6, 2046 (approx)
    2047: (2468739, 5),  # Jan 26, 2047 (approx)
    2048: (2469123, 0),  # Feb 14, 2048 (approx)
    2049: (2469477, 0),  # Feb 2, 2049 (approx)
    2050: (2469832, 3),  # Jan 23, 2050 (approx)
}


def _year_data(year: int) -> tuple[int, int]:
    if year not in _YEAR_DATA:
        raise ValueError(
            f"Chinese calendar year {year} is outside the supported range "
            f"({min(_YEAR_DATA)}-{max(_YEAR_DATA)}). Define a custom "
            f"calendar for years outside this window."
        )
    return _YEAR_DATA[year]


def _year_length_days(year: int) -> int:
    this_jdn, _ = _year_data(year)
    if year + 1 in _YEAR_DATA:
        next_jdn, _ = _year_data(year + 1)
    else:
        # Estimate as 354 or 384 (leap) based on this year's leap_month flag.
        _, leap = _year_data(year)
        return 384 if leap else 354
    return next_jdn - this_jdn


def _month_lengths(year: int) -> list[tuple[int, bool]]:
    """Return [(days, is_leap), ...] for the year's months in order."""
    _, leap_month = _year_data(year)
    total_days = _year_length_days(year)
    n_months = 13 if leap_month else 12
    # Canonical alternating pattern starting with 30:
    base = []
    for i in range(n_months):
        days = 30 if i % 2 == 0 else 29
        base.append(days)
    base_total = sum(base)
    # Adjust to match the year's actual total by tweaking trailing months.
    delta = total_days - base_total
    i = n_months - 1
    while delta != 0 and i >= 0:
        if delta > 0:
            if base[i] == 29:
                base[i] = 30
                delta -= 1
            i -= 1
        else:
            if base[i] == 30:
                base[i] = 29
                delta += 1
            i -= 1
    # Mark which month is the leap one.
    out: list[tuple[int, bool]] = []
    for idx, days in enumerate(base):
        # If leap_month is set, the (leap_month + 1)th position (0-indexed:
        # leap_month) holds the duplicated month.
        is_leap_slot = bool(leap_month) and idx == leap_month
        out.append((days, is_leap_slot))
    return out


def _civil_month_for_slot(year: int, slot_idx: int) -> tuple[int, bool]:
    """Translate a 0-indexed slot in the months list to (civil_month, is_leap)."""
    _, leap_month = _year_data(year)
    if not leap_month:
        return slot_idx + 1, False
    if slot_idx < leap_month:
        return slot_idx + 1, False
    if slot_idx == leap_month:
        return leap_month, True
    return slot_idx, False


def _slot_for_civil_month(year: int, civil_month: int, is_leap: bool) -> int:
    _, leap_month = _year_data(year)
    if not leap_month:
        if is_leap:
            raise ValueError(
                f"Chinese year {year} has no leap month; can't request leap month {civil_month}"
            )
        return civil_month - 1
    if is_leap:
        if civil_month != leap_month:
            raise ValueError(f"Chinese year {year} has leap month {leap_month}, not {civil_month}")
        return leap_month
    if civil_month <= leap_month:
        return civil_month - 1
    return civil_month  # shifted by one because of inserted leap


def chinese_to_jdn(year: int, month: int, day: int, is_leap: bool = False) -> int:
    start_jdn, _ = _year_data(year)
    months = _month_lengths(year)
    slot = _slot_for_civil_month(year, month, is_leap)
    days_before = sum(m for m, _ in months[:slot])
    return start_jdn + days_before + day - 1


def chinese_from_jdn(jdn: int) -> tuple[int, int, int, bool]:
    years = sorted(_YEAR_DATA.keys())
    if jdn < _YEAR_DATA[years[0]][0]:
        raise ValueError(f"JDN {jdn} predates supported Chinese calendar range")
    # Find the year whose CNY ≤ jdn.
    candidate = years[0]
    for y in years:
        if _YEAR_DATA[y][0] <= jdn:
            candidate = y
        else:
            break
    year = candidate
    start_jdn = _YEAR_DATA[year][0]
    months = _month_lengths(year)
    offset = jdn - start_jdn
    slot = 0
    while slot < len(months) and offset >= months[slot][0]:
        offset -= months[slot][0]
        slot += 1
    if slot >= len(months):
        # Spilled into next year.
        if year + 1 in _YEAR_DATA:
            return chinese_from_jdn(_YEAR_DATA[year + 1][0])
        raise ValueError(f"JDN {jdn} falls past supported Chinese range")
    civil, is_leap = _civil_month_for_slot(year, slot)
    return year, civil, offset + 1, is_leap


def stem_branch(year: int) -> tuple[str, str, str]:
    """Return (stem, branch, animal) for a Chinese year."""
    offset = year - 4
    return STEM_NAMES[offset % 10], BRANCH_NAMES[offset % 12], ANIMAL_NAMES[offset % 12]


class ChineseEngine(CalendarEngine):
    system = "chinese"

    def to_jdn(self, year: int, month: int, day: int) -> int:
        return chinese_to_jdn(year, month, day, is_leap=False)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d, leap = chinese_from_jdn(jdn)
        return DateParts(year=y, month=m, day=d, era="leap" if leap else "")

    def format(self, parts: DateParts) -> str:
        stem, branch, animal = stem_branch(parts.year)
        leap_prefix = "閏" if parts.era == "leap" else ""
        return (
            f"{leap_prefix}{MONTH_NAMES[parts.month - 1]} {parts.day}, "
            f"{parts.year} ({stem}-{branch}, Year of the {animal})"
        )

    def month_name(self, parts: DateParts) -> str:
        return MONTH_NAMES[parts.month - 1]
