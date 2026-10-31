"""Voice contract errors. Messages cite ids, reasons and index paths, never authored text."""

from __future__ import annotations


class _VoiceError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.message))


class InvalidVoice(_VoiceError, ValueError):
    """A voice value, binding or render result is malformed: fail closed."""


class VoiceMismatch(_VoiceError, ValueError):
    """A voice is offered to an actor it does not belong to."""


class MissingVoice(_VoiceError, LookupError):
    """No voice resolves for this actor; never substitute another revision or a namesake."""

    def __init__(self, reason: str, message: str, *, detail: str | None = None) -> None:
        super().__init__(reason, message)
        self.detail = detail

    def __reduce__(self) -> tuple[object, ...]:
        return (_missing, (self.reason, self.message, self.detail))


def _missing(reason: str, message: str, detail: str | None) -> MissingVoice:
    return MissingVoice(reason, message, detail=detail)
