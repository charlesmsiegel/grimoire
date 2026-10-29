# Measured V0.2.1 cost/quality comparison

**Outcome:** V2 should improve character fidelity without silently increasing ordinary play costs.

## Scope and first proof
Replay synthetic equal-model V0.2.1 comparison with two distinct NPC generation calls; record actual billed input/output, cached tokens, latency and quality.

## Subsequent milestones and moonshot
Matched V2 trials, budget-controlled optional reasoning and long-form voice fidelity regressions.

## Independence and interfaces
Independent evaluation harness; consumes cost flags and RP quality benchmarks.

## Proving scenario
Four characters are present but two respond; V2 does not pay for four separate background decisions.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
