# Typed Contracts, API Consistency and Error Semantics

**Outcome:** Make frontend/backend boundaries and failure semantics precise enough that callers cannot mistake a rejected edit for success.

## Scope
- Converge on clear request/response types and error categories for invalid, missing, stale, unauthorized and transient operations.
- Pay down targeted mypy/TypeScript debt without broad Any or unsafe casts.
- Preserve existing REST contracts and Pydantic 1/Android support; migrate deliberately.

## Milestones
1. Inventory current ambiguous boundaries and error paths.
2. Specify types and characterization tests for one highly-used interface.
3. Migrate callers, tighten checks and document compatibility.

## Moonshot
Generate contract conformance, client types and simulated failure cases from one versioned, machine-checkable behavioral definition.

## Proving scenario
A stale edit, missing record and invalid payload produce distinguishable UI behavior and consistently typed responses.

## Independence and interfaces
Can be delivered API-by-API; does not require a repository-wide rewrite or game-system architecture.

## Open questions
- Which boundary causes the most correctness bugs?
- Where are compatibility envelopes required?
- How can errors be made actionable without leaking private data?
