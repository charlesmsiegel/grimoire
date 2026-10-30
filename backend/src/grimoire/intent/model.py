"""Input and output values of intent routing.

These are a first-slice contract, not a persisted schema. An `Intent` is a
hypothesis about what the speaker meant; nothing here executes it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, get_args

from .commands import Command
from .text import word_form

InputMode = Literal["auto", "command", "speech", "action", "director"]
Origin = Literal["keyboard", "voice"]
IntentKind = Literal["ui_command", "pc_speech", "pc_action",
                     "director_instruction", "uncertain", "reject"]

REASONS = frozenset({
    "empty", "explicit_mode", "lane_conflict", "unknown_command", "quoted", "asterisks",
    "mixed_utterance", "wake_word", "bare_command", "director_marker", "marker_no_separator",
    "first_person", "polite_command", "command_like", "default_speech", "no_active_pc",
    "not_director", "asr_disagreement", "low_confidence",
})
PC_KINDS = frozenset({"pc_speech", "pc_action"})
_TEXT_SIGNED_KINDS = PC_KINDS | {"director_instruction"}


def _finite_fraction(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value <= 1)


@dataclass(frozen=True, slots=True)
class Utterance:
    """One typed or transcribed input; voice may carry recogniser uncertainty."""

    text: str
    mode: InputMode = "auto"
    origin: Origin = "keyboard"
    confidence: float | None = None
    alternatives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        if self.mode not in get_args(InputMode):
            raise ValueError(f"unknown input mode: {self.mode!r}")
        if self.origin not in get_args(Origin):
            raise ValueError(f"unknown origin: {self.origin!r}")
        if not isinstance(self.alternatives, tuple) or not all(
                isinstance(alternative, str) for alternative in self.alternatives):
            raise ValueError("alternatives must be a tuple of strings")
        if self.confidence is not None and not _finite_fraction(self.confidence):
            raise ValueError("confidence must be a finite number in [0, 1]")
        if self.origin == "keyboard" and (self.confidence is not None or self.alternatives):
            raise ValueError("keyboard input has no recognition confidence or alternatives")


@dataclass(frozen=True, slots=True)
class RoutingContext:
    """Who is speaking with what authority. Authority has no defaults."""

    active_pc: str | None
    can_direct: bool
    min_confidence: float = 0.75

    def __post_init__(self) -> None:
        if self.active_pc is not None and (
                not isinstance(self.active_pc, str) or not self.active_pc.strip()):
            raise ValueError("active_pc must be a nonempty actor id or None")
        if not isinstance(self.can_direct, bool):
            raise ValueError("can_direct must be a bool")
        if not _finite_fraction(self.min_confidence) or self.min_confidence == 0:
            raise ValueError("min_confidence must be a finite number in (0, 1]")


@dataclass(frozen=True, slots=True)
class Intent:
    """One routing hypothesis with machine reason and visible feedback."""

    kind: IntentKind
    text: str
    reason: str
    feedback: str
    command: Command | None = None
    actor_id: str | None = None
    candidates: tuple[Intent, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in get_args(IntentKind):
            raise ValueError(f"unknown intent kind: {self.kind!r}")
        if self.reason not in REASONS:
            raise ValueError(f"unknown reason: {self.reason!r}")
        if (self.command is not None) != (self.kind == "ui_command"):
            raise ValueError("a command is present exactly on ui_command intents")
        if (self.actor_id is not None) != (self.kind in PC_KINDS):
            raise ValueError("an actor is present exactly on PC intents")
        if not isinstance(self.candidates, tuple):
            raise ValueError("candidates must be a tuple")
        if self.candidates and self.kind != "uncertain":
            raise ValueError("only uncertain intents carry candidates")
        for candidate in self.candidates:
            if not isinstance(candidate, Intent) or candidate.kind in ("uncertain", "reject"):
                raise ValueError("candidates must be actionable readings")
            if candidate.command is not None and candidate.command.gate == "confirm":
                raise ValueError("a destructive command is never offered as a candidate")


def signature(intent: Intent) -> tuple[str, str | None, str | None, str | None]:
    """What two readings must share to count as the same hypothesis."""
    command = intent.command
    return (
        intent.kind,
        command.name if command else None,
        command.argument.lower() if command and command.argument else None,
        word_form(intent.text) if intent.kind in _TEXT_SIGNED_KINDS else None,
    )
