"""GET /api/calendars/{provider}/year — the Library's calendar reference.

The thing worth holding: **a calendar has no holidays on its own.** They fall
out of a configured one, and the config is per-provider in a way that does not
generalise — `gregorian` yields none without a region, `hebrew` switches on
Israel-vs-diaspora, a homebrew calendar's are its own. A page in the Library has
no world to take that from, so the route takes it.

And the year is the calendar's own. A Hebrew year is around 5786; defaulting to
a Gregorian one asks most calendars for a date they cannot represent, which is
exactly how this route failed the first time it was run.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


def _year(client, provider: str, **params) -> dict:
    r = client.get(f"/api/calendars/{provider}/year", params=params or None)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_shipped_calendars_are_listed(client):
    ids = [p["id"] for p in client.get("/api/calendars/providers").json()["providers"]]
    # Only these two ship; anything else is a plugin from the user's own store.
    assert "gregorian" in ids
    assert "hebrew" in ids


def test_the_year_defaults_to_the_calendars_own(client):
    """Not to a Gregorian one.

    The Hebrew year is roughly 3760 ahead, so a shared default is a date the
    calendar cannot represent -- and it raised rather than returning something
    wrong, which is the good failure but still a failure.
    """
    greg = _year(client, "gregorian")["year"]
    heb = _year(client, "hebrew")["year"]
    assert heb > greg + 3000


def test_a_gregorian_year_has_twelve_months_that_name_themselves(client):
    body = _year(client, "gregorian", year=2026)
    assert len(body["months"]) == 12
    assert body["months"][0]["name"] == "January"
    assert body["months"][0]["days"] == 31
    # February in a common year, so the length is really being read rather than
    # assumed from a table.
    assert body["months"][1]["days"] == 28


def test_a_leap_year_is_read_not_assumed(client):
    assert _year(client, "gregorian", year=2024)["months"][1]["days"] == 29


def test_gregorian_has_no_holidays_until_it_is_given_a_region(client):
    """The distinction the page's region control exists for.

    "No holidays" here does not mean the year is empty -- it means nobody has
    said whose holidays to show. Rendering that as a calendar with no
    observances would be a wrong answer to a question that was never asked.
    """
    assert _year(client, "gregorian", year=2026)["holidays"] == []
    named = _year(client, "gregorian", year=2026, region="US")["holidays"]
    assert named
    assert any(h["name"] == "New Year's Day" for h in named)


def test_a_region_changes_which_holidays_land(client):
    us = {h["name"] for h in _year(client, "gregorian", year=2026, region="US")["holidays"]}
    gb = {h["name"] for h in _year(client, "gregorian", year=2026, region="GB")["holidays"]}
    assert us != gb


def test_hebrew_observances_switch_on_israel(client):
    """`region` means something different here, which is why the page names it
    differently: an observance, not a country's public holidays."""
    diaspora = _year(client, "hebrew")["holidays"]
    israel = _year(client, "hebrew", region="IL")["holidays"]
    assert diaspora and israel
    # The diaspora keeps the second days that Israel does not.
    assert len(diaspora) > len(israel)


def test_every_holiday_is_placed_in_the_calendars_own_terms(client):
    """A bare fixed day is not a place in a calendar the reader is looking at."""
    for h in _year(client, "gregorian", year=2026, region="US")["holidays"]:
        assert h["month"], h
        assert h["day"], h
        assert isinstance(h["fixed"], int)


def test_holidays_fall_inside_the_months_that_are_returned(client):
    """The page groups observances under months, so a holiday whose month is not
    among them would silently vanish from the page rather than misplace itself."""
    body = _year(client, "gregorian", year=2026, region="US")
    keys = {m["key"] for m in body["months"]}
    assert {h["month_key"] for h in body["holidays"]} <= keys
    # ...and it is not the raw month NUMBER, which matches no key in either
    # shipped calendar -- grouping on that renders a year with no holidays.
    assert body["holidays"][0]["month_key"] != str(body["holidays"][0]["month"])


def test_an_unknown_calendar_is_a_404(client):
    assert client.get("/api/calendars/not-a-calendar/year").status_code == 404


def test_a_year_the_calendar_cannot_represent_is_refused_not_guessed(client):
    """400 rather than an empty year: "this calendar does not go there" and
    "there is nothing there" are different answers."""
    r = client.get("/api/calendars/hebrew/year", params={"year": 1})
    assert r.status_code == 400


def test_reading_a_calendar_writes_no_calendar_state(client, tmp_path):
    """A reference view. It builds a throwaway config and reads it.

    Scoped to calendars rather than to the whole store directory: the store
    creates `config.md`, `worlds/` and the rest lazily, on whichever call first
    needs them, so a before/after listing of the root is a claim about that
    laziness and not about this route. What must hold is that asking what a
    calendar observes never becomes a calendar anyone has configured.
    """
    _year(client, "gregorian", year=2026, region="US")
    _year(client, "hebrew", region="IL")
    _year(client, "gregorian", year=2030, region="GB")

    # Where a user's own providers are loaded FROM, and where a written
    # calendar would land. Reading creates neither.
    assert not (tmp_path / "calendars").exists()

    # ...and no world or campaign gained a calendar config from being asked.
    for world in (tmp_path / "worlds").glob("*/"):
        assert not (world / "calendar.json").exists()
