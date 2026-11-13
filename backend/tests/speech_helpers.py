"""Synthetic adapter fixtures; no network, filesystem or acoustic recognition."""
from dataclasses import replace

from grimoire.intent import Utterance
from grimoire.speech import BackendInfo, Policy, Selection, SpeechSession


class SyntheticBackend:
    info = BackendInfo("synthetic", frozenset({"stt", "tts"}), "local", False, False, "free", True)

    def __init__(self, **info):
        self.info = replace(self.info, **info)
        self.calls = []

    def transcribe(self, audio):
        self.calls.append(("stt", audio))
        return Utterance("open inventory", origin="voice", confidence=0.9)

    def synthesize(self, text):
        self.calls.append(("tts", text))
        return b"synthetic-output"


def policy(**changes):
    return replace(Policy(stt=Selection("local_only", "synthetic"),
                          tts=Selection("local_only", "synthetic"), recording=True), **changes)


def session(backend=None, **changes):
    backend = backend or SyntheticBackend()
    return SpeechSession((backend,), policy(**changes)), backend


def perform(s, sequence, feature, payload, **kwargs):
    """Latch the current capture generation at this synthetic input's creation."""
    return s.perform(sequence, feature, payload, capture_token=s.capture_token, **kwargs)
