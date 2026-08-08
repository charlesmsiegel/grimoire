# The new-scene front door

Issues #315 (played greetings are re-offered), #316 (steer generated
suggestions with a free-text direction), #317 ("Create manually" should infer
date and location from the typed scene-start summary), #89 (picker with a
refresh control and a free-text custom card — Option B), #90 (confirm step
with editable resolved metadata — Option A), #23 (accept LLM-emitted
structured scene dates via `set_datetime`).

All six describe the same surface: the path from "I want a new scene" to a
scene that exists with the right metadata on it.

## Problem

`frontend/src/components/NewSceneChooser.tsx` is the only way to start a scene
in an existing campaign, and it commits on click. Picking a card runs
`api.createScene` immediately, then seeds — `startFromGreeting`, or
`addCastBatch` + `setSceneLocation` with the premise handed up to `CastPanel`
as an opener seed. There is no step between choosing and writing.

That single shape produces all six issues:

- **Played greetings come back.** `greetings.availability()` reports a played
  greeting as `available: true` — the played set exists there to drive
  *predecessor* logic, and self-exclusion was never added. The picker filters
  on `available` and pcless only (`NewSceneChooser.tsx:32-33`), and so does
  `suggest.greeting_candidates` (`suggest.py:159-160`), so a greeting the
  campaign already played is offered twice over: as a card, and to the LLM
  ranker that chooses which cards to show. (#315)
- **Generation cannot be steered or re-rolled.** Suggestions are fetched once
  per open (`NewSceneChooser.tsx:37-42`). The only reroll is close-and-reopen,
  which redoes the entire call including greeting ranking, reshuffling the
  greeting cards. There is nowhere to say what kind of scene you want. (#316,
  #89)
- **Typed intent is thrown away.** "Create manually" is a button, not an
  input. You land on an empty scene and set location, date, and cast by hand
  in `CastPanel`, retyping in structured form what you already knew you
  wanted. (#317, #89)
- **The LLM's resolved metadata cannot be corrected.** A suggestion arrives
  with validated cast, location, and date; picking it applies all of them
  sight-unseen. Fixing a wrong cast pick means editing a scene that already
  exists. (#90)
- **A proposed date is only ever a hint.** `create_scene(suggested_date=…)`
  writes `suggested_date` into frontmatter, which pre-fills `CastPanel`'s date
  box and nothing else. The scene has no moment, no `time_history`, and no
  "Today" block until a human presses a button. (#23)

## Scope

In: the picker rework (mode → pick → confirm → create), played-greeting
exclusion at the store, a `direction` and `rank` parameter on the suggestions
route, a new scene-intent extraction route and its templates, and applying the
confirmed date through `set_datetime`.

Out: the Scene Ledger (#88 — nothing new is persisted here; #89 Option B is
deliberately the no-storage variant), adapted-greeting first posts (#91),
per-slot regeneration (#89 Option C), a backend scene-draft resource (#90
Option C), and scene import (#92, #93).

## Decisions

Four calls shape everything below; they are recorded here because each had a
plausible alternative.

1. **#315 is fixed in the store, not the presentation layer.** Played and
   completed greetings become `available: false`. Consequence, accepted
   deliberately: `start_from_greeting`'s existing guard turns replay into a
   409, so a greeting cannot be replayed through the API at all. Blast radius
   is narrow — `availability()`'s output reaches only `available_greetings`
   (→ the picker and `CampaignWizard`), `suggest.greeting_candidates`, and
   that guard. `GreetingEditor` never reads `available` or `reasons`.
2. **The confirm step edits metadata only.** `/opener` streams against a real
   scene id, so first-post *generation* cannot precede creation without
   breaking "nothing is written until confirm". `CastPanel` keeps the
   generate → preview → adopt loop and stays the post-creation editor; confirm
   hands it the premise through the existing `initialPrompt` prop.
3. **#317 gets its own extraction call**, not a re-use of the suggestions
   prompt with `n=1`. Extraction must preserve the user's typed text; a
   suggestion prompt rewrites it, and anchors its cast and location choices to
   the premise it invented rather than to what was typed.
4. **A refresh regenerates the generated slots only.** Greeting cards must not
   move under the cursor, so refreshes pass `rank=false` and skip greeting
   ranking entirely — a cheaper prompt and a stable layout.

## Architecture

### Flow

```
mode (pc | offscreen)  →  SceneIdeaPicker  →  SceneConfirmForm  →  scene exists
                             emits a SceneDraft      writes
```

`SceneDraft` is the seam, and every path produces one:

```ts
type SceneDraft = {
  source: "greeting" | "generated" | "custom";
  gid?: string;                      // source === "greeting"
  title: string;
  premise: string;                   // handed to CastPanel as initialPrompt
  date: string;                      // canonical native, "" if none
  location: string;                  // location id, "" if none
  cast: { kind: string; id: string; name: string }[];
};
```

### Components

`NewSceneChooser.tsx` (155 lines today) splits four ways. It roughly triples
in content otherwise, and the create sequence's ordering hazards should not
live interleaved with rendering.

- **`NewSceneChooser.tsx`** — orchestrator. Owns `mode`, `step`, the error
  banner, the backdrop, and Escape handling. Renders one of the two panes.
- **`SceneIdeaPicker.tsx`** — the pick pane. Four regions, described below.
  Emits a `SceneDraft`; writes nothing.
- **`SceneConfirmForm.tsx`** — takes a draft, edits it, runs the create
  sequence, reports the final sid.
- **`useSceneSuggestions.ts`** — the fetch/refresh state machine
  (`suggestions`, `picks`, `nextDate`, `busy`, `refresh(direction)`).

### The pick pane

- **Direction** — a free-text input plus a **↻ Regenerate** button. Regenerate
  re-fetches with `direction` and `rank=false`, replacing only the Generated
  cards. Session-only: the text is not persisted anywhere. (#316, #89)
- **From a greeting** — as today. #315's store fix removes played greetings
  without a change here.
- **Generated** — as today.
- **Your own** — a textarea. With text, the button reads **Use this →** and
  runs the extraction before opening confirm. Empty, it reads **Create blank
  scene** and opens confirm with an empty draft and no LLM call. This replaces
  today's "Create manually" button and preserves its behavior for the
  empty case. (#317, #89)

### The confirm pane

Title, date (`CalendarDatePicker`), location `<select>` over campaign
locations, cast chips, and a premise textarea — pre-filled from the draft.
Nothing is written until **Create scene**; **Back** returns to the picker and
**Cancel** closes, both without writing.

Two rules follow from what the backend already does:

- For `source: "greeting"` the cast is **read-only** — `start_from_greeting`
  seats the greeting's `present` set under locked-version rules, and
  re-implementing that seating in a form would drift from it. The first post
  is fixed to greeting-verbatim.
- For offscreen (`pcless`) scenes the cast picker offers no players, matching
  the `PlayError` guards in `start_from_greeting`.

## Backend surface

### #315 — self-exclusion in `availability()`

One condition in `store/greetings.py:availability()`:

```python
if gid in played:
    reasons.append("already played")
```

`playing.available_greetings` already passes `marks["played"] |
marks["completed"]` as `played`, so one line covers both marks.
`suggest.greeting_candidates` filters on `available` and inherits the fix, so
the LLM ranker stops seeing them too. Skipped greetings are still dropped from
the output entirely, as today.

The played set keeps its existing role in predecessor satisfaction — this is a
separate check against the same set, not a change to what `played` means.

### #316 / #89 — `direction` and `rank`

`post_scene_suggestions(cid, after=None, offscreen=False, direction: str = "",
rank: bool = True)`.

- `rank=False` skips `greeting_candidates` and `parse_greeting_picks`; the
  response carries `greeting_picks: []` and the frontend keeps the picks it
  already has rather than clobbering them with an empty list.
- `direction` is truncated to 500 characters (never rejected) and threaded into
  `suggest.build_prompt(snapshot, candidates, offscreen=…, direction=…)`. It
  renders as a new `templates/scene_suggestions/instruction/direction_addendum.j2`
  plus a distinctly-labelled block in `user.j2` — labelled so the model reads
  it as the GM's steer rather than as campaign data.
- `next_date` is still parsed on every call; a refresh adopts it when
  non-empty.

### #317 — `POST /campaigns/{cid}/scene-intent`

`computes_only`, `_require_connection`, `_bounded_call` — the same shape as
its neighbour `post_scene_suggestions`. Body `{text, offscreen}`; response
`{title, date, location, cast}` with `location` and `cast` resolved to names
for display, via the route's existing `_resolve_cast` and location-name map.
`offscreen` is carried because the picker already knows the mode and
`parse_intent` needs it to reject player tokens in a pcless scene, exactly as
`parse_output` does. The returned `title` pre-fills the confirm form's title
field; the user's typed text becomes the premise and is never replaced by it.

- Prompt: `suggest.build_intent_prompt(cid, typed, offscreen)` over the
  **full** `build_snapshot` — story-so-far included, because that is what
  resolves a phrase like "the morning after the funeral". To avoid restating
  the snapshot, `templates/scene_intent/user.j2` includes
  `scene_suggestions/user.j2` and appends the typed text as a labelled block.
- Parse: `suggest.parse_intent(reply, cid, offscreen)` — `reply` being the
  model's output, mirroring `parse_output`'s signature — reuses
  `_extract_json`, `_valid_ids`, and `_date_normalizer`. An unknown location
  becomes `""`, an unparseable date becomes `""`, invalid cast tokens are
  dropped, and a garbled reply yields empty fields. It never raises —
  extraction is a convenience, and a miss must degrade to a blank form.
- 400 on empty `text`; 502 on `LLMError`, matching `post_scene_suggestions`.
- Both new templates are registered in `scripts/verify_templates.py`
  alongside the existing `scene_suggestions` checks.

### #23 — the date applies instead of hinting

The confirmed date is written through `scenes.set_datetime`, so it passes
`calendars.normalize`, lands in `time_history`, stamps the start date into the
filename, and populates the "Today" context block — which is #23's acceptance
criterion. `create_scene(suggested_date=…)` still receives it too, so a
failure later in the sequence leaves the hint behind for `CastPanel`.

## Create sequence

Run by `SceneConfirmForm` for draft `d`. Both `startFromGreeting` and the
first `setSceneDatetime` rename the scene, so every step adopts the id the
previous one returned.

1. `createScene(cid, d.title, d.date, pcless)` → `sid`.
2. `source === "greeting"` → `startFromGreeting(cid, sid, d.gid)` → `sid`
   (renamed to the greeting's name).
   Otherwise → `addCastBatch` when `d.cast` is non-empty, then
   `setSceneLocation` when `d.location` is set.
3. Greeting source only: if `d.title` differs from the greeting name the draft
   was seeded with (`Availability.name`, so the comparison is client-side and
   needs no extra read), `renameScene(cid, sid, d.title)` → `sid`. This must
   come **after** step 2, which overwrites the title with that same greeting
   name. Other sources already got their title at step 1.
4. `d.date` non-empty → `setSceneDatetime(cid, sid, d.date)` → `sid`.
5. `onCreated(sid, source === "greeting" ? undefined : d.premise || undefined)`.

Ordering note: `rename_scene` preserves the date slug verbatim and
`_stamp_start_date` only stamps when there is not one, so steps 3 and 4 are
safe in either order. They are fixed in this order for one reason each —
step 3 must follow step 2, and step 4 is the one allowed to fail.

## Error handling

- **Steps 1–3 fail** → delete the half-seeded scene and surface the error,
  preserving the chooser's existing no-strays invariant.
- **Step 4 fails** → keep the scene. It is valid, just dateless, and #23 asks
  explicitly for the scene to open with the date unset rather than for the
  creation to fail.
- **Extraction fails or returns nothing** → confirm opens anyway, with the
  typed text as the premise and blank metadata, plus a hint saying so.
- **Suggestions fail** → unchanged: `suggestions` becomes `[]` and the picker
  degrades to greetings plus the custom card.
- **No LLM connection** → the Generated region and the Regenerate button are
  disabled with the existing hint; **Use this →** falls back to the blank-form
  path, so typing still works without a key.

## Testing

Backend (`backend/tests/`, `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`):

- A played greeting reports `available: false` with the new reason; a
  completed one does too; a skipped one is still absent.
- `start_from_greeting` on a played greeting raises `PlayError` → 409.
- `greeting_candidates` omits played greetings.
- `rank=false` returns `greeting_picks: []` and does not build candidates.
- A non-empty `direction` reaches the rendered prompt; an over-long one is
  truncated.
- `parse_intent` validation table: clean reply, unknown location id, unparseable
  date, invalid cast token, bare array, garbled JSON.
- `/scene-intent`: 400 on empty text, 502 on `LLMError`, names resolved on
  success.
- Existing tests that play a greeting and then read availability
  (`test_available_greetings_after_param`, `test_available_greetings_end_to_end`)
  are re-checked against the new semantics.

`scripts/verify_templates.py` gains checks for `scene_intent/{system,user}.j2`
and for the direction addendum, following the existing `scene_suggestions`
pattern.

Frontend (vitest run **from** `frontend/`, plus `npx tsc -b`):

- Each source emits the expected `SceneDraft`.
- Regenerate replaces only the generated cards, does not refetch greetings,
  and does not reorder them.
- Empty custom text opens confirm with no LLM call.
- Nothing is created until **Create scene**; Back and Cancel write nothing.
- Greeting path: the edited title survives, i.e. `renameScene` is called after
  `startFromGreeting` with the final id.
- A `setSceneDatetime` failure surfaces the error and still reports the scene.

Gate: `make check`.

## Issue coverage

| Issue | Resolved by |
|---|---|
| #315 | Self-exclusion in `availability()`; the ranker inherits it |
| #316 | `direction` parameter, its templates, and the picker's direction input |
| #317 | `POST /scene-intent` + the custom card's **Use this →** |
| #89 | Direction + Regenerate (`rank=false`) + the free-text custom card (Option B, no ledger) |
| #90 | `SceneConfirmForm`, metadata-only (Option A) |
| #23 | Step 4 of the create sequence: the confirmed date goes through `set_datetime` |
