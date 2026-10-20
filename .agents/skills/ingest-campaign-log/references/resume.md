# Resume, reconcile and verify

   e. `key` must be unique and stable across a whole ingestion run (e.g. `"<logfile>-scene<NN>"`).
      Re-running `ingest` with the same `--campaign` and the same `key` is a no-op if that scene
      already completed — check with:
      `$PY backend/scripts/ingest_scene.py status --campaign <cid>`
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
      identical batch. That record is a *position* — the offset its own append would start at —
      so a batch belonging to any other scene lies outside the question. The one case it cannot
      answer is a concurrent web absorb that files a byte-identical batch at that same offset
      while the ingest is down, which no comparison of the file's contents can distinguish from
      the ingest's own append. So avoid absorbing scenes in the app while a batch ingest is
      running.



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

The ingest script already performs write collection and sync. Send additional outside-script edits to [store-editing sync](../../grimoire-store-editing/references/sync/batch.md#sync-a-coherent-batch); do not repeat a completed ingestion to refresh caches.
