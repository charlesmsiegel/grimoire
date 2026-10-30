# Inference economics and optional intelligence flags

**Outcome:** Engineering effort is unbounded but model-call budgets are not; per-NPC generation is baseline, extra inference is optional.

## Scope and first proof
Define per-turn/session provider token accounting and flags for planning, epistemic inference, recall, graph lookups and critique; no mandatory call just because an actor exists.

## Subsequent milestones and moonshot
Actual provider-meter integrations, budget guardrails and user-visible cost/latency breakdown.

## Independence and interfaces
Inference policy and usage accounting; may start with synthetic usage; no graph or model provider needed initially.

## Proving scenario
Four present NPCs with only two speaking ones require two RP-generation calls, not four background thinkers.

## First slice status

Implemented as the pure, provisional package `grimoire.core.inference`; its contract is documented in [INFERENCE.md](../../INFERENCE.md). It covers:

- per-turn call plans with opt-in, capped optional stages and bounded retries;
- usage ledgers that never treat missing counts as zero;
- session aggregation.

The proving scenario is an executable test, `backend/tests/test_inference_plan.py::test_four_present_two_speaking_npcs_cost_two_generation_calls`. Usage so far is synthetic only: no provider is integrated.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
  - **First-slice answer:** caller mistakes raise before anything is planned.
  - Declined optional calls are returned with a reason, so a UI can show them.
  - Missing usage is reported as missing (unmetered calls, unreported counts, `complete=False`), never as zero.
  - Nothing is persisted yet, so there is nothing to roll back.
- Which optional later integrations can stay outside the first slice?
  - **First-slice answer:** provider adapters and pricing (013), latency, session caps, persistence and UI.
  - Also outside: job-scoped ledgers and embeddings.
