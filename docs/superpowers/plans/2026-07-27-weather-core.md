# Weather Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministic procedural weather appears in every scene prompt, driven by per-location climates.

**Architecture:** A climate registry (JSON documents, two-tier: shipped presets plus `<GRIMOIRE_HOME>/climates/`) supplies weighted tables per season. Weather is a pure function of `(campaign, weather zone, block ordinal)`: a hashed latent Gaussian field, smoothed by a one-sided exponential filter whose coefficient *is* the location's `persistence`, pushed through `Φ` to a uniform quantile, then through each season table's inverse CDF. Nothing is stored; every block is O(1) to resolve at any point in a campaign's history.

**Tech Stack:** Python 3.11+ (`hashlib.blake2b`, `statistics.NormalDist`, `math` — stdlib only), pytest, Jinja2 templates.

**Source spec:** `docs/superpowers/specs/2026-07-27-weather-design.md`

## Global Constraints

- **Stdlib only in this plan.** No new entries in `pyproject.toml`. Every module here must be Android-installable per `docs/android-architecture.md` — `hashlib`, `statistics` and `math` are all pure-Python-safe.
- **Determinism is scoped to an installation** (spec § Determinism scope). Reference vectors are regression fixtures, not cross-implementation conformance.
- **Filesystem access goes through `store.paths.home()`** — never a repo-relative path, never `~`.
- **Privacy rule (`CLAUDE.md`):** invented placeholder names only in tests, fixtures and docs. Use `saltmarch`, `highreach`, `seraphine`, `mara`, `winifred`, `realm`. Never a real world, campaign or character name. Shipped presets are generic real-world-ish climates only.
- **Backend tests isolate the store** with `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- **Run backend tests:** `backend/.venv/Scripts/python.exe -m pytest backend -q`
- **Run frontend tests from `frontend/`:** `npx vitest run` and `npx tsc -b`. Never `npx --prefix frontend`.
- **Weather never raises into a turn.** Every resolution path degrades to `None` or a documented fallback.

## Scope

This plan is **one of four**. It delivers the generation core and the prompt integration — working, testable software on its own: after Task 12, a scene prompt contains a weather line derived from the location's climate, and it is stable across restarts.

Deliberately **not** in this plan, each its own follow-up:

| Plan | Covers | Issues |
| --- | --- | --- |
| **1 — Weather core (this plan)** | climate registry, noise field, draw, prompt section | #44 |
| 2 — Override store | `weather.json`, spans, precedence, clear/resume, PUT/DELETE routes | #45 |
| 3 — Play surfaces | HUD widget, override popover, absorb extractor, advance sweep | #46, #104, #195 |
| 4 — Climate editor | two-pane editor, validation UI, campaign-default control | #40 |

Plan 1 therefore implements only the **procedural** branch of `current_weather`. Its signature is final and takes the override layer in Plan 2 without changing callers.

**Spec deviation resolved here:** the spec names the loader `store/climates.py` and the presets `store/climates/*.json`. A module and a package cannot share a name. This plan uses a package — `store/climates/__init__.py` for the loader, `store/climates/presets/*.json` for shipped data — mirroring `store/calendars/`.

---

## File Structure

**Created:**

| Path | Responsibility |
| --- | --- |
| `backend/src/grimoire/store/climates/__init__.py` | Two-tier registry: list, get, best-effort custom load |
| `backend/src/grimoire/store/climates/schema.py` | Parse + validate one climate document; `ClimateError` |
| `backend/src/grimoire/store/climates/presets/*.json` | Six shipped climates |
| `backend/src/grimoire/store/weather/__init__.py` | Public surface: `current_weather` |
| `backend/src/grimoire/store/weather/blocks.py` | Minute → block; block → ordinal; owning date |
| `backend/src/grimoire/store/weather/seasons.py` | Year fraction; fraction → season |
| `backend/src/grimoire/store/weather/noise.py` | Latent `z`, filter `g`, `Φ`, `W` |
| `backend/src/grimoire/store/weather/draw.py` | `inverse_cdf`, three-axis draw |
| `backend/src/grimoire/store/weather/settings.py` | Location + campaign climate/persistence/zone resolution |
| `templates/scene/sections/weather.j2` | The prompt section |
| `backend/tests/test_weather_*.py` | One test module per unit above |

**Modified:**

| Path | Change |
| --- | --- |
| `backend/src/grimoire/store/entity_schema.py` | Add `locations` field descriptors |
| `frontend/src/api/client.ts` | Mirror them in `ENTITY_FIELDS.locations` |
| `backend/src/grimoire/store/context.py` | `_weather_data()`, add to `_assemble` dict and `_SECTIONS` |
| `templates/scene/system.j2` | Include `weather.j2` in the hard-coded chain |

Weather is a package rather than one module because the numerical core, the calendar arithmetic and the settings-resolution chain change for unrelated reasons and are tested independently.

---

### Task 1: Climate document validation

**Files:**
- Create: `backend/src/grimoire/store/climates/__init__.py` (empty for now)
- Create: `backend/src/grimoire/store/climates/schema.py`
- Test: `backend/tests/test_climate_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ClimateError(Exception)`; `validate(doc: dict) -> dict` returning the document unchanged on success and raising `ClimateError` with a human-readable message naming the season on failure.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_climate_schema.py`:

```python
import pytest

from grimoire.store.climates.schema import ClimateError, validate


def climate(**over):
    doc = {
        "id": "temperate-coastal", "name": "Temperate Coastal", "persistence": 0.35,
        "seasons": [{
            "name": "winter", "from": 0.0, "to": 0.0,
            "temperature": [{"name": "cold", "weight": 6}, {"name": "mild", "weight": 2}],
            "conditions": [{"name": "clear", "weight": 2},
                           {"name": "snow", "weight": 2, "requires_temp": ["cold"]}],
            "wind": [{"name": "calm", "weight": 1}],
        }],
    }
    doc.update(over)
    return doc


def season(**over):
    s = climate()["seasons"][0]
    s.update(over)
    return s


def test_valid_document_passes():
    assert validate(climate())["id"] == "temperate-coastal"


def test_empty_entry_name_rejected():
    with pytest.raises(ClimateError, match="winter"):
        validate(climate(seasons=[season(wind=[{"name": "  ", "weight": 1}])]))


def test_duplicate_entry_names_rejected():
    with pytest.raises(ClimateError, match="duplicate"):
        validate(climate(seasons=[season(
            wind=[{"name": "calm", "weight": 1}, {"name": "calm", "weight": 2}])]))


def test_negative_weight_rejected_even_with_positive_sibling():
    with pytest.raises(ClimateError, match="weight"):
        validate(climate(seasons=[season(
            wind=[{"name": "calm", "weight": 1}, {"name": "gale", "weight": -1}])]))


def test_all_zero_axis_rejected():
    with pytest.raises(ClimateError, match="positive"):
        validate(climate(seasons=[season(wind=[{"name": "calm", "weight": 0}])]))


def test_non_finite_axis_total_rejected():
    with pytest.raises(ClimateError, match="finite"):
        validate(climate(seasons=[season(
            wind=[{"name": "a", "weight": 1e308}, {"name": "b", "weight": 1e308}])]))


def test_season_without_positive_unconstrained_condition_rejected():
    with pytest.raises(ClimateError, match="unconstrained"):
        validate(climate(seasons=[season(
            conditions=[{"name": "clear", "weight": 0},
                        {"name": "snow", "weight": 2, "requires_temp": ["cold"]}])]))


def test_dangling_requires_temp_rejected():
    with pytest.raises(ClimateError, match="requires_temp"):
        validate(climate(seasons=[season(
            conditions=[{"name": "clear", "weight": 1},
                        {"name": "snow", "weight": 2, "requires_temp": ["freezng"]}])]))


def test_requires_temp_naming_only_zero_weight_band_rejected():
    with pytest.raises(ClimateError, match="requires_temp"):
        validate(climate(seasons=[season(
            temperature=[{"name": "cold", "weight": 0}, {"name": "mild", "weight": 3}],
            conditions=[{"name": "clear", "weight": 1},
                        {"name": "snow", "weight": 2, "requires_temp": ["cold"]}])]))


def test_empty_requires_temp_rejected():
    with pytest.raises(ClimateError, match="requires_temp"):
        validate(climate(seasons=[season(
            conditions=[{"name": "clear", "weight": 1},
                        {"name": "snow", "weight": 2, "requires_temp": []}])]))


def test_temperature_band_with_no_eligible_condition_rejected():
    with pytest.raises(ClimateError, match="eligible"):
        validate(climate(seasons=[season(
            temperature=[{"name": "cold", "weight": 1}, {"name": "hot", "weight": 1}],
            conditions=[{"name": "snow", "weight": 2, "requires_temp": ["cold"]}])]))


def test_persistence_one_is_accepted():
    assert validate(climate(persistence=1))["persistence"] == 1


def test_persistence_out_of_range_rejected():
    with pytest.raises(ClimateError, match="persistence"):
        validate(climate(persistence=2))


def test_year_gap_rejected():
    with pytest.raises(ClimateError, match="cover"):
        validate(climate(seasons=[season(**{"from": 0.0, "to": 0.5})]))


def test_two_seasons_covering_the_year_pass():
    a = season(name="wet", **{"from": 0.0, "to": 0.5})
    b = season(name="dry", **{"from": 0.5, "to": 0.0})
    assert len(validate(climate(seasons=[a, b]))["seasons"]) == 2


def test_overlapping_seasons_that_still_cover_the_year_pass():
    # Overlaps are legal — the spec resolves them by array order. Only *gaps*
    # are an error, so exact tiling must not be required.
    a = season(name="long", **{"from": 0.0, "to": 0.6})
    b = season(name="late", **{"from": 0.5, "to": 0.0})
    assert len(validate(climate(seasons=[a, b]))["seasons"]) == 2


def test_non_object_document_rejected():
    with pytest.raises(ClimateError, match="object"):
        validate([{"id": "x"}])


def test_non_object_season_rejected():
    with pytest.raises(ClimateError, match="object"):
        validate(climate(seasons=[None]))


def test_non_object_table_entry_rejected():
    with pytest.raises(ClimateError, match="object"):
        validate(climate(seasons=[season(wind=["calm"])]))


def test_climate_id_with_slash_rejected():
    with pytest.raises(ClimateError, match="id"):
        validate(climate(id="a/b"))


def test_dot_only_climate_id_rejected():
    with pytest.raises(ClimateError, match="id"):
        validate(climate(id=".."))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_climate_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.climates'`

- [ ] **Step 3: Create the package and write the validator**

Create `backend/src/grimoire/store/climates/__init__.py` as an empty file (the registry lands in Task 2).

Create `backend/src/grimoire/store/climates/schema.py`:

```python
"""Validation for climate documents (spec: 2026-07-27-weather-design).

Every rule here exists because its absence produces a *silent* wrong answer
rather than an error — a table that cannot be drawn from, an entry that can
never be selected, a quantile that lands on a disabled row. Validation runs at
load and at save; the resolver assumes a validated document.
"""

from __future__ import annotations

import math
import re

_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_AXES = ("temperature", "conditions", "wind")


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
    if not isinstance(cid, str) or not _ID.match(cid) or not cid.strip("."):
        raise ClimateError(f"climate id must match [A-Za-z0-9._-]+ and not be dots only: {cid!r}")

    p = doc.get("persistence", 0.5)
    if not isinstance(p, (int, float)) or isinstance(p, bool) or not math.isfinite(p) or not 0 <= p <= 1:
        raise ClimateError(f"persistence must be a finite number in [0, 1], got {p!r}")

    seasons = doc.get("seasons")
    if not isinstance(seasons, list) or not seasons:
        raise ClimateError("a climate needs at least one season")

    for s in seasons:
        if not isinstance(s, dict):
            raise ClimateError(f"each season must be a JSON object, got {type(s).__name__}")
        where = f"season {s.get('name', '?')!r}"
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
            unknown = [r for r in req if r not in temp_names]
            if unknown:
                raise ClimateError(
                    f"{where}: requires_temp on {c['name']!r} names no such temperature: {unknown}")
            if c["weight"] > 0 and not (set(req) & live_temps):
                raise ClimateError(
                    f"{where}: requires_temp on {c['name']!r} names only zero-weight "
                    "temperatures, so it can never be drawn")

        if not any(c["weight"] > 0 and "requires_temp" not in c for c in conds):
            raise ClimateError(
                f"{where}: needs at least one unconstrained condition with a positive weight")

        for t in temps:
            if t["weight"] <= 0:
                continue
            eligible = [c for c in conds
                        if c["weight"] > 0 and t["name"] in (c.get("requires_temp") or [t["name"]])]
            if not eligible:
                raise ClimateError(
                    f"{where}: temperature {t['name']!r} has no eligible condition")

    if not _covers_year(seasons):
        raise ClimateError("seasons must cover the year without gaps")

    return doc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_climate_schema.py -q`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/climates backend/tests/test_climate_schema.py
git commit -m "feat(weather): validate climate documents"
```

---

### Task 2: Climate registry

**Files:**
- Modify: `backend/src/grimoire/store/climates/__init__.py`
- Create: `backend/src/grimoire/store/climates/presets/temperate-interior.json`
- Test: `backend/tests/test_climate_registry.py`

**Interfaces:**
- Consumes: `climates.schema.validate`, `climates.schema.ClimateError`; `store.paths.home()`.
- Produces: `list_climates() -> list[dict]` (each `{id, name, builtin: bool, custom: bool}`, sorted by id); `get(climate_id: str) -> dict | None` returning a validated document, custom shadowing builtin; `FALLBACK_ID = "temperate-interior"`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_climate_registry.py`:

```python
import json

from grimoire.store import climates


def write_custom(tmp_path, cid, **over):
    doc = {
        "id": cid, "name": cid.title(), "persistence": 0.5,
        "seasons": [{
            "name": "all year", "from": 0.0, "to": 0.0,
            "temperature": [{"name": "mild", "weight": 1}],
            "conditions": [{"name": "clear", "weight": 1}],
            "wind": [{"name": "calm", "weight": 1}],
        }],
    }
    doc.update(over)
    d = tmp_path / "climates"
    d.mkdir(exist_ok=True)
    (d / f"{cid}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_shipped_fallback_is_present(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert climates.get(climates.FALLBACK_ID) is not None


def test_unknown_id_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert climates.get("no-such-climate") is None


def test_custom_climate_is_listed_and_loadable(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    write_custom(tmp_path, "saltmarch-fens")
    listed = {c["id"]: c for c in climates.list_climates()}
    assert listed["saltmarch-fens"] == {
        "id": "saltmarch-fens", "name": "Saltmarch-Fens", "builtin": False, "custom": True}


def test_custom_shadows_builtin_and_both_flags_are_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    write_custom(tmp_path, climates.FALLBACK_ID, name="Mine")
    listed = {c["id"]: c for c in climates.list_climates()}
    assert listed[climates.FALLBACK_ID]["builtin"] is True
    assert listed[climates.FALLBACK_ID]["custom"] is True
    assert climates.get(climates.FALLBACK_ID)["name"] == "Mine"


def test_malformed_custom_file_is_skipped_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    d = tmp_path / "climates"
    d.mkdir()
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    write_custom(tmp_path, "highreach-scarp")
    ids = {c["id"] for c in climates.list_climates()}
    assert "highreach-scarp" in ids and "broken" not in ids


def test_invalid_custom_climate_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    write_custom(tmp_path, "bad-one", seasons=[{
        "name": "x", "from": 0.0, "to": 0.0,
        "temperature": [{"name": "mild", "weight": 0}],
        "conditions": [{"name": "clear", "weight": 1}],
        "wind": [{"name": "calm", "weight": 1}]}])
    assert climates.get("bad-one") is None


def test_climate_with_unsafe_id_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    write_custom(tmp_path, "fine-one")
    d = tmp_path / "climates"
    (d / "slashy.json").write_text(
        json.dumps({"id": "a/b", "name": "x", "seasons": []}), encoding="utf-8")
    assert climates.get("a/b") is None


def test_edits_are_seen_without_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert climates.get("late-arrival") is None
    write_custom(tmp_path, "late-arrival")
    assert climates.get("late-arrival") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_climate_registry.py -q`
Expected: FAIL — `AttributeError: module 'grimoire.store.climates' has no attribute 'FALLBACK_ID'`

- [ ] **Step 3: Write the registry**

Replace `backend/src/grimoire/store/climates/__init__.py`:

```python
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
    except (OSError, json.JSONDecodeError, ClimateError):
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
    return _scan(_custom_dir()).get(climate_id) or _scan(_PRESETS).get(climate_id)
```

Create `backend/src/grimoire/store/climates/presets/temperate-interior.json`:

```json
{
  "id": "temperate-interior",
  "name": "Temperate Interior",
  "persistence": 0.55,
  "seasons": [
    {
      "name": "winter", "from": 0.92, "to": 0.21,
      "temperature": [
        { "name": "freezing", "weight": 4 },
        { "name": "cold", "weight": 5 },
        { "name": "mild", "weight": 1 }
      ],
      "conditions": [
        { "name": "clear", "weight": 4 },
        { "name": "overcast", "weight": 5 },
        { "name": "light rain", "weight": 2 },
        { "name": "snow", "weight": 3, "requires_temp": ["freezing"] }
      ],
      "wind": [
        { "name": "still", "weight": 3 },
        { "name": "breeze", "weight": 4 },
        { "name": "strong", "weight": 2 }
      ]
    },
    {
      "name": "spring", "from": 0.21, "to": 0.42,
      "temperature": [
        { "name": "cold", "weight": 3 },
        { "name": "mild", "weight": 6 },
        { "name": "warm", "weight": 2 }
      ],
      "conditions": [
        { "name": "clear", "weight": 5 },
        { "name": "overcast", "weight": 3 },
        { "name": "light rain", "weight": 4 },
        { "name": "storm", "weight": 1 }
      ],
      "wind": [
        { "name": "still", "weight": 2 },
        { "name": "breeze", "weight": 5 },
        { "name": "strong", "weight": 2 }
      ]
    },
    {
      "name": "summer", "from": 0.42, "to": 0.67,
      "temperature": [
        { "name": "mild", "weight": 3 },
        { "name": "warm", "weight": 6 },
        { "name": "hot", "weight": 3 }
      ],
      "conditions": [
        { "name": "clear", "weight": 8 },
        { "name": "overcast", "weight": 2 },
        { "name": "light rain", "weight": 2 },
        { "name": "storm", "weight": 2 }
      ],
      "wind": [
        { "name": "still", "weight": 5 },
        { "name": "breeze", "weight": 4 },
        { "name": "strong", "weight": 1 }
      ]
    },
    {
      "name": "autumn", "from": 0.67, "to": 0.92,
      "temperature": [
        { "name": "freezing", "weight": 1 },
        { "name": "cold", "weight": 4 },
        { "name": "mild", "weight": 4 }
      ],
      "conditions": [
        { "name": "clear", "weight": 3 },
        { "name": "overcast", "weight": 5 },
        { "name": "light rain", "weight": 4 },
        { "name": "storm", "weight": 1 }
      ],
      "wind": [
        { "name": "still", "weight": 2 },
        { "name": "breeze", "weight": 4 },
        { "name": "strong", "weight": 3 }
      ]
    }
  ]
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_climate_registry.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/climates backend/tests/test_climate_registry.py
git commit -m "feat(weather): two-tier climate registry with the shipped fallback preset"
```

---

### Task 3: Blocks and ordinals

**Files:**
- Create: `backend/src/grimoire/store/weather/__init__.py` (empty for now)
- Create: `backend/src/grimoire/store/weather/blocks.py`
- Test: `backend/tests/test_weather_blocks.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `POSITIONS: tuple[str, ...]` = `("dawn", "morning", "afternoon", "evening", "night")`; `block_of(fixed_day: int, minutes: int | None) -> tuple[int, int]` returning `(owning_fixed_day, position_index)`; `ordinal(fixed_day: int, minutes: int | None) -> int` = `5 * owning_fixed_day + position_index`.

**Why this exists:** the minute coordinate decides *which* block a moment is in; the ordinal is what indexes the noise field. Feeding minutes to the filter would put adjacent blocks 240–480 apart on an axis whose correlation is defined at distance 1, silently making every block independent.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_weather_blocks.py`:

```python
from grimoire.store.weather.blocks import POSITIONS, block_of, ordinal

DAY = 700_000  # an arbitrary fixed day


def pos(minutes, day=DAY):
    return POSITIONS[block_of(day, minutes)[1]]


def test_block_names_by_minute():
    assert pos(5 * 60) == "dawn"
    assert pos(9 * 60) == "morning"
    assert pos(13 * 60) == "afternoon"
    assert pos(19 * 60) == "evening"
    assert pos(22 * 60) == "night"


def test_after_midnight_belongs_to_the_previous_date_night():
    assert block_of(DAY + 1, 2 * 60) == (DAY, POSITIONS.index("night"))
    assert block_of(DAY, 22 * 60) == (DAY, POSITIONS.index("night"))


def test_late_evening_and_early_morning_share_one_ordinal():
    assert ordinal(DAY, 23 * 60) == ordinal(DAY + 1, 1 * 60)


def test_dawn_boundary_is_not_night():
    assert ordinal(DAY, 3 * 60 + 59) != ordinal(DAY, 4 * 60 + 1)
    assert pos(4 * 60 + 1) == "dawn"


def test_missing_clock_resolves_to_afternoon():
    assert block_of(DAY, None) == (DAY, POSITIONS.index("afternoon"))


def test_consecutive_blocks_differ_by_one_across_a_day_boundary():
    evening = ordinal(DAY, 19 * 60)
    night = ordinal(DAY, 22 * 60)
    dawn_next = ordinal(DAY + 1, 5 * 60)
    assert night - evening == 1
    assert dawn_next - night == 1


def test_ordinals_are_defined_for_negative_days():
    assert ordinal(-3, 9 * 60) == 5 * -3 + POSITIONS.index("morning")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_blocks.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.weather'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/grimoire/store/weather/__init__.py` as an empty file (the public surface lands in Task 10).

Create `backend/src/grimoire/store/weather/blocks.py`:

```python
"""Blocks: which slice of a day a moment falls in, and its index in the field.

Two coordinates, deliberately separate. A moment picks its block by wall-clock
minute, which is what keeps `night` contiguous across midnight. The noise field
is indexed by a *consecutive ordinal*, because `persistence` is defined as the
correlation between indices one apart — and consecutive blocks are 240-480
minutes apart, so indexing by minute would leave every block independent while
every distribution still looked correct.
"""

from __future__ import annotations

POSITIONS = ("dawn", "morning", "afternoon", "evening", "night")

# (start minute, position index), ascending; `night` wraps past midnight and is
# handled separately below.
_DAY_BLOCKS = ((4 * 60, 0), (8 * 60, 1), (12 * 60, 2), (17 * 60, 3), (21 * 60, 4))

_NIGHT = POSITIONS.index("night")
_DEFAULT = POSITIONS.index("afternoon")  # the block containing midday


def block_of(fixed_day: int, minutes: int | None) -> tuple[int, int]:
    """(owning fixed day, position index) for a moment.

    A scene with a date but no clock resolves to `afternoon` — stable and
    unsurprising, rather than whatever the zero minute would give.
    """
    if minutes is None:
        return fixed_day, _DEFAULT
    if minutes < _DAY_BLOCKS[0][0]:
        return fixed_day - 1, _NIGHT  # 00:00-03:59 is the previous date's night
    position = _DAY_BLOCKS[0][1]
    for start, index in _DAY_BLOCKS:
        if minutes >= start:
            position = index
    return fixed_day, position


def ordinal(fixed_day: int, minutes: int | None) -> int:
    """The block's index in the noise field. Consecutive blocks differ by 1."""
    day, position = block_of(fixed_day, minutes)
    return 5 * day + position
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_blocks.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/weather backend/tests/test_weather_blocks.py
git commit -m "feat(weather): block resolution and consecutive ordinals"
```

---

### Task 4: Seasons

**Files:**
- Create: `backend/src/grimoire/store/weather/seasons.py`
- Test: `backend/tests/test_weather_seasons.py`

**Interfaces:**
- Consumes: `store.calendars` (`CalendarProvider.describe`, `.months`, `.parse`); `climates` documents from Task 1.
- Produces: `year_fraction(provider, fixed_day: int) -> float`; `year_length(provider, year: int) -> int`; `season_for(climate: dict, fraction: float) -> dict` returning the matching season, first match in array order.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_weather_seasons.py`:

```python
from grimoire.store.calendars import get_provider
from grimoire.store.weather.seasons import season_for, year_fraction, year_length

GREG = {"provider": "gregorian", "region": "US", "custom_holidays": [], "anchor": None}


def climate(seasons):
    return {"id": "c", "name": "C", "persistence": 0.5, "seasons": seasons}


def s(name, frm, to):
    return {"name": name, "from": frm, "to": to,
            "temperature": [{"name": "mild", "weight": 1}],
            "conditions": [{"name": "clear", "weight": 1}],
            "wind": [{"name": "calm", "weight": 1}]}


def test_year_length_gregorian_common_and_leap():
    p = get_provider(GREG)
    assert year_length(p, 2026) == 365
    assert year_length(p, 2024) == 366


def test_year_fraction_is_zero_on_the_first_day():
    p = get_provider(GREG)
    assert year_fraction(p, p.parse("2026-01-01")) == 0.0


def test_year_fraction_is_monotonic_across_the_year():
    p = get_provider(GREG)
    jan, jul, dec = (p.parse(d) for d in ("2026-01-01", "2026-07-01", "2026-12-31"))
    assert 0.0 == year_fraction(p, jan) < year_fraction(p, jul) < year_fraction(p, dec) < 1.0


def test_single_full_year_season_matches_everywhere():
    c = climate([s("all year", 0.0, 0.0)])
    assert season_for(c, 0.0)["name"] == "all year"
    assert season_for(c, 0.99)["name"] == "all year"


def test_two_seasons_split_the_year():
    c = climate([s("wet", 0.0, 0.5), s("dry", 0.5, 0.0)])
    assert season_for(c, 0.1)["name"] == "wet"
    assert season_for(c, 0.5)["name"] == "dry"
    assert season_for(c, 0.99)["name"] == "dry"


def test_wrapping_season_covers_both_ends_of_the_year():
    c = climate([s("winter", 0.92, 0.21), s("rest", 0.21, 0.92)])
    assert season_for(c, 0.95)["name"] == "winter"
    assert season_for(c, 0.01)["name"] == "winter"
    assert season_for(c, 0.5)["name"] == "rest"


def test_season_boundaries_are_half_open():
    c = climate([s("wet", 0.0, 0.5), s("dry", 0.5, 0.0)])
    assert season_for(c, 0.4999)["name"] == "wet"
    assert season_for(c, 0.5)["name"] == "dry"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_seasons.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.weather.seasons'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/grimoire/store/weather/seasons.py`:

```python
"""Where in its climate's year a block falls.

Seasons are declared by the *climate*, not the calendar, as fractions of the
year — so a monsoon climate can have two and a temperate one four, and a preset
drops into a 400-day homebrew calendar as cleanly as into a 365-day one.
"""

from __future__ import annotations

from ..calendars.base import CalendarProvider


def year_length(provider: CalendarProvider, year: int) -> int:
    return sum(m["days"] for m in provider.months(year))


def year_fraction(provider: CalendarProvider, fixed_day: int) -> float:
    """How far through its year ``fixed_day`` sits, in [0, 1)."""
    year = provider.describe(fixed_day)["year"]
    months = provider.months(year)
    first = provider.parse(f"{year}-{months[0]['key']}-01")
    return (fixed_day - first) / sum(m["days"] for m in months)


def _contains(season: dict, fraction: float) -> bool:
    start, end = float(season["from"]), float(season["to"])
    if start == end:
        return True  # a single season spanning the whole year
    if start < end:
        return start <= fraction < end
    return fraction >= start or fraction < end  # wraps the year end


def season_for(climate: dict, fraction: float) -> dict:
    """The season covering ``fraction``; first match in array order.

    Validation guarantees the seasons tile the year, so the final fallback is
    unreachable for a validated document — it exists so a resolver handed an
    unvalidated one still returns a season rather than raising into a turn.
    """
    for season in climate["seasons"]:
        if _contains(season, fraction):
            return season
    return climate["seasons"][0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_seasons.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/weather/seasons.py backend/tests/test_weather_seasons.py
git commit -m "feat(weather): climate-declared seasons over year fractions"
```

---

### Task 5: The latent field

**Files:**
- Create: `backend/src/grimoire/store/weather/noise.py`
- Test: `backend/tests/test_weather_noise.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `latent_u(cid: str, zone: str, axis: str, i: int) -> float` in `(0, 1)`; `latent_z(cid, zone, axis, i) -> float` standard normal.

**Reference vectors (spec § The construction, concretely).** `u` is final and asserted against the values below. These come from BLAKE2b and exact arithmetic; they are not regenerable from the implementation.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_weather_noise.py`:

```python
import math

import pytest

from grimoire.store.weather.noise import latent_u, latent_z

# Spec reference vectors. Determinism here is scoped to this installation
# (spec, Determinism scope); these pin the hash, the bit slice and the mapping.
U_VECTORS = [
    ("saltmarch-chronicle", "saltmarch", "temperature", 0, 0.45105387316006496),
    ("saltmarch-chronicle", "saltmarch", "condition", 0, 0.761560896101852),
    ("saltmarch-chronicle", "saltmarch", "wind", 0, 0.17774645354109275),
    ("saltmarch-chronicle", "saltmarch", "condition", 1, 0.9654995835326089),
    ("saltmarch-chronicle", "saltmarch", "condition", -1, 0.9510130394641975),
    ("saltmarch-chronicle", "highreach", "condition", 0, 0.21315957935313057),
]


@pytest.mark.parametrize("cid,zone,axis,i,expected", U_VECTORS)
def test_latent_u_matches_reference_vectors(cid, zone, axis, i, expected):
    assert latent_u(cid, zone, axis, i) == expected


def test_latent_u_is_strictly_inside_the_unit_interval():
    for i in range(2000):
        u = latent_u("realm", "saltmarch", "condition", i)
        assert 0.0 < u < 1.0


def test_latent_u_is_injective_over_a_large_sample():
    seen = {latent_u("realm", "saltmarch", "condition", i) for i in range(20_000)}
    assert len(seen) == 20_000


def test_axes_are_independent_streams():
    a = latent_u("realm", "saltmarch", "temperature", 7)
    b = latent_u("realm", "saltmarch", "condition", 7)
    assert a != b


def test_campaigns_do_not_share_skies():
    a = latent_u("realm-one", "saltmarch", "condition", 7)
    b = latent_u("realm-two", "saltmarch", "condition", 7)
    assert a != b


def test_unit_separator_prevents_key_collisions():
    # Without a separator, ("ab", "c") and ("a", "bc") would build one key.
    assert latent_u("ab", "c", "wind", 0) != latent_u("a", "bc", "wind", 0)


def test_latent_z_is_finite_and_roughly_standard_normal():
    xs = [latent_z("realm", "saltmarch", "wind", i) for i in range(20_000)]
    assert all(math.isfinite(x) for x in xs)
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    assert abs(mean) < 0.05
    assert abs(var - 1.0) < 0.05


def test_negative_ordinals_are_defined():
    assert math.isfinite(latent_z("realm", "saltmarch", "wind", -5000))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_noise.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.weather.noise'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/grimoire/store/weather/noise.py`:

```python
"""The correlated noise field behind every draw.

An i.i.d. standard-normal latent at every block ordinal, smoothed by a
one-sided exponential filter whose coefficient *is* `persistence` — so
`persistence` is the lag-1 autocorrelation between adjacent blocks, a real unit
rather than a dial. Sampling is random access: block 4,500 costs the same as
block 3, which is why a campaign's age never enters into it.
"""

from __future__ import annotations

import hashlib
import math
from statistics import NormalDist

_ND = NormalDist()

_MANTISSA = 1 << 53


def latent_u(cid: str, zone: str, axis: str, i: int) -> float:
    """A uniform in (0, 1), strictly interior and injective.

    ``(2n + 1) / 2**53`` over 52 digest bits: the numerator is an odd integer
    below 2**53 so it is exactly representable, and dividing by a power of two
    is exact. ``n / 2**53`` would emit 0, which has no normal quantile; the
    obvious repair of midpoints over 53 bits is not representable at the top of
    the range and collapses distinct inputs onto one value.
    """
    key = f"{cid}\x1f{zone}\x1f{axis}\x1f{i}".encode("utf-8")
    digest = hashlib.blake2b(key, digest_size=32).digest()
    n = int.from_bytes(digest[:8], "big") >> 12  # leading 52 bits
    return (2 * n + 1) / _MANTISSA


def latent_z(cid: str, zone: str, axis: str, i: int) -> float:
    """Standard normal latent at block ordinal ``i``."""
    return _ND.inv_cdf(latent_u(cid, zone, axis, i))


def window(persistence: float) -> int:
    """Maximum lag W. The filter runs k = 0..W inclusive, so W + 1 taps."""
    a = min(max(persistence, 0.0), 0.998)
    if a <= 0.0:
        return 0
    return math.ceil(4 / math.log(1 / a))


def field(cid: str, zone: str, axis: str, i: int, persistence: float) -> float:
    """The smoothed field g(i): a normalized one-sided exponential filter.

    One ascending pass with carried powers. ``a**k`` and repeated
    multiplication differ in their last bits, and the numerator and denominator
    accumulate together, so the arithmetic stays put when this module is
    refactored. Normalizing by the *finite* weight sum keeps the variance at 1
    for any W; the infinite form leaves a systematic error that grows as
    persistence falls.
    """
    a = min(max(persistence, 0.0), 0.998)
    w, num, den = 1.0, 0.0, 0.0
    for k in range(window(a) + 1):
        num += w * latent_z(cid, zone, axis, i - k)
        den += w * w
        w *= a
    return num / math.sqrt(den)


def quantile(cid: str, zone: str, axis: str, i: int, persistence: float) -> float:
    """The field pushed through the normal CDF: a uniform quantile in (0, 1).

    This is the copula step. Inverse-CDF sampling reproduces a table's declared
    weights only from uniform quantiles, and a smoothed Gaussian is not uniform
    until Phi is applied.
    """
    return _ND.cdf(field(cid, zone, axis, i, persistence))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_noise.py -q`
Expected: PASS (13 tests — the parametrized vector test counts as 6)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/weather/noise.py backend/tests/test_weather_noise.py
git commit -m "feat(weather): latent field with spec reference vectors"
```

---

### Task 6: Filter calibration and the copula

**Files:**
- Modify: `backend/src/grimoire/store/weather/noise.py` (no change expected — this task tests what Task 5 wrote)
- Test: `backend/tests/test_weather_field.py`

**Interfaces:**
- Consumes: `noise.window`, `noise.field`, `noise.quantile`.
- Produces: nothing new. This task exists because the filter's *statistical* contract needs its own test cycle — a reviewer can reject the calibration without rejecting the latent.

**The calibration that matters:** `persistence` must measure as the lag-1 autocorrelation. Truncation biases it slightly — the finite filter's value is `a·(1 − a^2W)/(1 − a^2(W+1))` — so the assertion carries a tolerance, and an implementer measuring `0.8997` against a documented `0.9` should know that is correct.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_weather_field.py`:

```python
import math

import pytest

from grimoire.store.weather.noise import field, quantile, window


def series(persistence, n=20_000, zone="saltmarch"):
    return [field("realm", zone, "condition", i, persistence) for i in range(n)]


def lag1(xs):
    mean = sum(xs) / len(xs)
    num = sum((xs[i] - mean) * (xs[i + 1] - mean) for i in range(len(xs) - 1))
    den = sum((x - mean) ** 2 for x in xs)
    return num / den


def test_window_is_zero_at_zero_persistence():
    assert window(0.0) == 0


def test_window_matches_the_documented_examples():
    assert window(0.9) == 38
    assert window(0.99) == 398


def test_window_is_clamped_at_the_upper_bound():
    assert window(1.0) == window(0.998)


def test_zero_persistence_gives_independent_blocks():
    assert abs(lag1(series(0.0))) < 0.03


@pytest.mark.parametrize("p", [0.0, 0.35, 0.5, 0.9])
def test_persistence_is_the_lag_one_autocorrelation(p):
    assert lag1(series(p)) == pytest.approx(p, abs=0.03)


def test_field_has_unit_variance():
    for p in (0.0, 0.5, 0.9):
        xs = series(p)
        mean = sum(xs) / len(xs)
        var = sum((x - mean) ** 2 for x in xs) / len(xs)
        assert var == pytest.approx(1.0, abs=0.05)


def test_higher_persistence_gives_longer_runs():
    def runs(p):
        xs = series(p)
        signs = [x > 0 for x in xs]
        changes = sum(1 for i in range(len(signs) - 1) if signs[i] != signs[i + 1])
        return len(signs) / max(changes, 1)
    assert runs(0.9) > 3 * runs(0.1)


def test_quantile_is_inside_the_unit_interval():
    for i in range(1000):
        u = quantile("realm", "saltmarch", "wind", i, 0.5)
        assert 0.0 < u < 1.0


def test_shared_zone_and_different_persistence_stay_correlated():
    # Both are smoothings of the same latent, so their fields must move together.
    a = [field("realm", "saltmarch", "condition", i, 0.3) for i in range(5000)]
    b = [field("realm", "saltmarch", "condition", i, 0.8) for i in range(5000)]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    norm = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    assert cov / norm > 0.5


def test_different_zones_are_uncorrelated():
    a = [field("realm", "saltmarch", "condition", i, 0.5) for i in range(5000)]
    b = [field("realm", "highreach", "condition", i, 0.5) for i in range(5000)]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    norm = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    assert abs(cov / norm) < 0.1


def test_no_seam_at_an_arbitrary_boundary():
    # Nothing distinguishes one ordinal from another: the correlation across a
    # chosen "boundary" ordinal matches the correlation everywhere else.
    #
    # One pair from each of many *independent* zones, not many pairs strided
    # along one series. Striding gives only a couple of dozen samples whose
    # noisy covariance is then normalized by the global variance, which
    # measures ~1.32 against a true 0.90 — a test that fails against a correct
    # implementation. Independent zones make each pair a fresh draw.
    boundary = 1825
    pairs = [(field("realm", f"seam-{n:04d}", "condition", boundary, 0.9),
              field("realm", f"seam-{n:04d}", "condition", boundary + 1, 0.9))
             for n in range(4000)]
    a = [p[0] for p in pairs]
    b = [p[1] for p in pairs]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in pairs)
    norm = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    assert cov / norm == pytest.approx(0.9, abs=0.05)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_field.py -q`
Expected: PASS if Task 5 was implemented correctly. **If any calibration test fails, that is the bug** — fix `noise.py`, not the test. The most likely culprits: using `a**k` instead of carried powers, normalizing by `sqrt(1 - a**2)` instead of the finite sum, or a two-sided filter (which gives `2a/(1+a²)`, so `0.35` measures as `0.62`).

- [ ] **Step 3: Fix any calibration failure in `noise.py`**

If `test_persistence_is_the_lag_one_autocorrelation` fails at ~0.62 for `p=0.35`, the filter is two-sided. The loop must run `k` from 0 upward over `z(i - k)` only — never `z(i + k)`.

- [ ] **Step 4: Run the whole weather suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_field.py backend/tests/test_weather_noise.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_weather_field.py backend/src/grimoire/store/weather/noise.py
git commit -m "test(weather): pin persistence calibration and zone coupling"
```

---

### Task 7: Drawing a block

**Files:**
- Create: `backend/src/grimoire/store/weather/draw.py`
- Test: `backend/tests/test_weather_draw.py`

**Interfaces:**
- Consumes: `noise.quantile`; a validated climate season dict.
- Produces: `inverse_cdf(entries: list[dict], u: float) -> str` returning a name; `draw(cid, zone, season, persistence, ordinal) -> dict` returning `{"temperature": str, "condition": str, "wind": str}`.

**Order matters:** temperature resolves first so `requires_temp` has something to filter against. Because the condition is *always* read through the temperature-filtered table at the same ordinal, an ineligible combination is not representable — there is no revalidation step to forget.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_weather_draw.py`:

```python
import pytest

from grimoire.store.weather.draw import draw, inverse_cdf

WIND = [{"name": "calm", "weight": 1}, {"name": "breeze", "weight": 4},
        {"name": "strong", "weight": 3}, {"name": "gale", "weight": 1}]


def season(**over):
    s = {"name": "winter", "from": 0.0, "to": 0.0,
         "temperature": [{"name": "freezing", "weight": 2}, {"name": "mild", "weight": 8}],
         "conditions": [{"name": "clear", "weight": 5},
                        {"name": "snow", "weight": 5, "requires_temp": ["freezing"]}],
         "wind": WIND}
    s.update(over)
    return s


def test_inverse_cdf_selects_by_cumulative_weight():
    assert inverse_cdf(WIND, 0.0) == "calm"
    assert inverse_cdf(WIND, 0.2) == "breeze"
    assert inverse_cdf(WIND, 0.6) == "strong"
    assert inverse_cdf(WIND, 0.95) == "gale"


def test_buckets_are_half_open_at_the_boundary():
    # calm occupies [0, 1/9); breeze starts exactly at 1/9.
    assert inverse_cdf(WIND, 1 / 9) == "breeze"


def test_zero_weight_entries_are_never_selected():
    table = [{"name": "a", "weight": 1}, {"name": "b", "weight": 9}, {"name": "off", "weight": 0}]
    drawn = {inverse_cdf(table, i / 1000) for i in range(1000)}
    assert "off" not in drawn


def test_largest_quantile_does_not_fall_through_to_a_disabled_row():
    # The spec's [1, 9, 0] case: the second entry's cumulative rounds to the
    # largest representable quantile, so closing on the physical last entry
    # would hand the draw to the zero-weight row.
    table = [{"name": "a", "weight": 1}, {"name": "b", "weight": 9}, {"name": "off", "weight": 0}]
    assert inverse_cdf(table, (2 * ((1 << 52) - 1) + 1) / (1 << 53)) == "b"


def test_huge_but_valid_weights_still_produce_their_distribution():
    table = [{"name": "a", "weight": 1e300}, {"name": "b", "weight": 1e300}]
    lows = sum(inverse_cdf(table, i / 1000) == "a" for i in range(1000))
    assert 450 < lows < 550


def test_snow_never_appears_outside_its_temperature_band():
    for i in range(4000):
        got = draw("realm", "saltmarch", season(), 0.5, i)
        if got["condition"] == "snow":
            assert got["temperature"] == "freezing"


def test_all_three_axes_are_populated():
    got = draw("realm", "saltmarch", season(), 0.5, 0)
    assert set(got) == {"temperature", "condition", "wind"}
    assert got["wind"] in {e["name"] for e in WIND}


def test_draw_is_deterministic():
    a = draw("realm", "saltmarch", season(), 0.5, 42)
    b = draw("realm", "saltmarch", season(), 0.5, 42)
    assert a == b


def test_degenerate_filtered_table_falls_back_to_an_unconstrained_condition():
    # `mild` has no eligible constrained condition; the fallback must be the
    # unconstrained one, never the constrained row it just filtered out.
    s = season(temperature=[{"name": "mild", "weight": 1}],
               conditions=[{"name": "drizzle", "weight": 3},
                           {"name": "snow", "weight": 5, "requires_temp": ["freezing"]}])
    for i in range(200):
        assert draw("realm", "saltmarch", s, 0.5, i)["condition"] == "drizzle"


def test_weight_fidelity_over_independent_zones():
    # Weight fidelity is a claim about the marginal, so sample across
    # independent streams rather than along one autocorrelated run.
    table = [{"name": "clear", "weight": 2}, {"name": "overcast", "weight": 5},
             {"name": "light rain", "weight": 4}, {"name": "storm", "weight": 1}]
    s = season(temperature=[{"name": "mild", "weight": 1}], conditions=table)
    n = 20_000
    counts = {e["name"]: 0 for e in table}
    for i in range(n):
        counts[draw("fidelity-check", f"fidelity-{i:05d}", s, 0.0, 0)["condition"]] += 1
    total = sum(e["weight"] for e in table)
    for e in table:
        p = e["weight"] / total
        assert counts[e["name"]] / n == pytest.approx(p, abs=3 * (p * (1 - p) / n) ** 0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_draw.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.weather.draw'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/grimoire/store/weather/draw.py`:

```python
"""Turning a quantile into weather.

Three axes, drawn in order. Temperature first, because a condition's
`requires_temp` filters against it — which is why an ineligible combination is
not representable here rather than being prevented by a rule someone has to
remember.
"""

from __future__ import annotations

from .noise import quantile


def inverse_cdf(entries: list[dict], u: float) -> str:
    """The entry whose half-open bucket contains ``u``.

    Zero-weight rows are skipped rather than given empty buckets, and the last
    *positive-weight* row closes the range at 1.0. Closing on the physical last
    entry instead lets floating-point drift hand the draw to a disabled row —
    and if that row was disabled by `requires_temp`, to an ineligible condition.

    Weights are scaled by the largest before summing, so a table of large but
    individually finite weights cannot overflow on the way to a distribution.
    """
    live = [e for e in entries if e["weight"] > 0]
    if not live:
        return entries[0]["name"]  # unreachable for a validated document
    scale = max(e["weight"] for e in live)
    total = sum(e["weight"] / scale for e in live)
    cumulative = 0.0
    for entry in live[:-1]:
        cumulative += (entry["weight"] / scale) / total
        if u < cumulative:
            return entry["name"]
    return live[-1]["name"]


def _eligible(conditions: list[dict], temperature: str) -> list[dict]:
    out = []
    for c in conditions:
        required = c.get("requires_temp")
        if required is None or temperature in required:
            out.append(c)
    if not any(c["weight"] > 0 for c in out):
        # A validated climate cannot reach here; a hand-edited one can. Fall
        # back to the best unconstrained row — never to a filtered-out one,
        # which would emit exactly the combination the constraint forbids.
        unconstrained = [c for c in conditions if c.get("requires_temp") is None and c["weight"] > 0]
        if unconstrained:
            best = max(e["weight"] for e in unconstrained)
            return [next(e for e in unconstrained if e["weight"] == best)]
    return out


def draw(cid: str, zone: str, season: dict, persistence: float, ordinal: int) -> dict:
    """The three resolved axes for one block."""
    temperature = inverse_cdf(
        season["temperature"], quantile(cid, zone, "temperature", ordinal, persistence))
    condition = inverse_cdf(
        _eligible(season["conditions"], temperature),
        quantile(cid, zone, "condition", ordinal, persistence))
    wind = inverse_cdf(
        season["wind"], quantile(cid, zone, "wind", ordinal, persistence))
    return {"temperature": temperature, "condition": condition, "wind": wind}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_draw.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Capture the end-to-end regression fixture**

`latent_u` is pinned to spec vectors, but everything downstream of it —
`inv_cdf`, the filter's evaluation order, `Φ(g)`, the inverse-CDF draw — is
covered only by distribution and correlation tests, which a change can move
without breaking. That change would silently move every existing campaign's
weather. The spec asks for `z`, `g`, `Φ(g)` and the drawn entry to be
fixtured; this is that.

Write `backend/tests/fixtures/generate_weather_vectors.py`:

```python
"""Regenerate the weather regression fixture. Run deliberately, never in CI.

Regenerating after an intentional algorithm change is correct. Regenerating to
make a red test go green destroys the only thing protecting a user's existing
weather from silently moving.
"""

import json
import pathlib

from grimoire.store.weather.draw import draw
from grimoire.store.weather.noise import field, latent_u, latent_z, quantile

SEASON = {
    "name": "winter", "from": 0.0, "to": 0.0,
    "temperature": [{"name": "freezing", "weight": 2}, {"name": "mild", "weight": 8}],
    "conditions": [{"name": "clear", "weight": 5},
                   {"name": "snow", "weight": 5, "requires_temp": ["freezing"]}],
    "wind": [{"name": "calm", "weight": 1}, {"name": "breeze", "weight": 4},
             {"name": "strong", "weight": 3}, {"name": "gale", "weight": 1}],
}
CASES = [("saltmarch-chronicle", "saltmarch", 0, 0.0),
         ("saltmarch-chronicle", "saltmarch", 0, 0.5),
         ("saltmarch-chronicle", "saltmarch", 0, 0.9),
         ("saltmarch-chronicle", "highreach", 137, 0.35),
         ("saltmarch-chronicle", "saltmarch", -42, 0.75)]

rows = []
for cid, zone, i, p in CASES:
    rows.append({
        "cid": cid, "zone": zone, "ordinal": i, "persistence": p,
        "u": latent_u(cid, zone, "condition", i),
        "z": latent_z(cid, zone, "condition", i),
        "g": field(cid, zone, "condition", i, p),
        "phi": quantile(cid, zone, "condition", i, p),
        "drawn": draw(cid, zone, SEASON, p, i),
    })

out = pathlib.Path(__file__).parent / "weather_vectors.json"
out.write_text(json.dumps({"season": SEASON, "rows": rows}, indent=2), encoding="utf-8")
print(f"wrote {len(rows)} rows to {out}")
```

Run it once and inspect the output before committing:

```bash
cd backend && PYTHONPATH=src python -m tests.fixtures.generate_weather_vectors
```

Sanity-check the result by eye: every `u` and `phi` strictly inside (0, 1),
`z` and `g` of order ±3, `drawn.condition` never `snow` unless
`drawn.temperature` is `freezing`. Then append to `backend/tests/test_weather_draw.py`:

```python
def test_end_to_end_regression_fixture():
    """Pins the whole chain: hash, inv_cdf, filter order, phi, inverse CDF.

    Scoped to this installation (spec: Determinism scope) — it detects an
    accidental change to the algorithm, not cross-implementation conformance.
    If this fails, weather in every existing campaign has moved. Regenerate the
    fixture only when that was the intent.
    """
    import json
    import pathlib
    from grimoire.store.weather.noise import field, latent_u, latent_z, quantile

    data = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                       "weather_vectors.json").read_text(encoding="utf-8"))
    for row in data["rows"]:
        cid, zone, i, p = row["cid"], row["zone"], row["ordinal"], row["persistence"]
        assert latent_u(cid, zone, "condition", i) == row["u"]
        assert latent_z(cid, zone, "condition", i) == row["z"]
        assert field(cid, zone, "condition", i, p) == row["g"]
        assert quantile(cid, zone, "condition", i, p) == row["phi"]
        assert draw(cid, zone, data["season"], p, i) == row["drawn"]
```

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_draw.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/weather/draw.py backend/tests/test_weather_draw.py \
        backend/tests/fixtures/generate_weather_vectors.py backend/tests/fixtures/weather_vectors.json
git commit -m "feat(weather): inverse-CDF draw across the three axes"
```

---

### Task 8: Location weather settings

**Files:**
- Create: `backend/src/grimoire/store/weather/settings.py`
- Modify: `backend/src/grimoire/store/entity_schema.py`
- Modify: `frontend/src/api/client.ts:100-106`
- Test: `backend/tests/test_weather_settings.py`
- Test: `backend/tests/test_entity_schema.py` (extend)

**Interfaces:**
- Consumes: `store.overlay.read_entity`, `store.entities.EntityNotFound`, `climates.get`, `climates.FALLBACK_ID`.
- Produces: `resolve(cid: str, location_id: str | None) -> dict` returning `{"climate": dict, "zone": str, "persistence": float}`. Never raises.

**Leniency is the contract.** An unknown climate falls back to the campaign default, an unparseable persistence to the climate's, a deleted location to the campaign default keyed on its own id. Weather must never take a turn down — but note the *authoring* surface (Plan 4) validates strictly, because a typo that silently produces plausible weather from the wrong climate is worse than an error.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_weather_settings.py`:

```python
import json

from grimoire.store import campaigns, climates, worlds
from grimoire.store.weather import settings


def make_campaign(tmp_path):
    worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch Chronicle", "realm")


def location(cid, name, **fields):
    """Create a campaign location, returning its generated id.

    `create_entity` takes a display *name* and slugifies it into the id, and
    accepts the extra frontmatter fields directly.
    """
    from grimoire.store import entities
    return entities.create_entity(
        campaigns.campaign_root(cid), "locations", name, "A place", fields=fields or None)


def test_untagged_location_uses_the_shipped_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    lid = location(cid, "Saltmarch Docks")
    got = settings.resolve(cid, lid)
    assert got["climate"]["id"] == climates.FALLBACK_ID
    assert got["zone"] == lid
    assert got["persistence"] == got["climate"]["persistence"]


def test_campaign_default_is_used_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    (tmp_path / "climates").mkdir()
    (tmp_path / "climates" / "saltmarch-fens.json").write_text(json.dumps({
        "id": "saltmarch-fens", "name": "Fens", "persistence": 0.2,
        "seasons": [{"name": "all", "from": 0.0, "to": 0.0,
                     "temperature": [{"name": "mild", "weight": 1}],
                     "conditions": [{"name": "clear", "weight": 1}],
                     "wind": [{"name": "calm", "weight": 1}]}]}), encoding="utf-8")
    (campaigns.campaign_root(cid) / "climate.json").write_text(
        json.dumps({"default_climate": "saltmarch-fens"}), encoding="utf-8")
    lid = location(cid, "Saltmarch Docks")
    assert settings.resolve(cid, lid)["climate"]["id"] == "saltmarch-fens"


def test_unknown_campaign_default_falls_through_to_the_preset(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    (campaigns.campaign_root(cid) / "climate.json").write_text(
        json.dumps({"default_climate": "deleted-climate"}), encoding="utf-8")
    lid = location(cid, "Saltmarch Docks")
    assert settings.resolve(cid, lid)["climate"]["id"] == climates.FALLBACK_ID


def test_unknown_location_climate_falls_back_without_raising(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    lid = location(cid, "Saltmarch Docks", climate="temperate-costal")  # typo
    assert settings.resolve(cid, lid)["climate"]["id"] == climates.FALLBACK_ID


def test_explicit_weather_zone_is_used(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    lid = location(cid, "Saltmarch Docks", weather_zone="saltmarch")
    assert settings.resolve(cid, lid)["zone"] == "saltmarch"


def test_location_persistence_overrides_the_climate(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    lid = location(cid, "Saltmarch Docks", persistence="0.3")
    assert settings.resolve(cid, lid)["persistence"] == 0.3


def test_out_of_range_persistence_falls_back_to_the_climate(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    for n, bad in enumerate(("2", "-1", "NaN", "wet")):
        lid = location(cid, f"Winifred Hall {n}", persistence=bad)
        got = settings.resolve(cid, lid)
        assert got["persistence"] == got["climate"]["persistence"], bad


def test_deleted_location_resolves_from_the_campaign_default(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    got = settings.resolve(cid, "was-deleted")
    assert got["climate"]["id"] == climates.FALLBACK_ID
    assert got["zone"] == "was-deleted"  # the id is still a stable seed


def test_no_location_at_all_resolves_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = make_campaign(tmp_path)
    assert settings.resolve(cid, None)["climate"]["id"] == climates.FALLBACK_ID
```

Append to `backend/tests/test_entity_schema.py`:

```python
def test_locations_accept_weather_fields():
    from grimoire.store import entity_schema
    assert entity_schema.invalid_keys(
        "locations", {"climate": "temperate-interior", "persistence": "0.3",
                      "weather_zone": "saltmarch"}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_settings.py backend/tests/test_entity_schema.py -q`
Expected: FAIL — `ModuleNotFoundError` for `settings`, and `invalid_keys` returning all three field names.

- [ ] **Step 3: Add the field descriptors**

In `backend/src/grimoire/store/entity_schema.py`, add a `locations` entry to `FIELDS` as the first key:

```python
FIELDS: dict[str, tuple[dict[str, str], ...]] = {
    "locations": (
        {"key": "climate", "label": "Climate", "widget": "text"},
        {"key": "persistence", "label": "Weather persistence", "widget": "text"},
        {"key": "weather_zone", "label": "Weather zone", "widget": "text"},
    ),
    "items": (
```

In `frontend/src/api/client.ts`, replace `locations: [],` in `ENTITY_FIELDS`:

```typescript
  locations: [
    { key: "climate", label: "Climate" },
    { key: "persistence", label: "Weather persistence" },
    { key: "weather_zone", label: "Weather zone" },
  ],
```

- [ ] **Step 4: Write the settings resolver**

Create `backend/src/grimoire/store/weather/settings.py`:

```python
"""Which climate, zone and persistence apply at a location.

Lenient throughout, and deliberately so: this runs inside prompt assembly, so
anything that raises here takes a turn down. A typo resolves to the campaign
default rather than an error — which is why the authoring surface validates
strictly instead, where the user is present to be told.
"""

from __future__ import annotations

import json
import math

from .. import campaigns, climates, entities, overlay


def _campaign_default(cid: str) -> dict:
    """The campaign's default climate, or the shipped preset."""
    path = campaigns.campaign_root(cid) / "climate.json"
    wanted = None
    try:
        wanted = json.loads(path.read_text(encoding="utf-8")).get("default_climate")
    except (OSError, json.JSONDecodeError, AttributeError):
        wanted = None
    return (climates.get(wanted) if wanted else None) or climates.get(climates.FALLBACK_ID)


def _fields(cid: str, location_id: str) -> dict:
    """A location's frontmatter, or {} if it no longer exists.

    Deleting a location does not clean the scene histories naming it, so this
    is reached in ordinary use — `context.py` already wraps the same read for
    the setting block.
    """
    try:
        return overlay.read_entity(cid, "locations", location_id).get("meta", {})
    except (entities.EntityNotFound, KeyError, OSError):
        return {}


def _persistence(raw, fallback: float) -> float:
    """A finite number in [0, 1], or the fallback.

    "2", "-1" and "NaN" all parse successfully and are all invalid — accepting
    them looks like a working setting while producing nonsense.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return fallback
    return value


def resolve(cid: str, location_id: str | None) -> dict:
    """{climate, zone, persistence} for a location. Never raises."""
    default = _campaign_default(cid)
    if not location_id:
        return {"climate": default, "zone": "_default",
                "persistence": default.get("persistence", 0.5)}

    meta = _fields(cid, location_id)
    climate = climates.get(meta.get("climate", "")) or default
    return {
        "climate": climate,
        "zone": meta.get("weather_zone") or location_id,
        "persistence": _persistence(meta.get("persistence"), climate.get("persistence", 0.5)),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_settings.py backend/tests/test_entity_schema.py -q`
Expected: PASS

Run from `frontend/`: `npx tsc -b`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/weather/settings.py backend/src/grimoire/store/entity_schema.py \
        frontend/src/api/client.ts backend/tests/test_weather_settings.py backend/tests/test_entity_schema.py
git commit -m "feat(weather): resolve per-location climate, zone and persistence"
```

---

### Task 9: The public entry point

**Files:**
- Modify: `backend/src/grimoire/store/weather/__init__.py`
- Test: `backend/tests/test_weather_resolve.py`

**Interfaces:**
- Consumes: everything above; `store.calendars.read_calendar`, `get_provider`, `fixed_of`, `minutes_of`, `CalendarError`.
- Produces: `current_weather(cid: str, location_id: str | None, native: str | None) -> dict | None` returning `{"temperature", "condition", "wind", "climate", "season"}` or `None`.

**Nullable on purpose.** A scene with no location, no moment, or a stored moment the campaign's current calendar can no longer parse all return `None` and render no section. Plan 2 adds the override layer *inside* this function; the signature does not change.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_weather_resolve.py`:

```python
from grimoire.store import campaigns, weather, worlds


def setup(monkeypatch, tmp_path):
    """Returns (cid, location id). `create_entity` slugifies the name it is given."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    from grimoire.store import entities
    lid = entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch Docks")
    return cid, lid


def test_resolves_all_three_axes(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    got = weather.current_weather(cid, lid, "2026-06-14T09:00")
    assert set(got) >= {"temperature", "condition", "wind", "climate", "season"}


def test_same_moment_resolves_identically(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    a = weather.current_weather(cid, lid, "2026-06-14T09:00")
    b = weather.current_weather(cid, lid, "2026-06-14T09:00")
    assert a == b


def test_one_night_block_is_stable_across_midnight(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    late = weather.current_weather(cid, lid, "2026-06-14T23:00")
    early = weather.current_weather(cid, lid, "2026-06-15T01:00")
    assert late == early


def test_a_night_spanning_a_season_boundary_stays_in_one_season(monkeypatch, tmp_path):
    # The boundary must actually fall on the crossed date, or the test passes
    # even when the season is (wrongly) looked up per queried moment. The
    # shipped fallback's winter wraps the year end, so 31 Dec / 1 Jan are both
    # winter and would prove nothing. 182/365 puts the boundary on 2 July.
    import json
    cid, lid = setup(monkeypatch, tmp_path)
    (tmp_path / "climates").mkdir(exist_ok=True)
    (tmp_path / "climates" / "split-year.json").write_text(json.dumps({
        "id": "split-year", "name": "Split Year", "persistence": 0.5,
        "seasons": [
            {"name": "first", "from": 0.0, "to": 182 / 365,
             "temperature": [{"name": "mild", "weight": 1}],
             "conditions": [{"name": "clear", "weight": 1}],
             "wind": [{"name": "calm", "weight": 1}]},
            {"name": "second", "from": 182 / 365, "to": 0.0,
             "temperature": [{"name": "hot", "weight": 1}],
             "conditions": [{"name": "dry", "weight": 1}],
             "wind": [{"name": "still", "weight": 1}]},
        ]}), encoding="utf-8")
    (campaigns.campaign_root(cid) / "climate.json").write_text(
        json.dumps({"default_climate": "split-year"}), encoding="utf-8")

    late = weather.current_weather(cid, lid, "2026-07-01T23:00")
    early = weather.current_weather(cid, lid, "2026-07-02T01:00")
    assert late["season"] == "first"      # 1 July's night, owned by 1 July
    assert early["season"] == "first"     # the same block, not 2 July's season
    assert late == early


def test_the_season_does_change_at_the_boundary_dawn(monkeypatch, tmp_path):
    # The mirror of the test above: the boundary is real, it just takes effect
    # at the first block the new date owns rather than at midnight.
    import json
    cid, lid = setup(monkeypatch, tmp_path)
    (tmp_path / "climates").mkdir(exist_ok=True)
    (tmp_path / "climates" / "split-year.json").write_text(json.dumps({
        "id": "split-year", "name": "Split Year", "persistence": 0.5,
        "seasons": [
            {"name": "first", "from": 0.0, "to": 182 / 365,
             "temperature": [{"name": "mild", "weight": 1}],
             "conditions": [{"name": "clear", "weight": 1}],
             "wind": [{"name": "calm", "weight": 1}]},
            {"name": "second", "from": 182 / 365, "to": 0.0,
             "temperature": [{"name": "hot", "weight": 1}],
             "conditions": [{"name": "dry", "weight": 1}],
             "wind": [{"name": "still", "weight": 1}]},
        ]}), encoding="utf-8")
    (campaigns.campaign_root(cid) / "climate.json").write_text(
        json.dumps({"default_climate": "split-year"}), encoding="utf-8")
    assert weather.current_weather(cid, lid, "2026-07-02T06:00")["season"] == "second"


def test_a_moment_at_the_calendar_lower_bound_does_not_raise(monkeypatch, tmp_path):
    # 0001-01-01T01:00 parses, but its block belongs to the previous date, and
    # `date.fromordinal(0)` raises. Weather must degrade, not take the turn down.
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, lid, "0001-01-01T01:00") is None


def test_different_blocks_can_differ(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    seen = {tuple(sorted(weather.current_weather(cid, lid, f"2026-06-{d:02d}T09:00").items()))
            for d in range(1, 29)}
    assert len(seen) > 1


def test_missing_moment_returns_none(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, lid, None) is None


def test_missing_location_returns_none(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, None, "2026-06-14T09:00") is None


def test_unparseable_moment_returns_none(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, lid, "not-a-date") is None


def test_date_without_a_clock_resolves(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, lid, "2026-06-14") is not None


def test_deleted_location_still_resolves(monkeypatch, tmp_path):
    cid, lid = setup(monkeypatch, tmp_path)
    assert weather.current_weather(cid, "gone-away", "2026-06-14T09:00") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_resolve.py -q`
Expected: FAIL — `AttributeError: module 'grimoire.store.weather' has no attribute 'current_weather'`

- [ ] **Step 3: Write the entry point**

Replace `backend/src/grimoire/store/weather/__init__.py`:

```python
"""Weather for a campaign moment.

Pure: nothing is stored, and any block resolves in O(1) whatever the campaign's
age. Plan 2 layers manual and extractor overrides on top of the procedural draw
inside `current_weather` without changing this signature.
"""

from __future__ import annotations

from . import blocks, draw as _draw, seasons, settings
from .. import calendars, campaigns


def current_weather(cid: str, location_id: str | None, native: str | None) -> dict | None:
    """Resolved weather, or None when there is nothing to resolve.

    None covers three real cases, none of which may raise: a scene with no
    location, a scene with no moment, and a stored moment the campaign's
    current calendar can no longer parse — which happens when the primary
    provider is switched after scenes exist.
    """
    if not location_id or not native:
        return None

    cfg = calendars.read_calendar(campaigns.campaign_root(cid))
    try:
        provider = calendars.get_provider(cfg["primary"])
        fixed = calendars.fixed_of(provider, native)
        minutes = calendars.minutes_of(native)
    except (calendars.CalendarError, KeyError, TypeError):
        return None

    owning_day, _ = blocks.block_of(fixed, minutes)
    ordinal = blocks.ordinal(fixed, minutes)

    resolved = settings.resolve(cid, location_id)
    climate = resolved["climate"]
    # Season comes from the block's owning date, not the queried moment: a
    # night spans midnight and may span a season boundary, and one block must
    # not render two different skies depending which minute inside it is asked.
    #
    # ValueError is in the net because the owning date can fall one day below
    # the provider's range: 0001-01-01T01:00 parses fine, but its block is the
    # previous date's night, and `gregorian.describe` calls `date.fromordinal`,
    # which rejects day 0. Rare, but it would raise inside prompt assembly.
    try:
        fraction = seasons.year_fraction(provider, owning_day)
    except (calendars.CalendarError, ValueError, OverflowError):
        return None
    season = seasons.season_for(climate, fraction)

    axes = _draw.draw(cid, resolved["zone"], season, resolved["persistence"], ordinal)
    return {**axes, "climate": climate["id"], "season": season["name"]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_resolve.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/weather/__init__.py backend/tests/test_weather_resolve.py
git commit -m "feat(weather): current_weather entry point"
```

---

### Task 10: The prompt section

**Files:**
- Create: `templates/scene/sections/weather.j2`
- Modify: `backend/src/grimoire/store/context.py` (add `_weather_data`, the `_assemble` key, and the `_SECTIONS` entry)
- Modify: `templates/scene/system.j2` (include the section)
- Test: `backend/tests/test_weather_prompt.py`

**Interfaces:**
- Consumes: `weather.current_weather`; `scenes.get_location_history`, `scenes.get_time_history`.
- Produces: a `weather` key in the `_assemble` data dict, shaped `{"condition", "temperature", "wind"} | None`.

**The trap this task exists to avoid:** `context._SECTIONS` is *only* the token-breakdown view — `context.py:644` says so. The prompt itself comes from `templates/scene/system.j2`'s hard-coded include chain. Registering in one place and not the other yields a weather line visible in the token breakdown and absent from every prompt sent to the model.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_weather_prompt.py`:

```python
from grimoire import prompts
from grimoire.store import campaigns, context, entities, scenes, worlds


def scene_at(monkeypatch, tmp_path, location=True, when="2026-06-14T09:00"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    sid = scenes.create_scene(cid, "Arrival")
    if location:
        lid = entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch Docks")
        scenes.set_location(cid, sid, lid)
    if when:
        # set_datetime renames the scene file on first set, so re-read the id.
        sid = scenes.set_datetime(cid, sid, when).get("id", sid)
    return cid, sid


def test_section_renders_a_sentence():
    out = prompts.render("scene/sections/weather.j2",
                         weather={"condition": "overcast", "temperature": "cold", "wind": "breeze"})
    assert "overcast" in out and "cold" in out and "breeze" in out


def test_section_is_empty_without_weather():
    assert prompts.render("scene/sections/weather.j2", weather=None).strip() == ""


def test_assemble_carries_weather(monkeypatch, tmp_path):
    cid, sid = scene_at(monkeypatch, tmp_path)
    data = context._assemble(cid, sid)["data"]
    assert set(data["weather"]) == {"condition", "temperature", "wind"}


def test_assemble_omits_weather_without_a_location(monkeypatch, tmp_path):
    cid, sid = scene_at(monkeypatch, tmp_path, location=False)
    assert context._assemble(cid, sid)["data"]["weather"] is None


def test_assemble_omits_weather_without_a_moment(monkeypatch, tmp_path):
    cid, sid = scene_at(monkeypatch, tmp_path, when=None)
    assert context._assemble(cid, sid)["data"]["weather"] is None


def test_weather_reaches_the_system_prompt(monkeypatch, tmp_path):
    cid, sid = scene_at(monkeypatch, tmp_path)
    data = context._assemble(cid, sid)["data"]
    rendered = prompts.render("scene/system.j2", **data)
    assert data["weather"]["condition"] in rendered


def test_weather_is_in_the_token_breakdown(monkeypatch, tmp_path):
    cid, sid = scene_at(monkeypatch, tmp_path)
    assert any(s["label"] == "Weather" for s in context.context_sections(cid, sid))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_prompt.py -q`
Expected: FAIL — `jinja2.exceptions.TemplateNotFound: scene/sections/weather.j2`

- [ ] **Step 3: Write the template**

Create `templates/scene/sections/weather.j2`:

```jinja
{#- The current sky. Vars: weather (None, or {condition, temperature, wind}). -#}
{%- if weather -%}
Weather: {{ weather.condition }}, {{ weather.temperature }}, wind {{ weather.wind }}.
{%- endif -%}
```

- [ ] **Step 4: Wire it into context.py**

In `backend/src/grimoire/store/context.py`, add after `_today_data`:

```python
def _weather_data(cid: str, sid: str) -> dict | None:
    """The sky at the scene's current location and moment, or None.

    Tolerant by construction — `current_weather` returns None rather than
    raising for a missing location, a missing moment, or a stored moment the
    campaign's calendar can no longer parse.
    """
    locations = scenes.get_location_history(cid, sid)
    moments = scenes.get_time_history(cid, sid)
    got = weather.current_weather(cid, locations[-1] if locations else None,
                                  moments[-1] if moments else None)
    if not got:
        return None
    return {k: got[k] for k in ("condition", "temperature", "wind")}
```

Add `weather` to the imports at the top of `context.py` (alongside `scenes`, `calendars`, …).

In the `_assemble` data dict, add immediately after the `"today"` line:

```python
        "weather": _weather_data(cid, sid),
```

In `_SECTIONS`, add immediately after the `("Today", …)` entry:

```python
    ("Weather", "scene/sections/weather.j2", False),
```

- [ ] **Step 5: Wire it into system.j2**

In `templates/scene/system.j2`, add immediately after the `today.j2` block:

```jinja
{%- set s -%}{%- include "scene/sections/weather.j2" -%}{%- endset -%}
{%- if s.strip() -%}{%- set _ = sections.append(s.strip()) -%}{%- endif -%}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_weather_prompt.py -q`
Expected: PASS (7 tests)

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS — no regressions in `test_context.py`

Run: `backend/.venv/Scripts/python.exe scripts/verify_templates.py`
Expected: all templates render

- [ ] **Step 7: Commit**

```bash
git add templates/scene/sections/weather.j2 templates/scene/system.j2 \
        backend/src/grimoire/store/context.py backend/tests/test_weather_prompt.py
git commit -m "feat(weather): render the weather section into scene prompts"
```

---

### Task 11: Campaign default climate file

**Files:**
- Modify: `backend/src/grimoire/store/campaigns.py` (`create_campaign` signature and body)
- Test: `backend/tests/test_campaigns_store.py` (extend)

**Interfaces:**
- Consumes: `climates.get`, `climates.FALLBACK_ID`.
- Produces: `create_campaign(name, world_id, region=None, calendar=None, module=None, climate=None) -> str`, writing `campaigns/<cid>/climate.json`.

**Validate before writing anything**, exactly as `create_campaign` already resolves its `calendar` argument up front: an unknown climate must fail before a campaign directory exists, not produce a campaign whose every untagged location silently reads the fallback.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_campaigns_store.py`:

```python
def test_create_campaign_writes_the_default_climate(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import campaigns, climates, worlds
    import json
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm",
                                    climate=climates.FALLBACK_ID)
    written = json.loads((campaigns.campaign_root(cid) / "climate.json").read_text())
    assert written == {"default_climate": climates.FALLBACK_ID}


def test_create_campaign_defaults_the_climate_when_omitted(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import campaigns, climates, worlds
    import json
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    written = json.loads((campaigns.campaign_root(cid) / "climate.json").read_text())
    assert written == {"default_climate": climates.FALLBACK_ID}


def test_create_campaign_rejects_an_unknown_climate_before_creating_anything(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import campaigns, climates, worlds
    import pytest
    worlds.create_world("Realm")
    # list_campaigns() returns dicts, so compare ids rather than the rows.
    before = {c["id"] for c in campaigns.list_campaigns()}
    with pytest.raises(climates.ClimateError):
        campaigns.create_campaign("Doomed", "realm", climate="no-such-climate")
    assert {c["id"] for c in campaigns.list_campaigns()} == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_campaigns_store.py -q`
Expected: FAIL — `TypeError: create_campaign() got an unexpected keyword argument 'climate'`

- [ ] **Step 3: Extend create_campaign**

In `backend/src/grimoire/store/campaigns.py`, add `json` to the imports — the
module currently imports only `filecmp`, `shutil` and `Path`, so the write
below would raise `NameError` *after* the campaign directory exists:

```python
import filecmp
import json
import shutil
```

Change the signature:

```python
def create_campaign(name: str, world_id: str, region: str | None = None,
                    calendar: str | None = None, module: str | None = None,
                    climate: str | None = None) -> str:
```

Add the validation beside the existing calendar check, before `cid = uniquify(...)`:

```python
    from . import climates
    wanted_climate = climate or climates.FALLBACK_ID
    if climates.get(wanted_climate) is None:  # unknown id -> fail before anything is created
        raise climates.ClimateError(f"unknown climate: {wanted_climate!r}")
```

Add the write beside `calendars.copy_calendar(...)`:

```python
    (root / "climate.json").write_text(
        json.dumps({"default_climate": wanted_climate}), encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_campaigns_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/campaigns.py backend/tests/test_campaigns_store.py
git commit -m "feat(weather): campaign default climate written at create time"
```

---

### Task 12: The remaining shipped presets

**Files:**
- Create: `backend/src/grimoire/store/climates/presets/temperate-coastal.json`
- Create: `backend/src/grimoire/store/climates/presets/high-desert.json`
- Create: `backend/src/grimoire/store/climates/presets/monsoon.json`
- Create: `backend/src/grimoire/store/climates/presets/boreal.json`
- Create: `backend/src/grimoire/store/climates/presets/equatorial.json`
- Test: `backend/tests/test_climate_presets.py`

**Interfaces:**
- Consumes: `climates.list_climates`, `climates.schema.validate`.
- Produces: nothing new — content only.

Season counts differ on purpose: a monsoon climate has two seasons and an equatorial one has a single year-long season. That is the whole reason seasons are climate-declared rather than calendar-declared.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_climate_presets.py`:

```python
import pytest

from grimoire.store import climates

EXPECTED = {"temperate-interior", "temperate-coastal", "high-desert",
            "monsoon", "boreal", "equatorial"}


def test_all_documented_presets_ship(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert {c["id"] for c in climates.list_climates()} == EXPECTED


@pytest.mark.parametrize("cid", sorted(EXPECTED))
def test_every_preset_loads_and_validates(monkeypatch, tmp_path, cid):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert climates.get(cid) is not None


def test_presets_have_climatically_varied_season_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    counts = {c: len(climates.get(c)["seasons"]) for c in EXPECTED}
    assert counts["equatorial"] == 1
    assert counts["monsoon"] == 2
    assert counts["temperate-interior"] == 4


def test_every_preset_resolves_weather_all_year(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store.weather.draw import draw
    from grimoire.store.weather.seasons import season_for
    for cid in EXPECTED:
        climate = climates.get(cid)
        for step in range(100):
            season = season_for(climate, step / 100)
            got = draw("realm", "saltmarch", season, climate["persistence"], step)
            assert got["condition"] and got["temperature"] and got["wind"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_climate_presets.py -q`
Expected: FAIL — only `temperate-interior` is present

- [ ] **Step 3: Author the presets**

`backend/src/grimoire/store/climates/presets/temperate-coastal.json`:

```json
{
  "id": "temperate-coastal",
  "name": "Temperate Coastal",
  "persistence": 0.35,
  "seasons": [
    {
      "name": "winter", "from": 0.92, "to": 0.21,
      "temperature": [
        { "name": "freezing", "weight": 2 },
        { "name": "cold", "weight": 6 },
        { "name": "mild", "weight": 2 }
      ],
      "conditions": [
        { "name": "clear", "weight": 2 },
        { "name": "overcast", "weight": 5 },
        { "name": "light rain", "weight": 4 },
        { "name": "snow", "weight": 2, "requires_temp": ["freezing"] },
        { "name": "storm", "weight": 1 }
      ],
      "wind": [
        { "name": "calm", "weight": 1 },
        { "name": "breeze", "weight": 4 },
        { "name": "strong", "weight": 3 },
        { "name": "gale", "weight": 1 }
      ]
    },
    {
      "name": "spring", "from": 0.21, "to": 0.42,
      "temperature": [
        { "name": "cold", "weight": 3 },
        { "name": "mild", "weight": 6 },
        { "name": "warm", "weight": 1 }
      ],
      "conditions": [
        { "name": "clear", "weight": 3 },
        { "name": "overcast", "weight": 4 },
        { "name": "light rain", "weight": 4 },
        { "name": "storm", "weight": 1 }
      ],
      "wind": [
        { "name": "calm", "weight": 2 },
        { "name": "breeze", "weight": 5 },
        { "name": "strong", "weight": 2 },
        { "name": "gale", "weight": 1 }
      ]
    },
    {
      "name": "summer", "from": 0.42, "to": 0.67,
      "temperature": [
        { "name": "mild", "weight": 5 },
        { "name": "warm", "weight": 5 },
        { "name": "hot", "weight": 1 }
      ],
      "conditions": [
        { "name": "clear", "weight": 6 },
        { "name": "overcast", "weight": 3 },
        { "name": "light rain", "weight": 3 },
        { "name": "storm", "weight": 1 }
      ],
      "wind": [
        { "name": "calm", "weight": 3 },
        { "name": "breeze", "weight": 5 },
        { "name": "strong", "weight": 2 }
      ]
    },
    {
      "name": "autumn", "from": 0.67, "to": 0.92,
      "temperature": [
        { "name": "cold", "weight": 4 },
        { "name": "mild", "weight": 5 },
        { "name": "warm", "weight": 1 }
      ],
      "conditions": [
        { "name": "clear", "weight": 2 },
        { "name": "overcast", "weight": 5 },
        { "name": "light rain", "weight": 5 },
        { "name": "storm", "weight": 2 }
      ],
      "wind": [
        { "name": "calm", "weight": 1 },
        { "name": "breeze", "weight": 4 },
        { "name": "strong", "weight": 3 },
        { "name": "gale", "weight": 2 }
      ]
    }
  ]
}
```

`backend/src/grimoire/store/climates/presets/high-desert.json`:

```json
{
  "id": "high-desert",
  "name": "High Desert",
  "persistence": 0.7,
  "seasons": [
    {
      "name": "cold season", "from": 0.83, "to": 0.25,
      "temperature": [
        { "name": "freezing", "weight": 4 },
        { "name": "cold", "weight": 5 },
        { "name": "mild", "weight": 2 }
      ],
      "conditions": [
        { "name": "clear", "weight": 8 },
        { "name": "overcast", "weight": 2 },
        { "name": "snow", "weight": 1, "requires_temp": ["freezing"] }
      ],
      "wind": [
        { "name": "still", "weight": 3 },
        { "name": "breeze", "weight": 3 },
        { "name": "dust wind", "weight": 3 }
      ]
    },
    {
      "name": "hot season", "from": 0.25, "to": 0.83,
      "temperature": [
        { "name": "mild", "weight": 2 },
        { "name": "warm", "weight": 4 },
        { "name": "scorching", "weight": 6 }
      ],
      "conditions": [
        { "name": "clear", "weight": 9 },
        { "name": "hazy", "weight": 3 },
        { "name": "dust storm", "weight": 1 }
      ],
      "wind": [
        { "name": "still", "weight": 4 },
        { "name": "breeze", "weight": 3 },
        { "name": "dust wind", "weight": 3 }
      ]
    }
  ]
}
```

`backend/src/grimoire/store/climates/presets/monsoon.json`:

```json
{
  "id": "monsoon",
  "name": "Monsoon",
  "persistence": 0.6,
  "seasons": [
    {
      "name": "dry", "from": 0.75, "to": 0.4,
      "temperature": [
        { "name": "warm", "weight": 5 },
        { "name": "hot", "weight": 5 }
      ],
      "conditions": [
        { "name": "clear", "weight": 7 },
        { "name": "hazy", "weight": 3 },
        { "name": "light rain", "weight": 1 }
      ],
      "wind": [
        { "name": "still", "weight": 4 },
        { "name": "breeze", "weight": 4 },
        { "name": "strong", "weight": 1 }
      ]
    },
    {
      "name": "wet", "from": 0.4, "to": 0.75,
      "temperature": [
        { "name": "warm", "weight": 7 },
        { "name": "hot", "weight": 3 }
      ],
      "conditions": [
        { "name": "overcast", "weight": 4 },
        { "name": "downpour", "weight": 7 },
        { "name": "storm", "weight": 3 },
        { "name": "clear", "weight": 1 }
      ],
      "wind": [
        { "name": "still", "weight": 2 },
        { "name": "breeze", "weight": 4 },
        { "name": "strong", "weight": 3 },
        { "name": "gale", "weight": 1 }
      ]
    }
  ]
}
```

`backend/src/grimoire/store/climates/presets/boreal.json`:

```json
{
  "id": "boreal",
  "name": "Boreal",
  "persistence": 0.75,
  "seasons": [
    {
      "name": "long winter", "from": 0.8, "to": 0.35,
      "temperature": [
        { "name": "bitter", "weight": 6 },
        { "name": "freezing", "weight": 4 },
        { "name": "cold", "weight": 1 }
      ],
      "conditions": [
        { "name": "clear", "weight": 3 },
        { "name": "overcast", "weight": 4 },
        { "name": "snow", "weight": 5, "requires_temp": ["bitter", "freezing"] },
        { "name": "blizzard", "weight": 1, "requires_temp": ["bitter"] }
      ],
      "wind": [
        { "name": "still", "weight": 3 },
        { "name": "breeze", "weight": 3 },
        { "name": "cutting wind", "weight": 4 }
      ]
    },
    {
      "name": "brief summer", "from": 0.35, "to": 0.8,
      "temperature": [
        { "name": "cold", "weight": 5 },
        { "name": "mild", "weight": 5 }
      ],
      "conditions": [
        { "name": "clear", "weight": 4 },
        { "name": "overcast", "weight": 4 },
        { "name": "light rain", "weight": 3 }
      ],
      "wind": [
        { "name": "still", "weight": 4 },
        { "name": "breeze", "weight": 4 },
        { "name": "cutting wind", "weight": 1 }
      ]
    }
  ]
}
```

`backend/src/grimoire/store/climates/presets/equatorial.json`:

```json
{
  "id": "equatorial",
  "name": "Equatorial",
  "persistence": 0.45,
  "seasons": [
    {
      "name": "all year", "from": 0.0, "to": 0.0,
      "temperature": [
        { "name": "warm", "weight": 6 },
        { "name": "hot", "weight": 4 }
      ],
      "conditions": [
        { "name": "clear", "weight": 3 },
        { "name": "overcast", "weight": 4 },
        { "name": "afternoon rain", "weight": 5 },
        { "name": "storm", "weight": 2 }
      ],
      "wind": [
        { "name": "still", "weight": 5 },
        { "name": "breeze", "weight": 4 },
        { "name": "strong", "weight": 1 }
      ]
    }
  ]
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_climate_presets.py -q`
Expected: PASS (9 tests)

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/climates/presets backend/tests/test_climate_presets.py
git commit -m "feat(weather): ship the six documented climate presets"
```

---

## Self-Review

**1. Spec coverage.** Plan-1 scope only; later-plan items are listed under § Scope.

| Spec section | Task |
| --- | --- |
| Climate documents (schema, validation, ordering, weights) | 1 |
| Two-tier registry, custom shadowing, best-effort load | 2 |
| Blocks, ordinals, night-across-midnight, no-clock default | 3 |
| Seasons as year fractions, wrapping, full-year | 4 |
| Latent field, hash, reference vectors | 5 |
| Filter, calibration, W, copula, zone coupling | 6 |
| `inverse_cdf`, three-axis draw, `requires_temp`, degenerate fallback | 7 |
| Location fields, leniency, `entity_schema` + `ENTITY_FIELDS` | 8 |
| `current_weather`, nullability, unparseable moment, season from owning date | 9 |
| Prompt section in **both** `system.j2` and `_SECTIONS` | 10 |
| `campaigns/<cid>/climate.json`, create-time validation | 11 |
| Six shipped presets | 12 |

**Deferred with the feature they belong to:** override store and precedence (Plan 2); HUD, popover, extractor, sweep (Plan 3); climate editor, campaign-default control, boundary date conversion (Plan 4). Accumulators are deferred by the spec itself.

**2. Placeholder scan.** No "TBD", no "add validation", no "similar to Task N". Every code step carries runnable code; every test step carries assertions.

**3. Type consistency.** `latent_u`/`latent_z`/`window`/`field`/`quantile` (noise.py) are used with those exact names in Tasks 6, 7. `inverse_cdf`/`draw` (draw.py) match Task 9's call. `resolve` returns `{climate, zone, persistence}` and Task 9 reads exactly those keys. `block_of` returns `(owning_day, position)` and Task 9 destructures it that way. `current_weather` returns `condition`/`temperature`/`wind`/`climate`/`season`; Task 10 selects the first three.

**One inconsistency found and fixed while reviewing:** Task 5's tests referenced `window`/`field`/`quantile`, which the spec places in the same module but the original task text only produced `latent_u`/`latent_z`. The implementation in Task 5 now defines all five, and Task 6 tests the statistical contract rather than adding functions.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-27-weather-core.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
