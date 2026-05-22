"""Calendar + holiday-set REST routes.

Mounted under the library prefix in main.py:

    GET    /api/library/calendars            list (built-in + custom)
    POST   /api/library/calendars            create custom
    GET    /api/library/calendars/{id}       fetch (built-in or custom)
    PATCH  /api/library/calendars/{id}       update (custom only)
    DELETE /api/library/calendars/{id}       delete (custom only)

    GET    /api/library/holiday-sets         list
    POST   /api/library/holiday-sets         create custom
    GET    /api/library/holiday-sets/{id}    fetch
    PATCH  /api/library/holiday-sets/{id}    update (custom only)
    DELETE /api/library/holiday-sets/{id}    delete (custom only)

    POST   /api/library/calendars/convert    convert a date across calendars
    GET    /api/library/calendars/{id}/holidays?year=YYYY&sets=a,b,c
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from grimoire.api.deps import CalendarDep
from grimoire.api.util import map_lookup_errors, to_payload

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateCalendarPayload(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    # `system` defaults to "custom"; built-in systems can be created
    # only with `custom` (other built-ins are reserved).
    system: str = "custom"
    custom: dict[str, Any] | None = None
    date_format: str = ""
    source: str = "user"


class UpdateCalendarPayload(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    custom: dict[str, Any] | None = None
    date_format: str | None = None
    source: str = "user"


class CreateHolidaySetPayload(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    calendar_system: str = "gregorian"
    holidays: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "user"


class UpdateHolidaySetPayload(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    calendar_system: str | None = None
    holidays: list[dict[str, Any]] | None = None
    source: str = "user"


class ConvertDatePayload(BaseModel):
    from_calendar_id: str
    to_calendar_ids: list[str]
    year: int
    month: int
    day: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/library/calendars")
async def list_calendars(calendar: CalendarDep) -> Any:
    return to_payload(await calendar.list_calendars())


@router.post("/library/calendars", status_code=201)
async def create_calendar(payload: CreateCalendarPayload, calendar: CalendarDep) -> Any:
    try:
        return to_payload(
            await calendar.create_calendar(payload.model_dump(), source=payload.source)
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/library/calendars/convert")
async def convert_date(payload: ConvertDatePayload, calendar: CalendarDep) -> Any:
    try:
        result = await calendar.convert_date(
            payload.from_calendar_id,
            payload.to_calendar_ids,
            payload.year,
            payload.month,
            payload.day,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/library/calendars/{calendar_id}")
async def get_calendar(calendar_id: str, calendar: CalendarDep) -> Any:
    try:
        return to_payload(await calendar.get_calendar(calendar_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/calendars/{calendar_id}/holidays")
async def get_holidays_in_year(
    calendar_id: str,
    calendar: CalendarDep,
    year: int = Query(...),
    sets: str = Query("", description="Comma-separated holiday set ids"),
) -> Any:
    set_ids = [s for s in sets.split(",") if s.strip()]
    try:
        result = await calendar.holidays_in_year(set_ids, year, anchor_calendar_id=calendar_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.patch("/library/calendars/{calendar_id}")
async def update_calendar(
    calendar_id: str, payload: UpdateCalendarPayload, calendar: CalendarDep
) -> Any:
    body = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return to_payload(await calendar.update_calendar(calendar_id, body, source=payload.source))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/library/calendars/{calendar_id}", status_code=204)
async def delete_calendar(
    calendar_id: str,
    calendar: CalendarDep,
    source: str = "user",
) -> None:
    try:
        await calendar.delete_calendar(calendar_id, source=source)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/holiday-sets")
async def list_holiday_sets(calendar: CalendarDep) -> Any:
    return to_payload(await calendar.list_holiday_sets())


@router.post("/library/holiday-sets", status_code=201)
async def create_holiday_set(payload: CreateHolidaySetPayload, calendar: CalendarDep) -> Any:
    try:
        return to_payload(
            await calendar.create_holiday_set(payload.model_dump(), source=payload.source)
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/library/holiday-sets/{set_id}")
async def get_holiday_set(set_id: str, calendar: CalendarDep) -> Any:
    try:
        return to_payload(await calendar.get_holiday_set(set_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.patch("/library/holiday-sets/{set_id}")
async def update_holiday_set(
    set_id: str, payload: UpdateHolidaySetPayload, calendar: CalendarDep
) -> Any:
    body = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return to_payload(await calendar.update_holiday_set(set_id, body, source=payload.source))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/library/holiday-sets/{set_id}", status_code=204)
async def delete_holiday_set(
    set_id: str,
    calendar: CalendarDep,
    source: str = "user",
) -> None:
    try:
        await calendar.delete_holiday_set(set_id, source=source)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
