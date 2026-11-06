# Spoken Turn Boundaries, Interruptions and Barge-In

**Outcome:** Make voice turns feel natural without submitting unintended pauses, cutting off player speech or duplicating model actions.

## Scope
- Define end-of-utterance/turn, silence thresholds, interruption, barge-in, cancel and resume policies.
- Keep audio streaming, staged transcript, pending action and committed result as distinct lifecycle states.
- Recover from connection loss, overlapping speech, retries and model stream cancellation.

## Milestones
1. Define a visible, deterministic turn-state machine usable with synthetic audio events.
2. Test push-to-talk and silence-ended turns with editable review before advance.
3. Support interruption of playback and optional interruptible dialogue without half-committed effects.

## Moonshot
Full-duplex conversational interaction supports natural repairs, overlaps and interruptions with auditable control over which spoken content became canonical.

## Proving scenario
The player pauses to think; no reply fires. Interrupting an NPC mid-speech stops sound but does not erase or secretly commit unfinished narrative effects.

## Independence and interfaces
Can be prototyped with recorded waveforms and text events; neither voice casting nor a specific live speech provider is required.

## Open questions
- Which events commit a turn?
- What happens to interrupted generated text and audio?
- Can voice 'undo' reach a previously committed event safely?
