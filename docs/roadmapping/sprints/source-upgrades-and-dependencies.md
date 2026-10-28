# Content Source Versions, Upgrades and Missing Dependencies

**Outcome:** External libraries can change or disappear without rewriting unrelated worlds or silently reinterpreting old scenes.

## Scope
- Represent human-editable source manifests, version constraints, availability and update policy.
- Preview source upgrades, removals and conflicts while preserving campaign divergence.
- Invalidate derived indexes and caches precisely after source changes.

## Milestones
1. Pin and report an optional source revision.
2. Run update/disable/remove against a campaign with inherited and overridden records.
3. Provide conflict reporting and a deliberate upgrade path for selected records.

## Moonshot
Enable reproducible campaign worlds assembled from independently versioned content sources, with time-travel comparisons but no hidden canonical database.

## Proving scenario
Reordering two catalogs does not swap identity; removing one shows missing-source references, not a similarly named replacement.

## Independence and interfaces
Can begin with one optional dependency; recursive dependency graphs are a separate later decision, not an initial requirement.

## Open questions
- Follow-live, pin-per-record or pin-per-campaign?
- Should recursive dependencies ever be allowed?
- How can a source update be rolled back safely?
