# Campaign log ingestion — design

**Date:** 2026-07-05
**Status:** Approved

## Goal

Turn a folder of raw Claude.ai RP transcripts (plus, optionally, an old
"campaign skill" `.skill` archive holding hand-authored state) into a real
grimoire Campaign — scenes, chronicle, character state, relationships, plot
threads — by driving grimoire's existing absorb pipeline scene by scene.

Build this as a **reusable project skill** plus a small helper script, not a
one-off script for a single campaign: there are several other `.skill`
archives under the user's `OLD/skills/` folder for other campaigns that are
candidates for the same treatment later.

First real target: `ashgrove-campaign-silver-oath.skill` +
`OLD/logs/claude-exports-20260516-064452/Silver Oath/Manor Vows {1..8}.md`,
imported into a new campaign `silver-oath` under World `ashgrove`.

## Decisions (from brainstorming)

- **New campaign, not a merge.** The existing `manor-vows` campaign (World
  `ashgrove`, created today) is a separate, unrelated fresh playthrough of the
  same premise and is left untouched.
- **Faithful re-derivation.** Every scene is genuinely run through grimoire's
  own absorb LLM call in order (not seeded directly from the old skill's
  hand-authored `state/` files) — state accumulates scene by scene exactly as
  it would during live play.
- **Missing characters are campaign-local.** Cassian, Dorian, Tobias (and any
  others the logs introduce) are created under the campaign root
  (`characters.create_character(campaign_root, ...)`), never written back to
  the shared `ashgrove` World.
- **Missing locations, minimal.** Only create a new location record where the
  schema actually needs a scene-level setting the World doesn't have (e.g.
  `thornfield-manor`, since it's the seat of most of the back half of this arc).
  Room-level detail (a reading room, the stables) stays in scene prose, same
  as the existing `manor-vows` scenes.
- **No canonical dates in the old setting material.** Anchor Day 1 =
  **1818-05-15** (late spring), matching the "late spring, Year 1 of the
  Winterbourne marriage" framing and the same 1818 Regency year `manor-vows`
  already uses for this World. Later in-transcript day counts ("Day 17") are
  computed as offsets from this anchor.
- **Segmentation and rewriting is agent judgment, not another LLM call.** The
  raw logs are two-bucket exports (`## User` = Julian in first person, `##
  Claude` = narration + every NPC's dialogue mashed into one turn, with only
  sparse/no explicit scene-break markers in the early files). Reformatting
  each Claude turn into grimoire's `**Speaker:**` marker grammar — splitting
  out `**Winifred:**`, `**Marisol:**`, etc. where a line is unambiguously one
  character's dialogue/action, leaving true omniscient narration under
  `**Grimoire:**` — and deciding where a scene actually ends, is done by
  whichever agent is running the skill, reading forward through the source
  file. This is *not* delegated to a separate OpenRouter call.
- **The absorb step itself does call the LLM** — that's grimoire's real,
  existing extraction pipeline (`store.absorb`), using the app's configured
  OpenRouter key/model. This is the one part of the process that must run
  strictly in scene order, since each call reads the campaign's
  now-accumulated state.
- **Auto-apply every materialized edit.** No human-in-the-loop review step —
  bulk import accepts 100% of what `absorb.materialize` proposes, mirroring
  what clicking "accept all" in the live UI would do.
- **Resumable, not tolerant.** A progress manifest tracks which scenes are
  fully done (created, cast, absorbed, applied). Any failure — bad LLM output,
  an unresolved character/location reference the segmentation step missed —
  halts the run at that scene rather than silently dropping state the way
  `absorb.materialize`'s tolerant "unknown target → skip" behavior does for
  live play.
- **Verify against the old skill's authored state where one exists.** After
  each source log finishes, diff the resulting campaign's `playstate` /
  `relationships` / `plot` against the corresponding hand-written
  `state/active_characters/*.md`, `relationship_web.md`, `plot_roadmap.md`
  (prose cross-reference, not a mechanical field diff) and report anything
  that looks meaningfully off before moving to the next log file.
- **First run scope:** `Manor Vows 1.md` only (the largest file — spans the
  Marisol murder scene through "Day 17" post-wedding, likely dozens of implicit
  scenes). Stop after it for review before touching files 2-8.

## 1. The skill

`ingest-campaign-log` (project skill, `.claude/skills/`). Invoked with:
logs directory, optional old `.skill` archive path, target World id, campaign
name/slug.

Skill instructions walk the agent through:

1. **Setup** — create the campaign from the named World if it doesn't exist
   yet; if a `.skill` archive was given, unzip it and read `SKILL.md` +
   `state/` for character-roster and premise context (used for actor
   resolution and the later verification step, never as a direct data
   source).
2. **Per source file, in order:**
   a. Read forward through the raw transcript. Rewrite each turn into the
      `**Speaker:**` marker grammar, stripping OOC/meta instructions
      (session-start briefings, `/updateskill`-style commands). Decide scene
      boundaries as this reading happens — explicit `<!-- new scene -->`
      markers are hard breaks; otherwise judge on location/time/POV shifts.
   b. Whenever a scene needs a character or location not yet in the campaign,
      resolve it against the World first, then create it campaign-local if
      genuinely new (per the decisions above) — *before* that scene runs.
   c. For each finished scene, call the helper script (below) to create it,
      write the rewritten transcript, seat the cast, set location/date, run
      absorb, and apply every edit.
   d. Update the progress manifest after each scene completes.
3. **After the file finishes**, cross-check against the old skill's
   hand-authored state (if provided) and report findings.

## 2. Helper script

A single script (e.g. `backend/scripts/ingest_scene.py`) invoked once per
finished scene, wrapping the mechanical store calls so the skill isn't
hand-assembling a dozen individual calls each time:

- `create` — `scenes.create_scene` with title + suggested date.
- `append-turn` — `scenes.append_message` for one rewritten turn (role,
  speaker, content).
- `seat-cast` — resolve/create any needed characters, then seat them
  (equivalent of the cast-batch endpoint).
- `set-scene` — `scenes.set_location` + `scenes.set_time`.
- `absorb` — runs the real pipeline: `absorb.build_prompt` with
  `state_snapshot` / `relationships_snapshot` / `plot_snapshot`, calls
  `OpenRouterClient.complete` with the configured model/key, then
  `absorb.parse_output` + `absorb.materialize`.
- `apply` — `absorb.apply_edits` (all of them) + `chronicle.absorb` +
  `chronicle.append_timeline` + `scenes.mark_absorbed` + dossier refresh for
  present NPCs — matching what `PUT .../chronicle` does today.
- `status` / manifest read-back for resuming.

This is glue over existing `grimoire.store` functions and the existing
`OpenRouterClient` — no new backend business logic.

## 3. Error handling

- Any step failing (LLM error, unparseable absorb output, unresolved actor)
  stops the run at the current scene. The manifest records the last
  successfully-completed scene; nothing partial is left applied.
- The helper script is idempotent per scene id — re-running after a fix
  resumes from the manifest rather than repeating completed scenes' LLM
  calls.

## 4. Out of scope for this pass

- Files 2-8 of "Silver Oath" (pending review of file 1's results).
- Any other `.skill` archive (for other campaigns) — the skill is
  built generally, but only exercised against this one campaign for now.
- Image/asset handling from the old logs (none observed in the raw
  transcripts sampled so far; revisit if later files include embedded
  images).
