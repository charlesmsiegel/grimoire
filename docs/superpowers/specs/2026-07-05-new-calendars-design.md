# New Calendars: Harptos & Hebrew — Design

**Date:** 2026-07-05
**Status:** Approved, ready for implementation plan

## Problem

The calendar engine (see `2026-06-29-scene-calendar-design.md`) ships exactly one
provider, `gregorian`. Add two more, each a full Python provider module in
`store/calendars/` registered in the existing `REGISTRY`:

1. **Calendar of Harptos** (Forgotten Realms) — pure arithmetic, no dependency.
2. **Hebrew calendar** with the full traditional Jewish holiday set — backed by
   the `pyluach` library.

Both calendars are used as a campaign's **primary calendar only**. No secondary /
anchor / sync work: `anchor` stays an ignored config key, and the fixed-day epoch
for Harptos is internal and arbitrary.

## Decisions (from brainstorming)

1. **Shipped provider modules**, not user-supplied scripts and not a generic
   data-driven engine. Each calendar is its own Python class implementing
   `CalendarProvider`; a generic engine can be extracted later if a second
   fantasy calendar ever lands (YAGNI).
2. **Hebrew math and holidays come from `pyluach`** (pure Python, no transitive
   deps, converts via `date.toordinal()` — exactly our fixed-day axis). We do not
   hand-write lunisolar arithmetic or holiday postponement rules.
3. **Diaspora/Israel observance is configurable**, reusing the config block's
   existing `region` field: `"IL"` → Israel, anything else → diaspora.
4. **Harptos embeds the Roll of Years**: a generated `dict[int, str]` data module
   scraped once from the wiki during implementation and committed as code; the
   year name is appended to friendly dates when known.
5. **Structured date entry** in the frontend: year + month + day dropdowns fed by
   a new backend months endpoint, replacing raw `type="date"` inputs everywhere
   scene dates and birthdates are entered.

## Provider interface additions

### `months(year) -> list[{key, name, days}]` (new abstract method)

The year's months in order, feeding the dropdowns and provider-aware custom-rule
validation. **Contract:** `f"{year}-{key}-{day:02d}"` is a valid native date for
every `1 <= day <= days`.

- Gregorian: keys `"01"…"12"` (composing ISO dates), names January…December,
  correct leap-February.
- Hebrew: name-token keys in **civil order** (Tishrei → Elul), with `Adar1`/
  `Adar2` replacing `Adar` in leap years, and correct 29/30 lengths per year.
- Harptos: 17 entries (18 in leap years) — the 12 months (30 days each)
  interleaved with the festivals as 1-day pseudo-months in calendar position.

### `split_native` fix (latent bug, must fix first)

`base.py` `split_native()` splits date from time on the **first `"T"`**.
`"5786-Tishrei-01"` and `"1492-Tarsakh-05"` contain a capital T and would be
mangled. Fix: split only on a **trailing time pattern** (regex
`T\d{1,2}:\d{2}$`). Backward-compatible with stored Gregorian
`2026-06-29T14:30` strings; no migration. Native month tokens must never match
this pattern, which they can't (they contain no `:`).

## Hebrew provider (`store/calendars/hebrew.py`)

- **Registry id:** `hebrew`. Config: `region` = `"IL"` → Israel observance, else
  diaspora; `custom_holidays` supported (fixed rules only); `anchor` ignored.
- **Native format:** `5786-Kislev-25`. Canonical month tokens (parse is
  case-insensitive): `Tishrei, Cheshvan, Kislev, Tevet, Shevat, Adar, Adar1,
  Adar2, Nisan, Iyar, Sivan, Tammuz, Av, Elul`. Our tokens, our spellings —
  mapped internally to pyluach month numbers. Year numbers are Hebrew years;
  the year increments at Rosh Hashanah.
  - In a leap year, `Adar` in user input is accepted and normalized to `Adar2`
    (the observance month); `Adar1`/`Adar2` are rejected in non-leap years.
- **parse/format:** via `pyluach.dates.HebrewDate` ↔ `to_pydate().toordinal()`.
  Bad month token, day out of range for that month/year, or leap-month misuse
  raise `CalendarError`.
- **describe:** `{year, month (civil position 1-12/13), month_name (token),
  day, weekday_name, weekday_index, friendly}`. Weekday names Sunday–Friday plus
  **Shabbat** (index 0=Sunday … 6=Shabbat). Friendly: `"25 Kislev 5786"`.
- **holidays(start, end):** walk each fixed day in the range; collect
  `festival(israel=…, include_working_days=True)` and `fast_day()` from pyluach.
  Yields the full traditional set: yom tov with diaspora second days, Chol
  HaMoed, Chanukah, Purim/Shushan Purim, the minor fasts with their deferral
  rules, Tu BiShvat, Lag BaOmer, Pesach Sheni. Merge custom fixed rules.
- **age/is_anniversary (override — required):** the base implementation compares
  `(month, day)` tuples, which is wrong for Hebrew because month numbers
  (Nisan=1) don't run in civil-year order across the Tishrei boundary. Compare
  by **position in the civil year** instead. Adar-born in a non-leap year is
  observed in Adar; 30-Cheshvan / 30-Kislev births are observed on the 29th in
  years where the month is short.
- **Dependency:** add `pyluach` to backend requirements.

## Harptos provider (`store/calendars/harptos.py`)

- **Registry id:** `harptos`. Config: `region` ignored, `custom_holidays`
  supported (fixed rules only), `anchor` ignored.
- **Structure:** 12 months × 30 days — Hammer, Alturiak, Ches, Tarsakh, Mirtul,
  Kythorn, Flamerule, Eleasis, Eleint, Marpenoth, Uktar, Nightal — with five
  1-day intercalary festivals: Midwinter (after Hammer), Greengrass (after
  Tarsakh), Midsummer (after Flamerule), Highharvestide (after Eleint), Feast of
  the Moon (after Uktar). **Shieldmeet** follows Midsummer when `DR % 4 == 0`.
  Year length 365, leap 366.
- **Native format:** `1492-Mirtul-05`; festivals are day 01 of a pseudo-month
  (`1492-Midsummer-01`), keeping the uniform three-part shape. Month/festival
  tokens parse case-insensitively.
- **Epoch:** `1 Hammer, 1 DR` = fixed day 1. Internal constant; primaries-only
  means it never aligns with a real calendar. Years before 1 DR are accepted
  arithmetically (year 0, negatives) — no special casing.
- **describe:** `weekday_name` is the tenday position (`"1st day of the
  tenday"` … `"10th day of the tenday"`, `weekday_index` 0–9, computed as
  `(day-1) % 10`); festivals get `weekday_name` `"festival day"` and
  `weekday_index` `None`. Friendly: `"5 Mirtul, 1492 DR (Year of Three Ships
  Sailing)"` — year name appended only when present in the Roll of Years;
  festival friendly: `"Midsummer, 1492 DR (…)"` with no day number.
- **Roll of Years:** `store/calendars/harptos_years.py`, a generated
  `YEAR_NAMES: dict[int, str]` scraped once from the Forgotten Realms wiki
  (coverage roughly 1 DR – 1600 DR where named) by a throwaway script during
  implementation; the data module is committed, the scraper is not.
- **Built-in holidays:** the five festivals + Shieldmeet + Spring Equinox
  (Ches 19), Summer Solstice (Kythorn 20), Autumn Equinox (Eleint 21), Winter
  Solstice (Nightal 20). Merged with custom fixed rules.
- Default `age`/`is_anniversary` work as-is: months and festivals occupy stable
  calendar positions, so `describe`-based month/day comparison is correct.
  (A Shieldmeet birth anniversaries only in leap years — acceptable, like
  Feb 29.)

## Custom holidays go provider-aware

- Fixed rule becomes `{name, month: <month key>, day}` where `month` is a key
  from the provider's `months()` (Gregorian keeps accepting the existing
  integer form — normalized to the zero-padded key). Validation delegates to
  the provider: a new provider hook `validate_rule(rule)` (default
  implementation checks the key exists in `months()` of a reference year and
  the day fits; Hebrew uses a leap reference year so `Adar1`/`Adar2` and day 30
  of variable months validate, Gregorian keeps the Feb-29-allowed behavior).
- The **nth-weekday rule form stays Gregorian-only**; Hebrew and Harptos
  `validate_rule` reject it with a clear `CalendarError`.
- `config.validate_calendar` calls `get_provider(block).validate_rule(rule)`
  instead of the current Gregorian-hardcoded checks.
- Existing stored Gregorian rules (integer `month`) keep working unchanged.

## Routes

- **New:** `GET /api/campaigns/{cid}/calendar/months?year=N` →
  `{"months": [{key, name, days}]}` from the campaign's **primary** provider.
  400 with detail on `CalendarError` / non-integer year.
- Everything else rides existing endpoints: calendar GET/PUT already carries
  `provider`; scene datetime PUT already normalizes via the provider and 400s
  on `CalendarError`.

## Frontend

- **`CalendarDatePicker`** (new shared component): year `<input type="number">`
  + month `<select>` + day `<select>`, populated from
  `api.getCalendarMonths(cid, year)` and re-fetched when the year changes
  (Hebrew leap months and Shieldmeet make month lists year-dependent). Day
  options are 1..days for the selected month; selections compose
  `{year}-{key}-{day padded to 2}`. An optional time field (existing `type="time"`,
  appended as `T hh:mm`) where the caller wants it. Replaces the raw
  `type="date"` input in the scene **When** panel (`CastPanel`) and the
  birthdate inputs in the PC and Character editors.
- **`CalendarConfig.tsx`:** provider `<select>` — Gregorian / Hebrew / Calendar
  of Harptos — plus a conditional second control: holidays-region dropdown
  (Gregorian, as today), Diaspora/Israel dropdown writing `region` `""`/`"IL"`
  (Hebrew), nothing (Harptos).
- **Campaign wizard:** the region step becomes a calendar step with the same
  provider + conditional control pair.
- **`api/client.ts`:** `getCalendarMonths(cid, year)`; `CalendarBlock` type
  unchanged (provider is already a string).

## Testing

### Backend (pytest)
- **split_native fix:** `"5786-Tishrei-01"` / `"1492-Tarsakh-05"` survive with
  and without a `T14:30` suffix; Gregorian datetime strings unchanged.
- **Hebrew:** parse/format round-trip incl. case-insensitive and `Adar`
  normalization in leap years; known conversions (25 Kislev 5786 → 2025-12-15,
  1 Tishrei 5786 → 2025-09-23); 5784 has 13 months with Adar1/Adar2; months()
  lengths per year; weekday incl. Shabbat; holidays: Chanukah, Pesach diaspora
  second day present with `region=""` and absent with `"IL"`, a deferred fast;
  age/anniversary across the Tishrei boundary and Adar-born in non-leap years;
  CalendarError on bad token / day 30 of a 29-day month / Adar1 in non-leap.
- **Harptos:** round-trip incl. festivals; epoch (1 Hammer 1 DR = fixed 1);
  Shieldmeet exists only when DR % 4 == 0 and CalendarError otherwise; year
  lengths 365/366; festival ordering on the fixed axis (Hammer 30 < Midwinter <
  Alturiak 1); tenday weekday and festival describe; friendly with and without
  a Roll-of-Years name; built-in holidays (festivals + solar observances);
  months() 17/18 entries in calendar order.
- **Custom rules:** provider-aware validate (Harptos month key, Hebrew Adar
  rules, Gregorian integer-month back-compat, nth-weekday rejected off-Gregorian).
- **Routes:** months endpoint happy path + 400; scene set_datetime with a
  Harptos-primary campaign appends the transition line with the Harptos friendly.
- **Context:** `# Today` block renders for a Hebrew-primary and Harptos-primary
  campaign (weekday line, holidays today, upcoming, ages).

### Frontend (vitest)
- `CalendarDatePicker`: renders month options from the endpoint, day count
  follows the month, composes the native string, re-fetches on year change.
- `CalendarConfig`: provider picker saves; conditional control per provider.
- Wizard calendar step; When-panel and birthdate tests updated to the picker.

## Out of scope

- Secondary calendars, anchors, and cross-calendar sync for the new providers
  (primaries only; the plumbing exists but gets no UI).
- Moon phases, tenday-vs-week multi-cycles beyond what Harptos needs,
  observational calendars, timezone/sub-minute precision.
- World→campaign calendar sync after create (unchanged).
- User-supplied calendar scripts loaded from the store (rejected: executing
  code from a synced data folder).
- Hebrew date rendering in Hebrew script / gematria year forms (Latin
  transliteration only).
