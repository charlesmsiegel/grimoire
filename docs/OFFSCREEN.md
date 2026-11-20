# Offscreen faction plans (076)

`grimoire.offscreen` progresses an institution's adopted plan through campaign time while the player
is elsewhere. It is a headless first slice: one known faction goal advanced by explicit calendar
advances, under a bounded deterministic relevance budget. It calls no model, scans no roster and
delivers nothing to any character. `grimoire.offscreenstore` keeps the plans beside the 055 calendar
in the campaign's native scope record.

## Plans and adoption

A `FactionPlan` names one campaign group, the 057 decision that authorized it (`decision_id` and its
`choice`), an authored `goal`, one to 16 ordered `PlanStep(step_id, label, minutes)` values, a
`priority` (1–100, default 50) and optionally the `deadline_id` of a 055 calendar deadline the plan
races. Step durations are exact positive campaign minutes; there is no estimate or random spread.

```python
from grimoire.offscreen import FactionPlan, PlanStep
from grimoire.offscreenstore import OffscreenLibrary

plan = FactionPlan("docks", guild_ref, "seize-docks", "Seize the docks", "Control the harbour",
                   (PlanStep("coin", "Gather the bribe money", 5 * 1440),
                    PlanStep("magistrate", "Buy the harbour magistrate", 7 * 1440),
                    PlanStep("seize", "Take the docks by force", 10 * 1440)),
                   priority=80, deadline_id="festival")
offscreen = OffscreenLibrary(store, handle)
view = offscreen.adopt(scope, "adopt-docks", plan, institution=guild_state,
                       expected=offscreen.read(scope).etag).commit()
```

Adoption is where a plan's authority is checked, against a caller-owned snapshot. The caller passes
the institution's current `InstitutionState` (057 has no store yet); adoption refuses unless that
institution is the plan's group, it recorded a decision with `decision_id`, and that decision's
choice is the plan's `choice` (`decision_missing`, `decision_mismatch`). One decision authorizes one
plan (`decision_used`). Like 057's own snapshots, the state is trusted domain input: this library
cannot prove it came from `decide`, so trusted application code must supply it. An adoption retry
rechecks the decision. The plan starts at the calendar's **current**
moment, so each step's due moment is fixed at adoption: start plus the cumulative step minutes.
Adoption refuses when the calendar is not initialized (`uninitialized`), when an earlier calendar
advance has not been applied to the plans yet (`pending_skips`), for a duplicate plan id, a missing or
already-passed deadline, more than 32 plans, or a plan that runs past the calendar's range.

## Skips

A skip is one committed 055 calendar advance, identified by **that advance's own operation id**:

```python
change = clock.advance(scope, "travel", Duration(20 * 1440), expected=view.etag)
view = change.commit()
view = offscreen.read(scope)             # view.pending == ("travel",)
change = offscreen.advance(scope, "travel", budget=4, expected=view.etag)
change.receipt.developments              # coin (day 5) and magistrate (day 12); seize is still pending
view = change.commit()
```

Skips apply in calendar order, from the plans' current time (`skip_out_of_order` otherwise); a
calendar operation that is not an advance is refused (`not_an_advance`). Every calendar advance counts
as fictional time, whether the party travelled or played on screen; `OffscreenView.pending` lists the
advances not yet applied. The calendar and the plans are separate writes on purpose: an advance whose
skip has not landed yet is visible as pending, and applying it later flags exactly the steps it
crossed, so nothing has to be committed together.

A skip flags every pending step due at or before the advance's end, each with its own due moment, in
due order. A plan's step is never flagged twice and is never re-timed to the end of the trip. The
receipt is `Advanced(operation, before, after, developments, deferred, deadlines)`.

**Relevance is event driven.** A skip takes only the adopted plans and the calendar, never the
roster. A character with no plan, such as a distant shopkeeper, is never read, simulated or sent to a
model; importing `grimoire.offscreen` loads no provider, runtime, generation, inference, goal or
decision package (`test_offscreen.py` checks this transitively). Among the plans with a due step, a plan whose
calendar deadline this advance crossed comes first, then higher priority, then plan id. `budget`
(1–32, part of the skip's identity) is how many plans this skip may advance. The rest are listed in
`deferred`: their steps stay pending and are flagged by a later skip with their original due moments.
Plans with nothing due are not deferred; they cost a comparison.

When the advance crosses a plan's calendar deadline, `deadlines` reports
`DeadlineOutcome(plan_id, deadline_id, "met" | "missed")`: met when the plan's last step was due by
the deadline. The outcome is arithmetic over authored durations; it commits nothing.

## Authority, hidden developments and discovery

A `Development` is a director-facing record, like a crossed calendar deadline: it writes no
`Knowledge`, accepted effect, event, report or rumor, and no character learns of it. Surfacing a
development (a messenger, a rumor, a scene) uses the existing evidence and rumor contracts as a
separate, explicit act. Consequential outcomes, such as the docks actually changing hands, still need
the applicable effect authority. The reader is director data; actor-facing consumers must establish
perception separately.

## Storage, retries and recovery

The `offscreen` extension of `campaigns/<id>/scope.json` (schema `grimoire.offscreen/1`) holds the
campaign id, the plans' current moment and the ordered receipt log. Progress is not stored separately:
reading replays every receipt against the campaign's **current** calendar and refuses any receipt that
replay does not reproduce (`invalid_history`), including a skip whose calendar advance is no longer in
the calendar's history, or a hand edit that contradicts a later receipt. Replay cannot check what no
receipt has observed yet, such as the duration of a step no skip has reached: that is the user's
canonical JSON, as authored. Unknown fields and schemas fail closed; other scope fields and the calendar
extension survive writes, and calendar writes preserve this extension. Replay cannot recheck an
adoption's institutional decision, because institutions are not persisted; the receipt keeps the
decision id and choice as provenance.

Planning writes nothing. New operations need the reviewed etag (`StaleWrite` otherwise). An exact
retry returns the original receipt with `already_applied` and an empty preview; a changed request
under the same id is refused (`operation_mismatch`). Adoption ids are this package's own and skip ids are
calendar advance ids, so the two are looked up separately and never shadow each other: an advance
named like an earlier adoption is still pending and still applies. Neither is ever evicted, so a full
scope record refuses new operations instead of forgetting old ids. Native recovery leaves each write wholly before or after; reopen and retry the same id.

## Not in this slice

No UI (Wave 9's exit, 116), no model-backed or narrated developments, no scheduled events other than
plan steps, no causal branching, conditions or hidden-consequence trees (spec milestone 3), no
multi-process cost measurement (milestone 2), no plan withdrawal or editing, no persistent
institutions, and no automatic delivery of developments to anyone.
