# Multiple Human Speakers and Table Audio

**Outcome:** Support multiple player voices at a table without mixing characters, permissions, secrets or speech attribution.

## Scope
- Identify speaker/channel with explicit assignment, not unreviewed biometric assumptions.
- Maintain separately controlled PC drafts, simultaneous speech and shared advancement boundaries.
- Protect private/director audio, spectator roles and multiplayer privacy.

## Milestones
1. Two named local microphone channels produce correctly attributed drafts.
2. Test overlapping speakers and disambiguation instead of silent misattribution.
3. Add remote tabletop voice sessions, mixed human/AI participants and private whispers.

## Moonshot
A virtual tabletop recognizes natural conversational flow and assigns actions to the right human-controlled PCs with negligible friction and explicit privacy guarantees.

## Proving scenario
One player says 'I attack' while another says 'wait'; no NPC action is committed under the wrong PC, and an unknown speaker requires selection.

## Independence and interfaces
Works with explicit per-user channels before any diarization model; local multi-PC text and multiplayer networking are optional integrations.

## Open questions
- Should identity be assigned by device, channel, manual selection or diarization?
- How is cross-talk handled?
- What audio recording is consented to and retained?
