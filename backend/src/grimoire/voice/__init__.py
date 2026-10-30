"""Portrayal-scoped voice evidence and one-call-per-speaker rendering (sprint 014).

Pure and deterministic: no model calls, clock, I/O or persistence. See
docs/VOICE.md for the accepted contract.
"""

from .binding import ResolvedVoice, VoiceBinding, resolve_voice
from .compare import FLATTENED_THRESHOLD, example_copies, flattened, voice_overlap
from .errors import InvalidVoice, MissingVoice, VoiceMismatch
from .fragment import (
    DEFAULT_MAX_CHARS, EXAMPLES_HEADER, HEADER, MIN_MAX_CHARS, Omission, SecretFact, SpeakerFragment,
    speaker_fragment,
)
from .render import SpeakerRenderer, SpeakerRequest, SpokenLine, render_speakers
from .profile import (
    ASPECTS, MAX_ANCHOR_CHARS, MAX_EXAMPLE_CHARS, MAX_ITEMS, ImportedVoiceSource, VoiceAnchor,
    VoiceCatalog, VoiceExample, VoiceProfile,
)

__all__ = [
    "FLATTENED_THRESHOLD", "example_copies", "flattened", "voice_overlap",
    "SpeakerRenderer", "SpeakerRequest", "SpokenLine", "render_speakers",
    "DEFAULT_MAX_CHARS", "EXAMPLES_HEADER", "HEADER", "MIN_MAX_CHARS", "Omission", "SecretFact",
    "SpeakerFragment", "speaker_fragment",
    "ResolvedVoice", "VoiceBinding", "resolve_voice",
    "InvalidVoice", "MissingVoice", "VoiceMismatch",
    "ASPECTS", "MAX_ANCHOR_CHARS", "MAX_EXAMPLE_CHARS", "MAX_ITEMS", "ImportedVoiceSource",
    "VoiceAnchor", "VoiceCatalog", "VoiceExample", "VoiceProfile",
]
