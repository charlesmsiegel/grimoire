# Actor-Specific Inference and Multi-Agent RP Experiments

**Outcome:** Determine when separate knowledge-limited character reasoning improves roleplay compared with one centrally prompted narrator.

## Scope and ownership
- Compare one integrated narrator against independently contextualized actor decision calls with strict actor-specific knowledge and goals.
- Aggregate proposals and resolve incompatible speaker/action plans through bounded director policy rather than open-ended agent negotiations.
- Measure character fidelity, secrets leakage, creative diversity, latency/cost and contradictory outcomes on paired scenarios.
- Prevent independent actor generations from directly committing state or overriding user intentions.

## Milestones
1. Run a blinded synthetic comparison of single-narrator and separate two-actor decisions with equal context/cost reporting.
2. Prototype bounded actor-specific plan proposals and a deterministic arbitration step.
3. Investigate large cast ensembles, attention routing and adaptive selection of which actors warrant independent inference.

## Moonshot
A fictional cast possesses genuinely separable perspectives and strategic decisions, while an efficient director coordinates the ensemble into one coherent evolving story.

## Proving scenario
Two characters with different private knowledge respond differently to an accusation; actor-specific calls never leak secret prompts to the other and cannot each commit conflicting turns.

## Interfaces and independence
Starts as a read-only [Story Quality Benchmarks](story-quality-benchmarks.md) experiment with current model routes; uses [Knowledge and Perspective](knowledge-and-perspective.md) for mature integration. Does not require generalized [Agentic Investigations](agentic-investigations.md).

## Open questions
- When is independent actor inference worth cost/latency?
- How does an arbiter preserve independent agency?
- What guard prevents cross-actor secret leakage?
