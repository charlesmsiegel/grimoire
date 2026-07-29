# Cross-process campaign locks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `locks.campaign_lock(cid)` and `module_edit._M` exclude concurrent *processes*, not just threads, so two grimoire backends on one machine cannot silently overwrite each other's edits.

**Architecture:** A new `store/proclock.py` owns the OS-level advisory file lock (platform branching, lock-file path resolution). `store/locks.py` keeps the domain — the per-campaign registry — but hands back a `_ProcessScopedLock` that composes the existing `threading.RLock` with a `proclock` file lock taken once at the outermost acquisition. Contention raises `StoreBusy`, which one FastAPI handler turns into HTTP 409.

**Tech Stack:** Python 3.11+, stdlib only (`fcntl`/`pwd` on POSIX, `msvcrt` on Windows), FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-07-28-cross-process-campaign-locks-design.md`

## Global Constraints

- **Stdlib only.** No new entry in `backend/pyproject.toml`. Base deps must stay Android-installable.
- **Android-safe.** No repo-checkout assumptions; filesystem access for the *store* goes through `store.paths`. The lock directory is deliberately outside the store and resolved by `proclock` alone.
- **pydantic v1/v2-agnostic.** Plain `BaseModel` fields, dump via `routes._dump`. (Only Task 5 touches route-adjacent code; no new models are needed.)
- **`LOCK_TIMEOUT = 30.0`** seconds, defined once in `store/locks.py`.
- **Lock hierarchy, never inverted:** module-edit lock → campaign locks in **sorted cid order** → audit baseline lock / rolls lock.
- **The public surface of `campaign_lock(cid)` does not change:** context-manager protocol, `acquire(blocking=True, timeout=-1) -> bool`, `release()`, `_is_owned()`, and identity stability (`campaign_lock(c) is campaign_lock(c)`).
- **Every existing test must keep passing unchanged.** That is the check that the wrapper is a drop-in. Run: `PYTHONPATH=<worktree>/backend/src backend/.venv/Scripts/python.exe -m pytest backend -q`
- **Test command** (from the repo root, note PYTHONPATH must shadow the editable install):
  `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`

## File Structure

| File | Responsibility |
|---|---|
| `backend/src/grimoire/store/proclock.py` | **new** — OS advisory file lock: lock-dir resolution, store key, lock filename, platform lock/unlock/retry. Knows nothing about campaigns. |
| `backend/src/grimoire/store/locks.py` | **modify** — the lock *domain*: registry, `_ProcessScopedLock`, `StoreBusy`/`CampaignBusy`/`ModuleEditBusy`, `LOCK_TIMEOUT`, `hold_all()`. |
| `backend/src/grimoire/store/module_edit.py` | **modify** — `_M` becomes the shared cross-process module-edit lock; `_campaign_locks()` delegates to `hold_all()`. |
| `backend/src/grimoire/store/scenes.py` | **modify** — `create_scene` takes the campaign lock across its whole body. |
| `backend/src/grimoire/store/audit.py` | **modify** — `capture_baseline` re-raises `StoreBusy` ahead of its broad catch. |
| `backend/src/grimoire/main.py` | **modify** — `StoreBusy` → 409 handler; `_lifespan` tolerates a busy `recover()`. |
| `backend/src/grimoire/routes.py` | **modify** — rebind route uses `hold_all()`; adjudication route maps contention to 409; `_fence_stream` wraps `finalize`. |
| `backend/tests/test_proclock.py` | **new** — the primitive: paths, platform semantics, cross-process exclusion. |
| `backend/tests/test_locks_store.py` | **modify** — the domain: hybrid wrapper contract, `hold_all`, cross-process campaign + module-edit exclusion. |
| `backend/tests/test_locks_http.py` | **new** — 409 mapping, startup tolerance, `create_scene` boundary, SSE busy frame. |

---

### Task 1: `store/proclock.py` — the OS advisory file lock

**Files:**
- Create: `backend/src/grimoire/store/proclock.py`
- Test: `backend/tests/test_proclock.py`

**Interfaces:**
- Consumes: nothing (leaf module; imports only stdlib).
- Produces:
  - `NO_WAIT` — sentinel for "one attempt, no retry"
  - `lock_dir() -> Path`
  - `lock_path(root: Path, name: str) -> Path` (creates the directory)
  - `acquire(path: Path, deadline) -> int | None` — fd, or `None` on timeout. `deadline` is `None` (wait forever), `NO_WAIT`, or a `time.monotonic()` float.
  - `release(fd: int) -> None`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_proclock.py`:

```python
"""store/proclock.py -- the OS advisory file lock behind store/locks.py (#234).

Cross-process behaviour needs real processes: an in-process test cannot
demonstrate that a second *process* is excluded. The child prints HELD only
after it owns the lock, so the parent never races the handshake.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

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
    before = {p for p in store.rglob("*")}
    fd = proclock.acquire(proclock.lock_path(store, "campaign-a"), None)
    try:
        assert {p for p in store.rglob("*")} == before
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
    assert proclock.lock_path(a, "run") != proclock.lock_path(b, "run")
    assert proclock.lock_path(a, "run") != proclock.lock_path(a, "other")
    assert proclock.lock_path(a, "run") == proclock.lock_path(a, "run")


def test_lock_path_sanitizes_a_hostile_name(tmp_path):
    p = proclock.lock_path(tmp_path, "../../escape/../../etc/passwd")
    assert p.parent == proclock.lock_path(tmp_path, "x").parent
    assert ".." not in p.name and "/" not in p.name and "\\" not in p.name


def test_lock_path_bounds_the_component_length(tmp_path):
    p = proclock.lock_path(tmp_path, "x" * 500)
    assert len(p.name) < 100


# ---- locking ----


def test_acquire_and_release_roundtrip(tmp_path):
    lock = proclock.lock_path(tmp_path, "roundtrip")
    fd = proclock.acquire(lock, None)
    assert isinstance(fd, int)
    proclock.release(fd)
    fd2 = proclock.acquire(lock, None)          # re-acquirable after release
    proclock.release(fd2)


def test_a_second_process_is_excluded(tmp_path):
    lock = proclock.lock_path(tmp_path, "excluded")
    p = _holder(lock)
    try:
        assert proclock.acquire(lock, time.monotonic() + 0.5) is None
    finally:
        p.kill()
        p.wait(timeout=10)


def test_the_lock_is_released_when_the_holder_dies(tmp_path):
    """No stale-lock reaping: the kernel releases on process death."""
    lock = proclock.lock_path(tmp_path, "died")
    p = _holder(lock)
    p.kill()
    p.wait(timeout=10)
    deadline = time.monotonic() + 10
    while (fd := proclock.acquire(lock, time.monotonic() + 0.2)) is None:
        assert time.monotonic() < deadline, "lock never released"
    proclock.release(fd)


def test_no_wait_returns_immediately(tmp_path):
    """NO_WAIT must make ONE attempt. Passing None here would retry forever."""
    lock = proclock.lock_path(tmp_path, "nowait")
    p = _holder(lock)
    try:
        started = time.monotonic()
        assert proclock.acquire(lock, proclock.NO_WAIT) is None
        assert time.monotonic() - started < 1.0
    finally:
        p.kill()
        p.wait(timeout=10)


def test_a_permanent_error_propagates_rather_than_timing_out(tmp_path, monkeypatch):
    """A filesystem that cannot lock, or a directory we may not write, must not
    be reported as contention."""
    lock = proclock.lock_path(tmp_path, "permanent")

    def boom(fd):
        raise OSError(38, "Function not implemented")  # ENOSYS

    monkeypatch.setattr(proclock, "_try_lock", boom)
    with pytest.raises(OSError):
        proclock.acquire(lock, time.monotonic() + 0.2)


def test_repeated_timeouts_leak_no_descriptors(tmp_path):
    lock = proclock.lock_path(tmp_path, "fdleak")
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


def _open_fds() -> int:
    if sys.platform == "win32":
        import ctypes
        return ctypes.cdll.msvcrt._getmaxstdio()  # stable; the real check is below
    return len(os.listdir("/proc/self/fd"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend/tests/test_proclock.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.proclock'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/grimoire/store/proclock.py`:

```python
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


def _user_home() -> Path:
    """The account's home directory, preferring the passwd database over the
    environment (see the module docstring)."""
    if not _WINDOWS:
        try:
            return Path(pwd.getpwuid(os.getuid()).pw_dir)
        except (KeyError, OSError):
            pass                      # fall through to Path.home()
    return Path.home()


def lock_dir() -> Path:
    base = _user_home()
    if _WINDOWS:
        return base / "AppData" / "Local" / "grimoire" / "locks"
    # ~/.local/state, not ~/.cache: the XDG location for state that persists
    # and is not swept. A cleaner unlinking a held lock file would split two
    # processes onto different inodes with both believing they hold the lock.
    return base / ".local" / "state" / "grimoire" / "locks"


def _store_key(root: Path) -> str:
    """Identify the store by its resolved path. ``realpath`` resolves symlinks,
    junctions, DOS 8.3 names and ``subst`` drives; ``normcase`` normalizes case
    and separators on Windows and is a no-op on POSIX, so genuinely distinct
    case-sensitive paths stay distinct."""
    norm = os.path.normcase(os.path.realpath(str(root)))
    return hashlib.sha256(norm.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def lock_path(root: Path, name: str) -> Path:
    """Resolve (and create the directory for) one lock file.

    ``name`` reaches us from a route parameter, so it is sanitized *and* hash
    suffixed: sanitizing alone would collide distinct ids, while the 64-bit
    suffix keeps them distinct and the readable prefix keeps the directory
    debuggable. A collision is not impossible, only negligible, and its
    consequence is bounded -- two names would share a lock file and serialize
    against each other, a spurious wait rather than a lost lock.
    """
    d = lock_dir() / _store_key(root)
    d.mkdir(parents=True, exist_ok=True)
    if not _WINDOWS:
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass                      # best effort; an existing dir may be ours already
    slug = _UNSAFE.sub("-", name)[:_MAX_NAME_HINT]
    digest = hashlib.sha256(name.encode("utf-8", "surrogateescape")).hexdigest()[:16]
    return d / f"{slug}-{digest}.lock"


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
        if not _WINDOWS and e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            return False
        raise


def _unlock(fd: int) -> None:
    if _WINDOWS:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _same_file(fd: int, path: Path) -> bool:
    try:
        held, named = os.fstat(fd), os.stat(str(path))
    except OSError:
        return False
    return (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino)


def acquire(path: Path, deadline) -> int | None:
    """Open and lock ``path``; return the fd, or None if it stayed held.

    ``deadline`` is None (wait indefinitely), ``NO_WAIT`` (one attempt), or a
    ``time.monotonic()`` value.
    """
    while True:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            got = _try_lock(fd)
        except BaseException:
            os.close(fd)
            raise
        if got:
            if _WINDOWS or _same_file(fd, path):
                return fd
            # Someone replaced the file under us. Defence in depth only -- the
            # chosen directory is not one anything cleans -- and not a proof:
            # an unlink between this check and the return is still possible.
            release(fd)
            continue
        os.close(fd)
        if deadline is NO_WAIT:
            return None
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(_RETRY_DELAY, remaining))
        else:
            time.sleep(_RETRY_DELAY)


def release(fd: int) -> None:
    """Unlock and close. The fd is closed even if the unlock raises: leaking an
    open descriptor would keep the OS lock held while the caller believed it
    had released, which is the worst available outcome."""
    try:
        _unlock(fd)
    finally:
        os.close(fd)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend/tests/test_proclock.py -q`
Expected: PASS

If `test_repeated_timeouts_leak_no_descriptors` cannot count fds on Windows, replace `_open_fds` with a `psutil`-free approximation: open 200 sentinel files after the loop and assert they all succeed. Do **not** delete the test.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/proclock.py backend/tests/test_proclock.py
git commit -m "Add proclock: the OS advisory file lock primitive (#234)"
```

---

### Task 2: `locks.py` — the hybrid `_ProcessScopedLock`

**Files:**
- Modify: `backend/src/grimoire/store/locks.py` (whole file)
- Test: `backend/tests/test_locks_store.py` (append)

**Interfaces:**
- Consumes: `proclock.acquire/release/lock_path/NO_WAIT` (Task 1); `paths.home()`.
- Produces:
  - `LOCK_TIMEOUT: float = 30.0`
  - `StoreBusy(Exception)` with `.name`; subclasses `CampaignBusy`, `ModuleEditBusy`
  - `campaign_lock(cid) -> _ProcessScopedLock` (unchanged signature)
  - `module_edit_lock() -> _ProcessScopedLock`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_locks_store.py`:

```python
import os
import subprocess
import sys
import time

_HOLDER = """
import sys, time
sys.path[:0] = {path!r}
from grimoire.store import locks
lock = locks.{factory}
with lock:
    print("HELD", flush=True)
    time.sleep({hold})
"""


def _hold_in_child(tmp_path, factory, hold=10.0):
    """Spawn a process holding a lock; return once it actually holds it."""
    src = _HOLDER.format(path=sys.path, factory=factory, hold=hold)
    p = subprocess.Popen([sys.executable, "-c", src], text=True,
                         stdout=subprocess.PIPE,
                         env={**os.environ, "GRIMOIRE_HOME": str(tmp_path)})
    assert p.stdout.readline().strip() == "HELD", "child never acquired"
    return p


# ---- cross-process exclusion (#234) ----


def test_a_second_process_cannot_hold_the_same_campaign(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    p = _hold_in_child(tmp_path, f"campaign_lock({cid!r})")
    try:
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.5)
        with pytest.raises(locks.CampaignBusy):
            with locks.campaign_lock(cid):
                pass
    finally:
        p.kill()
        p.wait(timeout=10)


def test_the_campaign_is_acquirable_once_the_holder_exits(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    p = _hold_in_child(tmp_path, f"campaign_lock({cid!r})")
    p.kill()
    p.wait(timeout=10)
    deadline = time.monotonic() + 15
    while not locks.campaign_lock(cid).acquire(timeout=0.2):
        assert time.monotonic() < deadline, "never released"
    locks.campaign_lock(cid).release()


def test_different_campaigns_do_not_block_across_processes(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    a = campaigns.create_campaign("One", wid, module="pool-basic")
    b = campaigns.create_campaign("Two", wid, module="pool-basic")
    p = _hold_in_child(tmp_path, f"campaign_lock({a!r})")
    try:
        with locks.campaign_lock(b):
            pass
    finally:
        p.kill()
        p.wait(timeout=10)


def test_reentrancy_takes_the_file_lock_exactly_once(monkeypatch, tmp_path):
    """A child must stay blocked at depth 2 and be freed only by the OUTERMOST
    release -- otherwise the inner exit drops cross-process exclusion."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    lock = locks.campaign_lock(cid)
    with lock:
        with lock:
            pass                                   # inner exit must NOT unlock
        probe = subprocess.run(
            [sys.executable, "-c", _HOLDER.format(
                path=sys.path, factory=f"campaign_lock({cid!r})", hold=0)],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "GRIMOIRE_HOME": str(tmp_path),
                 "GRIMOIRE_LOCK_TIMEOUT": "0.5"})
        assert "HELD" not in probe.stdout, "child acquired while we held it"


def test_a_second_process_cannot_hold_the_module_edit_lock(monkeypatch, tmp_path):
    """The second stated goal of #234. Round-1 review: without this test the
    module-edit lock could be entirely process-local and every other test
    would still pass."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    p = _hold_in_child(tmp_path, "module_edit_lock()")
    try:
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.5)
        with pytest.raises(locks.ModuleEditBusy):
            with locks.module_edit_lock():
                pass
    finally:
        p.kill()
        p.wait(timeout=10)


# ---- the RLock contract the wrapper must preserve ----


def test_acquire_returns_false_rather_than_raising(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    p = _hold_in_child(tmp_path, f"campaign_lock({cid!r})")
    try:
        assert locks.campaign_lock(cid).acquire(timeout=0.3) is False
    finally:
        p.kill()
        p.wait(timeout=10)


def test_non_blocking_acquire_does_not_retry(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    p = _hold_in_child(tmp_path, f"campaign_lock({cid!r})")
    try:
        started = time.monotonic()
        assert locks.campaign_lock(cid).acquire(blocking=False) is False
        assert time.monotonic() - started < 2.0
    finally:
        p.kill()
        p.wait(timeout=10)


def test_non_blocking_acquire_rejects_a_timeout(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        locks.campaign_lock(cid).acquire(blocking=False, timeout=5)


def test_release_by_a_non_owner_raises_and_changes_nothing(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    lock = locks.campaign_lock(cid)
    with lock:
        err = []

        def stranger():
            try:
                lock.release()
            except RuntimeError as e:
                err.append(e)

        t = threading.Thread(target=stranger)
        t.start()
        t.join(timeout=5)
        assert err, "a non-owner release must raise"
        assert lock._is_owned(), "our hold survived the intruder"
    assert lock.acquire(timeout=5), "still usable afterwards"
    lock.release()


def test_the_thread_lock_is_released_after_a_timeout(monkeypatch, tmp_path):
    """A timed-out acquire must not strand the in-process RLock."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    p = _hold_in_child(tmp_path, f"campaign_lock({cid!r})")
    try:
        assert locks.campaign_lock(cid).acquire(timeout=0.3) is False
        assert not locks.campaign_lock(cid)._is_owned()
    finally:
        p.kill()
        p.wait(timeout=10)
    deadline = time.monotonic() + 15
    while not locks.campaign_lock(cid).acquire(timeout=0.2):
        assert time.monotonic() < deadline
    locks.campaign_lock(cid).release()


def test_an_exception_inside_the_block_releases_both_layers(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    lock = locks.campaign_lock(cid)
    with pytest.raises(ValueError):
        with lock:
            raise ValueError("boom")
    assert not lock._is_owned()
    assert lock.acquire(timeout=5)
    lock.release()


def test_a_failing_unlock_still_closes_and_releases(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    lock = locks.campaign_lock(cid)
    closed = []
    real = locks.proclock.release

    def bad(fd):
        closed.append(fd)
        real(fd)
        raise OSError("unlock failed")

    monkeypatch.setattr(locks.proclock, "release", bad)
    with pytest.raises(OSError):
        with lock:
            pass
    assert closed, "release was attempted"
    assert not lock._is_owned(), "the thread lock was freed anyway"


# ---- hold_all ----


def test_hold_all_acquires_in_sorted_order(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    order = []
    real = locks.campaign_lock

    def spy(cid):
        order.append(cid)
        return real(cid)

    monkeypatch.setattr(locks, "campaign_lock", spy)
    with locks.hold_all(["zeta", "alpha", "mid"]):
        pass
    assert order == ["alpha", "mid", "zeta"]


def test_hold_all_unwinds_everything_when_a_later_lock_is_busy(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    p = _hold_in_child(tmp_path, "campaign_lock('zulu')")
    try:
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.5)
        with pytest.raises(locks.CampaignBusy):
            with locks.hold_all(["alpha", "zulu"]):
                pass
        assert not locks.campaign_lock("alpha")._is_owned(), "alpha was stranded"
        assert locks.campaign_lock("alpha").acquire(timeout=5)
        locks.campaign_lock("alpha").release()
    finally:
        p.kill()
        p.wait(timeout=10)


def test_hold_all_keeps_unwinding_when_one_release_raises(monkeypatch, tmp_path):
    """A plain `for lock in reversed(held): lock.release()` strands every
    remaining lock when one raises. ExitStack does not."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    first = locks.campaign_lock("aaa")
    real_release = first.release
    calls = []

    def angry():
        calls.append("aaa")
        real_release()
        raise OSError("release exploded")

    monkeypatch.setattr(first, "release", angry)
    with pytest.raises(OSError):
        with locks.hold_all(["aaa", "bbb"]):
            pass
    assert calls == ["aaa"]
    assert locks.campaign_lock("bbb").acquire(timeout=5), "bbb was stranded"
    locks.campaign_lock("bbb").release()


def test_hold_all_is_bounded_by_one_timeout_not_n(monkeypatch, tmp_path):
    """Per-lock deadlines would give an N x LOCK_TIMEOUT convoy."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    p = _hold_in_child(tmp_path, "campaign_lock('zzz')")
    try:
        monkeypatch.setattr(locks, "LOCK_TIMEOUT", 1.0)
        started = time.monotonic()
        with pytest.raises(locks.CampaignBusy):
            with locks.hold_all(["a", "b", "c", "d", "zzz"]):
                pass
        assert time.monotonic() - started < 3.0
    finally:
        p.kill()
        p.wait(timeout=10)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend/tests/test_locks_store.py -q`
Expected: FAIL — `AttributeError: module 'grimoire.store.locks' has no attribute 'CampaignBusy'`

- [ ] **Step 3: Write the implementation**

Replace the body of `backend/src/grimoire/store/locks.py` below its docstring (the docstring is rewritten in Task 9):

```python
from __future__ import annotations

import threading
import time
from contextlib import ExitStack, contextmanager

from . import paths, proclock

# Longer than any legitimate hold, but bounded so a cross-process lock-order
# inversion surfaces as a 409 naming the campaign rather than a wedged server.
# Not a proof: a module migration over a large library on a synced or removable
# filesystem can still exceed it, and its waiter then gets a retryable 409.
LOCK_TIMEOUT = 30.0


class StoreBusy(Exception):
    """Another *process* holds a store lock. One handler maps this to HTTP 409."""

    def __init__(self, name: str, what: str = "resource"):
        super().__init__(f"another grimoire process is editing this {what}")
        self.name = name


class CampaignBusy(StoreBusy):
    def __init__(self, cid: str):
        super().__init__(cid, "campaign")


class ModuleEditBusy(StoreBusy):
    def __init__(self, name: str = "module-edit"):
        super().__init__(name, "module library")


def _remaining(deadline) -> float:
    """RLock treats -1 as "no timeout" and rejects every other negative, so a
    computed remainder must be clamped rather than passed through."""
    if deadline is None or deadline is proclock.NO_WAIT:
        return -1
    return max(0.0, deadline - time.monotonic())


class _ProcessScopedLock:
    """A ``threading.RLock`` plus an OS advisory file lock.

    The file lock is taken on the OUTERMOST acquisition only and released on
    the outermost release, so reentrancy touches the filesystem once -- which
    ``audit.apply_delta`` -> ``sheets.set_field`` and ``module_edit._apply`` ->
    ``recover()`` both depend on.

    ``acquire`` keeps ``RLock``'s contract exactly (returns a bool, never
    raises ``StoreBusy``); ``__enter__`` is the layer that raises. The timeout
    is a deadline for the WHOLE acquisition -- time spent on the thread lock is
    subtracted from the file lock's budget -- and it bounds lock *contention*
    only: ``realpath``/``mkdir``/``open`` on an unavailable path are blocking
    syscalls no userspace deadline can interrupt.
    """

    def __init__(self, name: str, busy: type[StoreBusy]):
        self._name = name
        self._busy = busy
        self._rlock = threading.RLock()
        self._depth = 0
        self._fd: int | None = None

    def _path(self):
        # Resolved per outermost acquisition, never cached: home() resolves
        # live on every call and the Storage location can change at runtime.
        return proclock.lock_path(paths.home(), self._name)

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if not blocking:
            if timeout != -1:
                raise ValueError("can't specify a timeout for a non-blocking call")
            ok = self._rlock.acquire(False)
            deadline = proclock.NO_WAIT     # ONE attempt; None would retry forever
        else:
            deadline = time.monotonic() + timeout if timeout >= 0 else None
            ok = self._rlock.acquire(True, _remaining(deadline))
        if not ok:
            return False
        fd = None
        try:
            if self._depth > 0:             # we already hold the file lock
                self._depth += 1
                return True
            fd = proclock.acquire(self._path(), deadline)
            if fd is None:
                self._rlock.release()
                return False
            self._fd = fd                   # installed BEFORE depth becomes 1
            fd = None                       # ownership transferred to self
            self._depth = 1
            return True
        except BaseException:
            if fd is not None:              # locked but never installed: it
                proclock.release(fd)        # would leak for the process's life
            self._rlock.release()           # never strand the thread lock
            raise

    def release(self) -> None:
        if not self._rlock._is_owned():     # ownership BEFORE state, so a
            raise RuntimeError(             # non-owner corrupts nothing
                "cannot release un-acquired lock")
        if self._depth > 1:
            self._depth -= 1
            self._rlock.release()
            return
        fd, self._fd = self._fd, None
        try:
            if fd is not None:
                proclock.release(fd)
        finally:                            # even if the unlock raises
            self._depth = 0
            self._rlock.release()

    def __enter__(self):
        if not self.acquire(timeout=LOCK_TIMEOUT):
            raise self._busy(self._name)
        return self

    def __exit__(self, *exc) -> bool:
        self.release()
        return False

    def _is_owned(self) -> bool:
        """RLock parity: two sheets tests assert `modules.resolve` runs inside
        the lock by calling this."""
        return self._rlock._is_owned()


_registry_guard = threading.Lock()
_campaign_locks: dict[str, _ProcessScopedLock] = {}


def campaign_lock(cid: str) -> _ProcessScopedLock:
    """Get-or-create the per-campaign lock atomically -- a plain
    ``if cid not in _campaign_locks: ...`` is a check-then-act race that can
    hand two concurrent first-ever callers different lock objects.

    Keyed by cid alone: two *different* stores' campaigns sharing a cid share
    one lock object, which is over-serialization inside one process and never a
    correctness hole, while the lock *file* is per store.
    """
    with _registry_guard:
        lock = _campaign_locks.get(cid)
        if lock is None:
            lock = _campaign_locks[cid] = _ProcessScopedLock(cid, CampaignBusy)
        return lock


_module_edit = _ProcessScopedLock("module-edit", ModuleEditBusy)


def module_edit_lock() -> _ProcessScopedLock:
    """The global module-edit lock. Cross-process for the same reason the
    campaign locks are: whole-directory pack publication mutates the shared
    user library, not just one campaign."""
    return _module_edit


@contextmanager
def hold_all(cids):
    """Hold every named campaign lock, in sorted order, under ONE deadline.

    Sorted because the ordering rule below is what keeps two multi-campaign
    holders in different processes from deadlocking. One deadline because
    applying LOCK_TIMEOUT per lock would give an N x LOCK_TIMEOUT convoy.

    ``ExitStack`` rather than a hand-rolled reversed loop: it registers each
    lock the instant it is acquired, and it runs EVERY registered exit even
    when one of them raises. ``for lock in reversed(held): lock.release()``
    strands every remaining lock the moment one release fails.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT
    with ExitStack() as stack:
        for cid in sorted(set(cids)):
            lock = campaign_lock(cid)
            if not lock.acquire(timeout=_remaining(deadline)):
                raise CampaignBusy(cid)
            stack.push(lock)
        yield
```

- [ ] **Step 4: Run the whole backend suite**

Run: `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, including every pre-existing test. If `test_campaign_lock_is_stable_and_reentrant`, `test_campaign_lock_is_per_campaign`, `test_get_or_create_is_atomic_under_a_cold_registry`, or either `_is_owned()` test in `test_sheets_store.py` fails, the wrapper is not a drop-in — fix the wrapper, not the test.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/locks.py backend/tests/test_locks_store.py
git commit -m "Make campaign_lock cross-process; add hold_all and StoreBusy (#234)"
```

---

### Task 3: Route both multi-lock holders through `hold_all()`

**Files:**
- Modify: `backend/src/grimoire/store/module_edit.py:504-512` (`_campaign_locks`)
- Modify: `backend/src/grimoire/routes.py:1034-1037` (world-module rebind)
- Test: `backend/tests/test_locks_store.py` (append)

**Interfaces:**
- Consumes: `locks.hold_all(cids)` (Task 2).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_locks_store.py`:

```python
def test_both_multi_lock_holders_delegate_to_hold_all():
    """The deadlock this fix would otherwise CREATE: module_edit acquired in
    list_campaigns() order while the rebind route used sorted order. In-process
    the global _M masked the inversion; across processes it does not."""
    from grimoire import routes
    from grimoire.store import module_edit
    assert "hold_all(" in inspect.getsource(module_edit._campaign_locks)
    assert "hold_all(" in inspect.getsource(routes.put_world_module)
    assert "campaign_lock(" not in inspect.getsource(routes.put_world_module)
```

(`put_world_module` is the rebind route at `routes.py:1021`; verified.)

- [ ] **Step 2: Run it to verify it fails**

Run: `... -m pytest backend/tests/test_locks_store.py::test_both_multi_lock_holders_delegate_to_hold_all -q`
Expected: FAIL — `assert 'hold_all(' in ...`

- [ ] **Step 3: Rewrite `module_edit._campaign_locks`**

```python
@contextmanager
def _campaign_locks():
    """Every campaign's lock: the User-edit vs LLM-play exclusion.

    Sorted order is mandatory, not cosmetic: this and the world-module rebind
    route are the only multi-lock holders, and two of them in different
    processes acquiring the same campaigns in opposite orders deadlock. The
    global _M used to mask that; across processes it masks nothing.

    Known limit, pre-existing: the enumeration is a snapshot, so a campaign
    another process creates afterwards is not covered, and campaign deletion
    takes no lock at all.
    """
    with locks.hold_all(c["id"] for c in campaigns.list_campaigns()):
        yield
```

`_campaign_locks` was `ExitStack`'s only use in `module_edit.py` (verified: two hits, the import at line 22 and this function). Change line 22 to `from contextlib import contextmanager`.

- [ ] **Step 4: Rewrite the rebind route's lock block**

In `routes.py`, replace:

```python
        with contextlib.ExitStack() as stack:
            for c in all_cids:                   # sole multi-lock holder; sorted order
                stack.enter_context(store.locks.campaign_lock(c))
```

with:

```python
        with store.locks.hold_all(all_cids):     # sorted order; see locks.hold_all
```

and dedent the block's body one level. Keep `all_cids` and the comment above it intact — the re-read-under-the-lock reasoning is still correct.

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS. `test_module_edit_holds_every_campaign_lock_from_this_registry` asserts `"locks.campaign_lock("` appears in `_campaign_locks` — it will now fail. **Update that test** to assert `"locks.hold_all("` instead; its intent (the multi-holder uses this registry, not a private one) is preserved.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/module_edit.py backend/src/grimoire/routes.py backend/tests/test_locks_store.py
git commit -m "Route both multi-campaign holders through locks.hold_all (#234)"
```

---

### Task 4: Make the module-edit lock cross-process

**Files:**
- Modify: `backend/src/grimoire/store/module_edit.py:29` (`_M`)
- Test: covered by `test_a_second_process_cannot_hold_the_module_edit_lock` (Task 2)

**Interfaces:**
- Consumes: `locks.module_edit_lock()` (Task 2).

- [ ] **Step 1: Confirm the test currently fails for the right reason**

Run: `... -m pytest backend/tests/test_locks_store.py::test_a_second_process_cannot_hold_the_module_edit_lock -q`
Expected: FAIL — the child holds `locks.module_edit_lock()` but `module_edit._M` is a private `threading.RLock`, so the parent's publication is not excluded. (It passes only once `_M` *is* that lock.)

- [ ] **Step 2: Replace `_M`**

In `module_edit.py`, delete `_M = threading.RLock()` and add:

```python
# Cross-process (#234): pack publication rewrites a whole directory in the
# shared user library, so a second backend must be excluded, not just a second
# thread. Reentrant, which _apply -> recover() requires.
_M = locks.module_edit_lock()
```

`locks` is already imported at `module_edit.py:25`. Line 29 was `threading`'s only use in the file (verified), so delete `import threading` from the import block too.

- [ ] **Step 3: Run the tests**

Run: `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, including the whole `test_module_edit.py` suite (`_apply → recover()` exercises reentrancy heavily).

- [ ] **Step 4: Commit**

```bash
git add backend/src/grimoire/store/module_edit.py
git commit -m "Make the module-edit lock cross-process (#234)"
```

---

### Task 5: `StoreBusy` → HTTP 409, and startup tolerates a busy recover

**Files:**
- Modify: `backend/src/grimoire/main.py:32-37` (`_lifespan`), `:53-58` (handlers)
- Test: `backend/tests/test_locks_http.py` (create)

**Interfaces:**
- Consumes: `locks.StoreBusy` (Task 2).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_locks_http.py`:

```python
"""Contention reaches the user as a 409, and never as a hang, a silent skip,
or a mislabelled error (#234)."""

import pytest
from fastapi.testclient import TestClient

from grimoire import main
from grimoire.store import campaigns, locks, worlds


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return TestClient(main.create_app())


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid, module="pool-basic")


def test_store_busy_becomes_a_409(client, monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)

    def busy(*a, **k):
        raise locks.CampaignBusy(cid)

    monkeypatch.setattr(main.store.scenes, "create_scene", busy, raising=False)
    r = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"})
    assert r.status_code == 409
    assert "another grimoire process" in r.json()["detail"]


def test_startup_survives_a_busy_recover(monkeypatch, tmp_path):
    """A second backend starting while the first is mid-edit must serve, not
    refuse to start: recovery is idempotent and the holder is already doing it."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))

    def busy():
        raise locks.ModuleEditBusy()

    monkeypatch.setattr(main.module_edit, "recover", busy)
    with TestClient(main.create_app()) as c:
        assert c.get("/api/config").status_code == 200
```

(`GET /api/config` exists at `routes.py:533`; verified.)

- [ ] **Step 2: Run to verify failure**

Run: `... -m pytest backend/tests/test_locks_http.py -q`
Expected: FAIL — the 409 test returns 500; the startup test raises out of `_lifespan`.

- [ ] **Step 3: Add the handler and the startup guard**

In `main.py`, add to the imports: `from .store import locks, migrations, module_edit`.

Replace `_lifespan`:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    migrations.migrate_scene_ids()
    migrations.bake_char_macros()
    try:
        module_edit.recover()
    except locks.StoreBusy:
        # Another backend holds the module-edit lock: it is running recovery
        # itself, and replay is idempotent. Refusing to start would be strictly
        # worse than starting and serializing per request (#234).
        logging.getLogger(__name__).info(
            "module-edit recovery skipped: another grimoire process holds the lock")
    yield
```

Add `import logging` at the top.

Register the handler inside `create_app()`, beside the existing `HTTPException` one:

```python
    @app.exception_handler(locks.StoreBusy)
    async def store_busy_handler(request: Request, exc: locks.StoreBusy):
        # One handler rather than ~35 route-level try/except blocks.
        return JSONResponse(status_code=409, content={"detail": str(exc)})
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/main.py backend/tests/test_locks_http.py
git commit -m "Map StoreBusy to HTTP 409; tolerate a busy recover at startup (#234)"
```

---

### Task 6: `create_scene` holds the lock; `capture_baseline` stops swallowing

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py:137-165` (`create_scene`)
- Modify: `backend/src/grimoire/store/audit.py:64-93` (`capture_baseline`)
- Test: `backend/tests/test_locks_http.py` (append)

**Interfaces:**
- Consumes: `locks.campaign_lock`, `locks.StoreBusy` (Task 2).

**Why these two together:** `capture_baseline` runs *after* `create_scene` has already durably written the scene file, so re-raising there alone would return 409 and leave an orphaned scene. The lock has to move above the first write first.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_locks_http.py`:

```python
def test_create_scene_leaves_nothing_behind_when_busy(monkeypatch, tmp_path):
    """Contention must strike before the first durable write."""
    from grimoire.store import scenes

    cid = _campaign(monkeypatch, tmp_path)
    before = sorted(p.name for p in (campaigns.campaign_root(cid) / "scenes").glob("*")) \
        if (campaigns.campaign_root(cid) / "scenes").exists() else []

    real = locks.campaign_lock

    class Busy:
        def __enter__(self):
            raise locks.CampaignBusy(cid)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(locks, "campaign_lock", lambda c: Busy() if c == cid else real(c))
    with pytest.raises(locks.CampaignBusy):
        scenes.create_scene(cid, "Saltmarch")
    d = campaigns.campaign_root(cid) / "scenes"
    after = sorted(p.name for p in d.glob("*")) if d.exists() else []
    assert after == before, "a scene file survived a busy create"


def test_concurrent_scene_creation_yields_distinct_ids(monkeypatch, tmp_path):
    """_numbering/repad/uniquify pick the SID; without the lock around them two
    creators select the same one and one overwrites the other."""
    import threading

    from grimoire.store import scenes

    cid = _campaign(monkeypatch, tmp_path)
    got, guard = [], threading.Lock()

    def make(i):
        sid = scenes.create_scene(cid, f"Scene {i}")
        with guard:
            got.append(sid)

    ts = [threading.Thread(target=make, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    assert len(got) == 8
    assert len(set(got)) == 8, f"duplicate scene ids: {got}"


def test_capture_baseline_propagates_contention(monkeypatch, tmp_path):
    """It swallows every Exception so a capture failure cannot fail scene
    creation. Contention is the one exception: a scene that quietly cannot be
    audited is not recoverable, a 409 is."""
    from grimoire.store import audit

    cid = _campaign(monkeypatch, tmp_path)

    def busy(c):
        raise locks.CampaignBusy(c)

    monkeypatch.setattr(audit.locks, "campaign_lock", busy)
    with pytest.raises(locks.CampaignBusy):
        audit.capture_baseline(cid, "0001-x")
```

- [ ] **Step 2: Run to verify failure**

Run: `... -m pytest backend/tests/test_locks_http.py -q`
Expected: FAIL on all three.

- [ ] **Step 3: Wrap `create_scene`'s whole body**

In `scenes.py`, change `create_scene` so the entire body after `_require_campaign(cid)` runs under the lock:

```python
def create_scene(cid: str, title: str, suggested_date: str | None = None,
                 pcless: bool = False) -> str:
    _require_campaign(cid)
    # The WHOLE body, not just the write (#234): _numbering() picks the next
    # number from what is on disk, repad() renames every scene in the campaign
    # when the width grows, and uniquify() resolves the id against the
    # directory. Two concurrent creators outside this lock select the same id
    # and one overwrites the other. Locking here also means contention fails
    # before any durable side effect, which is what lets capture_baseline
    # below stop swallowing it.
    with locks.campaign_lock(cid):
        d = _scenes_dir(cid)
        d.mkdir(parents=True, exist_ok=True)
        number, width = _numbering(cid)
        if len(str(number)) > width:  # 999 -> 1000: widen the whole campaign first
            width = len(str(number))
            repad(cid, width)
        now = now_iso()
        base = scene_ids.format_sid(number, width, None, slugify(title))
        sid = uniquify(base, lambda c: _scene_path(cid, c).exists())
        active = _get_active_connection()
        meta = {"title": title, "model": active["model"] if active else "",
                "created": now, "updated": now}
        if pcless:
            meta["pcless"] = "true"
        if suggested_date:
            try:
                provider = calendars.get_provider(
                    calendars.read_calendar(campaigns.campaign_root(cid))["primary"])
                meta["suggested_date"] = calendars.normalize(provider, suggested_date)
            except (calendars.CalendarError, KeyError):
                pass  # only a hint — a bad one is dropped, never an error
        atomic.write_text(_scene_path(cid, sid), dump_frontmatter(meta, ""))
        from . import audit  # lazy: audit imports campaigns/sheets, scenes must not cycle
        audit.capture_baseline(cid, sid)   # reentrant: we already hold the lock
        return sid
```

Add `locks` to `scenes.py:8`, which currently reads
`from . import atomic, calendars, campaigns, overlay, scene_ids, scene_refs` —
make it `from . import atomic, calendars, campaigns, locks, overlay, scene_ids, scene_refs`.
No cycle: `locks` imports only `paths` and `proclock`.

- [ ] **Step 4: Re-raise contention in `capture_baseline`**

In `audit.py`, change the final handler:

```python
    except locks.StoreBusy:
        # The one exception to "never raises": contention means the snapshot
        # was not merely attempted-and-failed but never attempted, and a scene
        # without a baseline silently loses its audit delta. Its only caller,
        # scenes.create_scene, holds the lock already (reentrant), so this is a
        # guard against a future caller that does not (#234).
        raise
    except Exception:  # noqa: BLE001 — never fail the caller
        return
```

Update the docstring's "Never raises" to "Never raises, except `locks.StoreBusy`".

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/src/grimoire/store/audit.py backend/tests/test_locks_http.py
git commit -m "Lock create_scene's whole body; stop swallowing contention in capture_baseline (#234)"
```

---

### Task 7: Adjudication contention is a 409, not a `check_error`

**Files:**
- Modify: `backend/src/grimoire/routes.py:3896-3906`
- Test: `backend/tests/test_locks_http.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_locks_http.py`:

```python
def test_adjudication_contention_is_not_reported_as_a_check_error(monkeypatch):
    """resolve_check takes the campaign lock internally. A busy lock is not a
    check failure and must not be dressed up as one."""
    from grimoire import routes

    src = inspect.getsource(routes)
    marker = "except store.locks.StoreBusy"
    assert marker in src, "contention must be caught ahead of the broad handler"
```

Add `import inspect` at the top of the test file. Replace this source assertion with a behavioural test if the adjudication route can be driven end-to-end from an existing fixture in `backend/tests/test_proposals*.py` — prefer that if one exists.

- [ ] **Step 2: Run to verify failure**

Run: `... -m pytest backend/tests/test_locks_http.py -q`
Expected: FAIL

- [ ] **Step 3: Catch contention ahead of the broad handler**

In `routes.py`, immediately before `except Exception as exc:  # noqa: BLE001 — any failure reverts cleanly`, insert:

```python
            except store.locks.StoreBusy:
                # Contention is not a check error. Revert exactly as the broad
                # path does, then let the 409 handler answer. The revert can
                # itself contend; if it does, the record stays "resolving",
                # which is in proposals.NON_TERMINAL and so is retired by the
                # next send's supersede() -- and until then the route answers
                # 409 "adjudication in progress", which is accurate (#234).
                try:
                    store.proposals.transition(cid, sid, pid, ("resolving",), "pending")
                except store.locks.StoreBusy:
                    pass
                raise
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_locks_http.py
git commit -m "Report adjudication contention as 409, not check_error (#234)"
```

---

### Task 8: The SSE finalizers emit a `busy` frame and persist nothing

**Files:**
- Modify: `backend/src/grimoire/routes.py:2483-2503` (`_fence_stream.event_stream`)
- Test: `backend/tests/test_locks_http.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_locks_http.py`:

```python
def test_a_busy_finalize_emits_an_error_frame_and_persists_nothing(monkeypatch, tmp_path):
    """finalize() runs OUTSIDE _fence_stream's try, so contention there would
    abort the stream with no frame at all. It must NOT route through on_error:
    that persists the narration, which would leave a roll fence with no
    proposal record -- the invariant the proposal-before-narration ordering
    exists to maintain."""
    from grimoire import routes

    src = inspect.getsource(routes._fence_stream)
    assert "StoreBusy" in src, "finalize must be guarded"
    assert "on_error" not in src.split("for frame in")[-1], \
        "the busy path must not persist narration via on_error"
```

- [ ] **Step 2: Run to verify failure**

Run: `... -m pytest backend/tests/test_locks_http.py -q`
Expected: FAIL

- [ ] **Step 3: Guard the `finalize` call**

In `_fence_stream.event_stream`, replace:

```python
        for frame in finalize(watcher):
            yield frame
```

with:

```python
        try:
            frames = finalize(watcher)
        except store.locks.StoreBusy as exc:
            # Deliberately NOT on_error: that persists watcher.narration, and
            # narration whose roll fence has no proposal record destroys the
            # proposal-before-narration guarantee. The turn is lost and the
            # user re-sends; a lost turn is recoverable, a fence without its
            # proposal is not (#234).
            yield _sse({"error": {"detail": str(exc), "kind": "busy"}})
            return
        for frame in frames:
            yield frame
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_locks_http.py
git commit -m "Emit a busy SSE frame instead of persisting an orphaned narration (#234)"
```

---

### Task 9: Documentation — stop overclaiming

**Files:**
- Modify: `README.md:176-183`
- Modify: `docs/android-architecture.md` §synced-folder mode (~line 151)
- Modify: `backend/src/grimoire/store/locks.py` (module docstring)
- Modify: `backend/src/grimoire/store/module_edit.py:1-11` (threat model)

**Why this is a task and not a footnote:** the overclaim is half the bug. The code change makes the guarantee real where one is achievable; this makes the docs stop promising the part that is not.

- [ ] **Step 1: README**

After the "Because the whole library is just files…" paragraph, add:

```markdown
One caveat: **do not actively use grimoire on two devices at once.** Sync
clients (Dropbox, OneDrive, iCloud, Syncthing) resolve simultaneous edits by
making conflict copies on their own schedule, and grimoire cannot merge those —
one side's edit wins and the other becomes a stray file. Let the sync settle
before switching devices. Concurrent access by more than one grimoire process
*on the same machine* is safe: those serialize against each other.
```

- [ ] **Step 2: `docs/android-architecture.md`**

In the "Synced-folder mode (opt-in)" item, append:

```markdown
   The same caveat as on the desktop applies, and more sharply here: the phone
   and the PC are two devices, so do not play on both at once. Locking is
   machine-local (`store/proclock.py`) and cannot span them — let the sync
   settle before switching.
```

- [ ] **Step 3: `store/locks.py` docstring**

Replace the closing line `Locks are process-local; the store is single-process by design.` with:

```
The lock is an ``RLock`` so a caller can compose lower-level mutators --
``audit.apply_delta`` calls ``sheets.set_field`` under an already-held lock --
and it is additionally an OS advisory file lock (``store/proclock.py``), so it
excludes concurrent *processes* and not merely threads (#234).

Three limits, all deliberate:

- **Not across devices.** The lock file is machine-local, so a lock held on
  one device is invisible to another sharing the store through a synced
  folder. No filesystem lock on a sync-replicated store can do better; the
  sync client resolves simultaneous edits with conflict copies.
- **Not across OS users**, whose lock directories differ.
- **Not every campaign mutation** -- only the ones that take this lock.
  ``scenes.append_message`` (#254), ``rolls`` (#255), and
  ``campaigns.rename_campaign``/``delete_campaign`` still take none.

Ordering rules (deadlock avoidance):

- module-edit lock -> campaign locks -> audit baseline lock / rolls lock.
- Every multi-campaign holder acquires in **sorted cid order**, via
  ``hold_all``. Two of them in different processes acquiring the same
  campaigns in opposite orders would deadlock; the global module-edit lock
  used to mask that in-process and no longer can.
- campaign lock -> audit baseline lock, never reversed (``store/audit.py``).
```

Keep the existing "Who takes it:" list, adding `- ``scenes.create_scene`` (#234);`.

- [ ] **Step 4: `store/module_edit.py` threat model**

Replace the "Concurrency threat model (spec): exactly two actors…" paragraph:

```
Concurrency threat model: the spec assumed exactly two actors -- the User (UI)
and the LLM (play flows), both inside one process. A synced store adds a third,
a second grimoire process, which that design did not account for (#234). The
module-edit lock and every campaign lock are now OS file locks as well as
in-process ones, so all three actors are excluded on one machine; two *devices*
sharing a synced folder are still not, and cannot be by any filesystem lock.
```

- [ ] **Step 5: Verify nothing stale remains**

Run: `grep -rn "single-process by design\|process-local" backend/src/grimoire/store/locks.py backend/src/grimoire/store/module_edit.py`
Expected: no hits claiming the old guarantee.

- [ ] **Step 6: Run the full suite and commit**

```bash
PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q
git add README.md docs/android-architecture.md backend/src/grimoire/store/locks.py backend/src/grimoire/store/module_edit.py
git commit -m "Document the real concurrency guarantee and its three limits (#234)"
```

---

## Final verification

- [ ] Full backend suite: `PYTHONPATH="$PWD/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`
- [ ] Frontend untouched, but confirm: from `frontend/`, `npx tsc -b`
- [ ] `/codex:review` against the full diff (CLAUDE.md implementation→done gate)
- [ ] `/codex:adversarial-review` against the diff *and* the spec, asking specifically whether the diff implements the spec

## Follow-up issues to file

- Optimistic concurrency (CAS on a content digest) to *detect* the cross-device clobber no lock can prevent. `sheets.write(expected=…)` / `set_field(expect=…)` are the existing foothold.
- Lock campaign creation and deletion, closing the enumerate-then-lock window in `_campaign_locks()`.
- `campaigns.rename_campaign` / `set_campaign_response` / `touch` take no lock.
- `assets._image_locks` is still process-local.
- `routes.put_data_dir` switches the store root without excluding held locks.
