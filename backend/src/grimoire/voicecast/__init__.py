"""Character voice performance (083): voice casting, a performance plan of accepted text, and its rendering.

``model`` holds the casting (who speaks with which synthesized voice, and the right to use it), ``plan`` reads a
chat's accepted turns into cues without changing a word, and ``render`` turns cues into timed audio through a local
synthesizer (``flite``). See docs/VOICE_CASTING.md.
"""

from .model import (
    FORMAT, MAX_ENTRIES, MAX_NOTE, RIGHTS, TARGETS, CastEntry, Casting, CastingError, SpeechVoice,
    casting_from_document, casting_to_document, entry_from_document, entry_to_document,
)
from .plan import (
    NARRATOR_LABEL, REASONS, TEXT_ONLY, VOICED, Considered, Cue, Performance, SharedVoice, digest, explain,
    plan_performance, script_of,
)
from .render import (
    GAP, MAX_AUDIO, MAX_CHARS, MAX_CUES, MAX_SCRIPT, RENDER_REASONS, Rendering, Segment, Synthesizer, render_performance,
    wav_seconds,
)

__all__ = [
    "FORMAT", "MAX_ENTRIES", "MAX_NOTE", "RIGHTS", "TARGETS", "CastEntry", "Casting", "CastingError", "SpeechVoice",
    "casting_from_document", "casting_to_document", "entry_from_document", "entry_to_document",
    "NARRATOR_LABEL", "REASONS", "TEXT_ONLY", "VOICED", "Considered", "Cue", "Performance", "SharedVoice", "digest",
    "explain", "plan_performance", "script_of",
    "GAP", "MAX_AUDIO", "MAX_CHARS", "MAX_CUES", "MAX_SCRIPT", "RENDER_REASONS", "Rendering", "Segment", "Synthesizer",
    "render_performance", "wav_seconds",
]
