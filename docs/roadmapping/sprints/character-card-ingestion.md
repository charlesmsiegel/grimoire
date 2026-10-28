# Character Cards, Lorebooks and Scenario Import

**Outcome:** Import third-party character material without treating every greeting, scenario and lorebook instruction as canon.

## Scope
- Parse cards, internal lorebooks, alternate portrayals, scenario prompts and image/voice references separately.
- Preview matches, ambiguous aliases, licenses, facts versus suggestions and target scopes.
- Preserve originals, provenance and user edits; reconcile selected links rather than merging by names.

## Milestones
1. Build an inspectable import preview for a synthetic complex card.
2. Allow selective adoption of lore, portrayal and world references.
3. Exercise duplicate candidates, source removal and repeat import idempotency.

## Proving scenario
An imported character mentions a guild and city; Grimoire suggests existing group and location links without inventing them or canonizing a greeting.

## Independence and interfaces
Standalone import UX against current world record types; optionally uses world Change Sets for multi-record adoption.

## Open questions
- Which card fields are ever canonical?
- How are portrayals and instructions disambiguated?
- What privacy/license evidence must survive import?
