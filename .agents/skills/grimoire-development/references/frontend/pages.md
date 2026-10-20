Source: [EntityEditor.tsx](../../../../../frontend/src/components/EntityEditor.tsx), [PageShell.tsx](../../../../../frontend/src/components/PageShell.tsx), [rail.ts](../../../../../frontend/src/shell/rail.ts).

## Frontend: the list/detail page pattern

**Build every record-list page with this pattern.** A page that manages a list of
records (greetings, lore, locations, characters, …) is an editor with a browse
surface, a read-only detail view and an explicit edit step. Canonical
implementations: `components/EntityEditor.tsx` (a roster, as a grid) and
`components/GreetingEditor.tsx` (a handful, as a rail); `CharacterGrid.tsx` is the
grid for characters.

**A roster browses as a grid; a handful browses as a rail.** The section's root,
with no record named and no form asked for, is the browse surface. A roster
(locations, lore, items, groups, creatures, characters) is a grid of cards, because
a world can hold hundreds of records and the grid gets the full width. A handful
(greetings) is a `.editor-list` rail beside its body. The shape follows the size of
the roster, not the kind: a hundred records in a 220px rail has misread the page,
and so has a grid for three. (#437)

**The rail navigates the app. A column indexes the page.** `components/AppRail.tsx`
is the app's navigation: two tiers (the app, and the campaign that is open) in
chrome that outlives every route, because *which page of the app am I on* has
the same answer everywhere and should be asked once. A page's 274px context
column (`components/PageShell.tsx`) answers the other half — *which of this
page's records am I reading* — which only that page can ask. A page that builds
a second surface to answer the rail's question has misread the rail; a page
that puts its records in the rail has misread its column.

Rows come from the tables in `src/shell/rail.ts`, and **a row whose `to()`
returns `null` is not rendered at all** — which is what lets a row ship
complete in shape and sparse in fact (Sheets is absent where no mechanics
module is bound; Wrap-up is absent where nothing is pending). Badge counts come
from `GET /api/shell` in one read; a count nobody can answer cheaply is `null`
and draws no tail, which is the cost rule ("a price nobody reported is never
rendered as zero") one domain over.

**Neither Costs row draws a tail.** The app tier's goes to a global monthly
report, which one campaign's figure would mislabel, and a tail on the campaign
tier's would have to pick one of three columns that may never be added. The
campaign's money still rides the shell read: `GET /api/shell` answers
`campaign.money`, the ledger's all-time figure rather than a bounded window
wearing its name, and the campaign hub's card draws it as three labelled
columns (`MoneyColumns`). `store/usage/`'s `lifetime_since` reserves the
all-history scan for the all-time view and not the play path, so the shell
reads a maintained aggregate instead — `store/usage_rollup.py`, a byte
bookmark into each month file, so the read costs what has been played since the
last navigation rather than the library's age — and an aggregate it could not
bring up to date is `partial`, which the card says could not be totalled
rather than drawing `$0.00`.

**Where the browse surface goes depends on whether the page owns the screen.** An
editor that sits *inside* another page — a library section, a world tab, a
campaign panel — brings its own browse surface, as below. A page that IS the
screen (the ledger, `routes/SheetsView.tsx`) puts its records in `ColumnSection`s
in the context column and the detail in main. Building an `.editor` surface beside
that column is still a misread of the page — the modes, the read-only-by-default
rule and the explicit edit step are the same either way.

**Structure** — two states, one address each:
- **Browse** (the section root, nothing named, no form asked for): `.entity-browse`,
  holding a head with a `+ New …` link (to the form's own address, `?new=1`), the
  search and count, then `.entity-grid` — one `.row` card per record (its image and
  name). Lore's owner groups are full-width bands inside the grid.
- **Record or form**: `.editor-body`, the full width, with a **Back to the list**
  link (to the section root) above it.
- A handful browses as a rail instead: `.editor` containing `.editor-list` (a `+ New …`
  button plus one `.row` per record, `position: sticky`, `overflow-y: auto`) beside
  `.editor-body`. Greetings, PCs, climates and the other small lists do this; only
  the rosters above take the grid.

**Modes** — a `mode: "view" | "edit"` state, within the record or form state:
- Opening a card or rail row loads the record in **`view`** (read-only) — records
  are never editable by default.
- `+ New …` opens the **form** at its own address (`?new=1`; Lore's `?owner=` pre-owns
  it). The section root is never the form: a bare address is the browse grid.
- An **Edit** button (top of the sidebar) switches a viewed record into the form.
- Save returns to `view` (re-select the record); Cancel returns to `view`.

**View** — `.detail-view` containing:
- `.detail-main` — an `<h3>` title and `.detail-rendered`, the body rendered with
  `<Markdown remarkPlugins={[remarkGfm]}>` (react-markdown). Localized images are
  markdown, so they render here.
- `.detail-sidebar` — an `<aside>` holding the **Edit** button (in `.form-actions`,
  full-width) and the record's metadata in `.side-section` blocks (`<h4>` label +
  chips/hints). Metadata that references **other records** renders as clickable
  `chip` buttons that navigate to that record; plain attributes render as
  non-clickable `<span class="chip on">` or `.field-hint` text.

**Tests** — cover: clicking a card or row shows the read-only view (rendered body,
no `textarea`) with its sidebar; **Edit** reveals the form; `+ New` links to the
form's address and opens the form there; **Back to the list** returns to the grid.
See `GreetingEditor.test.tsx` / `EntityEditor.test.tsx`.
