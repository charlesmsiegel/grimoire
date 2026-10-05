# World and PC profiles — implementation plan

Spec: `docs/superpowers/specs/2026-10-05-world-and-pc-profiles-design.md`.
Three independent slices, landed as one commit each, in this order (the two PC
slices touch the same files; #38 touches none of them).

## 1. #65 — PC goals and narrator notes

1. `store/pcs.py`: `PERSONA_FIELDS += ("goals", "player_notes")`;
   `blank_persona` gains both; `_dump_persona` folds whitespace in every
   frontmatter scalar (`" ".join(str(v).split())`).
2. `templates/scene/_persona_blocks.j2`: `pc_block` appends `Goals:` /
   `Narrator guidance:` lines when set.
3. Tests: `test_pcs_store.py` (round-trip incl. new fields; newline folding;
   legacy file without the keys reads `""`), `test_context.py` (both lines in
   the player-personas section; absent when empty).
4. Frontend: `Persona` type; `PCPage.tsx` form inputs + column sections;
   `IncomingReview.tsx` `PERSONA_FIELDS`; `PCPage.test.tsx` cases.
5. `verify_templates.py`, `make check-py` subset, vitest for touched suites.

## 2. #67 — PC revision history

1. `store/pcs.py`: `_history_dir(root, pid, vid)`, `_snapshot(root, pid, vid,
   old_text)`, retention `HISTORY_KEEP = 20`; `update_version` snapshots when
   text changes; `list_revisions`, `read_revision`, `restore_revision`,
   `PCRevisionNotFound`; `delete_version` removes the version's history dir.
   All writes via `atomic.write_text`; deletes via `unlink`/`rmtree` on paths
   under the PC dir.
2. Routes: world twins in `routes/worlds.py`, campaign twins in
   `routes/campaigns.py` (campaign root for reads; lock + `ensure_actor_writable`
   + name-uniqueness for restore).
3. Tests: store (snapshot on change only, retention, `dir_hash`/`snapshot`
   unchanged, delete_version clears, restore round-trip + undoable, unsafe rid
   404) and routes for both scopes (campaign restore leaves world file bytes
   unchanged; inherited PC lists `[]`).
4. Frontend: `api.listPCRevisions / readPCRevision / restorePCRevision`;
   `PCRevision` type; PCPage History column section + preview + restore;
   tests.

## 3. #38 — World profile

1. `store/worlds/lifecycle.py`: `create_world(..., genre, tone, themes,
   description)`, `update_world`, `rename_world` → `update_world`; shared
   `_clean_line`/`_clean_themes`. `store/worlds/read.py`: `world_profile`,
   `read_world` meta `themes` list, row `genre`. Export from `__init__`.
2. `routes/models.py`: `WorldCreate`, `WorldUpdate`; `routes/worlds.py`
   `post_world`/`put_world`.
3. Prompt: `templates/scene/sections/world_overview.j2`; `SECTIONS` entry
   after `card_system_prompts`; data key `world_overview` in `_assemble`
   (read via `campaigns_read.world_root_of` + `world.md`, empty on any read
   failure); `verify_templates.py` gather + order mirror.
4. Tests: `test_worlds_store.py` (create/update/profile; legacy world empty;
   folding), route tests, fork carries profile, context section empty vs
   populated, frozen campaign unchanged.
5. Frontend: `WorldMeta`/`WorldSummary` types + `createWorld(name, details?)`
   + `updateWorld`; `WorldsView` details disclosure + genre on cards;
   `WorldOverview` About view/edit; tests.

## Finish

`make check` (lint ratchets → `make baseline` if counts move), the review gate
against the diff, the spec-conformance gate, push, PR.
