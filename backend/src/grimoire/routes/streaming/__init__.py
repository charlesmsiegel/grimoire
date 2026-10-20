"""SSE framing, the persisted-turn strategies, and the roll-proposal
machinery they share.

Crash-window disclosure (accepted risk, per the phase-4 spec). The fence
handoffs are serialized against concurrent writers by the per-campaign
proposals lock, but are NOT crash-atomic across files (proposals.json and
the scene transcript are separate writes; grimoire is a local single-process
app with no cross-file journal). Two microsecond-wide windows exist, both
bounded and non-corrupting:

  - initial fence: a crash between writing the pending record and persisting
    the pre-fence narration leaves a recoverable chip whose last narration
    beat is missing — the player can still adjudicate or decline;
  - follow-up fence: a crash between the old record's `narrated` write and
    the new pending record leaves the continuation fully persisted and the
    follow-up check simply lost; play continues on the next send.

The guaranteed invariant: no roll is ever duplicated or lost once logged, no
narration is attributed to a superseded decision, and no crash leaves an
unrecoverable or corrupted state. Full journaling was rejected as
disproportionate for a local single-user store.

This module holds no routes; ``scenes``, ``mechanics`` and ``greetings``
import from it.

A note for tests that patch the helpers below: importers bind them by value
(``from .streaming import _persist_reply``), so patch the module where the name
is *looked up*, not necessarily where it is defined. Patching
``routes.streaming._persist_reply`` covers the turn strategies here but not
``greetings.post_first_post``, which holds its own reference.
"""

from __future__ import annotations

import itertools
import json
import logging
import time
import uuid

import anyio
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ... import inference as operations
from ... import store, wire
from ...llm import ATTEMPTED, LLMClient
from ...llm_errors import LLMError
from ...store import overview
from ._display import _asked as _asked
from ._display import _Display as _Display
from ._display import _display_stream as _display_stream
from ._display import _served as _served
from ._display import _visible_frames as _visible_frames
from ._followups import _fire_follow_up as _fire_follow_up
from ._persistence import _FENCE_READS as _FENCE_READS
from ._persistence import _FENCE_RETRY_SECONDS as _FENCE_RETRY_SECONDS
from ._persistence import _MOVED as _MOVED
from ._persistence import DIRECTOR as DIRECTOR
from ._persistence import _active_variant as _active_variant
from ._persistence import _answering_post as _answering_post
from ._persistence import _carry_rewrite as _carry_rewrite
from ._persistence import _continuation_post as _continuation_post
from ._persistence import _is_director_note as _is_director_note
from ._persistence import _make_proposal as _make_proposal
from ._persistence import _narration as _narration
from ._persistence import _persist_reply as _persist_reply
from ._persistence import _prune_rewrites as _prune_rewrites
from ._persistence import _record_rewrite as _record_rewrite
from ._persistence import _scene_moved as _scene_moved
from ._persistence import _store_phase as _store_phase
from ._persistence import _tail_length as _tail_length
from ._persistence import _turn_settled as _turn_settled
from ._persistence import _would_land as _would_land
from ._state import _HEARTBEAT as _HEARTBEAT
from ._state import HEARTBEAT_GAP as HEARTBEAT_GAP
from ._state import StreamOutcome as StreamOutcome
from ._state import _claim_turn as _claim_turn

# Explicit compatibility aliases retain the original binding and star surface.
from ._state import _Liveness as _Liveness
from ._state import _owns_turn as _owns_turn
from ._state import _sse as _sse
from ._state import _turn_seq as _turn_seq
from ._state import _turn_tokens as _turn_tokens
from ._streams import _chat_stream as _chat_stream
from ._streams import _continuation_stream as _continuation_stream
from ._streams import _fence_stream as _fence_stream
from ._streams import _flush_on_abort as _flush_on_abort

_log = logging.getLogger('grimoire.routes.streaming')

# Captured from the original module before any responsibility leaves existed.
# Keep the original deferred module annotations at the compatibility boundary.
__annotations__ = {'_turn_tokens': 'dict[tuple[str, str], int]'}

__all__ = [
    'ATTEMPTED',
    'DIRECTOR',
    'HEARTBEAT_GAP',
    'LLMClient',
    'LLMError',
    'StreamOutcome',
    'StreamingResponse',
    'annotations',
    'anyio',
    'itertools',
    'json',
    'logging',
    'operations',
    'overview',
    'run_in_threadpool',
    'store',
    'time',
    'uuid',
    'wire',
]
