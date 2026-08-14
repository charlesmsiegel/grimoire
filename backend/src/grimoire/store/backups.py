"""Zipped snapshots of the whole store, and the retention sweep (#32).

The entire app state is plain files under one root (`paths.home()`), so a zip
of that tree *is* a complete, restorable backup — nothing else has to be
consulted and nothing else has to be running. That is the whole design: no
format, no manifest, no incremental chain. An archive is the store, and
restoring is unzipping it into an empty directory and pointing the
Configuration page's Storage location at it.

Three things are deliberately not in the archive:

- **The backup directory itself.** Left in, each archive would swallow every
  archive before it and the series would grow geometrically.
- **`.cache/`.** Derived data (thumbnails today), rebuildable on demand, and
  potentially larger than the library it was derived from.
- **The bootstrap pointer `~/.grimoire.json`.** It lives outside `home()` on
  purpose — it *names* the store — and a restored tree works under whatever
  pointer the machine it lands on happens to have.

What an archive is **not** is a transactional snapshot. Taking one does not
hold the campaign locks: an archive of a large library would hold every
campaign's lock for the length of a zip, stalling play, so a backup taken
while a turn is landing can catch that campaign's transcript and its sidecars
a moment apart. A coarse restore point that is always available beats a
consistent one that blocks the app, and the finer layer is the change journal
(#31), not this.

A read that fails is a failed backup, not a smaller one. Skipping the file and
publishing anyway would produce an archive that claims a completeness it does
not have — the one outcome a backup must never have — so the error propagates
and, because the archive is built into a temp and published by rename
(`atomic.streaming_write`), nothing is left behind for a listing to offer as a
restore point.
"""

from __future__ import annotations

import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import atomic, config, locks
from .paths import home

#: `now_iso()`'s format contains `:`, which is not a filename character on
#: Windows and names an NTFS alternate data stream when it is accepted at all.
_STAMP = "%Y%m%dT%H%M%SZ"
_PREFIX = "grimoire-"
#: What this module recognizes as *its own* archive. Everything the listing
#: shows and everything the sweep may delete has to match: the backup directory
#: is a real directory a user can put things in, and a retention rule that
#: deleted "the oldest file" would eventually delete one of those.
_NAME_RE = re.compile(r"^grimoire-(\d{8}T\d{6}Z)(?:-(\d+))?\.zip$")

#: Rebuildable derived data, relative to the store root.
_DERIVED = ".cache"


def backup_dir() -> Path:
    """Where archives are written: the configured directory, else
    ``home()/backups``.

    Resolved on every call, like `home()` itself — the Storage location can
    change at runtime, and so can this.
    """
    raw = config.backup_dir()
    if raw:
        return Path(raw).expanduser()  # paths-ok: expanding the user's own configured backup path is the setting
    return home() / "backups"


def _norm(path: Path) -> Path:
    """An absolute, lexically-normalized path for comparison only.

    Lexical rather than `resolve()`: the walk yields paths built from `home()`
    as it was handed to us, and resolving one side of a comparison but not the
    other would disagree on any store reached through a symlink (``/tmp`` on
    macOS being the everyday one).
    """
    return Path(os.path.normpath(os.path.abspath(str(path))))


def _skips(root: Path) -> tuple[Path, ...]:
    """Directories the archive never descends into — see the module docstring.

    The backup directory is excluded **only when it is inside the store**, and
    that condition is load-bearing rather than an optimization. Skipping it
    unconditionally would mean that pointing it at an *ancestor* of the store
    (`~`, the most natural choice for "somewhere else") matched every path in
    the walk and produced an empty archive that looked like a backup. Outside
    the store there is nothing to exclude, because nothing there is being
    archived in the first place.
    """
    nroot = _norm(root)
    skips = [_norm(root / _DERIVED)]
    target = _norm(backup_dir())
    if nroot in target.parents:
        skips.append(target)
    return tuple(skips)


def _is_skipped(path: Path, skip: tuple[Path, ...]) -> bool:
    candidate = _norm(path)
    return any(candidate == s or s in candidate.parents for s in skip)


def _is_own_archive(path: Path, directory: Path) -> bool:
    """An archive this module wrote, sitting in the backup directory.

    The one case `_skips` cannot express: a backup directory set to the store
    root itself. Excluding that directory would exclude the whole store, and
    excluding nothing would have each archive swallow every archive before it.
    Excluding them by name costs a user's own `grimoire-<stamp>.zip` parked at
    the store root, which is a trade nobody will ever notice.
    """
    return _NAME_RE.match(path.name) is not None and _norm(path.parent) == _norm(directory)


def _store_file(z: zipfile.ZipFile, path: Path, arcname: str) -> None:
    """One member. Its own function so the failure policy above is testable:
    what happens when a file in the store cannot be read is a decision, and a
    decision needs a seam to exercise it."""
    z.write(path, arcname)


def _archive_into(fh, root: Path, skip: tuple[Path, ...]) -> None:
    """Write the store at `root` into the open binary file `fh`.

    ``os.walk(followlinks=False)``, not ``rglob``: a directory symlink pointing
    at an ancestor makes `rglob` walk forever, and a store is a user-owned
    directory in which such a link is possible. File symlinks *are* followed —
    the content the store reads through them is content a restore needs — but a
    symlinked directory is not descended into, so a link cannot make the
    archive unbounded. Anything that is not a regular file (a broken link, a
    socket, a fifo) is skipped rather than opened.

    Entries are sorted so two archives of an unchanged store list their members
    in the same order.
    """
    directory = backup_dir()
    with zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            here = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames if not _is_skipped(here / d, skip))
            for name in sorted(filenames):
                path = here / name
                if _is_skipped(path, skip) or _is_own_archive(path, directory):
                    continue
                if not path.is_file():
                    continue
                _store_file(z, path, path.relative_to(root).as_posix())


def _utc(when: datetime | None) -> datetime:
    """`when` in UTC. A naive datetime is read as UTC rather than local time:
    every timestamp this module writes or parses is UTC, so interpreting one
    input as local would make an archive's name disagree with its own series."""
    if when is None:
        return datetime.now(timezone.utc)
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def _allocate(when: datetime) -> Path:
    """A free archive path for `when`.

    Two backups in the same second are reachable — a scheduled one and the
    Configuration page's *Back up now* — and the second must not silently
    replace the first, so the name gains a `-2`, `-3`, … Called under the
    backup lock, which is what makes the check-then-create meaningful.
    """
    directory = backup_dir()
    stamp = when.strftime(_STAMP)
    n = 1
    while True:
        name = f"{_PREFIX}{stamp}.zip" if n == 1 else f"{_PREFIX}{stamp}-{n}.zip"
        candidate = directory / name
        if not candidate.exists():
            return candidate
        n += 1


def create_backup(when: datetime | None = None) -> Path:
    """Zip the whole store into a new archive and return its path.

    Raises whatever the filesystem raises; on any failure the target name is
    still free and the listing is unchanged.
    """
    with locks.backup_lock():
        # Under the lock, not before it: `home()` resolves live, so reading it
        # outside could zip one store into another's backup directory if the
        # storage location moved while this call was waiting.
        root = home()
        target = _allocate(_utc(when))
        target.parent.mkdir(parents=True, exist_ok=True)
        with atomic.streaming_write(target) as fh:
            _archive_into(fh, root, _skips(root))
        return target


def _order(name: str) -> tuple[str, int]:
    """Sort key for an archive name: its stamp, then its same-second ordinal.

    The ordinal has to be parsed rather than left to string order — `-2` sorts
    *before* `.zip` bytewise, so plain name sorting inverts every pair of
    archives taken in the same second.
    """
    m = _NAME_RE.match(name)
    assert m is not None                      # callers filter on the same regex
    return m.group(1), int(m.group(2) or 1)


def _created_iso(stamp: str) -> str:
    return datetime.strptime(stamp, _STAMP).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_backups() -> list[dict]:
    """Every archive this module wrote, newest first.

    A missing backup directory is an empty list, not an error: a store that has
    never been backed up has nothing to report, and asking must not create the
    directory. Anything that is not one of our archives is ignored — see
    `_NAME_RE`.
    """
    try:
        entries = list(backup_dir().iterdir())
    except FileNotFoundError:
        return []
    rows = []
    for path in entries:
        m = _NAME_RE.match(path.name)
        if not m:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue                          # vanished, or unreadable: not a restore point
        rows.append({"name": path.name, "size": size,
                     "created": _created_iso(m.group(1))})
    rows.sort(key=lambda r: _order(r["name"]), reverse=True)
    return rows


def sweep(keep: int | None = None) -> list[str]:
    """Delete the oldest archives beyond `keep`, returning their names.

    `keep` defaults to the configured retention count; 0 keeps everything.
    Oldest first, so a sweep that fails part way through has still freed the
    least useful archives. An archive another process removed first is not an
    error — the outcome asked for is the outcome reached.
    """
    if keep is None:
        keep = config.backup_keep()
    if keep <= 0:
        return []
    with locks.backup_lock():
        directory = backup_dir()
        doomed = list(reversed(list_backups()[keep:]))
        for row in doomed:
            try:
                (directory / row["name"]).unlink()
            except FileNotFoundError:
                pass
        return [row["name"] for row in doomed]


def due(now: datetime | None = None) -> bool:
    """Whether the interval has elapsed since the newest archive.

    Derived from the archives themselves rather than a recorded "last run", so
    it survives a restart, a moved store, and two machines sharing one library
    through a synced folder. A store with no archive is always due.
    """
    rows = list_backups()
    if not rows:
        return True
    newest = datetime.strptime(_order(rows[0]["name"])[0], _STAMP).replace(
        tzinfo=timezone.utc)
    return _utc(now) - newest >= timedelta(hours=config.backup_interval_hours())


def run_scheduled(now: datetime | None = None) -> Path | None:
    """One tick of the schedule: back up if enabled and due, then sweep.

    Returns the archive written, or None when there was nothing to do. The
    enabled check and the due check are cheap and happen first, so a tick on a
    store with backups off costs one config read.

    `due` and `create_backup` share one hold of the backup lock: apart, two
    processes ticking together would both see the same stale newest archive and
    both zip the library.
    """
    if not config.backup_enabled():
        return None
    at = _utc(now)
    with locks.backup_lock():
        if not due(at):
            return None
        made = create_backup(when=at)
    sweep()
    return made
