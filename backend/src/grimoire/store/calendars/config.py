"""Calendar config IO: <root>/calendar.json = {primary, secondary|None}, each
calendar block {provider, region, custom_holidays, anchor}. World-scoped, copied
into a campaign on create."""

from __future__ import annotations

import json
from pathlib import Path

from .base import CalendarError, get_provider


def _blank(region: str = "US") -> dict:
    return {"provider": "gregorian", "region": region, "custom_holidays": [], "anchor": None}


def default_calendar() -> dict:
    return {"primary": _blank(), "secondary": None, "confirmed": False}


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
    return {"primary": primary, "secondary": _normalize_block(raw.get("secondary")),
            "confirmed": bool(raw.get("confirmed", False))}


def write_calendar(root: Path, cfg: dict) -> None:
    out = {"primary": _normalize_block(cfg.get("primary")) or _blank(),
           "secondary": _normalize_block(cfg.get("secondary")),
           "confirmed": bool(cfg.get("confirmed", False))}
    _path(root).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


def copy_calendar(wroot: Path, croot: Path) -> None:
    write_calendar(croot, read_calendar(wroot))


def validate_calendar(cfg: dict) -> None:
    """Raise CalendarError if any configured calendar has a malformed custom holiday."""
    for block in (cfg.get("primary"), cfg.get("secondary")):
        if not block:
            continue
        provider = get_provider(block)  # raises CalendarError on an unknown provider
        for rule in block.get("custom_holidays", []) or []:
            provider.validate_rule(rule)
