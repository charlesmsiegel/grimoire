# The frontend shell — the nav rail comes back, and the column stops carrying it

## Status

First of several. A redesign handoff (2026-08-26) covers sixteen screens; this
spec covers **only the shell** — the rail, the header, the layout that holds
them, and the one backend route that feeds the rail's counts. Every screen
inside the shell is re-hosted unchanged and rebuilt in its own later spec.

The order was chosen deliberately: the shell is the only piece every other
slice depends on, and a shell designed under pressure from two consumers is a
shell that gets rebuilt.

Names in this document are placeholders from the codebase's own set
(Saltmarch, Mara, Seraphine, Winifred). Every number is invented for the
example. Nothing here is measured from a real library.

## Problem

Three complaints, in the owner's words:

> "There's no central page for the campaign, it's not surfacing costs (or
> estimated costs for sources that don't report charge amounts), and on mobile
> it's extremely cluttered and hard to work with."

and later:

> "config not linking to connections is terrible" — pages should link in more
> logical ways.

All four of those are navigation complaints wearing different hats, and the
shell is where three of them are answered. The costs one is **not** answered
here; see §"Money is not in this slice".

### 1. There is no way to see what is waiting without going and looking

`AppHeader.tsx`'s docstring is explicit about the current model:

> Left: the mark and the wordmark, both home. Middle: the ⌘K pill, which is
> the app's whole navigation surface — it names where you are and opens the
> palette that takes you anywhere else. […] There is no nav sidebar and no
> scene rail. That is not an omission.

It was a good decision for the problem it solved — one shared list could not
answer "what am I navigating" for twenty different pages, so the answer moved
into each page's own column (`App.tsx`, the comment above `<Routes>`). But it
solved that by making the app's *state* unaddressable. Nothing anywhere says
that absorb proposals are holding the world back, or that a scene is open in
another campaign, unless you navigate to the page that computes it. The
palette can take you anywhere; it cannot tell you where you should go.

A rail with live counts is the smallest thing that fixes it, because a count
in persistent chrome is legible from every page at once.

### 2. The column is doing two jobs and one of them is not its

`PageShell.tsx`'s docstring names the rule:

> The column answers *what am I navigating*; main answers *what am I reading*.
> That single rule is what replaced the play view's rail plus inspector, the
> world view's ten-tab strip, the library's card hub and config's long scroll —
> so a page that wants a second navigation surface is a page that has misread
> its own column.

Read carefully, "what am I navigating" is two questions the rule conflates.
*Which page of the app am I on* is a constant across the whole session.
*Which of this page's records am I reading* changes with every page. One 274px
column has been answering both, which is why the campaigns shelf's column is a
world filter, the world view's is a section list, and Configuration's is a
settings index — three different kinds of thing in the same slot, and none of
them able to say anything about the app outside the page.

The rule is not wrong; it is under-specified. This spec splits it.

### 3. `librarySections.ts` already describes the rail that does not exist

```ts
/** One list, two consumers: the hub renders a card per entry, and the nav rail
 *  uses the same routes to decide whether you are currently inside the
 *  library. */
```

`inLibrary()` is exported and **has no callers** — `grep` finds only its own
definition; `AppPaletteSource.tsx` and `LibraryColumn.tsx` import
`LIBRARY_SECTIONS`, not it. It is a leftover from a rail that was retired
before the comment was updated, and the shape it describes is exactly the
shape this spec builds. It also turns out to be *needed*, not merely
convenient: see §"Active is per-row, not one rule".

## What this is not

**Not a re-skin.** `theme/themes/light.ts` already carries `--bg: #ecebe6`,
`--panel: #f6f5f1`, `--accent: #0d6c70`, `--alert: #8a2a6b` and the rest
verbatim — the design's token table is a transcription of the theme files,
which is what the handoff says it is. The three fonts are the ones already
loaded. No theme, token or font work is in scope.

**Not a page rebuild.** Every route renders exactly what it renders today,
inside the new layout. Two pages are touched, both minimally and both for a
named defect: `ConfigView` (§"Theme has one owner") and `App.tsx` (mounting).

### Money is not in this slice

The design puts a `$4.82` tail on the rail's Costs row. **That badge is not
built here**, and the `/api/shell` payload carries no money at all.

`CostsView` is an all-time view by design, and the rollup behind it is built
on `usage.lifetime_since()`, whose own docstring says:

> This is the one read here whose cost grows with the library's age, and it is
> deliberate — it backs the all-time view and nothing on the play path.

The rail runs on every navigation. Putting an all-history ledger scan there
contradicts that sentence directly. The alternative — quietly substituting a
bounded 30-day window — gives the same unlabelled figure a different meaning,
which is the drift `CLAUDE.md`'s three-money-columns rule exists to prevent.

Under this spec's own rule (§"A count nobody can answer cheaply is `null`"),
the correct behaviour is to omit the badge until a maintained aggregate
exists. Building that aggregate is the costs slice's job, which is where the
design's other four cost surfaces live anyway. The Costs **row** still ships;
only its tail is absent.

## Design

### The layout

`#root` is `display: flex; flex-direction: column` (`index.css:16`) and each
page's `.shell` is `flex: 1; min-height: 0` (`index.css:261`). One element
goes in between:

```
#root  (flex column)
├── <header class="app-header">      52px, flex:none — unchanged
└── <div class="app-body">           NEW — flex row, flex:1, min-height:0
    ├── <AppRail/>                   236px, flex:none — docked ≥1180px
    └── <Routes/> → .shell           flex:1, min-height:0 — unchanged
```

`.app-body` takes the `flex: 1; min-height: 0` that `.shell` had; `.shell`
keeps its own. That chain is load-bearing for reasons `PageShell`'s docstring
already sets out: break it and the column's pinned footer slides out of the
bottom of an `overflow: hidden` shell at short viewport heights, where it
cannot be scrolled back to.

**The header stays 52px.** The handoff's phone frames draw a 46px header; that
belongs to the slices that rebuild those screens, not to this one.

**`PageShell` is not modified.** That is the point of doing the shell first:
twenty-odd pages keep working inside the rail on day one.

#### Where the rail is not

The rail wraps `<Routes>`, so by default it would sit beside the two wizards.
It must not: `PlainShell`'s docstring calls `/welcome` and `/campaigns/new`
"one centred question at a time" pages that "would be answering *what am I
navigating* with *nothing, finish this first*", and the handoff agrees — "the
rail is hidden entirely on the two wizards". On a first run the rail would
otherwise offer Campaigns, Library and Configuration before setup has been
answered at all.

`RAIL_LESS = ["/welcome", "/campaigns/new"]`, matched with `isUnder`. On those
routes neither the rail nor the header's `☰` renders. Tested at both widths.

### The rule, restated

`CLAUDE.md`, `PageShell.tsx`, `AppHeader.tsx` and `App.tsx` all state the
retired model in prose. They are rewritten to:

> **The rail navigates the app. A column indexes the page.**
>
> The rail answers *which page of the app am I on* — a question whose answer
> is the same on every page, so it is asked once, in chrome that never moves.
> A page's column answers *which of this page's records am I reading* — a
> question only that page can ask. A page that builds a second surface to
> answer the rail's question has misread the rail; a page that puts its
> records in the rail has misread its column.

This paragraph goes in `CLAUDE.md` **only**. `test_no_document_restates_another`
compares every ordered pair in `DOCS + (CLAUDE,)` for a long shared run of
words, so it must not be pasted into `CONTRIBUTING.md` or `AGENTS.md` — which
point at `CLAUDE.md` rather than repeating it, by design.

### The rail is a table

Modelled on `LIBRARY_SECTIONS`, whose shape has already proved itself:

```ts
export type RailRow = {
  id: string;
  label: string;
  icon: string;                              // a literal glyph; no icon library
  to: (ctx: RailCtx) => string | null;       // null ⇒ the row is not rendered
  match: (pathname: string, ctx: RailCtx) => boolean;
  tail?: (b: ShellPayload) => ReactNode;     // undefined result ⇒ no tail
  tailLabel?: (b: ShellPayload) => string;   // what a screen reader hears
};
```

Two tiers, `APP_ROWS` and `CAMPAIGN_ROWS`.

**A row whose `to()` returns `null` is not rendered** — absent from the DOM,
not disabled. This is what lets the rail ship complete-in-shape and
sparse-in-fact.

To be precise about what that buys, because the first draft of this spec
overclaimed it: **a later slice still edits this table.** It adds its route to
the row's `to()` (one line) and, if the row carries a count, a `tail`. What
the table buys is that the edit is one line in one declarative list rather
than a change to the rail's markup, its matching, or its tests. `id` is a
stable row key and **not** a path into the payload — several counts are nested
(`sheets.sheeted`, `ledger_open`) and a projection function is how they are
read.

It is not a hypothetical concern that rows are missing: **most campaign-tier
destinations do not exist yet.**

| Design row | Route today | Ships? |
|---|---|---|
| Overview | `/campaigns/:cid` — the **play** view | Yes, labelled `Play`; the hub slice renames it |
| Scenes | *no scenes-list page exists* | No |
| Wrap-up | review lives inside `CampaignView` | No |
| Ledger & timeline | `/campaigns/:cid/ledger` | Yes (the timeline merge is a later slice) |
| Sheets | `/campaigns/:cid/sheets` | Yes |
| Images | `ImagesView` is a `WorldView` section, not a route | No |

App tier: `Campaigns` (`/`), `Library` (`/library`), `Search` (`/search`),
`Stats` (`/stats`) and `Configuration` (`/config`) exist and render. `To do`
has no backend and no page and does not. `Costs` renders only when a campaign
is open, and routes to `/campaigns/:cid/costs`; no all-campaigns costs page is
invented in a shell spec.

Labelling the first campaign row `Play` rather than `Overview` is deliberate.
The row is route-driven and `/campaigns/:cid` is the play view until the hub
slice moves it; a row labelled "Overview" that lands on a transcript is the
kind of small lie a reader stops trusting the rest of the rail over.

#### Two rows are dropped outright

`Empty & failed` and `On a phone` are in the prototype's rail and are **not
app pages**. They are the design's own catalogue — eleven states and six
device frames, documentation of decisions that belong inside the pages they
describe. Shipping them would be shipping the spec.

#### `Search` has no shortcut tail

The design's tail reads `⌘⇧F`. **That chord cannot exist in this app.**
`shortcuts/keys.ts`'s `chordOf` folds Shift into the character a printable key
produces — `if (e.shiftKey && key.length > 1) parts.push("shift")` — so
`Cmd+Shift+F` and `Cmd+F` both normalize to `mod+f`. A `mod+shift+f` binding
would never fire, and a `mod+f` one would take the browser's Find.

Changing that normalization to distinguish shifted modified letters is a real
change to the shortcut layer, and it is the layer that makes `?` work. **The
tail is dropped and no keybinding is added.** Search stays reachable by rail
row and by palette. If a chord is wanted later it is its own change, with its
own argument about `?`.

### Active is per-row, not one rule

A single `isUnder` rule is wrong in both directions, and both are reachable
today:

- **False negative.** `Library` points at `/library`, which is a `<Navigate
  to="/worlds" replace />` (`LibraryView.tsx`). One click and the pathname is
  `/worlds`, which is not under `/library` — the row you just used goes dark.
- **Two active rows.** `/campaigns/:cid/ledger` is under both `/campaigns/:cid`
  and `/campaigns/:cid/ledger`, so `Play` and `Ledger` would both light. Scene
  URLs do the same to `Play`.

So each row carries its own `match`:

- `Library` → `inLibrary(pathname)`, which finally gets the caller its comment
  has been promising. It already covers every section route.
- `Play` → exact `/campaigns/:cid`, plus its `scenes/:sid` children, and
  nothing else.
- Every leaf row → `isUnder(pathname, its route)`.

**At most one row per tier may be active**, asserted directly in a test that
walks a list of real pathnames — `/worlds`, `/modules`, `/climates`,
`/connections`, `/campaigns/c1`, `/campaigns/c1/scenes/s1`,
`/campaigns/c1/ledger`, `/campaigns/c1/sheets`, `/config`, `/stats`,
`/search`, `/modules-of-my-own` — rather than a test of one clever case.

### Rail rows, visually

38px min-height, `padding: 0 14px`, a 2px transparent left border that becomes
`--accent` when active, background `--surface` when active. Below 1180px the
min-height goes to **44px**, the design's floor for anything a phone touches.

`<nav>` with an accessible name per tier; the active row carries
`aria-current="page"`. A tail is never the only carrier of meaning: the row's
accessible name includes it (`tailLabel`), so "Ledger & timeline, 4 open
threads" is what a screen reader hears and no information lives only in a
`title` attribute. Nothing critical is hover-only.

The campaign tier carries a derived sub-line (`Saltmarch · 15 scenes · 2 open`)
and, beneath the rows, one indented row per un-ended scene. Every part is
computed from the payload; none of it is a literal.

### The header

| Change | Rationale |
|---|---|
| The crumb names **every** screen | See §"The crumb has an owner" |
| **Scene pill** `CTX 61%`, on scene screens only | `CTX` moves out of the permanent status block into it. A context percentage on a page with no scene is a claim about a prompt you are not composing — the argument `ShellStatus`'s docstring already makes about the campaign name outliving its page. **The design's `SCENE $0.41` half is not built here** (§"Money is not in this slice"); the pill gains it in the costs slice. |
| Model + health dot hidden below 1180px | Per the design. The dot's `title` is not its only channel: it already renders an `.sr-only` span carrying the same verdict, and that stays. Below 1180 the whole widget is gone, so the Connections page remains the reader's answer — which is what `AppHeader`'s comment already says it is. |
| **Theme toggle** added | §"Theme has one owner" |
| **`CONFIG` link removed** | It is a rail row now |
| `☰` added below 1180px | Opens the rail drawer. `aria-expanded`, `aria-controls` |
| `FOCUS` unchanged | — |

#### The crumb has an owner

`ShellStatus.context` is `null` outside a campaign, so the pill reads "go
anywhere" on Costs, Stats and Configuration alike. Making every screen name
itself must not mean adding a publish hook to twenty pages — that is a page
edit in a slice that promises not to make them.

A **route-title manifest** instead: an ordered list of `(match, title)` pairs
in the same file as the rail table, resolved centrally by the header. Rules:

- A page that **publishes** context wins over the manifest. Campaign pages
  already do, and they are the ones with a name the router cannot know.
- The manifest's entries are matched in order, first match wins, with a
  catch-all last so no route can fall through to "go anywhere" silently.
- `/library` resolves to the title of what it redirects **to**, because the
  reader never sees `/library` for longer than a frame.
- A test walks the same pathname list as the active-row test and asserts every
  one resolves to a non-empty title. That is what makes "every screen" a
  checkable claim rather than a sentence.

#### Theme has one owner

`ThemeProvider.setTheme()` changes client state only. `ConfigView` carries
`theme` inside its deferred draft (`d.theme = normalizeMode(c.theme)`,
`edit("theme", mode)`) and persists it with everything else on Save, applying
it immediately in the meantime.

Add a header toggle naively and there is a real bug: open Configuration (draft
holds the old theme), toggle from the header, then save an unrelated setting —
the stale draft writes the old theme back. A client-only toggle is worse in a
quieter way: it vanishes on reload.

So the toggle **persists immediately**, and `theme` stops being part of
`ConfigView`'s deferred draft. Both controls call one path; `ThemePicker` stays
where it is, pinned in Configuration, and starts persisting on pick like the
header toggle. `ConfigView`'s existing optimistic-apply-and-roll-back
behaviour is what that path does, so this is moving an owner rather than
inventing a mechanism. Busy and failure states come with it, and the test is
the sequence above: toggle from the header with Configuration open and dirty,
save, and assert the theme survives.

### Focus mode

`focus.tsx` and `index.css:206` describe focus mode as collapsing the app
header, the context column and the scene bar. **The rail is not rendered in
focus mode** — not `display: none`, because a hidden rail is fifteen tab stops
between the reader and the composer and `FocusRestore` must remain the first
of them.

(The context column is a different case and is left alone: it is rendered and
hidden by `.shell.focus .context-column { display: none }` at `index.css:258`,
which the comment beside it explains. This spec does not touch it.)

The drawer cannot be open in focus mode, because the control that opens it
lives in a header that is not rendered. Leaving focus mode must not restore an
open drawer.

### Responsive

`RAIL_PX = 1180`, exported from the rail module. It cannot be read by a plain
CSS media query, so the value is duplicated in `index.css` — the same
duplication `PageShell`'s `PHONE_PX` already has with the `720px` rules, and
for the same reason. A test asserts the two agree by reading the stylesheet,
so the pair cannot drift silently.

`innerWidth`, not `matchMedia`: it is the reading the CSS gets and jsdom needs
no shim. Event-driven, as `PageShell` does it.

- **≥ 1180px** — docked at 236px.
- **< 1180px** — an overlay drawer opened by the header's `☰`.
- **< 720px** — nothing further. `PageShell`'s existing phone push (its own
  `PHONE_PX`, unchanged) works underneath, and the drawer is the same drawer.
  The handoff's phone frames draw no rail and offer no route from the hub to
  To do; one header control is the smallest thing that makes the app navigable
  on a phone at all.

The two breakpoints stay distinct on purpose. 1180 is where the rail and a
page's column can no longer share a row; 720 is where a column and main
cannot. Different questions about different pairs of elements; collapsing them
would be a coincidence, not a simplification.

#### The drawer is a dialog

`useHotkeys(..., { modal: true })` gives Escape and binding-suppression and
**nothing else** — it does not move focus, trap Tab, or name anything. The
drawer therefore carries the rest explicitly:

- `role="dialog"`, `aria-modal="true"`, `aria-label`.
- Focus moves into the drawer on open and **returns to the `☰`** on close, by
  any route out (Escape, backdrop, row pick, widening).
- Tab and Shift+Tab are contained; content behind is `inert` where supported
  and `aria-hidden` regardless.
- A backdrop that closes on click.
- Closes on: Escape, picking a row, the pathname changing by any means, and
  the viewport widening past `RAIL_PX`.
- Tested for containment and focus restoration, not only for Escape.

### The open campaign

The rail's second tier is visible on app-tier pages — the handoff shows the
campaign heading while standing on To do. So "which campaign is open" is state
the app has never had: today it is purely `/campaigns/:cid` in the URL.

**Correcting a claim the first draft of this spec made:** `App.tsx`'s
`leftSetupFor` is in-memory `useState` compared against `dataDir`, **not**
`localStorage`. The keying idea is borrowed from it; the storage is not.

`useOpenCampaign()`:

- Set by any `/campaigns/:cid` route.
- Persisted in `localStorage`, keyed by `data_dir`, so a different library
  pointed at from Configuration gets its own answer rather than inheriting
  this one's. Reads and writes are wrapped in `try/catch` as `focus.tsx` wraps
  them: storage throws rather than returning `null` in a locked-down WebView,
  and a rail heading is not worth a blank screen.
- **It is a hint, not a fact.** The rendered tier comes from
  `payload.campaign`; the stored id is only what the next `/api/shell` request
  asks about. The rail never renders a campaign name out of `localStorage`.
- **A failed or pending read never clears it.** Only `campaign: null` from a
  *successful* read does — that is the server saying the id does not resolve.
  Confusing a dropped connection for a deleted campaign would erase valid
  state.
- **Cross-tab: last writer wins, and that is accepted.** Two tabs in one
  library can hold different campaigns in memory; a reload takes whichever
  wrote last. No `storage` event listener, because a rail heading changing
  under a reader because another tab moved is worse than a stale one. Stated
  here so it is a decision rather than a bug.

### The route

`GET /api/shell?campaign=<cid>` — `backend/src/grimoire/routes/shell.py`,
registered by adding `shell` to the `_domain` tuple in
`backend/src/grimoire/routes/__init__.py`. Order does not matter for it; it is
not a catch-all and it goes before `entities`, which is documented as staying
last.

```jsonc
{
  "campaigns": 3,
  "library": 6,
  "campaign": {
    "id": "saltmarch-run",
    "name": "A Campaign In Saltmarch",
    "world_name": "Saltmarch",
    "scenes": 15,
    "open": [{ "sid": "s15", "title": "The lower step", "turns": null }],
    "unreviewed": null,
    "ledger_open": 4,
    "sheets": { "sheeted": 4, "total": 7 },
    "images_undescribed": null
  },
  "todo": null
}
```

#### What each field means

Defined field by field, because "a count" admits several incompatible readings:

| Field | Means | `null` when |
|---|---|---|
| `campaigns` | Campaigns the shelf would list — readable ones. An unreadable directory is not counted and does not fail the read. | never; `0` is a real answer |
| `library` | The number of library **sections**, i.e. `len(LIBRARY_SECTIONS)`'s server-side equivalent — the design's `6`. Not a record count. | never |
| `campaign` | The whole block | no `campaign` param, or the id does not resolve |
| `scenes` | Scenes in the campaign, as `CampaignMeta.scenes` already counts them | never |
| `open` | Scenes whose frontmatter `done` is not set. `turns` is **always `null` in this slice**: `SceneMeta` carries no turn count and the only cheap candidate would undercount legacy scenes. The rail renders no tail for it. | never; `[]` is a real answer |
| `unreviewed` | Undecided absorb proposals | **always, in this slice** — the wrap-up slice fills it |
| `ledger_open` | Open threads, as the ledger already counts them | never |
| `sheets` | `{sheeted, total}` across the cast | if the campaign binds no mechanics module |
| `images_undescribed` | Images with no description text (`list_undescribed_images`) — **not** untagged, which the design keeps as a separate word | **always, in this slice** — the images slice fills it |
| `todo` | The chore block | **always, in this slice** |

No money field. See §"Money is not in this slice".

#### Three rules

**A count nobody can answer cheaply is `null`, never `0`.** The rail renders
no tail for `null`, and renders `0` for `0`. This is `CLAUDE.md`'s cost rule —
"a price nobody reported is never rendered as zero" — applied to counts, for
the same reason: an answer nobody computed is worse than silence. Every field
is typed nullable from the first commit, so a later slice filling one is a
value change and not a schema change.

**It must not walk scene bodies.** Open-scene detection reads scene
frontmatter only (`done`). A long-running campaign can hold a great many
scenes, and the rail runs on every navigation, so the rail must not be the
thing that reads them. Any field that cannot be answered under that constraint
answers `null` by the rule above — which is most of why the table has so many
"always, in this slice" entries.

**An unknown `cid` answers `campaign: null`, not 404.** The rail asks with a
remembered id that may no longer resolve; that is a normal state, not an
error.

Read-only, so no campaign lock. `test_lock_domain_guard.py` classifies modules
that **mutate** campaign-scoped state, so a read-only route module needs no
entry there — a claim the first draft of this spec got wrong. `store.paths`
and the module-scope import rules apply as they do everywhere.

### Freshness

The rail cannot refresh off navigation alone. `App.tsx` refetches config on
`location.pathname`, but the events that change a badge mostly do not move the
pathname — accepting a proposal, ending a scene, describing an image.

`onConfigChanged` is **not** the bus for this, which the first draft of this
spec got wrong: `appEvents.ts` emits it for the active connection and its
model only, and campaign creation and deletion go through a separate
`campaignsChanged` channel.

So: a third channel, `shellChanged`, in `appEvents.ts`. Emitted **from the api
client's mutators**, which is where the existing two are emitted from and for
the stated reason — "so a caller cannot forget: the mutators are the one place
every path goes through". The rail subscribes to all three; `campaignsChanged`
and `configChanged` already exist and already fire.

Which mutators emit `shellChanged` is enumerated in the plan, not guessed at
here, and the enumeration is bounded by the fields that are non-`null` in this
slice: scene create/delete/rename/end (`open`, `scenes`), ledger writes
(`ledger_open`), sheet create (`sheets`), campaign create/delete (`campaigns`).
A field that is always `null` needs no emitter yet, and gains one with the
slice that fills it.

#### Loading and failure

An explicit state, keyed by `(data_dir, cid)`:

- `loading` — rows that need no payload render normally; rows with a tail
  render **without** it. No spinner in chrome, and no skeleton number that
  could be read as a count.
- `ready` — the payload renders.
- `failed` — the last good payload for **this same key** is kept and the rail
  stays usable; a quiet inline marker says the counts may be stale, and offers
  a retry. Navigation still works, because navigation is what the rail is for.
- **A payload whose key does not match the current one is dropped
  immediately**, never rendered. Switching `data_dir` must not leave the
  previous library's campaign and counts in the chrome while the new read is
  in flight or failing. This is the concrete bug the key exists to prevent.

The in-flight guard `App.tsx` uses is required for the same reason it is
required there: the effect can be in flight twice at once and nothing orders
the responses, so a `live` latch stops an older response landing second and
reverting a badge a newer read had just corrected. The latch handles ordering;
the key handles identity. Both are needed and they are not the same thing.

## Testing

Frontend, under `CLAUDE.md`'s rule that an `await` means the page has
**settled** (`settle.test.tsx`), not that the query it named passed. Tests
that move `localStorage` or `innerWidth` restore both in `afterEach`, or they
become order-dependent.

- `AppRail.test.tsx` — both tiers render; a row whose `to()` is `null` is
  **absent from the DOM**; a `null` badge renders no tail while `0` renders
  `0`; the campaign tier is absent with no open campaign; one row per un-ended
  scene; the rail is absent on `/welcome` and `/campaigns/new` at both widths;
  the rail is absent in focus mode.
- Matching — the pathname list in §"Active is per-row" walked in one test,
  asserting **exactly one** active row per tier, `/worlds` lighting `Library`,
  and `/campaigns/c1/ledger` lighting `Ledger` and not `Play`.
- Drawer — `☰` opens below 1180; Escape, backdrop, row pick, pathname change
  and widening each close it; focus moves in on open and **returns to the
  `☰`** on close; Tab is contained.
- `useOpenCampaign` — survives navigation to `/config` and a remount; a
  different `data_dir` gets its own answer; a **successful** read with
  `campaign: null` clears it; a **failed** read does not.
- Payload states — pending renders no tails; failure keeps the previous
  payload for the same key and offers retry; a payload for the previous
  `data_dir` is dropped rather than rendered; an out-of-order response does
  not overwrite a newer one.
- `AppHeader.test.tsx`, extended — the scene pill renders only with a scene;
  the model and dot are hidden below 1180; `CONFIG` is gone; every pathname in
  the list resolves to a non-empty crumb title.
- Theme — the header toggle persists; toggling with Configuration open and
  dirty, then saving, leaves the toggled theme in place.

Backend, `test_shell_route.py` — an unavailable count is `null` and not `0`;
no money field is present; `campaign` is `null` with no param and with an
unknown `cid` (200, not 404); `open` reflects `done` and carries `turns: null`;
an unreadable campaign directory does not fail the read.

`make check` is the gate. The lint baselines are ratcheted, so `make baseline`
is needed **only if a finding count actually changes** — new files that are
clean change nothing, and the first draft of this spec said otherwise.

## Documentation

These four state the retired model in prose and become false:

- `CLAUDE.md` — the "second navigation surface" paragraph.
- `frontend/src/components/PageShell.tsx` — the docstring quoted in §2.
- `frontend/src/components/AppHeader.tsx` — "There is no nav sidebar and no
  scene rail. That is not an omission."
- `frontend/src/App.tsx` — "That is the whole reason the nav sidebar could be
  retired".

And one already false, corrected as a side effect:
`frontend/src/librarySections.ts`, whose comment describes a nav rail retired
before the comment was written — and which now has one.

`backend/tests/test_docs_guard.py` holds `README.md`, `CONTRIBUTING.md`,
`AGENTS.md`, `docs/store-guarantees.md` and `docs/screenshots/README.md` to
the tree; none mentions the shell, so none needs changing — confirmed by grep.
`docs/superpowers/specs/` is not among them, so this document needs no
`README.md` pointer, consistent with every spec already there.

## What this slice deliberately leaves undone

Stated so they are not mistaken for oversights:

1. **Five rail rows do not render.** Four because their pages do not exist —
   To do, Scenes, Wrap-up, Images — and Costs whenever no campaign is open.
   Two more, `Empty & failed` and `On a phone`, are dropped outright.
2. **The campaign hub does not exist.** `/campaigns/:cid` is still the play
   view and the rail's first campaign row says `Play`.
3. **No money anywhere in the shell** — not in the rail, not in the scene
   pill. §"Money is not in this slice" gives the reason and names the slice
   that owns it.
4. **Four payload fields are always `null`**: `unreviewed`,
   `images_undescribed`, `todo`, and `open[].turns`.
5. **No new keybinding.** The design's `⌘⇧F` is unrepresentable in the current
   shortcut layer and changing that layer is its own change.
6. **The 46px phone header is not built.** The header stays 52px.
7. **The two backend changes the handoff assumes** — director notes stored as
   ordinary transcript posts, and `_answering_post` returning their index
   instead of `None` (`streaming.py:717`) — belong to the costs slice. They
   land in the detached-run and scene-freeze machinery, which is not somewhere
   to go while also moving the layout.
