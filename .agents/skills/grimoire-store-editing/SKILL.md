---
name: grimoire-store-editing
description: "Use when creating, editing, renaming, moving or deleting private Grimoire library records through a script, Python session or editor outside the running app. Not needed for app UI or API edits."
---

# Editing a Grimoire store outside the app

Library records are authoritative; cache sync warms derived work already in use.
Cold records are expected. Keep names, prose and aggregate library data out of
this public checkout; follow [privacy](../../../AGENTS.md).

1. [Resolve the library and use its store writers](references/writers.md). Keep
   the target idle, including detached runs, for the entire external batch.
2. Choose embedding policy before entering `cache_sync.collecting`: it syncs on
   successful and exceptional exit. Use `embed=False` for no-spend work.
3. [Sync a coherent batch](references/sync/batch.md) once if the writer did not
   already collect and sync it. Deletions must be explicit.
4. [Interpret the complete report and verify a reader](references/sync/result.md).
   Report failures and deferred work separately from the edits.

Never hand-edit caches, credentials, logs, backups or campaign write tokens.
If quiescence cannot be maintained, use the app's guarded routes. Sync is not
a transaction or a cache-repair procedure.
