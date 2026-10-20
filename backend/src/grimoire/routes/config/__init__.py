"""Application-wide settings: config, LLM connections, styles, response
presets and the global response scope, plus the entity-kind, calendar-provider
and climate catalogues that worlds, campaigns and the import dialogs select
from."""

from __future__ import annotations

import asyncio
import logging
import math
import unicodedata
import uuid
from datetime import datetime
from typing import Literal, NamedTuple

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from ... import (
    cache_warm,
    catalog,
    decisions,
    embeddings,
    health,
    llm,
    llm_errors,
    llm_sampling,
    llm_usage,
    store,
    tool_calls,
    wire,
)
from ... import inference as operations
from ...llm import LLMClient
from ...llm_errors import LLMError
from ...store import compiled, group_refs
from ...store import inference_retired as retired
from ...store import paths as library_paths
from ...store.absorb import membership as absorb_membership
from ...store.inference import capabilities, controls, facts, limits, providers
from ...store.inference import migrate as inference_migrate
from ...store.inference import resolve as inference
from ...store.inference import settings as inference_settings
from .. import runs
from ..common import (
    NEWER_FORMAT,
    get_health,
    get_llm,
    legacy_refused,
    refuse_newer,
    refuse_unmigrated,
    require_inference,
    run_error,
)
from ..common import (
    _bounded_call as _bounded_call,
)
from ..common import (
    _dump as _dump,
)
from ..common import (
    _llm_http_error as _llm_http_error,
)
from ..common import (
    _response_body as _response_body,
)
from ..common import (
    _soft_resolved as _soft_resolved,
)
from ..common import (
    _write_response as _write_response,
)
from ..models import (
    CampaignPlayDecisions,
    CatalogProbe,
    ConfigUpdate,
    ConnectionCreate,
    ConnectionUpdate,
    DataDirUpdate,
    FactsUpdate,
    HealthCheck,
    ModelTestPreview,
    ModelTestRun,
    PromptLayoutUpdate,
    ResponseSettings,
    SamplerImportBody,
    SamplerPresetBody,
    StyleCreate,
    StyleUpdate,
)
from . import _probes as _probes

# Explicit compatibility aliases retain the original binding and star surface.
from . import _router as _router
from ._probes import _HALTING_KINDS as _HALTING_KINDS
from ._probes import _HALTING_STATUSES as _HALTING_STATUSES
from ._probes import _OPTIONS_NOT_SENT as _OPTIONS_NOT_SENT
from ._probes import _VERDICT_KEYS as _VERDICT_KEYS
from ._probes import GENERATING_CHECK_KINDS as GENERATING_CHECK_KINDS
from ._probes import HEALTH_CHECK_CEILING as HEALTH_CHECK_CEILING
from ._probes import HEALTH_UNCONFIRMED as HEALTH_UNCONFIRMED
from ._probes import MODEL_TEST_CEILING as MODEL_TEST_CEILING
from ._probes import TEST_UNCONFIRMED as TEST_UNCONFIRMED
from ._probes import _collecting as _collecting
from ._probes import _embed_endpoint as _embed_endpoint
from ._probes import _embed_probe as _embed_probe
from ._probes import _halts as _halts
from ._probes import _health_body as _health_body
from ._probes import _missed_call as _missed_call
from ._probes import _Outcome as _Outcome
from ._probes import _probe as _probe
from ._probes import _probe_all as _probe_all
from ._probes import _probe_target as _probe_target
from ._probes import _record_verdicts as _record_verdicts
from ._probes import _records as _records
from ._probes import _test_plan as _test_plan
from ._probes import _tools_choice as _tools_choice
from ._probes import close_clients as close_clients
from ._probes import post_connection_health as post_connection_health
from ._probes import post_connection_test as post_connection_test
from ._probes import post_connection_test_preview as post_connection_test_preview
from ._providers import _TEXT_FIELDS as _TEXT_FIELDS
from ._providers import ADDRESS_FIXED as ADDRESS_FIXED
from ._providers import FACTS_MANGLED as FACTS_MANGLED
from ._providers import FACTS_UNREADABLE as FACTS_UNREADABLE
from ._providers import MODEL_FIELD_MOVED as MODEL_FIELD_MOVED
from ._providers import InferenceControlsBody as InferenceControlsBody
from ._providers import _apply_preset as _apply_preset
from ._providers import _apply_preset_update as _apply_preset_update
from ._providers import _check_billing as _check_billing
from ._providers import _check_one_line as _check_one_line
from ._providers import _check_preset_field as _check_preset_field
from ._providers import _connection_sampling as _connection_sampling
from ._providers import _facts_body as _facts_body
from ._providers import _facts_conn as _facts_conn
from ._providers import _model_fields_refused as _model_fields_refused
from ._providers import _named_preset as _named_preset
from ._providers import _picker_models as _picker_models
from ._providers import _refuse_unconfirmed_facts as _refuse_unconfirmed_facts
from ._providers import _refuse_unconfirmed_reembed as _refuse_unconfirmed_reembed
from ._providers import _same_url as _same_url
from ._providers import _with_effective as _with_effective
from ._providers import delete_connection_route as delete_connection_route
from ._providers import get_connection as get_connection
from ._providers import get_connection_capabilities as get_connection_capabilities
from ._providers import get_connection_facts as get_connection_facts
from ._providers import get_connections as get_connections
from ._providers import get_provider_presets as get_provider_presets
from ._providers import post_connection as post_connection
from ._providers import post_connection_models_refresh as post_connection_models_refresh
from ._providers import post_inference_controls as post_inference_controls
from ._providers import post_model_catalog as post_model_catalog
from ._providers import put_connection as put_connection
from ._providers import put_connection_facts as put_connection_facts
from ._responses import _preset_or_404 as _preset_or_404
from ._responses import delete_sampler_preset as delete_sampler_preset
from ._responses import delete_style as delete_style
from ._responses import get_global_response as get_global_response
from ._responses import get_sampler_preset as get_sampler_preset
from ._responses import get_sampler_presets as get_sampler_presets
from ._responses import get_style as get_style
from ._responses import get_styles as get_styles
from ._responses import post_sampler_preset as post_sampler_preset
from ._responses import post_sampler_preset_import as post_sampler_preset_import
from ._responses import post_style as post_style
from ._responses import post_style_duplicate as post_style_duplicate
from ._responses import put_global_response as put_global_response
from ._responses import put_sampler_preset as put_sampler_preset
from ._responses import put_style as put_style
from ._router import router as router
from ._settings import _EMBEDDING_LEGACY_KEYS as _EMBEDDING_LEGACY_KEYS
from ._settings import _ON_OFF_KEYS as _ON_OFF_KEYS
from ._settings import _PLAY_FIELDS as _PLAY_FIELDS
from ._settings import _ceiling_ok as _ceiling_ok
from ._settings import _Chat as _Chat
from ._settings import _chat as _chat
from ._settings import _layout_body as _layout_body
from ._settings import _play_body as _play_body
from ._settings import _public_config as _public_config
from ._settings import _recursion_depth_ok as _recursion_depth_ok
from ._settings import _refuse_unconfirmed_config_reembed as _refuse_unconfirmed_config_reembed
from ._settings import _send_images_reach as _send_images_reach
from ._settings import _setup_state as _setup_state
from ._settings import get_campaign_play_decisions as get_campaign_play_decisions
from ._settings import get_config as get_config
from ._settings import get_play_decisions as get_play_decisions
from ._settings import get_prompt_layout as get_prompt_layout
from ._settings import put_campaign_play_decisions as put_campaign_play_decisions
from ._settings import put_config as put_config
from ._settings import put_prompt_layout as put_prompt_layout
from ._storage import _backups_body as _backups_body
from ._storage import get_backups as get_backups
from ._storage import get_data_dir as get_data_dir
from ._storage import get_store_conflicts as get_store_conflicts
from ._storage import post_backup as post_backup
from ._storage import post_image_backup as post_image_backup
from ._storage import put_data_dir as put_data_dir
from ._world_options import _climate_referrers as _climate_referrers
from ._world_options import delete_climate as delete_climate
from ._world_options import get_calendar_providers as get_calendar_providers
from ._world_options import get_calendar_year as get_calendar_year
from ._world_options import get_climate as get_climate
from ._world_options import get_climate_referrers as get_climate_referrers
from ._world_options import get_climates as get_climates
from ._world_options import get_entity_kinds as get_entity_kinds
from ._world_options import put_climate as put_climate

log = logging.getLogger('grimoire.routes.config')

_router.finalize()


def __getattr__(name: str):
    """Expose the current owned probe client after an internal replacement."""
    if name == "_EMBEDDINGS":
        return _probes._EMBEDDINGS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Captured from the original module before any responsibility leaves existed.
# Keep the original deferred module annotations at the compatibility boundary.
__annotations__ = {'GENERATING_CHECK_KINDS': 'frozenset[str]'}

__all__ = [
    'ADDRESS_FIXED',
    'FACTS_MANGLED',
    'FACTS_UNREADABLE',
    'GENERATING_CHECK_KINDS',
    'HEALTH_CHECK_CEILING',
    'HEALTH_UNCONFIRMED',
    'MODEL_FIELD_MOVED',
    'MODEL_TEST_CEILING',
    'NEWER_FORMAT',
    'TEST_UNCONFIRMED',
    'APIRouter',
    'BaseModel',
    'CampaignPlayDecisions',
    'CatalogProbe',
    'ConfigUpdate',
    'ConnectionCreate',
    'ConnectionUpdate',
    'DataDirUpdate',
    'Depends',
    'FactsUpdate',
    'HTTPException',
    'Header',
    'HealthCheck',
    'InferenceControlsBody',
    'LLMClient',
    'LLMError',
    'Literal',
    'ModelTestPreview',
    'ModelTestRun',
    'NamedTuple',
    'PromptLayoutUpdate',
    'Request',
    'ResponseSettings',
    'SamplerImportBody',
    'SamplerPresetBody',
    'StyleCreate',
    'StyleUpdate',
    'absorb_membership',
    'annotations',
    'asyncio',
    'cache_warm',
    'capabilities',
    'catalog',
    'close_clients',
    'compiled',
    'controls',
    'datetime',
    'decisions',
    'delete_climate',
    'delete_connection_route',
    'delete_sampler_preset',
    'delete_style',
    'embeddings',
    'facts',
    'get_backups',
    'get_calendar_providers',
    'get_calendar_year',
    'get_campaign_play_decisions',
    'get_climate',
    'get_climate_referrers',
    'get_climates',
    'get_config',
    'get_connection',
    'get_connection_capabilities',
    'get_connection_facts',
    'get_connections',
    'get_data_dir',
    'get_entity_kinds',
    'get_global_response',
    'get_health',
    'get_llm',
    'get_play_decisions',
    'get_prompt_layout',
    'get_provider_presets',
    'get_sampler_preset',
    'get_sampler_presets',
    'get_store_conflicts',
    'get_style',
    'get_styles',
    'group_refs',
    'health',
    'inference',
    'inference_migrate',
    'inference_settings',
    'legacy_refused',
    'library_paths',
    'limits',
    'llm',
    'llm_errors',
    'llm_sampling',
    'llm_usage',
    'log',
    'logging',
    'math',
    'operations',
    'post_backup',
    'post_connection',
    'post_connection_health',
    'post_connection_models_refresh',
    'post_connection_test',
    'post_connection_test_preview',
    'post_image_backup',
    'post_inference_controls',
    'post_model_catalog',
    'post_sampler_preset',
    'post_sampler_preset_import',
    'post_style',
    'post_style_duplicate',
    'providers',
    'put_campaign_play_decisions',
    'put_climate',
    'put_config',
    'put_connection',
    'put_connection_facts',
    'put_data_dir',
    'put_global_response',
    'put_prompt_layout',
    'put_sampler_preset',
    'put_style',
    'refuse_newer',
    'refuse_unmigrated',
    'require_inference',
    'retired',
    'router',
    'run_error',
    'runs',
    'store',
    'tool_calls',
    'unicodedata',
    'uuid',
    'wire',
]
