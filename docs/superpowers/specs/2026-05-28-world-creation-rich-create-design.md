# Rich world & entity creation + auto-derived ids

> Sub-project 3 of 4 for issue #441. Depends on **SP1** (the `<EntityForm>` system
> + descriptors) and pairs with **SP2** (descriptors for all kinds). SP3 reuses
> descriptors in a "create" mode and replaces the bare id+name create forms.

## Problem

Creation starts from nothing. "+ New world" (`WorldsListView.tsx`) and
"+ New {kind}" (`EntityListView.tsx`) both ask only for a lowercase-hyphen `id`
and a `name`, then dump you into an editor. Two frictions:

1. **No substance up front.** Genre, description, tags, tone for a world — and
   role, description for a character — all require a second trip to the editor.
2. **Manual id.** Users must invent a `[a-z0-9][a-z0-9-]*` slug before they can
   even name the thing, and the id box is the *first* field.

Both `create_world` (accepts a `meta` dict) and `create_entity` (accepts a
`frontmatter` dict + body) already support richer payloads — the UI just doesn't
send them.

## Goal (SP3)

- **Auto-derive the id from the name** (editable), so naming comes first and the
  slug is automatic.
- **Rich world create**: name, genre, description, tags, and a collapsed
  "Tone & atmosphere" section, submitted via `meta`.
- **Rich entity create**: reuse the kind's `<EntityForm>` descriptor in a compact
  "create" mode (the most-common fields shown, the rest editable post-create),
  submitted via `frontmatter`.

## Non-goals (SP3)

- The guided world hub / layout cleanup (SP4).
- Calendar attachment at create time (stays in the Meta tab).
- A creation *wizard* (rejected during brainstorming in favor of a rich
  single-page form).

## Design

### 1. Auto-id helper — `slugify.ts`

`slugify(name): string` — lowercase, spaces/underscores→`-`, strip characters
outside `[a-z0-9-]`, collapse repeats, trim leading/trailing `-`. Pure, unit-
tested. Used by both create forms.

### 2. `<CreateForm>` id field behavior

A small shared `<IdField>`:
- Shows the derived id beneath the Name input: "id: `ravenmark` (auto)".
- Stays auto-synced to the name until the user edits it, then it "sticks"
  (a `touched` flag stops further auto-sync), matching common slug-field UX.
- Validates against the existing pattern `[a-z0-9][a-z0-9-]*`; surfaces the same
  inline error as today on collision/invalid.

### 3. Rich world create (`WorldsListView.tsx`)

Replace the inline `id`+`name` form with a richer one (still inline/expanding, not
a modal):

```
Name        [______________]   id: <auto> (auto)
Genre       [______________]
Description [______________]
Tags        [chip chip + ]
▸ Tone & atmosphere (collapsed)
   Default register [____]   Default palette [____]
                                   [Create world]
```

Submits `createWorld(id, meta)` where `meta` = `{ name, genre, description, tags,
atmosphere: { default_register, default_palette } }` (atmosphere keys only
included when non-empty). The "Tone & atmosphere" section reuses the
`default_register`/`default_palette` fields from `WorldAtmosphereForm` so create
and the Meta tab agree on shape. Navigate to the new world on success (unchanged).

### 4. Rich entity create (`EntityListView.tsx`)

Replace the inline `id`+`name` form with `<EntityForm>` in **create mode**:
- `mode="create"` renders only fields flagged `createDefault` in the descriptor
  (e.g. character: name, role, description; location: name, kind, description) plus
  the `<IdField>`. The full form (all sections + Advanced) is available after
  create in the editor.
- Add an optional `createDefault?: boolean` to `FieldDescriptor` (SP1 type); the
  character/all-kind descriptors mark their headline fields. Absent flag → not
  shown in compact create.
- Submits `createEntity(world, kind, { id, frontmatter, body })` with the compact
  values. Greetings keep their existing create path (bespoke form).

### 5. Wiring

Both forms share `slugify` + `<IdField>`. `<EntityForm>` gains a `mode?:
"edit" | "create"` prop (default `"edit"`); in create mode it filters to
`createDefault` fields and omits the Advanced section. No new backend work — both
endpoints already accept the richer payloads (`api/library.py:34,49`).

## Components / files

New (frontend):
- `frontend/src/routes/library/slugify.ts` + test.
- `frontend/src/routes/library/IdField.tsx`.

Changed (frontend):
- `WorldsListView.tsx` — rich create form, auto-id.
- `EntityListView.tsx` — `<EntityForm mode="create">`, auto-id.
- `entitySchemas.ts` — add `createDefault` flags to descriptors.
- `EntityForm.tsx` — `mode` prop (compact create rendering).

No backend changes.

## Error handling

- Id collision / invalid → same inline error as today (server-validated).
- Empty name → submit disabled (name required), id derives from name so it can't
  be empty independently.
- Atmosphere/extra sections empty → omitted from payload, not written as blanks.

## Testing

- **slugify**: `"Ravenmark"→"ravenmark"`, `"The Old Gods"→"the-old-gods"`,
  unicode/punctuation stripped, leading digits/hyphens handled.
- **IdField**: auto-syncs until edited, then sticks; invalid id shows error.
- **World create**: submitting sends `meta` with name/genre/description/tags and
  (when filled) atmosphere; navigates to the new world.
- **Entity create**: character create-mode shows name/role/description only;
  submitting writes the corresponding frontmatter; full editor shows all sections
  after.
- **Scenario (backend)**: create a world via the rich payload, then a character
  via the compact payload; assert the world `world.yaml` and character frontmatter
  contain the submitted fields.

## Risks / trade-offs

- **Compact create field choice** is a judgment call; `createDefault` keeps it
  declarative and easy to tune per kind without touching the renderer.
- **Sticky-id UX**: once a user edits the id, renaming no longer updates it — the
  standard, least-surprising behavior; called out so it isn't mistaken for a bug.
