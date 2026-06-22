# PCs, Tags & Actor Roles — Design

> Adds **player characters (PCs)** — a lightweight, taggable, optionally-versioned player
> actor — plus a world **tag vocabulary**, and a **role** dimension (`player` / `npc`) that is
> decoupled from entity kind so a rich Character card can also be cast as a player. Records the
> `player` role for later use; **no prompt injection yet** (that moves to the context builder).

**Status:** Design — not yet implemented
**Date:** 2026-06-21
**Branch:** `pcs-tags-actors` (off `character-cards`)
**Builds on:** [`2026-06-21-character-cards-design.md`](2026-06-21-character-cards-design.md) — reuses the
character container/version/appearance/sync machinery.

This is a **foundation spec**. The greetings & context-builder work (which consumes the tags,
PCs, and `player` role delivered here) is captured separately in
[`2026-06-21-greetings-context-builder-decisions.md`](2026-06-21-greetings-context-builder-decisions.md);
that includes the `{{user}}` injection deferred out of this spec.

## Purpose

The character-cards work gave campaigns a cast of versioned Character cards that "appear" in
scenes and lock to one version. This spec adds the **player** side of the table and the
vocabulary the greeting system will gate on:

- A **PC** is a player actor with a *simpler* payload than a V3 card (name, pronouns, summary,
  description). It is versioned and world→campaign synced exactly like a Character, and it
  "appears" in a scene's cast through the same mechanism.
- A world owns a **tag vocabulary** (e.g. `student`, `hannahs-father`); PCs carry tags drawn
  from it. (Greetings requiring tags arrives in Spec 2.)
- An actor's **role** (`player` / `npc`) is a *cast-time* choice, not a property of the entity.
  A `pc` entity is always a player; a `character` entity defaults to `npc` but can be cast as a
  player ("use a character as a PC"). Role **locks per campaign at first appearance**, just like
  the version lock. The role is **recorded** so the context builder can later inject players.

## Non-goals (this iteration)

- **No prompt injection at all — including `{{user}}`.** Scene chat behaves exactly as today; no
  player/NPC persona or lore feeds the LLM. The `player` role is recorded but not yet consumed.
  All injection (starting with `{{user}}`) moves to the greetings & context-builder work.
- **No greetings / plot maps.** Captured separately; this spec only delivers the tag vocabulary
  and PC/role foundation they build on.
- **No tags on Characters.** Tags live on PC entities for now. A character cast as a player is
  untagged this iteration (Spec 2 may revisit if tag-gating needs it).
- **No per-scene role switching.** Role is locked per campaign at first appearance; to use one
  persona in both roles, author two cards (the intended pattern — see the ashgrove/desmond example).

## Decisions & defaults

- **PC payload = name + pronouns + summary + description.** Pronouns/summary are short
  structured frontmatter fields; description is the markdown body (the bulk of the `{{user}}`
  persona).
- **PCs are not copied on campaign create.** Like Characters, they **appear on use** (when cast)
  and sync via the appearances mechanism — *not* via copy-on-create (which still applies only to
  locations/lore).
- **Appearance records become actor-kind-and-role aware** (see "Appearances generalization").
- **Role locks at first appearance.** A later `appear()` with a different role for the same
  actor is a `409` (mirrors the version-mismatch rule).
- **Tag vocabulary is world-level**, ids are slugs with display names, stored in `world/tags.md`.

## Storage — `~/.grimoire/`

```
worlds/<wid>/
  tags.md                       # frontmatter maps tag-id -> display name:  student: Student
  pcs/<pc-id>/
    pc.md                       # frontmatter: name, tags (comma-joined tag-ids), default_version
    <version-id>.md             # frontmatter: name, pronouns, summary ; body: description
  characters/…                  # unchanged (V3 card containers)
  locations/… lore/…            # unchanged
campaigns/<cid>/
  pcs/<pc-id>/<version-id>.md    # the ONE locked PC version, copied on appearance
  characters/…                  # unchanged
  appearances.json              # EXTENDED — actor-kind + role aware (below)
  sync.md  scenes/…             # unchanged
```

- `tags.md` reuses the existing string-scalar frontmatter writer: one line per tag,
  `tag-id: Display Name`. Body unused.
- PC version files are **plain markdown** (frontmatter `name`/`pronouns`/`summary` + body
  description) — this is the "simpler payload" vs the Character's V3 JSON card.
- PC ids / version ids / tag ids are slugified + uniquified (no date prefix), as elsewhere.

### `appearances.json` generalization

Today (character-cards) it keys a bare character id → `{version, base, scenes}`. To carry PCs
and the role dimension through the **same** cast/lock/sync machinery, records are keyed
`"<kind>/<id>"` (kind ∈ `characters` | `pcs`) and gain a `role`:

```json
{
  "characters/seraphine": {"version": "corrupted", "base": "3f9a…", "scenes": ["s1"], "role": "npc"},
  "pcs/elara":            {"version": "default",   "base": "a12b…", "scenes": ["s1"], "role": "player"},
  "characters/desmond-pc": {"version": "default",   "base": "c0de…", "scenes": ["s1"], "role": "player"}
}
```

This is a deliberate, contained refactor of the just-built character-cards code (the branch this
builds on is unmerged, so evolving the format is cheap). The alternative — a parallel PC
appearance + sync system — would duplicate the whole engine.

## Actor model

An **actor** in a scene is `(kind, id, version, role)`:

| kind | payload | default role | castable as player? |
|------|---------|--------------|---------------------|
| `characters` | rich V3 card | `npc` | **yes** (→ its card becomes the `{{user}}` persona) |
| `pcs` | simple persona | `player` | always player |

`appear(cid, scene_id, kind, actor_id, version_id, role)`:
1. Resolve the world root for the kind (`worlds/<wid>/characters` or `…/pcs`).
2. On first appearance: copy the locked version file (+ assets for characters) into the
   campaign, write the record `{version, base: world hash, scenes:[scene], role}`. **Locks**
   version *and* role.
3. On a later appearance: `version`/`role` must match the locked values (else `409`); append the
   scene to `scenes`.

`pcs` always pass `role="player"`; `characters` pass `role` (`npc` default, `player` to use a
character as a PC). The recorded role is **not consumed** by this spec — it exists so the
context builder (separate work) can later inject `{{user}}`.

## Sync (PCs reuse the character engine)

The character-incoming computation generalizes to **actor-incoming** over `appearances.json`:
for each appeared actor of either kind, compare `world` / `base` / `mine` hashes of the **locked
version file** (JSON for characters, markdown for PCs — both hashed as file text), yielding
`update` / `conflict` / nothing. PCs never produce `new` (they appear on use); world-side
deletions skipped — identical rules to characters. `incoming/accept/reject` route a ref by its
`kind` (`characters` | `pcs` → actor engine; `locations` | `lore` → the existing `sync.md`
engine). `campaigns_for_world` counts PC pending the same way.

## Backend modules

```
backend/src/grimoire/store/
  tags.py          # NEW — world tag vocabulary CRUD (read map / add / rename / delete)
  pcs.py           # NEW — PC container/version CRUD (mirrors characters.py; simple md payload + tags)
  appearances.py   # EXTENDED — actor-kind+role aware appear/scene_cast/roster; suggestions adapt
  sync.py          # EXTENDED — actor-incoming covers pcs/* alongside characters/*
  worlds.py        # EXTENDED — counts include pcs
  characters.py    # unchanged (pcs.py mirrors its shape)
```

Core sketches:

```python
# tags.py  (operates on a world root)
read_tags(root) -> dict[str,str]            # {tag-id: display}
add_tag(root, name) -> tag_id
rename_tag(root, tag_id, name); delete_tag(root, tag_id)

# pcs.py
list_pcs(root) -> [{id, name, tags:[...], default_version, versions:[{id,name}]}]
read_pc(root, pid) -> {meta:{id,name,tags,default_version}, versions:[{id,name,persona}]}
create_pc(root, name, tags, version_name="default", persona=None) -> (pid, vid)
create_version/update_version/set_default_version/delete_version/delete_pc
version_hash(root, pid, vid) -> str | None
pc_count(root); pc_refs(root)

# appearances.py
appear(cid, scene_id, kind, actor_id, version_id, role)
scene_cast(cid, scene_id) -> [{kind, id, role}]
roster(cid) -> [{kind, id, version, role, scenes}]
players_in_scene(cid, scene_id) -> [{kind, id, version}]   # role == player (for the context builder)
```

## API (deltas, all under `/api`)

```
# World tags
GET    /worlds/{wid}/tags                         → {tag-id: display}
POST   /worlds/{wid}/tags        {name}            → {id}
PUT    /worlds/{wid}/tags/{tid}  {name}            → rename
DELETE /worlds/{wid}/tags/{tid}

# World PCs (mirror character routes; declared before the generic /{kind} routes)
GET/POST           /worlds/{wid}/pcs               # create takes {name, tags?, version_name?, persona?}
GET/PUT/DELETE     /worlds/{wid}/pcs/{pid}         # PUT sets default_version and/or tags
GET/POST           /worlds/{wid}/pcs/{pid}/versions
PUT/DELETE         /worlds/{wid}/pcs/{pid}/versions/{vid}

# Campaign cast — generalized to actor kind + role
POST   /campaigns/{cid}/scenes/{sid}/cast  {kind, id, version?, role?}
GET    /campaigns/{cid}/scenes/{sid}/cast          → [{kind, id, role}]
GET    /campaigns/{cid}/appearances                → [{kind, id, version, role, scenes}]
# incoming/accept/reject already take {kind,id}; now accept kind ∈ characters|pcs|locations|lore
```

`kind` is validated against the actor/entity allowlist. The existing `cast` body `{character,
version?}` is replaced by `{kind, id, version?, role?}`; PC casts force `role="player"`,
character casts default `role="npc"`.

## Error handling

- `PCNotFound`, `PCVersionNotFound`, `TagNotFound` → `404`. Unknown actor `kind` → `404`.
- `appear()` with a role or version that differs from the campaign's lock → `409`.
- Adding a PC tag not in the world vocabulary → `400`.
- Deleting a tag that PCs still reference: allowed; the tag id simply remains on those PCs as a
  dangling reference (no cascade) — surfaced, not blocked. *(Decision; revisit if noisy.)*
- Name collisions on pc/version/tag ids auto-uniquify (never an error).
- A scene with no player-role actors injects **no** system message (chat behaves as today).

## Testing

**Backend (pytest, temp `GRIMOIRE_HOME`):**

- tag vocabulary CRUD; PC create rejects a tag not in the vocabulary (`400`).
- PC container/version round-trip + `version_hash` stability (markdown payload).
- `appear()` locks **version and role**; a later appear with a different role → `409`; PC forced
  to `player`; a **character cast as `player`** records `role: player`.
- `scene_cast` returns both kinds with roles; `players_in_scene` filters role == player.
- **PC sync** update/conflict via the shared actor engine; PCs never produce `new`;
  `campaigns_for_world` counts PC pending.
- `players_in_scene` returns only role == player actors (the seam the context builder will use);
  scene chat is unchanged — no system message is injected (verify the streamed payload still
  equals today's raw turns).

## Phasing (for the implementation plan)

1. **Tags + PCs storage** — `tags.py`, `pcs.py`, world routes, world counts.
2. **Actor generalization** — `appearances.json` format + `appear/scene_cast/roster`, generalized
   cast route, sync actor-incoming covering `pcs/*`. Migrate the character-cards tests/format.

## What's next

The greetings & context-builder work — including the `{{user}}` injection deferred out of this
spec — is captured in
[`2026-06-21-greetings-context-builder-decisions.md`](2026-06-21-greetings-context-builder-decisions.md).
