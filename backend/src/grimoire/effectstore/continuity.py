"""Same-chat transcript provenance for the existing accepted-effect transaction owner.

Exact envelope etags are historical provenance. Staleness uses scene projections and the
whole same-chat listing, so acceptance bookkeeping may advance without changing fiction.
"""

from grimoire.continuity import scene_revision
from grimoire.continuity.provenance import (MAX_SCENE_SOURCES, SOURCE_VERSION, COVERAGES, SceneSource,
                                            decode_scene_sources, scene_sources_payload)
from grimoire.core.identity import ChatRef
from grimoire.scene import SceneRefused, turn_from_payload
from grimoire.store import StoreError
from grimoire.store.layout import address_parts

from .errors import InvalidEffect


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
