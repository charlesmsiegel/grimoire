---
name: populate-world-content
description: "Use when extracting locations, items, groups, lore and greetings from an existing Grimoire character roster, or recovering greetings including character-version variants."
---

# Populating World content from cards

Use the committed `scripts/populate_world_content.py`; private manifests and
orchestration scripts belong outside this checkout. Establish the target Worlds
and permitted work from the user's request. Follow
[store editing](../grimoire-store-editing/SKILL.md) before outside-app writes.

| Phase | Read |
|---|---|
| Resolver, library Git baseline and CLI | [Prepare](references/prepare.md) |
| Proposals with source citations and exact references | [Manifest](references/manifest.md) |
| Parallel proposal work and serialized applications | [Orchestration](references/orchestration.md) |
| Before any applying agent runs | [Apply contract](references/apply.md) |
| Dirty state, ambiguous greetings or refusals | [Recovery](references/recovery/index.md) |
| Distinct character versions | [Version passes](references/versions.md) |
| Completion and skipped work | [Verification](references/verification.md) |

Applications share one library Git repository: run them sequentially and require
a clean starting tree. A failed apply can leave partial writes; inspect them
before continuing. Never reset or clean the whole library to force a retry, and
never treat a refusal as permission to change the task's framing.

For CLI examples, select the interpreter and source path using
[Python commands](../grimoire-store-editing/references/python.md).
