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

## Multi-setting character inheritance

Use **022's** pinned composed effective definition and 042's reviewed rebase. Demonstrate a common Mara concept with explicit Regency, Modern, Mecha and Vampire portrayals that **inherit**, **replace** or **clear** authored fields without copying the entire source. Do not confuse `alternate_portrayal` with `derived_variant`, or use a matching name as evidence of shared identity.

Extend field provenance to voice anchors (027 owns speaking prompts), scoped art and expressions (018/038), compatible abilities and relationships (017). One world/local instance override must never mutate the core definition or another variant, and a source update must not silently change a pinned chat or cause old incompatible facts to reappear. [Chat model](../CHAT_MODEL.md).

## Open questions
- Which fields inherit live and which must snapshot?
- What is the semantics of explicit null?
- How are deprecated source values shown?
