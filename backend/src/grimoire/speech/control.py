"""Trusted capture metadata gates recognition before the existing intent grammar.

This lower-level boundary has no privacy settings; applications use SpeechSession
which couples it to recording, routing and lifecycle. Nothing here executes an intent.
"""
from dataclasses import dataclass, field
from uuid import uuid4

from grimoire.intent import Intent, RoutingContext, Utterance, route
from grimoire.intent.text import grammar_form, straighten

from .model import boolean, identifier, transcript


@dataclass(frozen=True)
class Recognition:
    utterance: Utterance = field(repr=False)
    attribution: str = "unknown"
    engaged: bool = False
    overlap: bool = False
    final: bool = False
    proposal_token: str | None = None
    capture_token: str | None = None

    def __post_init__(self):
        transcript(self.utterance)
        for token in (self.proposal_token, self.capture_token):
            if token is not None:
                identifier(token)
        if self.attribution not in ("foreground", "background", "unknown"):
            raise ValueError("invalid attribution")
        for value in (self.engaged, self.overlap, self.final):
            boolean(value)


@dataclass(frozen=True)
class ControlResult:
    status: str
    reason: str
    intent: Intent | None = field(default=None, repr=False)


def route_recognition(event: Recognition, context: RoutingContext) -> ControlResult:
    if not isinstance(event, Recognition) or not isinstance(context, RoutingContext):
        raise ValueError("invalid recognition context")
    if event.attribution != "foreground":
        return ControlResult("blocked", "unattributed")
    if not event.engaged:
        return ControlResult("blocked", "not_engaged")
    if event.overlap:
        return ControlResult("blocked", "overlap")
    if not event.final:
        return ControlResult("blocked", "partial")
    return ControlResult("routed", "hypothesis", route(event.utterance, context))


def _yes(text: str) -> bool:
    return grammar_form(text) in {"yes", "confirm", "yes confirm", "yes please"} and not straighten(text).strip().endswith("?")


@dataclass(frozen=True)
class PendingCommand:
    token: str
    intent: Intent = field(repr=False)

    @property
    def command(self):
        return self.intent.command


class SpeechControls:
    """One reviewed safe command, consumed once. No canonical writer or dispatcher."""
    def __init__(self, context: RoutingContext):
        self.configure_context(context)

    @property
    def pending(self) -> PendingCommand | None:
        return self._pending

    def recover(self) -> None:
        self._pending = None

    def configure_context(self, context: RoutingContext) -> None:
        if not isinstance(context, RoutingContext):
            raise ValueError("invalid routing context")
        self._context = context
        self.recover()

    def accept(self, event: Recognition) -> ControlResult:
        self.recover()
        result = route_recognition(event, self._context)
        if result.intent is not None and result.intent.kind == "uncertain":
            candidates = {c.command: c for c in result.intent.candidates
                          if c.command is not None and c.command.gate == "instant"}
            if len(candidates) == 1:
                self._pending = PendingCommand(uuid4().hex, next(iter(candidates.values())))
        return result

    def keyboard(self, utterance: Utterance) -> Intent:
        self.recover()
        if not isinstance(utterance, Utterance) or utterance.origin != "keyboard":
            raise ValueError("expected keyboard input")
        return route(utterance, self._context)

    def confirm(self, answer: Utterance | Recognition, *, token: str | None = None) -> Intent | None:
        pending = self._pending
        self.recover()
        if pending is None:
            return None
        if isinstance(answer, Recognition):
            if answer.proposal_token != pending.token:
                return None
            if route_recognition(answer, self._context).status != "routed":
                return None
            utterance = answer.utterance
            if (utterance.confidence is None or utterance.confidence < self._context.min_confidence
                    or not all(_yes(a) for a in utterance.alternatives)):
                return None
        elif isinstance(answer, Utterance) and answer.origin == "keyboard":
            if token != pending.token:
                return None
            utterance = answer
        else:
            raise ValueError("expected trusted recognition or keyboard answer")
        if utterance.mode in ("auto", "command") and _yes(utterance.text):
            return pending.intent
        return None
