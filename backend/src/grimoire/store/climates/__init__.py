"""Two-tier climate registry.

Shipped presets live in ``presets/`` beside this module; private climates live
in ``<GRIMOIRE_HOME>/climates/`` and shadow a preset of the same id. Mirrors the
split in ``calendars/plugins.py``: a malformed private file is skipped rather
than fatal, and is picked up again once fixed — no restart, no cache to stale.
"""

from __future__ import annotations

import json
from pathlib import Path

from .schema import ClimateError, validate  # noqa: F401  (re-exported)
from ..paths import home

FALLBACK_ID = "temperate-interior"

_PRESETS = Path(__file__).parent / "presets"


def _read(path: Path) -> dict | None:
    """A climate document, or None if the file is unreadable or invalid.

    Catches broadly on purpose. `validate` translates the shapes it anticipates
    into `ClimateError`, but a hand-edited file can be malformed in ways it does
    not reach — and one bad private file must never abort the registry scan and
    take prompt generation down with it. Skipped, not fatal, retried next call.
    """
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ClimateError):
        return None
    except Exception:  # malformed beyond what validate anticipates
        return None


def _custom_dir() -> Path:
    return home() / "climates"


def _scan(directory: Path) -> dict[str, dict]:
    if not directory.is_dir():
        return {}
    out: dict[str, dict] = {}
    for path in sorted(directory.glob("*.json")):
        doc = _read(path)
        if doc is not None:
            out[doc["id"]] = doc
    return out


def list_climates() -> list[dict]:
    builtin, custom = _scan(_PRESETS), _scan(_custom_dir())
    ids = sorted(set(builtin) | set(custom))
    return [{"id": i,
             "name": (custom.get(i) or builtin[i])["name"],
             "builtin": i in builtin,
             "custom": i in custom} for i in ids]


def get(climate_id: str) -> dict | None:
    """The validated document for ``climate_id``, custom shadowing builtin."""
    if not isinstance(climate_id, str) or not climate_id:
        return None
    return _scan(_custom_dir()).get(climate_id) or _scan(_PRESETS).get(climate_id)
