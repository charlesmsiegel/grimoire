# Entity kinds & the typed entity model — design

Covers four issues from the **Worlds & Library Content** milestone, in dependency
order:

1. **#36** — First-class Item / Group / Creature entity kinds
2. **#37** — Typed, per-kind structured entity forms
3. **#39** — Guided world hub (Overview tab: per-kind counts, checklist, next actions)
4. **#47** — Campaign-scoped group state (goals / resources / focus / public perception / secrets)

## Decisions (settled during brainstorming)

- **Kind ids and labels**: `items` / "Items", `groups` / "Groups", `creatures` /
  "Creatures". Neutral names chosen deliberately: a "group" may be a kingdom, a
  guild, a family, or a friend group; a "creature" may be harmless. Ids are
  permanent once shipped.
- **Architecture**: extend the existing `ENTITY_KINDS` tuple (issue #36
  Option A) plus a small per-kind field-descriptor module for #37. No `KindSpec`
  registry — per-kind behavior is uniform after the decisions below, so a
  registry would be structure without a payload. Revisit if kinds diverge.
- **Activation**: all three new kinds behave exactly like lore in the context
  builder — keyless = always-on, keyed = whole-word match against recent text,
  owners gate by presence. The keyless-suppression special case remains
  locations-only.
- **Images**: every entity kind gets the image shelf and rail thumbnails,
  including lore and groups. Backend asset routes are already kind-generic;
  only the frontend gate changes.
- **Lore ownership**: the new kinds do **not** join the Owners picker in v1.
  Owners gate by scene presence, and nothing makes an item/group/creature
  "present" today. Deferred (GH issue).
- **Typed fields**: minimal, text-only, flavor-oriented. No ref-valued fields
  (no holders/leaders/headquarters) and no game mechanics in v1 — both deferred
  (GH issues). Fields do **not** enter the scene prompt; context uses the body
  only. A fact that belongs in the prompt belongs in the body.
- **#39 shape**: client-side Overview tab composing existing endpoints. The
  calendar `confirmed` checklist item is deferred (needs a backend route that
  does not exist yet).
- **#47 scope**: full playstate mirror including absorb write-back.

## Stage 1 — the three new kinds (#36)

### Backend

- `store/entities.py`: `ENTITY_KINDS = ("locations", "lore", "items", "groups",
  "creatures")`. This single change carries CRUD, the generic `/{kind}` routes,
  `SYNCED_KINDS` (campaign copy-on-create + world→campaign sync), `entity_counts`,
  `all_refs`/`synced_refs`, and lorebook-import category validation.
- `store/context.py:_world_info`: iterate all five kinds. New kinds get lore's
  activation semantics verbatim. Campaign reads stay on `overlay.list_entities`
  / `overlay.read_entity`, so copy-on-write campaigns work unchanged.
- No backfill: existing campaigns receive new-kind records through sync
  `incoming` as "new" (matches `ensure_campaign_copy`'s deliberate skip).
- Verify `items` / `groups` / `creatures` collide with no literal route segment
  declared before the generic `/{kind}` catch-alls in `routes.py` (world side:
  `tags`, `pcs`, `characters`, `greetings`, `calendar`, `lorebook`, `subjects`;
  campaign side: `scenes`, `incoming`, `chronicle`, `changes`, `appearances`,
  `export.epub`). Believed safe; re-check at implementation time.

### Frontend

- `api/client.ts`: extend the `EntityKind` union.
- `routes/WorldView.tsx`: `TABS` gains Items / Groups / Creatures (nine content
  tabs; ten with Overview in Stage 2 — let the strip wrap rather than redesign
  navigation here).
- `components/EntityEditor.tsx`: `label` becomes a kind → singular-label map
  ("location", "lore entry", "item", "group", "creature"). The image shelf and
  rail-thumbnail rendering lose their locations-only gate and apply to every
  kind.
- `components/LorebookImport.tsx`: category dropdown gains the new kinds.
- `routes/WorldsView.tsx:footerLabel`: counts for the new kinds.

## Stage 2a — typed per-kind fields (#37)

- New `store/entity_schema.py`: ordered map of kind → field specs
  `{key, label, widget}`. v1 widgets: `text` only. v1 field sets:
  - `items`: `item_type` ("weapon, heirloom, document…"), `rarity`
  - `groups`: `group_type` ("family, friend group, guild, nation…")
  - `creatures`: `creature_type`, `threat` ("harmless… apex predator")
  - `locations`, `lore`: none (unchanged)
- Storage: extra frontmatter string scalars in the same `.md`, so
  `entity_hash`, sync conflict detection, and campaign copy-on-write cover them
  with zero extra work.
- API: `EntityCreate` / `EntityUpdate` gain optional `fields: dict[str, str]`
  (plain pydantic field — stays v1/v2-agnostic per the Android rules), merged
  into frontmatter by `create_entity` / `update_entity`. Keys not declared in
  the descriptor for that kind → 400. Unrecognized pre-existing frontmatter is
  preserved on update.
- Frontend: mirror the descriptor as a typed constant in `client.ts`;
  `EntityEditor` renders form inputs from it and shows values as labeled chips
  in the detail sidebar (`.side-section`).

## Stage 2b — Overview tab (#39)

- New client-side "Overview" tab in `WorldView`, the default tab, composed from
  existing endpoints: `GET /worlds/{wid}` (counts), greetings list, tags,
  `subjects/untagged`.
- Per-kind count tiles that navigate to their tabs. New-kind counts fall out of
  `entity_counts`; the one backend addition is a **greetings count** in
  `read_world` / `list_worlds`.
- Derived setup checklist: has a PC · has ≥1 greeting · has ≥1 location ·
  plotmap has edges · no untagged greeting-image subjects · no characters
  missing taglines.
- Next-action buttons reuse the existing `setTab` / `openCharacter`-style
  handlers `WorldView` already uses for cross-links.

## Stage 3 — campaign-scoped group state (#47)

- New `store/groupstate.py` mirroring `playstate.py`: per-group
  `campaigns/<cid>/groups/<gid>/state.md` (sibling directory beside the flat
  `groups/<gid>.md`, same layout as `<kind>/<eid>/assets/`). Sections:
  `## Goals`, `## Resources`, `## Focus`, `## Public perception`, `## Secrets`,
  plus an `updated` frontmatter stamp. Same parse/compose semantics as
  playstate: a body whose first non-empty line is not a recognized header reads
  wholesale into the first section; keep-on-omit per section.
- **Context injection follows the group's own activation**: `_world_info` also
  reports *which* entries activated (refs alongside bodies); `_assemble` reads
  state for each activated group. Always-on group ⇒ state always in context;
  keyed group ⇒ state only when the group is in play. New
  `templates/scene/sections/group_state.j2` + a "Group state" `_SECTIONS` entry
  for the token inspector. Garbled `state.md` omits the block, never crashes
  (mirror `_character_states`).
- **Absorb write-back**: `group_state_edits` parallel to
  `character_state_edits` — schema in `templates/absorb/system.j2`, handling in
  `parse_output` / `materialize` / `apply_edits`, keep-on-omit per section,
  surfaced through the existing StagedEdit review flow.
- API: `GET/PUT /campaigns/{cid}/groups/{gid}/state` (deeper than the generic
  `/{kind}/{eid}`; no collision).
- UI: campaign-scoped group detail view gains a sidebar state panel showing the
  five sections with an edit affordance, mirroring how NPC play-state is
  surfaced (pin down the exact component at plan time). World-scoped groups
  show no state panel; state is campaign-only.

## Testing

- **Backend** (pytest, `GRIMOIRE_HOME` isolated via `monkeypatch.setenv`):
  - Parameterize `test_entities.py` across all five kinds.
  - Sync test: a new-kind record flows world → campaign as "new".
  - `entity_schema`: unknown field key → 400; unrecognized frontmatter
    preserved on update; fields round-trip through create/read/update.
  - `groupstate`: parse/compose round-trips mirroring the playstate tests.
  - Context: an activated group injects body + state; a keyed, inactive group
    injects neither.
  - Absorb: `group_state_edits` parse/materialize/apply, keep-on-omit.
- **Frontend** (`npx vitest run` + `npx tsc -b`, run from `frontend/`):
  - List/detail-pattern tests for one new kind per the root CLAUDE.md contract
    (row → read-only view, Edit reveals form, + New opens form).
  - Typed-field rendering from the descriptor (form inputs + sidebar chips).
  - Overview tab: tiles navigate; checklist derives correctly from fixtures.
- Fixtures use the codebase's existing placeholder names only (Seraphine, Mara,
  Winifred, Realm, Saltmarch).

## Deferred (filed as GitHub issues)

1. Items/groups/creatures as lore owners + the presence semantics that would
   make owner-gating meaningful for them.
2. Rich game-mechanical per-kind fields (hooks into the Mechanics & Dice data
   contract).
3. Ref-valued fields + ref-picker widget (item holder, group leader,
   headquarters, creature habitat).
4. Calendar `confirmed` flag surfaced to the world Overview checklist (needs a
   backend route).
