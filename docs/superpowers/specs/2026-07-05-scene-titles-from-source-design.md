# Scene titles from their source

## Problem

Every scene created through the New-scene chooser or the campaign wizard is
titled "New scene" (the backend default), regardless of how it was started.
Greeting starts know the greeting's name and generated suggestion cards carry
an LLM-written title, but both are discarded.

## Behavior

- A scene started **from a greeting** (New-scene chooser or the wizard's final
  step) takes the greeting's `name` as its title.
- A scene created **from a generated suggestion card** takes the card's
  generated `title`.
- **Manual** creation is unchanged: title stays "New scene".

## Backend

`store.playing.start_from_greeting(cid, sid, gid)` retitles the scene as its
**last** step: after casting, marking the greeting played, stamping the
greeting id, and appending the opener, it calls
`scenes.rename_scene(cid, sid, greeting_name)` and returns the new sid.

- Ordering rationale: any failure before the rename leaves the original sid
  valid, so the chooser's cleanup-delete of a half-seeded scene still works.
- `rename_scene` already re-slugs the filename and repoints every referencing
  store (`scene_refs.repoint`), so the cast/greeting stamps written moments
  earlier carry across.
- Route `POST /campaigns/{cid}/scenes/{sid}/start-from-greeting` returns
  `{"ok": true, "id": <new sid>}`.

## Frontend

- `api.startFromGreeting` return type gains `id: string`.
- `NewSceneChooser`:
  - `create()` takes an optional title, passed to `api.createScene`; the seed
    callback may return a replacement sid (the post-rename id).
  - `pickGreeting` adopts the renamed id from the start-from-greeting response
    so `onCreated` opens the right scene.
  - `pickSuggestion` passes `s.title` as the creation title.
  - `pickManual` passes no title (backend default "New scene").
- `CampaignWizard`: no change — after starting a greeting it navigates to the
  campaign view, which re-lists scenes by id.

## Tests

- Backend (`test_playing_store.py`): `start_from_greeting` renames the scene to
  the greeting's name and returns the new sid; the scene's title metadata and
  filename slug reflect the greeting name.
- Backend (`test_routes.py`): the start-from-greeting response includes the new
  `id`.
- Frontend (`NewSceneChooser.test.tsx`): picking a suggestion calls
  `createScene` with the generated title; picking a greeting calls `onCreated`
  with the id returned by `startFromGreeting`.
