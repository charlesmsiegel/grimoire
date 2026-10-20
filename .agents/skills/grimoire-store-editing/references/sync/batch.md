# Synchronize a batch

## Sync a coherent batch

A collecting block already syncs its writes once. For editor changes or work
outside that block, gather changed/new paths and intended deletions, then sync
once after the batch. For a Git library, obtain unquoted, uncollapsed paths with:

```sh
git -C /path/to/library status --porcelain=v1 -z --untracked-files=all
```

Parse the NUL-delimited records. An unstaged rename is a delete plus a new
path; use `--deleted` for the former. `--renamed OLD=NEW` can report a path
change already made safely, but performs no authoritative rename itself.
For example, after the edits, run one of these equivalent local-only commands:

```sh
PYTHONPATH=backend/src backend/.venv/bin/python -m grimoire.cache sync worlds/realm/characters/seraphine/voice_anchor.md --deleted worlds/realm/lore/tidewatch.md --no-embed # macOS/Linux
PYTHONPATH=backend/src backend/.venv/Scripts/python.exe -m grimoire.cache sync worlds/realm/characters/seraphine/voice_anchor.md --deleted worlds/realm/lore/tidewatch.md --no-embed # Windows Git Bash
$env:PYTHONPATH="backend/src"; backend\.venv\Scripts\python.exe -m grimoire.cache sync worlds/realm/characters/seraphine/voice_anchor.md --deleted worlds/realm/lore/tidewatch.md --no-embed # PowerShell
```

Use `--campaign` or `--world` for broad passes. After local edits, preview any
large embedding warm-up with `--dry-run`, including before `--all`, and inspect
how many texts would be sent. Keep `--no-embed` on all invocations when spending
is ruled out; only enable embedding after that preview and within the user's
request. Deferred vectors can be rebuilt by a later read. `--quiet` prints counts;
`--json` gives the full structured report.
