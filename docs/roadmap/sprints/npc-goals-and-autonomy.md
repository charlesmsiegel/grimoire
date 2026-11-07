# Individual NPC Goals and Bounded Autonomy

> **V2 scope correction:** An NPC may have durable goals, but mere presence never launches paid background planning. Local/periodic thought is optional and requires a visible budget. See the [V2 ordered roadmap](../README.md) for hard prerequisites and delivery wave; older references to existing V1 systems are porting targets, not the V2 starting baseline.


**Status:** first slice implemented ([GOALS](../../GOALS.md)): an authored, campaign-scoped `Agenda` (goals with priorities, approaches with typed conditions, limits) and a deterministic policy, `choose`, that picks what one director NPC pursues from its own knowledge projection and the scene's cast, with no model call. The choice reaches the NPC's one ordinary generation call through `npc_brief(..., intention=)` ([GENERATION](../../GENERATION.md#intentions-039)). Milestone 1 is proven, and so is milestone 2's explained choice against a planner's wish (`desired`, `explain`), as an in-memory value: storing each turn's choice is 035 milestone 2's, and 035's runner does not call `choose` yet. Milestone 2's temporal and resource consequences are not proven.

**Planned later work:** storing agendas as a campaign record family (and in `syncable()`), editing them and tracking goal progress; commitments and deadlines; feasibility from abilities (025), holdings (023), possessions (034) and location and sight (041); consequences of autonomous actions in time and resources; milestone 3's longer plans and self-initiated hooks. Model-assisted planning or decisions are 045's, optional and capped.

**Outcome:** NPCs choose credible behavior from motives, knowledge and immediate circumstances instead of following a scene script.

## Scope
- Express goals, preferences, commitments, abilities and constraints for named NPCs.
- Select feasible actions through deterministic policies or bounded decisions, with narrators downstream.
- Give autonomous actions temporal and resource consequences without omniscience.

## Milestones
1. Make one NPC independently pursue a simple goal.
2. Record and explain one policy-based choice conflicting with a desired plot outcome.
3. Support longer personal plans and limited self-initiated scene hooks.

## Moonshot
Long-lived virtual characters develop recognizable personalities, habits and relationships through consistent but revisable memories and goals.

## Proving scenario
A merchant refuses a profitable but personally dangerous trade despite a narrative planner wishing for a confrontation.

## Independence and interfaces
NPCs can act within current scene authority immediately; faction policy and full world simulation are optional consumers.

## Open questions
- What makes an NPC action feasible?
- Which decisions deserve inference rather than deterministic scoring?
- How are player agency and NPC autonomy balanced?
