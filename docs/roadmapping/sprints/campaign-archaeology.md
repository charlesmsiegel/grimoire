# Campaign Archaeology and Evidence-Based Transcript Reconstruction

**Outcome:** Bring years of external RP conversations into Grimoire with source-faithful scenes, actor identity and reviewable reconstructed state.

## Scope and ownership
- Preserve and improve the existing transcript-ingestion skill and sequential absorb workflow rather than build an unrelated importer.
- Detect scene boundaries and out-of-character notes; reconstruct character versions, chronology, relationships, player-authored lines and ambiguous names with traceable evidence.
- Offer **preview/dry-run** of suggested scene segmentation, state extraction and cost before invoking real paid models or writing canonical files.
- Record uncertain, contradictory and unavailable details without hallucinating a missing scene or pretending reconstruction equals truth.

## Milestones
1. Preview one exported chat as proposed scenes, exact source spans and unknowns without canonical writes.
2. Import and verify the accepted segment order and proposed state using existing absorb routes.
3. Reconstruct cross-file/cross-platform campaigns with provenance, resumability and named-identity conflict review.

## Moonshot
A player hands Grimoire years of messy RP logs, saved character cards and notes; the app reconstructs a faithful editable campaign with uncertainty surfaced and can resume it seamlessly.

## Proving scenario
A log has two similarly named characters, interleaved OOC messages and a scene transition. Preview preserves author wording and shows unresolved identity; repeated ingestion neither doubles scenes nor invents missing facts.

## Interfaces and independence
Uses existing [ingest-campaign-log skill](../../../.agents/skills/ingest-campaign-log/SKILL.md) and current absorb pipeline. An independent read-only segmentation/preview tool need not await new state transactions or global world identity.

## Open questions
- What evidence is sufficient to accept a reconstructed fact?
- How are missing context, quoted logs and reordered exports represented?
- How should import cost and privacy be disclosed before running inference?
