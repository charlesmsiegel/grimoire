# Selective Offscreen Events and World Progression

**Outcome:** The world changes while the player is absent, but never demands a simulation tick for every character.

## Scope
- Advance relevant clocks, threats, environments, faction plans and scheduled events.
- Choose a bounded relevant subset with deterministic rules before using expensive inference.
- Track affected state, provenance, information discovery and idempotent time-skip IDs.

## Milestones
1. Advance one known faction goal during a time skip.
2. Bound scope and cost for several concurrent offscreen processes.
3. Support causal branching, hidden consequences and environment changes.

## Moonshot
A coherent living world evolves for years offscreen, with scalable event-driven causality rather than omniscient agents endlessly chatting.

## Proving scenario
A rival's deadline progresses while the party travels; a distant irrelevant shopkeeper receives no pointless model calls.

## Independence and interfaces
Starts with known scheduled facts/clocks; NPC agency and factions add richer sources but are not required for the initial scheduler.

## Open questions
- Who authorizes consequential offscreen events?
- What is the relevance budget?
- How are hidden developments surfaced without leaking spoilers?
