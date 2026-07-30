"""World path resolution and existence/identity checks."""

from __future__ import annotations

from pathlib import Path

from ..paths import home, safe_id


class WorldNotFound(Exception):
    pass


def _worlds_dir() -> Path:
    return home() / "worlds"


def world_root(wid: str) -> Path:
    """The world's directory.

    Raises WorldNotFound for an id that doesn't name a child of the worlds dir
    -- including "", which would otherwise resolve to the worlds dir itself.
    The guard lives here rather than in the router so a caller that isn't an
    HTTP path parameter (a body field, a CLI script, an importer) gets it too.
    """
    if not safe_id(wid):
        raise WorldNotFound(wid)
    return _worlds_dir() / wid


def world_meta_path(wid: str) -> Path:
    return world_root(wid) / "world.md"


def world_exists(wid: str) -> bool:
    """Existence check that survives an id `world_root` refuses to resolve.

    Callers testing "is there such a world?" want False for an unusable id,
    not an exception -- an id that can't name a world dir is exactly as absent
    as one that names a missing dir.
    """
    try:
        return world_meta_path(wid).exists()
    except WorldNotFound:
        return False


def names_its_directory(root: Path) -> bool:
    """True when ``root.name`` is how the filesystem itself spells that entry.

    Windows and macOS match paths case-insensitively, so a lookup can succeed
    under a spelling the store does not use: ``worlds/REALM`` opens
    ``worlds/realm``. Harmless for a read, dangerous for anything that deletes
    by an id or compares it against stored references -- ``delete_world`` did
    both, so ``DELETE /api/worlds/REALM`` found the world, compared the raw
    ``REALM`` against campaigns' stored ``realm``, and destroyed a world in use
    (#259 review). Asking the directory listing rather than lower-casing keeps
    this correct per filesystem: a genuinely distinct ``REALM`` on a
    case-sensitive one is still its own world.
    """
    try:
        return any(p.name == root.name for p in root.parent.iterdir())
    except OSError:
        return False


def canonical_id(wid: str) -> str:
    """`wid` respelled the way the filesystem holds it, or unchanged.

    `worlds/REALM` and `worlds/realm` are one directory on Windows and macOS,
    so a reference stored under either spelling points at the same world.
    Canonicalizing on the way in keeps every later comparison a plain string
    compare; `references_world` is what covers the ones already stored.
    """
    try:
        root = world_root(wid)
    except WorldNotFound:
        return wid
    try:
        for p in root.parent.iterdir():
            if p.name == wid or (p.is_dir() and p.samefile(root)):
                return p.name
    except OSError:
        pass
    return wid


def references_world(ref: str, root: Path) -> bool:
    """Does a campaign's stored world reference point at `root`?

    Not a string compare: a store written before `create_campaign`
    canonicalized can hold `REALM` for the directory `realm`, and missing that
    is what let `delete_world("realm")` destroy a world still inherited by a
    campaign (#259 review). `samefile` asks the filesystem instead of guessing
    at its case or normalization rules.
    """
    if not ref:
        return False
    try:
        other = world_root(ref)
    except WorldNotFound:
        return False            # cannot name a world at all, so not this one
    if other == root:
        return True
    try:
        return other.samefile(root)
    except OSError:
        return False            # a dangling reference pins nothing
