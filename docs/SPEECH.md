# Private speech boundary and synthetic safety tests

`grimoire.speech` implements the headless first-useful boundaries for 061 and
067. It does **not** implement microphone capture, acoustic recognition,
playback, or live voice roleplay. `grimoire.voice` continues to own written
characterization. Existing `intent` values remain unexecuted hypotheses.

## Explicit routing and truthful fallback

`SpeechSession` has separate STT and TTS `Selection`s: `off` (default),
`local_only`, or `provider`, each naming a backend explicitly. Recording is
off by default. `describe("stt")` / `describe("tts")` disclose status, audio
versus text input, network use, locality, backend retention, billing and text
fallback. The only shipped backend, `unsupported`, reports unavailable for
both features and never runs recognition or synthesis. Missing engines,
missing features and device-resource unavailability are unavailable states,
not a reason to switch to a remote service.

An embedding application can provide a trusted synchronous `Backend` adapter.
Local-only requires the selected adapter to declare local execution, no
network and no input retention. Unknown declarations fail closed in that
mode. Provider mode requires separate backend-bound upload consent for STT
**audio** and TTS **text**. Consent for one feature does not authorize the other.
All modes refuse paid or unknown-billing adapters with `spending_unsupported`;
there is no speech spending/accounting integration yet. No paid backend or
network client ships here. Adapter claims are trusted configuration, not OS
enforcement: a malicious plugin can lie, and must not be registered as local.
Provider retention, if declared, is outside session deletion's control.

```python
from grimoire.speech import Policy, Selection, SpeechSession

session = SpeechSession(policy=Policy(
    stt=Selection("local_only", "unsupported"), recording=True))
assert session.perform(1, "stt", b"synthetic input", capture_token=session.capture_token).status == "unavailable"
# No microphone permission, network request, or hidden fallback occurs.
```

Calls are serialized; adapters must bound their own running time and must not
call back into the session. Configuration/deletion waits for an in-flight call
and then purges session-owned artifacts. This is not cancellation of a hung
adapter. Exceptions become payload-free `backend_failed`, without logging the
provider message, retrying, or changing routing. Malformed/oversized output is
`invalid_output`. Audio is bounded to 4 MiB, text to 16,384 characters and
recognition alternatives to eight bounded strings. Real device budgets and
acoustic latency remain unmeasured.

## Retention, deletion and retries

Retention defaults to none. `Policy.retain` independently permits the latest
`raw_audio`, `provisional_transcript`, `corrected_draft` and `synthesized_audio`
for this session only. No files, crash logs or canonical records are written.
The provisional transcript is a synchronous STT result awaiting review, not a
streaming partial-recognition facility. `correct_draft(text)` changes only the
optional noncanonical draft artifact. Successful results return transcript or
audio explicitly; callers own those returned values regardless of retention.

`export(frozenset({...}))` returns only selected, retained artifacts; `export()`
returns all retained artifacts. `delete()` removes the session's references
and invalidates pending commands; a subsequent export is empty. Configuration,
recording-off, context change, recovery and close also purge artifacts and
pending commands. Close is terminal for speech but keyboard routing remains
available. These operations do not promise secure memory erasure, erase caller
copies, remove provider records, or delete canonical text. The existing scene
owner alone stores/exports/deletes canonical text, so disabling recording leaves
normal typed play available. The package never opens a V1 or V2 library.

`perform(sequence, feature, payload)` requires a positive increasing integer
through `2**63 - 1`, scoped to this session. Once an invocation is accepted,
including one refused by policy or failed by the backend, any ID at or below
that high-water mark yields payload-free `already_processed`. It never invokes
the backend or returns old audio/transcripts. Invalid inputs do not consume IDs.
Deletion/configuration never resets the mark. Retry a failed operation only with
a new ID; there is no automatic retry or restart-persistent deduplication. The
session retains one bounded artifact per enabled kind, not a growing history.

## Trusted input and accessible recovery

Use the session's `accept`, `confirm`, `keyboard`, `recover` and
`configure_context` methods to couple control safety with privacy settings.
Voice input requires recording enabled and a ready STT route. The capture adapter must latch `session.capture_token` before capturing audio and pass it to `perform(..., capture_token=...)`. A successful STT result echoes it; the resulting `Recognition` must carry that token too. Configuration, context changes, deletion/recovery and close rotate the token. Missing or stale tokens are refused before STT upload, intent routing or voice confirmation, so an old transcript cannot become a different PC’s action. These tokens correlate trusted capture, not authenticate a person; never relabel an old capture with a fresh token. A `Recognition`
requires a voice-origin utterance plus capture-adapter attribution, engagement,
overlap and finality. Defaults are unknown/disengaged/partial. Background,
unknown, overlapping, partial or disengaged events are blocked **before** the
intent grammar, including stop, pause, delete and confirmation words. Speaker
names or confidence in a transcript do not establish attribution. The lower-level
`route_recognition`/`SpeechControls` are independently testable boundaries,
not substitutes for session policy checks.

A quiet or uncertain utterance with exactly one instant-command candidate can
be shown through `pending` for explicit review. `confirm` accepts an unambiguous
keyboard yes or a fresh trusted foreground voice yes above the configured
confidence threshold. Multiple command candidates are not silently resolved.
Authorized/destructive commands and PC authorship never use this shortcut;
their existing authorization and target/revision confirmation requirements remain.
Each `pending` value carries `token` and `intent` (and a convenience `command` property). A trusted capture adapter latches the proposal token when capture starts and returns it as `Recognition.proposal_token`; it must not retag an old recognition result with a new token. Keyboard confirmation supplies the reviewed token explicitly via `confirm(answer, token=...)`. A stale token cannot confirm a different proposal. A pending proposal is consumed on any confirmation attempt. New input, recovery,
configuration, context changes or draft correction invalidate it. Ordinary new STT also invalidates it before work. To transcribe an affirmative, explicitly call `perform(..., confirmation_token=pending.token)`; it validates the pending token before invocation, preserves the proposal only on success, and returns the token beside the transcript. Refusals and failures clear pending state. Feed that returned token to the trusted recognition event for `confirm`. This full synthetic sequence is tested. `keyboard` immediately clears
pending speech and uses the existing typed intent router even with recording off.
No speech method dispatches a command or commits fictional state.

Returned transcript text and intent feedback are shown as captions in the
chat page's voice strip (below). No speaker identification or
accessibility-user study is claimed.

## One listening window, analyzed before recognition

`grimoire.speech.capture` is the trusted capture boundary between a listening
window and the session. A `Capture` is one window's mono 16-bit WAV, the
explicit gesture that opened it (`keyboard`, `pointer` or `switch`; there is no
always-on or wake-word trigger), the capture token latched when it opened and,
when it answers a pending proposal, that proposal's token. `hear` first runs
`analyze`, a 20 ms energy detector whose threshold sits 9 dB above the
window's own noise floor (never below -60 dBFS). Audio is **blocked before any
recognizer sees it** when the window holds:

- `no_speech`: nothing above the floor;
- `speech_at_open`: speech already under way within 0.12 s of the press, i.e.
  someone else was talking when the person pressed;
- `late_onset`: speech starting more than 3 s after the press;
- `multiple_segments`: more than one stretch of speech (two talkers in turn, or
  a bystander after the person);
- `cut_off`: speech running into the window's end;
- `too_long`: a window over 15 s.

Only then is the audio transcribed through `perform(..., capture_token=...)`
and turned into a foreground, engaged, final `Recognition`; all earlier gates
(attribution, staleness, confidence, command gates, pending review) still apply.
`voice_action` says what the page may do with the result: `act` only for an
instant UI command, `draft` for the person's own words (placed in the composer,
never posted), `show` otherwise. Authorized and confirm-gated commands, such as
delete the scene, are only named; voice never presses their buttons.

These are single-microphone heuristics, not speaker identification. A bystander
who starts speaking promptly inside the person's window, with the person silent,
is attributed to the person: an instant command can then act (for example opening
a view), and a bystander "yes" can confirm the person's own pending safe proposal.
It still cannot delete, undo, reroll or post. The evaluation keeps these as
measured `KNOWN_LIMITS`, not passing cases.

## In the app

The `speech` adapter (`app/adapters/speech.py`) serves `speech.status`,
`speech.listen`, `speech.confirm`, `speech.recover` and `speech.speak`. By
default each open library has only the unsupported backend with recording off,
so every op answers truthfully that voice is off and typing is untouched. An
embedding application (061's private-voice settings, or a test) installs a
trusted local backend and policy with `configure`; no request argument can.
The server numbers its own invocations; nothing it returns executes a command,
posts, deletes or writes the library.

The chat page's voice strip (`static/pages/chat/voice.js`) shows only controls
that can work, and a status line that says when voice is off or unavailable:

- **Listen once** (or Alt+V, which a switch can map to) asks for the microphone
  only on that press, records one bounded window that ends by itself after the
  speech, at 15 s, on a second press, or on **Back to typing** (Esc), and
  releases the microphone when the window ends. It says "Listening" only once the
  microphone is taking samples, and pauses any reading aloud while it listens
  (resuming afterwards unless what was said paused or stopped it). A cancelled
  window is never sent, and an answer that arrives after Back to typing is dropped.
- Every result is a caption in a `role=log` list; blocked windows say why.
- An instant command (pause, stop, resume, cancel) acts; the person's words go
  into the composer as a draft to check; a low-confidence miss can be used as a
  draft only by pressing **Use as draft**.
- An uncertain safe command asks "Did you mean …?" with **Yes** and **No**
  (focus moves to Yes), or **Answer by voice**, which sends the proposal token
  with the next window.
- **Read aloud** reads the latest reply through the configured TTS route, with
  **Pause reading** and **Read again**; its text stays on screen as the caption.
  Audio plays from a `blob:` URL, the only media source the page policy allows.

## Evidence and reproducible metrics

Run the synthetic acceptance tests without any microphone or personal recordings:

```sh
PYTHONPATH=backend/src python -m pytest backend/tests/test_speech_privacy.py \
  backend/tests/test_speech_control.py backend/tests/test_speech_evaluation.py \
  backend/tests/test_speech_acoustic.py backend/tests/test_app_speech.py -q
node --test backend/tests/js/voice.test.mjs
```

**Transcript/event cases.** The test-owned `speech_corpus.py` supplies invented
transcript/event cases: quiet confidence, disfluency, spelling/recognition
variants, noise hypotheses, background phrases, interruptions,
simultaneous-speaker flags, ambiguous commands and clear positive controls.
`evaluation.evaluate` reports unintended commands over negative command cases,
wrong commands on positive cases, channel misattribution over background/unknown
cases, wrong actors over foreground cases, exact task completion, and measured
routing-only latency. Absent denominators are `None`; empty corpora cannot pass.
Its `acoustic_tested=False` is a coverage label, not a measured zero error rate.

**Synthetic audio cases.** `backend/tests/fixtures/speech_audio/` holds 37
synthetic clips (1.5 MB) made by `generate.py` with espeak-ng's MBROLA voices
`us1`, `us2`, `us3` and `en1` plus the plain formant voice as an atypical,
robotic speaker, at slow, fast and high-pitched variants and one disfluent
command. `MANIFEST.json` records each clip's text, voice and SHA-256; no clip is
a recording of a person. `speech_audio_corpus.py` builds 75 cases from them
with `grimoire.speech.acoustic`'s deterministic degradations: quiet speech
(-24 and -30 dB), white noise and mains hum, hiss-only windows, far background
talkers already speaking at the press, simultaneous mixes with chatter and with
a bystander saying "delete scene", cut-off and two-utterance windows, quiet
confirmations (voice yes, typed yes, background yes) and back-to-typing.

`evaluation.evaluate_audio` runs each case through a fresh session, `hear` and
a real offline recognizer: the tests' `SphinxRecognizer`, CMU PocketSphinx
5.0.4 with a JSGF command grammar and a filler-word loop (a test dependency in
the `dev` extra, never a product backend; 061 owns the product's private
backend). It refuses any backend that is not local, offline and free. Measured
on the corpus:

| Metric | Result |
| --- | --- |
| Cases / tasks completed | 75 / 72 (96%) |
| Unintended actions on 28 negative cases | 0 |
| Wrong actions | 0 |
| Bystander utterances recognized / acted on (13 cases) | 0 / 0 |
| Command hypotheses on negatives that did not act (shown only) | 5 |
| Recognition latency per window (this machine) | p50 ~0.04 s, p95 ~0.16 s, max ~1 s |

The three misses are recognizer errors on clean "stop"/"pause" clips; each
fails safe (nothing acts). The test gate requires zero unintended, wrong and
bystander actions, no bystander recognized, and completion of at least 85%.
A capture analyzer that passes everything, or a recognizer that always hears
"open map", fails the gate. Results depend on these synthetic voices and this
recognizer; they say nothing about accents, disabilities, real rooms or real
microphones, and must not be used to claim them.

Remaining work needs people and devices, not more synthetic cases: a run with a
real microphone and speakers in a real room, hands-free campaign play with the
voice strip and alternative inputs (switch, keyboard only), and accessibility-user
testing. 061's product backend must also pass `evaluate_audio`. No persisted
schema or migration changes are introduced.
