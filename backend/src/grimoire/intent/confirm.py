"""Explicit confirmation for destructive commands, held by the caller.

The confirmation is bound to the exact command and to an opaque target the
caller supplies (for example a scene id plus revision). A "yes" can't change
which command runs or what it runs against.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, get_args

from .commands import COMMANDS, Command
from .model import Intent, RoutingContext, Utterance
from .text import grammar_form, straighten

ConfirmationStatus = Literal["confirmed", "declined", "expired", "stale", "unclear"]

_CONFIRM = frozenset({"yes", "confirm", "yes confirm", "do it", "yes do it", "yes delete it"})
_DECLINE = frozenset({"no", "cancel", "never mind", "stop", "no thanks"})


def _require_finite(**values: float) -> None:
    for label, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{label} must be a finite number")


@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    command: Command
    target: str
    issued_at: float
    expires_at: float
    prompt: str

    def __post_init__(self) -> None:
        if not isinstance(self.command, Command) or self.command.gate != "confirm":
            raise ValueError("only destructive commands need confirmation")
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("a confirmation must name its target")
        _require_finite(issued_at=self.issued_at, expires_at=self.expires_at)
        if self.expires_at <= self.issued_at:
            raise ValueError("a confirmation must expire after it is issued")


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    status: ConfirmationStatus
    command: Command | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        if self.status not in get_args(ConfirmationStatus):
            raise ValueError(f"unknown confirmation status: {self.status!r}")
        confirmed = self.status == "confirmed"
        if (self.command is not None) != confirmed or (self.target is not None) != confirmed:
            raise ValueError("only a confirmed result carries a command and target")


def request_confirmation(intent: Intent, *, target: str, now: float,
                         ttl: float = 30.0) -> PendingConfirmation:
    """Ask before a destructive command; the caller holds the pending value."""
    if intent.kind != "ui_command" or intent.command.gate != "confirm":
        raise ValueError("only a destructive ui_command needs confirmation")
    _require_finite(now=now, ttl=ttl)
    if ttl <= 0:
        raise ValueError("ttl must be positive")
    label = COMMANDS[intent.command.name].label
    return PendingConfirmation(intent.command, target, now, now + ttl,
                               f"{label.capitalize()}? Say 'yes' to confirm or 'no' to cancel.")


def _consents(text: str) -> bool:
    """A plain yes; a questioning "Yes?" isn't consent."""
    return grammar_form(text) in _CONFIRM and not straighten(text).strip().endswith("?")


def _confident_yes(utterance: Utterance, context: RoutingContext) -> bool:
    if utterance.origin == "keyboard":
        return True
    return (utterance.confidence is not None
            and utterance.confidence >= context.min_confidence
            and all(_consents(text) for text in utterance.alternatives))


def resolve_confirmation(pending: PendingConfirmation, utterance: Utterance, *, now: float,
                         current_target: str, context: RoutingContext) -> ConfirmationResult:
    """Consume a pending confirmation; only an unambiguous yes confirms."""
    _require_finite(now=now)
    if not pending.issued_at <= now < pending.expires_at:
        return ConfirmationResult("expired")
    if current_target != pending.target:
        return ConfirmationResult("stale")
    if utterance.mode not in ("auto", "command"):
        return ConfirmationResult("unclear")
    if grammar_form(utterance.text) in _DECLINE:
        return ConfirmationResult("declined")
    if _consents(utterance.text) and _confident_yes(utterance, context):
        return ConfirmationResult("confirmed", pending.command, pending.target)
    return ConfirmationResult("unclear")
