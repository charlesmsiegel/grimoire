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

1. **Setup:** If the user hasn't already told you which campaign (e.g. "continue campaign X" or
   "start a new one called Y"), don't assume — ask. List existing campaigns first (each
   `<grimoire home>/campaigns/*/campaign.md` has `name`/`world` in its frontmatter; CLAUDE.md
   documents how the home directory resolves), then use the **AskUserQuestion** tool to offer:
   continue one of the existing campaigns (call out any already on the target World by name), or
   start a fresh one (you'll need a name). Once that's settled:
   `backend/.venv/Scripts/python.exe backend/scripts/ingest_scene.py setup --world <world-id> --name "<Campaign Name>"`
   This is idempotent — it finds-or-creates by exact name+world match, so re-running it for an
   existing campaign is always safe. Prints the campaign id — use it for every following step.

2. **Per source file, in order** (state is cumulative — files, and scenes within them, must be
   *ingested* (the `ingest` CLI call) in strict story order — see step 3 for how the *rewriting*
   work itself parallelizes across subagents):

   a. Read forward through the raw transcript. An explicit `<!-- new scene -->` HTML comment (or
      similar authorial marker) is a hard scene break. Otherwise, judge breaks yourself on
      location change, a hard time skip, or a POV shift — the same signals a human GM would use.
      Strip anything that isn't in-fiction content: session-start briefings ("read the project
      documents..."), and session-end commands (`/updateskill`, skill-creator invocations,
      "write a summary of this conversation" — these show up at the very end of a file and mark
      where real content stops; grep for them so you don't ingest them as a scene).

   b. Rewrite each turn into grimoire's marker grammar as you go: `**Speaker:** content`, blank
      line between messages. The raw `## Claude` turn mixes narration with every present NPC's
      dialogue — split out `**<Name>:**` for any line that's unambiguously one character acting
      or speaking; leave true omniscient narration under `**Grimoire:**` (speaker `None` in the
      JSON below). The raw `## User` turn is the player character's first-person lines — tag it
      `role: "user", speaker: "<PC name>"`. Keep the source's own typos/quirks verbatim — they're
      the player's voice, not errors to correct.

   c. For each finished scene, note which characters/locations it needs. Anything not already in
      the World or a prior scene's `new_characters`/`new_locations` goes in this scene's own
      `new_characters`/`new_locations` list — never invent an id yourself, the tool derives one
      from the name (`slugify`). **Compute that slug yourself and use it verbatim in `location`**
      (lowercase, spaces→hyphens, articles like "The" are *not* stripped — `"The Western Road"`
      slugifies to `the-western-road`, not `western-road`). See Common Mistakes for what happens
      when this drifts.

   d. Write the scene to a JSON file and ingest it:
      ```json
      {
        "key": "file1-scene03",
        "title": "The Reckoning",
        "date": "1818-05-15",
        "location": "winterbourne-manor",
        "new_characters": [{"name": "Cassian", "personality": "wary, precise"}],
        "new_locations": [{"name": "Thornfield Manor", "notes": "Seat of Corvin."}],
        "characters": [{"kind": "pcs", "id": "julian"}, {"kind": "characters", "id": "cassian"}],
        "turns": [
          {"role": "assistant", "speaker": null, "content": "*The study is silent.*"},
          {"role": "assistant", "speaker": "Cassian", "content": "\"I didn't ask to come here.\""},
          {"role": "user", "speaker": "Julian", "content": "\"Neither did I, once.\""}
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
      same scene rather than duplicating it. A scene that absorbed but whose edits did not all
      land is recorded `incomplete`, with `failures` (why each row was refused) and `pending`
      (the rows worth replaying); re-issuing `ingest` replays exactly those rows — no second LLM
      call, no second copy of the scene's timeline events, and no re-application of the beats
      that already landed. **`ingest` exits nonzero for an incomplete scene** — do not go on to
      the next scene, because every later scene is absorbed against the state this one wrote.

      A `failures` entry with `"kind": "conflict"` is **not** replayable and gets no `pending`
      row: the record it was staged against has since moved, so re-running produces the same
      verdict forever. Reconcile it by hand (the reason names the record), then close the key
      with `ingest_scene.py resolve --campaign <cid> --key <key>`, which marks it `done` while
      keeping its `sid` and records what you reconciled under `reconciled`. **Do not delete the
      key** — a deleted key is an unknown scene, so the next run rebuilds and re-absorbs it,
      duplicating the scene, its timeline events and every beat that already landed. Likewise, **do not rename or delete a scene with an
      unfinished manifest entry** — a rename changes the scene's id, the manifest is not one of
      the stores `scene_refs.repoint` follows, and the new id is not recoverable, so `ingest`
      refuses to resume that key rather than write beats against an id no scene has. It
      records that refusal on the entry — `incomplete` with a `detail` naming the missing id —
      so the key can be closed with `resolve` once you have reconciled it; it is not stuck.
      Residual risk: if the process dies *inside* the apply
      sequence, or between it finishing and the manifest being written, a retry re-absorbs and
      re-applies that one scene — rare, and duplicated beats under one scene id are the
      consequence. The **timeline** is the exception, because it is the one part of that
      sequence that appends rather than being keyed by scene id: the extraction is recorded
      before anything is written, along with the timeline as it stood at that moment, so a
      resume can tell whether its own events were filed even when the scene before it filed an
      identical batch. What it cannot tell is a *concurrent* web absorb that appended in the
      meantime — so avoid absorbing scenes in the app while a batch ingest is running.

3. **Parallelize the rewriting with subagents — don't do it all yourself.** Segmentation and
   rewriting is real work (reading + judgment + producing verbatim prose), but only the final
   `ingest` calls have to run in sequence. Recommended pattern for a source file of any real
   length:

   a. Grep the whole file for `<!--` — this surfaces every explicit author marker in one pass
      (scene breaks, "let's skip forward to...", "let's jump to the next day...", and the
      session-end meta-commands to exclude). These give you free, high-confidence chunk
      boundaries without reading the whole file yourself first.

   b. Divide the file into a handful of chunks along those markers (a few thousand lines each is
      fine — a chunk usually contains 2-5 scenes). Dispatch one subagent per chunk, in parallel,
      each told to: read its whole range, find scene boundaries *within* the chunk itself (the
      markers plus its own judgment), and write one JSON file per scene it finds, using a
      chunk-scoped key/filename (`file1-c2s1`, `file1-c2s2`, ... — "c2" = chunk 2, numbered in
      story order within the chunk). Give each subagent the running cast of already-established
      character/location ids so it doesn't re-invent them.

   c. If you don't yet know exactly where an earlier chunk (or scene) stopped, don't guess a
      chunk's start line from a fixed offset — either wait for that agent's actual reported
      stopping line, or give the next chunk a self-bounding remit too ("if the start of your
      range still belongs to a scene already in progress, skip forward to the next fresh
      break"). A guessed boundary either duplicates or silently drops story content.

   d. Not every source file has clean markers — some are pure omniscient-narration exports with
      no `<!-- ... -->` comments at all. In that case, skip straight to soft, roughly-equal-sized
      chunks (a few thousand lines) with a self-bounding remit on *both* ends ("stop at a natural
      break near your target line, report the exact line"), and dispatch them one or two at a
      time rather than all at once, since you can't pre-verify non-overlapping ranges without
      an anchor. Don't worry if a chunk runs well past its soft target — a long chunk that
      finishes cleanly beats a short one cut off mid-scene.

   e. When a chunk agent reports it stopped "mid-conversation" (as opposed to at a clean scene
      end), treat that as a flag, not a clean handoff: check the raw source around that line
      yourself before dispatching the next chunk. A single raw turn cannot be split across two
      scenes — if the previous agent's last message ends partway through what is actually one
      continuous `## Claude` turn, the next chunk's first scene will legitimately re-include that
      whole turn (correctly, since it can't take half a turn either). Trim the duplicated prefix
      out of the second copy's `content` (find the sentence where the previous scene's version
      cut off, keep only what comes after it) before ingesting — don't let both copies through,
      the same content gets absorbed twice.

   f. Once chunks report back, ingest every scene from every chunk in strict global story order
      (chunk order, then each chunk's own `s1, s2, s3...` order) — never reorder, never run two
      `ingest` calls out of order, even though the rewriting happened out of order.

4. **After each source file finishes**, if an old skill archive was provided, compare the
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
  in `new_characters`/`new_locations` (lowercase, hyphenated, articles kept — `"The Western Road"`
  → `the-western-road`) — later scenes' `characters` references will silently fail to line up, and
  a mismatched scene `location` field is worse: `build_scene` creates the location, creates the
  scene, and stamps its date *before* it calls `set_location` — so a bad location id crashes with
  `EntityNotFound` after those steps already ran, orphaning a scene file (no messages, no cast, no
  manifest entry, since the crash happens before the resumability record is written). Recovery: check
  the orphaned scene has no entries in `appearances.json`/`chronicle.json` (it won't, this early),
  delete the stray `<campaign>/scenes/NNN--....md` file, fix the JSON, and re-run `ingest` — do not
  leave the orphan or try to hand-patch it in place.
- Running scenes out of order, or re-running an already-`"done"` key expecting it to refresh —
  `ingest_one_scene` treats "done" as final; delete the manifest entry first if a scene genuinely
  needs redoing — and know what that means: the key becomes unknown, so the run rebuilds the
  scene from the JSON and absorbs it again. That is right only when you have also deleted the
  scene it built; on top of an existing one it duplicates the scene, its timeline events and
  every beat. To close an `incomplete` key without redoing it, use `resolve` (above), not
  deletion. The manifest lives at `<campaign_root>/ingest_manifest.json`.
