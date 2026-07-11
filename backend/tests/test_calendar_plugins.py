import textwrap

import pytest

from grimoire.store.calendars import CalendarError, get_provider, list_providers

_PROVIDER_SRC = textwrap.dedent(
    """
    from grimoire.store.calendars.base import CalendarProvider, register

    class _PluginTestProvider(CalendarProvider):
        def __init__(self, config):
            self.custom_holidays = config.get("custom_holidays", []) or []

        def parse(self, native):
            return int(native)

        def format(self, fixed):
            return str(fixed)

        def describe(self, fixed):
            return {"year": fixed, "month": 1, "month_name": "Onlymonth", "day": 1,
                    "weekday_name": "Oneday", "weekday_index": 0, "friendly": f"day {fixed}"}

        def holidays(self, start_fixed, end_fixed):
            return []

        def months(self, year):
            return [{"key": "01", "name": "Onlymonth", "days": 1}]

    register("plugin-test-calendar", _PluginTestProvider, "Plugin Test Calendar")
    """
)


def _cfg(provider: str) -> dict:
    return {"provider": provider, "region": "", "custom_holidays": [], "anchor": None}


def test_loads_a_custom_provider_dropped_into_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    calendars_dir = tmp_path / "calendars"
    calendars_dir.mkdir()
    (calendars_dir / "plugin_test.py").write_text(_PROVIDER_SRC, encoding="utf-8")

    provider = get_provider(_cfg("plugin-test-calendar"))
    assert provider.format(provider.parse("5")) == "5"

    names = {p["id"]: p["name"] for p in list_providers()}
    assert names["plugin-test-calendar"] == "Plugin Test Calendar"
    assert "gregorian" in names  # built-ins still show up alongside the user-authored one


def test_unknown_provider_still_raises_after_scanning_for_plugins(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with pytest.raises(CalendarError):
        get_provider(_cfg("nope-not-real"))


def test_a_broken_plugin_file_is_skipped_without_crashing_other_lookups(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    calendars_dir = tmp_path / "calendars"
    calendars_dir.mkdir()
    (calendars_dir / "broken.py").write_text("this is not valid python (((", encoding="utf-8")

    provider = get_provider(_cfg("gregorian"))
    assert provider is not None
