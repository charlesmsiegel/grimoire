"""Assemble one campaign's stored knowledge into a valid 012 ledger, quarantining what it must (020).

Input is what a scan found: each canonical file's etag, schema and payload, or a damage code.
Output is a ``Knowledge`` plus every record left out and why. Visible damage can only make
actors know less. It never promotes a proposition's standing, never brings back an older
belief or ruling, and never moves a disclosure's moment. To guarantee this, quarantining
anything in a *chain* (one proposition's rulings, one holder's beliefs about one proposition,
one event's disclosures) quarantines the whole chain. Pure and deterministic: no I/O and no
model call, and the same input set always gives the same result.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import ChatRef, KnowledgeRef
from grimoire.core.knowledge import (
    Belief, Disclosure, Event, Knowledge, KnowledgeError, Perception, Proposition, Report, Ruling,
)

from .codec import Batch, ChainKey, EvidenceCodecError, Record, chain_key, decode, decode_batch

#: families whose chains can resurface an older value if only part of them is lost
CHAIN_FAMILIES = ("ruling", "belief", "disclosure")


@dataclass(frozen=True)
class ScannedFile:
    """One canonically named file as a scan found it: readable content, or a damage code."""

    etag: str | None = None
    schema: object = None
    payload: object = None
    damage: str | None = None


@dataclass(frozen=True, order=True)
class Quarantine:
    name: str  # "<family>:<id>"
    reason: str


@dataclass(frozen=True)
class Assembly:
    knowledge: Knowledge
    quarantined: tuple[Quarantine, ...]
    problems: tuple[str, ...]
    origins: Mapping[str, ChatRef]
    high_water: int
    #: the newest batch's (seq, etag) when the log is intact, else None
    head: tuple[int, str] | None
    log_intact: bool = True
    #: the kept records by name, for a writer that extends the ledger
    records: Mapping[str, Record] = field(default_factory=lambda: MappingProxyType({}), repr=False)

    @property
    def clean(self) -> bool:
        return not self.quarantined and not self.problems


def _decoded(ref: KnowledgeRef, scanned: ScannedFile) -> tuple[tuple[Record, ChatRef | None] | None, str | None]:
    if scanned.damage is not None:
        return None, scanned.damage
    try:
        return decode(ref, scanned.schema, scanned.payload), None
    except EvidenceCodecError as error:
        reason = error.reason
    return None, reason


def _verify_log(files: Mapping[KnowledgeRef, ScannedFile]) -> tuple[list[Batch], list[Batch], bool, tuple[int, str] | None]:
    """Verified batches, hint batches, whether the log broke, and the head when intact.

    Walks only the batch files present, so a stray huge sequence number costs one file, never a
    loop over the numbers below it.
    """
    present = {ref.seq: (ref, scanned) for ref, scanned in files.items() if ref.family == "batch"}
    if not present:
        return [], [], False, None
    decoded: dict[int, Batch | None] = {}
    for seq, (ref, scanned) in present.items():
        batch: Batch | None = None
        if scanned.damage is None and isinstance(scanned.etag, str):  # no byte hash: it cannot chain
            try:
                batch = decode_batch(ref, scanned.schema, scanned.payload)
            except EvidenceCodecError:
                batch = None
        decoded[seq] = batch
    order = sorted(present)
    broken_at: int | None = None
    seen: set[str] = set()
    previous_etag: str | None = None
    for expected, seq in enumerate(order, start=1):
        if seq != expected:  # a gap: batch `expected` is missing
            broken_at = expected
            break
        batch = decoded[seq]
        if batch is None or batch.previous != previous_etag or seen & set(batch.entries):
            broken_at = seq
            break
        seen |= set(batch.entries)
        previous_etag = present[seq][1].etag
    if broken_at is None:
        return [decoded[seq] for seq in order], [], False, (order[-1], present[order[-1]][1].etag)  # type: ignore[misc]
    # A previous-mismatch at k cannot tell whether k or k-1 was altered: trust only < k-1.
    verified = [decoded[seq] for seq in order if seq < broken_at - 1]
    hints = [decoded[seq] for seq in order if seq >= broken_at - 1 and decoded[seq] is not None]
    return verified, hints, True, None  # type: ignore[return-value]


def assemble(campaign_id: str, files: Mapping[KnowledgeRef, ScannedFile]) -> Assembly:
    """The valid ledger the files support, with every quarantined record named (see module doc)."""
    quarantined: dict[str, str] = {}
    chains: set[ChainKey] = set()
    families: set[str] = set()
    problems: list[str] = []

    def quarantine(name: str, reason: str, *keys: ChainKey | None) -> None:
        quarantined.setdefault(name, reason)
        chains.update(key for key in keys if key is not None)

    records = {ref: scanned for ref, scanned in files.items()
               if ref.family != "batch" and ref.campaign_id == campaign_id}
    decoded: dict[str, tuple[Record, ChatRef | None]] = {}
    failures: dict[str, str] = {}
    for ref, scanned in records.items():
        value, reason = _decoded(ref, scanned)
        if value is not None:
            decoded[ref.name] = value
        else:
            failures[ref.name] = reason  # type: ignore[assignment]

    def own_key(name: str) -> ChainKey | None:
        return chain_key(decoded[name][0]) if name in decoded else None

    verified, hints, broken, head = _verify_log(
        {ref: scanned for ref, scanned in files.items() if ref.family == "batch" and ref.campaign_id == campaign_id})
    if broken:
        problems.append("log_broken")
        families.update(CHAIN_FAMILIES)

    # 1. Every record a verified batch lists must be on disk, unchanged and readable.
    listed: dict[str, KnowledgeRef] = {}
    for batch in verified:
        for name, entry in batch.entries.items():
            ref = KnowledgeRef.from_name(campaign_id, name)
            listed[name] = ref
            key = entry.key(ref.family)
            scanned = records.get(ref)
            if scanned is None:
                quarantine(name, "missing", key)
            elif scanned.damage is not None:
                quarantine(name, scanned.damage, key, own_key(name))
            elif scanned.etag != entry.etag:  # the edit's own (untrusted) key would strip another actor's chain
                quarantine(name, "edited", key)
            elif name in failures:
                quarantine(name, failures[name], key)
            elif own_key(name) != key:
                problems.append("key_mismatch")
                quarantine(name, "key_mismatch", key, own_key(name))
    # 2. Batches after a break are untrusted hints: they can only widen quarantine.
    for batch in hints:
        for name, entry in batch.entries.items():
            ref = KnowledgeRef.from_name(campaign_id, name)
            # The chain families are already quarantined wholesale on a broken log; the keys stay as defence.
            quarantine(name, "missing" if ref not in records else "log_broken", entry.key(ref.family), own_key(name))
    # 3. A file no verified batch accounts for is not trusted.
    for ref in records:
        if ref.name in listed or ref.name in quarantined:
            continue
        quarantine(ref.name, "unlisted", own_key(ref.name))
        if ref.name not in decoded and ref.family in CHAIN_FAMILIES:
            families.add(ref.family)

    def sweep(candidates: dict[str, Record]) -> None:
        for name in sorted(candidates):
            family = name.split(":", 1)[0]
            if family in families:
                quarantine(name, f"{family}_family_damaged")
            elif chain_key(candidates[name]) in chains:
                quarantine(name, "chain_quarantined")
        for name in [name for name in candidates if name in quarantined]:
            del candidates[name]

    candidates: dict[str, Record] = {name: value[0] for name, value in decoded.items() if name not in quarantined}
    sweep(candidates)
    def drain_dangling() -> None:
        # A missing reference (one deleted event, a lost evidence folder) is common and cascades;
        # remove every record whose reference is gone in linear passes, with 012's own reason, so
        # the 012 loop below only meets genuine rule failures and a read stays near-linear.
        while True:
            dangling = _dangling(candidates)
            if not dangling:
                return
            by_chain: dict[ChainKey, list[str]] = {}
            for name, record in candidates.items():
                key = chain_key(record)
                if key is not None:
                    by_chain.setdefault(key, []).append(name)
            for name in sorted(dangling):
                if name in quarantined:
                    continue
                key = chain_key(candidates[name])
                quarantine(name, dangling[name], key)
                for member in by_chain.get(key, ()) if key is not None else ():
                    quarantine(member, "chain_quarantined")
            for name in [name for name in candidates if name in quarantined]:
                del candidates[name]
            sweep(candidates)

    drain_dangling()
    knowledge = None
    for _ in range(len(candidates) + 2):
        try:
            knowledge = build_ledger(campaign_id, candidates.values())
            break
        except KnowledgeError as error:
            named = [name for name in error.records if name in candidates]
            reason = error.reason
        if not named:
            for name in candidates:
                quarantine(name, "unassemblable")
            candidates.clear()
            continue
        quarantine(named[0], reason, chain_key(candidates[named[0]]))
        del candidates[named[0]]
        sweep(candidates)
        drain_dangling()  # its dependents now dangle: remove them linearly, not one rebuild each
    if knowledge is None:  # pragma: no cover - the loop always ends with an empty, valid ledger
        knowledge = Knowledge(campaign_id)

    kept = {name: candidates[name] for name in sorted(candidates)}
    origins = {name: decoded[name][1] for name in kept if decoded[name][1] is not None}
    high_water = max((record.moment.sequence for record in kept.values()), default=0)
    return Assembly(knowledge, tuple(sorted(Quarantine(name, reason) for name, reason in quarantined.items())),
                    tuple(sorted(set(problems))), MappingProxyType(origins), high_water, head,
                    log_intact=not broken, records=MappingProxyType(kept))


def _dangling(candidates: Mapping[str, Record]) -> dict[str, str]:
    """Records whose reference is not among ``candidates``, with the reason 012's check order gives."""
    def missing_event(subjects) -> bool:  # type: ignore[no-untyped-def]
        return any(s.kind == "event" and f"event:{s.ref}" not in candidates for s in subjects)

    found: dict[str, str] = {}
    for name, record in candidates.items():
        reason = None
        if isinstance(record, Proposition):
            reason = "unknown_event" if missing_event(record.about) else None
        elif isinstance(record, Ruling):
            if f"proposition:{record.proposition_id}" not in candidates:
                reason = "unknown_proposition"
            elif missing_event(record.grounds):
                reason = "unknown_event"
        elif isinstance(record, (Perception, Disclosure)):
            reason = "unknown_event" if f"event:{record.event_id}" not in candidates else None
        elif isinstance(record, Report):
            reason = "unknown_proposition" if f"proposition:{record.proposition_id}" not in candidates else None
        elif isinstance(record, Belief):
            if f"proposition:{record.proposition_id}" not in candidates:
                reason = "unknown_proposition"
            elif any(f"evidence:{e}" not in candidates for e in record.evidence_ids):
                reason = "unknown_evidence"
            elif record.supersedes is not None and f"belief:{record.supersedes}" not in candidates:
                reason = "unknown_belief"
        if reason is not None:
            found[name] = reason
    return found


def build_ledger(campaign_id: str, records) -> Knowledge:  # type: ignore[no-untyped-def]
    """A ``Knowledge`` from records of any families (perceptions and reports share evidence)."""
    groups: dict[type, list[Record]] = {Event: [], Proposition: [], Ruling: [], Perception: [], Belief: [],
                                        Disclosure: []}
    for record in records:
        groups[Perception if isinstance(record, Report) else type(record)].append(record)
    return Knowledge(campaign_id, groups[Event], groups[Proposition], groups[Ruling], groups[Perception],
                     groups[Belief], groups[Disclosure])
