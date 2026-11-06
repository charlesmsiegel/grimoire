# Low-Latency Live Spoken Roleplay

**Outcome:** Have genuine flowing spoken scenes with NPCs while preserving character agency, author control and committed story outcomes.

## Scope
- Coordinate incoming player speech, director intent, selected NPC responses, streaming spoken output and authoritative scene text.
- Respect chosen player role, interruption, knowledge, rules and explicit state-commit boundaries.
- Offer latency and cost controls, text fallback, transcript review and clear audio/visual turn status.

## Milestones
1. Run a one-PC/one-NPC voice session with synced visible transcript and explicit advance semantics.
2. Add multiple NPCs, player barge-in and bounded reaction choices.
3. Investigate near-real-time emotional dialogue, mixed audio/text and natural interruptions.

## Moonshot
A multi-character cast improvises a believable stage play with the player in real time—emotion, nonverbal beats and dramatic timing—without losing persistent continuity.

## Proving scenario
During a tense conversation, a model suggests an ally has surrendered; audio may convey hesitation, but game state changes only through an authorized accepted outcome.

## Independence and interfaces
A composition sprint that can prototype with existing STT/TTS turn envelopes; lower-level voice sprints remain independently valuable even if live sessions are never built.

## Open questions
- Must canonical dialogue be fully generated before audio starts?
- How do voice interruptions affect model-run state?
- Which latency-versus-fidelity tradeoffs are acceptable?
