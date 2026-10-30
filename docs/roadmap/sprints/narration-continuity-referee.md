# Narration Continuity Referee and Contradiction Repair

**Outcome:** Catch factual contradictions in proposed narration before they propagate into canon, without making the narrator rigid or repetitive.

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

## Open questions
- When may a repair be automatic?
- What confidence threshold avoids ruining deliberate ambiguity?
- Should failures block publication or become visible suggestions?
