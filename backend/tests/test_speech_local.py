"""061's local recognizer and its measurements (docs/SPEECH.md).

The format, budget and fail-closed tests need nothing installed. The acoustic tests need the optional
``pocketsphinx`` package and an ``ffmpeg`` built with Flite, which speaks the synthetic phrases at run time: that
is computer speech, not a person or a microphone, so these prove an installed engine and this device's cost, not
accuracy for real speakers. They are skipped where either is missing; CI's ``local voice`` job installs both and sets
``GRIMOIRE_REQUIRE_VOICE=1``, which makes a missing one an error instead of a skip.
"""

from __future__ import annotations

import json
import os
import struct

import pytest

from grimoire.speech import Policy, Selection, SpeechSession
from grimoire.speech import measure as measuring
from grimoire.speech.measure import Budget, Case, NetworkGuard, flite_available, measure, synthesize, word_errors
from grimoire.speech.sphinx import (
    RATE, AudioRefused, PocketSphinxBackend, grammar, installed, pcm, to_wav,
)

VOICE = installed() and flite_available()
if os.environ.get("GRIMOIRE_REQUIRE_VOICE") == "1" and not VOICE:
    raise RuntimeError("GRIMOIRE_REQUIRE_VOICE is set but pocketsphinx or ffmpeg with Flite is missing")
acoustic = pytest.mark.skipif(not VOICE, reason="pocketsphinx and ffmpeg with Flite are needed for acoustic tests")
COMMANDS = ("pause the scene", "resume the scene", "open the door", "delete that", "yes", "no")


def silence(seconds: float, rate: int = RATE) -> bytes:
    return to_wav(b"\x00\x00" * int(seconds * rate), rate)


def _bad_chunk() -> bytes:
    """A WAV whose fmt chunk says it is larger than the file: the standard library raises RuntimeError on it."""
    audio = bytearray(silence(0.1))
    struct.pack_into("<I", audio, 16, 0x7FFFFFF0)
    return bytes(audio)


def session_for(backend: PocketSphinxBackend, **policy) -> SpeechSession:
    return SpeechSession((backend,), Policy(stt=Selection("local_only", backend.info.id), recording=True, **policy))


# -- format and budget: nothing installed needed ---------------------------------------------------------------

def test_only_16_bit_mono_16khz_wav_within_the_limit_is_taken():
    samples, seconds = pcm(silence(1.5), 15)
    assert len(samples) == 2 * 24000 and seconds == pytest.approx(1.5)
    import io
    import wave
    stereo = io.BytesIO()
    with wave.open(stereo, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(b"\x00" * 400)
    for audio, reason in ((stereo.getvalue(), "invalid_audio"), (silence(1, 8000), "invalid_audio"),
                          (to_wav(b""), "invalid_audio"), (b"not a wav", "invalid_audio"),
                          (silence(16), "too_long"), (silence(1)[:-100], "invalid_audio"),
                          (_bad_chunk(), "invalid_audio")):
        with pytest.raises(AudioRefused) as refused:
            pcm(audio, 15)
        assert refused.value.reason == reason


def test_the_recording_limit_is_the_persons_to_lower_within_a_bound():
    backend = PocketSphinxBackend(available=False)
    assert backend.max_seconds == 15
    backend.max_seconds = 5
    with pytest.raises(AudioRefused):
        backend.transcribe(silence(6))
    for bad in (0, 61, float("nan"), True, "10"):
        with pytest.raises(ValueError):
            backend.max_seconds = bad


def test_it_declares_itself_local_free_and_non_retaining_and_is_stt_only():
    info = PocketSphinxBackend(available=True).info
    assert (info.id, info.features, info.locality, info.network, info.retains_inputs, info.billing) == \
        ("pocketsphinx", frozenset({"stt"}), "local", False, False, "free")
    session = session_for(PocketSphinxBackend(available=True))
    assert session.describe("stt").status == "ready"
    assert session.describe("tts").status == "off"  # it offers no speech output


def test_a_missing_engine_is_unavailable_and_nothing_is_tried_instead():
    backend = PocketSphinxBackend(available=False)
    session = session_for(backend)
    assert session.describe("stt").status == "unavailable"
    result = session.perform(1, "stt", silence(1), capture_token=session.capture_token)
    assert result.status == "unavailable" and result.transcript is None
    assert backend.measurements.loaded is False and backend.measurements.recognitions == 0
    with pytest.raises(RuntimeError):
        backend.load()


def test_a_command_grammar_takes_only_plain_lower_case_phrases():
    assert grammar(("yes", "pause the scene")).endswith("public <phrase> = yes | pause the scene;\n")
    for bad in ((), ("Yes",), ("a;b",), ("two  spaces",), ("<rule>",), tuple(f"w{i}" for i in range(65))):
        with pytest.raises(ValueError):
            grammar(bad)


def test_word_error_counts_edits_over_expected_words():
    assert word_errors("pause the scene", "pause the scene") == (0, 3)
    assert word_errors("pause the scene", "cause the scene") == (1, 3)
    assert word_errors("open the door", "") == (3, 3)
    assert word_errors("yes", "yes yes") == (1, 1)


def test_the_network_guard_counts_and_refuses_every_connection():
    import socket
    guard = NetworkGuard()
    with guard.active():
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", 9))
        probe = socket.socket()
        try:
            with pytest.raises(OSError):
                probe.connect(("127.0.0.1", 9))
        finally:
            probe.close()
    assert guard.attempts == ["create_connection", "connect"]
    assert socket.create_connection is not None and socket.socket.connect.__name__ == "connect"


def test_an_empty_corpus_measures_nothing():
    with pytest.raises(ValueError):
        measure(PocketSphinxBackend(available=False), [])


def test_the_measuring_tool_says_when_the_engine_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(measuring, "installed", lambda: False)
    assert measuring.main([]) == 2
    told = json.loads(capsys.readouterr().out)
    assert told["available"] is False and "pip install pocketsphinx" in told["reason"]


# -- acoustic: an installed engine, synthetic speech ----------------------------------------------------------

@acoustic
def test_local_only_recognition_hears_commands_with_no_network_and_keeps_nothing(capfd):
    backend = PocketSphinxBackend(phrases=COMMANDS)
    session = session_for(backend)
    guard = NetworkGuard()
    heard = []
    with guard.active():
        for sequence, phrase in enumerate(("pause the scene", "open the door", "delete that"), 1):
            result = session.perform(sequence, "stt", synthesize(phrase, "slt"), capture_token=session.capture_token)
            assert result.status == "ok"
            assert result.transcript.origin == "voice"
            heard.append(result.transcript.text)
    assert heard == ["pause the scene", "open the door", "delete that"]
    assert guard.attempts == []  # nothing left this process
    assert session.export() == {}  # retention defaults to none
    measured = backend.measurements
    assert measured.loaded and measured.load_seconds > 0 and measured.recognitions == 3
    assert measured.last_real_time_factor is not None and measured.last_real_time_factor < 1
    out, err = capfd.readouterr()
    for phrase in heard:  # the engine's log is off: what it heard is in no output stream
        assert phrase not in out and phrase not in err


@acoustic
def test_open_dictation_returns_some_words_and_silence_returns_none():
    backend = PocketSphinxBackend()
    spoken = backend.transcribe(synthesize("is the cargo safe", "awb"))
    assert spoken.origin == "voice" and spoken.text
    quiet = backend.transcribe(silence(1))
    assert quiet.text == "" and quiet.confidence is None


@acoustic
def test_unload_frees_the_model_and_the_next_recognition_loads_and_measures_it_again():
    backend = PocketSphinxBackend(phrases=COMMANDS)
    backend.load()
    first = backend.measurements.load_seconds
    backend.unload()
    assert backend.measurements.loaded is False and backend.measurements.load_seconds is None
    assert backend.transcribe(synthesize("yes", "slt")).text == "yes"
    assert backend.measurements.loaded and backend.measurements.load_seconds is not None and first is not None
    hypothesis = backend._decoder.hyp()  # the engine keeps no copy of what it heard
    assert hypothesis is None or hypothesis.hypstr == ""


@acoustic
def test_the_device_measurement_reports_cost_accuracy_and_budget():
    cases = [Case(phrase, synthesize(phrase, voice), phrase) for voice in ("slt", "rms") for phrase in COMMANDS[:3]]
    report = measure(PocketSphinxBackend(phrases=COMMANDS), cases, Budget(load_seconds=30, real_time_factor=1.0,
                                                                          peak_memory_mib=2048))
    assert report["cases"] == 6 and report["network_attempts"] == 0 and report["mode"] == "grammar"
    assert report["load_seconds"] > 0 and report["latency_seconds"]["max"] >= report["latency_seconds"]["p50"] > 0
    assert report["real_time_factor"]["max"] < 1 and report["word_error_rate"] <= 0.2 and report["exact"] >= 5
    assert report["within_budget"] == {"load_seconds": True, "real_time_factor": True,
                                       "peak_memory_mib": None if report["peak_memory_mib"] is None else True}
    assert len(report["rows"]) == 6 and all(row["audio_seconds"] > 0 for row in report["rows"])
    tight = measure(PocketSphinxBackend(phrases=COMMANDS), cases[:1], Budget(real_time_factor=1e-9))
    assert tight["within_budget"]["real_time_factor"] is False  # an unmet budget is said, not hidden


@acoustic
def test_the_measuring_tool_runs_from_the_command_line(tmp_path, capsys):
    path = tmp_path / "yes.wav"
    path.write_bytes(synthesize("open the door", "slt"))
    assert measuring.main(["--wav", f"{path}=open the door", "--grammar", "--rows"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["rows"][0]["heard"] == "open the door" and report["network_attempts"] == 0


def test_synthesis_refuses_anything_but_plain_words():
    for text, voice in (("it's; rm", "slt"), ("hello", "nobody"), ("a'b:c", "slt")):
        with pytest.raises(ValueError):
            synthesize(text, voice)


def test_wav_round_trips():
    samples = struct.pack("<4h", 0, 1000, -1000, 32767)
    assert pcm(to_wav(samples), 1) == (samples, 4 / RATE)
