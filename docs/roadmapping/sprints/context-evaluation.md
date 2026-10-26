# Context, Memory, Evaluation and Retrieval

**Priority:** continuous reliability improvements on top of existing prompt/decision/retrieval infrastructure.

## Goal

Grimoire reliably retrieves the right things, knows what it knows, explains why a context item was included, avoids hallucinating persistent facts and can measure improvements in cost, latency and roleplay consistency.

## Work to plan

- **Memory Consolidation:** extract, summarize, reconcile and retain facts, characters, promises, threads, recent events and historical evidence without overwriting contradictory or uncertain records.
- **Knowledge Management:** distinguish director knowledge, player visibility and individual NPC beliefs; build audience-aware provenance, source-qualified retrieval and consistency with external edits.
- **Context Assembly and Retrieval:** choose relevant lore, active cast, temporal state, optional inventory/mechanics and historical memories within a bounded token budget. Avoid eager inclusion of entire imported worlds or irrelevant old transcripts.
- **Prompt inspectability:** expose inclusion/exclusion reasons, triggering terms, versions, audience and budget shedding; retain old captured prompts with privacy rules rather than reconstructing history from today's files.
- **Quality Assurance and Agent Evaluation:** establish representative expected behavior for routing, decision quality, retrieval recall, epistemic boundaries, continuity, generated prose and cost per turn/job. Generated/synthetic test data needs valid labels/ground truth and meaningful sample counts.
- **Performance Optimization:** profile critical retrieval and context paths, cache only derived views that can be invalidated/rebuilt, and measure wall-clock cost as well as tokens.
- **Optional historical replay:** if there is a genuine unmet debugging use case, rerun a *captured old prompt* against a chosen model for ephemeral A/B comparison. Seed support is best-effort, not a determinism guarantee. The feature may be unnecessary because prompt capture and forks already exist; do not promise a full historical state-replay engine.
- **Library activity awareness:** useful source-change context and impact on dependent campaigns belongs to the scoped [World Maintenance](world-maintenance.md) change/provenance model, not a separate hidden activity database.

## Proving scenario

An NPC does not know a secret witnessed by another cast member. The prompt inspector explains which historical fact/lore excerpts were selected and omitted. Updating a world source changes only the correct effective references; measured recall improves without increasing average turn cost beyond an explicit budget. Replaying a past captured prompt is read-only if such a tool is later justified.

## Dependencies and boundaries

[World Resolution](world-resolution.md) handles source identity; [Generation Systems](generation.md) owns model decisions/routing; [Agentic Workers](agentic-workers.md) owns exceptional multi-step investigations; [Quality Gate](quality-gate.md) checks code quality, not product evaluation quality.

## Open questions

- What is the minimal canonical representation of knowledge, source evidence, uncertainty and perspective that avoids competing fact ledgers?
- Which prompt provenance fields and inclusion reasons must be queryable for old scenes?
- Which eval metrics, labeled/generated case sets and baselines actually predict better long-form RP?
- What latency/cost tradeoffs justify escalation to tool-using search versus ordinary indexed retrieval?
- Is historical model replay worth a distinct user feature beyond existing prompt history, reroll, regeneration and forks?
- How should long history and privacy-sensitive or adult roleplay data be retained, exported or purged?
