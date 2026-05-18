"""Process-wide registry of named test fixtures.

``TestApp.with_fixtures(name, root=...)`` looks here when no explicit
``registry=`` argument is passed. Tests register fixtures (typically
from ``conftest.py``) via :func:`register`; named lookups happen
through :func:`get`.

The registry is intentionally a simple module-level dict — fixtures
are pure data, registration is idempotent under the same name, and
pytest tears the process down between sessions.
"""

from __future__ import annotations

from grimoire.testing.app import TestAppFixture
from grimoire.testing.fixtures import LibraryCampaignFixture

AnyFixture = TestAppFixture | LibraryCampaignFixture

FIXTURES: dict[str, AnyFixture] = {}


def register(name: str, fixture: AnyFixture) -> None:
    """Register ``fixture`` under ``name``.

    Re-registering the same name replaces the previous entry — that
    keeps test reloads forgiving in development.
    """
    FIXTURES[name] = fixture


def get(name: str) -> AnyFixture:
    """Return the fixture registered under ``name`` or raise ``KeyError``."""
    if name not in FIXTURES:
        raise KeyError(f"unknown fixture {name!r}; registered: {sorted(FIXTURES)}")
    return FIXTURES[name]


def clear() -> None:
    """Drop every registered fixture (useful in tests)."""
    FIXTURES.clear()


__all__ = ["FIXTURES", "AnyFixture", "clear", "get", "register"]
