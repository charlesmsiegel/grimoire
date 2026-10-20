---
name: create-mechanics-module
description: "Use when authoring or extending a Grimoire mechanics data pack: sheet types, stats, checks, rules, layout or theme."
---

# Authoring a mechanics module

A module is JSON and Markdown rules data, never executable code. User packs live
under the resolved library's `modules/`; study built-in packs without editing them.
Use existing placeholder names in shareable examples and keep private library
content out of this checkout. Follow [store editing](../grimoire-store-editing/SKILL.md)
for outside-app writes, quiescence and sync.

Interview before guessing the pack's shape. Validate after each authored step,
fix load errors before proceeding, and inspect `display_errors` for cosmetics.

| Phase | Read when needed |
|---|---|
| System interview and pack location | [Planning](references/planning.md) |
| Groups, fields, resources, refs or expressions | [Sheets](references/sheets.md) |
| Named rolls and outcome tiers | [Checks](references/checks.md) |
| Rules text and starting entities | [Rules and content](references/rules-and-content.md) |
| Cosmetic layout and theme | [Display](references/display.md) |
| After every step and before handoff | [Validation](references/validation.md) |
| A small complete pack | [Example](references/example.md) |

Current contract: [module validators](../../../backend/src/grimoire/store/modules/validate.py),
[pack loader](../../../backend/src/grimoire/store/modules/pack.py), and
[pool-basic](../../../backend/src/grimoire/store/builtin_modules/pool-basic/).

For CLI examples, select the interpreter and source path using
[Python commands](../grimoire-store-editing/references/python.md).
