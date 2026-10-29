# Expression, Outfit and Portrait Asset Workflows

> **V2 scope correction:** V2's initial branch has no V1 art system. Begin with the canonical V2 image-object and scoped placement contract; port V1 outfit metadata, hidden/inherited overrides and version-specific expressions losslessly. See the [V2 ordered roadmap](../README.md) for hard prerequisites and delivery wave; older references to existing V1 systems are porting targets, not the V2 starting baseline.


**Outcome:** Manage art variants as meaningful inherited, versioned assets rather than unrelated gallery pictures.

## Scope
- Build on existing structured outfit/emotion expressions, image descriptions and content-addressed assets.
- Improve import, review, crop, scale, origin/version, override/reset and broken-asset workflows.
- Preserve inherited appearance and individual customization across cards, forks and source changes.

## Milestones
1. Expose readable provenance for one portrait and several expressions.
2. Simplify bulk import/edit and individual override management.
3. Support large expressive sets, alternate portrayals and responsive device budgets.

## Proving scenario
A goblin individual's custom outfit does not alter its species or another individual; a source-art update follows inheritance rules.

## Independence and interfaces
Uses present asset management; image generation is a separate optional producer and VN is a separate consumer.

## Open questions
- Which variants are inherited by default?
- What asset format and scale constraints suit Android?
- How should missing imported art degrade?
