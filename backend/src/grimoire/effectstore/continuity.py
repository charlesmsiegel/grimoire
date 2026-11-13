"""Same-chat transcript provenance for the existing accepted-effect transaction owner.

Exact envelope etags are historical provenance. Staleness uses scene projections and the
whole same-chat listing, so acceptance bookkeeping may advance without changing fiction.
"""
from collections.abc import Mapping
from dataclasses import dataclass

from grimoire.continuity import Pin, scene_revision
from grimoire.core.identity import ChatRef
from grimoire.scene import SceneRefused, turn_from_payload
from grimoire.scene.model import parse_turn_id
from grimoire.store import StoreError
from grimoire.store.jsonfmt import is_etag
from grimoire.store.layout import address_parts

from .errors import InvalidEffect

# An explicit refusal boundary: roughly 0.6 MiB of pins, well below the native 8 MiB
# JSON record limit even with portable maximum-length identifiers. Never truncate history.
MAX_SCENE_SOURCES = 1024
SOURCE_VERSION = 1
COVERAGES = frozenset({'same_chat_turns', 'empty_limited'})


@dataclass(frozen=True, order=True)
class SceneSource:
    path: str
    etag: str
    revision: str

    def __post_init__(self):
        Pin(self.path, self.revision)
        if len(self.path.split('/')) != 4 or self.path.split('/')[2] != 'turns' or not is_etag(self.etag):
            raise ValueError('a scene source pins an exact turn envelope and scene revision')


def capture_scene_sources(store, chat: ChatRef) -> tuple[SceneSource, ...]:
    """Decode actual native turns under the caller's snapshot lock; refuse uncertain scans."""
    found = store.campaign_turns(chat.campaign_id)
    if found.problems:
        raise InvalidEffect('turn scan is incomplete; scene provenance cannot be captured', 'continuity_sources')
    sources = []
    for ref, stored in sorted(found.records.items(), key=lambda pair: str(pair[0])):
        try:
            turn = turn_from_payload(ref, stored.record.schema, stored.record.payload)
        except (SceneRefused, ValueError, TypeError):
            raise InvalidEffect('a turn cannot be decoded; scene provenance cannot be captured', 'continuity_sources') from None
        if turn.chat != chat:
            continue
        if len(sources) >= MAX_SCENE_SOURCES:
            raise InvalidEffect('same-chat scene provenance exceeds its bounded capacity', 'continuity_sources')
        sources.append(SceneSource('/'.join(address_parts(ref)), stored.etag, scene_revision(stored.record.payload)))
    return tuple(sorted(sources))


def scene_sources_payload(sources: tuple[SceneSource, ...]) -> dict:
    return {'version': SOURCE_VERSION, 'coverage': 'same_chat_turns' if sources else 'empty_limited',
            'pins': [{'path': s.path, 'etag': s.etag, 'revision': s.revision} for s in sources]}


def decode_scene_sources(value: object, chat: ChatRef) -> tuple[SceneSource, ...]:
    """Strict validation for guard consumers; unknown/newer/malformed provenance refuses."""
    if (not isinstance(value, Mapping) or set(value) != {'version', 'coverage', 'pins'}
            or type(value['version']) is not int or value['version'] != SOURCE_VERSION
            or type(value['coverage']) is not str or value['coverage'] not in COVERAGES or not isinstance(value['pins'], (tuple, list))
            or len(value['pins']) > MAX_SCENE_SOURCES):
        raise ValueError('invalid or unsupported continuity_sources')
    sources = []
    for raw in value['pins']:
        if not isinstance(raw, Mapping) or set(raw) != {'path', 'etag', 'revision'}:
            raise ValueError('invalid continuity source pin')
        source = SceneSource(raw['path'], raw['etag'], raw['revision'])
        if Pin(source.path, source.revision).campaign_id != chat.campaign_id:
            raise ValueError('cross-campaign continuity source')
        # Decode identity from the filename too: a valid turn of a different chat cannot bind.
        parsed = parse_turn_id(source.path.split('/')[-1][:-5])
        if parsed is None or parsed[0] != chat.chat_id:
            raise ValueError('continuity source belongs to a different chat')
        sources.append(source)
    if len({s.path for s in sources}) != len(sources):
        raise ValueError('duplicate continuity sources')
    if (value['coverage'] == 'empty_limited') != (not sources):
        raise ValueError('inconsistent continuity coverage')
    return tuple(sorted(sources))


def stale_scene_sources(store, chat: ChatRef, sources: tuple[SceneSource, ...]) -> list[tuple[str, str]]:
    """Call under EffectChange.commit's lock; also detects newly added/deleted turns."""
    try:
        current = capture_scene_sources(store, chat)
    except (InvalidEffect, StoreError):
        return [(f'campaigns/{chat.campaign_id}/turns', 'scene source scan is no longer trustworthy')]
    before = {s.path: s.revision for s in sources}
    after = {s.path: s.revision for s in current}
    return [(path, 'scene source changed') for path in sorted(before.keys() | after.keys())
            if before.get(path) != after.get(path)]
