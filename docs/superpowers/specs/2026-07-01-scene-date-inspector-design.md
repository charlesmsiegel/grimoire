# Scene Date in the Inspector (confirm-gated) — Design

**Date:** 2026-07-01
**Status:** Design — approved, ready for implementation plan

## Problem

You can set a scene's date only *before* the scene starts. The date control (the
"When" block: current date + a date input + Set/Advance button) lives in
`CastPanel`, which `CampaignView` renders **only when the scene is empty**
(`messages.length === 0`, `CampaignView.tsx:266`). Once a scene is *in progress*
(has messages), `CastPanel` disappears; the only right-hand panel is
`SceneInspector`, which shows Active characters, Location, and Context — but **no
date and no way to set it**. Yet the backend is built for mid-scene changes:
`scenes.set_datetime` appends a *"Time passes. It is now …"* transition line when
the date advances.

A second gap surfaced while scoping: the date should be entered in **the
campaign's calendar**, and there must be a way to establish that calendar when it
isn't set up. Today only the Gregorian provider exists and `read_calendar` always
returns a default (`gregorian`/`US`), so a campaign's calendar is *never truly
unset* — it defaults silently. We want an explicit "the user has set up this
campaign's calendar" state that **gates** date entry.

## What we're building

1. A **shared `SceneWhen` component** that renders the "When" block, used by both
   `SceneInspector` (the in-progress case — the core ask) and `CastPanel`
   (replacing its current *ungated* duplicate). This makes the date control
   available during play and keeps the gate consistent everywhere a date can be set.
2. A real **`confirmed` flag** on the campaign calendar. Until the calendar is
   confirmed, `SceneWhen` shows a **setup prompt** instead of the date input; once
   confirmed, it shows the date control.

Out of scope (deferred): editable location in the inspector; non-Gregorian
calendars; **epoch/anchor UI** (the `anchor` field is ignored by the Gregorian
provider, so epoch entry would be non-functional today).

## Backend — the `confirmed` flag

A top-level boolean on `calendar.json`: `{primary, secondary, confirmed}`.

- `store/calendars/config.py`
  - `default_calendar()` returns `confirmed: False`.
  - `read_calendar` reads `confirmed = bool(raw.get("confirmed", False))` and
    includes it in the returned dict.
  - `write_calendar` persists `confirmed = bool(cfg.get("confirmed", False))`.
  - `copy_calendar` (world→campaign on create) already round-trips read→write, so
    an unconfirmed world yields an unconfirmed campaign — the desired default.
- `routes.py`: the `CalendarConfig` pydantic model gains `confirmed: bool = False`.
  The existing `PUT /campaigns/{cid}/calendar` writes the body verbatim, so the
  frontend confirms by PUTting the config with `confirmed: true`.
- **Frontend-only gate.** `set_datetime` stays permissive (no server-side block).
  This is a single-user local app; the gate is a UX guardrail, and enforcing it in
  the backend buys nothing while adding an error path.
- **Backward-compat.** Existing campaigns have a `calendar.json` (written by
  `copy_calendar` at creation) with no `confirmed` field, so they read as
  `confirmed: false` and will see the one-time setup prompt. Acceptable for the
  attempt-2 rebuild.

## Frontend — `components/SceneWhen.tsx`

Props: `{ cid: string; sid: string; refreshKey?: number; onChanged: () => void }`.

On mount / when `cid`, `sid`, or `refreshKey` change, it fetches the calendar
config (`api.getCalendarConfig`) and the scene datetime (`api.getSceneDatetime`).
It renders one of three states:

1. **Not confirmed** (`!cfg.confirmed`) — a `field-hint` *"Set up the campaign
   calendar to track dates."* followed by the setup UI (see below). No date input.
2. **Confirmed, no date** (`when.current == null`) — *"No date"* + `<input
   type="date" aria-label="Scene date">` + a **Set date** button (disabled when
   empty).
3. **Confirmed, date set** — the current date as `field-hint`
   (`${when.current.friendly} (${when.current.weekday})`), a holidays line when
   `holidays_today` is non-empty, then the input + an **Advance to** button.

`applyDatetime()`: `await api.setSceneDatetime(cid, sid, dateInput)`, clear the
input, re-fetch the datetime, then call `onChanged()`. Errors set a local `error`
rendered as a `.banner`, mirroring `CastPanel` (`set_datetime` can 400 on a date
the calendar can't parse). Formatting (friendly/weekday/holidays, button labels)
matches the current `CastPanel` "When" block exactly, so nothing regresses.

### Setup UI — reuse `CalendarConfig`

The not-confirmed state embeds the existing `CalendarConfig` (region dropdown), so
we don't duplicate region-selection logic. Two small changes to `CalendarConfig`:

- **Save sends `confirmed: true`** (an explicit save from either place = the user
  has set up the calendar).
- It takes an optional `onSaved?: () => void` callback so `SceneWhen` can re-fetch
  and swap from the setup prompt to the date control.

The sidebar's Calendar `<details>` (which already renders `CalendarConfig`) keeps
working; its Save now also confirms — which is correct.

## Wiring — `CampaignView.tsx`

- `SceneInspector` gains an `onSceneChanged: () => void` prop, wired to
  `() => activeId && selectScene(activeId)`. `selectScene` reloads messages (so the
  *"Time passes…"* line appears) and bumps `ctxKey`, which re-triggers the
  inspector's own reload — the same mechanism `CastPanel`'s `onSeeded` uses.
- `SceneInspector` renders `<SceneWhen cid sid refreshKey={refreshKey}
  onChanged={onSceneChanged} />` as a new side-section after Location.
- `CastPanel` drops its inline "When" block (and its `when`, `dateInput`,
  `reloadWhen`, `applyDatetime`) and renders `<SceneWhen cid sid
  onChanged={onSeeded} />` in its place.

## Types

`client.ts`: the `CalendarConfig` type gains `confirmed: boolean`.

## Tests

- **`SceneWhen.test.tsx`** (new): renders the setup prompt when
  `confirmed: false`; renders "No date" + input when confirmed with no current;
  renders the friendly date when a current exists; saving the setup
  (`setCalendarConfig` with `confirmed: true`) then re-fetch reveals the input;
  entering a date and clicking the button calls
  `api.setSceneDatetime("c","s",<value>)` and fires `onChanged`.
- **`SceneInspector.test.tsx`**: extend the mock with `getCalendarConfig` /
  `getSceneDatetime`; assert the confirmed date renders and the "No date"/setup
  paths behave. Existing cast/location/context assertions stay.
- **`CastPanel.test.tsx`**: update for the extracted component (the date
  assertions move to `SceneWhen`; CastPanel keeps its cast/location/greeting tests).
- **Backend** (`test_*calendar*`): `write_calendar` → `read_calendar` round-trips
  `confirmed`; a fresh `read_calendar` (no file) returns `confirmed: false`;
  `copy_calendar` preserves it.

## Files touched

- `backend/src/grimoire/store/calendars/config.py` — `confirmed` field.
- `backend/src/grimoire/routes.py` — `CalendarConfig.confirmed`.
- `frontend/src/components/SceneWhen.tsx` — new shared component.
- `frontend/src/components/CalendarConfig.tsx` — confirm-on-save + `onSaved`.
- `frontend/src/components/SceneInspector.tsx` — render `SceneWhen`, `onSceneChanged`.
- `frontend/src/components/CastPanel.tsx` — replace inline "When" with `SceneWhen`.
- `frontend/src/routes/CampaignView.tsx` — pass `onSceneChanged`.
- `frontend/src/api/client.ts` — `CalendarConfig.confirmed` type.
- Tests as above.
