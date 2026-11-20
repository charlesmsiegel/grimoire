"""Bounded PCM16 WAV values and deterministic degradations for acoustic evaluation.

Pure standard library: no microphone, device, provider or random-number generator state. Degradations
model what an evaluation needs (quiet speech, steady noise, a far talker, simultaneous audio, a cut-off
window); they are test stimuli, not a model of any real room, microphone or person.
"""
from __future__ import annotations

import io
import math
import wave
from array import array
from dataclasses import dataclass, field

from .model import MAX_AUDIO

RATES = frozenset({8000, 16000, 22050, 32000, 44100, 48000})
_PEAK = 32767


@dataclass(frozen=True)
class Pcm:
    """Mono signed 16-bit samples at one sample rate."""
    rate: int
    samples: array = field(repr=False)

    def __post_init__(self):
        if self.rate not in RATES:
            raise ValueError("unsupported sample rate")
        if not isinstance(self.samples, array) or self.samples.typecode != "h":
            raise ValueError("expected signed 16-bit samples")
        if len(self.samples) * 2 + 44 > MAX_AUDIO:
            raise ValueError("audio too long")

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.rate


def read_wav(data: bytes) -> Pcm:
    """Parse an uncompressed mono 16-bit WAV. Anything else is refused, never resampled or guessed."""
    if not isinstance(data, bytes) or not 44 <= len(data) <= MAX_AUDIO:
        raise ValueError("invalid or oversized audio")
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getcomptype() != "NONE":
                raise ValueError("expected mono 16-bit PCM WAV")
            rate, frames = reader.getframerate(), reader.readframes(reader.getnframes())
    except (wave.Error, EOFError) as error:
        raise ValueError("unreadable WAV") from error
    if len(frames) % 2:
        raise ValueError("truncated sample")
    samples = array("h")
    samples.frombytes(frames)
    if samples.itemsize != 2:
        raise ValueError("unsupported platform sample size")
    if not _little_endian():
        samples.byteswap()
    return Pcm(rate, samples)


def write_wav(pcm: Pcm) -> bytes:
    if not isinstance(pcm, Pcm):
        raise ValueError("expected PCM")
    samples = array("h", pcm.samples)
    if not _little_endian():
        samples.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(pcm.rate)
        writer.writeframes(samples.tobytes())
    return out.getvalue()


def _little_endian() -> bool:
    return array("h", [1]).tobytes() == b"\x01\x00"


def _clip(value: float) -> int:
    return max(-_PEAK - 1, min(_PEAK, int(round(value))))


def rms(samples) -> float:
    return math.sqrt(sum(s * s for s in samples) / len(samples)) if len(samples) else 0.0


def dbfs(level: float) -> float:
    return 20 * math.log10(level / _PEAK) if level > 0 else -math.inf


def gain(pcm: Pcm, db: float) -> Pcm:
    """Scale by ``db`` decibels (negative is quieter), clipping at full scale."""
    factor = _factor(db)
    return Pcm(pcm.rate, array("h", (_clip(s * factor) for s in pcm.samples)))


def _factor(db: float) -> float:
    if type(db) not in (int, float) or not math.isfinite(db) or not -90 <= db <= 40:
        raise ValueError("invalid gain")
    return 10 ** (db / 20)


def pad(pcm: Pcm, before: float = 0.0, after: float = 0.0) -> Pcm:
    """Digital silence before/after: what an opened listening window holds around speech."""
    head, tail = _frames(pcm.rate, before), _frames(pcm.rate, after)
    return Pcm(pcm.rate, array("h", bytes(2 * head)) + pcm.samples + array("h", bytes(2 * tail)))


def truncate(pcm: Pcm, seconds: float) -> Pcm:
    """The first ``seconds`` only, as when a listening window closes mid-utterance."""
    return Pcm(pcm.rate, pcm.samples[:_frames(pcm.rate, seconds)])


def _frames(rate: int, seconds: float) -> int:
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 120:
        raise ValueError("invalid duration")
    return int(round(seconds * rate))


def far(pcm: Pcm, db: float = -18.0) -> Pcm:
    """A crude far talker: quieter, high frequencies dulled and a short echo. A stimulus, not acoustics."""
    alpha, delay = 0.35, _frames(pcm.rate, 0.045)
    smoothed, previous = [], 0.0
    for sample in pcm.samples:
        previous = previous + alpha * (sample - previous)
        smoothed.append(previous)
    echoed = [v + (0.45 * smoothed[i - delay] if i >= delay else 0.0) for i, v in enumerate(smoothed)]
    factor = _factor(db)
    return Pcm(pcm.rate, array("h", (_clip(v * factor) for v in echoed)))


def mix(base: Pcm, other: Pcm, *, offset: float = 0.0, db: float = 0.0) -> Pcm:
    """``other`` added into ``base`` starting ``offset`` seconds in; the result has base's length."""
    if not isinstance(other, Pcm) or other.rate != base.rate:
        raise ValueError("mix needs matching sample rates")
    start, factor = _frames(base.rate, offset), _factor(db)
    out = array("h", base.samples)
    for i, sample in enumerate(other.samples):
        if start + i >= len(out):
            break
        out[start + i] = _clip(out[start + i] + sample * factor)
    return Pcm(base.rate, out)


def noise(pcm: Pcm, snr_db: float, *, seed: int, kind: str = "white") -> Pcm:
    """Add seeded steady noise at ``snr_db`` relative to the whole clip's RMS. ``kind`` is ``white`` or
    ``hum`` (a 60 Hz mains tone with harmonics). The generator is a fixed LCG, so bytes never depend on
    Python's random module version."""
    if type(snr_db) not in (int, float) or not math.isfinite(snr_db) or not -20 <= snr_db <= 80:
        raise ValueError("invalid SNR")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("invalid seed")
    if kind not in ("white", "hum"):
        raise ValueError("invalid noise kind")
    level = rms(pcm.samples)
    if level == 0:
        raise ValueError("no signal to set an SNR against")
    target = level / 10 ** (snr_db / 20)
    if kind == "white":
        state, raw = seed, []
        for _ in pcm.samples:
            state = (1103515245 * state + 12345) % 2**31
            raw.append(state / 2**30 - 1.0)
    else:
        raw = [sum(math.sin(2 * math.pi * 60 * h * i / pcm.rate + seed) / h for h in (1, 2, 3))
               for i in range(len(pcm.samples))]
    scale = target / (rms(raw) or 1.0)
    return Pcm(pcm.rate, array("h", (_clip(s + r * scale) for s, r in zip(pcm.samples, raw))))


def hiss(rate: int, seconds: float, level_dbfs: float, *, seed: int) -> Pcm:
    """Seeded white noise alone at an absolute RMS level: a window that holds no speech at all."""
    if type(level_dbfs) not in (int, float) or not math.isfinite(level_dbfs) or not -90 <= level_dbfs <= 0:
        raise ValueError("invalid level")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("invalid seed")
    count, state, raw = _frames(rate, seconds), seed, []
    for _ in range(count):
        state = (1103515245 * state + 12345) % 2**31
        raw.append(state / 2**30 - 1.0)
    scale = _PEAK * 10 ** (level_dbfs / 20) / (rms(raw) or 1.0)
    return Pcm(rate, array("h", (_clip(r * scale) for r in raw)))


def concat(*parts: Pcm) -> Pcm:
    if not parts or any(not isinstance(p, Pcm) or p.rate != parts[0].rate for p in parts):
        raise ValueError("concat needs PCM at one rate")
    out = array("h")
    for part in parts:
        out += part.samples
    return Pcm(parts[0].rate, out)


__all__ = ["Pcm", "RATES", "concat", "dbfs", "far", "gain", "hiss", "mix", "noise", "pad", "read_wav", "rms",
           "truncate", "write_wav"]
