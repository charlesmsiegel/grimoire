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
exclusion at the store **and the recovery path that exclusion makes
necessary**, a `direction` and `rank` parameter on the suggestions route, a
new scene-intent extraction route and its templates, applying the confirmed
date through `set_datetime`, and one adjacent correctness fix in
`suggest.py`'s offscreen filtering (below).

Out: the Scene Ledger (#88 — nothing new is persisted here; #89 Option B is
deliberately the no-storage variant), adapted-greeting first posts (#91),
per-slot regeneration (#89 Option C), a backend scene-draft resource (#90
Option C), and scene import (#92, #93).

## Decisions

Four calls shape everything below; each had a plausible alternative.

1. **#315 is fixed in the store, not the presentation layer.** Played and
   completed greetings become `available: false`. Consequence, accepted
   deliberately: `start_from_greeting`'s existing guard turns replay into a
   409, so a greeting cannot be replayed through the API at all. This is what
   forces the recovery path below — read it before implementing the one-liner.
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

`SceneDraft` is the seam. It is a discriminated union so that no path can
construct a state the create sequence cannot execute:

```ts
type DraftBase = {
  title: string;                     // trimmed; never empty (see Validation)
  date: string;                      // native notation as typed/proposed, not
                                     // yet normalized — set_datetime canonicalizes
  location: string;                  // location id, "" if none
  pcless: boolean;                   // the chosen mode travels with the draft
};

type SceneDraft =
  | (DraftBase & { source: "greeting"; gid: string })
  | (DraftBase & { source: "generated" | "custom";
                   premise: string;
                   cast: { kind: "characters" | "pcs"; id: string; name: string }[] });
```

Greeting drafts carry no `premise` and no `cast` **by construction**: the
greeting body *is* the first post, and `start_from_greeting` seats the
greeting's `present` set under locked-version rules that a form must not
re-implement. `Availability` carries no cast either, so there is nothing to
display — the confirm pane says so in a hint rather than showing empty chips.

Cast entries carry no `role`: `addCastBatch` is called exactly as today
(`kind` + `id`), and roles keep whatever the backend defaults to. Editing
roles stays in `CastPanel`.

### Components

`NewSceneChooser.tsx` (155 lines today) splits four ways. It roughly triples
in content otherwise, and the create sequence's ordering hazards should not
live interleaved with rendering.

- **`NewSceneChooser.tsx`** — orchestrator. Owns `mode`, `step`, the error
  banner, the backdrop, and Escape handling. Renders one of the two panes.
- **`SceneIdeaPicker.tsx`** — the pick pane. Emits a `SceneDraft`; writes
  nothing.
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
  scene** and opens confirm with a blank draft and no LLM call. This replaces
  today's "Create manually" button. (#317, #89)

### Draft construction

Every field's origin is fixed here, because "an empty draft" is the kind of
phrase two implementers read differently — and reading it as `date: ""` would
silently drop today's behavior, where *every* path seeds `nextDate`.

| | title | date | location | cast | premise |
|---|---|---|---|---|---|
| greeting | `Availability.name` | `nextDate` | `""` | — | — |
| generated | `suggestion.title` | `suggestion.date \|\| nextDate` | `suggestion.location?.id ?? ""` | `suggestion.cast` | `suggestion.premise` |
| custom | `intent.title \|\| "New scene"` | `intent.date \|\| nextDate` | `intent.location?.id ?? ""` | `intent.cast` | the typed text, verbatim |
| blank | `"New scene"` | `nextDate` | `""` | `[]` | `""` |

The typed text is always the premise; the extraction's job is metadata only,
and it never replaces what the user wrote.

### The confirm pane

Title, date (`CalendarDatePicker`), and location `<select>` over campaign
locations, for every source. Cast chips and a premise textarea for `generated`
and `custom` only; for `greeting`, a hint reading that the greeting supplies
the opening post and seats its own cast.

Nothing is written until **Create scene**; **Back** returns to the picker and
**Cancel** closes, both without writing. For a pcless draft the cast picker
offers no players, matching the `PlayError` guards in `start_from_greeting`.

**Every enabled control must change persisted or handed-off state.** That rule
is why location is applied for greeting drafts too (below), and why premise
and cast are absent rather than merely disabled for them.

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

Verified blast radius: `availability()`'s output reaches
`playing.available_greetings` (→ `NewSceneChooser`, `CampaignWizard`),
`suggest.greeting_candidates`, and `start_from_greeting`'s guard, and nothing
else. `GreetingEditor` reads neither `available` nor `reasons` — it lists
greetings through the ordinary greeting endpoints — so **the mark UI keeps
working on a greeting this change makes unavailable**, which is what the
recovery path below depends on. `CampaignWizard` runs against a
freshly-created campaign where nothing is played yet.

### #315 — the recovery path this makes necessary

**This is the part that must not be skipped.** Today a played greeting is
merely redundant; after the change it is *unstartable*, and three existing
behaviors combine into a trap:

- `start_from_greeting` calls `_mark_played` at `playing.py:132`, **before**
  `stamp_greeting`, macro expansion, `append_reply`, and `rename_scene`. A
  failure in any of those leaves the greeting marked played.
- The chooser's cleanup deletes the half-seeded scene, and `delete_scene`
  does not clear the mark — nothing does.
- `mark_greeting` explicitly refuses to change a played mark
  (`playing.py:61-62`, "greeting was played in a scene; its mark cannot be
  changed").

So an interrupted greeting start permanently burns the greeting, with no UI to
recover it. Two changes, both small:

1. **Move `_mark_played` to just before the retitle**, after `append_reply`.
   Nothing between them reads the mark, so this is a pure reordering, and it
   shrinks the window to the single call that the existing comment already
   documents as the safe-to-fail one ("retitle last: any earlier failure
   leaves the caller's sid valid for cleanup").
2. **Let `mark_greeting(cid, gid, "none")` clear a played mark when no scene
   stamps that greeting.** The refusal is right when a scene records the play
   and wrong when none does — that state is exactly the orphaned mark. The
   check needs the greeting stamp per scene, so `scenes.read.list_scenes`
   gains `"greeting": meta.get("greeting", "")` in its projection; it already
   does a head-only frontmatter parse, so this costs nothing. The refusal
   message stays for the case where a scene *does* stamp it.

### #316 / #89 — `direction` and `rank`

`post_scene_suggestions(cid, after=None, offscreen=False, direction: str = "",
rank: bool = True)`.

- `rank=False` skips `greeting_candidates` and `parse_greeting_picks`; the
  response carries `greeting_picks: []` and the frontend keeps the picks it
  already has rather than clobbering them with an empty list.
- `direction` is truncated to 500 characters (never rejected) and threaded
  into `suggest.build_prompt(snapshot, candidates, offscreen=…, direction=…)`.
  It renders as a new
  `templates/scene_suggestions/instruction/direction_addendum.j2` plus a
  distinctly-labelled block in `user.j2` — labelled so the model reads it as
  the GM's steer rather than as campaign data.
- `next_date` is parsed on every call; a refresh adopts it only when
  non-empty, so a refresh that returns none does not clear a good estimate.

### #317 — `POST /campaigns/{cid}/scene-intent`

`computes_only`, `_require_connection`, `_bounded_call` — the same shape as
its neighbour `post_scene_suggestions`.

Request: `{"text": str, "offscreen": bool}`. `text` is trimmed and truncated
to 2000 characters; empty after trimming → 400. `offscreen` is carried because
the picker already knows the mode and the parser needs it to filter player
tokens.

Response, mirroring `post_scene_suggestions`' shapes exactly so the frontend
reuses one converter:

```json
{"title": "", "date": "", 
 "location": {"id": "keep", "name": "The Keep"} | null,
 "cast": [{"kind": "characters", "id": "mara", "name": "Mara"}]}
```

- Prompt: `suggest.build_intent_prompt(cid, typed, offscreen)` over the
  **full** `build_snapshot` — story-so-far included, because that is what
  resolves a phrase like "the morning after the funeral". To avoid restating
  the snapshot, `templates/scene_intent/user.j2` includes
  `scene_suggestions/user.j2` and appends the typed text as a labelled block.
- Parse: `suggest.parse_intent(reply, cid, offscreen)` — `reply` being the
  model's output, mirroring `parse_output`'s signature — reuses
  `_extract_json`, `_valid_ids`, and `_date_normalizer`. An unknown location
  becomes `""`, an unparseable date becomes `""`, invalid cast tokens are
  dropped. A bare top-level array (which `_extract_json` accepts) takes its
  first object element and ignores the rest; anything else yields all-empty
  fields.
- **Malformed or semantically invalid model output never raises** — extraction
  is a convenience and a miss must degrade to a blank form. Store and calendar
  failures underneath (`_valid_ids` reads entities; `_date_normalizer` imports
  a user-authored provider) are *not* covered by that guarantee and surface as
  the route's ordinary 500, exactly as they do for `post_scene_suggestions`.
- 502 on `LLMError`, matching its neighbour.
- Both new templates are registered in `scripts/verify_templates.py`
  alongside the existing `scene_suggestions` checks.

### Adjacent fix — offscreen filtering in `suggest.py`

`_valid_token` accepts any `characters:<id>` present in the campaign before it
consults `player_tokens`:

```python
if kind == "characters" and aid in char_ids:
    return True
return not offscreen and tok in player_tokens
```

A player character seated as a `characters`-kind actor (which `CastPanel`
allows — its role selector offers `player` for characters) therefore passes
the offscreen filter, and `build_snapshot`'s `list_characters` loop offers it
to the model in the first place, labelled "(the player character)". The
offscreen guarantee is already weaker than it reads.

This is pre-existing, but #316 and #317 both aim user intent at that filter —
a direction like "what they do while she sleeps" must not cast her — so it is
fixed here: exclude roster player tokens in `_valid_token` regardless of
`kind`, and omit them from `build_snapshot`'s cast when `offscreen`. Tests
cover a player whose kind is `characters`, not only a `pcs` one.

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

1. `createScene(cid, d.title, d.date, d.pcless)` → `sid`.
2. `d.source === "greeting"` → `startFromGreeting(cid, sid, d.gid)` → `sid`
   (renamed to the greeting's name); then **unconditionally**
   `renameScene(cid, sid, d.title)` → `sid`. Unconditional rather than
   compared against the seeded name: the draft does not retain that name after
   editing, and the backend may have renamed the greeting since the picker
   loaded, so any comparison races. Renaming to the confirmed title always
   produces what the form showed.
   Otherwise → `addCastBatch` when `d.cast` is non-empty.
3. `d.location` non-empty → `setSceneLocation(cid, sid, d.location)`. **All
   sources**, greeting included — the confirm pane offers the control to every
   draft, so every draft must honor it.
4. `d.date` non-empty → `setSceneDatetime(cid, sid, d.date)` → `sid`.
5. `onCreated(sid, d.source === "greeting" ? undefined : d.premise || undefined)`.

Ordering note: `rename_scene` preserves the date slug verbatim and
`_stamp_start_date` only stamps when there is not one, so the two renaming
steps are safe in either order. They are fixed in this order for one reason
each — the rename must follow `startFromGreeting`, which overwrites the title,
and step 4 is the one step allowed to fail.

## Error handling

- **Steps 1–3 fail** → delete the half-seeded scene and surface the error in
  the confirm pane, which stays open; the picker refetches greetings on the
  way back, so a greeting another client played in the meantime disappears
  rather than failing twice.
- **Step 4 fails** → the scene is kept and `onCreated(sid)` is still called
  with the id from step 3, so the user lands in a valid, dateless scene and
  can set the date in `CastPanel`, whose box is pre-filled from the
  `suggested_date` written at step 1. Because `onCreated` navigates and closes
  the chooser, the error cannot be shown in the confirm pane's banner — it is
  reported through the parent's existing error surface in `CampaignView`. This
  is #23's stated fallback: the scene opens with the date unset rather than
  the creation failing.
- **Extraction fails or returns nothing** → confirm opens anyway with the
  blank-draft defaults and the typed text as the premise, plus a hint saying
  metadata could not be inferred.
- **Suggestions fail** → unchanged: `suggestions` becomes `[]` and the picker
  degrades to greetings plus the custom card.
- **No LLM connection** → the Generated region and Regenerate are disabled
  with the existing hint; **Use this →** falls back to the blank-draft path,
  so typing still works without a key. Inference is an enhancement, not a
  requirement, of the custom path.

### Request ordering

`useSceneSuggestions` carries a monotonic request id and applies only the
newest response for the current mode — the same discipline `CampaignView`'s
`sceneListSeq` already uses for scene lists. Without it the initial ranked
fetch can land after a regenerate and overwrite the directed result, and two
regenerates can resolve out of order. Regenerate is disabled while any
suggestions request is in flight.

## Accepted risks

Stated rather than solved, because each is today's behavior and none is made
worse in kind by this work:

- **`store.playing` takes no campaign lock** — it sits in
  `locks.UNREVIEWED`. `start_from_greeting` checks availability and then
  read-modify-writes `played.json`, so two concurrent starts of the same
  greeting can both pass the guard. #315 raises the *cost* of losing that race
  (a burned greeting rather than a duplicate scene), which is precisely why
  the recovery path above is in scope. Moving `store.playing` out of
  `UNREVIEWED` is a welcome follow-up and is not attempted here.
- **Scene creation is client-orchestrated across five calls**, so the scene is
  visible to other clients between them, and cleanup deletes a scene that a
  concurrent client could have touched. That is exactly today's shape; the
  confirm step reduces how often the sequence runs with wrong data rather than
  changing its transactionality. A single backend orchestration endpoint is
  the real fix and is #90 Option C, explicitly out of scope.
- **Ambiguous-commit on a renaming call** (the server renames, the response is
  lost) leaves the client holding a stale sid. `CastPanel.applyDatetime` has
  the same exposure today and documents it under #95.

## Validation

- `d.title` is trimmed; an empty title falls back to the source default from
  the construction table, so `renameScene` is never called with `""` and no
  path depends on `post_scene`'s own `"New scene"` coercion.
- `d.date` is whatever `CalendarDatePicker` produced or the model proposed. It
  is *not* canonical until `set_datetime` normalizes it — the spec calls it
  native-notation input, and step 4 is where validation actually happens.
- `d.location` is a location id chosen from the loaded list; a location
  deleted between load and Create fails at step 3 and is reported.

## Testing

Backend (`backend/tests/`, `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`):

- A played greeting reports `available: false` with the new reason; a
  completed one does too; a skipped one is still absent.
- `start_from_greeting` on a played greeting raises `PlayError` → 409.
- `greeting_candidates` omits played greetings.
- `_mark_played` runs after the body is appended: a failure injected at the
  retitle leaves the mark set, and one injected before `append_reply` does
  not.
- `mark_greeting(…, "none")` clears an orphaned played mark; it still refuses
  when a scene stamps that greeting; `list_scenes` exposes the stamp.
- `rank=false` returns `greeting_picks: []` and does not build candidates.
- A non-empty `direction` reaches the rendered prompt; an over-long one is
  truncated to 500 characters.
- Offscreen filtering: a player seated as `characters:<id>` is absent from an
  offscreen snapshot and rejected by `parse_output` and `parse_intent`.
- `parse_intent` table: clean reply, unknown location id, unparseable date,
  invalid cast token, bare array, garbled JSON.
- `/scene-intent`: 400 on empty or whitespace text, 502 on `LLMError`, names
  resolved on success, `offscreen` honored.
- Existing tests that play a greeting and then read availability
  (`test_available_greetings_after_param`,
  `test_available_greetings_end_to_end`) are re-checked against the new
  semantics.

`scripts/verify_templates.py` gains checks for `scene_intent/{system,user}.j2`
and for the direction addendum, following the existing `scene_suggestions`
pattern.

Frontend (vitest run **from** `frontend/`, plus `npx tsc -b`):

- Each source emits the expected `SceneDraft`, including the date-fallback
  column of the construction table.
- Regenerate replaces only the generated cards, does not refetch greetings,
  and does not reorder them; a stale response that resolves after a newer one
  is discarded.
- Empty custom text opens confirm with no LLM call.
- Nothing is created until **Create scene**; Back and Cancel write nothing.
- Greeting path: the confirmed title survives `startFromGreeting`, and an
  edited location is applied.
- Every enabled confirm control is asserted to reach an API call; the greeting
  pane renders no premise or cast control.
- A `setSceneDatetime` failure still reports the scene and does not delete it.

Gate: `make check`.

## Issue coverage

Stated as what ships, including where it is deliberately narrower than the
issue's wording.

| Issue | Delivered | Deliberately not |
|---|---|---|
| #315 | Played/completed greetings are excluded at the store, so cards and the LLM ranker both stop offering them; an orphaned mark is recoverable | Concurrent double-start still races (`store.playing` holds no lock); an already-open picker keeps its loaded list until the next fetch |
| #316 | A free-text direction steers the generated slots, with ordered requests | The direction does not re-rank greetings (decision 4) and is not persisted |
| #317 | Typed text yields a date, location, cast, and title, validated against the campaign; the typed text is preserved as the premise | Without an LLM connection the custom path creates a blank draft and infers nothing |
| #89 | Refresh control (`rank=false`, no card shuffle) and a free-text custom card | No ledger; unpicked suggestions are still lost on close (#88) |
| #90 | Title, date, location editable for every source; cast and premise for generated/custom; nothing written until Create | Greeting drafts expose no cast, premise, or first-post-source choice — the greeting body is the post and the backend seats its cast (decision 2) |
| #23 | The confirmed date is applied through `set_datetime`, populating `time_history` and the "Today" block, with a dateless-scene fallback on failure | The model still emits dates as parsed JSON fields, not tool calls |
