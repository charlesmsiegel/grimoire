"""Loads user-authored calendar providers from ``<GRIMOIRE_HOME>/calendars/``.

Homebrew/personal calendars (fictional settings, house rules) don't belong in
the git-tracked codebase alongside `gregorian`/`harptos`/`hebrew` — they live
in the user's own data directory instead. Any ``.py`` file dropped there is
imported once per process and is expected to call `register()` itself, the
same way the built-in providers in this package do:

    from grimoire.store.calendars.base import CalendarProvider, register

    class MyProvider(CalendarProvider):
        ...

    register("my-calendar", MyProvider, "My Calendar")
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ..paths import home

_loaded: set[Path] = set()


def load_custom_providers() -> None:
    """Import every not-yet-loaded ``<home>/calendars/*.py``.

    Best-effort: a file that fails to import is skipped rather than crashing
    calendar lookups for everyone, and is retried on the next call (so fixing
    it takes effect without a restart).
    """
    directory = home() / "calendars"
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_") or path in _loaded:
            continue
        module_name = f"grimoire_custom_calendar_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception:  # noqa: BLE001 - a homebrew calendar must not break the calendar list
            sys.modules.pop(module_name, None)
        else:
            _loaded.add(path)
