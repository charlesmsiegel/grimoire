# Content Provenance and Change Impact Explorer

**Outcome:** A world author can answer where a fact came from, who changed it and which campaigns will be affected.

## Scope
- Show source owner, parent chain, effective value, overrides, aliases, historical revisions and unresolved refs.
- Track new/changed/deleted source content, including ordinary external-editor edits.
- Provide a rebuildable, source-aware impact graph without making activity indexes canonical.

## Milestones
1. Expose provenance for a resolved inherited character attribute.
2. Show impacted campaigns before a source change is applied.
3. Surface external edits and navigate from a source to dependent scenes or actors.

## Moonshot
Interactive provenance graph and impact simulation for a whole multiverse of worlds and alternate character portrayals.

## Proving scenario
Changing a source expression indicates which campaigns inherit it and which explicitly override it; clearing the activity cache loses no authority.

## Independence and interfaces
Read-only viewer can launch independently; deeper history benefits from source version tracking but need not block basic provenance.

## Open questions
- What durable evidence supports external edits?
- How much history must be retained?
- How do we bound dependency impact analysis?
