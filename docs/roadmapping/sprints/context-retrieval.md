# Audience-Aware Context Retrieval and Packing

**Outcome:** Retrieve exactly the useful setting, actors and history for a turn within predictable model-context budgets.

## Scope
- Retrieve relevant world records, recent history, memories, optional mechanics and inventory.
- Respect sources, viewpoint, temporal consistency and hidden information.
- Prioritize and shed content transparently without dumping entire imported catalogs.

## Milestones
1. Improve scene-specific recall on labeled synthetic scenarios.
2. Apply audience filters and budget constraints with stable ordering.
3. Evaluate contextual usefulness for complex casts and large worlds.

## Proving scenario
A scene with six NPCs receives pertinent lore but not a forgotten continent's entire bestiary or a secret known only to an absent antagonist.

## Independence and interfaces
Consumes existing indexed search and context assembly; advanced knowledge provenance helps, but can be introduced incrementally.

## Open questions
- What relevance signals beat full-text/embedding baselines?
- How should context budget be divided?
- When should a source update invalidate retrieval?
