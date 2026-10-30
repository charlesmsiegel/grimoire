# Player Sovereignty and Narration Authority

**Status:** first slice implemented as a pure authority policy over *labelled* narration claims; see the [player sovereignty contract](../../PLAYER_SOVEREIGNTY.md). The roadmap's first proof (PC intention, voluntary dialogue and interiority stay under human control) is met **at policy level, on synthetic fixtures**:
- Milestone 1: the forbidden-authorship errors, narration policy and correction flow exist as pure functions; there are no freeform scenes to test them on yet. The correction flow cannot correct or flag a *misattributed* overreach (a claim labelled with an NPC subject, or PC speech bundled into an outcome). That needs the claim labeller and its mislabel review, owned by [Narration Continuity Referee](narration-continuity-referee.md#inherited-from-wave-1).
- Milestone 2: turn-submission and narration-acceptance checks exist as pure functions; no turn pipeline calls them.
- Milestone 3: delegation modes exist as an API, with one synthetic scenario each for ensemble, romance, combat and mystery.

Nothing constrains generated prose, no UI exposes delegation or correction, and player trust has not been evaluated. Owners: claim labelling, [Narration Continuity Referee](narration-continuity-referee.md); turn submission and narration acceptance, [Minimal Text Scene Runner](minimal-text-scene-runner.md); player-trust evaluation, [Story Quality Benchmarks](story-quality-benchmarks.md) milestone 3.

**Outcome:** The player retains meaningful control of their PC's intent, voluntary speech, choices and interior experience; Grimoire narrates a responsive world without stealing agency.

## Scope and ownership
- Define distinct authority for PC intentions, voluntary speech, involuntary sensations, external effects, GM facts and NPC actions; allow explicit user delegation without silent authority creep.
- Stop narrators from imposing unchosen commitments, romantic feelings, beliefs or PC dialogue as fact. Allow externally caused conditions such as fear or compulsion only under the active rules, agreed limits and clearly presented consequences.
- Support nuanced authorial control: quoted dialogue, shorthand actions, directorial requests, explicit consent to auto-complete a PC action, and a reliable way to correct an overreach.
- Evaluate player trust, useful challenge and non-patronizing refusals; preserving sovereignty does not guarantee PC success.

## Milestones
1. Document and test a small set of forbidden PC-authorship errors on current freeform scenes; add player-controlled narration policy and correction flow.
2. Carry explicit origin/authority through PC turn submission and narration acceptance without changing the underlying action/effect writer.
3. Expose adjustable delegation modes and demonstrate strong player agency across ensemble, romance, combat and mystery scenarios.

## Moonshot
Grimoire is a collaborator that knows *whose story choices belong to whom*. Even a forceful, surprising fictional world never mistakes authorship of a player's PC for its own.

## Proving scenario
The player says 'I reach for her hand.' Grimoire can describe her reaction and obstacles, but cannot add 'I confess my love' or decide that the PC now trusts her. A failed reach under the rules remains a failed reach.

## Interfaces and independence
Owns the PC-versus-director **authorship policy**. [Session Zero](session-zero-boundaries.md) owns negotiated content limits; [Fiction-First Adjudication](fiction-first-adjudication.md) owns outcomes, not PC intentions. A read-only prompt-policy test needs neither a transaction engine nor voice.

## Open questions
- Which involuntary PC reactions may the director establish through rules, environment or fiction?
- When can the player delegate characterization or auto-completion explicitly?
- How is policy inherited across worlds and retconned scenes?
