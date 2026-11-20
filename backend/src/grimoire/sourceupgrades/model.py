"""Read-only previews of a source change, and optional dependency declarations (079).

A preview never writes and never calls a model. Identity is the qualified reference as written:
a reference to ``bestiary/creature/wolf@r1`` is reported against exactly that source, kind, id and
revision. Names, slugs, list order and similarly spelled sources are never consulted, so a
removed source shows its references as missing, never as resolved by a namesake elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass

from grimoire.core.identity import SourceRef

#: The manifest extension key holding a source's optional dependencies (079).
DEPENDENCIES_KEY = 'optional_dependencies'
#: Direct dependencies one source may declare; they are never followed recursively.
MAX_DEPENDENCIES = 32

#: How one stored reference fares after the change.
STATUSES = ('unchanged', 'follows_head', 'edited', 'changed', 'missing', 'still_missing', 'restored')
#: Statuses a human should read before applying the change.
ATTENTION = frozenset({'follows_head', 'edited', 'changed', 'missing', 'restored'})


class SourceUpgradeError(ValueError):
    """``reason`` is one of ``invalid_source``, ``candidate_missing_source``, ``invalid_dependency``,
    ``self_dependency``, ``missing_dependency`` or ``limit_exceeded``."""

    def __init__(self, reason: str, detail: str | None = None):
        self.reason, self.detail = reason, detail
        super().__init__(reason if detail is None else f'{reason}: {detail}')


@dataclass(frozen=True)
class Reference:
    """One reference to the changed source, found in a file the change does not touch.

    ``text`` is the reference exactly as written (a definition ref, an image ref, or the bare
    source id under a ``source`` key, such as a world attachment or a dependency declaration).
    ``before`` and ``after`` name what it reaches: a revision for a definition ref, the record's
    etag for an image or an attached world, the dependency's ``source_version`` for a dependency
    declaration, ``None`` where nothing resolves. ``newer_head`` is the
    head revision after the change when a pinned ref stays on an older one (an upgrade a human may
    take deliberately, for an actor through 042's reviewed rebase); it never moves the pin.
    """

    path: str
    record: str
    text: str
    status: str
    before: str | None
    after: str | None
    newer_head: str | None = None

    @property
    def attention(self) -> bool:
        return self.status in ATTENTION


@dataclass(frozen=True)
class EntityChange:
    """One entity of the changed source whose head revision or display name differs, keyed by its
    qualified id only. ``before``/``after`` are head revisions (``None`` where absent)."""

    ref: SourceRef
    before: str | None
    after: str | None
    name_before: str | None
    name_after: str | None

    @property
    def status(self) -> str:
        if self.before is None:
            return 'added'
        if self.after is None:
            return 'removed'
        return 'moved' if self.before != self.after else 'unchanged'

    @property
    def renamed(self) -> bool:
        return None not in (self.name_before, self.name_after) and self.name_before != self.name_after


@dataclass(frozen=True)
class Problem:
    """A path the preview could not read, so its references are not listed (the preview is
    incomplete), or a load problem of the changed source on either side."""

    path: str
    code: str
    side: str = 'library'


@dataclass(frozen=True)
class SourceChangePreview:
    """What removing or updating one source would do; a disposable read, never an authorization."""

    source: str
    action: str
    before: str | None
    after: str | None
    entities: tuple[EntityChange, ...]
    edited: tuple[SourceRef, ...]
    references: tuple[Reference, ...]
    problems: tuple[Problem, ...]

    @property
    def complete(self) -> bool:
        return not any(problem.side == 'library' for problem in self.problems)

    @property
    def attention(self) -> tuple[Reference, ...]:
        return tuple(reference for reference in self.references if reference.attention)

    def missing(self) -> tuple[Reference, ...]:
        return tuple(reference for reference in self.references if reference.status == 'missing')


@dataclass(frozen=True)
class Dependency:
    """One declared optional dependency; ``version`` pins the dependency's ``source_version``."""

    source: str
    version: str | None = None


@dataclass(frozen=True)
class DependencyStatus:
    """``pinned`` (present at its pin), ``drifted`` (present, the pin differs), ``following`` (no
    pin) or ``missing`` (absent: optional, so the declaring source still loads)."""

    dependency: Dependency
    status: str
    current: str | None
