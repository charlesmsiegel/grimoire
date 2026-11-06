# NPC goals and bounded autonomy (first slice of sprint 039)

**Status:** first slice of roadmap sprint 039, [NPC Goals And Autonomy](roadmap/sprints/npc-goals-and-autonomy.md): milestone 1 (one NPC pursues a simple goal) and the recorded, explained half of milestone 2 (a choice that refuses a planner's wish). The code is the pure package `grimoire.goals`, plus one optional argument of 027's `npc_brief`. Headless and in memory: an agenda is a value the caller supplies, nothing is stored, and the choice is a proposal that changes no state.

## The rule

An NPC's pursuit of its goals costs **no model call**. Its policy is deterministic: authored goals, its own knowledge and the scene's cast go in, one `Choice` comes out. The choice reaches the NPC's one ordinary `npc_generation` call through its brief ([GENERATION](GENERATION.md#intentions-039)), so a turn in which an NPC acts on its goals plans and bills exactly what a turn without goals does ([INFERENCE](INFERENCE.md#the-rule)). Model-assisted `planning` and `decision` calls stay optional, off by default and are 045's ([Decision Policies](roadmap/sprints/decision-policies.md)). An NPC being present, or having an agenda, never starts a call.

## Agendas

An `Agenda(campaign_id, actor, goals, limits=())` is what the director authors for one NPC in one campaign. It is campaign-scoped, not part of a reusable definition, because its conditions name this campaign's propositions (012).

| Value | Fields | Meaning |
|---|---|---|
| `Goal` | `goal_id`, `aim`, `priority` (1 to 100), `approaches` (1 to 8) | What the NPC is after and how much it matters. Higher priority is tried first; ties keep authored order. |
| `Approach` | `approach_id`, `action`, `needs=()`, `tags=frozenset()` | One concrete thing the NPC might do about the goal, possible only when every `needs` condition holds. Approaches are tried in authored order: earlier is preferred. `tags` say what kind of act it is (`trade`, `threat`, ...) so a limit can forbid it. |
| `Limit` | `limit_id`, `rule`, `forbids` (tags), `when=()` | What the NPC will not do: any approach with a forbidden tag, while every `when` condition holds (always, with none). |
| `Present` | `actor` | The scene's cast includes `actor`. |
| `Holds` | `proposition_id`, `stances={believed, suspected}` | The NPC itself holds the proposition: `believed`, `suspected` or `rejected` is its **current** belief's stance; `aware` is holding it at all (a confirmed report received, a report it sent, or any belief of its own, current or superseded), 027's `known_ids` rule. |

Values validate on construction and raise `GoalError` with a `reason`: `invalid_field` (blank, over 400 characters or with a control character; a bad id, priority, tag or stance; a collection given as one string or a mapping; over the caps of 16 goals, 8 approaches per goal, 16 limits, 8 conditions and 8 tags; a repeated condition; an approach needing its own NPC present), `duplicate_id` (a goal, a goal's approach or a limit), `campaign_mismatch` (the actor or a `Present` actor of another campaign) and `unused_limit` (a limit that forbids no tag any approach has: a mistyped safety rule must never lapse silently). Messages cite ids, never authored text.

What the slice models from the sprint's scope: goals and **preferences** (priority, then approach order), **constraints** (limits) and **feasibility** from immediate circumstances (presence) and the NPC's own knowledge. Commitments, abilities, holdings and time are not conditions yet (see [Not in this slice](#not-in-this-slice)).

## Choosing

`choose(agenda, knowledge, *, present, controls, desired=None) -> Choice`:

1. **Only a director NPC has a policy.** The actor must have exactly one controller and it must be `director`: a player character is `player_actor` (its intentions are its player's), no controller is `unassigned_actor`, two are `ambiguous_control`, and a malformed `controls` is `bad_controls`.
2. **Only its own projection.** `knowledge` must be exactly an `ActorKnowledge` of this actor (`foreign_knowledge`): director knowledge, another actor's projection or a look-alike type is refused.
3. **It must be in the scene.** `present` is the cast as `InstanceRef`s of the agenda's campaign (`bad_present`, `campaign_mismatch`) and must include the NPC (`absent_actor`).
4. **The order.** Goals by priority (higher first, ties in authored order), each goal's approaches in authored order. The first approach whose needs all hold and that no active limit forbids is chosen.

Every approach is recorded in `Choice.considered` in the order tried, as a `Considered(goal_id, approach_id, outcome, unmet, forbidden_by)`: `unmet` (a condition does not hold now; it is `unmet` even when a limit would also forbid it), `forbidden` (possible, but an active limit forbids it), `chosen`, or `outranked` (possible and permitted, but something before it was chosen). An NPC with nothing possible gets a `Choice` with `acts` false and no `goal` or `approach`; that is a choice too.

`Choice.refusals` are the limits that forbid something the NPC could otherwise do now, in authored order: what it is actively refusing in this scene. A limit that only forbids an approach it could not do anyway is not one.

**No omniscience.** Conditions read the NPC's projection and nothing else. What is true (rulings, standing), what the director knows, who really sent a report, unconfirmed evidence and subjects or graph links a proposition is about never change a choice. A false rumour the NPC believes moves it; a truth it never heard does not. Hearing a rumour is not believing it: the default `Holds` needs a current `believed` or `suspected` stance, and an author who wants an NPC that acts on mere hearsay says `aware`.

**The cast is the caller's word.** `present` is who is in the scene (033's cast). An NPC "sees" every present actor as itself: a disguised actor is matched by its real reference. Who can see whom is [Scene Physical Continuity](roadmap/sprints/scene-physical-continuity.md)'s (041); until then a director should not gate an approach on the presence of an actor the NPC could not recognise.

### A planner's wish

`desired=(goal_id, approach_id)` names an approach a narrative planner would like (a confrontation, say). It is **recorded, never obeyed**: `Choice.desire` is a `Desire(goal_id, approach_id, outcome)` whose `outcome` is that approach's own `Considered` outcome, and `followed` is true only when it was chosen anyway. An approach the agenda does not have, or a malformed pair, is `unknown_approach`.

`explain(choice)` gives the choice in plain lines for the director and the table's tools: what the NPC pursues and, for a wish, that it follows it, cannot take it now (with the unmet conditions), refuses it (with each limit's id and rule), or prefers another approach. These lines name actors and propositions by id, so they are **director-side text**: never put them in an NPC prompt.

The sprint's proving scenario is `backend/tests/test_goals_scenarios.py`: the guild's merchant refuses to sell the guest ledger to a buyer it was told carries tales to the river lords, although a planner wants the fight over it, and pursues its goal another way in its one call. Told nothing, it sells, even though canon says the buyer really is an informant; believing a rumour canon refutes, it still refuses.

## In the NPC's call

`npc_brief(..., intention=choice)` ([GENERATION](GENERATION.md#intentions-039)) adds to the NPC's own brief what it is after (the goal's aim), what it means to do about it now (the approach's action) and what it will not do here (the refusals' rules). A refused or outranked approach's action is never shown. The brief refuses an intention that is not a `Choice` (`bad_intention`), is another actor's (`foreign_intention`) or was chosen from another projection of this actor's knowledge (`intention_mismatch`: another moment, or one a ledger reload has since changed), and one whose shown text states a secret hidden from the actor (`intention_leak`). An approach gated on `Holds` of the secret's proposition is safe: the NPC holds it by then. Shown intention text counts as exposed, so a reply stating the NPC's own aim is no leak.

## Requirements on later sprints

| Sprint | Must |
|---|---|
| 035 (scene runner) | Choose from the same `ActorKnowledge` the brief is built from, and the same cast the turn plans. A scene rule may give an NPC whose `Choice.acts` the turn; that is a scene rule, not a model call. Never let a narrator, a player's text or a model reply author or change an agenda: agendas are director-authored input. |
| Persisting agendas | A campaign record family, registered in `syncable()` (019/028) and validated on read like these values. |
| 045 (decision policies) | A model-selected choice is an optional, capped `decision` call, never a replacement for this deterministic default. |

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
