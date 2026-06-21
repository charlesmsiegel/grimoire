# Worlds & Campaigns — Design

> Adds two structural layers above the flat chat pile: **worlds** (reusable templates
> holding characters and setting information) and **campaigns** (a mutable, diverging copy
> of one world, plus the chats — now called **scenes** — played within it). A **push/sync**
> mechanism lets a world offer its additions and edits to the campaigns derived from it,
> accepted per object.

**Status:** Design — not yet implemented
**Date:** 2026-06-20
**Branch:** `worlds-campaigns` (off `attempt-2`)

## Purpose

The seed stores chats as a flat pile under `~/.grimoire/conversations/`. This change
introduces the world/campaign model the full app is built around:

- A **world** is a reusable template: characters, locations, lore (and more kinds later).
- A **campaign** is created *from* a world by copying its entities, then diverges freely as
  you play — character definitions change, locations evolve. The campaign also owns its
  **scenes** (chat transcripts).
- When a world changes (a new character is added, an existing one is edited), those changes
  can be **pushed** to the campaigns using that world and **accepted per object**, with
  conflicts (both sides changed the same entity) surfaced for the user to resolve.

This spec delivers the data model, copy-on-create, the push/sync engine, and the relocation
of chats into campaigns as scenes. It deliberately **defers** two things to later specs:
the internal schema of an entity (characters get a dedicated storage plan) and injecting
campaign entities into the LLM prompt.

## Non-goals (this iteration)

- **No prompt injection.** Scene chat behaves exactly as today; campaign entities do not yet
  feed the LLM. (Deferred until the entity schema lands.)
- **No fixed entity schema.** Entities are generic markdown (frontmatter + body); the shape
  of a character is intentionally loose for now.
- **No deletion propagation.** A world entity removed after a campaign forked stays in the
  campaign. Push handles adds / updates / conflicts only.
- **No migration.** The existing flat `conversations/` pile is treated as throwaway dev data;
  the new layout starts empty. The old `/api/conversations*` surface is removed.
- **One source world per campaign** (a campaign is a copy of *a* world).

## Decisions & defaults

- **One source world per campaign.**
- **Empty world/campaign is valid** — you can create a campaign with zero entities and chat
  immediately.
- **Deleting a world is allowed even if campaigns exist** — those campaigns keep their copied
  entities and simply detach (no further pushes from a deleted world).
- **Sync tracking = per-campaign manifest + content hashes** (chosen over per-entity
  frontmatter pointers or a pre-staged `incoming/` inbox; see "Why this approach").

## Data — `~/.grimoire/`

```
~/.grimoire/
  config.md                          # unchanged
  worlds/
    <world-id>/
      world.md                       # frontmatter: name, created, updated; body: description
      characters/<entity-id>.md      # entity = frontmatter (name + loose fields) + body
      locations/<entity-id>.md
      lore/<entity-id>.md
  campaigns/
    <campaign-id>/
      campaign.md                    # frontmatter: name, world: <world-id>, created, updated
      sync.md                        # base-hash manifest (see "Sync engine")
      characters/<entity-id>.md      # the campaign's OWN copy — diverges freely
      locations/<entity-id>.md
      lore/<entity-id>.md
      scenes/
        <scene-id>.md                # one chat transcript; same file format as today
```

`ensure_home` creates `worlds/` and `campaigns/` on first run.

### Entities are generic and content-only

- A **kind** is just a subfolder name. The kind list is open — adding `factions/` later is
  zero machinery, gated only by a small allowlist (`characters`, `locations`, `lore`).
- An entity file is frontmatter + body with **no enforced schema** beyond a `name`.
- **Entities carry no auto timestamps.** An entity's content hash therefore changes *only*
  when its content changes, which is what makes sync detection meaningful. (`world.md` and
  `campaign.md` do carry `created`/`updated`; entities do not.)

### IDs

Slugified from the name and uniquified on collision (the same helper conversations use today),
**without** a date prefix — worlds, campaigns, and entities are named things, not dated ones.

### `sync.md` manifest

Reuses the existing frontmatter writer: one line per ref, key = `<kind>/<id>`, value = the
base hash. Body unused.

```markdown
---
characters/seraphine: 3f9a2c…
locations/drowned-library: a12b8f…
---
```

### Scene files

Identical format to today's conversation files (frontmatter + `**You:**`/`**Grimoire:**`
transcript body), re-homed under `campaigns/<cid>/scenes/`.

## Sync engine (the heart)

Everything is driven by comparing three content hashes per ref `(kind, id)`:

- **world** — hash of the world's current entity file (or absent)
- **base** — `sync.md[ref]`, the world hash captured at the campaign's last accept/reject for
  this ref (or absent)
- **mine** — hash of the campaign's current entity file (or absent)

`hash = sha256(file text)`. Both sides serialize via the same frontmatter writer and entities
carry no timestamps, so identical content ⇒ identical hash.

### Computing a campaign's incoming changes

Over the union of refs across world ∪ campaign ∪ manifest:

| world vs base        | mine vs base          | campaign has file | status                  |
|----------------------|-----------------------|-------------------|-------------------------|
| changed (`world≠base`) | —                   | no                | **new** (world has it, you don't) |
| changed              | unchanged (`mine==base`) | yes            | **update** (world moved, you didn't) |
| changed              | changed (`mine≠base`)    | yes            | **conflict** (both moved) |
| unchanged (`world==base`) | anything           | —                 | *nothing to offer*      |

World-entity **deletions** (`world` absent but `base`/`mine` present) are skipped this
iteration.

### Acceptance (per object)

Two actions; **both advance `base` to the world's current hash**, so a handled change never
nags again — it only re-surfaces if the world changes *again*:

- **Accept(ref)** — copy world entity content into the campaign (creates the file for *new*,
  overwrites for *update*/*conflict*); set `sync.md[ref] = world_hash`; bump `campaign.updated`.
- **Reject / keep mine(ref)** — leave campaign content untouched; set `sync.md[ref] =
  world_hash`. A rejected *new* entity stays absent (no file), but the manifest now records the
  base so it won't reappear. Note: because reject advances `base` to the rejected world hash
  while the campaign content stays put, the campaign now differs from `base`; the *next* world
  edit to that entity therefore surfaces as a **conflict** (not a clean update) — which is
  correct, since accepting it would overwrite the version the user deliberately kept.

Accept/reject of a ref that isn't currently pending is an idempotent no-op.

### Push and pull are the same computation

- **Campaign side (pull/review):** `GET /incoming` returns the pending list, including both
  content blobs for *update*/*conflict* so the UI can show world-vs-mine. `POST
  /incoming/accept` and `/incoming/reject` take a list of refs.
- **World side (push):** `GET /worlds/{wid}/campaigns` runs the same `/incoming` computation
  for every campaign whose `campaign.md` `world == wid`, returning per-campaign counts
  `{new, update, conflict}`. It mutates nothing — it's a launcher into each campaign's review.
  This is what makes "push to all" and per-campaign "sync" one engine.

### Copy-on-create

Creating a campaign from a world deep-copies every world entity into the campaign under the
same `kind/id` and writes `sync.md[ref] = hash` for each. From that instant the campaign is
fully independent; divergence and future pushes flow through the table above. An empty world
produces an empty (but immediately chattable) campaign.

## Why this approach (sync tracking)

Considered three ways to track what a campaign has synced:

- **A. Per-campaign manifest + content hashes (chosen).** One `sync.md` per campaign mapping
  each ref → base hash; pending changes computed live from world/base/mine. Keeps entity files
  clean (no sync metadata polluting the deliberately-loose entity shape), represents a
  declined-*new* entity (manifest entry, no file), and makes push and pull the same live
  computation. Cost: one manifest file to keep consistent.
- **B. Base pointer in each entity's frontmatter.** No manifest, but pollutes entity files and
  cannot represent a declined brand-new entity (no file to hold the pointer) — rejects of new
  entities would nag forever or need tombstones.
- **C. World pre-stages content into a per-campaign `incoming/` inbox on push.** Explicit and
  auditable, but duplicates content and prevents a campaign from seeing world updates until
  someone pushes (no live "world has N updates").

A wins on cleanliness, the no-nag reject path, and unifying push/pull.

## Backend

### `store/` package split

This change roughly triples the data layer; one file would do too much. `store.py` becomes a
package, each module with one job:

```
backend/src/grimoire/store/
  __init__.py        # re-exports the public surface (so `from grimoire import store` still works)
  frontmatter.py     # parse_frontmatter / dump_frontmatter / quoting   (moved verbatim)
  paths.py           # home(), GRIMOIRE_HOME, slugify, uniquify, _now_iso
  config.py          # read_config / write_config                       (moved verbatim)
  entities.py        # generic kind/id CRUD + hashing over an arbitrary container root
  worlds.py          # world meta CRUD; uses entities.py for its kind folders
  campaigns.py       # campaign meta CRUD; copy-on-create; uses entities.py
  scenes.py          # scene CRUD + append_message (the old conversation code, re-homed)
  sync.py            # the sync engine: hashes, manifest IO, incoming, accept/reject
```

`entities.py` is **container-agnostic** — it operates on a root path, so a world dir and a
campaign dir share the exact same entity CRUD and hashing. `worlds.py`/`campaigns.py` pass
their root in.

Core functions (sketch):

```python
# entities.py
list_entities(root, kind) -> list[dict]            # {id, name, ...frontmatter}
read_entity(root, kind, eid) -> {meta, body}
create_entity(root, kind, name, body) -> eid
update_entity(root, kind, eid, name=None, body=None)
delete_entity(root, kind, eid)
entity_hash(root, kind, eid) -> str | None         # sha256 of file text, None if absent

# campaigns.py
create_campaign(name, world_id) -> cid             # copy-on-create + write sync.md

# sync.py
incoming(cid) -> list[Pending]                     # {ref:{kind,id}, status, world?, mine?}
accept(cid, refs); reject(cid, refs)
```

### API surface (all under `/api`)

```
# Worlds
GET    /worlds                          → [{id, name, counts}]
POST   /worlds                {name}     → {id}
GET    /worlds/{wid}                     → {meta}
PUT    /worlds/{wid}          {name}     → rename
DELETE /worlds/{wid}
GET    /worlds/{wid}/{kind}              → [entities]          # kind ∈ characters|locations|lore|…
POST   /worlds/{wid}/{kind}   {name, body}
GET    /worlds/{wid}/{kind}/{eid}
PUT    /worlds/{wid}/{kind}/{eid}  {name?, body?}
DELETE /worlds/{wid}/{kind}/{eid}
GET    /worlds/{wid}/campaigns           → [{id, name, pending:{new,update,conflict}}]   # push view

# Campaigns
GET    /campaigns                        → [{id, name, world}]
POST   /campaigns             {name, world}  → {id}            # copy-on-create
GET    /campaigns/{cid}                  → {meta}
PUT    /campaigns/{cid}       {name}
DELETE /campaigns/{cid}
GET/POST/PUT/DELETE /campaigns/{cid}/{kind}[/{eid}]           # diverged-copy entity CRUD
GET    /campaigns/{cid}/incoming         → [pending + content blobs]
POST   /campaigns/{cid}/incoming/accept  {refs:[{kind,id}]}
POST   /campaigns/{cid}/incoming/reject  {refs:[{kind,id}]}

# Scenes (the old conversation/chat endpoints, re-homed under a campaign)
GET    /campaigns/{cid}/scenes
POST   /campaigns/{cid}/scenes           {title?}
GET    /campaigns/{cid}/scenes/{sid}
PUT    /campaigns/{cid}/scenes/{sid}     {title}     # rename
DELETE /campaigns/{cid}/scenes/{sid}
POST   /campaigns/{cid}/scenes/{sid}/chat   {content}    # SSE stream — unchanged logic
POST   /campaigns/{cid}/scenes/{sid}/retry              # SSE stream — unchanged logic
```

`kind` is a path param validated against a small allowlist that's trivial to extend. The
chat/retry SSE streaming code is lifted as-is; only its storage path changes. The old
`/api/conversations*` routes are removed.

## Frontend

### Information architecture

Today's shell is Chat + Config. It becomes **Campaigns (default) · Worlds · Config**.

- **CampaignsView** — list of campaigns (name, source world, scene count) + "New campaign"
  (name + world picker). Click → CampaignView.
- **CampaignView** — the main play space; it *is* today's `ChatView`, scoped to one campaign:
  - **Scenes sidebar** = today's conversation sidebar, renamed (list + new + rename + delete).
  - **Transcript + input** = unchanged (SSE streaming, retry, markdown rendering).
  - **Campaign entities** panel (characters/locations/lore) using the shared entity editor.
  - **Incoming badge** — pending count from `GET /incoming`; opens the review.
- **WorldsView** — list of worlds (name, entity counts) + "New world". Click → WorldView.
- **WorldView**:
  - Entity management (characters/locations/lore CRUD) via the shared entity editor.
  - **Push panel** — `GET /worlds/{wid}/campaigns`: each campaign + its `{new, update,
    conflict}` counts; clicking a campaign jumps into that campaign's incoming review.

### Components

- `EntityList` + `EntityEditor` — one pair reused for both world and campaign entities (same
  CRUD shape, different base path).
- `IncomingReview` — the per-object accept/reject UI. `new`/`update` show the world version;
  `conflict` shows **world vs mine side-by-side** with a louder warning. Each row has Accept /
  Keep-mine; plus select-all bulk actions.
- `api/client.ts` gains typed functions for worlds, campaigns, entities, scenes, and sync; the
  old conversation functions are removed.

All components reference **theme tokens only** — no hardcoded colors or fonts.

## Error handling

- New typed store exceptions `WorldNotFound`, `CampaignNotFound`, `EntityNotFound`,
  `SceneNotFound` → `404`. Unknown `kind` → `404`. Creating a campaign against a missing world
  → `400`.
- Name collisions auto-uniquify (never an error).
- Accept/reject of a non-pending ref is an idempotent no-op (advances base harmlessly).
- `ensure_home` creates `worlds/`/`campaigns/`; a missing `sync.md` reads as an empty manifest.
- The chat SSE error contract is unchanged.

## Testing

- **Backend (pytest, temp `GRIMOIRE_HOME`):**
  - entity round-trip + hashing;
  - copy-on-create writes a manifest whose base hashes match the copied content;
  - **the sync table** — new / update / conflict / nothing-to-offer (the critical test);
  - accept copies content + advances base;
  - reject advances base only, and a rejected change does **not** re-surface (including a
    rejected *new* entity staying absent);
  - scene chat happy path under a campaign with a fake OpenRouter client + missing-key `409`;
  - deleting a world leaves its campaigns' copies intact.
- **Frontend (light):**
  - the scene streaming reducer (today's test, re-homed);
  - `IncomingReview` renders the three statuses and a conflict shows both sides;
  - the create-campaign flow hits the right endpoint.

## Phasing

The design is one coherent whole; the implementation plan will sequence it:

1. **Backend** — `store/` package split + worlds/campaigns/entities/scenes/sync + routes +
   tests. App stays green.
2. **Frontend core loop** — nav, worlds & campaigns CRUD, create-campaign-from-world, scene
   chat re-homed end-to-end.
3. **Frontend sync** — shared entity editor, `IncomingReview` (accept/reject, conflict diff),
   world push panel.

## What grows later (not built now)

- The internal schema of a character/location/lore entity (dedicated storage plan).
- Injecting campaign entities into the LLM prompt (context builder).
- Deletion propagation on push; multi-world composition per campaign; entity-level (sub-field)
  conflict resolution beyond whole-file keep-mine / take-world's.
