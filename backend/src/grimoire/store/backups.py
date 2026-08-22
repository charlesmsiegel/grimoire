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

A file that has *vanished* is the one exception, and it is not the same thing.
The store is live while this runs, and every atomic write in it creates a temp
beside its target and renames it away a moment later; a walk that listed one
of those and then found it gone has not lost any state, because the state it
named stopped existing before it could be copied. Failing there would mean a
backup could not be taken while anyone was playing, which is precisely when
one is worth having.
"""

from __future__ import annotations

import logging
import os
import re
import zipfile
from datetime import UTC, datetime, timedelta
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
#: Where a whole world is assembled before it is published (`worlds.staging`,
#: `module_edit.staging`). Disposable by construction and potentially the size
#: of the library itself: a backup taken while a gigabyte-scale fork or import
#: is copying would otherwise archive the half-made tree ALONGSIDE the world it
#: is a copy of, and restore partial data nothing will ever finish making
#: (Codex review). Named here rather than imported, for the reason `_DERIVED`
#: is: this module must not depend on every module that keeps scratch space.
_STAGING = (".world-staging", ".module-staging")

_log = logging.getLogger(__name__)


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


def _skips(root: Path, directory: Path) -> tuple[Path, ...]:
    """Directories the archive never descends into — see the module docstring.

    Derived data (`.cache`) and in-progress world/module copies (`_STAGING`)
    are excluded unconditionally: both sit inside the store by construction and
    neither is content a restore should bring back.

    The backup directory is excluded **only when it is inside the store**, and
    that condition is load-bearing rather than an optimization. Skipping it
    unconditionally would mean that pointing it at an *ancestor* of the store
    (`~`, the most natural choice for "somewhere else") matched every path in
    the walk and produced an empty archive that looked like a backup. Outside
    the store there is nothing to exclude, because nothing there is being
    archived in the first place.
    """
    nroot = _norm(root)
    skips = [_norm(root / _DERIVED), *(_norm(root / d) for d in _STAGING)]
    target = _norm(directory)
    if nroot in target.parents:
        skips.append(target)
    return tuple(skips)


def _is_skipped(path: Path, skip: tuple[Path, ...]) -> bool:
    candidate = _norm(path)
    return any(candidate == s or s in candidate.parents for s in skip)


def _is_backup_artifact(name: str) -> bool:
    """A file this module put in the backup directory: a finished archive, or
    the temp an in-flight one is being built into.

    Both have to be invisible to a walk that reaches the backup directory —
    which happens only when that directory IS the store root, since anywhere
    else inside the store is pruned outright. Excluding the directory itself is
    not available in that case (it would exclude the whole store), and
    excluding nothing archived each archive into the next *and* copied the
    half-written temp into itself. The cost is a user's own
    `grimoire-<stamp>.zip` parked at the store root, which nobody will notice.
    """
    return bool(_NAME_RE.match(name)) or (
        name.startswith(f".{_PREFIX}") and name.endswith(".tmp"))


def _store_file(z: zipfile.ZipFile, path: Path, arcname: str) -> None:
    """One member. Its own function so the failure policy above is testable:
    what happens when a file in the store cannot be read is a decision, and a
    decision needs a seam to exercise it."""
    z.write(path, arcname)


def _walk_error(exc: OSError) -> None:
    """`os.walk`'s error hook: re-raise, except for a directory that vanished.

    Load-bearing, and the default is the trap. `os.walk` swallows every error
    it hits listing a directory, so without this a directory the process cannot
    read — a permission bit, an I/O error, a sync client holding it on Windows
    — is dropped from the archive and the backup reports success. That is the
    exact failure the module docstring says cannot happen, and it was happening
    at the coarsest granularity there is: the omission is a whole subtree, not
    a file.

    A vanished directory is skipped for the same reason a vanished file is: a
    campaign deleted while the walk was running was not part of the state this
    archive is capturing.
    """
    if isinstance(exc, FileNotFoundError):
        return
    raise exc


def _archive_into(fh, root: Path, skip: tuple[Path, ...], directory: Path) -> None:
    """Write the store at `root` into the open binary file `fh`.

    ``os.walk(followlinks=False)``, not ``rglob``: a directory symlink pointing
    at an ancestor makes `rglob` walk forever, and a store is a user-owned
    directory in which such a link is possible. File symlinks *are* followed —
    the content the store reads through them is content a restore needs — but a
    symlinked directory is not descended into, so a link cannot make the
    archive unbounded. Anything that is not a regular file (a broken link, a
    socket, a fifo) is skipped rather than opened.

    A member that disappears between the listing and the copy is skipped, for
    the reason in the module docstring. Every other failure propagates —
    including one hit while *listing* a directory, which `os.walk` would
    otherwise swallow whole (see `_walk_error`).

    The skip check is per *directory*, not per file: `_norm` calls `getcwd`,
    and running it over every file in a library to re-answer a question the
    pruning above already settled is work for nothing.

    ``strict_timestamps=False`` because the zip format cannot represent a
    timestamp before 1980 or after 2107, and a member outside that range makes
    `write` raise -- one file restored out of an old tarball with an epoch-0
    mtime took the entire backup down with it. The flag clamps the stored stamp
    at both ends instead. A member's mtime is metadata about the file; losing a
    1970 on one of them is not a reason to have no backup at all.

    Entries are sorted so two archives of an unchanged store list their members
    in the same order.
    """
    backups_here = _norm(directory)
    with zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as z:
        for dirpath, dirnames, filenames in os.walk(root, onerror=_walk_error,
                                                    followlinks=False):
            here = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames if not _is_skipped(here / d, skip))
            in_backup_dir = _norm(here) == backups_here
            for name in sorted(filenames):
                path = here / name
                if in_backup_dir and _is_backup_artifact(name):
                    continue
                try:
                    if not path.is_file():
                        continue
                    _store_file(z, path, path.relative_to(root).as_posix())
                except FileNotFoundError:
                    continue


def _utc(when: datetime | None) -> datetime:
    """`when` in UTC. A naive datetime is read as UTC rather than local time:
    every timestamp this module writes or parses is UTC, so interpreting one
    input as local would make an archive's name disagree with its own series."""
    if when is None:
        return datetime.now(UTC)
    if when.tzinfo is None:
        return when.replace(tzinfo=UTC)
    return when.astimezone(UTC)


def _allocate(directory: Path, when: datetime) -> Path:
    """A free archive path for `when`.

    Two backups in the same second are reachable — a scheduled one and the
    Configuration page's *Back up now* — and the second must not silently
    replace the first, so the name gains a `-2`, `-3`, … Called under the
    backup lock, which is what makes the check-then-create meaningful.

    The loop terminates because every iteration names a distinct file that has
    to already exist for it to continue, so it is bounded by the directory's
    own contents rather than by a counter.
    """
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
        # Resolved ONCE and threaded through. It used to be re-read at four
        # points inside this one call -- the allocation, the exclusion set, the
        # walk's own check, and the sweep that follows -- each a fresh config
        # read silently assumed to agree with the others.
        directory = backup_dir()
        target = _allocate(directory, _utc(when))
        target.parent.mkdir(parents=True, exist_ok=True)
        with atomic.streaming_write(target) as fh:
            _archive_into(fh, root, _skips(root, directory), directory)
        return target


def _parsed(name: str) -> tuple[datetime, int] | None:
    """`(taken_at, same-second ordinal)` if `name` is one of our archives.

    Parsing IS the recognition, deliberately. The pattern alone accepts digit
    runs that are not dates — `grimoire-20269999T999999Z.zip`, or a
    leap-second `...235960Z` — and with the match and the parse as two separate
    steps such a file made the listing raise `ValueError`: an uncaught 500 for
    the route, and, because `due` reads that same listing, automatic backups
    that stopped and stayed stopped for as long as the file sat in the folder.
    One notion of "ours", and a name that cannot be a time is simply not one.

    The ordinal is parsed rather than left to string order — `-2` sorts
    *before* `.zip` bytewise, so plain name sorting inverts every pair of
    archives taken in the same second.
    """
    m = _NAME_RE.match(name)
    if not m:
        return None
    try:
        taken = datetime.strptime(m.group(1), _STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None
    return taken, int(m.group(2) or 1)


def list_backups() -> list[dict]:
    """Every archive this module wrote, newest first.

    A missing backup directory is an empty list, not an error: a store that has
    never been backed up has nothing to report, and asking must not create the
    directory. Anything that is not one of our archives is ignored — see
    `_parsed`, which is what "one of ours" means.
    """
    return _list_in(backup_dir())


def _list_in(directory: Path) -> list[dict]:
    """`list_backups` against an already-resolved directory, so a caller that
    is going to *act* on the result reads and acts on the same one."""
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return []
    # Sorted on the parse rather than by re-parsing each name a second time
    # inside the key function -- and it keeps the sort off an Optional, which
    # is only safe here because of the filter three lines above it.
    found = []
    for path in entries:
        parsed = _parsed(path.name)
        if parsed is None:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue                          # vanished, or unreadable: not a restore point
        found.append((parsed, {"name": path.name, "size": size,
                               "created": parsed[0].strftime("%Y-%m-%dT%H:%M:%SZ")}))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _parsed_key, row in found]


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
        # Listed from the SAME resolution it deletes from: two reads of a
        # setting that can change between them is how a sweep ends up unlinking
        # a same-named file out of a directory it never looked at.
        doomed = list(reversed(_list_in(directory)[keep:]))
        for row in doomed:
            try:
                (directory / row["name"]).unlink()
            except FileNotFoundError:
                pass
        return [row["name"] for row in doomed]


def _taken_at(name: str) -> datetime:
    """When the archive `name` was taken. Only ever called on names a listing
    returned, which `_parsed` has already vouched for."""
    parsed = _parsed(name)
    assert parsed is not None
    return parsed[0]


def due(now: datetime | None = None) -> bool:
    """Whether the interval has elapsed since the newest archive.

    Derived from the archives themselves rather than a recorded "last run", so
    it survives a restart, a moved store, and two machines sharing one library
    through a synced folder. A store with no archive is always due.

    Archives stamped in the *future* are ignored when answering, which is not
    fussiness: a store synced from a machine whose clock is ahead — or one
    restored with a nonsense stamp — otherwise makes this return False for as
    long as that stamp is in the future, and automatic backups stop dead with
    nothing anywhere saying so. Skipping them means one backup is taken now and
    the schedule then runs off *its* stamp, so the series converges instead of
    either stalling forever or re-running every tick.
    """
    rows = list_backups()
    at = _utc(now)
    past = [r for r in rows if _taken_at(r["name"]) <= at]
    if not past:
        return True
    return at - _taken_at(past[0]["name"]) >= timedelta(
        hours=config.backup_interval_hours())


def run_scheduled(now: datetime | None = None) -> Path | None:
    """One tick of the schedule: back up if enabled and due, then sweep.

    Returns the archive written, or None when there was nothing to do. The
    enabled check and the due check are cheap and happen first, so a tick on a
    store with backups off costs one config read.

    `due` and `create_backup` share one hold of the backup lock: apart, two
    processes ticking together would both see the same stale newest archive and
    both zip the library.

    The sweep runs **after** the archive, not before it, and on a nearly-full
    disk that is the difference between a failed backup and a lost one.
    Sweeping first would free the space the new archive needs — and would do it
    by deleting a restore point that exists, to make room for one that may
    still fail, leaving fewer archives than the retention setting promised.
    Failing to add a backup is recoverable; deleting a good one to attempt it
    is not.

    A sweep that fails is logged and does not take the archive down with it.
    The archive is what this call is for and it has already landed; raising
    here would have the ticker report a written backup as a skipped one, and
    "your backup failed" is the opposite of what happened. The unpruned
    directory is not hidden — the next manual run reports the same failure to
    the caller, who can act on it.
    """
    if not config.backup_enabled():
        return None
    at = _utc(now)
    with locks.backup_lock():
        if not due(at):
            return None
        made = create_backup(when=at)
    try:
        sweep()
    except OSError as exc:
        _log.warning("backup %s written, but retention could not run -- %s",
                     made.name, exc)
    return made
