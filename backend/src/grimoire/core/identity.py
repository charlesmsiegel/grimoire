"""Source-qualified reference values and their canonical text forms.

Three reference families share one strict grammar and are unambiguous by
scheme: definitions (``<source>/<kind>/<id>[@<revision>]``), campaign
instances (``campaign:<campaign>/<kind>/<id>``) and legacy V1 records
(``v1:<archive>:<path>``). Parsing rejects rather than normalizes, so a
reference written into a human-editable file round-trips exactly.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

SEGMENT_MAX = 128
TEXT_MAX = 512
LEGACY_PATH_MAX = 4096
# Control, surrogate, format (e.g. zero-width) and line/paragraph separators
# would make a path unwritable, invisible-different or line-breaking.
_UNSAFE_PATH_CATEGORIES = frozenset({"Cc", "Cs", "Cf", "Zl", "Zp"})
RESERVED_SCHEMES = frozenset({"v1", "campaign"})

_SEGMENT = r"[a-z0-9][a-z0-9._-]*"
_SEGMENT_RE = re.compile(_SEGMENT)
_SOURCE_RE = re.compile(rf"{_SEGMENT}(?::{_SEGMENT})*")
_SOURCE_REF_RE = re.compile(
    rf"(?P<source>{_SOURCE_RE.pattern})/(?P<kind>{_SEGMENT})/(?P<entity_id>{_SEGMENT})"
    rf"(?:@(?P<revision>{_SEGMENT}))?"
)
_INSTANCE_REF_RE = re.compile(
    rf"campaign:(?P<campaign_id>{_SEGMENT})/(?P<kind>{_SEGMENT})/(?P<instance_id>{_SEGMENT})"
)
_SCOPE_REF_RE = re.compile(rf"campaign:(?P<campaign_id>{_SEGMENT})")
_CHAT_REF_RE = re.compile(rf"campaign:(?P<campaign_id>{_SEGMENT})#chat/(?P<chat_id>{_SEGMENT})")


class IdentityConflict(ValueError):
    """Registrations that cannot coexist; ``sources`` names who to exclude."""

    def __init__(self, message: str, sources: tuple[str, ...] | list[str] = ()) -> None:
        super().__init__(message)
        self.sources: tuple[str, ...] = tuple(sorted(set(sources)))

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.sources))


def validate_segment(value: object, label: str) -> None:
    """One lowercase ID segment; IDs never differ only by case."""
    if not isinstance(value, str) or not _SEGMENT_RE.fullmatch(value):
        raise ValueError(f"{label} must match {_SEGMENT}: {value!r}")
    if len(value) > SEGMENT_MAX:
        raise ValueError(f"{label} is longer than {SEGMENT_MAX} characters")


def validate_source_id(value: object, label: str = "source") -> None:
    """Opaque ``:``-joined segments; only the reserved schemes mean anything."""
    if not isinstance(value, str) or not _SOURCE_RE.fullmatch(value):
        raise ValueError(f"{label} must be ':'-joined segments: {value!r}")
    segments = value.split(":")
    for segment in segments:
        validate_segment(segment, label)
    if segments[0] in RESERVED_SCHEMES:
        raise ValueError(f"{label} may not start with reserved scheme {segments[0]!r}")


def _require_text(text: object) -> str:
    if not isinstance(text, str):
        raise ValueError(f"a reference must be text, not {type(text).__name__}")
    return text


@dataclass(frozen=True, slots=True)
class SourceRef:
    """A definition reference; names and matching slugs are not identity.

    Without a revision the reference is unpinned and follows the latest
    (head) revision; with one it means exactly that revision.
    """

    source: str
    kind: str
    entity_id: str
    revision: str | None = None

    def __post_init__(self) -> None:
        validate_source_id(self.source)
        validate_segment(self.kind, "kind")
        validate_segment(self.entity_id, "entity_id")
        if self.revision is not None:
            validate_segment(self.revision, "revision")
        if len(str(self)) > TEXT_MAX:
            raise ValueError(f"reference text is longer than {TEXT_MAX} characters")

    def __str__(self) -> str:
        text = f"{self.source}/{self.kind}/{self.entity_id}"
        return text if self.revision is None else f"{text}@{self.revision}"

    @classmethod
    def parse(cls, text: object) -> SourceRef:
        match = _SOURCE_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not a definition reference: {text!r}")
        return cls(**match.groupdict())

    @property
    def pinned(self) -> bool:
        return self.revision is not None

    @property
    def entity_key(self) -> tuple[str, str, str]:
        return (self.source, self.kind, self.entity_id)

    def unpinned(self) -> SourceRef:
        return SourceRef(self.source, self.kind, self.entity_id)

    def at(self, revision: str) -> SourceRef:
        return SourceRef(self.source, self.kind, self.entity_id, revision)


@dataclass(frozen=True, slots=True)
class InstanceRef:
    """One individual in one campaign; never a definition or a display name."""

    campaign_id: str
    kind: str
    instance_id: str

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.kind, "kind")
        validate_segment(self.instance_id, "instance_id")
        if len(str(self)) > TEXT_MAX:
            raise ValueError(f"reference text is longer than {TEXT_MAX} characters")

    def __str__(self) -> str:
        return f"campaign:{self.campaign_id}/{self.kind}/{self.instance_id}"

    @classmethod
    def parse(cls, text: object) -> InstanceRef:
        match = _INSTANCE_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not a campaign instance reference: {text!r}")
        return cls(**match.groupdict())


@dataclass(frozen=True, slots=True)
class ScopeRef:
    """A play scope: the campaign namespace itself, not anything inside it.

    Not returned by ``parse_ref``: a scope is never a definition or an individual.
    """

    campaign_id: str

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")

    def __str__(self) -> str:
        return f"campaign:{self.campaign_id}"

    @classmethod
    def parse(cls, text: object) -> ScopeRef:
        match = _SCOPE_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not a play scope reference: {text!r}")
        return cls(**match.groupdict())


@dataclass(frozen=True, slots=True)
class ChatRef:
    """One conversation thread, homed in exactly one play scope; never an actor or a scene.

    ``campaign:<campaign_id>#chat/<chat_id>``. Not returned by ``parse_ref``.
    """

    campaign_id: str
    chat_id: str

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.chat_id, "chat_id")
        if len(str(self)) > TEXT_MAX:
            raise ValueError(f"reference text is longer than {TEXT_MAX} characters")

    def __str__(self) -> str:
        return f"campaign:{self.campaign_id}#chat/{self.chat_id}"

    @property
    def scope(self) -> ScopeRef:
        return ScopeRef(self.campaign_id)

    @classmethod
    def parse(cls, text: object) -> ChatRef:
        match = _CHAT_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not a chat reference: {text!r}")
        return cls(**match.groupdict())


@dataclass(frozen=True, slots=True)
class LegacyId:
    """A V1 record: explicit archive plus exact source-relative path.

    The path keeps its case and Unicode form; unsafe or ambiguous spellings
    are rejected instead of normalized, so equality is byte-exact.
    """

    archive_id: str
    path: str

    def __post_init__(self) -> None:
        validate_segment(self.archive_id, "archive_id")
        path = self.path
        if not isinstance(path, str) or not path:
            raise ValueError("legacy path must be nonempty text")
        if len(path) > LEGACY_PATH_MAX:
            raise ValueError(f"legacy path is longer than {LEGACY_PATH_MAX} characters")
        if "\\" in path or any(unicodedata.category(char) in _UNSAFE_PATH_CATEGORIES for char in path):
            raise ValueError(f"legacy path has a backslash or unsafe character: {path!r}")
        if any(part in ("", ".", "..") for part in path.split("/")):
            raise ValueError(f"legacy path must be relative with no empty, '.' or '..' parts: {path!r}")

    def __str__(self) -> str:
        return f"v1:{self.archive_id}:{self.path}"

    @classmethod
    def parse(cls, text: object) -> LegacyId:
        text = _require_text(text)
        scheme, _, rest = text.partition(":")
        archive_id, separator, path = rest.partition(":")
        if scheme != "v1" or not separator:
            raise ValueError(f"not a legacy V1 reference: {text!r}")
        return cls(archive_id, path)


def parse_ref(text: object) -> SourceRef | InstanceRef | LegacyId:
    """Parse any reference family, choosing by scheme and never by guess."""
    text = _require_text(text)
    if text.startswith("v1:"):
        return LegacyId.parse(text)
    if text.startswith("campaign:"):
        return InstanceRef.parse(text)
    return SourceRef.parse(text)
