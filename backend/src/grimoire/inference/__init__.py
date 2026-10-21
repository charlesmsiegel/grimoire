"""The inference operations a call site asks for by name: `generate` (spec 7.2)
and `decide` (spec 7.4).

A generation is free text, streamed or joined. `generate` is the only door
to it: it takes the resolution a call site already made (`require_inference`
or `override_inference`), refuses one made for another task or operation, or
one that resolved nothing, before any client call, and hands the facade
`resolved.chain`, the resolution's typed targets. `test_routing_guard.py`
holds that `client.stream`/`complete` are spelled only in this module; the
caller's meter, and the holder it hands in as `usage=`, stay at the call
site, where `test_usage_guard.py` reads them. With a `schema` (01f), the
facade is sent a per-call chain instead (`_structured_chain`: structured mode
on each attempt known to take it), the prompt must carry the schema, and a
route that refused the field is re-sent once without it
(`_without_refused_mode`, which `decide` shares).

A decision is a closed question answered from a set the caller fixed in
advance (`grimoire.decisions` holds that contract). `decide` is the operation:
it takes a resolution the call site already made -- `require_inference(task,
cid, operation="decide")`, so the seam's 409 and absorb's soft resolution come
before it -- and answers through a chain of stages (`stages`, spec 5.5): the
selection's one backend, then the role fallback's in one attempt. Two
backends: `structured` (slice F), `generate` (`LLMClient.complete(...,
schema=)`) over a prompt that carries the schema, read back by
`decisions.parse`; and `native` (slice H), a provider's own decisions endpoint
(`LLMClient.decide_native`), one request per item. An item moves to the next
stage only when its stage FAILED to answer it (`run_stages`). A task whose
code policy lists the primary's kind (`routing.TaskPolicy.native_first`, 01c)
asks a generating primary's decisions endpoint first, in an isolated stage
of its own (`stages`, `resolve.native_first`).

Four rules `decide` keeps:

- **One meter per call, opened here** (ruling 9): per structured chunk, and
  per native item. A caller's time budget runs inside it through `around`, so
  an overrun is filed as an `error/timeout` row exactly as a budgeted
  `complete` is today, and `usage.Meter.done` stays the one place an LLM
  failure is recorded. `test_usage_guard.py` scans this file. A caller's
  capture (`Capture`) is handed each call once it settles, outside its
  meter and guarded, so a capture never turns an answered call into an
  `error` row.
- **Nothing is written into the holder.** The operation and the decision mode
  reach the ledger through each target's account (`wire.Account`), which
  `llm._stamp` files: the operation from the resolution, the mode stamped here,
  per call, on new targets (`_with_mode`, `wire.Chain.with_account`) -- a
  resolution's targets are frozen, and `stages` builds the chains it sends
  (`wire.Chain.alone`) rather than changing the resolution's.
- **Provider errors propagate.** A failed call's `LLMError` is re-raised when
  no item answered, composed with the later stages' failures
  (`llm.routes_failed`) -- an isolated (native-first) stage's failure is not
  composed into the error of an item a later stage took; once one has, a failed unit's items are `None` with
  reason `error`, and `Decision.errors` holds each unit's final error.
  Cancellation passes through untouched (every open meter files `aborted`).
  One error is not yet the chunk's: a chain that failed on every route, one
  of them by refusing the structured field (`llm.SchemaRefusalError`), is
  answered by sending each refusing attempt once more without the mode, as
  its own metered call.
- **Prompt text lives in `templates/decide/`**, rendered here -- in a worker
  thread, never on the event loop. Jinja's loader stats (and on first use
  reads) the template files, and `decide` is awaited by a detached turn (the
  speaker pick), whose loop must not block on file I/O; the render ran in the
  threadpool before the switch, and it still does. A native item's capture
  (its request body, JSON-encoded) is built in a worker thread too.

A policy-declared escalation (roadmap 01d, `decide(escalation=)`) is not a
stage: after the unchanged chain has answered, an ANSWERED item whose own
report says it is unsure (`decisions.triggers`) is handed once to a stronger
resolver -- the escalation role's primary alone, or a caller's `Resolver` --
and a hop that fails, garbles or declines leaves the original answer
standing. One hop, never to the model that answered, recorded in
`Decision.escalations`; `Decision.errors` stays the chain's.

Nothing in `store/` or the gateway imports this module.
"""

from __future__ import annotations

import asyncio as asyncio
import json as json
import logging as logging
import math as math
import time as time
import weakref as weakref
from collections.abc import AsyncGenerator as AsyncGenerator
from collections.abc import AsyncIterator as AsyncIterator
from collections.abc import Awaitable as Awaitable
from collections.abc import Callable as Callable
from collections.abc import Iterator as Iterator
from collections.abc import Sequence as Sequence
from contextlib import aclosing as aclosing
from contextlib import contextmanager as contextmanager
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from functools import partial as partial
from typing import Any as Any
from typing import Literal as Literal
from typing import NamedTuple as NamedTuple
from typing import TypeVar as TypeVar
from typing import overload as overload

from .. import claude_agent as claude_agent
from .. import deadline as deadline
from .. import decisions as decisions
from .. import llm as llm
from .. import llm_errors as llm_errors
from .. import llm_reasoning as llm_reasoning
from .. import llm_sampling as llm_sampling
from .. import model_guidance as model_guidance
from .. import prompts as prompts
from .. import schemas as schemas
from .. import store as store
from .. import tool_calls as tool_calls
from .. import wire as wire
from ..llm import LLMClient as LLMClient
from ..llm_errors import LLMError as LLMError
from ..store.inference import probes as probes
from ..store.inference import resolve as resolve
from ..store.inference.resolved import Attempt as Attempt
from ..store.inference.resolved import ResolvedInference as ResolvedInference
from ._contracts import (
    NATIVE as NATIVE,
)
from ._contracts import (
    STRUCTURED as STRUCTURED,
)
from ._contracts import (
    Around as Around,
)
from ._contracts import (
    Backend as Backend,
)
from ._contracts import (
    Capture as Capture,
)
from ._contracts import (
    Escalator as Escalator,
)
from ._contracts import (
    LoopOptions as LoopOptions,
)
from ._contracts import (
    Outcome as Outcome,
)
from ._contracts import (
    Resolver as Resolver,
)
from ._contracts import (
    Stage as Stage,
)
from ._contracts import (
    TurnCapture as TurnCapture,
)
from ._contracts import (
    _Answered as _Answered,
)
from ._contracts import (
    _Call as _Call,
)

# Explicit aliases preserve the former module surface; implementation lives in its owners.
from ._contracts import (
    log as log,
)
from ._decision import (
    _BACKENDS as _BACKENDS,
)
from ._decision import (
    _all_dead as _all_dead,
)
from ._decision import (
    _caller_hop as _caller_hop,
)
from ._decision import (
    _capped_stage as _capped_stage,
)
from ._decision import (
    _checked_reply as _checked_reply,
)
from ._decision import (
    _checked_result as _checked_result,
)
from ._decision import (
    _escalate as _escalate,
)
from ._decision import (
    _escalated as _escalated,
)
from ._decision import (
    _final as _final,
)
from ._decision import (
    _hop_stage as _hop_stage,
)
from ._decision import (
    _HopDone as _HopDone,
)
from ._decision import (
    _merge as _merge,
)
from ._decision import (
    _on_dead_connection as _on_dead_connection,
)
from ._decision import (
    _policy_for as _policy_for,
)
from ._decision import (
    _role_hop as _role_hop,
)
from ._decision import (
    _row_record as _row_record,
)
from ._decision import (
    _same_connection as _same_connection,
)
from ._decision import (
    _settle as _settle,
)
from ._decision import (
    _settle_one as _settle_one,
)
from ._decision import (
    _Settled as _Settled,
)
from ._decision import (
    _skipped as _skipped,
)
from ._decision import (
    _take as _take,
)
from ._decision import (
    decide as decide,
)
from ._decision import (
    reports_distribution as reports_distribution,
)
from ._decision import (
    run_stages as run_stages,
)
from ._decision import (
    stages as stages,
)
from ._decision_calls import (
    NATIVE_CONCURRENCY as NATIVE_CONCURRENCY,
)
from ._decision_calls import (
    NATIVE_TIMEOUT_STOP as NATIVE_TIMEOUT_STOP,
)
from ._decision_calls import (
    _ask as _ask,
)
from ._decision_calls import (
    _captured as _captured,
)
from ._decision_calls import (
    _connection_wide as _connection_wide,
)
from ._decision_calls import (
    _meter as _meter,
)
from ._decision_calls import (
    _native as _native,
)
from ._decision_calls import (
    _native_request as _native_request,
)
from ._decision_calls import (
    _once as _once,
)
from ._decision_calls import (
    _outcome as _outcome,
)
from ._decision_calls import (
    _record as _record,
)
from ._decision_calls import (
    _refused_unsent as _refused_unsent,
)
from ._decision_calls import (
    _Reply as _Reply,
)
from ._decision_calls import (
    _served_by as _served_by,
)
from ._decision_calls import (
    _server as _server,
)
from ._decision_calls import (
    _stops_chunks as _stops_chunks,
)
from ._decision_calls import (
    _structured as _structured,
)
from ._decision_calls import (
    _with_mode as _with_mode,
)
from ._decision_calls import (
    structured_messages as structured_messages,
)
from ._generation import (
    for_task as for_task,
)
from ._generation import (
    generate as generate,
)
from ._generation import (
    note_outcome as note_outcome,
)
from ._pricing import (
    Price as Price,
)
from ._pricing import (
    _cost as _cost,
)
from ._pricing import (
    _prompt_count as _prompt_count,
)
from ._pricing import (
    decide_projection as decide_projection,
)
from ._pricing import (
    price_for as price_for,
)
from ._pricing import (
    prices_for as prices_for,
)
from ._pricing import (
    rows_cost as rows_cost,
)
from ._pricing import (
    tool_run_refusal as tool_run_refusal,
)
from ._requests import (
    MAX_OUTPUT_CAP as MAX_OUTPUT_CAP,
)
from ._requests import (
    T as T,
)
from ._requests import (
    _attempts_made as _attempts_made,
)
from ._requests import (
    _capped as _capped,
)
from ._requests import (
    _check_cap as _check_cap,
)
from ._requests import (
    _check_structured as _check_structured,
)
from ._requests import (
    _counting_earlier as _counting_earlier,
)
from ._requests import (
    _generating as _generating,
)
from ._requests import (
    _joined_structured as _joined_structured,
)
from ._requests import (
    _resends as _resends,
)
from ._requests import (
    _stream_without_refused_mode as _stream_without_refused_mode,
)
from ._requests import (
    _streamed_structured as _streamed_structured,
)
from ._requests import (
    _structured_chain as _structured_chain,
)
from ._requests import (
    _texts as _texts,
)
from ._requests import (
    _tool_chain as _tool_chain,
)
from ._requests import (
    _without_mode as _without_mode,
)
from ._requests import (
    _without_refused_mode as _without_refused_mode,
)
from ._requests import (
    call_chain as call_chain,
)
from ._requests import (
    cap_sent as cap_sent,
)
from ._requests import (
    clamp_to_max_output as clamp_to_max_output,
)
from ._tool_loops import (
    _loop_events as _loop_events,
)
from ._tool_loops import (
    run_tools as run_tools,
)
from ._tool_loops import (
    stream_tools as stream_tools,
)
from ._tool_policy import (
    _LOOPING as _LOOPING,
)
from ._tool_policy import (
    WallSpentError as WallSpentError,
)
from ._tool_policy import (
    _check_loop_prompt as _check_loop_prompt,
)
from ._tool_policy import (
    _check_loop_route as _check_loop_route,
)
from ._tool_policy import (
    _loop_refusal as _loop_refusal,
)
from ._tool_policy import (
    _ToolTimeoutError as _ToolTimeoutError,
)
from ._tool_policy import (
    finalize_message as finalize_message,
)
from ._tool_runtime import (
    _DRIVER_UNWIND_S as _DRIVER_UNWIND_S,
)
from ._tool_runtime import (
    _Loop as _Loop,
)
from ._tool_runtime import (
    _next_or_beat as _next_or_beat,
)
from ._tool_runtime import (
    _RunView as _RunView,
)
from ._tool_runtime import (
    _stopped as _stopped,
)
from ._tool_runtime import (
    _Turn as _Turn,
)

# Preserve deferred module annotations; values and their types have one owner.
__annotations__ = {
    '_BACKENDS': 'dict[str, Backend]',
    '_LOOPING': 'weakref.WeakSet[asyncio.Task]',
}


__all__ = [
    'MAX_OUTPUT_CAP',
    'NATIVE',
    'NATIVE_CONCURRENCY',
    'NATIVE_TIMEOUT_STOP',
    'STRUCTURED',
    'Any',
    'Around',
    'AsyncGenerator',
    'AsyncIterator',
    'Attempt',
    'Awaitable',
    'Backend',
    'Callable',
    'Capture',
    'Escalator',
    'Iterator',
    'LLMClient',
    'LLMError',
    'Literal',
    'LoopOptions',
    'NamedTuple',
    'Outcome',
    'Price',
    'ResolvedInference',
    'Resolver',
    'Sequence',
    'Stage',
    'T',
    'TurnCapture',
    'TypeVar',
    'WallSpentError',
    'aclosing',
    'annotations',
    'asyncio',
    'call_chain',
    'cap_sent',
    'clamp_to_max_output',
    'claude_agent',
    'contextmanager',
    'dataclass',
    'deadline',
    'decide',
    'decide_projection',
    'decisions',
    'finalize_message',
    'for_task',
    'generate',
    'json',
    'llm',
    'llm_errors',
    'llm_reasoning',
    'llm_sampling',
    'log',
    'logging',
    'math',
    'model_guidance',
    'note_outcome',
    'overload',
    'partial',
    'price_for',
    'prices_for',
    'probes',
    'prompts',
    'replace',
    'reports_distribution',
    'resolve',
    'rows_cost',
    'run_stages',
    'run_tools',
    'schemas',
    'stages',
    'store',
    'stream_tools',
    'structured_messages',
    'time',
    'tool_calls',
    'tool_run_refusal',
    'weakref',
    'wire',
]
