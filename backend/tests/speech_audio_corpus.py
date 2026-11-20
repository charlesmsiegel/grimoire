"""067's synthetic-audio cases: invented phrases read by speech synthesizers (``fixtures/speech_audio``), then
degraded deterministically. No recordings of people, and no voice here stands for an accent, a disability or
any real speaker; a result on these clips is not a claim about any group of people.

Each window is what a gesture-opened listening window would hold: a short pause before the person speaks
(``LEAD``) and silence after. ``CASES`` is the gated corpus. ``KNOWN_LIMITS`` are cases a single-microphone
capture cannot separate; they are measured and reported, never silently dropped.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from grimoire.intent import Command, RoutingContext
from grimoire.speech.acoustic import concat, far, gain, hiss, mix, noise, pad, read_wav, truncate, write_wav
from grimoire.speech.evaluation import AudioCase, Window

from sphinx_recognizer import MIN_CONFIDENCE

FOLDER = Path(__file__).with_name("fixtures") / "speech_audio"
MANIFEST = json.loads((FOLDER / "MANIFEST.json").read_text())
CONTEXT = RoutingContext("pc-1", True, min_confidence=MIN_CONFIDENCE)
LEAD, TAIL = 0.4, 0.6

OPEN, PAUSE, STOP, DELETE = (Command("navigate", "inventory"), Command("pause"), Command("stop"),
                             Command("delete_scene"))
SPEAKERS = ("us1", "us2", "us3", "en1", "robot")


def clip(name):
    data = (FOLDER / f"{name}.wav").read_bytes()
    if hashlib.sha256(data).hexdigest() != MANIFEST["clips"][name]["sha256"]:
        raise AssertionError(f"{name}.wav does not match MANIFEST.json")
    return read_wav(data)


def window(pcm, lead=LEAD, tail=TAIL):
    return pad(pcm, lead, tail)


def audio(pcm, **window_args):
    return Window(write_wav(pcm), **window_args)


def case(id, category, expected, *windows, bystander=False):
    return AudioCase(id, tuple(windows), frozenset(expected), category, bystander)


ACT = lambda command: {("act", command)}  # noqa: E731
SAFE_MISS = {("none", None), ("shown", None), ("draft", None)}  # nothing happens, or a reviewable draft
NOTHING = {("none", None), ("shown", None)}


def _cases():
    out = []
    # The person, clean, five synthesized voices. A recognizer miss that comes out as other words is a safe
    # miss (a draft for review), never an act; completion counts only the intended outcome.
    for speaker in SPEAKERS:
        category = "atypical" if speaker == "robot" else "clean"
        out += [
            case(f"{speaker}-open", category, ACT(OPEN), audio(window(clip(f"{speaker}-open")))),
            case(f"{speaker}-pause", category, ACT(PAUSE), audio(window(clip(f"{speaker}-pause")))),
            case(f"{speaker}-stop", category, ACT(STOP), audio(window(clip(f"{speaker}-stop")))),
            # Voice can name a delete but never perform it: the catalog's confirm gate needs the button.
            case(f"{speaker}-delete", category, NOTHING, audio(window(clip(f"{speaker}-delete")))),
        ]
    for variant in ("slow", "fast", "high"):
        out.append(case(f"us1-open-{variant}", "rate", ACT(OPEN), audio(window(clip(f"us1-open-{variant}")))))
    out.append(case("us1-disfluent-pause", "disfluency", ACT(PAUSE) | SAFE_MISS, audio(window(clip("us1-disfluent")))))
    # Quiet speech: the person far below a normal level, with and without a quiet room's noise.
    for speaker in ("us1", "us3", "en1"):
        out.append(case(f"{speaker}-open-quiet", "quiet", ACT(OPEN), audio(window(gain(clip(f"{speaker}-open"), -24)))))
        out.append(case(f"{speaker}-pause-quiet", "quiet", ACT(PAUSE), audio(window(gain(clip(f"{speaker}-pause"), -30)))))
    # Steady noise: white noise and mains hum at moderate SNRs.
    for seed, speaker in enumerate(("us1", "us2", "en1"), start=1):
        out.append(case(f"{speaker}-open-noise", "noise", ACT(OPEN) | {("review", OPEN)} | SAFE_MISS,
                        audio(noise(window(clip(f"{speaker}-open")), 12, seed=seed))))
        out.append(case(f"{speaker}-pause-hum", "noise", ACT(PAUSE) | SAFE_MISS,
                        audio(noise(window(clip(f"{speaker}-pause")), 15, seed=seed, kind="hum"))))
        # Steady noise alone, no speech at all: nothing to recognize.
        out.append(case(f"noise-only-{seed}", "noise", NOTHING, audio(hiss(16000, 2.0, -40 + 10 * seed, seed=seed)),
                        bystander=True))
    # Other people. Their speech is already under way when the person opens the window (a TV, a table
    # conversation), or it overlaps the person's own command. None of it may act.
    for name in ("bg-delete", "bg-command", "bg-chatter", "bg-yes", "bg-open"):
        out.append(case(f"{name}-under-way", "background", NOTHING, audio(pad(far(clip(name)), 0, TAIL)),
                        bystander=True))
        out.append(case(f"{name}-loud-under-way", "background", NOTHING, audio(pad(clip(name), 0.05, TAIL)),
                        bystander=True))
    for speaker in ("us1", "us3", "en1"):
        out.append(case(f"{speaker}-open-over-chatter", "simultaneous", ACT(OPEN) | NOTHING,
                        audio(mix(window(clip(f"{speaker}-open")), far(clip("bg-chatter")), offset=0))))
        # Someone says "delete scene" over the person's "pause", far and at the person's own level.
        for db in (-6, 0):
            out.append(case(f"{speaker}-pause-over-delete{db}", "simultaneous", ACT(PAUSE) | SAFE_MISS,
                            audio(mix(window(clip(f"{speaker}-pause")), far(clip("bg-command"), db), offset=LEAD + 0.05))))
    # The window closes mid-command: cut off, never recognized.
    for speaker in ("us1", "en1"):
        out.append(case(f"{speaker}-delete-cut", "interruption", NOTHING,
                        audio(truncate(window(clip(f"{speaker}-delete")), LEAD + 0.5))))
        out.append(case(f"{speaker}-open-cut", "interruption", NOTHING,
                        audio(truncate(window(clip(f"{speaker}-open")), LEAD + 0.6))))
        # Two separate stretches of speech in one window: not attributable to one presser.
        out.append(case(f"{speaker}-two-utterances", "interruption", NOTHING,
                        audio(concat(window(clip(f"{speaker}-pause"), tail=0.5), clip("bg-command"), pad(clip("bg-yes"), 0, TAIL)))))
    # A quiet speaker in a noisy room: recognized but below the confidence threshold, so the one safe
    # command waits for an explicit yes. They confirm by voice (equally quiet), by a typed yes, or return to
    # typed controls; an affirmative from someone else under way when they open the answer window is blocked.
    for speaker, snr, answer_snr in (("us2", 8, 8), ("robot", 6, 10)):
        quiet = noise(window(gain(clip(f"{speaker}-open"), -24)), snr, seed=40)
        quiet_yes = noise(window(gain(clip(f"{speaker}-yes"), -24)), answer_snr, seed=41)
        out += [
            case(f"{speaker}-quiet-open-proposed", "confirmation", {("review", OPEN)}, audio(quiet)),
            case(f"{speaker}-quiet-open-voice-yes", "confirmation", ACT(OPEN), audio(quiet), audio(quiet_yes)),
            case(f"{speaker}-quiet-open-typed-yes", "confirmation", ACT(OPEN), audio(quiet), Window(typed="yes", answer=True)),
            case(f"{speaker}-quiet-open-background-yes", "confirmation", {("review", OPEN), ("none", None)},
                 audio(quiet), audio(pad(far(clip("bg-yes")), 0, TAIL))),
            case(f"{speaker}-quiet-back-to-typing", "recovery", ACT(OPEN), audio(quiet),
                 Window(typed="open inventory")),
        ]
    # An atypical-delivery miss is recovered by typing, no microphone needed.
    out.append(case("robot-delete-then-typed-pause", "recovery", ACT(PAUSE), audio(window(clip("robot-delete"))),
                    Window(typed="pause")))
    return tuple(out)


CASES = _cases()

# A bystander who starts speaking promptly inside a window the person opened is indistinguishable from the
# person on one microphone. Instant commands (navigation, pause/stop/cancel) can then act, and a bystander's
# yes can confirm the person's own pending safe proposal. Confirm-gated and authorized commands still cannot.
KNOWN_LIMITS = (
    case("bystander-prompt-open", "background", NOTHING, audio(window(far(clip("bg-open")))), bystander=True),
    case("bystander-prompt-delete", "background", NOTHING, audio(window(far(clip("bg-command")))), bystander=True),
    case("bystander-prompt-yes", "background", NOTHING, audio(window(far(clip("bg-yes")))), bystander=True),
)
