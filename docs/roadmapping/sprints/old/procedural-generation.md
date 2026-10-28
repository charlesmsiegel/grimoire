# Procedural Generation and Worldbuilding Tools

**Priority:** later, optional; test small assisted-authoring needs before a broad content generation engine.

## Goal

Help authors produce coherent, reusable locations, creatures, encounters, organizations, maps and stories without replacing deliberate worldbuilding or creating disconnected artifacts that the campaign cannot understand.

## Work to plan

- **Worldbuilding assistance:** propose new world/local records compatible with surrounding setting constraints, provenance and style; give the author a reviewed diff before they become truth.
- **Procedural Generation:** generate bounded environments, location graphs, settlements, faction resources, encounter tables, rumors or scenario seeds from declared constraints and reproducible random state when practical.
- **Rule-aware content:** allow optional stats and encounters when a supported mechanics system is selected, but never require one merely to invent a town or creature.
- **Content identity:** distinguish a generated reusable template, a campaign-local individual/instance and a one-off scene suggestion; do not auto-create a universal world ontology or litter every campaign's data with discarded ideas.
- **Consistency and evaluation:** inspect existing setting lore and relevant constraints, detect duplicate-looking records and source conflicts, respect player surprises/spoilers and estimate authoring cost.
- **Workflow:** use [World Maintenance](world-maintenance.md) typed proposed changes for real edits, while freeform brainstorming can remain an ephemeral auxiliary task.
- **Publication:** exported generated definitions have human-editable format, provenance and optional seed, with honest inability to replay inherently nondeterministic model outputs.

## Proving scenario

An author asks for three neighboring towns and a local guild consistent with a small existing setting. Grimoire proposes distinct location/group records with links and names, identifies a similar existing guild rather than silently duplicating it, and lets the author approve only two of the towns. The campaign does not see rejected ideas.

## Dependencies and boundaries

[Content Composition](content-composition.md) provides defining setting identity; [Spatial World](spatial-world.md) provides graph constraints; [World Maintenance](world-maintenance.md) applies reviewed edits; [Generation Systems](generation.md) owns inference budgets. This is not a mandatory prerequisite for core gameplay.

## Open questions

- Which asset types deliver enough creative value to warrant structured procedural generation rather than ordinary authoring assistance?
- What should be deterministic and seedable versus generative and merely provenance-tracked?
- How does generation respect canon, provenance, licenses and existing world-author intent?
- What review/selection affordances prevent the store filling with unused, speculative content?
