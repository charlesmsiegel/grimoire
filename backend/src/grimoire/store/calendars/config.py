"""Calendar config IO: <root>/calendar.json = {primary, secondary|None}, each
calendar block {provider, region, custom_holidays, anchor}. World-scoped, copied
into a campaign on create."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .base import CalendarError


def _blank(region: str = "US") -> dict:
    return {"provider": "gregorian", "region": region, "custom_holidays": [], "anchor": None}


def default_calendar() -> dict:
    return {"primary": _blank(), "secondary": None}


def _normalize_block(block: dict | None) -> dict | None:
    if not block:
        return None
    base = _blank()
    base.update({k: block[k] for k in ("provider", "region", "custom_holidays", "anchor") if k in block})
    base["custom_holidays"] = base["custom_holidays"] or []
    return base


def _path(root: Path) -> Path:
    return root / "calendar.json"


def read_calendar(root: Path) -> dict:
    p = _path(root)
    if not p.exists():
        return default_calendar()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default_calendar()  # corrupt file — fall back rather than crash a render
    primary = _normalize_block(raw.get("primary")) or _blank()
    return {"primary": primary, "secondary": _normalize_block(raw.get("secondary"))}


def write_calendar(root: Path, cfg: dict) -> None:
    out = {"primary": _normalize_block(cfg.get("primary")) or _blank(),
           "secondary": _normalize_block(cfg.get("secondary"))}
    _path(root).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


def copy_calendar(wroot: Path, croot: Path) -> None:
    write_calendar(croot, read_calendar(wroot))


def _validate_rule(rule: dict) -> None:
    if not rule.get("name"):
        raise CalendarError(f"custom holiday needs a name: {rule!r}")
    try:
        month = int(rule["month"])
    except (KeyError, ValueError, TypeError):
        raise CalendarError(f"custom holiday needs a valid month: {rule!r}")
    if not (1 <= month <= 12):
        raise CalendarError(f"custom holiday month out of range: {rule!r}")
    try:
        if "day" in rule:
            date(2024, month, int(rule["day"]))  # leap year so Feb 29 is allowed
        else:
            nth, weekday = int(rule["nth"]), int(rule["weekday"])
            if not (1 <= nth <= 5 and 0 <= weekday <= 6):
                raise ValueError
    except (KeyError, ValueError, TypeError):
        raise CalendarError(f"custom holiday rule is malformed: {rule!r}")


def validate_calendar(cfg: dict) -> None:
    """Raise CalendarError if any configured calendar has a malformed custom holiday."""
    for block in (cfg.get("primary"), cfg.get("secondary")):
        if not block:
            continue
        for rule in block.get("custom_holidays", []) or []:
            _validate_rule(rule)
