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
from collections.abc import Iterable
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
_RELATION_REF_RE = re.compile(rf"campaign:(?P<campaign_id>{_SEGMENT})#relation/(?P<relation_id>{_SEGMENT})")
_ACCEPTED_REF_RE = re.compile(rf"campaign:(?P<campaign_id>{_SEGMENT})#accepted/(?P<event_id>{_SEGMENT})")
_WRAPUP_REF_RE = re.compile(rf"campaign:(?P<campaign_id>{_SEGMENT})#wrapup/(?P<wrapup_id>{_SEGMENT})")
_TRANSCRIPT_REF_RE = re.compile(
    rf"campaign:(?P<campaign_id>{_SEGMENT})#transcript/(?P<chat_id>{_SEGMENT})/(?P<seq>[0-9]{{10}})"
)
_TURN_REF_RE = re.compile(rf"campaign:(?P<campaign_id>{_SEGMENT})#turn/(?P<turn_id>{_SEGMENT})")
_HOLDINGS_REF_RE = re.compile(
    rf"campaign:(?P<campaign_id>{_SEGMENT})/(?P<kind>{_SEGMENT})/(?P<instance_id>{_SEGMENT})#holdings"
)
_IMAGE_REF_RE = re.compile(
    rf"(?P<scope>campaign:{_SEGMENT}|{_SOURCE_RE.pattern})#image/(?P<image_id>{_SEGMENT})"
)
#: The longest slot (and so placement file stem) the store can hold: 123 + ".json" = 128.
SLOT_MAX = 123


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


def paired_instances(refs: Iterable[InstanceRef]) -> frozenset[InstanceRef]:
    """The refs whose instance id another kind of the same campaign also uses: none of them is one actor.

    The one definition of an instance-id pair (IDENTITY.md), used by ``load()`` and by every service
    that re-checks an actor. Ids are validated lowercase segments, which the store's look-alike fold
    leaves unchanged, so comparing them exactly is that fold.
    """
    kinds: dict[tuple[str, str], set[InstanceRef]] = {}
    for ref in refs:
        kinds.setdefault((ref.campaign_id, ref.instance_id), set()).add(ref)
    return frozenset(ref for group in kinds.values() if len({ref.kind for ref in group}) > 1 for ref in group)


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


#: Record families of one campaign's events/evidence store (020): 012's error families plus the log.
KNOWLEDGE_FAMILIES = ("event", "proposition", "ruling", "evidence", "belief", "disclosure", "batch")
#: A batch id is its ten-digit sequence number, followed on an identified library (028) by ``-`` and the
#: appending device's eight-hex-digit tag, so two devices appending one campaign offline never write one path.
_BATCH_ID_RE = re.compile(r"[0-9]{10}(?:-[0-9a-f]{8})?")
_BATCH_TAG_RE = re.compile(r"[0-9a-f]{8}")
_KNOWLEDGE_REF_RE = re.compile(
    rf"campaign:(?P<campaign_id>{_SEGMENT})#(?P<family>{'|'.join(KNOWLEDGE_FAMILIES)})/(?P<record_id>{_SEGMENT})"
)


@dataclass(frozen=True, slots=True)
class KnowledgeRef:
    """One stored event/evidence record (020), or one batch of the campaign's knowledge log.

    ``campaign:<campaign_id>#<family>/<record_id>``. ``name`` is 012's ``KnowledgeError.records``
    form (``"<family>:<id>"``), so an error names exactly one stored file. A batch id is its
    sequence number as exactly ten digits, from 1, and on an identified library (028) the device tag
    after a ``-`` (``0000000007-3f2a91c0``). Not returned by ``parse_ref``.
    """

    campaign_id: str
    family: str
    record_id: str

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        if self.family not in KNOWLEDGE_FAMILIES:
            raise ValueError("unknown knowledge family")
        validate_segment(self.record_id, "record_id")
        if self.family == "batch" and (not _BATCH_ID_RE.fullmatch(self.record_id)
                                       or int(self.record_id[:10]) < 1):
            raise ValueError("a batch id is a ten-digit sequence number from 1, with an optional device tag")
        if len(str(self)) > TEXT_MAX:
            raise ValueError(f"reference text is longer than {TEXT_MAX} characters")

    def __str__(self) -> str:
        return f"campaign:{self.campaign_id}#{self.family}/{self.record_id}"

    @property
    def name(self) -> str:
        return f"{self.family}:{self.record_id}"

    @property
    def seq(self) -> int:
        if self.family != "batch":
            raise ValueError("only a batch has a sequence number")
        return int(self.record_id[:10])

    @property
    def tag(self) -> str:
        """A batch's device tag (028), or ``""`` for an untagged batch."""
        if self.family != "batch":
            raise ValueError("only a batch has a device tag")
        return self.record_id[11:]

    @classmethod
    def batch(cls, campaign_id: str, seq: int, tag: str = "") -> KnowledgeRef:
        if type(seq) is not int or not 1 <= seq <= 9_999_999_999:
            raise ValueError("a batch sequence number is an int from 1")
        if type(tag) is not str or (tag and not _BATCH_TAG_RE.fullmatch(tag)):
            raise ValueError("a batch's device tag is eight lowercase hex digits")
        return cls(campaign_id, "batch", f"{seq:010d}-{tag}" if tag else f"{seq:010d}")

    @classmethod
    def from_name(cls, campaign_id: str, name: object) -> KnowledgeRef:
        """The ref a ``"<family>:<id>"`` record name denotes in ``campaign_id``."""
        if not isinstance(name, str) or name.count(":") != 1:
            raise ValueError("not a knowledge record name")
        family, record_id = name.split(":")
        return cls(campaign_id, family, record_id)

    @classmethod
    def parse(cls, text: object) -> KnowledgeRef:
        match = _KNOWLEDGE_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not a knowledge record reference: {text!r}")
        return cls(**match.groupdict())


@dataclass(frozen=True, slots=True)
class RelationRef:
    """One canonical relation record of a campaign's typed graph (017); never an actor or a claim.

    ``campaign:<campaign_id>#relation/<relation_id>``. ``relation_id`` is the segment that a
    knowledge ``Subject("relation", ...)`` names. Not returned by ``parse_ref``.
    """

    campaign_id: str
    relation_id: str

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.relation_id, "relation_id")
        if len(str(self)) > TEXT_MAX:
            raise ValueError(f"reference text is longer than {TEXT_MAX} characters")

    def __str__(self) -> str:
        return f"campaign:{self.campaign_id}#relation/{self.relation_id}"

    @property
    def scope(self) -> ScopeRef:
        return ScopeRef(self.campaign_id)

    @classmethod
    def parse(cls, text: object) -> RelationRef:
        match = _RELATION_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not a relation reference: {text!r}")
        return cls(**match.groupdict())


@dataclass(frozen=True, slots=True)
class AcceptedRef:
    """One accepted fictional event of a campaign (021): an authority record with operations and receipts.

    ``campaign:<campaign_id>#accepted/<event_id>``. Distinct from 020's ``#event/`` knowledge family.
    Not returned by ``parse_ref``.
    """

    campaign_id: str
    event_id: str

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.event_id, "event_id")
        if len(str(self)) > TEXT_MAX:
            raise ValueError(f"reference text is longer than {TEXT_MAX} characters")

    def __str__(self) -> str:
        return f"campaign:{self.campaign_id}#accepted/{self.event_id}"

    @property
    def scope(self) -> ScopeRef:
        return ScopeRef(self.campaign_id)

    @classmethod
    def parse(cls, text: object) -> AcceptedRef:
        match = _ACCEPTED_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not an accepted-event reference: {text!r}")
        return cls(**match.groupdict())


@dataclass(frozen=True, slots=True)
class WrapUpRef:
    """One scene wrap-up (021): a retrospective director summary of one chat scene; never actor memory.

    ``campaign:<campaign_id>#wrapup/<wrapup_id>``. Not returned by ``parse_ref``.
    """

    campaign_id: str
    wrapup_id: str

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.wrapup_id, "wrapup_id")
        if len(str(self)) > TEXT_MAX:
            raise ValueError(f"reference text is longer than {TEXT_MAX} characters")

    def __str__(self) -> str:
        return f"campaign:{self.campaign_id}#wrapup/{self.wrapup_id}"

    @property
    def scope(self) -> ScopeRef:
        return ScopeRef(self.campaign_id)

    @classmethod
    def parse(cls, text: object) -> WrapUpRef:
        match = _WRAPUP_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not a wrap-up reference: {text!r}")
        return cls(**match.groupdict())


@dataclass(frozen=True, slots=True)
class TranscriptRef:
    """One page of one chat's transcript (036): an append-only, create-only page of posts.

    ``campaign:<campaign_id>#transcript/<chat_id>/<seq>``, ``seq`` written as exactly ten digits
    (``0000000001``), so the text form and the file name sort in page order. Pages of a chat are
    numbered from 1. Not returned by ``parse_ref``.
    """

    campaign_id: str
    chat_id: str
    seq: int

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.chat_id, "chat_id")
        if type(self.seq) is not int or not 1 <= self.seq < 10 ** 10:
            raise ValueError(f"transcript page out of range: {self.seq!r}")
        if len(str(self)) > TEXT_MAX:
            raise ValueError(f"reference text is longer than {TEXT_MAX} characters")

    def __str__(self) -> str:
        return f"campaign:{self.campaign_id}#transcript/{self.chat_id}/{self.seq:010d}"

    @property
    def scope(self) -> ScopeRef:
        return ScopeRef(self.campaign_id)

    @property
    def chat(self) -> ChatRef:
        return ChatRef(self.campaign_id, self.chat_id)

    @classmethod
    def parse(cls, text: object) -> TranscriptRef:
        match = _TRANSCRIPT_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not a transcript reference: {text!r}")
        return cls(match["campaign_id"], match["chat_id"], int(match["seq"]))


@dataclass(frozen=True, slots=True)
class TurnRef:
    """One played turn of one chat (035): its post, its generated takes and what the table kept.

    ``campaign:<campaign_id>#turn/<turn_id>``. Transcript, never world state. Not returned by ``parse_ref``.
    """

    campaign_id: str
    turn_id: str

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.turn_id, "turn_id")
        if len(str(self)) > TEXT_MAX:
            raise ValueError(f"reference text is longer than {TEXT_MAX} characters")

    def __str__(self) -> str:
        return f"campaign:{self.campaign_id}#turn/{self.turn_id}"

    @property
    def scope(self) -> ScopeRef:
        return ScopeRef(self.campaign_id)

    @classmethod
    def parse(cls, text: object) -> TurnRef:
        match = _TURN_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not a turn reference: {text!r}")
        return cls(**match.groupdict())


@dataclass(frozen=True, slots=True)
class ImageRef:
    """One picture object, owned by one source or one play scope; never a blob or a file path.

    ``<source>#image/<image_id>`` or ``campaign:<campaign_id>#image/<image_id>``. The source
    grammar forbids ``campaign`` as a first segment, so the two forms never collide. Not
    returned by ``parse_ref``.
    """

    scope: str
    image_id: str

    def __post_init__(self) -> None:
        if isinstance(self.scope, str) and self.scope.startswith("campaign:"):
            ScopeRef.parse(self.scope)
        else:
            validate_source_id(self.scope, "image scope")
        validate_segment(self.image_id, "image_id")
        if len(self.image_id) > SLOT_MAX:  # its file name is image_id + ".json"
            raise ValueError(f"image_id is longer than {SLOT_MAX} characters")
        if len(str(self)) > TEXT_MAX:
            raise ValueError(f"reference text is longer than {TEXT_MAX} characters")

    def __str__(self) -> str:
        return f"{self.scope}#image/{self.image_id}"

    @property
    def campaign_id(self) -> str | None:
        return self.scope[len("campaign:"):] if self.scope.startswith("campaign:") else None

    @property
    def source(self) -> str | None:
        return None if self.scope.startswith("campaign:") else self.scope

    @classmethod
    def parse(cls, text: object) -> ImageRef:
        match = _IMAGE_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not an image reference: {text!r}")
        return cls(**match.groupdict())


@dataclass(frozen=True, slots=True)
class OwnerRef:
    """A source or a play scope **as a whole**, as the subject of its own pictures: a world's or a
    campaign's cover, a chat's backdrop. Written exactly as an image scope is: ``<source>`` or
    ``campaign:<campaign_id>``. It names no entity and no instance, so nothing is inherited through it.
    Not returned by ``parse_ref``.
    """

    scope: str

    def __post_init__(self) -> None:
        if isinstance(self.scope, str) and self.scope.startswith("campaign:"):
            ScopeRef.parse(self.scope)
        else:
            validate_source_id(self.scope, "owner")

    def __str__(self) -> str:
        return self.scope

    @property
    def campaign_id(self) -> str | None:
        return self.scope[len("campaign:"):] if self.scope.startswith("campaign:") else None

    @property
    def source(self) -> str | None:
        return None if self.scope.startswith("campaign:") else self.scope

    @classmethod
    def parse(cls, text: object) -> OwnerRef:
        return cls(_require_text(text))


@dataclass(frozen=True, slots=True)
class PlacementRef:
    """One slot of one subject's pictures: ``<subject>#placement/<slot>``.

    The subject is an *unpinned* definition (the entity across all its revisions), a campaign
    instance, or a source or play scope as a whole (``OwnerRef``). Not returned by ``parse_ref``.
    """

    subject: SourceRef | InstanceRef | OwnerRef
    slot: str

    def __post_init__(self) -> None:
        if isinstance(self.subject, SourceRef):
            if self.subject.pinned:
                raise ValueError(f"a placement subject is an unpinned entity, not {self.subject}")
        elif not isinstance(self.subject, (InstanceRef, OwnerRef)):
            raise ValueError("a placement subject must be a SourceRef, an InstanceRef or an OwnerRef")
        validate_segment(self.slot, "slot")
        if len(self.slot) > SLOT_MAX:
            raise ValueError(f"slot is longer than {SLOT_MAX} characters")
        if len(str(self)) > TEXT_MAX:
            raise ValueError(f"reference text is longer than {TEXT_MAX} characters")

    def __str__(self) -> str:
        return f"{self.subject}#placement/{self.slot}"

    @classmethod
    def parse(cls, text: object) -> PlacementRef:
        text = _require_text(text)
        subject_text, separator, slot = text.rpartition("#placement/")
        if not separator:
            raise ValueError(f"not a placement reference: {text!r}")
        subject: SourceRef | InstanceRef | OwnerRef
        try:
            if "/" not in subject_text:  # no kind and id: the owner itself
                subject = OwnerRef(subject_text)
            elif subject_text.startswith("campaign:"):
                subject = InstanceRef.parse(subject_text)
            else:
                subject = SourceRef.parse(subject_text)
            return cls(subject, slot)
        except ValueError as error:
            raise ValueError(f"not a placement reference: {text!r} ({error})") from None


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


@dataclass(frozen=True, slots=True)
class HoldingsRef:
    """The counted holdings (Sprint 023) of one campaign instance; never the instance itself.

    ``campaign:<campaign_id>/<kind>/<instance_id>#holdings``. Not returned by ``parse_ref``.
    """

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
        return f"campaign:{self.campaign_id}/{self.kind}/{self.instance_id}#holdings"

    @classmethod
    def parse(cls, text: object) -> HoldingsRef:
        match = _HOLDINGS_REF_RE.fullmatch(_require_text(text))
        if match is None:
            raise ValueError(f"not a holdings reference: {text!r}")
        return cls(**match.groupdict())

    @classmethod
    def of(cls, holder: InstanceRef) -> HoldingsRef:
        if not isinstance(holder, InstanceRef):
            raise TypeError(f"holdings belong to an InstanceRef, not {type(holder).__name__}")
        return cls(holder.campaign_id, holder.kind, holder.instance_id)

    @property
    def holder(self) -> InstanceRef:
        return InstanceRef(self.campaign_id, self.kind, self.instance_id)
