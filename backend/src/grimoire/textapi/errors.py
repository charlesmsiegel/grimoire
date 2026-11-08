"""The text API's one refusal type (044)."""

from __future__ import annotations

#: Reasons that mean "the thing asked for is not here" rather than "the request is malformed" or "not now".
NOT_FOUND = frozenset({"no_chat", "unknown_op", "missing_image", "missing_blob", "no_actor", "unknown_peer"})
#: Reasons that mean the request itself is wrong: the client should not repeat it as it is.
BAD_REQUEST = frozenset({"invalid_field", "invalid_selection", "too_large", "unsupported_media", "empty",
                         "no_content", "unknown_opening"})


class ApiRefused(ValueError):
    """One op could not be done; nothing was written unless the reason's own service says so (a 035
    ``generate`` refused ``turn_changed`` after its running take, for instance). ``reason`` is
    machine-readable and, for a refusal of 035, 052, 038 or 028, exactly that service's reason.
    ``status`` is the HTTP status the app answers with: 404, 400, or 409 for every refusal of state."""

    def __init__(self, reason: str, message: str = "", status: int | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.status = status if status is not None else (
            404 if reason in NOT_FOUND else 400 if reason in BAD_REQUEST else 409)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, str(self), self.status))

    def to_json(self) -> dict:
        return {"error": self.reason, "message": str(self)}
