"""Pinned-root safety boundaries, no-clobber outbox publication and bounded local/foreign outbox listings."""

from __future__ import annotations

import contextlib
import errno
import os
import stat
from pathlib import Path

from .. import image_refs, image_store, maintenance_reports, paths, thumbs
from . import _contracts

# ---- paths -----------------------------------------------------------------

def _gc_dir(root: Path) -> Path:
    return Path(root).joinpath(*_contracts._GC_DIR)


def _tokens_dir(root: Path) -> Path:
    return _gc_dir(root) / "tokens"


def token_path(root: Path, token: str) -> Path:
    """Where the dry run that issued `token` recorded it. ValueError for a
    string that is not a token, so nothing else reaches a path."""
    if not isinstance(token, str) or not _contracts._TOKEN.fullmatch(token):
        raise ValueError("not a collection token")
    return _tokens_dir(root) / f"{token}.json"


def blob_thumbnails(root: Path, sha: str) -> list[Path]:
    """Every thumbnail path `thumbs` could have made of blob `sha` at this
    revision: each bucket width, both encoders, every suffix. Most are not
    there; the caller deletes the ones that are."""
    return [Path(root) / rel for rel in thumbs.blob_keys(sha)]


def _rel(root: Path, path: Path) -> str:
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _blob_key(sha: str, ext: str) -> str:
    return f"{sha}.{ext}"


def _split_key(key: str) -> tuple[str, str] | None:
    m = _contracts._BLOB_NAME.fullmatch(key)
    return (m.group(1), m.group(2)) if m and m.group(2) in image_store.MIME else None


def outbox_dir(root: Path) -> Path:
    """This device's outbox for this store: where a collected picture waits,
    under its bin name, between leaving ``blobs/`` and reaching the recycle
    bin. Per device key, so a synced ``.cache`` never hands one device's
    waiting pictures to another's collector."""
    return Path(root).joinpath(*_contracts._RECYCLE_DIR, maintenance_reports.device_key(root))


def bin_name(description: str | None, sha: str, ext: str) -> str:
    """The file name a collected picture reaches the bin under:
    ``grimoire - <description> - <sha8>.<ext>``, or ``grimoire - <sha8>.<ext>``
    when there is no description or it sanitises to nothing. The description
    loses what a Windows file name cannot hold and its control characters, has
    its whitespace collapsed, is cut to `BIN_DESCRIPTION_CHARS` at a word
    boundary where one exists (and to `BIN_DESCRIPTION_BYTES`), and loses
    trailing dots and spaces."""
    text = ""
    if isinstance(description, str):
        text = " ".join(description.split())
        text = " ".join(_contracts._NOT_IN_A_NAME.sub("", text).split())
        if len(text) > _contracts.BIN_DESCRIPTION_CHARS:
            head = text[:_contracts.BIN_DESCRIPTION_CHARS]
            if text[_contracts.BIN_DESCRIPTION_CHARS] != " " and " " in head:
                head = head[:head.rindex(" ")]
            text = head
        while len(text.encode("utf-8")) > _contracts.BIN_DESCRIPTION_BYTES:
            text = text[:-1]
        text = text.rstrip(". ")
    sha8 = sha[:8]
    return f"grimoire - {text} - {sha8}.{ext}" if text else f"grimoire - {sha8}.{ext}"


# ---- the pinned root ----------------------------------------------------------

def _live_root() -> Path | None:
    try:
        return paths.home().resolve()
    except (OSError, RuntimeError):
        return None


def _check_root(root: Path) -> None:
    if _live_root() != root:
        raise _contracts._RootChangedError()


def _delete_file(root: Path, path: Path) -> int:
    """Unlink one file the collector built the path of, after checking the
    live root is still `root`. Returns the bytes freed, 0 when it was not
    there. Only under `_DELETABLE`, never through a link, never anything but a
    regular file (`_UnsafePathError` otherwise)."""
    _check_root(root)
    rel = Path(path).relative_to(root).parts      # ValueError: not ours at all
    if not any(rel[:len(p)] == p for p in _contracts._DELETABLE):
        raise _contracts._UnsafePathError(Path(path))
    cur = Path(root)
    for part in rel[:-1]:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return 0
        if not stat.S_ISDIR(st.st_mode):        # a link, or anything else
            raise _contracts._UnsafePathError(Path(path))
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(st.st_mode):
        raise _contracts._UnsafePathError(Path(path))
    try:
        os.unlink(path)
    except FileNotFoundError:
        return 0
    return st.st_size


# ---- the outbox -------------------------------------------------------------------

def _real_dirs(root: Path, target: Path, *, create: bool) -> bool:
    """Whether every component from `root` down to `target` is a real
    directory (never a link). A missing one is made when `create`, else the
    answer is False; anything else there raises `_UnsafePathError`."""
    cur = Path(root)
    for part in Path(target).relative_to(root).parts:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            if not create:
                return False
            with contextlib.suppress(FileExistsError):
                os.mkdir(cur)
            st = os.lstat(cur)
        if not stat.S_ISDIR(st.st_mode):       # a link, or anything else
            raise _contracts._UnsafePathError(cur)
    return True


def _free_name(outbox: Path, name: str) -> Path:
    """`name` in `outbox`, or the first of ``<stem> (2).<ext>``,
    ``<stem> (3).<ext>``, ... that nothing there holds yet."""
    stem, dot, ext = name.rpartition(".")
    target, n = outbox / name, 2
    while os.path.lexists(target):
        target = outbox / (f"{stem} ({n}).{ext}" if dot else f"{name} ({n})")
        n += 1
    return target


def _move_to_outbox(root: Path, blob: Path, name: str) -> Path | None:
    """Move one blob into this device's outbox under bin name `name` (or a
    suffixed one), after checking the live root is still `root`: the blob a
    regular file under ``blobs/`` and the outbox a real directory, neither
    reached through a link (`_UnsafePathError` otherwise). Returns where it
    went, or None when the blob was already gone.

    The move never replaces a file (`_no_clobber_move`): `_free_name` only says
    a name was free when it looked, a sync client can deliver a file there
    before the move lands, and a file in the outbox may be the only copy left
    of an earlier collection the bin refused. A taken name moves on to the
    next suffix, up to `MOVE_TRIES` names, and then the run stops with
    FileExistsError and the blob in place. Any other failure of the move --
    hard links unsupported here (EPERM, ENOTSUP, EXDEV, ...) among them --
    stops the run the same way: there is no fallback to a replacing move."""
    _check_root(root)
    rel = Path(blob).relative_to(root).parts     # ValueError: not ours at all
    if rel[:len(_contracts._BLOBS_DIR)] != _contracts._BLOBS_DIR or len(rel) != len(_contracts._BLOBS_DIR) + 2:
        raise _contracts._UnsafePathError(Path(blob))
    if not _real_dirs(root, Path(blob).parent, create=False):
        return None
    try:
        st = os.lstat(blob)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise _contracts._UnsafePathError(Path(blob))
    outbox = outbox_dir(root)
    _real_dirs(root, outbox, create=True)
    for _ in range(_contracts.MOVE_TRIES):
        target = _free_name(outbox, name)
        try:
            _no_clobber_move(blob, target)
        except FileExistsError:
            continue               # it arrived after `_free_name` looked: next name
        except FileNotFoundError:
            if os.path.lexists(blob):
                raise              # the outbox went, not the blob: stop, blob in place
            return None
        return target
    raise FileExistsError(errno.EEXIST, "no free name in the outbox", str(outbox / name))


def _no_clobber_move(src: Path, dst: Path) -> None:
    """Move `src` to `dst`, raising FileExistsError when `dst` exists rather
    than replacing it. On Windows `os.rename` is that move. Elsewhere it is
    ``os.link`` -- atomic, and refusing a taken name -- and then the unlink of
    `src`. A crash (or a failed unlink, which raises) between the two leaves
    the picture under both names, which loses nothing: the next collection
    drains the outbox copy to the bin and, finding the object still there,
    moves the blob again under a suffixed name, so both copies reach the bin.
    A blob someone else unlinked after the link is not an error: the picture
    is in the outbox."""
    if _contracts._RENAME_REFUSES_EXISTING:
        os.rename(src, dst)
        return
    os.link(src, dst, follow_symlinks=False)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(src)


def _outbox_files(root: Path) -> list[Path]:
    """The regular files waiting in this device's outbox, by name. Raises
    `_UnsafePathError` when the way to it runs through a link."""
    outbox = outbox_dir(root)
    if not _real_dirs(root, outbox, create=False):
        return []
    with os.scandir(outbox) as it:
        return sorted(Path(e.path) for e in it
                      if e.is_file(follow_symlinks=False)
                      and e.name.casefold() not in image_refs.OS_LITTER)


def _outbox_listing(root: Path) -> tuple[list[str], int]:
    """(this device's waiting file names, how many files wait in other
    devices' outboxes). Read-only and fail-soft: an outbox reached through a
    link lists nothing (the scan blocks on it), and one that does not list
    counts as empty."""
    try:
        mine = [p.name for p in _outbox_files(root)]
    except (OSError, _contracts._UnsafePathError):
        mine = []
    recycle = Path(root).joinpath(*_contracts._RECYCLE_DIR)
    own = outbox_dir(root).name
    foreign = 0
    try:
        if not _real_dirs(root, recycle, create=False):
            return mine, 0
        with os.scandir(recycle) as it:
            others = [Path(e.path) for e in it
                      if e.name != own and e.is_dir(follow_symlinks=False)]
        for d in others:
            with os.scandir(d) as it:
                foreign += sum(1 for e in it if e.is_file(follow_symlinks=False))
    except (OSError, _contracts._UnsafePathError):
        pass
    return mine, foreign
