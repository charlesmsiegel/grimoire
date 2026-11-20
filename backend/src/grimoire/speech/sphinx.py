"""A local speech recognizer (061): CMU PocketSphinx, an optional, free, on-device engine.

PocketSphinx is not a dependency of Grimoire. Where the ``pocketsphinx`` package and its bundled English model
are installed, ``PocketSphinxBackend`` declares itself available, local, network-free and non-retaining; where
they are not, it says so and the session reports ``unavailable``. It never falls back to another engine.

Audio is a WAV file: one channel, 16-bit samples, 16 000 frames a second, at most ``max_seconds`` long. The
model loads on first use (or ``load``) and stays loaded until ``unload``; every load and every recognition is
measured (``measurements``) so a device's real cost is visible, not assumed. The engine's own log is turned off,
so recognized words never reach a log or a crash trace. Recognition is in this process: no file is written and
no socket is opened.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from array import array
import math
import os
import re
import time
import wave
from dataclasses import dataclass
from threading import Lock

from grimoire.intent import Utterance

from .model import BackendInfo

ID = "pocketsphinx"
RATE = 16000
#: The longest recording a device may be asked to recognize, and the default: a budget the person can lower.
MAX_SECONDS = 60
DEFAULT_SECONDS = 15
#: A grammar phrase: lower-case words of letters and apostrophes, single spaces.
_PHRASE = re.compile(r"[a-z']+(?: [a-z']+){0,11}")
MAX_PHRASES = 64
#: Below this root-mean-square level (about -50 dBFS) a recording is silence: nothing is recognized from it,
#: because the engine otherwise "hears" a word in pure silence.
SILENCE_RMS = 100.0


class AudioRefused(ValueError):
    """The audio is not a recording this recognizer takes (format, length); nothing was recognized."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def installed() -> bool:
    """Whether the engine and its bundled English model are present, without loading the model."""
    if importlib.util.find_spec("pocketsphinx") is None:
        return False
    try:
        from pocketsphinx import get_model_path
    except Exception:  # a broken install is an absent one
        return False
    folder = os.path.join(get_model_path(), "en-us")
    return all(os.path.exists(os.path.join(folder, name)) for name in ("en-us", "en-us.lm.bin", "cmudict-en-us.dict"))


def pcm(audio: bytes, max_seconds: float) -> tuple[bytes, float]:
    """The 16-bit mono 16 kHz samples of a WAV recording and its length in seconds, or ``AudioRefused``."""
    if not isinstance(audio, bytes | bytearray):
        raise AudioRefused("invalid_audio", "the recording must be WAV bytes")
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            channels, width, rate, frames = (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(),
                                             reader.getnframes())
            if (channels, width, rate) != (1, 2, RATE):
                raise AudioRefused("invalid_audio", "the recording must be 16-bit mono WAV at 16 000 Hz")
            seconds = frames / RATE
            if frames == 0:
                raise AudioRefused("invalid_audio", "the recording is empty")
            if seconds > max_seconds:
                raise AudioRefused("too_long", f"the recording is longer than the {max_seconds:g}-second limit")
            samples = reader.readframes(frames)
    except (wave.Error, EOFError):
        raise AudioRefused("invalid_audio", "the recording is not a readable WAV file") from None
    if len(samples) != frames * 2:
        raise AudioRefused("invalid_audio", "the recording is shorter than its header says")
    return samples, seconds


def level(samples: bytes) -> float:
    """The root-mean-square level of 16-bit little-endian samples."""
    values = array("h")
    values.frombytes(samples)
    if sys.byteorder != "little":
        values.byteswap()
    return math.sqrt(sum(value * value for value in values) / len(values)) if values else 0.0


def _rss_bytes() -> int | None:
    """This process's resident memory now, where the system says (Linux and Android); else ``None``."""
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            return int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


@dataclass(frozen=True)
class Measurements:
    """What the engine cost on this device, measured here: never an estimate."""

    loaded: bool
    load_seconds: float | None = None
    #: Resident memory the model load added, in bytes, where the system reports resident memory.
    load_memory: int | None = None
    recognitions: int = 0
    last_audio_seconds: float | None = None
    last_seconds: float | None = None

    @property
    def last_real_time_factor(self) -> float | None:
        """Seconds spent per second of audio on the last recognition: under 1 is faster than speech."""
        if not self.last_audio_seconds or self.last_seconds is None:
            return None
        return self.last_seconds / self.last_audio_seconds


def grammar(phrases: tuple[str, ...]) -> str:
    """A JSGF grammar accepting exactly ``phrases`` (a command vocabulary)."""
    if not isinstance(phrases, tuple) or not 0 < len(phrases) <= MAX_PHRASES:
        raise ValueError("a grammar needs one to 64 phrases")
    for phrase in phrases:
        if not isinstance(phrase, str) or not _PHRASE.fullmatch(phrase):
            raise ValueError("a phrase is lower-case words separated by single spaces")
    return "#JSGF V1.0;\ngrammar grimoire;\npublic <phrase> = " + " | ".join(phrases) + ";\n"


class PocketSphinxBackend:
    """A trusted local STT adapter for ``SpeechSession``. ``phrases`` restricts recognition to a command
    vocabulary; without it, recognition is open dictation with the bundled language model."""

    def __init__(self, *, phrases: tuple[str, ...] | None = None, max_seconds: float = DEFAULT_SECONDS,
                 available: bool | None = None) -> None:
        self._grammar = grammar(phrases) if phrases is not None else None
        self._lock = Lock()
        self._decoder = None
        self._max_seconds = DEFAULT_SECONDS
        self.max_seconds = max_seconds
        self._measured = Measurements(False)
        self.info = BackendInfo(ID, frozenset({"stt"}), "local", False, False, "free",
                                installed() if available is None else available)

    @property
    def max_seconds(self) -> float:
        return self._max_seconds

    @max_seconds.setter
    def max_seconds(self, value: float) -> None:
        if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= MAX_SECONDS:
            raise ValueError(f"the recording limit is 1 to {MAX_SECONDS} seconds")
        self._max_seconds = value

    @property
    def measurements(self) -> Measurements:
        with self._lock:
            return self._measured

    def load(self) -> None:
        """Load the model now (it otherwise loads on first use), measuring the time and memory it takes."""
        with self._lock:
            self._load()

    def _load(self):  # type: ignore[no-untyped-def]
        if self._decoder is not None:
            return self._decoder
        if not self.info.available:
            raise RuntimeError("speech recognizer not installed")
        from pocketsphinx import Config, Decoder

        before, started = _rss_bytes(), time.perf_counter()
        # FATAL only: the engine's informational log would otherwise print what it heard.
        config = Config(samprate=RATE, loglevel="FATAL")
        decoder = Decoder(config)
        if self._grammar is not None:
            decoder.add_jsgf_string("grimoire", self._grammar)
            decoder.activate_search("grimoire")
        seconds = time.perf_counter() - started
        after = _rss_bytes()
        memory = after - before if before is not None and after is not None else None
        self._decoder = decoder
        self._measured = Measurements(True, seconds, memory, self._measured.recognitions,
                                      self._measured.last_audio_seconds, self._measured.last_seconds)
        return decoder

    def unload(self) -> None:
        """Let the model's memory go; the next recognition loads it again."""
        with self._lock:
            self._decoder = None
            self._measured = Measurements(False, None, None, self._measured.recognitions,
                                          self._measured.last_audio_seconds, self._measured.last_seconds)

    def transcribe(self, audio: bytes) -> Utterance:
        samples, seconds = pcm(audio, self.max_seconds)
        if level(samples) < SILENCE_RMS:
            return Utterance("", origin="voice")
        with self._lock:
            decoder = self._load()
            started = time.perf_counter()
            decoder.start_utt()
            try:
                decoder.process_raw(samples, full_utt=True)
            finally:
                decoder.end_utt()
            hypothesis = decoder.hyp()
            spent = time.perf_counter() - started
            measured = self._measured
            self._measured = Measurements(measured.loaded, measured.load_seconds, measured.load_memory,
                                          measured.recognitions + 1, seconds, spent)
        words = hypothesis.hypstr.strip() if hypothesis is not None else ""
        probability = hypothesis.prob if hypothesis is not None else None
        confidence = probability if isinstance(probability, float) and 0.0 <= probability <= 1.0 else None
        return Utterance(words, origin="voice", confidence=confidence if words else None)

    def synthesize(self, text: str) -> bytes:
        raise NotImplementedError("pocketsphinx does not synthesize speech")


def to_wav(samples: bytes, rate: int = RATE) -> bytes:
    """16-bit mono samples as a WAV file (for tests and the measuring tool)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples)
    return buffer.getvalue()


__all__ = ["DEFAULT_SECONDS", "ID", "MAX_SECONDS", "RATE", "SILENCE_RMS", "level", "AudioRefused", "Measurements", "PocketSphinxBackend",
           "grammar", "installed", "pcm", "to_wav"]
