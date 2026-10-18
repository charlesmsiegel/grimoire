import textwrap
import threading

import pytest

from grimoire.store import birthdays
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


def test_numeric_native_birthdate_remains_a_full_date(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    calendars_dir = tmp_path / "calendars"
    calendars_dir.mkdir()
    (calendars_dir / "plugin_test.py").write_text(_PROVIDER_SRC, encoding="utf-8")
    provider = get_provider(_cfg("plugin-test-calendar"))

    assert birthdays.facts(provider, "5", "5") == (0, True)
    assert birthdays._when(provider, "5", 5) == ("today", 0)
    assert birthdays.crossed(provider, 4, 5, [{"name": "Mara", "birth": "5"}]) == [
        {"name": "Mara", "native": "5", "friendly": "day 5", "age": 0}]


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


def test_concurrent_first_lookups_import_a_plugin_once(tmp_path, monkeypatch):
    """Every lookup asks for the plugins first, from whatever worker thread it
    runs on. A plugin whose top level is slow to run is the window: without a
    guard, each lookup that arrived in it imported the file again."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    calendars_dir = tmp_path / "calendars"
    calendars_dir.mkdir()
    imports = calendars_dir / "imports.log"
    slow = textwrap.dedent(f"""
        import time
        with open({str(imports)!r}, "a", encoding="utf-8") as f:
            f.write("imported\\n")
        time.sleep(0.2)
    """) + _PROVIDER_SRC
    (calendars_dir / "plugin_slow.py").write_text(slow, encoding="utf-8")

    start = threading.Barrier(4)
    found = []

    def lookup():
        start.wait()
        found.append(get_provider(_cfg("plugin-test-calendar")).format(5))

    threads = [threading.Thread(target=lookup) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert found == ["5"] * 4
    assert imports.read_text(encoding="utf-8").splitlines() == ["imported"]
