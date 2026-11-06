# NPC goals and bounded autonomy (first slice of sprint 039)

**Status:** first slice of roadmap sprint 039, [NPC Goals And Autonomy](roadmap/sprints/npc-goals-and-autonomy.md): milestone 1 (one NPC pursues a simple goal) and the recorded, explained half of milestone 2 (a choice that refuses a planner's wish). The code is the pure package `grimoire.goals`, plus one optional argument of 027's `npc_brief`. Headless and in memory: an agenda is a value the caller supplies, nothing is stored, and the choice is a proposal that changes no state.

## The rule

An NPC's pursuit of its goals costs **no model call**. Its policy is deterministic: authored goals, its own knowledge and the scene's cast go in, one `Choice` comes out. The choice reaches the NPC's one ordinary `npc_generation` call through its brief ([GENERATION](GENERATION.md#intentions-039)), so a turn in which an NPC acts on its goals plans exactly the calls a turn without goals does ([INFERENCE](INFERENCE.md#the-rule)). The intention adds its own text to that call's input: an aim, an action and the rules refused now, each at most 400 characters. Model-assisted `planning` and `decision` calls stay optional, off by default and are 045's ([Decision Policies](roadmap/sprints/decision-policies.md)). An NPC being present, or having an agenda, never starts a call.

## Agendas

An `Agenda(campaign_id, actor, goals, limits=())` is what the director authors for one NPC in one campaign. It is campaign-scoped, not part of a reusable definition, because its conditions name this campaign's propositions (012).

| Value | Fields | Meaning |
|---|---|---|
| `Goal` | `goal_id`, `aim`, `priority` (1 to 100), `approaches` (1 to 8) | What the NPC is after and how much it matters. Higher priority is tried first; ties keep authored order. |
| `Approach` | `approach_id`, `action`, `needs=()`, `tags=frozenset()` | One concrete thing the NPC might do about the goal, possible only when every `needs` condition holds. Approaches are tried in authored order: earlier is preferred. `tags` say what kind of act it is (`trade`, `threat`, ...) so a limit can forbid it. |
| `Limit` | `limit_id`, `rule`, `forbids` (tags), `when=()` | What the NPC will not do: any approach with a forbidden tag, while every `when` condition holds (always, with none). |
| `Present` | `actor` | `actor` is in the scene as the NPC knows it: in the cast the caller gives `choose` (see [Choosing](#choosing)). |
| `Holds` | `proposition_id`, `stances={believed, suspected}` | The NPC itself holds the proposition: `believed`, `suspected` or `rejected` is its **current** belief's stance; `aware` is holding it at all (a confirmed report received, a report it sent, or any belief of its own, current or superseded), 027's `known_ids` rule. |

Values validate on construction and raise `GoalError` with a `reason`: `invalid_field` (an aim, action or rule that is blank, over 400 characters, or holds a line break, tab or other control character, a line or paragraph separator, a bidi embedding, override or isolate, a byte-order mark, a zero-width space, a word joiner or a Unicode tag character, since each is one line of the NPC's brief, the director must read it as written and an invisible character could split a secret's phrase past the leak screen, while zero-width joiners and non-joiners are ordinary text, as in 023; a bad id, priority, tag or stance; a collection given as one string or a mapping; over the caps of 16 goals, 8 approaches per goal, 16 limits, 8 conditions and 8 tags; a repeated condition; an approach's `needs` or a limit's `when` naming the NPC itself as present), `duplicate_id` (a goal, a goal's approach or a limit), `campaign_mismatch` (the actor or a `Present` actor of another campaign) and `unused_limit` (a limit forbidding any tag no approach has: a mistyped tag would let part of a safety rule lapse silently). Messages cite ids and reasons, never authored text or a refused value.

Construction cannot see the ledger, so a mistyped id in a `Holds` or `Present` would never hold: an approach needing it is never possible and, worse, a limit gated on it never applies. **`check_agenda(agenda, *, propositions, actors)`** refuses that, director-side: `propositions` are the campaign ledger's proposition ids and `actors` its actor instances, and an agenda naming another is `unknown_proposition` or `unknown_actor` (citing the id). Anything but an `Agenda` is `bad_agenda`, and `propositions` or `actors` given as one string, a mapping, or `actors` that are not `InstanceRef`s are `invalid_field`. Run it wherever an agenda is authored, stored or loaded. `conditions_of(agenda)` lists every condition an agenda names, once each.

What the slice models from the sprint's scope: goals and **preferences** (priority, then approach order), **constraints** (limits) and **feasibility** from immediate circumstances (presence) and the NPC's own knowledge. Commitments, abilities, holdings and time are not conditions yet (see [Not in this slice](#not-in-this-slice)).

## Choosing

`choose(agenda, knowledge, *, present, controls, desired=None) -> Choice`:

Refusals are checked in this order, so a player character is refused as one whether or not it is in the scene:

1. **An agenda.** Anything else is `bad_agenda`.
2. **Only its own projection.** `knowledge` must be exactly an `ActorKnowledge` of this actor (`foreign_knowledge`): director knowledge, another actor's projection, a look-alike type or a projection with a list field (it could change after the choice and still compare equal) is refused.
3. **A cast.** `present` and `unrecognised` (default none) are `InstanceRef`s of the agenda's campaign (`bad_present`, `campaign_mismatch`); an actor in both, or the NPC itself unrecognised, is `bad_present`.
4. **Only a director NPC has a policy.** The actor must have exactly one controller and it must be `director`: a player character is `player_actor` (its intentions are its player's), no controller is `unassigned_actor`, two are `ambiguous_control`, and a malformed `controls` is `bad_controls`.
5. **It must be in the scene.** `present` must include the NPC (`absent_actor`).
6. **A wish it has.** `desired` must name an approach of the agenda (`unknown_approach`).
7. **The order.** Goals by priority (higher first, ties in authored order), each goal's approaches in authored order. The first approach whose needs all hold and that no active limit forbids is chosen.

Every approach is recorded in `Choice.considered` in the order tried, as a `Considered(goal_id, approach_id, outcome, unmet, forbidden_by)`: `unmet` (a condition does not hold now; it is `unmet` even when a limit would also forbid it), `forbidden` (possible, but an active limit forbids it), `chosen`, or `outranked` (possible and permitted, but something before it was chosen). An NPC with nothing possible gets a `Choice` with `acts` false and no `goal` or `approach`; that is a choice too.

`Choice.refusals` are the limits that forbid something the NPC could otherwise do now, in authored order: what it is actively refusing in this scene. A limit that only forbids an approach it could not do anyway is not one.

**No omniscience.** Conditions read the NPC's projection and nothing else. What is true (rulings, standing), what the director knows, who really sent a report, unconfirmed evidence and subjects or graph links a proposition is about never change a choice. A false rumour the NPC believes moves it; a truth it never heard does not. Hearing a rumour is not believing it: the default `Holds` needs a current `believed` or `suspected` stance, and an author who wants an NPC that acts on mere hearsay says `aware`. A line the NPC hears aloud this turn is not in its projection either: it moves the choice only once the scene admits it as the NPC's evidence or belief (012), and the caller chooses again.

**The cast is who the NPC knows to be there.** `present` is the scene's cast (033's) as this NPC recognises it, and the caller's word. An actor the NPC could not recognise, such as one in disguise, goes in `unrecognised` instead, or `Present` of its real reference would hold and the NPC would act on a name it does not know. A `Present` is matched by the actor's real reference, so an unrecognised actor is simply absent to the NPC. Who can see whom is [Scene Physical Continuity](roadmap/sprints/scene-physical-continuity.md)'s (041). As a backstop, an action naming the disguised actor is `intention_leak` in the brief when the disguise is registered as a `SecretFact`.

**A choice is for one scene.** `Choice.scene` is `present` with `unrecognised`, and `run_turn` refuses a choice in a turn whose plan has any other actors present (`intention_cast_mismatch`, [In the NPC's call](#in-the-npcs-call)): someone an approach needs may have left, or someone a limit watches for may have arrived, which would otherwise let the limit lapse silently. Choose again for every turn.

### A planner's wish

`desired=(goal_id, approach_id)` names an approach a narrative planner would like (a confrontation, say). It is **recorded, never obeyed**: `Choice.desire` is a `Desire(goal_id, approach_id, outcome)` whose `outcome` is that approach's own `Considered` outcome, and `followed` is true only when it was chosen anyway. An approach the agenda does not have, or a malformed pair, is `unknown_approach`.

`explain(choice)` gives the choice in plain lines for the director and the table's tools: what the NPC pursues and, for a wish, that it follows it, cannot take it now (with the unmet conditions), refuses it (with each limit's id and rule), or prefers another approach. These lines name actors and propositions by id, so they are **director-side text**: never put them in an NPC prompt. Anything but a `Choice` is `bad_choice`; a `Choice` built by hand is `unsealed_choice`.

The sprint's proving scenario is `backend/tests/test_goals_scenarios.py`: the guild's merchant refuses to sell the guest ledger to a buyer it was told carries tales to the river lords, although a planner wants the fight over it, and pursues its goal another way in its one call. Told nothing, it sells, even though canon says the buyer really is an informant; believing a rumour canon refutes, it still refuses. It acts on its own initiative: no one addresses it, and the turn's speakers are the NPCs whose `Choice.acts`, so another NPC with an agenda but nothing it can do now stays silent and costs nothing.

## In the NPC's call

`npc_brief(..., intention=choice)` ([GENERATION](GENERATION.md#intentions-039)) adds to the NPC's own brief what it is after (the goal's aim), what it means to do about it now (the approach's action) and what it will not do here (the refusals' rules). A refused or outranked approach's action is never shown. The brief refuses an intention that is not a `Choice` (`bad_intention`), is another actor's (`foreign_intention`) or was chosen from another projection of this actor's knowledge (`intention_mismatch`: another moment, or one a ledger reload has since changed), and one whose shown text states a secret hidden from the actor (`intention_leak`). Gating an approach on `Holds` of the secret's proposition makes that approach's **action** safe, since the NPC holds it by then. It does not make the goal's **aim** safe (shown whenever any approach of the goal is chosen) or a limit's **rule** (shown whenever it refuses something): an aim or a rule must not state what the NPC may not know. `run_turn` then refuses, before any call, a brief whose choice was made for another scene than the turn's plan has present (`intention_cast_mismatch`): exactly the same actor ids, recognised or not.

## Requirements on later sprints

| Sprint | Must |
|---|---|
| 035 (scene runner) | Choose afresh for every turn's plan, from the same `ActorKnowledge` the brief is built from, with the turn's cast split into who this NPC recognises (`present`) and who it does not (`unrecognised`). A warning heard aloud moves an NPC only once admitted as its evidence or belief, so admit, then choose again. A scene rule may give an NPC whose `Choice.acts` the turn; that is a scene rule, not a model call. Run `check_agenda` against the campaign ledger before an agenda is used. Never let a narrator, a player's text or a model reply author or change an agenda: agendas are director-authored input. |
| Persisting agendas | A campaign record family, registered in `syncable()` (019/028), built through these constructors on read (never unpickled, which skips validation), checked with `check_agenda` against the ledger it is loaded with, and serialised with `tags`, `forbids` and `stances` sorted, since frozenset order changes with the hash seed. |
| 045 (decision policies) | A model-selected choice is an optional, capped `decision` call, never a replacement for this deterministic default. It may pick only among approaches whose outcome is `chosen` or `outranked`, through a new argument of `choose`: never through `desired`, which is a planner's wish that is never obeyed, and never a hand-built `Choice` (sealed, and closed to subclasses). |

## Not in this slice

| Item | Owner |
|---|---|
| Storing agendas, editing them in play, goal progress and completion | 039, next slice |
| Commitments and deadlines (promises owed to someone, by when) | 039 next slice, with [Quests And Commitments](roadmap/sprints/quests-and-commitments.md) |
| Feasibility from abilities (025), holdings (023), possessions (034) and location (041) | 039, later |
| Temporal and resource consequences of autonomous actions | 039 milestone 2 onwards |
| Longer personal plans and self-initiated scene hooks | 039 milestone 3 |
| Who may see whom (disguises, hidden actors) | 041 |
| Model-assisted planning or decisions | 045 |
| Factions' goals | [Faction Decision Making](roadmap/sprints/faction-decision-making.md) |
