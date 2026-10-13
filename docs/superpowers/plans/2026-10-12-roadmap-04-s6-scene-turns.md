# Roadmap 04 S6 — `scene_turns`, in-process, on the shell

> Executed inline; plan gate by substitute review, the PR's `@codex review`
> standing in for the CLI gates.

**Goal:** 04-C1b's in-process `overview.scene_turns` and the shell's
`open[].turns` reading it (spec 04 sections 3.6, 4.3).

## Tasks

1. `overview.read.scene_turns(cid, sid)`: `SceneNotFound` for a bad or missing
   scene; `players` = sorted `cast.player_names(cid, sid)`; `memo_stamped` on
   `("overview.scene_turns", <scene path>, players)` in `_OVERVIEW_POOL`,
   stamped on the scene file before the read. The compute is the live path,
   `kinds.scene_turns_live` (`read_scene` + `_model_blocks`), so the
   algorithm is unchanged; a `UnicodeDecodeError` is remembered as `None`
   (a property of the bytes), any other failure raises and is not
   remembered.
2. `kinds.scene_turns(data, players)`: the byte-fed compute S8's persisted
   kind runs (strict decode, universal newlines, the same parse); held equal
   to the live path here.
3. The shell's `_scene_turns` calls it inside today's `try` with today's
   `None` mapping.

## Tests (`tests/test_overview_scene_turns.py`)

A second shell read records `transcript.parse == 0`; an append is counted; a
player cast change misses on `players`; a failed player read is `null`; a
missing scene raises; byte-fed equals live for LF, CRLF and a bad byte.
`test_shell_route.py` passes unchanged.
