# Played/completed greetings are excluded from new scenes

**Date:** 2026-07-17
**Status:** Approved

## Problem

When creating a new scene, greetings the campaign has already used should not be
offered again. Today the three campaign marks behave inconsistently:

- `skipped` ("won't do") — already excluded: `greetings.availability()` drops
  skipped greetings from its output entirely. Nothing to do.
- `played` (set automatically when a scene starts from the greeting) and
  `completed` ("done", marked off-screen) — **not excluded**. `availability()`
  uses the played/completed set only to unlock *successors*; the marked greeting
  itself stays `available: true`. Consequently:
  - the New Scene chooser (`NewSceneChooser.tsx`) offers it again,
  - the LLM greeting ranker (`suggest.greeting_candidates`) ranks it,
  - `playing.start_from_greeting` will replay it via the API.

## Decision (user-approved)

Enforce in the **backend availability layer**, as a **hard block** — no
intentional replay path. Clearing a `completed` mark (already supported in the
greetings UI) re-enables a greeting; `played` marks are fixed, so a played
greeting cannot start another scene in that campaign. "Hard" means enforced
server-side within the store's existing single-writer assumptions — see Known
limitations for the (pre-existing, accepted) concurrency caveat.

## Design

One change in `playing.available_greetings` (backend/src/grimoire/store/playing.py):
after the existing `mark` field is attached, a greeting whose mark is `played`
or `completed` becomes unavailable:

```python
for g in out:
    g["mark"] = mark_of.get(g["id"])
    if g["mark"] == "played":
        g["available"] = False
        g["reasons"].append("already played")
    elif g["mark"] == "completed":
        g["available"] = False
        g["reasons"].append("marked complete")
```

The pure `greetings.availability()` function is unchanged — it keeps computing
plot-gating (predecessors / excludes / tags) from the merged played∪completed
set, and the mark-based self-exclusion stays where the marks live.

All three consumers are fixed by this single change:

- **New Scene chooser** — already filters on `g.available`; marked greetings
  disappear with no frontend code change.
- **LLM ranking** — `suggest.greeting_candidates` already filters on
  `g["available"]`; marked greetings are no longer sent for ranking.
- **API replay** — `start_from_greeting` already validates against the
  `available` flag and raises `PlayError("greeting {gid} is not available")`;
  replay is now blocked server-side, not just hidden.

Note that a completed greeting can no longer be started directly (today,
starting one silently supersedes the mark with `played`). This is intended:
to play a completed greeting, clear its mark in the greetings sidebar first.

Ordering note: exclusion changes the *count* of startable greetings, which can
cross the chooser's `>2` LLM-ranking threshold and switch it between ranked
order and the backend's unlocked-first order. This is inherent to any
exclusion — `skipped` already behaves this way — not a new interaction.

### Companion fixes (regressions the hard block would otherwise introduce)

**Mark on success, not up front.** `start_from_greeting` currently writes the
immutable `played` mark *before* stamping, macro expansion, the opener append,
and the rename. Any failure in those later steps leaves the mark behind while
the chooser deletes its half-seeded scene — under the new rule the greeting
would be permanently unstartable. Move `_mark_played` to the end of
`start_from_greeting` (after the rename succeeds). The residual failure mode
inverts: if the mark write itself fails after the rename, the started (and
renamed) scene survives while the greeting stays unmarked and startable — the
chooser's cleanup deletes by the pre-rename id and will miss it, so the user
may end up with an orphaned started scene and the ability to start the
greeting again. That is recoverable by deleting a scene; the alternative
(mark-first) failure mode is a permanent, unclearable lockout. Every ordering
has one residual mode; this is the benign one.

**Purge marks on greeting delete.** Deleting a campaign-local greeting leaves
its id in `played.json` and (with no tombstone, since tombstones only cover
world ids) leaves the slug reusable — a recreated same-name greeting would
inherit an unclearable `played` mark and be permanently unavailable. Add
`playing.forget_greeting(cid, gid)` (drop the id from all three mark sets) and
call it from `overlay.delete_greeting` on every successful delete, via a lazy
import (precedent: `suggest.py` lazily imports `playing` to keep the import
graph flat). Purging is safe for successor unlocking: the delete already
removes the greeting's plotmap edges, so no predecessor list references it
afterwards.

**Fix the completed-mark hint.** `GreetingEditor`'s sidebar copy for
`completed` ("successors are unlocked") becomes incomplete once completion
also blocks starting. Extend it: "Marked complete: successors are unlocked;
it won't be offered for new scenes."

## Non-impacts

- `GreetingEditor` uses `listGreetings` (world/campaign record list), not the
  availability endpoint — mark badges and the mark-editing flow are untouched
  apart from the completed-hint copy fix above.
- `CampaignWizard` calls `availableGreetings` on a fresh campaign, where the
  mark sets are empty — behavior unchanged.
- Successor unlocking is unchanged: playing or completing a greeting still
  unlocks its `leads_to` targets; only the marked greeting itself is removed
  from the startable set.

## Known limitations (accepted, pre-existing)

- **No store-level locking.** The availability check and the `played.json`
  read-modify-write are not atomic; two truly concurrent starts could both
  pass the check, and concurrent mark writes can lose one. This race predates
  this change (it already affects successor unlocking) and the store has no
  locking anywhere; the app is single-user/local. Out of scope.
- **Stale chooser snapshots.** The chooser's availability and LLM-ranking
  fetches are point-in-time; a mark set in another tab after the fetch leaves
  a stale card. Clicking it fails the server-side availability check, the
  half-seeded scene is cleaned up, and the error banner shows — the server
  remains the authority. Also pre-existing; out of scope.
- **World-level slug reuse.** Deleting a greeting *from the world* goes through
  `greetings.delete_greeting` directly, not `overlay.delete_greeting`, so
  campaign mark sets are not purged (the world layer does not know its
  campaigns). If a world greeting is later recreated under the same name (the
  world's uniquify only checks current files), a campaign that played the
  original sees the recreated greeting as unavailable with an unclearable
  `played` mark. A blanket cross-campaign purge on world delete would be
  wrong: a campaign with a materialized plotmap keeps edges naming the deleted
  id, and purging its played mark would re-lock already-unlocked successors.
  Accepted as a limitation (stale marks on world slug reuse are pre-existing;
  this change only upgrades the symptom from a stale badge to unavailability).
  Escape hatch: create a campaign-side greeting with the same name — campaign
  uniquify treats the world id as taken, so it gets a fresh, startable id.

## Tests

Backend (`backend/tests/test_playing_store.py`, plus a route-level check if the
existing suite covers `/greetings/available` there):

- A greeting that has been played comes back `available: false` with reason
  `"already played"` (and its successors still unlock).
- A greeting marked `completed` comes back `available: false` with reason
  `"marked complete"`; clearing the mark restores `available: true`.
- `start_from_greeting` on a played greeting raises `PlayError`; same for a
  completed one.
- Legacy `played.json` (bare list of ids) also excludes and blocks — the
  hard block must hold for the legacy schema, not just the mark dict.
- A failure late in `start_from_greeting` (e.g. force `rename_scene` to raise)
  leaves the greeting unmarked and still startable.
- After `overlay.delete_greeting`, the gid is gone from all three mark sets;
  recreating a same-name campaign greeting yields a fresh, startable record.

Frontend (`NewSceneChooser.test.tsx`):

- Flip the test at the "marks tolerated" case (currently asserts a
  marked-complete greeting with `available: true` still renders): the server
  now sends marked greetings as `available: false`, and the chooser must not
  render them as cards.
- `GreetingEditor.test.tsx`: assert the updated completed-mark hint copy.

Placeholder names in fixtures follow the repo convention (Seraphine, Mara,
Winifred, Realm, Saltmarch) — never real content.
