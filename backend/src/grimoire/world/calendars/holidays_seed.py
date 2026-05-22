"""Built-in HolidaySet definitions.

Each set binds to a specific CalendarSystem because holiday dates are
defined in the calendar that owns them — US Federal holidays only make
sense in Gregorian, Jewish holidays in Hebrew, Islamic holidays in
Hijri, etc.

Holidays use declarative rules:

  * FIXED — month + day (e.g. Christmas: Dec 25)
  * NTH_WEEKDAY — nth weekday of a month (e.g. US Thanksgiving:
    4th Thursday of November)
  * LAST_WEEKDAY — last weekday of a month (e.g. US Memorial Day:
    last Monday of May)
  * EASTER_WESTERN / EASTER_ORTHODOX — offset_days from Easter Sunday
  * LUNAR_NEW_YEAR — offset_days from Chinese Lunar New Year (on the
    Gregorian calendar)

Built-in sets are immutable; users can copy any set into a custom one
to edit holidays.
"""

from __future__ import annotations

from grimoire.types.calendar import (
    CalendarSystem,
    Holiday,
    HolidayRule,
    HolidaySet,
)


# -------------------------------------------------------------------------
# Gregorian-system holiday sets (real-world public holidays)
# -------------------------------------------------------------------------


US_FEDERAL = HolidaySet(
    id="us-federal",
    name="US Federal Holidays",
    description="Public holidays observed by the US federal government.",
    tags=["public", "usa"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="new-years-day", name="New Year's Day", rule=HolidayRule.FIXED, month=1, day=1),
        Holiday(id="mlk-day", name="Martin Luther King Jr. Day", rule=HolidayRule.NTH_WEEKDAY,
                weekday_month=1, weekday=0, nth=3),  # 3rd Monday of January
        Holiday(id="presidents-day", name="Presidents' Day", rule=HolidayRule.NTH_WEEKDAY,
                weekday_month=2, weekday=0, nth=3),  # 3rd Monday of February
        Holiday(id="memorial-day", name="Memorial Day", rule=HolidayRule.LAST_WEEKDAY,
                weekday_month=5, weekday=0),  # last Monday of May
        Holiday(id="juneteenth", name="Juneteenth", rule=HolidayRule.FIXED, month=6, day=19),
        Holiday(id="independence-day", name="Independence Day", rule=HolidayRule.FIXED,
                month=7, day=4),
        Holiday(id="labor-day", name="Labor Day", rule=HolidayRule.NTH_WEEKDAY,
                weekday_month=9, weekday=0, nth=1),  # 1st Monday of September
        Holiday(id="columbus-day", name="Columbus Day / Indigenous Peoples' Day",
                rule=HolidayRule.NTH_WEEKDAY, weekday_month=10, weekday=0, nth=2),
        Holiday(id="veterans-day", name="Veterans Day", rule=HolidayRule.FIXED,
                month=11, day=11),
        Holiday(id="thanksgiving", name="Thanksgiving", rule=HolidayRule.NTH_WEEKDAY,
                weekday_month=11, weekday=3, nth=4),  # 4th Thursday of November
        Holiday(id="christmas", name="Christmas Day", rule=HolidayRule.FIXED,
                month=12, day=25),
    ],
)


UK_BANK = HolidaySet(
    id="uk-bank",
    name="UK Bank Holidays",
    description="England & Wales bank holidays.",
    tags=["public", "uk"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="new-years-day", name="New Year's Day", rule=HolidayRule.FIXED, month=1, day=1),
        Holiday(id="good-friday", name="Good Friday", rule=HolidayRule.EASTER_WESTERN,
                offset_days=-2),
        Holiday(id="easter-monday", name="Easter Monday", rule=HolidayRule.EASTER_WESTERN,
                offset_days=1),
        Holiday(id="early-may", name="Early May Bank Holiday", rule=HolidayRule.NTH_WEEKDAY,
                weekday_month=5, weekday=0, nth=1),
        Holiday(id="spring-bank", name="Spring Bank Holiday", rule=HolidayRule.LAST_WEEKDAY,
                weekday_month=5, weekday=0),
        Holiday(id="summer-bank", name="Summer Bank Holiday", rule=HolidayRule.LAST_WEEKDAY,
                weekday_month=8, weekday=0),
        Holiday(id="christmas", name="Christmas Day", rule=HolidayRule.FIXED, month=12, day=25),
        Holiday(id="boxing-day", name="Boxing Day", rule=HolidayRule.FIXED, month=12, day=26),
    ],
)


CANADIAN_FEDERAL = HolidaySet(
    id="canadian-federal",
    name="Canadian Federal Holidays",
    description="Statutory federal holidays of Canada.",
    tags=["public", "canada"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="new-years-day", name="New Year's Day", rule=HolidayRule.FIXED, month=1, day=1),
        Holiday(id="good-friday", name="Good Friday", rule=HolidayRule.EASTER_WESTERN,
                offset_days=-2),
        Holiday(id="victoria-day", name="Victoria Day", rule=HolidayRule.NTH_WEEKDAY,
                weekday_month=5, weekday=0, nth=4),  # Mon on or before May 24 — approximate
        Holiday(id="canada-day", name="Canada Day", rule=HolidayRule.FIXED, month=7, day=1),
        Holiday(id="labour-day", name="Labour Day", rule=HolidayRule.NTH_WEEKDAY,
                weekday_month=9, weekday=0, nth=1),
        Holiday(id="thanksgiving-ca", name="Thanksgiving", rule=HolidayRule.NTH_WEEKDAY,
                weekday_month=10, weekday=0, nth=2),
        Holiday(id="remembrance-day", name="Remembrance Day", rule=HolidayRule.FIXED,
                month=11, day=11),
        Holiday(id="christmas", name="Christmas Day", rule=HolidayRule.FIXED, month=12, day=25),
        Holiday(id="boxing-day", name="Boxing Day", rule=HolidayRule.FIXED, month=12, day=26),
    ],
)


EU_COMMON = HolidaySet(
    id="eu-common",
    name="EU Common Holidays",
    description="Public holidays widely observed across continental EU "
    "(specific dates vary by member state).",
    tags=["public", "europe"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="new-years-day", name="New Year's Day", rule=HolidayRule.FIXED, month=1, day=1),
        Holiday(id="good-friday", name="Good Friday", rule=HolidayRule.EASTER_WESTERN,
                offset_days=-2),
        Holiday(id="easter-monday", name="Easter Monday", rule=HolidayRule.EASTER_WESTERN,
                offset_days=1),
        Holiday(id="labour-day", name="Labour Day", rule=HolidayRule.FIXED, month=5, day=1),
        Holiday(id="europe-day", name="Europe Day", rule=HolidayRule.FIXED, month=5, day=9),
        Holiday(id="ascension", name="Ascension", rule=HolidayRule.EASTER_WESTERN,
                offset_days=39),
        Holiday(id="whit-monday", name="Whit Monday", rule=HolidayRule.EASTER_WESTERN,
                offset_days=50),
        Holiday(id="all-saints", name="All Saints' Day", rule=HolidayRule.FIXED,
                month=11, day=1),
        Holiday(id="christmas", name="Christmas Day", rule=HolidayRule.FIXED, month=12, day=25),
        Holiday(id="st-stephens", name="St. Stephen's Day", rule=HolidayRule.FIXED,
                month=12, day=26),
    ],
)


MEXICAN_PUBLIC = HolidaySet(
    id="mexican-public",
    name="Mexican Public Holidays",
    description="Statutory holidays of Mexico, plus widely-observed cultural dates.",
    tags=["public", "mexico"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="new-years-day", name="Año Nuevo", rule=HolidayRule.FIXED, month=1, day=1),
        Holiday(id="constitution-day", name="Día de la Constitución",
                rule=HolidayRule.NTH_WEEKDAY, weekday_month=2, weekday=0, nth=1),
        Holiday(id="benito-juarez", name="Natalicio de Benito Juárez",
                rule=HolidayRule.NTH_WEEKDAY, weekday_month=3, weekday=0, nth=3),
        Holiday(id="labour-day", name="Día del Trabajo", rule=HolidayRule.FIXED, month=5, day=1),
        Holiday(id="independence-day", name="Día de la Independencia",
                rule=HolidayRule.FIXED, month=9, day=16),
        Holiday(id="dia-de-muertos", name="Día de los Muertos",
                rule=HolidayRule.FIXED, month=11, day=2, duration_days=2),
        Holiday(id="revolution-day", name="Día de la Revolución",
                rule=HolidayRule.NTH_WEEKDAY, weekday_month=11, weekday=0, nth=3),
        Holiday(id="guadalupe", name="Día de la Virgen de Guadalupe",
                rule=HolidayRule.FIXED, month=12, day=12),
        Holiday(id="christmas", name="Navidad", rule=HolidayRule.FIXED, month=12, day=25),
    ],
)


CHRISTIAN_WESTERN = HolidaySet(
    id="christian-western",
    name="Christian (Western)",
    description="Major holy days of Western (Roman Catholic / Protestant) "
    "Christianity. Easter is computed by the Gregorian Computus.",
    tags=["religious", "christian", "western"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="epiphany", name="Epiphany", rule=HolidayRule.FIXED, month=1, day=6),
        Holiday(id="ash-wednesday", name="Ash Wednesday", rule=HolidayRule.EASTER_WESTERN,
                offset_days=-46),
        Holiday(id="palm-sunday", name="Palm Sunday", rule=HolidayRule.EASTER_WESTERN,
                offset_days=-7),
        Holiday(id="good-friday", name="Good Friday", rule=HolidayRule.EASTER_WESTERN,
                offset_days=-2),
        Holiday(id="easter", name="Easter Sunday", rule=HolidayRule.EASTER_WESTERN,
                offset_days=0),
        Holiday(id="ascension", name="Ascension", rule=HolidayRule.EASTER_WESTERN,
                offset_days=39),
        Holiday(id="pentecost", name="Pentecost", rule=HolidayRule.EASTER_WESTERN,
                offset_days=49),
        Holiday(id="all-saints", name="All Saints' Day", rule=HolidayRule.FIXED,
                month=11, day=1),
        Holiday(id="advent-1", name="First Sunday of Advent",
                # 4th Sunday before Christmas = 4 Sundays before Dec 25.
                # Approximation as last Sunday of November:
                rule=HolidayRule.LAST_WEEKDAY, weekday_month=11, weekday=6),
        Holiday(id="christmas-eve", name="Christmas Eve", rule=HolidayRule.FIXED,
                month=12, day=24),
        Holiday(id="christmas", name="Christmas Day", rule=HolidayRule.FIXED,
                month=12, day=25),
    ],
)


CHRISTIAN_ORTHODOX = HolidaySet(
    id="christian-orthodox",
    name="Christian (Orthodox)",
    description="Major holy days of Eastern Orthodoxy. Easter follows the "
    "Julian computus; Christmas is observed on 7 January (Gregorian).",
    tags=["religious", "christian", "orthodox"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="orthodox-christmas", name="Orthodox Christmas",
                rule=HolidayRule.FIXED, month=1, day=7),
        Holiday(id="theophany", name="Theophany", rule=HolidayRule.FIXED, month=1, day=19),
        Holiday(id="orthodox-good-friday", name="Good Friday",
                rule=HolidayRule.EASTER_ORTHODOX, offset_days=-2),
        Holiday(id="pascha", name="Pascha", rule=HolidayRule.EASTER_ORTHODOX,
                offset_days=0),
        Holiday(id="pentecost-orthodox", name="Pentecost",
                rule=HolidayRule.EASTER_ORTHODOX, offset_days=49),
        Holiday(id="dormition", name="Dormition of the Theotokos",
                rule=HolidayRule.FIXED, month=8, day=15),
        Holiday(id="nativity-mary", name="Nativity of the Theotokos",
                rule=HolidayRule.FIXED, month=9, day=8),
    ],
)


JAPANESE_PUBLIC = HolidaySet(
    id="japanese-public",
    name="Japanese Public Holidays",
    description="National holidays of Japan (kokumin no shukujitsu).",
    tags=["public", "japan"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="new-years-day", name="元日 (New Year's Day)", rule=HolidayRule.FIXED,
                month=1, day=1),
        Holiday(id="coming-of-age", name="成人の日 (Coming of Age Day)",
                rule=HolidayRule.NTH_WEEKDAY, weekday_month=1, weekday=0, nth=2),
        Holiday(id="national-foundation", name="建国記念の日 (National Foundation Day)",
                rule=HolidayRule.FIXED, month=2, day=11),
        Holiday(id="emperors-birthday", name="天皇誕生日 (Emperor's Birthday)",
                rule=HolidayRule.FIXED, month=2, day=23),
        Holiday(id="vernal-equinox", name="春分の日 (Vernal Equinox Day)",
                rule=HolidayRule.FIXED, month=3, day=20),  # approximation
        Holiday(id="showa-day", name="昭和の日 (Showa Day)", rule=HolidayRule.FIXED,
                month=4, day=29),
        Holiday(id="constitution-memorial", name="憲法記念日 (Constitution Memorial Day)",
                rule=HolidayRule.FIXED, month=5, day=3),
        Holiday(id="greenery-day", name="みどりの日 (Greenery Day)",
                rule=HolidayRule.FIXED, month=5, day=4),
        Holiday(id="childrens-day", name="こどもの日 (Children's Day)",
                rule=HolidayRule.FIXED, month=5, day=5),
        Holiday(id="marine-day", name="海の日 (Marine Day)", rule=HolidayRule.NTH_WEEKDAY,
                weekday_month=7, weekday=0, nth=3),
        Holiday(id="mountain-day", name="山の日 (Mountain Day)", rule=HolidayRule.FIXED,
                month=8, day=11),
        Holiday(id="respect-for-aged", name="敬老の日 (Respect for the Aged Day)",
                rule=HolidayRule.NTH_WEEKDAY, weekday_month=9, weekday=0, nth=3),
        Holiday(id="autumnal-equinox", name="秋分の日 (Autumnal Equinox Day)",
                rule=HolidayRule.FIXED, month=9, day=23),
        Holiday(id="sports-day", name="スポーツの日 (Sports Day)",
                rule=HolidayRule.NTH_WEEKDAY, weekday_month=10, weekday=0, nth=2),
        Holiday(id="culture-day", name="文化の日 (Culture Day)", rule=HolidayRule.FIXED,
                month=11, day=3),
        Holiday(id="labour-thanksgiving", name="勤労感謝の日 (Labour Thanksgiving)",
                rule=HolidayRule.FIXED, month=11, day=23),
    ],
)


CHINESE_TRADITIONAL = HolidaySet(
    id="chinese-traditional",
    name="Chinese Traditional Holidays",
    description="Major traditional Chinese holidays computed against the "
    "lunisolar calendar (rendered on Gregorian via Lunar New Year offset).",
    tags=["traditional", "chinese", "lunisolar"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="lunar-new-year", name="春节 (Spring Festival / Lunar New Year)",
                rule=HolidayRule.LUNAR_NEW_YEAR, offset_days=0, duration_days=7),
        Holiday(id="lantern-festival", name="元宵节 (Lantern Festival)",
                rule=HolidayRule.LUNAR_NEW_YEAR, offset_days=14),
        Holiday(id="dragon-boat", name="端午节 (Dragon Boat Festival)",
                # Approx: 5th day of 5th lunar month ~ 125 days after LNY.
                rule=HolidayRule.LUNAR_NEW_YEAR, offset_days=125),
        Holiday(id="mid-autumn", name="中秋节 (Mid-Autumn Festival)",
                # 15th day of 8th lunar month ~ 220 days after LNY (approx).
                rule=HolidayRule.LUNAR_NEW_YEAR, offset_days=220),
        Holiday(id="national-day-cn", name="国庆节 (National Day, PRC)",
                rule=HolidayRule.FIXED, month=10, day=1, duration_days=7),
    ],
)


HINDU_MAJOR = HolidaySet(
    id="hindu-major",
    name="Hindu Major Holidays",
    description="Widely-observed Hindu festivals. Dates here use fixed "
    "Gregorian approximations; for precise observance the lunisolar Hindu "
    "calendars (Vikrami / Saka) should be used.",
    tags=["religious", "hindu"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="makar-sankranti", name="Makar Sankranti",
                rule=HolidayRule.FIXED, month=1, day=14),
        Holiday(id="republic-day-in", name="Republic Day (India)",
                rule=HolidayRule.FIXED, month=1, day=26),
        Holiday(id="holi", name="Holi (approximate)",
                rule=HolidayRule.FIXED, month=3, day=8),
        Holiday(id="ram-navami", name="Ram Navami (approximate)",
                rule=HolidayRule.FIXED, month=4, day=10),
        Holiday(id="independence-day-in", name="Independence Day (India)",
                rule=HolidayRule.FIXED, month=8, day=15),
        Holiday(id="janmashtami", name="Krishna Janmashtami (approximate)",
                rule=HolidayRule.FIXED, month=8, day=27),
        Holiday(id="ganesh-chaturthi", name="Ganesh Chaturthi (approximate)",
                rule=HolidayRule.FIXED, month=9, day=7),
        Holiday(id="navaratri", name="Navaratri (approximate)",
                rule=HolidayRule.FIXED, month=10, day=3, duration_days=9),
        Holiday(id="dussehra", name="Dussehra (approximate)",
                rule=HolidayRule.FIXED, month=10, day=12),
        Holiday(id="diwali", name="Diwali (approximate)",
                rule=HolidayRule.FIXED, month=11, day=1, duration_days=5),
    ],
)


BUDDHIST_MAJOR = HolidaySet(
    id="buddhist-major",
    name="Buddhist Major Holidays",
    description="Widely-observed Buddhist festivals; dates here use fixed "
    "Gregorian approximations.",
    tags=["religious", "buddhist"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="magha-puja", name="Magha Puja (approximate)",
                rule=HolidayRule.FIXED, month=2, day=24),
        Holiday(id="songkran", name="Songkran / Buddhist New Year",
                rule=HolidayRule.FIXED, month=4, day=13, duration_days=3),
        Holiday(id="vesak", name="Vesak / Buddha Day (approximate)",
                rule=HolidayRule.FIXED, month=5, day=23),
        Holiday(id="asalha-puja", name="Asalha Puja (approximate)",
                rule=HolidayRule.FIXED, month=7, day=21),
        Holiday(id="bodhi-day", name="Bodhi Day", rule=HolidayRule.FIXED,
                month=12, day=8),
    ],
)


WHEEL_OF_THE_YEAR = HolidaySet(
    id="wheel-of-the-year",
    name="Wheel of the Year (Pagan)",
    description="The eight neopagan sabbats (Wiccan Wheel of the Year). "
    "Useful for fantasy campaigns with witchcraft, druidic, or seasonal themes.",
    tags=["religious", "pagan", "fantasy"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="imbolc", name="Imbolc", rule=HolidayRule.FIXED, month=2, day=1),
        Holiday(id="ostara", name="Ostara (Spring Equinox)",
                rule=HolidayRule.FIXED, month=3, day=20),
        Holiday(id="beltane", name="Beltane", rule=HolidayRule.FIXED, month=5, day=1),
        Holiday(id="litha", name="Litha (Summer Solstice)",
                rule=HolidayRule.FIXED, month=6, day=21),
        Holiday(id="lammas", name="Lammas / Lughnasadh",
                rule=HolidayRule.FIXED, month=8, day=1),
        Holiday(id="mabon", name="Mabon (Autumn Equinox)",
                rule=HolidayRule.FIXED, month=9, day=22),
        Holiday(id="samhain", name="Samhain", rule=HolidayRule.FIXED, month=10, day=31),
        Holiday(id="yule", name="Yule (Winter Solstice)",
                rule=HolidayRule.FIXED, month=12, day=21),
    ],
)


INTERNATIONAL_OBSERVANCES = HolidaySet(
    id="international",
    name="International Observances",
    description="Secular international days widely recognized by the UN "
    "and global civil society.",
    tags=["secular", "international"],
    calendar_system=CalendarSystem.GREGORIAN,
    builtin=True,
    holidays=[
        Holiday(id="new-years-day", name="New Year's Day", rule=HolidayRule.FIXED,
                month=1, day=1),
        Holiday(id="womens-day", name="International Women's Day",
                rule=HolidayRule.FIXED, month=3, day=8),
        Holiday(id="earth-day", name="Earth Day", rule=HolidayRule.FIXED, month=4, day=22),
        Holiday(id="world-press", name="World Press Freedom Day",
                rule=HolidayRule.FIXED, month=5, day=3),
        Holiday(id="world-environment", name="World Environment Day",
                rule=HolidayRule.FIXED, month=6, day=5),
        Holiday(id="pride-day", name="LGBTQ+ Pride Day (Stonewall)",
                rule=HolidayRule.FIXED, month=6, day=28),
        Holiday(id="un-day", name="United Nations Day", rule=HolidayRule.FIXED,
                month=10, day=24),
        Holiday(id="human-rights", name="Human Rights Day",
                rule=HolidayRule.FIXED, month=12, day=10),
    ],
)


# -------------------------------------------------------------------------
# Hebrew-system holiday sets
# -------------------------------------------------------------------------


JEWISH_HOLIDAYS = HolidaySet(
    id="jewish",
    name="Jewish Holidays",
    description="Major Jewish holidays observed in the Hebrew calendar.",
    tags=["religious", "jewish"],
    calendar_system=CalendarSystem.HEBREW,
    builtin=True,
    holidays=[
        Holiday(id="rosh-hashanah", name="Rosh Hashanah", rule=HolidayRule.FIXED,
                month=1, day=1, duration_days=2),
        Holiday(id="yom-kippur", name="Yom Kippur", rule=HolidayRule.FIXED,
                month=1, day=10),
        Holiday(id="sukkot", name="Sukkot", rule=HolidayRule.FIXED,
                month=1, day=15, duration_days=7),
        Holiday(id="shemini-atzeret", name="Shemini Atzeret / Simchat Torah",
                rule=HolidayRule.FIXED, month=1, day=22, duration_days=2),
        Holiday(id="chanukah", name="Chanukah", rule=HolidayRule.FIXED,
                month=3, day=25, duration_days=8),
        Holiday(id="tu-bishvat", name="Tu BiShvat", rule=HolidayRule.FIXED,
                month=5, day=15),
        Holiday(id="purim", name="Purim", rule=HolidayRule.FIXED,
                # Adar 14 in common years; in leap years observed in Adar II.
                # Hebrew month 6 = Adar (common) or Adar I (leap); we put it
                # at month 6 day 14 here and accept the ~1-month drift in
                # leap years (fix with calendar-aware rendering later).
                month=6, day=14),
        Holiday(id="pesach", name="Pesach (Passover)", rule=HolidayRule.FIXED,
                month=7, day=15, duration_days=8),
        Holiday(id="shavuot", name="Shavuot", rule=HolidayRule.FIXED,
                month=9, day=6, duration_days=2),
        Holiday(id="tisha-bav", name="Tisha B'Av", rule=HolidayRule.FIXED,
                month=11, day=9),
    ],
)


# -------------------------------------------------------------------------
# Islamic-system holiday sets
# -------------------------------------------------------------------------


ISLAMIC_HOLIDAYS = HolidaySet(
    id="islamic",
    name="Islamic Holidays",
    description="Major Islamic holy days observed in the Hijri calendar.",
    tags=["religious", "muslim"],
    calendar_system=CalendarSystem.ISLAMIC,
    builtin=True,
    holidays=[
        Holiday(id="hijri-new-year", name="Islamic New Year", rule=HolidayRule.FIXED,
                month=1, day=1),
        Holiday(id="ashura", name="Day of Ashura", rule=HolidayRule.FIXED,
                month=1, day=10),
        Holiday(id="mawlid", name="Mawlid an-Nabi", rule=HolidayRule.FIXED,
                month=3, day=12),
        Holiday(id="isra-miraj", name="Isra and Mi'raj", rule=HolidayRule.FIXED,
                month=7, day=27),
        Holiday(id="ramadan-start", name="Beginning of Ramadan", rule=HolidayRule.FIXED,
                month=9, day=1, duration_days=30),
        Holiday(id="laylat-al-qadr", name="Laylat al-Qadr", rule=HolidayRule.FIXED,
                month=9, day=27),
        Holiday(id="eid-al-fitr", name="Eid al-Fitr", rule=HolidayRule.FIXED,
                month=10, day=1, duration_days=3),
        Holiday(id="day-of-arafah", name="Day of Arafah", rule=HolidayRule.FIXED,
                month=12, day=9),
        Holiday(id="eid-al-adha", name="Eid al-Adha", rule=HolidayRule.FIXED,
                month=12, day=10, duration_days=4),
    ],
)


BUILTIN_HOLIDAY_SETS: dict[str, HolidaySet] = {
    s.id: s for s in [
        US_FEDERAL,
        UK_BANK,
        CANADIAN_FEDERAL,
        EU_COMMON,
        MEXICAN_PUBLIC,
        CHRISTIAN_WESTERN,
        CHRISTIAN_ORTHODOX,
        JAPANESE_PUBLIC,
        CHINESE_TRADITIONAL,
        HINDU_MAJOR,
        BUDDHIST_MAJOR,
        WHEEL_OF_THE_YEAR,
        INTERNATIONAL_OBSERVANCES,
        JEWISH_HOLIDAYS,
        ISLAMIC_HOLIDAYS,
    ]
}
