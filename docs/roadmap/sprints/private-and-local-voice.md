# Offline Voice, Privacy and Data Retention

**Outcome:** Make voice usable with local inference and meaningful control over what audio leaves a device or survives a session.

## Scope
- Offer provider/local routing for STT/TTS with explicit upload consent and per-feature choices.
- Define retention, deletion and artifact export for raw audio, partial transcript, corrected draft and canonical text.
- Handle resource-constrained Android/desktop devices and truthful offline fallback.

## Milestones
1. Disclose and test current audio/network flow with one speech backend.
2. Add a working local/offline path or explicit unsupported-state behavior.
3. Support switchable private modes and verifiable deletion of noncanonical audio.

## Moonshot
Fully local, low-latency conversational RP runs with private models and voice synthesis, with all stories, recordings and metadata under player control.

## Proving scenario
A user selects local-only voice; the app does not secretly upload audio, and turning recording off leaves normal text play unaffected.

## Independence and interfaces
Independent cross-cutting privacy/runtime sprint; it can inspect providers before other voice capabilities are implemented.

## Open questions
- Is microphone audio ephemeral by default?
- What speech hardware budgets are acceptable?
- How do partial transcript logs and crash traces avoid retaining raw private speech?
