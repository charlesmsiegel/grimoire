"""Shared file recipes and the single-file warm-up hook."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .. import compiled
from . import _contracts, _signals

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


def _fresh(recipe: FileRecipe, path: Path, instance: object) -> object:
    """`recipe`'s value for `path`'s current bytes with the compiled layer
    bypassed -- verify's comparison (spec section 7.7)."""
    data = path.read_bytes()
    view = compiled.DerivationInputs(
        (compiled.ComputeInput(recipe.role, hashlib.sha256(data).hexdigest(), data, ()),), ())
    value = recipe.compute(view, recipe.params(path, instance))
    return value.value if isinstance(value, compiled.Uncached) else value


class _FilesHook:
    name = "files"
    on_write: Literal["quiet", "explicit"] = "quiet"

    def owns(self, kind: str) -> bool:
        recipe = FILE_RECIPES.get(kind)
        return recipe is not None and compiled.KINDS.get(kind) == recipe.kind

    def local(self, ctx: _contracts.WarmContext,
              hot: Mapping[str, tuple[compiled.Materialized, ...]]) -> _contracts.LocalResult:
        result = _contracts.LocalResult()
        for rel, rows in hot.items():
            if ctx.halted():
                break
            for row in rows:
                outcome = self._one(ctx, rel, row, result)
                key = (rel, row.kind)
                result.outcomes[key] = _contracts._worse(result.outcomes.get(key), outcome)
        return result

    def _one(self, ctx: _contracts.WarmContext, rel: str, row: compiled.Materialized,
             result: _contracts.LocalResult) -> str:
        recipe = FILE_RECIPES[row.kind]
        path = ctx.path(rel)
        try:
            instance = _contracts._instance(row)
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
            if ctx.verify and _contracts._canonical(value) != _contracts._canonical(_fresh(recipe, path, instance)):
                result.verify_failed.add(rel)
                _signals._log.warning("cache sync verify mismatch in kind %s", row.kind)
            return "built" if ran else "hit"
        except Exception as exc:                          # noqa: BLE001 -- a path's failure is a status
            return f"failed:{type(exc).__name__}"

    def network(self, ctx: _contracts.WarmContext, plan: object) -> _contracts.NetworkResult:
        return _contracts.NetworkResult()


FILES = _FilesHook()
