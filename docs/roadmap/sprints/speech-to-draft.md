# Speech-to-Draft and Spoken Player Input

**Outcome:** Let a player speak rather than type while keeping their words editable, attributed and under their control.

## Scope
- Support explicit push-to-talk or recording-to-draft with streaming/final transcripts, punctuation, corrections and language selection.
- Distinguish transcription fidelity from rewriting, and show confidence/uncertain words where useful.
- Route an approved draft into existing PC dialogue or action composer with no automatic canonical effect.

## Milestones
1. Transcribe a short player utterance into an editable scene draft.
2. Handle pauses, corrections, proper names and long dictated posts with clear start/stop controls.
3. Offer natural low-latency mixed speech/typing and configurable transcript correction.

## Moonshot
Multilingual, low-latency voice authoring understands character names and campaign jargon without silently rewriting the player's narrative intent.

## Proving scenario
A player dictates an invented NPC name and says 'no, I meant the west gate'; the draft is editable and nothing advances until the player explicitly submits.

## Independence and interfaces
Can ship over existing composer and one STT provider. Does not require live NPC speech, full-duplex sessions or command recognition.

## Open questions
- Is push-to-talk, toggle recording or wake-word capture the right default?
- When may partial transcription appear in the composer?
- How much correction can occur without changing intended meaning?
