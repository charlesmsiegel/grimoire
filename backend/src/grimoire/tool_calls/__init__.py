"""Tool calling's provider-neutral half (spec 01g): the shapes a caller
declares its tools in, how a tool definition and a tool turn are spelled per
provider kind, and what calls a reply made.

A gateway leaf, standard library plus `schemas` (itself a stdlib-only leaf),
so the adapters, the facade and -- later -- the store can all import it, as
they import `wire`.

01g-S1 landed what the `tools` capability's probe needs (the neutral
definition and its check, each kind's spelling of a definition and of
`tool_choice`, and call detection); 01g-S2 grew it in place with the
`ToolSpec` / `Toolset` shapes, `conforms`, argument accumulation, opaque
provider state and the tool-turn message lowering. The loop that uses them
is 01g-S4's.

**A definition** is `{"name", "description", "parameters"}`, `parameters`
inside the portable schema subset (`schemas.check`, spec 3.2). **A choice**
is one of `CHOICES`, OpenAI's own spelling: `auto` (the model may call),
`none` (it may not) and `required` (it must). Nothing here decides which
choice a caller asks: an adapter lowers what it is handed, and never
rewrites `required` (the loop's downgrade beside thinking is 01g-S4's).

**Two neutral message shapes** carry a tool history (spec 3.2):

    {"role": "assistant", "content": "<text, may be ''>",
     "tool_calls": [{"id": ..., "name": ..., "arguments": {...}}],
     "_opaque": {"kind", "provider_id", "model", "items": [...]}}   # optional
    {"role": "tool", "tool_call_id": ..., "name": ..., "content": "<text>",
     "is_error": False}

Nothing above the adapters spells a provider's format. `provenanced` drops
an `_opaque` that another `(kind, provider_id, model)` wrote, and the
per-kind lowering (`openai_messages`, the `anthropic_*` blocks) turns the
rest into the wire's own. Every lowering hands back the SAME list when no
message carries a tool turn, so a call without tools is the call it always
was, byte for byte.

**The `Collector`** rides the usage holder under `KEY`, as the reasoning
buffer does (`llm_reasoning.Buffer`): installed by the caller before the
facade call, kept and reset by `llm._stamp` at each attempt -- which also
tells it the attempt's provenance -- so a retry starts with nothing, and fed
by the adapters' stream readers. The feeders answer the text they consumed
(names and argument fragments), which the adapters note for the local token
estimate: arguments are billed completion. A holder without a collector is
never written.
"""

from __future__ import annotations

import asyncio as asyncio
import inspect as inspect
import json as json
import math as math
import re as re
import threading as threading
from collections.abc import AsyncIterator as AsyncIterator
from collections.abc import Awaitable as Awaitable
from collections.abc import Callable as Callable
from concurrent.futures import Future as Future
from concurrent.futures import ThreadPoolExecutor as ThreadPoolExecutor
from dataclasses import dataclass as dataclass
from typing import Literal as Literal
from typing import Protocol as Protocol

from .. import schemas as schemas
from ._definitions import (
    _TYPE_WORDS as _TYPE_WORDS,
)
from ._definitions import (
    CHOICES as CHOICES,
)
from ._definitions import (
    ERROR_PREFIX as ERROR_PREFIX,
)
from ._definitions import (
    FINISHES as FINISHES,
)

# Explicit aliases preserve the former module surface; implementation lives in its owners.
from ._definitions import (
    KEY as KEY,
)
from ._definitions import (
    MAX_DESCRIPTION as MAX_DESCRIPTION,
)
from ._definitions import (
    MAX_RESULT_CHARS as MAX_RESULT_CHARS,
)
from ._definitions import (
    MAX_TOOLS as MAX_TOOLS,
)
from ._definitions import (
    NAME as NAME,
)
from ._definitions import (
    REFUSED as REFUSED,
)
from ._definitions import (
    TOOL_TIMEOUT_S as TOOL_TIMEOUT_S,
)
from ._definitions import (
    Execute as Execute,
)
from ._definitions import (
    RunView as RunView,
)
from ._definitions import (
    ToolCall as ToolCall,
)
from ._definitions import (
    ToolContext as ToolContext,
)
from ._definitions import (
    ToolError as ToolError,
)
from ._definitions import (
    ToolFn as ToolFn,
)
from ._definitions import (
    ToolOutput as ToolOutput,
)
from ._definitions import (
    Toolset as Toolset,
)
from ._definitions import (
    ToolSpec as ToolSpec,
)
from ._definitions import (
    _checked_name as _checked_name,
)
from ._definitions import (
    _checked_spec as _checked_spec,
)
from ._definitions import (
    _object_violation as _object_violation,
)
from ._definitions import (
    _typed as _typed,
)
from ._definitions import (
    anthropic_choice as anthropic_choice,
)
from ._definitions import (
    anthropic_tools as anthropic_tools,
)
from ._definitions import (
    check as check,
)
from ._definitions import (
    conforms as conforms,
)
from ._definitions import (
    openai_tools as openai_tools,
)
from ._definitions import (
    violation as violation,
)
from ._messages import (
    _ANTHROPIC_FINISH as _ANTHROPIC_FINISH,
)
from ._messages import (
    _DETAIL_TEXT as _DETAIL_TEXT,
)
from ._messages import (
    _OPENAI_FINISH as _OPENAI_FINISH,
)
from ._messages import (
    _THINKING as _THINKING,
)
from ._messages import (
    Collector as Collector,
)
from ._messages import (
    _arguments as _arguments,
)
from ._messages import (
    _collector as _collector,
)
from ._messages import (
    _index as _index,
)
from ._messages import (
    _items as _items,
)
from ._messages import (
    _parsed as _parsed,
)
from ._messages import (
    _result_text as _result_text,
)
from ._messages import (
    _same_source as _same_source,
)
from ._messages import (
    _text as _text,
)
from ._messages import (
    _tool_turn as _tool_turn,
)
from ._messages import (
    anthropic_assistant as anthropic_assistant,
)
from ._messages import (
    anthropic_result as anthropic_result,
)
from ._messages import (
    anthropic_stop as anthropic_stop,
)
from ._messages import (
    carries_tool_turns as carries_tool_turns,
)
from ._messages import (
    feed_anthropic as feed_anthropic,
)
from ._messages import (
    from_openai_chunk as from_openai_chunk,
)
from ._messages import (
    openai_call as openai_call,
)
from ._messages import (
    openai_messages as openai_messages,
)
from ._messages import (
    provenanced as provenanced,
)
from ._runs import (
    _RUNNING as _RUNNING,
)
from ._runs import (
    CAPACITY as CAPACITY,
)
from ._runs import (
    CUT_OFF as CUT_OFF,
)
from ._runs import (
    LIMITS as LIMITS,
)
from ._runs import (
    MIN_TURN_SECONDS as MIN_TURN_SECONDS,
)
from ._runs import (
    NOT_RUN as NOT_RUN,
)
from ._runs import (
    PROJECTION_MARGIN as PROJECTION_MARGIN,
)
from ._runs import (
    SAFE_ID as SAFE_ID,
)
from ._runs import (
    TIMED_OUT as TIMED_OUT,
)
from ._runs import (
    TOOL_FAILED as TOOL_FAILED,
)
from ._runs import (
    TOOL_WORKERS as TOOL_WORKERS,
)
from ._runs import (
    LoopEvent as LoopEvent,
)
from ._runs import (
    LoopResult as LoopResult,
)
from ._runs import (
    Registered as Registered,
)
from ._runs import (
    RunBudget as RunBudget,
)
from ._runs import (
    RunRefused as RunRefused,
)
from ._runs import (
    TraceEntry as TraceEntry,
)
from ._runs import (
    _cut_marker as _cut_marker,
)
from ._runs import (
    _positive_int as _positive_int,
)
from ._runs import (
    _Running as _Running,
)
from ._runs import (
    _Workers as _Workers,
)
from ._runs import (
    idle as idle,
)
from ._runs import (
    loop_id as loop_id,
)
from ._runs import (
    registered as registered,
)
from ._runs import (
    rewrite_ids as rewrite_ids,
)
from ._runs import (
    text_deltas as text_deltas,
)
from ._runs import (
    truncate as truncate,
)

# Preserve deferred module annotations; values and their types have one owner.
__annotations__ = {
    'CHOICES': 'tuple[str, ...]',
    'FINISHES': 'tuple[str, ...]',
    'LIMITS': 'tuple[str, ...]',
}


__all__ = [
    'CAPACITY',
    'CHOICES',
    'CUT_OFF',
    'ERROR_PREFIX',
    'FINISHES',
    'KEY',
    'LIMITS',
    'MAX_DESCRIPTION',
    'MAX_RESULT_CHARS',
    'MAX_TOOLS',
    'MIN_TURN_SECONDS',
    'NAME',
    'NOT_RUN',
    'PROJECTION_MARGIN',
    'REFUSED',
    'SAFE_ID',
    'TIMED_OUT',
    'TOOL_FAILED',
    'TOOL_TIMEOUT_S',
    'TOOL_WORKERS',
    'AsyncIterator',
    'Awaitable',
    'Callable',
    'Collector',
    'Execute',
    'Future',
    'Literal',
    'LoopEvent',
    'LoopResult',
    'Protocol',
    'Registered',
    'RunBudget',
    'RunRefused',
    'RunView',
    'ThreadPoolExecutor',
    'ToolCall',
    'ToolContext',
    'ToolError',
    'ToolFn',
    'ToolOutput',
    'ToolSpec',
    'Toolset',
    'TraceEntry',
    'annotations',
    'anthropic_assistant',
    'anthropic_choice',
    'anthropic_result',
    'anthropic_stop',
    'anthropic_tools',
    'asyncio',
    'carries_tool_turns',
    'check',
    'conforms',
    'dataclass',
    'feed_anthropic',
    'from_openai_chunk',
    'idle',
    'inspect',
    'json',
    'loop_id',
    'math',
    'openai_call',
    'openai_messages',
    'openai_tools',
    'provenanced',
    're',
    'registered',
    'rewrite_ids',
    'schemas',
    'text_deltas',
    'threading',
    'truncate',
    'violation',
]
