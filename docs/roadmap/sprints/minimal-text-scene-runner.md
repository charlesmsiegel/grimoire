# First playable V2 text roleplay

**Outcome:** Deliver a playable, persistent text roleplay loop both for an imported V0.2.1 world **and for a world-less chat launched from standalone character/scenario/lorebook cards**.

## Scope and first proof
Open scene, post as player, run an independent NPC reply, accept/save, reopen and resume from persisted state.

## Subsequent milestones and moonshot
Corrections, recoverable effects, optional portraits and full multi-NPC scenes with cost reporting.

## Independence and interfaces
Owns minimal application flow; depends on NPC generation, accepted event storage and turn authority.

## Proving scenario
**Two independent first-playable proofs:** (1) play and resume three turns with a migrated V0.2.1 world and recognizable NPC voice; (2) choose a standalone character or scenario plus optional lorebook and persona, start with **no world**, play and resume three turns, add a second card mid-chat and preserve history without unauthorized knowledge.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- A PC turn becomes a `PlayerContribution` with its real submitter, mapped from the routed intent as in [intent routing](../../intent-routing.md#handing-a-pc-intent-to-the-authority-policy), and passes `submit_contribution`; narration is accepted only through `review_narration`, with corrections and revocations available to the player (003). Until narration claims can be labelled ([Narration Continuity Referee](narration-continuity-referee.md)), unlabelled narration is shown as proposed text and commits no state about a PC's protected domains.
- Every executed `ui_command` is authorized through the same permission path as the equivalent click. A destructive one also needs a confirmation for its exact target revision; the executor discards the pending value after any result and advances the revision when it acts (007, 008). With one local player the requester is the only speaker; who may confirm among several humans is [Collaborative Multiplayer](collaborative-multiplayer.md#inherited-from-wave-1).

## Wave 2 handoff: playable execution of session-zero intent

The Wave 5 text runner consumes **016's typed optional content/participation boundaries** and Wave 1 intent + PC authority. Add an explicit, authorized text **pause/skip/redirect** operation as part of the first playable three-turn scenario. Skipping graphic depiction changes *presentation* and future proposed narration but does **not** erase or replace an already accepted injury, transfer or event. Apply the same player-authority and correction flow when rerolling.

Load either **026-reviewed V0.2.1 imported data** or **037-approved standalone character/scenario/lorebook sources**, both through 010/107's canonical play scope and 022's effective source projection. Each actual speaking NPC receives a separate 027 model call; 031 supplies bounded actor context without secret leakage. Save, reopen and continue after process restart with exactly-once effects, no lost player submission and visible usage/cost. No default narration tax beyond its explicitly recorded inference stage. This is the **first true playable milestone**; do not confuse Wave 2's headless policy with a working UI.

## First-playable world-less chat and minimal navigation

Wave 5 **must not require** a world, realm, location graph, faction, ruleset, pre-created campaign wizard or catalog manager. In addition to the migrated V1 world campaign, the first text/CLI/web shell starts a fresh chat from a standalone card/scenario and optional lorebook. Creating it allocates one **internal lightweight campaign namespace** for independent `InstanceRef`s and the one canonical chat history; the user sees a chat, not mandatory campaign setup.

- Start, save, restart and resume with `world: null`. A scenario-only chat is legal even with zero NPCs: if the user wants a generated response, request **one explicitly planned, metered director generation** under 027's reviewed stage, not a phantom NPC. A character chat schedules **one independent call per speaking NPC**, no background model calls for attached silent cards, and no hidden extra narrator call. Player persona is optional unless the user elects to play a defined PC.
- Provide a **minimal chat list** with stable IDs, recent order, Continue and source links (character/scenario/campaign if present), not an elaborate browser. A chat found through two source links still has **one** authoritative history. Names/titles can change without rebinding.
- Adding a second NPC or lorebook after turn 2 changes only explicitly authorized bindings and future actor perception; previous private dialogue is not retroactively known by that new actor.
- A chat can be named/grouped as a campaign or record a proposed world attachment without restarting or rewriting actor/scene IDs. Actual reviewed new-world authoring is later (042/052/056).
- If a source card disappears, report its exact broken ref, preserve accepted story and imported snapshot provenance; do not bind another similarly named card. No silent prompt expansion when a lorebook is attached.
- A full SillyTavern chat-history import is separately owned by 036; **new standalone-card play does not wait** for its same-wave importer.

[Chat navigation and lifecycle](../CHAT_MODEL.md).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
