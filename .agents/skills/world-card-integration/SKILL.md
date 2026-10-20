---
name: world-card-integration
description: "Use when newly imported character cards need an editorial integration pass: duplicate lore, cast corrections, setting entities, greetings, plot links, tags or voice anchors."
---

# Integrating imported cards into a World

Read the cards and the World's existing conventions before editing. This is an
editorial pass; use `populate-world-content` first if the roster has never been
extracted. Private content, scripts and library commits stay outside this repo.
Follow [store editing](../grimoire-store-editing/SKILL.md) for all external writes.

| Phase | Read |
|---|---|
| Resolve operations and preserve newline handling | [Store writers](references/writers.md) |
| Existing history and new card inspection | [Inspect](references/inspect.md) |
| Duplicate lore, cast and mechanics judgments | [Reconcile](references/reconcile.md) |
| Groups, locations, items, tags, greetings and voices | [Scene content](references/scene-content.md) |
| Prose examples, after reading neighboring entries | [House formats](references/house-formats.md) |
| Integrity, sync, diff review and a library commit | [Verify](references/verify.md) |

Preserve genuine homonyms and deliberate earlier edits. Copy greeting bodies
verbatim, keep stable record IDs, and add plot edges only where the text supports
an actual dependency. Never reset unrelated library changes.

For CLI examples, select the interpreter and source path using
[Python commands](../grimoire-store-editing/references/python.md).
