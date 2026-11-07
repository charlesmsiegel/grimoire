# First playable V2 text roleplay

**Outcome:** Deliver a playable, persistent text roleplay loop both for an imported V0.2.1 world **and for a world-less chat launched from standalone character/scenario/lorebook cards**.

**Status:** **first slice implemented**, headless: `grimoire.scene` (pure), `grimoire.scenestore` and `grimoire.play`'s `SceneRunner`, on an additive `turns/` record family that sync holds back. The contract is in [SCENES.md](../../SCENES.md). Both proofs are executable in `backend/tests/test_scene_proof.py`: a migrated V1 campaign and a world-less card chat (with its lorebook, and a second card attached mid-chat) are played, closed and reopened with a new store, authority and runtime, with each NPC in its own voice and hearing only what it was present for; a scenario-only chat plays with an explicit director reply. Pause, skip, redirect, rephrase, keep, strike and pass are authorized text operations; every call's usage is stored with its take.

**What the first slice leaves (milestone 2):**
- ~~First, due before 044: read a chat's imported history as the history before turn 1, and decide who heard it.~~ **Done in milestone 2's first PR**: the cast a chat was imported with heard it, a later arrival none ([SCENES](../../SCENES.md#who-heard-what)).
- Kept lines are transcript, not canon. The labeller, 029 framing and decision, `check_effect`, 021 acceptance and 030 open-time recovery before the next acceptance (the Wave 4 obligations of 029, 030 and 021) are milestone 2. Milestone 2 has the labeller and 029 framing and decision ([SCENES](../../SCENES.md#confirming-and-deciding-an-action)), and accepts a turn's structured effect proposal into 021 with every key, `check_effect` first and 030's recovery at open time before the next acceptance ([SCENES](../../SCENES.md#accepting-an-effect)); no model proposes an effect from kept narration yet, and no ability use is proposed.
- 032's delivery records are not written to 020 yet: an NPC's memory of a scene is the window of lines it heard.
- A session-zero configuration is not executed (016 has no codec yet): a scope with one refuses generation. Mid-session requests run with an empty configuration.
- A redirect's or rephrase's direction is recorded on its take but not sent to the model.
- UI commands, director instructions and interiority are refused (`not_in_this_slice`); PC-authorship detection in generated text, the UI, streaming, the chat list and `last_activity` per post are later.
- Turn ids carry the device's tag on a library with a device identity, so two offline devices' turn *n* are two turns; a chat holding both is forked and waits. Merging forked turns is 028's merge milestone before 044.

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

## Obligations from Wave 5

The [Wave 5 audit](../README.md#wave-5-status) found these contracts that other Wave 5 first slices leave to this runner, none of which the first slice does. They are milestone 2 work unless a row says otherwise; each is stated in the named doc.

- **036 ([TRANSCRIPTS](../../TRANSCRIPTS.md), [ARCHAEOLOGY](../../ARCHAEOLOGY.md)), first item of milestone 2, before 044:** read a chat's imported pages (V1 scenes and SillyTavern chats) as the history before its first turn, and decide then who heard each line. Until then the scene reports `imported_history_unread` and no NPC has heard any imported line (the requester's decision in the Wave 5 audit: flag now, hear later).
- **039 ([GOALS](../../GOALS.md#requirements-on-later-sprints)):** ~~choose a director NPC's intention afresh for every turn's plan from the same `ActorKnowledge` its brief uses, with the turn's cast split into recognised and unrecognised actors; run `check_agenda` before an agenda is used; never let narration, a player's text or a reply author an agenda. Store each turn's `Choice` and its explanation in the turn record, so a reload can say why an NPC acted (045 relies on it).~~ **done with 035 milestone 2's intentions slice**: `SceneRunner(..., agendas=)` chooses for every take and records each choice as `TurnRecord.intentions` ([SCENES](../../SCENES.md#npc-intentions-039)); an agenda that fails `check_agenda`, or meets a damaged ledger, refuses the take. Agendas stay app configuration until 039 stores them, and the runner does not yet give the turn to the NPCs whose `Choice.acts`.
- **040 ([OBSTACLES](../../OBSTACLES.md#known-limits)):** the labeller picks a suggested or novel approach and frames it through 029, kept separate from the decider; turn `due()` costs and stakes into 021 accepted effects.
- **041 ([STAGING](../../STAGING.md#checks-against-033-and-034)):** keep and pass on the latest stage (041 milestone 2 persists it); take 032 presences from the stage, so a whisper never passes a shut door in play (today the runner builds presences from the 033 cast); treat an absent cast member as not speaking in the room; check the stage against the current 033 cast every sequence; decide with 041 milestone 2 how a turn's several physical changes map onto sequences when 021 accepts one effect per turn.
- **031 ([CONTEXT](../../CONTEXT.md)):** pack for a player character's controller (also listed under Wave 4).
- **016 ([SESSION_ZERO](../../SESSION_ZERO.md)):** extend the intent router with skip and redirect lanes; authenticate the audience and speakers. Today `BoundaryContext.audience` is the requester alone, which holds for one local player; 102 changes it.
- **029 ([ADJUDICATION](../../ADJUDICATION.md#known-limits)):** take consent only from the player's own live choice (also listed under Wave 4).
- **052 ([Instant Campaign Creation](instant-campaign-creation.md)):** ~~let a launch choose among a card's greetings (`begin` takes only the first)~~ **done with 052's first slice**: `begin(..., opening=id)` ([SCENES](../../SCENES.md)); a quick start runs with no session zero at all (a scope with one still refuses generation until 016's codec exists).
- **048 ([Performance And Tracing](performance-and-tracing.md)):** turns store no timings and the runner has no tracing hook; every call reloads the library and re-reads every turn file of the campaign (see [SCENES](../../SCENES.md#known-limits)).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
