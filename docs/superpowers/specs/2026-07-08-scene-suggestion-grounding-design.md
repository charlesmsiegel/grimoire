# Scene-suggestion grounding — design

**Date:** 2026-07-08
**Status:** approved, ready for planning
**Scope:** backend only (`store/suggest.py`, `templates/scene_suggestions/`).
Single LLM call — unchanged shape.

## Problem

Scene suggestions for the *hollow-manor* campaign's second scene are unusable.
Multiple proposals assume marisol winterbourne is present at the manor (she is an
**exiled** character who has never appeared), and one refers to her as a man
(she is a woman). This is not prompt-tuning noise — the grounding the model
would need simply is not in the prompt.

### Root cause

`suggest.build_snapshot()` hands the model the campaign's entire cast as
**bare name/token pairs**:

```python
available_cast.append({"token": tok, "name": c.get("name", c["id"])})
```

- **No per-character facts.** marisol appears only in `available_cast` (she has
  no appearance record, so the `absent_cast` list — which *does* carry taglines
  — excludes her). The model sees `characters:marisol = marisol winterbourne` and
  nothing else: no gender, no status, no one-line description. So it places her
  in the room and guesses "he." Every character's one-line tagline already
  exists on disk (`characters/<id>/tagline.md`) and goes unused at the one
  moment it is needed.
- **No current situation.** The scene-1 summary sits in `chronicle.json`
  (`one_line`, `summary`, `location`) but is never included. The model has no
  narrative anchor for "where things stand right now."
- **No thread staleness.** Open plot threads *are* sent (`title`, `status`,
  `latest_beat`) and `open_threads()` already sorts them least-recently-advanced
  first — but nothing tells the model *how long* a thread has been neglected, so
  it cannot deliberately choose to revive a cold one.
- **The instruction pushes the wrong way.** `instruction/standard.j2` tells the
  model to "revisit a long-absent character," actively encouraging it to grab
  distant names it knows nothing about.

## Goals

1. Every castable character carries its **tagline** and a **presence status**
   so the model stops hallucinating who is present and mis-gendering them.
2. The model sees a **story-so-far** anchor (recent scene one-liners).
3. Open threads carry a **dormancy** signal so cold threads can be revived
   deliberately.
4. The instruction is reframed to respect presence status instead of pushing
   the model toward unknown characters.

## Non-goals (explicitly out of scope)

- **Authored character groups / co-appearance ("Layer 2").** A better cast
  backbone, but it belongs on top of the `factions` entity kind planned in the
  mechanics-dice roadmap (Phase 0, #693). Not built here.
- **A structured gender/pronoun field.** Cards have no gender field; gender is
  carried implicitly by the tagline (marisol's says "mother"). Accepted limit: a
  tagline that omits gender may still be guessed wrong. Rare; deferred.
- **Relationship state.** `relationships.json` renders feelings *between a known
  present cast*; at suggestion-time there is no fixed cast, so it fits awkwardly.
  Deferred.
- **A second LLM call / pre-pass.** Assembly stays deterministic, single-call.

## Design

All changes are additive assembly in `build_snapshot()` plus the two templates
that render it. `parse_output()` is untouched — cast/location validation still
runs against the campaign's real ids, so annotating the list cannot break
validation.

### Snapshot (`suggest.build_snapshot`)

New/changed return keys:

- **`story_so_far`** — up to the 3 most recent chronicle records, **newest
  first**, each `{one_line, location, date}`. Fetched by widening the existing
  `chronicle.recent(cid, 1)` (used for `now`) to `recent(cid, 3)`; `now` is
  still the latest record's date.

- **`cast`** — a single unified list replacing `absent_cast` **and**
  `available_cast`. Same population as today (`characters.list_characters` +,
  for non-offscreen, role=player actors), each entry now:

  ```python
  {"token": "characters:marisol", "name": "marisol winterbourne",
   "tagline": "vivienne's exiled mother and the story's patient spider…",
   "status": "unseen", "role": "npc"}
  ```

  `status` is one of:
  - **`present`** — the actor is in the **most recent scene** (its ref appears
    in `recent[-1]["cast"]`, matched as `characters/<id>` / `pcs/<id>`).
  - **`appeared`** — has an appearance record (in `appearances.roster`) but is
    not in the most recent scene (offstage; this subsumes the old
    `absent_cast`).
  - **`unseen`** — no appearance record (never on screen).

  Taglines come from `taglines.read(croot, id)` for characters (already used for
  `absent_cast`; tolerant of a missing file → `""`). The player PC needs no
  tagline — it is marked by `role: "player"` and rendered as the player
  character. Offscreen suggestions still omit the player entirely (unchanged).

- **`open_threads`** — each entry gains **`dormancy`**: the number of scenes
  that have occurred **after** the thread's `last_scene`. Computed from the
  chronological scene-id order (`sorted(chronicle.read_chronicle(cid).keys())`):
  `dormancy = count of scene ids ordered after last_scene`. `0` = advanced in
  the most recent scene; a missing/unknown `last_scene` sorts as maximally cold.
  Existing stale-first ordering from `plot.open_threads()` is kept.

Unchanged keys: `now`, `friendly`, `holidays_today`, `upcoming`, `birthdays`,
`available_locations`.

### Prompt (`templates/scene_suggestions/`)

**`user.j2`** — replace the `absent_cast` + `available_cast` sections with:

- A **Story so far** block (skipped when empty): one line per recent scene,
  `— <one_line> (<location>, <date>)`, newest first.
- A **cast** block grouped by status, so it reads as a stage direction. Only
  non-empty groups render:

  ```
  In the most recent scene (present):
  - characters:vivienne-winterbourne = vivienne winterbourne — cold countess who set the household rules
  - pcs:julian = julian (the player character)
  Appeared earlier, now offstage:
  - (rendered only if any)
  Not yet appeared — introduce only with an in-world reason:
  - characters:marisol = marisol winterbourne — vivienne's exiled mother and the story's patient spider…
  ```

  Tokens are still explicit (`<kind>:<id> = Name`) so the model reuses the exact
  id the parser validates against.

- The **Open plot threads** block annotates dormancy, e.g.
  `- <title> (advanced last scene) — <latest_beat>` /
  `- <title> (cold — N scenes) — <latest_beat>`.

**`instruction/standard.j2`** — reframe the goal line. Instead of "revisit a
long-absent character," instruct the model to:
- advance an open plot thread (cold threads are especially worth reviving),
  reintroduce an offstage character, or reach an upcoming date/birthday;
- **never assume a character is present unless listed under "present"**; an
  offstage or not-yet-appeared character may be introduced only with a plausible
  in-world reason for their arrival;
- respect each character's tagline for who they are, including gender.

The `offscreen` variant gets the same presence discipline, minus the player.
`date_addendum.j2` / `rank_addendum.j2` are unchanged.

## Testing

`backend/tests/test_suggest_store.py`:

- `build_snapshot` classifies a most-recent-scene character as `present`, an
  earlier-only character as `appeared`, and a never-appeared character (marisol)
  as `unseen`, **each carrying its tagline**.
- `story_so_far` carries the most recent scene's `one_line` (+ location, date),
  newest first, capped at 3.
- `open_threads` entries carry a `dormancy` count: 0 for a thread advanced in
  the most recent scene, N for one last advanced N scenes back.
- `build_prompt` renders the grouped present/offstage/unseen sections, the
  story-so-far block, dormancy annotations, and the "never assume present"
  instruction; distant `unseen` characters render with their taglines under the
  unseen heading.

No frontend change — the suggestions API response and its rendering are
unchanged.

## Files touched

- `backend/src/grimoire/store/suggest.py` — `build_snapshot` assembly (+ small
  helpers for status classification and thread dormancy).
- `templates/scene_suggestions/user.j2` — story-so-far, grouped cast, dormancy.
- `templates/scene_suggestions/instruction/standard.j2` and `offscreen.j2` —
  reframed goal + presence discipline.
- `backend/tests/test_suggest_store.py` — coverage above.
