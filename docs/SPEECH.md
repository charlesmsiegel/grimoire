# Private speech boundary and synthetic safety tests

`grimoire.speech` implements the headless first-useful boundaries for 061 and
067, and 061's optional local recognizer with its measurements. The app's Voice
page (settings) records from the microphone and recognizes on the device; see
[Local recognizer and the Voice page](#local-recognizer-and-the-voice-page-061).
It does **not** implement speech synthesis, playback, streaming recognition or
live voice roleplay. `grimoire.voice` continues to own written
characterization. Existing `intent` values remain unexecuted hypotheses.

## Explicit routing and truthful fallback

`SpeechSession` has separate STT and TTS `Selection`s: `off` (default),
`local_only`, or `provider`, each naming a backend explicitly. Recording is
off by default. `describe("stt")` / `describe("tts")` disclose status, audio
versus text input, network use, locality, backend retention, billing and text
fallback. The always-present backend, `unsupported`, reports unavailable for
both features and never runs recognition or synthesis. The optional
`pocketsphinx` backend (below) is STT only. Missing engines,
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
recognition alternatives to eight bounded strings. Measured acoustic cost is
reported by the local recognizer (below); a phone's budget is still unmeasured.

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

Returned transcript text and intent feedback support visible transcripts and
captions for a future UI. Actual caption widgets, keyboard affordances,
replay/pause playback controls and hands-free campaign interaction belong to
115/105 integration and are not shipped here. No acoustic speaker attribution or
accessibility-user study is claimed.

## Evidence and reproducible metrics

Run the synthetic acceptance tests without any microphone or personal recordings:

```sh
PYTHONPATH=backend/src python -m pytest backend/tests/test_speech_privacy.py \
  backend/tests/test_speech_control.py backend/tests/test_speech_evaluation.py -q
```

The test-owned `speech_corpus.py` supplies invented **transcript/event** cases:
quiet confidence, disfluency, spelling/recognition variants, noise hypotheses,
background phrases, interruptions, simultaneous-speaker flags, ambiguous commands
and clear positive controls. This is **not a synthetic acoustic speech corpus**;
the acoustic tests are 061's, below, and simultaneous audio/noise robustness
is unmeasured. It must not be used to claim accent or disability performance.

`evaluation.evaluate` reports unintended commands over negative command cases,
wrong commands on positive cases, channel misattribution over background/unknown
cases, wrong actors over foreground cases, exact task completion, and measured
routing-only mean/p50/p95/max latency. Absent denominators are `None`; empty
corpora cannot pass. Expected commands include arguments and actors are compared
exactly. The synthetic gate requires all tasks to match and zero command/attribution
errors. Deliberately unsafe and reject-everything routers fail the test harness.
Its `acoustic_tested=False` is a coverage label, not a measured zero error rate.
Safe confirmation and keyboard recovery are separate executable sequence tests.

Remaining full-sprint work includes validation with a real microphone and real
speakers, a phone's resource budget, acoustic noise/simultaneous speech, partial
streaming, playback, accessibility-user validation and the sequential UI exit. No persisted schema or
migration changes are introduced by this boundary.

## Local recognizer and the Voice page (061)

`grimoire.speech.sphinx.PocketSphinxBackend` is a trusted local STT adapter over
[CMU PocketSphinx](https://github.com/cmusphinx/pocketsphinx), free and offline,
with its bundled US English model. It is **optional**: `pip install -e
"./backend[voice]"` (or `pip install pocketsphinx`). Without it the backend
declares itself unavailable and nothing else is tried. It declares local
execution, no network and no input retention; recognition runs in the app's own
process, writes no file and opens no socket, and the engine's log is set to
`FATAL` so what it heard never reaches a log or crash trace. It takes a WAV
recording, 16-bit mono at 16 000 Hz, up to a recording limit the person sets
(1 to 60 seconds, default 15); anything else is refused before recognition. A
recording below about -50 dBFS is silence and yields an empty transcript without
running the engine (it otherwise "hears" a word in pure silence). The model loads
on first use, its load time and the resident memory it added (where the system
reports resident memory: Linux, Android) are measured, as is each recognition's
time and real-time factor; `unload` frees it. `phrases=` restricts recognition to
a command vocabulary (a JSGF grammar); without it, recognition is open dictation.

### The Voice page and `voice.*` ops

The `voice` adapter (owner 061) keeps one `SpeechSession` per open library, in
memory only: every launch starts with voice off, the microphone off and nothing
kept. Settings (Voice) offers speech input **Off** or **On this device only**;
there is no online option, and `voice.configure` refuses any other mode. The
page shows the route's disclosure (runs on this device, no network, keeps no
audio, free), a separate microphone switch (off by default), what may be kept for
this launch (only the last transcript: recordings are never kept), what is kept now and a
**Delete voice data now** button, and this device's measured cost with a
recording-limit choice and **Free the recognizer's memory**. Turning voice off
also frees it.

Record opens the microphone only while recording (every track stopped and the
audio context closed on Stop, at the limit by the recorder itself, on a change of
settings, on delete or unload, or when the page is left). A Stop before any sound
sends nothing. The page resamples to 16 kHz, encodes WAV and sends it to `voice.transcribe` on the
app's loopback server, which never leaves the device. The capture token and
sequence are latched when recording starts, so a recording made before a change
of settings is dropped as `stale_capture`. The transcript is shown as text with a
Copy button; **nothing is posted**: speaking into a chat, speech output and hands-free
play are shown as not available yet. With the microphone or voice off, typed play
is exactly as before.

| Op | Arguments | Answer |
|---|---|---|
| `voice.status` | none | `stt`/`tts` disclosures, `mode`, `recording`, `retain`, `kept`, `backends` (with `max_seconds` and `measurements`), `selected`, `capture_token` and `next_sequence` (only while ready), `limits`, `keepable` |
| `voice.configure` | any of `mode` (`off`, `local_only`), `backend`, `recording`, `retain` (`provisional_transcript` only), `max_seconds` (1-60) | the new status; every change forgets what was kept and rotates the capture token |
| `voice.transcribe` | `sequence`, `capture_token`, `audio` (base64 WAV) | `status` (`ok` with `text` and `confidence`, or why not), `next_sequence`, `measurements`, `kept`; `invalid_audio`/`too_long` are 400 refusals; when not ready the recognizer is not touched |
| `voice.delete` | none | the new status, `kept` empty |
| `voice.unload` | none | the new status, the recognizer unloaded |

The voice ops touch no library state, so they run **without** the library's
writer lock (`Adapter.unlocked`, `TextApi.unlocked`; refusals are mapped exactly
as for any op): a recognition, however long, never holds up typed play. The
adapter's own lock serializes the voice ops among themselves. A malformed WAV of
any kind is an `invalid_audio` refusal, and after each recognition the engine's
own last hypothesis is cleared, so delete leaves no transcript in the recognizer.

### Measuring a device

```sh
python -m grimoire.speech.measure            # synthetic Flite speech, open dictation
python -m grimoire.speech.measure --grammar  # the same phrases as a command vocabulary
python -m grimoire.speech.measure --wav me.wav="open the door" --rows
```

It reports model load time, the memory the first load in the process added (a
reload after unloading reuses memory the process kept, so the page keeps showing
the first figure), the process's peak
memory, latency p50/p95/max, real-time factor, word error rate, exact matches and
the Python-level network connections attempted while it ran (refused and
counted; the tool fails if any is attempted). That guard sees Python sockets only,
not native code, so CI also runs the acoustic tests and the measurement in a
process with no network at all (`unshare -n`): there the engine still works,
which shows it needs none, against a desktop budget of 10 s load, real-time
factor 1 and 512 MiB peak. The default corpus is eight phrases spoken by four
`ffmpeg` Flite voices, synthesized at run time: computer speech, not a person or
a microphone. One run in a Linux x86-64 cloud development container (Python 3.13,
pocketsphinx 5.1.1), 32 recordings:

| Mode | Load | Memory added | Peak | Latency p50 / p95 | Real-time factor (max) | Word error rate | Exact | Network |
|---|---|---|---|---|---|---|---|---|
| dictation | 0.33 s | 91 MiB | 118 MiB | 0.25 s / 0.70 s | 0.47 | 42% | 16/32 | 0 |
| grammar | 0.35 s | 91 MiB | 112 MiB | 0.015 s / 0.027 s | 0.015 | 2.3% | 30/32 | 0 |

So on that machine the engine is well within budget, open dictation of synthetic
speech is poor and a command vocabulary is reliable. These are not phone numbers
and say nothing of real voices, accents or rooms. The acoustic tests
(`test_speech_local.py`, the last test of `test_app_voice.py`) run where
pocketsphinx and an `ffmpeg` with Flite are installed, as in CI's `local voice`
job, and are skipped elsewhere.

