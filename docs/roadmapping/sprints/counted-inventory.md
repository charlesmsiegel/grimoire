# Counted Holdings for Freeform Roleplay

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
- Ledger per campaign or holder sidecars?
- What is the minimum operation history?
- When does an item mention merit a proposed inventory edit?
