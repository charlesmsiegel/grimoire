# Narration Continuity Referee and Contradiction Repair

**Outcome:** Catch factual contradictions in proposed narration before they propagate into canon, without making the narrator rigid or repetitive.

**Status:** **first slice implemented**, headless and pure: `grimoire.referee` (no labeller call, no runner). The contract is in [REFEREE.md](../../REFEREE.md). Milestone 1 and the proving scenario are met on synthetic fixtures: five categories (presence, staging, possessions, knowledge, outcomes) checked against 041, 023/034, 012 and 029/040 records, with cited evidence and the prose unchanged, plus 003 authorship review and mislabel routing. The labeller and milestone 2 (one narration workflow in 035's runner) are next.

## Scope and ownership
- Check generated text against currently authoritative presence, possessions, identity, capabilities, knowledge, scene staging and resolved outcomes.
- Classify explicit contradiction, uncertain inference, acceptable metaphor and intentional unreliable narration; cite source evidence for material findings.
- Offer targeted repair, player review or an accepted exception instead of unbounded silent rewriting.
- Measure false positives, latency and creative damage as carefully as obvious continuity errors.

## Milestones
1. Build tests for five known categories of narration errors and report grounded evidence without changing generated prose.
2. Repair or flag high-confidence contradictions in one narration workflow while preserving original text and optional user override.
3. Evaluate long-form continuity, stylized prose and intentionally deceptive narrators at realistic latency/cost.

## Moonshot
An unobtrusive editorial co-director catches contradictions and fixes them before the player notices, while preserving ambiguity, literary voice and deliberate unreliable characters.

## Proving scenario
A model has an NPC reach through a locked door, reference a secret they were never told and spend an already-consumed potion. The referee identifies distinct grounded errors without objecting to a harmless metaphor.

## Interfaces and independence
Consumes immutable facts from [Scene Physical Continuity](scene-physical-continuity.md), [Knowledge and Perspective](knowledge-and-perspective.md) and accepted actions; owns detection/repair only, **not** state mutation or adjudication.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**First-proof additions**
- Extract atomic claims (domain, subject) from generated prose with a labeller that is not the narrator, and run them through `review_narration` (003). Route possible mislabels to review: a claim given an NPC subject that speaks for a PC, or PC speech bundled into an adjudicated outcome, cannot be struck or flagged through the player's correction flow today ([player sovereignty](../../PLAYER_SOVEREIGNTY.md)).

## Wave 2 handoff: verify output, do not own state

The first 047 claim labeller is **independent of the NPC/narrator generation call**. It consumes the Wave 1 player-sovereignty claim checker, 012's epistemic policy, 017's relation graph and accepted effects, distinguishing PC authorship overreach from a reported false claim or narrator metaphor. Return source-anchored findings to the player/referee rather than silently changing text or replaying an effect. A review **cannot grant** a hidden NPC fact or a new PC commitment. Additional billed critique is opt-in and transparently metered; deterministic contradiction checks come first.

## Inherited from Wave 5

The [Wave 5 audit](../README.md#wave-5-status) found these inputs this referee relies on; none is stored across turns yet.
- **Physical continuity (041, [STAGING](../../STAGING.md)).** The referee consumes `reach`, `sees` and `hears` answers with their `assumed` lists and never mutates a stage. Checking a narration needs the stage as of that narration's turn, which needs 041 milestone 2 (a stage kept across turns) or a stage 035's runner builds for each turn. Off stage is one place in the first slice: the stage does not remember which outside an absent actor is on.
- **Obstacle escalations (040, [OBSTACLES](../../OBSTACLES.md)).** Escalated stakes ("the guard reported you") are derived from the obstacle's ledgers, not 017, 020 or 021 records, so the referee reads `ObstacleState` to check narration against them; 029's `resolutions()` covers only outcomes.
- **NPC intentions (039, [GOALS](../../GOALS.md#not-in-this-slice)).** A reply that breaks the NPC's briefed refusal is kept today; this referee flags it for review rather than rejecting it.

In the first slice ([REFEREE](../../REFEREE.md)):
- 041: a stage at any sequence other than the review's own is refused (`stale_stage`), and staging claims are reported `unchecked` without one.
- 040: `overcomes` checks the derived `ObstacleState` and cites a fact that `prevents` the narrated approach.
- 039: not yet checked. A claim kind that compares an NPC's reply with its briefed refusal waits on the requester's call on whether 047 or 045 owns it.

## Open questions
- When may a repair be automatic?
- What confidence threshold avoids ruining deliberate ambiguity?
- Should failures block publication or become visible suggestions?
