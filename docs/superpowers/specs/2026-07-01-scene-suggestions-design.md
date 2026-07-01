# Suggested Next Scenes (Phase 5b) — Design

**Date:** 2026-07-01
**Status:** Design — approved, ready for implementation plan
**Phase:** 5b of the scene lifecycle & continuity system (the read-forward companion to
**5a plot threads**, which shipped first)
**Parent:** [`2026-06-30-scene-lifecycle-continuity-design.md`](2026-06-30-scene-lifecycle-continuity-design.md) (umbrella; see "Suggested next scenes")
**Builds on:** Phase 5a (`plot.open_threads`), Phase 1 (`chronicle.recent`), the calendar
system, and the existing ephemeral-generation pattern (`build_opener_messages` /
`post_opener` / `_ephemeral_stream`).

## Problem

Starting a new scene is a blank page. The campaign already knows what's unresolved (open
plot threads), who's been off-screen too long (roster vs. recent chronicle), and what the
calendar holds (holidays, character birthdays) — but the user has to hold all of that in
their head. Phase 5b adds an **ephemeral, one-shot helper** at scene creation that reads
those signals and proposes 3–4 concrete openings. The user picks one — which **auto-seeds**
the new scene's cast + location and prefills the opener prompt — or ignores it and starts
blank / from a greeting.

## Scope

- **In:** a read-only suggestion call at scene creation returning 3–4 openings, each a
  `{title, premise, cast, location}`; a "Suggest scenes" affordance in the empty-scene
  `CastPanel`; picking a suggestion auto-seeds cast + location and prefills the opener
  prompt, reusing the existing cast/location endpoints.
- **Deferred / out:** persisting suggestions; a regenerate/dismiss loop (one fetch, pick
  or ignore); suggesting brand-new characters or locations (only existing ids are
  seedable — the premise prose may still mention new elements the user creates by hand).
  Campaign-vs-base world view remains the final umbrella phase.

## Decisions

1. **Ephemeral and non-streaming.** Like the opener, nothing is persisted by the call.
   Unlike the opener (streamed prose), suggestions are **structured** (3–4 items), so it is
   a single non-streaming `client.complete` returning JSON — mirroring `post_absorb`.
2. **Campaign-level endpoint, no `sid`.** The inputs are campaign-wide (threads, chronicle,
   roster, calendar). A new `POST /campaigns/{cid}/scene-suggestions` avoids colliding with
   the existing `/scenes/{sid}/suggestions` (cast-suggestion) route.
3. **"Now" = the latest chronicled scene's date**, with full calendar awareness at that
   date (today's holidays, the upcoming holiday, and **roster birthdays** today / within the
   upcoming window). If there is no chronicle yet, the calendar facts are simply omitted
   (threads + roster still drive suggestions).
4. **Seedable ids only, drawn wide.** Suggested **NPCs may be any character in the world**
   (the add-cast route already seeds any world character); **players** come from the
   campaign roster; **locations** from the campaign's locations. `parse_output` validates
   every id against these sets and drops unknowns, so auto-seed can never reference a
   non-existent actor/location.
5. **Auto-seed on the client, via existing endpoints.** The suggestion call stays pure
   (read-only). Picking a card calls the existing `addToCast` / `setLocation` APIs and
   prefills the opener prompt — no new write path, no new persistence.

## Deterministic snapshot — `store/suggest.py`

`build_snapshot(cid) -> dict` assembles the read-only inputs the model should not have to
infer (each piece tolerant — a missing/garbled source contributes nothing, never raises):

- **`open_threads`** — `plot.open_threads(cid)` (title, status, latest beat).
- **`now`** — the latest chronicled scene's `date` (`chronicle.recent(cid, 1)`), `""` if
  no chronicle.
- **`date_facts`** — when `now` is set and the calendar parses: `calendars.today_facts`
  (friendly date, `holidays_today`, `upcoming` holiday) **plus** `birthdays` — for each
  roster actor with a birthdate, `{name, age, when}` where `when` is `"today"` or
  `"in N days"` for a birthday within `UPCOMING_WINDOW_DAYS`; actors with no upcoming
  birthday are omitted. Computed via `calendars.get_provider` / `age` / `is_anniversary`.
- **`absent_cast`** — roster NPC characters (`role == "npc"`, `kind == "characters"`) whose
  id is **not** in the union of the recent 5 chronicle scenes' `cast`, each
  `{name, tagline}` from `briefs.read_brief`.
- **`available_cast`** — every world character (`characters.list_characters(wroot)`) as a
  seedable NPC token `characters:<id>` + name, plus the campaign roster's players
  (`pcs:<id>`/`characters:<id>` + name). Feeds the model the ids to reference.
- **`available_locations`** — `entities.list_entities(croot, "locations")` (id + name).

`build_prompt(snapshot) -> list[dict]` renders these into an instruction + a user block.
The instruction: *propose 3–4 distinct openings that advance an open thread, revisit a
long-absent character, or land on an upcoming date/birthday; reference only the given ids
for cast and location; reply with ONLY a JSON object.* Prompt/parse live here; the LLM call
is in the route (the `absorb`/`briefs` split).

## Parse — `store/suggest.py`

`parse_output(text, cid) -> list[dict]` (tolerant, mirrors `absorb._obj`):

```jsonc
{ "suggestions": [
  { "title": "The creditor at the gate",
    "premise": "A debt-collector arrives at the salt cathedral asking after Doran…",
    "cast": ["characters:seraphine", "pcs:elara"],   // "<kind>:<id>" tokens
    "location": "salt-cathedral" } ] }
```

Each suggestion is kept only with a non-empty `title` and `premise`. `cast` tokens are
validated (character exists in the world; pc/player exists in the roster) and unknowns
dropped; `location` is kept only if it names a campaign location (else `""`). Returns `[]`
on garble or an empty list. The route resolves ids → display names before returning.

## Route (`routes.py`)

`POST /campaigns/{cid}/scene-suggestions` — requires an API key (`_require_key`, `409` as
the opener). Builds the snapshot + prompt, `await client.complete(...)`, parses, resolves
names, and returns:

```jsonc
{ "suggestions": [
  { "title": "…", "premise": "…",
    "cast": [ {"kind": "characters", "id": "seraphine", "name": "Seraphine"} ],
    "location": {"id": "salt-cathedral", "name": "The Salt Cathedral"} } ] }  // location may be null
```

`404` on unknown campaign. No `sid` — campaign-wide.

## Backend modules

- **`store/suggest.py`** (new) — `build_snapshot(cid)`, `build_prompt(snapshot)`,
  `parse_output(text, cid)`, plus small render/validate helpers. Pure assembly +
  prompt/parse; no LLM call, no writes. Imports `plot`, `chronicle`, `appearances`,
  `characters`, `pcs`, `entities`, `briefs`, `calendars`, `campaigns`, `worlds`.
- **`routes.py`** — the one new endpoint above; a `SceneSuggest`-free `POST` (no body).

No new import cycles (`suggest` reads the stores the way `context` already does).

## Frontend

- **`api/client.ts`** — `sceneSuggestions(cid) -> { suggestions: SceneSuggestion[] }`,
  and a `SceneSuggestion` type (`title`, `premise`, `cast: {kind,id,name}[]`,
  `location: {id,name} | null`).
- **`components/CastPanel.tsx`** (empty-scene view) — a **"Suggest scenes"** button
  (disabled without a key / while busy) that fetches and renders 3–4 cards (title,
  premise, cast names, location). Each card's **"Use this scene"**:
  1. `addToCast(cid, sid, {kind, id})` for each cast member (a duplicate/`409` is
     tolerated — already-present actors are skipped);
  2. `setLocation(cid, sid, location.id)` when a location is present;
  3. `setPrompt(premise)` to seed the existing opener box; then `onSeeded()`.
  No new component beyond the suggestions block; reuses the panel's cast/opener controls.

## Testing

### Backend (pytest)
- **`suggest.build_snapshot`**: includes open threads, absent cast (roster minus recent
  chronicle), birthday/holiday facts at the latest scene's date, available cast (world
  characters + roster players) and locations; tolerant when chronicle/plot/calendar are
  empty or garbled (no raise, missing keys ⇒ empty).
- **`suggest.parse_output`**: parses suggestions; drops a suggestion with no title/premise;
  drops unknown cast tokens and a non-existent `location`; `[]` on garble.
- **route**: `POST /scene-suggestions` returns resolved suggestions (names filled);
  `409` without a key; `404` for an unknown campaign. (LLM client mocked, as the absorb/
  opener route tests do.)

### Frontend (vitest)
- The **"Suggest scenes"** button fetches and renders cards; **"Use this scene"** calls
  `addToCast` for each cast id and `setLocation` with the location id, and prefills the
  opener prompt with the premise.

## Out of scope

- Persisting or versioning suggestions; regenerate/dismiss iteration.
- Proposing new characters/locations to create (only existing, seedable ids).
- Any change to the absorb/write-back pipeline — this phase is purely read-forward.

## Phasing (for the plan)

1. `suggest.py`: `build_snapshot` (threads / now / date+birthday facts / absent cast /
   available cast + locations), tolerant.
2. `suggest.py`: `build_prompt` + `parse_output` (validate/drop ids), with the instruction.
3. `routes.py`: `POST /campaigns/{cid}/scene-suggestions` (key-gated, name resolution).
4. Frontend: `api.sceneSuggestions` + `SceneSuggestion` type.
5. Frontend: `CastPanel` suggestions UI + auto-seed on pick.
