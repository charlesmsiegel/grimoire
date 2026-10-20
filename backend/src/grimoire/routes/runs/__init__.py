"""The run record and the registry that indexes it. Pure data, no scheduling.

A run is one route invocation that produces LLM output, decoupled from the HTTP
request that asked for it. The request may end -- the phone locks, the tab is
backgrounded, the socket resets -- while the run keeps going; a later request
finds it by id and picks up the frames it missed.

Two axes decide how a run behaves, and they are deliberately separate:

* the **subject** it acts on -- a scene, a campaign, a world, or the app --
  which is what a client asks about and what scopes every lookup;
* the **class** of work -- ``turn``, ``review``, ``background``, ``draft``,
  ``maintenance`` -- which decides whether it excludes other work on that
  subject.

``turn`` and ``review`` share one exclusion key per scene, because both rewrite
that scene's transcript and two of them at once lose one. ``background`` and
``draft`` declare none: they legitimately overlap, so the subject index is a
collection rather than a most-recent pointer. ``maintenance`` -- image-store
migration and collection -- holds one key for the whole CLASS, whatever its
subject, and runs its work in a worker thread the run outlives
(`run_maintenance`).

Nothing here imports ``streaming`` or ``scenes``. The registry is reachable from
the whole route layer, and a dependency in that direction would make the module
graph cyclic; ``test_import_guard`` holds it to that.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import contextlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal, Protocol

import anyio
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ... import runner, store
from ...store import attempts as scenes_attempts
from ...store import maintenance_reports, paths, proclock, scenes
from ...store.campaigns import read as campaigns_read
from ...store.campaigns.paths import CampaignNotFound
from . import _router as _router
from ._contracts import _EXCLUSIVE as _EXCLUSIVE
from ._contracts import _MAX_PRECANCEL as _MAX_PRECANCEL
from ._contracts import ATTACHABLE as ATTACHABLE
from ._contracts import CACHE_SYNC_KIND as CACHE_SYNC_KIND
from ._contracts import CANCEL_TIMEOUT_SECONDS as CANCEL_TIMEOUT_SECONDS
from ._contracts import MAINTENANCE_KEY as MAINTENANCE_KEY
from ._contracts import REAP_SECONDS as REAP_SECONDS
from ._contracts import STREAM_POLL_SECONDS as STREAM_POLL_SECONDS
from ._contracts import UNRESOLVED as UNRESOLVED
from ._contracts import HandshakeEvent as HandshakeEvent
from ._contracts import MaintenanceHeldError as MaintenanceHeldError
from ._contracts import MaintenanceRunningError as MaintenanceRunningError
from ._contracts import RunClass as RunClass
from ._contracts import RunInFlightError as RunInFlightError
from ._contracts import RunState as RunState
from ._contracts import StoreMovingError as StoreMovingError
from ._contracts import Subject as Subject
from ._contracts import SyncRunningError as SyncRunningError
from ._contracts import _log as _log
from ._contracts import _PlainEvent as _PlainEvent
from ._contracts import exclusion_key as exclusion_key
from ._events import _start_cursor as _start_cursor
from ._events import lead_frame as lead_frame
from ._events import run_payload as run_payload
from ._events import sse as sse
from ._events import tail_response as tail_response
from ._execution import _detail_kind as _detail_kind
from ._execution import _detail_text as _detail_text
from ._execution import _release_unstarted as _release_unstarted
from ._execution import answer_without_running as answer_without_running
from ._execution import release_before_start as release_before_start
from ._execution import reservation as reservation
from ._execution import start_computing as start_computing
from ._execution import start_detached as start_detached
from ._fences import require_campaign_free as require_campaign_free
from ._fences import require_group_free as require_group_free
from ._fences import require_scene_free as require_scene_free
from ._fences import require_scene_open as require_scene_open
from ._fences import scene_held_free as scene_held_free
from ._fences import scene_held_open as scene_held_open
from ._fences import store_held_still as store_held_still
from ._maintenance import _failure_report as _failure_report
from ._maintenance import _report_failure as _report_failure
from ._maintenance import maintenance_excluded as maintenance_excluded
from ._maintenance import maintenance_live as maintenance_live
from ._maintenance import reserve_cache_sync as reserve_cache_sync
from ._maintenance import run_maintenance as run_maintenance
from ._maintenance import start_maintenance as start_maintenance
from ._maintenance_claims import _acquire_maintenance_lock as _acquire_maintenance_lock
from ._maintenance_claims import _claim_maintenance as _claim_maintenance
from ._maintenance_claims import _maintenance_lock_path as _maintenance_lock_path
from ._record import Run as Run
from ._registry import RunRegistry as RunRegistry
from ._registry import install_registry as install_registry
from ._reservations import _reserve as _reserve
from ._reservations import await_reviews_stopped as await_reviews_stopped
from ._reservations import cancel_review as cancel_review
from ._reservations import drop_pending_touched as drop_pending_touched
from ._reservations import forget_root as forget_root
from ._reservations import forget_subject as forget_subject
from ._reservations import pin_root_until as pin_root_until
from ._reservations import replay_attempt as replay_attempt
from ._reservations import reserve_background as reserve_background
from ._reservations import reserve_campaign_background as reserve_campaign_background
from ._reservations import reserve_draft as reserve_draft
from ._reservations import reserve_review as reserve_review
from ._reservations import reserve_scene_draft as reserve_scene_draft
from ._reservations import reserve_turn as reserve_turn
from ._reservations import run_draft as run_draft
from ._router import router as router
from ._scene_routes import _resolve as _resolve
from ._scene_routes import get_attempt_state as get_attempt_state
from ._scene_routes import get_run as get_run
from ._scene_routes import get_run_stream as get_run_stream
from ._scene_routes import get_scene_by_identity as get_scene_by_identity
from ._scene_routes import get_scene_run as get_scene_run
from ._scene_routes import post_attempt_cancel as post_attempt_cancel
from ._scene_routes import post_run_cancel as post_run_cancel
from ._subject_routes import _subject_cancel as _subject_cancel
from ._subject_routes import _subject_run as _subject_run
from ._subject_routes import _subject_runs as _subject_runs
from ._subject_routes import _subject_stream as _subject_stream
from ._subject_routes import get_campaign_run as get_campaign_run
from ._subject_routes import get_campaign_run_stream as get_campaign_run_stream
from ._subject_routes import get_campaign_runs as get_campaign_runs
from ._subject_routes import get_global_run as get_global_run
from ._subject_routes import get_global_run_stream as get_global_run_stream
from ._subject_routes import get_global_runs as get_global_runs
from ._subject_routes import get_world_run as get_world_run
from ._subject_routes import get_world_run_stream as get_world_run_stream
from ._subject_routes import get_world_runs as get_world_runs
from ._subject_routes import post_campaign_run_cancel as post_campaign_run_cancel
from ._subject_routes import post_global_run_cancel as post_global_run_cancel
from ._subject_routes import post_world_run_cancel as post_world_run_cancel
from ._subjects import GLOBAL_SUBJECT as GLOBAL_SUBJECT
from ._subjects import _campaign_label as _campaign_label
from ._subjects import _gone as _gone
from ._subjects import _scene_label as _scene_label
from ._subjects import _scene_subject as _scene_subject
from ._subjects import _subject_labels as _subject_labels
from ._subjects import campaign_subject as campaign_subject
from ._subjects import world_subject as world_subject

_router.finalize()

# Original star-import surface, captured before extraction.
__all__ = [
    'ATTACHABLE',
    'CACHE_SYNC_KIND',
    'CANCEL_TIMEOUT_SECONDS',
    'GLOBAL_SUBJECT',
    'MAINTENANCE_KEY',
    'REAP_SECONDS',
    'STREAM_POLL_SECONDS',
    'UNRESOLVED',
    'APIRouter',
    'Callable',
    'CampaignNotFound',
    'HTTPException',
    'HandshakeEvent',
    'Iterable',
    'Literal',
    'MaintenanceHeldError',
    'MaintenanceRunningError',
    'Path',
    'Protocol',
    'Query',
    'Request',
    'Run',
    'RunClass',
    'RunInFlightError',
    'RunRegistry',
    'RunState',
    'StoreMovingError',
    'StreamingResponse',
    'Subject',
    'SyncRunningError',
    'annotations',
    'answer_without_running',
    'anyio',
    'await_reviews_stopped',
    'campaign_subject',
    'campaigns_read',
    'cancel_review',
    'contextlib',
    'drop_pending_touched',
    'exclusion_key',
    'forget_root',
    'forget_subject',
    'get_attempt_state',
    'get_campaign_run',
    'get_campaign_run_stream',
    'get_campaign_runs',
    'get_global_run',
    'get_global_run_stream',
    'get_global_runs',
    'get_run',
    'get_run_stream',
    'get_scene_by_identity',
    'get_scene_run',
    'get_world_run',
    'get_world_run_stream',
    'get_world_runs',
    'install_registry',
    'json',
    'lead_frame',
    'logging',
    'maintenance_excluded',
    'maintenance_live',
    'maintenance_reports',
    'paths',
    'pin_root_until',
    'post_attempt_cancel',
    'post_campaign_run_cancel',
    'post_global_run_cancel',
    'post_run_cancel',
    'post_world_run_cancel',
    'proclock',
    'release_before_start',
    'replay_attempt',
    'require_campaign_free',
    'require_group_free',
    'require_scene_free',
    'require_scene_open',
    'reservation',
    'reserve_background',
    'reserve_cache_sync',
    'reserve_campaign_background',
    'reserve_draft',
    'reserve_review',
    'reserve_scene_draft',
    'reserve_turn',
    'router',
    'run_draft',
    'run_maintenance',
    'run_payload',
    'runner',
    'scene_held_free',
    'scene_held_open',
    'scenes',
    'scenes_attempts',
    'sse',
    'start_computing',
    'start_detached',
    'start_maintenance',
    'store',
    'store_held_still',
    'tail_response',
    'threading',
    'time',
    'uuid',
    'world_subject',
]

# Preserve deferred module annotations without copying live values.
__annotations__ = {'_EXCLUSIVE': 'frozenset[str]', 'ATTACHABLE': 'frozenset[str]', 'GLOBAL_SUBJECT': 'Subject'}
