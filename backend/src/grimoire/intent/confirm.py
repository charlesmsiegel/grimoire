"""Explicit confirmation for destructive commands, held by the caller.

The confirmation is bound to the exact command, to an opaque target and to that
target's revision, all supplied by the caller. A "yes" can't change which
command runs, what it runs against, or which version of it.

This is a pure check, not an authorization and not a ledger: resolving does
not consume anything, and nothing here knows who is speaking. The executor
discards the pending value after any terminal result, authorizes the command
as the human who asked (as for a click), and advances the target's revision
when it acts -- so a replayed "yes" is then ``stale``.
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
    revision: str
    issued_at: float
    expires_at: float
    prompt: str

    def __post_init__(self) -> None:
        if not isinstance(self.command, Command) or self.command.gate != "confirm":
            raise ValueError("only destructive commands need confirmation")
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("a confirmation must name its target")
        if not isinstance(self.revision, str) or not self.revision.strip():
            raise ValueError("a confirmation must name its target's revision")
        _require_finite(issued_at=self.issued_at, expires_at=self.expires_at)
        if self.expires_at <= self.issued_at:
            raise ValueError("a confirmation must expire after it is issued")


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    status: ConfirmationStatus
    command: Command | None = None
    target: str | None = None
    revision: str | None = None

    def __post_init__(self) -> None:
        if self.status not in get_args(ConfirmationStatus):
            raise ValueError(f"unknown confirmation status: {self.status!r}")
        confirmed = self.status == "confirmed"
        if any((value is not None) != confirmed for value in (self.command, self.target, self.revision)):
            raise ValueError("only a confirmed result carries a command, target and revision")


def request_confirmation(intent: Intent, *, target: str, revision: str, now: float,
                         ttl: float = 30.0) -> PendingConfirmation:
    """Ask before a destructive command; the caller holds the pending value."""
    if intent.kind != "ui_command" or intent.command.gate != "confirm":
        raise ValueError("only a destructive ui_command needs confirmation")
    _require_finite(now=now, ttl=ttl)
    if ttl <= 0:
        raise ValueError("ttl must be positive")
    label = COMMANDS[intent.command.name].label
    return PendingConfirmation(intent.command, target, revision, now, now + ttl,
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
                         current_target: str, current_revision: str,
                         context: RoutingContext) -> ConfirmationResult:
    """Check an answer to a pending confirmation; only an unambiguous yes confirms."""
    _require_finite(now=now)
    if not pending.issued_at <= now < pending.expires_at:
        return ConfirmationResult("expired")
    if current_target != pending.target or current_revision != pending.revision:
        return ConfirmationResult("stale")
    if utterance.mode not in ("auto", "command"):
        return ConfirmationResult("unclear")
    if grammar_form(utterance.text) in _DECLINE:
        return ConfirmationResult("declined")
    if _consents(utterance.text) and _confident_yes(utterance, context):
        return ConfirmationResult("confirmed", pending.command, pending.target, pending.revision)
    return ConfirmationResult("unclear")
