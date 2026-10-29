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

## Open questions
- Which identifiers must be pinned by version?
- How should missing sources be represented?
- Which legacy references are ambiguous and need review?
