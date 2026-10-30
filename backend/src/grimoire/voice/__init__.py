"""Portrayal-scoped voice evidence and one-call-per-speaker rendering (sprint 014).

Pure and deterministic: no model calls, clock, I/O or persistence. See
docs/VOICE.md for the accepted contract.
"""

from .binding import ResolvedVoice, VoiceBinding, resolve_voice
from .errors import InvalidVoice, MissingVoice, VoiceMismatch
from .profile import (
    ASPECTS, MAX_ANCHOR_CHARS, MAX_EXAMPLE_CHARS, MAX_ITEMS, ImportedVoiceSource, VoiceAnchor,
    VoiceCatalog, VoiceExample, VoiceProfile,
)

__all__ = [
    "ResolvedVoice", "VoiceBinding", "resolve_voice",
    "InvalidVoice", "MissingVoice", "VoiceMismatch",
    "ASPECTS", "MAX_ANCHOR_CHARS", "MAX_EXAMPLE_CHARS", "MAX_ITEMS", "ImportedVoiceSource",
    "VoiceAnchor", "VoiceCatalog", "VoiceExample", "VoiceProfile",
]
