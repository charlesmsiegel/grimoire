"""Bounded scope listing and explicit/collected warm-up entry adapters."""

from __future__ import annotations

import contextlib
import stat
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path

from .. import compiled, paths, writeset
from . import _contracts, _engine, _paths, _signals


def _scope_dirs(root: Path, campaigns: Iterable[str], worlds: Iterable[str], all_records: bool,
                result: _contracts.ScopeResult) -> list[str]:
    dirs: list[str] = []
    selectors = [(top, given) for top, ids in (("campaigns", campaigns), ("worlds", worlds))
                 for given in dict.fromkeys(ids)]
    if len(selectors) > _contracts.MAX_PATHS:        # one refusal stands for the rest, never examined
        result.refused.append(_contracts.PathReport(str(selectors[_contracts.MAX_PATHS][1]), "refused", "too_many"))
        result.truncated = True
        selectors = selectors[:_contracts.MAX_PATHS]
    for top, given in selectors:
        if not paths.safe_id(given):
            result.refused.append(_contracts.PathReport(str(given), "refused", "bad_id"))
            continue
        rel = f"{top}/{given}"
        refusal, info = _paths._walk(root, [top, given])
        if refusal is not None:
            result.refused.append(_contracts.PathReport(rel, "refused", refusal, rel=rel))
        elif info is None or not stat.S_ISDIR(info.st_mode):
            result.refused.append(_contracts.PathReport(rel, "refused", "not_found", rel=rel))
        else:
            dirs.append(rel)
    if all_records:
        for top in sorted(_contracts.SYNCABLE_ROOTS):
            refusal, info = _paths._walk(root, [top])
            if refusal is None and info is not None and stat.S_ISDIR(info.st_mode):
                dirs.append(top)
    return dirs


def expand_scopes(root: Path, *, campaigns: Iterable[str] = (), worlds: Iterable[str] = (),
                  all_records: bool = False, stop: Callable[[], bool] = lambda: False) -> _contracts.ScopeResult:
    """The files under `--campaign`, `--world` and `--all` that may have
    changed, found from a live listing and never from the cache.

    A file with no `materialized` rows is `cold` and is not read. A file whose
    stamp a `sources` row vouches for (`known_hashes`, which stats and never
    reads) and every one of whose rows was built from that hash is `current`
    and is not read either. A `sources` row alone proves only that SOME read
    hashed the file since the edit, not that recall's vector or the overview
    card was rebuilt, which is why every row's `built_from` must agree; an
    empty one (a rename copy) never does. Everything else is a candidate."""
    result = _contracts.ScopeResult()
    listed = _list_scopes(root, campaigns, worlds, all_records, stop, result)
    if result.stopped is not None:
        return result
    if compiled.unavailable_reason() is not None or not compiled.published(root):
        result.known = dict.fromkeys(listed, "cold")
        return result
    with compiled.batch(root) as request:
        try:
            for offset in range(0, len(listed), compiled.SOURCE_CHUNK):
                if (halt := _signals._halted(root, stop)) is not None:
                    result.stopped, result.truncated = halt, True
                    break
                _classify_chunk(request, root, listed[offset:offset + compiled.SOURCE_CHUNK], result)
        finally:
            request.disabled = True                       # a listing writes nothing
    return result


def _list_scopes(root: Path, campaigns: Iterable[str], worlds: Iterable[str], all_records: bool,
                 stop: Callable[[], bool], result: _contracts.ScopeResult) -> list[str]:
    """The scopes' files, bounded by `MAX_SCOPE_FILES`. A stop or a moved root
    while listing ends the listing and says so on `result`."""
    halt: list[str] = []

    def halted() -> bool:
        if not halt and (reason := _signals._halted(root, stop)) is not None:
            halt.append(reason)
        return bool(halt)

    listed: list[str] = []
    for rel in _disjoint(_scope_dirs(root, campaigns, worlds, all_records, result)):
        walk = _paths._expand(root, rel, _contracts.MAX_SCOPE_FILES - len(listed), halted)
        listed.extend(f for f in walk.files if f.split("/", 1)[0] in _contracts.SYNCABLE_ROOTS)
        result.truncated = result.truncated or walk.cut
        if walk.unread:
            result.refused.append(_contracts.PathReport(rel, "refused", "unreadable", rel=rel))
        if halt:
            result.stopped = halt[0]
            break
        if walk.cut:                      # past `MAX_SCOPE_FILES`: the rest was never listed
            result.refused.append(_contracts.PathReport(rel, "refused", "too_many", rel=rel))
            break
    return listed


def _disjoint(dirs: Iterable[str]) -> list[str]:
    """Scope directories with repeats and nested ones dropped (`--world realm
    --all` lists `worlds` once), so no walk lists a file another already
    listed and no file is charged to the scope limit twice."""
    kept: list[str] = []
    for rel in sorted(set(dirs), key=lambda d: (d.count("/"), d)):
        if not any(rel == top or rel.startswith(top + "/") for top in kept):
            kept.append(rel)
    return kept


def _classify_chunk(request: compiled.Batch, root: Path, chunk: list[str], result: _contracts.ScopeResult) -> None:
    """One chunk of a scope listing: one stat-only hash read and one history
    read for the chunk, path by path only when the chunk's history together
    is past the read budget."""
    absolute = [root.joinpath(*rel.split("/")) for rel in chunk]
    hashes = request.known_hashes(compiled.SYNC_SOURCES, absolute)
    answer = request.materialized_answer(absolute, split=True)
    for rel, path in zip(chunk, absolute, strict=True):
        if answer is compiled.HISTORY_OVER_BUDGET:
            alone = request.materialized_answer([path])
            rows = alone.get(path, ()) if alone is not None else None
        else:
            rows = answer.get(path, ()) if answer is not None else None
        digest = hashes.get(path)
        if rows is None:
            result.candidates.append(rel)       # unanswered: read it, never call it cold
        elif not rows:
            result.known[rel] = "cold"
        elif digest and all(row.built_from == digest for row in rows):
            result.known[rel] = "current"
        else:
            result.candidates.append(rel)


def run(relpaths: Iterable[str] = (), *, campaigns: Iterable[str] = (), worlds: Iterable[str] = (),
        all_records: bool = False, renamed: Mapping[str, str] | None = None,
        deleted: Iterable[str] = (), embed: bool = True, dry_run: bool = False,
        verify: bool = False, root: Path | None = None,
        stop: Callable[[], bool] = lambda: False,
        progress: Callable[[dict], None] | None = None) -> _contracts.SyncReport:
    """One explicit batch over named paths and scopes: the CLI's and the API's
    door. The batch is the union of what was named."""
    root = Path(root) if root is not None else paths.home()
    campaigns, worlds = list(campaigns), list(worlds)
    scoped = bool(campaigns or worlds or all_records)
    scope = expand_scopes(root, campaigns=campaigns, worlds=worlds, all_records=all_records,
                          stop=stop) if scoped else _contracts.ScopeResult()
    candidates, known = scope.candidates, scope.known
    if verify:
        # `current` is proved from stamps and `built_from` alone; `--verify`
        # exists to check what a rebuild would make, so it reads those too.
        candidates = candidates + [rel for rel, status in known.items() if status == "current"]
        known = {rel: status for rel, status in known.items() if status != "current"}
    report = _engine.sync_paths(list(relpaths), listed=candidates, mode="explicit", embed=embed,
                        dry_run=dry_run, verify=verify, renamed=renamed, deleted=deleted,
                        root=root, stop=stop, known=known, progress=progress)
    report.paths.extend(scope.refused)
    report.truncated = report.truncated or scope.truncated
    report.stopped = report.stopped or scope.stopped
    if scoped:
        report.listed = "notable"
    return report


@contextlib.contextmanager
def collecting(*, embed: bool = True, deleted: Iterable[str] = ()) -> Iterator[_contracts.Collected]:
    """Sync what the block wrote through `grimoire.store`, once it ends.

    For a script, which has no app and so no write-through queue. Opens an
    ISOLATED write-set scope, and on exit closes it and runs one explicit
    batch over the paths written (under the root read at entry), leaving the
    report on the yielded object to print. On an exception it syncs what was
    written anyway -- a half-applied batch still changed those files -- and
    re-raises. Deletes are not in the write set: pass them as `deleted`."""
    root = paths.home()
    held = _contracts.Collected()
    deleted = list(deleted)
    with writeset.collecting(isolated=True) as scope:
        try:
            yield held
        except BaseException:
            try:
                held.report = _engine.sync_paths(_paths._relative_to(root, scope.close()), mode="explicit",
                                         embed=embed, deleted=deleted, root=root)
            except Exception:                             # noqa: BLE001 -- never mask the real failure
                _signals._warn_once("collecting")
            raise
        held.report = _engine.sync_paths(_paths._relative_to(root, scope.close()), mode="explicit",
                                 embed=embed, deleted=deleted, root=root)
