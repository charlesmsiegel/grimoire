# Decision and Generation Systems

**Priority:** incremental improvements to existing infrastructure; don't rebuild delivered tool loops or model routing.

## Goal

Separate choosing and validating what happens from narrating it, while keeping model cost, latency and provider independence under control. A game turn needs authoritative decisions and outcomes, not multiple narrators inventing incompatible state.

## Work to plan

- **Decision Models:** support well-specified candidate actions, legal choices, scoring, probability distribution, sampled choice and recordable randomness. Apply to NPC attitude, tactics, speaker, social reaction, optional expression choices and exceptional uncertainty.
- **Generation Orchestration:** compose relevant world, campaign, audience, history, context budget and selected outcome; stream appropriate character voices and narration without altering the authoritative result. Handle cancellation, retries, model fallback and partial streams coherently.
- **Model Routing and Inference Economics:** independently route Fast/Decision/Primary/Embedding/optional specialist tasks, track actual token/cost/latency/retries, and compare cost per entire player turn or worker job. Avoid gratuitous LLM calls for computable choices.
- **Structured and conditional generation:** read-only tool calls and evidence-led decisions remain bounded; authoring or state changes must pass a separately approved deterministic operation.
- **Auxiliary generation variants:** assess missing user-facing options for write-as-PC, continue-as-character, hypothetical dialogue, polish, brainstorm and translate. Treat results as **non-canonical until deliberately applied**, with the simplest adequate flow (copy, replace draft, append reviewed post or discard), not an arbitrary universal dispatcher.
- Evaluate roleplay coherence, consistency, latency and cost using representative multi-NPC and knowledge-limited scenarios, including consequences whose narration must not contradict already-resolved dice.

## Proving scenarios

Player provokes a jealous NPC; an explicit policy chooses a feasible reaction using attitude and knowledge, records the decision, commits its known consequences and lets the chosen character narrate. In a mechanics-free scene the same process works without constructing a game-system check. An auxiliary hypothetical answer leaves the original scene unchanged.

## Dependencies and boundaries

Build on existing decision and inference packages and bounded tool calling. Read/write multi-step workers are covered by [Agentic Workers](agentic-workers.md); actual game actions by [Mechanics](mechanics.md) and [State Transactions](state-transactions.md). Character and campaign tuning policies are consumers, not new mandatory providers.

## Open questions

- Which decisions deserve explicit distributions and reproducible seeds versus direct authored/story choices?
- When should user-facing auxiliary variants become actual features, and can existing composer/edit actions handle their acceptance without a new dispatcher?
- What evidence/uncertainty should trigger a more expensive decision or tool-using investigation?
- Which task-specific costs, turn budgets, prompt transparency and latency ceilings determine adaptive routing?
