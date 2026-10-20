"""Shared helpers for the route modules.

Dependency-injection providers, the pydantic-version shim, the response-scope
read/write pair, image serving, the 404 guards every domain module reuses, the
opt-in page window the growing list routes share (#216), the stale-write
precondition every record editor shares (#35), and the LLM error-status
taxonomy every non-stream generation route answers with (#213). This module
holds no routes and imports no sibling route module, so it is always safe to
import from one.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ... import deadline, decisions, embeddings, llm, llm_sampling, model_guidance, store, wire
from ... import inference as operations
from ...health import ProviderHealth
from ...llm import LLMClient
from ...llm_errors import LLMError
from ...openai_compatible import OpenAICompatibleClient
from ...store.inference import cascade as inference_cascade
from ...store.inference import facts as inference_facts
from ...store.inference import limits as inference_limits
from ...store.inference import migrate as inference_migrate
from ...store.inference import resolve as inference
from ...store.inference.resolved import ResolvedInference
from ._clients import _count_tokens as _count_tokens
from ._clients import _load_post_image as _load_post_image
from ._clients import _post_images_for as _post_images_for
from ._clients import build_embeddings as build_embeddings
from ._clients import build_llm as build_llm
from ._clients import build_openai_compatible_client as build_openai_compatible_client
from ._clients import get_embeddings as get_embeddings
from ._clients import get_health as get_health
from ._clients import get_llm as get_llm
from ._clients import get_openai_compatible_client as get_openai_compatible_client
from ._deadlines import PLAN_CEILING_S as PLAN_CEILING_S
from ._deadlines import DeadlineRefused as DeadlineRefused
from ._deadlines import _bounded_call as _bounded_call
from ._deadlines import _noting as _noting
from ._deadlines import _plan_clock as _plan_clock
from ._deadlines import plan_ceiling as plan_ceiling
from ._deadlines import plan_deadline as plan_deadline
from ._drafts import draft_completion as draft_completion
from ._drafts import image_draft_prompt as image_draft_prompt
from ._errors import _LLM_STATUS as _LLM_STATUS
from ._errors import _LLM_STATUS_FALLBACK as _LLM_STATUS_FALLBACK

# Explicit compatibility aliases retain the original binding and star surface.
from ._errors import _fresh_or_409 as _fresh_or_409
from ._errors import _llm_http_error as _llm_http_error
from ._errors import _retry_after_header as _retry_after_header
from ._errors import run_error as run_error
from ._images import _BLOB_TOKEN as _BLOB_TOKEN
from ._images import _IMAGE_MEDIA as _IMAGE_MEDIA
from ._images import THUMB_BUCKETS as THUMB_BUCKETS
from ._images import THUMB_W as THUMB_W
from ._images import _description_failure as _description_failure
from ._images import _honours_version as _honours_version
from ._images import _image_source as _image_source
from ._images import _require_intact as _require_intact
from ._images import _serve_attachment as _serve_attachment
from ._images import _serve_image as _serve_image
from ._images import _serve_image_file as _serve_image_file
from ._images import _thumb_width as _thumb_width
from ._images import _upload_image_ext as _upload_image_ext
from ._images import _with_descriptions as _with_descriptions
from ._images import attachment_disposition as attachment_disposition
from ._images import field_provided as field_provided
from ._images import image_id_field as image_id_field
from ._images import image_promote_guards as image_promote_guards
from ._images import record_art_mutation as record_art_mutation
from ._images import thumb_query as thumb_query
from ._inference import _CONFIG_UNREADABLE as _CONFIG_UNREADABLE
from ._inference import LEGACY_MOVED as LEGACY_MOVED
from ._inference import NEWER_FORMAT as NEWER_FORMAT
from ._inference import RATES_AFTER_UPGRADE as RATES_AFTER_UPGRADE
from ._inference import RATES_NEWER_FORMAT as RATES_NEWER_FORMAT
from ._inference import RATES_UNREADABLE as RATES_UNREADABLE
from ._inference import UsableInference as UsableInference
from ._inference import _decide_error as _decide_error
from ._inference import _has_standing_model as _has_standing_model
from ._inference import _narrowed as _narrowed
from ._inference import _not_migrated as _not_migrated
from ._inference import _raise as _raise
from ._inference import _refuse_incapable as _refuse_incapable
from ._inference import _refuse_unusable as _refuse_unusable
from ._inference import _soft_inference as _soft_inference
from ._inference import _soft_resolved as _soft_resolved
from ._inference import _usable as _usable
from ._inference import escalation_inference as escalation_inference
from ._inference import legacy_refused as legacy_refused
from ._inference import override_inference as override_inference
from ._inference import rates_block as rates_block
from ._inference import rates_editable as rates_editable
from ._inference import refuse_newer as refuse_newer
from ._inference import refuse_unmigrated as refuse_unmigrated
from ._inference import require_inference as require_inference
from ._lookups import PC_HISTORY_MISSES as PC_HISTORY_MISSES
from ._lookups import _campaign_root_or_404 as _campaign_root_or_404
from ._lookups import _card_data as _card_data
from ._lookups import _content_fields as _content_fields
from ._lookups import _display_name_or_400 as _display_name_or_400
from ._lookups import _page_of as _page_of
from ._lookups import _page_window as _page_window
from ._lookups import _require_scene as _require_scene
from ._lookups import _sheet_failure_status as _sheet_failure_status
from ._lookups import _world_char_version_or_404 as _world_char_version_or_404
from ._lookups import _world_root_or_404 as _world_root_or_404
from ._lookups import pc_history_404 as pc_history_404
from ._prompts import _record_campaign_prompt as _record_campaign_prompt
from ._prompts import _record_prompt as _record_prompt
from ._prompts import _sampling_report as _sampling_report
from ._prompts import model_window as model_window
from ._responses import _dump as _dump
from ._responses import _response_body as _response_body
from ._responses import _turn_override as _turn_override
from ._responses import _write_response as _write_response
from ._responses import computes_only as computes_only
from ._responses import leaves_campaign_unchanged as leaves_campaign_unchanged
from ._responses import stamps_campaign_write as stamps_campaign_write
from ._uploads import _spooled_upload as _spooled_upload

log = logging.getLogger('grimoire.routes.common')

# Captured from the original module before any responsibility leaves existed.
__all__ = [
    'LEGACY_MOVED',
    'NEWER_FORMAT',
    'PC_HISTORY_MISSES',
    'PLAN_CEILING_S',
    'RATES_AFTER_UPGRADE',
    'RATES_NEWER_FORMAT',
    'RATES_UNREADABLE',
    'THUMB_BUCKETS',
    'THUMB_W',
    'BaseModel',
    'Callable',
    'DeadlineRefused',
    'HTTPException',
    'LLMClient',
    'LLMError',
    'OpenAICompatibleClient',
    'Path',
    'ProviderHealth',
    'Request',
    'ResolvedInference',
    'Response',
    'UsableInference',
    'annotations',
    'attachment_disposition',
    'build_embeddings',
    'build_llm',
    'build_openai_compatible_client',
    'computes_only',
    'contextlib',
    'dataclass',
    'deadline',
    'decisions',
    'draft_completion',
    'embeddings',
    'escalation_inference',
    'field_provided',
    'fields',
    'get_embeddings',
    'get_health',
    'get_llm',
    'get_openai_compatible_client',
    'image_draft_prompt',
    'image_id_field',
    'image_promote_guards',
    'inference',
    'inference_cascade',
    'inference_facts',
    'inference_limits',
    'inference_migrate',
    'leaves_campaign_unchanged',
    'legacy_refused',
    'llm',
    'llm_sampling',
    'log',
    'logging',
    'math',
    'model_guidance',
    'model_window',
    'operations',
    'os',
    'override_inference',
    'pc_history_404',
    'plan_ceiling',
    'plan_deadline',
    'quote',
    'rates_block',
    'rates_editable',
    're',
    'record_art_mutation',
    'refuse_newer',
    'refuse_unmigrated',
    'require_inference',
    'run_error',
    'stamps_campaign_write',
    'store',
    'tempfile',
    'thumb_query',
    'time',
    'wire',
]
