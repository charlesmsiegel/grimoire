# Downtime and long activities (074)

`grimoire.downtimestore.DowntimeLibrary` is a headless interface for long activities resolved in
compressed narration: weeks of study, research, training, recovery, crafting or travel that spend
campaign time as one explicit, reviewable decision instead of an unexplained jump. A pursuit spends
an exact span of 055 calendar time and records the activity's progress in the same write, once per
operation id. It makes no model call, runs no offscreen simulation and changes no older scene or
transcript. There is no downtime UI yet; Wave 9's sequential UI exit owns those controls.

## Begin, pursue, complete

Give trusted application code an open `LibraryStore` and its scoped apply handle:

```python
from grimoire.calendars import Duration
from grimoire.core.identity import InstanceRef, ScopeRef
from grimoire.downtime import Activity
from grimoire.downtimestore import DowntimeLibrary

downtime = DowntimeLibrary(store, handle)
scope = ScopeRef("saltmarch")
view = downtime.read(scope)  # no writes; activities are empty until one begins
study = Activity("tide-cant", InstanceRef("saltmarch", "character", "mara"), "study",
                 "Learn the harbor's tide cant", required_minutes=20 * 1440)
view = downtime.begin(scope, "begin-tide-cant", study, expected=view.etag).commit()
change = downtime.pursue(scope, "study-twenty", "tide-cant", Duration(20 * 1440),
                         note="Mara can follow the tide cant at the quay", expected=view.etag)
change.preview()        # the exact native bytes: one scope record holding both time and progress
view = change.commit()  # a trusted handle, or the native store's exact-byte approvals
# view.calendar.now is twenty days later; view.state.activity("tide-cant").status is "complete".
```

An `Activity` is one actor's bounded intent: an id, the campaign actor (`InstanceRef`), a kind
(`study`, `research`, `training`, `recovery`, `crafting`, `travel` or `other`), the intent in words,
and `required_minutes` of **effort**. Effort is not elapsed time. `pursue(..., elapsed,
effort_minutes=...)` spends `elapsed` campaign time and credits `effort_minutes` of it (all of it by
default; never more). Two hours a day of language study on a ten-day road is
`pursue(scope, op, "road-cant", Duration(10 * 1440), effort_minutes=20 * 60, ...)`. The kind is
descriptive; this slice resolves every kind the same way. A ruleset or the director decides the
effort requirement before the activity begins.

Progress accumulates across pursuits. The pursuit that meets the requirement completes the activity,
records its `note` as the activity's outcome, and records completion at the end of that stretch
(within a stretch of partial effort, the exact hour it was met is unknown). Effort beyond what
remains is not credited, but the stretch's time still passes. `abandon` stops an active activity,
keeping its progress and recording when and why; it spends no time. A completed or abandoned
activity accepts no further pursuit. Each receipt reports the effort credited, whether it completed
the activity, the calendar span and any deadlines that span crossed.

`begin` needs an actor the campaign has (an instance id two kinds share is no one actor); it does
not need a calendar. `pursue` needs the campaign calendar to be initialized and an exact span:
an estimate such as `Duration(5 * 1440, 9 * 1440, note="About a week")` is refused until a value
inside its bounds is selected, and no midpoint is guessed. A zero span is refused, since a pursuit
that spends no time is not one.

## Once, and only with the calendar

A pursuit's calendar side is a 055 `Advance` with the pursuit's own operation id. Both land in one
scope record, so the native store's preview, authorization, compare-and-swap, journal and recovery
cover them together: a crash leaves neither or both. Retry by calling the same method with the same
operation id and request, after a crash, a reopen or later operations: the retained receipt comes
back with `already_applied=True`, an empty preview, and no time or progress added. A different
request reusing an id is refused (`operation_mismatch`), as is a pursuit reusing an id the calendar
already holds, since the calendar's own retry rule would let it credit study for no new time.

Deadlines crossed by a pursuit are flagged exactly as a plain 055 advance flags them, and listed on
the receipt. A flag invokes nothing and does not interrupt the activity: deciding that play should
stop for a deadline, an encounter or a shortage is later work.

## Authority, conflicts and recovery

`campaigns/<id>/scope.json` gains one `downtime` extension with schema `grimoire.downtime/1`, beside
`calendar`. It holds every activity's current progress and the full ordered operation receipts, as
readable canonical JSON. Reads replay every receipt through the same rules that wrote it and check
the stored activities against the replay. Each pursuit must match the calendar receipt with its id,
in the calendar's order, and each begin or abandon must be dated to a time the calendar reached. An
inconsistent hand edit (study whose time the calendar lacks, progress or receipts that replay does
not produce, reordered pursuits, mistyped numbers or flags, unknown fields or schemas) fails closed,
without repair or writes. A pursuit's advance is an ordinary 055 advance, so a coherent manual
replacement of both histories, such as deleting a pursuit together with its progress, is outside
the receipt guarantee, as it is for the calendar. Plain calendar
operations and ordinary scope edits preserve the extension. The log is not pruned: when the scope
reaches the native file limit, new operations are refused rather than forgetting old ids.

As with the calendar, sync compares the whole scope file. Study done on two disconnected devices is
kept as a conflict, never summed; reads, planning and commit refuse until the explicit keep-mine or
keep-peer resolution chooses one lineage. Exactly-once applies to that kept lineage: a plan made
before the resolution for an operation it discarded is refused (`history_changed`), but replanning
that operation afterwards is a new request against the kept history and would spend its time again.
A client must not automatically resubmit an operation that an intentional resolution or manual
restore discarded; the person decides whether that study happened. The reader is director-facing canonical data,
not an actor's knowledge feed. Existing scopes without the extension remain valid; removing this
code leaves it as opaque data for clients that preserve unknown scope fields.

## Not in this slice

Food, supplies and other resource checks; deadlines that interrupt or partially resolve a stretch;
overlapping commitments and several activities in one stretch; pauses and resumption; ruleset study
tables such as Ars Magica seasons; played-out interim scenes; and checking that the actor's
controller, rather than the director, chose the activity. Each is a later milestone of the sprint.

Tests: `test_downtime.py`, `test_downtimestore.py`, `test_downtimestore_sync.py` and
`test_downtimestore_recovery.py` use only synthetic native records. They cover the 20-day study
proof with a deadline crossing and retries after restart and later calendar operations, partial
effort during travel across two stretches, estimates, abandonment, refusals that write nothing,
concurrent and stale plans, authorization, replay tampering, offline conflict resolution and native
crash/I/O-error recovery. These are headless proofs, not application UI acceptance.
