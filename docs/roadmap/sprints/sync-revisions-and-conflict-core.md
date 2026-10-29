# Synchronization revisions and conflict detection

**Outcome:** A transport-independent version history makes V2 libraries safe across independently edited devices.

## Scope and first proof
Demonstrate deterministic merge of nonconflicting file revisions and explicit keep-both conflict with two local copies; preserve tombstones and integrity checks.

## Subsequent milestones and moonshot
Idempotent interrupted transfers, retained ancestry, causal branches and user-visible reconciliation.

## Independence and interfaces
Storage/synchronization protocol; depends on native revision contract; not world/campaign inheritance.

## Proving scenario
A desktop edit and phone edit cannot silently overwrite one another based on timestamps.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
