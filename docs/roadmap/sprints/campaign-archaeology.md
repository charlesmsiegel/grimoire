# Campaign Archaeology and Evidence-Based Transcript Reconstruction

**Outcome:** Faithfully import **SillyTavern chat histories and group-chat exports**, alongside V0.2.1 scenes, without fabricating events, overwriting swipes or leaking actor knowledge.

## V2 migration contract

Use the new [migration skill](../../../.agents/skills/migrate-v1-to-v2/SKILL.md) for V0.2.1 source files, actor mapping and image/art provenance. Its [verification plan](../../../.agents/skills/migrate-v1-to-v2/references/verification.md) owns source untouched/idempotency and media mapping requirements. The V1 `ingest-campaign-log` / `absorb` pipeline is **reference implementation only**, absent from the clean V2 branch.

## Scope

- Segment external transcripts into scenes with exact source spans and validated speaker/PC attribution.
- Identify OOC notes, rerolls, alternatives, uncertain names, time jumps and source-specific character versions.
- Propose historical events, relations and actor memories, **but require evidence and review** before canonical commit.
- Produce preview and estimated inference bill **before** invoking models; text-only scanning is free of provider calls.
- Reconstruct private knowledge conservatively; a character reading a line in a transcript is not proof that they witnessed it.

## Milestones

1. Import a **synthetic SillyTavern conversation export** into a 107 chat/play scope after 037's card import, preserving exact user/NPC messages, order, persona, alternate swipes/edits and active-branch choice with source provenance; separately preview V0.2.1 transcript segmentation.
2. Reconcile an accepted segmented transcript with V2's events, actor IDs and source version map without duplicate scenes.
3. Support multi-file/multi-era campaigns with versioned historical evidence and verified rollback.

## Moonshot

A multi-year, million-word RP can be moved into V2 with faithful chronology, personal continuities, and image/scene assets; missing information remains honestly missing.

## Proving scenario

A synthetic ST group chat has two same-named characters from different sources, a persona, alternate swipes, edited messages and a private exchange. Every source message and chosen active branch is recoverable; unchosen swipes do not create canon or automatically grant secret knowledge.

## Independence and interfaces

Consumes first [V1 import/apply](v1-to-v2-import-apply.md), native files and scene evidence. Owns historical transcript reconstruction, not image conversion or basic V1 source reading.

## Wave 2 handoff: evidence-aware reconstruction

Accept 011's exact V1 source inventory and 026's approved identity/scene mapping. Use 012's access-versus-belief distinction: an old transcript line is evidence that the narrator **wrote** it, not proof that every named character observed it. Preserve original passages and provenance; proposed historical belief/relationship edges remain reviewable suggestions until accepted. Do not rescan images using a new ID scheme or run the V1 absorb pipeline to fabricate extra prose.

## SillyTavern chat-history migration: preserve, don't reinterpret

This Wave 5 first slice is the **historical conversation** side of SillyTavern replacement. 037 (moved to Wave 4) owns standalone **character/scenario/lorebook card import**, and 107 owns the canonical world-less chat scope. Here accept explicitly selected ST chat export files and linked group/character IDs and produce a loss-aware preview/apply for the chat timeline. The accepted import is a *record of prior text*, not an invitation to run V1's `absorb` or expensive language-model reconstruction.

- Preserve exact source text, roles, speaker IDs, timestamps when supplied, chosen greeting/persona, imported author-note/lorebook associations, message edits and alternate swipes. Distinguish active text from **unchosen historical variants**; neither multiple swipes nor reruns may create duplicate accepted events.
- Missing card IDs, abandoned swipe branches, unsupported ST metadata and ambiguous names are reported for review; never match actors automatically by a shared display name. A group chat keeps one canonical conversation with distinct speaker instances.
- A private line does **not** imply every present or mentioned character heard it. Maintain reported evidence separately from director truth and typed knowledge; unknown audiences remain unknown.
- The original export remains unchanged, the import uses the approved 010/107 writer, repeats are idempotent, and no model calls occur during ordinary scan/replay.
- The first playable **new chat** owned by 035 is independent of this same-wave work; after both land, imported ST chats may be continued with the usual per-speaking-NPC generation. More complex variants and lorebook semantics remain explicitly tracked for later interoperability.

[Navigation contract](../CHAT_MODEL.md) / [Standalone card import](character-card-ingestion.md).

## Open questions

- What can safely be inferred from an unstructured transcript?
- Which historical observations must stay unknown until manually confirmed?
- Which old response variants are part of the original accepted continuity?
