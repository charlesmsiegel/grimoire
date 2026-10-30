# Distinct Characterization and Dialogue Quality

**Status:** first slice (milestone 1 and the Wave 2 handoff) implemented as the pure `grimoire.voice` package; see the [voice evidence contract](../../VOICE.md). Portrayal-scoped profiles, campaign pins/overrides, a fail-closed single-speaker fragment and a one-call-per-NPC rendering contract are proved **on synthetic fixtures with a deterministic fake renderer**, scored by the offline story-quality checks. No provider, persistence or scene runner calls it yet (027, 010, 035). Milestones 2 and 3 are open.

> **V2 scope correction:** Retain V0.2.1's *separate generation per speaking NPC* as the default. Never replace this with one ensemble model call merely to save money. Preserve distinctive source voice evidence in compact individual prompts; no paid per-NPC background thinking by default. See the [V2 ordered roadmap](../README.md) for prerequisites, waves and current first-slice proof. Older references to shipped V1 APIs describe behavior to *port*, not V2 capabilities.


**Outcome:** Characters sound, think and behave like identifiable people while respecting history, tone and scene authority.

## Scope
- Keep narrative voice, diction, cadence, habits, knowledge and subtext consistent across generated prose.
- Distinguish physical speaking voice from textual characterization and from narrator style.
- Use actor/version, mood, relationships and existing dialogue as evidence, not fixed caricature presets.

## Milestones
1. Define a **portrayal-scoped voice evidence contract** and synthetic, individually addressed NPC prompt fixtures demonstrating distinguishable dialogue and no borrowed secret knowledge. Build a repeatable comparison independent of an LLM provider.
2. Provide author-editable style and characterization anchors.
3. Support long-term development of voice, emotional state and contextual code-switching.

## Moonshot
A player can recognize a character by dialogue alone, across years of evolving relationships and radically different situations.

## Proving scenario
Four NPCs discussing a betrayal remain distinguishable without labels while none knows facts it has not learned.

## Independence and interfaces
Improves current prompting and evaluation without requiring TTS, audio, rulesets or a new world schema.

## Wave 2 portrayal and voice handoff

- Keep voice anchors, cadence, preferred diction and dialogue **examples** linked to an authored portrayal/definition revision. Do not confuse a voice with a campaign individual's evolving emotions, knowledge or relationship history. A campaign instance can override/pin a portrayal without editing the source or copying the whole character card.
- Define a compact prompt-input fragment for *one* NPC speaker and demonstrate two distinctly authored characters against the Wave 1 [offline quality gate](../../evals/STORY_QUALITY.md). No default ensemble-generation call may impersonate multiple speakers.
- Include negative tests for quoted voice samples being reproduced verbatim, mismatched portrayal/actor assignment, hidden facts included in a voice example, and generic/flattened voices. No actual LLM or independent NPC decision loop is necessary for the first proof.
- Preserve the fields required for future **loss-aware CC V3 import** (example messages, imported anchors, selected versions and unknown extensions), but do not create a new card-native persistent schema or V1 UI.
- **Parallelism:** test with supplied source-qualified identities and synthetic portrayal data. 014 must not require 009's facet implementation, 010's file writer or 013's live adapter; 027 later composes voices with actor-specific evidence and separate provider calls.

See [Wave 2 handoffs](../WAVE2_HANDOFFS.md#providervoicequality-boundaries-013--014--015--027).

## Open questions
- Which stylistic anchors are stable versus situational?
- How should human correction become a durable preference?
- How can dialogue quality be scored without flattening creative variety?
