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

## Open questions
- World versus content library: storage type or role?
- Which scopes may select add-ons?
- How do world-specific exclusions persist?
