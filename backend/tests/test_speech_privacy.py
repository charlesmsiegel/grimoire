"""Synthetic backends exercise the boundary, never an acoustic recognizer."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from grimoire.intent import Utterance
from grimoire.speech import Policy, Selection, SpeechSession


from speech_helpers import SyntheticBackend, policy, session, perform


@pytest.mark.parametrize("info", [dict(locality="remote", network=True), dict(locality="unknown"),
                                    dict(network=None), dict(retains_inputs=True), dict(retains_inputs=None)])
def test_local_only_refuses_before_backend_even_with_consent(info):
    s, b = session(SyntheticBackend(**info), stt=Selection("local_only", "synthetic", "synthetic"))
    assert perform(s, 1, "stt", b"synthetic").status == "privacy_refused"
    assert b.calls == []


def test_feature_specific_upload_consent_and_disclosure():
    b = SyntheticBackend(locality="remote", network=True, retains_inputs=True)
    s = SpeechSession((b,), policy(stt=Selection("provider", "synthetic", "synthetic"),
                                   tts=Selection("provider", "synthetic")))
    d = s.describe("stt")
    assert (d.status, d.input_kind, d.retains_inputs, d.fallback) == ("ready", "audio", True, "text")
    assert perform(s, 1, "stt", b"input").transcript.text == "open inventory"
    assert perform(s, 2, "tts", "caption").status == "consent_required"
    assert len(b.calls) == 1


@pytest.mark.parametrize("billing", ["paid", "unknown"])
def test_upload_consent_is_not_spending_authorization(billing):
    s, b = session(SyntheticBackend(billing=billing), stt=Selection("provider", "synthetic", "synthetic"))
    assert perform(s, 1, "stt", b"input").status == "spending_unsupported"
    assert b.calls == []


def test_missing_unavailable_and_off_are_truthful():
    s = SpeechSession(policy=policy())
    assert perform(s, 1, "stt", b"input").status == "backend_missing"
    s, b = session(SyntheticBackend(available=False))
    assert perform(s, 1, "stt", b"input").status == "unavailable"
    s.configure(Policy())
    assert perform(s, 2, "stt", b"input").status == "recording_off"
    assert perform(s, 3, "tts", "caption").status == "off"
    assert b.calls == []
    assert s.keyboard(Utterance("open inventory")).command.name == "navigate"
    s.close()
    assert perform(s, 4, "tts", "caption").status == "closed"
    assert s.keyboard(Utterance("pause")).command.name == "pause"


def test_retention_export_delete_and_monotonic_replay():
    s, b = session(retain=frozenset({"raw_audio", "provisional_transcript", "corrected_draft", "synthesized_audio"}))
    perform(s, 4, "stt", b"input")
    s.correct_draft("corrected")
    perform(s, 5, "tts", "caption")
    exported = s.export(frozenset({"raw_audio", "corrected_draft"}))
    assert exported == {"raw_audio": b"input", "corrected_draft": "corrected"}
    s.delete()
    assert s.export() == {}
    assert perform(s, 4, "stt", b"changed").status == "already_processed"
    assert perform(s, 5, "tts", "changed").audio is None
    assert len(b.calls) == 2
    assert exported["raw_audio"] == b"input"  # caller-owned copy is not secure-erased
    assert perform(s, 6, "tts", "fresh").status == "ok"
    s.configure(policy())
    assert s.export() == {}
    s.close()
    assert s.export() == {}


def test_default_retention_never_caches_payload_for_replay():
    s, b = session()
    assert perform(s, 1, "stt", b"input").transcript is not None
    assert perform(s, 2, "tts", "caption").audio is not None
    s.correct_draft("draft")
    assert s.export() == {}
    assert perform(s, 1, "stt", b"input").transcript is None
    assert perform(s, 2, "tts", "caption").audio is None
    assert len(b.calls) == 2


def test_limits_and_invalid_types_are_refused_without_call():
    s, b = session()
    for seq in (True, 0, -1, float("nan"), 2**63):
        with pytest.raises(ValueError):
            perform(s, seq, "stt", b"input")
    for feature, payload in (("stt", "text"), ("tts", b"audio"), ("stt", b"x" * (4*1024*1024+1)),
                             ("tts", "x" * 16385), ("invalid", "text")):
        with pytest.raises(ValueError):
            perform(s, 1, feature, payload)
    for change in (dict(recording=1), dict(retain={"raw_audio"}), dict(retain=frozenset({"canonical_text"}))):
        with pytest.raises(ValueError):
            policy(**change)
    assert b.calls == []
    assert perform(s, 2**63-1, "stt", b"input").status == "ok"
    s.delete()
    assert perform(s, 1, "stt", b"input").status == "already_processed"


def test_backend_failure_redacted_and_never_retried():
    class Broken(SyntheticBackend):
        def transcribe(self, audio):
            self.calls.append(audio)
            raise RuntimeError("private transcript and provider credential")
    s, b = session(Broken())
    result = perform(s, 1, "stt", b"input")
    assert result.status == "backend_failed"
    assert "private" not in repr(result)
    assert perform(s, 1, "stt", b"input").status == "already_processed"
    assert len(b.calls) == 1
    assert s.export() == {}


@pytest.mark.parametrize("output", [Utterance("keyboard"), Utterance("x" * 16385, origin="voice"),
                                    Utterance("a", origin="voice", alternatives=("b",) * 9), "bad"])
def test_malformed_stt_backend_output_is_not_retained(output):
    class Broken(SyntheticBackend):
        def transcribe(self, audio):
            return output
    s, _ = session(Broken(), retain=frozenset({"provisional_transcript"}))
    assert perform(s, 1, "stt", b"input").status == "invalid_output"
    assert s.export() == {}


def test_concurrent_duplicate_invokes_once_and_configure_purges():
    entered, release = Event(), Event()
    class Slow(SyntheticBackend):
        def transcribe(self, audio):
            entered.set()
            assert release.wait(5)
            return super().transcribe(audio)
    s, b = session(Slow(), retain=frozenset({"raw_audio", "provisional_transcript"}))
    capture = s.capture_token
    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(s.perform, 1, "stt", b"input", capture_token=capture)
        assert entered.wait(5)
        duplicate = pool.submit(s.perform, 1, "stt", b"input", capture_token=capture)
        config = pool.submit(s.configure, Policy())
        release.set()
        assert first.result().status == "ok"
        assert duplicate.result().status == "already_processed"
        config.result()
    assert len(b.calls) == 1
    assert s.export() == {}


def test_shipped_backend_is_explicitly_unsupported_for_both_features():
    p = Policy(Selection("local_only", "unsupported"), Selection("local_only", "unsupported"), True)
    s = SpeechSession(policy=p)
    assert perform(s, 1, "stt", b"synthetic input").status == "unavailable"
    assert perform(s, 2, "tts", "caption").status == "unavailable"
    assert s.describe("stt").network is False
    assert s.describe("tts").fallback == "text"
    assert s.export() == {}


@pytest.mark.parametrize("output", ["audio", b"", b"x" * (4*1024*1024+1)])
def test_invalid_tts_output_does_not_escape_or_retain(output):
    class Broken(SyntheticBackend):
        def synthesize(self, text):
            return output
    s, _ = session(Broken(), retain=frozenset({"synthesized_audio"}))
    result = perform(s, 1, "tts", "caption")
    assert result.status == "invalid_output" and result.audio is None
    assert s.export() == {}


def test_stt_off_does_not_disable_separately_selected_tts_or_typed_play():
    s, b = session(recording=False)
    assert perform(s, 1, "stt", b"input").status == "recording_off"
    assert perform(s, 2, "tts", "caption").status == "ok"
    assert [c[0] for c in b.calls] == ["tts"]
    assert s.keyboard(Utterance("I wave")).kind == "reject"  # no PC authority by default
