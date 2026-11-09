# Campaign Tuning and Adaptive Difficulty

**Priority:** introduce typed settings alongside real consumers; no giant policy framework first.

## Goal

Let a player tune the **kind of roleplay and challenge** they want, without secretly changing already-established facts, dice results or ruleset meanings.

## Controls to develop

| Setting | Intended range / behavior |
|---|---|
| **Difficulty** | Casual → Brutal: challenge requirements and necessary preparation |
| **Drama–Simulation** | Drama → Simulation: how much to prioritize dramatic selection vs independent world dynamics |
| **Pacing** | Slow Burn → Fast-Paced: event frequency and scene transitions |
| **Tone** | Lighthearted → Grim: presentation and emotional atmosphere |
| **Lethality** | Forgiving → Deadly: probability and severity of lasting harm |
| **Plot Pressure** | Sandbox → Directed: imposed dilemmas, deadlines and external demands |
| **Scarcity** | Abundant → Scarce: resources and acquisition opportunities |
| **Spotlight** | Ensemble → Protagonist: distribution of attention among characters |
| **Adaptive Difficulty** | Optional **checkbox**, not another slider; respond to performance by changing future obstacles/resources, never rerolling a loss |

## Work to plan

- Define typed persistent campaign settings, inherited defaults/presets and override behavior, with a clear writer and migration story.
- Identify at least one concrete **consumer** for each setting before generalizing a policy engine: speaker/action choice, obstacle planner, offscreen simulation, scene suggestions, world-resource generation or narration.
- Keep Difficulty, Lethality and Drama–Simulation independent; an intellectually challenging political game need not be deadly. Mechanics for a selected ruleset remain authoritative.
- Allow per-campaign editing and explain what effect a setting has *going forward*, including when a setting is changed mid-campaign.
- Evaluate clearly differentiated play on comparable scenes and worlds. Settings must not be decorative text tokens or invisible manipulations of established results.

## Proving scenario

Two campaigns share the same setting and mechanics: one uses slow, exploratory, low-lethality play; the other has urgent external deadlines and scarce resources. Their generated opportunities differ, but a recorded mechanical result is not secretly altered to achieve a preferred story beat.

## Dependencies and boundaries

[Generation Systems](generation.md) and [Societies](societies.md) consume decision preferences; [Challenges](challenges.md), [World Simulation](world-simulation.md) and [Narrative Design](storytelling.md) consume world/challenge/pacing policies; [Mechanics](mechanics.md) keeps roll rules unchanged.

## Open questions

- What settings schema/defaults, presets, override/migration behavior and UI best fit existing campaign settings?
- Which actual subsystem consumes each control, and what counts as meaningful visible impact?
- How are style/pacing policies calibrated across rulesets and mechanics-free play?
- What disclosure/consent should accompany adaptive difficulty and changes made during a campaign?
- How do conflicting dials resolve—for example high difficulty, low lethality, high simulation and protagonist spotlight?
