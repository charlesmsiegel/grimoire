# Scene Date in the Inspector — Design

**Date:** 2026-07-01
**Status:** Design — approved, ready for implementation plan

## Problem

You can set a scene's date only *before* the scene starts — the date control lives
in `CastPanel`, which renders only for an empty scene. Once a scene is in progress,
the only right-hand panel is `SceneInspector`, which shows cast, location, and
context but **no date and no way to set one**.

## What we're building

Two small widgets in **`SceneInspector` only**, in a new "When" section:

1. **No calendar selected → a calendar picker.** A `<select>` of the available
   calendars (only "Gregorian" today) + a button to choose it. Choosing sets the
   campaign's calendar.
2. **Calendar selected, no date → a date picker.** An `<input type="date">` + a
   button to set the scene's date.
3. **Date set → show it** (`friendly (weekday)`, plus a holidays line if any).

That's the whole feature. No shared component, no changes to `CastPanel`, no
region/epoch setup UI.

## The one new state: "no calendar selected"

Today `read_calendar` always returns a default (`gregorian`), so "no calendar
selected" doesn't exist in the data. We add a single boolean `confirmed` to
`calendar.json`, set to `true` when the user picks a calendar in the widget.

- `store/calendars/config.py`: `default_calendar()` → `confirmed: False`;
  `read_calendar` reads `bool(raw.get("confirmed", False))`; `write_calendar`
  persists it; `copy_calendar` round-trips it (unconfirmed world → unconfirmed
  campaign).
- `routes.py`: the `CalendarConfig` pydantic model gains `confirmed: bool = False`.
  The existing `PUT /campaigns/{cid}/calendar` writes the body, so the widget
  confirms by PUTting `{...cfg, confirmed: true}`.
- Frontend-only gate — `set_datetime` stays permissive.
- Backward-compat: existing campaigns read as `confirmed: false` and show the
  picker once. Fine for the attempt-2 rebuild.

## Frontend — `SceneInspector.tsx`

Fetch the calendar config (`api.getCalendarConfig`) and scene datetime
(`api.getSceneDatetime`) in the effect already keyed on `[cid, sid, refreshKey]`.
Render the "When" section (after Location) by state:

- `!when?.current && !cfg?.confirmed` → **calendar picker**. Options come from a
  small hardcoded `CALENDARS` list (`[{ id: "gregorian", name: "Gregorian" }]`,
  which grows when providers are added). The button calls
  `api.setCalendarConfig(cid, { ...cfg, primary: { ...cfg.primary, provider },
  confirmed: true })`, then re-fetches.
- `!when?.current` (confirmed) → **date picker**: `<input type="date"
  aria-label="Scene date">` + "Set date". The button calls
  `api.setSceneDatetime(cid, sid, dateInput)`, then re-fetches and calls
  `onSceneChanged()`.
- otherwise → the current date as a `field-hint`
  (`${when.current.friendly} (${when.current.weekday})`) + a holidays line when
  `holidays_today` is non-empty. Keep the same input + an "Advance to" button so a
  set date can still be changed (same code path; `set_datetime` appends the
  "Time passes…" line on a real change).

Errors set a local `error` rendered as a `.banner`.

`SceneInspector` gains an `onSceneChanged: () => void` prop; `CampaignView` passes
`() => activeId && selectScene(activeId)` (reloads messages so the "Time passes…"
line appears — the same mechanism `CastPanel`'s `onSeeded` uses).

## Types

`client.ts`: the `CalendarConfig` type gains `confirmed: boolean`.

## Tests

- **`SceneInspector.test.tsx`**: extend the mock with `getCalendarConfig`,
  `getSceneDatetime`, `setCalendarConfig`, `setSceneDatetime`. Assert: unconfirmed
  + no date → the calendar picker shows and choosing calls `setCalendarConfig` with
  `confirmed: true`; confirmed + no date → the date input shows and setting a date
  calls `setSceneDatetime("c","s",<value>)` and fires `onSceneChanged`; a current
  date renders its `friendly` text. Existing cast/location/context tests stay.
- **Backend** (`test_*calendar*`): `write_calendar` → `read_calendar` round-trips
  `confirmed`; a fresh `read_calendar` returns `confirmed: false`; `copy_calendar`
  preserves it.

## Files touched

- `backend/src/grimoire/store/calendars/config.py` — `confirmed` field.
- `backend/src/grimoire/routes.py` — `CalendarConfig.confirmed`.
- `frontend/src/components/SceneInspector.tsx` — the "When" section + `onSceneChanged`.
- `frontend/src/routes/CampaignView.tsx` — pass `onSceneChanged`.
- `frontend/src/api/client.ts` — `CalendarConfig.confirmed` type.
- Tests as above.
