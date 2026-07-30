"""OS advisory file locks: the cross-process half of ``store/locks.py`` (#234).

``store/locks.py`` owns the *domain* (which campaign, which hierarchy); this
module owns the *mechanism* and knows nothing about campaigns.

**Where the lock files live.** Machine-local, per-user, outside the store, and
outside any directory subject to automatic cleaning. Not inside the store,
because the store may be a synced folder: a lock file there would be
replicated, would collect conflict copies, and on Windows with OneDrive
Files-On-Demand an open handle on it can stall. A file lock cannot cross
devices anyway, so putting it in the synced tree buys nothing.

The path is derived from the account database rather than the environment.
``$XDG_RUNTIME_DIR`` is set in a desktop session and unset under cron or
systemd, and ``$HOME`` can be rewritten; either would make two processes of the
*same user* pick *different* lock files and exclude nothing -- which is the
whole guarantee. ``pwd`` does not consult the environment. Windows has no
env-independent equivalent short of a ctypes ``SHGetKnownFolderPath``, which is
not worth a compiled-API dependency for a case that needs someone to
deliberately run two backends under differing ``USERPROFILE`` values.

Spec: docs/superpowers/specs/2026-07-28-cross-process-campaign-locks-design.md
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import sys
import time
from pathlib import Path

_WINDOWS = sys.platform == "win32"

if _WINDOWS:
    import msvcrt
else:
    import fcntl
    import pwd

# Sentinel deadline: make exactly one attempt and give up. Distinct from None,
# which means "no deadline" -- passing None for a non-blocking acquire would
# retry forever, the opposite of what the caller asked for.
NO_WAIT = object()

_RETRY_DELAY = 0.02
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_NAME_HINT = 40          # matches atomic.py's bound, well inside NAME_MAX


def _on_android() -> bool:
    """CPython only defines ``sys.getandroidapilevel`` on Android builds; the
    env vars are the belt-and-braces check for a Chaquopy runtime that does
    not."""
    return (hasattr(sys, "getandroidapilevel")
            or bool(os.environ.get("ANDROID_ROOT") and os.environ.get("ANDROID_DATA")))


def _user_home() -> Path:
    """The account's home directory, preferring the passwd database over the
    environment (see the module docstring) -- except on Android.

    **Android is the deliberate exception.** ``android_entry.start_server``
    points ``$HOME`` at the app's writable files directory precisely so the
    whole store resolves there, while Android's synthesized passwd record
    reports ``/`` as the home. Consulting passwd there would put the lock
    directory at ``/.local/state/grimoire/locks``, which is unwritable -- and
    since the first thing a lock does is ``mkdir``, startup would die with
    PermissionError instead of ever reaching a lock. The environment is the
    right source there, and there is no second process on the device for the
    determinism argument to protect.

    The same sanity check guards a broken passwd entry anywhere: a home that
    is not a real directory (or is the filesystem root) is not usable, and
    both processes of one user evaluate it identically, so falling back stays
    deterministic.
    """
    if not _WINDOWS and not _on_android():
        try:
            candidate = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except (KeyError, OSError):
            candidate = None
        if candidate is not None and candidate.parent != candidate and candidate.is_dir():
            return candidate
    return Path.home()  # paths-ok: machine-local lock state, deliberately outside the data root


def lock_dir() -> Path:
    base = _user_home()
    if _WINDOWS:
        return base / "AppData" / "Local" / "grimoire" / "locks"
    # ~/.local/state, not ~/.cache: the XDG location for state that persists
    # and is not swept. A cleaner unlinking a held lock file would split two
    # processes onto different inodes with both believing they hold the lock.
    return base / ".local" / "state" / "grimoire" / "locks"


def _store_key(root: Path) -> str:
    """Identify the store, preferring filesystem identity over its spelling.

    ``(st_dev, st_ino)`` is the only thing that answers "same directory?"
    correctly. A path string cannot: ``normcase`` folds case on Windows but is
    a no-op on POSIX, so on a case-INSENSITIVE POSIX volume (the macOS
    default) ``/Users/A/Store`` and ``/Users/a/store`` are one directory with
    two spellings, and hashing the spelling would put two processes on
    different lock files while both believed they held the campaign. Inode
    identity also absorbs the residual cases a path never could: bind mounts,
    a mapped drive versus its UNC form, and hard-linked roots.

    Falls back to the normalized path when the root does not exist yet, which
    is only reachable before the store is created -- at which point there is
    no data for the lock to protect, and the two keys converge as soon as it
    is. ``realpath`` still resolves symlinks, junctions, DOS 8.3 names and
    ``subst`` drives for that fallback.
    """
    try:
        st = os.stat(str(root))
        ident = f"{st.st_dev}\0{st.st_ino}"
    except OSError:
        ident = os.path.normcase(os.path.realpath(str(root)))
    return hashlib.sha256(ident.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def lock_path(root: Path, domain: str, name: str) -> Path:
    """Resolve (and create the directory for) one lock file.

    ``domain`` separates lock namespaces that must never collide. Without it a
    campaign whose id happens to be ``module-edit`` would hash to the same file
    as the global module-edit lock -- and since module publication takes the
    module-edit lock and *then* every campaign lock, that campaign would make
    publication block on itself until the timeout, and could make journal
    recovery skip forever. The domain is folded into the digest, not just the
    readable prefix, so it cannot be spoofed by a crafted name.

    ``name`` reaches us from a route parameter, so it is sanitized *and* hash
    suffixed: sanitizing alone would collide distinct ids, while the 64-bit
    suffix keeps them distinct and the readable prefix keeps the directory
    debuggable. A collision is not impossible, only negligible, and its
    consequence is bounded -- two names would share a lock file and serialize
    against each other, a spurious wait rather than a lost lock.
    """
    d = lock_dir() / _store_key(root)
    # mode= on creation closes the window where a freshly made directory is
    # briefly group/world-readable; the chmod then covers a directory that
    # already existed with wider permissions (mkdir's mode is also masked by
    # the umask, so it is not sufficient on its own).
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not _WINDOWS:
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass                      # best effort; an existing dir may not be ours
    slug = _UNSAFE.sub("-", name)[:_MAX_NAME_HINT]
    keyed = f"{domain}\0{name}".encode("utf-8", "surrogateescape")
    digest = hashlib.sha256(keyed).hexdigest()[:16]
    return d / f"{_UNSAFE.sub('-', domain)}-{slug}-{digest}.lock"


def _try_lock(fd: int) -> bool:
    """True if acquired, False if another process holds it.

    Only the errors that *mean* "held elsewhere" return False. ENOSYS, ENOTSUP,
    ENOLCK, EBADF and permission failures propagate: reporting a filesystem
    that cannot lock as "another process is editing this campaign" would send
    the user hunting a process that does not exist.
    """
    try:
        if _WINDOWS:
            # msvcrt.locking works at the CURRENT file position, unlike
            # whole-file flock, so the seek is mandatory in both directions.
            # Byte 0, length 1; locking past EOF is legal, so the file stays
            # zero-length.
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as e:
        if _WINDOWS and e.errno in (errno.EACCES, errno.EDEADLOCK):
            return False
        # EACCES on POSIX as well as EAGAIN/EWOULDBLOCK: several platforms
        # (and any libc emulating flock over fcntl) report an already-held
        # lock as EACCES, and treating that as permanent turns ordinary
        # contention into a 500 instead of a 409. A genuine permission problem
        # surfaces earlier, at the os.open above, so EACCES *from the lock
        # call* is contention in practice.
        if not _WINDOWS and e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
            return False
        raise


def _unlock(fd: int) -> None:
    if _WINDOWS:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _same_file(fd: int, path: Path) -> bool:
    """Is the fd we locked still the file at ``path``?

    Only ENOENT counts as a mismatch (the name is gone, so it certainly is not
    our inode). Every other stat failure propagates, per the same rule that
    keeps permanent errors out of the contention path.
    """
    try:
        named = os.stat(str(path))
    except FileNotFoundError:
        return False
    held = os.fstat(fd)
    return (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino)


def _expired(deadline) -> bool:
    if deadline is NO_WAIT:
        return True
    return deadline is not None and deadline - time.monotonic() <= 0


def acquire(path: Path, deadline) -> int | None:
    """Open and lock ``path``; return the fd, or None if it stayed held.

    ``deadline`` is None (wait indefinitely), ``NO_WAIT`` (one attempt), or a
    ``time.monotonic()`` value.
    """
    while True:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        got = False
        try:
            got = _try_lock(fd)
            if got:
                if _WINDOWS or _same_file(fd, path):
                    return fd
                # Someone replaced the file under us. Defence in depth only --
                # the chosen directory is not one anything cleans -- and not a
                # proof: an unlink between this check and the return is still
                # possible.
                release(fd)
                got = False
                # Retrying must still respect the caller's deadline: a file
                # being replaced repeatedly would otherwise make a NO_WAIT or
                # expired acquisition spin forever.
                if _expired(deadline):
                    return None
                continue
        except BaseException:
            # The whole post-open span is guarded, not just _try_lock: a raise
            # from _same_file (or a KeyboardInterrupt anywhere in here) would
            # otherwise leak the descriptor AND the kernel lock, blocking this
            # store's lock for the life of the process.
            if got:
                release(fd)           # unlocks and closes
            else:
                os.close(fd)
            raise
        os.close(fd)
        if _expired(deadline):
            return None
        if deadline is None:
            time.sleep(_RETRY_DELAY)
        else:
            # max(0.0, ...): the deadline can expire between _expired() above
            # and this subtraction, and time.sleep() raises on a negative.
            time.sleep(max(0.0, min(_RETRY_DELAY, deadline - time.monotonic())))


def release(fd: int) -> None:
    """Unlock and close. The fd is closed even if the unlock raises: leaking an
    open descriptor would keep the OS lock held while the caller believed it
    had released, which is the worst available outcome."""
    try:
        _unlock(fd)
    finally:
        os.close(fd)
