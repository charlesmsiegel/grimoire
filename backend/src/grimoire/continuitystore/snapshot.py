"""Disposable native dependency snapshot; never actor knowledge or copied canon."""
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from grimoire.continuity import Dependency, Pin
from grimoire.continuitywire.projection import scene_revision
from grimoire.continuitywire.archive import FORK_SCHEMA, decode_fork
from grimoire.core.identity import AcceptedRef, RelationRef, KnowledgeRef, HoldingsRef, OwnerRef, InstanceRef
from grimoire.core.knowledge import Perception, Report, Proposition, Ruling
from grimoire.effects import accepted_from_payload, wrapup_from_payload
from grimoire.continuity.provenance import decode_scene_sources
from grimoire.continuity.declarations import DEPENDENCY_SCHEMA, decode_dependency
from grimoire.evidence import decode_batch, decode_claimed
from grimoire.graph.codec import relation_from_payload
from grimoire.holdings import holdings_from_payload
from grimoire.images import IMAGE_SCHEMA, PLACEMENT_SCHEMA, image_from_payload, placement_from_payload
from grimoire.library import LibraryPathError
from grimoire.store import CampaignRecords, StoreError
from grimoire.scene import turn_from_payload
from grimoire.store.layout import address_parts



@dataclass(frozen=True)
class RecordInfo:
    pin: Pin
    ref: str
    visibility: str = 'director'
    source_versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativeSnapshot:
    campaign_id: str
    pins: Mapping[str, Pin]
    dependencies: tuple[Dependency, ...]
    problems: tuple[str, ...]
    records: Mapping[str, RecordInfo]
    coverage: str = 'recorded_dependencies_only'


def native_snapshot(store, campaign):
    pins, infos, decoded, problems, edges = {}, {}, {}, [], set()

    def path(ref): return '/'.join(address_parts(ref))

    def scan(found, codec, kind):
        problems.extend(f'{p.path}: {p.code}' for p in found.problems)
        for ref, stored in found.records.items():
            name = path(ref)
            try:
                value = codec(ref, stored.record.schema, stored.record.payload)
                revision = scene_revision(stored.record.payload) if kind == 'turn' else stored.etag
                pin = Pin(name, revision)
                versions = ()
                if kind == 'turn':
                    versions = tuple(sorted({line.actor_revision for take in value.takes for line in take.lines
                                             if line.actor_revision is not None}))
                pins[name] = pin
                infos[name] = RecordInfo(pin, str(ref), source_versions=versions)
                decoded[name] = (kind, value)
            except (ValueError, TypeError, AttributeError, KeyError) as error:
                problems.append(f'{name}: undecodable {kind}: {error}')

    scan(store.campaign_turns(campaign), turn_from_payload, 'turn')
    scan(store.campaign_accepted(campaign), accepted_from_payload, 'accepted')
    scan(store.campaign_relations(campaign), relation_from_payload, 'relation')
    scan(store.campaign_wrapups(campaign), wrapup_from_payload, 'wrapup')
    scan(store.campaign_knowledge(campaign), lambda ref, schema, payload:
         decode_batch(ref, schema, payload) if ref.family == 'batch' else decode_claimed(ref, schema, payload)[0],
         'knowledge')
    scan(store.campaign_holdings(campaign), lambda ref, schema, payload:
         holdings_from_payload(ref.holder, schema, payload), 'holdings')

    def asset_scan(refs, listed, schema, codec, kind):
        problems.extend(f'{p.path}: {p.code}' for p in listed)
        records = {}
        for ref in refs:
            try:
                records[ref] = store.read(ref)
            except (StoreError, LibraryPathError, ValueError, OSError) as error:
                problems.append(f'{path(ref)}: unreadable {kind}: {type(error).__name__}')

        def decode_asset(ref, found_schema, payload):
            if found_schema != schema:
                raise ValueError('unsupported asset schema')
            return codec(payload)

        scan(CampaignRecords(records, ()), decode_asset, kind)

    # Only canonical campaign-owned image and placement envelopes are endpoints.
    # Blob bytes and image connectivity never imply causal dependence or knowledge.
    images, listed = store.list_images(f'campaign:{campaign}')
    asset_scan(images, listed, IMAGE_SCHEMA, image_from_payload, 'image')
    # The subject enumerator treats a regular-file placements parent as empty.
    # A read-only listing probe validates that ancestry without inventing a node.
    _, listed = store.list_placements(InstanceRef(campaign, 'character', 'continuity-scan'))
    problems.extend(f'{p.path}: {p.code}' for p in listed)
    subjects, complete = store.campaign_placement_subjects(campaign)
    if not complete:
        problems.append(f'campaigns/{campaign}/placements: incomplete placement subjects')
    for subject in (*subjects, OwnerRef(f'campaign:{campaign}')):
        placements, listed = store.list_placements(subject)
        asset_scan(placements, listed, PLACEMENT_SCHEMA, placement_from_payload, 'placement')

    def link(source, dependent, reason, certainty='known'):
        # Missing references remain explicit graph premises: preview must fail closed.
        if isinstance(source, str):
            if source not in pins:
                problems.append(f'missing dependency source: {source}')
                return
            source = pins[source]
        edges.add(Dependency(source, pins[dependent], reason, certainty))

    def subject_path(subject):
        if subject.kind == 'event':
            accepted = path(AcceptedRef(campaign, subject.ref))
            return accepted if accepted in pins else path(KnowledgeRef(campaign, 'event', subject.ref))
        if subject.kind == 'relation': return path(RelationRef(campaign, subject.ref))
        # Instance/definition subjects are metadata, never arbitrary graph reachability.
        return None

    for name, (kind, value) in decoded.items():
        if kind == 'turn':
            # Recorded follows establishes potential impact; legacy number order is
            # conservative when a turn predates explicit branch ancestry.
            parents = [(p, t) for p, (k, t) in decoded.items() if k == 'turn' and t.chat == value.chat
                       and t.number == value.number - 1
                       and (value.follows is None or t.turn_id == value.follows)]
            if value.follows is not None and not parents:
                problems.append(f'{name}: missing follows turn')
            for parent, _ in parents:
                link(parent, name, 'chronological', 'potential')
        elif kind == 'accepted':
            try:
                sources = decode_scene_sources(value.extras.get('continuity_sources'), value.chat)
                if not sources: problems.append(f'{name}: empty limited scene provenance')
                for source in sources: link(Pin(source.path, source.revision), name, 'scene')
            except (ValueError, TypeError, KeyError) as error:
                problems.append(f'{name}: incomplete scene provenance: {error}')
            for receipt in value.receipts:
                target = path(receipt.relation)
                if target in pins:
                    relation = decoded[target][1]
                    link(name, target, 'possession' if getattr(relation, 'predicate', None) == 'carries'
                         else 'relationship')
                else: problems.append(f'missing receipt relation: {target}')
            if value.holdings is not None:
                for holder in value.holdings.seqs:
                    target = path(HoldingsRef.of(holder))
                    if target in pins: link(name, target, 'possession')
                    else: problems.append(f'missing receipt holdings: {target}')
            if value.after:
                for prior in value.after: link(path(AcceptedRef(campaign, prior)), name, 'chronological', 'potential')
        elif kind == 'wrapup':
            for cite in value.cites: link(path(AcceptedRef(campaign, cite)), name, 'summary')
        elif kind == 'knowledge':
            if isinstance(value, Perception):
                event = path(AcceptedRef(campaign, value.event_id))
                link(event if event in pins else path(KnowledgeRef(campaign, 'event', value.event_id)), name, 'evidence')
            elif isinstance(value, Report):
                link(path(KnowledgeRef(campaign, 'proposition', value.proposition_id)), name, 'evidence')
            elif isinstance(value, (Proposition, Ruling)):
                if isinstance(value, Ruling):
                    link(path(KnowledgeRef(campaign, 'proposition', value.proposition_id)), name, 'evidence')
                for subject in value.about if isinstance(value, Proposition) else value.grounds:
                    source = subject_path(subject)
                    if source is not None: link(source, name, 'evidence')
            else:
                # Objective event bridge shares an accepted id; does not grant access.
                for evidence_id in getattr(value, 'evidence_ids', ()):
                    link(path(KnowledgeRef(campaign, 'evidence', evidence_id)), name, 'evidence')
                proposition_id = getattr(value, 'proposition_id', None)
                if proposition_id:
                    link(path(KnowledgeRef(campaign, 'proposition', proposition_id)), name, 'evidence')
                event_id = getattr(value, 'event_id', None)
                if event_id:
                    accepted = path(AcceptedRef(campaign, event_id))
                    if accepted in pins: link(accepted, name, 'recorded_source')
    declarations = store.campaign_continuity(campaign)
    problems.extend(f'{p.path}: {p.code}' for p in declarations.problems)
    for ref, stored in declarations.records.items():
        try:
            if stored.record.schema == FORK_SCHEMA:
                archive = decode_fork(stored.record.schema, stored.record.payload, campaign)
                if archive['operation_id'] != ref.record_id:
                    raise ValueError('fork archive identity differs from envelope')
                continue
            edge = decode_dependency(stored.record.schema, stored.record.payload)
            if edge.source.campaign_id != campaign: raise ValueError('cross-campaign declaration')
            if edge in edges: problems.append(f'{path(ref)}: duplicate dependency')
            edges.add(edge)
        except (ValueError, TypeError, KeyError) as error:
            problems.append(f'{path(ref)}: {error}')
    return NativeSnapshot(campaign, MappingProxyType(pins), tuple(sorted(edges)), tuple(sorted(set(problems))),
                          MappingProxyType(infos))
