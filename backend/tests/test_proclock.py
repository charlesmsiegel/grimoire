"""store/proclock.py -- the OS advisory file lock behind store/locks.py (#234).

Cross-process behaviour needs real processes: an in-process test cannot
demonstrate that a second *process* is excluded. The child prints HELD only
after it owns the lock, so the parent never races the handshake.
"""

import os
import subprocess
import sys
import time

import pytest

from grimoire.store import proclock

_CHILD = """
import sys, time
sys.path[:0] = {path!r}
from grimoire.store import proclock
fd = proclock.acquire({lock!r}, None)
print("HELD", flush=True)
time.sleep({hold})
proclock.release(fd)
"""


def _holder(lock_file, hold=10.0):
    """Spawn a process holding `lock_file`; return once it actually holds it."""
    src = _CHILD.format(path=sys.path, lock=str(lock_file), hold=hold)
    p = subprocess.Popen([sys.executable, "-c", src],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "HELD", "child never acquired"
    return p


def _open_fds() -> int:
    """Live handle/descriptor count for this process.

    Must be a value that actually MOVES when a descriptor leaks -- a constant
    such as msvcrt's _getmaxstdio() would make the leak tests below pass
    unconditionally.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        # Explicit signatures: GetCurrentProcess returns a pseudo-handle
        # (-1), which ctypes truncates without a declared HANDLE restype.
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE,
                                              ctypes.POINTER(wintypes.DWORD)]
        k32.GetProcessHandleCount.restype = wintypes.BOOL
        count = wintypes.DWORD()
        ok = k32.GetProcessHandleCount(k32.GetCurrentProcess(), ctypes.byref(count))
        assert ok, f"GetProcessHandleCount failed: {ctypes.get_last_error()}"
        return count.value
    # /dev/fd works on macOS and the BSDs as well as Linux; /proc/self/fd is
    # Linux-only.
    return len(os.listdir("/dev/fd"))


# ---- paths ----


def test_lock_dir_is_outside_the_store_and_per_user(tmp_path):
    d = proclock.lock_dir()
    assert "grimoire" in d.parts and d.name == "locks"
    assert tmp_path not in d.parents and d != tmp_path


def test_locking_writes_nothing_into_the_store(tmp_path):
    """The guard that lock files stay out of the user's (possibly synced)
    library -- the whole reason the lock dir is machine-local."""
    store = tmp_path / "store"
    store.mkdir()
    before = set(store.rglob("*"))
    fd = proclock.acquire(proclock.lock_path(store, "t", "campaign-a"), None)
    try:
        assert set(store.rglob("*")) == before
    finally:
        proclock.release(fd)


def test_lock_dir_ignores_environment_overrides(monkeypatch):
    """The round-2 finding: an environment-conditional path means two
    processes of one user pick different lock files and exclude nothing."""
    before = proclock.lock_dir()
    for var in ("XDG_RUNTIME_DIR", "XDG_STATE_HOME", "LOCALAPPDATA", "TMPDIR"):
        monkeypatch.delenv(var, raising=False)
    if sys.platform != "win32":
        monkeypatch.setenv("HOME", "/nonexistent-home")
    assert proclock.lock_dir() == before


def test_lock_path_separates_stores_and_names(tmp_path):
    a, b = tmp_path / "store-a", tmp_path / "store-b"
    a.mkdir()
    b.mkdir()
    assert proclock.lock_path(a, "t", "run") != proclock.lock_path(b, "t", "run")
    assert proclock.lock_path(a, "t", "run") != proclock.lock_path(a, "t", "other")
    assert proclock.lock_path(a, "t", "run") == proclock.lock_path(a, "t", "run")


@pytest.mark.parametrize("hostile", [
    "../../escape/../../etc/passwd",
    "..",
    "/absolute/path",
    "C:\\Windows\\System32",
    "with spaces and\ttabs",
    "\u0000truncation",
])
def test_lock_path_cannot_escape_the_lock_directory(tmp_path, hostile):
    """The cid reaches us from a route parameter. What matters is not that
    the name looks tidy but that the resolved path stays inside the lock
    directory -- a literal '..' *within* a filename traverses nothing."""
    d = proclock.lock_path(tmp_path, "t", "x").parent
    p = proclock.lock_path(tmp_path, "t", hostile)
    assert p.parent == d
    assert d.resolve() in p.resolve().parents
    assert p.name.endswith(".lock")


def test_lock_path_bounds_the_component_length(tmp_path):
    p = proclock.lock_path(tmp_path, "t", "x" * 500)
    assert len(p.name) < 100


# ---- locking ----


def test_acquire_and_release_roundtrip(tmp_path):
    lock = proclock.lock_path(tmp_path, "t", "roundtrip")
    fd = proclock.acquire(lock, None)
    assert isinstance(fd, int)
    proclock.release(fd)
    fd2 = proclock.acquire(lock, None)          # re-acquirable after release
    proclock.release(fd2)


def test_a_second_process_is_excluded(tmp_path):
    lock = proclock.lock_path(tmp_path, "t", "excluded")
    p = _holder(lock)
    try:
        assert proclock.acquire(lock, time.monotonic() + 0.5) is None
    finally:
        p.kill()
        p.wait(timeout=10)


def test_the_lock_is_released_when_the_holder_dies(tmp_path):
    """No stale-lock reaping: the kernel releases on process death."""
    lock = proclock.lock_path(tmp_path, "t", "died")
    p = _holder(lock)
    p.kill()
    p.wait(timeout=10)
    deadline = time.monotonic() + 10
    while (fd := proclock.acquire(lock, time.monotonic() + 0.2)) is None:
        assert time.monotonic() < deadline, "lock never released"
    proclock.release(fd)


def test_no_wait_returns_immediately(tmp_path):
    """NO_WAIT must make ONE attempt. Passing None here would retry forever."""
    lock = proclock.lock_path(tmp_path, "t", "nowait")
    p = _holder(lock)
    try:
        started = time.monotonic()
        assert proclock.acquire(lock, proclock.NO_WAIT) is None
        assert time.monotonic() - started < 1.0
    finally:
        p.kill()
        p.wait(timeout=10)


@pytest.mark.parametrize("errcode", [38, 37])   # ENOSYS, ENOLCK
def test_a_permanent_error_propagates_rather_than_timing_out(tmp_path, monkeypatch, errcode):
    """A filesystem that cannot lock, or a directory we may not write, must not
    be reported as contention."""
    lock = proclock.lock_path(tmp_path, "t", "permanent")

    def boom(fd):
        raise OSError(errcode, "permanent lock failure")

    monkeypatch.setattr(proclock, "_try_lock", boom)
    with pytest.raises(OSError):
        proclock.acquire(lock, time.monotonic() + 0.2)


def test_repeated_timeouts_leak_no_descriptors(tmp_path):
    lock = proclock.lock_path(tmp_path, "t", "fdleak")
    p = _holder(lock)
    try:
        proclock.acquire(lock, proclock.NO_WAIT)          # warm any lazy state
        before = _open_fds()
        for _ in range(25):
            assert proclock.acquire(lock, proclock.NO_WAIT) is None
        assert _open_fds() - before < 5
    finally:
        p.kill()
        p.wait(timeout=10)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the inode-mismatch branch is POSIX-only: a locked+open file cannot "
           "be unlinked on Windows, so acquire() short-circuits past _same_file")
def test_inode_mismatch_retries_respect_the_deadline_and_leak_nothing(
        tmp_path, monkeypatch):
    """A file replaced under us must not make a NO_WAIT acquire spin forever,
    and each discarded attempt must close its descriptor."""
    lock = proclock.lock_path(tmp_path, "t", "mismatch")
    monkeypatch.setattr(proclock, "_same_file", lambda fd, path: False)
    before = _open_fds()
    started = time.monotonic()
    assert proclock.acquire(lock, proclock.NO_WAIT) is None
    assert time.monotonic() - started < 1.0, "NO_WAIT spun on the mismatch path"
    assert proclock.acquire(lock, time.monotonic() + 0.3) is None
    assert _open_fds() - before < 5, "mismatch retries leaked descriptors"


def test_android_uses_the_configured_home_not_the_passwd_entry(monkeypatch, tmp_path):
    """android_entry.start_server points $HOME at the app's writable files dir,
    while Android's synthesized passwd record reports `/`. Consulting passwd
    there would put the lock dir at /.local/state/grimoire/locks -- unwritable,
    and since a lock's first act is mkdir, startup would die with
    PermissionError before ever reaching a lock."""
    app_home = tmp_path / "app-files"
    app_home.mkdir()
    monkeypatch.setattr(proclock, "_on_android", lambda: True)
    monkeypatch.setenv("HOME", str(app_home))
    monkeypatch.setenv("USERPROFILE", str(app_home))
    assert app_home in proclock.lock_dir().parents


def test_an_unusable_passwd_home_falls_back_to_the_environment(monkeypatch, tmp_path):
    """The same guard, without needing the platform probe: a passwd entry
    pointing at `/` (or anywhere that is not a real directory) is not usable."""
    if sys.platform == "win32":
        pytest.skip("passwd is POSIX-only")
    fake_home = tmp_path / "env-home"
    fake_home.mkdir()
    monkeypatch.setattr(proclock.pwd, "getpwuid",
                        lambda uid: type("E", (), {"pw_dir": "/"})())
    monkeypatch.setenv("HOME", str(fake_home))
    assert fake_home in proclock.lock_dir().parents


def test_the_store_key_follows_filesystem_identity_not_spelling(tmp_path):
    """Two spellings of one directory must key to one lock.

    A path string cannot decide this: normcase folds case on Windows but is a
    no-op on POSIX, so on a case-INSENSITIVE POSIX volume (the macOS default)
    `/Users/A/Store` and `/Users/a/store` are one directory with two spellings.
    Hashing the spelling would put two processes on different lock files while
    both believed they held the campaign. (st_dev, st_ino) answers correctly on
    every platform, and this symlink stands in for the same question.
    """
    real = tmp_path / "real-store"
    real.mkdir()
    link = tmp_path / "aliased-store"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    assert proclock.lock_path(real, "campaign", "c") == \
        proclock.lock_path(link, "campaign", "c")


def test_distinct_stores_still_key_apart(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert proclock.lock_path(a, "campaign", "c") != proclock.lock_path(b, "campaign", "c")


def test_a_missing_store_root_still_resolves_a_lock_path(tmp_path):
    """The stat-based key falls back to the path when the root does not exist
    yet -- reachable only before the store is created, when there is no data
    for the lock to protect."""
    missing = tmp_path / "not-created-yet"
    p = proclock.lock_path(missing, "campaign", "c")
    assert p.name.endswith(".lock")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX errno path")
def test_posix_eacces_from_the_lock_call_counts_as_contention(tmp_path, monkeypatch):
    """Some platforms -- and any libc emulating flock over fcntl -- report an
    already-held lock as EACCES. Treating it as permanent would turn ordinary
    contention into a 500 instead of a 409."""
    import errno as _errno

    lock = proclock.lock_path(tmp_path, "t", "eacces")
    monkeypatch.setattr(proclock, "fcntl", type("F", (), {
        "LOCK_EX": 2, "LOCK_NB": 4, "LOCK_UN": 8,
        "flock": staticmethod(
            lambda fd, op: (_ for _ in ()).throw(OSError(_errno.EACCES, "denied"))),
    }))
    assert proclock.acquire(lock, proclock.NO_WAIT) is None   # contention, not a raise
