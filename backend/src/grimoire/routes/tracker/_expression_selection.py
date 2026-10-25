"""Two conditioned batches choose presentation from historical assets.

A pair is indivisible: missing evidence clears it, never inheriting a mood
from a different outfit. Publication checks the same historical binding and
collection revision under the writers' asset locks, outside all provider calls.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager

from starlette.concurrency import run_in_threadpool

from ... import decisions, store
from ... import inference as operations
from ...store.expression_library import contracts, paths, read
from ...store.record_art import targets as art_targets
from ...store.scene_history import records as history
from ..common import require_inference
from . import _fences

KEYS = ('expression-outfit', 'expression-mood')
UNAVAILABLE = (ValueError, OSError, LookupError, art_targets.ArtTargetNotFoundError)


def clear_pair(snapshot: dict, ref: str) -> None:
    for key in KEYS:
        snapshot.get(ref, {}).get('fields', {}).pop(key, None)


def public_values(actor: dict, fields=None) -> dict:
    public = {f["key"] for f in (fields or store.tracker.fields.DEFAULT_FIELDS)
              if f.get("aware") == "present" and not f.get("off")}
    return {key: row.get('value') for key, row in actor.get('fields', {}).items()
            if key in public and key not in KEYS and row.get('aware') == 'present'}


def binding(cid: str, sid: str, identity: str, index: int) -> tuple[dict, dict]:
    messages = store.scenes.read_scene(cid, sid)['messages']
    message = messages[index]
    checkpoint = history.checkpoint(cid, identity, message.get('scene_state_token'))
    versions = {a['ref']: a['version_id'] for a in (checkpoint or {}).get('actors', [])}
    return message, versions


def target(cid: str, ref: str, version: str) -> contracts.ExpressionTarget:
    kind, actor = ref.split(':', 1)
    return contracts.ExpressionTarget('campaign', cid, kind, actor, version)


def _inputs(cid: str, sid: str, identity: str, key: str, prep: dict, snapshot: dict):
    with store.locks.campaign_lock(cid):
        metadata: dict = {'scene_identity': identity, 'key': key, 'post_id': None,
                          'versions': {}, 'collections': {}, 'errors': {}}
        for ref in prep['present']:
            clear_pair(snapshot, ref)
        actual = _fences._current_sid(cid, sid, identity)
        if actual is None:
            return {}, metadata
        sid = actual
        prep['sid'] = actual
        prep['_expression_identity'] = identity
        try:
            message, versions = binding(cid, sid, identity, prep['index'])
        except IndexError:
            return {}, metadata
        metadata.update(post_id=message.get('post_id'), versions=versions)
        enabled = {f['key'] for f in store.tracker.fields.active(store.tracker.fields.effective(cid, sid))}
        collections = {}
        for ref in sorted(prep['present']):
            clear_pair(snapshot, ref)
            if (not store.tracker.settings.enabled(cid) or not set(KEYS) <= enabled
                    or ref not in versions or ref not in snapshot):
                continue
            try:
                collection = read.effective(target(cid, ref, versions[ref]))
                outfits = {oid: {**row, 'expressions': {m: image for m, image in row['expressions'].items()
                                                       if image.get('available') and image.get('image_id')}}
                           for oid, row in collection['outfits'].items()}
                outfits = {oid: row for oid, row in outfits.items() if row['expressions']}
                if outfits:
                    collections[ref] = outfits
                    metadata['collections'][ref] = collection['effective_revision']
            except UNAVAILABLE as exc:
                metadata['errors'][ref] = str(exc)
        return collections, metadata


def _eligible(cid, sid, identity):
    with store.locks.campaign_lock(cid):
        sid = _fences._current_sid(cid, sid, identity)
        if sid is None:
            return False
        active = {f['key'] for f in store.tracker.fields.active(store.tracker.fields.effective(cid, sid))}
        return store.tracker.settings.enabled(cid) and set(KEYS) <= active


def _items(task, rows, prep, snapshot, errors, chosen):
    refs, items = [], []
    outfit_stage = task == 'tracker-expression-outfit'
    instruction = ("Choose the outfit best matching this actor's visible clothing in this post."
                   if outfit_stage else
                   'Choose the visible expression for this actor from the selected outfit in this post.')
    for ref, options in rows.items():
        if len(options) <= 1:
            continue
        try:
            item = decisions.Item(store.tracker.prompt.expression_context(ref=ref,
                name=prep.get('roster', {}).get(ref, snapshot[ref].get('name', ref)),
                outfit=(chosen or {}).get(ref),
                public=public_values(snapshot[ref], prep.get('fields')),
                posts=prep['context_posts'], post=prep['post_msg']),
                (decisions.Choice('choice', instruction,
                    tuple(decisions.Option(oid, description) for oid, description in options.items())),))
            # One oversized or otherwise unofferable actor must not refuse its
            # neighbours' valid choices before the facade can chunk the batch.
            decisions.validate([item])
        except (ValueError, TypeError) as exc:
            errors[ref] = str(exc)
            continue
        refs.append(ref)
        items.append(item)
    return refs, items


async def _batch(task, rows, prep, snapshot, cid, sid, client, errors, chosen=None):
    if not rows or not await run_in_threadpool(_eligible, cid, sid, prep['_expression_identity']):
        return {}
    selected = {ref: next(iter(options)) for ref, options in rows.items() if len(options) == 1}
    refs, items = _items(task, rows, prep, snapshot, errors, chosen)
    if not items:
        return selected
    try:
        resolved = await run_in_threadpool(lambda: require_inference(task, cid, operation='decide'))
        if not await run_in_threadpool(_eligible, cid, sid, prep['_expression_identity']):
            return {}
        result = await operations.decide(task, items, client=client, resolved=resolved,
                                         campaign=cid, scene=sid, post=prep['post'])
        for index, ref in enumerate(refs):
            answer = result.items[index].answers.get('choice') if index < len(result.items) else None
            value = answer.answer if answer else None
            if isinstance(value, str) and value in rows[ref]:
                selected[ref] = value
            else:
                errors[ref] = 'No valid selection was returned'
    except Exception as exc:  # noqa: BLE001 -- presentation failures preserve narrative success
        for ref in refs:
            errors[ref] = str(exc) or type(exc).__name__
    return selected


async def choose(cid, sid, identity, key, prep, snapshot, client):
    snapshot = copy.deepcopy(snapshot)
    collections, metadata = await run_in_threadpool(_inputs, cid, sid, identity, key, prep, snapshot)
    sid = prep.get('sid', sid)
    outfits = await _batch('tracker-expression-outfit',
        {ref: {oid: row['label'] + '; clothing: ' + ', '.join(row['clothing_aliases'])
               for oid, row in outfits.items()} for ref, outfits in collections.items()},
        prep, snapshot, cid, sid, client, metadata['errors'])
    moods = await _batch('tracker-expression-mood',
        {ref: {m: m for m in collections[ref][oid]['expressions']} for ref, oid in outfits.items()},
        prep, snapshot, cid, sid, client, metadata['errors'],
        chosen={ref: {'id': oid, 'label': collections[ref][oid]['label'],
                      'clothing_aliases': collections[ref][oid]['clothing_aliases']}
                for ref, oid in outfits.items()})
    for ref, mood in moods.items():
        values = snapshot[ref].setdefault('fields', {})
        for field, value in zip(KEYS, (outfits[ref], mood), strict=True):
            values[field] = {'value': value, 'aware': 'present'}
    summary = {'selected': len(moods), 'failed': len(metadata['errors']),
               'skipped': len(prep['present']) - len(moods) - len(metadata['errors']),
               'errors': metadata['errors']}
    return snapshot, metadata, summary


@contextmanager
def publication(cid, sid, identity, prep, snapshot, metadata):
    """Hold relevant collection writers out while validating and publishing."""
    targets = {ref: target(cid, ref, metadata['versions'][ref]) for ref in metadata['collections']}
    addresses = []
    surviving = {}
    for ref, t in targets.items():
        try:
            addresses.extend((directory, 'manifest.json') for _, directory in paths.layers(t))
            surviving[ref] = t
        except UNAVAILABLE:
            clear_pair(snapshot, ref)
            metadata['errors'][ref] = 'Historical actor version is no longer available'
    targets = surviving
    with store.assets.sidecar_locks_held(*addresses):
        try:
            message, versions = binding(cid, sid, identity, prep['index'])
        except (IndexError, ValueError, OSError):
            yield False
            return
        enabled = {f['key'] for f in store.tracker.fields.active(store.tracker.fields.effective(cid, sid))}
        for ref, t in targets.items():
            try:
                current = read.effective(t)
                values = snapshot.get(ref, {}).get('fields', {})
                outfit = values.get(KEYS[0], {}).get('value')
                mood = values.get(KEYS[1], {}).get('value')
                image = current.get('outfits', {}).get(outfit, {}).get('expressions', {}).get(mood, {})
                valid = (set(KEYS) <= enabled and store.tracker.settings.enabled(cid)
                         and versions.get(ref) == t.version_id
                         and current['effective_revision'] == metadata['collections'][ref]
                         and (not outfit or (image.get('available') and image.get('image_id'))))
            except UNAVAILABLE:
                valid = False
            if not valid:
                clear_pair(snapshot, ref)
                metadata['errors'][ref] = 'Historical sprite collection changed before publication'
        yield message.get('post_id') == metadata['post_id'] and versions == metadata['versions']
