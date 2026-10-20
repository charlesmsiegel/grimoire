# Segmentation and rewriting

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
