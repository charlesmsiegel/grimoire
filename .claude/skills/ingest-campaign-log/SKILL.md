---
name: ingest-campaign-log
description: Use when turning a raw Claude.ai RP transcript export (and, optionally, an old campaign-skill archive with hand-authored state) into a real grimoire Campaign.
---

# Ingesting a campaign log into grimoire

Turns raw session transcripts into a real grimoire Campaign by driving the app's own absorb
pipeline scene by scene, so character state, relationships, and plot threads accumulate exactly
as they would during live play. Scene segmentation and speaker attribution is **your** judgment
call while reading — this skill does not delegate that to another LLM call.

## Inputs

- A directory of raw logs (Claude.ai exports: `## User` / `## Claude` turns).
- Optionally, an old campaign-skill `.skill` archive (a zip) — unzip it and read `SKILL.md` +
  `state/active_characters/*.md` for roster/premise context and, later, for verification. Never
  copy its `state/` prose directly into grimoire — every fact must come from re-running absorb on
  the actual transcript.
- A target grimoire World id, and a campaign name.
- Any player character (PC) referenced via `{"kind": "pcs", "id": "..."}` in a scene's `characters`
  field must already exist in the target World — PCs are copied to the campaign only at creation
  time. Add missing PCs to the World via the Worlds editor before running `setup`.

## Workflow

1. **Setup:**
   `backend/.venv/Scripts/python.exe backend/scripts/ingest_scene.py setup --world <world-id> --name "<Campaign Name>"`
   Prints the campaign id — use it for every following step.

2. **Per source file, in order** (state is cumulative — files and scenes within them must be
   ingested in story order, never in parallel):

   a. Read forward through the raw transcript. An explicit `<!-- new scene -->` HTML comment (or
      similar authorial marker) is a hard scene break. Otherwise, judge breaks yourself on
      location change, a hard time skip, or a POV shift — the same signals a human GM would use.
      Strip anything that isn't in-fiction content: session-start briefings ("read the project
      documents..."), and session-end commands (`/updateskill`, skill-creator invocations).

   b. Rewrite each turn into grimoire's marker grammar as you go: `**Speaker:** content`, blank
      line between messages. The raw `## Claude` turn mixes narration with every present NPC's
      dialogue — split out `**<Name>:**` for any line that's unambiguously one character acting
      or speaking; leave true omniscient narration under `**Grimoire:**` (speaker `None` in the
      JSON below). The raw `## User` turn is the player character's first-person lines — tag it
      `role: "user", speaker: "<PC name>"`.

   c. For each finished scene, note which characters/locations it needs. Anything not already in
      the World or a prior scene's `new_characters`/`new_locations` goes in this scene's own
      `new_characters`/`new_locations` list — never invent an id yourself, the tool derives one
      from the name (`slugify`).

   d. Write the scene to a JSON file and ingest it:
      ```json
      {
        "key": "file1-scene03",
        "title": "The Reckoning",
        "date": "1818-05-15",
        "location": "winterbourne-manor",
        "new_characters": [{"name": "cassian", "personality": "wary, precise"}],
        "new_locations": [{"name": "Thornfield Manor", "notes": "Seat of corvin."}],
        "characters": [{"kind": "pcs", "id": "julian"}, {"kind": "characters", "id": "cassian"}],
        "turns": [
          {"role": "assistant", "speaker": null, "content": "*The study is silent.*"},
          {"role": "assistant", "speaker": "cassian", "content": "\"I didn't ask to come here.\""},
          {"role": "user", "speaker": "julian", "content": "\"Neither did I, once.\""}
        ]
      }
      ```
      ```bash
      backend/.venv/Scripts/python.exe backend/scripts/ingest_scene.py ingest --campaign <cid> --input scene.json
      ```
      This creates the scene, seats the cast, sets location/date, runs the real absorb LLM call
      against grimoire's configured OpenRouter key/model, and auto-applies every edit it proposes.
      It's a real API call and real spend — there is no dry-run mode.

   e. `key` must be unique and stable across a whole ingestion run (e.g. `"<logfile>-scene<NN>"`).
      Re-running `ingest` with the same `--campaign` and the same `key` is a no-op if that scene
      already completed — check with:
      `backend/.venv/Scripts/python.exe backend/scripts/ingest_scene.py status --campaign <cid>`
      A failed or interrupted run resumes cleanly: the scene is only ever created once per `key`
      (recorded `in_progress` with its `sid` right after creation, before the LLM call), so
      fixing the problem and re-issuing `ingest` for the scene that failed resumes work on that
      same scene rather than duplicating it. Residual risk: if the process dies between the
      absorb call finishing and the manifest being marked `done`, a retry re-absorbs and
      re-applies that one scene — rare, and applying the same edits twice is the only
      consequence.

3. **After each source file finishes**, if an old skill archive was provided, compare the
   resulting campaign's state/relationships/plot against its hand-authored `state/` files (prose
   cross-reference — this is a judgment call about whether the story came out right, not a
   mechanical diff) and report anything that looks meaningfully off before moving to the next file.

## Common mistakes

- Treating one raw log file as one scene, or one "session." These exports are Claude.ai
  conversation continuations, not clean session/scene boundaries — always read for actual scene
  breaks.
- Feeding a whole `## Claude` turn through as a single `**Grimoire:**` block when it clearly
  contains one or more NPCs' distinct dialogue — split it, per Workflow step 2b.
- Inventing a character/location id instead of using the exact `slugify` of the `name` you gave it
  in `new_characters`/`new_locations` (lowercase, hyphenated) — `resolve_version` and later scenes'
  `characters` references will silently fail to line up otherwise.
- Running scenes out of order, or re-running an already-`"done"` key expecting it to refresh —
  `ingest_one_scene` treats "done" as final; delete the manifest entry first if a scene genuinely
  needs redoing (this also un-applies nothing — you'd be re-applying on top of the old state).
  The manifest lives at `<campaign_root>/ingest_manifest.json`.
