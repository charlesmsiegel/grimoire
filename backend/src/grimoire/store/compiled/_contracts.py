"""Bounded cache vocabulary, immutable derivations and result contracts."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .. import readstats, statcache

SCHEMA = 3
APPLICATION_ID = 0x4752494D
MAX_PAYLOAD_BYTES = 1 << 20
MAX_KEY_BYTES = 64 << 10
MAX_JSON_DEPTH = 64
MAX_JSON_ITEMS = 100_000
MAX_STAGED_ROWS = 128
MAX_STAGED_BYTES = 4 << 20
MAX_HISTORY_ROWS = 128
MAX_HISTORY_BYTES = 4 << 20
WRITE_LOCK_SECONDS = 0.010
# How long a strict history read (`Batch.materialized_answer`) waits for the
# process exclusion before saying it could not answer.
STRICT_LOCK_SECONDS = 2.0
IDLE_SECONDS = 30.0
SIZE_CAP_BYTES = 64 << 20
LOW_WATER_BYTES = 48 << 20
MAINTENANCE_INTERVAL_S = 3600
RETENTION_S = 90 * 86400
TOUCH_INTERVAL_S = 86400
VACUUM_PAGES = 256
MAX_EVICTION_CHUNKS = 64
_DEVICE_KEY = re.compile(r"[0-9a-f]{64}")
_CACHE_FILE = re.compile(r"([0-9a-f]{32})-v([0-9]+)\.sqlite")
_REPARSE_POINT = 0x400

ABSENT = "absent"
PERSIST_WINDOW_NS = 5_000_000_000
SOURCE_CHUNK = 128


@dataclass(frozen=True)
class FilesystemClock:
    now_ns: int
    device: int


@dataclass(frozen=True)
class _SourceInfo:
    path: Path
    stamp: statcache.Stamp
    device: int
    relative: str | None
    plain: bool
    regular: bool = True
    metadata_trusted: bool = True


@dataclass(frozen=True)
class Kind:
    name: str
    version: int
    path_inputs: tuple[str, ...]
    params: tuple[str, ...]
    allow_persisted_sources: bool
    absent_ok_roles: tuple[str, ...] = ()
    member_filter: Callable[[str], bool] | None = None


# The registry (spec 03 section 4): every kind that persists is declared
# here, so one place lists what the file can hold. A consumer imports its kind
# from this module rather than declaring one of its own. Search extraction and
# the world and campaign rows are absent on purpose: they lost section 14's
# warm-after-restart measurement, so they are not wired.
TOKENS_RECORD_BODY = Kind('tokens:record_body', 1, (), ('text_hash', 'encoding'), False)

# Roadmap 04's overview projections (`store/overview/`). Each is byte-fed,
# keyed on whole files (no path-derived params: a name fallback is applied
# outside the cache), and may use persisted sources (spec 04 section 3.2, rule
# 5): a stale answer is a stale card line or badge, never a decision. Declared
# here, as every persisted kind is; REGISTERED below only when it won spec
# 03's section 14 measurement, which `scripts/bench_overview.py` takes. An
# unregistered one is never looked up or stored: its reader takes its live
# path at today's cost.
#
# The ruling (roadmap 04-S10, synthetic libraries only): a lookup costs more
# than parsing one small `world.md` or `campaign.md`, and more than the whole
# continuity summary at every ledger size the profiles reach -- the same
# outcome spec 03 recorded for the shelf rows -- so those three stay declared
# and unwired. `scene_turns` is registered: a transcript parse grows with the
# transcript and passes a lookup's fixed cost for long scenes, so it persists
# above an admission size (`overview.read.SCENE_TURNS_PERSIST_MIN_BYTES`), the
# token-count kind's precedent, and a shorter scene keeps the in-process memo.
OVERVIEW_WORLD_ROW = Kind('overview.world_row', 1, (), (), True)
OVERVIEW_CAMPAIGN_ROW = Kind('overview.campaign_row', 1, (), (), True)
OVERVIEW_SCENE_TURNS = Kind('overview.scene_turns', 1, (), ('players',), True)
OVERVIEW_CONTINUITY = Kind(
    'overview.continuity_summary', 1, (), (), True,
    absent_ok_roles=('plot', 'commitments', 'events', 'continuity', 'candidates'))
OVERVIEW_CANDIDATES: dict[str, Kind] = {k.name: k for k in (
    OVERVIEW_WORLD_ROW, OVERVIEW_CAMPAIGN_ROW, OVERVIEW_SCENE_TURNS, OVERVIEW_CONTINUITY)}

# Spec 05's scoped sync asks `known_hashes` which listed files a `sources` row
# vouches for, and that policy is per kind. This kind is never keyed and holds
# no artifact: it names the trust policy alone, so a file proved current by
# its stamp is not read again (05 section 7.3).
SYNC_SOURCES = Kind('sync:sources', 1, (), (), True)

# A scene's history SearchDocument (roadmap 08-C2a): keyed by its per-scene
# slice digests and what would not read, never by a whole file. `version` is
# `searchdocs.scene.SEARCH_DOCUMENT_VERSION`; bumping it moves every key.
SEARCHDOCS_SCENE = Kind('searchdocs.scene', 1, (), ('slices', 'degraded'), False)

# Roadmap 09: statistics of exactly the live SearchDocument key the caller
# supplied. No corpus frequency persists: the live set belongs to this query.
HISTORY_TERMS = Kind('history.terms', 1, (), ('document_key',), False)

KINDS: dict[str, Kind] = {TOKENS_RECORD_BODY.name: TOKENS_RECORD_BODY,
                          OVERVIEW_SCENE_TURNS.name: OVERVIEW_SCENE_TURNS,
                          SYNC_SOURCES.name: SYNC_SOURCES,
                          SEARCHDOCS_SCENE.name: SEARCHDOCS_SCENE,
                          HISTORY_TERMS.name: HISTORY_TERMS}


@dataclass(frozen=True)
class Input:
    role: str
    path: Path


@dataclass(frozen=True)
class Collection:
    role: str
    path: Path
    recursive: bool = False


@dataclass(frozen=True)
class Materialized:
    path: Path
    kind: str
    instance: str
    built_from: str
    last_used: int


@dataclass(frozen=True)
class Uncached:
    value: object


@dataclass(frozen=True)
class WrittenInput:
    role: str
    path: Path
    data: bytes


@dataclass(frozen=True)
class ComputeInput:
    role: str
    content_hash: str
    data: bytes | None
    path_parts: tuple[str, ...]


@dataclass(frozen=True)
class ComputeCollection:
    role: str
    content_hash: str
    members: tuple[ComputeInput, ...]


@dataclass(frozen=True)
class DerivationInputs:
    files: tuple[ComputeInput, ...]
    collections: tuple[ComputeCollection, ...]


@dataclass(frozen=True)
class ValidatedInput:
    role: str
    path: Path
    content_hash: str
    stamp: statcache.Stamp | None
    data: bytes | None
    materialized: tuple[Materialized, ...] = ()


@dataclass(frozen=True)
class CollectionDigest:
    role: str
    content_hash: str
    members: tuple[ValidatedInput, ...]


class InputUnavailable(OSError):  # noqa: N818 -- Spec03 public exception contract
    """A listed input cannot supply the bytes required by this derivation."""


@dataclass(frozen=True)
class Key:
    kind: str
    digest: str


@dataclass(frozen=True)
class Lookup:
    hit: bool
    value: object = None


@dataclass(frozen=True)
class Counts:
    hits: int
    misses: int


@dataclass(frozen=True)
class BatchLookup:
    values: dict[Key | None, Lookup]
    counts: dict[str, Counts]


def _lookup_result(values: dict[Key | None, Lookup]) -> BatchLookup:
    counts: dict[str, list[int]] = {}
    for key, lookup in values.items():
        if key is not None:  # make_key's refusal: answered as a miss, counted nowhere
            counts.setdefault(key.kind, [0, 0])[0 if lookup.hit else 1] += 1
    for kind, (hits, misses) in counts.items():  # 04-C3a: 03-C6's counts, per request
        if hits:
            readstats.bump(f"compiled.hit.{kind}", hits)
        if misses:
            readstats.bump(f"compiled.miss.{kind}", misses)
    return BatchLookup(values, {kind: Counts(*count) for kind, count in counts.items()})


# `_materialized_rows`' answer for a family past the history budget: an
# answer (empty), not a failure to ask.
_OVER_BUDGET: list[tuple] = []

#: `Batch.materialized_answer(..., split=True)`'s answer for a read past the
#: history budget, compared by identity.
HISTORY_OVER_BUDGET: Mapping[Path, tuple] = MappingProxyType({})


LEASE_CHECK_S = 60.0
