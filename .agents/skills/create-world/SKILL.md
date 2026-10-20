---
name: create-world
description: "Use when building a new Grimoire World or a coherent batch of World content from a concept, rather than extracting an existing roster of imported cards."
---

# Creating a World from a concept

Author a JSON plan and let `backend/scripts/create_world.py` write records through
the store. Keep the plan and all World content outside this public checkout.
Read [store editing](../grimoire-store-editing/SKILL.md) before outside-app writes.
For imported cards, use `populate-world-content` then `world-card-integration`.

1. [Interview and confirm names](references/planning.md) before applying content.
2. [Author the plan](references/plan/index.md), loading the schema and only the
   activation, openings and settings branches that the plan needs.
3. [Dry-run, apply and check](references/apply.md). Resolve every error; explain
   warnings that remain. A later apply requires the explicit World id.
4. Check the opening in the app; use [verify](../verify/SKILL.md) for an isolated run.

Names select records; changing a name in a plan can create another record.
Omitted keys remain unchanged and the plan never deletes records. The apply CLI
already collects and syncs its writes; do not sync that same batch twice.

For CLI examples, select the interpreter and source path using
[Python commands](../grimoire-store-editing/references/python.md).
