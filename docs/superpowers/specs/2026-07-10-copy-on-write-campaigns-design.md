# Copy-on-Write Campaigns — Design

**Date:** 2026-07-05
**Status:** Design — approved (questions resolved with user)
**Builds on / partially reverses:** campaign world editing
(`2026-07-04-campaign-world-editing-design.md`) — decisions 1–2 there (full copy at
creation, play reads only the campaign copy) are **reversed**; decisions 3–10 (version
locks with purge, explicit pick / lazy lock, import-to-replace, greeting marks,
availability semantics) are **preserved unchanged**.

## Problem

A campaign duplicates its entire world at creation: every lore/location/greeting file,
entity images, `plotmap.json`, and every version of every character and PC **including
avatar and gallery assets**. A library world with dozens of characters costs that much
disk again for every campaign made from it, even though a typical campaign touches a
handful of records. The user asked for campaigns that only copy files **when they
differ from the world**.

## Decisions (resolved with user)

1. **Track the world live.** A record the campaign has not touched is *inherited*: it
   is read through to the world at request time, so world edits show up in campaigns
   immediately with no sync step. This deliberately reverses the 07-04 "snapshot"
   semantics for untouched records; isolation now comes from **divergence** (edit,
   lock, delete), not from copying. The Sync page shrinks to handling only diverged
   records.
2. **Auto-slim on access.** Existing full-copy campaigns are migrated by a lazy,
   idempotent pass (same hook as `ensure_campaign_copy` today) that deletes campaign
   files which are provably redundant, and preserves user deletions via tombstones.
3. **World deletion is blocked** while any campaign references the world (HTTP 409
   naming the campaigns). Campaigns whose world was deleted *before* this change stay
   fat and fully functional (slim skips when the world dir is missing).
4. **Architecture: a new `store/overlay.py`** exposes campaign-scoped operations that
   implement the resolution rules. The generic single-root store modules
   (`entities.py`, `characters.py`, `pcs.py`, `greetings.py`, `assets.py`) stay
   single-root; campaign routes and campaign-reading consumers switch to overlay calls.

## Resolution rules

- **Flat records** (locations, lore, greetings; `plotmap.json`): campaign file wins if
  present (*materialized*); else a tombstone means *absent*; else the world file
  (*inherited*).
- **Actors** (characters, pcs): resolution is **whole-dir**, keyed on the container
  meta (`character.md` / `pc.md`) existing under the campaign. A materialized actor
  dir is authoritative for meta + version files — never union versions, or
  lock-purged versions would resurrect. Sidecars (`tagline.md`) and **assets** still
  overlay per file even for materialized actors.
- **Assets** (`<base>/<id>/assets/<vid>/*`): per-file union; a campaign file of the
  same name wins; asset tombstones hide inherited files. Asset-derived fields in
  actor payloads (`images`, `has_avatar`, `gallery_count`, `localized_count`,
  `avatar_focus`, `tagline`) are patched from the union for materialized actors.
  Focus: a campaign-side avatar file makes campaign focus authoritative; otherwise
  campaign focus falls back to world focus.
- **Campaign-local state is untouched**: scenes, appearances.json, chronicle,
  relationships, plot, changes, played.json, dossiers (`dossier.md`), playstate
  (`state.md`), calendar config (still copied at creation — tiny, campaign-owned).
  Note `dossier.md`/`state.md` live *inside* `croot/characters/<id>/`; writing them
  must not count as materializing the actor (resolution keys on `character.md`, not
  dir existence).

## Divergence lifecycle

- **Materialize** = copy from world into campaign + record the world's current hash as
  the sync base in `sync.md`. Triggers: editing an inherited entity/greeting; any
  version write (`create_version`, `update_version`, `delete_version`,
  `set_default_version`) on an inherited actor (copies meta + all version files, **no
  assets**); plotmap edge edits; absorb write-backs; version lock (existing
  `appearances._lock` path, which copies a single version — `_copy_actor` stops
  copying assets).
- **Delete** an inherited record ⇒ tombstone in `<campaign>/deleted.json` (JSON list
  of refs: `"lore/<id>"`, `"locations/<id>"`, `"greetings/<id>"`, `"characters/<id>"`,
  `"pcs/<id>"`, `"plotmap"`, `"assets/<base>/<id>/<vid>/<name>"`). Delete a
  materialized record ⇒ remove file(s) **and** tombstone (if the world still has it)
  and drop the manifest entry. Tombstoned ids count as **taken** for uniquify — a
  recreated same-name record gets a new id; no resurrection semantics.
- **Create** in campaign ⇒ campaign-local file, no manifest entry; uniquify runs over
  the merged namespace (campaign ∪ world ∪ tombstones) so a new record never shadows
  a world record. (The generic `create_*` functions gain an optional `taken`
  callable for this.)
- **`sync.md` manifest** holds base hashes **only for materialized records**. A new
  campaign starts with an empty manifest. Locked actors keep their base in
  `appearances.json` as today.

## Sync rework (`store/sync.py`)

- `incoming(cid)` iterates **manifest refs + locked appearance refs only** (drop the
  union over world/campaign `synced_refs` and the world sweep in
  `_unpicked_incoming`). Inherited records never produce items. Status logic per ref
  is unchanged (world vs base vs mine); `"new"` no longer occurs for flat refs.
- `accept` ("take world") for flat refs and materialized-unlocked actors:
  **delete the campaign copy and drop the manifest entry** — the record reverts to
  inherited (space reclaimed). Actor dematerialization removes meta + version files
  only, keeping sidecars/assets. Locked actors keep today's per-version copy +
  base advance in appearances.json.
- `reject` ("keep mine"): advance the manifest base, exactly as today.
- `campaigns_for_world` unchanged in shape.

## Slim migration (`campaigns.ensure_campaign_slim`)

Replaces `ensure_campaign_copy` at its call sites (routes.py `_campaign_root_or_404`
and the campaign read/update/sync routes). Idempotent; done-marker
`world_copy: overlay` in campaign.md. Skips (without marking) when the world dir is
missing — a synced folder may deliver the world later.

For each manifest ref with base `b`:
- Flat ref, campaign file present, `hash(croot) == b == hash(wroot)` → delete the
  file, drop the entry. Campaign file **missing** but world file present → tombstone
  (preserves a user deletion), drop the entry. World file also gone → just drop.
- Unlocked actor ref, `dir_hash(croot) == b == dir_hash(wroot)` → dematerialize
  (delete meta + version files), drop the entry.
- `plotmap` analogous via `plotmap_hash`.

Then prune **byte-identical duplicates** (`filecmp.cmp(..., shallow=False)`) of world
files across all campaign actor/entity asset dirs and sidecars (`tagline.md`,
`focus.json`) — for locked and unlocked actors alike — keeping campaign-only or
diverged files. Remove emptied dirs. Finally stamp `world_copy: overlay`.

Diverged records, locked actors' cards, dossiers/state (world has none), and all
campaign-local state are never touched.

## World deletion guard (`store/worlds.py`)

`delete_world(wid)` raises `WorldInUse(wid, campaign_names)` when any campaign's
`world` meta references it (function-level import of `campaigns` to avoid the module
cycle). Route maps it to 409 with the names. `WorldsView.remove` surfaces the message
and its confirm text drops the now-false "campaigns keep their copies" claim.

## Consumers that switch to overlay reads

- `routes.py` campaign sections: entity CRUD + images (~2075–2125), characters/PCs
  read+write (~1598–1737), campaign greetings CRUD (~1961–2025), the campaign
  character image GET (~1611), pick-version existence check (~1748, must accept a
  world-side version for an unmaterialized actor), `_seat_cast_member` default-version
  resolution (already tries croot-then-wroot; becomes overlay), scene location name
  lookup (~1855).
- `context.py`: `_world_info`, current-setting lookup, `_cast_directory_data`
  (character refs, taglines, versions), `_char_name`. Locked-cast card/persona reads
  may stay on croot (lock ⇒ materialized is an invariant), except
  `_campaign_player_refs`/`scene_substitutions` which follow the same invariant.
- `playing.py`: `available_greetings` (greeting list + plotmap via overlay),
  `start_from_greeting` (greeting read; co-present **unlocked** characters'
  `default_version` must fall through to the world), `mark_greeting` existence check.
- `appearances.py`: `suggestions` candidate list; `pick_version` guard accepts
  world-side versions; `_copy_actor` stops copying assets.
- `absorb.py`: entity reads/writes via overlay (write-backs materialize);
  `chronicle.py` location read; `suggest.py` candidate lists.

## API

No new endpoints; no response-shape changes. Behavior changes only:
- Campaign lists/reads now include inherited world records (previously equivalent
  because everything was copied).
- `DELETE /worlds/{wid}` can return 409 `{"detail": "world is used by campaigns: …"}`.
- Sync payloads only ever contain diverged records.

## Testing

- **Overlay unit tests** (`backend/tests/test_overlay.py`): fallthrough reads, merged
  lists (campaign wins), tombstones (delete inherited → gone, world edit doesn't
  resurrect), materialize-on-write records base hash, merged-namespace uniquify,
  asset union + focus/tagline fallthrough, actor whole-dir resolution keeps purged
  versions purged, dossier/state writes don't materialize.
- **Thin creation**: after `create_campaign` the campaign dir holds only
  campaign.md, sync.md (empty manifest), scenes/, calendar.md; world records
  readable through campaign routes.
- **Slim migration**: a fat fixture slims to the same observable API payloads;
  diverged records, locked actors, campaign-only assets survive; a user-deleted copy
  becomes a tombstone; idempotent; skips when the world is missing.
- **Sync**: inherited world edits produce no incoming items; materialized ones show
  update/conflict; accept dematerializes; reject advances base; locked-actor flow
  byte-identical to today.
- **World delete**: blocked with campaign names; allowed once campaigns are gone.
- **Frontend**: WorldsView surfaces the 409; existing suites (`EntityEditor`,
  `CharacterEditor`, `GreetingEditor`, `CastPanel`, sync UI) keep passing — API
  shapes are unchanged.

## Phasing (for the plan)

1. `overlay.py`: tombstones + flat-record resolution (locations/lore).
2. `overlay.py`: greetings + plotmap.
3. `overlay.py`: actors (roots, merged lists, materialize/dematerialize, taglines).
4. `overlay.py`: assets (union, serving root, focus, delete/promote copy-up).
5. Campaign routes + consumers switch to overlay (behavior-neutral while campaigns
   are still fat).
6. `create_campaign` goes thin; `appearances._copy_actor` stops copying assets;
   pick/seat guards accept world-side versions.
7. Sync rework.
8. `ensure_campaign_slim` + call-site swap.
9. World-delete guard, backend + frontend.
