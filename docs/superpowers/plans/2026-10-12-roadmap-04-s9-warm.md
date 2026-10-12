# Roadmap 04 S9 — Post-turn warm and `warm_paths`

> Executed inline; plan gate by substitute review, the PR's `@codex review`
> standing in for the CLI gates.

**Goal:** 04-C2a in full (spec 04 section 5).

## Tasks

1. `store/overview/warm.py`:
   - `warm_scene(cid, sid)`: for a registered `scene_turns` kind and an
     available layer, reads the scene file back, computes the byte-fed count
     with the players the shell will ask with (`cast.player_names`), and
     stores it with `Batch.store_written` — artifacts only, never a `sources`
     row. Never raises (a Debug `overview_warm` row names the error type).
   - `warm_paths(paths)`: the 5.3 table (`world.md`, `campaign.md`, a
     `safe_id` scene, the five continuity files; anything else `"none"`),
     instances coalesced, outcomes `warmed` / `none` (no persisted kind reads
     it, or nothing is persisted: unregistered or cache off) / `failed`.
     Continuity is warmed through `derive`, because an absent ledger is keyed
     as the explicit absent sentinel and a written input is bytes. Nothing
     calls it until 05 registers it.
2. `routes/streaming._fire_follow_up(after_turn, box, cid, sid)`: after the
   follow-ups, gated on the turn having written (`box.at`), the warm runs on
   a worker thread (`anyio.to_thread.run_sync`), holding no lock or key, its
   failures swallowed. Both callers pass `cid, sid`: `_fence_stream`'s three
   exits and `character_turns`' per-response chain (the format-2 chat path).

## Tests (`tests/test_overview_warm.py`)

The shell after a real chat turn records `compiled.hit.overview.scene_turns`
and `transcript.parse == 0`; a raising compute, a failed player read, the
cache off and a missing scene each leave the turn's transcript unchanged and
the next shell read correct; `warm_scene` never raises and stages no source;
`warm_paths` maps each path class, coalesces the five continuity paths into
one warm, reports `none` for entity, character and non-`safe_id` paths, and
what it warmed is a compiled hit after a restart; a raising warm is `failed`,
the cache off is `none`. `test_group_play_turns.py`'s stub of
`_fire_follow_up` takes the two new arguments.
