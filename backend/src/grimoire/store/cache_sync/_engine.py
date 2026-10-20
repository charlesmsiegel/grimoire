"""Fixed hook order, one endpoint context and phased materialization warm-up."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Literal

from .. import compiled, embed_space, paths, revision, vectors
from . import _contracts, _files, _other_hooks, _paths, _signals, _vectors

#: The hooks, in their fixed order: `files`, `overview` (04-C2a), `searchdocs`
#: (08-C2c), `vectors`. A tuple naming each hook by module rather than a
#: registry filled by import side effects, so a process that imports only this
#: module -- the CLI -- still has every hook.
HOOKS: tuple[_contracts.WarmHook, ...] = (_files.FILES, _other_hooks.OVERVIEW, _other_hooks.SEARCHDOCS, _vectors.VECTORS)


def _context(root: Path, mode: str, *, dry_run: bool, verify: bool, embed: bool,
             stop: Callable[[], bool]) -> _contracts.WarmContext:
    """The only constructor of a `WarmContext`: the space is read here, once."""
    try:
        space = embed_space.endpoint()
    except Exception:                                     # noqa: BLE001 -- unreadable settings embed nothing
        _signals._warn_once("embedding_space")
        space = None
    digest = compiled.space_digest(space["space"]) if space and space.get("space") else None
    return _contracts.WarmContext(root=root, mode=mode, dry_run=dry_run, verify=verify, embedding=embed,
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
               hooks: tuple[_contracts.WarmHook, ...] | None = None,
               known: Mapping[str, str] | None = None,
               listed: Iterable[str] = (),
               progress: Callable[[dict], None] | None = None) -> _contracts.SyncReport:
    """Warm what was hot for `relpaths` (spec 05 section 3.3). Never raises
    for one path's failure -- that is its status; raises only for a
    programming error, such as an unknown mode.

    `renamed` maps OLD to NEW for renames the caller states (section 7.5): OLD's
    `materialized` rows are copied to NEW before it is classified, and OLD is
    an intended delete. `deleted` names removals the caller intended (7.4); a
    world or campaign root among them that is gone purges the cache (7.6).
    `known` carries paths a scope listing already classified as `cold` or
    `current` (7.3), which are reported and bump their campaign's token but
    are not read again. `progress`, when given, is told the counts (never a
    path) as the batch passes each phase -- the API run's progress frames."""
    if mode not in _contracts.MODES:
        raise ValueError(f"unknown sync mode {mode!r}")
    root = Path(root) if root is not None else paths.home()
    hooks = HOOKS if hooks is None else hooks
    report = _contracts.SyncReport(root=str(root), root_source=_signals._root_source(root), mode=mode, dry_run=dry_run)
    # History a turn parked under its lock is what makes its paths hot. A dry
    # run writes nothing, so it leaves the parked rows for the next handoff.
    if not dry_run:
        vectors.flush_deferred()
    quiet = mode == "write"               # refusals and missing paths are not news to the queue
    items = _classify_arguments(root, relpaths, deleted, report, quiet=quiet,
                                renamed=renamed or {}, known=known or {}, listed=listed)

    reason = compiled.unavailable_reason()
    if reason is not None:
        report.cache, report.cache_reason = "off", reason
        _signals._warn_once(f"cache_off:{reason}")

    # Section 10: the app is told about the edit before anything slow can
    # stop the batch -- the bump depends only on validation, never on what
    # was hot -- and only a moved root, never a cancel, skips it.
    if not dry_run and mode == "explicit":
        _bump(root, report)
        # Section 7.6's purge erases the whole cache, so it runs before any
        # rebuild: rebuilding first would report paths refreshed whose rows
        # the purge then erased. After it nothing is hot.
        _purge_roots(root, report, stop)
    hot = _classify_hot(root, items, hooks, report, stop, dry_run=dry_run)
    _progress(progress, "classified", report)

    if report.cache == "on" and hot and report.stopped is None:
        _run_hooks(root, mode, hooks, hot, report, embed=embed, dry_run=dry_run,
                   verify=verify, stop=stop)
    _progress(progress, "rebuilt", report)
    for item in report.paths:
        if item.status == "pending":      # the batch stopped before reaching it
            item.status = "unsynced"
    _signals._log.info("cache sync: mode=%s dry_run=%s %s", mode, dry_run,
              " ".join(f"{k}={v}" for k, v in report.counts().items() if v))
    return report


def _classify_hot(root: Path, items: list[_contracts.PathReport], hooks: tuple[_contracts.WarmHook, ...], report: _contracts.SyncReport,
                  stop: Callable[[], bool], *, dry_run: bool) -> dict[str, tuple[compiled.Materialized, ...]]:
    """What was hot, once stated renames carried their rows over. A batch
    that purged has nothing hot: every pending path is `cold`."""
    if report.purged or not compiled.published(root):
        # Purged, or never published: no history anywhere, so nothing is hot
        # -- and a batch is never opened, which would create the store.
        if report.stopped is None and (halt := _signals._halted(root, stop)) is not None:
            report.stopped = halt                 # left pending, so `unsynced`
            return {}
        for item in items:
            if item.status == "pending":
                item.status = "cold"
        return {}
    copied = _copy_renames(root, items, hooks, dry_run=dry_run) if report.cache == "on" else {}
    return _find_hot(root, items, report, stop, copied) if report.stopped is None else {}


def _progress(progress: Callable[[dict], None] | None, phase: str, report: _contracts.SyncReport) -> None:
    if progress is None:
        return
    try:
        pending = sum(item.status == "pending" for item in report.paths)
        progress({"phase": phase, "counts": {k: v for k, v in report.counts().items() if v},
                  "pending": pending, "tokens_bumped": report.tokens_bumped})
    except Exception:                                     # noqa: BLE001 -- a frame is not the batch
        _signals._warn_once("progress")


def _find_hot(root: Path, items: list[_contracts.PathReport], report: _contracts.SyncReport, stop: Callable[[], bool],
              copied: Mapping[str, tuple[compiled.Materialized, ...]]) -> dict[str, tuple[compiled.Materialized, ...]]:
    """Each pending path's `materialized` rows, read by its own path; a path
    with none is `cold`, and costs nothing more. `copied` holds a dry run's
    rename rows, which were never written."""
    hot: dict[str, tuple[compiled.Materialized, ...]] = {}
    for item in items:
        if item.status != "pending" or item.rel is None:
            continue
        if (halt := _signals._halted(root, stop)) is not None:
            report.stopped = halt
            break
        rows: tuple[compiled.Materialized, ...] | None
        if item.rel in copied:
            rows = copied[item.rel]
        else:
            rows = _rows(root, item.rel) if report.cache == "on" else ()
        if rows is None:
            _signals._warn_once("history_unreadable")
            item.status = "unsynced"
        elif rows:
            hot[item.rel] = rows
        else:
            item.status = "cold"
    return hot


class _Collector:
    """The batch's path list as it is built: coalesced by store-relative path,
    and quiet about refusals and missing paths in `write` mode."""

    def __init__(self, report: _contracts.SyncReport, quiet: bool) -> None:
        self.report, self.quiet = report, quiet
        self.seen: set[str] = set()
        self.items: list[_contracts.PathReport] = []
        self.accepted = 0
        #: Whether the batch hit `MAX_PATHS`: one `too_many` refusal stands for
        #: everything past it, which is never examined.
        self.capped = False

    def add(self, item: _contracts.PathReport) -> bool:
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
        """Past `MAX_PATHS`: refuse the first path past it as `too_many` and
        say so; the caller then stops reading selectors, so the report stays
        bounded however many were sent."""
        if self.accepted < _contracts.MAX_PATHS:
            return False
        if not self.capped:
            self.capped = self.report.truncated = True
            self.add(_contracts.PathReport(os.fspath(given), "refused", "too_many"))
        return True

    def repeats(self, checked: _paths._Checked) -> bool:
        """A path already in the batch: coalesced before the quota is charged."""
        return checked.rel is not None and checked.rel in self.seen

    def deleted(self, root: Path, given: str) -> None:
        checked = _paths._check(root, given)
        if self.repeats(checked) or self.full(given):
            return
        if checked.reason is not None:
            item = _contracts.PathReport(checked.given, "refused", checked.reason, rel=checked.rel)
        elif checked.kind != "absent":
            item = _contracts.PathReport(checked.given, "refused", "not_deleted", rel=checked.rel)
        else:
            item = _contracts.PathReport(checked.given, "deleted", rel=checked.rel)
        self.accepted += self.add(item)

    def renamed(self, root: Path, old: str, new: str) -> None:
        """OLD is an intended delete; NEW is a path that inherits OLD's rows.
        Both sides count against the batch's quota."""
        if self.full(old):
            return
        before = len(self.items)
        self.deleted(root, old)
        gone = self.items[before:] and self.items[-1].status == "deleted"
        if gone:
            self.items[-1].from_rename = True
        if self.full(new):
            return
        checked = _paths._check(root, new)
        if checked.reason is not None:
            self.accepted += self.add(_contracts.PathReport(checked.given, "refused", checked.reason, rel=checked.rel))
        elif checked.kind == "absent":
            self.accepted += self.add(_contracts.PathReport(checked.given, "missing", rel=checked.rel))
        elif checked.kind != "file":
            self.accepted += self.add(_contracts.PathReport(checked.given, "refused", "not_a_file", rel=checked.rel))
        else:
            self.accepted += self.add(_contracts.PathReport(checked.given, "pending", rel=checked.rel,
                                                 renamed_from=self.items[before].rel if gone else None))

    def listed(self, root: Path, rel: str) -> None:
        """A scope listing's candidate: held to section 8 like any path, but
        bounded by `MAX_SCOPE_FILES` where it was listed, not by `MAX_PATHS`."""
        checked = _paths._check(root, rel)
        if checked.reason is not None:
            self.add(_contracts.PathReport(checked.given, "refused", checked.reason, rel=checked.rel))
        elif checked.kind == "file":
            self.add(_contracts.PathReport(checked.given, "pending", rel=checked.rel))

    def argument(self, root: Path, given: str) -> None:
        # Every distinct selector counts against `MAX_PATHS` -- a refused or
        # missing one too -- so the report is bounded whatever was sent.
        checked = _paths._check(root, given)
        if self.repeats(checked) or self.full(given):
            return
        if checked.reason is not None:
            self.accepted += self.add(_contracts.PathReport(checked.given, "refused", checked.reason, rel=checked.rel))
        elif checked.kind == "absent":
            self.accepted += self.add(_contracts.PathReport(checked.given, "missing", rel=checked.rel))
        elif checked.kind == "dir" and checked.rel is not None:
            walk = _paths._expand(root, checked.rel, _contracts.MAX_PATHS - self.accepted, skip=self.seen)
            for rel in walk.files:
                self.accepted += self.add(_contracts.PathReport(rel, "pending", rel=rel))
            if walk.cut:                  # the files past the quota were never looked at
                self.report.truncated = True
                self.add(_contracts.PathReport(checked.given, "refused", "too_many"))
            if walk.unread:               # part of it was never examined
                self.accepted += self.add(_contracts.PathReport(checked.given, "refused", "unreadable"))
        else:
            self.accepted += self.add(_contracts.PathReport(checked.given, "pending", rel=checked.rel))


def _classify_arguments(root: Path, relpaths: Iterable[str], deleted: Iterable[str],
                        report: _contracts.SyncReport, *, quiet: bool, renamed: Mapping[str, str],
                        known: Mapping[str, str], listed: Iterable[str] = ()) -> list[_contracts.PathReport]:
    collector = _Collector(report, quiet)
    for old, new in renamed.items():
        if collector.capped:
            break
        collector.renamed(root, old, new)
    for given in deleted:
        if collector.capped:
            break
        collector.deleted(root, given)
    for given in relpaths:
        if collector.capped:
            break
        collector.argument(root, given)
    for rel in listed:
        collector.listed(root, rel)
    for rel, status in known.items():
        if status in ("cold", "current"):
            collector.add(_contracts.PathReport(rel, status, rel=rel))
    return collector.items


def _remapper(hooks: tuple[_contracts.WarmHook, ...], old: str, new: str) -> Callable[[str, str], object]:
    """The instance map a stated rename copies rows through: each hook remaps
    the path-dependent part of its own kinds' instances (`remap`, optional),
    and keeps independent unit selectors such as a passage position."""
    def remap(kind: str, instance: str) -> object:
        for hook in hooks:
            if hook.owns(kind) and hasattr(hook, "remap"):
                return hook.remap(kind, instance, old, new)
        return instance
    return remap


def _copy_renames(root: Path, items: list[_contracts.PathReport], hooks: tuple[_contracts.WarmHook, ...], *,
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
                found = _rows(root, item.renamed_from)
                answered = found is not None
                copied[item.rel] = tuple(
                    compiled.Materialized(new, row.kind, _remapped(remap, row), "", row.last_used)
                    for row in found or ())
            else:
                with compiled.batch(root) as request:
                    answered = request.copy_materialized(old, new, instance_map=remap)
        except Exception as exc:                          # noqa: BLE001 -- a lost copy is a cold path
            _signals._warn_once(f"rename:{type(exc).__name__}")
            continue
        if not answered:                                  # OLD's history went unanswered: never cold
            _signals._warn_once("history_unreadable")
            item.status = "unsynced"
    return copied


def _remapped(remap: Callable[[str, str], object], row: compiled.Materialized) -> str:
    mapped = remap(row.kind, row.instance)
    return mapped if isinstance(mapped, str) else _contracts._canonical(mapped)


_ROOT_SCOPES = ("worlds", "campaigns")


def _purge_roots(root: Path, report: _contracts.SyncReport, stop: Callable[[], bool]) -> None:
    """Section 7.6: a world or campaign root named in `deleted` and gone means
    a whole record left the store outside the app, so its derived text must
    not outlive it. Only an explicit `deleted` root ever purges -- a scope flag
    or a plain path naming a missing root is `not_found` or `missing` -- since
    03-C8's purge makes every device start cold."""
    named = [item for item in report.paths if item.status == "deleted" and item.rel is not None
             and not item.from_rename
             and len(parts := item.rel.split("/")) == 2 and parts[0] in _ROOT_SCOPES]
    if not named:
        return
    if (halt := _signals._halted(root, stop)) is not None:
        report.stopped = report.stopped or halt
        report.purged = False
        return
    report.purged = compiled.purge_for_delete(root)
    if not report.purged:                 # the request was never published: not done
        for item in named:
            item.status = "failed"


def _bump(root: Path, report: _contracts.SyncReport) -> None:
    """Section 10: once per campaign per batch, before any rebuild, for every
    path that is not refused or missing -- hot, cold or an intended delete --
    in a campaign that still exists. A cancel does not skip it (it costs one
    atomic write per campaign); a moved root does, since `revision.bump`
    resolves the root itself."""
    cids = sorted({cid for item in report.paths
                   if item.rel is not None and item.status not in ("refused", "missing")
                   and (cid := _paths._campaign_of(root, item.rel)) is not None})
    for cid in cids:
        if _signals._halted(root, lambda: False) is not None:
            report.stopped = "root_moved"
            return
        # `bump` never raises; a token it could not write comes back as the
        # initial one, and the app was then not told.
        if revision.bump(cid) == revision.INITIAL:
            report.tokens_failed += 1
        else:
            report.tokens_bumped += 1


def _run_hooks(root: Path, mode: str, hooks: tuple[_contracts.WarmHook, ...],
               hot: Mapping[str, tuple[compiled.Materialized, ...]], report: _contracts.SyncReport, *,
               embed: bool, dry_run: bool, verify: bool, stop: Callable[[], bool]) -> None:
    by_rel = {item.rel: item for item in report.paths if item.rel is not None and item.rel in hot}
    _mark_lazy(hooks, hot, by_rel)
    if (halt := _signals._take_lane(root, mode, stop)) is not None:
        report.stopped = halt
        return
    try:
        ctx = _context(root, mode, dry_run=dry_run, verify=verify, embed=embed, stop=stop)
        plans = _local_phases(ctx, hooks, hot, by_rel, report)
        if not dry_run and embed:
            _network_phases(ctx, plans, by_rel, report)
    finally:
        _signals._lane.release()
    _settle_paths(hot, by_rel, report, dry_run=dry_run)


def _settle_paths(hot: Mapping[str, tuple[compiled.Materialized, ...]],
                  by_rel: Mapping[str, _contracts.PathReport], report: _contracts.SyncReport, *, dry_run: bool) -> None:
    """After releasing the lane, distinguish completed paths from unreached work."""
    for rel, item in by_rel.items():
        if item.status != "pending":
            continue
        reached = all(row.kind in item.kinds for row in hot[rel])
        # A kind still `planned` after a real batch was never sent: a stop,
        # or a network phase that raised.
        unsent = not dry_run and any(o == "planned" for o in item.kinds.values())
        item.status = _status(item) if (report.stopped is None or reached) and not unsent else "unsynced"


def _mark_lazy(hooks: tuple[_contracts.WarmHook, ...], hot: Mapping[str, tuple[compiled.Materialized, ...]],
               by_rel: Mapping[str, _contracts.PathReport]) -> None:
    """A materialized kind no hook owns is never rebuilt: `lazy`."""
    for rel, rows in hot.items():
        for row in rows:
            if not any(hook.owns(row.kind) for hook in hooks):
                by_rel[rel].kinds[row.kind] = "lazy"


def _local_phases(ctx: _contracts.WarmContext, hooks: tuple[_contracts.WarmHook, ...],
                  hot: Mapping[str, tuple[compiled.Materialized, ...]],
                  by_rel: Mapping[str, _contracts.PathReport], report: _contracts.SyncReport) -> list[tuple[_contracts.WarmHook, object]]:
    plans: list[tuple[_contracts.WarmHook, object]] = []
    for hook in hooks:
        if (halt := ctx.halted()) is not None:
            report.stopped = halt
            break
        mine = {rel: owned for rel, rows in hot.items()
                if (owned := tuple(r for r in rows if hook.owns(r.kind)))}
        if not mine:
            continue
        if (plan := _local_phase(ctx, hook, mine, by_rel, report)) is not None:
            plans.append((hook, plan))
    return plans


def _local_phase(ctx: _contracts.WarmContext, hook: _contracts.WarmHook,
                 mine: Mapping[str, tuple[compiled.Materialized, ...]],
                 by_rel: Mapping[str, _contracts.PathReport], report: _contracts.SyncReport) -> object:
    """Run and account for one owner's local work; return only its network plan."""
    summary = report.hooks.setdefault(hook.name, {})
    if ctx.mode == "write" and hook.on_write == "explicit":
        _merge(by_rel, {(rel, row.kind): "deferred" for rel, rows in mine.items() for row in rows},
               summary)
        return None
    try:
        result = hook.local(ctx, mine)
    except Exception as exc:                              # noqa: BLE001 -- one hook's failure is a status
        _signals._warn_once(f"hook:{hook.name}")
        result = _contracts.LocalResult(outcomes={(rel, row.kind): f"failed:{type(exc).__name__}"
                                       for rel, rows in mine.items() for row in rows})
    _merge(by_rel, result.outcomes, summary)
    if (halt := ctx.halted()) is not None:         # the hook may have stopped part-way
        report.stopped = halt
    for rel in result.verify_failed & by_rel.keys():
        by_rel[rel].status = "verify_failed"
    _add_embedding(report, result.embedding)
    return result.plan


def _network_phases(ctx: _contracts.WarmContext, plans: list[tuple[_contracts.WarmHook, object]],
                    by_rel: Mapping[str, _contracts.PathReport], report: _contracts.SyncReport) -> None:
    for hook, plan in plans:
        if report.stopped is not None:
            return
        if (halt := ctx.halted()) is not None:
            report.stopped = halt
            return
        try:
            net = hook.network(ctx, plan)
        except Exception as exc:                          # noqa: BLE001 -- see `_local_phase`
            _signals._warn_once(f"hook:{hook.name}:network")
            report.embedding["failure"] = type(exc).__name__
            continue
        _merge(by_rel, net.outcomes, report.hooks.setdefault(hook.name, {}), final=True)
        _add_embedding(report, net.embedding)
        if net.failure:
            report.embedding["failure"] = net.failure
        for rel in net.verify_failed & by_rel.keys():
            by_rel[rel].status = "verify_failed"
    if report.stopped is None and (halt := ctx.halted()) is not None:
        report.stopped = halt                     # stopped inside a network phase


def _add_embedding(report: _contracts.SyncReport, figures: Mapping[str, object]) -> None:
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


def _merge(by_rel: Mapping[str, _contracts.PathReport], outcomes: Mapping[tuple[str, str], str],
           summary: dict, *, final: bool = False) -> None:
    """Fold a phase's outcomes into the paths. A network phase's outcome is
    `final`: it replaces the local phase's `planned` for the same kind."""
    kinds = set(summary.get("kinds", ()))
    for (rel, kind), outcome in outcomes.items():
        item = by_rel.get(rel)
        if item is None:
            continue
        previous = item.kinds.get(kind)
        if final and previous is not None:            # a replacement, never a second count
            old = previous.split(":", 1)[0]
            summary[old] = summary.get(old, 0) - 1
            if summary[old] <= 0:
                del summary[old]
        item.kinds[kind] = (outcome if final or previous is None else _contracts._worse(previous, outcome))
        kinds.add(kind)
        word = outcome.split(":", 1)[0]
        summary[word] = summary.get(word, 0) + 1
    summary["kinds"] = sorted(kinds)


def _status(item: _contracts.PathReport) -> str:
    words = {outcome.split(":", 1)[0] for outcome in item.kinds.values()}
    if "failed" in words:
        return "failed"
    if words & {"built", "planned"}:
        return "refreshed"
    return "current"
