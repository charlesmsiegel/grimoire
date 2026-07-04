# Campaign World Editing — Design

**Date:** 2026-07-04
**Status:** Design — pending review
**Builds on:** campaign sync engine (`store/sync.py`), appearances/version locking
(`store/appearances.py`), greetings & plot maps availability (`store/greetings.py`,
`store/playing.py`), new-scene chooser (`2026-07-04-new-scene-chooser-design.md`) —
`?after=` unlock flag, greeting stamping.

## Problem

A campaign is supposed to be a full, independently editable copy of its world — but today
it isn't. `create_campaign` copies only locations and lore; greetings and the plot map are
read **live from the world** during play (`playing.py` resolves `_world_root`), and
characters/PCs are copied lazily, one version file at a time, when an actor first appears
in a scene. Consequences:

- Editing world greetings or characters silently changes running campaigns.
- There is no way to manipulate a campaign's world data directly without touching the
  world baseline.
- A campaign has access to every version of every character; the version used is locked
  implicitly on first appearance, with no explicit choice and no isolation from the rest.
- Greetings can only become "done" by actually playing them, and there is no way to
  exclude a greeting from scene-creation options.

## Scope

- **In:** truly self-contained campaigns (full copy at creation, lazy backfill for
  existing campaigns, play reads only the campaign copy); sync-engine coverage for the
  newly copied kinds; explicit character/PC version picking with purge of unpicked
  versions and import-to-replace from the world; greeting marks (**completed** /
  **skipped**) with availability semantics; campaign-side world tabs in the UI mirroring
  the world editor, plus the campaign-only sidebar actions.
- **Out:** the world editor itself (unchanged); the untagged-image tagging queue (stays
  world-only — image assets are a library concern, not mirrored into campaigns);
  campaign-to-world push (sync remains one-directional, world → campaign);
  `NewSceneChooser` layout changes (it just consumes the updated availability);
  multi-world campaigns.

## Decisions

1. **Full copy + migrate on read.** `create_campaign` copies everything: locations, lore
   (as today), greetings, `plotmap.json`, characters and PCs (meta + **all** version
   files + assets), calendar. Existing campaigns backfill lazily: when a campaign is
   opened and a piece of its copy is missing (no `greetings/` dir, no `plotmap.json`, no
   copy of a world actor), that piece is copied from the world then, with base hashes
   recorded. Locked actors are **not** backfilled with additional versions — a lock means
   the pick already happened.
2. **Play reads the campaign copy only.** `playing.py` (availability, casting, greeting
   bodies, default versions) and scene casting resolve against the campaign root, never
   the world root. The world is reachable only through sync and explicit version import.
3. **Copy all versions, pick one, purge the rest.** The campaign starts with every
   version of every actor. An explicit **pick** locks one version and deletes the other
   version files from the campaign tree (and their manifest refs), so the campaign has no
   access to unpicked versions afterward.
4. **Explicit pick or lazy lock.** Picking is available any time from the campaign's
   Characters/PCs tabs. If an unpicked actor appears in a scene first, `appear()` routes
   through the same pick logic — locking the requested (or default) version and purging
   the rest. One code path, two triggers.
5. **Import replaces the pick.** `import_version` copies the named version file from the
   source world into the campaign, deletes the previously picked file, re-points the lock,
   and resets that actor's sync base to the world's current hash. Past scene transcripts
   are untouched. The one-version-per-locked-actor invariant always holds.
6. **Greeting cast conflicts: the pick wins.** If a greeting's frontmatter names a
   version other than the locked one, casting uses the locked version.
7. **Greeting marks: completed and skipped, reversible.** `played.json` grows from a bare
   list to `{"played": [], "completed": [], "skipped": []}` (silent migration of the old
   list format — a bare list becomes `played`). *Played* = actually started as a scene
   (immutable from the marks UI); *completed* = marked done off-screen; *skipped* = won't
   do. Completed counts exactly like played for predecessor joins and `excludes` edges.
8. **Skipped greetings are routed around.** They disappear from availability output
   entirely and are removed from other greetings' predecessor lists before joins are
   evaluated: an `all` join is satisfied by the remaining predecessors; a greeting whose
   only predecessor was skipped becomes available. A skipped greeting was never played, so
   its `excludes` never fire.
9. **The `unlocked` flag survives unchanged.** Mark filtering happens before the
   new-scene chooser's `?after=` unlock flagging and unlocked-first sort; the availability
   payload shape the chooser depends on is preserved (each item additionally carries its
   mark for badging).
10. **Sync granularity.** Greetings sync per-file like locations/lore (manifest refs
    `greetings/<gid>`). The plot map syncs as a single ref (`plotmap`). Locked actors keep
    today's flow (only the locked version diffs; other world versions — including new
    ones — are invisible). Unpicked actors sync **whole-actor**: if any world-side version
    or meta hash differs from base, one incoming item is offered whose accept re-copies
    the entire actor dir; conflict if the campaign side also changed.

## Backend

### `store/entities.py` / `store/campaigns.py`

- A `SYNCED_KINDS` tuple (`ENTITY_KINDS + ("greetings",)`) drives copy-on-create,
  `all_refs`-style enumeration for sync, and hashing. Greetings do **not** join
  `ENTITY_KINDS` — generic entity CRUD stays locations/lore; greetings keep their
  dedicated store module and routes.
- `create_campaign` additionally copies `greetings/*.md`, `plotmap.json`, and the full
  `characters/` and `pcs/` trees (meta + every version + assets), recording manifest
  base hashes for greetings/plotmap and per-actor base hashes for unpicked actors.
- `ensure_campaign_copy(cid)` — idempotent backfill, called when a campaign is read via
  the API: copies any missing piece from the world (greetings dir, plotmap, actors absent
  from the campaign tree) and records bases. Skips locked actors' unpicked versions.

### `store/playing.py`

- `_world_root` usages replaced by the campaign root: `available_greetings`,
  `start_from_greeting` (greeting meta/body, plot map, character default versions) all
  read the campaign copy.
- `read_played` → `read_marks(cid) -> {"played": set, "completed": set, "skipped": set}`
  with list-format migration on read. `mark_greeting(cid, gid, status)` sets
  completed/skipped/none; refuses to alter a genuinely played greeting.
- `available_greetings`: `done = played ∪ completed` feeds availability; skipped ids are
  passed for filtering and predecessor pruning; `unlocked` flagging and sort run on the
  filtered list; each item gains `"mark": "completed" | "skipped" | null` — for the
  chooser, marked greetings simply never reach it (completed/played are unavailable,
  skipped are absent).

### `store/greetings.py`

- `availability(root, plotmap, done, tags, skipped=frozenset())`: drop skipped greetings
  from the output; prune skipped ids from every predecessor list before evaluating
  `predecessor_join`; a greeting whose predecessor list becomes empty has no predecessor
  requirement.

### `store/appearances.py`

- `pick_version(cid, kind, aid, vid)` — validates the version exists in the campaign
  copy; records the lock (reusing the appearance-record shape, with an empty scenes list
  when picked outside a scene); deletes the other version files from the campaign actor
  dir and their manifest/base refs; sets `default_version` in the campaign's actor meta to
  the pick. The actor's existing sync base for the picked version is preserved, so
  campaign-side edits made before picking still diff correctly.
- `appear()` routes through `pick_version` when the actor is unlocked, then proceeds as
  today.
- `import_version(cid, kind, aid, vid)` — copies `<vid>` from the world, removes the
  previously locked version file, re-points the lock, sets base to the world's current
  hash for that version.

### `store/sync.py`

- `incoming` extends over `SYNCED_KINDS` refs plus the `plotmap` ref (blob = raw JSON
  text for conflict display). `_actor_incoming` keeps the locked-actor flow and adds
  whole-actor items for unpicked campaign actors whose world tree changed.
- `accept`/`reject` handle the new ref kinds via the existing `_advance` path (accept of
  a whole-actor ref re-copies the actor dir; accept of `plotmap` copies the file).

## API

- `POST /campaigns/{cid}/{kind}/{aid}/pick-version` and `.../import-version`
  (body `{"version": str}`, kind ∈ `characters` | `pcs`) — pick 409s if the actor is
  already locked; import 409s if it is **not** locked (unlocked actors take world changes
  via sync, not import); 404 for unknown actor/version.
- `POST /campaigns/{cid}/greetings/{gid}/mark` (body
  `{"status": "completed" | "skipped" | "none"}`) — 409 when targeting a played greeting.
- Campaign-scoped read/CRUD routes for greetings, characters, and PCs mirroring the
  world-scoped ones (`/campaigns/{cid}/greetings...`, `/campaigns/{cid}/characters...`,
  `/campaigns/{cid}/pcs...`), reusing the store modules' existing root-parameterized
  functions. Plot-map read/edit routes likewise (`/campaigns/{cid}/plotmap`).
- `GET /campaigns/{cid}/greetings/available` unchanged in shape; items gain `mark`.
- Campaign read routes invoke `ensure_campaign_copy` (the migrate-on-read hook).

## Frontend

- **CampaignView gains the world's tabs** — Characters, PCs, Locations, Lore, Greetings —
  alongside its existing tabs, reusing the existing list/detail editors parameterized by
  a container (`{scope: "world" | "campaign", id}`) instead of a hardcoded world id.
  `api/client.ts` helpers become container-aware. The list/detail pattern from CLAUDE.md
  is unchanged. The untagged-image tagging queue does not appear in campaign scope.
- **Campaign-only sidebar sections:**
  - Character/PC detail: a **Version** side-section — the locked/picked version as a chip
    when locked; while unlocked, the version list with a **Pick this version** action
    (with a confirm, since it purges the rest); when locked, an **Import from world…**
    control listing the source world's versions for that actor (fetched from the world
    routes) with a replace confirm.
  - Greeting detail: a status control — **Mark complete** / **Won't do** / **Clear** —
    disabled with an explanatory hint when the greeting was genuinely played. The list
    rail badges completed/skipped/played greetings.
- **NewSceneChooser** needs no structural change: skipped greetings never arrive,
  completed ones make their successors rank as unlocked. Its tests gain a regression case
  for marked greetings being absent.

## Testing

### Backend (pytest)

- Copy-on-create: campaign tree contains greetings, plotmap, all actor versions; manifest
  and actor bases recorded.
- Backfill: a pre-existing campaign missing greetings/plotmap/actors gains them on read;
  locked actors do not regain purged versions; backfill is idempotent.
- Play reads the campaign copy: editing a world greeting/character after creation does
  not change availability, casting, or greeting bodies in the campaign.
- Pick: purges other version files and manifest refs, sets lock + default_version;
  lazy `appear()` on an unpicked actor picks and purges; pick on a locked actor → error.
- Import: replaces the file, re-points the lock, resets base; unknown version → error.
- Marks: list-format `played.json` migrates; completed unlocks successors and fires
  `excludes`; skipped disappears from availability and is pruned from predecessor lists
  (`all` join satisfied by the rest; sole-predecessor-skipped → available); marking a
  played greeting → error; clearing a mark restores availability; `unlocked` flag/sort
  unaffected by filtering.
- Sync: world greeting/plotmap edits show as new/update/conflict and accept/reject
  correctly; unpicked-actor world changes produce one whole-actor item whose accept
  re-copies the dir; locked actors diff only the locked version; purged versions never
  appear as incoming.

### Frontend (vitest)

- Campaign tabs render the campaign copy via the container-aware client (mocked).
- Version side-section: pick action (confirm → API call → refreshed lock), import listing
  world versions, locked state rendering.
- Greeting mark control: mark/clear calls, played-greeting disabled state, rail badges.
- `NewSceneChooser`: marked greetings absent from cards.
- Editor components keep passing under both container scopes (list/detail pattern tests
  per CLAUDE.md).

## Phasing (for the plan)

1. `SYNCED_KINDS`, full copy in `create_campaign`, `ensure_campaign_copy` backfill.
2. Repoint `playing.py`/casting to the campaign root.
3. Sync extension: greetings, plotmap, whole-actor unpicked diffs.
4. `pick_version` / purge / `import_version`; `appear()` routing.
5. Greeting marks store + availability filtering/pruning; `mark` route.
6. Campaign-scoped routes (greetings/characters/pcs/plotmap CRUD, pick/import/mark).
7. Frontend: container-aware client + campaign world tabs reusing the editors.
8. Frontend: version side-section, greeting mark control, chooser regression test.
