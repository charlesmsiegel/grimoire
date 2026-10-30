# Roleplay Quality Benchmarks and Human Evaluation

> **V2 scope correction:** Build a synthetic 0.2.1 baseline with separately generated NPC contributions; measure character-voice fidelity, agency, leak rate, billed tokens and latency. Do not certify cheaper V2 prompts unless actual quality and cost are compared. See the [V2 ordered roadmap](../README.md) for prerequisites, waves and current first-slice proof. Older references to shipped V1 APIs describe behavior to *port*, not V2 capabilities.


> **First slice status:** the automatic offline gate is implemented — see [Story-quality gate](../../evals/STORY_QUALITY.md). It scores recorded runs against a **synthetic** V0.2.1-shaped baseline, so a pass is a regression check, not a cost or quality certification (measured V0.2.1 comparison is [V1 Cost And Voice Regression](v1-cost-and-voice-regression.md)). Blinded human comparison, long-horizon branching, NPC agency evaluation and the player-trust evaluation from [Player Sovereignty](player-sovereignty.md) (milestone 3) remain open.

**Outcome:** Measure whether changes make Grimoire a better roleplay partner, not merely a system that passes technical tests.

## Scope
- Build synthetic and curated scenarios testing agency, continuity, pacing, emotion, distinct voices, knowledge limits and creativity.
- Separate technical correctness from subjective dramatic satisfaction; collect blinded human comparisons where useful.
- Measure long-horizon behavior and regression under comparable model/budget conditions.

## Milestones
1. Create a small repeatable benchmark for player choice and continuity.
2. Add long-form branching, NPC agency and characterization evaluations.
3. Validate benchmark predictions against actual player assessments.

## Moonshot
A realistic interactive RP benchmark measures multi-hour character development and satisfying consequences without rewarding railroading or generic sentiment.

## Proving scenario
A candidate change that improves prose fluency but makes NPCs omniscient or repeatedly negates player choices is rejected by independent checks.

## Independence and interfaces
Independent quality harness; CI code quality is not a substitute and no particular engine feature is prerequisite.

## Open questions
- What measures correlate with player enjoyment?
- How do we account for subjective taste and genre?
- How can benchmarks avoid contamination and optimizing for a narrow style?
