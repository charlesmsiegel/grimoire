"""Typed memory editorial operations and disposable evidence views."""
from dataclasses import dataclass
from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.knowledge import Moment
from grimoire.core.perspective import KnownEvidence


def _ids(ids):
    if type(ids) is not tuple or not 1 <= len(ids) <= 500 or len(set(ids)) != len(ids):
        raise ValueError('invalid_evidence_ids')
    for value in ids:
        validate_segment(value, 'evidence_id')


def _common(operation_id, actor, moment):
    validate_segment(operation_id, 'operation_id')
    if type(actor) is not InstanceRef or type(moment) is not Moment:
        raise ValueError('invalid_record')


@dataclass(frozen=True, slots=True)
class MemorySelection:
    operation_id: str
    actor: InstanceRef
    collection_id: str
    evidence_ids: tuple[str, ...]
    moment: Moment

    def __post_init__(self):
        _common(self.operation_id, self.actor, self.moment)
        validate_segment(self.collection_id, 'collection_id')
        _ids(self.evidence_ids)


@dataclass(frozen=True, slots=True)
class MemoryCorrection:
    operation_id: str
    actor: InstanceRef
    obsolete_ids: tuple[str, ...]
    replacement_ids: tuple[str, ...]
    moment: Moment
    provenance: str

    def __post_init__(self):
        _common(self.operation_id, self.actor, self.moment)
        _ids(self.obsolete_ids)
        _ids(self.replacement_ids)
        if set(self.obsolete_ids) & set(self.replacement_ids) or type(self.provenance) is not str or not self.provenance.strip():
            raise ValueError('invalid_correction')


@dataclass(frozen=True, slots=True)
class MemoryItem:
    evidence: KnownEvidence
    replacement_ids: tuple[str, ...] = ()
    selection_ids: tuple[str, ...] = ()
    correction_id: str | None = None
    correction_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UnavailableMemory:
    evidence_id: str
    selection_ids: tuple[str, ...]
    correction_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemoryView:
    actor: InstanceRef
    collection_id: str
    as_of: int
    current: tuple[MemoryItem, ...]
    history: tuple[MemoryItem, ...]
    unavailable: tuple[str, ...]
    omitted: int
    unavailable_details: tuple[UnavailableMemory, ...] = ()


#: An authored summary line is a bounded editorial value, never a transcript (053).
MAX_SUMMARY_TEXT = 2000


@dataclass(frozen=True, slots=True)
class MemorySummary:
    """One editor's attributable account of a remembered detail, citing the evidence it rests on (053).

    ``text`` is authored by the actor's present controller (a human, or the director through a
    trusted app service); nothing here generates it, so a summary can only say what an editor wrote
    and can always be traced to the ``evidence_ids`` it cites. ``topic`` names the remembered detail
    (``identity-hooded-visitor``) so accounts of one detail meet in one dossier entry, and
    ``supersedes`` names an earlier summary of the same editor, collection and topic that this one
    means to replace. Whether that replacement takes effect is decided on every read
    (``consolidate_dossier``): an account resting on corrected evidence, or on evidence older than
    the account it would replace, never displaces it.
    """

    operation_id: str
    actor: InstanceRef
    collection_id: str
    topic: str
    text: str
    evidence_ids: tuple[str, ...]
    moment: Moment
    supersedes: str | None = None

    def __post_init__(self):
        _common(self.operation_id, self.actor, self.moment)
        validate_segment(self.collection_id, 'collection_id')
        validate_segment(self.topic, 'topic')
        _ids(self.evidence_ids)
        if (type(self.text) is not str or not self.text.strip() or len(self.text) > MAX_SUMMARY_TEXT
                or not all(ch.isprintable() or ch in '\n\t' for ch in self.text)):
            raise ValueError('invalid_summary_text')
        if self.supersedes is not None:
            validate_segment(self.supersedes, 'supersedes')
            if self.supersedes == self.operation_id:
                raise ValueError('invalid_supersedes')


#: Why a summary is, or is not, part of an actor's current memory as of a view.
SUMMARY_STATUSES = ('current', 'contested', 'superseded', 'stale', 'older_basis', 'unsupported')


@dataclass(frozen=True, slots=True)
class SummaryItem:
    """A summary as of one view, with the exact evidence behind it resolved again from the actor's projection.

    - ``current``: the topic's one standing account; ``contested``: one of several standing accounts
      that no editorial step reconciled, all kept (competing interpretations are never collapsed);
    - ``superseded``: an account with as late a basis as its whole chain replaced it, directly or through
      the accounts that named it; ``superseded_by`` names those naming this one that the replacement came by;
    - ``stale``: it cites evidence a correction made obsolete (``obsolete_ids``), so it can never be
      current again, whenever it was written; ``replacement_ids`` are the terminal corrections of
      that evidence the actor now holds;
    - ``older_basis``: it names an account to replace, but rests on older evidence than that account,
      which stays standing: an older recap never overwrites a later, corrected one;
    - ``unsupported``: some cited evidence is not (or no longer) in the actor's projection
      (``unavailable_ids``), so nothing vouches for it.

    ``basis`` is the latest sequence of the evidence it cites (``None`` when none resolves);
    ``scenes`` the distinct scenes that evidence comes from, in order.
    """

    operation_id: str
    topic: str
    text: str
    moment: Moment
    status: str
    evidence: tuple[KnownEvidence, ...]
    basis: int | None
    scenes: tuple[str, ...]
    supersedes: str | None = None
    superseded_by: tuple[str, ...] = ()
    obsolete_ids: tuple[str, ...] = ()
    replacement_ids: tuple[str, ...] = ()
    unavailable_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DossierTopic:
    """Every account of one remembered detail: what stands now, and the history that does not."""

    topic: str
    current: tuple[SummaryItem, ...]
    history: tuple[SummaryItem, ...]


@dataclass(frozen=True, slots=True)
class MemoryDossier:
    """One editor's consolidated memory of one actor in one collection, as of one moment (053).

    A disposable view, rebuilt on every read. ``evidence`` is the collection's ``MemoryView`` (exact
    selected evidence after corrections); ``topics`` the authored summaries, each traced to its
    evidence; ``suppressed`` every evidence id the editor's corrections made obsolete as of
    ``as_of``, in any collection, so a context pack never offers an obsolete item as memory.
    ``editor`` is the controller whose operations it shows (``director`` or a human principal).
    ``omitted`` counts the history the ``limit`` shed; standing accounts are never shed.
    """

    actor: InstanceRef
    collection_id: str
    as_of: int
    editor: str
    evidence: MemoryView
    topics: tuple[DossierTopic, ...]
    suppressed: tuple[str, ...]
    omitted: int = 0

    def standing(self) -> tuple[SummaryItem, ...]:
        """The accounts that stand now (``current`` and ``contested``), topic by topic."""
        return tuple(item for topic in self.topics for item in topic.current)
