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

from grimoire.graph import ClaimShape

from .codec import Batch, ChainKey, EvidenceCodecError, Record, chain_key, decode_batch, decode_claimed

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
    #: the newest head's (seq, etag) when the log is intact, else None
    head: tuple[int, str] | None
    log_intact: bool = True
    #: every head (a batch no batch extends) as (seq, etag), sorted, when the log is intact (028: two
    #: devices' offline appends leave two until the next append joins them); else empty
    heads: tuple[tuple[int, str], ...] = ()
    #: the kept records by name, for a writer that extends the ledger
    records: Mapping[str, Record] = field(default_factory=lambda: MappingProxyType({}), repr=False)
    #: each kept proposition's 017 claim shape, by proposition id; a quarantined one has none
    claims: Mapping[str, ClaimShape] = field(default_factory=lambda: MappingProxyType({}), repr=False)

    @property
    def clean(self) -> bool:
        return not self.quarantined and not self.problems


def _decoded(ref: KnowledgeRef, scanned: ScannedFile
             ) -> tuple[tuple[Record, ChatRef | None, ClaimShape | None] | None, str | None]:
    if scanned.damage is not None:
        return None, scanned.damage
    try:
        return decode_claimed(ref, scanned.schema, scanned.payload), None
    except EvidenceCodecError as error:
        reason = error.reason
    return None, reason


def _verify_log(files: Mapping[KnowledgeRef, ScannedFile]
                ) -> tuple[list[Batch], list[Batch], bool, tuple[tuple[int, str], ...]]:
    """Verified batches, hint batches, whether the log broke, and its heads when intact.

    028: the log is a DAG of create-only batches. Two devices that appended offline each wrote their
    own batch (a tagged name, so neither overwrote the other), and the next append names every head
    it extends. Each batch must decode, each parent etag must be exactly one present batch's, its
    ``seq`` must be one more than its highest parent's (a root is seq 1, and has none), and no record
    may be listed twice. A linear log is the special case with one parent each, verified as before.

    Walks only the batch files present, so a stray huge sequence number costs one file, never a
    loop over the numbers below it.
    """
    present = {ref: scanned for ref, scanned in files.items() if ref.family == "batch"}
    if not present:
        return [], [], False, ()
    decoded: dict[KnowledgeRef, Batch | None] = {}
    for ref, scanned in present.items():
        batch: Batch | None = None
        if scanned.damage is None and isinstance(scanned.etag, str):  # no byte hash: it cannot chain
            try:
                batch = decode_batch(ref, scanned.schema, scanned.payload)
            except EvidenceCodecError:
                batch = None
        decoded[ref] = batch
    by_etag: dict[str, list[KnowledgeRef]] = {}
    for ref, batch in decoded.items():
        if batch is not None:
            by_etag.setdefault(present[ref].etag, []).append(ref)  # type: ignore[arg-type]
    bad: set[KnowledgeRef] = set()
    parents: dict[KnowledgeRef, tuple[KnowledgeRef, ...]] = {}
    for ref, batch in decoded.items():
        if batch is None:
            bad.add(ref)
            continue
        found = [by_etag.get(parent, ()) for parent in batch.parents]
        if any(len(match) != 1 for match in found):  # a parent that is gone, edited or ambiguous
            bad.add(ref)
            continue
        parents[ref] = tuple(match[0] for match in found)
        if batch.parents and max(parent.seq for parent in parents[ref]) != ref.seq - 1:
            bad.add(ref)  # a gap below it, or a parent that is not its predecessor
    listers: dict[str, list[KnowledgeRef]] = {}
    for ref in sorted(decoded, key=lambda ref: (ref.seq, ref.record_id)):
        batch = decoded[ref]
        for name in batch.entries if batch is not None else ():
            listers.setdefault(name, []).append(ref)
    for refs in listers.values():
        bad.update(refs[1:])  # listed again: the later listing is the break, as in a linear walk
    if not bad:
        named = {parent for found in parents.values() for parent in found}
        heads = tuple(sorted((ref.seq, present[ref].etag) for ref in decoded if ref not in named))  # type: ignore[misc]
        order = sorted(decoded, key=lambda ref: (ref.seq, ref.record_id))
        return [decoded[ref] for ref in order], [], False, heads  # type: ignore[misc]
    # A parent mismatch at k cannot tell whether k or the batch it should name was altered, so trust
    # only batches below k-1 that a good batch below k still names (in a linear log: exactly < k-1),
    # closed under parents. A head is named by none, so a broken log trusts no head.
    k = min(ref.seq for ref in bad)
    vouched = {parent for ref, found in parents.items() if ref not in bad and ref.seq < k for parent in found}
    trusted = {ref for ref in vouched if ref not in bad and ref.seq < k - 1}
    changed = True
    while changed:
        changed = False
        for ref in sorted(trusted, key=lambda ref: (ref.seq, ref.record_id)):
            if any(parent not in trusted for parent in parents.get(ref, ())):
                trusted.discard(ref)
                changed = True
    order = sorted(decoded, key=lambda ref: (ref.seq, ref.record_id))
    verified = [decoded[ref] for ref in order if ref in trusted]
    hints = [decoded[ref] for ref in order if ref not in trusted and decoded[ref] is not None]
    return verified, hints, True, ()  # type: ignore[return-value]


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
    decoded: dict[str, tuple[Record, ChatRef | None, ClaimShape | None]] = {}
    failures: dict[str, str] = {}
    for ref, scanned in records.items():
        value, reason = _decoded(ref, scanned)
        if value is not None:
            decoded[ref.name] = value
        else:
            failures[ref.name] = reason  # type: ignore[assignment]

    def own_key(name: str) -> ChainKey | None:
        return chain_key(decoded[name][0]) if name in decoded else None

    verified, hints, broken, heads = _verify_log(
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
    claims = {shape.proposition_id: shape for name in kept if (shape := decoded[name][2]) is not None}
    high_water = max((record.moment.sequence for record in kept.values()), default=0)
    return Assembly(knowledge, tuple(sorted(Quarantine(name, reason) for name, reason in quarantined.items())),
                    tuple(sorted(set(problems))), MappingProxyType(origins), high_water,
                    heads[-1] if heads else None, log_intact=not broken, heads=heads, records=MappingProxyType(kept), claims=MappingProxyType(claims))


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
