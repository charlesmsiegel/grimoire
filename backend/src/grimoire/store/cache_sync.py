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

import contextlib
import hashlib
import json
import logging
import os
import stat
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from . import atomic, compiled, embed_space, paths, revision, writeset

_log = logging.getLogger(__name__)

# -- the report vocabulary (05-C2): every word a report can contain ----------

#: What happened to one path.
PATH_STATUSES = ("refreshed", "current", "cold", "deleted", "missing",
                 "refused", "failed", "verify_failed", "unsynced")
#: Why a path was refused.
REFUSAL_REASONS = ("outside_store", "link", "not_syncable", "temp_file", "not_a_file",
                   "not_found", "not_deleted", "bad_id", "too_many")
#: What happened to one kind on one path. `failed` is written
#: `failed:<ExceptionClass>`, the class name and never its message. `planned`
#: is a dry run's "would be built".
KIND_OUTCOMES = ("built", "hit", "planned", "lazy", "deferred", "skipped", "failed")
#: Why a batch stopped early.
STOP_REASONS = ("cancelled", "root_moved", "lane_busy")
#: The top-level fields of a report.
BATCH_FIELDS = ("version", "root", "root_source", "mode", "dry_run", "cache", "cache_reason",
                "paths", "listed", "hooks", "counts", "tokens_bumped", "purged", "embedding",
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

#: How long a write-through batch waits for the sync lane before giving its
#: paths back to the queue. An explicit batch waits as long as it takes.
LANE_WAIT_S = 0.5

_REPARSE_POINT = 0x400
_lane = threading.Lock()
_warned: set[str] = set()
_warned_lock = threading.Lock()


def _warn_once(kind: str) -> None:
    with _warned_lock:
        if kind in _warned:
            return
        _warned.add(kind)
    _log.warning("cache sync degraded: %s", kind)


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
    embed: bool
    space: Mapping | None
    space_digest: str | None
    stop: Callable[[], bool]

    def halted(self) -> str | None:
        """`cancelled` or `root_moved` when the batch must stop now."""
        return _halted(self.root, self.stop)

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
    outcomes: dict[tuple[str, str], str] = field(default_factory=dict)
    embedding: dict[str, object] = field(default_factory=dict)


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


# -- the `files` hook: 03's single-file kinds --------------------------------


@dataclass(frozen=True)
class FileRecipe:
    """How a single-file kind is derived from one path.

    The reader that wires such a kind registers its recipe in `FILE_RECIPES`
    and derives through `derive_file` with the same recipe, so a warm-up and a
    read make the same `derive` call and cannot disagree about a key.
    `params(path, instance)` gives the derivation's params for the
    `materialized` row's instance (its decoded JSON, or None)."""

    kind: compiled.Kind
    role: str
    params: Callable[[Path, object], Mapping[str, object]]
    compute: Callable[[compiled.DerivationInputs, Mapping[str, object]], object]


#: Single-file kinds the `files` hook can rebuild, by kind name. Empty today:
#: 03 wired no per-file kind (its section 14 measurement left them unwired), so
#: the hook claims nothing until one is. A kind whose reader registers here is
#: rebuilt for every path that has its row.
FILE_RECIPES: dict[str, FileRecipe] = {}


def derive_file(request: compiled.Batch, recipe: FileRecipe, path: Path, instance: object = None,
                *, compute: Callable | None = None) -> object:
    """The one `derive` call for a single-file kind, reader and warm-up alike."""
    return request.derive(recipe.kind, [compiled.Input(recipe.role, path)],
                          params=recipe.params(path, instance),
                          compute=compute or recipe.compute, instance=instance)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fresh(recipe: FileRecipe, path: Path, instance: object) -> object:
    """`recipe`'s value for `path`'s current bytes with the compiled layer
    bypassed -- verify's comparison (spec section 7.7)."""
    data = path.read_bytes()
    view = compiled.DerivationInputs(
        (compiled.ComputeInput(recipe.role, hashlib.sha256(data).hexdigest(), data, ()),), ())
    value = recipe.compute(view, recipe.params(path, instance))
    return value.value if isinstance(value, compiled.Uncached) else value


def _instance(row: compiled.Materialized) -> object:
    return json.loads(row.instance) if row.instance else None


class _FilesHook:
    name = "files"
    on_write: Literal["quiet", "explicit"] = "quiet"

    def owns(self, kind: str) -> bool:
        recipe = FILE_RECIPES.get(kind)
        return recipe is not None and compiled.KINDS.get(kind) == recipe.kind

    def local(self, ctx: WarmContext,
              hot: Mapping[str, tuple[compiled.Materialized, ...]]) -> LocalResult:
        result = LocalResult()
        for rel, rows in hot.items():
            if ctx.halted():
                break
            for row in rows:
                outcome = self._one(ctx, rel, row, result)
                key = (rel, row.kind)
                result.outcomes[key] = _worse(result.outcomes.get(key), outcome)
        return result

    def _one(self, ctx: WarmContext, rel: str, row: compiled.Materialized,
             result: LocalResult) -> str:
        recipe = FILE_RECIPES[row.kind]
        path = ctx.path(rel)
        try:
            instance = _instance(row)
            if ctx.dry_run:
                with compiled.batch(ctx.root) as request:
                    try:
                        (item,) = request.validate(recipe.kind, [compiled.Input(recipe.role, path)])
                        key = compiled.make_key(recipe.kind, [(item.role, item.content_hash)],
                                                params=recipe.params(path, instance))
                        return "hit" if request.lookup(key).hit else "planned"
                    finally:
                        request.disabled = True       # a dry run stores nothing
            ran = []

            def compute(view, params):
                ran.append(True)
                return recipe.compute(view, params)

            with compiled.batch(ctx.root) as request:
                value = derive_file(request, recipe, path, instance, compute=compute)
            if ctx.verify and _canonical(value) != _canonical(_fresh(recipe, path, instance)):
                result.verify_failed.add(rel)
                _log.warning("cache sync verify mismatch in kind %s", row.kind)
            return "built" if ran else "hit"
        except Exception as exc:                          # noqa: BLE001 -- a path's failure is a status
            return f"failed:{type(exc).__name__}"

    def network(self, ctx: WarmContext, plan: object) -> NetworkResult:
        return NetworkResult()


FILES = _FilesHook()

#: The hooks, in their fixed order: `files`, `overview` (04-C2a), `searchdocs`
#: (08-C2c), `vectors`. A tuple naming each hook by module rather than a
#: registry filled by import side effects, so a process that imports only this
#: module -- the CLI -- still has every hook. An owner not yet landed is
#: absent, never stubbed.
HOOKS: tuple[WarmHook, ...] = (FILES,)

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
    purged: bool | None = None
    embedding: dict[str, object] = field(default_factory=lambda: dict.fromkeys(EMBEDDING_FIELDS, 0))
    truncated: bool = False
    stopped: str | None = None
    #: Whether every path is listed, or only the notable ones (a scope).
    listed: str = "all"

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(PATH_STATUSES, 0)
        for item in self.paths:
            out[item.status] += 1
        return out

    @property
    def ok(self) -> bool:
        """No path failed, was refused, is missing, failed verify or was left
        unsynced by a batch that stopped early."""
        return not any(item.status in ("failed", "refused", "missing", "verify_failed", "unsynced")
                       for item in self.paths)

    def as_dict(self) -> dict:
        shown = self.paths if self.listed == "all" else [
            p for p in self.paths if p.status not in ("current", "cold")]
        embedding = dict(self.embedding)
        out = {"version": REPORT_VERSION, "root": self.root, "root_source": self.root_source,
               "mode": self.mode, "dry_run": self.dry_run, "cache": self.cache,
               "cache_reason": self.cache_reason, "paths": [p.as_dict() for p in shown],
               "listed": self.listed, "hooks": self.hooks, "counts": self.counts(),
               "tokens_bumped": self.tokens_bumped, "purged": self.purged,
               "embedding": embedding, "truncated": self.truncated, "stopped": self.stopped}
        assert tuple(out) == BATCH_FIELDS
        return out


# -- path validation (section 8) ---------------------------------------------


@dataclass(frozen=True)
class _Checked:
    given: str
    rel: str | None = None
    reason: str | None = None
    kind: str = ""          # "file", "dir", "absent" or "" when refused


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


#: The reparse tags that redirect a path: a symlink and a junction (mount
#: point). Any other reparse point -- a cloud-sync placeholder, say -- is the
#: file itself, and refusing it would refuse a library kept in a synced folder.
_LINK_TAGS = frozenset({0xA000000C, 0xA0000003})


def _linked(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    if not getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
        return False
    tag = getattr(info, "st_reparse_tag", None)
    return tag is None or tag in _LINK_TAGS


def _relative_parts(root: Path, given: str) -> list[str] | None:
    """`given` as components under `root`, lexically normalised; None when it
    escapes the root (a `..` past it, or an absolute path elsewhere). Never
    through `resolve()`, which would follow links."""
    if not isinstance(given, str) or not given or "\0" in given:
        return None
    base = os.path.abspath(root)
    full = os.path.normpath(os.path.abspath(given if os.path.isabs(given) else os.path.join(base, given)))
    folded, folded_base = os.path.normcase(full), os.path.normcase(base).rstrip(os.sep)
    if folded != folded_base and not folded.startswith(folded_base + os.sep):
        return None
    parts = [p for p in os.path.relpath(full, base).split(os.sep) if p and p != "."]
    return parts if parts and ".." not in parts else None


def _walk(root: Path, parts: list[str]) -> tuple[str | None, os.stat_result | None]:
    """lstat every component from the root down: (`link`, None) at a link or
    junction, (None, None) at the first missing one, else the leaf's stat."""
    current = Path(os.path.abspath(root))
    info = None
    for part in parts:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return None, None
        except OSError:
            return "not_a_file", None
        if _linked(info):
            return "link", None
    return None, info


def _check(root: Path, given: str) -> _Checked:
    """Hold one argument to section 8's closed rule. Reads nothing but stats."""
    text = os.fspath(given)
    parts = _relative_parts(root, text)
    if parts is None:
        return _Checked(str(text), reason="outside_store")
    # The top-level name is matched without case, and reported in the
    # store's own spelling: on a case-insensitive volume `Worlds/...` is the
    # same directory (section 8, rule 6). A wrong-case component below it is
    # accepted as the OS resolves it, but its rows are keyed by the spelling
    # the app wrote, so it reads cold; name paths in their own case.
    top = next((name for name in SYNCABLE_ROOTS if name.casefold() == parts[0].casefold()), None)
    if top is None:
        return _Checked(text, reason="not_syncable")
    parts = [top, *parts[1:]]
    rel = "/".join(parts)
    if atomic.is_write_temp(Path(parts[-1])):
        return _Checked(text, rel, reason="temp_file")
    refusal, info = _walk(root, parts)
    if refusal is not None:
        return _Checked(text, rel, reason=refusal)
    if info is None:
        return _Checked(text, rel, kind="absent")
    if stat.S_ISREG(info.st_mode):
        return _Checked(text, rel, kind="file")
    if stat.S_ISDIR(info.st_mode):
        return _Checked(text, rel, kind="dir")
    return _Checked(text, rel, reason="not_a_file")


def _expand(root: Path, rel: str, limit: int) -> tuple[list[str], bool]:
    """The files under a directory, as store-relative paths: temps, links and
    anything `_check` refuses are skipped. Bounded by `limit`; the flag says
    whether the walk stopped looking."""
    found: list[str] = []
    pending = [root.joinpath(*rel.split("/"))]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as listing:
                entries = sorted(listing, key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if _linked(info) or atomic.is_write_temp(Path(entry.name)):
                continue
            if stat.S_ISDIR(info.st_mode):
                pending.append(Path(entry.path))
            elif stat.S_ISREG(info.st_mode):
                if len(found) >= limit:
                    return sorted(found), True
                found.append(Path(os.path.relpath(entry.path, root)).as_posix())
    return sorted(found), False


def _campaign_of(root: Path, rel: str) -> str | None:
    """The campaign a path inside `campaigns/<cid>/` belongs to, while that
    campaign's directory exists. A file directly under `campaigns/`, or a
    whole campaign deleted, has no token to bump."""
    parts = rel.split("/")
    if (len(parts) >= 3 and parts[0] == "campaigns" and paths.safe_id(parts[1])
            and (root / "campaigns" / parts[1]).is_dir()):
        return parts[1]
    return None


def _halted(root: Path, stop: Callable[[], bool]) -> str | None:
    try:
        if stop():
            return "cancelled"
    except Exception:                                     # noqa: BLE001 -- a broken stop is a stop
        return "cancelled"
    if _norm(os.path.abspath(paths.home())) != _norm(os.path.abspath(root)):
        return "root_moved"
    return None


def _root_source(root: Path) -> str:
    try:
        info = paths.data_dir_info()
    except Exception:                                     # noqa: BLE001 -- a label, not a decision
        return "unknown"
    if _norm(info.get("data_dir", "")) != _norm(os.path.abspath(root)):
        return "explicit"
    return str(info.get("source", "unknown"))


def _context(root: Path, mode: str, *, dry_run: bool, verify: bool, embed: bool,
             stop: Callable[[], bool]) -> WarmContext:
    """The only constructor of a `WarmContext`: the space is read here, once."""
    try:
        space = embed_space.endpoint()
    except Exception:                                     # noqa: BLE001 -- unreadable settings embed nothing
        _warn_once("embedding_space")
        space = None
    digest = compiled.space_digest(space["space"]) if space and space.get("space") else None
    return WarmContext(root=root, mode=mode, dry_run=dry_run, verify=verify, embed=embed,
                       space=space, space_digest=digest, stop=stop)


def _rows(root: Path, rel: str) -> tuple[compiled.Materialized, ...] | None:
    """`rel`'s `materialized` rows, read by its path alone (03 section 4), or
    None when the history could not be read -- a busy cache, a read error --
    which must never pass for "nothing was hot"."""
    path = root.joinpath(*rel.split("/"))
    with compiled.batch(root) as request:
        try:
            answer = request.materialized_answer([path])
            return None if answer is None else answer.get(path, ())
        finally:
            request.disabled = True                       # a read: nothing to flush


# -- the primitive (05-C1) ---------------------------------------------------


def sync_paths(relpaths: Iterable[str], *,
               mode: Literal["explicit", "write"],
               embed: bool = True,
               dry_run: bool = False,
               verify: bool = False,
               renamed: Mapping[str, str] | None = None,
               deleted: Iterable[str] = (),
               root: Path | None = None,
               stop: Callable[[], bool] = lambda: False,
               hooks: tuple[WarmHook, ...] | None = None,
               known: Mapping[str, str] | None = None) -> SyncReport:
    """Warm what was hot for `relpaths` (spec 05 section 3.3). Never raises
    for one path's failure -- that is its status; raises only for a
    programming error, such as an unknown mode.

    `renamed` maps OLD to NEW for renames the caller states (section 7.5): OLD's
    `materialized` rows are copied to NEW before it is classified, and OLD is
    an intended delete. `deleted` names removals the caller intended (7.4); a
    world or campaign root among them that is gone purges the cache (7.6).
    `known` carries paths a scope listing already classified as `cold` or
    `current` (7.3), which are reported and bump their campaign's token but
    are not read again."""
    if mode not in MODES:
        raise ValueError(f"unknown sync mode {mode!r}")
    root = Path(root) if root is not None else paths.home()
    hooks = HOOKS if hooks is None else hooks
    report = SyncReport(root=str(root), root_source=_root_source(root), mode=mode, dry_run=dry_run)
    quiet = mode == "write"               # refusals and missing paths are not news to the queue
    items = _classify_arguments(root, relpaths, deleted, report, quiet=quiet,
                                renamed=renamed or {}, known=known or {})

    reason = compiled.unavailable_reason()
    if reason is not None:
        report.cache, report.cache_reason = "off", reason
        _warn_once(f"cache_off:{reason}")

    # Section 10: the app is told about the edit before anything slow can
    # stop the batch -- the bump depends only on validation, never on what
    # was hot -- and only a moved root, never a cancel, skips it.
    if not dry_run and mode == "explicit":
        _bump(root, report)
    copied = _copy_renames(root, items, hooks, dry_run=dry_run) if report.cache == "on" else {}
    hot = _find_hot(root, items, report, stop, copied) if report.stopped is None else {}

    if report.cache == "on" and hot and report.stopped is None:
        _run_hooks(root, mode, hooks, hot, report, embed=embed, dry_run=dry_run,
                   verify=verify, stop=stop)
    if not dry_run and mode == "explicit":
        _purge_roots(root, report, stop)
    for item in report.paths:
        if item.status == "pending":      # the batch stopped before reaching it
            item.status = "unsynced"
    _log.info("cache sync: mode=%s dry_run=%s %s", mode, dry_run,
              " ".join(f"{k}={v}" for k, v in report.counts().items() if v))
    return report


def _find_hot(root: Path, items: list[PathReport], report: SyncReport, stop: Callable[[], bool],
              copied: Mapping[str, tuple[compiled.Materialized, ...]]) -> dict[str, tuple[compiled.Materialized, ...]]:
    """Each pending path's `materialized` rows, read by its own path; a path
    with none is `cold`, and costs nothing more. `copied` holds a dry run's
    rename rows, which were never written."""
    hot: dict[str, tuple[compiled.Materialized, ...]] = {}
    for item in items:
        if item.status != "pending" or item.rel is None:
            continue
        if (halt := _halted(root, stop)) is not None:
            report.stopped = halt
            break
        rows: tuple[compiled.Materialized, ...] | None
        if item.rel in copied:
            rows = copied[item.rel]
        else:
            rows = _rows(root, item.rel) if report.cache == "on" else ()
        if rows is None:
            _warn_once("history_unreadable")
            item.status = "unsynced"
        elif rows:
            hot[item.rel] = rows
        else:
            item.status = "cold"
    return hot


class _Collector:
    """The batch's path list as it is built: coalesced by store-relative path,
    and quiet about refusals and missing paths in `write` mode."""

    def __init__(self, report: SyncReport, quiet: bool) -> None:
        self.report, self.quiet = report, quiet
        self.seen: set[str] = set()
        self.items: list[PathReport] = []
        self.accepted = 0

    def add(self, item: PathReport) -> bool:
        """Record `item`; False when it repeats a path already in the batch."""
        if item.rel is not None:
            if item.rel in self.seen:
                return False
            self.seen.add(item.rel)
        if self.quiet and item.status in ("refused", "missing"):
            return True
        self.report.paths.append(item)
        self.items.append(item)
        return True

    def full(self, given: str) -> bool:
        """Past `MAX_PATHS`: refuse `given` as `too_many` and say so."""
        if self.accepted < MAX_PATHS:
            return False
        self.report.truncated = True
        self.add(PathReport(os.fspath(given), "refused", "too_many"))
        return True

    def deleted(self, root: Path, given: str) -> None:
        if self.full(given):
            return
        self.accepted += 1
        checked = _check(root, given)
        if checked.reason is not None:
            self.add(PathReport(checked.given, "refused", checked.reason, rel=checked.rel))
        elif checked.kind != "absent":
            self.add(PathReport(checked.given, "refused", "not_deleted", rel=checked.rel))
        else:
            self.add(PathReport(checked.given, "deleted", rel=checked.rel))

    def renamed(self, root: Path, old: str, new: str) -> None:
        """OLD is an intended delete; NEW is a path that inherits OLD's rows."""
        before = len(self.items)
        self.deleted(root, old)
        gone = self.items[before:] and self.items[-1].status == "deleted"
        checked = _check(root, new)
        if checked.reason is not None:
            self.add(PathReport(checked.given, "refused", checked.reason, rel=checked.rel))
        elif checked.kind == "absent":
            self.add(PathReport(checked.given, "missing", rel=checked.rel))
        elif checked.kind != "file":
            self.add(PathReport(checked.given, "refused", "not_a_file", rel=checked.rel))
        else:
            self.accepted += self.add(PathReport(checked.given, "pending", rel=checked.rel,
                                                 renamed_from=self.items[before].rel if gone else None))

    def argument(self, root: Path, given: str) -> None:
        if self.full(given):
            return
        checked = _check(root, given)
        if checked.reason is not None:
            self.add(PathReport(checked.given, "refused", checked.reason, rel=checked.rel))
        elif checked.kind == "absent":
            self.add(PathReport(checked.given, "missing", rel=checked.rel))
        elif checked.kind == "dir" and checked.rel is not None:
            files, cut = _expand(root, checked.rel, MAX_PATHS - self.accepted)
            self.report.truncated = self.report.truncated or cut
            for rel in files:
                self.accepted += self.add(PathReport(rel, "pending", rel=rel))
        else:
            self.accepted += self.add(PathReport(checked.given, "pending", rel=checked.rel))


def _classify_arguments(root: Path, relpaths: Iterable[str], deleted: Iterable[str],
                        report: SyncReport, *, quiet: bool, renamed: Mapping[str, str],
                        known: Mapping[str, str]) -> list[PathReport]:
    collector = _Collector(report, quiet)
    for old, new in renamed.items():
        collector.renamed(root, old, new)
    for given in deleted:
        collector.deleted(root, given)
    for given in relpaths:
        collector.argument(root, given)
    for rel, status in known.items():
        if status in ("cold", "current"):
            collector.add(PathReport(rel, status, rel=rel))
    return collector.items


def _remapper(hooks: tuple[WarmHook, ...], old: str, new: str) -> Callable[[str, str], object]:
    """The instance map a stated rename copies rows through: each hook remaps
    the path-dependent part of its own kinds' instances (`remap`, optional),
    and keeps independent unit selectors such as a passage position."""
    def remap(kind: str, instance: str) -> object:
        for hook in hooks:
            if hook.owns(kind) and hasattr(hook, "remap"):
                return hook.remap(kind, instance, old, new)
        return instance
    return remap


def _copy_renames(root: Path, items: list[PathReport], hooks: tuple[WarmHook, ...], *,
                  dry_run: bool) -> dict[str, tuple[compiled.Materialized, ...]]:
    """Give each stated rename's NEW path OLD's rows (03-C3's
    `copy_materialized`: same kinds and units, empty `built_from`, no
    artifact). A dry run writes nothing, so it returns the rows instead."""
    copied: dict[str, tuple[compiled.Materialized, ...]] = {}
    for item in items:
        if item.status != "pending" or item.renamed_from is None or item.rel is None:
            continue
        old, new = root.joinpath(*item.renamed_from.split("/")), root.joinpath(*item.rel.split("/"))
        remap = _remapper(hooks, item.renamed_from, item.rel)
        try:
            if dry_run:
                rows = []
                for row in _rows(root, item.renamed_from) or ():
                    mapped = remap(row.kind, row.instance)
                    text = mapped if isinstance(mapped, str) else _canonical(mapped)
                    rows.append(compiled.Materialized(new, row.kind, text, "", row.last_used))
                copied[item.rel] = tuple(rows)
            else:
                with compiled.batch(root) as request:
                    request.copy_materialized(old, new, instance_map=remap)
        except Exception as exc:                          # noqa: BLE001 -- a lost copy is a cold path
            _warn_once(f"rename:{type(exc).__name__}")
    return copied


_ROOT_SCOPES = ("worlds", "campaigns")


def _purge_roots(root: Path, report: SyncReport, stop: Callable[[], bool]) -> None:
    """Section 7.6: a world or campaign root named in `deleted` and gone means
    a whole record left the store outside the app, so its derived text must
    not outlive it. Only an explicit `deleted` root ever purges -- a scope flag
    or a plain path naming a missing root is `not_found` or `missing` -- since
    03-C8's purge makes every device start cold."""
    named = [item for item in report.paths if item.status == "deleted" and item.rel is not None
             and len(parts := item.rel.split("/")) == 2 and parts[0] in _ROOT_SCOPES]
    if not named:
        return
    if (halt := _halted(root, stop)) is not None:
        report.stopped = report.stopped or halt
        report.purged = False
        return
    compiled.purge_for_delete(root)
    report.purged = True


def _bump(root: Path, report: SyncReport) -> None:
    """Section 10: once per campaign per batch, before any rebuild, for every
    path that is not refused or missing -- hot, cold or an intended delete --
    in a campaign that still exists. A cancel does not skip it (it costs one
    atomic write per campaign); a moved root does, since `revision.bump`
    resolves the root itself."""
    cids = sorted({cid for item in report.paths
                   if item.rel is not None and item.status not in ("refused", "missing")
                   and (cid := _campaign_of(root, item.rel)) is not None})
    for cid in cids:
        if _halted(root, lambda: False) is not None:
            report.stopped = "root_moved"
            return
        revision.bump(cid)
        report.tokens_bumped += 1


def _run_hooks(root: Path, mode: str, hooks: tuple[WarmHook, ...],
               hot: Mapping[str, tuple[compiled.Materialized, ...]], report: SyncReport, *,
               embed: bool, dry_run: bool, verify: bool, stop: Callable[[], bool]) -> None:
    by_rel = {item.rel: item for item in report.paths if item.rel is not None and item.rel in hot}
    _mark_lazy(hooks, hot, by_rel)
    if mode == "explicit":
        _lane.acquire()
    elif not _lane.acquire(timeout=LANE_WAIT_S):
        report.stopped = "lane_busy"
        return
    try:
        ctx = _context(root, mode, dry_run=dry_run, verify=verify, embed=embed, stop=stop)
        plans = _local_phases(ctx, hooks, hot, by_rel, report)
        if not dry_run and embed:
            _network_phases(ctx, plans, by_rel, report)
    finally:
        _lane.release()
    for rel, item in by_rel.items():
        if item.status != "pending":
            continue
        reached = all(row.kind in item.kinds for row in hot[rel])
        item.status = _status(item) if report.stopped is None or reached else "unsynced"


def _mark_lazy(hooks: tuple[WarmHook, ...], hot: Mapping[str, tuple[compiled.Materialized, ...]],
               by_rel: Mapping[str, PathReport]) -> None:
    """A materialized kind no hook owns is never rebuilt: `lazy`."""
    for rel, rows in hot.items():
        for row in rows:
            if not any(hook.owns(row.kind) for hook in hooks):
                by_rel[rel].kinds[row.kind] = "lazy"


def _local_phases(ctx: WarmContext, hooks: tuple[WarmHook, ...],
                  hot: Mapping[str, tuple[compiled.Materialized, ...]],
                  by_rel: Mapping[str, PathReport], report: SyncReport) -> list[tuple[WarmHook, object]]:
    plans: list[tuple[WarmHook, object]] = []
    for hook in hooks:
        if (halt := ctx.halted()) is not None:
            report.stopped = halt
            break
        mine = {rel: owned for rel, rows in hot.items()
                if (owned := tuple(r for r in rows if hook.owns(r.kind)))}
        if not mine:
            continue
        summary = report.hooks.setdefault(hook.name, {})
        if ctx.mode == "write" and hook.on_write == "explicit":
            _merge(by_rel, {(rel, row.kind): "deferred" for rel, rows in mine.items() for row in rows},
                   summary)
            continue
        try:
            result = hook.local(ctx, mine)
        except Exception as exc:                          # noqa: BLE001 -- one hook's failure is a status
            _warn_once(f"hook:{hook.name}")
            result = LocalResult(outcomes={(rel, row.kind): f"failed:{type(exc).__name__}"
                                           for rel, rows in mine.items() for row in rows})
        _merge(by_rel, result.outcomes, summary)
        if (halt := ctx.halted()) is not None:     # the hook may have stopped part-way
            report.stopped = halt
        for rel in result.verify_failed & by_rel.keys():
            by_rel[rel].status = "verify_failed"
        _add_embedding(report, result.embedding)
        if result.plan is not None:
            plans.append((hook, result.plan))
    return plans


def _network_phases(ctx: WarmContext, plans: list[tuple[WarmHook, object]],
                    by_rel: Mapping[str, PathReport], report: SyncReport) -> None:
    for hook, plan in plans:
        if report.stopped is not None:
            return
        if (halt := ctx.halted()) is not None:
            report.stopped = halt
            return
        try:
            net = hook.network(ctx, plan)
        except Exception as exc:                          # noqa: BLE001 -- see `_local_phases`
            _warn_once(f"hook:{hook.name}:network")
            report.embedding["failure"] = type(exc).__name__
            continue
        _merge(by_rel, net.outcomes, report.hooks.setdefault(hook.name, {}))
        _add_embedding(report, net.embedding)


def _add_embedding(report: SyncReport, figures: Mapping[str, object]) -> None:
    """Counts add up; anything else (a failure kind) is the latest word."""
    for key, value in figures.items():
        current = report.embedding.get(key, 0)
        if isinstance(value, int) and isinstance(current, int):
            report.embedding[key] = current + value
        elif isinstance(value, dict):
            merged = dict(current) if isinstance(current, dict) else {}
            for name, count in value.items():
                merged[name] = merged.get(name, 0) + count
            report.embedding[key] = merged
        else:
            report.embedding[key] = value


def _merge(by_rel: Mapping[str, PathReport], outcomes: Mapping[tuple[str, str], str],
           summary: dict) -> None:
    kinds = set(summary.get("kinds", ()))
    for (rel, kind), outcome in outcomes.items():
        item = by_rel.get(rel)
        if item is None:
            continue
        item.kinds[kind] = _worse(item.kinds.get(kind), outcome) if kind in item.kinds else outcome
        kinds.add(kind)
        word = outcome.split(":", 1)[0]
        summary[word] = summary.get(word, 0) + 1
    summary["kinds"] = sorted(kinds)


def _status(item: PathReport) -> str:
    words = {outcome.split(":", 1)[0] for outcome in item.kinds.values()}
    if "failed" in words:
        return "failed"
    if words & {"built", "planned"}:
        return "refreshed"
    return "current"


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


def _scope_dirs(root: Path, campaigns: Iterable[str], worlds: Iterable[str], all_records: bool,
                result: ScopeResult) -> list[str]:
    dirs: list[str] = []
    for top, ids in (("campaigns", campaigns), ("worlds", worlds)):
        for given in ids:
            if not paths.safe_id(given):
                result.refused.append(PathReport(str(given), "refused", "bad_id"))
                continue
            rel = f"{top}/{given}"
            refusal, info = _walk(root, [top, given])
            if refusal is not None:
                result.refused.append(PathReport(rel, "refused", refusal, rel=rel))
            elif info is None or not stat.S_ISDIR(info.st_mode):
                result.refused.append(PathReport(rel, "refused", "not_found", rel=rel))
            else:
                dirs.append(rel)
    if all_records:
        for top in sorted(SYNCABLE_ROOTS):
            refusal, info = _walk(root, [top])
            if refusal is None and info is not None and stat.S_ISDIR(info.st_mode):
                dirs.append(top)
    return dirs


def expand_scopes(root: Path, *, campaigns: Iterable[str] = (), worlds: Iterable[str] = (),
                  all_records: bool = False, stop: Callable[[], bool] = lambda: False) -> ScopeResult:
    """The files under `--campaign`, `--world` and `--all` that may have
    changed, found from a live listing and never from the cache.

    A file with no `materialized` rows is `cold` and is not read. A file whose
    stamp a `sources` row vouches for (`known_hashes`, which stats and never
    reads) and every one of whose rows was built from that hash is `current`
    and is not read either. A `sources` row alone proves only that SOME read
    hashed the file since the edit, not that recall's vector or the overview
    card was rebuilt, which is why every row's `built_from` must agree; an
    empty one (a rename copy) never does. Everything else is a candidate."""
    result = ScopeResult()
    listed: list[str] = []
    for rel in _scope_dirs(root, campaigns, worlds, all_records, result):
        files, cut = _expand(root, rel, MAX_SCOPE_FILES - len(listed))
        listed.extend(f for f in files if f.split("/", 1)[0] in SYNCABLE_ROOTS)
        result.truncated = result.truncated or cut
    listed = list(dict.fromkeys(listed))
    if compiled.unavailable_reason() is not None:
        result.known = dict.fromkeys(listed, "cold")
        return result
    with compiled.batch(root) as request:
        try:
            for offset in range(0, len(listed), compiled.SOURCE_CHUNK):
                if _halted(root, stop) is not None:
                    break
                chunk = listed[offset:offset + compiled.SOURCE_CHUNK]
                absolute = [root.joinpath(*rel.split("/")) for rel in chunk]
                hashes = request.known_hashes(compiled.SYNC_SOURCES, absolute)
                for rel, path in zip(chunk, absolute, strict=True):
                    rows = request.materialized([path]).get(path, ())
                    digest = hashes.get(path)
                    if not rows:
                        result.known[rel] = "cold"
                    elif digest and all(row.built_from == digest for row in rows):
                        result.known[rel] = "current"
                    else:
                        result.candidates.append(rel)
        finally:
            request.disabled = True                       # a listing writes nothing
    return result


def run(relpaths: Iterable[str] = (), *, campaigns: Iterable[str] = (), worlds: Iterable[str] = (),
        all_records: bool = False, renamed: Mapping[str, str] | None = None,
        deleted: Iterable[str] = (), embed: bool = True, dry_run: bool = False,
        verify: bool = False, root: Path | None = None,
        stop: Callable[[], bool] = lambda: False) -> SyncReport:
    """One explicit batch over named paths and scopes: the CLI's and the API's
    door. The batch is the union of what was named."""
    root = Path(root) if root is not None else paths.home()
    campaigns, worlds = list(campaigns), list(worlds)
    scoped = bool(campaigns or worlds or all_records)
    scope = expand_scopes(root, campaigns=campaigns, worlds=worlds, all_records=all_records,
                          stop=stop) if scoped else ScopeResult()
    report = sync_paths([*relpaths, *scope.candidates], mode="explicit", embed=embed,
                        dry_run=dry_run, verify=verify, renamed=renamed, deleted=deleted,
                        root=root, stop=stop, known=scope.known)
    report.paths.extend(scope.refused)
    report.truncated = report.truncated or scope.truncated
    if scoped:
        report.listed = "notable"
    return report


# -- scripts (section 5.5) ----------------------------------------------------


@dataclass
class Collected:
    """What `collecting()` yields: the batch's report, once the block ends."""

    report: SyncReport | None = None


def _relative_to(root: Path, written: Iterable[str]) -> list[str]:
    """The write set's absolute paths as store-relative ones; an entry that is
    relative or outside `root` is dropped."""
    base = os.path.normcase(os.path.abspath(root)).rstrip(os.sep)
    out = []
    for text in written:
        if not os.path.isabs(text):
            continue
        full = os.path.normpath(text)
        if os.path.normcase(full).startswith(base + os.sep):
            out.append(Path(os.path.relpath(full, os.path.abspath(root))).as_posix())
    return sorted(out)


def syncable(root: Path, written: Iterable[str]) -> list[str]:
    """The write set's paths that a write-through batch could warm: inside
    `root`, under a syncable record directory, and not a write temp. The
    queue filters with this before anything is looked up, so the server's own
    ledgers, logs, keys and top-level files never reach a batch at all."""
    return [rel for rel in _relative_to(root, written)
            if rel.split("/", 1)[0] in SYNCABLE_ROOTS and "/" in rel
            and not atomic.is_write_temp(Path(rel.rsplit("/", 1)[-1]))]


@contextlib.contextmanager
def collecting(*, embed: bool = True, deleted: Iterable[str] = ()) -> Iterator[Collected]:
    """Sync what the block wrote through `grimoire.store`, once it ends.

    For a script, which has no app and so no write-through queue. Opens an
    ISOLATED write-set scope, and on exit closes it and runs one explicit
    batch over the paths written (under the root read at entry), leaving the
    report on the yielded object to print. On an exception it syncs what was
    written anyway -- a half-applied batch still changed those files -- and
    re-raises. Deletes are not in the write set: pass them as `deleted`."""
    root = paths.home()
    held = Collected()
    deleted = list(deleted)
    with writeset.collecting(isolated=True) as scope:
        try:
            yield held
        except BaseException:
            try:
                held.report = sync_paths(_relative_to(root, scope.close()), mode="explicit",
                                         embed=embed, deleted=deleted, root=root)
            except Exception:                             # noqa: BLE001 -- never mask the real failure
                _warn_once("collecting")
            raise
        held.report = sync_paths(_relative_to(root, scope.close()), mode="explicit",
                                 embed=embed, deleted=deleted, root=root)
