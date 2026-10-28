# Voice-first Grimoire — primary architectural open question

Voice is not one feature. It is **how a player interacts with the application, performs a character, addresses a director and hears a cast**. Merely bolting transcription and TTS onto the composer would miss the central design problem.

## The decision to investigate first

**How does the app distinguish these intents, with enough reliability to trust it hands-free?**

| Spoken input | Intended meaning | Canonical effect |
|---|---|---|
| "Open inventory" | Navigate app UI | None |
| "I open the chest" | PC action in fiction | Draft/accepted action, possibly effects later |
| "Tell the narrator to make the room ominous" | Director/out-of-character guidance | Generation guidance, no fact created |
| "I say, 'open the inventory'" | PC dialogue | Quoted in-character words, not a command |
| "Undo the last move" | Potential edit/retcon command | Preview/confirm if already committed |
| Background TV or another person's speech | Not authorized user input | None |

**Candidate strategies to compare:**

1. **Explicit control mode / push-to-talk**: distinguish *Speak as PC*, *Narrate action*, *Director* and *App command*. Understandable and safest, but risks excessive mode friction.
2. **Wake phrase or command modifier**: unmarked speech defaults to one role; a phrase routes commands and guidance. Easier to stay immersed but prone to false positives and accidental quoting.
3. **Contextual continuous routing**: infer speaker/intent and disambiguate as needed. Most natural, but must prove error rates, privacy and rollback behavior before it can be trusted.

These are alternatives to **research**, not architecture already approved. A promising first experiment uses typed/recorded transcripts to test the intent router and visible mode states before committing to STT providers or always-on microphones.

## Thin proposed interface between independent voice work

A staged utterance can carry: user/controller; requested/selected PC; capture mode; transcribed text and uncertain spans; intended destination (`ui_command`, `pc_dialogue`, `pc_action`, `director_instruction`, `out_of_character`, `unknown`); confidence/evidence; review state; authorization and target revision. This is a **pre-spec sketch**, not a mandatory ontology. Separate audio recording, draft text, accepted post, committed operation and spoken playback.

Audio is **not the source of truth for game events**. Accepted scene text, durable decisions/effects and reviewed state stay canonical in current human-editable files. Voice-generated narration or performances cannot create facts without the ordinary approval/effect boundary. Stopping playback need not undo a resolved action; canceling an unaccepted draft must not commit one.

## Independent sprints and why they are separate

| Sprint | First proof | Moonshot |
|---|---|---|
| [Command and intent routing](sprints/spoken-command-and-intent.md) | Navigate vs quote vs speak as PC without mistakes | Entire game hands-free |
| [Speech-to-draft](sprints/speech-to-draft.md) | Editable dictated post | Natural multilingual speech writing |
| [Turn-taking](sprints/conversation-turn-taking.md) | Safe pause/interrupt boundaries | Full duplex barge-in |
| [Character performance](sprints/character-voice-performance.md) | Two distinct voices over canonical text | Long-form audio-drama acting |
| [Live voice RP](sprints/live-voice-roleplay.md) | Text-synced one-PC/one-NPC voice scene | Real-time improvising cast |
| [Multi-speaker table](sprints/multi-speaker-table-audio.md) | Two known audio channels with correct attribution | Natural tabletop conversation |
| [Soundscapes](sprints/soundscapes-and-audio-cues.md) | Optional scene ambience | Spatial dramatic sound design |
| [Private/local speech](sprints/private-and-local-voice.md) | Inspect and control where audio goes | Fully local live RP |
| [Accessibility/evaluation](sprints/voice-accessibility-and-testing.md) | Safe commands in noise and with ambiguity | Broadly inclusive speech controls |

Each can be prototyped without waiting for the rest: STT need not perform TTS; character TTS can read saved text without listening; a command router can consume text; turn-taking can run on synthetic audio events. Live voice integrates them when the interaction contract has evidence behind it.

## Critical design questions and evidence

- **What should be the default microphone state?** Explicit push-to-talk is a plausible baseline, not an entitlement to background listening.
- **Who is speaking?** Controller, PC persona, NPC performance, director and app operator are distinct identities and permissions.
- **When has speech been submitted?** Partial recognition, a silent pause, an interrupted reply and an accepted action cannot be conflated.
- **What requires confirmation?** Navigation may be instant; deletion, committing action effects, edits and ambiguous utterances must have deliberate policies.
- **Can the player correct errors without losing immersion?** Draft repair and fast mode recovery matter as much as recognition accuracy.
- **How fast is fast enough?** Measure end-to-end perceived turn latency, interruption response and erroneous advance, not just audio token throughput.
- **What leaves the device?** Explicit routing/consent for STT/TTS providers, ephemeral microphone buffers, retained audio and captions; local-first operation remains important.
- **How is performance controlled?** TTS voices, emotions and pronunciation must respect actor versions, user authoring, rights and the accepted transcript.
- **How do multiple real people interact?** Prefer known channels/permissions before speculative biometric speaker recognition.

The early trial should include all six sample utterances above, difficult invented names, background voices, partial sentences, interruption, ambiguity and a completed hands-free scene. Judge **unintended command/commit rate**, recovery time, speaker attribution, immersion, accessibility, cost and latency. A high recognition score alone does not qualify command safety.

## Expansive goal, without a prerequisite trap

The moonshot is a living, responsive tabletop or immersive theater: a cast with distinct personalities and voices, meaningful interruptions, sound design and realistic consequences of player speech. That can eventually connect to [Immersive World Interface](sprints/immersive-world-interface.md), [NPC Goals](sprints/npc-goals-and-autonomy.md), [Narrative Arcs](sprints/narrative-arcs.md) and [Story Quality Benchmarks](sprints/story-quality-benchmarks.md). But ordinary roleplay, typing and accessibility must never be subordinated to this vision.
