# Richer new-scene opener context — design

**Date:** 2026-07-01
**Status:** designed

## Problem

The new-scene opener (`build_opener_messages` in `backend/src/grimoire/store/context.py`,
streamed by `POST /campaigns/{cid}/scenes/{sid}/opener`) sends the model a deliberately
minimal, "character-less" context: the `OPENER_INSTRUCTION`, the player personas, and
world-info activated by the prompt. It cannot see who is in the scene, where it is set,
when it happens, what plot threads are open, or what has happened so far — so the opening
narration it writes is blind to the campaign state that a normal in-scene turn sees.

## Goal

The opener should see the same context an in-progress scene turn sees, plus a fuller
recap of recent scenes. Concretely it must gain: plot threads, the current location /
setting, the characters in the scene (descriptions, state, relationships), the date, and
the story so far — with the story-so-far upgraded to the **full summaries of the last
five scenes** rather than compact one-liners.

## Approach

Rebuild `build_opener_messages` on top of `_assemble` — the existing normal-turn context
builder that already produces every labeled system section. Two small, additive
parameters extend `_assemble` to cover the opener's two differences from a normal turn.

### `_assemble(cid, sid, wi_seed="", full_recap=0)`

1. **`wi_seed: str = ""`** — a brand-new scene has no conversation history, so keyed
   world-info entries have nothing to activate against. The opener passes the prompt as
   `wi_seed`; it is folded into the `recent_text` used for world-info activation so prompt
   words still trigger keyed lore/locations. This preserves today's prompt-driven
   world-info behavior. `wi_seed` affects **only** world-info activation (the sole
   consumer of `recent_text`).

2. **`full_recap: int = 0`** — when `> 0`, the **Story so far** section uses each scene's
   full `summary` for the last `full_recap` scenes (one paragraph per scene, blank-line
   separated) instead of the compact `recap_depth` one-liners. The opener passes
   `OPENER_RECAP_DEPTH = 5`.

`_story_so_far` is refactored to `_story_so_far(cid, depth=None, full=False)`:

- `depth=None` → read `recap_depth` from config (current behavior); otherwise use `depth`.
- `full=False` → the current `- {one_line or summary}` bullet list.
- `full=True` → `{summary or one_line}` per scene as paragraphs.
- `depth <= 0` → `""`. The whole body stays wrapped in the existing tolerant
  `try/except` (a garbled chronicle/config omits the block, never crashes).

`_assemble` calls it as `_story_so_far(cid, depth=full_recap or None, full=bool(full_recap))`
so a normal turn (`full_recap=0`) is unchanged.

### Message shape the opener produces

- `system`: `OPENER_INSTRUCTION` + all assembled sections joined — global/NPC system
  prompts, character descriptions, character state, relationships, player personas,
  message examples, story-so-far (full, last 5), plot threads, Today (date), current
  setting (location), world info (prompt-seeded), off-scene cast.
- `user`: the opener prompt, token-substituted via `scene_substitutions(cid, sid)`.
- `system`: post-history instructions, if any NPC card defines them — kept last, right
  before generation, matching `build_messages` semantics.

**No conversation history is included.** The opener is definitionally for a scene with no
messages, so `_assemble`'s `history` is dropped from the opener output; world-info
activation relies solely on `wi_seed` (the prompt) plus owners of present cast.

`OPENER_INSTRUCTION` wording is unchanged — it already sets the scene in the second person
and forbids acting for the player, which still holds now that NPCs are present. Its
docstring is updated to drop the now-inaccurate "character-less" description.

## What changes for the user

The opening narration is now grounded in the full scene: it knows the cast, the setting,
the date, open plot threads, and a detailed recap of the last five scenes, matching the
context an ongoing turn already receives.

## Testing

Extend `test_build_opener_messages` in `backend/tests/test_context.py`:

- Keep: player persona present, `salt` lore activated by the prompt, ambient lore present,
  `{{user}}` substituted, the opener prompt appears as the `user` message (located by role,
  not by assuming it is the last message, since a post-history system message can follow).
- Add: seed an in-scene NPC, an open plot thread, a scene date, a current location, and a
  chronicle record with a distinct full `summary`; assert each renders in the system text
  (NPC description, plot thread line, date line, current-setting body, the full summary),
  and that the full summary is used rather than the one-liner.

## Non-goals

- No new configuration knob; `OPENER_RECAP_DEPTH` is a hardcoded module constant (5).
- No change to `context_sections` (the token-breakdown inspector), which is per-scene and
  prompt-less.
- No route-level guard forcing the opener onto empty scenes (out of scope).
