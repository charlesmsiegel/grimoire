"""Scenes and the play loop: scene CRUD and suggestions, the generating
routes (chat / retry / regenerate), cast seating, scene location, datetime and
response scope, the chronicle, and the absorb/audit end-of-scene flow."""

from __future__ import annotations

# Compatibility facade: definitions and state live in responsibility leaves.
# Keep the old import/dependency objects and explicit star surface; internal
# fault injection targets the defining leaf, not a copied facade alias.
# Leaves use the same eager router; finalize only reorders its original objects.
import asyncio
import contextlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ... import decisions, llm_sampling, prompts, store, wire
from ... import inference as operations
from ...llm import LLMClient
from ...llm_errors import LLMError
from ...store import campaign_settings
from ...store.absorb import membership as absorb_membership
from ...store.continuity import identity as continuity_identity
from ...store.continuity import review as continuity_review
from ...store.continuity import similarity as continuity_similarity
from ...store.inference import resolve as inference
from ...store.inference.resolved import ResolvedInference
from .. import character_turns, decision_capture, history_recall, runs, streaming
from .. import continuity as continuity_routes
from .. import tracker as tracker_routes
from ..common import (
    UsableInference,
    computes_only,
    draft_completion,
    get_embeddings,
    get_llm,
    model_window,
    override_inference,
    require_inference,
    run_error,
)
from ..common import (
    _campaign_root_or_404 as _campaign_root_or_404,
)
from ..common import (
    _decide_error as _decide_error,
)
from ..common import (
    _dump as _dump,
)
from ..common import (
    _llm_http_error as _llm_http_error,
)
from ..common import (
    _noting as _noting,
)
from ..common import (
    _page_of as _page_of,
)
from ..common import (
    _page_window as _page_window,
)
from ..common import (
    _record_prompt as _record_prompt,
)
from ..common import (
    _require_scene as _require_scene,
)
from ..common import (
    _response_body as _response_body,
)
from ..common import (
    _soft_inference as _soft_inference,
)
from ..common import (
    _soft_resolved as _soft_resolved,
)
from ..common import (
    _turn_override as _turn_override,
)
from ..common import (
    _write_response as _write_response,
)
from ..models import (
    Appear,
    AppearBatch,
    BranchScene,
    ChatTurn,
    ChronicleSave,
    Dismiss,
    EditMessage,
    EmergentCast,
    ExcludeMessage,
    NewScene,
    RegenerateBody,
    RenameScene,
    ReplayCancel,
    ReplayStart,
    ResponseSettings,
    RetryBody,
    SceneDatetime,
    SceneIdeaCreate,
    SceneIdeaStatus,
    SceneImportCommit,
    SceneIntent,
    SceneLocation,
    SceneSuggestionsRequest,
)
from ..streaming import StreamOutcome
from ..streaming import _chat_stream as _chat_stream
from . import _router as _router
from ._alternates import _PREVIEW_CHARS as _PREVIEW_CHARS
from ._alternates import _alternate as _alternate
from ._alternates import _put_back as _put_back
from ._alternates import _repair_a_broken_promotion as _repair_a_broken_promotion
from ._alternates import _undo_a_promotion as _undo_a_promotion
from ._alternates import get_scene_alternates as get_scene_alternates
from ._alternates import post_scene_alternate as post_scene_alternate
from ._breaks import _break_ask as _break_ask
from ._breaks import _break_body as _break_body
from ._breaks import _break_commit as _break_commit
from ._breaks import _break_intact as _break_intact
from ._breaks import _break_once as _break_once
from ._breaks import _break_provider as _break_provider
from ._breaks import _break_title as _break_title
from ._breaks import _break_title_commit as _break_title_commit
from ._breaks import _break_titled as _break_titled
from ._breaks import _break_view as _break_view
from ._breaks import get_scene_break as get_scene_break
from ._breaks import post_scene_break as post_scene_break
from ._breaks import post_scene_break_dismiss as post_scene_break_dismiss
from ._chat import _chat_resolution as _chat_resolution
from ._chat import _chat_run as _chat_run
from ._chat import _disown_dead_pending as _disown_dead_pending
from ._chat import _is_ephemeral as _is_ephemeral
from ._chat import _regenerate_run as _regenerate_run
from ._chat import _remove_outgoing_reply as _remove_outgoing_reply
from ._chat import _reroll_replaces as _reroll_replaces
from ._chat import _resolved_for_a_turn as _resolved_for_a_turn
from ._chat import _restore_reroll as _restore_reroll
from ._chat import _retry_run as _retry_run
from ._chat import _start_round as _start_round
from ._chat import _take_the_post_back as _take_the_post_back
from ._chat import post_chat as post_chat
from ._chat import post_regenerate as post_regenerate
from ._chat import post_retry as post_retry
from ._chronicle import CHRONICLE_PAGE as CHRONICLE_PAGE
from ._chronicle import _append_timeline_once as _append_timeline_once
from ._chronicle import _changes_membership as _changes_membership
from ._chronicle import _merged_detail as _merged_detail
from ._chronicle import _refuse_a_foreign_replay as _refuse_a_foreign_replay
from ._chronicle import _refuse_a_stale_review as _refuse_a_stale_review
from ._chronicle import _refuse_an_unresumable_save as _refuse_an_unresumable_save
from ._chronicle import _refuse_drifted_edits as _refuse_drifted_edits
from ._chronicle import get_chronicle as get_chronicle
from ._chronicle import put_chronicle as put_chronicle
from ._followups import _break_follow_up as _break_follow_up
from ._followups import _follow_up_hook as _follow_up_hook
from ._followups import _rolling_follow_up as _rolling_follow_up
from ._followups import _start_background as _start_background
from ._followups import schedule_follow_ups as schedule_follow_ups
from ._inspection import LIVE_SIDE as LIVE_SIDE
from ._inspection import _diff_side as _diff_side
from ._inspection import _require_campaign as _require_campaign
from ._inspection import get_campaign_prompt as get_campaign_prompt
from ._inspection import get_campaign_prompts as get_campaign_prompts
from ._inspection import get_scene_context as get_scene_context
from ._inspection import get_scene_prompt as get_scene_prompt
from ._inspection import get_scene_prompt_diff as get_scene_prompt_diff
from ._inspection import get_scene_prompts as get_scene_prompts
from ._lifecycle import delete_scene as delete_scene
from ._lifecycle import post_branch as post_branch
from ._lifecycle import post_scene as post_scene
from ._lifecycle import post_scene_import as post_scene_import
from ._lifecycle import post_scene_import_parse as post_scene_import_parse
from ._lifecycle import put_scene as put_scene
from ._messages import _edit_store_phase as _edit_store_phase
from ._messages import _forget_rewrite as _forget_rewrite
from ._messages import _own_record_keys as _own_record_keys
from ._messages import _require_restorable as _require_restorable
from ._messages import _settle_rewrite as _settle_rewrite
from ._messages import delete_scene_messages_from as delete_scene_messages_from
from ._messages import post_scene_retcon as post_scene_retcon
from ._messages import put_scene_message as put_scene_message
from ._messages import put_scene_message_excluded as put_scene_message_excluded
from ._reads import _active_variants as _active_variants
from ._reads import _describes as _describes
from ._reads import _first_part_of as _first_part_of
from ._reads import _first_parts as _first_parts
from ._reads import _record_for as _record_for
from ._reads import _with_actor_refs as _with_actor_refs
from ._reads import get_scene as get_scene
from ._reads import get_scene_rewrites as get_scene_rewrites
from ._reads import get_scenes as get_scenes
from ._replay import _replay_in_branch as _replay_in_branch
from ._replay import _replay_session as _replay_session
from ._replay import _replay_turn_run as _replay_turn_run
from ._replay import get_replay as get_replay
from ._replay import get_replay_preview as get_replay_preview
from ._replay import post_replay as post_replay
from ._replay import post_replay_accept as post_replay_accept
from ._replay import post_replay_cancel as post_replay_cancel
from ._replay import post_replay_turn as post_replay_turn
from ._review_budget import _ABANDONED_REASON as _ABANDONED_REASON
from ._review_budget import ABANDON_POLL as ABANDON_POLL
from ._review_budget import BUDGET_EXHAUSTED as BUDGET_EXHAUSTED
from ._review_budget import Abandoned as Abandoned
from ._review_budget import BudgetRefused as BudgetRefused
from ._review_budget import _Budget as _Budget
from ._review_budget import _budget_overrun as _budget_overrun
from ._review_budget import _clock as _clock
from ._review_budget import _gather_phases as _gather_phases
from ._review_budget import _phase_or_raise as _phase_or_raise
from ._review_budget import _phase_report as _phase_report
from ._review_budget import _watched as _watched
from ._review_identity import _IDENTITY_CUT_SHORT as _IDENTITY_CUT_SHORT
from ._review_identity import _IDENTITY_REFUSED as _IDENTITY_REFUSED
from ._review_identity import _IDENTITY_STAGING_FAILED as _IDENTITY_STAGING_FAILED
from ._review_identity import _SEMANTIC_BUDGET as _SEMANTIC_BUDGET
from ._review_identity import _SEMANTIC_UNAVAILABLE as _SEMANTIC_UNAVAILABLE
from ._review_identity import _embedding_reason as _embedding_reason
from ._review_identity import _extract_and_identify as _extract_and_identify
from ._review_identity import _identify as _identify
from ._review_identity import _identity_outcome as _identity_outcome
from ._review_identity import _identity_status as _identity_status
from ._review_identity import _resolve_identity as _resolve_identity
from ._review_phases import _DOSSIER_WORDS as _DOSSIER_WORDS
from ._review_phases import _VOICE_WORDS as _VOICE_WORDS
from ._review_phases import _judgeable_npcs as _judgeable_npcs
from ._review_phases import _npc_failed as _npc_failed
from ._review_phases import _npc_phase_result as _npc_phase_result
from ._review_phases import _NpcPhaseWords as _NpcPhaseWords
from ._review_phases import _run_audit as _run_audit
from ._review_phases import _stage_dossiers as _stage_dossiers
from ._review_phases import _stage_voice_drift as _stage_voice_drift
from ._review_phases import _tally_voice_verdict as _tally_voice_verdict
from ._review_phases import _transcript_label as _transcript_label
from ._review_phases import _voice_item as _voice_item
from ._review_snapshot import _absorb_snapshot as _absorb_snapshot
from ._review_snapshot import _absorb_tracked as _absorb_tracked
from ._review_snapshot import _already_absorbed as _already_absorbed
from ._review_snapshot import _contradictions as _contradictions
from ._review_snapshot import _Prepared as _Prepared
from ._review_state import _PERSIST_ATTEMPTS as _PERSIST_ATTEMPTS
from ._review_state import _PERSIST_BACKOFF as _PERSIST_BACKOFF
from ._review_state import _accepted as _accepted
from ._review_state import _already_committed as _already_committed
from ._review_state import _cancelled_error as _cancelled_error
from ._review_state import _clear_pending_review as _clear_pending_review
from ._review_state import _pending_for_retry as _pending_for_retry
from ._review_state import _persist_review as _persist_review
from ._review_state import _require_unmoved_review as _require_unmoved_review
from ._review_state import _retry_start as _retry_start
from ._review_state import _review_abandoned as _review_abandoned
from ._review_state import _ReviewCancelledError as _ReviewCancelledError
from ._review_state import _run_error as _run_error
from ._review_state import _SceneMovedError as _SceneMovedError
from ._review_state import _under_review_lock as _under_review_lock
from ._review_state import delete_pending_review as delete_pending_review
from ._review_state import get_pending_review as get_pending_review
from ._reviews import _absorb_start as _absorb_start
from ._reviews import _absorb_work as _absorb_work
from ._reviews import post_absorb as post_absorb
from ._reviews import post_audit as post_audit
from ._reviews import post_dossiers as post_dossiers
from ._rolling import _ROLLING_WAIT_CEILING as _ROLLING_WAIT_CEILING
from ._rolling import _ROLLING_WAIT_POLL as _ROLLING_WAIT_POLL
from ._rolling import _rolling_body as _rolling_body
from ._rolling import _rolling_claim as _rolling_claim
from ._rolling import _rolling_commit as _rolling_commit
from ._rolling import _rolling_due as _rolling_due
from ._rolling import _rolling_inflight as _rolling_inflight
from ._rolling import _rolling_inflight_guard as _rolling_inflight_guard
from ._rolling import _rolling_once as _rolling_once
from ._rolling import _rolling_refresh as _rolling_refresh
from ._rolling import _rolling_reread as _rolling_reread
from ._rolling import _rolling_wait as _rolling_wait
from ._rolling import get_rolling_summary as get_rolling_summary
from ._rolling import post_rolling_summary as post_rolling_summary
from ._router import router as router
from ._stage import _actor_version as _actor_version
from ._stage import _cast_role as _cast_role
from ._stage import _resolve_role as _resolve_role
from ._stage import _seat_cast_member as _seat_cast_member
from ._stage import _with_avatar_v as _with_avatar_v
from ._stage import delete_scene_cast as delete_scene_cast
from ._stage import get_cast_casefile as get_cast_casefile
from ._stage import get_cast_changes as get_cast_changes
from ._stage import get_cast_detail as get_cast_detail
from ._stage import get_scene_briefing as get_scene_briefing
from ._stage import get_scene_cast as get_scene_cast
from ._stage import get_scene_datetime as get_scene_datetime
from ._stage import get_scene_location as get_scene_location
from ._stage import get_scene_response as get_scene_response
from ._stage import get_scene_suggestions as get_scene_suggestions
from ._stage import post_dismiss as post_dismiss
from ._stage import post_emergent_cast as post_emergent_cast
from ._stage import post_scene_cast as post_scene_cast
from ._stage import post_scene_cast_batch as post_scene_cast_batch
from ._stage import put_scene_datetime as put_scene_datetime
from ._stage import put_scene_location as put_scene_location
from ._stage import put_scene_response as put_scene_response
from ._suggestions import _controls_or_refuse as _controls_or_refuse
from ._suggestions import _idea_card as _idea_card
from ._suggestions import _intent_payload as _intent_payload
from ._suggestions import _resolve_cast as _resolve_cast
from ._suggestions import _suggestions_payload as _suggestions_payload
from ._suggestions import get_scene_ideas as get_scene_ideas
from ._suggestions import post_scene_idea as post_scene_idea
from ._suggestions import post_scene_intent as post_scene_intent
from ._suggestions import post_scene_suggestions as post_scene_suggestions
from ._suggestions import put_scene_idea as put_scene_idea
from ._targets import _chat_target as _chat_target
from ._targets import _primary as _primary
from ._targets import _target_kind as _target_kind
from ._targets import _target_model as _target_model

log = logging.getLogger('grimoire.routes.scenes')

_router.finalize()

# Captured from the original module, before any leaves existed.
# Keep the original deferred module annotations at the compatibility boundary.
__annotations__ = {'_rolling_inflight': 'set[tuple[str, str]]'}

__all__ = [
    'ABANDON_POLL',
    'BUDGET_EXHAUSTED',
    'CHRONICLE_PAGE',
    'LIVE_SIDE',
    'APIRouter',
    'Abandoned',
    'Appear',
    'AppearBatch',
    'BaseModel',
    'BranchScene',
    'BudgetRefused',
    'ChatTurn',
    'ChronicleSave',
    'Depends',
    'Dismiss',
    'EditMessage',
    'EmergentCast',
    'ExcludeMessage',
    'File',
    'HTTPException',
    'Header',
    'LLMClient',
    'LLMError',
    'NewScene',
    'Query',
    'RegenerateBody',
    'RenameScene',
    'ReplayCancel',
    'ReplayStart',
    'Request',
    'ResolvedInference',
    'ResponseSettings',
    'RetryBody',
    'SceneDatetime',
    'SceneIdeaCreate',
    'SceneIdeaStatus',
    'SceneImportCommit',
    'SceneIntent',
    'SceneLocation',
    'SceneSuggestionsRequest',
    'StreamOutcome',
    'UploadFile',
    'UsableInference',
    'absorb_membership',
    'annotations',
    'asyncio',
    'campaign_settings',
    'character_turns',
    'computes_only',
    'contextlib',
    'continuity_identity',
    'continuity_review',
    'continuity_routes',
    'continuity_similarity',
    'dataclass',
    'decision_capture',
    'decisions',
    'delete_pending_review',
    'delete_scene',
    'delete_scene_cast',
    'delete_scene_messages_from',
    'draft_completion',
    'get_campaign_prompt',
    'get_campaign_prompts',
    'get_cast_casefile',
    'get_cast_changes',
    'get_cast_detail',
    'get_chronicle',
    'get_embeddings',
    'get_llm',
    'get_pending_review',
    'get_replay',
    'get_replay_preview',
    'get_rolling_summary',
    'get_scene',
    'get_scene_alternates',
    'get_scene_break',
    'get_scene_briefing',
    'get_scene_cast',
    'get_scene_context',
    'get_scene_datetime',
    'get_scene_ideas',
    'get_scene_location',
    'get_scene_prompt',
    'get_scene_prompt_diff',
    'get_scene_prompts',
    'get_scene_response',
    'get_scene_rewrites',
    'get_scene_suggestions',
    'get_scenes',
    'history_recall',
    'inference',
    'llm_sampling',
    'log',
    'logging',
    'model_window',
    'operations',
    'override_inference',
    'post_absorb',
    'post_audit',
    'post_branch',
    'post_chat',
    'post_dismiss',
    'post_dossiers',
    'post_emergent_cast',
    'post_regenerate',
    'post_replay',
    'post_replay_accept',
    'post_replay_cancel',
    'post_replay_turn',
    'post_retry',
    'post_rolling_summary',
    'post_scene',
    'post_scene_alternate',
    'post_scene_break',
    'post_scene_break_dismiss',
    'post_scene_cast',
    'post_scene_cast_batch',
    'post_scene_idea',
    'post_scene_import',
    'post_scene_import_parse',
    'post_scene_intent',
    'post_scene_retcon',
    'post_scene_suggestions',
    'prompts',
    'put_chronicle',
    'put_scene',
    'put_scene_datetime',
    'put_scene_idea',
    'put_scene_location',
    'put_scene_message',
    'put_scene_message_excluded',
    'put_scene_response',
    'require_inference',
    'router',
    'run_error',
    'run_in_threadpool',
    'runs',
    'schedule_follow_ups',
    'store',
    'streaming',
    'threading',
    'time',
    'tracker_routes',
    'uuid',
    'wire',
]
