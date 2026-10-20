"""Bounded live-path expansion with link and containment proofs."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Container, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import atomic, paths
from . import _contracts

_REPARSE_POINT = 0x400


def _campaign_named(rel: PurePosixPath) -> str | None:
    """The campaign a campaign-layer path sits in, by its path alone."""
    parts = rel.parts
    return parts[1] if len(parts) >= 3 and parts[0] == "campaigns" else None


def _scene_named(rel: PurePosixPath) -> tuple[str, str] | None:
    """`(campaign, sid)` for a scene file's path, by its path alone."""
    parts = rel.parts
    if len(parts) == 4 and parts[0] == "campaigns" and parts[2] == "scenes" \
            and parts[3].endswith(".md"):
        return parts[1], parts[3][:-3]
    return None


# -- path validation (section 8) ---------------------------------------------


@dataclass(frozen=True)
class _Checked:
    given: str
    rel: str | None = None
    reason: str | None = None
    kind: str = ""          # "file", "dir", "absent" or "" when refused


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
    top = next((name for name in _contracts.SYNCABLE_ROOTS if name.casefold() == parts[0].casefold()), None)
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


@dataclass
class _Walk:
    """A directory's files, and whether the walk saw all of them: `cut` when
    it stopped looking (the limit, or a stop), `unread` when a directory or
    entry under it could not be read, so part of the tree went unexamined."""

    files: list[str] = field(default_factory=list)
    cut: bool = False
    unread: bool = False


def _expand(root: Path, rel: str, limit: int,
            stop: Callable[[], bool] = lambda: False, skip: Container[str] = ()) -> _Walk:
    """The files under a directory, as store-relative paths: temps, links and
    anything `_check` refuses are skipped. Bounded by `limit`, counting only
    files not in `skip` (paths the batch already holds), and asks `stop`
    before each directory it lists."""
    walk = _Walk()
    pending = [root.joinpath(*rel.split("/"))]
    while pending and not walk.cut:
        if stop():
            walk.cut = True
            break
        directory = pending.pop()
        try:
            with os.scandir(directory) as listing:
                entries = sorted(listing, key=lambda e: e.name)
        except OSError:
            walk.unread = True
            continue
        for entry in entries:
            _visit(root, entry, walk, pending, limit, skip)
            if walk.cut:
                break
    walk.files.sort()
    return walk


def _visit(root: Path, entry: os.DirEntry, walk: _Walk, pending: list[Path], limit: int,
           skip: Container[str]) -> None:
    """One directory entry of `_expand`'s walk: a directory to list next, a
    file to take while the limit allows, or something skipped."""
    try:
        info = entry.stat(follow_symlinks=False)
    except OSError:
        walk.unread = True
        return
    if _linked(info) or atomic.is_write_temp(Path(entry.name)):
        return
    if stat.S_ISDIR(info.st_mode):
        pending.append(Path(entry.path))
    elif stat.S_ISREG(info.st_mode):
        found = Path(os.path.relpath(entry.path, root)).as_posix()
        if found in skip:
            return
        if len(walk.files) >= limit:
            walk.cut = True
            return
        walk.files.append(found)


def _campaign_of(root: Path, rel: str) -> str | None:
    """The campaign a path inside `campaigns/<cid>/` belongs to, while that
    campaign's directory exists. A file directly under `campaigns/`, or a
    whole campaign deleted, has no token to bump."""
    parts = rel.split("/")
    if (len(parts) >= 3 and parts[0] == "campaigns" and paths.safe_id(parts[1])
            and (root / "campaigns" / parts[1]).is_dir()):
        return parts[1]
    return None


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
            if rel.split("/", 1)[0] in _contracts.SYNCABLE_ROOTS and "/" in rel
            and not atomic.is_write_temp(Path(rel.rsplit("/", 1)[-1]))]
