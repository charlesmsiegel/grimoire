"""One assigned actor per call, one bounded round per player post."""

from __future__ import annotations

# Compatibility facade: definitions and state live in responsibility leaves.
# Keep the old import/dependency objects and explicit star surface; internal
# fault injection targets the defining leaf, not a copied facade alias.
# Leaves use the same eager router; finalize only reorders its original objects.
import contextlib
import copy
import dataclasses
import json
import logging
import random
from collections.abc import Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ... import deadline, decisions, draws, llm_reasoning, prompts, store, tool_calls, wire
from ... import inference as operations
from ...llm import ATTEMPTED, LLMClient, fallback_sampling, prefill_capable
from ...llm_errors import LLMError
from ...model_guidance import PreparedMessages
from ...store.inference import providers as inference_providers
from ...store.inference import resolve as inference_resolve
from .. import decision_capture, history_recall, runs, streaming, tool_decision
from .. import epistemic as epistemic_stage
from .. import tracker as tracker_routes
from ..common import (
    _dump as _dump,
)
from ..common import (
    _record_prompt as _record_prompt,
)
from ..common import (
    _require_scene as _require_scene,
)
from ..common import (
    _soft_resolved as _soft_resolved,
)
from ..common import (
    _turn_override as _turn_override,
)
from ..common import (
    get_llm,
    model_window,
    override_inference,
    plan_ceiling,
    plan_deadline,
    require_inference,
)
from ..models import GroupSettings, RegenerateBody
from . import _router as _router
from ._generation import _PLAN_TASKS as _PLAN_TASKS
from ._generation import PLAY_TOOL_BUDGET as PLAY_TOOL_BUDGET
from ._generation import PLAY_TOOL_CALLS as PLAY_TOOL_CALLS
from ._generation import PLAY_TOOL_SHAPE as PLAY_TOOL_SHAPE
from ._generation import _contribution_tool as _contribution_tool
from ._generation import _drawn_stance as _drawn_stance
from ._generation import _loop_or_plain as _loop_or_plain
from ._generation import _LoopMeter as _LoopMeter
from ._generation import _offers_tool as _offers_tool
from ._generation import _opened as _opened
from ._generation import _plan_extras as _plan_extras
from ._generation import _plan_mode as _plan_mode
from ._generation import _play_tool as _play_tool
from ._generation import _PlayTool as _PlayTool
from ._generation import _pre_generation as _pre_generation
from ._generation import _reasoning_frame as _reasoning_frame
from ._generation import _stream_contribution as _stream_contribution
from ._generation import _stream_events as _stream_events
from ._generation import _tool_events as _tool_events
from ._generation import _tool_selection as _tool_selection
from ._generation import _turn_inputs as _turn_inputs
from ._generation import _turn_plan as _turn_plan
from ._generation import _visibility as _visibility
from ._persistence import _attempted_id as _attempted_id
from ._persistence import _land_variant as _land_variant
from ._persistence import _made_by as _made_by
from ._persistence import _normalise as _normalise
from ._persistence import _save as _save
from ._planning import _HANDOFF_ISSUES as _HANDOFF_ISSUES
from ._planning import _chain_continues as _chain_continues
from ._planning import _decision_successor as _decision_successor
from ._planning import _ending_issue as _ending_issue
from ._planning import _first_actor as _first_actor
from ._planning import _follow_on as _follow_on
from ._planning import _handoff_pool as _handoff_pool
from ._planning import _last_contribution as _last_contribution
from ._planning import _may_pick as _may_pick
from ._planning import _plan as _plan
from ._planning import _plan_round as _plan_round
from ._planning import _rng as _rng
from ._planning import _samples_pick as _samples_pick
from ._planning import _select as _select
from ._planning import _select_successor as _select_successor
from ._planning import _selector_inputs as _selector_inputs
from ._planning import _settings as _settings
from ._planning import _stop as _stop
from ._planning import _successor as _successor
from ._planning import _successor_answer as _successor_answer
from ._planning import _successor_candidates as _successor_candidates
from ._planning import _successor_inputs as _successor_inputs
from ._planning import answers_nothing as answers_nothing
from ._planning import refuse_an_unanswerable_pick as refuse_an_unanswerable_pick
from ._prompts import OUTCOME_SECTION_ID as OUTCOME_SECTION_ID
from ._prompts import _capture as _capture
from ._prompts import _compose as _compose
from ._prompts import _prepare as _prepare
from ._prompts import _stored_view as _stored_view
from ._prompts import _variant_target as _variant_target
from ._responses import _EXTEND_CLOSERS as _EXTEND_CLOSERS
from ._responses import _EXTEND_RETAINED as _EXTEND_RETAINED
from ._responses import _EXTEND_ROLL as _EXTEND_ROLL
from ._responses import ExtendPlan as ExtendPlan
from ._responses import _accept_extend as _accept_extend
from ._responses import _accept_reroll as _accept_reroll
from ._responses import _extend_joiner as _extend_joiner
from ._responses import _extend_messages as _extend_messages
from ._responses import _extend_mode as _extend_mode
from ._responses import _extend_rewrite as _extend_rewrite
from ._responses import _extend_target as _extend_target
from ._responses import _extend_views as _extend_views
from ._responses import _extends_record as _extends_record
from ._responses import _prefills as _prefills
from ._responses import _refuse as _refuse
from ._responses import _reroll_frames as _reroll_frames
from ._responses import _served_mode as _served_mode
from ._responses import activate_response as activate_response
from ._responses import delete_response as delete_response
from ._responses import extend_response as extend_response
from ._responses import get_group as get_group
from ._responses import get_response as get_response
from ._responses import get_response_swipe as get_response_swipe
from ._responses import put_group as put_group
from ._responses import regenerate_response as regenerate_response
from ._responses import replay_actor as replay_actor
from ._rounds import _after_saved_contribution as _after_saved_contribution
from ._rounds import _frames as _frames
from ._rounds import _history_replays as _history_replays
from ._rounds import _pause as _pause
from ._rounds import _recover_completed as _recover_completed
from ._rounds import _refine_step as _refine_step
from ._rounds import _rescue as _rescue
from ._rounds import _retry_context_matches as _retry_context_matches
from ._rounds import _round_frames as _round_frames
from ._rounds import _round_history as _round_history
from ._rounds import _round_post as _round_post
from ._rounds import _settle_recovered_successor as _settle_recovered_successor
from ._rounds import resume_roll as resume_roll
from ._rounds import retry as retry
from ._rounds import start as start
from ._router import router as router
from ._state import _abort_meter as _abort_meter
from ._state import _Claim as _Claim
from ._state import _Progress as _Progress
from ._state import _public_error as _public_error
from ._state import _round_state as _round_state
from ._state import _start_tracking as _start_tracking
from ._state import enabled as enabled
from ._state import roster as roster
from ._state import validate_actor as validate_actor

_log = logging.getLogger('grimoire.character_turns')

_router.finalize()

# Captured from the original module, before any leaves existed.
# Keep the original deferred module annotations at the compatibility boundary.
__annotations__ = {'_rng': 'Callable[[], random.Random]'}

__all__ = [
    'ATTEMPTED',
    'OUTCOME_SECTION_ID',
    'PLAY_TOOL_BUDGET',
    'PLAY_TOOL_CALLS',
    'PLAY_TOOL_SHAPE',
    'APIRouter',
    'Any',
    'Callable',
    'Depends',
    'ExtendPlan',
    'GroupSettings',
    'HTTPException',
    'Header',
    'LLMClient',
    'LLMError',
    'PreparedMessages',
    'RegenerateBody',
    'Request',
    'aclosing',
    'activate_response',
    'annotations',
    'answers_nothing',
    'anyio',
    'contextlib',
    'copy',
    'dataclass',
    'dataclasses',
    'deadline',
    'decision_capture',
    'decisions',
    'delete_response',
    'draws',
    'enabled',
    'epistemic_stage',
    'extend_response',
    'fallback_sampling',
    'get_group',
    'get_llm',
    'get_response',
    'get_response_swipe',
    'history_recall',
    'inference_providers',
    'inference_resolve',
    'json',
    'llm_reasoning',
    'logging',
    'model_window',
    'operations',
    'override_inference',
    'plan_ceiling',
    'plan_deadline',
    'prefill_capable',
    'prompts',
    'put_group',
    'random',
    'refuse_an_unanswerable_pick',
    'regenerate_response',
    'replay_actor',
    'require_inference',
    'resume_roll',
    'retry',
    'roster',
    'router',
    'run_in_threadpool',
    'runs',
    'start',
    'store',
    'streaming',
    'tool_calls',
    'tool_decision',
    'tracker_routes',
    'validate_actor',
    'wire',
]
