"""Speech privacy values; adapter claims are trusted configuration, not a sandbox."""
from dataclasses import dataclass, field
from typing import Protocol

from grimoire.intent import Utterance

FEATURES = frozenset({"stt", "tts"})
ARTIFACTS = frozenset({"raw_audio", "provisional_transcript", "corrected_draft", "synthesized_audio"})
MAX_AUDIO = 4 * 1024 * 1024
MAX_TEXT = 16384
MAX_SEQUENCE = 2**63 - 1


def boolean(value: object) -> None:
    if type(value) is not bool:
        raise ValueError("expected boolean")


def identifier(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("invalid backend identifier")


def text(value: object) -> None:
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise ValueError("invalid or oversized text")


def audio(value: object) -> None:
    if not isinstance(value, bytes) or not 0 < len(value) <= MAX_AUDIO:
        raise ValueError("invalid or oversized audio")


def transcript(value: object) -> None:
    if not isinstance(value, Utterance) or value.origin != "voice":
        raise ValueError("expected voice transcript")
    text(value.text)
    if len(value.alternatives) > 8:
        raise ValueError("too many transcript alternatives")
    for alternative in value.alternatives:
        text(alternative)


@dataclass(frozen=True)
class Selection:
    mode: str = "off"
    backend_id: str | None = None
    consent_backend_id: str | None = None

    def __post_init__(self):
        if self.mode not in ("off", "local_only", "provider"):
            raise ValueError("invalid speech mode")
        if self.mode != "off" and self.backend_id is None:
            raise ValueError("select an explicit backend")
        for value in (self.backend_id, self.consent_backend_id):
            if value is not None:
                identifier(value)


@dataclass(frozen=True)
class Policy:
    stt: Selection = Selection()
    tts: Selection = Selection()
    recording: bool = False
    retain: frozenset[str] = frozenset()

    def __post_init__(self):
        if not isinstance(self.stt, Selection) or not isinstance(self.tts, Selection):
            raise ValueError("invalid speech selection")
        boolean(self.recording)
        if not isinstance(self.retain, frozenset) or not self.retain <= ARTIFACTS:
            raise ValueError("invalid retention selection")


@dataclass(frozen=True)
class BackendInfo:
    id: str
    features: frozenset[str]
    locality: str = "unknown"
    network: bool | None = None
    retains_inputs: bool | None = None
    billing: str = "unknown"
    available: bool = False

    def __post_init__(self):
        identifier(self.id)
        if not isinstance(self.features, frozenset) or not self.features <= FEATURES:
            raise ValueError("invalid backend features")
        if self.locality not in ("local", "remote", "unknown"):
            raise ValueError("invalid locality")
        if self.billing not in ("free", "paid", "unknown"):
            raise ValueError("invalid billing")
        for value in (self.network, self.retains_inputs):
            if value is not None:
                boolean(value)
        boolean(self.available)


class Backend(Protocol):
    info: BackendInfo

    def transcribe(self, audio: bytes) -> Utterance: ...
    def synthesize(self, text: str) -> bytes: ...


class UnsupportedBackend:
    """Explicit capability, not a pretend local recognizer or synthesizer."""
    info = BackendInfo("unsupported", FEATURES, "local", False, False, "free", False)

    def transcribe(self, audio: bytes) -> Utterance:
        raise NotImplementedError("speech unavailable")

    def synthesize(self, text: str) -> bytes:
        raise NotImplementedError("speech unavailable")


@dataclass(frozen=True)
class Disclosure:
    status: str
    backend: str | None
    input_kind: str
    locality: str
    network: bool | None
    retains_inputs: bool | None
    billing: str
    fallback: str = "text"


@dataclass(frozen=True)
class SpeechResult:
    status: str
    confirmation_token: str | None = None
    capture_token: str | None = None
    transcript: Utterance | None = field(default=None, repr=False)
    audio: bytes | None = field(default=None, repr=False)
