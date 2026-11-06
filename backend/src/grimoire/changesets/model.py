"""World Change Set values (043): one reviewed batch of edits to one world source's definitions.

A Change Set is a *proposal*. It names, for each record it touches, the typed target, the
revision it expects to replace, the evidence offered for it and the exact proposed content.
It never writes anything; ``grimoire.changesetstore`` previews and applies it through the
store's guarded transaction. The values are pure and immutable, so the representation a
human reviews is the one that is applied (see docs/CHANGESETS.md).

Targets are always definitions of the change set's own source: there is no field that can
name another source, a campaign or a raw path, so a change set cannot reach outside the world
it was drafted for (the store re-checks every target's address and authorization at commit).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from grimoire.core.identity import SourceRef, validate_segment, validate_source_id
from grimoire.core.json_value import JsonObject, JsonValueError, freeze_object

#: The most records one change set may change, and the most unchanged records it may pin.
#: A reviewer reads every diff; past this, split the work into several change sets.
MAX_CHANGES = 64
MAX_PRECONDITIONS = 64
#: Text limits: ids, attribution and per-item evidence are short; the rationale is prose.
ID_MAX = 128
TEXT_MAX = 2000
RATIONALE_MAX = 20000
MAX_EVIDENCE = 16

_ID = re.compile(r"[a-z0-9][a-z0-9._-]*")
_ETAG = re.compile(r"sha256:[0-9a-f]{64}")


class ChangeSetError(ValueError):
    """A change set that cannot be represented, reviewed or applied; ``reason`` is stable."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.detail))


def _text(value: object, label: str, limit: int = TEXT_MAX) -> str:
    if type(value) is not str or not value.strip():
        raise ChangeSetError("invalid_field", f"{label} must be nonempty text")
    if len(value) > limit:
        raise ChangeSetError("invalid_field", f"{label} is longer than {limit} characters")
    return value


def _segment(value: object, label: str) -> str:
    try:
        validate_segment(value, label)
    except ValueError as error:
        raise ChangeSetError("invalid_field", str(error)) from None
    return value  # type: ignore[return-value]


def _etag(value: object, label: str) -> str:
    if type(value) is not str or not _ETAG.fullmatch(value):
        raise ChangeSetError("invalid_field", f"{label} must be 'sha256:' and 64 lowercase hex digits")
    return value


def _evidence(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ChangeSetError("invalid_field", f"{label} must be a list of text")
    items = tuple(_text(item, label) for item in value)
    if len(items) > MAX_EVIDENCE:
        raise ChangeSetError("invalid_field", f"{label} has more than {MAX_EVIDENCE} items")
    if len(set(items)) != len(items):
        raise ChangeSetError("invalid_field", f"{label} repeats an item")
    return items


@dataclass(frozen=True, slots=True)
class RecordChange:
    """One definition the change set writes: a new entity, or a new revision of an existing one.

    - ``base is None``: **create** ``kind/entity_id`` at ``revision``. The entity must not exist.
    - otherwise: **revise** it. ``base`` is the revision the change was drafted against (the
      entity's head then) and ``base_etag`` that revision file's etag; both must still hold when
      the change set is applied. ``revision`` is the new revision, and the head moves to it.

    ``payload`` and ``name`` are the proposed content, exactly. A revision carries its base's
    ``owner`` and unknown envelope keys forward unchanged; a created entity's owner is the
    change set's author. ``evidence`` is attribution text ("session 12 notes"), never authority.
    """

    kind: str
    entity_id: str
    revision: str
    payload: JsonObject
    name: str | None = None
    base: str | None = None
    base_etag: str | None = None
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _segment(self.kind, "kind")
        _segment(self.entity_id, "entity_id")
        _segment(self.revision, "revision")
        try:
            object.__setattr__(self, "payload", freeze_object(self.payload))
        except JsonValueError as error:
            raise ChangeSetError("invalid_field", f"payload: {error}") from None
        if self.name is not None:
            _text(self.name, "name")
        if (self.base is None) != (self.base_etag is None):
            raise ChangeSetError("invalid_field", "a revise needs both base and base_etag; a create neither")
        if self.base is not None:
            _segment(self.base, "base")
            _etag(self.base_etag, "base_etag")
            if self.base == self.revision:
                raise ChangeSetError("invalid_field", "a revise writes a new revision, not its base")
        object.__setattr__(self, "evidence", _evidence(self.evidence, "evidence"))

    @property
    def action(self) -> str:
        return "create" if self.base is None else "revise"

    @property
    def key(self) -> str:
        """``kind/entity_id``: the manifest's head key for this entity."""
        return f"{self.kind}/{self.entity_id}"


@dataclass(frozen=True, slots=True)
class Precondition:
    """An unchanged record the change set relies on: its head must still be ``revision``, and that
    revision file must still have ``etag``, when the change set is applied."""

    kind: str
    entity_id: str
    revision: str
    etag: str

    def __post_init__(self) -> None:
        _segment(self.kind, "kind")
        _segment(self.entity_id, "entity_id")
        _segment(self.revision, "revision")
        _etag(self.etag, "etag")

    @property
    def key(self) -> str:
        return f"{self.kind}/{self.entity_id}"


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """A reviewed batch of edits to one world source, applied all together or not at all."""

    changeset_id: str
    source: str
    title: str
    author: str
    changes: tuple[RecordChange, ...]
    rationale: str = ""
    preconditions: tuple[Precondition, ...] = field(default=())

    def __post_init__(self) -> None:
        if type(self.changeset_id) is not str or not _ID.fullmatch(self.changeset_id) \
                or len(self.changeset_id) > ID_MAX:
            raise ChangeSetError("invalid_field", f"changeset_id must match {_ID.pattern} (at most {ID_MAX})")
        try:
            validate_source_id(self.source)
        except ValueError as error:
            raise ChangeSetError("invalid_field", str(error)) from None
        _text(self.title, "title")
        _text(self.author, "author")
        if type(self.rationale) is not str or len(self.rationale) > RATIONALE_MAX:
            raise ChangeSetError("invalid_field", f"rationale must be text of at most {RATIONALE_MAX} characters")
        changes, preconditions = self.changes, self.preconditions
        if isinstance(changes, (str, bytes)) or not isinstance(changes, (tuple, list)) \
                or not all(type(change) is RecordChange for change in changes):
            raise ChangeSetError("invalid_field", "changes must be RecordChange values")
        if isinstance(preconditions, (str, bytes)) or not isinstance(preconditions, (tuple, list)) \
                or not all(type(item) is Precondition for item in preconditions):
            raise ChangeSetError("invalid_field", "preconditions must be Precondition values")
        changes, preconditions = tuple(changes), tuple(preconditions)
        if not changes:
            raise ChangeSetError("empty_changeset", "a change set changes at least one record")
        if len(changes) > MAX_CHANGES:
            raise ChangeSetError("too_many_changes", f"at most {MAX_CHANGES} records per change set")
        if len(preconditions) > MAX_PRECONDITIONS:
            raise ChangeSetError("too_many_preconditions", f"at most {MAX_PRECONDITIONS} per change set")
        keys = [change.key for change in changes]
        if len(set(keys)) != len(keys):
            raise ChangeSetError("duplicate_target", "a change set changes each entity at most once")
        ids = [change.entity_id for change in changes]
        if len(set(ids)) != len(ids):  # the store refuses an entity id reused under another kind
            raise ChangeSetError("duplicate_target", "two changes name one entity id under different kinds")
        pinned = [item.key for item in preconditions]
        if len(set(pinned)) != len(pinned):
            raise ChangeSetError("duplicate_precondition", "each record is pinned at most once")
        if set(pinned) & set(keys):
            raise ChangeSetError("precondition_on_target",
                                 "a changed record carries its own expectation; do not also pin it")
        try:  # every target must make a storable reference of this source
            for change in changes:
                SourceRef(self.source, change.kind, change.entity_id, change.revision)
        except ValueError as error:
            raise ChangeSetError("invalid_field", str(error)) from None
        object.__setattr__(self, "changes", changes)
        object.__setattr__(self, "preconditions", preconditions)

    def target(self, change: RecordChange) -> SourceRef:
        """The pinned revision ``change`` writes."""
        return SourceRef(self.source, change.kind, change.entity_id, change.revision)


def summarize(change: RecordChange) -> str:
    """One plain sentence for a reviewer."""
    named = f" ({change.name})" if change.name else ""
    if change.base is None:
        return f"create {change.key}{named} as {change.revision}"
    return f"revise {change.key}{named} from {change.base} to {change.revision}"
