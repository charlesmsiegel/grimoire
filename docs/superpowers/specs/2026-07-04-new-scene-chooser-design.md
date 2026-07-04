# New Scene Chooser — Design

**Date:** 2026-07-04
**Status:** Design — approved, ready for implementation plan
**Builds on:** greetings & plot maps (`2026-06-22-greetings-plotmaps-design.md`) — availability,
`leads_to` edges, `start_from_greeting`; scene suggestions Phase 5b
(`2026-07-01-scene-suggestions-design.md`) — `POST /campaigns/{cid}/scene-suggestions`.

## Problem

`+ New Scene` creates a blank scene with no guidance. The campaign already knows which
greetings are available — including which ones the current scene just **unlocked** through
the plot map — and can generate next-scene suggestions, but both only surface *after* the
user has committed to a blank scene (greeting chips and the "Suggest scenes" block inside
the empty-scene `CastPanel`). The choice belongs at the moment of clicking **+ New Scene**.

## Scope

- **In:** a chooser modal on `+ New Scene` offering up to **4 scene cards** (available
  greetings, unlocked-by-current-scene first, plus at least two LLM-generated premise
  cards) and a permanent **Create manually** option; stamping the originating greeting id
  on scenes started from a greeting; an `?after=<sid>` extension to the availability
  route; removal of the now-redundant greeting chips and "Suggest scenes" block from
  `CastPanel`.
- **Out:** pre-generating opener prose for the generated cards (picking one prefills the
  opener prompt; the user still clicks Generate); persisting chooser results; any change
  to the scene-suggestions endpoint or `CampaignWizard`'s first-scene greeting picker;
  retroactively inferring greetings for pre-existing scenes.

## Decisions

1. **Chooser first, create on pick.** Clicking `+ New Scene` opens the modal without
   creating anything. Cancel/dismiss creates nothing — no stray blank scenes.
2. **Generated cards are premise-only.** Same shape as Phase 5b suggestions
   (title/premise/cast/location). Picking one seeds cast + location and prefills the
   opener prompt; opening prose is not generated until the user asks.
3. **Unlock linkage is recorded, not inferred.** `start_from_greeting` stamps
   `greeting: <gid>` into the scene's frontmatter (the `mark_absorbed` pattern). Only
   scenes started after this ships carry it; older scenes simply produce no unlock boost.
4. **Reference scene = the selected scene, falling back to the latest.** The chooser asks
   for availability relative to the scene the user is looking at; with nothing selected it
   uses the most recent scene; with no scenes, no boost.
5. **Slot mix: 2 greetings + 2 generated, with backfill.** Target the top 2 greetings
   (unlocked first) and 2 generated cards. Fewer greetings ⇒ more generated cards fill in
   (the suggestions endpoint already returns 3–4). No API key ⇒ up to 4 greeting cards and
   a "set an OpenRouter key in Config to generate" hint. No greetings ⇒ the greeting
   section says so; generated + manual remain. **Create manually** is always present.
6. **Two calls, server-side ranking.** Greetings come from the (extended) availability
   route — fast, rendered instantly. Generated cards come from the existing
   `POST /scene-suggestions` — slow, loaded async behind a placeholder. The modal composes
   the slots client-side; the fast path is never held hostage to LLM latency.
7. **The chooser supersedes the in-scene helpers.** `CastPanel` loses the "Start from a
   greeting" chips and the "Suggest scenes" block; it keeps manual cast/location/date/
   opener setup. A manually created scene stays manual.

## Backend

### `store/playing.py`

- `start_from_greeting(cid, sid, gid)` additionally stamps `greeting: <gid>` into the
  scene's frontmatter via a new `scenes.stamp_greeting(cid, sid, gid)` (same
  read-meta/write-meta pattern as `scenes.mark_absorbed`).
- `available_greetings(cid, after: str | None = None) -> list[dict]` — when `after` names
  a scene: read that scene's stamped `greeting`; look up its `leads_to` in the plot map;
  each availability item gains `"unlocked": bool` (true iff its id is in that list);
  results sorted **unlocked first**, otherwise in today's order. When `after` is `None`,
  omitted-stamp, unknown greeting, or the scene predates stamping: every item gets
  `"unlocked": false` and the order is unchanged (existing callers see today's behavior
  plus an ignorable field). Unknown `after` scene ⇒ `404` at the route.

### `routes.py`

- `GET /campaigns/{cid}/greetings/available` gains an optional `after` query param passed
  through to `available_greetings`. No new endpoints; `POST /scene-suggestions` unchanged.

## Frontend

### `api/client.ts`

- `availableGreetings(cid, after?)` appends `?after=` when given; `Availability` gains
  `unlocked: boolean`.

### `components/NewSceneChooser.tsx` (new)

Modal owned by `CampaignView`; `+ New Scene` opens it (passing the reference `sid`).

- On open: fetch `availableGreetings(cid, after)` (instant) and, if a key is set,
  `sceneSuggestions(cid)` (async, placeholder card meanwhile).
- Compose slots: top `min(2, …)` greetings (unlocked flagged visually, e.g. an
  "unlocked" chip), then generated cards to fill 4 total (minimum 2 generated when the
  key allows; up to 4 greetings when it doesn't).
- **Pick a greeting card:** `createScene` → `startFromGreeting` → close, select the new
  scene.
- **Pick a generated card:** `createScene` → `addToCast` per cast member (409 tolerated)
  → `setSceneLocation` when present → close, select the new scene with the premise handed
  to `CastPanel` as `initialPrompt`.
- **Create manually:** `createScene` → close, select — today's behavior.
- **Cancel / backdrop / Escape:** close, nothing created.
- Errors surface in the modal banner; a failed suggestions call degrades to
  greetings + manual.

### `routes/CampaignView.tsx`

- `newScene()` becomes "open chooser"; the creation/seeding sequences move into the
  chooser's pick handlers (or callbacks the view provides). After any pick, refresh the
  scene list and select the new scene. Holds transient `initialPrompt` state passed to
  `CastPanel` for the generated-card path.

### `components/CastPanel.tsx`

- Remove the "Start from a greeting" section and the "Suggest scenes" block (and their
  now-unused state/handlers/imports). Add `initialPrompt?: string` to seed the opener
  prompt state.

## Testing

### Backend (pytest)

- `start_from_greeting` stamps `greeting` in the scene frontmatter.
- `available_greetings(cid, after=sid)`: flags exactly the `leads_to` targets of the
  stamped greeting as `unlocked` and sorts them first; a scene without a stamp, an
  unknown stamped greeting, or `after=None` yields all-`false` in today's order.
- Route: `?after` passes through; unknown `after` scene ⇒ `404`; no-param response is
  unchanged apart from the `unlocked` field.

### Frontend (vitest)

- Chooser renders greeting cards immediately and generated cards after the mocked
  suggestions resolve; unlocked greetings rank first.
- Picking a greeting card calls `createScene` then `startFromGreeting` with the new sid.
- Picking a generated card calls `createScene`, `addToCast` per member,
  `setSceneLocation`, and results in the opener prompt prefilled with the premise.
- Create manually calls only `createScene`; cancel calls nothing.
- No key ⇒ no suggestions fetch, hint shown, up to 4 greeting cards.
- `CastPanel` tests updated: greeting chips and suggest block gone; `initialPrompt`
  seeds the opener prompt.

## Phasing (for the plan)

1. `scenes.stamp_greeting` + stamping in `start_from_greeting`.
2. `available_greetings(after=…)` unlock flag + sort; route query param.
3. `api/client.ts` param + type.
4. `NewSceneChooser` modal + `CampaignView` wiring.
5. `CastPanel` cleanup + `initialPrompt`.
