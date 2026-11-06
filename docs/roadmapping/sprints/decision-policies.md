# Bounded Decisions and Director Policies

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

## Open questions
- Which decisions require sampled randomness?
- What justifies expensive inference?
- How should policy confidence and evidence be displayed?
