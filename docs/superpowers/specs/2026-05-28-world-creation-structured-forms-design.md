# Structured entity forms — foundation (proven on characters)

> Sub-project 1 of 4 for issue #441 ("UI Improvements for World Creation").
> SP2 extends the forms to all entity kinds; SP3 adds rich creation + auto-id;
> SP4 adds the guided world hub. Each gets its own spec → plan → implementation.

## Problem

Authoring a character in the library is unnatural. Creation is bare `id` + `name`
(`EntityListView.tsx`), then the editor drops you into a **generic untyped
key-value editor** (`FrontmatterEditor.tsx`): to set `role`, `aliases`, `age`,
`tags`, or `structural_relationships` you must *know* the frontmatter key, pick a
type from a dropdown, and hand-build nested objects/lists. Only Identity / Voice /
Image have structured sub-forms (`CharacterExtras.tsx`); everything else falls
through to the raw editor.

Meanwhile the backend already defines a rich, typed schema
(`types/characters.py`: `CharacterData` with `role` enum, `VoiceAnchor`,
`ImagePromptTemplate`, `StructuralRelationship`, etc.). The UI simply doesn't
surface it. There is also no signal of how much **context budget** an entity
consumes, even though the Context Builder spends real tokens on it.

## Goal (SP1)

A reusable, declarative **field-descriptor** system that renders a real
structured form for an entity kind, with anything unknown preserved under a
collapsed **"Advanced / raw fields"** section. Prove it end-to-end on
**characters**, and show a live **token estimate** in the editor header.

## Non-goals (SP1)

- Descriptors for non-character kinds (SP2).
- Token badges on entity **list cards** (SP2).
- Rich world/entity **creation** forms and auto-id (SP3).
- The guided **world hub** and layout cleanup (SP4).
- A character **image gallery** editor (`images: list[CharacterImage]`) — stays in
  the existing `ExtrasTable`/raw path for now.
- Any change to storage, the read cascade, or the create/update API contract
  (still file-as-SSOT through the existing `frontmatter` dict + `body`).

## Design

### 1. Field-descriptor system — `entitySchemas.ts`

A descriptor declares, per kind, the sections and fields a structured form
should render. It is the single front-end source of the form shape; values still
flow through the existing `frontmatter` dict, so a field's `key` is the
frontmatter key it reads/writes.

```ts
type Widget =
  | "text" | "textarea" | "number" | "bool"
  | "enum"        // fixed options → <EnumSelect>
  | "tags"        // list<string>, chip input (e.g. tags, role_tags, aliases)
  | "stringList"  // list<string>, multiline rows (reuse StringListEditor)
  | "ref"         // single entity ref → <RefPicker> over a target kind
  | "refList"     // list<ref>
  | "object"      // nested group of fields (e.g. voice, image)
  | "objectList"; // list of nested field-groups (e.g. structural_relationships)

interface FieldDescriptor {
  key: string;
  label: string;
  widget: Widget;
  help?: string;
  options?: { value: string; label: string }[]; // enum
  refKinds?: EntityKind[];                       // ref / refList target(s)
  fields?: FieldDescriptor[];                     // object / objectList children
  rows?: number;                                  // textarea
}

interface EntitySectionDescriptor { title: string; fields: FieldDescriptor[]; }
interface EntityDescriptor { kind: EntityKind; sections: EntitySectionDescriptor[]; }
```

The **character descriptor** (SP1's only descriptor) covers:

| Section | Fields (frontmatter key → widget) |
|---|---|
| Identity | `name`→text, `id`→text (read-only display), `role`→enum (`pc`/`major_npc`/`minor_npc`/`ensemble`/`named_flavor`), `aliases`→tags, `age`→text, `tags`→tags, `role_tags`→tags, `household_id`→text |
| Description | `description`→textarea |
| Voice | `voice`→object: `summary`→textarea, `voice_register`→text, `samples`→stringList, `speech_patterns`→stringList, `dos`→stringList, `donts`→stringList, `address_terms`→object(map) |
| Image prompt | `image`→object: `base_prompt`→textarea, `negative_prompt`→textarea, `canonical_seed`→number |
| Relationships | `structural_relationships`→objectList: `to_ref`→ref(characters, factions), `kind`→text, `note`→text |

`privacy` and `images` are intentionally left to the Advanced section in SP1.

### 2. `<EntityForm>` renderer

Pure controlled component:

```ts
function EntityForm(props: {
  descriptor: EntityDescriptor;
  frontmatter: Frontmatter;
  body: string;
  worldId: string;                 // for ref pickers
  onFrontmatterChange: (next: Frontmatter) => void;
  onBodyChange: (next: string) => void;
}): JSX.Element
```

- Renders each section as a `<fieldset>` (matching existing `.character-card`
  styling), each field via the widget map.
- **Known keys** = the set of all `key`s the descriptor manages (including nested
  object keys' top-level parent, e.g. `voice`, `image`,
  `structural_relationships`). The Markdown `body` is edited in its own panel as
  today.
- **Advanced / raw fields**: a collapsed `<details>` wrapping the existing
  `FrontmatterEditor` with `hiddenKeys` = the descriptor's managed keys. This
  surfaces `extras`, `privacy`, `images`, and any unknown keys, fully editable.
  Nothing is ever dropped.

This component replaces the `EditorPanel` frontmatter half in
`EntityEditorView.tsx` for kinds that have a descriptor; kinds without one keep
the current generic editor (so SP1 changes nothing for non-characters).

### 3. Widgets

- Reuse `StringListEditor` (currently nested in `CharacterExtras.tsx`) — extract
  it to its own module so `EntityForm` and `CharacterExtras` share it.
- `<EnumSelect>` — `<select>` over `options`.
- `<TagsInput>` — chip-style list<string> editor (comma/enter to add).
- `<RefPicker>` — autocomplete `<input>` + datalist over the world's entities of
  `refKinds`, fetched via `libraryApi.listEntities`. Stores the selected
  `asset_id`. Free text allowed (refs may point at not-yet-created entities).
- `<ObjectListEditor>` — add/remove rows, each row a sub-form of `fields`
  (powers `structural_relationships`).
- `<MapEditor>` — key/value rows for `address_terms` (object of string→string).

Voice/Image become descriptor `object` sections, so `CharacterExtras.tsx` is
superseded by the descriptor + `EntityForm` and is removed (its Identity/Voice/
Image coverage is fully reproduced).

### 4. Token estimate — `tokens.ts` (frontend) + `<TokenBadge>`

- Add dependency `js-tiktoken` (pure-JS, no WASM). Lazy-load the `cl100k_base`
  encoding on first use so its rank table stays out of the initial bundle.
- `estimateTokens(text: string): number` — encodes and returns length; the
  encoder is loaded once and memoized. Before the encoder resolves, fall back to
  `Math.ceil(len / 4)` so the badge never blocks render.
- The estimated cost of an entity = `estimateTokens(serializeFrontmatter(fm) +
  "\n" + body)`. Reuse the existing frontmatter serializer used on save so the
  estimate matches what is written to disk.
- `<TokenBadge value={n} />` renders `~{n} tokens` (with thousands separator),
  `title` explaining it's a cl100k estimate, not the exact Claude count.
- In SP1, `<TokenBadge>` sits in the **editor header** (`entity-editor-header`)
  and recomputes (debounced ~150ms) as frontmatter/body change.

### 5. Schema-drift guard

To stop a renamed/removed Pydantic field from silently dropping a widget:

- **Backend route** `GET /library/entity-schemas/{kind}` → `model_json_schema()`
  for the kind's model (`Character`/`Location`/`Item`/`Monster`/`Faction`/
  `LoreEntry`). Thin, read-only, no service state. Path sits outside the
  `/library/worlds/{world_id}/{kind}` namespace to avoid the catch-all matcher
  (`library.py:284`).
- **Frontend test** asserts every `key` the character descriptor manages (and
  nested object child keys) exists in the `Character` schema's `properties`. A
  drift fails CI rather than silently losing a field. SP2 extends the test to
  each new descriptor.

## Components / files

New (frontend):
- `frontend/src/routes/library/entitySchemas.ts` — descriptor types + character descriptor.
- `frontend/src/routes/library/EntityForm.tsx` — renderer.
- `frontend/src/routes/library/widgets/` — `EnumSelect.tsx`, `TagsInput.tsx`,
  `RefPicker.tsx`, `ObjectListEditor.tsx`, `MapEditor.tsx`, `StringListEditor.tsx`
  (extracted).
- `frontend/src/components/tokens.ts` — `estimateTokens` + encoder lazy-load
  (helper module paired with the component, mirroring `schemaForm.ts`/`SchemaField.tsx`).
- `frontend/src/components/TokenBadge.tsx`.

Changed:
- `frontend/src/routes/library/EntityEditorView.tsx` — use `EntityForm` for kinds
  with a descriptor; token badge in header.
- Remove `frontend/src/routes/library/CharacterExtras.tsx` (superseded).
- `frontend/src/api/library/worlds.ts` — `getEntitySchema(kind)` client.
- `frontend/package.json` — add `js-tiktoken`.

New (backend):
- `backend/src/grimoire/api/library.py` — `GET /library/entity-schemas/{kind}`
  returning `model_json_schema()`.

## Data flow

Unchanged contract. `EntityForm` reads `entity.frontmatter` + `entity.body`,
writes back the same shapes; save still calls
`libraryApi.updateEntity(world, kind, id, { frontmatter_patch, body })`. Known
keys are owned by the structured widgets; unknown keys round-trip through the
Advanced editor untouched.

## Error handling

- `RefPicker` autocomplete fetch failure → input still works as free text; no
  blocking error (refs are advisory strings).
- `estimateTokens` before encoder load or on encoder failure → `len/4` fallback;
  badge shows the estimate regardless.
- Schema-fetch failure for the drift test is a test-time/CI concern only; runtime
  rendering never depends on the backend schema.

## Testing

- **Component (Vitest + RTL)**: `EntityForm` round-trips a frontmatter dict —
  known keys render in their widgets, unknown keys (`extras`, `privacy`) appear in
  Advanced; editing a structured field and an Advanced field both reflect in
  `onFrontmatterChange`; `objectList` add/remove for relationships; `RefPicker`
  suggests world entities and stores `asset_id`.
- **tokens**: `estimateTokens` returns the encoder length once loaded and the
  `len/4` fallback before; `<TokenBadge>` formats `~1,234 tokens`.
- **Drift test**: character descriptor keys ⊆ `Character` schema properties.
- **Scenario (backend)**: `GET /library/entity-schemas/character` returns a JSON
  schema whose `properties` include `role`, `voice`, `structural_relationships`.
- **Regression**: a character whose file already uses arbitrary extra frontmatter
  keys still shows those keys (in Advanced) and saving preserves them.

## Risks / trade-offs

- **Estimate accuracy**: tiktoken `cl100k` ≠ Claude tokenizer. Acceptable as a
  budget gauge; the label says "~". A future backend exact-count endpoint can
  swap in behind `estimateTokens` without UI changes.
- **Bundle size**: `js-tiktoken` rank data is ~1–2 MB uncompressed; lazy-loading
  keeps it off the critical path for users who never open an editor.
- **Descriptor drift**: mitigated by the schema-drift test (§5).
