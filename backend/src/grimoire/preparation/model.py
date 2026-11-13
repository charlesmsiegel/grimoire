"""Director preparation values, deliberately separate from accepted fiction (064)."""
from dataclasses import dataclass

from grimoire.core.identity import InstanceRef, KnowledgeRef, SourceRef, validate_segment

KINDS = frozenset({'secret', 'goal', 'calendar', 'choice'})


def text(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError('invalid_text')


def frozen_tuple(value: tuple, item_type: type) -> None:
    if type(value) is not tuple or len(value) > 64 or any(type(x) is not item_type for x in value):
        raise ValueError('invalid_collection')


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    kind: str
    source: SourceRef | KnowledgeRef | InstanceRef
    revision: str
    text: str

    def __post_init__(self):
        validate_segment(self.evidence_id, 'evidence_id')
        if self.kind not in KINDS:
            raise ValueError('unknown_kind')
        if type(self.source) not in (SourceRef, KnowledgeRef, InstanceRef):
            raise ValueError('invalid_source')
        if isinstance(self.source, SourceRef) and not self.source.pinned:
            raise ValueError('unpinned_source')
        if self.kind in ('choice', 'goal') and isinstance(self.source, SourceRef):
            raise ValueError('campaign_evidence_required')
        text(self.revision)
        text(self.text)


@dataclass(frozen=True)
class Snapshot:
    campaign_id: str
    revision: str
    evidence: tuple[Evidence, ...]
    cast: tuple[InstanceRef, ...]
    locations: tuple[InstanceRef, ...]

    def __post_init__(self):
        validate_segment(self.campaign_id, 'campaign_id')
        text(self.revision)
        frozen_tuple(self.evidence, Evidence)
        frozen_tuple(self.cast, InstanceRef)
        frozen_tuple(self.locations, InstanceRef)
        if len({e.evidence_id for e in self.evidence}) != len(self.evidence):
            raise ValueError('duplicate_evidence')
        if len(set(self.cast)) != len(self.cast) or len(set(self.locations)) != len(self.locations):
            raise ValueError('duplicate_instance')
        refs = (*self.cast, *self.locations, *(e.source for e in self.evidence))
        if any(getattr(r, 'campaign_id', self.campaign_id) != self.campaign_id for r in refs):
            raise ValueError('wrong_campaign')

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(sorted(KINDS - {e.kind for e in self.evidence}))


@dataclass(frozen=True)
class Note:
    """A proposed possibility with explicit supporting evidence, never a fact."""
    text: str
    evidence: tuple[str, ...]

    def __post_init__(self):
        text(self.text)
        frozen_tuple(self.evidence, str)
        if not self.evidence or len(set(self.evidence)) != len(self.evidence):
            raise ValueError('evidence_required')


@dataclass(frozen=True)
class Draft:
    premise: Note
    cast: tuple[InstanceRef, ...]
    location: InstanceRef
    questions: tuple[Note, ...]
    clues: tuple[Note, ...]
    tensions: tuple[Note, ...]
    stakes: tuple[Note, ...]
    directions: tuple[Note, ...]

    def __post_init__(self):
        if type(self.premise) is not Note or type(self.location) is not InstanceRef:
            raise ValueError('invalid_draft')
        frozen_tuple(self.cast, InstanceRef)
        for notes in (self.questions, self.clues, self.tensions, self.stakes, self.directions):
            frozen_tuple(notes, Note)
        if not self.questions or not self.tensions or not self.stakes:
            raise ValueError('incomplete_situation')
        if len({n.text for n in self.directions}) < 2:
            raise ValueError('two_directions_required')

    def check(self, snapshot: Snapshot) -> None:
        if not set(self.cast) <= set(snapshot.cast) or self.location not in snapshot.locations:
            raise ValueError('unknown_instance')
        known = {e.evidence_id for e in snapshot.evidence}
        for note in (self.premise, *self.questions, *self.clues, *self.tensions, *self.stakes, *self.directions):
            if not set(note.evidence) <= known:
                raise ValueError('unknown_evidence')


@dataclass(frozen=True)
class Proposal:
    request_id: str
    brief: str
    snapshot: Snapshot
    draft: Draft


@dataclass(frozen=True)
class Review:
    proposal: Proposal
    credible_stakes: bool
    open_agency: bool
    grounded: bool


@dataclass(frozen=True)
class PreparedScene:
    """Reviewed notes only: not an accepted event, player contribution or outcome."""
    review: Review
