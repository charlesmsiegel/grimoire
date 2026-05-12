"""Consistent error shapes for JSON Schema validation.

The UI renders validation problems for mechanics sheets, plugin configs,
and manifests; they all come back in the same shape so a single error
list component can render them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ValidationError:
    """A single validation problem.

    `path` is the JSON Pointer-style location of the offending value as a
    list of keys/indexes (e.g., `["attributes", "strength"]`). `schema_path`
    points to the failing rule inside the schema. `message` is the
    human-readable description shown in the UI.
    """

    message: str
    path: tuple[str | int, ...] = ()
    schema_path: tuple[str | int, ...] = ()
    validator: str = ""

    @property
    def pointer(self) -> str:
        """Return the path as a JSON Pointer (RFC 6901), e.g. `/attributes/strength`."""
        if not self.path:
            return ""
        parts = []
        for p in self.path:
            s = str(p).replace("~", "~0").replace("/", "~1")
            parts.append(s)
        return "/" + "/".join(parts)

    def to_dict(self) -> dict:
        return {
            "message": self.message,
            "path": list(self.path),
            "pointer": self.pointer,
            "schema_path": list(self.schema_path),
            "validator": self.validator,
        }


@dataclass(frozen=True)
class ValidationResult:
    """Result of a validation call.

    Always returned (never raised) so callers can present multiple errors
    to the user at once. `ok` is True iff `errors` is empty.
    """

    ok: bool
    errors: tuple[ValidationError, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return self.ok

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": [e.to_dict() for e in self.errors],
        }

    @classmethod
    def success(cls) -> ValidationResult:
        return cls(ok=True, errors=())

    @classmethod
    def failure(cls, errors: list[ValidationError]) -> ValidationResult:
        return cls(ok=False, errors=tuple(errors))
