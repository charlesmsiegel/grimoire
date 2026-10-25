"""Expression-only runs share tracker serialization without marking narrative pending.

The captured record and its complete index entry are the write fence. Transcript
and historical bindings are checked again at publication; a refresh never repairs
or certifies narrative history as a side effect of choosing sprites.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ... import store
from ...llm import LLMClient
from .. import runs
from ..common import _require_scene, get_llm
from . import _expression_selection as selection
from . import _fences, _locks, _records, _transcript
from ._router import router


def _capture(cid, sid, identity, key):
    with store.locks.campaign_lock(cid):
        _records._require_on(cid)
        body = store.tracker.records.read_snapshot(cid, identity, key)
        if body is None:
            raise HTTPException(409, detail='Generate tracker state before refreshing expressions')
        at = store.tracker.walk.index_of(cid, sid, key)
        located = _transcript._locate(cid, sid, key) if at is not None else None
        if located is None:
            raise HTTPException(409, detail='This tracker record is not the active post')
        prep = {**located, 'sid': sid, 'present': store.tracker.walk.present_at(cid, sid, at),
                'fields': store.tracker.fields.effective(cid, sid),
                'roster': store.tracker.walk.roster(cid, sid),
                'context_posts': _transcript._context_posts(cid, sid, located['first'])}
        return prep, body, store.tracker.records.read_index(cid, identity).get(key)


def _current(cid, sid, identity, key, prep, body, entry):
    actual = _fences._current_sid(cid, sid, identity)
    if actual is None or not store.tracker.settings.enabled(cid):
        return None
    if (store.tracker.walk.index_of(cid, actual, key) != prep['index']
            or _transcript._locate(cid, actual, key) != {k: prep[k] for k in
                ('index', 'first', 'post', 'rid', 'post_msg')}
            or store.tracker.records.read_snapshot(cid, identity, key) != body
            or store.tracker.records.read_index(cid, identity).get(key) != entry):
        return None
    return actual


def _commit(cid, sid, identity, key, prep, body, entry, snapshot, metadata):
    with store.locks.campaign_lock(cid):
        actual = _current(cid, sid, identity, key, prep, body, entry)
        if actual is None:
            return False
        with selection.publication(cid, actual, identity, prep, snapshot, metadata) as valid:
            if not valid:
                return False
            written = store.tracker.records.save_expressions(cid, identity, key, snapshot, metadata,
                                                            expected=body, expected_index=entry)
            if written:
                store.revision.bump(cid)
            return written


async def _work(app, cid, sid, identity, key, prep, body, entry, client):
    async with _locks._scene_lock(app, cid, identity):
        def current():
            with store.locks.campaign_lock(cid):
                return _current(cid, sid, identity, key, prep, body, entry)
        actual = await run_in_threadpool(current)
        if actual is None:
            return {'state': 'landed', 'result': {'selected': 0, 'skipped': len(prep['present']),
                                                   'failed': 0, 'applied': False, 'outcome': 'obsolete'}}
        snapshot, metadata, summary = await selection.choose(
            cid, actual, identity, key, prep, body['snapshot'], client)
        written = await run_in_threadpool(_commit, cid, actual, identity, key, prep,
                                          body, entry, snapshot, metadata)
        # Collection changes discovered at publication are visible as failures,
        # even though unaffected actors' pairs can still land.
        summary.update(failed=len(metadata['errors']), errors=metadata['errors'])
        summary['selected'] = sum(all(k in a.get('fields', {}) for k in selection.KEYS)
                                  for ref, a in snapshot.items() if ref in prep['present'])
        if not written:
            summary.update(selected=0, skipped=len(prep['present']), failed=0)
        else:
            summary['skipped'] = max(0, len(prep['present']) - summary['selected'] - summary['failed'])
        return {'state': 'landed', 'result': {**summary, 'applied': written,
                                             'outcome': 'refreshed' if written else 'obsolete'}}


def start(app, cid, sid, key, client):
    _require_scene(cid, sid)
    _records._require_key(key)
    identity = store.scenes.ensure_identity(cid, sid)
    prep, body, entry = _capture(cid, sid, identity, key)
    run = runs.reserve_background(app, cid, sid, 'tracker-expressions')
    if run is None:
        raise HTTPException(503, detail='Expression refresh could not be scheduled')
    if run.scene_identity != identity:
        runs.release_before_start(app, run, 'failed', {'kind': 'scene_replaced', 'detail': 'Scene replaced'})
        raise HTTPException(409, detail='Scene replaced')
    try:
        runs.start_computing(app, run, lambda: _work(app, cid, sid, identity, key, prep, body, entry, client))
    except Exception as exc:
        runs.release_before_start(app, run, 'failed', {'kind': 'run_failed', 'detail': str(exc)})
        raise
    return {'run': runs.run_payload(run)}


@router.post('/campaigns/{cid}/scenes/{sid}/tracker/records/{key}/expressions', status_code=202)
def post_tracker_expressions(cid: str, sid: str, key: str, request: Request,
                             client: LLMClient = Depends(get_llm)):
    return start(request.app, cid, sid, key, client)


@router.get('/campaigns/{cid}/scenes/{sid}/tracker/runs')
def get_tracker_runs(cid: str, sid: str, request: Request):
    _require_scene(cid, sid)
    identity = store.scenes.scene_identity(cid, sid)
    found = request.app.state.runs.for_subject(('scene', cid, identity), identity=identity)
    return {'runs': [runs.run_payload(run) for run in found
                     if run.kind in ('tracker-update', 'tracker-expressions')]}


def start_post(app, cid, sid, post_id, client):
    _require_scene(cid, sid)
    with store.locks.campaign_lock(cid):
        messages = store.scenes.read_scene(cid, sid)['messages']
        at = next((i for i, m in enumerate(messages) if m.get('post_id') == post_id), None)
        if at is None:
            raise HTTPException(404, detail='Post not found')
        # A split response shares one tracker record, owned at its final part.
        message = messages[at]
        if message.get('response_id'):
            at = max(i for i, m in enumerate(messages) if m.get('response_id') == message['response_id'])
        key = store.tracker.walk.key_at(cid, sid, at)
        if key is None:
            raise HTTPException(409, detail='Generate tracker state before refreshing expressions')
    return start(app, cid, sid, key, client)
