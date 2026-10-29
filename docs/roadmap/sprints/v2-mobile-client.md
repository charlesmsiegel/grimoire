# Early phone play and synchronized handoff

**Outcome:** Phone play is a primary migration acceptance gate, not a distant presentation moonshot.

## Scope and first proof
Use a minimal phone web/Android client against V2's text API, open a scene offline, and exchange updates/images with desktop using built-in sync.

## Subsequent milestones and moonshot
Background/automatic synchronization, low-friction paired device UX and optimized mobile reading/voice.

## Independence and interfaces
Owns minimal mobile UI and sync wiring, not canonical graph/storage; depends on text runner and sync transport.

## Proving scenario
Add a post on a phone, sync to desktop, and resume without duplicate turns or missing art.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
