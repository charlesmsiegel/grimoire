"""Snapshot-bound semantic admission; reports contain identities, never authored text."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import SimpleNamespace

from grimoire.continuitywire.projection import scene_revision
from grimoire.continuitywire.archive import FORK_SCHEMA, decode_fork
from grimoire.continuity.declarations import decode_dependency
from grimoire.effects import accepted_from_payload
from grimoire.continuity.provenance import decode_scene_sources
from grimoire.scene import turn_from_payload
from grimoire.store import etag
from grimoire.store.envelope import parse_scope_record
from grimoire.store.jsonfmt import parse_strict
from grimoire.store.layout import address_parts, classify
from .documents import syncable
from .identity import device_of
from .model import Base, Conflict
from . import bookkeeping, units


@dataclass(frozen=True)
class SemanticReview:
    campaign: str
    scenes: tuple[str, ...]
    reasons: tuple[str, ...]
    token: str
    endpoints: tuple[str, ...] = ()


def relevant(path: str, campaign: str) -> bool:
    parts = path.split('/')
    return len(parts) >= 3 and parts[:2] == ['campaigns', campaign] and parts[2] in {'turns', 'accepted', 'continuity', 'relations'}


def premise_path(path: str, campaign: str) -> bool:
    """A conservative complete campaign listing also binds absent declaration endpoints.

    Endpoint records can belong to any native family. Binding the complete canonical set
    avoids losing absent endpoints or declarations that exist only on the other snapshot.
    """
    parts = tuple(path.split('/'))
    return len(parts) >= 3 and parts[:2] == ('campaigns', campaign) and classify(parts) is not None


def expected_listing(plan, device, campaign, published=()):
    scan = plan.scans[device]
    files = {p: etag(b) for p, b in scan.files.items() if premise_path(p, campaign)}
    files.update({c.path: c.etag for c in published if premise_path(c.path, campaign)})
    blocked = sorted(p for p in scan.blocked if (p.startswith(f'campaigns/{campaign}/') or p in ('', 'campaigns', f'campaigns/{campaign}')) and p not in files)
    return files, blocked


def listing(store, campaign):
    # The canonical scanner refuses symlinks and records unlistable directories.
    scan = store.scan()
    return {**{p: etag(b) for p, b in scan.files.items() if premise_path(p, campaign)},
            **{p: None for p in scan.blocked if p.startswith(f'campaigns/{campaign}/') or p in ('', 'campaigns', f'campaigns/{campaign}')}}


def token(plan, campaign):
    value = [(d, *expected_listing(plan, d, campaign)) for d in plan.devices]
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _record(data):
    return parse_scope_record(parse_strict(data))[0]


def reviews(plan):
    """Immutable reviews for fork owners; exact snapshot token, scene paths and reason codes."""
    campaigns = {p.split('/')[1] for p in plan.paths if p.startswith('campaigns/') and len(p.split('/')) > 2}
    found = []
    for campaign in sorted(campaigns):
        scenes, reasons, endpoints = set(), set(), set()
        turns, events = {}, {}
        for device in plan.devices:
            turns[device], events[device] = {}, {}
            for path, data in plan.scans[device].files.items():
                if not relevant(path, campaign):
                    continue
                try:
                    record = _record(data)
                    kind = syncable(path)
                    if kind == 'turn':
                        turn = turn_from_payload(record.address, record.schema, record.payload)
                        turns[device][path] = (turn.chat, scene_revision(record.payload))
                    elif kind == 'accepted':
                        event = accepted_from_payload(record.address, record.schema, record.payload)
                        events[device][path] = event
                except (ValueError, TypeError, KeyError, AttributeError):
                    reasons.add('malformed_semantic_record')
                    if syncable(path) == 'turn':
                        scenes.add(path)
        for device in plan.devices:
            other = plan.other(device)
            for path, event in events[device].items():
                if 'continuity_sources' in event.extras:
                    try:
                        pins = decode_scene_sources(event.extras['continuity_sources'], event.chat)
                    except (ValueError, TypeError, KeyError, AttributeError):
                        reasons.add('malformed_continuity_sources')
                        scenes.update(p for p, (chat, _) in turns[device].items() if chat == event.chat)
                        scenes.update(p for p, (chat, _) in turns[other].items() if chat == event.chat)
                        continue
                    for pin in pins:
                        # A not-yet-synced new scene is safe when the receiver lacks it and a copy brings it.
                        for side in plan.devices:
                            current = turns[side].get(pin.path)
                            if current is not None and current[1] != pin.revision:
                                scenes.add(pin.path)
                                reasons.add('accepted_scene_changed')
                            elif current is None and (not any(pin.path in turns[d] for d in plan.devices)
                                                     or (pin.path in plan.bases and plan.bases[pin.path].kind not in ('first', 'none'))):
                                scenes.add(pin.path)
                                reasons.add('accepted_scene_missing')
                elif plan.scans[device].files.get(path) != plan.scans[other].files.get(path):
                    # Legacy uncertainty only implicates existing edited same-chat scenes.
                    for p in turns[device].keys() | turns[other].keys():
                        ours, theirs = turns[device].get(p), turns[other].get(p)
                        if (ours != theirs and (ours or theirs)[0] == event.chat
                                and ((ours is not None and theirs is not None)
                                     or (p in plan.bases and plan.bases[p].kind == 'agreed'))):
                            scenes.add(p)
                            reasons.add('legacy_scene_history')
            for path, data in plan.scans[device].files.items():
                if not relevant(path, campaign) or syncable(path) != 'continuity':
                    continue
                try:
                    record = _record(data)
                    if record.schema == FORK_SCHEMA:
                        archive = decode_fork(record.schema, record.payload, campaign)
                        if archive['operation_id'] != record.address.record_id:
                            raise ValueError('fork archive identity differs from envelope')
                        continue
                    edge = decode_dependency(record.schema, record.payload)
                    for endpoint in (edge.source, edge.dependent):
                        present = False
                        for side in plan.devices:
                            raw = plan.scans[side].files.get(endpoint.path)
                            if raw is None:
                                if endpoint.path in plan.bases and plan.bases[endpoint.path].kind == 'agreed':
                                    reasons.add('missing_dependency_endpoint')
                                    endpoints.add(endpoint.path)
                                continue
                            present = True
                            actual = (turns[side][endpoint.path][1] if endpoint.path in turns[side] else etag(raw))
                            if actual != endpoint.revision:
                                reasons.add('stale_dependency_endpoint')
                                endpoints.add(endpoint.path)
                                if syncable(endpoint.path) == 'turn':
                                    scenes.add(endpoint.path)
                        if not present:
                            reasons.add('missing_dependency_endpoint')
                            endpoints.add(endpoint.path)
                    source = edge.source.path
                    if source not in turns[device] and source not in turns[other]:
                        continue
                    for side in plan.devices:
                        if source in turns[side] and turns[side][source][1] != edge.source.revision:
                            scenes.add(source)
                            reasons.add('declared_scene_changed')
                except (ValueError, TypeError, KeyError, AttributeError):
                    reasons.add('malformed_dependency')
        # Editorial relations have no scene pins: divergent direct facts and an agreed
        # existing scene edit are potential impact, never a claim of causality.
        a, b = plan.devices
        edited = {p for p in turns[a].keys() | turns[b].keys()
                  if (turns[a].get(p, (None, None))[1] != turns[b].get(p, (None, None))[1])
                  and p in plan.bases and plan.bases[p].kind == 'agreed'}
        owned = set()
        for device in plan.devices:
            other = plan.other(device)
            for p, event in events[device].items():
                if plan.scans[device].files[p] != plan.scans[other].files.get(p):
                    owned.update('/'.join(address_parts(r.relation)) for r in event.receipts)
        direct = {p for p in plan.paths if syncable(p) == 'relation' and p.split('/')[1] == campaign
                  and p not in owned and plan.scans[a].files.get(p) != plan.scans[b].files.get(p)}
        if edited and direct:
            scenes.update(edited)
            reasons.add('potential_direct_relation_impact')
        # Unreadable editorial relations belong to the existing relation/ledger
        # owner unless edited fiction creates an actual cross-domain uncertainty.
        # Turns, accepted provenance and declarations always remain fail-closed.
        if any((relevant(p, campaign) or p in ('', 'campaigns', f'campaigns/{campaign}'))
               and (not p.startswith(f'campaigns/{campaign}/relations') or edited)
               for d in plan.devices for p in plan.scans[d].blocked):
            reasons.add('unreadable_semantic_sources')
        if reasons:
            found.append(SemanticReview(campaign, tuple(sorted(scenes)), tuple(sorted(reasons)), token(plan, campaign),
                                        tuple(sorted(endpoints))))
    return tuple(found)


def guard(plan):
    for review in reviews(plan):
        implicated = {p for p in (*review.scenes, *review.endpoints) if p in plan.sides} | {
            p for p, c in plan.units.items() if c == review.campaign}
        if 'missing_dependency_endpoint' in review.reasons:
            implicated.update(p for p in plan.paths if premise_path(p, review.campaign))
        implicated.update(p for p in plan.paths if syncable(p) in ('relation', 'continuity')
                          and p.split('/')[1] == review.campaign)
        if (not review.scenes and not review.endpoints and review.reasons == ('unreadable_semantic_sources',)
                and any(plan.conflicts[p].code == 'merge_review' for p in implicated if p in plan.conflicts)):
            # This is the ledger owner's structural failure, without evidence of a
            # cross-domain invalidation. Its unit is already held; keep its native
            # code and detailed refusal instead of replacing them with a new label.
            continue
        for path in implicated:
            plan.held.add(path)
            plan.agreed.discard(path)
            if path in review.scenes or path in review.endpoints or not units._same(plan, path):
                native = plan.conflicts.get(path)
                plan.conflicts[path] = Conflict(path, 'semantic_review', ', '.join(review.reasons),
                                                review=native.review if native is not None else (), token=review.token)
        if not any(p in plan.conflicts and plan.conflicts[p].code == 'semantic_review' for p in implicated):
            anchor = next((p for p in plan.paths if relevant(p, review.campaign)), None)
            if anchor is not None:
                native = plan.conflicts.get(anchor)
                plan.conflicts[anchor] = Conflict(anchor, 'semantic_review', ', '.join(review.reasons),
                                                  review=native.review if native is not None else (), token=review.token)
                plan.agreed.discard(anchor)
                plan.held.add(anchor)
                implicated.add(anchor)
        for device in plan.devices:
            plan.copies[device] = [c for c in plan.copies[device] if c.path not in implicated]
            plan.unit_copies.get(device, {}).pop(review.campaign, None)


def campaigns_for(plan, copies, deletes=()):
    return {c.path.split('/')[1] for c in (*copies, *deletes) if c.path.startswith('campaigns/') and
            (syncable(c.path) == 'turn' or getattr(c, 'unit', None) is not None or syncable(c.path) in ('continuity', 'relation', 'image', 'placement'))}


def changed(plan, copies, published, deletes=(), deleted=None):
    for campaign in sorted(campaigns_for(plan, copies, deletes)):
        for device in plan.devices:
            files, blocked = expected_listing(plan, device, campaign, published.get(device, ()))
            for removed in (deleted or {}).get(device, ()):
                files.pop(removed.path, None)
            why = units.stale_listing(listing(plan.stores[device], campaign), files, blocked)
            if why is not None: return f'campaign {campaign} semantic sources changed on {device}: {why}'
    return None


def step_listings(plan, device, copies, published=(), deletes=()):
    return [dict(campaign=c, files=expected_listing(plan, device, c, published)[0],
                 blocked=expected_listing(plan, device, c, published)[1]) for c in sorted(campaigns_for(plan, copies, deletes))]


def validate_transition(store, replacements, removals=()):
    """Serving-side independent check: a peer cannot combine fiction with incompatible pins."""
    scan = store.scan()
    files = dict(scan.files)
    files.update(replacements)
    for path in removals:
        files.pop(path, None)
    # Both sides retain the local original and proposed canonical snapshot; no invented ancestry.
    proposed = SimpleNamespace(files=files, blocked=scan.blocked)
    paths = tuple(sorted(set(scan.files) | set(files)))
    plan = SimpleNamespace(devices=('before', 'after'), scans={'before': scan, 'after': proposed}, paths=paths,
                           bases={p: Base('agreed', etag(b))
                                  for p, b in scan.files.items()},
                           other=lambda d: 'after' if d == 'before' else 'before')
    return reviews(plan)


def unmet(path, data, final):
    """Accepted copies require their exact semantic inputs in the receiving final view."""
    if syncable(path) != 'accepted': return None
    try:
        record = _record(data)
        event = accepted_from_payload(record.address, record.schema, record.payload)
        if 'continuity_sources' not in event.extras: return None
        for pin in decode_scene_sources(event.extras['continuity_sources'], event.chat):
            source = final(pin.path)
            if source is None or scene_revision(_record(source).payload) != pin.revision:
                return f'{pin.path} semantic source is missing or changed'
    except (ValueError, TypeError, KeyError, AttributeError):
        return 'semantic source cannot be decoded'
    return None


def resolution_risk(store, path, peer):
    """Bound serving generic resolution refusal to recorded pins and uncertain edited facts."""
    scan = store.scan()
    campaign = path.split('/')[1]
    target = scan.files.get(path)
    target_chat = None
    try:
        if syncable(path) == 'turn' and target is not None:
            record = _record(target)
            target_chat = turn_from_payload(record.address, record.schema, record.payload).chat
        for p, data in scan.files.items():
            if not relevant(p, campaign): continue
            record = _record(data)
            if syncable(p) == 'accepted':
                event = accepted_from_payload(record.address, record.schema, record.payload)
                if syncable(path) == 'relation' and any('/'.join(address_parts(r.relation)) == path for r in event.receipts):
                    return True
                if 'continuity_sources' in event.extras:
                    if any(pin.path == path for pin in decode_scene_sources(event.extras['continuity_sources'], event.chat)):
                        return True
                elif target_chat == event.chat:
                    return True
            elif syncable(p) == 'continuity':
                if record.schema == FORK_SCHEMA:
                    archive = decode_fork(record.schema, record.payload, campaign)
                    if archive['operation_id'] != record.address.record_id:
                        raise ValueError('fork archive identity differs from envelope')
                    continue
                edge = decode_dependency(record.schema, record.payload)
                if path in (edge.source.path, edge.dependent.path): return True
        if syncable(path) in ('image', 'placement'):
            return False  # Only an explicit declaration binds artwork; relation proximity does not.
        pair = tuple(sorted((peer, device_of(store))))
        archive = bookkeeping.read_document(store, 'archive', peer, pair)
        # Direct editorial facts only implicate a peer's reviewed agreed scene when
        # either the scene or the direct relation has moved since that agreement.
        if syncable(path) == 'relation':
            return any(syncable(p) == 'turn' and archive.entries.get(p, (None,))[0] == 'agreed'
                       and archive.entries[p][1] != etag(data) for p, data in scan.files.items() if relevant(p, campaign))
        return any(syncable(p) == 'relation' and archive.entries.get(p) != ('agreed', etag(data))
                   for p, data in scan.files.items() if relevant(p, campaign))
    except (ValueError, TypeError, KeyError, AttributeError):
        return True
