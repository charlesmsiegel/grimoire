"""Store-backed events and evidence (Sprint 020): append 012 records durably, read actor projections.

``EvidenceLibrary`` is trusted application code: it is never handed to a worker or to narration.
Rulings, events and disclosures must reach it only from director or adjudication paths
(021/035 decide who). ``controls`` and contributions come from the caller, because Wave 1
control is not persisted, so 035 must take them from the authenticated session.

Every read scans the campaign's knowledge files and assembles them (``grimoire.evidence``), so
quarantined damage only ever removes what an actor knows. Every write is a ``KnowledgeChange``:
records plus one batch of the campaign's knowledge log, in one store transaction that can be
previewed and approved, then committed. Previews show raw bytes, so they are director-facing.
No model call is made anywhere.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from grimoire.core.authority import DIRECTOR, AuthorityError, PlayerContribution
from grimoire.core.identity import ChatRef, InstanceRef, KnowledgeRef
from grimoire.core.knowledge import Belief, Knowledge, Subject
from grimoire.core.model import ControlAssignment
from grimoire.core.perspective import (
    ActorKnowledge, PlayerKnowledge, actor_knowledge, admit_belief, knowledge_about, player_knowledge, standing,
)
from grimoire.evidence import (
    BATCH_LIMIT, FAMILIES, SCHEMAS, Assembly, EvidenceCodecError, Quarantine, ScannedFile, assemble, batch_entry,
    build_ledger, decode, encode, encode_batch,
)
from grimoire.store import (
    CommitIncomplete, KnowledgeRecord, KnowledgeScan, LibraryStore, RecordNotFound, StaleWrite, StoreError,
    UnstorableIdentifier, WriteConflict, etag,
)
from grimoire.store.layout import address_parts, logical

#: one append may move the campaign's high-water mark by at most this much
MAX_SEQUENCE_STEP = 1_000_000


class EvidenceWriteError(ValueError):
    """An append that was refused before anything was written. Never carries record text."""

    def __init__(self, reason: str, records: Iterable[str] = ()) -> None:
        self.reason = reason
        self.records: tuple[str, ...] = tuple(records)
        super().__init__(f"{reason}: {', '.join(self.records)}" if self.records else reason)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.records))


@dataclass(frozen=True)
class CampaignKnowledge:
    """The director's view of a campaign's stored knowledge: the ledger and what was left out."""

    knowledge: Knowledge
    quarantined: tuple[Quarantine, ...]
    problems: tuple[str, ...]
    origins: Mapping[str, ChatRef]


def _files(scan: KnowledgeScan) -> dict[KnowledgeRef, ScannedFile]:
    files = {ref: ScannedFile(stored.etag, stored.record.schema, stored.record.payload)
             for ref, stored in scan.records.items()}
    files.update({ref: ScannedFile(None, damage=code) for ref, code in scan.damaged.items()})
    return files


def _problems(assembly: Assembly, scan: KnowledgeScan) -> tuple[str, ...]:
    return tuple(sorted(set(assembly.problems) | {f"{p.path}:{p.code}" for p in scan.problems}))


def _actors(record: object) -> set[InstanceRef]:
    found: set[InstanceRef] = set()
    for label in ("holder", "sender", "attributed_to"):
        value = getattr(record, label, None)
        if value is not None:
            found.add(value)
    found.update(getattr(record, "participants", ()))
    for label in ("about", "grounds"):
        for subject in getattr(record, label, ()):
            if isinstance(subject, Subject) and subject.kind == "instance":
                found.add(InstanceRef.parse(subject.ref))
    return found


def _name(record: object) -> str:
    return f"{FAMILIES[type(record)]}:{getattr(record, _ID[type(record)])}"


_ID = {cls: label for cls, label in (
    (cls, {"event": "event_id", "proposition": "proposition_id", "ruling": "ruling_id", "evidence": "evidence_id",
           "belief": "belief_id", "disclosure": "disclosure_id"}[family]) for cls, family in FAMILIES.items())}


@dataclass
class KnowledgeChange:
    """One append, not yet committed: ``preview`` for a human, then ``commit``.

    A fresh store transaction is built for every preview and commit, so a refusal before the
    commit point (``StaleWrite``, ``WriteDenied``, ``UnstorableIdentifier``, ``NameCollision``,
    an ``OSError`` while staging) leaves the change retryable.
    """

    library: EvidenceLibrary
    campaign_id: str
    puts: tuple[KnowledgeRecord, ...]
    snapshot: tuple[object, ...]
    chat: ChatRef | None
    actors: frozenset[InstanceRef]
    batch: KnowledgeRef
    _committed: bool = field(default=False, repr=False)

    def _transaction(self):  # type: ignore[no-untyped-def]
        transaction = self.library.store.transaction()
        for record in self.puts:
            transaction.put(record, None)
        return transaction

    def preview(self) -> dict[str, bytes]:
        """The exact bytes of every new file, keyed by logical path (raw director data)."""
        return self._transaction().preview()

    def commit(self, approvals: Mapping[str, str] | None = None) -> Mapping[str, str]:
        store = self.library.store
        with store.lock:  # nothing in this process can write between these checks and the commit
            if self._committed:
                raise StoreError("this change was already committed")
            stale = self._stale()
            if stale:
                raise StaleWrite(stale)
            try:
                result = self._transaction().commit(self.library.handle, approvals)
            except (CommitIncomplete, WriteConflict):
                self._committed = True  # past the commit point: never resend these bytes
                raise
            self._committed = True
        return result.etags

    def _stale(self) -> list[tuple[str, str]]:
        """Separate store reads, each with its own listing cache (never one around the commit)."""
        store, campaign = self.library.store, self.campaign_id
        base = f"campaigns/{campaign}"
        if store.campaign_presence(campaign) != "present":
            return [(base, "absent")]
        if self.chat is not None and not self.library._chat_exists(self.chat):
            return [(str(self.chat), "absent")]
        present = set(store.campaign_instances(campaign))
        missing = sorted(str(actor) for actor in self.actors - present)
        if missing:
            return [(text, "absent") for text in missing]
        scan = store.campaign_knowledge(campaign)
        if (scan.entries, scan.problems) != self.snapshot:
            return [(f"{base}/evidence", "changed")]
        return []


class EvidenceLibrary:
    """Durable events and evidence over one ``LibraryStore`` (read-only stores can read)."""

    def __init__(self, store: LibraryStore, handle: object = None, *, owner: str | None = None) -> None:
        self.store = store
        self.handle = handle
        self.owner = owner

    # -- reading ------------------------------------------------------------------------------

    def _assembly(self, campaign_id: str) -> tuple[Assembly, KnowledgeScan, dict[KnowledgeRef, ScannedFile]]:
        scan = self.store.campaign_knowledge(campaign_id)
        files = _files(scan)
        return assemble(campaign_id, files), scan, files

    def read(self, campaign_id: str, *, requested_by: str) -> CampaignKnowledge:
        """The raw ledger: director-only, so call sites stay explicit and auditable."""
        if requested_by != DIRECTOR:
            raise AuthorityError("the stored ledger is the director's; read an actor projection instead")
        assembly, scan, _ = self._assembly(campaign_id)
        return CampaignKnowledge(assembly.knowledge, assembly.quarantined, _problems(assembly, scan),
                                 assembly.origins)

    def actor_knowledge(self, campaign_id: str, actor: InstanceRef, *, as_of: int) -> ActorKnowledge:
        return actor_knowledge(self._assembly(campaign_id)[0].knowledge, actor, as_of=as_of)

    def knowledge_about(self, campaign_id: str, actor: InstanceRef, subject: Subject, *,
                        as_of: int) -> ActorKnowledge:
        return knowledge_about(self._assembly(campaign_id)[0].knowledge, actor, subject, as_of=as_of)

    def player_knowledge(self, campaign_id: str, player_id: str, controls: Iterable[ControlAssignment], *,
                         as_of: int) -> PlayerKnowledge:
        return player_knowledge(self._assembly(campaign_id)[0].knowledge, player_id, controls, as_of=as_of)

    def standing(self, campaign_id: str, proposition_id: str, *, as_of: int) -> str:
        return standing(self._assembly(campaign_id)[0].knowledge, proposition_id, as_of=as_of)

    def _chat_exists(self, chat: ChatRef) -> bool:
        try:
            self.store.read(chat)
        except RecordNotFound:
            return False
        except StoreError:
            return True  # an unreadable chat file still exists; 107 reports it
        return True

    # -- writing ------------------------------------------------------------------------------

    def append(self, campaign_id: str, records: Iterable[object], *,
               controls: Iterable[ControlAssignment] = (),
               contributions: Mapping[str, PlayerContribution] | None = None,
               chat: ChatRef | None = None) -> KnowledgeChange:
        """Validate and stage new records plus one log batch; nothing is written until ``commit``."""
        if not self.store.writable or self.handle is None:
            raise EvidenceWriteError("read_only")
        if isinstance(records, (str, bytes)) or not isinstance(records, Iterable):
            raise EvidenceWriteError("invalid_record")
        new = tuple(records)
        if not new:
            raise EvidenceWriteError("empty")
        if len(new) > BATCH_LIMIT:
            raise EvidenceWriteError("too_many")
        if not all(type(record) in SCHEMAS for record in new):
            raise EvidenceWriteError("invalid_record")
        foreign = [_name(r) for r in new if r.campaign_id != campaign_id]  # type: ignore[attr-defined]
        if foreign:
            raise EvidenceWriteError("campaign_mismatch", foreign)
        names = [_name(record) for record in new]
        # 2-4: the campaign, chat and every actor named must already exist (no name lookup).
        if self.store.campaign_presence(campaign_id) != "present":
            raise EvidenceWriteError("unknown_campaign")
        if chat is not None and (not isinstance(chat, ChatRef) or chat.campaign_id != campaign_id
                                 or not self._chat_exists(chat)):
            raise EvidenceWriteError("unknown_chat")
        present = set(self.store.campaign_instances(campaign_id))
        actors = frozenset().union(*(_actors(record) for record in new))
        unknown = sorted(_name(r) for r in new if _actors(r) - present)
        if unknown:
            raise EvidenceWriteError("unknown_actor", unknown)
        # 5: only an intact ledger is extended.
        assembly, scan, files = self._assembly(campaign_id)
        if not assembly.clean or scan.problems:
            raise EvidenceWriteError("ledger_damaged", sorted({q.name for q in assembly.quarantined}))
        # 6: fresh ids only.
        on_disk = {ref.name for ref in files}
        taken = sorted(set(names) & on_disk)
        if taken:
            raise EvidenceWriteError("duplicate_id", taken)
        # 7: no backdating; a delayed letter's sent_at is the single exception.
        high = assembly.high_water
        late = sorted(_name(r) for r in new if r.moment.sequence < high)  # type: ignore[attr-defined]
        if late:
            raise EvidenceWriteError("backdated", late)
        jump = sorted(_name(r) for r in new if r.moment.sequence > high + MAX_SEQUENCE_STEP)  # type: ignore[attr-defined]
        if jump:
            raise EvidenceWriteError("sequence_jump", jump)
        # 8-9: 012's ledger rules, then sovereignty for every new belief.
        combined = build_ledger(campaign_id, (*assembly.records.values(), *new))
        contributions = dict(contributions or {})
        controls = tuple(controls)
        for record in new:
            if isinstance(record, Belief):
                admit_belief(record, combined, controls, contributions.get(record.belief_id))
        # 10: store only what reads back exactly, at an address every platform can hold.
        stored = []
        for record in new:
            outcome = _encoded(record, chat)
            if isinstance(outcome, str):
                raise EvidenceWriteError(outcome, (_name(record),))
            stored.append(outcome)
        # 11: the records, then one batch accounting for their exact bytes.
        puts = [KnowledgeRecord(ref, schema, payload, owner=self.owner) for ref, schema, payload in stored]
        transaction = self.store.transaction()
        for put in puts:
            transaction.put(put, None)
        preview = transaction.preview()
        tags = {put.address: etag(preview[str(logical(address_parts(put.address)))]) for put in puts}
        entries = {ref.name: batch_entry(record, tags[ref]) for record, (ref, _, _) in zip(new, stored)}
        seq, previous = (assembly.head[0] + 1, assembly.head[1]) if assembly.head else (1, None)
        batch_ref, batch_schema, batch_payload = encode_batch(campaign_id, seq, previous, entries)
        batch = KnowledgeRecord(batch_ref, batch_schema, batch_payload, owner=self.owner)
        transaction.put(batch, None)
        batch_tag = etag(transaction.preview()[str(logical(address_parts(batch_ref)))])
        # 12: the post-state must assemble with nothing quarantined.
        after = dict(files)
        after.update({ref: ScannedFile(tags[ref], schema, payload) for ref, schema, payload in stored})
        after[batch_ref] = ScannedFile(batch_tag, batch_schema, batch_payload)
        check = assemble(campaign_id, after)
        if not check.clean or check.knowledge != combined:
            raise AssertionError("an append would not read back as an intact ledger")
        return KnowledgeChange(self, campaign_id, (*puts, batch), (scan.entries, scan.problems), chat,
                               actors, batch_ref)


def _encoded(record: object, chat: ChatRef | None) -> tuple[KnowledgeRef, str, dict[str, object]] | str:
    """The stored form, or the reason it cannot be stored (raised by the caller, outside any handler)."""
    try:
        ref, schema, payload = encode(record, chat)  # type: ignore[arg-type]
        same = decode(ref, schema, payload) == (record, chat)
        logical(address_parts(ref))
    except EvidenceCodecError as error:
        return error.reason
    except UnstorableIdentifier:
        return "unstorable_id"
    if not same:
        return "invalid_field"
    return ref, schema, payload
