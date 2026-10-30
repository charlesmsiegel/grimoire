# Player Sovereignty and Narration Authority

**Status:** first slice implemented as a pure authority policy over *labelled* narration claims. See the [player sovereignty contract](../../PLAYER_SOVEREIGNTY.md). It covers all three milestones as deterministic tests over synthetic fixtures. No prose narrator is constrained yet, there is no freeform scene runner, and player trust has not been evaluated with people or models.

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
