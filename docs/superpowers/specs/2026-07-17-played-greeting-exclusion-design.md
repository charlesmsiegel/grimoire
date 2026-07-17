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

Enforce in the **backend availability layer**, as a **hard block** — no replay
escape hatch. Clearing a `completed` mark (already supported in the greetings
UI) re-enables a greeting; `played` marks are fixed, so a played greeting can
never start another scene in that campaign.

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

Ordering note: the self-exclusion runs before the `unlocked` sort, which only
reorders — no interaction.

## Non-impacts

- `GreetingEditor` uses `listGreetings` (world/campaign record list), not the
  availability endpoint — mark badges and the mark-editing sidebar are
  untouched. Its hint text ("Marked complete: successors are unlocked.") gains
  an extra true meaning (also hidden from new scenes) but needs no change;
  the `skipped` hint already says "hidden from new scenes".
- `CampaignWizard` calls `availableGreetings` on a fresh campaign, where the
  mark sets are empty — behavior unchanged.
- Successor unlocking is unchanged: playing or completing a greeting still
  unlocks its `leads_to` targets; only the marked greeting itself is removed
  from the startable set.

## Tests

Backend (`backend/tests/test_playing_store.py`, plus a route-level check if the
existing suite covers `/greetings/available` there):

- A greeting that has been played comes back `available: false` with reason
  `"already played"` (and its successors still unlock).
- A greeting marked `completed` comes back `available: false` with reason
  `"marked complete"`; clearing the mark restores `available: true`.
- `start_from_greeting` on a played greeting raises `PlayError`.

Frontend (`NewSceneChooser.test.tsx`):

- Flip the test at the "marks tolerated" case (currently asserts a
  marked-complete greeting with `available: true` still renders): the server
  now sends marked greetings as `available: false`, and the chooser must not
  render them as cards.

Placeholder names in fixtures follow the repo convention (Seraphine, Mara,
Winifred, Realm, Saltmarch) — never real content.
