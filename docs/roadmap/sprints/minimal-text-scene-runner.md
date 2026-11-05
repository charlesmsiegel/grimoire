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

## Obligations from 025

When play runs ability attempts ([fictional abilities](../../ABILITIES.md)): take a player-controlled actor's attempt from that player's own contribution, obtain the controller's consent before an instance's powers change, and authenticate the ruling channel (with 029).

## Obligations from Wave 4

The [Wave 4 audit](../README.md#wave-4-status) collected the contracts its first slices leave to this runner. Each is stated in the named doc; they are listed here so none is missed.

- **029 ([ADJUDICATION](../../ADJUDICATION.md#known-limits)):** keep the labeller that frames an action separate from the decider; refuse outcome narration for a framed but undecided attempt; take a consent decision only from the player's own live choice; never allocate a contribution id that starts with `director:`; run `check_effect` before every acceptance it hands to 021; own the prompts and how much adjudication a player sees.
- **030 ([RECOVERY](../../RECOVERY.md#out-of-this-slice-named-owners)):** persist a proposal and its endorsements before committing its acceptance, detect a partly published acceptance when a campaign opens, and recover it (`complete`, `adopt` or `revoke(partial=True)`) before accepting the next effect.
- **027 ([GENERATION](../../GENERATION.md)):** persisting generated lines is the runner's, re-checking the run's `FenceToken` inside that commit; every line is an unreviewed proposal until it passes `review_narration`. Each turn, build every speaker's pack and the director's lore from the current turn's snapshot and pass that same snapshot to `npc_brief` and `director_brief` (the briefs prove the pack, view and snapshot agree, not that the snapshot is current); pass every silent present NPC's resolved voice as `run_turn(..., scene_voices=...)`; the app's generation runtime holds a trusted `app:` handle within [TOOL_AUTHORITY](../../TOOL_AUTHORITY.md#requirements-on-later-sprints)'s limits; review `DirectorBrief.unscreened` lore, which no reply screen covers.
- **031 ([CONTEXT](../../CONTEXT.md)):** supply the recent `scene_text` each actor witnessed, and pack for a player character's controller.
- **032 ([COMMUNICATION](../../COMMUNICATION.md)):** read a message only from a resolved PC intent (`read_message`); build `Presence` from the accepted scene state, never from narration; pass each NPC only the lines it received; keep private channels out of the table's transcript.
- **033 ([PARTY](../../PARTY.md)):** build a scene's present cast from 033's staged `SceneCast`, so a reserve never hears or speaks, and read control from the sovereignty ledger.
- **037 ([CARDS](../../CARDS.md)):** build the opening from a card's greetings.
- **028 ([SYNC](../../SYNC.md)):** register every new stored family (scenes, transcripts, ledgers) in `grimoire.sync.syncable()`; a family it does not name is never synced.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
