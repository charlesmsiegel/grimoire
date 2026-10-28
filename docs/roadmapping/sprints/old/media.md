# Image Generation, Voice and Audio

**Priority:** optional presentation capabilities; not required for playable text-first RP.

## Goal

Supplement character-driven roleplay with controllable visuals and audio where they add genuine player value, while preserving source identity, privacy, user authorship and predictable model costs.

## Work to plan

- **Image Generation:** produce optional setting backgrounds, character art, portraits and expression/outfit variants through selected image providers or local models. Respect existing gallery, expression asset identity, content-addressed blobs, prompt provenance and import/export.
- **Asset organization:** associate images with a world, character/version, creature definition or campaign scene without overwriting original templates; honor permission/license source, scaling, decoded-pixel budgets, orientation and mobile memory.
- **Voice Roleplay:** optional speech output or voices per character, with clear speaker attribution, version-appropriate voice profile and the choice of local/provider audio; text remains the canonical transcript.
- **Audio Ambience:** optional environmental sound or music tied to scene/location/theme without introducing background inference on each text turn. User can mute, disable, replace or explicitly choose.
- **Latency and cost:** separately budget image/audio jobs, asynchronous generation and caching, with cancellation, approval of imported/generated assets and visible provider/privacy tradeoffs.
- **Accessibility and safety:** transcripts remain available to users who cannot hear audio, visuals have usable descriptions/alt data, and no audio/video content is silently uploaded to an external service.

## Proving scenario

An NPC with a generated outfit expression and a chosen voice appears in VN mode; missing voice/image gracefully falls back to text and portrait. The asset is linked to the correct character version, survives backup/export and has an inspectable provider/cost record. Disabling media has no effect on gameplay rules or historical scene facts.

## Dependencies and boundaries

[Presentation](presentation.md) renders and controls the assets. [World Resolution](world-resolution.md) preserves source-qualified attachment and fallback. [Generation Systems](generation.md) supplies task/model budgets. Media is an optional presentation layer, not part of Mechanics II or compulsory story orchestration.

## Open questions

- Which provider/local media APIs, consent and content-storage policies are desirable for in-app generation?
- Which identity and versioning policy applies to expression/outfit variant generation and later manual replacement?
- How are voice profiles, third-party card voices, sample assets and rights/provenance imported?
- What latency, VRAM/RAM, decoded-pixel and storage budgets are acceptable for Android and modest hardware?
- Does audio ambience follow scene metadata, deterministic cues or explicit user selection?
