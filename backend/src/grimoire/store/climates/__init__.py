"""Two-tier climate registry.

Shipped presets live in ``presets/`` beside this module; private climates live
in ``<GRIMOIRE_HOME>/climates/`` and shadow a preset of the same id. Mirrors the
split in ``calendars/plugins.py``: a malformed private file is skipped rather
than fatal, and is picked up again once fixed — no restart, no cache to stale.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import atomic
from ..paths import home
from .schema import ClimateError, validate

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
    except Exception:  # noqa: BLE001 - malformed beyond what validate anticipates
        return None


def _custom_dir() -> Path:
    return home() / "climates"


def _scan(directory: Path) -> dict[str, dict]:
    """Every valid document in a tier, keyed by id.

    A file whose `id` differs from its filename is skipped. Registering it
    under the document id while `custom_path`, the tier flags and deletion all
    address `<id>.json` leaves a climate that lists as custom, opens as
    non-custom, and cannot be removed through the API — and two mismatched
    files can shadow each other by filename order.
    """
    if not directory.is_dir():
        return {}
    out: dict[str, dict] = {}
    for path in sorted(directory.glob("*.json")):
        doc = _read(path)
        if doc is not None and doc["id"] == path.stem:
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


def is_builtin(climate_id: str) -> bool:
    return climate_id in _scan(_PRESETS)


def custom_path(climate_id: str) -> Path:
    return _custom_dir() / f"{climate_id}.json"


def save(doc: dict) -> dict:
    """Validate and write a climate to the private tier.

    Shipped presets live inside the installed backend package and must never be
    written, so editing one **copies it to `<GRIMOIRE_HOME>/climates/{id}.json`
    and edits the copy** — the same copy-on-write shape campaigns already use
    when diverging from a world. Lookup precedence does the rest: a custom
    climate shadows a shipped one of the same id.

    Raises `ClimateError` for an invalid document, so the caller can report it
    rather than writing a file the registry would silently skip.
    """
    doc = validate(doc)
    _custom_dir().mkdir(parents=True, exist_ok=True)
    atomic.write_text(custom_path(doc["id"]), json.dumps(doc, indent=2) + "\n")
    return doc


def remove(climate_id: str) -> bool:
    """Delete the private copy. Returns whether one existed.

    A custom climate that shadows a preset reverts to the preset rather than
    vanishing; one with no preset behind it simply disappears. That asymmetry
    is why `list_climates` reports both tier flags rather than a single
    `custom` label — the editor cannot otherwise tell *Revert to preset* from
    *Delete*, or know whether deleting frees the id.
    """
    if not isinstance(climate_id, str) or not climate_id:
        return False
    try:
        custom_path(climate_id).unlink()
        return True
    except (FileNotFoundError, ValueError):
        return False
    # Any other OSError — a read-only directory, an I/O failure — propagates.
    # Reporting "no custom climate to delete" for a climate that still exists
    # tells the user the opposite of what happened.
