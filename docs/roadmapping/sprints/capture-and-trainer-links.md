# Capture, Companion Ownership and Trainer Links

**Outcome:** A successful capture immediately associates the encountered individual with a trainer without treating it as inventory.

## Scope
- Define successful/failed capture outcomes, optional consumed devices, actor reuse and trainer association.
- Use one logical operation for resource spending, individual identity and ownership/association.
- Never create duplicate actors on retry; never change an already-named creature's identity.

## Milestones
1. Implement mechanics-free explicitly accepted capture of an existing creature.
2. Link capture success to optional ball consumption and immediate scene context.
3. Add ruleset-specific capture probabilities, failed attempts and reserve/active placement adapters.

## Proving scenario
Catching a named monster consumes at most one ball and reuses its ID; catching another of the same species creates a different actor; a failure does not grant a companion.

## Independence and interfaces
Consumes narrow inventory, actor individualization and action-recovery ports; can prototype each with fixtures rather than waiting for every full feature.

## Open questions
- What counts as consent to a capture in freeform RP?
- How is a failed-but-costly capture recorded?
- Where is trainer association stored versus party presence?
