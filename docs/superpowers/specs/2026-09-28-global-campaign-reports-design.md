# Global and campaign reports

## Purpose

The Grimoire rail's To do, Costs, and Stats entries answer questions about the
whole library. The Open campaign rail offers the same subjects scoped to its
campaign. A reader can tell which scope is in use, change it on the page, and
share or reload the resulting URL without an implicit open-campaign choice
changing the answer.

## Routes and scope

`/todo`, `/costs`, and `/stats` are the global routes. The matching
`/campaigns/:cid/todo`, `/campaigns/:cid/costs`, and `/campaigns/:cid/stats`
routes are campaign-scoped. Each page has an accessible scope selector with
"All campaigns" and the readable campaign names. Selecting a scope navigates
to its canonical route. The Grimoire rail links only to global routes; the
Open campaign rail links only to the campaign routes. Exactly one rail row is
active for any of these paths. A direct link to a missing campaign reports an
unavailable campaign rather than a zero-valued report.

The global view must not use the shell's open-campaign hint as a data filter.
Campaign names come from the existing campaign listing; a ledger row for a
deleted campaign keeps its id as a fallback name instead of disappearing.

## To do

The global page computes library chores once and campaign chores for every
readable campaign. Each campaign chore is a separate row with its campaign
name, fix link, and on-demand item expansion. A campaign-scoped page shows
only that campaign's chores. Counts are live reads; a zero-count chore does
not appear. The global and campaign page totals count the visible,
non-ignored rows in their respective scopes.

Ignore and Restore for a campaign chore apply to that campaign and chore
type, so acting on one row does not hide another campaign's row. Library
chores retain library-wide ignores. Existing saved campaign-type ignores
retain their current effect when first read; on the first write to the
ignore set they are expanded into scoped entries for campaigns that exist
at that point. A later-created campaign starts with no inherited ignore.
The existing `chores.json` stays a display-preference file, not campaign
state. An expanded row requests items with its own campaign id; the global
list never fetches every item's details up front.

The global rail must not show a count computed for only the open campaign.
If a global badge cannot be computed at the rail's cheap-read cost, omit that
badge; the page itself reports the live total. The Open campaign badge, if
shown, counts only its campaign's chores.

## Costs

Both scopes default to the current UTC calendar month, including a current
month with no calls. Month tabs expose months present in the ledger and let
the reader navigate backward and forward; the current month is always a tab
even before its first call. The selected month is reflected in
the URL, so a shared link reopens the same period. The page names the selected
month and the exact UTC date bounds. A campaign-scoped page keeps the existing
scene table and sorting, but every row and total covers the selected month.
The sort happens before the scene-row cap, and truncation remains explicit.

The global page shows a graph above a table with one row per campaign in the
selected month, plus an explicit "Outside a campaign" row for calls with no
campaign id. Only campaigns with calls in that month need a table row; the
scope selector still lists campaigns with no calls. Its total includes all
those rows, including usage belonging to
deleted campaigns. The graph shows up to twelve consecutive months ending at
the selected month. It has separate labelled series for provider charges,
subscription estimates, and modelled amounts, **and** an "Estimated total"
series equal to their sum for each month. This total is a projected activity
cost, never called spend or used for a budget. It is a deliberate new graph
metric: the repository's three accounting columns remain separate everywhere
they describe charges, and no existing money column changes meaning. A month
with only modelled values therefore still has a visible estimated total.
Unpriced calls make that month's estimated total incomplete; the graph and
table say so. Compute the projected total before display rounding. Update the
project convention in `CLAUDE.md` to name this narrowly scoped projection and
retain its rule for accounting, budgets, and the other money surfaces. A failed
ledger read does not render a zero total. Each plotted value has a text label
and accessible amount, so colour or bar height is never the only way to read it.

The backend computes each requested monthly aggregate from ledger rows in one
pass over its month rather than asking the frontend to sum capped scene
reports or make one request per campaign. It exposes the selected-month
global breakdown and bounded graph trend through one read endpoint. The
existing all-time scene endpoint keeps its old behavior for callers that do
not pass a month; the Costs page passes one explicitly.

## Stats

Stats keeps its daily window selector and existing performance, error, and
log sections. The global performance section adds one row per campaign, plus
an explicit unassigned row for calls with no campaign id. Each row carries
call count, failed-call count, and latency percentiles; the count remains
beside the percentiles so a tiny sample does not look conclusive. The global
Errors section also shows error counts by campaign, and global log rows show
their campaign when one was recorded. The campaign-scoped route filters
performance, errors, the log page, and the live log tail to the same campaign.
A scope change clears or replaces data from
the previous scope and restarts the tail with the new filter; stale replies
must not appear under a new scope label.

The existing daily median-latency and failure trends stay. Add a daily manual
reroll trend: count `retry` and `regenerate` usage tasks, excluding `replay`
because a retcon replay is not evidence that a reader rejected a response.
Show both the count and its share of the day's initial chat plus manual
reroll attempts, with the denominator visible. A day with no eligible calls
has no rate, not a zero-percent quality claim. This is a signal for the
reader to investigate, not a diagnosis of model quality.

## Failure and verification behavior

The selector always shows the requested scope, including while a read is
loading. A failed read names the section that failed and offers its existing
retry path; it never presents stale or missing data as zero. Empty months,
campaigns with no chores or calls, unassigned usage, deleted campaign ids,
and changing scope during an in-flight request are explicit test cases.

Tests cover backend aggregation and scope boundaries, ignore migration,
month parsing and caps, graph series and estimated-total semantics, daily
reroll counts and rates, frontend route/rail activation, selector navigation,
and each report's scoped reads. The normal `make check` gate is run before
integration, using the repository's documented environment requirements.
