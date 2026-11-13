# One Player, Multiple PCs per Turn

**Outcome:** Compose actions for multiple PCs and advance the scene once with correct speaker and knowledge attribution.

## Scope
- Provide separate editable PC drafts with reorder/correction before one combined submission.
- Distinguish controller, PC, present cast, NPC and actual speaker in the transcript.
- Avoid intermediate GM responses or duplicate posts during partial failure.

## Milestones
1. Compose and submit a two-PC combined turn.
2. Persist drafts and recover an interrupted submission.
3. Support troupe-style play and multiple simultaneous protagonists.

## Proving scenario
Two PCs speak, the narrator responds once, and each post retains the correct author; no NPC is accidentally promoted into a PC.

## Independence and interfaces
A local UI + turn envelope change; does not depend on network collaboration or voice diarization.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Map routed intents to contributions with the actual speaker as submitter ([intent routing](../../intent-routing.md#handing-a-pc-intent-to-the-authority-policy)), and reconcile authority `controller_id`s with `grimoire.security` principals (003, 007, 008).
- Record control history, or end delegations explicitly on a control change. Today a delegation revives when its grantor regains control ([player sovereignty](../../PLAYER_SOVEREIGNTY.md)).

## Current headless slice
See [the accepted local composition contract](../../LOCAL_MULTI_PC.md). Drafts survive restart;
confirmations require the current session's issued handles. The owner controls ordering, every
controller confirms the exact revision, and one combined turn advances through SceneRunner with
one explicitly requested director response. Per-NPC generation remains separate. Sprint 115 owns
the remaining application UI/adapter; combined adjudication and effects require a reviewed policy.
