"""Strict historical fork wire codec. Archived turns are never live transcript nodes.

Only native value codecs are imported here; no store services or sync orchestration.
"""

import json
from collections.abc import Mapping

from grimoire.core.identity import validate_segment
from grimoire.scene import turn_from_payload
from grimoire.security import ResourcePath
from grimoire.store.envelope import parse_scope_record
from grimoire.store.jsonfmt import etag, is_etag, parse_strict, thaw
from grimoire.store.layout import address_parts, classify

FORK_SCHEMA = 'grimoire.continuity-fork/1'
MAX_ARCHIVE_BYTES = 6 * 1024 * 1024
MAX_ARCHIVE_SCENES = 256
MAX_PREMISES = 8192
_KEYS = {'operation_id', 'campaign', 'devices', 'token', 'source_device', 'fingerprint', 'premises', 'scenes', 'reconciled'}


def fingerprint(payload):
    data = {k: v for k, v in payload.items() if k != 'fingerprint'}
    return etag(json.dumps(thaw(data), sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                           allow_nan=False).encode('utf-8'))


def archived_turn(path, text):
    if type(text) is not str:
        raise ValueError('archived envelope must be exact UTF8 text')
    record = parse_scope_record(parse_strict(text.encode('utf-8')))[0]
    if '/'.join(address_parts(record.address)) != path or path.split('/')[2] != 'turns':
        raise ValueError('archived turn path does not match its envelope')
    return record, turn_from_payload(record.address, record.schema, record.payload)


def decode_fork(schema, payload, campaign=None):
    """Validate the entire bounded archive, not merely its schema or checksum."""
    if schema != FORK_SCHEMA or not isinstance(payload, Mapping) or set(payload) != _KEYS:
        raise ValueError('invalid or unsupported continuity fork archive')
    data = thaw(payload)
    if len(json.dumps(data, ensure_ascii=False).encode('utf-8')) > MAX_ARCHIVE_BYTES:
        raise ValueError('fork archive size limit exceeded')
    validate_segment(data['campaign'], 'campaign')
    validate_segment(data['operation_id'], 'operation_id')
    if campaign is not None and data['campaign'] != campaign:
        raise ValueError('cross-campaign fork archive')
    devices = data['devices']
    if (not isinstance(devices, list) or len(devices) != 2 or any(type(d) is not str for d in devices)
            or devices != sorted(set(devices))):
        raise ValueError('fork archive requires two stable device identities')
    for device in devices:
        validate_segment(device, 'device')
        ResourcePath((device,))
    if data['source_device'] not in devices or not is_etag(data['token']) or not is_etag(data['fingerprint']):
        raise ValueError('invalid fork identity')
    if not isinstance(data['premises'], dict) or set(data['premises']) != set(devices):
        raise ValueError('invalid fork premises')
    for listing in (*data['premises'].values(), data['reconciled']):
        if not isinstance(listing, dict) or len(listing) > MAX_PREMISES:
            raise ValueError('fork premise capacity exceeded')
        for path, tag in listing.items():
            if type(path) is not str:
                raise ValueError('invalid fork premise path')
            parts = tuple(path.split('/'))
            if (len(parts) < 3 or parts[:2] != ('campaigns', data['campaign'])
                    or classify(parts) is None or not is_etag(tag)):
                raise ValueError('invalid fork premise path or revision')
    scenes = data['scenes']
    if not isinstance(scenes, list) or not scenes or len(scenes) > MAX_ARCHIVE_SCENES:
        raise ValueError('fork scene capacity exceeded')
    paths = []
    for scene in scenes:
        if not isinstance(scene, dict) or set(scene) != {'path', 'source_etag', 'variants'}:
            raise ValueError('invalid archived scene')
        path = scene['path']
        if type(path) is not str or path.split('/')[:3] != ['campaigns', data['campaign'], 'turns']:
            raise ValueError('cross-campaign or unsupported fork scene')
        paths.append(path)
        if not is_etag(scene['source_etag']) or not isinstance(scene['variants'], dict) or set(scene['variants']) != set(devices):
            raise ValueError('invalid fork scene revisions')
        for d, variant in scene['variants'].items():
            if (not isinstance(variant, dict) or set(variant) != {'before', 'after', 'text'}
                    or not is_etag(variant['before']) or variant['after'] != scene['source_etag']):
                raise ValueError('invalid archived variant')
            archived_turn(path, variant['text'])
            if etag(variant['text'].encode('utf-8')) != variant['before']:
                raise ValueError('archived scene digest differs')
            if data['premises'][d].get(path) != variant['before']:
                raise ValueError('archived variant does not match its premise')
        if data['reconciled'].get(path) != scene['source_etag']:
            raise ValueError('reconciled scene differs from selected source')
        if scene['variants'][data['source_device']]['before'] != scene['source_etag']:
            raise ValueError('selected source revision differs')
    if paths != sorted(set(paths)):
        raise ValueError('fork scenes must be distinct and ordered')
    expected_token = etag(json.dumps([(d, data['premises'][d], []) for d in devices],
                                    sort_keys=True, separators=(',', ':')).encode())
    if data['token'] != expected_token:
        raise ValueError('fork token differs from recorded premises')
    if data['fingerprint'] != fingerprint(data):
        raise ValueError('fork fingerprint differs')
    return data
