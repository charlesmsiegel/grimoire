"""Immutable, bounded director-side continuity evidence stored beside unchanged prose (047).

Only status and coverage belong in ordinary player projections. ``report`` deliberately returns a
fresh JSON value: callers cannot mutate the record by changing their projection.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from collections.abc import Mapping
from grimoire.core.json_value import JsonObject, freeze_object, thaw

REVIEW_STATES = frozenset({"unlabelled", "reviewed", "needs_review", "blocked", "failed", "unchecked", "stale", "exception"})
MAX_REVIEW_BYTES = 128 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _encoded_size(value) -> int:
    """Byte length of compact ASCII JSON, without giving a pure value a serialization dependency."""
    if isinstance(value, str):
        return 2 + sum(2 if char in '\"\\\b\f\n\r\t' else
                       6 if ord(char) < 32 or 127 <= ord(char) <= 65535 else
                       12 if ord(char) > 65535 else 1 for char in value)
    if isinstance(value, JsonObject):
        return 2 + max(0, len(value) - 1) + sum(_encoded_size(key) + 1 + _encoded_size(item)
                                               for key, item in value.items())
    if isinstance(value, tuple):
        return 2 + max(0, len(value) - 1) + sum(_encoded_size(item) for item in value)
    if value is None:
        return 4
    if type(value) is bool:
        return 4 if value else 5
    return len(str(value))


def valid_digest(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class LineReview:
    source_sha256: str
    complete: bool
    report_data: JsonObject

    def __post_init__(self) -> None:
        if not valid_digest(self.source_sha256) or type(self.complete) is not bool:
            raise ValueError("invalid_review")
        try:
            frozen = freeze_object(self.report_data)
            if _encoded_size(frozen) > MAX_REVIEW_BYTES:
                raise ValueError("invalid_review")
            value = thaw(frozen)
        except (ValueError, RecursionError):
            raise ValueError("invalid_review") from None
        object.__setattr__(self, "report_data", frozen)
        if type(value) is not dict or set(value) != {"findings", "unchecked", "authorship", "mislabels", "reason"}:
            raise ValueError("invalid_review")
        for name in ("findings", "unchecked", "authorship", "mislabels"):
            if type(value[name]) is not list or len(value[name]) > 256:
                raise ValueError("invalid_review")
        if value["reason"] is not None and (type(value["reason"]) is not str or len(value["reason"]) > 100):
            raise ValueError("invalid_review")

    def report(self) -> dict:
        return thaw(self.report_data)

    def matches(self, text: str) -> bool:
        return self.source_sha256 == hashlib.sha256(text.encode()).hexdigest()

    @classmethod
    def make(cls, text: str, *, complete: bool = False, findings=(), unchecked=(), authorship=(), mislabels=(), reason=None):
        report = {"findings": list(findings), "unchecked": list(unchecked), "authorship": list(authorship),
                  "mislabels": list(mislabels), "reason": reason}
        return cls(hashlib.sha256(text.encode()).hexdigest(), complete,
                   freeze_object(report))


def review_to_payload(value: LineReview) -> dict:
    return {"source_sha256": value.source_sha256, "complete": value.complete, "report": value.report()}


def review_from_payload(value: object) -> LineReview:
    if not isinstance(value, Mapping) or set(value) != {"source_sha256", "complete", "report"}:
        raise ValueError("invalid_review")
    return LineReview(value["source_sha256"], value["complete"],
                      freeze_object(value["report"]))
