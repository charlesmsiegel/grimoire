"""Validation for climate documents (spec: 2026-07-27-weather-design).

Every rule here exists because its absence produces a *silent* wrong answer
rather than an error — a table that cannot be drawn from, an entry that can
never be selected, a quantile that lands on a disabled row. Validation runs at
load and at save; the resolver assumes a validated document.

The container-shape checks matter as much as the semantic ones: this runs
against hand-edited private files, and an unguarded ``.get()`` on a malformed
document would raise something other than ``ClimateError`` and escape the
registry's skip-and-continue contract.
"""

from __future__ import annotations

import math
import re
import warnings

# fullmatch, and note the pattern has no anchors: `$` in Python matches before a
# trailing newline, so `re.match(r"^[A-Za-z0-9._-]+$", "saltmarch\n")` succeeds
# and would admit an id the climate routes cannot address.
_ID = re.compile(r"[A-Za-z0-9._-]+")

CLAMP = 0.998


class ClimateError(Exception):
    pass


def _weights(entries: list[dict], where: str) -> list[float]:
    out = []
    for e in entries:
        if not isinstance(e, dict):
            raise ClimateError(f"{where}: each entry must be a JSON object")
        name = e.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ClimateError(f"{where}: every entry needs a non-empty name")
        w = e.get("weight")
        if not isinstance(w, (int, float)) or isinstance(w, bool) or not math.isfinite(w) or w < 0:
            raise ClimateError(f"{where}: weight for {name!r} must be a finite number >= 0")
        out.append(float(w))
    names = [e["name"] for e in entries]
    if len(set(names)) != len(names):
        raise ClimateError(f"{where}: duplicate entry names")
    if not any(w > 0 for w in out):
        raise ClimateError(f"{where}: needs at least one entry with a positive weight")
    # Plain sum, not math.fsum: fsum *raises* OverflowError on an intermediate
    # overflow, which would escape as something other than a ClimateError.
    # sum() saturates to inf, which is exactly the condition being tested for.
    if not math.isfinite(sum(out)):
        raise ClimateError(f"{where}: weights sum to a non-finite total")
    return out


def _intervals(seasons: list[dict]) -> list[tuple[float, float]]:
    """Seasons as plain [start, end) intervals on [0, 1), wraps unrolled."""
    out: list[tuple[float, float]] = []
    for s in seasons:
        a, b = float(s["from"]), float(s["to"])
        if a == b:
            return [(0.0, 1.0)]  # a single season spanning the whole year
        if a < b:
            out.append((a, b))
        else:
            out.append((a, 1.0))
            out.append((0.0, b))
    return out


def _covers_year(seasons: list[dict]) -> bool:
    """True when the seasons leave no *gap*.

    Overlaps are legal — the spec resolves them by array order — so this sweeps
    for uncovered intervals rather than demanding an exact tiling. Requiring
    each season to start exactly where the last ended would reject
    ``[0.0, 0.6)`` followed by ``[0.5, 0.0)``, which covers the year perfectly
    well.
    """
    reach = 0.0
    for start, end in sorted(_intervals(seasons)):
        if start > reach:
            return False
        reach = max(reach, end)
    return reach >= 1.0


def validate(doc: dict) -> dict:
    if not isinstance(doc, dict):
        raise ClimateError("a climate document must be a JSON object")

    cid = doc.get("id")
    if not isinstance(cid, str) or not _ID.fullmatch(cid) or not cid.strip("."):
        raise ClimateError(f"climate id must match [A-Za-z0-9._-]+ and not be dots only: {cid!r}")

    name = doc.get("name")
    if not isinstance(name, str) or not name.strip():
        # `list_climates` dereferences doc["name"]; without this one malformed
        # private file takes the whole merged registry down with a KeyError.
        raise ClimateError(f"climate {cid!r} needs a non-empty name")

    p = doc.get("persistence", 0.5)
    if not isinstance(p, (int, float)) or isinstance(p, bool) or not math.isfinite(p) or not 0 <= p <= 1:
        raise ClimateError(f"persistence must be a finite number in [0, 1], got {p!r}")
    if p > CLAMP:
        # Accepted range is [0, 1]; effective range is [0, CLAMP]. The author
        # should know the number they wrote is not the number in use.
        warnings.warn(
            f"climate {cid!r}: persistence {p} is clamped to {CLAMP} when sampling",
            stacklevel=2)

    seasons = doc.get("seasons")
    if not isinstance(seasons, list) or not seasons:
        raise ClimateError("a climate needs at least one season")

    for s in seasons:
        if not isinstance(s, dict):
            raise ClimateError(f"each season must be a JSON object, got {type(s).__name__}")
        s_name = s.get("name")
        if not isinstance(s_name, str) or not s_name.strip():
            # `current_weather` returns season["name"] to its callers; a season
            # without one raises KeyError inside prompt assembly.
            raise ClimateError(f"climate {cid!r}: every season needs a non-empty name")
        where = f"season {s_name!r}"
        for edge in ("from", "to"):
            v = s.get(edge)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v < 1:
                raise ClimateError(f"{where}: {edge} must be a fraction in [0, 1)")

        temps = s.get("temperature") or []
        conds = s.get("conditions") or []
        winds = s.get("wind") or []
        for axis, entries in (("temperature", temps), ("conditions", conds), ("wind", winds)):
            if not isinstance(entries, list) or not entries:
                raise ClimateError(f"{where}: {axis} must be a non-empty array")
            _weights(entries, f"{where} {axis}")

        temp_names = {t["name"] for t in temps}
        live_temps = {t["name"] for t in temps if t["weight"] > 0}

        for c in conds:
            req = c.get("requires_temp")
            if req is None:
                continue
            if not isinstance(req, list) or not req:
                raise ClimateError(
                    f"{where}: requires_temp on {c['name']!r} must be a non-empty array "
                    "(omit the key entirely for an unconstrained condition)")
            if not all(isinstance(r, str) for r in req):
                # Elements as well as the array: a dict element would raise
                # `TypeError: unhashable` at the set intersection below, which
                # a direct caller of validate() sees instead of a ClimateError.
                raise ClimateError(
                    f"{where}: requires_temp on {c['name']!r} must contain only strings")
            unknown = [r for r in req if r not in temp_names]
            if unknown:
                raise ClimateError(
                    f"{where}: requires_temp on {c['name']!r} names no such temperature: {unknown}")
            if c["weight"] > 0 and not (set(req) & live_temps):
                raise ClimateError(
                    f"{where}: requires_temp on {c['name']!r} names only zero-weight "
                    "temperatures, so it can never be drawn")

        # This also guarantees every positive-weight temperature has an
        # eligible condition — an unconstrained condition is eligible for all
        # of them — so no separate per-temperature check is needed. An earlier
        # draft had one; it was unreachable.
        if not any(c["weight"] > 0 and "requires_temp" not in c for c in conds):
            raise ClimateError(
                f"{where}: needs at least one unconstrained condition with a positive weight")

    if not _covers_year(seasons):
        raise ClimateError("seasons must cover the year without gaps")

    return doc
