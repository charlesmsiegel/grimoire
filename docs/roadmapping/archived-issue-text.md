# Source issue archive — deferred roadmap goals

**Historical reference, not a current implementation specification.**
**Captured:** 2026-10-15 from the GitHub issue bodies.
**Why archived:** Nineteen distinct issues were assessed for long-term `roadmapping` across two waves. **Twelve trackers were retired in the first wave**; seven additional discretionary trackers (#81, #93, #132, #188, #151, #190, #196) were retired in the second. Four other independent issues remain open for near-term implementation. The original discussions and alternatives remain available here. The original discussions and alternatives remain linked, and this preserves their full original issue bodies.

Many original issue bodies were written before later refactors and describe then-current code incorrectly today. Their **suggested approaches are not approved decisions**, and dated assertions about features absent from the backend should not be repeated as current facts. For updated, implementation-neutral requirements, see [inventory.md](inventory.md), [deferred-systems.md](deferred-systems.md), [content-architecture-study.md](content-architecture-study.md), and related thematic documents.

**Disposition:** All 19 archived issue trackers were retired as standalone backlog entries, **NOT implemented**. Most goals remain active long-term; #151 and #190 are explicitly optional and may be declined after further product review. #93 and #151 were briefly reopened during the first-wave prerequisite review, then selected for deferral in the second wave. No archived requirement is counted as delivered. See [backlog-discretionary.md](backlog-discretionary.md), [inventory.md](inventory.md), and [deferred-systems.md](deferred-systems.md).

---

## [#167 — Per-campaign inventory subsystem, toggleable](https://github.com/charlesmsiegel/grimoire/issues/167)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Inventory** milestone. From the pre-rebuild target feature list:

> Per-campaign toggleable inventory subsystem (who holds what)

## Current state

Inventory does not exist anywhere — zero hits for "inventory" or item records in `backend/src`, `frontend/src`, or `templates/`. The natural pattern to follow is the campaign-scoped continuity ledgers: `backend/src/grimoire/store/relationships.py` (`relationships.json`, actor tokens `"<kind>:<id>"`, tolerant `read()` with defaults), `store/plot.py` (`plot.json`, typed `set_movement`, `render_open(cid, with_id)` shared by prompt snapshot and context block), and `store/chronicle.py` (`chronicle.json` + `timeline.md`).

Holders already exist: cast actors come from `store/appearances.py` (`ACTOR_KINDS=("characters","pcs")`, `scene_cast(cid, sid)`, `roster(cid)`). There is no per-campaign settings mechanism to hang a toggle on — `store/config.py` (`config.md`) is global; `campaign.md` frontmatter (written by `store/campaigns.py:create_campaign`) carries only `{name, world, created, updated, world_copy}`.

## Suggested approaches

### Option A — `inventory.json` ledger + campaign-frontmatter toggle (recommended)
- New `store/inventory.py` mirroring `plot.py`: `campaigns/<cid>/inventory.json`, keyed by holder token `"<kind>:<id>"` (plus a reserved `"party"` pool), values = list of entries (schema in #168).
- Toggle = an `inventory` key in `campaign.md` frontmatter (`"on"`/absent), read via `campaigns.read_campaign`; add a small setter in `store/campaigns.py` and expose it on the existing campaign `PUT`.
- Routes in `routes.py`: `GET /api/campaigns/{cid}/inventory` (whole ledger or `?holder=`), ops via #169. Declare literal paths before the generic `/{kind}` entity catch-alls.
- Context: a `templates/scene/sections/inventory.j2` block (present cast's holdings), added to `templates/scene/system.j2` and the `_SECTIONS` list in `store/context.py` so `GET .../context` shows its token cost; render empty when toggled off.
- Recommended because every continuity axis (plot, relationships, chronicle) already works exactly this way.

### Option B — per-holder markdown beside the actor copy
- `characters/<id>/inventory.md` next to `state.md` (`store/playstate.py` pattern), parsed sections per `playstate._parse_body`.
- Human-editable, but scattered across folders, no holder for a shared party pool, and whole-campaign views require a directory sweep.

### Option C — fold into playstate (`## Carries` section in `state.md`)
- Near-zero new surface, but unstructured (no quantity/equipped), PCs have no `state.md`, and it can't support typed ops (#169).

## Implementation notes

- Tolerant reads: garbled `inventory.json` must omit, never crash (`plot.render_open` shows the convention).
- Gate everything on the toggle: context section, absorb extraction keys (#169), and routes should degrade quietly when off.
- If entries carry scene provenance, add `inventory.repoint_scenes` and register the module in `store/scene_refs.py:repoint` (currently exactly four stores: appearances, chronicle, changes, plot).
- Tests: `backend/tests/test_inventory.py` with `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`; frontend vitest run from `frontend/` + `npx tsc -b`.
- JSON convention: `json.dumps(data, indent=2, sort_keys=True) + "\n"` (see `relationships._write`).

## Dependencies & related

- #168 — entry schema (discrete vs fungible, quantity/equipped/provenance).
- #169 — typed ops that mutate this ledger.
- #171 — the CampaignView surface reading it.
- #73 — per-campaign settings tabs would be the natural home for the toggle later.
- #36 — first-class Item entity kind (library-side item definitions).

---

## [#168 — Items and fungible resources with quantity, equipped state and provenance](https://github.com/charlesmsiegel/grimoire/issues/168)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Inventory** milestone. From the pre-rebuild target feature list:

> Discrete items + fungible resources with quantity / equipped / provenance

## Current state

There are no item records of any kind. `backend/src/grimoire/store/entities.py` defines `ENTITY_KINDS = ("locations", "lore")` (generic `<kind>/<eid>.md` with frontmatter `{name, keys, owners}`) and `SYNCED_KINDS = ENTITY_KINDS + ("greetings",)`; #36 tracks adding an Item entity kind at library scope. Campaign-scoped mutable state (who holds it, how many, equipped) must live in campaign files, following the invariant used by `store/playstate.py`, `store/relationships.py`, and `store/plot.py`: campaign play-state never edits library records.

Provenance has a working precedent: plot beats store `{"scene": sid, "text": ...}` (`plot.set_movement`), and scene ids are renamed (title renames, date stamps — `store/scene_ids.py`), so any persisted scene ref needs a `repoint_scenes` hook registered in `store/scene_refs.py:repoint`.

## Suggested approaches

### Option A — library item definitions + campaign holdings ledger (recommended)
- Library side (via #36): `items` added to `ENTITY_KINDS`, so worlds get `items/<eid>.md` records that campaigns deep-copy and sync for free — `campaigns.create_campaign` copies every ref from `entities.synced_refs(wroot)` (which walks `SYNCED_KINDS`) and records base hashes in `sync.md`; add `"items"` to `SYNCED_KINDS`.
- Campaign side (`inventory.json`, #167): entries keyed by holder token, each `{item: "<eid>"|null, name, kind: "discrete"|"fungible", qty, unit, equipped, provenance: [{scene, op, note}]}`.
- Discrete items reference a library/campaign item record (or are emergent, `item: null` — #170); fungible resources (coins, rations, arrows) are name+qty+unit rows, usually with no item record at all.
- `equipped` is only meaningful for discrete entries; validate `qty >= 0` on fungibles.
- Recommended: it separates identity (shareable, syncable, has images via the existing entity image routes) from possession (campaign-local), matching the world/campaign split everywhere else.

### Option B — fully campaign-local (no library kind)
- Entries carry their own `name`/`description`; no dependency on #36.
- Simplest to ship, but items can't be authored in a world, reused across campaigns, or synced; a later migration to Option A would need an identity-matching pass.

### Option C — items as campaign `lore` entities + a refs/qty ledger
- Reuses entity CRUD, images, and the `context.activate` keyword surfacing untouched.
- Pollutes lore, and lore has no quantity/equipped semantics; reclassification (#119) would eventually be needed anyway.

## Implementation notes

- Adding an entity kind touches: `entities.ENTITY_KINDS`/`SYNCED_KINDS`, sync hashing (`sync._entity_blob` diffs each synced ref), and the generic entity routes in `routes.py` (`GET/POST/PUT/DELETE .../{kind}`), which already accept any kind in `ENTITY_KINDS`. Existing campaigns backfill through `campaigns.ensure_campaign_copy`.
- Provenance `scene` fields require `inventory.repoint_scenes(cid, mapping)` wired into `scene_refs.repoint` (currently exactly four stores: appearances, chronicle, changes, plot).
- JSON files: `indent=2, sort_keys=True` + trailing newline (see `relationships._write`); tolerant reads returning defaults.
- Backend tests: `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`; cover discrete vs fungible rows, equipped flips, provenance append, repoint.

## Dependencies & related

- #167 — the ledger file and toggle this schema lives in.
- #169 — ops are the only writers of qty/equipped/provenance.
- #170 — emergent (`item: null`) entries and identity matching.
- #36 — library Item entity kind (Option A's other half).
- #119 — lore reclassification, relevant if Option C is ever chosen.

---

## [#169 — Typed inventory operations: acquire, drop, transfer, consume, adjust, equip](https://github.com/charlesmsiegel/grimoire/issues/169)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Inventory** milestone. From the pre-rebuild target feature list:

> Typed inventory ops (acquire/drop/transfer/consume/adjust/equip)

## Current state

No inventory exists (#167/#168 define the ledger). The canonical write pattern for campaign state is typed setter functions dispatched per StagedEdit kind: `backend/src/grimoire/store/absorb.py:apply_edits` calls `plot.set_movement`, `relationships.set_feeling`/`set_bond`, `playstate.write_state` etc., best-effort per edit, and never runs without an explicit user confirmation (`PUT /api/campaigns/{cid}/scenes/{sid}/chronicle` in `routes.py`, after the review checklist in `frontend/src/routes/CampaignView.tsx`).

The extraction side is equally settled: `POST .../absorb` → `absorb.build_prompt` (snapshots via `state_snapshot`/`relationships_snapshot`/`plot_snapshot`, prompt text in `templates/absorb/system.j2` + `user.j2`) → `absorb.parse_output` (tolerant JSON) → `absorb.materialize` (StagedEdits `{id, kind, target, label, field, before, after, authored, payload?}` — writes nothing) → review → `apply_edits`. Inventory ops must join this flow, never silent writes.

## Suggested approaches

### Option A — typed op functions + embedded op log (recommended)
- `store/inventory.py:apply_op(cid, op)` with `op = {"type": acquire|drop|transfer|consume|adjust|equip|unequip, "holder", "to"?, "item"?, "name", "qty"?, "scene", "note"}`.
- `inventory.json` holds derived `holdings` plus an append-only `ops` list (provenance and #171's review feed); `apply_op` validates (transfer needs both holders; consume/adjust require a fungible row; drop of an unheld item fails → flag, #170) then updates both.
- Extraction: add an `"inventory_ops"` key to the absorb JSON contract in `templates/absorb/system.j2`, an inventory snapshot in `user.j2` (mirror `plot_snapshot`), parse in `absorb.parse_output`, stage in `materialize` as `kind: "inventory"` edits carrying the op as `payload` (like relationship/bond/plot edits), apply in `apply_edits`.
- Manual ops: `POST /api/campaigns/{cid}/inventory/ops` in `routes.py` for direct UI edits (#171), same `apply_op` path.
- Recommended: identical shape to plot/relationships, plus the op history the milestone's other issues need.

### Option B — direct-mutation setters only (no op log)
- `set_holding(cid, holder, name, qty, equipped)` in the style of `relationships.set_feeling`; ops become client-side sugar.
- Least code, but no provenance trail, no flagged-op review feed, and transfer atomicity is left to callers.

### Option C — pure event sourcing
- Persist only `ops`; fold them into holdings on every read.
- Perfect history and trivially reversible, but every read re-folds, conflicts surface late, and hand-fixing a bad state means editing history rather than a snapshot.

## Implementation notes

- Follow `plot.set_movement`'s tolerance: unknown thread → create; unknown status → default. Ops should clamp/validate rather than raise where sensible; hard failures return a flagged result instead of a 500.
- `apply_edits` re-guards client-supplied payloads (see the `_CARD_FIELDS` re-check for `authored` edits) — re-validate op types server-side on `PUT .../chronicle`.
- Gate on the campaign toggle (#167): extraction keys and the ops route are no-ops when inventory is off.
- Ops carry `scene` ids → wire `repoint_scenes` into `store/scene_refs.py:repoint`.
- Tests: `backend/tests/test_inventory.py` (`monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`) covering each op type, validation failures, and materialize→apply round-trip; template changes must keep `scripts/verify_templates.py` green.

## Dependencies & related

- #167 — ledger + toggle (blocking).
- #168 — entry schema the ops mutate (blocking).
- #170 — resolving which item an extracted op refers to; conflict flagging.
- #171 — HUD + flagged-ops review consumes the op log.
- #110 — confidence-based routing of extracted proposals, if built, applies to these ops too.

---

## [#170 — Resolve an item to known, fungible or emergent, and reconcile conflicts](https://github.com/charlesmsiegel/grimoire/issues/170)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Inventory** milestone. From the pre-rebuild target feature list:

> Item-identity resolution (known / fungible / emergent) + reconcile conflicts

## Current state

No inventory or item identity exists (#167/#168). The extractor's current id-resolution behavior in `backend/src/grimoire/store/absorb.py:materialize` is instructive: unknown targets are **dropped silently** (`_actor_exists`, `_entity_kind` — "targets that don't exist are dropped (tolerated, not an error)"), with one exception — plot movements may create a **new** thread when the model omits `id` but supplies a `title` (`pid = slugify(title)`, colliding titles fold into the existing thread). That emergent-creation path, plus `uniquify(base, exists)` in `store/paths.py`, is the machinery this issue generalizes to items.

The prompt side already teaches the model to use known ids: `absorb.plot_snapshot` renders threads *with ids* (`plot.render_open(cid, with_id=True)`, line formats in `templates/snippets/plot_thread_line/absorb.j2`) so the model references existing threads instead of inventing duplicates. An inventory snapshot in `templates/absorb/user.j2` should do the same.

There is no conflict/contradiction handling anywhere in the pipeline today — edits are approve/reject checkboxes in the CampaignView review panel, all defaulting approved (`editRows` in `frontend/src/routes/CampaignView.tsx`).

## Suggested approaches

### Option A — resolution inside `materialize` + flagged StagedEdits (recommended)
- Extend the absorb contract (#169): the model emits `item` (a known id from the snapshot) when it can, else a free-text `name`.
- Resolution order in `materialize`: exact id match → `slugify(name)` match against existing holdings/item records → known fungible-resource names (coins, rations) → otherwise **emergent**: mint an id with `slugify` + `uniquify` and stage a create.
- Conflicts (drop/consume of something the holder lacks, qty going negative, transfer from the wrong holder, duplicate acquisition of a held discrete item) are not dropped: stage the edit with a `flagged: "<reason>"` field and `approved: false` default in the review UI (#171).
- Recommended: keeps the whole decision visible in the existing review checklist, no extra LLM calls, and mirrors how plot already handles emergent ids.

### Option B — optimistic apply + separate reconcile view
- Apply all ops; a `GET /api/campaigns/{cid}/inventory/conflicts` endpoint lists inconsistencies (negative qty, orphaned equips) for later cleanup.
- Simple materialize, but the ledger can be wrong between absorbs, and the review moment — where the user still remembers the scene — is wasted.

### Option C — second-pass LLM disambiguation
- Ambiguous mentions get one extra `OpenRouterClient.complete()` call with the holder's current holdings, asking "same item or new?" (new template folder, e.g. `templates/inventory_resolve/`).
- Best matching quality (handles "the rusty key" ≈ "old iron key"), but adds cost/latency to every absorb and another parse to keep tolerant.

## Implementation notes

- Never resolve against or edit library files — resolution targets the campaign copy only (same invariant as #113).
- Follow keep-on-omit discipline (`parse_output`'s knows/suspects handling): distinguish "field absent" from "explicit empty" wherever it matters.
- Emergent items should record provenance (`scene`, first op) so #60-style later promotion is possible.
- One staged edit per item per scene (see `seen_pids` dedup in the plot loop of `materialize`) to avoid double-apply.
- Tests: resolution table-driven in `backend/tests/test_inventory.py`; cover id hit, name-slug hit, emergent mint, each conflict class producing a flagged edit.

## Dependencies & related

- #168 — entry schema (`item: null` = emergent) (blocking).
- #169 — the ops being resolved (blocking).
- #171 — flagged edits need the review surface.
- #111 — contradiction detection with keep/replace/merge is the generalized version of this reconcile step.
- #48 / #60 / #98 — emergent locations/characters follow the same mint-and-review pattern.

---

## [#171 — Inventory HUD widget and flagged-operation review](https://github.com/charlesmsiegel/grimoire/issues/171)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Inventory** milestone. From the pre-rebuild target feature list:

> Inventory HUD widget + flagged-ops review

## Current state

**⚠️ Pre-rebuild concept — needs re-scoping.** There is no HUD: the "always-visible scene HUD" with configurable widgets is itself an open pre-rebuild idea (#195/#196). The current play surface is `frontend/src/routes/CampaignView.tsx` with side panels — `components/CastPanel.tsx` (seat cast, set location/date, opener) and `components/SceneInspector.tsx` (cast portraits, setting, datetime, context token breakdown, recap, `RecordDrawer`). "Inventory HUD widget" should be re-scoped to a CampaignView panel/section.

The review half maps cleanly onto existing machinery: the absorb review panel in `CampaignView.tsx` renders `editRows: (StagedEdit & { approved: boolean })[]` as a checkbox checklist (all default-approved) and confirms via `api.saveChronicle` → `PUT /api/campaigns/{cid}/scenes/{sid}/chronicle`. `components/ChangesPanel.tsx` (over `GET /api/campaigns/{cid}/changes`, `store/changes.py:line_diff`) is the precedent for after-the-fact per-record diffs. The `StagedEdit` type lives in `frontend/src/api/client.ts`.

Nothing inventory-shaped exists on either side; this issue is the frontend for #167–#170.

## Suggested approaches

### Option A — SceneInspector section + absorb-review integration (recommended)
- Add an "Inventory" block to `SceneInspector.tsx`: fetch `GET /api/campaigns/{cid}/inventory` (new typed method in `api/client.ts`), filter holders to the scene cast (it already loads cast + a `names` map), render per-holder holdings as chips/rows; hide entirely when the campaign toggle (#167) is off.
- Flagged ops (#170) arrive as `kind: "inventory"` StagedEdits in the existing absorb checklist; render entries with a `flagged` reason using a warning badge and `approved: false` default (today every row defaults approved — this is the one behavioral change to the review panel).
- Manual corrections post via `POST .../inventory/ops` (#169) from a small inline form (an `EditableRow.tsx`-style affordance).
- Recommended: smallest new surface, and the review half reuses the canonical StagedEdits flow rather than inventing a second review UI.

### Option B — dedicated InventoryPanel (list/detail editor pattern)
- New `components/InventoryPanel.tsx` following the root `CLAUDE.md` list/detail pattern (`.editor-list` rail of holders, `.editor-body` detail with holdings + op history from the op log, `mode: "view"|"edit"`).
- Room for provenance/op history that Option A lacks; costs a whole new panel plus a place to mount it in CampaignView.

### Option C — RecordDrawer extension
- Extend `components/RecordDrawer.tsx` with an actor-inventory `DrawerTarget` so clicking a cast member shows their holdings.
- Minimal, but per-actor only — no scene-wide "who holds what" overview and nowhere to show party-pool resources.

## Implementation notes

- Frontend conventions: typed methods in `api/client.ts` (types in the same file, e.g. next to `StagedEdit`), vitest tests co-located (`SceneInspector.test.tsx` shows the mocking style); run `npx vitest run` **from `frontend/`** and `npx tsc -b`.
- Backend counterpart routes come from #167/#169; declare literal `inventory` paths before the generic `/{kind}` entity catch-alls in `routes.py`.
- The absorb checklist change (default-unapproved flagged rows) should be covered by a CampaignView test around `editRows` initialization.
- Refresh the inventory block after `PUT .../chronicle` succeeds (the panel already refreshes recap/changes on absorb confirm via `refreshKey`).

## Dependencies & related

- #167 / #168 / #169 / #170 — the entire backend this UI reads (blocking).
- #195 / #196 — if a real HUD shell ever lands, this panel becomes one of its widgets.
- #110 — confidence-based routing would generalize the flagged/default-unapproved behavior.

---

## [#50 — Character variants across worlds, selected per campaign](https://github.com/charlesmsiegel/grimoire/issues/50)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Worlds & Library Content** milestone. From the pre-rebuild target feature list:

> Character variants (cross-world overlays) with per-campaign selection

## Current state
**⚠️ Pre-rebuild concept — needs re-scoping.** The feature assumes a global character library with per-world overlays; in the rebuilt architecture characters are strictly world-scoped (`worlds/<wid>/characters/<cid>/`, `backend/src/grimoire/store/characters.py`) and a campaign is a copy of exactly one world — no cross-world references exist anywhere.

Half of the feature already exists *within* a world: characters have **versions** (one V3 card JSON per `<vid>.json`, `create_version`/`update_version`/`set_default_version`, per-version `grimoire_label` display override in `_version_label`), and **per-campaign selection** is done — `store/appearances.py` locks a version per campaign (`pick_version`/`locked_version`), `POST /campaigns/{cid}/{kind}/{aid}/pick-version` and `.../import-version` in `routes.py`. What does not exist is any way to reuse a character across worlds short of re-importing the card.

## Suggested approaches
### Option A — Copy-character-to-world with lineage (recommended)
- New route `POST /worlds/{wid}/characters/{cid}/copy-to/{target_wid}`: deep-copy the character folder (all version cards, `assets/`, `tagline.md`) with `shutil.copytree`, id via `paths.uniquify` on collision.
- Record lineage in `character.md` frontmatter (e.g. `origin: <wid>/<cid>`); a "Variants in other worlds" side-section in `CharacterEditor.tsx` lists same-origin characters across worlds.
- Rewrite localized asset URLs during copy: card/greeting text embeds absolute `/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}` URLs (`store/localize.py`), which break under a new wid/cid.
- Recommended: delivers the user value (same character in multiple worlds, diverging freely) without breaking the world-scoped store or the 3-hash sync model.

### Option B — Versions-as-variants convention
- Keep one "hub" world per character; model each world-flavor as a version (`create_version`) labeled via `grimoire_label`; campaigns already pick per-campaign.
- Zero code; but campaigns can only be created from that one world, so it's a workaround, not the feature.

### Option C — True shared library with overlays
- A home-level `library/characters/` plus per-world overlay files.
- Breaks the core invariant that a container root (world or campaign dir) holds everything (`entities.py`, `campaigns.create_campaign`, `sync.py` all assume it); a rearchitecture — not recommended.

## Implementation notes
- Copy must include per-version assets (`store/assets.py` layout `characters/<cid>/assets/<vid>/`, avatar + `focus.json`) and `tagline.md` (`store/taglines.py`).
- `chub_source` lives per-version inside card `extensions` — it survives copy verbatim, which is fine.
- Lineage is informational only in v1: no live sync between variant copies (that would need a new cross-world diff engine; sync.py is world→campaign only).
- Tests: backend `test_characters.py`-style with `GRIMOIRE_HOME` tmp_path — copy then assert cards/assets/tagline present and ids unique; frontend `CharacterEditor.test.tsx`.

## Dependencies & related
- #41 — world fork (deep copy of a whole world; same mechanics at larger scope).
- #64 — character search across worlds (surfacing variants needs it).
- #60 — emergent campaign characters + promote-to-library (the campaign→world direction).

---

## [#42 — Spatial queries over locations: adjacency, path-between, within-depth](https://github.com/charlesmsiegel/grimoire/issues/42)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Worlds & Library Content** milestone. From the pre-rebuild target feature list:

> Spatial queries (adjacency, path-between, within-depth)

## Current state
**⚠️ Pre-rebuild concept — needs re-scoping.** The feature assumes locations form a graph; in the current store they are flat markdown entities with no coordinates, connections, or any spatial data.

Locations are generic entities (`store/entities.py`, frontmatter `{name, keys, owners}` + body). The only spatial notion anywhere is per-scene `location_history` (`store/scenes.py:get_location_history` / `set_location`), whose last id becomes the "Current setting" section in prompts (`store/context.py:_assemble` → `templates/scene/sections/current_setting.j2`). There is no edge storage, no graph module, and no query endpoint. A close structural precedent exists: greetings form a DAG in `plotmap.json` (`store/greetings.py:read_plotmap` / `set_edges` / `predecessors_of`), with dedicated sync handling (`sync.py:_plotmap_incoming`).

## Suggested approaches
### Option A — `connections` frontmatter on locations (recommended)
- Add a comma-list frontmatter field `connections` (location ids, same convention as `keys`/`owners`), edited via `EntityUpdate`; treat edges as symmetric at query time so authors only write one side.
- New pure module `store/geography.py`: `adjacency(root)` builds the graph from `entities.list_entities(root, "locations")`; `neighbors(root, eid)`, `path_between(root, a, b)` (BFS), `within_depth(root, eid, n)`.
- Routes: `GET /worlds/{wid}/geography` + `/campaigns/{cid}/geography` returning the adjacency map (query helpers can run client-side), declared **before** the generic `/{kind}` catch-alls in `routes.py`.
- Recommended: edges live in the entity file, so `entities.entity_hash` + `sync.py` diff/conflict machinery and campaign copy-on-create cover them with zero sync work.

### Option B — `map.json` sidecar (plotmap-style)
- `worlds/<wid>/map.json` `{eid: {connects_to: [...]}}` with `read_map`/`set_edges` mirroring `store/greetings.py` plotmap functions.
- Cleaner separation and one-file graph reads, but requires copy-on-create wiring in `campaigns.create_campaign` and a `sync.py` special ref like `_plotmap_incoming` — real extra work.

### Option C — coordinates + distance
- Store `{x, y}` per location for metric queries instead of (or besides) topology. Only worth it as part of #43's map view; adjacency answers the actual play questions (what's nearby, how do you get there).

## Implementation notes
- Dangling connection ids (deleted locations) must be tolerated — skip, never crash, matching the `EntityNotFound` handling in `context._assemble`.
- Consumers to wire once queries exist: context could include `within_depth(current_loc, 1)` neighbor names as travel options in the setting section (template change in `templates/scene/sections/current_setting.j2` + `context._SECTIONS` awareness), and `store/suggest.py` snapshots could mention adjacent locations for next-scene ideas — keep both as follow-ups.
- Campaign copies may diverge from the world graph (campaign entity edits are normal); queries always run against the scoped root passed in, never reach across scopes.
- Tests: new `backend/tests/test_geography.py` with a small graph under `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`; frontend typing via `npx tsc -b` if the adjacency endpoint is typed in `api/client.ts`.

## Dependencies & related
- #43 — the map UI renders exactly this graph; land the data model here first.
- #48 — emergent-location generation should offer connecting the new location to the current one.
- #104 — weather-across-locations on time advance wants `within_depth`.

---

## [#43 — Map view: locations with coordinates and connections](https://github.com/charlesmsiegel/grimoire/issues/43)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Worlds & Library Content** milestone. From the pre-rebuild target feature list:

> Map / spatial view UI (locations with coordinates + connections)

## Current state
**⚠️ Pre-rebuild concept — needs re-scoping.** Locations carry no coordinates and no connections in the current store, so there is nothing to draw yet; the data model must come from #42.

What exists to build on: locations are entities with a primary image ("avatar") and gallery in `<root>/locations/<eid>/assets/default` (`store/assets.py`), served with cache-friendly `?v=`/`?w=` params (`routes.py:_serve_image`, `store/thumbs.py`) — `components/EntityEditor.tsx` already renders row thumbnails and an image shelf. The Locations tab in `routes/WorldView.tsx` is the natural mount point. The frontend has **no charting/canvas dependency** (React + react-markdown only, per `frontend/package.json`), so any map rendering should be hand-rolled SVG.

## Suggested approaches
### Option A — SVG graph view (recommended)
- New `components/LocationMap.tsx` rendering nodes (location name + avatar thumbnail via `api.entityImageUrl`) and edges from #42's adjacency endpoint as plain SVG; no new dependencies.
- Layout: store optional `x`/`y` frontmatter per location (drag-to-place writes back via `api.updateEntity`); nodes without coordinates fall back to a simple deterministic auto-layout (circle or grid — skip force simulation).
- Mount as a "Map" sub-view of the Locations tab (or a toolbar toggle in `EntityEditor` when `kind === "locations"`); clicking a node opens that location's read-only detail (`select(id)` in `EntityEditor.tsx`).
- Recommended: works at both scopes (world + campaign copy) through the existing `EntityScope` plumbing in `api/client.ts`, and degrades gracefully for unplaced locations.

### Option B — image-backed map with pins
- Upload a hand-drawn map as a world asset; place locations by clicking, storing normalized `x/y`; render pins over the image.
- Strong for tabletop-style worlds, but needs a home for the map image (a new world-level asset — `store/assets.py` is entity/version-keyed, so this means a small routes + storage addition) and gives no value before an image exists.

### Option C — no canvas: connections in the detail sidebar
- Render a "Connections" `.side-section` of chip buttons in `EntityEditor`'s detail sidebar (the CLAUDE.md pattern: refs are clickable chips) navigating between locations, plus a "nearby" list from `within_depth`.
- Minimal and pattern-conformant; worth shipping as step 1 even if A follows.

## Implementation notes
- Blocked by #42 (edges); coordinates can land with this issue as extra frontmatter (string scalars — serialize numbers as strings per `store/frontmatter.py` convention).
- Campaign scope reads the campaign's copied locations — a campaign map may legitimately differ from the world map; never reach across scopes.
- Keep the map read-mostly: edits (add/remove edge, move node) go through the normal `PUT .../{kind}/{eid}` entity update so sync/conflict handling stays uniform.
- Tests: `LocationMap.test.tsx` with mocked `api` (vitest run from `frontend/`), assert node click navigation and that unplaced nodes still render; `npx tsc -b`.

## Dependencies & related
- #42 — provides connections (and optionally coordinates); this issue is its UI.
- #48 — emergent locations should appear on the map immediately after review.
- #195 — the scene HUD's location widget could deep-link into this view.

---

## [#55 — Library activity feed: recent edits and the campaigns they affect](https://github.com/charlesmsiegel/grimoire/issues/55)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Worlds & Library Content** milestone. From the pre-rebuild target feature list:

> Library activity feed (recent edits across the library + the campaigns they affect)

## Current state
There is no library-wide activity log. The available signals are partial: `world.md` has an `updated` frontmatter stamp, but it is bumped **only by `worlds.rename_world`** (`backend/src/grimoire/store/worlds.py`) — entity/character/greeting edits never touch it; entity frontmatter has no `updated` field at all (`store/entities.py` writes only `name`/`keys`/`owners`). Campaign side is better: `campaigns.touch` bumps `campaign.md`, and `changes.json` (`store/changes.py`) records absorb write-back deltas.

For the "campaigns they affect" half, the machinery exists: `sync.campaigns_for_world(wid)` (`store/sync.py`) already computes per-campaign pending new/update/conflict counts, and per-ref "is campaign X behind on record Y" is exactly `sync.md` base ≠ current world hash (`entities.entity_hash`, memoized by `store/statcache.py`).

## Suggested approaches
### Option A — Append-only activity log (recommended)
- New `store/activity.py`: `log(wid, kind, eid, action)` appending one JSON line (`{ts, kind, id, action}`) to `worlds/<wid>/activity.jsonl`; call it from the world-scoped write paths in `routes.py` (entity create/update/delete, character/PC version writes, greeting writes, `edges`, tags, calendar).
- `GET /worlds/{wid}/activity?limit=N`: read the tail, enrich each row with the record's current name and — per ref — the campaigns still behind on it (manifest base ≠ world hash, reusing the `sync.incoming` comparison).
- Precise actions (created/edited/deleted) going forward; blind to edits made before the feature or by external tools.

### Option B — Derived from file mtimes
- Sweep `entities.synced_refs(wroot)` + character/PC dirs, sort by `st_mtime` (via `statcache.signature`), return the most recent N.
- Zero write-path changes and it catches **external edits** — relevant because the store is designed to live in synced folders (`store/paths.py` home resolution). But: no create-vs-edit distinction, deletes invisible, and mtimes lie after folder copies/restores.

### Option C — Hybrid
- Option B's mtime sweep as the feed source, plus Option A's sync-base comparison for the affected-campaigns column; optionally add the jsonl later for action fidelity.
- Best coverage-to-effort ratio if external edits matter more than precise verbs.

## Implementation notes
- Do **not** retro-stamp `updated` into entity frontmatter as part of this: rewriting every file changes its hash and would flood every campaign's sync queue with spurious updates. Stamping on *future* real edits is fine (content changed anyway).
- Keep the endpoint cheap: hashes are already stat-memoized (`statcache.memo`), and `campaigns.read_manifest` is one frontmatter read per campaign.
- Deletions can't be attributed after the fact — only Option A sees them; note `sync.incoming` deliberately skips world-side deletions, so a deleted record affects no campaign in the sync sense.
- Frontend: an activity panel on `WorldsView.tsx` (library-wide, merging per-world feeds) or per-world on `WorldView.tsx`; rows link to the record's editor. Tests both sides per repo convention (pytest with `GRIMOIRE_HOME` tmp_path; vitest run from `frontend/`).

## Dependencies & related
- #35 — file watcher reindexing external edits (would feed/replace Option B).
- a general-purpose SQLite-derived index/cache substrate (not re-filed as its own issue) — SQLite-derived index (natural home for a durable activity table later).
- #31 — reversible change log (per-write deltas; activity feed is its lightweight cousin).
- the sync-engine parent tracking issue (not re-filed as its own issue) — sync engine (source of the "campaigns affected" computation).

---

## [#93 — Copy a scene in from another campaign](https://github.com/charlesmsiegel/grimoire/issues/93)

**Second-wave disposition: standalone issue deferred (or subject to reassessment for #151); original issue text follows unchanged:**

Part of the **Scenes & Play** milestone. From the pre-rebuild target feature list:

> Copy / import a scene from another existing campaign into the current one

## Current state

Nothing exists for cross-campaign scene copy; every existing cross-campaign mechanism copies *library content into a fresh campaign*, not a scene between two live campaigns.

A scene is not self-contained: `store/scenes.py`'s file is just the transcript + frontmatter, but a scene also has cast/continuity state scattered across sibling stores, all keyed by scene id:
- **Cast**: `appearances.json` keyed `"<kind>/<id>"`, each record's `scenes[]` list contains the scene ids it appeared in (`appearances.scene_cast(cid, sid)` reads the reverse view). Actors are version-**locked** on first appearance (`appearances._lock`/`appear`) — a locked actor's other versions are purged from the campaign.
- **Ids**: a scene's id is its filename stem, in the `<number>--<date-slug>--<title-slug>` grammar (`store/scene_ids.py:format_sid`/`parse_sid`); `_numbering(cid)` derives the next number from files already on disk in the *target* campaign, so a copied scene needs a fresh id assigned in that numbering, not the source's.
- **Repointing**: exactly four stores hold scene ids (`store/scene_refs.py:repoint` fans out to `appearances`, `chronicle`, `changes`, `plot`) — this machinery exists for *renaming* a scene id within one campaign, not for copying a scene's associated records into a different campaign's stores.
- **Actor copy**: `appearances._copy_actor(wroot, croot, kind, actor_id, vid)` only copies from a **world** root into a campaign root (used when an actor first appears) — there is no campaign→campaign actor copy path today. If the source and destination campaigns don't share every cast member (different worlds, or the same world but different actors already used/locked), the destination campaign may not have the character/PC files at all.

So a naive copy of the `.md` file, if the destination doesn't share cast ids or an id collision occurs, silently produces a scene with unreadable cast or the wrong content once ids collide.

## Suggested approaches

### Option A — copy transcript + re-seat cast from names (recommended)
- New `store/scenes.py` (or a new `store/scene_copy.py`) function `copy_scene(src_cid, src_sid, dst_cid) -> str`: read the source scene (`scenes.read_scene`), mint a fresh id in the destination via the existing `_numbering`/`create_scene` path, then replay the transcript with `append_message` per turn (never copy the raw file — this sidesteps id collisions and lets the target's own frontmatter/model default apply).
- Cast: for each source actor (`appearances.scene_cast(src_cid, src_sid)`), resolve the *same actor id* in the destination campaign if it already exists there (same world lineage); if the destination has never seen that actor, copy it from the shared world root the way `_copy_actor` does today (requires both campaigns to share a `world` — validate and reject/flag cross-world copies, since a cross-world actor has no source of truth to copy from). Seat via the normal `appearances.appear(dst_cid, new_sid, kind, id, version, role)`.
- Route: `POST /campaigns/{cid}/scenes/import-from-campaign` body `{source_campaign, source_scene}`; declare before the generic `/campaigns/{cid}/{kind}` catch-all. Frontend: a picker (campaign dropdown limited to campaigns on the same world + scene dropdown) in `NewSceneChooser.tsx` or a dedicated small dialog.
- Tradeoff: cross-world copies are rejected rather than attempted — recommended because attempting to fabricate an actor from nothing when the destination has no source of truth for it is worse than refusing.

### Option B — full deep copy including continuity records
- Beyond A, also copy the slice of `chronicle.json`/`plot.json`/`relationships.json` entries that reference the source scene (mirroring how `scene_refs.repoint` already knows which four stores key on scene id) so the destination campaign's continuity ledger reflects the imported scene as if absorbed there.
- More faithful, but risks duplicating facts that don't belong in the destination's narrative (a chronicle entry authored against the *source* campaign's cast/state may not make sense once transplanted) — needs an explicit decision on which continuity axes travel with the scene versus staying scene-transcript-only.

### Option C — re-run absorb instead of copying continuity state
- Copy only the transcript + cast (Option A), then let the user explicitly run `POST .../absorb` on the new scene in the destination campaign to (re-)derive chronicle/state/relationships/plot fresh, exactly as the `ingest-campaign-log` skill does for external logs.
- Costs one extra LLM call and requires a configured key, but avoids Option B's "does this fact even apply here" ambiguity entirely, and reuses a pipeline that's already correct and tested. Pairs naturally with A.

## Implementation notes
- The destination scene must get a **new** id from the destination's own numbering (`scenes._numbering`) — never reuse the source sid, which could collide or misorder the destination's lexicographic play order.
- Version-locking matters: if an actor is already locked in the destination at a *different* version than the source scene used, `appearances.appear` raises `AppearError` — decide (and document) whether the import keeps the destination's lock (likely correct) or fails loudly; don't silently relock.
- Images/localized assets referenced in the transcript (`/api/worlds|campaigns/.../images/...` URLs, per `localize.py`) point at the source campaign's/world's paths — for cross-*world* actors this is moot (rejected in Option A), but cross-campaign-same-world copies should still verify image URLs resolve in the destination.
- Backend tests: `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`, cover same-world copy (cast resolves), missing-actor-in-destination (falls back to world copy), cross-world rejection, and id/numbering correctness in the destination. Frontend: vitest from `frontend/`, plus `npx tsc -b`.

## Dependencies & related
- #92 — sibling import feature (external chat log); a shared review-and-commit UI shape could serve both.
- #41 — world fork (deep copy) is the analogous whole-world operation; this is the single-scene granularity.
- #72 — campaign fork already needs to reason about copying every scene at once; this issue's cast/id logic is that operation's single-scene building block.

---

## [#102 — Tick NPC and group state forward over a time skip](https://github.com/charlesmsiegel/grimoire/issues/102)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Time & Calendars** milestone. From the pre-rebuild target feature list:

> NPC / group ticks on time skips

## Current state

**⚠️ Pre-rebuild concept — needs re-scoping.** This assumes an off-screen simulation engine (per-entity tick handlers, group agendas, an event bus); the rebuilt codebase has no background simulation, no scheduler, and no group record kind (groups exist only as freeform `lore` entities — first-class groups are #36/#47).

What the current architecture offers as hooks: per-NPC campaign play-state (`backend/src/grimoire/store/playstate.py` — `state.md` with `## Current state / ## Knows / ## Suspects`), per-NPC dossiers (`store/dossiers.py`, refreshed during `/absorb`), plot threads (`store/plot.py`), and offscreen play (pcless scenes + `context.build_director_messages`). The canonical write path for LLM-proposed state changes is the absorb pipeline: `absorb.build_prompt` → `parse_output` → `materialize` (StagedEdits, writes nothing) → user review → `absorb.apply_edits` (`store/absorb.py`). There is no time-skip operation yet (#100).

The honest re-scope: on a large advance, generate a reviewable "what happened meanwhile" pass over the off-screen cast and open threads — never silent writes.

## Suggested approaches

### Option A — Interim tick through the StagedEdits review flow (recommended)
- Hook the #100 advance pipeline: when the skip exceeds a threshold, offer an "interim" LLM pass.
- Build a prompt from the same snapshots absorb uses (`absorb.state_snapshot`, `relationships_snapshot`, `plot_snapshot` in `store/absorb.py`) plus the deterministic digest (elapsed days, holidays/events crossed); new template folder `templates/interim/` (`system.j2`/`user.j2`, per `templates/README.md` — never hard-code LLM text).
- One `OpenRouterClient.complete()` in the route (mirrors `routes.post_absorb`), parse into StagedEdits limited to kinds `apply_edits` already handles: `character_state`, `plot`, `relationship`.
- Reuse the existing absorb review checklist in `frontend/src/routes/CampaignView.tsx`; applied edits land in `changes.json` via the `_BROWSABLE_KINDS` capture.
- Recommended: zero new write machinery — the whole continuity pipeline already exists; only the prompt and trigger are new.
- Tradeoff: one flat LLM pass, not per-NPC simulation; quality depends on snapshot completeness.

### Option B — Auto-seeded offscreen scene per skip
- On advance, create a pcless scene (`scenes.create_scene(..., pcless=True)`) dated at the skip's end, seeded with a director note describing the gap (`context.build_director_messages`); the user plays/steers it and absorbs it normally.
- Fully auditable as transcript; heavier per skip, and multiplies scenes for routine downtime.

### Option C — Deterministic staleness report only (no LLM)
- The advance digest lists NPCs not in recent chronicle cast (`suggest._recent_char_ids`) and threads with old `last_scene`, prompting the user to run Option B manually for whoever matters.
- Cheapest and fully predictable; does nothing by itself.

## Implementation notes

- OpenRouter is the only LLM path (`openrouter.py`); model/key from `store/config.py:read_config()`.
- Roster and roles come from `store/appearances.py:roster(cid)` (`ACTOR_KINDS=("characters","pcs")`); campaign-local state must never edit world/library files — `character_state` edits write `campaigns/<cid>/characters/<id>/state.md` only.
- Group-level goals/resources have no store yet — do not invent one here; keep group ticks blocked on #47.
- Tests: prompt/parse pure-function tests plus an `apply_edits` round-trip under `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`; `scripts/verify_templates.py` must pass for new templates.

## Dependencies & related

- #100 — blocking: the tick fires from the advance pipeline's digest/threshold.
- #47 / #36 — campaign group state / group entity kind; true group ticks wait on these.
- #83 — director / PC-absent scenes (implemented; Option B's vehicle).
- #103, #115 — commitment aging is the deterministic sibling of this pass.

---

## [#108 — Advance to the end of a long activity and resolve it](https://github.com/charlesmsiegel/grimoire/issues/108)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Time & Calendars** milestone. From the pre-rebuild target feature list:

> Activity-based advancement ("advance to the end of a long activity and resolve it")

## Current state

No activity concept or duration-estimation exists. #100 (posted, open) defines the base advance-by-duration/skip-to primitive this issue builds on — `POST /campaigns/{cid}/advance` with `{to?, days?, reason}` and a deterministic digest (elapsed days, holidays/birthdays crossed) — still unbuilt.

Two existing pipelines are the templates to compose from. The ephemeral-generation pattern: `context.build_opener_messages(cid, sid, prompt)` (`store/context.py`) builds a full-context system prompt plus the caller's prompt as a user turn, streamed via an ephemeral SSE call (`routes.py`'s `_ephemeral_stream`) that persists nothing until explicitly adopted (`POST .../first-post`) — the model for "ask the LLM something, show it, let the user accept." The extraction/review pattern: `absorb.py`'s `build_prompt`/`parse_output`/`materialize`/`apply_edits` — the model for "LLM proposes edits, user reviews, then writes land" via `character_state`/`lore`/`authored`/`relationship`/`bond`/`plot` StagedEdits.

## Suggested approaches

### Option A — estimate, advance, resolve as three composed steps (recommended)
- New ephemeral LLM call (`templates/activity_duration/`, `OpenRouterClient.complete()`) turns free text ("a week of travel and training") into an estimated duration/target date, mirroring the opener's non-persistent pattern.
- Feed that estimate into #100's advance (`{days, reason: <activity text>}`).
- Run a "resolve" pass building a prompt from `absorb.state_snapshot`/`relationships_snapshot`/`plot_snapshot` plus the activity text and the advance digest (same shape as #102's proposed interim-tick), producing StagedEdits through the existing review checklist rather than writing anything directly.
- Recommended: composes three already-designed pieces (ephemeral estimate, advance, absorb-shaped resolution) instead of inventing new persistence or a new StagedEdit kind.

### Option B — single combined LLM call
- One new template/route does duration-estimation and narrative resolution together, returning `{days, edits}` in one completion.
- Fewer round-trips and one review step, but duration-inference and narrative resolution are different skills for one prompt to carry, and there's no intermediate point to show/confirm the estimated duration before committing to it.

### Option C — manual duration, LLM resolve only
- Skip the duration-estimation call; the user picks a duration/date via #100's UI (built on the existing `CalendarDatePicker`), and only the "resolve what happened" step is LLM-driven.
- Cheaper (one LLM call instead of two) and simpler, but drops the "figure out how long this activity takes" half of the original feature line — defers that judgment to the user every time.

## Implementation notes
- Hard-blocked on #100 (no advance primitive to target yet); shares its resolve-through-review shape with #102's "interim tick" proposal — implement one, reuse for the other rather than duplicating the pipeline.
- Any new LLM call needs a template under `templates/` (`prompts.render`, Jinja2 `StrictUndefined`) per `templates/README.md` — never hard-code prompt text in `routes.py`.
- Resolution edits should go through the existing `absorb.materialize`/`apply_edits` StagedEdit kinds rather than inventing a new one, unless a genuinely new edit shape turns out to be necessary.
- Tests: prompt/parse pure-function tests plus `scripts/verify_templates.py` for new templates; pytest for the advance+resolve round trip with `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.

## Dependencies & related
- #100 — blocking: the advance-by-duration primitive this operates through.
- #102 — same "interim tick" resolve-through-review shape; share the design once either lands.
- #83 — director/PC-absent scenes are the alternative (played rather than resolved) way to cover activity time.

---

## [#151 — Replay a turn through the gateway, with fork and seed](https://github.com/charlesmsiegel/grimoire/issues/151)

**Second-wave disposition: standalone issue deferred (or subject to reassessment for #151); original issue text follows unchanged:**

Part of the **Observability, Cost & Metrics** milestone. From the pre-rebuild target feature list:

> Turn replay through the gateway (with fork + seed)

## Current state
**⚠️ Pre-rebuild concept — needs re-scoping.** There is no gateway (OpenRouter via `backend/src/grimoire/openrouter.py` is the sole LLM path) and no seed support: `_payload()` sends only `{model, messages, stream}`, and even with a `seed` field the OpenRouter chat API cannot guarantee deterministic replay across upstream providers.

What exists nearby: `POST /campaigns/{cid}/scenes/{sid}/retry` rebuilds the *current* composition and re-streams; `regenerate` (`routes.py:post_regenerate`) drops the trailing assistant run and re-streams, optionally with `scene/regenerate_guidance.j2` guidance. Both mutate/replace the scene — neither is a side-effect-free replay of a *historical* turn, and nothing stores what was actually sent (see #150). `_ephemeral_stream` (used by the opener route, `routes.py:post_opener`) is the existing persist-nothing streaming primitive.

## Suggested approaches
### Option A — ephemeral replay of an audited turn (recommended)
- Requires #150 with full-prompt capture (`audit_prompts: full`).
- `POST /campaigns/{cid}/audit/{entry_id}/replay` (optional body: `{model?}`): load the stored messages verbatim, stream through `_ephemeral_stream` — persists nothing.
- Frontend: side-by-side stored response vs replayed response; diff with `store/changes.py:line_diff` (server-side, reusing ChangesPanel's tagged-op rendering).
- Honest framing: this is an A/B re-roll of the exact historical prompt, not a bit-identical reproduction.
- Recommended because it needs no new infrastructure beyond #150 and delivers the real debugging value (does the model still answer this way? what does another model do?).

### Option B — "replay" as re-run against current state (no #150)
- Reuse `context.build_messages` like `retry`, but stream ephemerally and never call `_persist_reply`.
- Cheap, but composition has drifted (chronicle, state, cast locks) since the original turn — it answers a different question.

### Option C — best-effort seed pass-through (add-on to A)
- Add optional `seed` to `_payload` (OpenAI-compatible; OpenRouter forwards it to providers that support it) and record it in the audit entry.
- Must be labeled best-effort: most providers do not honor it deterministically.

### Fork
"Fork" here should defer to #72 (campaign fork + lineage): adopting a replayed alternate belongs in a forked campaign rather than overwriting history. A minimal hook: replay result panel offers "fork campaign from this scene and adopt" once #72 exists.

## Implementation notes
- Keep replay strictly read-only on the store: no `scenes.append_message`, no `changes.record`.
- Model override in replay must not touch `config.md` (`store/config.py:write_config`) — pass it through to `client.stream()` directly.
- Tests: fake `OpenRouterClient` via the `get_openrouter` dependency override; assert the scene file is byte-identical after replay.
- Frontend consumes SSE via `frontend/src/api/stream.ts:streamPost`, same as chat/opener.

## Dependencies & related
- #150 — blocking: stored per-turn prompts are the replay input.
- #72 — campaign fork; the "fork" half of this feature lives there.
- #77 — regenerate with a different model shares the model-override plumbing.
- #130 — diffing a composition against a prior turn is the non-LLM sibling of replay.

---

## [#140 — Export filters: strip OOC, anonymize, consolidate POV — plus export history](https://github.com/charlesmsiegel/grimoire/issues/140)

**Transferred to roadmap; original issue text follows unchanged:**

Part of the **Import / Export** milestone. From the pre-rebuild target feature list:

> Export filters (strip OOC/mechanics, anonymize, POV consolidation) + history

## Current state

**⚠️ Pre-rebuild concept — needs re-scoping.** Two of the three named filters assume markup that doesn't exist in this codebase: there is no OOC/mechanics marker convention anywhere (grep across `backend/src/grimoire` for OOC/mechanics annotations finds nothing) — a scene's transcript is plain `**Speaker:** content` blocks (`store/scenes.py`), with narration and dialogue indistinguishable at the markup level. "Strip OOC/mechanics" therefore has nothing to key off unless a marker convention is invented first (see Option B).

`store/epub.py:build_epub(cid)` is the only export path today (`GET /campaigns/{cid}/export.epub`) — one chapter per scene in filename order, `_message_html(speaker, content)` per message, images resolved and packed, an appendix of roster actors + visited locations. It writes nothing back to the store and takes no options; there is no export-history record anywhere (no `exports.json` or similar). #139 tracks adding markdown/HTML/txt/JSON sibling formats via a proposed shared `store/export.py:collect(cid)` — this issue's filters should sit on top of whatever that collector produces, not duplicate scene-walking logic.

What *is* real and usable for the "anonymize" half: `appearances.roster(cid)` gives every campaign actor's `(kind, id, name, role)`, and scenes already carry per-message `speaker` names (the same names `_message_html` labels chapters with) — a name-substitution pass over already-collected chapter/appendix text is mechanically straightforward. "POV consolidation" has no real anchor at all yet — scenes have no POV field (see #86, itself not yet built); consolidating "by POV" presupposes that field exists.

## Suggested approaches

### Option A — anonymize only, shipped now (recommended first slice)
- A post-processing pass over the shared collector's output (once #139 lands) or directly over `epub.py`'s per-chapter/appendix text today: build a `{real_name: "Character A"/"Player 1"/...}` map from `appearances.roster(cid)` (stable per-campaign numbering by first-appearance order), and substitute names in speaker labels and prose (reusing `context._substitute`'s function-replacement approach to avoid backslash/regex-group pitfalls).
- New query param, e.g. `GET /campaigns/{cid}/export.epub?anonymize=true`, threaded into `build_epub` (and each sibling renderer once #139 exists).
- Ship this alone first: it's real, well-anchored functionality (unlike OOC/mechanics stripping, which has nothing to strip) and delivers the "share this with people who shouldn't see real names" use case immediately.

### Option B — invent an OOC/mechanics marker convention, then filter on it
- Define a lightweight in-transcript convention (e.g. a `((double-parens))` OOC marker, or a `*/mechanics/*` fenced block) that authors opt into during play; `store/scenes.py` or the export collector strips matched spans before rendering.
- Real value if adopted, but it's authoring a *new* markup convention from scratch with no existing precedent in this app (unlike SillyTavern-style `{{macro}}` handling, which at least has prior art here) — high risk of solving a problem nobody's transcripts actually have yet. Ship only after confirming real transcripts contain something worth stripping.

### Option C — defer POV consolidation entirely; option it out
- Do not attempt "POV consolidation" until #86 (per-scene POV field) exists — there's no data to consolidate by. Once #86 lands, a future export filter could group/relabel chapters by their `pov` frontmatter value; today, explicitly scope this issue to exclude it.

### Export-history record (applies to whichever filters ship)
- Add `store/exports.py` over a small `campaigns/<cid>/exports.json` log: `{eid: {format, filters: {...}, created}}`, written after each successful `build_epub`/sibling-renderer call; `GET /campaigns/{cid}/exports` surfaces it in `CampaignView` (e.g. "last exported 3 days ago, anonymized"). This is independent of which content filters ship and gives "+ history" concrete meaning.

## Implementation notes
- Filters must not mutate the store — exports are already in-memory-only (`build_epub` "writes nothing into the store"); a filter is a transform on the collected/rendered text, applied per-request.
- If #139's shared collector lands first, implement filters once against `store/export.py:collect(cid)`'s structured output rather than duplicating per-format text-substitution logic across epub/markdown/html/txt/json renderers.
- Name substitution must cover both message `speaker` labels and any prose mentions of a character's name in `content` (the same ambiguity `scenes.match_name` already handles for role attribution) — decide explicitly whether prose mentions get substituted too (harder, riskier false-positives) or only speaker labels (safer, partial anonymity).
- Backend tests: `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`, alongside `backend/tests/test_epub_store.py`; cover anonymized output containing no real actor names in speaker labels, and export-history recording one entry per call.

## Dependencies & related
- #139 — campaign export formats; this issue's filters are meant to apply across whichever formats land there, not just EPUB.
- #86 — per-scene POV field; a hard prerequisite for any real "POV consolidation," which is out of scope here until it exists.
- #178 — cover-image auto-generation is a separate export enhancement that could share the same history record.

---



## Second wave: five additional issue bodies (2026-10-15)

The earlier archive already contains the full original bodies of #93 and #151. The five other second-wave issue bodies appear below without abridgment. Their active/discretionary status is in [backlog-discretionary.md](backlog-discretionary.md).


---

## [#81 — Multi-PC turn loop: combine pending PC posts into one advance](https://github.com/charlesmsiegel/grimoire/issues/81)

**Source body captured before second-wave tracking consolidation (not current design):**

Part of the **Scenes & Play** milestone. From the pre-rebuild target feature list:

> Multi-PC turn loop ("Advance" to combine pending PC posts)

## Current state

Scenes already support multiple simultaneous player characters at the data layer: `appearances.players_in_scene(cid, scene_id)` (`store/appearances.py`) returns every cast member with `role == "player"` in a scene, and can return more than one. But the *turn-submission* path only handles the single-player case: `routes.py:post_chat` does `names = store.appearances.player_names(cid, sid); speaker = names[0] if len(names) == 1 else None` — with more than one player in scene, `speaker` is `None` and `scenes.stamp_user_speaker` (which backfills the *sole* player's name onto speakerless messages, `store/scenes.py`) is skipped entirely, so a submitted post has no attributed speaker unless the caller already labeled it. There is no buffer for "pending, not-yet-submitted posts from multiple PCs" anywhere — `post_chat` always immediately appends the user message and calls the LLM in the same request; there's no notion of collecting N posts first and then triggering one combined NPC turn.

## Suggested approaches

### Option A — Client-side pending buffer + an explicit "Advance" call (recommended)
- Keep the wire format unchanged: `ChatTurn{content}` already accepts arbitrary content, and posts can already carry an explicit `**Speaker:**` marker (`_MARKER` in `scenes.py`) — the frontend can compose one combined turn as `**Alice:** ...\n\n**Bob:** ...` and call `post_chat` once. `split_reply`/`_parse_messages`'s marker grammar already round-trips multiple same-role blocks correctly (it's exactly how model replies get split per speaker, #82) — user-authored multi-speaker turns are unhandled by name resolution but the marker parser itself is symmetric.
- Frontend: a per-scene local (unsaved) queue of `{speaker, content}` entries in `CampaignView.tsx`, one input row per player in scene (`players_in_scene` cast data already fetched for the cast panel), plus an "Advance" button that concatenates them into the marker format above and sends one `post_chat`.
- Backend: no new endpoint required for the minimal version — `append_message(cid, sid, "user", content, speaker=name)` already supports a name argument per call, so a small backend loop appending each pending post before building context is also viable if the frontend prefers N small requests over one composed string.

### Option B — Server-side pending-post buffer (persisted)
- Add a `pending.json`-style sidecar holding not-yet-turned-in posts per scene, with its own submit/clear endpoints, so a multi-PC party can each submit independently (e.g. from different devices/sessions) before anyone triggers "Advance." More correct for genuinely asynchronous multi-player use, but a materially bigger addition (new store module + routes + repointing on scene rename) for a single-user-driven app today. Only justified if real concurrent multi-user play is a near-term goal.

## Implementation notes

- `scenes.stamp_user_speaker` is explicitly single-player only (docstring: "Backfill: give every speakerless user message the (sole) player's name") — do not extend it; multi-PC turns should always submit pre-labeled content instead.
- Test in `backend/tests/test_scene_store.py` / `test_routes.py`: multiple players in scene, combined turn round-trips through `_parse_messages` with correct per-speaker roles.

## Dependencies & related

- #82 — the marker-parsing/splitting machinery (`split_reply`) this option reuses is the same code the per-character-posts feature already relies on.
- #96 — cast suggestions determine who's in scene, which determines how many pending-post rows to show.


---

## [#132 — Live prompt preview while typing, debounced](https://github.com/charlesmsiegel/grimoire/issues/132)

**Source body captured before second-wave tracking consolidation (not current design):**

Part of the **Context Building & Inspector** milestone. From the pre-rebuild target feature list:

> Live debounced preview while drafting input

## Current state
`SceneInspector` fetches `/context` only inside a `useEffect` keyed on `[cid, sid, refreshKey, ...]` (`frontend/src/components/SceneInspector.tsx:63-71`) — on mount and whenever the parent's `ctxKey` counter increments. `CampaignView.tsx` only bumps `ctxKey` on scene selection (`selectScene`, line 98) and after a turn is sent/absorbed (line 242 and its neighboring effect) — never on keystrokes. The compose box's in-progress text lives entirely in `CampaignView`'s own `input` state (`CampaignView.tsx:39, 546`) and is never passed to `SceneInspector` or threaded into `context.build_messages`/`context_sections` — the preview always reflects only persisted scene messages, never the draft.

Backend-side, `context.activate()`'s scan window (`recent_text`, built in `_assemble`, `context.py:313-321`) is derived purely from the last `context_scan_depth` **persisted** messages (`history[-depth:]`). There is already a precedented seam for folding extra unpersisted text into that window: `build_opener_messages` passes `wi_seed=prompt` into `_assemble` (`context.py:128-142, 319-320`) specifically so an opener prompt (which has no history yet) still activates world-info. Threading a live draft through the same parameter is a natural reuse, not new plumbing.

No debounce hook exists in `frontend/src` — the only two `setTimeout` uses (`ModelCombobox.tsx:60`, a blur-close delay; `ConfigView.tsx:56`, a "saved" flag reset) are simple UI delays, not input debouncing.

## Suggested approaches
### Option A — thread the draft through `wi_seed`, debounce the fetch (recommended)
- Give `context_sections` an optional `draft: str = ""` param that flows into `_assemble(cid, sid, wi_seed=draft)` — already supported, so a draft's keywords activate world-info exactly like the opener prompt does.
- Add a `draft` query/body param to the `/context` call (prefer POST over a GET query param to avoid URL-length limits on a full draft), pass `CampaignView`'s `input` down as a new `SceneInspector` prop, and debounce the re-fetch (~400-600ms after the last keystroke).
- Reuses `_assemble`'s existing seeding mechanism; no new backend selection logic.

### Option B — debounce only, ignore draft content
- Re-fetch `/context` as-is (current persisted state) on a debounce timer while the compose box has focus, so the token bar stays fresh without reflecting the draft's own text.
- Cheaper to build, but doesn't preview what the draft would actually activate — the more valuable half of the feature.

### Option C — client-side token estimate, no round trip
- Estimate the draft's token contribution locally (mirroring `context.count_tokens`'s own fallback, `context.py:439-445`, `len(text)//4`) and add it to the last-fetched total.
- Zero network cost per keystroke, but can't reflect draft-triggered world-info activation (needs `activate()` server-side) — best layered on top of A/B as a between-fetch estimate, not a replacement.

## Implementation notes
- `context_sections`/`_assemble` already accept `wi_seed`; verify reusing it for a live (non-opener) draft doesn't trip the `opener=True` rendering branch in `_system_text` (`context.py:362-364`) — this call path should keep `opener=False`.
- No debounce utility exists yet; add a small `useDebouncedValue`-style hook or hand-roll `useEffect` + `setTimeout`/`clearTimeout`.
- Never persist the draft — preview-only, matching the opener path's "ephemeral, caller does not persist" convention (`context.py:133-134`).
- Tests: `backend/tests/test_context.py` (draft text activates a keyed world-info entry via the new param, alongside existing `wi_seed`/opener tests); frontend `SceneInspector.test.tsx` with fake timers asserting the debounced re-fetch fires once after typing stops.

## Dependencies & related
- #29 — the context-builder tracking epic.
- #126 — once the budget packer exists, a live preview should also reflect drop-under-pressure decisions, not just raw token counts.
- #130 — a live preview naturally wants to distinguish "current draft" from "last sent turn," which is what #130's diff view provides.


---

## [#188 — Auxiliary task kinds: impersonate, polish, continue-as, "what would X say", brainstorm, translate](https://github.com/charlesmsiegel/grimoire/issues/188)

**Source body captured before second-wave tracking consolidation (not current design):**

Part of the **Auxiliary Generation** milestone. From the pre-rebuild target feature list:

> Task kinds: impersonate PC, rewrite/polish, continue-as-character, "what would X say", brainstorm, translate

## Current state

No task-kind dispatch exists yet (that's #187). But every kind here can be built from context-builder pieces that already exist and are verified:

- **continue-as-character** — `store/context.py:build_director_messages(cid, sid, note)` already assembles full system + history + a final user-turn note for one NPC-perspective continuation in a pcless scene; a per-character variant just needs to seed the same assembly with one specific NPC's card (`characters.read_card`) + campaign playstate (`store/playstate.py:read_state`) instead of the general cast.
- **impersonate PC** — `templates/scene/_persona_blocks.j2` (referenced by `system.j2`'s `players` section, per `templates/README.md`) already renders a seated PC's persona (`kind:"pcs", name, pronouns, summary, description"` from `store/pcs.py:read_persona`). A task kind for "write as this PC" reuses that exact block as its subject.
- **"what would X say"** — same NPC-card + playstate context as continue-as-character, but framed as a question/prediction rather than a scene continuation; differs only in the instruction template, not the context subset.
- **rewrite/polish, brainstorm, translate** — these take an existing post's text (or the composer draft) as their primary input rather than scene context; they need the target message body (`store/scenes.py:read_scene(cid,sid)["messages"][i]`) but not necessarily the full cast/world-info assembly `_assemble()` builds — a lighter-weight context path than the other three kinds.

## Suggested approaches

### Option A — one `templates/aux/<kind>/` folder per kind, thin context builders (recommended)
- Per `templates/README.md` convention ("one folder per LLM call"), add `templates/aux/impersonate_pc/`, `.../continue_as_character/`, `.../what_would_say/`, `.../rewrite/`, `.../brainstorm/`, `.../translate/`, each with `system.j2`/`user.j2`.
- Split kinds into two context tiers: "full scene" kinds (impersonate PC, continue-as-character, what-would-X-say) call a variant of `_assemble()`/`build_director_messages()`-style assembly seeded with the target actor; "text transform" kinds (rewrite, brainstorm, translate) need only the target text + minimal framing, no cast/world-info walk.
- Tradeoff: six template folders to author and keep in sync with `scripts/verify_templates.py`, but each is small and the split avoids over-fetching full scene context for a one-line polish request.

### Option B — single generic template with a `kind` selector variable
- One `templates/aux/system.j2` with `{% if kind == "impersonate_pc" %}...{% endif %}` branches, mirroring how `scene/system.j2` already branches on `opener`/`pcless` selectors.
- Fewer files, but conflates six genuinely different prompts into one template, working against `templates/README.md`'s "one file per message part" convention and making any single kind harder to review/edit in isolation. Not recommended given how differently the "full scene" vs "text transform" kinds consume context.

## Implementation notes

- `_persona_blocks.j2` and `_world_info`/`_cast_directory_data` in `context.py` are reusable building blocks — reuse them rather than re-deriving persona/NPC text.
- Run `backend/.venv/Scripts/python.exe scripts/verify_templates.py` after adding any new template folder — it validates every template renders against a throwaway store.
- Translate/brainstorm/rewrite kinds should NOT require an in-scene target (they may run against arbitrary text), so their context builder must tolerate no scene/campaign at all — check this doesn't implicitly require `_require_scene`.

## Dependencies & related

- #187 — the dispatch framework these kinds plug into.
- #189 — these kinds must not trigger absorb/state-write side effects.
- #190 — where each kind's output lands once accepted.


---

## [#190 — Accept or discard an auxiliary result into the right canonical path](https://github.com/charlesmsiegel/grimoire/issues/190)

**Source body captured before second-wave tracking consolidation (not current design):**

Part of the **Auxiliary Generation** milestone. From the pre-rebuild target feature list:

> Accept/discard dispatch routing output to the correct canonical path

## Current state

The exact pattern this issue asks to generalize already exists for one case: the opener. `POST .../opener` streams ephemeral text (`routes.py:_ephemeral_stream`, persists nothing); accepting it is a separate explicit call, `POST .../first-post` (`routes.py:post_first_post`), which persists via `_persist_reply(cid, sid, body.text)` — the same helper `_chat_stream` uses for ordinary turns, splitting the text into per-speaker posts (`store/scenes.py:split_reply` + `append_message`). "Discard" for the opener is simply not calling `first-post` — the frontend never wrote anything to discard in the first place.

What's missing is the generalization to multiple task kinds (#188), each of which needs a *different* canonical destination for its accepted output:
- **Verified in `store/scenes.py`**: `append_message(cid, sid, role, content, speaker=None)` writes a `**Speaker:** content` block to the scene body; `edit_message` (routes has `PUT .../messages/{index}`) overwrites an existing block in place.
- **Verified in `frontend/src/routes/CampaignView.tsx`**: the composer is a single controlled `<textarea>` bound to `const [input, setInput] = useState("")` (line ~39), sent via `send()` (line ~164) which calls `api.chat(...)`. There is no per-kind draft object — "replace draft in composer" for an accepted aux-task output means calling `setInput(text)` and leaving the send to the user, exactly like `reroll`'s existing `rerollPrompt` state does for guided regenerate.
- **Verified in `routes.py`**: `_persist_reply` is the only path that turns raw text into assistant posts; there is no existing "append as user post with speaker stamp" helper distinct from what `post_chat` already does inline (`scenes.stamp_user_speaker` + `append_message(..., "user", ...)`).

## Suggested approaches

### Option A — per-kind canonical-path table, mirroring first-post's shape (recommended)
- Define, per #188 task kind, exactly one accept destination:
  - **impersonate PC** → append as a user post via `scenes.append_message(cid, sid, "user", text, speaker=<pc name>)`, mirroring `post_chat`'s `stamp_user_speaker`+`append_message` pair.
  - **rewrite/polish** → replace the target draft in the composer (`setInput(text)` in `CampaignView.tsx`) if the source was unsent input, or `PUT .../messages/{index}` (`edit_message`) if the source was an existing post being polished.
  - **continue-as-character / "what would X say"** → append as an assistant post via the existing `_persist_reply(cid, sid, text)` path, same as `first-post`.
  - **brainstorm / translate** → copy-to-clipboard only; these don't correspond to any canonical scene artifact, so there is nothing to persist.
- One dispatch function (e.g. `routes.py: _accept_aux_task(kind, cid, sid, text, target)`) switches on kind to the above; "discard" is always a no-op (nothing was ever written), matching the opener's existing behavior exactly.
- Tradeoff: the table grows with every new kind #188 adds, but each entry is a one-line call to an already-existing store function — no new persistence primitives needed.

### Option B — every kind returns a client-side action descriptor, frontend dispatches
- The aux-task response includes a `{action: "append_user"|"append_assistant"|"replace_draft"|"edit_message"|"clipboard", ...}` tag; `CampaignView.tsx` (or a shared aux-task hook) does the routing instead of the backend.
- Keeps `routes.py` kind-agnostic, but duplicates the same kind→action knowledge on the frontend that Option A keeps server-side, and risks drift between what a kind means and how the client happens to route it. Only worth it if multiple frontends end up consuming the same aux-task API.

## Implementation notes

- Reuse `_persist_reply` and `scenes.append_message`/`edit_message` exactly as-is — this issue is a routing layer, not a new persistence primitive.
- Frontend: no existing generic "accept into composer" helper — `reroll`'s `rerollPrompt` state (`CampaignView.tsx`) is the closest existing precedent for "hold generated text pending an explicit user action."
- Backend tests: `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`, assert each kind's accept call produces exactly the expected scene-file diff (e.g. one new `**<PC name>:** ...` block for impersonate-PC) and that discard leaves the scene file untouched.

## Dependencies & related

- #187 — the ephemeral generation framework whose output this issue routes.
- #188 — defines the task kinds and therefore the canonical-path table's rows.
- #189 — accept-dispatch must still never trigger absorb/state-write side effects beyond the one canonical write per kind.


---

## [#196 — Per-campaign HUD: choose, reorder, hide and group widgets](https://github.com/charlesmsiegel/grimoire/issues/196)

**Source body captured before second-wave tracking consolidation (not current design):**

Part of the **Frontend Shell, HUD & Views** milestone. From the pre-rebuild target feature list:

> Per-campaign HUD configuration (choose / reorder / hide widgets, density, position, groups, reset to defaults)

## Current state
This depends entirely on `#195` (Always-visible scene HUD) existing first — today there is no HUD widget list to configure, only the fixed, non-configurable `SceneInspector.tsx` sections (Active characters / Location / When / Story so far, in that hard-coded order) described in `#195`. There is also no existing per-campaign UI-preference storage of any kind to build on:

- **`campaign.md` frontmatter** (`store/campaigns.py`, `dump_frontmatter`/`parse_frontmatter` in `store/frontmatter.py:1,29`) is **string-scalar only** — `_needs_quotes`/`_quote` (`frontmatter.py:8-19`) handle plain strings, not nested structures. A widget list with per-widget order/visibility/density/group would have to be serialized as a single JSON-in-a-string frontmatter value, which is workable but not the pattern any existing frontmatter key uses (`name`, `world`, `created`, `updated`, `world_copy` are all flat scalars).
- **`localStorage`** is not used anywhere in the frontend today (verified: zero hits in `frontend/src`) — even theme, which is a natural localStorage candidate, is persisted server-side via `config.md`'s `theme` key and fetched at boot (`App.tsx`). So there's no established local-persistence convention to extend either; both options below are green-field.
- The closest precedent for "structured per-campaign state in its own file" is the existing JSON sidecars alongside `campaign.md`: `appearances.json`, `relationships.json`, `plot.json`, `changes.json`, `sync.md` (frontmatter-only manifest) — i.e. campaigns already mix flat-frontmatter files with structured JSON files for exactly this kind of shape mismatch.

## Suggested approaches

### Option A — new `hud.json` sidecar, server-persisted (recommended)
- Add `campaigns/<cid>/hud.json` (a new small `store/hud.py`, following the `relationships.py`/`plot.json` shape: `read_hud(cid)`/`write_hud(cid, cfg)`) storing `{widgets: [{id, visible, order, density?, group?}], defaults_version}`.
- New routes `GET/PUT /campaigns/{cid}/hud` alongside the other per-campaign config routes (calendar GET/PUT is the closest existing precedent at `routes.py:1181-1198`).
- Frontend: a small settings popover/panel next to the HUD (from `#195`) with drag-reorder, visibility toggles, density selector, and a "reset to defaults" button that just re-`PUT`s the default config.
- Tradeoff: syncs across devices sharing the same `GRIMOIRE_HOME` (per the project's "point Storage location at a synced folder" model in root `CLAUDE.md`), matching how every other campaign setting behaves — recommended because HUD prefs are exactly the kind of per-campaign state the store already models this way (unlike, say, transient UI scroll position).

### Option B — `localStorage`, keyed per campaign id, no backend change
- Store `{[cid]: hudConfig}` in `localStorage` entirely client-side; no new route, no new store module.
- Simpler and faster to ship, but breaks the project's synced-storage model (root `CLAUDE.md`: "point it at a synced folder... to share a library across devices") — HUD layout would silently reset on a new device/browser, which is inconsistent with every other piece of campaign state living in the store.

## Implementation notes
- Do not touch `campaign.md` frontmatter for this — its string-scalar format is a poor fit for a widget list; use a sidecar JSON file (Option A) to match `relationships.json`/`plot.json`, or `localStorage` (Option B) if the team decides sync-across-devices isn't wanted for this particular preference.
- Widget IDs must be stable strings shared between this config and whatever `#195` ships (e.g. `"date"`, `"location"`, `"cast"`, `"events"`), so this issue can't fully land before `#195` defines them.
- Backend tests: `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`, one `test_hud.py` if a new store module is added. Frontend: `npx vitest run` from `frontend/` + `npx tsc -b`; cover reorder/hide/reset round-tripping through the chosen persistence layer.

## Dependencies & related
- #195 — must exist first; this issue configures its widget set.
- #197 — mechanics-contributed widgets would need to appear in this same configuration list once `#160` exists.
