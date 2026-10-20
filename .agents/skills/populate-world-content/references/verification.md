# Verify results and avoid repeated work

## Common mistakes

- **Leaving a just-finished world in the `WORLDS` array** when adding a new call after the main
  loop (an era pass, a targeted recovery pass). The loop re-runs that whole world's
  propose/merge/apply from scratch and repeats already-completed work. Reset the world list to empty before appending anything else.
- **Trusting a task-notification's inline result text as complete.** It truncates long results
  silently. Always read the actual output file (or the run's `journal.jsonl`) before concluding
  "nothing happened" — a `grep` that returns zero matches on a truncated file is not evidence of
  absence.
- **Assuming "0 items imported" means the source data is missing.** Spot-check the actual
  character JSON (`first_mes`/`alternate_greetings` lengths) before concluding a re-download from
  the card's origin (chub.ai, etc.) is needed — in the one case this came up, the data was fully
  present locally and the zero-import was a pipeline defect, not a missing-source problem.
- **Believing "the dirty file list hasn't changed in 30 minutes" means an agent is stuck**,
  without checking wall-clock time against the most recently modified transcript file first. A
  large single chunk can legitimately run long while still actively working; only intervene once
  the transcript itself has gone stale.
- Verify every "done" claim against **real** `git log --oneline` / `git status --short` in
  the resolved library root — never report success from a cached/remembered result alone.

Once the content pass has written its records, follow [store-editing sync](../../grimoire-store-editing/references/sync/batch.md#sync-a-coherent-batch).
