"""Built-in calendar + holiday-set registry.

Built-in calendars live in code (rather than in the library_index) so
their leap-rule arithmetic is owned by deterministic engines and not
exposed as editable data. Users can attach built-ins to a world via
their stable ids (`gregorian`, `hebrew`, etc.) the same way they
reference custom calendars; the calendar service routes the lookup to
the right place.
"""

from __future__ import annotations

from grimoire.types.calendar import Calendar, CalendarSystem, HolidaySet

from .bahai import BahaiEngine
from .base import CalendarEngine
from .buddhist import BuddhistEngine
from .chinese import ChineseEngine
from .coptic import CopticEngine, EthiopianEngine
from .custom import CustomCalendarEngine
from .gregorian import GregorianEngine
from .hebrew import HebrewEngine
from .holidays_seed import BUILTIN_HOLIDAY_SETS
from .indian import IndianSakaEngine
from .islamic import IslamicEngine
from .iso_week import IsoWeekEngine
from .japanese import JapaneseEraEngine
from .julian import JulianEngine
from .persian import PersianEngine
from .stardate import StardateEngine


# Map of built-in calendar id -> Calendar definition.
BUILTIN_CALENDARS: dict[str, Calendar] = {
    "gregorian": Calendar(
        id="gregorian",
        name="Gregorian",
        description="The civil calendar in use worldwide. Solar, with a "
        "400-year leap cycle (every 4 years, except centuries not divisible by 400).",
        tags=["solar", "civil", "western"],
        system=CalendarSystem.GREGORIAN,
        builtin=True,
        date_format="%B %-d, %Y",
    ),
    "julian": Calendar(
        id="julian",
        name="Julian",
        description="The pre-1582 European calendar. Drifts ~3 days per 400 "
        "years from Gregorian; still used liturgically in Orthodox Christianity.",
        tags=["solar", "historical", "orthodox"],
        system=CalendarSystem.JULIAN,
        builtin=True,
    ),
    "hebrew": Calendar(
        id="hebrew",
        name="Hebrew",
        description="Lunisolar calendar used in Judaism. 12-or-13 months, "
        "Metonic 19-year cycle keeping months aligned to the solar year.",
        tags=["lunisolar", "jewish", "religious"],
        system=CalendarSystem.HEBREW,
        builtin=True,
    ),
    "islamic": Calendar(
        id="islamic",
        name="Islamic (Hijri)",
        description="Tabular Hijri calendar. Pure-lunar, 12 months of 29 or "
        "30 days. Drifts ~11 days per solar year against Gregorian.",
        tags=["lunar", "muslim", "religious"],
        system=CalendarSystem.ISLAMIC,
        builtin=True,
    ),
    "persian": Calendar(
        id="persian",
        name="Persian (Solar Hijri)",
        description="Solar calendar of Iran and Afghanistan. New year is the "
        "vernal equinox; 2820-year algorithmic cycle approximates the equinox.",
        tags=["solar", "persian", "civil"],
        system=CalendarSystem.PERSIAN,
        builtin=True,
    ),
    "chinese": Calendar(
        id="chinese",
        name="Chinese Lunisolar",
        description="Traditional lunisolar calendar with stem-branch year "
        "names and 12 or 13 lunar months. Table-driven for 1900-2050.",
        tags=["lunisolar", "chinese", "traditional"],
        system=CalendarSystem.CHINESE,
        builtin=True,
    ),
    "japanese": Calendar(
        id="japanese",
        name="Japanese Era",
        description="Gregorian day count with reign-era year names (Reiwa, "
        "Heisei, Showa, Taisho, Meiji).",
        tags=["era", "japanese", "civil"],
        system=CalendarSystem.JAPANESE_ERA,
        builtin=True,
    ),
    "indian-saka": Calendar(
        id="indian-saka",
        name="Indian National (Saka)",
        description="The official civil calendar of India. Year = Gregorian "
        "- 78; new year falls in late March.",
        tags=["solar", "indian", "civil"],
        system=CalendarSystem.INDIAN_SAKA,
        builtin=True,
    ),
    "ethiopian": Calendar(
        id="ethiopian",
        name="Ethiopian",
        description="13-month solar calendar of Ethiopia/Eritrea. Year ~ "
        "Gregorian - 7 or - 8. New year falls on 11 September.",
        tags=["solar", "ethiopian", "religious"],
        system=CalendarSystem.ETHIOPIAN,
        builtin=True,
    ),
    "coptic": Calendar(
        id="coptic",
        name="Coptic",
        description="13-month solar calendar of the Coptic Orthodox Church. "
        "Same structure as Ethiopian but anchored to the Diocletian era (284 CE).",
        tags=["solar", "coptic", "religious"],
        system=CalendarSystem.COPTIC,
        builtin=True,
    ),
    "bahai": Calendar(
        id="bahai",
        name="Bahá'í (Badí')",
        description="19 months of 19 days plus 4 or 5 intercalary days "
        "(Ayyám-i-Há). Year begins on the vernal equinox.",
        tags=["solar", "bahai", "religious"],
        system=CalendarSystem.BAHAI,
        builtin=True,
    ),
    "buddhist": Calendar(
        id="buddhist",
        name="Thai Buddhist Era",
        description="Gregorian month/day with Buddhist Era year = Gregorian "
        "+ 543. Civil calendar of Thailand.",
        tags=["solar", "buddhist", "civil"],
        system=CalendarSystem.BUDDHIST,
        builtin=True,
    ),
    "iso-week": Calendar(
        id="iso-week",
        name="ISO 8601 Week Date",
        description="Year-week-weekday representation. Useful for business "
        "or sci-fi settings that want week-based time tracking.",
        tags=["week-based", "iso", "modern"],
        system=CalendarSystem.ISO_WEEK,
        builtin=True,
        date_format="%G-W%V-%u",
    ),
    "stardate": Calendar(
        id="stardate",
        name="Stardate (Star Trek TNG)",
        description="Linear decimal year offset. Stardate 0 = 1 Jan 2323 CE; "
        "1000 stardate units per Gregorian year.",
        tags=["scifi", "linear", "star-trek"],
        system=CalendarSystem.STARDATE,
        builtin=True,
    ),
}


# Map of CalendarSystem -> engine factory (instantiated lazily).
_ENGINE_FACTORIES: dict[CalendarSystem, type[CalendarEngine]] = {
    CalendarSystem.GREGORIAN: GregorianEngine,
    CalendarSystem.JULIAN: JulianEngine,
    CalendarSystem.HEBREW: HebrewEngine,
    CalendarSystem.ISLAMIC: IslamicEngine,
    CalendarSystem.PERSIAN: PersianEngine,
    CalendarSystem.CHINESE: ChineseEngine,
    CalendarSystem.JAPANESE_ERA: JapaneseEraEngine,
    CalendarSystem.INDIAN_SAKA: IndianSakaEngine,
    CalendarSystem.ETHIOPIAN: EthiopianEngine,
    CalendarSystem.COPTIC: CopticEngine,
    CalendarSystem.BAHAI: BahaiEngine,
    CalendarSystem.BUDDHIST: BuddhistEngine,
    CalendarSystem.ISO_WEEK: IsoWeekEngine,
    CalendarSystem.STARDATE: StardateEngine,
}


def engine_for(calendar: Calendar) -> CalendarEngine:
    """Return the right CalendarEngine instance for `calendar`.

    Built-in systems get their dedicated engine; CUSTOM gets a generic
    engine driven by the calendar's CustomCalendarConfig.
    """
    if calendar.system == CalendarSystem.CUSTOM:
        if calendar.custom is None:
            raise ValueError(
                f"calendar {calendar.id!r} uses CUSTOM system but has no custom config"
            )
        return CustomCalendarEngine(calendar.custom, era_name=calendar.custom.era_name)
    factory = _ENGINE_FACTORIES.get(calendar.system)
    if factory is None:
        raise ValueError(f"no engine registered for calendar system {calendar.system!r}")
    return factory()


def is_builtin_calendar(calendar_id: str) -> bool:
    return calendar_id in BUILTIN_CALENDARS


def list_builtin_calendars() -> list[Calendar]:
    return list(BUILTIN_CALENDARS.values())


def get_builtin_calendar(calendar_id: str) -> Calendar | None:
    return BUILTIN_CALENDARS.get(calendar_id)


def is_builtin_holiday_set(set_id: str) -> bool:
    return set_id in BUILTIN_HOLIDAY_SETS


def list_builtin_holiday_sets() -> list[HolidaySet]:
    return list(BUILTIN_HOLIDAY_SETS.values())


def get_builtin_holiday_set(set_id: str) -> HolidaySet | None:
    return BUILTIN_HOLIDAY_SETS.get(set_id)
