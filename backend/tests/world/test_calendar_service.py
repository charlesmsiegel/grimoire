"""CalendarService integration tests against a real LibraryService."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.library import LibraryConflictError, LibraryNotFoundError, LibraryService
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.world.calendar_service import CalendarService


@pytest.fixture
async def calendar_svc(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(tmp_path / "test.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    store = StateStore(db, data_root)
    library = LibraryService(store)
    try:
        yield CalendarService(library)
    finally:
        await db.close()


async def test_list_includes_all_builtins(calendar_svc: CalendarService) -> None:
    cals = await calendar_svc.list_calendars()
    ids = {c.id for c in cals}
    assert {
        "gregorian",
        "julian",
        "hebrew",
        "islamic",
        "persian",
        "chinese",
        "japanese",
        "indian-saka",
        "ethiopian",
        "coptic",
        "bahai",
        "buddhist",
        "iso-week",
        "stardate",
    } <= ids


async def test_list_builtin_holiday_sets(calendar_svc: CalendarService) -> None:
    sets = await calendar_svc.list_holiday_sets()
    ids = {s.id for s in sets}
    assert {
        "us-federal",
        "jewish",
        "islamic",
        "japanese-public",
        "chinese-traditional",
        "wheel-of-the-year",
    } <= ids


async def test_get_builtin_calendar(calendar_svc: CalendarService) -> None:
    greg = await calendar_svc.get_calendar("gregorian")
    assert greg.builtin is True
    assert greg.system.value == "gregorian"


async def test_cannot_create_calendar_with_builtin_id(calendar_svc: CalendarService) -> None:
    with pytest.raises(LibraryConflictError):
        await calendar_svc.create_calendar({"id": "gregorian", "name": "My Greg"})


async def test_cannot_edit_or_delete_builtin(calendar_svc: CalendarService) -> None:
    with pytest.raises(LibraryConflictError):
        await calendar_svc.update_calendar("gregorian", {"name": "Edited"})
    with pytest.raises(LibraryConflictError):
        await calendar_svc.delete_calendar("gregorian")


async def test_create_and_get_custom_calendar(calendar_svc: CalendarService) -> None:
    payload = {
        "id": "fantasy-cal",
        "name": "Fantasy Calendar",
        "description": "An imagined fantasy calendar.",
        "tags": ["fantasy"],
        "system": "custom",
        "custom": {
            "months": [
                {"name": "Sunmoon", "days": 30},
                {"name": "Starwane", "days": 30},
                {"name": "Frosthold", "days": 28},
            ],
            "days_per_week": 6,
            "week_day_names": ["Mon", "Sec", "Tre", "Qua", "Pen", "Sex"],
            "leap_rule": {"kind": "none"},
            "epoch_jdn": 2400000,
            "era_name": "AC",
        },
    }
    created = await calendar_svc.create_calendar(payload)
    assert created.id == "fantasy-cal"
    assert created.builtin is False
    assert created.custom is not None
    assert len(created.custom.months) == 3

    fetched = await calendar_svc.get_calendar("fantasy-cal")
    assert fetched.name == "Fantasy Calendar"


async def test_convert_date_via_jdn(calendar_svc: CalendarService) -> None:
    """A Gregorian date converts to multiple calendars consistently."""
    result = await calendar_svc.convert_date(
        from_calendar_id="gregorian",
        to_calendar_ids=["hebrew", "islamic", "buddhist"],
        year=2025,
        month=5,
        day=22,
    )
    assert "hebrew" in result and "islamic" in result and "buddhist" in result
    # Buddhist = Gregorian + 543
    assert result["buddhist"].year == 2568


async def test_holidays_in_year_includes_thanksgiving(calendar_svc: CalendarService) -> None:
    occs = await calendar_svc.holidays_in_year(["us-federal"], 2024)
    names = {o.name for o in occs}
    assert "Thanksgiving" in names
    assert "Independence Day" in names


async def test_holidays_in_year_jewish_against_gregorian_anchor(
    calendar_svc: CalendarService,
) -> None:
    """Hebrew-system holidays render against a Gregorian-year window."""
    occs = await calendar_svc.holidays_in_year(["jewish"], 2024)
    ids = {o.holiday_id for o in occs}
    # Pesach + Rosh Hashanah should both fall in Gregorian 2024.
    assert "rosh-hashanah" in ids
    assert "pesach" in ids


async def test_custom_holiday_set_round_trip(calendar_svc: CalendarService) -> None:
    payload = {
        "id": "my-holidays",
        "name": "My Holidays",
        "tags": ["custom"],
        "calendar_system": "gregorian",
        "holidays": [
            {"id": "blorbday", "name": "Blorbday", "rule": "fixed", "month": 3, "day": 17},
        ],
    }
    created = await calendar_svc.create_holiday_set(payload)
    assert created.id == "my-holidays"
    fetched = await calendar_svc.get_holiday_set("my-holidays")
    assert len(fetched.holidays) == 1
    assert fetched.holidays[0].name == "Blorbday"


async def test_delete_custom_calendar(calendar_svc: CalendarService) -> None:
    await calendar_svc.create_calendar(
        {
            "id": "throwaway",
            "name": "Throwaway",
            "system": "custom",
            "custom": {"months": [{"name": "x", "days": 1}]},
        }
    )
    await calendar_svc.delete_calendar("throwaway")
    with pytest.raises(LibraryNotFoundError):
        await calendar_svc.get_calendar("throwaway")
