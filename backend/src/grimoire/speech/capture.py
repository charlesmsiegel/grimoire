"""One explicitly opened listening window, conservatively attributed before any recognition.

There is no always-on microphone here. Capture exists only inside a window a person opened with a gesture
(a key, a pointer press, an accessibility switch); this module never opens one. ``analyze`` reads the window's
audio with a level-based speech detector and refuses to call it the presser's speech unless it is one
complete, prompt, clearly-above-the-floor region. Anything else (no speech, speech already under way when the
window opened, speech that starts long after it opened, two separate stretches of speech, speech cut off by
the window's end) is blocked **before** recognition, so bystanders' speech is never sent to a
recognizer. This is a single-microphone heuristic, not speaker identification: it cannot separate a talker
who overlaps the person, or who starts promptly inside a window the person opened. The command catalog's gates are the backstop, and
``voice_action`` never lets voice act on an authorized or confirm-gated command.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from grimoire.intent import Intent

from .acoustic import Pcm, dbfs, read_wav, rms
from .control import ControlResult, Recognition
from .model import SpeechResult, identifier

#: Gestures that may open a window. A wake word or continuous listening is deliberately not one of them.
TRIGGERS = frozenset({"keyboard", "pointer", "switch"})
MAX_WINDOW = 15.0       # seconds; a window is bounded, never an open-ended stream
OPEN_GUARD = 0.12       # speech starting this early was already under way when the window opened
ONSET_GRACE = 3.0       # speech starting later than this is not presumed to be the presser's
TAIL = 0.25             # a final utterance ends at least this long before the window closes
MERGE_GAP = 0.35        # pauses shorter than this stay inside one utterance
MIN_REGION = 0.10
FLOOR_MARGIN = 9.0      # dB above the window's noise floor that counts as speech
ABSOLUTE_FLOOR = -60.0  # dBFS; quieter frames are never speech
REASONS = frozenset({"no_speech", "speech_at_open", "late_onset", "multiple_segments", "cut_off",
                     "too_long", "ok"})


@dataclass(frozen=True)
class Analysis:
    reason: str
    regions: tuple[tuple[float, float], ...] = ()
    floor_db: float = -math.inf

    @property
    def foreground(self) -> bool:
        return self.reason == "ok"


def _frames_db(pcm: Pcm) -> list[float]:
    size = max(1, pcm.rate // 50)  # 20 ms
    return [dbfs(rms(pcm.samples[i:i + size])) for i in range(0, len(pcm.samples) - size + 1, size)]


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


def analyze(pcm: Pcm) -> Analysis:
    """Where the window holds speech and whether it may be attributed to the person who opened it."""
    if not isinstance(pcm, Pcm):
        raise ValueError("expected PCM")
    if pcm.seconds > MAX_WINDOW:
        return Analysis("too_long")
    levels = _frames_db(pcm)
    if not levels:
        return Analysis("no_speech")
    floor = _percentile(levels, 0.10)
    threshold = max(floor + FLOOR_MARGIN if math.isfinite(floor) else ABSOLUTE_FLOOR, ABSOLUTE_FLOOR)
    step = 0.02
    runs, start = [], None
    for i, level in enumerate(levels + [-math.inf]):
        if level >= threshold and start is None:
            start = i
        elif level < threshold and start is not None:
            runs.append([start, i])
            start = None
    merged = []
    for run in runs:
        if merged and (run[0] - merged[-1][1]) * step < MERGE_GAP:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    kept = [r for r in merged if (r[1] - r[0]) * step >= MIN_REGION]
    regions = tuple((round(a * step, 3), round(b * step, 3)) for a, b in kept)
    if not regions:
        return Analysis("no_speech", (), floor)
    first, last = regions[0], regions[-1]
    if first[0] < OPEN_GUARD:
        return Analysis("speech_at_open", regions, floor)
    if first[0] > ONSET_GRACE:
        return Analysis("late_onset", regions, floor)
    if len(regions) > 1:
        return Analysis("multiple_segments", regions, floor)
    if last[1] > pcm.seconds - TAIL:
        return Analysis("cut_off", regions, floor)
    return Analysis("ok", regions, floor)


@dataclass(frozen=True)
class Capture:
    """Audio from one window and how it was opened. The capture adapter latches ``capture_token`` (and, to
    answer a pending proposal, its ``proposal_token``) when the window opens, never afterwards."""
    audio: bytes = field(repr=False)
    trigger: str
    capture_token: str
    proposal_token: str | None = None

    def __post_init__(self):
        if self.trigger not in TRIGGERS:
            raise ValueError("a listening window needs an explicit gesture")
        identifier(self.capture_token)
        if self.proposal_token is not None:
            identifier(self.proposal_token)


@dataclass(frozen=True)
class Heard:
    """What one window came to. ``status``: ``blocked`` (never recognized), ``refused`` (the session said
    no), ``routed`` (a hypothesis, nothing executed), ``confirmed`` / ``not_confirmed`` for an answer."""
    status: str
    reason: str
    transcript: str | None = field(default=None, repr=False)
    intent: Intent | None = field(default=None, repr=False)
    analysis: Analysis | None = None


def hear(session, sequence: int, capture: Capture) -> Heard:
    """Analyze, then (only for attributable speech) recognize through the session and route or confirm."""
    if not isinstance(capture, Capture):
        raise ValueError("expected a capture")
    try:
        pcm = read_wav(capture.audio)
    except ValueError:
        return Heard("blocked", "unreadable_audio")
    analysis = analyze(pcm)
    if not analysis.foreground:
        return Heard("blocked", analysis.reason, analysis=analysis)
    result: SpeechResult = session.perform(sequence, "stt", capture.audio, capture_token=capture.capture_token,
                                           confirmation_token=capture.proposal_token)
    if result.status != "ok":
        return Heard("refused", result.status, analysis=analysis)
    utterance = result.transcript
    event = Recognition(utterance, attribution="foreground", engaged=True, overlap=False, final=True,
                        proposal_token=result.confirmation_token, capture_token=result.capture_token)
    if capture.proposal_token is not None:
        intent = session.confirm(event)
        return Heard("confirmed" if intent is not None else "not_confirmed",
                     "confirmed" if intent is not None else "not_affirmed", utterance.text, intent, analysis)
    control: ControlResult = session.accept(event)
    return Heard(control.status, control.reason, utterance.text, control.intent, analysis)


def voice_action(intent: Intent | None) -> str:
    """What a voice result may do in the UI: ``act`` (an instant command), ``draft`` (PC or director text,
    placed for review, never posted), ``review`` (one safe command awaiting an explicit yes) or ``show``
    (a caption only). Authorized and confirm-gated commands are ``show``: the same button or key is required,
    so speech, including someone else's, can never delete a scene."""
    if intent is None:
        return "show"
    if intent.kind == "ui_command":
        return "act" if intent.command.gate == "instant" else "show"
    if intent.kind in ("pc_speech", "pc_action", "director_instruction"):
        return "draft"
    return "show"


__all__ = ["Analysis", "Capture", "Heard", "MAX_WINDOW", "REASONS", "TRIGGERS", "analyze", "hear",
           "voice_action"]
