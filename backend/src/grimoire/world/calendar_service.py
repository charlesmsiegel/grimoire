"""High-level Calendar service.

Combines the in-code built-in calendar/holiday-set registry with the
LibraryService's user-defined custom entries. Provides:

  * `list_calendars()` / `get_calendar(id)` / CRUD for custom calendars
  * `list_holiday_sets()` / `get_holiday_set(id)` / CRUD for custom sets
  * `convert_date(from_id, to_id, date)` — JDN reconciliation between
    any two calendars (built-in or custom)
  * `holidays_in_year(calendar_id, holiday_set_ids, year)` — resolve
    movable holidays for a given year in the display calendar
"""

from __future__ import annotations

from typing import Any

from grimoire.library.errors import (
    LibraryConflictError,
    LibraryNotFoundError,
)
from grimoire.library.service import LibraryService
from grimoire.types.calendar import (
    Calendar,
    CalendarDate,
    CalendarSystem,
    CustomCalendarConfig,
    HolidayOccurrence,
    HolidaySet,
    LeapRule,
    LeapRuleKind,
)
from grimoire.types.composition import LibraryEntity

from .calendars import (
    BUILTIN_CALENDARS,
    engine_for,
    get_builtin_calendar,
    get_builtin_holiday_set,
    is_builtin_calendar,
    is_builtin_holiday_set,
    jdn_weekday_name,
    list_builtin_calendars,
    list_builtin_holiday_sets,
    occurrences_in_year,
)


def _calendar_from_entity(entity: LibraryEntity) -> Calendar:
    """Build a Calendar from a library_index row's frontmatter."""
    fm = entity.frontmatter or {}
    custom_raw = fm.get("custom") or {}
    leap_raw = (custom_raw or {}).get("leap_rule") or {}
    months_raw = (custom_raw or {}).get("months") or []
    seasons_raw = (custom_raw or {}).get("seasons") or []
    custom: CustomCalendarConfig | None = None
    if fm.get("system") == CalendarSystem.CUSTOM.value or fm.get("system") == "custom":
        custom = CustomCalendarConfig(
            months=[
                {"name": m.get("name") or f"M{i+1}", "days": int(m.get("days") or 30),
                 "short_name": m.get("short_name") or ""}  # type: ignore[arg-type]
                for i, m in enumerate(months_raw)
            ],
            days_per_week=int(custom_raw.get("days_per_week") or 7),
            week_day_names=list(custom_raw.get("week_day_names") or []),
            seasons=[
                {"name": s.get("name") or "", "start_month": int(s.get("start_month") or 1),
                 "start_day": int(s.get("start_day") or 1),
                 "palette": s.get("palette") or ""}  # type: ignore[arg-type]
                for s in seasons_raw
            ],
            leap_rule=LeapRule(
                kind=LeapRuleKind(leap_raw.get("kind") or "none"),
                cycle_short=int(leap_raw.get("cycle_short") or 4),
                cycle_skip=int(leap_raw.get("cycle_skip") or 100),
                cycle_keep=int(leap_raw.get("cycle_keep") or 400),
                leap_days=int(leap_raw.get("leap_days") or 1),
                leap_day_month=int(leap_raw.get("leap_day_month") or 2),
                cycle_years=int(leap_raw.get("cycle_years") or 0),
                leap_years_in_cycle=list(leap_raw.get("leap_years_in_cycle") or []),
                leap_month_name=str(leap_raw.get("leap_month_name") or ""),
                leap_month_days=int(leap_raw.get("leap_month_days") or 30),
                leap_month_position=int(leap_raw.get("leap_month_position") or 1),
            ),
            epoch_jdn=int(custom_raw.get("epoch_jdn") or 1721426),
            era_name=str(custom_raw.get("era_name") or ""),
        )
    return Calendar(
        id=entity.asset_id,
        name=fm.get("name") or entity.name or entity.asset_id,
        description=fm.get("description") or "",
        tags=list(entity.tags),
        system=CalendarSystem(fm.get("system") or "custom"),
        builtin=False,
        custom=custom,
        date_format=fm.get("date_format") or "",
        version=entity.version,
    )


def _holiday_set_from_entity(entity: LibraryEntity) -> HolidaySet:
    fm = entity.frontmatter or {}
    holidays_raw = fm.get("holidays") or []
    from grimoire.types.calendar import Holiday, HolidayRule

    holidays = []
    for h in holidays_raw:
        holidays.append(
            Holiday(
                id=h.get("id") or "",
                name=h.get("name") or "",
                description=h.get("description") or "",
                tags=list(h.get("tags") or []),
                rule=HolidayRule(h.get("rule") or "fixed"),
                month=int(h.get("month") or 1),
                day=int(h.get("day") or 1),
                weekday=int(h.get("weekday") or 0),
                nth=int(h.get("nth") or 1),
                weekday_month=int(h.get("weekday_month") or 1),
                offset_days=int(h.get("offset_days") or 0),
                duration_days=int(h.get("duration_days") or 1),
            )
        )
    return HolidaySet(
        id=entity.asset_id,
        name=fm.get("name") or entity.name or entity.asset_id,
        description=fm.get("description") or "",
        tags=list(entity.tags),
        calendar_system=CalendarSystem(fm.get("calendar_system") or "gregorian"),
        holidays=holidays,
        builtin=False,
        version=entity.version,
    )


def _calendar_to_frontmatter(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip a write payload to the frontmatter shape we persist on disk."""
    out: dict[str, Any] = {
        "name": payload.get("name") or payload.get("id"),
        "description": payload.get("description") or "",
        "tags": list(payload.get("tags") or []),
        "system": payload.get("system") or "custom",
    }
    if payload.get("date_format"):
        out["date_format"] = payload["date_format"]
    if payload.get("custom"):
        out["custom"] = payload["custom"]
    return out


def _holiday_set_to_frontmatter(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": payload.get("name") or payload.get("id"),
        "description": payload.get("description") or "",
        "tags": list(payload.get("tags") or []),
        "calendar_system": payload.get("calendar_system") or "gregorian",
        "holidays": list(payload.get("holidays") or []),
    }


class CalendarService:
    """Public Calendar surface backed by built-ins + the Library service."""

    def __init__(self, library: LibraryService) -> None:
        self.library = library

    # -------- listing / fetch --------------------------------------------

    async def list_calendars(self) -> list[Calendar]:
        result = list_builtin_calendars()
        customs = await self.library.list_custom_calendars()
        for entity in customs:
            result.append(_calendar_from_entity(entity))
        return result

    async def get_calendar(self, calendar_id: str) -> Calendar:
        builtin = get_builtin_calendar(calendar_id)
        if builtin is not None:
            return builtin
        try:
            entity = await self.library.get_custom_calendar(calendar_id)
        except LibraryNotFoundError as exc:
            raise LibraryNotFoundError(f"calendar {calendar_id!r} not found") from exc
        return _calendar_from_entity(entity)

    async def list_holiday_sets(self) -> list[HolidaySet]:
        result = list_builtin_holiday_sets()
        customs = await self.library.list_custom_holiday_sets()
        for entity in customs:
            result.append(_holiday_set_from_entity(entity))
        return result

    async def get_holiday_set(self, set_id: str) -> HolidaySet:
        builtin = get_builtin_holiday_set(set_id)
        if builtin is not None:
            return builtin
        try:
            entity = await self.library.get_custom_holiday_set(set_id)
        except LibraryNotFoundError as exc:
            raise LibraryNotFoundError(f"holiday set {set_id!r} not found") from exc
        return _holiday_set_from_entity(entity)

    # -------- write surface (custom only) --------------------------------

    async def create_calendar(self, payload: dict[str, Any], *, source: str = "user") -> Calendar:
        id_ = str(payload.get("id") or "").strip()
        if not id_:
            raise ValueError("calendar id required")
        if is_builtin_calendar(id_):
            raise LibraryConflictError(
                f"calendar id {id_!r} is reserved for a built-in calendar"
            )
        # Reject if a custom one already exists with this id.
        try:
            await self.library.get_custom_calendar(id_)
        except LibraryNotFoundError:
            pass
        else:
            raise LibraryConflictError(f"calendar {id_!r} already exists")
        fm = _calendar_to_frontmatter(payload)
        entity = await self.library.write_custom_calendar(
            id_, frontmatter=fm, source=source
        )
        return _calendar_from_entity(entity)

    async def update_calendar(
        self, calendar_id: str, payload: dict[str, Any], *, source: str = "user"
    ) -> Calendar:
        if is_builtin_calendar(calendar_id):
            raise LibraryConflictError(
                f"calendar {calendar_id!r} is built-in and cannot be edited"
            )
        existing = await self.library.get_custom_calendar(calendar_id)
        fm = dict(existing.frontmatter or {})
        merged = {**fm, **_calendar_to_frontmatter({**fm, **payload, "id": calendar_id})}
        entity = await self.library.write_custom_calendar(
            calendar_id, frontmatter=merged, source=source
        )
        return _calendar_from_entity(entity)

    async def delete_calendar(self, calendar_id: str, *, source: str = "user") -> None:
        if is_builtin_calendar(calendar_id):
            raise LibraryConflictError(
                f"calendar {calendar_id!r} is built-in and cannot be deleted"
            )
        await self.library.delete_custom_calendar(calendar_id, source=source)

    async def create_holiday_set(
        self, payload: dict[str, Any], *, source: str = "user"
    ) -> HolidaySet:
        id_ = str(payload.get("id") or "").strip()
        if not id_:
            raise ValueError("holiday set id required")
        if is_builtin_holiday_set(id_):
            raise LibraryConflictError(
                f"holiday set id {id_!r} is reserved for a built-in set"
            )
        try:
            await self.library.get_custom_holiday_set(id_)
        except LibraryNotFoundError:
            pass
        else:
            raise LibraryConflictError(f"holiday set {id_!r} already exists")
        fm = _holiday_set_to_frontmatter(payload)
        entity = await self.library.write_custom_holiday_set(
            id_, frontmatter=fm, source=source
        )
        return _holiday_set_from_entity(entity)

    async def update_holiday_set(
        self, set_id: str, payload: dict[str, Any], *, source: str = "user"
    ) -> HolidaySet:
        if is_builtin_holiday_set(set_id):
            raise LibraryConflictError(
                f"holiday set {set_id!r} is built-in and cannot be edited"
            )
        existing = await self.library.get_custom_holiday_set(set_id)
        fm = dict(existing.frontmatter or {})
        merged = {**fm, **_holiday_set_to_frontmatter({**fm, **payload, "id": set_id})}
        entity = await self.library.write_custom_holiday_set(
            set_id, frontmatter=merged, source=source
        )
        return _holiday_set_from_entity(entity)

    async def delete_holiday_set(self, set_id: str, *, source: str = "user") -> None:
        if is_builtin_holiday_set(set_id):
            raise LibraryConflictError(
                f"holiday set {set_id!r} is built-in and cannot be deleted"
            )
        await self.library.delete_custom_holiday_set(set_id, source=source)

    # -------- conversion / reconciliation --------------------------------

    async def date_to_jdn(
        self, calendar_id: str, year: int, month: int, day: int
    ) -> int:
        calendar = await self.get_calendar(calendar_id)
        engine = engine_for(calendar)
        return engine.to_jdn(year, month, day)

    async def date_from_jdn(self, calendar_id: str, jdn: int) -> CalendarDate:
        calendar = await self.get_calendar(calendar_id)
        engine = engine_for(calendar)
        parts = engine.from_jdn(jdn)
        return CalendarDate(
            calendar_id=calendar_id,
            jdn=jdn,
            year=parts.year,
            month=parts.month,
            day=parts.day,
            formatted=engine.format(parts),
            era=parts.era,
            weekday=jdn_weekday_name(jdn),
        )

    async def convert_date(
        self,
        from_calendar_id: str,
        to_calendar_ids: list[str],
        year: int,
        month: int,
        day: int,
    ) -> dict[str, CalendarDate]:
        """Convert one date into representations in many calendars."""
        jdn = await self.date_to_jdn(from_calendar_id, year, month, day)
        out: dict[str, CalendarDate] = {}
        for cid in to_calendar_ids:
            out[cid] = await self.date_from_jdn(cid, jdn)
        return out

    async def holidays_in_year(
        self,
        holiday_set_ids: list[str],
        year: int,
        *,
        anchor_calendar_id: str = "gregorian",
    ) -> list[HolidayOccurrence]:
        """Resolve all holidays from `holiday_set_ids` for the given year.

        `year` is interpreted in `anchor_calendar_id`'s system. Each
        holiday set is expanded in its own calendar system's year and
        then filtered/sorted by the JDN range corresponding to the
        anchor year.
        """
        anchor = await self.get_calendar(anchor_calendar_id)
        anchor_engine = engine_for(anchor)
        # JDN range for the anchor year [1/1, 12/31 (or last month/last day)].
        try:
            year_start = anchor_engine.to_jdn(year, 1, 1)
        except Exception:
            return []
        try:
            year_end = anchor_engine.to_jdn(year + 1, 1, 1) - 1
        except Exception:
            year_end = year_start + 366

        out: list[HolidayOccurrence] = []
        for set_id in holiday_set_ids:
            try:
                hs = await self.get_holiday_set(set_id)
            except LibraryNotFoundError:
                continue
            # Find the native years of this holiday set's system overlapping
            # [year_start, year_end].
            from .calendars.registry import _ENGINE_FACTORIES

            sys_engine_factory = _ENGINE_FACTORIES.get(hs.calendar_system)
            if sys_engine_factory is None:
                continue
            sys_engine = sys_engine_factory()
            y_lo = sys_engine.from_jdn(year_start).year
            y_hi = sys_engine.from_jdn(year_end).year
            for y in range(y_lo - 1, y_hi + 2):
                for occ in occurrences_in_year(hs, y):
                    if occ.jdn_end >= year_start and occ.jdn_start <= year_end:
                        out.append(occ)
        out.sort(key=lambda o: o.jdn_start)
        return out
