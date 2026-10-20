"""Eager rebuild of what was hot, after an edit (spec 05, 05-C1 and 05-C3).

Spec 03's compiled cache keys every artifact on the bytes a reader just read,
so nothing superseded is reachable and a forgotten sync costs a cold read,
never a wrong one (03-C2). What this module adds is *time*: the rebuild the
next read would have done lazily is done now, off the read path. It is a
warm-up, never a correctness step, and it owns no table and no key of its own.

`sync_paths` takes store-relative paths and, for one batch:

1. validates each as a path inside the store (`_check`, spec section 8);
2. classifies it: refused, `missing`, an intended `deleted`, `cold` (nothing
   materialized from it, for every hook) or hot;
3. bumps the write token of every campaign a valid path belongs to, before
   any rebuild, so a slow or cancelled batch has still told the app about the
   edit (section 10; `explicit` mode only, never a dry run);
4. runs each hook's local phase in `HOOKS` order, then each network phase;
5. reports, in counts and kind names and never in record content (section 9).

"Hot" is 03's `materialized` record and nothing else: a path is hot for a kind
when a `(path, kind)` row exists. A hook only ever sees its own kinds' rows, and
a kind no hook owns is reported `lazy`. Rows are read only for paths this batch
already holds -- from its arguments or a live listing -- never by enumeration.

The module takes no campaign lock and writes no record except the write token
(`revision.bump`, already classified outside the lock domain), which is why it
has no entry in `store/locks.py`. It is synchronous and does file and network
I/O, so it is never called on the event loop; callers reach it through a
shielded `anyio.to_thread.run_sync`. One batch runs at a time per process (the
*sync lane*); across processes two batches may compute the same artifact,
which content addressing makes idempotent.

The root is read once (`paths.home()` unless pinned), and everything this
module builds is built from it. The functions it calls resolve `home()` per
call, so the batch re-checks `paths.home() == root` between paths, hooks and
chunks and stops with `root_moved` if it differs; the server also refuses a
data-dir move while a batch runs (the sync hold, `routes/runs.py`).
"""

from __future__ import annotations

import contextlib as contextlib
import contextvars as contextvars
import hashlib as hashlib
import json as json
import logging as logging
import os as os
import stat as stat
import threading as threading
from collections.abc import Callable as Callable
from collections.abc import Container as Container
from collections.abc import Iterable as Iterable
from collections.abc import Iterator as Iterator
from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from pathlib import Path as Path
from pathlib import PurePosixPath as PurePosixPath
from typing import Literal as Literal
from typing import Protocol as Protocol

from ... import embeddings as embeddings
from ...llm_errors import LLMError as LLMError
from .. import atomic as atomic
from .. import compiled as compiled
from .. import embed_space as embed_space
from .. import paths as paths
from .. import revision as revision
from .. import semsearch as semsearch
from .. import vectors as vectors
from .. import writeset as writeset
from ..context import art as art
from ..context import layout as layout
from ..context import semantic as semantic
from ..inference import embed as embed
from ..overview import warm as overview_warm
from ..searchdocs import hook as searchdocs_hook
from ._contracts import (
    _ORDER as _ORDER,
)
from ._contracts import (
    BATCH_FIELDS as BATCH_FIELDS,
)
from ._contracts import (
    EMBEDDING_FIELDS as EMBEDDING_FIELDS,
)
from ._contracts import (
    KIND_OUTCOMES as KIND_OUTCOMES,
)
from ._contracts import (
    MAX_PATHS as MAX_PATHS,
)
from ._contracts import (
    MAX_SCOPE_FILES as MAX_SCOPE_FILES,
)
from ._contracts import (
    MODES as MODES,
)
from ._contracts import (
    PATH_STATUSES as PATH_STATUSES,
)
from ._contracts import (
    REFUSAL_REASONS as REFUSAL_REASONS,
)
from ._contracts import (
    REPORT_VERSION as REPORT_VERSION,
)
from ._contracts import (
    STOP_REASONS as STOP_REASONS,
)
from ._contracts import (
    SYNCABLE_ROOTS as SYNCABLE_ROOTS,
)
from ._contracts import (
    Collected as Collected,
)
from ._contracts import (
    LocalResult as LocalResult,
)
from ._contracts import (
    NetworkResult as NetworkResult,
)
from ._contracts import (
    PathReport as PathReport,
)
from ._contracts import (
    ScopeResult as ScopeResult,
)
from ._contracts import (
    SyncReport as SyncReport,
)
from ._contracts import (
    WarmContext as WarmContext,
)
from ._contracts import (
    WarmHook as WarmHook,
)
from ._contracts import (
    _canonical as _canonical,
)
from ._contracts import (
    _fold as _fold,
)
from ._contracts import (
    _instance as _instance,
)
from ._contracts import (
    _worse as _worse,
)
from ._engine import (
    _ROOT_SCOPES as _ROOT_SCOPES,
)
from ._engine import (
    HOOKS as HOOKS,
)
from ._engine import (
    _add_embedding as _add_embedding,
)
from ._engine import (
    _bump as _bump,
)
from ._engine import (
    _classify_arguments as _classify_arguments,
)
from ._engine import (
    _classify_hot as _classify_hot,
)
from ._engine import (
    _Collector as _Collector,
)
from ._engine import (
    _context as _context,
)
from ._engine import (
    _copy_renames as _copy_renames,
)
from ._engine import (
    _find_hot as _find_hot,
)
from ._engine import (
    _local_phase as _local_phase,
)
from ._engine import (
    _local_phases as _local_phases,
)
from ._engine import (
    _mark_lazy as _mark_lazy,
)
from ._engine import (
    _merge as _merge,
)
from ._engine import (
    _network_phases as _network_phases,
)
from ._engine import (
    _progress as _progress,
)
from ._engine import (
    _purge_roots as _purge_roots,
)
from ._engine import (
    _remapped as _remapped,
)
from ._engine import (
    _remapper as _remapper,
)
from ._engine import (
    _rows as _rows,
)
from ._engine import (
    _run_hooks as _run_hooks,
)
from ._engine import (
    _settle_paths as _settle_paths,
)
from ._engine import (
    _status as _status,
)
from ._engine import (
    sync_paths as sync_paths,
)
from ._files import (
    FILE_RECIPES as FILE_RECIPES,
)
from ._files import (
    FILES as FILES,
)
from ._files import (
    FileRecipe as FileRecipe,
)
from ._files import (
    _FilesHook as _FilesHook,
)
from ._files import (
    _fresh as _fresh,
)
from ._files import (
    derive_file as derive_file,
)
from ._other_hooks import (
    _OVERVIEW_OUTCOMES as _OVERVIEW_OUTCOMES,
)
from ._other_hooks import (
    OVERVIEW as OVERVIEW,
)
from ._other_hooks import (
    SEARCHDOCS as SEARCHDOCS,
)
from ._other_hooks import (
    _OverviewHook as _OverviewHook,
)
from ._other_hooks import (
    _pinned as _pinned,
)
from ._other_hooks import (
    _SearchDocsHook as _SearchDocsHook,
)
from ._paths import (
    _LINK_TAGS as _LINK_TAGS,
)
from ._paths import (
    _REPARSE_POINT as _REPARSE_POINT,
)
from ._paths import (
    _campaign_named as _campaign_named,
)
from ._paths import (
    _campaign_of as _campaign_of,
)
from ._paths import (
    _check as _check,
)
from ._paths import (
    _Checked as _Checked,
)
from ._paths import (
    _expand as _expand,
)
from ._paths import (
    _linked as _linked,
)
from ._paths import (
    _relative_parts as _relative_parts,
)
from ._paths import (
    _relative_to as _relative_to,
)
from ._paths import (
    _scene_named as _scene_named,
)
from ._paths import (
    _visit as _visit,
)
from ._paths import (
    _Walk as _Walk,
)
from ._paths import (
    _walk as _walk,
)
from ._paths import (
    syncable as syncable,
)
from ._scopes import (
    _classify_chunk as _classify_chunk,
)
from ._scopes import (
    _disjoint as _disjoint,
)
from ._scopes import (
    _list_scopes as _list_scopes,
)
from ._scopes import (
    _scope_dirs as _scope_dirs,
)
from ._scopes import (
    collecting as collecting,
)
from ._scopes import (
    expand_scopes as expand_scopes,
)
from ._scopes import (
    run as run,
)
from ._signals import (
    _MOVED as _MOVED,
)
from ._signals import (
    LANE_POLL_S as LANE_POLL_S,
)
from ._signals import (
    LANE_WAIT_S as LANE_WAIT_S,
)
from ._signals import (
    _halted as _halted,
)
from ._signals import (
    _lane as _lane,
)

# Explicit aliases retain the former module surface; shared values have one owner.
from ._signals import (
    _log as _log,
)
from ._signals import (
    _norm as _norm,
)
from ._signals import (
    _root_moved as _root_moved,
)
from ._signals import (
    _root_source as _root_source,
)
from ._signals import (
    _take_lane as _take_lane,
)
from ._signals import (
    _warn_once as _warn_once,
)
from ._signals import (
    _warned as _warned,
)
from ._signals import (
    _warned_lock as _warned_lock,
)
from ._vectors import (
    _CLIENT as _CLIENT,
)
from ._vectors import (
    VECTOR_PROJECTIONS as VECTOR_PROJECTIONS,
)
from ._vectors import (
    VECTORS as VECTORS,
)
from ._vectors import (
    Projection as Projection,
)
from ._vectors import (
    _art_on as _art_on,
)
from ._vectors import (
    _embed_chunk as _embed_chunk,
)
from ._vectors import (
    _embed_chunk_literal as _embed_chunk_literal,
)
from ._vectors import (
    _enabled as _enabled,
)
from ._vectors import (
    _recall_on as _recall_on,
)
from ._vectors import (
    _unit_order as _unit_order,
)
from ._vectors import (
    _vector_kind as _vector_kind,
)
from ._vectors import (
    _VectorsHook as _VectorsHook,
)
from ._vectors import (
    _VectorSource as _VectorSource,
)
from ._vectors import (
    _Wanted as _Wanted,
)
from ._vectors import (
    _wanted as _wanted,
)

# Preserve deferred module annotations; values and their types have one owner.
__annotations__ = {
    '_warned': 'set[str]',
    'FILE_RECIPES': 'dict[str, FileRecipe]',
    'VECTOR_PROJECTIONS': 'dict[str, Projection]',
    'HOOKS': 'tuple[WarmHook, ...]',
}

__all__ = [
    'BATCH_FIELDS',
    'EMBEDDING_FIELDS',
    'FILES',
    'FILE_RECIPES',
    'HOOKS',
    'KIND_OUTCOMES',
    'LANE_POLL_S',
    'LANE_WAIT_S',
    'MAX_PATHS',
    'MAX_SCOPE_FILES',
    'MODES',
    'OVERVIEW',
    'PATH_STATUSES',
    'REFUSAL_REASONS',
    'REPORT_VERSION',
    'SEARCHDOCS',
    'STOP_REASONS',
    'SYNCABLE_ROOTS',
    'VECTORS',
    'VECTOR_PROJECTIONS',
    'Callable',
    'Collected',
    'Container',
    'FileRecipe',
    'Iterable',
    'Iterator',
    'LLMError',
    'Literal',
    'LocalResult',
    'Mapping',
    'NetworkResult',
    'Path',
    'PathReport',
    'Projection',
    'Protocol',
    'PurePosixPath',
    'ScopeResult',
    'SyncReport',
    'WarmContext',
    'WarmHook',
    'annotations',
    'art',
    'atomic',
    'collecting',
    'compiled',
    'contextlib',
    'contextvars',
    'dataclass',
    'derive_file',
    'embed',
    'embed_space',
    'embeddings',
    'expand_scopes',
    'field',
    'hashlib',
    'json',
    'layout',
    'logging',
    'os',
    'overview_warm',
    'paths',
    'revision',
    'run',
    'searchdocs_hook',
    'semantic',
    'semsearch',
    'stat',
    'sync_paths',
    'syncable',
    'threading',
    'vectors',
    'writeset',
]
