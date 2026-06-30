# Scene Calendar — Design

**Date:** 2026-06-29
**Status:** Approved, ready for implementation plan

## Problem

Scenes have no notion of *when* they take place. We want a scene to occur on a
**real, correct date** — so that the weekday, the holidays in effect, and every
character's age are **computed facts, never hallucinated by the model**. The
motivating uses are placing holidays correctly and representing birthdays.

The catch: "correct" must hold for more than the Gregorian calendar. The long-term
goal is to represent Gregorian, Hebrew, Islamic, Japanese, and arbitrary
worldbuilt calendars (lunar / solar / lunisolar / other). Only **Gregorian is
implemented now**; the architecture must let the others slot in later without
touching scenes or the context builder.

## Guiding principle

The model never does date arithmetic. The backend computes weekday, holidays, and
ages from a calendar **definition** and injects them as ground truth. Whatever the
calendar, dates reduce to a single **fixed-day integer** so they stay orderable and
arithmetic stays exact across calendars.

## Model context (why the decisions below)

- **Calendars are pluggable engines, not static data.** Hebrew (lunisolar, leap
  *months*), Islamic (pure lunar, ~354-day year), and Japanese (Gregorian
  structure + nengō eras) cannot be captured by a naïve "list of months with fixed
  lengths." The proven unifying model (*Calendrical Calculations*, Reingold &
  Dershowitz) makes every date convertible to/from a single absolute day count and
  treats each calendar as a small provider over that axis. So the model is a
  **provider registry with a fixed-day interchange**, not a data schema of months.
- The **scene-location** feature is the structural template. A scene references its
  setting via an ordered `location_history` frontmatter scalar (last = current),
  injects only the current setting as an always-on block, and records mid-scene
  moves as italic assistant transition lines. Scene dates mirror this exactly with
  `time_history` and a `# Today` block.
- Scenes are markdown files with frontmatter (`title, model, created, updated,
  dismissed, location_history`) plus the transcript. Comma-joined scalars are the
  established shape; a `time_history` scalar fits it.
- Worlds own canon; campaigns **copy-on-create** from a world and may diverge. The
  calendar config follows the same model (copied at create).
- Structured, non-flat state already lives in JSON next to the markdown
  (`appearances.json`). The calendar config (a holiday list isn't a flat scalar)
  uses the same shape: `calendar.json`.

## Decisions

1. **Provider registry, fixed-day interchange (chosen approach).** A calendar is a
   provider object over a fixed-day axis. We ship exactly one provider, `gregorian`,
   backed by Python `datetime` (exact leap years and weekdays) and the maintained
   `holidays` package (floating holidays like US Thanksgiving and Easter computus
   computed correctly). Hebrew/Islamic/Japanese/fantasy are future registry entries.
   The rejected alternative — pure data-driven month/length definitions — cannot
   correctly express the very calendars named (lunisolar, observational, era-based).
2. **Fixed day = proleptic Gregorian ordinal** (`date.toordinal()` /
   `date.fromordinal()`), i.e. a Rata Die day count. Any future provider converts its
   own notation to/from the same integer.
3. **Time of day is optional**, carried alongside the fixed day as minutes after
   midnight. A scene "moment" is `(fixed_day, minutes | None)`. The fixed day is the
   primary ordering key; minutes is the within-day tiebreaker.
4. **Calendar defined on the world, copied into the campaign on creation** (same
   copy-on-create model as lore/locations); the campaign may diverge.
5. **Holidays = library + custom.** The `holidays` package keyed to a configured
   region, plus user-authored custom holidays (fixed `month/day` or
   nth-weekday-of-month). Default region `US`, **chosen in the campaign-creation
   wizard**; editable afterward.
6. **A scene is date-less until set** (mirrors location). No auto-default from prior
   scenes or real "today".
7. **A scene carries date + optional time-of-day and can advance mid-scene.** Each
   advance appends an italic assistant transition line and updates the injected
   "now". No monotonic-forward enforcement (flashbacks/retcons remain possible).
8. **Birthdays are stored on cast and ages are computed.** Each present character /
   player persona may carry a birth date; the backend computes exact age as of the
   scene's current date and flags "today is X's birthday". Anniversaries reuse the
   same machinery.
9. **Optional anchor + a synchronized secondary calendar.** A calendar config may
   carry an optional **anchor** that pins an otherwise free-floating (fantasy)
   calendar onto the fixed-day axis; canonical providers (Gregorian/Hebrew/Islamic)
   ignore it. A campaign has a **primary** and an **optional secondary** calendar;
   the scene stores one moment in the primary, and the secondary is a second
   rendering of the same fixed day (so they cannot drift). **Holidays from all
   configured calendars merge into one list** — the explicit reason for supporting
   two calendars is natural multi-tradition holiday coverage.

## The calendar engine

### Fixed-day axis and provider interface

`store/calendars/` (new package). A `CalendarProvider` is bound to its config
(region + optional anchor) at construction and implements:

| method | purpose |
|---|---|
| `parse(native: str) -> int` | native date string → fixed day; raises `CalendarError` on bad input |
| `format(fixed: int) -> str` | fixed day → canonical native string (round-trips `parse`) |
| `describe(fixed: int) -> dict` | `{year, month, month_name, day, weekday_name, weekday_index, friendly}` |
| `holidays(start_fixed, end_fixed) -> list[{name, fixed}]` | observances in a fixed-day range (library region + custom) |
| `age(birth_fixed, asof_fixed) -> int` | full years, month/day aware |
| `is_anniversary(birth_fixed, asof_fixed) -> bool` | same month + day |

`age` / `is_anniversary` have default implementations in the base (pure arithmetic
via `describe`), overridable per provider.

A module-level `REGISTRY: dict[str, type[CalendarProvider]]` and
`get_provider(config) -> CalendarProvider`. `config.provider` selects the class;
the instance is bound to `config.region`, `config.custom_holidays`, and
`config.anchor`. Unknown provider id ⇒ `CalendarError` (callers fall back to
`gregorian`). The **only shipped provider is `gregorian`**.

### Anchors and synchronization

Canonical providers know their fixed-day correspondence mathematically, so the
anchor is ignored. An **anchorable** (fantasy / data-driven) provider's
day-numbering is arbitrary; its anchor positions it:

```json
"anchor": {"native": "<date in this calendar's notation>", "gregorian": "2026-06-29"}
```

The `gregorian` side converts to a fixed day (always known), pinning
`native ⇄ fixed`. From that one pinning the calendar advances in lockstep with
every other. This is the user-facing "pick one day and say what it is on both".

The **anchor-aware interface and dual rendering are built now** even though only
Gregorian ships; a degenerate "Gregorian-as-secondary" exercises the plumbing in
tests. A genuinely useful second calendar waits on a second provider.

## Calendar config & scoping

`<world>/calendar.json` (copied to `<campaign>/calendar.json` on create). `primary`
is required; `secondary` is optional (absent ⇒ single-calendar, unchanged behavior).
Each calendar block: `provider`, `region`, `custom_holidays`, optional `anchor`.

```json
{
  "primary":   {"provider": "gregorian", "region": "US", "custom_holidays": [], "anchor": null},
  "secondary": {"provider": "hebrew", "region": "IL", "custom_holidays": [],
                "anchor": {"native": "5786-10-14", "gregorian": "2026-06-29"}}
}
```

- `custom_holidays`: `{name, month, day}` (fixed) or `{name, month, nth, weekday}`
  (nth weekday of month; `weekday` 0–6).
- Missing file ⇒ defaults (`gregorian` / `US` / no custom / no secondary).
- Syncing world→campaign **after** create is out of scope (copy at create only).

## Scene moment data model

Scene frontmatter gains **`time_history`**: a comma-joined, ordered list of native
datetime strings in the **primary** calendar; the **last is current**. Gregorian
entries are ISO (`2026-06-29` or `2026-06-29T14:30`; commas never appear).

`store/scenes.py`:
- `get_time_history(cid, sid) -> list[str]` — missing scene ⇒ `[]` (mirrors
  `get_location_history`).
- `set_datetime(cid, sid, native) -> dict` — resolves the primary provider from the
  campaign calendar config, normalizes via `parse` → `format` (bad input raises
  `CalendarError`), then applies the location-style transition:
  - **empty history** → append, silent (`advanced: False`);
  - **equals current** → no-op (`advanced: False`);
  - **different** → append the assistant transition line
    `*Time passes. It is now {friendly}.*`, then append to history
    (`advanced: True`).

  Returns `{"advanced": bool, "friendly": str}`. `friendly` is the primary
  calendar's `describe(...).friendly`.

No cycle: `calendars` does not import `scenes`.

## Birthdays & ages

Birth dates are full native date strings in the active (primary) calendar;
partial precision (year-unknown) is out of scope.

- **PCs** — add `birthdate` to `pcs.PERSONA_FIELDS` (persona frontmatter).
- **NPC characters** — store `birthdate` on the **character container meta**
  (`<root>/characters/<cid>/character.md` frontmatter): version-independent and
  canonical (briefs are LLM-derived / staleness-tracked, so not a home for a hard
  fact). `characters.py` gains read + a setter.

Age and birthday-today are computed by the provider (`age`, `is_anniversary`).

## Context injection — the `# Today` block

In `store/context.py`, when the scene has a current datetime, prepend an **always-on
`# Today` block** (never keyword-gated — same treatment as `# Current setting`),
containing computed facts only:

```
# Today
It is {primary friendly} ({weekday}).            # + "; {secondary friendly}" when a secondary calendar exists
Holidays today: Thanksgiving, Hanukkah.          # merged across all configured calendars
Upcoming: Christmas in 12 days.                   # nearest holiday within 30 days; omitted if none
Present today: Seraphine (age 34); it is Elara's birthday (age 29).
```

- **Date line** — primary `friendly` + weekday; appends the secondary `friendly`
  when a secondary calendar is configured.
- **Holidays today** — union of every configured calendar's `holidays(...)` landing
  on the current fixed day (library region + custom), **merged into one list**.
- **Upcoming** — nearest holiday across all configured calendars within **30 days**
  (configurable); omitted when empty.
- **Present cast** — for each in-scene actor with a `birthdate`: exact age as of the
  current date, flagged when today is their birthday. Actors without a birthdate are
  silently skipped.

No current datetime ⇒ no block (today's behavior unchanged). A deleted/garbled date
or calendar config is tolerated (block omitted, no crash), exactly like a deleted
location.

## Backend modules

- **`store/calendars/`** (new):
  - `base.py` — `CalendarProvider` ABC, `CalendarError`, `REGISTRY`,
    `get_provider(config)`, default `age` / `is_anniversary`.
  - `gregorian.py` — `GregorianProvider`: `datetime`-backed `parse`/`format`/
    `describe`; `holidays`-package-backed `holidays(...)` merged with custom rules.
  - `config.py` — `read_calendar(root) -> {primary, secondary|None}` (defaults when
    absent), `write_calendar(root, cfg)`, `copy_calendar(wroot, croot)`.
- **`store/scenes.py`** — `get_time_history`, `set_datetime`.
- **`store/pcs.py`** — `birthdate` in `PERSONA_FIELDS`.
- **`store/characters.py`** — `birthdate` on container meta (read + setter).
- **`store/context.py`** — the `# Today` block (resolve providers from the campaign
  calendar config; current datetime from `get_time_history`; merged holidays;
  present-cast ages/birthdays).
- **`store/campaigns.py`** — `create_campaign` copies the world's `calendar.json`
  (defaults if none); the wizard then sets the chosen region.
- **Dependency** — add `holidays` to the backend requirements.

## Routes (`routes.py`)

Mirror the location endpoints:

- `GET /api/campaigns/{cid}/scenes/{sid}/datetime` →
  ```
  {"current": {"native", "friendly", "weekday",
               "secondary": {"friendly"} | null,
               "holidays_today": [str], "upcoming": {"name", "in_days"} | null,
               "cast": [{"kind", "id", "name", "age", "birthday_today"}]} | null,
   "history": [native, …]}
  ```
- `PUT /api/campaigns/{cid}/scenes/{sid}/datetime` body `{datetime: native}` →
  `{"ok": True, "advanced": bool, "friendly": str}`. 404 missing scene; **400 on
  `CalendarError`**.
- `GET /api/campaigns/{cid}/calendar` → `{primary, secondary|null}`;
  `PUT /api/campaigns/{cid}/calendar` updates region / custom holidays / secondary.
  (A parallel world-level pair is a noted follow-up.)
- **Birthdate** rides existing editors: extend the PC-version PUT and the
  character-meta PUT to accept `birthdate`.

## Frontend

### `api/client.ts`
- `getSceneDatetime(cid, sid)` / `setSceneDatetime(cid, sid, datetime)`
- `getCalendarConfig(cid)` / `setCalendarConfig(cid, cfg)`
- Types for the datetime payload and calendar config.

### `components/CastPanel.tsx`
A **When** section beside **Setting**:
- Shows the current friendly datetime (+ secondary when configured) and holiday /
  birthday chips, or "No date" when none.
- A `type="date"` input (+ optional `type="time"`) with **Set date** when none and
  **Advance to** when changing.
- On change → `setSceneDatetime` → `onSeeded()` (the transition line streams in) →
  reload the display.

### Calendar config & wizard
- The campaign-creation wizard gains a **region picker** (decision 1c) and an
  optional "add a second, synchronized calendar" with the anchor entry (prefilled
  with the canonical value for real calendars).
- A compact calendar config section edits region + custom-holiday add/remove (full
  custom-holiday CRUD is the larger slice).

### Editors
- **Birthdate** inputs in the PC editor and the Character editor.

## Testing

### Backend (pytest)
- Gregorian `parse`/`format` round-trip; weekday correctness against known dates;
  leap-day validity (2000 ✓, 1900 ✗).
- `holidays` for a known region/year (US Thanksgiving 2026 = Nov 26; Christmas);
  custom holidays (fixed + nth-weekday).
- `age` and `is_anniversary`.
- `set_datetime` transitions: silent first set, no-op on same, italic transition on
  change (mirrors the location tests); `CalendarError` on bad input.
- `# Today` block: date line, merged holidays-today, 30-day upcoming, present-cast
  ages/birthday; omitted when date-less; tolerant of a garbled date / missing config.
- Anchor / secondary: a configured secondary renders a second dateline and **merges
  its holidays** into the one list; a degenerate Gregorian-as-secondary exercises the
  dual-render path; anchor `{native ⇄ gregorian}` round-trips to a fixed day.
- Calendar config read/write/copy + defaults.
- Routes: datetime GET/PUT (advance appends the line), calendar GET/PUT, 400 on bad
  date.

### Frontend (vitest)
- The **When** section renders the current datetime, sets/advances and fires
  `onSeeded`.
- Region picker; birthdate inputs.

## Out of scope

- **Non-Gregorian providers themselves** (Hebrew/Islamic/Japanese/fantasy) — the
  registry, anchor, and dual-render machinery are built and tested now, but a
  genuinely usable second calendar waits on a second provider.
- World→campaign calendar **sync after create** (copy-at-create only).
- Moon phases, multi-cycle (e.g. a 7-day week *and* a 10-day market cycle),
  intercalary/festival days outside months, observational calendars.
- Time zones, sub-minute precision, partial-precision birthdates (year-only).
- **LLM-emitted scene dates** — when scenes are created/opened by the model, it
  should also emit structured date info through the same validated `set_datetime`
  path. Captured in `TODO.md`.
