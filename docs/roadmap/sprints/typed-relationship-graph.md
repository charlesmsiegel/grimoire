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

## Wave 2 handoff: truth, epistemic edges and index ownership

Consume the **entity/instance identities from 009**, generic guarded relations storage from 010 and actor access/evidence rules from 012. Canonical relations carry source/scope, directed typed predicate, participant references, time and provenance; an actor's claim about an edge is **not** an objective graph edge.

The first proof must show that the director can traverse established memberships while an uninformed NPC cannot; being connected by `member_of`, `knows` or `has_met` is not itself evidence of a secret. A reported false membership is visible as a *claim with attributed source and uncertainty*, never silently materialized as a factual membership. Bound depth/size, reject unresolved identities without rebinding by name, and keep any graph index rebuildable. This sprint owns graph semantics, **not** 012's evidence authorization logic or 010's file envelope.

See [Wave 2 handoffs](../WAVE2_HANDOFFS.md#observationevidence-boundary-012--017--020).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
