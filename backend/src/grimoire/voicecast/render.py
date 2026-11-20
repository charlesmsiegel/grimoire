"""Rendering a performance plan to speech (083): one synthesis call per voiced cue, with timings.

``render_performance`` takes a ``Performance`` and the synthesizers this device has (one per engine) and gives,
for a window of cues, one ``Segment`` each: the cue's audio (a WAV), when it starts and how long it lasts, laid
end to end with a short gap, or no audio and why. Every cue's text is the plan's accepted text, untouched: a
segment carries the cue's digest, never a second copy of the words, and audio is never turned back into text.

A cue the plan left as text, a voice whose engine is missing or does not have that voice, a synthesis that fails
and an answer that is not a readable WAV each leave that cue **text only**; the rest still render. Nothing falls
back to another engine or another speaker's voice. A synthesizer's exception is not kept or retried (its message
could hold the line), and with ``audio=False`` nothing is synthesized at all: the transcript is read as text.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from .model import CastingError
from .plan import TEXT_ONLY, VOICED, Performance

#: One render call covers at most this many cues and this much script, so one request stays bounded.
MAX_CUES = 32
MAX_CHARS = 8000
#: The longest script one cue speaks: a longer cue stays text (``too_long``) and is never sent to an engine.
MAX_SCRIPT = 4000
#: One cue's audio: a longer answer is refused, not kept. 4000 characters of slow speech fit well inside it.
MAX_AUDIO = 16 * 1024 * 1024
#: The pause between two spoken cues, in seconds.
GAP = 0.3
#: Why a segment has no audio beyond the cue's own reason.
RENDER_REASONS = ("audio_off", "too_long", "engine_unavailable", "voice_unavailable", "synthesis_failed",
                  "invalid_audio")


class Synthesizer(Protocol):
    """A local speech engine. ``engine`` names it as a casting does; ``voices`` are the voices it has now."""

    engine: str

    def voices(self) -> tuple[str, ...]: ...

    def synthesize(self, script: str, voice: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class Segment:
    cue: int
    digest: str
    voice: str | None
    status: str  # "audio" or "text_only"
    reason: str | None
    start: float
    duration: float
    audio: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class Rendering:
    segments: tuple[Segment, ...]
    next: int | None  # the first cue not rendered, or ``None`` when the window reached the end

    @property
    def duration(self) -> float:
        """How long the window's audio plays: the end of its last spoken segment (no trailing gap)."""
        return max((segment.start + segment.duration for segment in self.segments if segment.audio is not None),
                   default=0.0)


def _u16(data: bytes, at: int) -> int:
    return int.from_bytes(data[at:at + 2], "little")


def _u32(data: bytes, at: int) -> int:
    return int.from_bytes(data[at:at + 4], "little")


def wav_seconds(data: object) -> float | None:
    """How long a WAV lasts, or ``None`` when ``data`` is not a readable, nonempty, bounded PCM WAV: a RIFF/WAVE
    file whose ``fmt `` chunk is PCM with sane fields and whose ``data`` chunk is whole (a truncated file, whose
    header promises samples it does not hold, is not a recording). Read by hand, so the core stays pure."""
    if not isinstance(data, bytes) or not 44 <= len(data) <= MAX_AUDIO:
        return None
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    end = 8 + _u32(data, 4)  # the RIFF chunk's own extent: every chunk the file holds lies inside it
    if not 12 <= end <= len(data):
        return None
    at, fmt = 12, None
    while at + 8 <= end:
        kind, size = data[at:at + 4], _u32(data, at + 4)
        body = at + 8
        if body + size > end:
            return None  # a chunk longer than the file: truncated
        if kind == b"fmt ":
            if size < 16:
                return None
            fmt = (_u16(data, body), _u16(data, body + 2), _u32(data, body + 4), _u16(data, body + 12),
                   _u16(data, body + 14))
        elif kind == b"data":
            if fmt is None:
                return None
            code, channels, rate, align, bits = fmt
            if code != 1 or not 1 <= channels <= 8 or rate <= 0 or bits not in (8, 16, 24, 32) \
                    or align != channels * bits // 8:
                return None
            frames = size // align
            return frames / rate if frames > 0 else None
        at = body + size + (size & 1)  # chunks are padded to an even length
    return None


def _engines(synthesizers: Mapping[str, Synthesizer] | Iterable[Synthesizer]) -> dict[str, Synthesizer]:
    if isinstance(synthesizers, Mapping):
        return dict(synthesizers)
    found: dict[str, Synthesizer] = {}
    for synthesizer in synthesizers:
        if synthesizer.engine in found:
            raise CastingError("duplicate_engine", f"two synthesizers for {synthesizer.engine}")
        found[synthesizer.engine] = synthesizer
    return found


def render_performance(performance: Performance, synthesizers: Mapping[str, Synthesizer] | Iterable[Synthesizer],
                       *, start: int = 0, count: int = MAX_CUES, audio: bool = True, gap: float = GAP,
                       max_chars: int = MAX_CHARS, clock: Callable[[], float] | None = None,
                       budget: float | None = None) -> Rendering:
    """Render cues ``start`` to ``start + count`` (at most ``MAX_CUES``, and no more script than ``max_chars``
    unless one cue alone is longer). With ``clock`` and ``budget`` (seconds), no synthesis starts once the
    budget is spent: the window ends there, after at least one cue. ``next`` is where the following call starts."""
    if type(performance) is not Performance:
        raise CastingError("invalid_performance", "render_performance renders a Performance")
    if type(start) is not int or not 0 <= start <= len(performance.cues):
        raise CastingError("invalid_window", "start is a cue index of the performance")
    if type(count) is not int or not 1 <= count <= MAX_CUES:
        raise CastingError("invalid_window", f"count is 1..{MAX_CUES}")
    engines = _engines(synthesizers) if audio else {}
    known: dict[str, tuple[str, ...] | None] = {}
    segments: list[Segment] = []
    at, spent = 0.0, 0
    began = clock() if clock is not None and budget is not None else None
    index = start

    def spent_time() -> bool:
        """Whether the call's time is up: no new synthesis then, once a segment has been made."""
        return bool(segments) and began is not None and clock() - began >= budget  # type: ignore[misc,operator]

    while index < len(performance.cues) and index < start + count:
        cue = performance.cues[index]
        if cue.status == VOICED and segments and spent + len(cue.script) > max_chars:
            break
        if cue.status == VOICED and spent_time():
            break
        index += 1
        if cue.status != VOICED or cue.chosen is None:
            segments.append(Segment(cue.index, cue.digest, None, TEXT_ONLY, cue.reason, at, 0.0))
            continue
        voice = cue.chosen.voice
        reason = None
        data = None
        if not audio:
            reason = "audio_off"
        elif len(cue.script) > MAX_SCRIPT:
            reason = "too_long"
        else:
            engine = engines.get(voice.engine)
            if engine is not None and voice.engine not in known:
                try:
                    known[voice.engine] = tuple(engine.voices())
                except Exception:  # an engine that cannot say what it has has none to use
                    known[voice.engine] = None
                if spent_time():  # asking for the voices used up the time: this cue starts the next window
                    index -= 1
                    break
            if engine is None or not known.get(voice.engine):  # an engine with no voices has none to speak
                reason = "engine_unavailable"
            elif voice.voice not in known[voice.engine]:  # type: ignore[operator]
                reason = "voice_unavailable"
            else:
                spent += len(cue.script)
                try:
                    data = engine.synthesize(cue.script, voice.voice)
                except Exception:  # its message may quote the line: neither kept nor retried
                    reason = "synthesis_failed"
        seconds = None if data is None else wav_seconds(data)
        if reason is None and seconds is None:
            reason = "invalid_audio"
        if reason is not None:
            segments.append(Segment(cue.index, cue.digest, voice.key, TEXT_ONLY, reason, at, 0.0))
            continue
        assert seconds is not None and isinstance(data, bytes)
        segments.append(Segment(cue.index, cue.digest, voice.key, "audio", None, at, seconds, data))
        at += seconds + gap
    return Rendering(tuple(segments), index if index < len(performance.cues) else None)


__all__ = ["GAP", "MAX_AUDIO", "MAX_CHARS", "MAX_CUES", "MAX_SCRIPT", "RENDER_REASONS", "Rendering", "Segment", "Synthesizer",
           "render_performance", "wav_seconds"]
