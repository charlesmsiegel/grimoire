# Structured entity forms — all remaining kinds + list-card token badges

> Sub-project 2 of 4 for issue #441. Depends on **SP1**
> (`2026-05-28-world-creation-structured-forms-design.md`), which builds the
> field-descriptor system, `<EntityForm>`, the widget set, the client tokenizer,
> and the schema-drift route/test. SP2 reuses all of that unchanged.

## Problem

After SP1, only **characters** have a structured editor. Locations, items,
monsters, factions, and lore still drop into the generic untyped
`FrontmatterEditor`, even though each has a typed backend model
(`types/world.py`). Token estimates exist only in the editor header (SP1); the
entity **list cards** give no sense of an entity's context cost.

## Goal (SP2)

- A field descriptor for every remaining kind, wired through the existing
  `<EntityForm>` with the same "Advanced / raw fields" fallback.
- A token badge on each entity **list card** (all kinds, including characters).

## Non-goals (SP2)

- Rich creation forms / auto-id (SP3) and the guided hub (SP4).
- Greetings: already have a bespoke structured form (`GreetingFormFields.tsx`);
  left as-is. A descriptor for greetings is out of scope.
- Faction/location campaign-local *state* (goals, current focus) — library card
  only.

## Design

### 1. Descriptors (`entitySchemas.ts`)

Add one `EntityDescriptor` per kind, mirroring `types/world.py`. Fields not listed
fall through to Advanced. Shared fields (`aliases`→tags, `tags`→tags,
`description`→textarea, `body` in its own panel) follow the SP1 character pattern.

**Location** (`Location`):
| Section | Fields |
|---|---|
| Identity | `name`→text, `id`→text(read-only), `kind`→enum(`city`/`building`/`room`/`region`/`outdoor`/`other`), `parent_id`→ref(locations), `aliases`→tags, `tags`→tags |
| Geography | `climate_zone`→text, `indoor`→bool, `coordinates`→object(`x`→number,`y`→number) |
| Detail | `permanent_features`→stringList, `typical_occupants`→stringList, `description`→textarea |
| Connections | `connections`→objectList(`to`→ref(locations), `via`→text, `duration_min`→number, `notes`→text) |

**Item** (`Item`):
| Section | Fields |
|---|---|
| Identity | `name`, `id`(ro), `aliases`→tags, `tags`→tags |
| Detail | `provenance`→text, `current_holder`→ref(characters), `description`→textarea |

**Monster** (`Monster`):
| Section | Fields |
|---|---|
| Identity | `name`, `id`(ro), `category`→enum(beast/undead/dragon/fey/demon/aberration/humanoid/construct/elemental/other), `aliases`→tags, `tags`→tags |
| Detail | `threat_level`→text, `habitat`→stringList, `abilities`→stringList, `weaknesses`→stringList, `description`→textarea |

**Faction** (`Faction`):
| Section | Fields |
|---|---|
| Identity | `name`, `id`(ro), `kind`→text, `tags`→tags |
| Detail | `base_location`→ref(locations), `description`→textarea |
| Membership | `leaders`→refList(characters), `members`→refList(characters), `allies`→refList(factions), `rivals`→refList(factions) |

**Lore** (`LoreEntry`) — note the primary label key is **`title`**, not `name`
(`library/service.py:980,1024` maps `name`↔`title`; `name` is a legacy alias):
| Section | Fields |
|---|---|
| Identity | `title`→text, `id`(ro), `tags`→tags, `keywords`→tags, `secrecy`→enum(public/common-knowledge/common-knowledge-among-kindred/restricted/secret) |
| Relations | `related_locations`→refList(locations), `related_factions`→refList(factions), `related_characters`→refList(characters) |
| Activation (lorebook) | `keys`(=`keywords`), `secondary_keys`→tags, `selective_logic`→enum(and_any/and_all/not_any/not_all), `constant`→bool, `enabled`→bool, `priority`→number, `probability`→number, `position`→enum(before_cast/after_cast/at_depth/archive), `at_depth`→number, `scan_depth`→number, `case_sensitive`→bool, `match_whole_words`→bool, `comment`→textarea |

The lore "Activation" section is collapsed by default (advanced lorebook tuning).
`import_source` is read-only metadata → Advanced.

`EntityEditorView.tsx` already routes a descriptor when one exists (SP1); SP2 just
populates the registry so all kinds resolve a descriptor instead of the generic
editor. Greetings keep their existing bespoke form.

### 2. Section collapse support

SP2 adds an optional `collapsed?: boolean` to `EntitySectionDescriptor` and
renders collapsed sections inside `<details>` (used by lore's Activation and
location's Connections when empty). Small additive change to `<EntityForm>`.

### 3. List-card token badges

`EntityListView.tsx` (`EntityListBody`) renders each entity card from data that
already includes `frontmatter` + `body` (`LibraryEntity`). Compute
`estimateTokens(serializeFrontmatter(fm) + "\n" + body)` per card (SP1 helper) and
render `<TokenBadge>` in `library-card-meta`. Greetings expose `body` too, so they
get a badge from their body text.

Because the tokenizer is lazy-loaded and encoding many cards is cheap once loaded,
compute lazily/memoized; before the encoder resolves, the `len/4` fallback renders
and is replaced on the next paint.

### 4. Schema-drift test extension

Extend the SP1 drift test: each new descriptor's managed keys ⊆ its model's
`model_json_schema().properties` (via `GET /library/entity-schemas/{kind}`). The
lore `keys`↔`keywords` alias is asserted explicitly so the mapping is intentional,
not silent.

## Components / files

Changed (frontend):
- `entitySchemas.ts` — add location/item/monster/faction/lore descriptors; add
  `collapsed` to section type.
- `EntityForm.tsx` — honor `collapsed` sections.
- `EntityListView.tsx` — token badge per card.
- drift test — cover all descriptors.

No backend changes (the `entity-schemas` route from SP1 already serves every
kind's model).

## Testing

- **Component**: each kind's descriptor round-trips a representative frontmatter
  dict (known→widgets, unknown→Advanced); lore renders `title` as the label field
  and round-trips a card with only `title` set; location `connections` and faction
  `members` objectList/refList add-remove.
- **List cards**: a card shows a token badge; a long-body entity shows a larger
  estimate than a short one.
- **Drift**: all descriptors' keys ⊆ their schema properties; lore alias asserted.
- **Regression**: an entity (any kind) with arbitrary extra frontmatter keys keeps
  them under Advanced and preserves them on save.

## Risks / trade-offs

- **Lore title/name duality**: the descriptor writes `title`; the backend keeps
  `name` in sync. Tested explicitly so the two don't drift.
- **Card render cost**: encoding every card adds work to list render. Mitigated by
  memoization and the lazy encoder; if a world has thousands of entities and this
  shows up in the `library:render` perf budget, fall back to `len/4` for cards and
  keep the exact estimate for the editor (noted, not implemented unless measured).
