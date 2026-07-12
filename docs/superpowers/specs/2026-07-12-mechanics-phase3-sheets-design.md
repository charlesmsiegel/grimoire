# Mechanics Phase 3 — sheets

Full design for Phase 3 of the Mechanics & Dice milestone (issue #161),
superseding `2026-07-12-mechanics-phase3-sheets-draft.md`. Depends on
Phase 1 (`2026-07-12-mechanics-phase1-modules-design.md`, landed) and
Phase 2 (dice engine, landed). Sheet instances for every sheetable entity:
storage, CRUD, validation, derived computation, seeding, a generic
editor/viewer, and coverage indicators.

## Decisions (settled 2026-07-12)

| Decision | Choice | Why |
|---|---|---|
| Bulk sheet creation | None this phase; deferred to Phase 7's creation wizard | Blank default sheets defeat the coverage indicator; the wizard is the real fix for fast sheet creation. |
| Campaign indicator | Detail-side Sheet section (per entity) + coverage counts in the campaign Mechanics panel | Information where you act on it, plus one at-a-glance view; no rail badges (noise). |
| Editor surface | Sidebar summary + **takeover editor** (sheet swaps the detail body; view→Edit→save) | Sheets are dozens of fields; the sidebar column is for the compact summary only. Phase 6 replaces just the takeover rendering. |
| Type change | Preserves values for keys shared via common groups; drops orphans after a client-side confirm listing them | Warden→Adept keeps attributes/skills; the module's composition model makes "shared" well-defined (same assembled key). |
| World starting-sheet editing | Module context **picker** (not world-default-only) | The primary use case is world default = none with campaigns opting in; default-only would make world sheets unreachable exactly then. |
| Invalid sheets | Flagged (per-sheet `errors` on read; invalid counts in coverage), never auto-deleted; repairable in the editor incl. type change | Phase 1 rule: module switches flag, don't destroy. |
| Sheet writes without a module | Rejected | A sheet is meaningless without a schema to validate against. |

## Storage (per the Phase 1 contract)

- Campaign sheets (live, mutable): `<campaign>/sheets/<kind>--<id>.json`;
  PCs use `pcs--<id>.json` but validate against `characters` sheet types.
- World starting sheets: `<world>/sheets/<mid>/<kind>--<id>.json`.
- File shape: `{"sheet_type": "<type>", "fields": {...}}`. Derived values
  are computed on read, never stored.
- Sheets are campaign-owned mutable state — **copied at create, never
  overlay-read**. Later edits to world sheets do not propagate to existing
  campaigns; changing a campaign's module later never re-seeds.

## `store/sheets.py`

Pure stdlib. Never raises on malformed sheet-file content (same posture as
`modules.load_pack`); domain exceptions only for missing campaign/world and
rejected writes.

- `read(cid, kind, eid) -> dict | None` — `None` when no sheet file.
  An unparseable/wrong-shaped file returns
  `{"sheet_type": None, "fields": {}, "derived": {}, "errors": [<why>]}`
  rather than raising. Otherwise returns
  `{"sheet_type", "fields", "derived", "errors"}`:
  - `derived`: every group- and type-level derived expression evaluated
    over the numeric scope (`number`/`dots`/`track` → `key`; `resource` →
    `key` current and `key_max`; `track` value = checked-box count).
    Expression evaluation failures surface in `errors`, not as raises.
  - `errors`: validation of the stored sheet against the **currently
    resolved** module — unknown sheet type, kind mismatch, bad values
    (via `modules.validate_sheet_values`), or "no module resolved". A
    non-empty list marks the sheet *invalid* but readable.
- `write(cid, kind, eid, sheet_type, fields)` — validates: a module
  resolves (`SheetError` otherwise); `sheet_type` exists and targets this
  kind (`pcs` maps to `characters`); `validate_sheet_values` passes.
  **Type change is a `write` with a new `sheet_type`**: the store keeps
  values whose keys exist in the new type's assembled field set and drops
  the rest.
- `delete(cid, kind, eid)`; `list_refs(cid) -> list[(kind, eid)]`.
- World variants `read_world / write_world / delete_world(wid, mid, kind,
  eid)` — validated against `mid` directly (the UI picker's choice); no
  campaign resolve involved.
- `seed(cid)` — called from `campaigns.create_campaign` after the calendar
  copy: if the campaign resolves to module M and `<world>/sheets/M/`
  exists, copy those files into `<campaign>/sheets/`.
- `coverage(cid) -> dict` — per sheetable kind (kinds with ≥1 sheet type
  in the resolved module): `{"total", "sheeted", "invalid"}`. Totals count
  campaign-visible entities (overlay-merged, minus tombstones);
  `characters` and `pcs` are **separate coverage rows** (both validate
  against `characters` sheet types, but they live in different stores and
  the counts answer different questions). `{}` when no module resolves.
- `world_coverage(wid, mid) -> dict` — same shape; totals are world
  entities; sheeted counts from `<world>/sheets/<mid>/`.

## Reserved field keys (modules.py addition)

Two new load-time pack validation errors, closing the deferred
expression-visibility gap before sheets rely on it:

- A field key may not be one of the expression function names
  (`min`, `max`, `floor`, `ceil`, `abs`) — such names are invisible to
  `expressions.names()` and would silently skip validation.
- A field key may not equal another field's implicit resource-max name
  (`<key>_max` of a `resource` field in the same assembled set).

Both reference packs must still validate clean.

## Routes

Registered **before** the generic `{kind}` catch-alls (house rule, same as
`/module` and `/rolls`):

- `GET /api/campaigns/{cid}/sheets` → `{"coverage": {...}, "refs": [...]}`
- `GET /api/campaigns/{cid}/sheets/{kind}/{eid}` → sheet dict or 404
- `PUT /api/campaigns/{cid}/sheets/{kind}/{eid}` body
  `{sheet_type, fields}` → `{"ok": true}`; 400 on validation failure or no
  module; 404 on unknown campaign/kind/entity
- `DELETE /api/campaigns/{cid}/sheets/{kind}/{eid}`
- `GET /api/worlds/{wid}/sheets/{mid}` → world coverage + refs
- `GET/PUT/DELETE /api/worlds/{wid}/sheets/{mid}/{kind}/{eid}`

Sheet payloads carry no schema; the editor reads the module schema from
the existing `GET /api/modules/{mid}`. Models stay pydantic v1/v2-agnostic.

## Frontend

### SheetPanel (`components/SheetPanel.tsx`)

Kind-agnostic `.side-section`, rendered in every sheetable entity's detail:
inside the existing `.detail-sidebar` for `EntityEditor`/`PCEditor`; as a
stacked section in `CharacterEditor`'s single-column detail (next to its
campaign-only Version block). Props: `scope`, `kind`, `eid`, and the module
context (campaign: resolved module via `getCampaignModule`; world: the
picker's choice from WorldMechanics). Hidden when the context module has no
sheet type for the kind.

States:
- **Unsheeted** — "No sheet" hint + Add sheet: a sheet-type select
  (filtered to the kind; auto-selected when only one) and Create, which
  writes **schema defaults** and opens the editor. Defaults per field
  type: the field's `default` where declared, else `number`/`dots` 0,
  `track` 0, `text` "", `list` []; `resource` gets
  `{current: default ?? max, max: <schema max>}`.
- **Sheeted** — sheet-type chip + compact read-only summary: resources as
  `current/max`, derived values as labeled chips (the shape Phase 4's
  context summaries will mirror) + Open sheet.
- **Invalid** — summary + warning hint listing `errors`; Open sheet still
  available for repair.

### SheetEditor (takeover)

Opening a sheet swaps the entity's detail body for a full-width sheet view
(house view→edit rhythm). View: one labeled section per group (then own
fields), `label: value` rows, derived values rendered computed/read-only.
Edit widgets:

| type | widget |
|---|---|
| `number`, `dots`, `track` | number input, bounded by schema min/max |
| `resource` | paired current / max number inputs |
| `text` | text input |
| `list` | textarea, one entry per line |

Header actions: Save (PUT → back to view with fresh derived), Cancel,
**Change type…** (select of the kind's other types; confirm dialog lists
the field values that will drop, computed client-side from the two
assembled field sets), Delete sheet (confirm). Save failures and sheet
`errors` render in the standard `.banner`. Pretty rendering is Phase 6;
this editor is deliberately plain.

### Coverage

- **Campaign**: `MechanicsConfig` gains a coverage block under the module
  select (only when a module resolves): one hint-styled line per kind —
  "Characters 12/30 · Items 0/8", appending "· 2 invalid" when non-zero.
- **World**: `WorldMechanics` gains the same block, per module, headed by
  a "Starting sheets for:" module select — default: the world default
  module, else the first module with a `<world>/sheets/<mid>/` directory,
  else the first installed module. The selection is lifted into
  `WorldView` state and threaded to world-scope SheetPanels as their
  module context (alongside the existing `scope` threading).

### Client API (`api/client.ts`)

`Sheet`, `SheetCoverage` types; `getCampaignSheets(cid)`,
`getSheet/putSheet/deleteSheet(cid, kind, eid, ...)`,
`getWorldSheets(wid, mid)`, `getWorldSheet/putWorldSheet/deleteWorldSheet`.

## Reference module fleshing

Small additions so every widget type appears on at least one sheet type:
`pool-basic` character types gain two more abilities and a `text` +
`list` field each (e.g. quirk, gear); `d20-basic`'s adept gains a spell
`list`. Packs stay minimal and must keep validating clean.

## Testing

- **Store** (`test_sheets_store.py`, `GRIMOIRE_HOME`-isolated): CRUD per
  kind incl. `pcs`→`characters` mapping; derived computation (resource
  `_max`, group + type expressions, evaluation-failure → errors);
  write-rejection with no resolved module; invalid flagging after a module
  switch; type-change preservation/dropping; `seed` on create + no re-seed
  on later binding; world CRUD keyed by module; coverage (overlay-merged
  totals incl. tombstones excluded; world per-module); malformed sheet
  files tolerated.
- **Modules**: reserved-key and `_max`-collision errors; reference packs
  still clean (incl. the fleshed versions).
- **Routes**: endpoint round-trips, 400/404 mappings, ordering vs `{kind}`
  catch-alls.
- **Frontend (vitest)**: SheetPanel three states; SheetEditor
  view→edit→save, type-change confirm, delete; MechanicsConfig coverage;
  WorldMechanics picker driving panel context; existing editor tests
  extended where their client mocks need the new api fns (SheetPanel
  mounts inside detail views).
- **End state**: bind `pool-basic` to a campaign, sheet a character via
  the UI flow (type picker → defaults → edit → save), see coverage move
  from 0/N to 1/N; author a world starting sheet under a module the world
  doesn't default to; create a campaign bound to that module and confirm
  the sheet seeded.

## Out of scope

Pretty rendering/layouts/themes (Phase 6); sheets in LLM context and roll
integration (Phase 4); absorb-proposed deltas (Phase 5); creation
budgets/bulk creation (Phase 7); attaching content entries to sheets
(Phase 7).

## Privacy note

All names in fixtures, tests, and docs are invented, per the repo privacy
rule.
