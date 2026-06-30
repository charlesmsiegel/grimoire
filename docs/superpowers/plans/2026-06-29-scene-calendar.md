# Scene Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give scenes a real, correct date + optional time so weekday, holidays, and character ages are computed facts (never hallucinated), starting with the Gregorian calendar but built on a pluggable provider engine that future calendars slot into.

**Architecture:** A `store/calendars/` provider registry over a fixed-day integer axis (proleptic Gregorian ordinal). One shipped provider, `gregorian` (Python `datetime` + the `holidays` package). Calendar config (provider, region, custom holidays, optional anchor, optional synchronized secondary calendar) lives in `calendar.json`, world-scoped and copied into a campaign on create. Scenes store a single moment in `time_history` (mirrors the existing `location_history`); the context builder injects an always-on `# Today` block of computed facts. Birthdays live on cast members; ages are computed by the provider.

**Tech Stack:** Python 3.11+, FastAPI, pytest (backend); React + TypeScript, Vite, vitest (frontend). New dependency: `holidays`.

## Global Constraints

- Python `requires-python = ">=3.11"`. Backend deps live in `backend/pyproject.toml`.
- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Run frontend tests: `npx --prefix frontend vitest run`; typecheck: `tsc -b` in `frontend/`.
- Frontmatter is **string-scalars only** (`store/frontmatter.py`). Structured data (holiday lists, anchors) MUST be JSON, never frontmatter.
- The model never does date arithmetic. All weekday/holiday/age values are computed server-side.
- Follow the list/detail + scene-location patterns already in the codebase. Mirror `set_location`/`get_location_history` for `set_datetime`/`get_time_history`.
- Commit after every task (each task ends green).

---

## File Structure

**Created:**
- `backend/src/grimoire/store/calendars/__init__.py` — package exports (`get_provider`, `CalendarError`, helpers `normalize`, `fixed_of`, `minutes_of`, `friendly`, `age`, `is_anniversary`, `today_facts`, and config IO re-exports).
- `backend/src/grimoire/store/calendars/base.py` — `CalendarProvider` ABC, `CalendarError`, `REGISTRY`, `get_provider`, the calendar-agnostic helpers, `today_facts`.
- `backend/src/grimoire/store/calendars/gregorian.py` — `GregorianProvider`.
- `backend/src/grimoire/store/calendars/config.py` — `read_calendar`, `write_calendar`, `copy_calendar`, defaults.
- `backend/tests/test_calendars.py` — provider + helpers + config tests.
- `frontend/src/components/CalendarConfig.tsx` — region + custom-holiday + secondary editor.
- `frontend/src/components/CalendarConfig.test.tsx`.

**Modified:**
- `backend/pyproject.toml` — add `holidays` dependency.
- `backend/src/grimoire/store/scenes.py` — `get_time_history`, `set_datetime`.
- `backend/src/grimoire/store/pcs.py` — `birthdate` in `PERSONA_FIELDS`.
- `backend/src/grimoire/store/characters.py` — `birthdate` on container meta + `set_birthdate`.
- `backend/src/grimoire/store/campaigns.py` — copy calendar on create + optional region.
- `backend/src/grimoire/store/context.py` — `# Today` block.
- `backend/src/grimoire/routes.py` — datetime + calendar-config + birthdate routes; region on campaign create.
- `backend/tests/test_scene_store.py`, `test_routes.py`, `test_context.py`, `test_appearances_store.py` — new tests.
- `frontend/src/api/client.ts` — datetime + calendar-config clients, `createCampaign` region.
- `frontend/src/components/CastPanel.tsx` — "When" section.
- `frontend/src/routes/CampaignWizard.tsx` — region picker.
- `frontend/src/components/CastPanel.test.tsx`, `CampaignWizard.test.tsx`, `client.test.ts` — new tests.

---

## Phase 1 — Calendar engine & config

### Task 1: Calendar provider base + Gregorian date core

**Files:**
- Modify: `backend/pyproject.toml:6-12`
- Create: `backend/src/grimoire/store/calendars/__init__.py`
- Create: `backend/src/grimoire/store/calendars/base.py`
- Create: `backend/src/grimoire/store/calendars/gregorian.py`
- Test: `backend/tests/test_calendars.py`

**Interfaces:**
- Produces:
  - `CalendarError(Exception)`
  - `class CalendarProvider(ABC)` with `parse(native: str) -> int`, `format(fixed: int) -> str`, `describe(fixed: int) -> dict` (`{year, month, month_name, day, weekday_name, weekday_index, friendly}`).
  - `get_provider(config: dict) -> CalendarProvider` (config = a single calendar block `{provider, region, custom_holidays, anchor}`).
  - Module helpers in `base.py`, re-exported from the package: `split_native(native) -> tuple[str, str | None]`, `minutes_of(native) -> int | None`, `fixed_of(provider, native) -> int`, `normalize(provider, native) -> str`, `friendly(provider, native) -> str`.

- [ ] **Step 1: Add the `holidays` dependency**

In `backend/pyproject.toml`, add `holidays` to `dependencies`:

```toml
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "httpx>=0.27",
    "python-multipart>=0.0.9",
    "tiktoken>=0.7",
    "holidays>=0.40",
]
```

Then install it into the venv:

Run: `backend/.venv/Scripts/python.exe -m pip install "holidays>=0.40"`
Expected: ends with `Successfully installed holidays-...`

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_calendars.py`:

```python
import pytest

from grimoire.store import calendars
from grimoire.store.calendars import CalendarError, get_provider, normalize, fixed_of, minutes_of, split_native


def greg(region="US", custom=None, anchor=None):
    return {"provider": "gregorian", "region": region,
            "custom_holidays": custom or [], "anchor": anchor}


def test_gregorian_parse_format_roundtrip():
    p = get_provider(greg())
    fixed = p.parse("2026-06-29")
    assert p.format(fixed) == "2026-06-29"


def test_gregorian_weekday_known_date():
    p = get_provider(greg())
    d = p.describe(p.parse("2026-06-29"))
    assert d["weekday_name"] == "Monday"
    assert d["friendly"] == "29 June 2026"


def test_gregorian_leap_validity():
    p = get_provider(greg())
    assert p.format(p.parse("2000-02-29")) == "2000-02-29"   # 2000 is a leap year
    with pytest.raises(CalendarError):
        p.parse("1900-02-29")                                # 1900 is not


def test_unknown_provider_raises():
    with pytest.raises(CalendarError):
        get_provider({"provider": "nope", "region": "US", "custom_holidays": [], "anchor": None})


def test_split_native_and_minutes():
    assert split_native("2026-06-29T14:30") == ("2026-06-29", "14:30")
    assert split_native("2026-06-29") == ("2026-06-29", None)
    assert minutes_of("2026-06-29T14:30") == 14 * 60 + 30
    assert minutes_of("2026-06-29") is None


def test_normalize_preserves_time_and_canonicalizes_date():
    p = get_provider(greg())
    assert normalize(p, "2026-06-29T14:30") == "2026-06-29T14:30"
    with pytest.raises(CalendarError):
        normalize(p, "2026-13-01")          # bad month
    with pytest.raises(CalendarError):
        normalize(p, "2026-06-29T25:00")    # bad time


def test_fixed_of_ignores_time():
    p = get_provider(greg())
    assert fixed_of(p, "2026-06-29T14:30") == fixed_of(p, "2026-06-29")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_calendars.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.calendars'`

- [ ] **Step 4: Implement `base.py`**

Create `backend/src/grimoire/store/calendars/base.py`:

```python
"""Calendar engine: a provider registry over a fixed-day integer axis.

Every date reduces to a fixed day (proleptic Gregorian ordinal, a Rata Die day
count) so dates from any calendar are orderable and arithmetic is exact. A
provider knows how to convert its own notation <-> fixed day, name the weekday,
and (in gregorian.py) list its holidays. Only `gregorian` ships today; future
calendars register here and need no changes elsewhere.

Time-of-day is handled by the agnostic helpers below (split/minutes/normalize),
not the providers — providers are purely calendrical (date-level).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class CalendarError(Exception):
    pass


class CalendarProvider(ABC):
    @abstractmethod
    def parse(self, native: str) -> int:
        """Native date string (no time component) -> fixed day. Raise CalendarError on bad input."""

    @abstractmethod
    def format(self, fixed: int) -> str:
        """Fixed day -> canonical native date string (round-trips parse)."""

    @abstractmethod
    def describe(self, fixed: int) -> dict:
        """{year, month, month_name, day, weekday_name, weekday_index, friendly}."""

    # Age helpers default to fixed-day arithmetic via describe(); override if needed.
    def age(self, birth_fixed: int, asof_fixed: int) -> int:
        b, a = self.describe(birth_fixed), self.describe(asof_fixed)
        years = a["year"] - b["year"]
        if (a["month"], a["day"]) < (b["month"], b["day"]):
            years -= 1
        return years

    def is_anniversary(self, birth_fixed: int, asof_fixed: int) -> bool:
        b, a = self.describe(birth_fixed), self.describe(asof_fixed)
        return (a["month"], a["day"]) == (b["month"], b["day"])


REGISTRY: dict[str, type[CalendarProvider]] = {}


def register(provider_id: str, cls: type[CalendarProvider]) -> None:
    REGISTRY[provider_id] = cls


def get_provider(config: dict) -> CalendarProvider:
    cls = REGISTRY.get(config.get("provider", "gregorian"))
    if cls is None:
        raise CalendarError(f"unknown calendar provider: {config.get('provider')!r}")
    return cls(config)


# ---- time-of-day-aware, calendar-agnostic helpers ----

def split_native(native: str) -> tuple[str, str | None]:
    date_str, sep, time_str = native.partition("T")
    return date_str, (time_str if sep else None)


def minutes_of(native: str) -> int | None:
    _, time_str = split_native(native)
    if not time_str:
        return None
    try:
        hh, mm = time_str.split(":")
        h, m = int(hh), int(mm)
    except ValueError as e:
        raise CalendarError(f"bad time-of-day: {time_str!r}") from e
    if not (0 <= h < 24 and 0 <= m < 60):
        raise CalendarError(f"time-of-day out of range: {time_str!r}")
    return h * 60 + m


def fixed_of(provider: CalendarProvider, native: str) -> int:
    date_str, _ = split_native(native)
    return provider.parse(date_str)


def normalize(provider: CalendarProvider, native: str) -> str:
    """Canonicalize: validate date + optional time, return canonical date(+Thh:mm)."""
    date_str, time_str = split_native(native)
    canonical = provider.format(provider.parse(date_str))
    if time_str is not None:
        minutes_of(native)  # validates range, raises CalendarError
        return f"{canonical}T{time_str}"
    return canonical


def friendly(provider: CalendarProvider, native: str) -> str:
    return provider.describe(fixed_of(provider, native))["friendly"]
```

- [ ] **Step 5: Implement `gregorian.py`**

Create `backend/src/grimoire/store/calendars/gregorian.py`:

```python
"""The Gregorian provider: Python `datetime`-backed, so leap years and weekdays
are exact. Holidays come in Task 2."""

from __future__ import annotations

import calendar as _cal
from datetime import date

from .base import CalendarError, CalendarProvider, register


class GregorianProvider(CalendarProvider):
    def __init__(self, config: dict):
        self.region = config.get("region", "US")
        self.custom_holidays = config.get("custom_holidays", []) or []
        self.anchor = config.get("anchor")  # canonical calendar — anchor is ignored

    def parse(self, native: str) -> int:
        try:
            y, m, d = (int(x) for x in native.split("-"))
            return date(y, m, d).toordinal()
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad gregorian date: {native!r}") from e

    def format(self, fixed: int) -> str:
        return date.fromordinal(fixed).isoformat()

    def describe(self, fixed: int) -> dict:
        d = date.fromordinal(fixed)
        return {
            "year": d.year, "month": d.month, "month_name": _cal.month_name[d.month],
            "day": d.day, "weekday_name": _cal.day_name[d.weekday()],
            "weekday_index": d.weekday(),
            "friendly": f"{d.day} {_cal.month_name[d.month]} {d.year}",
        }


register("gregorian", GregorianProvider)
```

- [ ] **Step 6: Implement the package `__init__.py`**

Create `backend/src/grimoire/store/calendars/__init__.py`:

```python
from . import gregorian  # noqa: F401  (registers the provider)
from .base import (  # noqa: F401
    CalendarError, CalendarProvider, get_provider, register,
    split_native, minutes_of, fixed_of, normalize, friendly,
)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_calendars.py -q`
Expected: PASS (7 passed)

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/src/grimoire/store/calendars backend/tests/test_calendars.py
git commit -m "feat: calendar provider engine + gregorian date core"
```

---

### Task 2: Holidays + `today_facts` (with merged secondary calendar)

**Files:**
- Modify: `backend/src/grimoire/store/calendars/gregorian.py`
- Modify: `backend/src/grimoire/store/calendars/base.py`
- Modify: `backend/src/grimoire/store/calendars/__init__.py`
- Test: `backend/tests/test_calendars.py`

**Interfaces:**
- Produces:
  - `CalendarProvider.holidays(self, start_fixed: int, end_fixed: int) -> list[dict]` — list of `{name, fixed}` for days in `[start_fixed, end_fixed]` (library region + custom rules).
  - `today_facts(cfg: dict, native: str) -> dict` in `base.py`, re-exported — `cfg` is the full `{primary, secondary|None}` config. Returns `{friendly, weekday, secondary_friendly | None, holidays_today: [str], upcoming: {name, in_days} | None}`. Merges holidays across primary + secondary.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_calendars.py`:

```python
from grimoire.store.calendars import today_facts


def test_gregorian_library_holiday():
    p = get_provider(greg(region="US"))
    start = p.parse("2026-11-01")
    end = p.parse("2026-11-30")
    names = {h["name"] for h in p.holidays(start, end)}
    assert any("Thanksgiving" in n for n in names)


def test_custom_fixed_and_nth_weekday_holidays():
    custom = [
        {"name": "Founding Day", "month": 4, "day": 12},
        {"name": "Harvest Moon", "month": 9, "nth": 3, "weekday": 6},  # 3rd Sunday of Sept
    ]
    p = get_provider(greg(region="", custom=custom))
    founding = p.holidays(p.parse("2026-04-01"), p.parse("2026-04-30"))
    assert [h["name"] for h in founding] == ["Founding Day"]
    assert p.format(founding[0]["fixed"]) == "2026-04-12"
    harvest = p.holidays(p.parse("2026-09-01"), p.parse("2026-09-30"))
    assert [h["name"] for h in harvest] == ["Harvest Moon"]
    assert p.format(harvest[0]["fixed"]) == "2026-09-20"  # 3rd Sunday of Sept 2026


def test_today_facts_dateline_and_holiday():
    cfg = {"primary": greg(region="US"), "secondary": None}
    facts = today_facts(cfg, "2026-12-25")
    assert facts["friendly"] == "25 December 2026"
    assert facts["weekday"] == "Friday"
    assert facts["secondary_friendly"] is None
    assert "Christmas Day" in facts["holidays_today"]


def test_today_facts_merges_secondary_holidays():
    # Boxing Day (Dec 26) is a GB holiday, not US — proves the secondary merge.
    cfg = {"primary": greg(region="US"), "secondary": greg(region="GB")}
    facts = today_facts(cfg, "2026-12-26")
    assert facts["secondary_friendly"] == "26 December 2026"
    assert any("Boxing Day" in n for n in facts["holidays_today"])


def test_today_facts_upcoming_within_30_days():
    cfg = {"primary": greg(region="US"), "secondary": None}
    facts = today_facts(cfg, "2026-12-20")
    assert facts["upcoming"] == {"name": "Christmas Day", "in_days": 5}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_calendars.py -q -k "holiday or today_facts"`
Expected: FAIL — `AttributeError: 'GregorianProvider' object has no attribute 'holidays'`

- [ ] **Step 3: Add `holidays` to the Gregorian provider**

In `backend/src/grimoire/store/calendars/gregorian.py`, add the import and method.

At the top, add:

```python
import holidays as _holidays
```

Add this method to `GregorianProvider` (after `describe`):

```python
    def holidays(self, start_fixed: int, end_fixed: int) -> list[dict]:
        out: list[dict] = []
        start, end = date.fromordinal(start_fixed), date.fromordinal(end_fixed)
        years = list(range(start.year, end.year + 1))
        if self.region:
            try:
                lib = _holidays.country_holidays(self.region, years=years)
            except NotImplementedError:
                lib = {}
            for d, name in lib.items():
                f = d.toordinal()
                if start_fixed <= f <= end_fixed:
                    out.append({"name": name, "fixed": f})
        for rule in self.custom_holidays:
            for y in years:
                d = _custom_date(rule, y)
                if d is None:
                    continue
                f = d.toordinal()
                if start_fixed <= f <= end_fixed:
                    out.append({"name": rule.get("name", ""), "fixed": f})
        out.sort(key=lambda h: h["fixed"])
        return out
```

Add this module-level helper at the bottom of `gregorian.py` (above `register(...)`):

```python
def _custom_date(rule: dict, year: int):
    """Resolve a custom-holiday rule to a date in `year`: fixed {month, day} or
    nth-weekday {month, nth, weekday} (weekday 0=Mon..6=Sun). None if malformed."""
    try:
        month = int(rule["month"])
        if "day" in rule:
            return date(year, month, int(rule["day"]))
        nth, weekday = int(rule["nth"]), int(rule["weekday"])
    except (KeyError, ValueError, TypeError):
        return None
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    day = 1 + offset + (nth - 1) * 7
    try:
        return date(year, month, day)
    except ValueError:
        return None
```

- [ ] **Step 4: Add `today_facts` to `base.py`**

Append to `backend/src/grimoire/store/calendars/base.py`:

```python
UPCOMING_WINDOW_DAYS = 30


def _configured(cfg: dict) -> list[CalendarProvider]:
    out = [get_provider(cfg["primary"])]
    if cfg.get("secondary"):
        out.append(get_provider(cfg["secondary"]))
    return out


def today_facts(cfg: dict, native: str) -> dict:
    """Computed date facts for a scene's current moment, merged across all
    configured calendars. `cfg` is {primary, secondary|None}."""
    providers = _configured(cfg)
    primary = providers[0]
    fixed = fixed_of(primary, native)
    primary_desc = primary.describe(fixed)

    secondary_friendly = None
    if len(providers) > 1:
        secondary_friendly = providers[1].describe(fixed)["friendly"]

    holidays_today: list[str] = []
    for p in providers:
        for h in p.holidays(fixed, fixed):
            if h["name"] not in holidays_today:
                holidays_today.append(h["name"])

    upcoming = None
    soonest: dict | None = None
    for p in providers:
        for h in p.holidays(fixed + 1, fixed + UPCOMING_WINDOW_DAYS):
            if soonest is None or h["fixed"] < soonest["fixed"]:
                soonest = h
    if soonest is not None:
        upcoming = {"name": soonest["name"], "in_days": soonest["fixed"] - fixed}

    return {
        "friendly": primary_desc["friendly"],
        "weekday": primary_desc["weekday_name"],
        "secondary_friendly": secondary_friendly,
        "holidays_today": holidays_today,
        "upcoming": upcoming,
    }
```

Also add `holidays` to the ABC in `base.py` so the interface is explicit — add this abstract method to `CalendarProvider` (after `describe`):

```python
    @abstractmethod
    def holidays(self, start_fixed: int, end_fixed: int) -> list[dict]:
        """Observances landing in [start_fixed, end_fixed], each {name, fixed}."""
```

- [ ] **Step 5: Re-export `today_facts`**

In `backend/src/grimoire/store/calendars/__init__.py`, add `today_facts` to the import from `.base`:

```python
from .base import (  # noqa: F401
    CalendarError, CalendarProvider, get_provider, register,
    split_native, minutes_of, fixed_of, normalize, friendly, today_facts,
)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_calendars.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/calendars backend/tests/test_calendars.py
git commit -m "feat: calendar holidays + today_facts with merged secondary calendar"
```

---

### Task 3: Age & anniversary helpers

**Files:**
- Modify: `backend/src/grimoire/store/calendars/base.py`
- Modify: `backend/src/grimoire/store/calendars/__init__.py`
- Test: `backend/tests/test_calendars.py`

**Interfaces:**
- Produces (re-exported from package): `age(provider, birth_native, asof_native) -> int`, `is_anniversary(provider, birth_native, asof_native) -> bool` — native-string wrappers over the provider's fixed-day `age`/`is_anniversary`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_calendars.py`:

```python
from grimoire.store.calendars import age, is_anniversary


def test_age_and_anniversary():
    p = get_provider(greg())
    # born 1990-06-29; as of 2026-06-28 still 35, on 2026-06-29 turns 36
    assert age(p, "1990-06-29", "2026-06-28") == 35
    assert age(p, "1990-06-29", "2026-06-29") == 36
    assert is_anniversary(p, "1990-06-29", "2026-06-29") is True
    assert is_anniversary(p, "1990-06-29", "2026-06-30") is False
    # time-of-day on either side does not change the result
    assert age(p, "1990-06-29", "2026-06-29T08:00") == 36
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_calendars.py -q -k "age_and_anniversary"`
Expected: FAIL — `ImportError: cannot import name 'age'`

- [ ] **Step 3: Add native-string wrappers to `base.py`**

Append to `backend/src/grimoire/store/calendars/base.py`:

```python
def age(provider: CalendarProvider, birth_native: str, asof_native: str) -> int:
    return provider.age(fixed_of(provider, birth_native), fixed_of(provider, asof_native))


def is_anniversary(provider: CalendarProvider, birth_native: str, asof_native: str) -> bool:
    return provider.is_anniversary(fixed_of(provider, birth_native), fixed_of(provider, asof_native))
```

- [ ] **Step 4: Re-export**

In `backend/src/grimoire/store/calendars/__init__.py`, add `age, is_anniversary` to the `.base` import list.

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_calendars.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/calendars backend/tests/test_calendars.py
git commit -m "feat: calendar age + anniversary helpers"
```

---

### Task 4: Calendar config storage

**Files:**
- Create: `backend/src/grimoire/store/calendars/config.py`
- Modify: `backend/src/grimoire/store/calendars/__init__.py`
- Test: `backend/tests/test_calendars.py`

**Interfaces:**
- Produces (re-exported from package):
  - `DEFAULT_CALENDAR -> dict` factory `default_calendar() -> {"primary": {...}, "secondary": None}`.
  - `read_calendar(root: Path) -> dict` — reads `<root>/calendar.json`, returns defaults when absent; always shaped `{primary, secondary}` with each block carrying `provider, region, custom_holidays, anchor`.
  - `write_calendar(root: Path, cfg: dict) -> None`.
  - `copy_calendar(wroot: Path, croot: Path) -> None` — copies the world's `calendar.json` into the campaign if present (else writes defaults).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_calendars.py`:

```python
from grimoire.store.calendars import default_calendar, read_calendar, write_calendar, copy_calendar


def test_default_calendar_when_absent(tmp_path):
    cfg = read_calendar(tmp_path)
    assert cfg["primary"]["provider"] == "gregorian"
    assert cfg["primary"]["region"] == "US"
    assert cfg["primary"]["custom_holidays"] == []
    assert cfg["primary"]["anchor"] is None
    assert cfg["secondary"] is None


def test_write_then_read_roundtrip(tmp_path):
    cfg = default_calendar()
    cfg["primary"]["region"] = "GB"
    cfg["secondary"] = {"provider": "gregorian", "region": "IL", "custom_holidays": [],
                        "anchor": {"native": "2026-06-29", "gregorian": "2026-06-29"}}
    write_calendar(tmp_path, cfg)
    got = read_calendar(tmp_path)
    assert got["primary"]["region"] == "GB"
    assert got["secondary"]["region"] == "IL"
    assert got["secondary"]["anchor"]["gregorian"] == "2026-06-29"


def test_read_fills_missing_keys(tmp_path):
    # a hand-written partial file still normalizes to the full shape
    (tmp_path / "calendar.json").write_text('{"primary": {"provider": "gregorian"}}', encoding="utf-8")
    cfg = read_calendar(tmp_path)
    assert cfg["primary"]["region"] == "US"
    assert cfg["primary"]["custom_holidays"] == []
    assert cfg["secondary"] is None


def test_copy_calendar_copies_world_file(tmp_path):
    wroot, croot = tmp_path / "w", tmp_path / "c"
    wroot.mkdir(); croot.mkdir()
    cfg = default_calendar(); cfg["primary"]["region"] = "FR"
    write_calendar(wroot, cfg)
    copy_calendar(wroot, croot)
    assert read_calendar(croot)["primary"]["region"] == "FR"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_calendars.py -q -k "calendar and (default or roundtrip or missing or copy)"`
Expected: FAIL — `ImportError: cannot import name 'read_calendar'`

- [ ] **Step 3: Implement `config.py`**

Create `backend/src/grimoire/store/calendars/config.py`:

```python
"""Calendar config IO: <root>/calendar.json = {primary, secondary|None}, each
calendar block {provider, region, custom_holidays, anchor}. World-scoped, copied
into a campaign on create."""

from __future__ import annotations

import json
from pathlib import Path


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
    raw = json.loads(p.read_text(encoding="utf-8"))
    primary = _normalize_block(raw.get("primary")) or _blank()
    return {"primary": primary, "secondary": _normalize_block(raw.get("secondary"))}


def write_calendar(root: Path, cfg: dict) -> None:
    out = {"primary": _normalize_block(cfg.get("primary")) or _blank(),
           "secondary": _normalize_block(cfg.get("secondary"))}
    _path(root).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


def copy_calendar(wroot: Path, croot: Path) -> None:
    write_calendar(croot, read_calendar(wroot))
```

- [ ] **Step 4: Re-export config IO from the package**

In `backend/src/grimoire/store/calendars/__init__.py`, add:

```python
from .config import (  # noqa: F401
    default_calendar, read_calendar, write_calendar, copy_calendar,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_calendars.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/calendars backend/tests/test_calendars.py
git commit -m "feat: calendar config storage (calendar.json)"
```

---

### Task 5: Copy calendar on campaign create + region on create route

**Files:**
- Modify: `backend/src/grimoire/store/campaigns.py:66-89`
- Modify: `backend/src/grimoire/routes.py:770-777` (the `POST /api/campaigns` handler + `CampaignCreate` model)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `calendars.copy_calendar`, `calendars.read_calendar`, `calendars.write_calendar`.
- Produces: `create_campaign(name, world_id, region: str | None = None) -> str` copies the world calendar and, when `region` is given, overrides the primary region. `CampaignCreate` Pydantic model gains `region: str | None = None`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_routes.py` (use the existing `client` fixture + `_world` helper; the fixture already isolates `GRIMOIRE_HOME`, so don't re-set it):

```python
def test_campaign_create_writes_calendar_with_region(client):
    from grimoire.store import campaigns, calendars
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid, "region": "GB"}).json()["id"]
    cfg = calendars.read_calendar(campaigns.campaign_root(cid))
    assert cfg["primary"]["region"] == "GB"


def test_campaign_create_defaults_region_us(client):
    from grimoire.store import campaigns, calendars
    _wid, cid = _campaign(client)
    assert calendars.read_calendar(campaigns.campaign_root(cid))["primary"]["region"] == "US"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "calendar_with_region or defaults_region"`
Expected: FAIL — region not written / `CampaignCreate` rejects `region`

- [ ] **Step 3: Update `create_campaign`**

In `backend/src/grimoire/store/campaigns.py`, add the import at the top with the other `from . import ...`:

```python
from . import calendars, entities, worlds
```

Change the signature and append the calendar copy at the end of `create_campaign` (after `write_manifest(cid, manifest)`, before `return cid`):

```python
def create_campaign(name: str, world_id: str, region: str | None = None) -> str:
```

```python
    write_manifest(cid, manifest)
    calendars.copy_calendar(wroot, root)
    if region is not None:
        cfg = calendars.read_calendar(root)
        cfg["primary"]["region"] = region
        calendars.write_calendar(root, cfg)
    return cid
```

- [ ] **Step 4: Update the route + model**

Find `CampaignCreate` in `backend/src/grimoire/routes.py` (`grep -n "class CampaignCreate" backend/src/grimoire/routes.py`) and add the field:

```python
class CampaignCreate(BaseModel):
    name: str
    world: str
    region: str | None = None
```

Update the handler (around line 774) to pass it:

```python
        return {"id": store.campaigns.create_campaign(body.name, body.world, body.region)}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "calendar_with_region or defaults_region"`
Expected: PASS

- [ ] **Step 6: Run the full backend suite (no regressions)**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/campaigns.py backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: copy calendar on campaign create + region on create route"
```

---

## Phase 2 — Scene dates

### Task 6: `get_time_history` + `set_datetime`

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py:8` (imports) and append two functions
- Test: `backend/tests/test_scene_store.py`

**Interfaces:**
- Consumes: `calendars.read_calendar`, `calendars.get_provider`, `calendars.normalize`, `calendars.friendly`, `calendars.CalendarError`.
- Produces:
  - `get_time_history(cid, sid) -> list[str]` (missing scene ⇒ `[]`).
  - `set_datetime(cid, sid, native) -> dict` → `{"advanced": bool, "friendly": str}`; raises `SceneNotFound` / `calendars.CalendarError`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_scene_store.py`:

```python
def test_set_datetime_first_silent_then_advance(monkeypatch, tmp_path):
    from grimoire.store import calendars
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    # first set: silent, no transcript line
    assert scenes.set_datetime(cid, sid, "2026-06-29") == {"advanced": False, "friendly": "29 June 2026"}
    assert scenes.get_time_history(cid, sid) == ["2026-06-29"]
    assert scenes.read_scene(cid, sid)["messages"] == []
    # change: appends an italic transition line
    res = scenes.set_datetime(cid, sid, "2026-07-04T09:00")
    assert res == {"advanced": True, "friendly": "4 July 2026"}
    assert scenes.get_time_history(cid, sid) == ["2026-06-29", "2026-07-04T09:00"]
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "assistant", "content": "*Time passes. It is now 4 July 2026.*"}]
    # re-set the same current: no-op
    assert scenes.set_datetime(cid, sid, "2026-07-04T09:00") == {"advanced": False, "friendly": "4 July 2026"}
    assert len(scenes.read_scene(cid, sid)["messages"]) == 1


def test_set_datetime_bad_input_raises(monkeypatch, tmp_path):
    from grimoire.store import calendars
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    with pytest.raises(calendars.CalendarError):
        scenes.set_datetime(cid, sid, "2026-13-40")


def test_get_time_history_missing_scene_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert scenes.get_time_history(cid, "nope") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q -k datetime`
Expected: FAIL — `AttributeError: module 'grimoire.store.scenes' has no attribute 'set_datetime'`

- [ ] **Step 3: Implement in `scenes.py`**

In `backend/src/grimoire/store/scenes.py`, extend the import line:

```python
from . import appearances, calendars, campaigns, entities
```

Append at the end of the file:

```python
def get_time_history(cid: str, sid: str) -> list[str]:
    """Ordered scene moments (native datetime strings); last is current. Missing ⇒ []."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return [x for x in meta.get("time_history", "").split(",") if x]


def set_datetime(cid: str, sid: str, native: str) -> dict:
    """Set the scene's current moment (in the primary calendar). First set is silent;
    a change appends an assistant transition line. Returns {"advanced", "friendly"}."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    cfg = calendars.read_calendar(campaigns.campaign_root(cid))
    provider = calendars.get_provider(cfg["primary"])
    canonical = calendars.normalize(provider, native)  # raises calendars.CalendarError
    friendly = calendars.friendly(provider, canonical)
    history = get_time_history(cid, sid)
    if history and history[-1] == canonical:
        return {"advanced": False, "friendly": friendly}
    advanced = bool(history)
    if advanced:
        append_message(cid, sid, "assistant", f"*Time passes. It is now {friendly}.*")
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    history.append(canonical)
    meta["time_history"] = ",".join(history)
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return {"advanced": advanced, "friendly": friendly}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q -k datetime`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/tests/test_scene_store.py
git commit -m "feat: scene set_datetime + get_time_history"
```

---

### Task 7: Datetime routes

**Files:**
- Modify: `backend/src/grimoire/routes.py` (add a Pydantic model near the others ~line 125; add two routes after the location routes ~line 1050)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.scenes.get_time_history`, `store.scenes.set_datetime`, `store.calendars.read_calendar`, `store.calendars.today_facts`, `store.calendars.CalendarError`.
- Produces:
  - `GET /api/campaigns/{cid}/scenes/{sid}/datetime` → `{"current": {...} | null, "history": [native, …]}`. `current` includes `today_facts(...)` plus the raw `native`. (`cast` facts are added in Task 13.)
  - `PUT /api/campaigns/{cid}/scenes/{sid}/datetime` body `{datetime: native}` → `{"ok": True, "advanced": bool, "friendly": str}`; 404 missing scene; **400 on `CalendarError`**.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_routes.py` (use the existing `client`/`_campaign` helpers; scene-create shape matches the existing tests — `client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"})`):

```python
def test_datetime_get_put_roundtrip(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]

    # no date yet
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()["current"] is None

    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime", json={"datetime": "2026-12-25"})
    assert r.json() == {"ok": True, "advanced": False, "friendly": "25 December 2026"}

    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()
    assert got["current"]["native"] == "2026-12-25"
    assert got["current"]["weekday"] == "Friday"
    assert "Christmas Day" in got["current"]["holidays_today"]
    assert got["history"] == ["2026-12-25"]


def test_datetime_put_bad_date_is_400(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime", json={"datetime": "2026-13-40"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k datetime`
Expected: FAIL — 404/405 (routes not defined)

- [ ] **Step 3: Add the Pydantic model**

In `backend/src/grimoire/routes.py`, near `class SceneLocation(BaseModel)` (~line 125), add:

```python
class SceneDatetime(BaseModel):
    datetime: str
```

- [ ] **Step 4: Add the routes**

In `backend/src/grimoire/routes.py`, after the location routes (after `put_scene_location`, ~line 1050), add:

```python
@router.get("/campaigns/{cid}/scenes/{sid}/datetime")
def get_scene_datetime(cid: str, sid: str):
    _require_scene(cid, sid)
    history = store.scenes.get_time_history(cid, sid)
    current = None
    if history:
        cfg = store.calendars.read_calendar(store.campaigns.campaign_root(cid))
        native = history[-1]
        current = {"native": native, **store.calendars.today_facts(cfg, native)}
    return {"current": current, "history": history}


@router.put("/campaigns/{cid}/scenes/{sid}/datetime")
def put_scene_datetime(cid: str, sid: str, body: SceneDatetime):
    _require_scene(cid, sid)
    try:
        result = store.scenes.set_datetime(cid, sid, body.datetime)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}
```

- [ ] **Step 5: Ensure `calendars` is reachable as `store.calendars`**

Confirm `store/__init__.py` exposes the submodule (`grep -n "calendars\|import" backend/src/grimoire/store/__init__.py`). If the package imports submodules explicitly, add `from . import calendars` alongside the others; if it relies on attribute access, `import grimoire.store.calendars` is already triggered by `scenes`/`campaigns` importing it. Verify by running the test in the next step.

- [ ] **Step 6: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k datetime`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: scene datetime GET/PUT routes"
```

---

### Task 8: `# Today` context block

**Files:**
- Modify: `backend/src/grimoire/store/context.py:163-235` (the `_assemble` function, near the `Current setting` block)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `scenes.get_time_history`, `calendars.read_calendar`, `calendars.today_facts`.
- Produces: a `# Today` system section (label `"Today"`) prepended to the world context when the scene has a current datetime. Lines: date+weekday, optional secondary dateline, holidays-today, 30-day upcoming. (Present-cast ages/birthdays are added in Task 13.)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_context.py` (mirror the existing helpers in that file for building a campaign + scene; if it lacks one, use `campaigns`/`scenes` directly as in `test_scene_store.py`):

```python
def test_today_block_present_when_dated(monkeypatch, tmp_path):
    from grimoire.store import campaigns, scenes, worlds, context
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)  # default region US
    sid = scenes.create_scene(cid, "S")
    scenes.set_datetime(cid, sid, "2026-12-25")
    labels = [s["label"] for s in context.context_sections(cid, sid)]
    assert "Today" in labels
    today = next(s["text"] for s in context.context_sections(cid, sid) if s["label"] == "Today")
    assert "It is 25 December 2026 (Friday)." in today
    assert "Christmas Day" in today


def test_no_today_block_when_undated(monkeypatch, tmp_path):
    from grimoire.store import campaigns, scenes, worlds, context
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    assert "Today" not in [s["label"] for s in context.context_sections(cid, sid)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k today`
Expected: FAIL — no `Today` label

- [ ] **Step 3: Add a `# Today` builder + wire it into `_assemble`**

In `backend/src/grimoire/store/context.py`, add this helper above `_assemble`:

```python
def _today_block(cid: str, sid: str, croot) -> str:
    history = scenes.get_time_history(cid, sid)
    if not history:
        return ""
    cfg = calendars.read_calendar(croot)
    try:
        facts = calendars.today_facts(cfg, history[-1])
    except calendars.CalendarError:
        return ""  # garbled date — omit, don't crash
    lines = [f"It is {facts['friendly']} ({facts['weekday']})."]
    if facts["secondary_friendly"]:
        lines[0] = f"It is {facts['friendly']} ({facts['weekday']}); {facts['secondary_friendly']}."
    if facts["holidays_today"]:
        lines.append("Holidays today: " + ", ".join(facts["holidays_today"]) + ".")
    if facts["upcoming"]:
        u = facts["upcoming"]
        lines.append(f"Upcoming: {u['name']} in {u['in_days']} days.")
    return "# Today\n" + "\n".join(lines)
```

Add `calendars` to the `context.py` import line (line 11):

```python
from . import appearances, briefs, calendars, campaigns, characters, config, entities, pcs, scenes, worlds
```

Then, in `_assemble`, immediately **before** the `# Current setting` block (the `history_ids = scenes.get_location_history(...)` line ~223), add:

```python
    add("Today", _today_block(cid, sid, croot))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k today`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat: inject computed # Today block into scene context"
```

---

### Task 9: Frontend datetime client

**Files:**
- Modify: `frontend/src/api/client.ts` (types near line 115; methods near the location methods ~line 299)
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Produces:
  - Types: `SceneDatetimeFacts = { native: string; friendly: string; weekday: string; secondary_friendly: string | null; holidays_today: string[]; upcoming: { name: string; in_days: number } | null; cast: SceneDatetimeCast[] }`; `SceneDatetimeCast = { kind: string; id: string; name: string; age: number | null; birthday_today: boolean }`; `SceneDatetime = { current: SceneDatetimeFacts | null; history: string[] }`.
  - `api.getSceneDatetime(cid, sid) -> Promise<SceneDatetime>`
  - `api.setSceneDatetime(cid, sid, datetime) -> Promise<{ ok: boolean; advanced: boolean; friendly: string }>`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/api/client.test.ts` (match the existing mock-fetch style in that file):

```python
# (TypeScript — shown here verbatim for the .test.ts file)
```

```ts
test("setSceneDatetime PUTs the datetime", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ ok: true, advanced: true, friendly: "4 July 2026" }),
  });
  vi.stubGlobal("fetch", fetchMock);
  await api.setSceneDatetime("run", "s1", "2026-07-04");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/datetime",
    expect.objectContaining({ method: "PUT" }),
  );
});
```

(If `client.test.ts` uses a shared `request`-mock helper rather than `fetch`, follow that file's existing convention instead — the assertion is that a PUT hits `/api/campaigns/run/scenes/s1/datetime`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npx --prefix frontend vitest run src/api/client.test.ts`
Expected: FAIL — `api.setSceneDatetime is not a function`

- [ ] **Step 3: Add types + methods to `client.ts`**

Near the `SceneLocation` types (~line 116) add:

```ts
export type SceneDatetimeCast = { kind: string; id: string; name: string; age: number | null; birthday_today: boolean };
export type SceneDatetimeFacts = {
  native: string; friendly: string; weekday: string; secondary_friendly: string | null;
  holidays_today: string[]; upcoming: { name: string; in_days: number } | null; cast: SceneDatetimeCast[];
};
export type SceneDatetime = { current: SceneDatetimeFacts | null; history: string[] };
```

Near the `getSceneLocation`/`setSceneLocation` methods (~line 299) add:

```ts
  getSceneDatetime: (cid: string, sid: string) =>
    request<SceneDatetime>("GET", `/api/campaigns/${cid}/scenes/${sid}/datetime`),
  setSceneDatetime: (cid: string, sid: string, datetime: string) =>
    request<{ ok: boolean; advanced: boolean; friendly: string }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/datetime`, { datetime }),
```

- [ ] **Step 4: Run tests + typecheck to verify they pass**

Run: `npx --prefix frontend vitest run src/api/client.test.ts`
Expected: PASS
Run: `cd frontend && npx tsc -b`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "feat: scene datetime API client"
```

---

### Task 10: CastPanel "When" section

**Files:**
- Modify: `frontend/src/components/CastPanel.tsx` (add state + a "When" section beside "Setting")
- Test: `frontend/src/components/CastPanel.test.tsx`

**Interfaces:**
- Consumes: `api.getSceneDatetime`, `api.setSceneDatetime`, the `onSeeded` prop (already passed to `CastPanel`).
- Produces: a `<div>` "When" section rendering the current friendly datetime (or "No date"), holiday/birthday hints, a `type="date"` input + **Set date**/**Advance to** button that calls `setSceneDatetime` then `onSeeded()` and reloads.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/CastPanel.test.tsx` (follow the file's existing render/mocks setup; mock `api.getSceneDatetime`/`api.setSceneDatetime` alongside the existing mocks):

```ts
test("When section shows the current date and advances", async () => {
  (api.getSceneDatetime as any).mockResolvedValue({
    current: { native: "2026-12-25", friendly: "25 December 2026", weekday: "Friday",
               secondary_friendly: null, holidays_today: ["Christmas Day"], upcoming: null, cast: [] },
    history: ["2026-12-25"],
  });
  (api.setSceneDatetime as any).mockResolvedValue({ ok: true, advanced: true, friendly: "1 January 2027" });
  const onSeeded = vi.fn();
  render(<CastPanel cid="run" sid="s1" sceneEmpty={false} keySet={true} onSeeded={onSeeded} />);
  expect(await screen.findByText(/25 December 2026/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Scene date"), { target: { value: "2027-01-01" } });
  fireEvent.click(screen.getByRole("button", { name: /Advance to|Set date/ }));
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalledWith("run", "s1", "2027-01-01"));
  await waitFor(() => expect(onSeeded).toHaveBeenCalled());
});
```

Ensure the imports at the top of the test include `fireEvent`, `waitFor`, `screen` from `@testing-library/react` (match what the file already imports).

- [ ] **Step 2: Run test to verify it fails**

Run: `npx --prefix frontend vitest run src/components/CastPanel.test.tsx`
Expected: FAIL — no "Scene date" control

- [ ] **Step 3: Add datetime state + loader to `CastPanel`**

In `frontend/src/components/CastPanel.tsx`, add to the imported types: `type SceneDatetime` (extend the existing `import { api, ... } from "../api/client"`).

Add state near the other `useState` hooks:

```tsx
  const [when, setWhen] = useState<SceneDatetime | null>(null);
  const [dateInput, setDateInput] = useState("");
```

Add a reloader near `reloadSetting`:

```tsx
  const reloadWhen = useCallback(
    () => api.getSceneDatetime(cid, sid).then(setWhen).catch(() => setWhen(null)),
    [cid, sid]);
```

Call it in the existing mount `useEffect` (add `reloadWhen();` next to `reloadSetting();`, and add `reloadWhen` to that effect's dependency array).

Add a handler near `setLocation`:

```tsx
  async function applyDatetime() {
    if (!dateInput) return;
    setError(null);
    try {
      await api.setSceneDatetime(cid, sid, dateInput);
      setDateInput("");
      await reloadWhen();
      onSeeded(); // surface the transition line in the stream
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }
```

- [ ] **Step 4: Render the "When" section**

In `CastPanel.tsx`, immediately after the closing `</div>` of the "Setting" section, add:

```tsx
        <div>
          <div className="role">When</div>
          <div className="field-hint">
            {when?.current
              ? `${when.current.friendly} (${when.current.weekday})`
              : "No date"}
          </div>
          {when?.current?.holidays_today?.length ? (
            <div className="field-hint">Holidays: {when.current.holidays_today.join(", ")}</div>
          ) : null}
          <div className="picker">
            <input type="date" aria-label="Scene date" value={dateInput}
                   onChange={(e) => setDateInput(e.target.value)} />
            <button className="primary" onClick={applyDatetime} disabled={!dateInput}>
              {when?.current ? "Advance to" : "Set date"}
            </button>
          </div>
        </div>
```

- [ ] **Step 5: Run tests + typecheck**

Run: `npx --prefix frontend vitest run src/components/CastPanel.test.tsx`
Expected: PASS
Run: `cd frontend && npx tsc -b`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/CastPanel.tsx frontend/src/components/CastPanel.test.tsx
git commit -m "feat: CastPanel When section for scene dates"
```

---

### Task 11: Region picker in the campaign wizard

**Files:**
- Modify: `frontend/src/api/client.ts:166` (`createCampaign` gains optional region)
- Modify: `frontend/src/routes/CampaignWizard.tsx` (region `<select>` + pass it through)
- Test: `frontend/src/routes/CampaignWizard.test.tsx`, `frontend/src/api/client.test.ts`

**Interfaces:**
- Produces: `api.createCampaign(name, world, region?) -> Promise<{ id: string }>` (region sent only when provided).

- [ ] **Step 1: Write the failing client test**

In `frontend/src/api/client.test.ts`, update/extend the existing `createCampaign` test to assert region is included when passed:

```ts
test("createCampaign includes region when given", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: "run" }) });
  vi.stubGlobal("fetch", fetchMock);
  await api.createCampaign("Run One", "w1", "GB");
  const body = JSON.parse(fetchMock.mock.calls[0][1].body);
  expect(body).toMatchObject({ name: "Run One", world: "w1", region: "GB" });
});
```

(Match the file's existing fetch/request mock convention.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npx --prefix frontend vitest run src/api/client.test.ts`
Expected: FAIL — region not in body / arity mismatch

- [ ] **Step 3: Update `createCampaign`**

In `frontend/src/api/client.ts` (~line 166):

```ts
  createCampaign: (name: string, world: string, region?: string) =>
    request<{ id: string }>("POST", "/api/campaigns",
      region ? { name, world, region } : { name, world }),
```

- [ ] **Step 4: Write the failing wizard test**

In `frontend/src/routes/CampaignWizard.test.tsx`, extend the existing flow test to select a region and assert it reaches `createCampaign`. After the steps that set the name/world, before submission, add:

```ts
  fireEvent.change(screen.getByLabelText("Holidays region"), { target: { value: "GB" } });
```

And update the existing assertion to:

```ts
  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith("Run One", "w1", "GB"));
```

- [ ] **Step 5: Run wizard test to verify it fails**

Run: `npx --prefix frontend vitest run src/routes/CampaignWizard.test.tsx`
Expected: FAIL — no "Holidays region" control / arity mismatch

- [ ] **Step 6: Add the region picker to the wizard**

In `frontend/src/routes/CampaignWizard.tsx`, add region state near the other `useState`:

```tsx
  const [region, setRegion] = useState("US");
```

Render a select in the form (near the name/world inputs):

```tsx
        <label>
          Holidays region
          <select aria-label="Holidays region" value={region} onChange={(e) => setRegion(e.target.value)}>
            <option value="US">United States</option>
            <option value="GB">United Kingdom</option>
            <option value="CA">Canada</option>
            <option value="AU">Australia</option>
            <option value="">None</option>
          </select>
        </label>
```

Update the create call (~line 64):

```tsx
      const { id: cid } = await api.createCampaign(name.trim(), world, region || undefined);
```

- [ ] **Step 7: Run tests + typecheck**

Run: `npx --prefix frontend vitest run src/routes/CampaignWizard.test.tsx src/api/client.test.ts`
Expected: PASS
Run: `cd frontend && npx tsc -b`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts frontend/src/routes/CampaignWizard.tsx frontend/src/routes/CampaignWizard.test.tsx
git commit -m "feat: holidays-region picker in campaign wizard"
```

---

## Phase 3 — Birthdays & ages

### Task 12: Birthdate storage on cast

**Files:**
- Modify: `backend/src/grimoire/store/pcs.py:17` (`PERSONA_FIELDS`)
- Modify: `backend/src/grimoire/store/characters.py` (add `birthdate` to meta read + a `set_birthdate`)
- Test: `backend/tests/test_appearances_store.py` (or a small new `test_birthdate.py`)

**Interfaces:**
- Produces:
  - PC personas round-trip a `birthdate` field (empty string when unset).
  - `characters.read_character(...)["meta"]["birthdate"]` (empty when unset).
  - `characters.set_birthdate(root, cid, birthdate: str) -> None` (raises `CharacterNotFound`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_birthdate.py`:

```python
from grimoire.store import characters, pcs, worlds


def test_pc_persona_roundtrips_birthdate(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    root = worlds.world_root(wid)
    pid, vid = pcs.create_pc(root, "Elara", [], persona={
        "name": "Elara", "pronouns": "she/her", "summary": "scholar",
        "description": "A wanderer.", "birthdate": "1990-06-29"})
    assert pcs.read_persona(root, pid, vid)["birthdate"] == "1990-06-29"


def test_pc_persona_birthdate_defaults_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    root = worlds.world_root(wid)
    pid, vid = pcs.create_pc(root, "Mara", [], persona=pcs.blank_persona("Mara"))
    assert pcs.read_persona(root, pid, vid)["birthdate"] == ""


def test_character_meta_birthdate_set_and_read(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    root = worlds.world_root(wid)
    cid, _ = characters.create_character(root, "Seraphine", "default", characters.blank_card("Seraphine"))
    assert characters.read_character(root, cid)["meta"]["birthdate"] == ""
    characters.set_birthdate(root, cid, "1985-03-14")
    assert characters.read_character(root, cid)["meta"]["birthdate"] == "1985-03-14"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_birthdate.py -q`
Expected: FAIL — `birthdate` missing / `set_birthdate` undefined

- [ ] **Step 3: Add `birthdate` to PC persona fields**

In `backend/src/grimoire/store/pcs.py`, line 17:

```python
PERSONA_FIELDS = ("name", "pronouns", "summary", "birthdate")  # frontmatter scalars; description is the body
```

And update `blank_persona` (line 48-49) to include it:

```python
def blank_persona(name: str) -> dict:
    return {"name": name, "pronouns": "", "summary": "", "birthdate": "", "description": ""}
```

(`_dump_persona`/`_load_persona` already iterate `PERSONA_FIELDS`, so they pick it up automatically.)

- [ ] **Step 4: Add `birthdate` to character meta + a setter**

In `backend/src/grimoire/store/characters.py`, in `read_character` (~line 146), add `birthdate` to the returned meta:

```python
        "meta": {"id": cid, "name": meta.get("name", cid),
                 "default_version": meta.get("default_version", ""),
                 "birthdate": meta.get("birthdate", "")},
```

Add a setter after `set_default_version` (~line 112):

```python
def set_birthdate(root: Path, cid: str, birthdate: str) -> None:
    _require_char(root, cid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    meta["birthdate"] = birthdate
    _meta_path(root, cid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_birthdate.py -q`
Expected: PASS

- [ ] **Step 6: Run the full backend suite (persona-shape change touches other tests)**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (all). If a persona-equality assertion elsewhere now fails because of the new key, update that assertion to include `"birthdate": ""`.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/pcs.py backend/src/grimoire/store/characters.py backend/tests/test_birthdate.py
git commit -m "feat: birthdate storage on PCs and characters"
```

---

### Task 13: Birthdate routes + cast facts in context & datetime

**Files:**
- Modify: `backend/src/grimoire/routes.py` (extend the PC-version PUT + character-meta PUT; add cast facts to `get_scene_datetime`)
- Modify: `backend/src/grimoire/store/context.py` (`_today_block` gains present-cast ages/birthdays) and a shared `cast_datetime_facts` helper
- Test: `backend/tests/test_context.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Produces:
  - `context.cast_datetime_facts(cid, sid, native) -> list[dict]` — `[{kind, id, name, age, birthday_today}]` for in-scene actors that have a birthdate (others skipped). Used by both `_today_block` and the datetime route.
  - `get_scene_datetime` `current.cast` is populated.
  - A way to set birthdate via the existing editors' PUT routes.

- [ ] **Step 1: Write the failing test (context cast facts)**

Append to `backend/tests/test_context.py`:

```python
def test_today_block_includes_present_cast_age(monkeypatch, tmp_path):
    from grimoire.store import campaigns, scenes, worlds, characters, appearances, context
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", characters.blank_card("Seraphine"))
    characters.set_birthdate(wroot, "seraphine", "1990-12-25")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", "seraphine", "default", "npc")
    # birthdate lives on the campaign copy after appear; set it there too
    characters.set_birthdate(campaigns.campaign_root(cid), "seraphine", "1990-12-25")
    scenes.set_datetime(cid, sid, "2026-12-25")
    today = next(s["text"] for s in context.context_sections(cid, sid) if s["label"] == "Today")
    assert "Seraphine" in today and "36" in today and "birthday" in today.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k present_cast_age`
Expected: FAIL — no cast line in the Today block

- [ ] **Step 3: Add `cast_datetime_facts` + extend `_today_block`**

In `backend/src/grimoire/store/context.py`, add this helper above `_today_block`:

```python
def cast_datetime_facts(cid: str, sid: str, native: str) -> list[dict]:
    """Age / birthday-today for each in-scene actor that has a birthdate. Others skipped."""
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    provider = calendars.get_provider(cfg["primary"])
    out: list[dict] = []
    for a in appearances.scene_cast(cid, sid):
        vid = appearances.locked_version(cid, a["kind"], a["id"])
        birth = ""
        name = a["id"]
        try:
            if a["kind"] == "pcs":
                persona = pcs.read_persona(croot, a["id"], vid)
                birth, name = persona.get("birthdate", ""), persona.get("name", a["id"])
            else:
                meta = characters.read_character(croot, a["id"])["meta"]
                birth, name = meta.get("birthdate", ""), meta.get("name", a["id"])
        except (pcs.PCNotFound, pcs.PCVersionNotFound, characters.CharacterNotFound):
            continue
        if not birth:
            continue
        try:
            out.append({"kind": a["kind"], "id": a["id"], "name": name,
                        "age": calendars.age(provider, birth, native),
                        "birthday_today": calendars.is_anniversary(provider, birth, native)})
        except calendars.CalendarError:
            continue
    return out
```

In `_today_block`, after the `upcoming` line, add the present-cast line:

```python
    facts_cast = cast_datetime_facts(cid, sid, history[-1])
    if facts_cast:
        bits = []
        for c in facts_cast:
            if c["birthday_today"]:
                bits.append(f"it is {c['name']}'s birthday (age {c['age']})")
            else:
                bits.append(f"{c['name']} (age {c['age']})")
        lines.append("Present today: " + "; ".join(bits) + ".")
```

- [ ] **Step 4: Run the context test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k present_cast_age`
Expected: PASS

- [ ] **Step 5: Populate `current.cast` in the datetime route**

In `backend/src/grimoire/routes.py`, update `get_scene_datetime` so `current` includes cast facts:

```python
        current = {"native": native, **store.calendars.today_facts(cfg, native),
                   "cast": store.context.cast_datetime_facts(cid, sid, native)}
```

- [ ] **Step 6: Add birthdate to the editors' PUT routes**

Find the PC-version update route and the character-meta route (`grep -n "pcs/{pid}/versions\|class PersonaBody\|characters/{cid}\b" backend/src/grimoire/routes.py`). Add `birthdate` handling:

- For the **PC version** PUT: the persona body already carries arbitrary persona fields — confirm the Pydantic persona model includes `birthdate: str = ""` (add it if the model enumerates fields). Since `update_version` writes whatever persona dict it gets and `PERSONA_FIELDS` now includes `birthdate`, the value will persist.
- For the **character** meta: add a route (or extend the existing rename/meta route) that calls `store.characters.set_birthdate`. If no character-meta PUT exists, add:

```python
class CharacterBirthdate(BaseModel):
    birthdate: str = ""


@router.put("/worlds/{wid}/characters/{cid}/birthdate")
def put_character_birthdate(wid: str, cid: str, body: CharacterBirthdate):
    root = store.worlds.world_root(wid)
    try:
        store.characters.set_birthdate(root, cid, body.birthdate)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"ok": True}
```

(Adjust the path to match the existing character route prefix — confirm with the grep above whether characters are addressed under `/worlds/{wid}/characters/...` or a campaign path, and mirror it.)

- [ ] **Step 7: Write + run a route test for birthdate**

Append to `backend/tests/test_routes.py` a test that creates a character, PUTs a birthdate, and asserts `read_character(...)["meta"]["birthdate"]` updates (mirror the route path you used). Then:

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k birthdate`
Expected: PASS

- [ ] **Step 8: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (all)

- [ ] **Step 9: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/src/grimoire/routes.py backend/tests/test_context.py backend/tests/test_routes.py
git commit -m "feat: present-cast ages/birthdays in context + datetime cast facts + birthdate routes"
```

---

### Task 14: Birthdate inputs in the editors

**Files:**
- Modify: `frontend/src/api/client.ts` (a `setCharacterBirthdate` method; PC editor already sends persona)
- Modify: `frontend/src/components/CharacterEditor.tsx` and `frontend/src/components/PCEditor.tsx` (confirm exact filenames first with a glob)
- Test: the corresponding `*.test.tsx`

**Interfaces:**
- Produces: a birthdate `type="date"` input in each editor; `api.setCharacterBirthdate(wid, cid, birthdate)`.

- [ ] **Step 1: Confirm the editor filenames**

Run: `ls frontend/src/components | grep -iE "pc|character"` (or use Glob `frontend/src/components/*Editor*.tsx`). Use the real PC-editor and character-editor filenames in the steps below.

- [ ] **Step 2: Write the failing PC-editor test**

In the PC editor's test file, add a test that the persona form has a "Birthdate" input and that editing + saving includes `birthdate` in the persona passed to the save API (mirror the file's existing save assertion):

```ts
test("PC editor saves a birthdate", async () => {
  // ...render the editor in edit mode (follow the file's existing setup)...
  fireEvent.change(screen.getByLabelText("Birthdate"), { target: { value: "1990-06-29" } });
  fireEvent.click(screen.getByRole("button", { name: /Save/ }));
  await waitFor(() => expect(savePersonaMock).toHaveBeenCalledWith(
    expect.objectContaining({ birthdate: "1990-06-29" })));
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npx --prefix frontend vitest run <pc-editor test path>`
Expected: FAIL — no "Birthdate" input

- [ ] **Step 4: Add the birthdate input to the PC editor**

Add `birthdate` to the editor's persona state/initialization (default `""`) and render, in the persona form:

```tsx
        <label>
          Birthdate
          <input type="date" aria-label="Birthdate" value={persona.birthdate ?? ""}
                 onChange={(e) => setPersona({ ...persona, birthdate: e.target.value })} />
        </label>
```

Ensure the `Persona` type in `client.ts` includes `birthdate?: string`.

- [ ] **Step 5: Add the character-editor birthdate input + client method**

In `client.ts`:

```ts
  setCharacterBirthdate: (wid: string, cid: string, birthdate: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/characters/${cid}/birthdate`, { birthdate }),
```

(Match the path used in Task 13 Step 6.) In the character editor, render a `type="date"` "Birthdate" input bound to `read_character(...).meta.birthdate`, calling `setCharacterBirthdate` on change/save. Add a test mirroring Step 2 for the character editor.

- [ ] **Step 6: Run tests + typecheck**

Run: `npx --prefix frontend vitest run` (the two editor test files)
Expected: PASS
Run: `cd frontend && npx tsc -b`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat: birthdate inputs in PC and character editors"
```

---

## Phase 4 — Calendar config UI

### Task 15: Calendar config client + editor

**Files:**
- Modify: `backend/src/grimoire/routes.py` (calendar config GET/PUT)
- Modify: `frontend/src/api/client.ts` (calendar config client + types)
- Create: `frontend/src/components/CalendarConfig.tsx`, `frontend/src/components/CalendarConfig.test.tsx`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Produces:
  - `GET /api/campaigns/{cid}/calendar` → `{primary, secondary|null}`; `PUT /api/campaigns/{cid}/calendar` body the same shape → `{ok: true}`.
  - `api.getCalendarConfig(cid)` / `api.setCalendarConfig(cid, cfg)`.
  - `CalendarConfig` component: region select, custom-holiday add/remove list, optional secondary-region toggle.

- [ ] **Step 1: Write the failing route test**

Append to `backend/tests/test_routes.py`:

```python
def test_calendar_config_get_put(client):
    _wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["primary"]["region"] == "US"
    cfg = {"primary": {"provider": "gregorian", "region": "GB", "custom_holidays":
            [{"name": "Founding Day", "month": 4, "day": 12}], "anchor": None},
           "secondary": None}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=cfg).json() == {"ok": True}
    got = client.get(f"/api/campaigns/{cid}/calendar").json()
    assert got["primary"]["region"] == "GB"
    assert got["primary"]["custom_holidays"][0]["name"] == "Founding Day"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k calendar_config`
Expected: FAIL — routes not defined

- [ ] **Step 3: Add the calendar-config routes**

In `backend/src/grimoire/routes.py`, add a model near the others:

```python
class CalendarConfig(BaseModel):
    primary: dict
    secondary: dict | None = None
```

And the routes (near the other campaign routes):

```python
@router.get("/campaigns/{cid}/calendar")
def get_calendar_config(cid: str):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    return store.calendars.read_calendar(store.campaigns.campaign_root(cid))


@router.put("/campaigns/{cid}/calendar")
def put_calendar_config(cid: str, body: CalendarConfig):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    store.calendars.write_calendar(store.campaigns.campaign_root(cid),
                                   {"primary": body.primary, "secondary": body.secondary})
    return {"ok": True}
```

- [ ] **Step 4: Run the route test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k calendar_config`
Expected: PASS

- [ ] **Step 5: Add the client + types**

In `frontend/src/api/client.ts`:

```ts
export type CalendarBlock = {
  provider: string; region: string;
  custom_holidays: Array<{ name: string; month: number; day?: number; nth?: number; weekday?: number }>;
  anchor: { native: string; gregorian: string } | null;
};
export type CalendarConfig = { primary: CalendarBlock; secondary: CalendarBlock | null };
```

```ts
  getCalendarConfig: (cid: string) =>
    request<CalendarConfig>("GET", `/api/campaigns/${cid}/calendar`),
  setCalendarConfig: (cid: string, cfg: CalendarConfig) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/calendar`, cfg),
```

- [ ] **Step 6: Write the failing component test**

Create `frontend/src/components/CalendarConfig.test.tsx`:

```ts
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, test, expect, beforeEach } from "vitest";
import { api } from "../api/client";
import { CalendarConfig } from "./CalendarConfig";

vi.mock("../api/client", () => ({ api: { getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn() } }));

beforeEach(() => {
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null }, secondary: null });
  (api.setCalendarConfig as any).mockResolvedValue({ ok: true });
});

test("edits region and saves", async () => {
  render(<CalendarConfig cid="run" />);
  const sel = await screen.findByLabelText("Holidays region");
  fireEvent.change(sel, { target: { value: "GB" } });
  fireEvent.click(screen.getByRole("button", { name: /Save/ }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith("run",
    expect.objectContaining({ primary: expect.objectContaining({ region: "GB" }) })));
});
```

- [ ] **Step 7: Run it to verify it fails**

Run: `npx --prefix frontend vitest run src/components/CalendarConfig.test.tsx`
Expected: FAIL — module `./CalendarConfig` not found

- [ ] **Step 8: Implement `CalendarConfig.tsx`**

Create `frontend/src/components/CalendarConfig.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api, type CalendarConfig as Cfg } from "../api/client";

const REGIONS = ["US", "GB", "CA", "AU", "IL", ""];

export function CalendarConfig({ cid }: { cid: string }) {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.getCalendarConfig(cid).then(setCfg).catch(() => setCfg(null)); }, [cid]);
  if (!cfg) return <div className="field-hint">Loading calendar…</div>;

  function setRegion(region: string) {
    setCfg({ ...cfg!, primary: { ...cfg!.primary, region } });
  }
  async function save() {
    setError(null);
    try { await api.setCalendarConfig(cid, cfg!); }
    catch (err: any) { setError(err.detail ?? String(err)); }
  }

  return (
    <div className="calendar-config">
      {error && <div className="banner">{error}</div>}
      <label>
        Holidays region
        <select aria-label="Holidays region" value={cfg.primary.region}
                onChange={(e) => setRegion(e.target.value)}>
          {REGIONS.map((r) => <option key={r || "none"} value={r}>{r || "None"}</option>)}
        </select>
      </label>
      <button className="primary" onClick={save}>Save</button>
    </div>
  );
}
```

(Custom-holiday add/remove rows and the optional secondary-calendar region can be layered onto this same component as a follow-up; the region editor is the minimum that makes the config user-editable. Keep the add/remove list out of scope here unless time allows — it edits `cfg.primary.custom_holidays` with the same save call.)

- [ ] **Step 9: Run tests + typecheck**

Run: `npx --prefix frontend vitest run src/components/CalendarConfig.test.tsx`
Expected: PASS
Run: `cd frontend && npx tsc -b`
Expected: no errors

- [ ] **Step 10: Mount the editor**

Add `<CalendarConfig cid={cid} />` to the campaign settings/inspector surface where other campaign-level config lives (find it with `grep -rn "getCampaign\|campaign settings\|SceneInspector" frontend/src`). Render it under a labeled section. Re-run `tsc -b`.

- [ ] **Step 11: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py frontend/src
git commit -m "feat: calendar config routes + region editor UI"
```

---

## Final verification

- [ ] **Backend:** `backend/.venv/Scripts/python.exe -m pytest backend -q` — all pass.
- [ ] **Frontend:** `npx --prefix frontend vitest run` — all pass.
- [ ] **Typecheck:** `cd frontend && npx tsc -b` — no errors.
- [ ] **Manual smoke (optional):** create a campaign with region GB, open a scene, set the date to 2026-12-26, confirm the "When" panel shows the date and that Boxing Day appears once a GB secondary is configured.

---

## Self-review notes (coverage map)

- Provider engine + fixed-day + Gregorian → Tasks 1–3.
- Holidays (library + custom) + `today_facts` + secondary merge → Task 2.
- Calendar config storage + copy-on-create + region → Tasks 4–5, 15.
- Scene moment (`time_history`, `set_datetime`, transition line, date-less default) → Task 6; routes → Task 7; `# Today` block → Task 8; UI → Task 10.
- Region in the wizard (decision 1c) → Task 11.
- Birthdays on cast + computed ages + `# Today` cast line → Tasks 12–14.
- Anchor + synchronized secondary calendar (dual render + merged holidays) → Tasks 2, 4, 8, 15 (exercised by the US+GB secondary test).
- Out-of-scope items (non-Gregorian providers, world→campaign sync, moon phases, time zones, LLM-emitted dates) remain unbuilt by design.
