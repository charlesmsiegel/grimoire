"""Measure a local speech recognizer on this device (061): ``python -m grimoire.speech.measure``.

It reports what recognition costs here and how well it heard: model load time, the resident memory the load
added and the process's peak, each recording's latency and real-time factor, word error rate and exact
matches, and every network connection attempted while it ran (there must be none). Budgets are checked against
those measurements; a result says which were met, never that an unmeasured one was.

The default corpus is synthetic: a few short phrases spoken by ``ffmpeg``'s built-in Flite voices, generated
here at run time. That is synthetic computer speech, not a person or a microphone, so it measures the engine
and the device, not accuracy for real speakers, accents or rooms. ``--wav FILE=TEXT`` measures your own
recordings instead (16-bit mono 16 kHz WAV); they are read, never copied or uploaded.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass

from .sphinx import ID, RATE, PocketSphinxBackend, installed, pcm, to_wav

#: The default phrases: commands and short dialogue of the kind a player says.
PHRASES = ("pause the scene", "resume the scene", "open the door", "delete that", "yes", "no",
           "who keeps the customs house", "is the cargo safe")
VOICES = ("slt", "awb", "rms", "kal")


@dataclass(frozen=True)
class Budget:
    """What this device may spend on recognition. The defaults are a desktop budget; a phone's is open."""

    load_seconds: float = 10.0
    real_time_factor: float = 1.0
    peak_memory_mib: float = 512.0


@dataclass(frozen=True)
class Case:
    label: str
    audio: bytes
    expected: str


class NetworkGuard:
    """Counts (and refuses) every socket connection attempted while it is active."""

    def __init__(self) -> None:
        self.attempts: list[str] = []

    @contextlib.contextmanager
    def active(self) -> Iterator[NetworkGuard]:
        original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
        original_create, original_sendto = socket.create_connection, socket.socket.sendto
        attempts = self.attempts

        def refuse(kind: str):  # type: ignore[no-untyped-def]
            def blocked(*_args, **_kwargs):  # type: ignore[no-untyped-def]
                attempts.append(kind)
                raise OSError("network refused while measuring local speech")
            return blocked

        socket.socket.connect = refuse("connect")  # type: ignore[method-assign]
        socket.socket.connect_ex = refuse("connect")  # type: ignore[method-assign]
        socket.socket.sendto = refuse("sendto")  # type: ignore[method-assign]
        socket.create_connection = refuse("create_connection")  # type: ignore[assignment]
        try:
            yield self
        finally:
            socket.socket.connect, socket.socket.connect_ex = original_connect, original_connect_ex  # type: ignore[method-assign]
            socket.socket.sendto, socket.create_connection = original_sendto, original_create  # type: ignore[method-assign,assignment]


def flite_available() -> bool:
    """Whether this ``ffmpeg`` has the Flite voice filter (it is often built without it)."""
    tool = shutil.which("ffmpeg")
    if tool is None:
        return False
    try:
        listed = subprocess.run([tool, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[1:2] == ["flite"] for line in listed.stdout.splitlines() if line.strip())


def synthesize(text: str, voice: str = "slt") -> bytes:
    """``text`` spoken by a Flite voice, as 16-bit mono 16 kHz WAV."""
    if voice not in VOICES or not all(ch.isalpha() or ch in " '" for ch in text):
        raise ValueError("plain words and a known voice only")
    tool = shutil.which("ffmpeg")
    if tool is None:
        raise RuntimeError("ffmpeg is not installed")
    spoken = text.replace("'", "")
    done = subprocess.run([tool, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                           f"flite=text='{spoken}':voice={voice}", "-ar", str(RATE), "-ac", "1", "-f", "s16le", "-"],
                          capture_output=True, timeout=60, check=True)
    return to_wav(done.stdout)


def synthetic_corpus(phrases: tuple[str, ...] = PHRASES, voices: tuple[str, ...] = VOICES) -> list[Case]:
    return [Case(f"{voice}: {phrase}", synthesize(phrase, voice), phrase) for voice in voices for phrase in phrases]


def word_errors(expected: str, heard: str) -> tuple[int, int]:
    """(word edits, words expected): the numerator and denominator of word error rate."""
    want, got = expected.lower().split(), heard.lower().split()
    row = list(range(len(got) + 1))
    for i, word in enumerate(want, 1):
        previous, row[0] = row[0], i
        for j, other in enumerate(got, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (word != other))
    return row[len(got)], len(want)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]


def _peak_memory_mib() -> float | None:
    """The process's peak resident memory, where the system reports it (not on Windows)."""
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def measure(backend: PocketSphinxBackend, cases: list[Case], budget: Budget = Budget()) -> dict:
    """Recognize every case on ``backend``, network refused, and report the measurements and budgets."""
    if not cases:
        raise ValueError("an empty corpus measures nothing")
    guard = NetworkGuard()
    rows, latencies, factors = [], [], []
    edits = words = exact = 0
    with guard.active():
        backend.load()
        loaded = backend.measurements
        for case in cases:
            _, seconds = pcm(case.audio, backend.max_seconds)
            started = time.perf_counter()
            heard = backend.transcribe(case.audio).text
            spent = time.perf_counter() - started
            wrong, total = word_errors(case.expected, heard)
            edits, words, exact = edits + wrong, words + total, exact + (wrong == 0)
            latencies.append(spent)
            factors.append(spent / seconds)
            rows.append({"case": case.label, "expected": case.expected, "heard": heard, "audio_seconds": round(seconds, 3),
                         "seconds": round(spent, 4), "real_time_factor": round(spent / seconds, 4), "word_errors": wrong})
    peak = _peak_memory_mib()
    worst_factor = max(factors)
    result = {
        "backend": ID,
        "mode": "grammar" if backend._grammar is not None else "dictation",
        "cases": len(cases),
        "load_seconds": round(loaded.load_seconds or 0.0, 4),
        "load_memory_mib": None if loaded.load_memory is None else round(loaded.load_memory / (1024 * 1024), 1),
        "peak_memory_mib": None if peak is None else round(peak, 1),
        "latency_seconds": {"p50": round(_percentile(latencies, 0.5), 4), "p95": round(_percentile(latencies, 0.95), 4),
                            "max": round(max(latencies), 4)},
        "real_time_factor": {"mean": round(sum(factors) / len(factors), 4), "max": round(worst_factor, 4)},
        "word_error_rate": round(edits / words, 4) if words else None,
        "exact": exact,
        "network_attempts": len(guard.attempts),
        "budget": asdict(budget),
        "within_budget": {"load_seconds": (loaded.load_seconds or 0.0) <= budget.load_seconds,
                          "real_time_factor": worst_factor <= budget.real_time_factor,
                          "peak_memory_mib": None if peak is None else peak <= budget.peak_memory_mib},
        "rows": rows,
    }
    return result


def _cases(args: argparse.Namespace) -> list[Case]:
    if args.wav:
        cases = []
        for item in args.wav:
            path, separator, expected = item.partition("=")
            if not separator:
                raise SystemExit("--wav takes FILE=EXPECTED TEXT")
            with open(path, "rb") as handle:
                cases.append(Case(path, handle.read(), expected.strip().lower()))
        return cases
    if not flite_available():
        raise SystemExit("ffmpeg with Flite is needed to synthesize the default corpus; pass --wav FILE=TEXT instead")
    return synthetic_corpus()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m grimoire.speech.measure", description=__doc__.split("\n\n")[0])
    parser.add_argument("--wav", action="append", metavar="FILE=TEXT", help="a 16-bit mono 16 kHz WAV and what it says")
    parser.add_argument("--grammar", action="store_true", help="recognize only the expected phrases (command mode)")
    parser.add_argument("--max-seconds", type=float, default=60.0, help="the longest recording to accept")
    parser.add_argument("--rows", action="store_true", help="print every recording's result, not only the totals")
    args = parser.parse_args(argv)
    if not installed():
        print(json.dumps({"backend": ID, "available": False,
                          "reason": "pocketsphinx is not installed (pip install pocketsphinx)"}, indent=2))
        return 2
    cases = _cases(args)
    phrases = tuple(sorted({case.expected for case in cases})) if args.grammar else None
    result = measure(PocketSphinxBackend(phrases=phrases, max_seconds=args.max_seconds), cases)
    if not args.rows:
        result.pop("rows")
    print(json.dumps(result, indent=2))
    return 0 if result["network_attempts"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
