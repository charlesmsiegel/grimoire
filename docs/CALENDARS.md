# Campaign calendars and deadline clocks (055)

`grimoire.calendarstore.CalendarLibrary` is a headless interface for explicit campaign-local time.
It advances a campaign once per operation id and flags deadlines crossed by that advancement. It
makes no model call, runs no offscreen simulation, and never changes a previously stored scene or
transcript date. There is no calendar UI yet; Wave 8's sequential UI exit owns those controls.

## Read, preview and commit

Give trusted application code an open `LibraryStore` and its scoped apply handle:

```python
from grimoire.calendars import CalendarDefinition, Duration
from grimoire.calendarstore import CalendarLibrary
from grimoire.core.identity import ScopeRef

clock = CalendarLibrary(store, handle)
scope = ScopeRef("saltmarch")
view = clock.read(scope)  # no writes; state is None until explicitly initialized
change = clock.initialize(scope, "start-clock", CalendarDefinition(), "2024-02-20",
                          minute=23 * 60 + 30, expected=view.etag)
change.preview()  # exact native bytes, for review/approval
view = change.commit()  # a trusted handle, or the native store's exact-byte approvals

view = clock.schedule(scope, "schedule-meeting", "meeting", "2024-03-01",
                      minute=23 * 60 + 30, label="Meet at the gate",
                      expected=view.etag).commit()
change = clock.advance(scope, "move-twenty-days", Duration(20 * 1440), expected=view.etag)
view = change.commit()
# view.state.now is 2024-03-11 at 23:30; change.receipt.triggered is ("meeting",).
```

`read` validates the existing scope schema and returns `CalendarView(scope, state, etag)`.
The campaign must already exist; no read initializes or creates one. The domain state is immutable.
New operations require the etag that was reviewed. An intervening scope edit, including a rename,
makes a previously prepared write stale. The store's authorization, V1 path isolation, file-size cap,
compare-and-swap, journal and recovery apply. Preview is nonmutating and abandoning it has no effect.

Retry by calling the same method with the same operation id and request. Date spellings normalize
to fixed-day values, so `2024-2-20` and `2024-02-20` are equivalent. A retained identical receipt
returns `already_applied=True` with an empty preview before checking the old expected etag; a changed
request using the same id is refused. `change.receipt` always describes that original operation;
`change.commit()` returns the **current** campaign view, which may have advanced since. Committing
the same successful plan again is also read-only. Initialization and deadline scheduling share this
retry contract. Operation ids are campaign-local and are never evicted or automatically regenerated.

## Chronology and uncertainty

`Moment(fixed_day, minute)` is ordered by fixed day and then minute. Fixed day uses V1's Rata Die /
Python Gregorian ordinal axis: 1 is 0001-01-01; the upper bound is 3652059 (9999-12-31). Minutes are
0 through 1439 in a 24-hour campaign-local day. This is fictional chronology, not a real-world alarm:
there is no UTC conversion, timezone, wall-clock tick or inferred sunset. No implicit midnight is
inserted into old scene records. Explicit initialization/scheduling defaults their new time to minute 0.

`Duration(20)` means exactly twenty minutes. An estimate is retained as bounds and an explanation:
`Duration(10, 30, selected=20, note="About twenty minutes")`. An unresolved range is a valid proposal
but cannot advance authority until an exact value inside its bounds is explicitly selected. No
midpoint is guessed. Negative, noninteger and boolean durations are refused; zero is a recorded
no-op. Advancing beyond the calendar's supported range is refused without writes.

Calendar arithmetic is deterministic and performs no I/O. Because it adapts `datetime` and the
third-party `pyluach` package, `grimoire.calendars` is classified outside the repository's
dependency-free pure kernel; `grimoire.calendarstore` is its persistence adapter. This does not
permit the pure kernel to import either package.

Gregorian notation is `YYYY-MM-DD`, using `datetime` leap arithmetic. Hebrew notation is
`5786-Kislev-25`, using the pure-Python `pyluach` dependency for lunisolar conversion. Month tokens
are Tishrei, Cheshvan, Kislev, Tevet, Shevat, Adar, Nisan, Iyar, Sivan, Tammuz, Av and Elul;
leap years use Adar1 and Adar2. As in V1, plain Adar in a leap year aliases Adar2. Gregorian and
Hebrew dates for the same day have the same fixed-day value. Holidays and anniversaries are not
part of this first slice's API.

Custom calendars use portable data, never executable Python plugins:

```python
from grimoire.calendars import CalendarDefinition, Month
custom = CalendarDefinition("custom", "synthetic", (Month("First", 10), Month("Last", 5)),
                            epoch_fixed=738886)
# 1-First-01 is fixed day 738886; after 15 days comes 2-First-01.
```

The readable representation holds `provider`, `custom_id`, `months` (`name`, `days`) and
`epoch_fixed`. Each month name is an ASCII letter followed by up to 63 ASCII letters, digits or
underscores, unique ignoring case. There are 1–128 positive-length months, with total year length
at most the supported fixed-day range. Native year 1, first month, day 1 equals `epoch_fixed`;
dates before that anchor are refused. Years repeat the same month lengths. The definition is
copied into immutable values and pinned at initialization. No authored fictional calendar ships
with the application. Complex leap/intercalation rules, alternate day lengths, parallel schedules,
calendar changes and executable V1 plugin migration remain later work.

## Deadlines, reminders and triggers

`schedule` records a future `Moment`, a unique deadline id, label, and `kind` (`deadline`, `reminder`
or `trigger`). An advance crossing `(before, after]` records its operation id as `triggered_by`.
All crossed pending clocks are flagged atomically and only once. Their original due moments remain
available, even when advancement skips past them. Scheduling at or before now is refused.

A flag is only a flag. It invokes no callback, runs no activity, changes no resources, and grants no
actor knowledge. Consequential triggers require a later explicit decision through the applicable
action/effect authority. The service is director-facing; callers must not treat its labels or
canonical facts as NPC memory. Future rumors can consume a deliberately authorized time reading;
UI exit 115 can consume `read`, previews and flagged deadlines. Neither integration is implemented
here, and advancement never guesses player actions or updates other domains on their behalf.

## Authority, conflicts and recovery

The existing `campaigns/<id>/scope.json` owns one `calendar` extension with schema
`grimoire.calendar/1`. It stores the calendar definition, current moment, deadlines and full ordered
operation receipts. These remain readable canonical JSON, not a database or cache. Reads replay all
receipts and check the stored materialization. Unknown schemas/fields, duplicate receipts, wrong
campaigns, contradictory timestamps/flags and malformed operations fail closed without repair.
Noncalendar scope fields and unknown envelope fields survive writes; ordinary scope edits preserve
the extension. No parallel calendar copy is authoritative.

Native recovery leaves the scope transaction wholly before or after. Reopen and replan the same
operation after an interrupted/uncertain commit; its retained receipt determines whether it landed.
The log is not pruned. When the scope reaches the native file limit, new operations refuse rather
than silently forgetting old ids. This first implementation replays the retained history on each
read; large-history compaction with durable retry protection is future work.

Sync compares the whole scope file. Divergent offline clocks are preserved as a conflict, never
added together or field-merged. Authoritative reads, planning and commit refuse an unresolved scope
conflict or incomplete relevant conflict metadata, including a conflict arriving after preview.
The existing explicit keep-mine/keep-peer resolution chooses the retained canonical lineage.
Exactly-once applies to that lineage: an operation discarded by an intentional resolution or manual
restore must not be automatically resubmitted as a new operation. Coherent manual replacement or
intentional deletion of canonical history is outside the receipt guarantee. There is no hidden
secondary ledger to override the user's canonical files.

Existing calendarless scopes remain valid. No V1 library is read, converted or modified. Removing
this code leaves the extension as opaque data for clients which preserve unknown scope fields;
there is no destructive downmigration.

Tests: `test_calendars.py`, `test_calendarstore.py`, `test_calendarstore_sync.py` and
`test_calendarstore_recovery.py` use only synthetic native records. They cover date arithmetic,
uncertainty, the 20-day/deadline/old-history proof, retries after restart and later operations,
authorization/preview, stale/concurrent writes, replay tampering, offline conflict resolution and
native crash/I/O-error recovery. These are headless proofs, not application UI acceptance.
