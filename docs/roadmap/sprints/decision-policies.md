# Bounded Decisions and Director Policies

> **V2 scope correction:** NPC decision selection must be optional and flagged when it adds a billable model call; independent NPC *writing* remains required. Use deterministic rules and actor evidence first. See the [V2 ordered roadmap](../README.md) for hard prerequisites and delivery wave; older references to existing V1 systems are porting targets, not the V2 starting baseline.


**Outcome:** Choose and record feasible NPC/world actions before narration, with reproducibility where appropriate.

## Scope
- Represent candidate actions, constraints, scores, probabilities, chosen action and optional sampled randomness.
- Use deterministic rules before bounded decision inference; enforce actor knowledge and permissions.
- Keep narration from rewriting recorded intentions or outcomes.

## Milestones
1. Record one attitude/tactics decision with evidence.
2. Compare deterministic and model-selected decisions in a scene.
3. Support adaptive policies with explainable distributions and calibration.

## Moonshot
An inspectable director can coordinate hundreds of plausible agents using bounded hierarchical decisions, not hundreds of costly independent narrators.

## Proving scenario
An NPC chooses to retreat based on fear, constraints and available exits; retrying narration doesn't change that already-recorded choice.

## Independence and interfaces
Can layer over the current Decision model role; mechanical action legality is delegated to the active ruleset if any.

## Inherited from Wave 5

The [Wave 5 audit](../README.md#wave-5-status) records what this sprint builds on.
- **039 ([GOALS](../../GOALS.md#requirements-on-later-sprints)) is the deterministic default.** A model-selected decision is an optional, capped `decision` call that picks only among approaches `choose` found `chosen` or `outranked`, through a new argument of `choose`; never through `desired` (a planner's wish, never obeyed) and never a hand-built `Choice`.
- **The recorded choice is 035's.** 039's `Choice` and `explain()` are in-memory values; 035 milestone 2 stores each turn's choice in its turn record. This sprint's "already-recorded choice" proof needs that record, or its own decision record.
- **Narration is not checked against an intention yet.** A reply that breaks a briefed refusal is kept like any other line; [Narration Continuity Referee](narration-continuity-referee.md) (047) flags such a breach. This sprint never rewrites a recorded intention.

## Open questions
- Which decisions require sampled randomness?
- What justifies expensive inference?
- How should policy confidence and evidence be displayed?
