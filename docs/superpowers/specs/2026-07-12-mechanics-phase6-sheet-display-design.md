# Mechanics Phase 6 — sheet display

Full design for Phase 6 of the Mechanics & Dice milestone (issue #165),
superseding `2026-07-12-mechanics-phase6-sheet-display-draft.md`. Depends on
Phase 1 (modules, landed) and Phase 3 (sheets, landed); independent of
Phases 4/5. Pretty sheet rendering: a widget library per field type, a
per-sheet-type `layout.json` arrangement format, and per-module visual
theming — replacing the Phase-3 generic `label: value` rendering.

## Decisions (settled 2026-07-12)

| Decision | Choice | Why |
|---|---|---|
| Rendering model | Three independent layers: **widgets** (always on), **arrangement** (`layout.json`, optional), **skin** (`theme.json`, optional) | Every sheet gets widgets even in modules that ship no display files; layout and theme are additive polish, each with a graceful fallback. |
| Theme format | **`theme.json` token whitelist, not raw `theme.css`** (supersedes the Phase-1 contract line "layout.json + theme.css") | Sanitizing author CSS is a tarpit: `url()` exfiltration beacons, `position:fixed` shell takeover, parser edge cases — and modules are shareable data packs that must never smuggle active content. A validated token set (colors, font choices, shape hints) delivers "a d20 sheet looks like a d20 sheet, a pool sheet looks gothic" with zero attack surface, works identically in the Android WebView, and is Phase-8-authorable. |
| Display errors | `layout.json`/`theme.json` problems land in a **separate `display_errors`** list, never in pack `errors` | `modules.resolve()` refuses packs with `errors`; a cosmetic typo must not switch off mechanics for every campaign bound to the module. Invalid display files are dropped (per sheet type for layouts, per token for themes) with the reasons surfaced where they matter (see Display-error surfacing). |
| `theme.css` handling | A pack containing `theme.css` gets a `display_errors` entry ("not supported — use theme.json"); the file is otherwise ignored | No `theme.css` was ever loadable — Phase 1 documented the intent but nothing implements it, and the authoring skill never scaffolded one — so detection *is* the whole migration path; a converter would convert zero files. |
| Layout scope | Applies to **both** SheetEditor modes (view and edit); widgets swap read-only ↔ interactive per mode | A sheet that rearranges itself when you hit Edit is disorienting; the layout is the sheet's shape, not a view skin. |
| Edit interactivity | `dots`/`track` become **click-to-set** widgets in edit mode; `resource`/`number` keep (restyled) number inputs; `text`/`list` unchanged | Number inputs for ratings are the thing dot widgets exist to fix. Resource/number values are genuinely numeric — typing is fine. Play-time quick-adjust from *view* mode is deliberately out (Phases 4/5 own play-time mutation). |
| Derived placement | Derived values render **only where a `derived` layout node places them**; group nodes render fields only; unplaced derived (and fields) fall into a trailing fallback section | One explicit placement rule instead of automatic-plus-explicit duplication; the fallback section guarantees no stored or computed value is ever invisible. |
| Fallback arrangement | No/invalid layout for a sheet type ⇒ the Phase-3 structure (groups in order → own fields → derived) rendered with widgets | Ships the widget upgrade to every module on day one; layout only changes arrangement. |
| SheetPanel summary | Unchanged this phase | It is a compact sidebar summary; chips are the right shape. The Phase-4 draft's context-summary question stays with Phase 4. |
| Print/export | Out of scope | YAGNI until someone asks; the EPUB exporter is a separate pipeline. |

## `layout.json` — per-sheet-type arrangement

```json
{
  "fragments": {
    "attribute-block": {"row": [{"group": "attributes", "grid": true}]}
  },
  "sheet_types": {
    "medium": {
      "column": [
        {"use": "attribute-block"},
        {"row": [
          {"column": [{"group": "abilities"}], "title": "Abilities"},
          {"column": [
            {"fields": ["essence", "health"]},
            {"derived": ["awareness", "sight_pool"]}
          ], "title": "Essence"}
        ]}
      ]
    }
  }
}
```

Both top-level keys optional. A **node** is an object with exactly one of:

- `"row": [<node>…]` — children laid out horizontally (wrapping).
- `"column": [<node>…]` — children stacked vertically.
- `"group": "<gid>"` — that group's fields, in group order, as widgets
  (fields only — group derived render where a `derived` node places them).
- `"fields": ["<key>"…]` — specific fields from the sheet type's assembled
  set (group fields and own fields are both addressable).
- `"derived": ["<name>"…]` — derived badges (group- or type-level names in
  the sheet type's scope).
- `"use": "<fid>"` — splice in a fragment; shared groups can share one
  fragment so attributes render identically on every sheet type.

Optional keys on any node: `"title"` (renders panel chrome with a heading);
`"grid": true` on `group`/`fields` nodes (fields render as a stat grid of
compact value-over-label cells instead of stacked rows — the classic
attribute block).

### Validation (module load time, non-fatal)

Recorded in `display_errors` with the offending path; an error in a sheet
type's tree drops **that type's layout** (other types keep theirs); errors
in `fragments` drop only the sheet-type trees that `use` them:

- Root/`fragments`/`sheet_types` must be objects; sheet-type keys must
  exist in `sheets.json`.
- Every node must have exactly one of the six forms; unknown keys beyond
  `title`/`grid` are errors (catch typos early — this is authored JSON).
- **The schema is total** — every value type is checked, never assumed:
  `row`/`column` must be arrays of valid nodes, `group`/`use` non-empty
  strings, `fields`/`derived` arrays of non-empty strings, `title` a
  string, `grid` a boolean. A wrong-typed value (`"row": "x"`,
  `"fields": [1]`, `"grid": "yes"`) is an error on that node's path;
  nothing malformed ever reaches the renderer.
- `group` refs, `fields` keys, and `derived` names must exist in that sheet
  type's assembled scope; `use` refs must name an existing fragment.
- Fragment expansion is cycle-checked (visited set); the **expanded** tree
  is capped at depth 32 and 1000 nodes per sheet type (a hand-authored or
  generated blob past either cap drops that type's layout).
- A field or derived name may be placed **at most once** per sheet type
  (double-rendering an editable widget would create two competing inputs).

Fields/derived a valid layout does not place render after the layout root
in a trailing "Other" section (fallback arrangement, fields then derived).

## `theme.json` — visual tokens

```json
{
  "colors": {"bg": "#171a21", "ink": "#d8d2c4", "accent": "#8a2a3b"},
  "fonts": {"display": "display", "body": "serif"},
  "dots": "diamond",
  "corners": "sharp"
}
```

All keys optional; the whitelist is exhaustive:

- `colors` — `bg`, `ink`, `muted`, `accent`, `rule`; values must be hex
  (`#rgb`/`#rrggbb`). **`bg` and `ink` must be set as a pair** (else both
  are dropped with an error) — an authored background under the app
  theme's ink is an illegibility trap. `muted`/`accent`/`rule` fall back
  to the sheet ink, then to app tokens.
- `fonts` — `display` and/or `body`, each one of `display` | `body` |
  `mono` | `serif` | `sans`: the app-shipped stacks (`--fd`, `--fb`,
  `--fm`) plus generic `serif`/`sans-serif`. No font files, no URLs —
  Android-safe and offline-safe by construction.
- `dots` — `circle` | `square` | `diamond` (dot/track glyph shape).
- `corners` — `sharp` | `rounded`.

Unknown keys, unknown enum values, and malformed colors are per-entry
`display_errors`; the offending entry is dropped, the rest apply.

Application: the sheet takeover container gets scoped CSS custom
properties (`--sheet-bg`, `--sheet-ink`, `--sheet-muted`, `--sheet-accent`,
`--sheet-rule`, `--sheet-fd`, `--sheet-fb`) set inline from the validated
tokens, plus `data-dots`/`data-corners` attributes; widget CSS consumes
them with app-token fallbacks (`var(--sheet-bg, var(--surface))`). No
module-authored CSS ever reaches the DOM; nothing escapes the container.

## Backend (`store/modules.py`)

`load_pack` additions, same never-raise posture as every other pack file:

- Parse `layout.json` → `pack["layout"]`: `{"sheet_types": {<tid>: <tree
  with fragments already spliced>}}` — only the sheet types whose trees
  validated; `{}`/absent file ⇒ `{"sheet_types": {}}`. Fragments are
  expanded server-side so the frontend renderer never sees `use` nodes.
- Parse `theme.json` → `pack["theme"]`: the validated token object (dropped
  entries removed); absent ⇒ `{}`.
- `pack["display_errors"]`: a list of **structured** entries
  `{"source": "layout" | "theme", "sheet_type": "<tid>" | null,
  "message": "<path>: <why>"}` — structured so the UI can route a dropped
  layout to the sheet type it affects (`sheet_type` is set for per-type
  layout errors, `null` for file-level and theme errors). Not consulted by
  `resolve()` or the registry `valid` flag.
- A pack containing `theme.css` gets one `display_errors` entry
  (`source: "theme"`, "theme.css is not supported — use theme.json");
  the file is otherwise ignored.
- The registry `_scan` rows gain `display_ok: not display_errors` so
  lists can flag display problems without loading full packs twice.

No new routes: `GET /api/modules/{mid}` already returns the full pack, so
`layout`, `theme`, and `display_errors` flow to the client for free. No
`store/sheets.py` changes. Models stay pydantic v1/v2-agnostic (plain
dicts through `routes._dump`).

## Frontend

### Widget library (`components/SheetWidgets.tsx`)

One widget per field type plus the derived badge; each takes the field
def, the value, and (edit mode) an `onChange`:

| type | view | edit |
|---|---|---|
| `dots` | filled/empty glyphs up to `max` | glyphs are buttons: click dot *n* ⇒ value *n*; clicking the current value ⇒ *n−1* (reaches 0) |
| `track` | boxes, first *value* filled | same click-to-set semantics as dots |
| `resource` | bar (fill = current/max) with `current / max` text | bar plus the existing paired current/max number inputs |
| `number` | `label: value` row; compact value-over-label **stat cell** inside a `grid` node | number input (stat-cell-styled in grids) |
| `text` | labeled prose | text input |
| `list` | bulleted list | one-per-line textarea (existing draft/normalize discipline preserved) |
| derived | badge chip: label + computed value | same (read-only — derived are computed) |

Interactive glyphs are real buttons with `aria-label`s (`"Vigor 3"`), so
the widgets stay testable and accessible.

### Layout renderer (`components/SheetLayout.tsx`)

Renders a validated layout tree (rows via flex-wrap, columns via stack,
`title` panels, `grid` stat grids) over a sheet's field defs + values,
appending the "Other" section for anything unplaced. Exports the default
arrangement (used when the module has no layout for the type) built from
the same node model — groups → own fields → derived — so there is exactly
one rendering path.

### SheetEditor

The view/edit bodies swap their hand-rolled sections for
`SheetLayout` + widgets; mode toggling, Save/Cancel, type change, delete,
draft handling, and error banners are untouched. The takeover container
gains the theme vars + data attributes when the module ships a theme.
The list-field draft rule (raw string while editing, normalize at commit)
carries over unchanged.

### Display-error surfacing

Display problems must be visible **where sheets are used**, not only in
the library:

- **SheetEditor**: a hint line renders under the header — "This module's
  layout for this sheet type is invalid — using the default arrangement."
  — when the current sheet type has **no tree in `pack.layout`** *and* a
  `source: "layout"` entry either names the current sheet type or is
  file-level (`sheet_type: null`) **while no sheet-type tree survived at
  all** (a malformed root dropping every layout). All three conditions
  matter: an invalid-but-unused fragment (an error that drops nothing)
  must not raise a false alarm on sheets whose layouts survived, a type
  that never had a layout must not warn just because some *other* type's
  tree was dropped, and a file-level entry that coexists with surviving
  trees (unknown root key, unused broken fragment) must not warn
  never-layouted types either. Non-blocking; the fallback arrangement is
  fully functional.
- **Module library list**: rows for packs with `display_errors` get a
  hint-styled "display issues" marker (distinct from the existing
  invalid-module treatment — mechanics still work).
- **`ModulesView` detail**: a Display section when relevant — which sheet
  types have layouts, whether a theme is present, and every
  `display_errors` message (hint-styled warnings). `ModuleDetail` TS type
  gains `layout`, `theme`, `display_errors`; the list-row type gains
  `display_ok`.

### CSS

Widget/layout/theme styles join `index.css` (single-stylesheet
convention), all scoped under the sheet takeover. Everything uses the
`--sheet-*` custom properties with app-token fallbacks, so unthemed
modules render in the app's active theme exactly as today. Flex/grid
only — fine in the Android WebView (modern Chromium).

## Reference modules

Both built-ins gain `layout.json` + `theme.json`, staying the contract's
fixtures — between them they must exercise every node form (`row`,
`column`, `group`, `fields`, `derived`, `use`), `title`, `grid`, both
edit interactions, and every theme key:

- `d20-basic` — parchment look (light bg/ink pair, `serif` body), stat
  grid for attributes via a shared fragment used by both character types,
  rounded corners, `circle` dots.
- `pool-basic` — gothic look (dark bg/ink pair, `display` heading font,
  accent red), dotted attributes/abilities in columns, health track,
  `diamond` dots, sharp corners.

The `create-mechanics-module` skill gains layout/theme authoring steps
(schema reference + validate-after-each-step, as with the other files).

## Testing

- **Backend** (`test_modules` additions): both reference packs validate
  with zero `errors` *and* zero `display_errors`; a broken-pack fixture
  per layout error (non-object root, unknown node form, unknown key on a
  node, **wrong value type per node form** — `row` non-array, `fields`
  with non-string entries, `grid` non-boolean, `title` non-string —
  unknown group/field/derived/fragment ref, fragment cycle, depth cap,
  node-count cap, duplicate placement, layout for a nonexistent sheet
  type) and per theme error (bad hex, `bg` without `ink`, unknown font
  enum, unknown top-level key, `theme.css` present); granularity (one bad
  sheet-type tree drops only itself; one bad token drops only itself);
  fragment splicing output contains no `use` nodes; structured entries
  carry the right `sheet_type`; `display_errors` never affect `errors`,
  the registry `valid` flag, or `resolve()`; `_scan` rows carry
  `display_ok`.
- **Frontend (vitest)**: per-widget render + edit-interaction tests (dot
  click-to-set incl. decrement-at-current, track same, resource inputs,
  stat cell); layout renderer (row/column nesting, titles, grid, unplaced
  → "Other"); SheetEditor with a layouted module (same arrangement in
  view and edit, save round-trip unchanged); SheetEditor with no layout
  (default arrangement, widgets still used); SheetEditor dropped-layout
  hint routing (fires when an entry names the current type; fires on a
  file-level `sheet_type: null` layout error when no tree survived; does
  NOT fire for an unused-fragment error when the current type's layout
  survived, nor for a never-layouted type when only another type's tree
  was dropped, nor for a never-layouted type when a file-level entry
  coexists with another type's surviving tree); theme vars +
  data attributes present when themed, absent when not; ModulesView
  Display section incl. `display_errors`; library list "display issues"
  marker.
- **End state**: bind `pool-basic`, open a sheeted character — the gothic
  dotted sheet renders and dots are clickable in edit; `d20-basic` shows
  the parchment stat-grid sheet; a scaffolded user module (no display
  files) still renders every sheet with widgets in the app theme.

## Out of scope

Print/export views; play-time quick-adjust from view mode (Phases 4/5);
module-customizable Phase-4 context summaries (Phase 4); SheetPanel
summary redesign; layout/theme authoring UI (Phase 8); custom font files
or images in packs.

## Privacy note

All names in fixtures, tests, and docs are invented, per the repo privacy
rule.
