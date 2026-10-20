# Fiction-first architecture

## Objectives
Sustained coherent roleplay; distinct voices and consistent context; low inference costs; fewer hallucinations; deterministic, auditable state transitions; support for both mechanics-free campaigns and multiple distinct rule families.

## Model and system responsibilities
1. Read authoritative world, campaign, scene, character, relationship, resource and rule state.
2. Interpret open-ended player input when needed (potentially an LLM).
3. Enumerate eligible NPC responses or combat actions; assess attitude, objectives, tactics and constraints using deterministic logic or a lightweight decision model.
4. Sample a choice from an explicit distribution; record choice and random information where practical.
5. Apply any ruleset-specific resolution, including dice rolls, damage, conditions, resources and outcomes.
6. Commit authoritative state transitions.
7. Let an LLM narrate and voice the chosen action and established outcome without inventing contradictory mechanical results.

The pipeline may vary by situation. Creative obstacle proposals, ambiguous intent, and unsupported edge cases can be LLM-driven, with proposals then validated and resolved by the authoritative systems. Avoid unnecessary model calls.

## Examples
**Social response:** Player insults an NPC. Stored relationship, personality, embarrassment and threat level inform choices such as withdraw, deflect, retaliate or negotiate. A decision policy samples an action; the state records resentment and relevant consequences; a narrative model renders consistent dialogue.

**Combat:** Enumerate legal actions, score using strategy and personality, sample an attempt, roll applicable dice and resolve mechanics, commit damage/conditions, then narrate. A miss cannot become a hit merely for dramatic prose.

## Three compute tiers
1. **Deterministic:** rules, state, legality, clocks, calculations, resource accounting, random sampling and dice.
2. **Lightweight inference:** attitude, speaker choice, action scoring and classification.
3. **Generative inference:** dialogue, description, proposals, hard-to-formalize interpretation and unusual situations.

Measure total **cost per player turn**, not only generation tokens; include retrieval, decision calls, retries and latency. Multi-NPC decisions may be batched. Consistent authoritative state reduces opportunities for hallucination but does not eliminate prose errors. Explicit decisions and seeded randomness aid reproducibility. Models should be substitutable without changing rules or state transitions.

## Different rulesets must stay different
- D&D: combat, inventory, resources, advancement and exploration.
- World of Darkness: supernatural abilities, contested social and political action, reputation and long-lived consequences.
- Ars Magica: troupe play, seasons, covenant resources, research, flexible magic and laboratories.
- PbtA: fiction-triggered moves, partial successes, GM moves and playbooks, not merely DC checks.

**Fiction first, mechanics second:** fictional circumstances determine whether and how mechanics trigger. Mechanics then determine outcomes, rather than permitting narration to secretly override rolls.

## Engineering priorities
Reliable state updates, separation of narratorial freedom from rules authority, routing different tasks to different models, decision-model confidence and fallback, controlled sampling and evaluation. Preserve player agency, and don't manipulate outcomes under the guise of dramatic pacing or adaptation.

## Optional full agentic workers

A third mode complements lightweight decision models and single-shot narrative generation: **multi-step, tool-using Agentic Workers**, suitable for world maintenance, explicit investigation, scenario preparation and exceptional complex tasks. Use existing 01g tool-loop budgets/routing and roadmap-12 investigation infrastructure; avoid adding agent loops to every routine roleplay turn. Tools can read/propose; guarded deterministic code is authoritative for validated mutation. External Claude/Codex and hand-edited Markdown/JSON remain fully supported alongside an in-app experience. See [agentic-workers.md](agentic-workers.md).
