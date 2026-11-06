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

## Wave 2 handoff: imported art traceability

Consume 018's image-object/placement contract, 022's source/instance effective view and **026's verified V1 import receipts**. The first proof must be a source-backed portrait plus multiple expression/outfit placements that retain their **per-version crop, logical role, provenance and override/hide state**. A missing picture must remain an explicit unresolved placement, not a different actor's file found by name. No additional copy of an identical image blob is required and no implicit remote downloads or image-generation calls occur.

## First slice: implemented

The headless first slice is implemented with synthetic tests; it has no UI. It reaches the handoff's proof for character cards:

- **An explicit missing picture.** 018 gains a fourth placement mode, `unresolved`: a picture belongs in a slot but its bytes are not here. It names the slot's role (and a sprite's outfit and name), no image, and a note saying why. A source may write it (it still may not `hide` or `adjust`), so an individual inherits the slot as missing; a campaign may write it too, and it never falls back to the source's picture or to any picture found by name. Sync copies it with no blob. Documented in [IMAGES](../../IMAGES.md).
- **Imported card art as placements.** 037's importer places a V3 card's `assets` on the imported character or scenario: icons, `emotion` sprites (outfit `default`), backgrounds and other pictures, each in its own slot with its asset in the provenance. Inline `data:` pictures are decoded locally; remote, packaged and undecodable ones become `unresolved` slots, and nothing is fetched. Identical bytes are one image object and one blob. Every slot has the portrait's hand-edit protection: a person's picture, crop or focus stands until `replace_edits`. Documented in [CARDS](../../CARDS.md#card-art-038).
- **The proving scenario.** One individual's own sprite, crop or hidden background changes neither the card nor another individual of it; an updated card moves the art the other individuals inherit and never the first individual's own picture.

Answers recorded for the open questions below: by default every card variant is inherited (an individual overrides it slot by slot), and missing imported art degrades to an explicit `unresolved` slot that shows nothing and says why.

## Later milestones

Planned, not in the first slice:

1. **V1 art (026).** Carry the V1 expression settings the import still drops (outfit metadata, the default outfit, hidden outfits and expressions), campaign expression layers (overrides and hides), campaign-only slots, and write `unresolved` placements for V1 art whose bytes did not arrive instead of only reporting it.
2. **Derived portrayals.** Art inheritance through 022's derived variants (a portrayal's own art over its base's), and art pinned to a revision or release (042/056); today art follows the entity, not an instance's pinned revision.
3. **More card art.** Pictures packaged in a PNG or CHARX file (037's later containers), outfits other than `default`, and a crop, scale, review and reset workflow (milestone 2). A slot an updated card no longer fills (an asset renamed or removed) keeps its last picture, which a source cannot hide and 018 cannot delete: the importer should report such slots and offer to mark them `unresolved`.
4. **Large sets and devices.** Large expressive sets, alternate portrayals and Android format and size budgets (milestone 3).
5. **Older builds.** A build older than 038 reads an `unresolved` placement as malformed (`invalid_placement`), so its own repair or `replace_edits` may overwrite it; a format marker that older builds refuse as newer would protect it.

## Open questions
- Which variants are inherited by default?
- What asset format and scale constraints suit Android?
- How should missing imported art degrade?
