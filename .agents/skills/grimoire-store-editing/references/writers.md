# Resolve the library and choose writers

## Find the library

From the repository root, ask the resolver rather than assuming `~/.grimoire`:

```sh
PYTHONPATH=backend/src backend/.venv/bin/python -m grimoire.where # macOS/Linux
PYTHONPATH=backend/src backend/.venv/Scripts/python.exe -m grimoire.where # Windows Git Bash
$env:PYTHONPATH="backend/src"; backend\.venv\Scripts\python.exe -m grimoire.where # PowerShell
```

For a worktree, select the shared interpreter using
[CONTRIBUTING.md's worktree rule](../../../../CONTRIBUTING.md#four-things-that-will-bite-you).
Confirm record IDs from the library, and inspect its existing Git status if
it is versioned. Preserve changes somebody else already made.

## Prefer store writers

Use `grimoire.store`; the [integration guide](../../world-card-integration/references/writers.md#work-through-the-store-not-the-filesystem)
locates the existing operations. Keep the target campaign idle for the entire
external batch, including its detached runs, even when using those writers.
Closing a tab does not end generation; use the app's guarded routes instead
if quiescence cannot be maintained. See [process exclusion](../../../../docs/store-guarantees.md#a-second-process-on-the-same-store)
and [detached runs](../../grimoire-development/references/backend/runs.md#detached-runs-a-turn-outlives-the-request-that-asked-for-it).

Choose embedding policy before entering `cache_sync.collecting`: it syncs on
both successful and exceptional exit. For no-spend work or a large batch that
needs a later preview, pass `embed=False`. Deletions need explicit `deleted=`.
With the example IDs confirmed, one local-only batch is:

```python
from grimoire.store import cache_sync, entities, paths, voice_anchors

root = paths.home() / "worlds" / "realm"
with cache_sync.collecting(embed=False, deleted=["worlds/realm/lore/tidewatch.md"]) as collected:
    voice_anchors.write(root, "seraphine", "Short sentences; a quiet, deliberate voice.")
    entities.delete_entity(root, "lore", "tidewatch")
assert collected.report is not None
print(collected.report.as_dict())
```

Check affected references before deleting or moving records. A display rename
keeps the record's ID; supported lifecycle operations own moves and
reclassifications. A cache path report does not repair those references.
Use `image_descriptions` and `image_subjects` for art metadata, rather than
editing image-object files; read the [shared metadata guarantees](../../../../docs/store-guarantees.md#shared-metadata-descriptions-and-subjects).
If a direct text edit is needed, preserve existing line endings and keep
competing writers off the target. Record partial edits before retrying a failure.

## Leave controls alone

Do not hand-edit `.cache/`, backups, logs, usage, provider credentials or a
campaign's `revision.txt`. Sync updates the write token itself.
Do not alter records to imitate a cached answer, or delete the compiled folder
to repair one: its purge marker and deletion intents support recovery across
devices. Report a cache fault and consult the [compiled-cache guarantees](../../../../docs/store-guarantees.md#the-compiled-cache).
This procedure provides no cache-repair or deletion-control operation.

For interpreter selection and shell syntax, read [Python commands](python.md).
