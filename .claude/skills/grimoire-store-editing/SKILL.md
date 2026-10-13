---
name: grimoire-store-editing
description: Use when creating, editing, renaming, moving or deleting records in a grimoire store (the library under GRIMOIRE_HOME) by any route other than the running app -- a script calling grimoire.store, a Python session, or an editor -- including voice anchors, image descriptions, lore and entity cleanup, tags, and bulk passes after another skill. Covers finding the store, what to write through and what never to touch, and the one `cache sync` call that brings search, recall and overview pages current afterwards. Not needed for edits made in the app's own UI or API.
---

# Editing a Grimoire store outside the app

Use this for library work performed by scripts, a Python session or an editor.
The records remain authoritative; sync warms derived work already in use.
A cold record is expected, and omitting warm-up only delays its next rebuild.
Edits through the app's UI or API already receive its write-through handling.

## Privacy

Library content can be discussed with its owner, but must stay out of this
public checkout, including names and statistics. Follow the project's
[privacy rules](../../../CLAUDE.md).
The examples below use existing placeholders, never a real library.

## Find the library

From the repository root, ask the resolver rather than assuming `~/.grimoire`:

```sh
PYTHONPATH=backend/src backend/.venv/bin/python -m grimoire.where # macOS/Linux
PYTHONPATH=backend/src backend/.venv/Scripts/python.exe -m grimoire.where # Windows Git Bash
$env:PYTHONPATH="backend/src"; backend\.venv\Scripts\python.exe -m grimoire.where # PowerShell
```

For a worktree, select the shared interpreter using
[CONTRIBUTING.md's worktree rule](../../../CONTRIBUTING.md#four-things-that-will-bite-you).
Confirm record IDs from the library, and inspect its existing Git status if
it is versioned. Preserve changes somebody else already made.

## Prefer store writers

Use `grimoire.store`; the [integration guide](../world-card-integration/SKILL.md#work-through-the-store-not-the-filesystem)
locates the existing operations. Keep the target campaign idle for the entire
external batch, including its detached runs, even when using those writers.
Closing a tab does not end generation; use the app's guarded routes instead
if quiescence cannot be maintained. See [process exclusion](../../../docs/store-guarantees.md#a-second-process-on-the-same-store)
and [detached runs](../../../CLAUDE.md#detached-runs-a-turn-outlives-the-request-that-asked-for-it).

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
editing image-object files; read the [shared metadata guarantees](../../../docs/store-guarantees.md#shared-metadata-descriptions-and-subjects).
If a direct text edit is needed, preserve existing line endings and keep
competing writers off the target. Record partial edits before retrying a failure.

## Leave controls alone

Do not hand-edit `.cache/`, backups, logs, usage, provider credentials or a
campaign's `revision.txt`. Sync updates the write token itself.
Do not alter records to imitate a cached answer, or delete the compiled folder
to repair one: its purge marker and deletion intents support recovery across
devices. Report a cache fault and consult the [compiled-cache guarantees](../../../docs/store-guarantees.md#the-compiled-cache).
This procedure provides no cache-repair or deletion-control operation.

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

## Read the result

Exit 0 means the sync completed; 1 means failed, refused, missing, unverified or
unfinished work, an early stop, or failed token updates. Exit 2 is invalid syntax;
3 means the library root could not be used. Read the entire report: successful
listed paths alone do not establish that the batch finished.

| Vocabulary | Names | Meaning |
|---|---|---|
| Path status | `refreshed`, `current` | The derived work is current. |
| Path status | `cold` | Nothing was hot for this record; this is expected. |
| Path status | `deleted` | An intended removal was handled. |
| Path status | `missing` | Check the path or declare the intended deletion. |
| Path status | `refused` | Resolve the refusal reason before retrying. |
| Path status | `failed` | A reader or rebuild failed; inspect the exception class. |
| Path status | `verify_failed` | Cached and fresh derivations disagreed; report it. |
| Path status | `unsynced` | Work is unfinished; inspect its reason. |
| Refusal reason | `outside_store`, `link`, `not_syncable`, `temp_file`, `not_a_file` | The target is not a permitted record path. |
| Refusal reason | `not_found`, `not_deleted`, `bad_id`, `too_many`, `unreadable` | Correct the input or access problem. |
| Kind outcome | `built`, `hit`, `planned` | Rebuilt, already current, or planned by a dry run. |
| Kind outcome | `lazy`, `deferred`, `skipped`, `failed` | Inspect work left for later, deliberately omitted, or failed. |
| Batch field | `tokens_bumped`, `tokens_failed` | Failed updates leave the app uninformed about some writes. |
| Batch field | `truncated`, `stopped` | A bound or stop curtailed the batch; report it. |
| Embedding field | `would_send`, `sent`, `requests`, `held` | Preview, request and cached-text counts. |
| Embedding field | `stale_space`, `embedding_off`, `deferred` | Vectors were not refreshed in this space, or spending was disabled. |
| Embedding field | `not_sent`, `failure` | Report incomplete embedding warm-up. |
| Stop reason | `cancelled`, `root_moved`, `lane_busy` | Explain why the batch stopped before claiming completion. |

A nonzero `tokens_failed` or nonempty `stopped` is incomplete, even beside
`refreshed` paths. `cache: off` is a supported configuration. Never discard
embedding `failure` or `not_sent` when reporting the result. The report has
private paths and root names, although it includes no record prose: keep it
in conversation, and put only scratch-run counts/statuses in repository evidence.

## Check one reader

Read an edited record through its store reader, or inspect its app page.
For a substantial batch, rerun the same sync with `--verify`, preserving
`--no-embed` when required. Verification and a failed writer need separate
reporting; a multi-file batch is not a transaction with automatic rollback.

## A library without a Python shell

On a phone, ordinary reads rebuild lazily. `POST /api/cache/sync` is available
through loopback only, for a PC with an existing `adb forward` setup.
An open page can paint remembered data briefly before revalidation; see the
[overview consistency bound](../../../docs/store-guarantees.md#what-an-overview-page-may-show-from-memory).

## Report back

Describe the verified edits, sync counts and any failed, deferred or stopped
work. Library commits follow that library's own history. This public checkout
gets no library names or contents; its evidence concerns isolated placeholders.
