# State Transactions and Recovery

**Priority:** concrete mechanics and inventory vertical slices first; genericity must be earned by a second consumer.

## Goal

A player action can change multiple authoritative records without losing track of what actually happened. Outcomes must be deterministic once committed, inspectable, crash-recoverable, replay-safe and compatible with human-editable canonical files.

## Work to plan

- Adopt/validate the *existing proposed Mechanics II Action/Effect transaction design* for accepted game actions and effects. No competing general-purpose transaction framework by default.
- Define a logical operation ID and expected versions for changes to sheets, inventory holdings, clocks, relationships, location/party status, resources and action/scene evidence.
- Validate prerequisites, legal action choices, available quantities, ownership, mechanics checks, deterministic randomness and output effects before publication.
- Coordinate individual file writes under existing lock/atomic-publication semantics, plus a durable file-backed journal or recovery protocol where cross-file all-or-nothing writes are impossible.
- On retry, recovery, undo, cancellation, conflicting external edits and scene retcon, preserve **exactly-once logical intent**: never double-spend an item, duplicate a captured monster, or reroll an already-fixed outcome without an explicit new event.
- Keep source definitions, imported catalogs and global world facts separate from mutable campaign results. A failure must be visible, not a fabricated successful operation.
- Expose a safe read API and audit/undo view; narration is downstream of committed effects and must not secretly change the final result.
- If World Maintenance requires multi-record writes with different semantics, test reuse of only the common protocol (preconditions, validation, journal, recovery), not forced coupling to game-specific Effects.

## Proving scenario

One successful monster capture consumes a ball, creates or reuses an individual character, and associates it with its trainer. A crash after one file publication is recovered without extra ball consumption, missing companion, or duplicate actor. A different action moves a key between holders exactly once. Mechanics-free play can commit an explicitly accepted fictional result without requiring a dice roll.

## Dependencies and boundaries

Works with [Mechanics](mechanics.md), [Inventory](inventory.md), [Creature → Character](creature-to-character.md) and [World Maintenance](world-maintenance.md). It preserves the [file-authority contract](../../principles.md): an event journal, when authoritative, is a human-editable file, not a disposable cache.

## Open questions

- What exactly is the smallest useful journal/recovery scheme already designed for Mechanics II, and where are its limits?
- Are accepted fiction-first operations and mechanically resolved effects the same transaction type or separate callers of a shared mutation protocol?
- What is the user-visible recovery and undo behavior when part of an effect is applied before interruption?
- How should source edits, actor-version changes, scene deletion, forks and manual Markdown edits interact with replay protection?
- Does world-maintenance Change Sets justify generalizing a transaction ledger, or can common guards be shared without a universal event store?
- What level of cross-device concurrency would require stronger coordination beyond existing local file locks?
