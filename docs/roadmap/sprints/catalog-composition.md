# Shared Catalogs and World Add-ons

**Outcome:** Reuse common bestiary, gear and optional character collections without copying every entry into every world or prompt.

## Scope
- Let worlds remain coherent self-contained settings while explicitly referencing optional catalogs.
- Offer include/exclude and setting-specific specializations without mutating shared packs.
- Discover relevant records without eagerly inserting all imported content into context.

## Milestones
1. Compose invented Faerûn, Krynn and Athas with a shared small catalog.
2. Exclude Athas-inappropriate gear and add a setting-specific creature.
3. Prove large catalog search has bounded prompt/context cost.

## Proving scenario
The same shared bestiary supports two settings; a campaign opts into one creator card set but neither campaign imports thousands of unrelated cards into every turn.

## Independence and interfaces
Uses source-qualified lookup when available; a constrained initial flat import list is acceptable. Packaging/distribution is separate.

## Shared characters and lorebooks independent of worlds

A catalog is an **optional authoring source**, not a requirement to create a world. Allow a single standalone character card, a scenario-only source or a lorebook to be attached directly to a 107 chat and reused across many chats or world portrayals. Source-qualified `derived_variant` relationships make explicit which Mara/Elin portrayals inherit selected authored core material. No world becomes the sole owner of the base card merely because it was the first importer.

The early direct card/scenario/lorebook import and chat association are in Waves 3–4 (107/037); this sprint's later responsibility is larger *optional* source packs, include/exclude rules, source distribution and bounded catalog discovery. Don't defer a one-character quick chat until this Wave 7 sprint. [Chat model](../CHAT_MODEL.md).

## Open questions
- World versus content library: storage type or role?
- Which scopes may select add-ons?
- How do world-specific exclusions persist?
