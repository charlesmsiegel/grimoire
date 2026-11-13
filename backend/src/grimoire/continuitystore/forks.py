"""Owner-only terminal retained fork. Turns retain authorship; effects keep their sole writer."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace

from grimoire.continuity import scene_revision
from grimoire.continuity.archive import FORK_SCHEMA, MAX_ARCHIVE_BYTES, archived_turn, decode_fork, fingerprint
from grimoire.continuity.projection import TURN_FIELDS
from grimoire.continuity.provenance import decode_scene_sources
from grimoire.core.authority import AuthorityError
from grimoire.core.identity import ContinuityRef
from grimoire.effects import accepted_from_payload
from grimoire.ledgers import DurableAuthority
from grimoire.store import LibraryStore, ScopeRecord, etag
from grimoire.store.envelope import parse_scope_record
from grimoire.store.jsonfmt import parse_strict, thaw
from grimoire.store.layout import address_parts
from grimoire.sync import ResolveRefused, semantic, units
from grimoire.sync.identity import device_of
from grimoire.sync.session import _locked, plan_sync

MAX_FORK_BYTES = MAX_ARCHIVE_BYTES


@dataclass(frozen=True)
class ForkResult:
    receipt: ContinuityRef
    status: str


def _device_order(devices):
    return tuple(sorted(devices))


def _commit_device(device, transaction, handle):
    transaction.commit(handle)


def _owners(stores, handles, owners):
    for device, store in stores.items():
        authority, handle = store.authority, handles.get(device)
        if (type(authority) is not DurableAuthority or not authority.usable or not authority.issued(handle)
                or getattr(handle, 'trusted', False) is not True
                or getattr(handle, 'principal', None) not in authority.owners
                or frozenset(owners.get(device, ())) != authority.owners):
            raise AuthorityError('fork resolution requires this library authority and configured owner handle')


def _bookkeeping(record):
    payload = thaw(record.payload)
    return (record.schema, record.owner, thaw(record.extra),
            {k: v for k, v in payload.items() if k not in (*TURN_FIELDS, 'takes')},
            [(take.get('take'), take['calls']) for take in payload.get('takes', ()) if take.get('calls')])


def _restored_plan(plan, scenes, source):
    scans = {}
    for device in plan.devices:
        scan = plan.scans[device]
        files = dict(scan.files)
        for scene in scenes:
            files[scene['path']] = scene['variants'][source]['text'].encode('utf-8')
        scans[device] = SimpleNamespace(files=files, blocked=scan.blocked)
    return SimpleNamespace(devices=plan.devices, scans=scans, paths=plan.paths, bases=plan.bases, other=plan.other)


def _validate_restoration(plan, scenes, source):
    chats = {archived_turn(s['path'], s['variants'][source]['text'])[1].chat for s in scenes}
    campaign = scenes[0]['path'].split('/')[1]
    for device in plan.devices:
        for path, raw in plan.scans[device].files.items():
            parts = path.split('/')
            if len(parts) != 4 or parts[:3] != ['campaigns', campaign, 'accepted']:
                continue
            record = parse_scope_record(parse_strict(raw))[0]
            event = accepted_from_payload(record.address, record.schema, record.payload)
            if event.chat in chats:
                try:
                    pins = decode_scene_sources(event.extras.get('continuity_sources'), event.chat)
                except (ValueError, TypeError, KeyError):
                    raise ResolveRefused('legacy or uncertain same-chat scene ancestry cannot authorize restoration') from None
                if not pins:
                    raise ResolveRefused('uncertain limited same-chat scene ancestry cannot authorize restoration')
    for scene in scenes:
        path = scene['path']
        selected, selected_turn = archived_turn(path, scene['variants'][source]['text'])
        for device in plan.devices:
            current, turn = archived_turn(path, scene['variants'][device]['text'])
            if _bookkeeping(current) != _bookkeeping(selected):
                raise ResolveRefused('restoration would discard incompatible turn bookkeeping')
            if scene['variants'][device]['before'] == scene['source_etag']:
                continue
            # Every divergent later same-chat turn is potential use of the edited fiction,
            # even legacy turns whose explicit follows field is absent.
            for p, raw in plan.scans[device].files.items():
                if not p.startswith(f'campaigns/{selected_turn.chat.campaign_id}/turns/'):
                    continue
                _, later = archived_turn(p, raw.decode('utf-8'))
                if later.chat != turn.chat or later.number <= turn.number:
                    continue
                peer_raw = plan.scans[source].files.get(p)
                later_revision = scene_revision(archived_turn(p, raw.decode('utf-8'))[0].payload)
                peer_revision = (None if peer_raw is None else
                                 scene_revision(archived_turn(p, peer_raw.decode('utf-8'))[0].payload))
                if peer_revision != later_revision:
                    raise ResolveRefused('divergent offline continuation may depend on the edited scene')
    if any(r.campaign == campaign for r in semantic.reviews(_restored_plan(plan, scenes, source))):
        raise ResolveRefused('selected source scenes do not satisfy every causal premise')


def _new_archive(plan, campaign, operation_id, token, source):
    review = next((r for r in semantic.reviews(plan) if r.campaign == campaign), None)
    if review is None or review.token != token:
        raise ResolveRefused('semantic review token is stale or there is no review')
    if set(review.reasons) - {'accepted_scene_changed', 'declared_scene_changed', 'stale_dependency_endpoint'}:
        raise ResolveRefused('uncertain or malformed history cannot establish compatible source scenes')
    if not review.scenes:
        raise ResolveRefused('review has no supported source scenes')
    premises = {}
    for d in plan.devices:
        files, blocked = semantic.expected_listing(plan, d, campaign)
        if blocked:
            raise ResolveRefused('unreadable causal premises')
        premises[d] = files
    if sum(len(plan.scans[d].files.get(p, b'')) for d in plan.devices for p in review.scenes) > MAX_FORK_BYTES:
        raise ResolveRefused('fork archive size limit exceeded')
    scenes = []
    for path in review.scenes:
        variants = {}
        source_bytes = plan.scans[source].files.get(path)
        if source_bytes is None:
            raise ResolveRefused('selected source scene is missing')
        source_etag = etag(source_bytes)
        for device in plan.devices:
            raw = plan.scans[device].files.get(path)
            if raw is None:
                raise ResolveRefused('missing turn variant cannot be restored safely')
            variants[device] = dict(before=etag(raw), after=source_etag, text=raw.decode('utf-8'))
        scenes.append(dict(path=path, source_etag=source_etag, variants=variants))
    _validate_restoration(plan, scenes, source)
    # Bind the exact ordinary ledger owner's planned output for idempotence after
    # sync. This is a read-only merge, never a second effects writer.
    reconciled = {}
    for d in plan.devices:
        reconciled.update(premises[d])
    for scene in scenes:
        reconciled[scene['path']] = scene['source_etag']
    paths = sorted(p for p, c in plan.units.items() if c == campaign)
    if paths:
        merged = units.plan_unit(plan, campaign, paths, None, units.unit_bases(plan, paths))
        if merged.review is not None:
            raise ResolveRefused('ordinary ledger reconciliation still requires independent review')
        reconciled.update({p: etag(raw) for p, raw in (merged.files or {}).items()})
    for d in plan.devices:
        for p, tag in premises[d].items():
            if p not in paths and not any(s['path'] == p for s in scenes):
                peer = premises[plan.other(d)].get(p)
                if peer is not None and peer != tag:
                    raise ResolveRefused('unrelated divergent campaign premise requires review first')
    # Existing sync owns unrelated safe deletions too; its planned tombstones
    # must not make an unchanged completed operation look like a later edit.
    for device in plan.devices:
        for deletion in plan.deletes.get(device, ()):
            reconciled.pop(deletion.path, None)
    data = dict(operation_id=operation_id, campaign=campaign, devices=list(plan.devices), token=token,
                source_device=source, premises=premises, scenes=scenes, reconciled=reconciled)
    data['fingerprint'] = fingerprint(data)
    try:
        return decode_fork(FORK_SCHEMA, data, campaign)
    except (ValueError, TypeError, KeyError) as error:
        raise ResolveRefused(f'fork archive refused: {error}') from None


def _check_state(stores, data, receipt_path, receipt_etag):
    """Resume is exact; a completed operation accepts only the predicted ledger output."""
    current = {d: semantic.listing(s, data['campaign']) for d, s in stores.items()}
    expected = {d: dict(data['premises'][d]) for d in stores}
    for d in stores:
        for scene in data['scenes']:
            path, variant = scene['path'], scene['variants'][d]
            if current[d].get(path) not in {variant['before'], variant['after']}:
                raise ResolveRefused('scene changed since fork operation')
        tag = current[d].pop(receipt_path, None)
        if tag is not None and tag != receipt_etag:
            raise ResolveRefused('fork receipt collision')
    all_restored = all(current[d].get(s['path']) == s['source_etag'] for d in stores for s in data['scenes'])
    for d in stores:
        for scene in data['scenes']:
            # Either original or atomically restored fiction must retain all other premises.
            expected[d][scene['path']] = current[d][scene['path']]
    if all_restored and all(current[d] == data['reconciled'] for d in stores):
        return True
    if all(current[d] == expected[d] for d in stores):
        return False
    raise ResolveRefused('causal premises changed since fork review')


def resolve_fork(left: LibraryStore, right: LibraryStore, campaign: str, operation_id: str, token: str,
                 source_device: str, handles: Mapping[str, object], owners: Mapping[str, Iterable[str]]) -> ForkResult:
    """Retain both exact variants, restore pinned fiction, then require ordinary sync.

    Both stores must be locally open with their real durable authorities. No accepted
    event, relation receipt or holding is written by this operation.
    """
    ref = ContinuityRef(campaign, operation_id)
    receipt_path = '/'.join(address_parts(ref))
    with _locked(left, right):
        stores = {device_of(s): s for s in (left, right)}
        if len(stores) != 2 or source_device not in stores:
            raise ResolveRefused('fork requires two distinct devices and a selected source')
        _owners(stores, handles, owners)
        existing = [(d, s.read_exact(receipt_path)) for d, s in stores.items()]
        existing = [(d, raw) for d, raw in existing if raw is not None]
        if existing:
            raw = existing[0][1]
            if any(b != raw for _, b in existing):
                raise ResolveRefused('fork receipt collision')
            try:
                record = parse_scope_record(parse_strict(raw))[0]
                data = decode_fork(record.schema, record.payload, campaign)
            except (ValueError, TypeError, KeyError) as error:
                raise ResolveRefused(f'invalid existing fork receipt: {error}') from None
            if (data['operation_id'] != operation_id or data['devices'] != sorted(stores)
                    or data['token'] != token or data['source_device'] != source_device):
                raise ResolveRefused('operation id reused with changed fork identity')
            already = _check_state(stores, data, receipt_path, etag(raw))
            # Revalidate selected fiction against ALL current recorded dependencies.
            plan = plan_sync(left, right, handles)
            if semantic.reviews(plan):
                # A partly restored pair still has the original incompatible scene.
                _validate_restoration(plan, data['scenes'], source_device)
        else:
            plan = plan_sync(left, right, handles)
            data = _new_archive(plan, campaign, operation_id, token, source_device)
            record = ScopeRecord(ref, FORK_SCHEMA, data, owner=handles[source_device].principal)
            tx = left.transaction()
            tx.put(record, None)
            raw = next(iter(tx.preview().values()))
            already = False
        if len(raw) > MAX_FORK_BYTES:
            raise ResolveRefused('fork archive size limit exceeded')
        transactions = {}
        for device, store in stores.items():
            tx = store.transaction()
            present = store.read_exact(receipt_path)
            if present is None:
                tx.put_exact(receipt_path, raw, None)
            elif present != raw:
                raise ResolveRefused('fork receipt collision')
            for scene in data['scenes']:
                current = store.read_exact(scene['path'])
                if current is None:
                    raise ResolveRefused('scene changed since fork review')
                if etag(current) == scene['source_etag']:
                    continue
                if etag(current) != scene['variants'][device]['before']:
                    raise ResolveRefused('scene changed since fork review')
                tx.put_exact(scene['path'], scene['variants'][source_device]['text'].encode('utf-8'), etag(current))
            if tx.staged():
                tx.check(handles[device])
                transactions[device] = tx
        # Checks can audit and adapters can observe interruption; reread campaign premises
        # immediately before each per-device publication, under both locks.
        for device in _device_order(stores):
            if device not in transactions:
                continue
            _check_state(stores, data, receipt_path, etag(raw))
            _commit_device(device, transactions[device], handles[device])
            _check_state(stores, data, receipt_path, etag(raw))
        return ForkResult(ref, 'already_applied' if already else 'pending_sync')
