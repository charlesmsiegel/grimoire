# Bounded NPC decisions (first slice of sprint 045)

**Status:** first slice of roadmap sprint 045, [Decision Policies](roadmap/sprints/decision-policies.md): milestone 1 (one attitude or tactics decision recorded with its evidence), with the sprint's proving scenario. It builds on 039's goals ([GOALS](GOALS.md)): an NPC's `Agenda` and deterministic policy `choose` say what it may do; this slice adds an **optional, capped** model pick among what the policy allows, and a **record** of the turn's decision that retried narration replays and never re-decides. The code is `grimoire.decisions` (pure), `grimoire.generation.decision` (the call's messages), `GenerationRuntime.decide` (issuing it) and `grimoire.decisionstore` (the ledger).

## The rule

The deterministic policy is the default and costs **no model call** ([INFERENCE](INFERENCE.md#the-rule)). A model-selected decision is an optional `decision` call: off unless the turn's policy gives the stage a cap, at most one per NPC per turn, and booked in the `optional` bucket. When the mode does not permit it, the request is declined as data (`disabled` or `cap_reached`) and the policy decides, so the turn costs exactly what it would have without decisions. An NPC with fewer than two possible approaches has nothing to decide: its planned call is skipped, no call is made, and the turn runs on the policy's pick.

A decision can only reorder what the policy allows. The call is offered the NPC's **possible and permitted** approaches (039's `chosen` and `outranked`) and nothing else: an approach a condition rules out, or a limit forbids, is never an option and can never be selected. Limits are not negotiable for a model either.

## Selecting among permitted approaches

`choose(..., selected=(goal_id, approach_id))` is the one way a pick becomes a choice (039's obligation on 045). The selected approach is `chosen` and the authored order's own pick becomes `outranked`; `Choice.default` is that pick either way, `Choice.feasible` is every possible and permitted approach in the order tried, and `Choice.selection` is the pair given. A selection the agenda does not have is `unknown_approach`; one that is unmet or forbidden now is `infeasible_selection`. `explain` says when a decision picked the approach, and what the authored order would have picked. A planner's `desired` is still only recorded, never obeyed.

## The decision call

Plan it with `plan_generation_turn(..., decisions=(npc_id, ...))`: a `select` phase holding one `decision` request per NPC, before the generation phase. Each decider must be present and director-controlled (`absent_decider`, `player_speaker`, `unassigned_speaker`): a player character's choices are its player's. An NPC is named once (`duplicate_decision`), and the generation phase cannot itself be called `select` (`bad_input`). When the mode declines every request, no `select` phase is planned and the plan is the one it would have been without decisions.

`decision_messages(brief)` renders the call from the NPC's own brief, built by `npc_brief(..., intention=choice)` with the policy's choice: the same knowledge, card and voice the NPC's generation call sees, without what the authored order would do (`NpcBrief.decision_system`, so the call is not anchored to option 1; the refusals that hold are kept), then what the NPC has heard so far, the numbered options (each goal's aim and the approach's action), and an instruction to answer with one number. Its refusals: a brief with no 039 choice (`no_intention`), a choice already selected (`already_selected`), fewer than two options (`nothing_to_decide`), more than `MAX_OPTIONS` (9, `too_many_options`), and an option whose text states a secret the NPC has not learned (`option_leak`, screened against what the NPC holds itself, as the intention is: a secret only heard aloud does not clear it).

`GenerationRuntime.decide(meter, call, brief, *, controls, token=None, cancel=None)` issues the planned call, billed to the turn's meter like every other ([PROVIDERS](PROVIDERS.md)), with a small output cap. It returns a `DecisionOutcome(call_id, actor, status, pick, reason)`, which also keeps the choice it decided on (`basis`); `status` is the call's own status on the meter. `pick` is the option the reply named; the reply must be exactly one listed number (a closing stop is tolerated). Anything else leaves `pick` `None` with a `reason` (at most 64 characters, as a record keeps it), and the policy's order decides: `unreadable_pick`, a provider refusal or error kind, `cancelled`, or `stale` when a newer run claimed the fence. The call is billed either way. With nothing to offer (`no_intention`, `nothing_to_decide`, `too_many_options`, `option_leak`) no call is made: `status` is `skipped`, `reason` says why, and the policy decides. Refused before the call, recording nothing: `decision_mismatch` (not a planned decision about this NPC on this meter), `call_already_run` (it ran or was skipped), `intention_cast_mismatch` (the choice was made for another set of present actors, so its options may be stale), `already_selected`, the actor no longer being director-controlled, and anything the provider adapter refuses before sending (it raises, as for any call). Outcomes are kept per meter, so a second meter on the same plan never touches the first one's decision.

`run_turn`, on the same runtime and meter, then refuses before any call a plan whose `decision` call `decide` has not run or skipped (`decision_not_run`), a plan with two decision calls about one NPC (`duplicate_decision`), an NPC brief that does not carry what its decision call picked (its pick as the choice's `selection`, or no selection after a fallback or a skip; only an NPC skipped for `no_intention` may have no choice at all), or whose choice was made on other ground than the one the call decided on (another agenda, knowledge or scene, which would rebind a pick's ids or change what the fallback picks) (`decision_ignored`); a selection with no decision call on the meter is a reroll's replayed record, held like any selection to what is possible and permitted. Narration never re-decides.

## The record

`record_decision(choice, turn, *, call=None, fallback=None)` writes the turn's decision down as a `DecisionRecord`:

| Field | Meaning |
|---|---|
| `turn`, `actor` | The scene turn (035's `TurnRef`) and the NPC. One decision per NPC per turn. |
| `method` | `policy` (the authored order; no `call`) or `model` (a decision call: its id in `call`, and its pick, or with `fallback` the reason it gave none and the authored order's pick). |
| `as_of` | The knowledge sequence the choice was made from. |
| `agenda_digest` | A hash-seed-independent digest of everything in the agenda the policy reads. |
| `candidates` | Every approach considered, in the order tried: outcome, unmet conditions (`present:<ref>`, `holds:<id>:<stances>`) and the limits that forbade it. |
| `chosen`, `default` | The approach taken, and the authored order's pick (the same for a `policy` record). |
| `evidence` | What it rests on, read from the NPC's own projection: for the chosen approach, each condition it needed (a proposition with the NPC's current stance, or `aware`, or an actor it saw present); for each limit that refused something, the conditions it was gated on. |

A record validates on construction (`DecisionError`, reason `invalid_field` or `campaign_mismatch`): a `model` record has at least two possible candidates and, without a fallback, a chosen approach; every candidate's outcome matches its unmet conditions and limits as 039's policy assigns them, each approach is a candidate once, exactly the one `chosen` candidate is `chosen`, `default` is the first possible one, a `policy` record takes the default and names no call. Its payload is `grimoire.decision/1` (`decision_to_payload`, `decision_from_payload`): every key required, none extra, lists in the policy's order, rebuilt through the constructors (`bad_record`).

**A recorded decision is the turn's for good.** `replay(record, agenda, knowledge, *, present, controls, unrecognised=())` rebuilds the turn's `Choice` for its next take (the same pick, the same authored-order pick, every approach considered as recorded; the evidence and `as_of` are those of the knowledge given), with the selection a model pick recorded, or refuses: `foreign_record` (another NPC's agenda), `agenda_changed` (edited since), `decision_changed` (the NPC's knowledge or the scene now gives the authored order another pick, considers any approach otherwise than the record does, or makes the recorded approach impossible or forbidden), and 039's own refusals. A reroll gets the recorded choice or a refusal, never a new decision; `replay` makes no call, and planning no decision call for a reroll is the runner's part (035, below). Deciding again is the director's act on a new turn.

A recorded decision is **not world state**: it changes no belief, relation, effect or ruling. It is what the NPC chose and why, for the narration that carries it and for later review.

## Persistence

Each campaign has one authority ledger, `authority/decisions/<campaign_id>` ([STORAGE](STORAGE.md#authority-ledgers-010b)), with one entry kind, `decision`, whose value is the record's `grimoire.decision/1` payload. `DecisionLibrary(store, authority, handle)` takes a trusted handle the authority issued (the director's channel, as 029's and 040's ledgers do):

- `record(record)` appends it. The same decision again returns the existing entry and writes nothing; a different decision for the same NPC and turn is `already_decided`. The campaign must exist (`missing_campaign`), and the decider and every evidence actor must be its instances (`unknown_actor`). What is written must read back through the codec as the very record (`bad_record`), so one bad entry can never make the ledger unreadable.
- `get(turn, actor)` and `decisions(turn)` read back.
- Replay on load re-checks every entry through the codec and the once-per-turn rule; anything else is `LedgerInvalid`.
- `decision_actor_ids(store, campaign_id)` scans the ledger raw (deciders, evidence actors, and actors named in unmet `present:` conditions) and is part of `campaign_taken_ids` ([CHATS](CHATS.md)): a newcomer given a hand-deleted NPC's id never inherits its decisions.

## Proofs

`backend/tests/test_decisions.py` is the sprint's proving scenario, for tactics only: "fear" is a belief the NPC holds (raiders at the door) and the constraint an authored limit; no attitude state is recorded in this slice. Oda retreats from the counting-house because it believes the raiders are at the door and that the back stair is clear, and no limit forbids flight now (Quill, whom it will not leave behind, has gone). The decision is recorded with that evidence, and two rerolls replay it with no decision call whatever the narration says. Without the fear it tallies, without an exit it bars the door, and with Quill present the limit forbids flight. The same file proves the optional call: a model pick among permitted approaches recorded and replayed; a mode without the stage declining it at no extra cost; an unreadable pick falling back; narration that ignores the pick refused; a forbidden approach never offered. `backend/tests/test_decision_store.py` proves the ledger across a restart.

## Requirements on later sprints

| Sprint | Must |
|---|---|
| 035 (scene runner) | Decide once per scene turn, before the first take: `choose`, optionally `decide`, then `record` the decision; for every later take of the turn (a reroll), `replay` the recorded decision instead of choosing again, and plan no decision call. Choosing an intention every turn and keeping the turn's `Choice` in its turn record are 035 milestone 2's ([GOALS](GOALS.md#not-in-this-slice)); the decision ledger stays the record a reroll replays from. |
| 047 (continuity referee) | A recorded decision is the reference for "narration never rewrites a recorded intention". This slice makes the brief carry it and refuses a turn whose brief does not, but a generated reply that contradicts it (or breaks a briefed refusal) is still kept; 047 flags such a breach for review ([GOALS](GOALS.md#not-in-this-slice)), and 045 never rewrites a recorded intention. |

## Not in this slice

| Item | Owner |
|---|---|
| Sampled randomness, scores and probability distributions over options | 045 milestone 3 |
| Comparing deterministic and model-selected decisions across a scene | 045 milestone 2 |
| Decisions that are not one NPC's approach (who answers, a faction's move) | 045 later; [Faction Decision Making](roadmap/sprints/faction-decision-making.md) |
| Showing policy confidence and evidence in a UI | 045 later |
| Model-assisted `planning` | 039 milestone 3 |
