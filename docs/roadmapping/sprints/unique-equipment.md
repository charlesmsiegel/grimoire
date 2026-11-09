# Unique Items, Equipment and Ownership

**Outcome:** Represent one distinct sword or artifact separately from a fungible stack, with correct ownership and equipped state.

## Scope
- Separate reusable item definition, individual instance and counted resource.
- Track owner, location, equipped slot, condition, identity and optional inherited attributes.
- Validate move/equip/drop/unequip, duplicate unique-item acquisition and incompatible slots.
- Treat **playable fictional artifacts**—letters, photographs, journals, recorded messages, keys and evidence—as individual objects with authored contents, revisions, reader permissions, custody and provenance where appropriate.

## Milestones
1. Create a unique item instance with its own portrait/provenance and location.
2. Transfer and equip it through explicit validated state changes.
3. Optionally attach ruleset effects without embedding d20-specific statistics in the core.
4. Prove an evidence-bearing letter changes holder and is read by a character without granting its contents to everyone or rewriting its earlier revision.

## Proving scenario
Two same-named swords remain different instances; equipping a sword owned by another character fails and a fork preserves its identity.

## Independence and interfaces
Can consume existing inventory storage and item definition lookup through a narrow adapter; does not require a complete economy.

## Open questions
- Which unique properties are first-class?
- How does equipment inheritance work?
- Should equipment effects be entirely mechanics-owned?
- Are artifact contents immutable snapshots, editable versions or externally linked records, and who is allowed to read them?
