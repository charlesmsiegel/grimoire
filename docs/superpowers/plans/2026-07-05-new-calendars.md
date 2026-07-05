# Harptos & Hebrew Calendars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new calendar providers — the Calendar of Harptos (Forgotten Realms) and the Hebrew calendar with the full traditional Jewish holiday set — plus structured date-entry UI, per `docs/superpowers/specs/2026-07-05-new-calendars-design.md`.

**Architecture:** Each calendar is a Python `CalendarProvider` subclass in `backend/src/grimoire/store/calendars/`, registered in the existing `REGISTRY` over the fixed-day (Rata Die) axis. Hebrew wraps `pyluach`; Harptos is pure arithmetic with an internal epoch. A new `months(year)` provider method feeds a new months endpoint and a shared `CalendarDatePicker` React component that replaces every raw `type="date"` input.

**Tech Stack:** FastAPI + pytest (backend), Vite/React + vitest (frontend), `pyluach` (new dep).

## Global Constraints

- **Worktree:** all paths relative to `C:\Users\charl\github\grimoire\.worktrees\new-calendars`. All commands run from that directory unless stated.
- **Backend tests:** `PYTHONPATH=/c/Users/charl/github/grimoire/.worktrees/new-calendars /c/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend -q` (PYTHONPATH must shadow the editable install; abbreviated below as `pytest backend`). Single test: append `backend/tests/test_calendars.py::test_name -v`.
- **Frontend tests:** run **from `frontend/`**: `npx vitest run` (never `npx --prefix`); typecheck `npx tsc -b`.
- **`pyluach` is already installed** in `backend/.venv` (dev machine); Task 4 adds it to `pyproject.toml`.
- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Native date strings never contain commas; time suffix is `Thh:mm`.
- Providers raise `CalendarError` (never ValueError) on bad input.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Fix `split_native` to split only on a trailing time suffix

Hebrew (`5786-Tishrei-01`) and Harptos (`1492-Tarsakh-05`) native dates contain a capital `T`; the current first-`T` `partition` mangles them.

**Files:**
- Modify: `backend/src/grimoire/store/calendars/base.py:68-70`
- Test: `backend/tests/test_calendars.py`

**Interfaces:**
- Produces: `split_native(native: str) -> tuple[str, str | None]` — unchanged signature, now splits only on a trailing `T\d{1,2}:\d{2}`.

- [ ] **Step 1: Write the failing test** — append to `backend/tests/test_calendars.py`:

```python
def test_split_native_survives_month_names_containing_T():
    # Hebrew and Harptos month tokens contain a capital T; only a trailing
    # Thh:mm may be treated as a time suffix.
    assert split_native("5786-Tishrei-01") == ("5786-Tishrei-01", None)
    assert split_native("1492-Tarsakh-05") == ("1492-Tarsakh-05", None)
    assert split_native("5786-Tishrei-01T14:30") == ("5786-Tishrei-01", "14:30")
    assert split_native("1492-Tarsakh-05T9:05") == ("1492-Tarsakh-05", "9:05")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest backend/tests/test_calendars.py::test_split_native_survives_month_names_containing_T -v`
Expected: FAIL — `("5786-", "ishrei-01")` instead of `("5786-Tishrei-01", None)`.

- [ ] **Step 3: Implement** — in `base.py`, add `import re` below `from abc import ...`, and replace the body of `split_native`:

```python
_TIME_SUFFIX = re.compile(r"T(\d{1,2}:\d{2})$")


def split_native(native: str) -> tuple[str, str | None]:
    m = _TIME_SUFFIX.search(native)
    if m:
        return native[: m.start()], m.group(1)
    return native, None
```

- [ ] **Step 4: Run the full backend suite** (existing Gregorian `T` tests must stay green)

Run: `pytest backend -q`
Expected: all pass (707 + 1 new).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/calendars/base.py backend/tests/test_calendars.py
git commit -m "fix(calendars): split time suffix only on trailing Thh:mm"
```

---

### Task 2: `months(year)` provider method + Gregorian implementation

**Files:**
- Modify: `backend/src/grimoire/store/calendars/base.py` (add abstract method to `CalendarProvider`)
- Modify: `backend/src/grimoire/store/calendars/gregorian.py`
- Test: `backend/tests/test_calendars.py`

**Interfaces:**
- Produces: `CalendarProvider.months(year: int) -> list[dict]` — the year's months in calendar order, each `{"key": str, "name": str, "days": int}`. **Contract:** `f"{year}-{key}-{day:02d}"` is a valid native date for `1 <= day <= days`. Raises `CalendarError` on an unusable year.

- [ ] **Step 1: Write the failing test:**

```python
def test_gregorian_months_shape_and_leap_february():
    p = get_provider(greg())
    ms = p.months(2024)
    assert len(ms) == 12
    assert ms[0] == {"key": "01", "name": "January", "days": 31}
    assert ms[1]["days"] == 29                      # leap February
    assert p.months(2026)[1]["days"] == 28
    # composition contract: year-key-day parses
    assert p.format(p.parse("2026-02-28")) == "2026-02-28"
    with pytest.raises(CalendarError):
        p.months("nope")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest backend/tests/test_calendars.py::test_gregorian_months_shape_and_leap_february -v`
Expected: FAIL — `AttributeError: 'GregorianProvider' object has no attribute 'months'` (or abstract error).

- [ ] **Step 3: Implement.** In `base.py`, add to `CalendarProvider` after `holidays`:

```python
    @abstractmethod
    def months(self, year: int) -> list[dict]:
        """The year's months in calendar order, each {key, name, days}.
        f"{year}-{key}-{day:02d}" must be a valid native date for 1 <= day <= days."""
```

In `gregorian.py`, add to `GregorianProvider`:

```python
    def months(self, year: int) -> list[dict]:
        try:
            y = int(year)
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad gregorian year: {year!r}") from e
        return [{"key": f"{m:02d}", "name": _cal.month_name[m],
                 "days": _cal.monthrange(y, m)[1]} for m in range(1, 13)]
```

- [ ] **Step 4: Run the suite** — `pytest backend -q`, expected all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/calendars/base.py backend/src/grimoire/store/calendars/gregorian.py backend/tests/test_calendars.py
git commit -m "feat(calendars): months(year) on the provider interface"
```

---

### Task 3: Provider-aware custom-holiday validation

Move rule validation from Gregorian-hardcoded `config._validate_rule` onto the providers.

**Files:**
- Modify: `backend/src/grimoire/store/calendars/base.py` (default `validate_rule`, `_month_entry`, `_custom_fixed` helpers)
- Modify: `backend/src/grimoire/store/calendars/gregorian.py` (override keeps nth-weekday)
- Modify: `backend/src/grimoire/store/calendars/config.py` (delegate; delete `_validate_rule` and its `date` import)
- Test: `backend/tests/test_calendars.py`

**Interfaces:**
- Produces: `CalendarProvider.validate_rule(rule: dict) -> None` (raises `CalendarError`); `CalendarProvider._custom_fixed(start_fixed, end_fixed) -> list[{name, fixed}]` for later providers; class attr `RULE_REFERENCE_YEAR` (base `2024`).
- Consumes: `months()` from Task 2.

- [ ] **Step 1: Write the failing test:**

```python
def test_validate_rule_is_provider_aware():
    p = get_provider(greg())
    p.validate_rule({"name": "Founding Day", "month": 4, "day": 12})     # int month (legacy)
    p.validate_rule({"name": "Founding Day", "month": "04", "day": 12})  # key form
    p.validate_rule({"name": "Leap", "month": 2, "day": 29})             # Feb 29 allowed
    p.validate_rule({"name": "Harvest", "month": 9, "nth": 3, "weekday": 6})
    for bad in ({"name": "X", "month": 13, "day": 1},
                {"name": "X", "month": 4},
                {"month": 4, "day": 12},
                {"name": "X", "month": 2, "day": 30}):
        with pytest.raises(CalendarError):
            p.validate_rule(bad)
```

- [ ] **Step 2: Run it** — expected FAIL: no attribute `validate_rule`.

- [ ] **Step 3: Implement.** In `base.py`, add to `CalendarProvider` (after the `is_anniversary` default):

```python
    # Fixed {name, month, day} custom-holiday rules, validated against months().
    RULE_REFERENCE_YEAR = 2024  # a leap year, so Feb-29-style rules validate

    def _month_entry(self, key) -> dict | None:
        wanted = str(key)
        if wanted.isdigit():
            wanted = f"{int(wanted):02d}"  # legacy integer months (Gregorian)
        wanted = wanted.lower()
        for m in self.months(self.RULE_REFERENCE_YEAR):
            if m["key"].lower() == wanted:
                return m
        return None

    def validate_rule(self, rule: dict) -> None:
        """Raise CalendarError unless rule is a valid fixed {name, month, day} rule."""
        if not rule.get("name"):
            raise CalendarError(f"custom holiday needs a name: {rule!r}")
        if "day" not in rule:
            raise CalendarError(f"only fixed {{month, day}} custom holidays are supported: {rule!r}")
        try:
            day = int(rule["day"])
        except (ValueError, TypeError) as e:
            raise CalendarError(f"custom holiday day is malformed: {rule!r}") from e
        m = self._month_entry(rule.get("month"))
        if m is None:
            raise CalendarError(f"custom holiday month is unknown: {rule!r}")
        if not (1 <= day <= m["days"]):
            raise CalendarError(f"custom holiday day out of range: {rule!r}")

    def _custom_fixed(self, start_fixed: int, end_fixed: int) -> list[dict]:
        """Resolve this provider's fixed custom rules within a fixed-day range."""
        out: list[dict] = []
        y0 = self.describe(start_fixed)["year"]
        y1 = self.describe(end_fixed)["year"]
        for rule in getattr(self, "custom_holidays", []) or []:
            if "day" not in rule:
                continue
            for y in range(y0, y1 + 1):
                try:
                    f = self.parse(f"{y}-{rule['month']}-{int(rule['day']):02d}")
                except (CalendarError, KeyError, ValueError, TypeError):
                    continue
                if start_fixed <= f <= end_fixed:
                    out.append({"name": rule.get("name", ""), "fixed": f})
        return out
```

In `gregorian.py`, add the override (keeps nth-weekday rules Gregorian-only):

```python
    def validate_rule(self, rule: dict) -> None:
        if "day" in rule:
            super().validate_rule(rule)
            return
        if not rule.get("name"):
            raise CalendarError(f"custom holiday needs a name: {rule!r}")
        try:
            month, nth, weekday = int(rule["month"]), int(rule["nth"]), int(rule["weekday"])
        except (KeyError, ValueError, TypeError) as e:
            raise CalendarError(f"custom holiday rule is malformed: {rule!r}") from e
        if not (1 <= month <= 12 and 1 <= nth <= 5 and 0 <= weekday <= 6):
            raise CalendarError(f"custom holiday rule is malformed: {rule!r}")
```

In `config.py`, replace `validate_calendar` (and **delete** `_validate_rule` plus the now-unused `from datetime import date`):

```python
def validate_calendar(cfg: dict) -> None:
    """Raise CalendarError if any configured calendar has a malformed custom holiday."""
    for block in (cfg.get("primary"), cfg.get("secondary")):
        if not block:
            continue
        provider = get_provider(block)  # raises CalendarError on an unknown provider
        for rule in block.get("custom_holidays", []) or []:
            provider.validate_rule(rule)
```

- [ ] **Step 4: Run the suite** — `pytest backend -q`. The existing `test_validate_calendar_rejects_bad_custom_rules` must still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/calendars/base.py backend/src/grimoire/store/calendars/gregorian.py backend/src/grimoire/store/calendars/config.py backend/tests/test_calendars.py
git commit -m "feat(calendars): provider-aware custom-holiday validation"
```

---

### Task 4: Hebrew provider — parse/format/describe/months

**Files:**
- Create: `backend/src/grimoire/store/calendars/hebrew.py`
- Modify: `backend/src/grimoire/store/calendars/__init__.py` (import registers the provider)
- Modify: `backend/pyproject.toml:12` (add `"pyluach>=2.2",` after the `holidays` line)
- Create: `backend/tests/test_calendar_hebrew.py`

**Interfaces:**
- Produces: registry id `"hebrew"`; native format `5786-Kislev-25`; canonical tokens `Tishrei Cheshvan Kislev Tevet Shevat Adar Adar1 Adar2 Nisan Iyar Sivan Tammuz Av Elul`; `describe()["month"]` = civil position (Tishrei=1); weekdays `Sunday…Friday, Shabbat` (index 0–6).
- Consumes: `CalendarProvider` ABC incl. Task 2/3 additions.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_calendar_hebrew.py`:

```python
import pytest

from grimoire.store.calendars import CalendarError, get_provider


def heb(region=""):
    return {"provider": "hebrew", "region": region, "custom_holidays": [], "anchor": None}


def test_known_conversions_roundtrip():
    p = get_provider(heb())
    g = get_provider({"provider": "gregorian", "region": "", "custom_holidays": [], "anchor": None})
    # 25 Kislev 5786 = 15 Dec 2025; 1 Tishrei 5786 (Rosh Hashanah) = 23 Sep 2025
    assert p.parse("5786-Kislev-25") == g.parse("2025-12-15")
    assert p.parse("5786-Tishrei-01") == g.parse("2025-09-23")
    for native in ("5786-Kislev-25", "5784-Adar2-14", "5786-Nisan-15"):
        assert p.format(p.parse(native)) == native


def test_parse_is_case_insensitive_and_normalizes_adar():
    p = get_provider(heb())
    assert p.format(p.parse("5786-kislev-25")) == "5786-Kislev-25"
    # leap year: plain Adar is accepted and normalized to Adar2 (observance month)
    assert p.format(p.parse("5784-Adar-14")) == "5784-Adar2-14"


def test_bad_dates_raise():
    p = get_provider(heb())
    with pytest.raises(CalendarError):
        p.parse("5786-Adar1-01")        # Adar I doesn't exist in a non-leap year
    with pytest.raises(CalendarError):
        p.parse("5786-Cheshvan-30")     # Cheshvan is short in 5786
    with pytest.raises(CalendarError):
        p.parse("5786-Floof-01")
    with pytest.raises(CalendarError):
        p.parse("5786-Kislev")          # missing day


def test_describe_weekday_and_friendly():
    p = get_provider(heb())
    d = p.describe(p.parse("5786-Tishrei-01"))   # 23 Sep 2025 is a Tuesday
    assert d["weekday_name"] == "Tuesday"
    assert d["friendly"] == "1 Tishrei 5786"
    assert d["month"] == 1                        # civil position
    # Shabbat: 27 Sep 2025 is a Saturday = 5 Tishrei 5786
    s = p.describe(p.parse("5786-Tishrei-05"))
    assert s["weekday_name"] == "Shabbat"
    assert s["weekday_index"] == 6


def test_months_leap_and_common_years():
    p = get_provider(heb())
    common = p.months(5786)
    assert [m["key"] for m in common][:6] == ["Tishrei", "Cheshvan", "Kislev", "Tevet", "Shevat", "Adar"]
    assert len(common) == 12
    leap = p.months(5784)
    assert len(leap) == 13
    keys = [m["key"] for m in leap]
    assert "Adar1" in keys and "Adar2" in keys and "Adar" not in keys
    assert next(m for m in leap if m["key"] == "Adar1")["name"] == "Adar I"
    # composition contract
    for m in leap:
        assert p.format(p.parse(f"5784-{m['key']}-{m['days']:02d}")) == f"5784-{m['key']}-{m['days']:02d}"
```

- [ ] **Step 2: Run them** — `pytest backend/tests/test_calendar_hebrew.py -v`
Expected: FAIL — `CalendarError: unknown calendar provider: 'hebrew'`.

- [ ] **Step 3: Implement** — create `backend/src/grimoire/store/calendars/hebrew.py`:

```python
"""Hebrew calendar provider backed by pyluach (exact lunisolar arithmetic and
the traditional holiday cycle). Native format: 5786-Kislev-25. The config
`region` field selects observance: "IL" = Israel, anything else = diaspora."""

from __future__ import annotations

from datetime import date

from pyluach import dates as _pd, hebrewcal as _pc

from .base import CalendarError, CalendarProvider, register

# (token, display name, pyluach month number, in leap years, in common years)
_MONTHS = [
    ("Tishrei", "Tishrei", 7, True, True),
    ("Cheshvan", "Cheshvan", 8, True, True),
    ("Kislev", "Kislev", 9, True, True),
    ("Tevet", "Tevet", 10, True, True),
    ("Shevat", "Shevat", 11, True, True),
    ("Adar", "Adar", 12, False, True),
    ("Adar1", "Adar I", 12, True, False),
    ("Adar2", "Adar II", 13, True, False),
    ("Nisan", "Nisan", 1, True, True),
    ("Iyar", "Iyar", 2, True, True),
    ("Sivan", "Sivan", 3, True, True),
    ("Tammuz", "Tammuz", 4, True, True),
    ("Av", "Av", 5, True, True),
    ("Elul", "Elul", 6, True, True),
]
_WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Shabbat"]


def _is_leap(year: int) -> bool:
    return _pc.Year(year).leap


def _year_months(year: int) -> list[tuple[str, str, int]]:
    leap = _is_leap(year)
    return [(t, disp, num) for t, disp, num, in_leap, in_common in _MONTHS
            if (in_leap if leap else in_common)]


class HebrewProvider(CalendarProvider):
    def __init__(self, config: dict):
        self.region = config.get("region", "")
        self.custom_holidays = config.get("custom_holidays", []) or []
        self.anchor = config.get("anchor")  # canonical calendar — anchor is ignored

    def parse(self, native: str) -> int:
        parts = str(native).rsplit("-", 2)
        if len(parts) != 3:
            raise CalendarError(f"bad hebrew date: {native!r}")
        y_str, token, d_str = parts
        try:
            y, d = int(y_str), int(d_str)
            leap = _is_leap(y)
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad hebrew date: {native!r}") from e
        tok = token.lower()
        if tok == "adar" and leap:
            tok = "adar2"  # plain Adar in a leap year = the observance month
        entry = next((e for e in _year_months(y) if e[0].lower() == tok), None)
        if entry is None:
            raise CalendarError(f"unknown hebrew month for {y}: {token!r}")
        try:
            return _pd.HebrewDate(y, entry[2], d).to_pydate().toordinal()
        except ValueError as e:
            raise CalendarError(f"bad hebrew date: {native!r}") from e

    def format(self, fixed: int) -> str:
        h = _pd.HebrewDate.from_pydate(date.fromordinal(fixed))
        token = next(e[0] for e in _year_months(h.year) if e[2] == h.month)
        return f"{h.year}-{token}-{h.day:02d}"

    def describe(self, fixed: int) -> dict:
        h = _pd.HebrewDate.from_pydate(date.fromordinal(fixed))
        ms = _year_months(h.year)
        pos = next(i for i, e in enumerate(ms, 1) if e[2] == h.month)
        _token, disp, _num = ms[pos - 1]
        widx = (date.fromordinal(fixed).weekday() + 1) % 7  # Sunday=0 … Shabbat=6
        return {"year": h.year, "month": pos, "month_name": disp, "day": h.day,
                "weekday_name": _WEEKDAYS[widx], "weekday_index": widx,
                "friendly": f"{h.day} {disp} {h.year}"}

    def holidays(self, start_fixed: int, end_fixed: int) -> list[dict]:
        return []  # Task 5

    def months(self, year: int) -> list[dict]:
        try:
            y = int(year)
            ms = _year_months(y)
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad hebrew year: {year!r}") from e
        return [{"key": token, "name": disp,
                 "days": len(list(_pc.Month(y, num).iterdates()))}
                for token, disp, num in ms]


register("hebrew", HebrewProvider)
```

In `__init__.py`, change the first line to:

```python
from . import gregorian, hebrew  # noqa: F401  (import registers the providers)
```

In `backend/pyproject.toml`, after `"holidays>=0.40",` add:

```toml
    "pyluach>=2.2",
```

- [ ] **Step 4: Run them** — `pytest backend/tests/test_calendar_hebrew.py -v`, expected PASS; then `pytest backend -q` for the full suite.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/calendars/hebrew.py backend/src/grimoire/store/calendars/__init__.py backend/pyproject.toml backend/tests/test_calendar_hebrew.py
git commit -m "feat(calendars): hebrew provider (pyluach) — dates, describe, months"
```

---

### Task 5: Hebrew holidays, age/anniversary override, rule validation

**Files:**
- Modify: `backend/src/grimoire/store/calendars/hebrew.py`
- Test: `backend/tests/test_calendar_hebrew.py`

**Interfaces:**
- Produces: full `holidays()` (pyluach festivals + fasts + custom fixed rules), `age`/`is_anniversary` with Adar folding, `validate_rule` (fixed-only, static max-days table).

- [ ] **Step 1: Write the failing tests** — append to `test_calendar_hebrew.py`:

```python
def test_holidays_chanukah_and_observance_toggle():
    p = get_provider(heb())          # diaspora
    il = get_provider(heb("IL"))     # Israel
    start, end = p.parse("5786-Kislev-24"), p.parse("5786-Tevet-03")
    names = [h["name"] for h in p.holidays(start, end)]
    assert any("Chanuka" in n for n in names)
    # 22 Nisan is yom tov in the diaspora only (2nd day of the last day of Pesach)
    day = p.parse("5786-Nisan-22")
    assert any("Pesach" in h["name"] for h in p.holidays(day, day))
    assert il.holidays(day, day) == []


def test_holidays_include_fasts_and_customs():
    p = get_provider(heb())
    # 3 Tishrei 5786 is a Thursday (25 Sep 2025) — Tzom Gedaliah, not deferred.
    fast = p.parse("5786-Tishrei-03")
    assert any("Gedalia" in h["name"] for h in p.holidays(fast, fast))
    custom = get_provider({"provider": "hebrew", "region": "",
                           "custom_holidays": [{"name": "Grandma's yahrzeit", "month": "Shevat", "day": 10}],
                           "anchor": None})
    day = custom.parse("5786-Shevat-10")
    assert any(h["name"] == "Grandma's yahrzeit" for h in custom.holidays(day, day))


def test_age_and_anniversary_across_tishrei_and_adar():
    p = get_provider(heb())
    # born 10 Tishrei 5750; year rolls at Rosh Hashanah
    birth = "5750-Tishrei-10"
    from grimoire.store.calendars import age, is_anniversary
    assert age(p, birth, "5786-Tishrei-09") == 35
    assert age(p, birth, "5786-Tishrei-10") == 36
    assert is_anniversary(p, birth, "5786-Tishrei-10") is True
    # born in Adar II of leap 5784 → observed in plain Adar of common 5786
    assert is_anniversary(p, "5784-Adar2-14", "5786-Adar-14") is True
    # born 30 Cheshvan (long 5782) → observed 29 Cheshvan when short (5786)
    assert is_anniversary(p, "5782-Cheshvan-30", "5786-Cheshvan-29") is True


def test_validate_rule_hebrew():
    p = get_provider(heb())
    p.validate_rule({"name": "OK", "month": "Adar", "day": 14})
    p.validate_rule({"name": "OK", "month": "Kislev", "day": 30})
    for bad in ({"name": "X", "month": "Adar", "day": 30},      # Adar caps at 29
                {"name": "X", "month": "Floof", "day": 1},
                {"name": "X", "month": "Elul", "nth": 1, "weekday": 0}):  # nth-weekday off-Gregorian
        with pytest.raises(CalendarError):
            p.validate_rule(bad)
```

- [ ] **Step 2: Run them** — expected FAIL (holidays returns `[]`, base anniversary logic wrong across Tishrei).

- [ ] **Step 3: Implement** — in `hebrew.py`, replace the `holidays` stub and add the overrides:

```python
    _MAX_DAYS = {"Tishrei": 30, "Cheshvan": 30, "Kislev": 30, "Tevet": 29,
                 "Shevat": 30, "Adar": 29, "Adar1": 30, "Adar2": 29, "Nisan": 30,
                 "Iyar": 29, "Sivan": 30, "Tammuz": 29, "Av": 30, "Elul": 29}

    def holidays(self, start_fixed: int, end_fixed: int) -> list[dict]:
        israel = self.region == "IL"
        out: list[dict] = []
        for f in range(start_fixed, end_fixed + 1):
            h = _pd.HebrewDate.from_pydate(date.fromordinal(f))
            for name in (h.festival(israel=israel, include_working_days=True),
                         h.fast_day()):
                if name:
                    out.append({"name": name, "fixed": f})
        out.extend(self._custom_fixed(start_fixed, end_fixed))
        out.sort(key=lambda h: h["fixed"])
        return out

    def _anniversary_fixed(self, birth_fixed: int, asof_year: int) -> int:
        """The fixed day the birth date is observed in asof_year (Adar folding,
        day-30 births observed on the 29th when the month is short)."""
        b = _pd.HebrewDate.from_pydate(date.fromordinal(birth_fixed))
        token = next(e[0] for e in _year_months(b.year) if e[2] == b.month)
        if _is_leap(asof_year):
            if token == "Adar":
                token = "Adar2"
        elif token in ("Adar1", "Adar2"):
            token = "Adar"
        num = next(e[2] for e in _year_months(asof_year) if e[0] == token)
        try:
            return _pd.HebrewDate(asof_year, num, b.day).to_pydate().toordinal()
        except ValueError:
            return _pd.HebrewDate(asof_year, num, 29).to_pydate().toordinal()

    def age(self, birth_fixed: int, asof_fixed: int) -> int:
        b_year = _pd.HebrewDate.from_pydate(date.fromordinal(birth_fixed)).year
        a_year = _pd.HebrewDate.from_pydate(date.fromordinal(asof_fixed)).year
        years = a_year - b_year
        if self._anniversary_fixed(birth_fixed, a_year) > asof_fixed:
            years -= 1
        return years

    def is_anniversary(self, birth_fixed: int, asof_fixed: int) -> bool:
        a_year = _pd.HebrewDate.from_pydate(date.fromordinal(asof_fixed)).year
        return self._anniversary_fixed(birth_fixed, a_year) == asof_fixed

    def validate_rule(self, rule: dict) -> None:
        if "day" not in rule:
            raise CalendarError(
                f"the hebrew calendar supports only fixed {{month, day}} custom holidays: {rule!r}")
        if not rule.get("name"):
            raise CalendarError(f"custom holiday needs a name: {rule!r}")
        token = next((t for t in self._MAX_DAYS
                      if t.lower() == str(rule.get("month", "")).lower()), None)
        if token is None:
            raise CalendarError(f"custom holiday month is unknown: {rule!r}")
        try:
            day = int(rule["day"])
        except (ValueError, TypeError) as e:
            raise CalendarError(f"custom holiday day is malformed: {rule!r}") from e
        if not (1 <= day <= self._MAX_DAYS[token]):
            raise CalendarError(f"custom holiday day out of range: {rule!r}")
```

- [ ] **Step 4: Run** `pytest backend/tests/test_calendar_hebrew.py -v` then `pytest backend -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/calendars/hebrew.py backend/tests/test_calendar_hebrew.py
git commit -m "feat(calendars): hebrew holidays, adar-folding anniversaries, rule validation"
```

---

### Task 6: Harptos provider — core arithmetic

**Files:**
- Create: `backend/src/grimoire/store/calendars/harptos.py`
- Modify: `backend/src/grimoire/store/calendars/__init__.py` (add to the registering import)
- Create: `backend/tests/test_calendar_harptos.py`

**Interfaces:**
- Produces: registry id `"harptos"`; native `1492-Mirtul-05` (festivals `1492-Midsummer-01`); epoch `1-Hammer-01` = fixed day 1; `describe()["month"]` = stable index 1–18 (Shieldmeet always 11); month keys `Hammer Midwinter Alturiak Ches Tarsakh Greengrass Mirtul Kythorn Flamerule Midsummer Shieldmeet Eleasis Eleint Highharvestide Marpenoth Uktar FeastOfTheMoon Nightal`.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_calendar_harptos.py`:

```python
import pytest

from grimoire.store.calendars import CalendarError, get_provider


def har(custom=None):
    return {"provider": "harptos", "region": "", "custom_holidays": custom or [], "anchor": None}


def test_epoch_and_roundtrip():
    p = get_provider(har())
    assert p.parse("1-Hammer-01") == 1
    for native in ("1492-Mirtul-05", "1492-Midsummer-01", "1492-Shieldmeet-01",
                   "1491-Nightal-30", "1492-FeastOfTheMoon-01"):
        assert p.format(p.parse(native)) == native


def test_year_lengths_and_festival_ordering():
    p = get_provider(har())
    # 1491 is common (365), 1492 is leap (366): Shieldmeet exists
    assert p.parse("1492-Hammer-01") - p.parse("1491-Hammer-01") == 365
    assert p.parse("1493-Hammer-01") - p.parse("1492-Hammer-01") == 366
    # Midwinter sits between Hammer 30 and Alturiak 1
    assert p.parse("1492-Midwinter-01") == p.parse("1492-Hammer-30") + 1
    assert p.parse("1492-Alturiak-01") == p.parse("1492-Midwinter-01") + 1
    with pytest.raises(CalendarError):
        p.parse("1491-Shieldmeet-01")   # not a leap year
    with pytest.raises(CalendarError):
        p.parse("1492-Mirtul-31")
    with pytest.raises(CalendarError):
        p.parse("1492-Floof-01")


def test_describe_tenday_and_festivals():
    p = get_provider(har())
    d = p.describe(p.parse("1492-Mirtul-05"))
    assert d["weekday_name"] == "5th day of the tenday"
    assert d["weekday_index"] == 4
    assert d["month_name"] == "Mirtul"
    assert d["month"] == 7                      # stable index: Mirtul is 7th slot
    f = p.describe(p.parse("1492-Midsummer-01"))
    assert f["weekday_name"] == "festival day"
    assert f["weekday_index"] is None
    assert f["friendly"].startswith("Midsummer, 1492 DR")
    # stable month indices: Eleasis is slot 12 in leap AND common years
    assert p.describe(p.parse("1492-Eleasis-01"))["month"] == 12
    assert p.describe(p.parse("1491-Eleasis-01"))["month"] == 12


def test_months_lists_and_age():
    p = get_provider(har())
    common, leap = p.months(1491), p.months(1492)
    assert len(common) == 17 and len(leap) == 18
    assert [m["key"] for m in leap][9:12] == ["Midsummer", "Shieldmeet", "Eleasis"]
    assert next(m for m in leap if m["key"] == "FeastOfTheMoon")["name"] == "Feast of the Moon"
    from grimoire.store.calendars import age, is_anniversary
    assert age(p, "1450-Eleasis-05", "1492-Eleasis-04") == 41
    assert age(p, "1450-Eleasis-05", "1492-Eleasis-05") == 42
    # birthday works across the Shieldmeet insertion (born common year, asof leap)
    assert is_anniversary(p, "1451-Eleasis-05", "1492-Eleasis-05") is True


def test_negative_and_zero_years():
    p = get_provider(har())
    assert p.format(p.parse("0-Hammer-01")) == "0-Hammer-01"
    assert p.format(p.parse("-100-Nightal-30")) == "-100-Nightal-30"
    assert p.parse("1-Hammer-01") - p.parse("0-Hammer-01") == 366  # year 0 is leap (0 % 4 == 0)
```

- [ ] **Step 2: Run them** — expected FAIL: unknown provider `'harptos'`.

- [ ] **Step 3: Implement** — create `backend/src/grimoire/store/calendars/harptos.py`:

```python
"""Calendar of Harptos (Forgotten Realms): 12 months x 30 days with five 1-day
festivals between months, plus Shieldmeet after Midsummer when DR % 4 == 0.
Pure arithmetic; epoch 1 Hammer 1 DR = fixed day 1 (internal, primaries-only).
Native format: 1492-Mirtul-05; festivals are day 01 (1492-Midsummer-01)."""

from __future__ import annotations

from .base import CalendarError, CalendarProvider, register
from .harptos_years import YEAR_NAMES

# (key, display name, days). Stable month index = 1-based position in THIS
# list; Shieldmeet always owns slot 11 (absent from common years), so indices
# never shift and default age/is_anniversary stay correct.
_MONTHS = [
    ("Hammer", "Hammer", 30),
    ("Midwinter", "Midwinter", 1),
    ("Alturiak", "Alturiak", 30),
    ("Ches", "Ches", 30),
    ("Tarsakh", "Tarsakh", 30),
    ("Greengrass", "Greengrass", 1),
    ("Mirtul", "Mirtul", 30),
    ("Kythorn", "Kythorn", 30),
    ("Flamerule", "Flamerule", 30),
    ("Midsummer", "Midsummer", 1),
    ("Shieldmeet", "Shieldmeet", 1),
    ("Eleasis", "Eleasis", 30),
    ("Eleint", "Eleint", 30),
    ("Highharvestide", "Highharvestide", 1),
    ("Marpenoth", "Marpenoth", 30),
    ("Uktar", "Uktar", 30),
    ("FeastOfTheMoon", "Feast of the Moon", 1),
    ("Nightal", "Nightal", 30),
]
_ORDINALS = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th"]


def _is_leap(year: int) -> bool:
    return year % 4 == 0


def _year_entries(year: int) -> list[tuple[int, str, str, int]]:
    """(stable_index, key, name, days) for the year, in calendar order."""
    return [(i, k, n, d) for i, (k, n, d) in enumerate(_MONTHS, 1)
            if k != "Shieldmeet" or _is_leap(year)]


def _days_before_year(year: int) -> int:
    # 365 per year plus one Shieldmeet per DR % 4 == 0 year; Python floor
    # division keeps this exact for zero and negative years.
    return 365 * (year - 1) + (year - 1) // 4


class HarptosProvider(CalendarProvider):
    RULE_REFERENCE_YEAR = 4  # leap, so Shieldmeet rules validate

    def __init__(self, config: dict):
        self.region = config.get("region", "")          # unused
        self.custom_holidays = config.get("custom_holidays", []) or []
        self.anchor = config.get("anchor")               # primaries-only — ignored

    def parse(self, native: str) -> int:
        parts = str(native).rsplit("-", 2)
        if len(parts) != 3:
            raise CalendarError(f"bad harptos date: {native!r}")
        y_str, token, d_str = parts
        try:
            y, d = int(y_str), int(d_str)
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad harptos date: {native!r}") from e
        offset = 0
        for _i, key, _name, days in _year_entries(y):
            if key.lower() == token.lower():
                if not 1 <= d <= days:
                    raise CalendarError(f"harptos day out of range: {native!r}")
                return _days_before_year(y) + offset + d
            offset += days
        raise CalendarError(f"unknown harptos month: {native!r}")

    def _locate(self, fixed: int) -> tuple[int, tuple[int, str, str, int], int]:
        y = fixed // 366  # underestimate; walk up to the right year
        while _days_before_year(y + 1) < fixed:
            y += 1
        rem = fixed - _days_before_year(y)
        for entry in _year_entries(y):
            if rem <= entry[3]:
                return y, entry, rem
            rem -= entry[3]
        raise CalendarError(f"fixed day out of range: {fixed}")  # unreachable

    def format(self, fixed: int) -> str:
        y, (_i, key, _name, _days), d = self._locate(fixed)
        return f"{y}-{key}-{d:02d}"

    def describe(self, fixed: int) -> dict:
        y, (idx, _key, name, days), d = self._locate(fixed)
        if days == 1:  # festival
            friendly, weekday_name, weekday_index = f"{name}, {y} DR", "festival day", None
        else:
            pos = (d - 1) % 10
            friendly = f"{d} {name}, {y} DR"
            weekday_name, weekday_index = f"{_ORDINALS[pos]} day of the tenday", pos
        year_name = YEAR_NAMES.get(y)
        if year_name:
            friendly += f" ({year_name})"
        return {"year": y, "month": idx, "month_name": name, "day": d,
                "weekday_name": weekday_name, "weekday_index": weekday_index,
                "friendly": friendly}

    def holidays(self, start_fixed: int, end_fixed: int) -> list[dict]:
        return []  # Task 8

    def months(self, year: int) -> list[dict]:
        try:
            y = int(year)
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad harptos year: {year!r}") from e
        return [{"key": k, "name": n, "days": d} for _i, k, n, d in _year_entries(y)]


register("harptos", HarptosProvider)
```

Create a **placeholder** `backend/src/grimoire/store/calendars/harptos_years.py` (Task 7 replaces it with scraped data):

```python
"""Roll of Years: DR year -> named year. Generated from the Forgotten Realms
wiki (Task 7 of the 2026-07-05 new-calendars plan); do not hand-edit."""

YEAR_NAMES: dict[int, str] = {}
```

In `__init__.py`:

```python
from . import gregorian, harptos, hebrew  # noqa: F401  (import registers the providers)
```

- [ ] **Step 4: Run** `pytest backend/tests/test_calendar_harptos.py -v` then `pytest backend -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/calendars/harptos.py backend/src/grimoire/store/calendars/harptos_years.py backend/src/grimoire/store/calendars/__init__.py backend/tests/test_calendar_harptos.py
git commit -m "feat(calendars): harptos provider — months, festivals, shieldmeet, tendays"
```

---

### Task 7: Roll of Years data module

**Files:**
- Create (scratch, NOT committed): `<scratchpad>/scrape_roll_of_years.py`
- Replace: `backend/src/grimoire/store/calendars/harptos_years.py`
- Test: `backend/tests/test_calendar_harptos.py`

**Interfaces:**
- Produces: `YEAR_NAMES: dict[int, str]` with entries like `1492: "Year of Three Ships Sailing"`.

- [ ] **Step 1: Write the failing test** — append to `test_calendar_harptos.py`:

```python
def test_friendly_includes_roll_of_years_name():
    from grimoire.store.calendars.harptos_years import YEAR_NAMES
    assert YEAR_NAMES[1492] == "Year of Three Ships Sailing"
    assert YEAR_NAMES[1372] == "Year of Wild Magic"
    assert len(YEAR_NAMES) > 1000
    p = get_provider(har())
    d = p.describe(p.parse("1492-Mirtul-05"))
    assert d["friendly"] == "5 Mirtul, 1492 DR (Year of Three Ships Sailing)"
    # unnamed years render without the suffix
    assert "(" not in p.describe(p.parse("9999-Hammer-01"))["friendly"]
```

- [ ] **Step 2: Run it** — expected FAIL: `KeyError: 1492` (placeholder dict is empty).

- [ ] **Step 3: Scrape and generate.** Write the scraper to the scratchpad directory and run it with the backend venv Python. Each wiki year page (`1492 DR`) opens with `{{Roll of years|1492|Three Ships Sailing}}`; batch 50 titles per API request:

```python
# scrape_roll_of_years.py — one-off generator; do NOT commit this file.
import json
import re
import time
import urllib.parse
import urllib.request

API = "https://forgottenrealms.fandom.com/api.php"
PAT = re.compile(r"\{\{Roll of years\|\s*-?\d+\s*\|([^}|]+)")
OUT = r"C:\Users\charl\github\grimoire\.worktrees\new-calendars\backend\src\grimoire\store\calendars\harptos_years.py"

names: dict[int, str] = {}
for start in range(1, 1601, 50):
    titles = "|".join(f"{y} DR" for y in range(start, min(start + 50, 1601)))
    q = urllib.parse.urlencode({"action": "query", "prop": "revisions",
                                "rvprop": "content", "rvslots": "main",
                                "format": "json", "titles": titles})
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": "Mozilla/5.0"})
    data = json.load(urllib.request.urlopen(req))
    for page in data["query"]["pages"].values():
        m = re.match(r"^(\d+) DR$", page.get("title", ""))
        revs = page.get("revisions")
        if not (m and revs):
            continue
        found = PAT.search(revs[0]["slots"]["main"]["*"])
        if found and found.group(1).strip():
            names[int(m.group(1))] = "Year of " + found.group(1).strip()
    print(f"{start}: {len(names)} names so far")
    time.sleep(0.5)

with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    f.write('"""Roll of Years: DR year -> named year. Generated from the Forgotten\n'
            'Realms wiki (scrape_roll_of_years.py, 2026-07-05); do not hand-edit."""\n\n'
            "YEAR_NAMES: dict[int, str] = {\n")
    for y in sorted(names):
        f.write(f"    {y}: {names[y]!r},\n")
    f.write("}\n")
print(f"wrote {len(names)} year names")
```

Run: `/c/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe <scratchpad>/scrape_roll_of_years.py`
Expected: `wrote ~1500 year names` (most of 1–1600 are named). Spot-check the generated file for `1492: 'Year of Three Ships Sailing'` and `1372: 'Year of Wild Magic'`. If the API rate-limits, increase the sleep and re-run — the script is idempotent.

- [ ] **Step 4: Run** `pytest backend/tests/test_calendar_harptos.py -v` then `pytest backend -q`. Expected: all pass.

- [ ] **Step 5: Commit** (the data module only — not the scraper)

```bash
git add backend/src/grimoire/store/calendars/harptos_years.py backend/tests/test_calendar_harptos.py
git commit -m "feat(calendars): roll of years data for harptos friendly dates"
```

---

### Task 8: Harptos holidays + rule validation

**Files:**
- Modify: `backend/src/grimoire/store/calendars/harptos.py`
- Test: `backend/tests/test_calendar_harptos.py`

**Interfaces:**
- Produces: `holidays()` = five festivals + Shieldmeet + four solar observances + custom fixed rules; base `validate_rule` (fixed-only) works via `RULE_REFERENCE_YEAR = 4`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_builtin_holidays_and_customs():
    p = get_provider(har())
    year_start, year_end = p.parse("1492-Hammer-01"), p.parse("1492-Nightal-30")
    names = [h["name"] for h in p.holidays(year_start, year_end)]
    for expected in ("Midwinter", "Greengrass", "Midsummer", "Shieldmeet",
                     "Highharvestide", "Feast of the Moon", "Spring Equinox",
                     "Summer Solstice", "Autumn Equinox", "Winter Solstice"):
        assert expected in names
    assert "Shieldmeet" not in [h["name"] for h in
                                get_provider(har()).holidays(p.parse("1491-Hammer-01"),
                                                             p.parse("1491-Nightal-30"))]
    custom = get_provider(har(custom=[{"name": "Founders' Day", "month": "Uktar", "day": 3}]))
    day = custom.parse("1492-Uktar-03")
    assert any(h["name"] == "Founders' Day" for h in custom.holidays(day, day))


def test_validate_rule_harptos():
    p = get_provider(har())
    p.validate_rule({"name": "OK", "month": "Uktar", "day": 3})
    p.validate_rule({"name": "OK", "month": "Shieldmeet", "day": 1})
    for bad in ({"name": "X", "month": "Uktar", "day": 31},
                {"name": "X", "month": "Midsummer", "day": 2},
                {"name": "X", "month": "Floof", "day": 1},
                {"name": "X", "month": "Uktar", "nth": 1, "weekday": 0}):
        with pytest.raises(CalendarError):
            p.validate_rule(bad)
```

- [ ] **Step 2: Run them** — expected FAIL (holidays returns `[]`).

- [ ] **Step 3: Implement** — in `harptos.py`, add above the class:

```python
_OBSERVANCES = [("Spring Equinox", "Ches", 19), ("Summer Solstice", "Kythorn", 20),
                ("Autumn Equinox", "Eleint", 21), ("Winter Solstice", "Nightal", 20)]
```

and replace the `holidays` stub:

```python
    def holidays(self, start_fixed: int, end_fixed: int) -> list[dict]:
        out: list[dict] = []
        y0 = self.describe(start_fixed)["year"]
        y1 = self.describe(end_fixed)["year"]
        for y in range(y0, y1 + 1):
            for _i, key, name, days in _year_entries(y):
                if days == 1:
                    f = _days_before_year(y) + self._offset_of(y, key) + 1
                    if start_fixed <= f <= end_fixed:
                        out.append({"name": name, "fixed": f})
            for name, key, d in _OBSERVANCES:
                f = self.parse(f"{y}-{key}-{d:02d}")
                if start_fixed <= f <= end_fixed:
                    out.append({"name": name, "fixed": f})
        out.extend(self._custom_fixed(start_fixed, end_fixed))
        out.sort(key=lambda h: h["fixed"])
        return out

    def _offset_of(self, year: int, key: str) -> int:
        offset = 0
        for _i, k, _n, days in _year_entries(year):
            if k == key:
                return offset
            offset += days
        raise CalendarError(f"unknown harptos month: {key!r}")
```

(`validate_rule` needs no code: the base fixed-rule implementation from Task 3 + `RULE_REFERENCE_YEAR = 4` covers Shieldmeet, and the base rejects nth-weekday rules by requiring `"day"`.)

- [ ] **Step 4: Run** `pytest backend/tests/test_calendar_harptos.py -v` then `pytest backend -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/calendars/harptos.py backend/tests/test_calendar_harptos.py
git commit -m "feat(calendars): harptos festivals, solar observances, custom rules"
```

---

### Task 9: Months endpoints + non-Gregorian integration

**Files:**
- Modify: `backend/src/grimoire/routes.py` (two GET routes next to `get_calendar_config` at `routes.py:1169`)
- Test: `backend/tests/test_routes.py` (append; follow the file's existing client/fixture pattern — it uses a `client` fixture and `monkeypatch.setenv("GRIMOIRE_HOME", ...)`; copy the setup of the existing calendar-config route tests)

**Interfaces:**
- Produces: `GET /api/campaigns/{cid}/calendar/months?year=N` and `GET /api/worlds/{wid}/calendar/months?year=N` → `{"months": [{key, name, days}]}`; 404 unknown id, 400 on `CalendarError`, 422 non-integer year (FastAPI).
- Consumes: `store.worlds.world_root(wid)` / `world_meta_path(wid)`, `store.campaigns.campaign_root(cid)` / `campaign_meta_path(cid)`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_routes.py`, reusing its existing helpers for creating a world + campaign (mirror the neighboring calendar-config tests' setup):

```python
def test_calendar_months_campaign_and_world(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = client.post("/api/worlds", json={"name": "Faerun"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "C", "world": wid}).json()["id"]
    # default gregorian
    r = client.get(f"/api/campaigns/{cid}/calendar/months", params={"year": 2024})
    assert r.status_code == 200
    assert r.json()["months"][1] == {"key": "02", "name": "February", "days": 29}
    # switch the campaign to harptos and re-read
    cfg = client.get(f"/api/campaigns/{cid}/calendar").json()
    cfg["primary"]["provider"] = "harptos"
    assert client.put(f"/api/campaigns/{cid}/calendar", json=cfg).status_code == 200
    months = client.get(f"/api/campaigns/{cid}/calendar/months", params={"year": 1492}).json()["months"]
    assert len(months) == 18 and months[10]["key"] == "Shieldmeet"
    # world-level (defaults to gregorian)
    r = client.get(f"/api/worlds/{wid}/calendar/months", params={"year": 2026})
    assert r.status_code == 200 and len(r.json()["months"]) == 12
    # errors
    assert client.get("/api/campaigns/nope/calendar/months", params={"year": 2026}).status_code == 404
    assert client.get(f"/api/campaigns/{cid}/calendar/months", params={"year": "abc"}).status_code == 422


def test_scene_datetime_with_harptos_primary(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = client.post("/api/worlds", json={"name": "Faerun"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "C", "world": wid}).json()["id"]
    cfg = client.get(f"/api/campaigns/{cid}/calendar").json()
    cfg["primary"]["provider"] = "harptos"
    client.put(f"/api/campaigns/{cid}/calendar", json=cfg)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime",
                   json={"datetime": "1492-mirtul-05"})
    assert r.status_code == 200
    assert r.json()["friendly"].startswith("5 Mirtul, 1492 DR")
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()
    assert got["current"]["native"] == "1492-Mirtul-05"   # normalized casing
    assert got["history"] == ["1492-Mirtul-05"]
```

**Note:** adapt the world/campaign/scene creation calls to the exact endpoints used by neighboring tests in `test_routes.py` (e.g., if world creation requires more fields, copy what `test_routes.py` already does). The assertions are the contract; the setup mirrors the file.

- [ ] **Step 2: Run them** — `pytest backend/tests/test_routes.py -q -k calendar_months or harptos_primary`
Expected: FAIL — 404 on the months URLs.

- [ ] **Step 3: Implement** — in `routes.py`, directly after `put_calendar_config` (line ~1186):

```python
@router.get("/campaigns/{cid}/calendar/months")
def get_calendar_months(cid: str, year: int):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.calendars.read_calendar(store.campaigns.campaign_root(cid))
    try:
        return {"months": store.calendars.get_provider(cfg["primary"]).months(year)}
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/worlds/{wid}/calendar/months")
def get_world_calendar_months(wid: str, year: int):
    if not store.worlds.world_meta_path(wid).exists():
        raise HTTPException(status_code=404, detail="world not found")
    cfg = store.calendars.read_calendar(store.worlds.world_root(wid))
    try:
        return {"months": store.calendars.get_provider(cfg["primary"]).months(year)}
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 4: Run** the two tests, then `pytest backend -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(backend): calendar months endpoints for campaigns and worlds"
```

---

### Task 10: Campaign creation accepts a calendar provider

**Files:**
- Modify: `backend/src/grimoire/routes.py:45-48` (`NewCampaign`), `routes.py:1189-1194` (`post_campaign`)
- Modify: `backend/src/grimoire/store/campaigns.py:67,104-109` (`create_campaign`)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Produces: `POST /api/campaigns` body gains optional `calendar` (provider id); `create_campaign(name, world_id, region=None, calendar=None)`. Given `calendar`, the campaign's primary provider is set and `confirmed` becomes `True`; unknown provider → 400.

- [ ] **Step 1: Write the failing test:**

```python
def test_create_campaign_with_calendar_provider(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = client.post("/api/worlds", json={"name": "Faerun"}).json()["id"]
    cid = client.post("/api/campaigns",
                      json={"name": "FR", "world": wid, "calendar": "harptos"}).json()["id"]
    cfg = client.get(f"/api/campaigns/{cid}/calendar").json()
    assert cfg["primary"]["provider"] == "harptos"
    assert cfg["confirmed"] is True
    r = client.post("/api/campaigns", json={"name": "X", "world": wid, "calendar": "bogus"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run it** — expected FAIL (unknown field ignored; provider stays `gregorian`).

- [ ] **Step 3: Implement.** `NewCampaign`:

```python
class NewCampaign(BaseModel):
    name: str
    world: str
    region: str | None = None
    calendar: str | None = None
```

`post_campaign`:

```python
@router.post("/campaigns")
def post_campaign(body: NewCampaign):
    try:
        return {"id": store.campaigns.create_campaign(body.name, body.world,
                                                      body.region, body.calendar)}
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=400, detail="world not found")
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

`create_campaign` — signature `def create_campaign(name: str, world_id: str, region: str | None = None, calendar: str | None = None) -> str:` and replace the region block at `campaigns.py:105-108`:

```python
    if region is not None or calendar is not None:
        cfg = calendars.read_calendar(root)
        if calendar is not None:
            cfg["primary"]["provider"] = calendar
            cfg["confirmed"] = True   # an explicit wizard choice
        if region is not None:
            cfg["primary"]["region"] = region
        calendars.validate_calendar(cfg)   # unknown provider -> CalendarError
        calendars.write_calendar(root, cfg)
```

**Caution:** `validate_calendar` must run **before** `write_calendar`, and the campaign directory already exists at this point — an invalid provider leaves a campaign with a default calendar; that's acceptable (the POST 400s and the wizard retries). Do not reorder campaign creation.

- [ ] **Step 4: Run** `pytest backend -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/src/grimoire/store/campaigns.py backend/tests/test_routes.py
git commit -m "feat(backend): campaign creation accepts a calendar provider"
```

---

### Task 11: Frontend API client additions

**Files:**
- Modify: `frontend/src/api/client.ts` (types near line 172; methods near lines 281 and 501)

**Interfaces:**
- Produces: `CalendarMonth = {key, name, days}`; `CalendarScope = {kind: "campaign" | "world"; id: string}`; `api.getCalendarMonths(scope, year)`; `api.createCampaign(name, world, region?, calendar?)`; `splitNativeDate(native)` helper exported from `client.ts`.

- [ ] **Step 1: Implement** (types + client are exercised by component tests in Tasks 12–15; no standalone test file). Next to `CalendarBlock` (line ~172):

```ts
export type CalendarMonth = { key: string; name: string; days: number };
export type CalendarScope = { kind: "campaign" | "world"; id: string };

/** Split a native datetime on its trailing Thh:mm only — month tokens may contain T. */
export function splitNativeDate(native: string): { date: string; time: string | null } {
  const m = native.match(/T(\d{1,2}:\d{2})$/);
  return m ? { date: native.slice(0, m.index), time: m[1] } : { date: native, time: null };
}
```

Replace `createCampaign` (line ~281):

```ts
  createCampaign: (name: string, world: string, region?: string, calendar?: string) =>
    request<{ id: string }>("POST", "/api/campaigns",
      { name, world, ...(region ? { region } : {}), ...(calendar ? { calendar } : {}) }),
```

Next to `getCalendarConfig` (line ~501):

```ts
  getCalendarMonths: (scope: CalendarScope, year: number) =>
    request<{ months: CalendarMonth[] }>(
      "GET",
      `/api/${scope.kind === "campaign" ? "campaigns" : "worlds"}/${scope.id}/calendar/months?year=${year}`),
```

- [ ] **Step 2: Typecheck** — from `frontend/`: `npx tsc -b`. Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(frontend): calendar months API, calendar param on campaign create"
```

---

### Task 12: `CalendarDatePicker` component

**Files:**
- Create: `frontend/src/components/CalendarDatePicker.tsx`
- Create: `frontend/src/components/CalendarDatePicker.test.tsx`

**Interfaces:**
- Produces: `<CalendarDatePicker scope={CalendarScope} value={string} onChange={(native: string) => void} ariaLabel={string} />`. Emits a full native date (`1492-Mirtul-05`) when year+month+day are all chosen, else `""`. Controls carry aria-labels `` `${ariaLabel} year` ``, `` `${ariaLabel} month` ``, `` `${ariaLabel} day` ``.
- Consumes: `api.getCalendarMonths`, `splitNativeDate`, `CalendarMonth`, `CalendarScope` (Task 11).

- [ ] **Step 1: Write the failing tests** — create `CalendarDatePicker.test.tsx` (mock pattern per the project's existing component tests — `vi.mock("../api/client")` with `globals: true`):

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { api } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: { getCalendarMonths: vi.fn() } };
});

const HARPTOS_1492 = [
  { key: "Hammer", name: "Hammer", days: 30 },
  { key: "Midwinter", name: "Midwinter", days: 1 },
  { key: "Mirtul", name: "Mirtul", days: 30 },
];

test("year entry loads months; picking month+day emits the native date", async () => {
  (api.getCalendarMonths as any).mockResolvedValue({ months: HARPTOS_1492 });
  const onChange = vi.fn();
  render(<CalendarDatePicker scope={{ kind: "campaign", id: "c1" }} value=""
                             onChange={onChange} ariaLabel="Scene date" />);
  expect(screen.getByLabelText("Scene date month")).toBeDisabled();
  await userEvent.type(screen.getByLabelText("Scene date year"), "1492");
  await waitFor(() => expect(api.getCalendarMonths).toHaveBeenCalledWith(
    { kind: "campaign", id: "c1" }, 1492));
  await userEvent.selectOptions(await screen.findByLabelText("Scene date month"), "Mirtul");
  await userEvent.selectOptions(screen.getByLabelText("Scene date day"), "5");
  expect(onChange).toHaveBeenLastCalledWith("1492-Mirtul-05");
});

test("festival pseudo-months offer a single day", async () => {
  (api.getCalendarMonths as any).mockResolvedValue({ months: HARPTOS_1492 });
  const onChange = vi.fn();
  render(<CalendarDatePicker scope={{ kind: "campaign", id: "c1" }} value=""
                             onChange={onChange} ariaLabel="Scene date" />);
  await userEvent.type(screen.getByLabelText("Scene date year"), "1492");
  await userEvent.selectOptions(await screen.findByLabelText("Scene date month"), "Midwinter");
  const day = screen.getByLabelText("Scene date day") as HTMLSelectElement;
  expect([...day.options].map(o => o.value)).toEqual(["", "1"]);
});

test("an existing value pre-fills the controls", async () => {
  (api.getCalendarMonths as any).mockResolvedValue({ months: HARPTOS_1492 });
  render(<CalendarDatePicker scope={{ kind: "campaign", id: "c1" }} value="1492-Mirtul-05"
                             onChange={() => {}} ariaLabel="Scene date" />);
  expect(screen.getByLabelText("Scene date year")).toHaveValue(1492);
  await waitFor(() =>
    expect(screen.getByLabelText("Scene date month")).toHaveValue("Mirtul"));
  expect(screen.getByLabelText("Scene date day")).toHaveValue("5");
});
```

- [ ] **Step 2: Run them** — from `frontend/`: `npx vitest run src/components/CalendarDatePicker.test.tsx`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement** — create `CalendarDatePicker.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api, splitNativeDate, type CalendarMonth, type CalendarScope } from "../api/client";

// "1492-Mirtul-05" -> ["1492", "Mirtul", "5"]; tolerates negative years.
function parseParts(dateOnly: string): [string, string, string] {
  const m = dateOnly.match(/^(-?\d+)-(.+)-(\d{1,2})$/);
  return m ? [m[1], m[2], String(parseInt(m[3], 10))] : ["", "", ""];
}

export function CalendarDatePicker({ scope, value, onChange, ariaLabel }: {
  scope: CalendarScope; value: string; onChange: (native: string) => void; ariaLabel: string;
}) {
  const [initYear, initMonth, initDay] = parseParts(splitNativeDate(value).date);
  const [year, setYear] = useState(initYear);
  const [month, setMonth] = useState(initMonth);
  const [day, setDay] = useState(initDay);
  const [months, setMonths] = useState<CalendarMonth[]>([]);

  useEffect(() => {
    const n = parseInt(year, 10);
    if (isNaN(n)) { setMonths([]); return; }
    let stale = false;
    api.getCalendarMonths(scope, n)
      .then((r) => { if (!stale) setMonths(r.months); })
      .catch(() => { if (!stale) setMonths([]); });
    return () => { stale = true; };
  }, [scope.kind, scope.id, year]);

  // A year change can invalidate the month (Shieldmeet, Adar I/II).
  useEffect(() => {
    if (months.length && month && !months.some((m) => m.key === month)) {
      setMonth(""); setDay(""); onChange("");
    }
  }, [months]);

  function emit(y: string, mKey: string, d: string) {
    const n = parseInt(y, 10);
    if (!isNaN(n) && mKey && d) onChange(`${y}-${mKey}-${d.padStart(2, "0")}`);
    else onChange("");
  }

  const entry = months.find((m) => m.key === month);
  const dayCount = entry?.days ?? 0;
  return (
    <span className="date-picker">
      <input type="number" aria-label={`${ariaLabel} year`} value={year}
             onChange={(e) => { setYear(e.target.value); emit(e.target.value, month, day); }} />
      <select aria-label={`${ariaLabel} month`} value={month} disabled={!months.length}
              onChange={(e) => { setMonth(e.target.value); setDay(""); onChange(""); }}>
        <option value="">— month —</option>
        {months.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
      </select>
      <select aria-label={`${ariaLabel} day`} value={day} disabled={!entry}
              onChange={(e) => { setDay(e.target.value); emit(year, month, e.target.value); }}>
        <option value="">—</option>
        {Array.from({ length: dayCount }, (_, i) => String(i + 1)).map((d) =>
          <option key={d} value={d}>{d}</option>)}
      </select>
    </span>
  );
}
```

- [ ] **Step 4: Run** the component tests, then from `frontend/`: `npx tsc -b`. Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CalendarDatePicker.tsx frontend/src/components/CalendarDatePicker.test.tsx
git commit -m "feat(frontend): CalendarDatePicker — provider-aware structured date entry"
```

---

### Task 13: Scene date entry uses the picker; calendar choice lists new providers

**Files:**
- Modify: `frontend/src/components/CastPanel.tsx:192-198`
- Modify: `frontend/src/components/SceneInspector.tsx:10-12` (CALENDARS), `:202-206`, `:220-226`
- Test: `frontend/src/components/CastPanel.test.tsx`, `frontend/src/components/SceneInspector.test.tsx` (update existing date-input tests; mock `getCalendarMonths`)

**Interfaces:**
- Consumes: `CalendarDatePicker` (Task 12). The `dateInput` state in both components already holds a native string; only the input element changes.

- [ ] **Step 1: Update the tests.** In both test files, find the tests that type into `Scene date` (`type="date"` input) and rework them to the picker flow (mock `api.getCalendarMonths` to return 12 Gregorian months, e.g. `{key: "06", name: "June", days: 30}`…; select year/month/day; assert `api.setSceneDatetime` is called with `"2026-06-29"`). Add to the `SceneInspector` mock-API object `getCalendarMonths: vi.fn().mockResolvedValue({ months: GREG_MONTHS })`. Keep every other assertion intact.

- [ ] **Step 2: Run them** — from `frontend/`: `npx vitest run src/components/CastPanel.test.tsx src/components/SceneInspector.test.tsx`
Expected: FAIL (still the old input).

- [ ] **Step 3: Implement.** In `CastPanel.tsx`, add the import and replace lines 193–194:

```tsx
import { CalendarDatePicker } from "./CalendarDatePicker";
```

```tsx
            <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={dateInput}
                                onChange={setDateInput} ariaLabel="Scene date" />
```

In `SceneInspector.tsx`, same replacement at both `type="date"` sites (lines 203–204 and 222–223), and grow the calendar list (line 12):

```tsx
const CALENDARS = [
  { id: "gregorian", name: "Gregorian" },
  { id: "hebrew", name: "Hebrew" },
  { id: "harptos", name: "Calendar of Harptos" },
];
```

- [ ] **Step 4: Run** both test files, then the full `npx vitest run` and `npx tsc -b`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CastPanel.tsx frontend/src/components/SceneInspector.tsx frontend/src/components/CastPanel.test.tsx frontend/src/components/SceneInspector.test.tsx
git commit -m "feat(frontend): scene date entry via CalendarDatePicker; new calendars selectable"
```

---

### Task 14: Birthdate entry uses the picker

**Files:**
- Modify: `frontend/src/components/PCEditor.tsx:274-275`
- Modify: `frontend/src/components/CharacterEditor.tsx:1104-1105`
- Test: `frontend/src/components/PCEditor.test.tsx:72-80`, `frontend/src/components/CharacterEditor.test.tsx:127-…`

**Interfaces:**
- Consumes: `CalendarDatePicker`. PCEditor already receives `scope: EntityScope` (same `{kind, id}` shape as `CalendarScope` — `kind` is `"world" | "campaign"`); CharacterEditor receives the world id (`wid` prop or equivalent — check the component's props) and uses `scope={{ kind: "world", id: wid }}`.

- [ ] **Step 1: Update the tests.** In `PCEditor.test.tsx` (the `editing the birthdate saves it on the persona` test) and `CharacterEditor.test.tsx` (`editing the birthdate persists it on the character`), replace the `type="date"` interaction with the picker flow (mock `getCalendarMonths` with Gregorian months; select 1990 / June / 29; keep the existing save assertions — `birthdate: "1990-06-29"`).

- [ ] **Step 2: Run them** — expected FAIL.

- [ ] **Step 3: Implement.** `PCEditor.tsx` (line ~274):

```tsx
              <CalendarDatePicker scope={scope} value={persona.birthdate ?? ""}
                                  onChange={(v) => setPersona({ ...persona, birthdate: v })}
                                  ariaLabel="Birthdate" />
```

`CharacterEditor.tsx` (line ~1104) — the editor is world-scoped:

```tsx
              <CalendarDatePicker scope={{ kind: "world", id: wid }} value={birthdate}
                                  onChange={setBirthdate} ariaLabel="Birthdate" />
```

(Verify the world-id prop name in `CharacterEditor.tsx`'s signature and use it; add the `CalendarDatePicker` import to both files.)

- [ ] **Step 4: Run** both test files, then full `npx vitest run` + `npx tsc -b`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PCEditor.tsx frontend/src/components/CharacterEditor.tsx frontend/src/components/PCEditor.test.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat(frontend): birthdate entry via CalendarDatePicker"
```

---

### Task 15: Calendar config UI + wizard calendar step

**Files:**
- Modify: `frontend/src/components/CalendarConfig.tsx` (full rewrite of the form body)
- Modify: `frontend/src/routes/CampaignWizard.tsx:21,65-70,163-184`
- Test: `frontend/src/components/CalendarConfig.test.tsx`, `frontend/src/routes/CampaignWizard.test.tsx`

**Interfaces:**
- Consumes: `api.setCalendarConfig` (unchanged), `api.createCampaign(name, world, region?, calendar?)` (Task 11).
- Produces: provider `<select aria-label="Calendar">` with options `gregorian | hebrew | harptos`; Gregorian keeps `aria-label="Holidays region"`; Hebrew shows `aria-label="Observance"` (`""` Diaspora / `"IL"` Israel); Harptos shows no second control.

- [ ] **Step 1: Update/add the tests.** `CalendarConfig.test.tsx`: keep the existing region test for Gregorian; add — selecting provider `hebrew` shows the Observance select and saving PUTs `primary.provider === "hebrew"`, `primary.region === "IL"` when Israel chosen; selecting `harptos` hides both region and observance and saves `primary.provider === "harptos"`. `CampaignWizard.test.tsx`: add — choosing "Calendar of Harptos" in the wizard hides the Holidays select and `createCampaign` is called with `("FR", wid, undefined, "harptos")`; choosing Hebrew + Israel calls `("H", wid, "IL", "hebrew")`; default Gregorian path stays `("G", wid, "US", "gregorian")` — check the existing wizard test's exact argument style and keep unrelated assertions.

- [ ] **Step 2: Run them** — expected FAIL.

- [ ] **Step 3: Implement.** `CalendarConfig.tsx` — replace the component body (keep load/save/error scaffolding):

```tsx
const PROVIDERS = [
  { id: "gregorian", name: "Gregorian" },
  { id: "hebrew", name: "Hebrew" },
  { id: "harptos", name: "Calendar of Harptos" },
];
const REGIONS = ["US", "GB", "CA", "AU", "IL", ""];

// inside the component, replacing the single region <label> block:
  function setPrimary(patch: Partial<Cfg["primary"]>) {
    setSaved(false);
    setCfg({ ...cfg!, primary: { ...cfg!.primary, ...patch } });
  }

  return (
    <div className="calendar-config">
      {error && <div className="banner">{error}</div>}
      <label>
        Calendar
        <select aria-label="Calendar" value={cfg.primary.provider}
                onChange={(e) => setPrimary({ provider: e.target.value, region: e.target.value === "gregorian" ? "US" : "" })}>
          {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      {cfg.primary.provider === "gregorian" && (
        <label>
          Holidays region
          <select aria-label="Holidays region" value={cfg.primary.region}
                  onChange={(e) => setPrimary({ region: e.target.value })}>
            {REGIONS.map((r) => <option key={r || "none"} value={r}>{r || "None"}</option>)}
          </select>
        </label>
      )}
      {cfg.primary.provider === "hebrew" && (
        <label>
          Observance
          <select aria-label="Observance" value={cfg.primary.region}
                  onChange={(e) => setPrimary({ region: e.target.value })}>
            <option value="">Diaspora</option>
            <option value="IL">Israel</option>
          </select>
        </label>
      )}
      <button className="primary" onClick={save}>Save</button>
      {saved && <span className="field-hint">Saved.</span>}
    </div>
  );
```

`CampaignWizard.tsx` — add state `const [calendar, setCalendar] = useState("gregorian");` next to `region` (line 21); pass it in `commit()` (line 69):

```tsx
      const { id: cid } = await api.createCampaign(name.trim(), world, region || undefined, calendar);
```

(For Harptos pass no region: change the `region || undefined` expression to `calendar === "harptos" ? undefined : region || undefined`.) Replace the static calendar `<select>` (lines 166–169):

```tsx
              <select id="wiz-calendar" aria-label="Calendar" value={calendar}
                      onChange={(e) => { setCalendar(e.target.value);
                                         setRegion(e.target.value === "gregorian" ? "US" : ""); }}>
                <option value="gregorian">Gregorian</option>
                <option value="hebrew">Hebrew</option>
                <option value="harptos">Calendar of Harptos</option>
              </select>
              <div className="field-caption">The campaign's primary calendar</div>
```

and make the Holidays field conditional — wrap the existing region `<div className="field">` in `{calendar === "gregorian" && (...)}`, and add after it:

```tsx
            {calendar === "hebrew" && (
              <div className="field">
                <label htmlFor="wiz-observance">Observance</label>
                <select id="wiz-observance" aria-label="Observance" value={region}
                        onChange={(e) => setRegion(e.target.value)}>
                  <option value="">Diaspora</option>
                  <option value="IL">Israel</option>
                </select>
                <div className="field-caption">Israeli or diaspora holiday scheme</div>
              </div>
            )}
```

- [ ] **Step 4: Run** the two test files, then full `npx vitest run` + `npx tsc -b`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CalendarConfig.tsx frontend/src/components/CalendarConfig.test.tsx frontend/src/routes/CampaignWizard.tsx frontend/src/routes/CampaignWizard.test.tsx
git commit -m "feat(frontend): provider picker in calendar config and campaign wizard"
```

---

### Task 16: Full verification + context-block integration test

**Files:**
- Test: `backend/tests/test_calendars.py` (one cross-provider context check)

**Interfaces:** none new — final gate.

- [ ] **Step 1: Add a `# Today`-path test** — append to `backend/tests/test_calendars.py`:

```python
def test_today_facts_with_hebrew_and_harptos_primaries():
    heb_cfg = {"primary": {"provider": "hebrew", "region": "", "custom_holidays": [],
                           "anchor": None}, "secondary": None}
    facts = today_facts(heb_cfg, "5786-Kislev-25")
    assert facts["friendly"] == "25 Kislev 5786"
    assert any("Chanuka" in n for n in facts["holidays_today"])

    har_cfg = {"primary": {"provider": "harptos", "region": "", "custom_holidays": [],
                           "anchor": None}, "secondary": None}
    facts = today_facts(har_cfg, "1492-Midsummer-01")
    assert facts["friendly"].startswith("Midsummer, 1492 DR")
    assert "Midsummer" in facts["holidays_today"]
    assert facts["upcoming"] == {"name": "Shieldmeet", "in_days": 1}
```

- [ ] **Step 2: Run everything:**

```
pytest backend -q                      # expected: all pass, 0 failures
cd frontend && npx vitest run          # expected: all pass
npx tsc -b                             # expected: clean
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_calendars.py
git commit -m "test(calendars): today_facts coverage for hebrew and harptos primaries"
```

---

## Self-Review Notes (already applied)

- Task 5's fast-day assertion was verified against pyluach directly during planning
  (3 Tishrei 5786 = Thursday 25 Sep 2025, `fast_day()` → "Tzom Gedalia").
- `_custom_fixed` (Task 3) is exercised through Hebrew/Harptos custom-holiday tests (Tasks 5, 8); Gregorian keeps its own custom logic untouched.
- Route-test setup (Task 9/10) mirrors `test_routes.py` conventions rather than inventing new fixtures — the implementer adapts creation calls to the file's existing style.
- The spec's world-level months endpoint and campaign-creation `calendar` param were added to the spec in the same commit as this plan.
