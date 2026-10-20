"""Closed warm-up vocabulary and immutable context/result/report contracts."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from .. import compiled
from . import _signals

# -- the report vocabulary (05-C2): every word a report can contain ----------

#: What happened to one path.
PATH_STATUSES = ("refreshed", "current", "cold", "deleted", "missing",
                 "refused", "failed", "verify_failed", "unsynced")
#: Why a path was refused.
REFUSAL_REASONS = ("outside_store", "link", "not_syncable", "temp_file", "not_a_file",
                   "not_found", "not_deleted", "bad_id", "too_many", "unreadable")
#: What happened to one kind on one path. `failed` is written
#: `failed:<ExceptionClass>`, the class name and never its message. `planned`
#: is a dry run's "would be built".
KIND_OUTCOMES = ("built", "hit", "planned", "lazy", "deferred", "skipped", "failed")
#: Why a batch stopped early.
STOP_REASONS = ("cancelled", "root_moved", "lane_busy")
#: The top-level fields of a report.
BATCH_FIELDS = ("version", "root", "root_source", "mode", "dry_run", "cache", "cache_reason",
                "paths", "listed", "hooks", "counts", "tokens_bumped", "tokens_failed", "purged",
                "embedding",
                "truncated", "stopped")
#: The fields of a report's `embedding` block.
EMBEDDING_FIELDS = ("would_send", "sent", "requests", "held", "stale_space", "embedding_off",
                    "deferred", "not_sent", "failure")
REPORT_VERSION = 1

MODES = ("explicit", "write")

#: The record directories a path may name (section 8, rule 3). Closed, so a
#: directory added later is refused -- which costs a lazy rebuild -- until
#: someone adds it here. Everything else at the root is derived (`.cache`,
#: `backups`), a ledger (`logs`, `usage`), keys (`llm_connections`), transient
#: staging, or a top-level file such as `config.md`.
SYNCABLE_ROOTS = frozenset({"worlds", "campaigns", "assets", "modules", "calendars",
                            "climates", "styles"})

#: Most explicit paths one batch accepts, and most files one directory expands
#: to. Structural -- a batch is memory and a report is output -- and to be
#: tuned later; past it the rest is `too_many` and the report says
#: `truncated`, so "found nothing" and "stopped looking" never read the same.
MAX_PATHS = 4096


# -- the hook protocol (05-C3) ----------------------------------------------


@dataclass(frozen=True)
class WarmContext:
    """What a hook may know about the batch. Built only by `_context()`.

    `space` is `embed_space.endpoint()` read ONCE at the start of the batch
    (None when embedding is off), and `space_digest` is 03's one spelling of
    its digest; a network phase sends with `space["space"]` and nothing else.
    """

    root: Path
    mode: str
    dry_run: bool
    verify: bool
    embedding: bool
    space: dict | None
    space_digest: str | None
    stop: Callable[[], bool]

    def halted(self) -> str | None:
        """`cancelled` or `root_moved` when the batch must stop now."""
        return _signals._halted(self.root, self.stop)

    def path(self, rel: str) -> Path:
        return self.root.joinpath(*rel.split("/"))


@dataclass
class LocalResult:
    """A local phase's answer: an outcome per `(path, kind)`, the paths whose
    verify found a mismatch, and the embedding the network phase would send."""

    outcomes: dict[tuple[str, str], str] = field(default_factory=dict)
    verify_failed: set[str] = field(default_factory=set)
    plan: object = None
    embedding: dict[str, int] = field(default_factory=dict)


@dataclass
class NetworkResult:
    """A network phase's answer: outcomes, embedding counts, and the failure
    kind that stopped it, if one did."""

    outcomes: dict[tuple[str, str], str] = field(default_factory=dict)
    embedding: dict[str, int] = field(default_factory=dict)
    failure: str = ""
    verify_failed: set[str] = field(default_factory=set)


class WarmHook(Protocol):
    """One owner's rebuild of its own kinds (spec section 6.2).

    `owns(kind)` says whether a `materialized` kind is this hook's; vector
    kinds carry a space digest, so ownership is a test rather than a list.
    `local` rebuilds and stores and touches no network; `network` sends what
    `local` planned and is never called in a dry run, with `embed=False`, on a
    request path or under a lock.
    """

    name: str
    on_write: Literal["quiet", "explicit"]

    def owns(self, kind: str) -> bool: ...

    def local(self, ctx: WarmContext,
              hot: Mapping[str, tuple[compiled.Materialized, ...]]) -> LocalResult: ...

    def network(self, ctx: WarmContext, plan: object) -> NetworkResult: ...


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _instance(row: compiled.Materialized) -> object:
    return json.loads(row.instance) if row.instance else None


# -- the searchdocs hook (08-C2c) ----------------------------------------------


def _fold(outcomes: Iterable[tuple[str, str, str]]) -> dict[tuple[str, str], str]:
    """Per-row outcomes, one per `(path, kind)`: the worst of a path's rows."""
    folded: dict[tuple[str, str], str] = {}
    for rel, kind, outcome in outcomes:
        key = (rel, kind)
        folded[key] = _worse(folded.get(key), outcome) if key in folded else outcome
    return folded

_ORDER = {o: i for i, o in enumerate(("failed", "built", "planned", "hit", "skipped", "deferred", "lazy"))}


def _worse(old: str | None, new: str) -> str:
    """The outcome a kind reports when several of its instances answered."""
    if old is None:
        return new
    return min(old, new, key=lambda o: _ORDER.get(o.split(":", 1)[0], len(_ORDER)))


# -- the report --------------------------------------------------------------


@dataclass
class PathReport:
    given: str
    status: str
    reason: str | None = None
    kinds: dict[str, str] = field(default_factory=dict)
    rel: str | None = None
    #: For the NEW side of a stated rename, OLD's store-relative path.
    renamed_from: str | None = None
    #: The OLD side of a stated rename: deleted, but never by `--deleted`,
    #: so it never purges (section 7.6).
    from_rename: bool = False

    def as_dict(self) -> dict:
        out: dict[str, object] = {"path": self.given, "status": self.status}
        if self.reason is not None:
            out["reason"] = self.reason
        if self.kinds:
            out["kinds"] = dict(sorted(self.kinds.items()))
        return out


@dataclass
class SyncReport:
    """A batch's outcome. Holds names and counts, never record content."""

    root: str
    root_source: str
    mode: str
    dry_run: bool
    cache: str = "on"
    cache_reason: str | None = None
    paths: list[PathReport] = field(default_factory=list)
    hooks: dict[str, dict] = field(default_factory=dict)
    tokens_bumped: int = 0
    #: Campaigns whose write token could not be written: the app was not told.
    tokens_failed: int = 0
    purged: bool | None = None
    embedding: dict[str, object] = field(default_factory=lambda: dict.fromkeys(EMBEDDING_FIELDS, 0))
    truncated: bool = False
    stopped: str | None = None
    #: Whether every path is listed, or only the notable ones (a scope).
    listed: str = "all"

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(PATH_STATUSES, 0)
        for item in self.paths:
            if item.status in out:            # a batch in progress also holds `pending` ones
                out[item.status] += 1
        return out

    @property
    def ok(self) -> bool:
        """No path failed, was refused, is missing, failed verify or was left
        unsynced, and the batch did not stop early -- "stopped looking" never
        reads as "found nothing"."""
        return self.stopped is None and not self.tokens_failed and not any(
            item.status in ("failed", "refused", "missing", "verify_failed", "unsynced")
            for item in self.paths)

    def as_dict(self) -> dict:
        shown = self.paths if self.listed == "all" else [
            p for p in self.paths if p.status not in ("current", "cold")]
        embedding = dict(self.embedding)
        out = {"version": REPORT_VERSION, "root": self.root, "root_source": self.root_source,
               "mode": self.mode, "dry_run": self.dry_run, "cache": self.cache,
               "cache_reason": self.cache_reason, "paths": [p.as_dict() for p in shown],
               "listed": self.listed, "hooks": self.hooks, "counts": self.counts(),
               "tokens_bumped": self.tokens_bumped, "tokens_failed": self.tokens_failed,
               "purged": self.purged,
               "embedding": embedding, "truncated": self.truncated, "stopped": self.stopped}
        assert tuple(out) == BATCH_FIELDS
        return out


# -- scoped modes (section 7.3) ----------------------------------------------

#: Most files one scoped sync lists. A listing costs a stat per file and no
#: read, so this is far above `MAX_PATHS`; past it the report says
#: `truncated`. Structural, and to be tuned later.
MAX_SCOPE_FILES = 200_000


@dataclass
class ScopeResult:
    """A scope's files: those to sync, those already classified (`cold` or
    `current`), the scopes refused, and whether the listing stopped looking."""

    candidates: list[str] = field(default_factory=list)
    known: dict[str, str] = field(default_factory=dict)
    refused: list[PathReport] = field(default_factory=list)
    truncated: bool = False
    #: Why the listing stopped before it classified every file, if it did.
    stopped: str | None = None


# -- scripts (section 5.5) ----------------------------------------------------


@dataclass
class Collected:
    """What `collecting()` yields: the batch's report, once the block ends."""

    report: SyncReport | None = None
