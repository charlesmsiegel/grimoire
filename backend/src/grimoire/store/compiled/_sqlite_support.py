"""Optional SQLite availability and its closed failure vocabulary."""

from __future__ import annotations

from types import ModuleType

_sqlite: ModuleType | None
try:
    import sqlite3 as _sqlite_available
except (ImportError, OSError):
    _sqlite = None
else:
    _sqlite = _sqlite_available

_CACHE_ERRORS = (OSError, ValueError, TypeError, RuntimeError, MemoryError) + (
    (_sqlite.Error,) if _sqlite is not None else ())
