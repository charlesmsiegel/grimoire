# A failed apply left library changes

The CLI checks the whole resolved library Git repository, including campaign
files affected by a reclassification. A dirty tree blocks the next apply.

1. Keep proposals and output files. Inspect the result and NUL-delimited Git
   status against the clean starting state.
2. Identify exactly which files this attempt wrote and what already landed.
   Preserve unrelated work and every untracked source file.
3. Prefer reconciling valid partial writes. If rollback is necessary, prepare
   an explicit file list and review its diff before restoring those exact files.
   Deleting private untracked content requires the user's authorization for
   those named paths. A generic request to populate a World does not grant it.
4. Verify the tree and completed work before retrying. Do not use repository-wide
   checkout, reset or clean commands, or ask an agent to make the library clean.

Source: `cmd_run` in
[populate_world_content.py](../../scripts/populate_world_content.py).
