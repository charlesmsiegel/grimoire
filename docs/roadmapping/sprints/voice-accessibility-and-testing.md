# Robust, Accessible Voice Interaction and Evaluation

**Outcome:** Voice is an optional assistive control that works across noisy rooms, different speech patterns and device limitations.

## Scope
- Test recognizer/command errors, accents, speech disfluency, quiet speech, background voices and interruptions.
- Provide visible transcripts, captions, replay/pause, keyboard equivalents and easy mode recovery.
- Measure unintended-command rate, misattribution, latency and task completion, not just word error rate.

## Milestones
1. Define a privacy-safe voice task test corpus and failure taxonomy.
2. Verify commands remain safe under synthetic noise, ambiguous phrases and simultaneous audio.
3. Test hands-free campaign operation with accessibility users and alternative input paths.

## Moonshot
Adaptive voice interaction accommodates diverse speech abilities and situational constraints without profiling a player or assuming continuous microphone access.

## Proving scenario
Background speech cannot issue 'delete scene'; a quiet or atypical speaker can confirm a safe action and return to typed controls instantly.

## Independence and interfaces
Builds acceptance harnesses around recorded or synthetic events before any live provider is chosen; supplies quality gates for every other voice sprint.

## Open questions
- What command false-positive threshold is tolerable?
- What accessibility scenarios are mandatory?
- How can tests preserve privacy and avoid language/accent bias?
