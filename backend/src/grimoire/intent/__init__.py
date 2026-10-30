"""Spoken/typed intent routing: utterances become gated, unexecuted hypotheses.

Pure and deterministic: no model calls, clock, I/O or UI. See
docs/intent-routing.md for the accepted contract.
"""

from .commands import COMMANDS, SAFE_STOP_COMMANDS, Command, CommandSpec, Gate
from .confirm import (
    ConfirmationResult, ConfirmationStatus, PendingConfirmation, request_confirmation,
    resolve_confirmation,
)
from .model import REASONS, InputMode, Intent, IntentKind, Origin, RoutingContext, Utterance
from .router import route

__all__ = [
    "COMMANDS", "SAFE_STOP_COMMANDS", "Command", "CommandSpec", "Gate",
    "ConfirmationResult", "ConfirmationStatus", "PendingConfirmation",
    "request_confirmation", "resolve_confirmation",
    "REASONS", "InputMode", "Intent", "IntentKind", "Origin", "RoutingContext", "Utterance",
    "route",
]
