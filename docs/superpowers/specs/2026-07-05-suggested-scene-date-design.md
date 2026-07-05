# Suggested scene date — design

2026-07-05

## Goal

New scenes arrive with a suggested date pre-filled in the SceneInspector's date
input, derived from the previous scene's date plus an LLM estimate of how much
in-world time passes between scenes. The user still confirms with "Set date" —
the scene is never dated (and its filename never stamped) automatically.

## Decisions

- **Scope: all creation paths, one LLM call.** The New Scene chooser's existing
  scene-suggestions call is extended; no new LLM calls are added. Generated
  cards each carry a premise-specific date; manual and greeting creations use a
  general "next scene" estimate from the same call. Keyless / no-answer-yet
  cases fall back deterministically to the previous scene's chronicle date.
- **Apply mode: pre-fill, not auto-set.** A wrong guess costs nothing — no
  silent filename stamping, no spurious "Time passes" transition lines.
- **Plumbing: persist the hint in scene frontmatter**, exposed through
  `GET /scenes/{sid}/datetime`. Survives reload; one source of truth for the
  pre-fill.

## Backend

### `store/suggest.py`

- When the snapshot has a current date (`snapshot["now"]`), the instruction
  additionally requests:
  - `"date"` on each suggestion — the native-calendar date that scene most
    plausibly opens on, given the in-world gap its premise implies;
  - top-level `"next_date"` — a general estimate for a scene that isn't one of
    the suggestions.
  The prompt names the same native notation the "Current date" line uses.
- When the campaign has no current date, the prompt is unchanged and no dates
  are requested.
- Parsing validates each returned date with `calendars.normalize`; invalid or
  missing dates drop silently (suggestion survives without a date). A
  `parse_next_date`-style helper does the same for the top-level key.

### `store/scenes.py`

- `create_scene(cid, title, suggested_date=None)` — when the hint is present
  and valid (`calendars.normalize`), write it to frontmatter as
  `suggested_date`; invalid values are ignored (it is only a hint).
- `set_datetime` removes the `suggested_date` frontmatter key on the first real
  date set (the hint is stale once a date exists).

### Routes

- `POST /campaigns/{cid}/scenes` body gains optional `suggested_date`.
- `POST /campaigns/{cid}/scene-suggestions` response: each suggestion gains
  `"date"` (or absent), and the response gains top-level `"next_date"`.
- `GET /campaigns/{cid}/scenes/{sid}/datetime`: while `history` is empty the
  response gains `"suggested"` — the frontmatter hint if present and valid,
  otherwise the latest chronicle date with any time-of-day stripped (this
  fallback is what covers manual creations and keyless setups). Once the scene
  has a real date, `suggested` is `null`.

## Frontend

- `api/client.ts`: `SceneSuggestion` gains `date?`; the suggestions response
  gains `next_date`; `createScene` gains an optional suggested date;
  `SceneDatetime` gains `suggested: string | null`.
- `NewSceneChooser`: picking a generated card passes that card's `date` to
  `createScene`; manual and greeting picks pass the response's `next_date`.
  If the LLM hasn't answered when the user clicks, nothing is passed — the
  chronicle fallback covers it.
- `SceneInspector`: when the datetime loads with no current date and a
  `suggested` value, and the date input is untouched, initialize the input to
  the suggestion. The user confirms with the existing "Set date" button.
  Gregorian's native format is already `YYYY-MM-DD`, so the value drops
  straight into `<input type="date">`.

## Edge cases

- LLM suggests a date before the current date: allowed (flashbacks are
  legitimate); `normalize` only validates well-formedness.
- Campaign with no dates anywhere (no chronicle, no hint): `suggested` is
  `null`; the input starts empty exactly as today.
- Chronicle dates may carry `Thh:mm`; the date input is date-only, so the
  fallback strips the time component.

## Tests

Backend:
- suggest: instruction includes the date keys only when the snapshot has a
  current date; parse keeps valid dates and drops malformed ones (per-card and
  `next_date`).
- scenes: `create_scene` stores a valid hint, ignores an invalid one;
  `set_datetime` clears the hint.
- routes: `GET /datetime` returns the frontmatter hint; falls back to the
  chronicle date (time stripped); returns `null` once a real date is set.

Frontend:
- chooser: card pick sends the card's date; manual pick sends `next_date`.
- inspector: dateless scene with `suggested` pre-fills the input; dated scene
  does not.
