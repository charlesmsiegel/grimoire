# Worlds: edit + delete buttons everywhere, no JSON inputs

Date: 2026-05-22
Scope: frontend only (`frontend/src/routes/library/`)
Backend: no changes — `PATCH` / `DELETE` endpoints already exist.

## Goal

Every element under `/library/worlds/...` is editable and deletable through a form. No screen requires the user to type or read JSON.

Today's gaps:

- Worlds, entities (characters, items, locations, lore, factions, greetings), and nested calendar items have no delete button anywhere in the UI.
- `WorldMetaView` renders Calendar / Atmosphere / Defaults as three raw `JSON.stringify` / `JSON.parse` textareas.
- `FrontmatterEditor` falls back to a JSON textarea for any list- or object-shaped frontmatter value, and offers "json (list/object)" as an Add-field type.

## Non-goals

- Reordering list items (calendar months, season order, etc.). Append + delete only.
- A schema-driven editor that reads JSON Schemas from the mechanics module. The dedicated forms here hard-code the canonical world-meta shape.
- Backend changes. `PATCH /library/worlds/{id}`, `DELETE /library/worlds/{id}`, `PATCH /library/worlds/{id}/{kind}/{entity_id}`, `DELETE /library/worlds/{id}/{kind}/{entity_id}` all already exist and stay as-is.
- Touching the campaign-side `WorldView.tsx` (it's read-only).

## Architecture & file layout

Two shared primitives plus three dedicated world-meta forms. All new files under `frontend/src/routes/library/`.

**New shared primitives:**

- `StructuredValueEditor.tsx` — recursive form for any JSON-shaped value (`string | number | boolean | null | array | object`). Replaces `JsonField` inside `FrontmatterEditor`; also used inside `WorldCalendarForm` for the `extras` bag and inside the inner `weather_bias` map.
- `ConfirmDestructiveDialog.tsx` — generalized version of today's inline `ConfirmEditDialog` in `EntityEditorView`. Accepts free-form title/body, an optional list of `CampaignRef` dependents, and an optional typed-confirmation requirement.

**New dedicated world-meta forms:**

- `WorldCalendarForm.tsx` — typed UI for the canonical calendar shape; extras fall through to `StructuredValueEditor`.
- `WorldAtmosphereForm.tsx` — same pattern, flatter shape.
- `WorldDefaultsForm.tsx` — same pattern; `default_style_guide_id` and `default_image_preset_id` render as `<select>` populated from `libraryApi.listStyleGuides()` / `listImagePresets()`.

**Edits to existing files:**

- `WorldsListView.tsx` — per-card delete button (typed-confirmation required).
- `WorldDetailView.tsx` — "Delete world" button in the header (typed-confirmation required). Both world-delete entry points share the same `ConfirmDestructiveDialog` configuration; deleting a world cascades to all its entities, hence the typed confirm.
- `WorldMetaView.tsx` — drop the three JSON textareas; render the three dedicated forms.
- `EntityListView.tsx` — per-card delete button.
- `EntityEditorView.tsx` — "Delete" button in the editor header; replace the inline `ConfirmEditDialog` definition with the new shared `ConfirmDestructiveDialog`.
- `FrontmatterEditor.tsx` — delete `JsonField`; render non-scalar fields with `StructuredValueEditor`; the "Add field" type picker offers `list` and `object` (not "json").

## Component contracts

### `StructuredValueEditor`

```ts
interface Props {
  value: unknown;                            // string | number | boolean | null | array | object
  onChange: (next: unknown) => void;
  readOnly?: boolean;
  label?: string;
}
```

Renders one of five layouts based on the runtime type of `value`:

| `typeof value` | Layout |
|---|---|
| `string` | text input |
| `number` | number input |
| `boolean` | checkbox |
| `null` | "(empty)" placeholder + type picker that initializes a default |
| `array` | numbered rows; `[+ add item]`; per-row `[×]`; each row recurses into `StructuredValueEditor` |
| `object` | key/value rows; `[+ add field]`; per-row `[×]`; key-rename inline; each value recurses |

Every row has a small type picker (text / number / boolean / list / object). Changing the type of a non-empty value triggers an inline confirm ("Changing list → text will discard 3 items. Continue?") — not a modal. The editor never displays JSON.

### `ConfirmDestructiveDialog`

```ts
interface Props {
  open: boolean;
  title: string;
  body?: React.ReactNode;
  dependents?: CampaignRef[];                // undefined = still loading; [] = none
  typedConfirmation?: { expected: string; label: string };
  confirmLabel?: string;                     // default "Delete"
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}
```

Reuses the existing `.modal-backdrop` / `.modal` markup so styling matches today's confirm-edit dialog. Confirm is disabled while `dependents === undefined` (lookup in flight) and, when `typedConfirmation` is set, until the typed string `===` `expected` (case-sensitive).

### `WorldCalendarForm`

```ts
interface WorldCalendar {
  epoch: string;                              // ISO date
  days_per_week: number;
  week_day_names: string[];
  months: { name: string; days: number }[];
  seasons: {
    name: string;
    start_month: number;
    start_day: number;
    palette: string;
    weather_bias: Record<string, number>;
  }[];
  holidays: {
    name: string;
    month: number;
    day: number;
    description: string;
    tags: string[];
  }[];
  extras: Record<string, unknown>;            // any keys not in the canonical shape
}

interface Props {
  value: WorldCalendar;
  onChange: (next: WorldCalendar) => void;
}
```

The form parses an incoming `unknown` calendar dict into `WorldCalendar` at the boundary (`parseCalendar`) and re-serializes it on every change (`serializeCalendar`). Unknown top-level keys collect into `extras`. Months, seasons, and holidays render as labeled row groups with `[+ add month]` etc. and per-row `[×]`. `weather_bias` is a small `Record<string, number>` editor (key + numeric value, add/remove rows). The `extras` bag renders via `StructuredValueEditor`.

### `WorldAtmosphereForm` / `WorldDefaultsForm`

Same `parse` → typed view → `serialize` pattern. `WorldAtmosphereForm` renders rows for `default_register` and `default_palette` (both strings). `WorldDefaultsForm` renders rows for `starting_location` (text), `default_style_guide_id` (select), `default_image_preset_id` (select). Both render `extras` via `StructuredValueEditor`.

## Data flow & state

**Save round-trip is unchanged at the network layer.** `WorldMetaView` still calls `libraryApi.updateWorld(id, body)` with `body.calendar` / `body.atmosphere` / `body.defaults` as plain objects — the dedicated forms produce those objects directly via `serializeCalendar` / `serializeAtmosphere` / `serializeDefaults`. The `JSON.parse` step in today's save handler is removed.

**Local state lives in the leaf forms.** Each of the three meta forms owns its draft and bubbles `onChange` up. `WorldMetaView` keeps a single `dirty` flag plus the top-level Save button, matching today's flow. No debouncing.

**`FrontmatterEditor`.** Value flow stays controlled-prop. Each row calls `update(key, next)` which spreads into a new `Frontmatter` object and bubbles up. `StructuredValueEditor` is internally controlled the same way. The current `useEffect` / `useRef` "skip-echo" logic in `JsonField` disappears with the textarea, leaving simpler code.

**Delete flow.**

1. User clicks Delete on a card or in an editor header.
2. For entity delete: component calls `libraryApi.dependents(worldId, kind, entityId)`. For world delete: component runs the same composition fan-out that today lives inline in `WorldDependentsView` (list `/api/campaigns`, fetch each `/api/campaigns/{id}/composition`, filter to those whose `worlds[]` references this world). This fan-out is extracted into a helper `fetchWorldDependents(worldId): Promise<CampaignRef[]>` colocated with `libraryApi`, and `WorldDependentsView` is updated to use the same helper (no duplication).
3. `ConfirmDestructiveDialog` opens immediately with `dependents={undefined}`. The Confirm button is disabled while the lookup is in flight so the user can't confirm without seeing the dependent list.
4. On confirm: `libraryApi.deleteWorld(id)` / `deleteEntity(worldId, kind, entityId)`. On 204, navigate away — `/library/worlds` for a world delete, the parent kind list (`…/{kind}`) for an entity delete — and reload the list.
5. On error: render the error inside the dialog; leave it open for retry.

**World delete typed confirmation.** The expected string is the world's `id` (not `name` — ids are stable). Case-sensitive match.

## Error handling

**Pre-submit validation (browser-enforced):**

- Calendar number fields use `<input type="number" min=… max=…>`. Month days, season `start_month` (1-12), `start_day` (1-31), holiday month/day.
- `epoch` is `<input type="date">`.
- `StructuredValueEditor` rejects duplicate object keys inline: red border + "key already exists" hint, and refuses to fire `onChange` until resolved.
- World-delete typed confirm: button stays disabled until the typed string matches.

**API failures:**

- Save failures (PATCH 4xx/5xx) → existing `setSaveErr` path in `WorldMetaView`. No change.
- Delete failures (DELETE 4xx/5xx) → render the message inside `ConfirmDestructiveDialog`; dialog stays open for retry. A 404 is treated as success (the thing is already gone) — close the dialog and reload.
- Dependents lookup failure → warning banner inside the dialog ("Couldn't load dependent campaigns; proceed at your own risk") rather than blocking. The user has already seen the body copy.

**Edge cases:**

- Removing the last item from a calendar list (zero months, zero seasons, zero holidays) is allowed; the dict serializes with an empty array. No "sanity" validation — that's the user's call.
- `StructuredValueEditor` with `value === null` shows a "(empty)" stub plus a type picker; picking a type initializes a default value (`""`, `0`, `false`, `[]`, `{}`).
- Changing a row's type when it holds data shows an inline single-line confirm with a count ("Changing list → text will discard 3 items. Continue?"). It does not open a modal.

## Testing

**New component tests** (Vitest + Testing Library, matching `ConvertModal.test.tsx` / `ImportDialog.test.tsx`):

- `StructuredValueEditor.test.tsx`
  - String renders as text input; typing fires `onChange`.
  - List: numbered rows, `[+ add item]` appends, `[×]` removes.
  - Nested object in a list (e.g. `[{name, days}]`) — edits propagate up.
  - Type picker on a non-empty list shows inline confirm before switching to text; only fires `onChange` after confirm.
  - Duplicate object key shows red hint; no `onChange` fires.
- `ConfirmDestructiveDialog.test.tsx`
  - No dependents: confirm enabled.
  - Dependents loading (`undefined`): confirm disabled.
  - Dependents list renders when populated.
  - `typedConfirmation`: confirm disabled until match (case-sensitive).
  - `busy=true`: confirm disabled, spinner copy.
- `WorldCalendarForm.test.tsx`
  - Round-trip: feed the `sakura-high` calendar dict, render, edit one month's `days`, assert the resulting object matches input with that single mutation.
  - Add holiday: click `[+ add holiday]`, fill rows, assert new entry appended.
  - Unknown extra key (e.g. `calendar.lunar_phase: { period: 28 }`) renders via `StructuredValueEditor` and survives a no-op round-trip.
- `WorldAtmosphereForm.test.tsx` / `WorldDefaultsForm.test.tsx`
  - Round-trip + extras tests as above.
  - `WorldDefaultsForm` additionally asserts the `default_style_guide_id` and `default_image_preset_id` selects populate from mocked `libraryApi.listStyleGuides()` / `listImagePresets()`.

**Updated existing tests:**

- `WorldsListView` / `WorldDetailView`: delete test (mock `libraryApi.deleteWorld` and `fetchWorldDependents`, click delete, satisfy typed-confirm, confirm, assert refetch / navigate).
- `WorldDependentsView`: assert it still works after the fan-out is moved into `fetchWorldDependents`.
- `EntityListView`: one parametric delete test covering characters + greetings (shared code path).
- `EntityEditorView`: header delete → dialog with dependents → confirm → navigate back to list.
- `FrontmatterEditor` test (new if absent): nested-object field renders via `StructuredValueEditor`; no `<textarea>` in DOM.

**Backend tests:** none new. `DELETE` / `PATCH` endpoints have existing coverage.

**Manual verification before claiming done:** start frontend dev server, open `/library/worlds/sakura-high/meta`, edit a holiday, save, hard-reload, confirm persistence; delete a character from the entity list, confirm it's gone from disk under `data/library/worlds/sakura-high/characters/`.

## Implementation sequencing

Three independently reviewable steps. The implementation plan (writing-plans) should split commits along these lines:

1. **Shared primitives.** Add `StructuredValueEditor` + `ConfirmDestructiveDialog` with their tests. Lift `ConfirmEditDialog` out of `EntityEditorView` and replace it with `ConfirmDestructiveDialog` configured for the save-warn case (proves the abstraction).
2. **Delete buttons.** Wire delete into `WorldsListView`, `WorldDetailView`, `EntityListView`, `EntityEditorView`. Each calls dependents-lookup and opens `ConfirmDestructiveDialog`. World delete uses typed-confirmation.
3. **Forms replace JSON.** Add `WorldCalendarForm`, `WorldAtmosphereForm`, `WorldDefaultsForm`; rewire `WorldMetaView` to use them and drop the three textareas. Replace `JsonField` in `FrontmatterEditor` with `StructuredValueEditor`; update the Add-field type picker.

Each step is shippable on its own.
