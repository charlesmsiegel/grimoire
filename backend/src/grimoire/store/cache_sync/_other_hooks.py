"""Overview and SearchDocument adapters with pinned-library ownership."""

from __future__ import annotations

import contextlib
import contextvars
import json
from collections.abc import Callable, Iterator, Mapping
from pathlib import PurePosixPath
from typing import Literal

from .. import compiled, paths
from ..overview import warm as overview_warm
from ..searchdocs import hook as searchdocs_hook
from . import _contracts, _paths, _signals, _vectors

# -- the overview hook (05-S5) -------------------------------------------------

#: `overview.warm_paths`' answers, as outcomes: "warmed" is said only once a
#: read-back found what was stored, "none" means no persisted overview kind
#: reads the path now (a short scene, an unregistered kind, a file gone), and
#: "failed" is a store that did not land.
_OVERVIEW_OUTCOMES = {"warmed": "built", "none": "skipped", "failed": "failed:not_saved"}


class _OverviewHook:
    """04's overview composites (04-C2a), wrapped (section 6.2): `local` hands
    the hot paths to `overview.warm_paths` in ONE call, which coalesces them
    by instance -- edits to a campaign's five continuity ledgers warm one
    `continuity_summary` -- and maps its per-path answer to every overview
    kind the path holds. It has no network phase. `warm_paths` reads every
    file back, holds no lock and never raises; it reads its own result back
    before it says "warmed", so `--verify` has nothing more to check here."""

    name = "overview"
    on_write: Literal["quiet", "explicit"] = "quiet"

    def owns(self, kind: str) -> bool:
        return kind in compiled.OVERVIEW_CANDIDATES

    def local(self, ctx: _contracts.WarmContext,
              hot: Mapping[str, tuple[compiled.Materialized, ...]]) -> _contracts.LocalResult:
        result = _contracts.LocalResult()
        if ctx.dry_run:                                   # `warm_paths` stores: plan only
            result.outcomes = {(rel, row.kind): "planned" for rel, rows in hot.items() for row in rows}
            return result
        if ctx.halted():
            return result
        answered = overview_warm.warm_paths(sorted(hot))
        for rel, rows in hot.items():
            outcome = _OVERVIEW_OUTCOMES.get(answered.get(rel, "failed"), "failed:not_saved")
            for row in rows:
                result.outcomes[(rel, row.kind)] = outcome
        return result

    def network(self, ctx: _contracts.WarmContext, plan: object) -> _contracts.NetworkResult:
        return _contracts.NetworkResult()


OVERVIEW = _OverviewHook()


@contextlib.contextmanager
def _pinned(ctx: _contracts.WarmContext) -> Iterator[Callable[[Callable[[], object]], object]]:
    """Run a hook's work against the batch's root: every store resolver --
    the campaign it reads, the compiled cache and vector store it writes --
    is pinned to `ctx.root`, so a pointer moved mid-hook cannot send one
    library's document into another's cache. Yields a runner for the checks
    that must still see the move: under the pin `paths.home()` answers the
    pinned root, so `ctx.halted` and `_root_moved` are run in the context
    captured before it."""
    outer = contextvars.copy_context()
    with paths.pin_library(ctx.root):
        yield lambda check: outer.copy().run(check)


class _SearchDocsHook:
    """08's scene SearchDocuments (08-C2c), wrapped: `searchdocs.hook` does
    the work, since it may not import this module. `explicit`: an app write
    re-renders lazily on the next read at the cost of a slice and a digest,
    so only an explicit sync -- an edit made outside the app -- rebuilds
    eagerly. `local` rebuilds the documents the hot rows name and plans the
    re-embedding of those embedded in the batch's space whose text moved;
    `network` sends it, `REINDEX_LIMIT` a campaign."""

    name = "searchdocs"
    on_write: Literal["quiet", "explicit"] = "explicit"

    def owns(self, kind: str) -> bool:
        return searchdocs_hook.owns(kind)

    def remap(self, kind: str, instance: str, old: str, new: str) -> object:
        """A stated rename. A scene from before the identity backfill names
        itself on its own file by `sid`, which is the file's stem
        (`campaigns/<cid>/scenes/<sid>.md`), so that row follows the move;
        an identity does not move. A campaign-layer file is read by its own
        campaign alone, so a move into one names the destination, as the
        `vectors` hook's does; the copy coalesces rows that then coincide."""
        try:
            value = json.loads(instance) if instance else None
        except ValueError:
            return instance
        if not isinstance(value, dict):
            return instance
        before, after = PurePosixPath(old), PurePosixPath(new)
        moved = dict(value)
        own = _paths._scene_named(before)
        if own is not None and (value.get("campaign"), value.get("sid")) == own \
                and (destination := _paths._scene_named(after)) is not None:
            moved["campaign"], moved["sid"] = destination
        elif (campaign := _paths._campaign_named(after)) is not None:
            moved["campaign"] = campaign
        return moved

    def local(self, ctx: _contracts.WarmContext,
              hot: Mapping[str, tuple[compiled.Materialized, ...]]) -> _contracts.LocalResult:
        with _pinned(ctx) as unpinned:
            got = searchdocs_hook.rebuild(hot, space=ctx.space, space_digest=ctx.space_digest,
                                          embedding=ctx.embedding, dry_run=ctx.dry_run,
                                          halted=lambda: unpinned(ctx.halted))
        return _contracts.LocalResult(outcomes=_contracts._fold(got.outcomes), plan=got.plan, embedding=got.embedding)

    def network(self, ctx: _contracts.WarmContext, plan: object) -> _contracts.NetworkResult:
        result = _contracts.NetworkResult()
        assert isinstance(plan, dict) and ctx.space is not None
        planned = [(rel, kind) for entries in plan.values() for _doc, keys in entries
                   for rel, kind in keys]
        if _vectors._VectorsHook._role_moved(ctx):
            # As the `vectors` hook: never send into a space the role has
            # moved to since the batch read it.
            result.outcomes = dict.fromkeys(planned, "skipped")
            result.embedding["stale_space"] = sum(len(entries) for entries in plan.values())
            return result
        with _pinned(ctx) as unpinned:
            got = searchdocs_hook.reembed(
                plan, space=ctx.space, halted=lambda: unpinned(ctx.halted),
                root_moved=lambda: bool(unpinned(lambda: _signals._root_moved(ctx.root))))
        result.outcomes = _contracts._fold(got.outcomes)
        result.embedding = got.embedding
        result.failure = got.failure
        return result


SEARCHDOCS = _SearchDocsHook()
