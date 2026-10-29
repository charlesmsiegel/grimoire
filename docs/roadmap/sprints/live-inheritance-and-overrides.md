# Inherited Defaults and Local Overrides

> **V2 scope correction:** World/campaign inheritance is a *native source-revision and semantic merge* problem. Consume the selective-rebase interface; do not revive whole-actor V1 copies as the only resolution. See the [V2 ordered roadmap](../README.md) for hard prerequisites and delivery wave; older references to existing V1 systems are porting targets, not the V2 starting baseline.


**Outcome:** An individual or campaign specialization inherits source defaults until that particular property is explicitly changed.

## Scope
- Support per-field fallback for description, art, expressions, traits and compatible mechanics baselines.
- Distinguish inherited, pinned, missing, cleared and overridden values in inspectors.
- Protect personal state from later template updates, deletion and accidental cycles.

## Milestones
1. Prove portrait/description inheritance for an individualized actor.
2. Add explicit override/reset semantics and source-diff visibility.
3. Extend to versioned sheets and nested content where genuinely justified.

## Proving scenario
A creature species gets new artwork; ordinary children inherit it, one customized individual keeps their picture, and removing the source never picks a namesake.

## Independence and interfaces
Can prototype on existing species/character records; reusable identity is a narrow reference, not a demand for full catalog DAGs.

## Open questions
- Which fields inherit live and which must snapshot?
- What is the semantics of explicit null?
- How are deprecated source values shown?
