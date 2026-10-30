# Campaign Archaeology and Evidence-Based Transcript Reconstruction

**Outcome:** Import long-running RP transcripts faithfully after V0.2.1 source inventory and basic V2 import become real, without fabricating events or leaking knowledge.

## V2 migration contract

Use the new [migration skill](../../../.agents/skills/migrate-v1-to-v2/SKILL.md) for V0.2.1 source files, actor mapping and image/art provenance. Its [verification plan](../../../.agents/skills/migrate-v1-to-v2/references/verification.md) owns source untouched/idempotency and media mapping requirements. The V1 `ingest-campaign-log` / `absorb` pipeline is **reference implementation only**, absent from the clean V2 branch.

## Scope

- Segment external transcripts into scenes with exact source spans and validated speaker/PC attribution.
- Identify OOC notes, rerolls, alternatives, uncertain names, time jumps and source-specific character versions.
- Propose historical events, relations and actor memories, **but require evidence and review** before canonical commit.
- Produce preview and estimated inference bill **before** invoking models; text-only scanning is free of provider calls.
- Reconstruct private knowledge conservatively; a character reading a line in a transcript is not proof that they witnessed it.

## Milestones

1. Preview one transcript as exact source-backed scenes and ambiguity warnings without writes.
2. Reconcile an accepted segmented transcript with V2's events, actor IDs and source version map without duplicate scenes.
3. Support multi-file/multi-era campaigns with versioned historical evidence and verified rollback.

## Moonshot

A multi-year, million-word RP can be moved into V2 with faithful chronology, personal continuities, and image/scene assets; missing information remains honestly missing.

## Proving scenario

Two same-named actors and a telepathic private exchange occur in one imported transcript. The mapping remains explicit and only the right recipients acquire knowledge.

## Independence and interfaces

Consumes first [V1 import/apply](v1-to-v2-import-apply.md), native files and scene evidence. Owns historical transcript reconstruction, not image conversion or basic V1 source reading.

## Wave 2 handoff: evidence-aware reconstruction

Accept 011's exact V1 source inventory and 026's approved identity/scene mapping. Use 012's access-versus-belief distinction: an old transcript line is evidence that the narrator **wrote** it, not proof that every named character observed it. Preserve original passages and provenance; proposed historical belief/relationship edges remain reviewable suggestions until accepted. Do not rescan images using a new ID scheme or run the V1 absorb pipeline to fabricate extra prose.

## Open questions

- What can safely be inferred from an unstructured transcript?
- Which historical observations must stay unknown until manually confirmed?
- Which old response variants are part of the original accepted continuity?
