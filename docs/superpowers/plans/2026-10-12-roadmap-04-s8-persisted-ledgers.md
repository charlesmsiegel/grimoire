# Roadmap 04 S8 — Persisted `scene_turns` and `continuity_summary`

> Executed inline; plan gate by substitute review, the PR's `@codex review`
> standing in for the CLI gates.

**Goal:** 04-C1b and 04-C1c in full (spec 04 sections 3.3, 3.5–3.7, 4.4,
12.1, 12.3, 12.4). As in S7, both kinds are declared in `compiled.py` and
registered only on S10's measured win; the layer is complete either way.

## Tasks

1. `overview.read._turns` (the in-process miss under `scene_turns`): the
   scene file stamped first, then `persist.derive(OVERVIEW_SCENE_TURNS,
   [Input("scene", p)], {"players": [...]}, kinds.scene_turns, live)` where
   the live path is `read_scene` + `_model_blocks` (a decode failure is
   `None`, remembered; any other failure raises and is not).
2. `overview.read._continuity`: the root and each of the five files stamped
   first; `persist.derive(OVERVIEW_CONTINUITY, five absent-ok inputs, {},
   kinds.continuity(cid, files), live)`; the live path reads the five files'
   bytes (a failed read answers through the path readers and the value is
   not remembered); an absent file is vouched for by the root.
3. Content-determined failures are part of the value (a garbled ledger's
   `null` is cached under its bytes); failed reads never are
   (`InputUnavailable` -> live path, `overview.live_path.<kind>`).
4. The 4.4 chore table is now the registry of what is projected (`open-
   scenes`, `owed`, `continuity-overlaps`, `continuity-closures`) and what is
   live (every other chore), as `routes/todo.py`'s docstring says.

## Tests (`tests/test_overview_persisted.py`, kinds registered)

The spec's fixtures (CRLF and non-UTF-8 scenes, byte-identical `campaign.md`
in two directories, a scene with no title, a campaign with no ledgers, a
garbled `commitments.json`): every overview route identical cold, warm, after
a restart, with the database locked, and with the cache off. Warm after
restart: the shell parses no transcript (compiled hits) and global Todo
computes no continuity summary. An unrelated (lore) change misses no kind.
No closed scene's transcript is read in any state, and a closed scene file is
opened at most once (head-only). A forced `InputUnavailable` counts
`live_path` and stores nothing; a corrupt or absent database answers
identically; a content-determined `null` is served from the cache after a
restart.
