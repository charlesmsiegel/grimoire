# Prompt Evidence, Replay and Context Evaluation

**Outcome:** Explain why a model saw a fact, why it missed another and whether retrieval changes actually improve RP.

## Scope
- Expose historical prompt snapshots, audience, versions, selected/omitted chunks and budget shedding.
- Run labeled tests for recall, leakage, contradiction, latency and cost with appropriate unknown states.
- Keep replay of a captured prompt read-only and optional; don't invent a second historical state engine.

## Milestones
1. Show inclusion/exclusion evidence for a real turn.
2. Measure recall and leakage on synthetic multi-perspective campaigns.
3. Compare prompt packing variants without mutating canonical history.

## Proving scenario
A failed NPC response is traced to missing evidence; an old captured prompt is inspected after today's world changes without being silently reconstructed.

## Independence and interfaces
Uses current prompt capture, retrieval and eval fixtures; code-quality grading is a separate project.

## Open questions
- What evidence must be captured at generation time?
- Which tests predict better roleplay?
- Is historical model rerun more useful than existing replay/forks?
