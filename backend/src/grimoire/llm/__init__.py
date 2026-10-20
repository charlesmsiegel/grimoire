"""Provider-agnostic LLM surface: the dispatch facade over the providers.

The shared error type lives in `llm_errors.py`, not here — see its docstring.
"""

from __future__ import annotations

import asyncio as asyncio
import contextlib as contextlib
import dataclasses as dataclasses
import functools as functools
import logging as logging
import queue as queue
import random as random
import re as re
import threading as threading
import time as time
import uuid as uuid
from collections.abc import AsyncIterator as AsyncIterator
from collections.abc import Sequence as Sequence
from concurrent.futures import Executor as Executor
from concurrent.futures import Future as Future
from typing import NamedTuple as NamedTuple

from .. import adapters as adapters
from .. import content_parts as content_parts
from .. import decisions as decisions
from .. import llm_capture as llm_capture
from .. import llm_errors as llm_errors
from .. import llm_reasoning as llm_reasoning
from .. import llm_sampling as llm_sampling
from .. import llm_usage as llm_usage
from .. import model_guidance as model_guidance
from .. import tool_calls as tool_calls
from .. import wire as wire
from ..anthropic import AnthropicClient as AnthropicClient
from ..claude_agent import ClaudeAgentClient as ClaudeAgentClient
from ..llm_errors import LLMError as LLMError
from ..openai_compatible import OpenAICompatibleClient as OpenAICompatibleClient
from ..openrouter import OpenRouterClient as OpenRouterClient
from ._accounting import (
    COUNT_TIMEOUT_S as COUNT_TIMEOUT_S,
)
from ._accounting import (
    _count_executor as _count_executor,
)
from ._accounting import (
    _count_stall as _count_stall,
)
from ._accounting import (
    _DaemonExecutor as _DaemonExecutor,
)
from ._accounting import (
    _estimate as _estimate,
)
from ._accounting import (
    _observe as _observe,
)
from ._accounting import (
    _Stall as _Stall,
)
from ._accounting import (
    _stamp as _stamp,
)
from ._client import (
    LLMClient as LLMClient,
)
from ._lifecycle import (
    _CLOSE_TIMEOUT as _CLOSE_TIMEOUT,
)
from ._lifecycle import (
    HEARTBEAT_INTERVAL as HEARTBEAT_INTERVAL,
)
from ._lifecycle import (
    _aclose as _aclose,
)
from ._lifecycle import (
    _close_when_settled as _close_when_settled,
)
from ._lifecycle import (
    _guard as _guard,
)
from ._lifecycle import (
    _settle as _settle,
)
from ._lifecycle import (
    _swallow as _swallow,
)
from ._refusals import (
    _CONTEXT_OVERFLOW as _CONTEXT_OVERFLOW,
)
from ._refusals import (
    _DISCRIMINATORS as _DISCRIMINATORS,
)
from ._refusals import (
    _TOOLS_NAMED as _TOOLS_NAMED,
)
from ._refusals import (
    PRESET_REFUSAL_STATUSES as PRESET_REFUSAL_STATUSES,
)
from ._refusals import (
    CapRefusalError as CapRefusalError,
)
from ._refusals import (
    PresetRefusalError as PresetRefusalError,
)
from ._refusals import (
    SchemaRefusalError as SchemaRefusalError,
)
from ._refusals import (
    ToolsRefusalError as ToolsRefusalError,
)
from ._refusals import (
    _call_capped as _call_capped,
)
from ._refusals import (
    _preset_refusal as _preset_refusal,
)
from ._refusals import (
    _said as _said,
)
from ._refusals import (
    _schema_refusal as _schema_refusal,
)
from ._refusals import (
    _structured_share as _structured_share,
)
from ._refusals import (
    _tools_refusal as _tools_refusal,
)
from ._refusals import (
    _wire_spellings as _wire_spellings,
)
from ._refusals import (
    routes_failed as routes_failed,
)
from ._retry import (
    RETRY_AFTER_CAP as RETRY_AFTER_CAP,
)
from ._retry import (
    RETRY_BASE as RETRY_BASE,
)
from ._retry import (
    RETRY_CAP as RETRY_CAP,
)
from ._retry import (
    RETRYABLE_KINDS as RETRYABLE_KINDS,
)
from ._retry import (
    _backoff_delay as _backoff_delay,
)
from ._retry import (
    _resilient as _resilient,
)
from ._routes import (
    ATTEMPTED as ATTEMPTED,
)
from ._routes import (
    CLAUDE_DEFAULT_MODEL as CLAUDE_DEFAULT_MODEL,
)
from ._routes import (
    DEFAULT_RETRIES as DEFAULT_RETRIES,
)
from ._routes import (
    DEFAULT_TIMEOUT as DEFAULT_TIMEOUT,
)
from ._routes import (
    LISTABLE_KINDS as LISTABLE_KINDS,
)
from ._routes import (
    MAX_OUTPUT_CAP as MAX_OUTPUT_CAP,
)
from ._routes import (
    NATIVE_REJECTED_STATUSES as NATIVE_REJECTED_STATUSES,
)
from ._routes import (
    REJECTED_STATUSES as REJECTED_STATUSES,
)
from ._routes import (
    ROUTE_SCOPES as ROUTE_SCOPES,
)
from ._routes import (
    TEXT_ONLY_KINDS as TEXT_ONLY_KINDS,
)
from ._routes import (
    _carries_parts as _carries_parts,
)
from ._routes import (
    _chain_of as _chain_of,
)
from ._routes import (
    _degraded as _degraded,
)
from ._routes import (
    _label as _label,
)
from ._routes import (
    _may_send_refs as _may_send_refs,
)
from ._routes import (
    _native_route as _native_route,
)
from ._routes import (
    _offer as _offer,
)
from ._routes import (
    _Route as _Route,
)
from ._routes import (
    _same_route as _same_route,
)
from ._routes import (
    _target_of as _target_of,
)
from ._routes import (
    _unflagged as _unflagged,
)
from ._routes import (
    _with_degrades as _with_degrades,
)
from ._routes import (
    clamp_to_max_output as clamp_to_max_output,
)
from ._routes import (
    effective_model as effective_model,
)
from ._routes import (
    fallback_sampling as fallback_sampling,
)

# Explicit aliases preserve the former module surface; implementation lives in its owners.
from ._routes import (
    log as log,
)
from ._routes import (
    native_body as native_body,
)
from ._routes import (
    prefill_capable as prefill_capable,
)

__all__ = [
    'ATTEMPTED',
    'CLAUDE_DEFAULT_MODEL',
    'COUNT_TIMEOUT_S',
    'DEFAULT_RETRIES',
    'DEFAULT_TIMEOUT',
    'HEARTBEAT_INTERVAL',
    'LISTABLE_KINDS',
    'MAX_OUTPUT_CAP',
    'NATIVE_REJECTED_STATUSES',
    'PRESET_REFUSAL_STATUSES',
    'REJECTED_STATUSES',
    'RETRYABLE_KINDS',
    'RETRY_AFTER_CAP',
    'RETRY_BASE',
    'RETRY_CAP',
    'ROUTE_SCOPES',
    'TEXT_ONLY_KINDS',
    'AnthropicClient',
    'AsyncIterator',
    'CapRefusalError',
    'ClaudeAgentClient',
    'Executor',
    'Future',
    'LLMClient',
    'LLMError',
    'NamedTuple',
    'OpenAICompatibleClient',
    'OpenRouterClient',
    'PresetRefusalError',
    'SchemaRefusalError',
    'Sequence',
    'ToolsRefusalError',
    'adapters',
    'annotations',
    'asyncio',
    'clamp_to_max_output',
    'content_parts',
    'contextlib',
    'dataclasses',
    'decisions',
    'effective_model',
    'fallback_sampling',
    'functools',
    'llm_capture',
    'llm_errors',
    'llm_reasoning',
    'llm_sampling',
    'llm_usage',
    'log',
    'logging',
    'model_guidance',
    'native_body',
    'prefill_capable',
    'queue',
    'random',
    're',
    'routes_failed',
    'threading',
    'time',
    'tool_calls',
    'uuid',
    'wire',
]
