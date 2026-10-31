"""Deterministic voice checks built on the story-quality gate's own measures.

``example_copies`` uses the gate's copy length, so a line it accepts is not a
``voice_sample_copy`` when the gate's samples are these examples. ``flattened``
is a provisional authoring warning that catches near-duplicate voices, not
bland ones; it is not a quality score.
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import combinations

from grimoire.core.identity import InstanceRef
from grimoire.evals.checks import SAMPLE_COPY_CHARS
from grimoire.evals.text import cosine, longest_shared_run, trigram_counts

from .binding import ResolvedVoice
from .errors import InvalidVoice

FLATTENED_THRESHOLD = 0.85


def example_copies(text: str, voices: Iterable[ResolvedVoice]) -> tuple[tuple[InstanceRef, str], ...]:
    """Every example of every given voice that ``text`` reproduces, screened out or not."""
    found = {(voice.actor, example.example_id) for voice in voices for example in voice.examples
             if longest_shared_run(text, example.text) >= SAMPLE_COPY_CHARS}
    return tuple(sorted(found, key=lambda pair: (str(pair[0]), pair[1])))


def _profile(voice: ResolvedVoice):
    return trigram_counts("\n".join(item.text for item in (*voice.anchors, *voice.examples)))


def voice_overlap(a: ResolvedVoice, b: ResolvedVoice) -> float:
    """Character-trigram cosine of two voices' authored text, in [0, 1]."""
    return cosine(_profile(a), _profile(b))


def flattened(voices: Iterable[ResolvedVoice],
              threshold: float = FLATTENED_THRESHOLD) -> tuple[tuple[str, str], ...]:
    """Pairs of actors whose voices are near-duplicates, as sorted ``(str, str)`` refs."""
    if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
            or not 0 <= threshold <= 1):
        raise InvalidVoice("bad_threshold", "threshold must be a number in [0, 1]")
    profiles = {}
    for voice in voices:
        if str(voice.actor) in profiles:
            raise InvalidVoice("duplicate_voice", f"{voice.actor} is given twice")
        profiles[str(voice.actor)] = _profile(voice)
    return tuple(sorted(
        (left, right) for left, right in combinations(sorted(profiles), 2)
        if cosine(profiles[left], profiles[right]) >= threshold
    ))
