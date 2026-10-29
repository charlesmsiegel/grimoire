# Distinct Characterization and Dialogue Quality

> **V2 scope correction:** Retain V0.2.1's *separate generation per speaking NPC* as the default. Never replace this with one ensemble model call merely to save money. Preserve distinctive source voice evidence in compact individual prompts; no paid per-NPC background thinking by default. See the [V2 ordered roadmap](../README.md) for prerequisites, waves and current first-slice proof. Older references to shipped V1 APIs describe behavior to *port*, not V2 capabilities.


**Outcome:** Characters sound, think and behave like identifiable people while respecting history, tone and scene authority.

## Scope
- Keep narrative voice, diction, cadence, habits, knowledge and subtext consistent across generated prose.
- Distinguish physical speaking voice from textual characterization and from narrator style.
- Use actor/version, mood, relationships and existing dialogue as evidence, not fixed caricature presets.

## Milestones
1. Create synthetic tests for recognizable multi-character dialogue.
2. Provide author-editable style and characterization anchors.
3. Support long-term development of voice, emotional state and contextual code-switching.

## Moonshot
A player can recognize a character by dialogue alone, across years of evolving relationships and radically different situations.

## Proving scenario
Four NPCs discussing a betrayal remain distinguishable without labels while none knows facts it has not learned.

## Independence and interfaces
Improves current prompting and evaluation without requiring TTS, audio, rulesets or a new world schema.

## Open questions
- Which stylistic anchors are stable versus situational?
- How should human correction become a durable preference?
- How can dialogue quality be scored without flattening creative variety?
