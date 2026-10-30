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

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1--foundational-contracts-isolation-and-cost-baselines) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**First-proof additions**
- Prove keep-both conflicts under `explore_crashes` with `Interleave`/`meddle` on replica roots, and add the `preserves_interleave` built-in invariant. Wave 1's "repeatable faults for native record and sync writes" delivered only a synthetic replica copy (006).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
