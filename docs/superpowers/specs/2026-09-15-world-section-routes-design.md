# A world's sections and records get addresses

*2026-09-15*

## The problem

`WorldView` keeps which section you are reading — Characters, Items, Lore — in
React state, and its own docstring says so deliberately: *"picking a section
from the column leaves the URL alone."* Three things follow from that, and all
three are the complaint.

- **There is no address for a section.** `/worlds/realm` is Characters, Items
  and Lore all at once. Nothing can be bookmarked, linked to, or sent to
  somebody else.
- **Nothing can be opened in a new tab.** Every row in the column is a
  `<button>` with an `onClick`, so right-click offers nothing, and ctrl-click
  and middle-click do nothing at all. This holds even where a real URL already
  exists: a character's page is a route (`/worlds/:wid/characters/:eid`), but
  `CharacterGrid` opens it with `<button onClick={() => navigate(…)}>`, so the
  one screen in this page that *is* addressable still cannot be opened beside
  what you are reading.
- **The back button skips the page.** Moving Overview → Lore → Items pushes no
  history, so Back leaves the world entirely.

There is a deep-link vocabulary — `?section=lore&id=the-salt-pact` — but it is
arrival-only. Search hits, the character page's chips, the campaign hub's cast
card and the rail's Images row all mint those links; the page reads them once
on mount and then never writes one.

A second cost sits underneath the first. Because selection is state that must
be *pushed* into a child, every cross-navigation needs a nonce to re-fire an
effect when the same record is asked for twice. `WorldView` carries five of
these — `charReset`, `loreReset`, `focusGreeting.n`, `focusPC.n`, and the
`entityNav`/`onNavConsumed` handshake — and `GreetingEditor` and `PCEditor`
each take a `focus`+`focusNonce` prop pair to receive them. A URL is idempotent
by construction, so route-driven selection deletes the whole mechanism.

## What this is not

**No appearance changes.** Not one. Every screen keeps its current layout,
type, spacing and controls. The column keeps its groups and counts, the
editors keep their `.editor-list` rails, the greetings section keeps its
List/Plot map chips. This is routing and handling only — where CSS is touched
at all it is to *preserve* an appearance that would otherwise change (see
Risks).

No backend change. No new API. `store/` is not opened.

## The route table

Sections and records become path segments. A query param survives only where it
is genuinely a *modifier* of the screen rather than the identity of it.

```
/worlds/:wid                      Overview
/worlds/:wid/push                 Push to campaigns
/worlds/:wid/images               Images                     (?for= kept)
/worlds/:wid/characters           the grid
/worlds/:wid/characters/:eid      CharacterPage — exists already  (?v= kept)
/worlds/:wid/pcs[/:rid]
/worlds/:wid/creatures[/:rid]
/worlds/:wid/groups[/:rid]
/worlds/:wid/locations[/:rid]
/worlds/:wid/items[/:rid]
/worlds/:wid/lore[/:rid]          (?owner=<kind>:<id> pre-owns a new entry)
/worlds/:wid/greetings[/:rid]     (?view=graph for the plot map)
/worlds/:wid/tags                 no record segment — tags have no detail pane
```

The campaign's fork of a world mirrors it under `/campaigns/:cid/world/…`,
carrying every index row except Tags — which is the filter the column already
applies, because the tag vocabulary is a world concern. Overview, Push and
Images stay world-only for the same reason, exactly as the column already
renders them. `/campaigns/:cid/world` redirects (`replace`) to
`/campaigns/:cid/world/characters`, which is the section that shape already
defaults to — so every screen has exactly one address rather than two.

**A campaign's character record keeps its existing address**,
`/campaigns/:cid/characters/:eid`, and is deliberately *not* moved under
`/world/`. It is a route that already exists and is already linked from the
campaign rail and from `CharacterPage`'s own back link; moving it would churn
both for no reader-visible gain. The asymmetry is worth one sentence in the
code: a campaign's copy of a character is a record of the campaign, and the
world page is where you browse them, not where they live.

### A section root is the new-record screen

`EntityEditor` mounted with nothing selected already shows a blank form in
`edit` mode — its `mode` state initialises to `"edit"` precisely so a brand-new
entry goes straight to the form. So `/worlds/realm/items` *is* the `+ New item`
screen, and `+ New` becomes a link back to the section root rather than a state
flip. The two forms agree because they were always the same screen.

There is deliberately no `/items/new`. `new` is a legal slug, so a record
called "new" would be unreachable behind its own creation screen. The one deep
link that names no record — "start a lore entry already owned by this
character", today `?section=lore&owner=characters:seraphine` — stays a query
param on the section root: `/worlds/realm/lore?owner=characters:seraphine`. It
modifies the blank form; it does not identify a record.

### Mounting

One splat route per shape, not a family of sibling routes:

```tsx
<Route path="/worlds/:wid/*" element={<WorldView />} />
<Route path="/campaigns/:cid/world/*" element={<WorldView campaign />} />
```

A splat matches an empty tail, so `/worlds/realm` and `/worlds/realm/items/the-salt-pact`
are the same route object and React keeps one `WorldView` instance across every
section. Sibling routes would give React a different element identity per
section and remount the page on every column click, re-fetching the world and
its cover each time. One instance across every section is the point.

`/worlds/:wid/characters/:eid` stays its own top-level route rendering
`CharacterPage`. React Router ranks static segments above dynamic ones and both
above a splat, so it wins without any ordering care.

**`WorldView` parses the tail itself, and validates it.** `useParams()["*"]`
gives `"items/the-salt-pact"`. Splitting drops empty segments, so a trailing
slash or a doubled one (`/worlds/w/items/`, `/worlds/w//items`) normalises to
the same two-or-fewer segments rather than minting a second address for the
section root. More than two non-empty segments is rejected like any other
unknown route. There
is no `useMatch` involved — an earlier draft of this spec claimed empty child
routes would surface `:section`/`:rid` to `useMatch`, and that is simply wrong:
`useMatch` tests one explicit pattern and returns a match or `null`, so it would
need four patterns (world/campaign × root/record) and would still not be reading
anything the splat tail does not already say.

```
parseTail(tail, shape) -> { section, rid } | "unknown"
```

`section` defaults to `overview` on the world shape when the tail is empty, and
is checked against a per-shape allowlist: the world shape allows the nine index
rows plus `overview`, `push` and `images`; the campaign shape allows the eight
index rows it actually renders — no `tags`, no `overview`, no `push`, no
`images`. `rid` is accepted only for a section that has records, which excludes
`tags` and the three non-record screens.

**Anything the allowlist rejects redirects to the shape's default**, replacing
rather than pushing: `/worlds/realm/garbage`, `/campaigns/run/world/tags` and
`/campaigns/run/world/images` all land on the default rather than mounting a
page whose heading claims to be Overview. There is no catch-all `*` route in
this app and no Not Found screen to send them to; inventing one is a larger
decision than this change, and a redirect to a real screen is the honest
behaviour available today.

### The campaign root redirects from inside the page

`/campaigns/:cid/world` → `/campaigns/:cid/world/characters`, `replace`. It
cannot be an index child with a `<Navigate>`: `WorldView` renders no
`<Outlet />`, so a child element would never render at all. It is done inside
`WorldView`, from the path params, and it must sit **above** the existing
`if (campaign && !wid) return null` guard — building that path needs only
`cid`, and waiting for the campaign's world id to arrive would leave the
duplicate address on screen for a round trip.

**It is second in line, not first.** `/campaigns/:cid/world?section=lore&id=x`
satisfies both this redirect and the legacy translation below, and firing this
one would throw the destination away and land on Characters. The order is:
translate a legacy query if there is a valid one, and fall back to this default
only when there is not. The same precedence holds at `/worlds/:wid`, where the
fallback is Overview.

## One place that builds these paths

Every producer goes through a single helper rather than assembling strings.
A loose `(scope, section, rid?, query?)` signature is not enough — it can spell
`?v=` on Lore, `?for=` on Items, an `owner` beside a record id, or a record id
on Tags, and "every producer uses the helper" does not make those illegal. The
target is a discriminated union instead, so the combinations that have no
meaning cannot be written:

```ts
type RecordSection =
  | "characters" | "pcs" | "creatures" | "groups"
  | "locations" | "items" | "lore" | "greetings";

type SectionTarget =
  // a section's own screen
  | { kind: "section"; at: "overview" | "push" | "tags" }
  | { kind: "section"; at: "images"; forCampaign?: string }
  | { kind: "section"; at: "greetings"; view?: "graph" }
  | { kind: "section"; at: "lore"; newOwner?: string }
  | { kind: "section"; at: Exclude<RecordSection, "lore" | "greetings"> }
  // one record inside one
  | { kind: "record"; at: "characters"; rid: string; v?: string }
  | { kind: "record"; at: Exclude<RecordSection, "characters">; rid: string };

sectionHref(scope: EntityScope, target: SectionTarget): string
```

`kind` is the discriminant and each modifier sits on the single variant that
gives it meaning, so the illegal spellings cannot be written: `v` without a
record, `view` or `newOwner` beside one, a record id on `tags`, `push`,
`overview` or `images`. That is what makes the rules in **Old links** below
executable rather than advisory.

The campaign shape's forbidden sections (`overview`, `push`, `images`, `tags`)
are refused at **runtime**, because `scope` and `at` are independent parameters
and the type system cannot pair them.

`sectionHref` is the **only** authority on these paths, the campaign-character
exception included — so the exception lives inside it, as the one branch where
`{ kind: "record", at: "characters" }` on a campaign scope answers
`/campaigns/:cid/characters/:eid` rather than a path under `/world/`. The two
existing helpers become thin wrappers rather than a second implementation of
that knowledge: `charactersHref(scope)` is
`sectionHref(scope, { kind: "section", at: "characters" })` and
`characterHref(scope, cid, vid)` is
`sectionHref(scope, { kind: "record", at: "characters", rid: cid, v: vid })`.
Both keep their names and call signatures, so their callers elsewhere in the app
are untouched.

**Every current caller of `select()` and the nav setters migrates to it.** That
list is longer than the column, and missing any of it means a control that
compiles and silently stops navigating:

- the column's twelve rows
- every palette item this page contributes (`usePaletteSource`)
- `WorldOverview`'s tiles and check rows, via `onNavigate`
- `openCharacter`, `openGreeting`, `openLore`, `openEntity`, `openOwner`
- `EntityEditor`'s `onReclassified`, which lands on the same record under a new
  kind — so it navigates to a *different section's* record path
- `PlotMapEditor`'s node callback
- the two footer importers, **Import lorebook** and **Import scenario card**.
  These navigate to their section and set a local disclosure flag; the flag can
  stay component state, because the page is no longer remounted by the
  navigation that used to accompany it.

## What deletes itself

Route-driven selection retires, in `WorldView`:

- `charReset`, `loreReset` — remount signals for "you picked the section, so
  show me the list rather than whoever was open".
- `focusGreeting` and `focusPC`, both `{ id, n }` nonce records, and the
  render-time scope-derivation `focusPC` needs to avoid handing a child a
  stale id.
- `entityNav` / `navFor(kind)` / `onNavConsumed` — the keyed handshake that
  stops a nav aimed at Lore being consumed by Items.
- `select(key)`'s fan-out, which clears three of the above on every column
  click.

…and in the children, `GreetingEditor`'s and `PCEditor`'s `focus` + `focusNonce`
prop pairs, and `EntityEditor`'s `nav` + `onNavConsumed` pair, each replaced by
a single `selected: string | null` read from the route.

`CharacterGrid`'s `resetSignal` is retired for the same reason; its `reveal`
prop stays, because "reveal a character the filter is hiding" is a different
question from "which record is open".

**Two things the nonces were doing must survive their removal**, and neither is
free:

- **Stale-read ordering.** Each editor's `select()` increments a `readReq` ref
  and drops a response that is no longer the newest. Moving selection into an
  effect on `selected` does not change that hazard — click A then B and A's
  slower read can still land last. Every editor keeps its request token; the
  effect increments it exactly where `select()` did.
- **Re-selecting the record you are already on is now a no-op.** Today it
  re-reads, discarding an unsaved draft without warning; `GreetingEditor`'s
  comment says opening the same greeting twice must reach the editor twice, and
  the nonce is why. Under a URL, navigating to the address you are on changes
  nothing. This is a deliberate behaviour change and not merely a consequence:
  the control whose job is "throw away my edits and re-read" is the form's own
  **Cancel**, which calls `select(editing)` and keeps working. Clicking the rail
  row you are already reading is not a request to lose work. It gets a test
  saying so, rather than being left to be discovered.

## Anchors

Every control whose only job is to go somewhere becomes a `<Link>`:

| Control | File | Becomes |
|---|---|---|
| Overview / Push / Images rows | `WorldView` | `<Link className="column-row">` |
| the nine index rows | `WorldView` | `<Link className="column-row">` |
| `+ New <kind>` | `EntityEditor` and siblings | `<Link>` to the section root |
| a record row (`.row`) | `EntityEditor`, `PCEditor`, `GreetingEditor` | `<Link className="row">` |
| a character card | `CharacterGrid` | `<Link className="char-card-main">` |
| an overview tile / check row | `WorldOverview` | `<Link>` |
| `‹ <campaign> / World Copy` back | `WorldView` | `<Link className="column-back">` |

That last one is the campaign shape's way back to its hub, and it is a
`<button>` today while the world shape's `‹ All worlds` beside it is already a
`<Link>`. `a.column-back` is already styled (`text-decoration: none`), so the
two become the same control written the same way.

`.column-row` needs no CSS at all: `CampaignHub` already renders that class on
both `<Link>` and `<a>`, and the class pins its own `font-family`,
`font-weight`, `font-size`, `line-height` and `text-decoration`. That it
already works both ways is the proof the conversion is free there.

Three controls stay buttons, on purpose. The module **template** rows
(`.content-row`) preview a record that does not exist yet and has no id in this
world to address. The greetings **List / Plot map** chips are a `role="group"`
of `aria-pressed` toggles; they get `?view=` in the URL, but the control stays
a toggle rather than becoming two links, because `GreetingEditor` is kept
mounted-but-`hidden` behind the graph so a half-written greeting survives the
switch. **Import lorebook** and **Import scenario card** open a `<details>` on
a section rather than naming a screen.

**`greetingView` stops being state and is derived**, or the chips and the Back
button disagree within three clicks — push `?view=graph`, click List, press
Back, and a `?view=graph` URL renders the list. The rule is a function of the
route alone:

```
rid present                 -> list   (the graph has no detail pane)
no rid and view=graph       -> graph
otherwise                   -> list
```

Because a record segment forces the list, a record URL is canonical without
`view`: `openGreeting` navigates to `/worlds/realm/greetings/tide-watch` with
no `view` param rather than leaving one that the renderer then contradicts.

`GreetingEditor` must not be keyed or conditionally unmounted by the view
change. It is kept mounted-but-`hidden` behind the graph today precisely so a
half-written greeting survives the switch, and moving the switch into the URL
is exactly the kind of change that quietly turns it into a remount.

## Old links

`?section=` remains readable and stops being written.

On arrival, `WorldView` translates a legacy URL into the new path and
`navigate(…, { replace: true })`s to it, so the old URL never stays in the
address bar and never enters history twice. Bookmarks and any link already sent
to somebody keep working.

Four rules, because a loose version of this rewrite is worse than none:

1. **It runs only at the legacy roots** — exactly `/worlds/:wid` and
   `/campaigns/:cid/world`, with an empty splat tail. A modern URL that also
   carries a stray `?section=` (`/worlds/realm/lore/current?section=items`) is
   already addressing a record by path, and the path wins; the query is
   ignored, not obeyed.
2. **`section` and `id` are consumed, never copied forward.** Carrying
   `?section=` into the new URL leaves the trigger in place and the redirect
   can fire against itself.
3. **A legacy `section` that the shape's allowlist rejects is ignored**, which
   is what happens today — the current code tests membership in `INDEX` and
   falls through silently. It must not be translated into a path, because that
   would manufacture exactly the invalid routes the allowlist exists to refuse.
4. **Modifiers are rebuilt per destination, not copied wholesale.** `v` only on
   a character redirect, `for` only on Images, `owner` only on a recordless
   Lore root — and `owner` is never combined with a legacy `id`, because one
   asks for a blank pre-owned form and the other for an existing record.

`location.state` is untouched by any of this. `CharacterPage`'s `‹ All
characters` link already carries `state={{ reveal: eid }}` so a campaign grid
does not swallow an unseated character on the way back; only the `to` string
changes, and the `state` prop stays exactly where it is.

Every in-app producer moves to the new form:

- `components/character/shared.tsx` — `charactersHref`
- `routes/SearchView.tsx` — `hitTo`, which gains the ability to point at a
  record directly (`/worlds/realm/lore/the-salt-pact`) rather than at a section
  carrying an id
- `routes/CharacterPage.tsx` — the lore chips (both the "open this entry" and
  the "start one owned by me" forms) and the greeting chips, four call sites
- `routes/CampaignHub.tsx` — the cast card's `Everyone →` foot and the
  per-face link
- `components/WorldOverview.tsx` — `onNavigate`
- `shell/rail.ts` — the Images row's `to`, and its `match`, which stops being a
  hardcoded `false`. That row's comment already says what to do: *"the day
  Images earns a real route this becomes a one-line `isUnder`."* It does, and
  the row lights.

`librarySections.ts` needs no change — `inLibrary` already tests `isUnder(p,
"/worlds")`, which covers every deeper path.

## Risks

**`.row`'s font is the one real appearance risk.** `index.css` has no global
`button { font: inherit }` and `.row` sets no font family, so it currently
renders in the browser's default *button* font; an `<a>` carrying the same class
would inherit the body serif instead. The fix is the one `.column-row` already
demonstrates — pin the class's typography explicitly — and the values must be
read off the live page before the conversion and asserted after it, not
guessed. A UA button default is not a value to reproduce from memory.

`.char-card-main` is **not** exposed to this: it already sets
`font-family: var(--fb)`. Neither class sets `text-decoration`, though, so both
need `text-decoration: none` or the converted anchors arrive underlined. That is
the whole of the CSS change: one declaration on `.char-card-main`, and
typography plus that declaration on `.row`.

**Unsaved edits are unchanged, not improved.** Clicking another record while a
form holds unsaved changes discards them today, with no warning. A link does
the same thing. This design does not fix that and does not make it worse;
naming it here so a reviewer does not read the silence as an oversight.

**A section change must not remount the page.** Covered by the splat route
above, and worth a test of its own rather than trust: the failure mode is
invisible (a second round of fetches) until the counts flicker.

**But the counts are supposed to re-run on a section change.** The existing
effect depends on `section` deliberately — leaving a section is the first moment
the column can hear about a record created in it, since the editors own their
own lists and cannot say they added to one. The route-derived `section` stays in
that dependency list. "One instance" buys not re-reading the world and its
cover; it must not be mistaken for a licence to freeze the counts.

## Testing

`vitest`, in `WorldView.test.tsx` plus the four suites that mint links:

- each section renders at its own path, in both the world and campaign shapes
- a column row is an `<a href="…">` carrying the section's path. This is the
  assertion that actually covers the feature: right-click and middle-click
  cannot be driven from jsdom, so the guarantee has to be stated as "it is a
  real link with a real href", which is what the browser needs to offer *Open
  in new tab*.
- a record row and a character card are likewise `<a href>`
- `?section=lore&id=…` lands on `/worlds/:wid/lore/…` with the entry open, and
  `replace`s rather than pushes
- `?section=characters` with no id still means the grid, not an empty character
  id — the existing guard, restated against the new path
- Back moves between sections rather than leaving the world
- `hitTo`, `charactersHref`, the hub's two producers and the rail's Images row
  emit the new form; the rail's Images row now lights on `/worlds/:wid/images`
- `WorldView` fetches the world once across a section change, while the counts
  re-read on each — the two halves of the previous point, which pass and fail
  independently

…and the failure cases the design itself creates, which is where a routing
change actually breaks:

- an unknown section (`/worlds/w/garbage`) redirects rather than rendering a
  page headed "Overview"
- a campaign-forbidden section (`/campaigns/c/world/tags`, `/images`,
  `/overview`) redirects to `/campaigns/c/world/characters`
- `/campaigns/:cid/world` redirects before the campaign's world id has arrived
- `/campaigns/:cid/world?section=lore&id=x` lands on the lore entry, not on
  Characters — the legacy translation beats the default redirect
- a modern record path carrying a stray `?section=` keeps the path's record
- a legacy `?section=` naming something the allowlist rejects is ignored, not
  translated
- `?for=`, `?v=`, `?owner=` each survive to their own destination and nowhere
  else; `state: { reveal }` survives the character back-link
- a record → section-root move blanks the form (`+ New` works as a link)
- a trailing or doubled slash resolves to the section root rather than a second
  address for it
- `sectionHref` refuses a campaign-forbidden section, and the union refuses
  `v` without a record, `owner` beside one, and a record id on Tags
- re-selecting the open record leaves an unsaved draft alone, and **Cancel**
  still discards it
- the same record id under a changed `wid`/`cid` reads in the new scope
- select A then B with A's read resolving last shows B
- Back across the greetings List/Plot map switch renders what the URL says,
  and the switch does not remount `GreetingEditor` or lose a half-written
  greeting

A browser pass with the `verify` skill for the font question in Risks:
computed-style comparison of `.row` and `.char-card-main` before and after.

## Out of scope

Record-level addressing for module template previews. The `.editor-list`
rail-versus-grid question `EntityEditor`'s comment files under #437. Any change
to what a section *shows*.
