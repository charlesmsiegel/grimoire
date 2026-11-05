# Counted Holdings for Freeform Roleplay

**Status:** **first slice implemented**, headless: `grimoire.holdings` (pure) and `grimoire.holdingstore` (service), on an additive `holdings/` record family in `grimoire.store`. The contract is in [HOLDINGS.md](../../HOLDINGS.md). The proving scenario, the failure cases and the crash exploration are executable. The requester decided:
- **storage:** per-holder sidecar files;
- **history:** a bounded operation log;
- **"stable IDs":** holder instance IDs plus stable item keys and operation IDs. A stack has no ID of its own.
- **milestone 3:** re-homed to [021](turn-effects-and-wrap-up.md) (narrative extraction).

Prompt context is a pure holder-only helper for 031, and the manual editor is the headless `adjust` line; the UI is later. [021's next slice](turn-effects-and-wrap-up.md#next-slice-holdings-and-ability-charges-delivered) now applies the holdings operation an accepted turn effect carries, in the event's own transaction ([HOLDINGS](../../HOLDINGS.md#driven-by-accepted-effects-021)); extracting proposals from narration is still to come.

**Outcome:** Track how many ordinary resources each actor owns without requiring any mechanics module.

## Scope
- Persist optional campaign-local counted stacks and stable holder references in human-editable authority.
- Implement add, consume, adjust and transfer with nonnegative constraints and clear failure results.
- Provide a small manual editor and relevant prompt context; disabling UI tracking never erases possessions.

## Milestones
1. Count ordinary/strong capture balls across saves and scene changes.
2. Apply idempotent consume and transfer through one deterministic mutator.
3. Expose reviewed extraction from narrative as a proposal, never an automatic unverified item grant.

## Proving scenario
Start with 8 ordinary and 2 strong balls, spend 3 ordinary, acquire 4: reload shows 9 and 2. Repeated consume does not double-spend; spending 15 fails.

## Independence and interfaces
Independent of equipment, crafting, shared catalog DAGs and mechanics; can use campaign-local simple item refs initially.

## Open questions
- ~~Ledger per campaign or holder sidecars?~~ Holder sidecars: `campaigns/<id>/holdings/<kind>/<instance_id>.json`, with a batch operation spanning holders in one transaction.
- ~~What is the minimum operation history?~~ The newest 1000 operations per holder, and fewer when the holder's whole rendered file would exceed 7 MiB, with `evicted_through` and per-holder `peers` seqs, so that a replay is recognised while its entry is in the log and an ambiguous one is refused. After eviction, only a caller-supplied `basis` (recorded when the operation was created) prevents a re-application; see [HOLDINGS](../../HOLDINGS.md#known-limits).
- When does an item mention merit a proposed inventory edit? Re-homed to 021.
