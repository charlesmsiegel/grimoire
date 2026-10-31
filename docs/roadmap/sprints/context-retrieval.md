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

## Wave 2 handoff: graph + evidence + effective world

Consume 012's actor visibility/claim evidence, 017's bounded typed graph queries and 022's revision-consistent effective world. A connection in the graph does not give an NPC the connected node's secrets; a rumor is evidence of a report, not of truth. Assemble per-actor compact context deterministically, with provenance and token budget. Optional embeddings or reasoning require explicit provider policy and usage accounting.

**Parallelism in Wave 4:** 027 owns minimally sufficient actor context and actual separate NPC calls; 031 owns optional richer retrieval/prompt packing through a stable extension interface. Do not make 027's first useful slice depend on completing 031 in the same wave.

## World-optional source and lorebook context

A scene may start with a single imported card or a scenario/lorebook bundle and no world. Retrieve **only explicitly attached and relevant source entries** plus current chat/actor observations; do not force a world lookup, include the full imported catalog, or promote a lorebook instruction to director truth. Deterministic trigger/semantic retrieval must respect session revisions, speaker viewpoint, scope and a strict input token cap.

Newly added source material applies to **future context selection**, and does not retroactively make a previously absent NPC hear old private messages. Adding a card must not automatically cause a paid graph lookup or independent background reasoning for every present NPC. [Chat lifecycle](../CHAT_MODEL.md).

## Open questions
- What relevance signals beat full-text/embedding baselines?
- How should context budget be divided?
- When should a source update invalidate retrieval?
