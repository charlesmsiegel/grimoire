"""The scene state tracker's HTTP surface (store/tracker/), and its update run.

The on/off setting and the field-definition layers, and the run that keeps a
snapshot per post: `schedule` is called after every post lands (a player's, a
character's, an opener) and starts a `background` run that asks the model what
changed and stores the merged result under that post's key. The scene's
records are read, hand-edited and re-run through the routes at the bottom, and
the `after_*` hooks are how the transcript routes that edit, cut or swipe keep
those records consistent with what the transcript now says.

The callers are `routes/scenes.py`, `routes/character_turns.py` and
`routes/greetings.py`; this module imports none of them, which is what keeps
the route graph acyclic."""

from __future__ import annotations

import asyncio
import logging
import threading

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ... import inference as operations
from ... import store
from ...llm import LLMClient
from ...llm_errors import LLMError
from .. import runs
from ..common import (
    _dump as _dump,
)
from ..common import (
    _llm_http_error as _llm_http_error,
)
from ..common import (
    _require_scene as _require_scene,
)
from ..common import (
    get_llm,
    require_inference,
    run_error,
)
from ..models import CampaignTracker, TrackerEdit, TrackerLayer

# Explicit compatibility aliases retain the original binding and star surface.
from . import _router as _router
from ._fences import Base as Base
from ._fences import Mark as Mark
from ._fences import _base_mark as _base_mark
from ._fences import _base_unsettled as _base_unsettled
from ._fences import _current_sid as _current_sid
from ._fences import _obsolete as _obsolete
from ._hooks import _soft as _soft
from ._hooks import after_cut as after_cut
from ._hooks import after_swipe as after_swipe
from ._hooks import after_take_back as after_take_back
from ._hooks import after_text_edit as after_text_edit
from ._locks import _LOCKS_GUARD as _LOCKS_GUARD
from ._locks import _scene_lock as _scene_lock
from ._persistence import _commit as _commit
from ._persistence import _fail as _fail
from ._persistence import _fail_by_sid as _fail_by_sid
from ._persistence import _prepare as _prepare
from ._persistence import _settle_unwritten as _settle_unwritten
from ._records import INTERRUPTED as INTERRUPTED
from ._records import _flags as _flags
from ._records import _identity_or_404 as _identity_or_404
from ._records import _mood_text as _mood_text
from ._records import _moods as _moods
from ._records import _record_body as _record_body
from ._records import _reported as _reported
from ._records import _require_key as _require_key
from ._records import _require_on as _require_on
from ._records import _update_live as _update_live
from ._records import get_scene_tracker as get_scene_tracker
from ._records import get_tracker_record as get_tracker_record
from ._records import post_tracker_rerun_from as post_tracker_rerun_from
from ._records import post_tracker_retry as post_tracker_retry
from ._records import put_tracker_record as put_tracker_record
from ._router import router as router
from ._scheduling import mark as mark
from ._scheduling import mark_response as mark_response
from ._scheduling import schedule as schedule
from ._scheduling import schedule_response as schedule_response
from ._scheduling import schedule_untracked as schedule_untracked
from ._scheduling import start as start
from ._settings import _bad_layer as _bad_layer
from ._settings import _campaign_world as _campaign_world
from ._settings import _inherited_for_world as _inherited_for_world
from ._settings import _layer_body as _layer_body
from ._settings import _setting_body as _setting_body
from ._settings import _world_or_404 as _world_or_404
from ._settings import get_campaign_tracker as get_campaign_tracker
from ._settings import get_campaign_tracker_fields as get_campaign_tracker_fields
from ._settings import get_scene_tracker_fields as get_scene_tracker_fields
from ._settings import get_world_tracker_fields as get_world_tracker_fields
from ._settings import put_campaign_tracker as put_campaign_tracker
from ._settings import put_campaign_tracker_fields as put_campaign_tracker_fields
from ._settings import put_scene_tracker_fields as put_scene_tracker_fields
from ._settings import put_world_tracker_fields as put_world_tracker_fields
from ._transcript import _context_posts as _context_posts
from ._transcript import _locate as _locate
from ._transcript import _shown as _shown
from ._transcript import _speaker as _speaker
from ._update_run import _detail as _detail
from ._update_run import _text as _text
from ._update_run import _update as _update
from ._update_run import _update_locked as _update_locked

log = logging.getLogger('grimoire.routes.tracker')

_router.finalize()

# Captured from the original module before any responsibility leaves existed.
__all__ = [
    'INTERRUPTED',
    'APIRouter',
    'Base',
    'CampaignTracker',
    'Depends',
    'HTTPException',
    'LLMClient',
    'LLMError',
    'Mark',
    'Request',
    'TrackerEdit',
    'TrackerLayer',
    'after_cut',
    'after_swipe',
    'after_take_back',
    'after_text_edit',
    'annotations',
    'anyio',
    'asyncio',
    'get_campaign_tracker',
    'get_campaign_tracker_fields',
    'get_llm',
    'get_scene_tracker',
    'get_scene_tracker_fields',
    'get_tracker_record',
    'get_world_tracker_fields',
    'log',
    'logging',
    'mark',
    'mark_response',
    'operations',
    'post_tracker_rerun_from',
    'post_tracker_retry',
    'put_campaign_tracker',
    'put_campaign_tracker_fields',
    'put_scene_tracker_fields',
    'put_tracker_record',
    'put_world_tracker_fields',
    'require_inference',
    'router',
    'run_error',
    'run_in_threadpool',
    'runs',
    'schedule',
    'schedule_response',
    'schedule_untracked',
    'start',
    'store',
    'threading',
]
