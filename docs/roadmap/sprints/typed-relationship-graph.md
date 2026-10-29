# Typed relationships and bounded graph traversal

**Outcome:** Represent source links, organizations, places, ownership, emotions and events as typed, dated, source-qualified relations.

## Scope and first proof
Store and traverse a small directed multigraph; distinguish symmetrical relations, missing links and subjective beliefs; no omniscient inference.

## Subsequent milestones and moonshot
Rebuildable indexed traversal with bounded path queries, time and campaign/perspective filtering.

## Independence and interfaces
Canonical relation files plus derived graph index; consumes native entity IDs and file revisions.

## Proving scenario
The director sees an actual membership; an NPC with no evidence of it does not automatically learn it.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
