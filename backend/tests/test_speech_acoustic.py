"""067 acoustic evidence: synthetic speech clips, deterministic degradations, a real offline recognizer
(PocketSphinx), the capture analysis and the speech session's gates, measured end to end.

Everything here is synthetic. It proves the safety boundary under acoustic recognition errors on invented
clips; it does not measure accuracy for any accent, age, disability or real room, and a pass is not an
accessibility-user result.
"""
from __future__ import annotations

import hashlib
import json
import math

import pytest

from grimoire.intent import Command, RoutingContext, Utterance
from grimoire.speech import Policy, Selection, SpeechSession
from grimoire.speech import acoustic
from grimoire.speech.capture import MAX_WINDOW, Capture, analyze, hear, voice_action
from grimoire.speech.evaluation import AudioCase, Window, evaluate_audio

import speech_audio_corpus as corpus
from sphinx_recognizer import SphinxRecognizer


@pytest.fixture(scope="module")
def recognizer():
    return SphinxRecognizer()


@pytest.fixture(scope="module")
def report(recognizer):
    return evaluate_audio(corpus.CASES, recognizer, corpus.CONTEXT)


def _session(recognizer, context=corpus.CONTEXT, **policy):
    return SpeechSession((recognizer,), Policy(stt=Selection("local_only", recognizer.info.id),
                                               **({"recording": True} | policy)), context)


def _capture(session, pcm, token=None, trigger="keyboard"):
    return Capture(acoustic.write_wav(pcm), trigger, session.capture_token, token)


def test_committed_clips_are_the_generators_synthetic_output():
    manifest = json.loads((corpus.FOLDER / "MANIFEST.json").read_text())
    assert manifest["synthetic"] is True
    on_disk = {p.stem for p in corpus.FOLDER.glob("*.wav")}
    assert on_disk == set(manifest["clips"])
    for name, entry in manifest["clips"].items():
        assert hashlib.sha256((corpus.FOLDER / f"{name}.wav").read_bytes()).hexdigest() == entry["sha256"]
        assert entry["role"] in ("foreground", "background")
        pcm = corpus.clip(name)
        assert pcm.rate == 16000 and 0.2 < pcm.seconds < 6


def test_wav_values_round_trip_and_refuse_what_they_cannot_read():
    pcm = corpus.clip("us1-yes")
    assert acoustic.read_wav(acoustic.write_wav(pcm)) == pcm
    for bad in (b"", b"RIFF" + bytes(60), b"x" * 100):
        with pytest.raises(ValueError):
            acoustic.read_wav(bad)
    stereo = bytearray(acoustic.write_wav(pcm))
    stereo[22] = 2  # channel count
    with pytest.raises(ValueError):
        acoustic.read_wav(bytes(stereo))


def test_degradations_are_deterministic_and_bounded():
    pcm = corpus.window(corpus.clip("us2-open"))
    assert acoustic.noise(pcm, 10, seed=3) == acoustic.noise(pcm, 10, seed=3)
    assert acoustic.noise(pcm, 10, seed=3) != acoustic.noise(pcm, 10, seed=4)
    quiet = acoustic.gain(pcm, -24)
    assert acoustic.dbfs(acoustic.rms(quiet.samples)) == pytest.approx(
        acoustic.dbfs(acoustic.rms(pcm.samples)) - 24, abs=0.5)
    noisy = acoustic.noise(pcm, 10, seed=1)
    added = [a - b for a, b in zip(noisy.samples, pcm.samples)]
    assert 20 * math.log10(acoustic.rms(pcm.samples) / acoustic.rms(added)) == pytest.approx(10, abs=0.5)
    assert len(acoustic.mix(pcm, corpus.clip("bg-chatter"), offset=0.2).samples) == len(pcm.samples)
    for bad in (float("nan"), 200):
        with pytest.raises(ValueError):
            acoustic.gain(pcm, bad)
    with pytest.raises(ValueError):
        acoustic.mix(pcm, acoustic.Pcm(8000, pcm.samples[:10]))


def test_analysis_attributes_only_one_prompt_complete_utterance():
    speech = corpus.clip("us1-open")
    assert analyze(corpus.window(speech)).reason == "ok"
    assert analyze(acoustic.hiss(16000, 2, -30, seed=1)).reason == "no_speech"
    assert analyze(acoustic.pad(speech, 0, 0.6)).reason == "speech_at_open"
    assert analyze(corpus.window(speech, lead=3.5)).reason == "late_onset"
    assert analyze(acoustic.truncate(corpus.window(speech), 1.0)).reason == "cut_off"
    two = acoustic.concat(corpus.window(speech, tail=0.6), acoustic.pad(corpus.clip("us1-yes"), 0, 0.6))
    assert analyze(two).reason == "multiple_segments"
    assert analyze(acoustic.pad(speech, 0.4, MAX_WINDOW)).reason == "too_long"


def test_no_window_without_a_gesture_and_blocked_audio_never_reaches_the_recognizer():
    for trigger in ("always_on", "wake_word", "voice", ""):
        with pytest.raises(ValueError):
            Capture(b"RIFF", trigger, "token")

    class Counting(SphinxRecognizer):
        calls = 0

        def transcribe(self, audio):
            Counting.calls += 1
            return super().transcribe(audio)

    backend = Counting()
    session = _session(backend)
    blocked = (acoustic.pad(acoustic.far(corpus.clip("bg-delete")), 0, 0.6), acoustic.hiss(16000, 2, -30, seed=2),
               acoustic.truncate(corpus.window(corpus.clip("us1-delete")), 0.9))
    for sequence, pcm in enumerate(blocked, start=1):
        heard = hear(session, sequence, _capture(session, pcm))
        assert heard.status == "blocked" and heard.transcript is None
    assert Counting.calls == 0 and session.export() == {}


def test_acoustic_corpus_is_safe_and_measured(report):
    assert report.acoustic_tested and report.evidence == "synthetic_audio" and report.backend == "pocketsphinx-eval"
    assert report.cases == len(corpus.CASES) >= 70
    # The gate: nothing acted that should not have, and nothing acted on a different command.
    assert report.safe
    assert report.unintended_actions == report.wrong_actions == report.bystander_actions == 0
    assert report.unintended_action_rate == 0
    # Measured, not gated to zero: the recognizer does hypothesize commands on some negative cases (voice may
    # name "delete scene" but never act on it), and misses some clean commands. Floors guard regressions only.
    assert report.unintended_hypothesis_rate is not None
    assert report.task_completion_rate >= 0.85
    assert report.bystander_cases >= 10 and report.bystander_recognition_rate == 0
    assert report.windows > report.cases and report.recognized_windows < report.windows
    assert 0 < report.latency_p50 <= report.latency_p95 <= report.latency_max


def test_background_speech_cannot_issue_delete_scene(recognizer):
    """The proving scenario's first half, acoustically: from a far or loud talker already speaking, overlapping
    the person at their own level, or starting promptly in the person's own window, nothing deletes."""
    cases = tuple(c for c in corpus.CASES + corpus.KNOWN_LIMITS
                  if "delete" in c.id or c.category in ("background", "simultaneous"))
    assert len(cases) >= 20
    report = evaluate_audio(cases, recognizer, corpus.CONTEXT)
    assert report.wrong_actions == 0
    for case in cases:
        session = _session(recognizer)
        heard = hear(session, 1, Capture(case.windows[0].audio, "keyboard", session.capture_token))
        acted = voice_action(heard.intent) == "act" and heard.intent.command
        assert acted != Command("delete_scene"), case.id
        assert session.pending is None or session.pending.command.gate == "instant"
    # Even the person's own clean "delete scene" is recognized yet only shown: the button is required.
    session = _session(recognizer)
    heard = hear(session, 1, _capture(session, corpus.window(corpus.clip("us3-delete"))))
    assert heard.intent.command == Command("delete_scene") and voice_action(heard.intent) == "show"


def test_quiet_atypical_speaker_confirms_a_safe_action_and_returns_to_typing(recognizer):
    """The proving scenario's second half: a quiet speaker in noise, and the formant-synthesizer voice, are
    recognized below the threshold; the one safe command waits for an explicit yes."""
    for speaker, snr, answer_snr in (("us2", 8, 8), ("robot", 6, 10)):
        quiet = acoustic.noise(corpus.window(acoustic.gain(corpus.clip(f"{speaker}-open"), -24)), snr, seed=40)
        session = _session(recognizer)
        heard = hear(session, 1, _capture(session, quiet))
        assert heard.status == "routed" and heard.intent.kind == "uncertain"
        pending = session.pending
        assert pending.command == corpus.OPEN
        # Someone else's "yes", already under way when the answer window opens, is blocked and confirms nothing.
        background = hear(session, 2, _capture(session, acoustic.pad(acoustic.far(corpus.clip("bg-yes")), 0, 0.6),
                                                pending.token))
        assert background.status == "blocked" and session.pending == pending
        yes = acoustic.noise(corpus.window(acoustic.gain(corpus.clip(f"{speaker}-yes"), -24)), answer_snr, seed=41)
        confirmed = hear(session, 3, _capture(session, yes, pending.token))
        assert confirmed.status == "confirmed" and confirmed.intent.command == corpus.OPEN
        assert session.pending is None
        # Typed controls take over at once, with recording turned off and no recognizer involved.
        session.configure(Policy())
        typed = session.keyboard(Utterance("open inventory"))
        assert typed.command == corpus.OPEN and voice_action(typed) == "act"
        assert session.describe("stt").status == "recording_off"


def test_a_stale_window_cannot_answer_and_a_typed_yes_needs_its_token(recognizer):
    quiet = acoustic.noise(corpus.window(acoustic.gain(corpus.clip("us2-open"), -24)), 8, seed=40)
    yes = acoustic.noise(corpus.window(acoustic.gain(corpus.clip("us2-yes"), -24)), 8, seed=41)
    session = _session(recognizer)
    hear(session, 1, _capture(session, quiet))
    pending = session.pending
    old = _capture(session, yes, pending.token)
    session.configure_context(RoutingContext("pc-2", True, min_confidence=corpus.CONTEXT.min_confidence))
    assert hear(session, 2, old).status == "refused" and session.pending is None
    hear(session, 3, _capture(session, quiet))
    assert session.confirm(Utterance("yes"), token="0" * 32) is None and session.pending is None


def test_known_single_microphone_limits_are_measured_not_hidden(recognizer):
    """A bystander who starts promptly inside the person's window is attributed to the person. Only instant
    commands can then act; the limit is documented in docs/SPEECH.md."""
    report = evaluate_audio(corpus.KNOWN_LIMITS, recognizer, corpus.CONTEXT)
    assert report.bystander_cases == len(corpus.KNOWN_LIMITS)
    assert report.bystander_recognition_rate == 1
    for case in corpus.KNOWN_LIMITS:
        session = _session(recognizer)
        heard = hear(session, 1, Capture(case.windows[0].audio, "keyboard", session.capture_token))
        if voice_action(heard.intent) == "act":
            assert heard.intent.command.gate == "instant", case.id


def test_evaluation_refuses_networked_or_paid_recognizers_and_bad_cases(recognizer):
    from grimoire.speech import BackendInfo

    class Remote(SphinxRecognizer):
        info = BackendInfo("remote", frozenset({"stt"}), "remote", True, False, "free", True)

    class Paid(SphinxRecognizer):
        info = BackendInfo("paid", frozenset({"stt"}), "local", False, False, "paid", True)

    for backend in (Remote(), Paid()):
        with pytest.raises(ValueError):
            evaluate_audio(corpus.CASES[:1], backend, corpus.CONTEXT)
    with pytest.raises(ValueError):
        evaluate_audio((), recognizer, corpus.CONTEXT)
    with pytest.raises(ValueError):
        AudioCase("x", (Window(typed="yes"),), frozenset({("none", None)}))
    with pytest.raises(ValueError):
        AudioCase("x", (Window(b"RIFF"),), frozenset({("act", None)}))
    with pytest.raises(ValueError):
        Window(audio=b"RIFF", typed="yes")


def test_unsafe_mutants_fail_the_gate(recognizer, monkeypatch):
    """The harness can fail: an analyzer that attributes everything, with a recognizer that hears every window
    as an instant command, lets bystanders act and acts on the wrong command."""
    import grimoire.speech.capture as capture

    monkeypatch.setattr(capture, "analyze", lambda pcm: capture.Analysis("ok", ((0.4, 1.0),)))

    class Hearing(SphinxRecognizer):
        def transcribe(self, audio):
            return Utterance("open map", origin="voice", confidence=1.0)

    unsafe = evaluate_audio(corpus.CASES, Hearing(), corpus.CONTEXT)
    assert not unsafe.safe and unsafe.bystander_actions and unsafe.wrong_actions
