"""Disposable compiled artifacts, owned by this process and pinned library.

Spec 03's foundation: SQLite is an optional memo, never the live inventory.
Files establish membership; callers will select artifacts with content keys.
The stable application id and metadata distinguish our cache from a foreign
database. Unreadable, linked, busy or unowned storage costs a miss, never the
caller's file-reader answer. Tests/test_compiled.py pins those boundaries.

Keys select a particular derivation; row checks bind canonical JSON to that
key, so even damaged but valid JSON is a miss. Only SQLite's primary CORRUPT
or NOTADB codes permit replacement, under a machine-local nonblocking lock.
Locked, foreign and incompatible databases survive untouched. See spec
docs/superpowers/specs/2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md
and test_compiled's corruption, foreign-file and hostile-row cases.

Ownership is probed immutably before read-write SQLite may recover journals.
Ordinary SQL and replacement share a nonblocking process exclusion; idle
handles recheck their inode under it before use. A failed transaction finishes
its rollback or closes under that exclusion, never across a rename window.

Only this module imports SQLite. Connections stay private and serialized;
parsing never belongs under their lock. Cache-owned directories and leaves
are checked with lstat, including Windows reparse attributes. The chosen
library root is the user's boundary: these checks reject established links,
not a hostile filesystem replacing a component between check and use.
"""

from __future__ import annotations

import asyncio as asyncio
import contextlib as contextlib
import functools as functools
import hashlib as hashlib
import itertools as itertools
import json as json
import math as math
import os as os
import platform as platform
import re as re
import secrets as secrets
import stat as stat
import sys as sys
import threading as threading
import time as time
from _thread import LockType as LockType
from collections import deque as deque
from collections.abc import Callable as Callable
from collections.abc import Iterator as Iterator
from collections.abc import Mapping as Mapping
from collections.abc import Sequence as Sequence
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from dataclasses import replace as replace
from pathlib import Path as Path
from types import MappingProxyType as MappingProxyType
from types import ModuleType as ModuleType
from typing import NoReturn as NoReturn

from ... import compiled_build as compiled_build
from ... import template_paths as template_paths
from .. import atomic as atomic
from .. import file_stats as file_stats
from .. import locks as locks
from .. import logs as logs
from .. import maintenance_reports as maintenance_reports
from .. import paths as paths
from .. import proclock as proclock
from .. import readstats as readstats
from .. import statcache as statcache
from . import _filesystem, _runtime, _sqlite_support
from ._batch import (
    _ARTIFACT_MANY_SQL as _ARTIFACT_MANY_SQL,
)
from ._batch import (
    _MATERIALIZED_SQL as _MATERIALIZED_SQL,
)
from ._batch import (
    _SOURCE_SQL as _SOURCE_SQL,
)
from ._batch import (
    Batch as Batch,
)
from ._contracts import (
    _CACHE_FILE as _CACHE_FILE,
)
from ._contracts import (
    _DEVICE_KEY as _DEVICE_KEY,
)
from ._contracts import (
    _OVER_BUDGET as _OVER_BUDGET,
)
from ._contracts import (
    _REPARSE_POINT as _REPARSE_POINT,
)
from ._contracts import (
    ABSENT as ABSENT,
)
from ._contracts import (
    APPLICATION_ID as APPLICATION_ID,
)
from ._contracts import (
    HISTORY_OVER_BUDGET as HISTORY_OVER_BUDGET,
)
from ._contracts import (
    HISTORY_TERMS as HISTORY_TERMS,
)
from ._contracts import (
    IDLE_SECONDS as IDLE_SECONDS,
)
from ._contracts import (
    KINDS as KINDS,
)
from ._contracts import (
    LEASE_CHECK_S as LEASE_CHECK_S,
)
from ._contracts import (
    LOW_WATER_BYTES as LOW_WATER_BYTES,
)
from ._contracts import (
    MAINTENANCE_INTERVAL_S as MAINTENANCE_INTERVAL_S,
)
from ._contracts import (
    MAX_EVICTION_CHUNKS as MAX_EVICTION_CHUNKS,
)
from ._contracts import (
    MAX_HISTORY_BYTES as MAX_HISTORY_BYTES,
)
from ._contracts import (
    MAX_HISTORY_ROWS as MAX_HISTORY_ROWS,
)
from ._contracts import (
    MAX_JSON_DEPTH as MAX_JSON_DEPTH,
)
from ._contracts import (
    MAX_JSON_ITEMS as MAX_JSON_ITEMS,
)
from ._contracts import (
    MAX_KEY_BYTES as MAX_KEY_BYTES,
)
from ._contracts import (
    MAX_PAYLOAD_BYTES as MAX_PAYLOAD_BYTES,
)
from ._contracts import (
    MAX_STAGED_BYTES as MAX_STAGED_BYTES,
)
from ._contracts import (
    MAX_STAGED_ROWS as MAX_STAGED_ROWS,
)
from ._contracts import (
    OVERVIEW_CAMPAIGN_ROW as OVERVIEW_CAMPAIGN_ROW,
)
from ._contracts import (
    OVERVIEW_CANDIDATES as OVERVIEW_CANDIDATES,
)
from ._contracts import (
    OVERVIEW_CONTINUITY as OVERVIEW_CONTINUITY,
)
from ._contracts import (
    OVERVIEW_SCENE_TURNS as OVERVIEW_SCENE_TURNS,
)
from ._contracts import (
    OVERVIEW_WORLD_ROW as OVERVIEW_WORLD_ROW,
)
from ._contracts import (
    PERSIST_WINDOW_NS as PERSIST_WINDOW_NS,
)
from ._contracts import (
    RETENTION_S as RETENTION_S,
)
from ._contracts import (
    SCHEMA as SCHEMA,
)
from ._contracts import (
    SEARCHDOCS_SCENE as SEARCHDOCS_SCENE,
)
from ._contracts import (
    SIZE_CAP_BYTES as SIZE_CAP_BYTES,
)
from ._contracts import (
    SOURCE_CHUNK as SOURCE_CHUNK,
)
from ._contracts import (
    STRICT_LOCK_SECONDS as STRICT_LOCK_SECONDS,
)
from ._contracts import (
    SYNC_SOURCES as SYNC_SOURCES,
)
from ._contracts import (
    TOKENS_RECORD_BODY as TOKENS_RECORD_BODY,
)
from ._contracts import (
    TOUCH_INTERVAL_S as TOUCH_INTERVAL_S,
)
from ._contracts import (
    VACUUM_PAGES as VACUUM_PAGES,
)
from ._contracts import (
    WRITE_LOCK_SECONDS as WRITE_LOCK_SECONDS,
)
from ._contracts import (
    BatchLookup as BatchLookup,
)
from ._contracts import (
    Collection as Collection,
)
from ._contracts import (
    CollectionDigest as CollectionDigest,
)
from ._contracts import (
    ComputeCollection as ComputeCollection,
)
from ._contracts import (
    ComputeInput as ComputeInput,
)
from ._contracts import (
    Counts as Counts,
)
from ._contracts import (
    DerivationInputs as DerivationInputs,
)
from ._contracts import (
    FilesystemClock as FilesystemClock,
)
from ._contracts import (
    Input as Input,
)
from ._contracts import (
    InputUnavailable as InputUnavailable,
)
from ._contracts import (
    Key as Key,
)
from ._contracts import (
    Kind as Kind,
)
from ._contracts import (
    Lookup as Lookup,
)
from ._contracts import (
    Materialized as Materialized,
)
from ._contracts import (
    Uncached as Uncached,
)
from ._contracts import (
    ValidatedInput as ValidatedInput,
)
from ._contracts import (
    WrittenInput as WrittenInput,
)
from ._contracts import (
    _lookup_result as _lookup_result,
)
from ._contracts import (
    _SourceInfo as _SourceInfo,
)
from ._deletion import (
    _erase_owned_corrupt as _erase_owned_corrupt,
)
from ._deletion import (
    _intent_lock as _intent_lock,
)
from ._deletion import (
    _pending_maintenance as _pending_maintenance,
)
from ._deletion import (
    _prepare_delete as _prepare_delete,
)
from ._deletion import (
    _purge_connection as _purge_connection,
)
from ._deletion import (
    _purge_corrupt_aside as _purge_corrupt_aside,
)
from ._deletion import (
    _purge_cursor as _purge_cursor,
)
from ._deletion import (
    _purge_fenced_page as _purge_fenced_page,
)
from ._deletion import (
    _purge_file as _purge_file,
)
from ._deletion import (
    _purge_if_needed as _purge_if_needed,
)
from ._deletion import (
    _purge_local_files as _purge_local_files,
)
from ._deletion import (
    _purge_progress_check as _purge_progress_check,
)
from ._deletion import (
    _purge_table_page as _purge_table_page,
)
from ._deletion import (
    _purge_trigger_page as _purge_trigger_page,
)
from ._deletion import (
    _purge_view_page as _purge_view_page,
)
from ._deletion import (
    _purge_virtual_page as _purge_virtual_page,
)
from ._deletion import (
    _quote_table as _quote_table,
)
from ._deletion import (
    _recover_pending as _recover_pending,
)
from ._deletion import (
    _release_finished_intent as _release_finished_intent,
)
from ._deletion import (
    _release_finished_intents as _release_finished_intents,
)
from ._deletion import (
    _release_finished_intents_locked as _release_finished_intents_locked,
)
from ._deletion import (
    _release_intents as _release_intents,
)
from ._deletion import (
    _release_intents_locked as _release_intents_locked,
)
from ._deletion import (
    _retire_intent as _retire_intent,
)
from ._deletion import (
    _retire_intent_locked as _retire_intent_locked,
)
from ._deletion import (
    _synchronize as _synchronize,
)
from ._deletion import (
    prepare_delete as prepare_delete,
)
from ._deletion import (
    purge_for_delete as purge_for_delete,
)
from ._encoding import (
    _JSON_NOT_STRUCTURE as _JSON_NOT_STRUCTURE,
)
from ._encoding import (
    _JSON_STRING as _JSON_STRING,
)
from ._encoding import (
    _canonical as _canonical,
)
from ._encoding import (
    _canonical_instance as _canonical_instance,
)
from ._encoding import (
    _check as _check,
)
from ._encoding import (
    _collection_hash as _collection_hash,
)
from ._encoding import (
    _composite as _composite,
)
from ._encoding import (
    _decode as _decode,
)
from ._encoding import (
    _decode_materialized as _decode_materialized,
)
from ._encoding import (
    _decode_source as _decode_source,
)
from ._encoding import (
    _history_size as _history_size,
)
from ._encoding import (
    _history_transfer as _history_transfer,
)
from ._encoding import (
    _instance as _instance,
)
from ._encoding import (
    _json_depth as _json_depth,
)
from ._encoding import (
    _json_value as _json_value,
)
from ._encoding import (
    _key_inputs as _key_inputs,
)
from ._encoding import (
    _materialized_check as _materialized_check,
)
from ._encoding import (
    _materialized_row as _materialized_row,
)
from ._encoding import (
    _registered as _registered,
)
from ._encoding import (
    _relative as _relative,
)
from ._encoding import (
    _scalar as _scalar,
)
from ._encoding import (
    _source_check as _source_check,
)
from ._encoding import (
    content_digest as content_digest,
)
from ._encoding import (
    make_key as make_key,
)
from ._encoding import (
    space_digest as space_digest,
)
from ._entry import (
    batch as batch,
)
from ._entry import (
    open_library as open_library,
)
from ._fences import (
    _control_paths as _control_paths,
)
from ._fences import (
    _control_snapshot as _control_snapshot,
)
from ._fences import (
    _controls as _controls,
)
from ._fences import (
    _location as _location,
)
from ._fences import (
    _prove_location as _prove_location,
)
from ._fences import (
    _publish_marker as _publish_marker,
)
from ._fences import (
    _read_generation as _read_generation,
)
from ._fences import (
    _read_intent as _read_intent,
)
from ._fences import (
    _read_intents as _read_intents,
)
from ._fences import (
    _read_marker as _read_marker,
)
from ._fences import (
    _settled as _settled,
)
from ._fences import (
    _wall_ns as _wall_ns,
)
from ._filesystem import (
    _LINUX_CLOCK as _LINUX_CLOCK,
)
from ._filesystem import (
    _LOCAL_FILESYSTEMS as _LOCAL_FILESYSTEMS,
)
from ._filesystem import (
    _WINDOWS_CLOCK as _WINDOWS_CLOCK,
)
from ._filesystem import (
    _clock_path as _clock_path,
)
from ._filesystem import (
    _clock_supported as _clock_supported,
)
from ._filesystem import (
    _eligible as _eligible,
)
from ._filesystem import (
    _kernel32 as _kernel32,
)
from ._filesystem import (
    _libc as _libc,
)
from ._filesystem import (
    _linux_filesystem_type as _linux_filesystem_type,
)
from ._filesystem import (
    _source_info as _source_info,
)
from ._filesystem import (
    filesystem_clock as filesystem_clock,
)
from ._maintenance import (
    _evict as _evict,
)
from ._maintenance import (
    _incremental_vacuum as _incremental_vacuum,
)
from ._maintenance import (
    _live_bytes as _live_bytes,
)
from ._maintenance import (
    _lru_victims as _lru_victims,
)
from ._maintenance import (
    _maintain as _maintain,
)
from ._ownership import (
    _corruption as _corruption,
)
from ._ownership import (
    _create_schema as _create_schema,
)
from ._ownership import (
    _identity as _identity,
)
from ._ownership import (
    _initialise as _initialise,
)
from ._ownership import (
    _owned as _owned,
)
from ._ownership import (
    _owned_tables as _owned_tables,
)
from ._ownership import (
    _probe_owned as _probe_owned,
)
from ._ownership import (
    _probe_recognized as _probe_recognized,
)
from ._ownership import (
    _recovery_lock as _recovery_lock,
)
from ._ownership import (
    _still_corrupt as _still_corrupt,
)
from ._ownership import (
    _unborn as _unborn,
)
from ._path_checks import (
    _cache_file_safe as _cache_file_safe,
)
from ._path_checks import (
    _cache_filename as _cache_filename,
)
from ._path_checks import (
    _cache_parent as _cache_parent,
)
from ._path_checks import (
    _controls_safe as _controls_safe,
)
from ._path_checks import (
    _directory as _directory,
)
from ._path_checks import (
    _leaf as _leaf,
)
from ._path_checks import (
    _linked as _linked,
)
from ._retention import (
    _erase_recognized_aside as _erase_recognized_aside,
)
from ._retention import (
    _renew_lease as _renew_lease,
)
from ._retention import (
    _retention_used as _retention_used,
)
from ._retention import (
    _retire_owned_aside as _retire_owned_aside,
)
from ._retention import (
    _retire_owned_files as _retire_owned_files,
)
from ._retention import (
    _sweep_retention as _sweep_retention,
)
from ._runtime import (
    DeleteIntent as DeleteIntent,
)
from ._runtime import (
    _after_fork_child as _after_fork_child,
)
from ._runtime import (
    _after_fork_parent as _after_fork_parent,
)
from ._runtime import (
    _available as _available,
)
from ._runtime import (
    _before_fork as _before_fork,
)
from ._runtime import (
    _build as _build,
)
from ._runtime import (
    _clock as _clock,
)
from ._runtime import (
    _close_inherited_intents as _close_inherited_intents,
)
from ._runtime import (
    _close_locked as _close_locked,
)
from ._runtime import (
    _device as _device,
)
from ._runtime import (
    _fingerprint as _fingerprint,
)
from ._runtime import (
    _idle as _idle,
)
from ._runtime import (
    _inherit_build as _inherit_build,
)
from ._runtime import (
    _inherited as _inherited,
)
from ._runtime import (
    _on_loop as _on_loop,
)
from ._runtime import (
    _process_state as _process_state,
)
from ._runtime import (
    _schedule_idle as _schedule_idle,
)
from ._runtime import (
    _Session as _Session,
)
from ._runtime import (
    _State as _State,
)
from ._runtime import (
    _timer as _timer,
)
from ._runtime import (
    _warn_once as _warn_once,
)
from ._runtime import (
    available as available,
)
from ._runtime import (
    build_fingerprint as build_fingerprint,
)
from ._runtime import (
    close as close,
)
from ._runtime import (
    enabled as enabled,
)
from ._runtime import (
    prime_build as prime_build,
)
from ._runtime import (
    published as published,
)
from ._runtime import (
    root_change as root_change,
)
from ._runtime import (
    unavailable_reason as unavailable_reason,
)

# Explicit aliases retain the former module surface; state has one owner.
from ._sqlite_support import (
    _CACHE_ERRORS as _CACHE_ERRORS,
)
from ._storage import (
    _connect_owned as _connect_owned,
)
from ._storage import (
    _database as _database,
)
from ._storage import (
    _discard_aside as _discard_aside,
)
from ._storage import (
    _failed_sql as _failed_sql,
)
from ._storage import (
    _open_locked as _open_locked,
)
from ._storage import (
    _recover as _recover,
)
from ._storage import (
    _replace_corrupt_locked as _replace_corrupt_locked,
)


def __getattr__(name: str):
    # State is replaced after fork/pid changes; optional aliases may be absent.
    if name == "_state":
        return _runtime._state
    if name in ("_sqlite", "_sqlite_available"):
        return getattr(_sqlite_support, name)
    if name in ("_ctypes", "_ctypes_available"):
        return getattr(_filesystem, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    names = set(globals()) | {"_state", "_sqlite", "_ctypes"}
    if hasattr(_sqlite_support, "_sqlite_available"):
        names.add("_sqlite_available")
    if hasattr(_filesystem, "_ctypes_available"):
        names.add("_ctypes_available")
    return sorted(names)


# Preserve deferred module annotations; values and their types have one owner.
__annotations__ = {
    '_sqlite': 'ModuleType | None',
    '_ctypes': 'ModuleType | None',
    'OVERVIEW_CANDIDATES': 'dict[str, Kind]',
    'KINDS': 'dict[str, Kind]',
    '_inherited': 'list[_State]',
    '_OVER_BUDGET': 'list[tuple]',
    'HISTORY_OVER_BUDGET': 'Mapping[Path, tuple]',
}


__all__ = [
    'ABSENT',
    'APPLICATION_ID',
    'HISTORY_OVER_BUDGET',
    'HISTORY_TERMS',
    'IDLE_SECONDS',
    'KINDS',
    'LEASE_CHECK_S',
    'LOW_WATER_BYTES',
    'MAINTENANCE_INTERVAL_S',
    'MAX_EVICTION_CHUNKS',
    'MAX_HISTORY_BYTES',
    'MAX_HISTORY_ROWS',
    'MAX_JSON_DEPTH',
    'MAX_JSON_ITEMS',
    'MAX_KEY_BYTES',
    'MAX_PAYLOAD_BYTES',
    'MAX_STAGED_BYTES',
    'MAX_STAGED_ROWS',
    'OVERVIEW_CAMPAIGN_ROW',
    'OVERVIEW_CANDIDATES',
    'OVERVIEW_CONTINUITY',
    'OVERVIEW_SCENE_TURNS',
    'OVERVIEW_WORLD_ROW',
    'PERSIST_WINDOW_NS',
    'RETENTION_S',
    'SCHEMA',
    'SEARCHDOCS_SCENE',
    'SIZE_CAP_BYTES',
    'SOURCE_CHUNK',
    'STRICT_LOCK_SECONDS',
    'SYNC_SOURCES',
    'TOKENS_RECORD_BODY',
    'TOUCH_INTERVAL_S',
    'VACUUM_PAGES',
    'WRITE_LOCK_SECONDS',
    'Batch',
    'BatchLookup',
    'Callable',
    'Collection',
    'CollectionDigest',
    'ComputeCollection',
    'ComputeInput',
    'Counts',
    'DeleteIntent',
    'DerivationInputs',
    'FilesystemClock',
    'Input',
    'InputUnavailable',
    'Iterator',
    'Key',
    'Kind',
    'LockType',
    'Lookup',
    'Mapping',
    'MappingProxyType',
    'Materialized',
    'ModuleType',
    'NoReturn',
    'Path',
    'Sequence',
    'Uncached',
    'ValidatedInput',
    'WrittenInput',
    'annotations',
    'asyncio',
    'atomic',
    'available',
    'batch',
    'build_fingerprint',
    'close',
    'compiled_build',
    'content_digest',
    'contextlib',
    'dataclass',
    'deque',
    'enabled',
    'field',
    'file_stats',
    'filesystem_clock',
    'functools',
    'hashlib',
    'itertools',
    'json',
    'locks',
    'logs',
    'maintenance_reports',
    'make_key',
    'math',
    'open_library',
    'os',
    'paths',
    'platform',
    'prepare_delete',
    'prime_build',
    'proclock',
    'published',
    'purge_for_delete',
    're',
    'readstats',
    'replace',
    'root_change',
    'secrets',
    'space_digest',
    'stat',
    'statcache',
    'sys',
    'template_paths',
    'threading',
    'time',
    'unavailable_reason',
]
