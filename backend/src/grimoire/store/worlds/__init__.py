"""World meta CRUD. A world is a directory of entity kind-folders + world.md."""

from __future__ import annotations

# Submodules before names, and `paths`/`read` before `lifecycle`: `lifecycle`
# imports `campaigns.read`, which imports back into `worlds.paths` -- so
# `paths` (and `read`, imported alongside it) must already be bound as
# attributes on this package before `lifecycle`'s import runs, or the
# in-progress `campaigns.read` import (itself triggered from here) would see
# an as-yet-unbound `worlds.paths`.
from . import paths, read, lifecycle  # noqa: F401
from .paths import (  # noqa: F401
    WorldNotFound, _worlds_dir, canonical_id, names_its_directory,
    references_world, world_exists, world_meta_path, world_root,
)
from .read import has_worlds, list_worlds, read_world, world_name  # noqa: F401
from .lifecycle import WorldInUse, create_world, delete_world, rename_world  # noqa: F401
