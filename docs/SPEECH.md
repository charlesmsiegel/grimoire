# Private speech boundary and synthetic safety tests

`grimoire.speech` implements the headless first-useful boundaries for 061 and
067, and 061's optional local recognizer with its measurements. The app's Voice
page (settings) records from the microphone and recognizes on the device; see
[Local recognizer and the Voice page](#local-recognizer-and-the-voice-page-061);
a chat's **Dictate** speaks a draft into its composer through the same route; see
[Speech to draft](#speech-to-draft-080).
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
  One window is open at a time, through recognition, and reading never starts
  inside an open window: synthesis that finishes while listening waits for it.
  The window's own end-of-speech check uses the same floor-relative level as the
  analysis, so quiet speech is not taken for silence.
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
fails safe (nothing acts). Safety counts every window of a case, so a later
typed recovery cannot erase an earlier wrong action. The test gate requires zero unintended, wrong and
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
Copy button; **nothing is posted**: speech output and hands-free play are shown as
not available yet, and speaking a draft into a chat is the chat page's Dictate
([Speech to draft](#speech-to-draft-080), below). With the microphone or voice off, typed play
is exactly as before. 067's chat voice strip and read-aloud (`speech.*`, above)
still start on the `unsupported` backend: installing this backend there, and
running it through `evaluate_audio`, is the next step.

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

## Speech to draft (080)

The chat page's **Dictate** (`static/pages/chat/dictate.js`) lets a player speak
their reply instead of typing it. What was heard goes into the composer as unsent
text, to read, edit and post themselves: **nothing is posted, advanced or written
until they press Post**, and saying "post it" (or "send it", "submit") is answered
with "Voice doesn't post", never a post.

It records through 061's private route: the `dictation.*` ops wrap `voice.*`'s
session, capture token, sequence and recognizer, so dictation is off until the
Voice page turns speech input on (this device only) with the microphone on, and
there is nothing new to consent to. The strip says why it cannot record (voice
off, microphone off, recognizer missing, a browser that cannot record) and shows
**Dictate** only when it can. Dictate opens the microphone; **Stop dictating**
sends the recording; **Cancel** or Esc closes it unsent; leaving the page or the
recording limit (Settings, 1 to 60 s) closes it too. Pauses do not end a
recording, so a long post is one recording. The token and sequence are latched at
Dictate, so a recording made before a change of settings is dropped as
`stale_capture`, and one recording is heard at a time.

`grimoire.speech.dictation.revise` decides what one utterance does to the draft.
It is pure (no recognizer, file or library) and keeps fidelity first: the verbatim
words are always shown ("Heard …"), and appended speech changes only a capital at
a sentence's start and a capital "I". Three spoken edits act on the **last
dictated passage** only, never on typed text:

| Said | Does | The other reading (one press) |
|---|---|---|
| "no, I meant the west gate", "I meant …", "correction …" | replaces the part of the passage it plainly corrects: one edit around a shared word that is not a stop word (content words swapped in place, "the east gate"; words of the same kind swapped one by one, "an old gate"; words put in between shared words, "the gate"; content words taken out from between them), or a whole that sounds alike (a misheard name). Neighbouring words are never taken or left doubled, and a trailing "please" is not put in | keep the words as said, in case they were a character's line |
| the same, when it is not plainly one edit ("a gate" for "the west gate") | keeps the words as said | replace the whole last passage instead |
| "scratch that", "delete that", "strike that" | removes the last passage | keep the words as said |
| "post it", "send it", "submit" … | nothing: voice never posts | keep the words as said |

**Undo** puts the draft back as it was before the recording. Names in the scene's
cast that sound like part of what was heard ("mara bell" for Mara Vell) are only
offered ("Use Mara Vell for “mara bell”"); an invented name the recognizer
cannot know is the player's to type over. A recognizer confidence under 0.5
(untuned) adds "check it". The player's own typing always wins: a draft typed in
while a recording was heard is revised as it is now (`dictation.revise`), and an
offer whose text has since changed does nothing and says so. Once the reply is
typed in, posted or changed by anything but dictation, there is no "last
passage" any more: a correction or "scratch that" is then kept as words. A
correction is lined up within the passage's last 200 words, and names are looked
for in its first 300, so a long draft costs about what a sentence does.

| Op | Arguments | Answer |
|---|---|---|
| `dictation.status` | none | `status` (061's, but `off` whenever speech input is off), `capture_token` and `next_sequence` only when ready, `max_seconds`, `language`, `kept` |
| `dictation.hear` | `audio`, `sequence`, `capture_token` as `voice.transcribe`; `draft` (≤ 20 000 characters), `last` (the passage last dictated, or null), `names` (≤ 64 of ≤ 80 characters) | `status`, `heard`, `confidence`, `revision` (`kind`, `text`, `passage`, `before`, `after`, `uncertain`, `suggestions`, `alternative`), `next_sequence`, `kept` |
| `dictation.revise` | `draft`, `last`, `names`, `heard`, `confidence` | `revision` |

The ops touch no library state and run without its writer lock. A bad draft or
name list is refused before anything is recognized.

The language is US English only, the bundled recognizer's one model, and the
strip says so rather than offering a choice it cannot keep. Not built yet:
streaming or partial transcripts, spoken punctuation, per-word confidence, scene
names in the recognizer's own vocabulary, other languages and mixed speech and
typing while recording (the sprint's later milestones). The proof is a stand-in
recognizer on the real page (`test_app_dictation.py`, `js/dictate.e2e.mjs`) and
the installed engine on synthetic speech (CI's `local voice` job): **no real
microphone, room or voice has been tried**.

