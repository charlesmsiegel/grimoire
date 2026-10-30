# First playable V2 text roleplay

**Outcome:** Provide a real playable loop with imported characters before rich UI, voice, mechanics or world simulation.

## Scope and first proof
Open scene, post as player, run an independent NPC reply, accept/save, reopen and resume from persisted state.

## Subsequent milestones and moonshot
Corrections, recoverable effects, optional portraits and full multi-NPC scenes with cost reporting.

## Independence and interfaces
Owns minimal application flow; depends on NPC generation, accepted event storage and turn authority.

## Proving scenario
Play and resume three turns with a migrated V0.2.1 character and intact dialogue voice.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- A PC turn becomes a `PlayerContribution` with its real submitter, mapped from the routed intent as in [intent routing](../../intent-routing.md#handing-a-pc-intent-to-the-authority-policy), and passes `submit_contribution`; narration is accepted only through `review_narration`, with corrections and revocations available to the player (003). Until narration claims can be labelled ([Narration Continuity Referee](narration-continuity-referee.md)), unlabelled narration is shown as proposed text and commits no state about a PC's protected domains.
- Every executed `ui_command` is authorized through the same permission path as the equivalent click. A destructive one also needs a confirmation for its exact target revision; the executor discards the pending value after any result and advances the revision when it acts (007, 008). With one local player the requester is the only speaker; who may confirm among several humans is [Collaborative Multiplayer](collaborative-multiplayer.md#inherited-from-wave-1).

## Wave 2 handoff: playable execution of session-zero intent

The Wave 5 text runner consumes **016's typed optional content/participation boundaries** and Wave 1 intent + PC authority. Add an explicit, authorized text **pause/skip/redirect** operation as part of the first playable three-turn scenario. Skipping graphic depiction changes *presentation* and future proposed narration but does **not** erase or replace an already accepted injury, transfer or event. Apply the same player-authority and correction flow when rerolling.

Load only 026-reviewed V0.2.1 imported entities, scenes and image placements. Each actual speaking NPC receives a separate 027 model call; 031 supplies bounded actor context without secret leakage. Save, reopen and continue after process restart with exactly-once effects, no lost player submission and visible usage/cost. No default narration tax beyond its explicitly recorded inference stage. This is the **first true playable milestone**; do not confuse Wave 2's headless policy with a working UI.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
