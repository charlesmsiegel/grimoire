# Source-Qualified Content Identity

> **V2 scope correction:** Source-qualified identity applies to *every* definition and campaign instance, not just PCs/NPCs. A name, legacy V1 slug, or inherited portrayal is not a V2 instance ID. This contract is a prerequisite for all migrated data. See the [V2 ordered roadmap](../README.md) for prerequisites, waves and current first-slice proof. Older references to shipped V1 APIs describe behavior to *port*, not V2 capabilities.


**Outcome:** Know exactly which reusable definition, adaptation, portrayal or individual a reference means even when names collide.

## Scope
- Define stable source/kind/ID references and safe legacy ID compatibility.
- Distinguish equal names, same character, alternate portrayal, derived variant and overridden record explicitly.
- Retain origin, version, owner and broken-reference information in human-editable files.

## Milestones
1. Write a small identity contract and ambiguous-reference test matrix.
2. Resolve two same-named characters across different sources without accidental aliasing.
3. Migrate selected existing links and validate round-trips through fork/import/export.

## Proving scenario
Faerûn and a creator add-on both contain a Drizzt card; the user can select one portrayal without silently replacing the other.

## Independence and interfaces
One narrow resolver contract; catalog composition and UI can consume it later. Do not require a universal character ontology.

## First-slice status

Milestones 1–2 are implemented in the pure core and documented in [the identity contract](../../IDENTITY.md): strict text forms for definition, campaign-instance and legacy V1 references; a name-safe index where unpinned references follow the declared head and pinned ones keep their revision; explicit links; and attributed conflicts. Milestone 3's fork/import/export round trips wait for those subsystems; only the text-form round trip is proven.

## Open questions
- ~~Which identifiers must be pinned by version?~~ Definitions, derivation/override bases and legacy-map targets; unpinned references follow the head.
- ~~How should missing sources be represented?~~ As a `BrokenReference` with reason `missing_source`, `missing_entity` or `missing_revision`; the reference is kept verbatim. The persisted file form remains open.
- Which legacy references are ambiguous and need review? Initial list in the identity contract; the migration sprints own the review flow.
