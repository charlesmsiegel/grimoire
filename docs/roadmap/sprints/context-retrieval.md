# Audience-Aware Context Retrieval and Packing

> **V2 scope correction:** Replace V1 transcript/lore-key dumping with *deterministic typed graph traversal and per-actor evidence filtering*, bounded by actual input token budgets. Embeddings are optional and may cost money; graph lookups do not require a model. See the [V2 ordered roadmap](../README.md) for prerequisites, waves and current first-slice proof. Older references to shipped V1 APIs describe behavior to *port*, not V2 capabilities.


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
