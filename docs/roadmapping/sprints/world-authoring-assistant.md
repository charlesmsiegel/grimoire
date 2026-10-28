# Constraint-Aware Worldbuilding Assistant

**Outcome:** Help authors create coherent locations, factions, creatures and lore as reviewed records instead of hallucinated unsaved chat.

## Scope
- Generate bounded proposals from current setting conventions, explicit constraints and source-aware retrieval.
- Distinguish template, campaign individual and disposable brainstorm; don't persist rejected ideas.
- Detect conflicting or duplicate content before publishing accepted records.

## Milestones
1. Propose three distinct linked towns and one guild for a small world.
2. Review and accept only a subset as actual records.
3. Generate and validate larger linked regions with controlled seed/provenance and no blanket writes.

## Moonshot
A world design studio that simulates the social, geographic and historical consequences of author choices and highlights plausibility gaps.

## Proving scenario
Two accepted towns are written with correct links; the third is not stored, and a suggested duplicate guild is surfaced instead of created.

## Independence and interfaces
Can return drafts without world Change Sets initially; accepted structured edits can use the shared Change Set path later.

## Open questions
- Which generated record types are valuable enough to formalize?
- What is seedable versus just provenance-tracked?
- How should canon conflicts be surfaced?
