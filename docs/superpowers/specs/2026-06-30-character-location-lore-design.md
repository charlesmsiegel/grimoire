# Character- & location-owned lore — design

## Problem

Lore today is a flat list of markdown entries scoped to a **world** (and
optionally overlaid per **campaign**), activated purely by keyword matching
against recent chat (`activate()` in `store/context.py`). Much of the lore in a
real world actually belongs to *one* character, PC, or location — e.g. a
character's backstory, a location's hidden history. Filed at world level it
(a) clutters a single undifferentiated list and (b) either fires globally
(keyless = always-on) or leans on hand-maintained keywords, so it activates when
the owning character/location isn't even in the scene.

## Goal

Let a lore entry be **owned** by one or more characters, PCs, or locations,
which (1) files it under its owner in the UI and (2) gates its activation on the
owner being **present** in the scene — so owned lore goes quiet whenever its
owner is absent, including in the character-less scene opener.

Non-goals: changing how unowned (world-level) lore behaves; a new context
section; multi-world lore sharing; automatic cleanup of dangling owner refs.

## Data model

Add one optional frontmatter field, `owners`, to a lore entry — a
comma-separated list of typed references `kind:id` where `kind ∈
{characters, pcs, locations}`:

```
---
name: Tanaka's exile from the dojo
keys: exile, banishment
owners: characters:master-tanaka, locations:old-dojo
---
He was cast out after the duel...
```

- `owners` absent/empty → a **world-level** entry. Behaves exactly as today
  (keyless = always-on; keyed = keyword match). No migration; existing files are
  unchanged and parse with an empty owner list.
- The field lives in the generic entity frontmatter (`store/entities.py`), so it
  is harmless on any entity kind; only lore exposes it in the UI.
- A reference whose target was deleted is simply never "present" — harmless, no
  crash, no cleanup pass.

## Activation semantics

Extend `activate()` to be presence-aware. The caller computes the scene's
**present set** once:

```
present = { f"{a['kind']}:{a['id']}" for a in appearances.scene_cast(cid, sid) }
        ∪ ( {f"locations:{current_loc}"} if current_loc else ∅ )
```

where `current_loc = scenes.get_location_history(cid, sid)[-1]` (if any). `kind`
from `scene_cast` is already `characters` or `pcs`.

Per entry, with `owned = bool(entry.owners)`:

| owned | owner present | has keys | keyword matches | activates |
|-------|---------------|----------|-----------------|-----------|
| no    | —             | no       | —               | **yes** (always-on, unchanged) |
| no    | —             | yes      | yes             | yes (unchanged) |
| no    | —             | yes      | no              | no (unchanged) |
| yes   | **no**        | —        | —               | **no** (silent — the key win) |
| yes   | yes           | no       | —               | yes (on whenever an owner is in scene) |
| yes   | yes           | yes      | yes             | yes |
| yes   | yes           | yes      | no              | no (presence + keyword both required) |

"Owner present" = any of the entry's owners is in `present`.

The opener path (`build_opener_messages`) is character-less; it passes an empty
present set, so every owned entry stays silent there.

Owned lore continues to render inside the existing **"World info"** system
section — no new section.

## Backend / API changes

- `store/entities.py`: parse/serialize the `owners` frontmatter field
  (comma-separated `kind:id`, trimmed, empties dropped) alongside
  `name`/`keys`/`body`. Round-trips losslessly.
- `store/context.py`:
  - `activate()` gains a `present: set[str]` parameter and applies the truth
    table above. Default `present=frozenset()` keeps existing callers' behavior
    for unowned entries and silences owned ones.
  - `_world_info()` builds the present set from `scene_cast` + current location
    and threads `owners` through each entry dict; passes `present` to
    `activate()`.
  - `build_opener_messages()` calls `_world_info()` with an empty present set
    (owned lore silent).
- `routes.py`:
  - `EntitySummary` and `EntityDetail` expose `owners` (list of `kind:id`
    strings).
  - `EntityCreate` / `EntityUpdate` accept optional `owners`.
  - No new endpoint: lore lists are small, so the frontend filters/groups
    client-side.

## Frontend changes

- `api/client.ts`: add `owners?: string[]` to entity summary/detail types and
  create/update payloads. Add a small helper to fetch owner candidates for a
  world (characters, pcs, locations) for the picker.
- **Lore tab** (`EntityEditor` for `lore`):
  - Form metadata gains an **Owners** multi-select populated with the world's
    characters, PCs, and locations.
  - The rail groups entries: an **"Unowned (world)"** group plus one group per
    owner that has entries. Locations appear in the rail grouping like any other
    owner.
  - Read-only view renders owners as clickable `chip` buttons that navigate to
    the owning record (per the metadata-references-other-records convention).
- **Owner editors** (CharacterEditor, PC editor, and the location detail in
  `EntityEditor`): add a `.side-section` **"Lore"** block listing entries this
  record owns (clickable chips) plus a **+ New lore entry** button that opens the
  lore form with this owner pre-filled.

This stays within the established list/detail pattern (CLAUDE.md) and the single
`activate()` swap point.

## Testing

Backend (pytest, `GRIMOIRE_HOME` isolated):
- `activate()` truth-table: unowned unchanged; owned+absent → silent;
  owned+present+keyless → on; owned+present+keyed → keyword gates; multi-owner
  activates if any owner present.
- `entities` round-trip of `owners` (including empty/absent → `[]`).
- `_world_info()` / opener integration: owned entry appears only when its owner
  is in scene; opener keeps owned lore silent.

Frontend (vitest):
- Lore form shows the Owners multi-select and persists the selection.
- Rail groups entries by owner with an "Unowned (world)" group.
- Owner chips in the read-only view navigate to the owner.
- An owner editor's "Lore" section lists owned entries; **+ New** pre-fills the
  owner.

## Migration

None. `owners` is additive and optional; existing lore files load with an empty
owner list and behave exactly as before.
