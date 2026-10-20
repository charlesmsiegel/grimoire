---
name: ingest-campaign-log
description: "Use when converting raw roleplay transcript exports, optionally with an old campaign-skill archive, into a Grimoire campaign."
---

# Ingesting a campaign log

Reconstruct scenes from the source, then run the app's real absorb pipeline in
strict story order. Ingestion makes real provider calls and spends money; it has
no dry-run. Confirm the target campaign and scope using information already given.
Keep logs, rewritten scenes and scratch scripts outside this public repository.
Follow [store editing](../grimoire-store-editing/SKILL.md) for external library work.

1. [Prepare the campaign and required PCs](references/prepare.md).
2. [Segment and rewrite the transcript](references/rewrite.md). Preserve the
   player's wording and distinguish narrator and character speech. Parallel
   rewriting may help; final ingest calls must remain sequential.
3. [Build and ingest each scene payload](references/scene.md).
4. [Resume and verify](references/resume.md) before advancing past any failure.

Keep stable ingestion keys. Never delete a key to recover an incomplete scene or
rename an unfinished scene. The CLI already collects and syncs its writes.

For CLI examples, select the interpreter and source path using
[Python commands](../grimoire-store-editing/references/python.md).
