# Cross-process campaign locks (#234)

Every mutation that takes `locks.campaign_lock(cid)` serializes on a
`threading.RLock`. A `threading.RLock` protects nothing across process
boundaries. Meanwhile the README tells users to point the data directory at a
synced folder to share one library across devices, and the same store is
reachable from the desktop app and the Android APK.

Two backends — two devices, or two processes on one machine — editing one
campaign therefore have no mutual exclusion at all. Because most records are
rewritten whole, the loser's edit is silently overwritten: no detection, no
conflict marker, no user-visible symptom.

## What this actually guarantees

Stated precisely, because the issue's framing conflates two different problems
and the first draft of this spec then overclaimed in three more places.

**Mutations that take the campaign lock are serialized across processes running
as one OS user on one machine.** That is the desktop app running alongside
`run.ps1`, two backends started by accident, or a CLI script racing the server.
This is a real guarantee, enforced by the operating system.

Three limits, all deliberate:

- **Not across devices.** The lock file is machine-local by design (see The
  lock file), so the store's own filesystem and its lock support are
  irrelevant — a lock held on device A is invisible to device B whether the
  store is on Dropbox, NFS, or SMB. No lock-based design can fix the
  multi-device case: the sync client owns that domain and resolves simultaneous
  edits with conflict copies on its own schedule.
- **Not across OS users.** The lock directory is per-user, so two accounts on
  one machine pointed at one shared store are not excluded from each other.
  Grimoire is a single-user desktop/mobile app; the alternative (a
  world-readable lock directory at a predictable path) trades this narrow gap
  for symlink-substitution and unlink-denial attacks.
- **Not every campaign mutation** — only the ones that take the lock. Several
  campaign-scoped writers take no lock today, and this spec does not change
  that. They are enumerated in What remains unlocked.

Detecting the cross-device clobber after the fact — optimistic concurrency,
compare-and-swap on a content hash — is a separate, larger change. See
Non-goals.

## Goals

- The campaign lock domain excludes concurrent processes, not just threads.
- The module-edit domain likewise: whole-directory pack publication mutates the
  shared user library and is guarded today by a process-local `_M`.
- One mechanism, at the existing choke point, with the public surface of
  `campaign_lock()` unchanged so its ~35 call sites do not move.
- Contention fails loudly and quickly rather than hanging — including at the
  three sites that would otherwise swallow or misreport it.
- Documentation that describes the synced-folder workflow honestly.

## Non-goals

- **Cross-device mutual exclusion.** Not achievable with locks (above).
- **Optimistic concurrency / CAS.** Option 2 in the issue: compare the on-disk
  digest at write time against the digest that was read, turning a silent
  cross-device clobber into an error. It is the only thing that can *detect*
  the multi-device case, but it prevents nothing and touches ~100
  `atomic.write_text` call sites, overlapping #233's whole-record-rewrite
  territory. Filed as a follow-up. Note that `sheets.write(expected=…)` and
  `sheets.set_field(expect=…)` already do exactly this at the sheet level, so a
  future generalization has a foothold rather than a blank page.
- **Widening the lock domain.** Every unlocked writer listed in What remains
  unlocked stays unlocked. Locking them is a behaviour change with its own
  deadlock analysis, and bundling it here would make this diff unreviewable.
- **Stale-lock reaping.** Not needed: both `flock` and `LockFileEx` are
  released by the kernel when the holding process dies, however it dies, and
  the lock fd is non-inheritable (PEP 446 makes Python fds non-inheritable by
  default), so an `exec`'d child cannot extend the lock past its parent.
  Grimoire never `fork()`s without `exec`.
- **Multi-file transactions.** Unchanged from #233; the lock makes a
  read-modify-write span exclusive, it does not make it atomic.

## Design

### The hybrid lock

`campaign_lock(cid)` keeps its registry — same `setdefault` under
`_registry_guard`, same identity stability (`campaign_lock(c) is
campaign_lock(c)`), same key (the cid alone). What changes is the object it
hands back: a `_CampaignLock` composing the process-local `threading.RLock`
with an OS advisory file lock.

Keying the registry by cid alone means two *different* stores' campaigns that
share a cid share one lock object. That is over-serialization inside one
process, never a correctness hole, and it keeps the registry's existing tests
meaningful. The lock *file* path is resolved per outermost acquisition and
pinned for the hold, so a **Storage location** change takes effect on the next
acquisition (`home()` resolves live on every call).

#### The acquire/release protocol, exactly

`acquire()` keeps `threading.RLock`'s contract — `acquire(blocking=True,
timeout=-1) -> bool`, returning `False` on failure and never raising
`CampaignBusy`. `__enter__` is the layer that raises. `test_locks_store.py:57`
depends on the bool return; the `with` statement is what every other call site
uses.

The timeout is a deadline for the **whole** acquisition. Time spent waiting on
the thread lock is subtracted from the budget left for the file lock, so a
`with` block waits `LOCK_TIMEOUT` in total, not twice that.

```
acquire(blocking, timeout):
    deadline = monotonic() + timeout   (None if timeout < 0)
    if not rlock.acquire(blocking, remaining(deadline)):
        return False
    if depth > 0:                      # we already hold the file lock
        depth += 1
        return True
    try:
        fd, path = open_and_lock(deadline)   # None,None on timeout
    except BaseException:
        rlock.release()                # never strand the thread lock
        raise
    if fd is None:
        rlock.release()
        return False
    self._fd, self._path = fd, path    # installed BEFORE depth becomes 1
    depth = 1
    return True

release():
    if depth == 0:
        raise RuntimeError("release unlocked lock")   # RLock parity
    if depth > 1:
        depth -= 1
        rlock.release()
        return
    try:
        unlock_and_close(self._fd)
    finally:                           # even if unlock raises
        self._fd = self._path = None
        depth = 0
        rlock.release()
```

`depth` is only ever read or written while the thread lock is held, so it needs
no guard of its own, and no second thread can observe an intermediate state.
The invariants the review demanded: depth stays 0 until the OS lock is held;
the fd is installed before depth becomes 1; the outer release clears state,
closes the fd and releases the thread lock even if the unlock call raises;
every failure path — `CampaignBusy`, `PermissionError`, `KeyboardInterrupt`,
`SystemExit`, a failing `stat` — releases the thread lock via the bare
`except BaseException`.

The public surface is preserved exactly — the context-manager protocol,
`acquire(blocking, timeout)`, `release()`, and `_is_owned()`. `_is_owned()` in
particular is what two `test_sheets_store.py` tests use to assert that
`modules.resolve` happens inside the lock; it delegates to the thread lock.

Reentrancy touches the filesystem once, not once per level: `audit.apply_delta`
calling `sheets.set_field` under an already-held lock, and `_apply` calling
`recover()` under an already-held `_M`, both take the `depth > 0` path. The
`_M` case is not hypothetical — `module_edit._apply` opens with `with _M:` and
immediately calls `recover()`, which opens with `with _M:` again.

### The lock file

Machine-local, per-user, outside the store, and outside any directory subject
to automatic cleaning:

```
<lockdir>/<sha256(normcase(realpath(home())))[:16]>/<cid-slug>-<sha256(cid)[:16]>.lock
```

`<lockdir>` is, in order:

- **Windows**: `%LOCALAPPDATA%\grimoire\locks`
- **POSIX with `$XDG_RUNTIME_DIR`**: `$XDG_RUNTIME_DIR/grimoire/locks` — per-user
  by construction, mode 0700, and cleared only at logout
- **POSIX otherwise, including Android/Chaquopy**: `~/.cache/grimoire/locks`.
  Under Chaquopy `Path.home()` is the app's private files directory, which is
  stable and is *not* the cache directory Android evicts under storage
  pressure.

The first draft put this under `tempfile.gettempdir()`, which was wrong on
three counts the review caught: a `/tmp` cleaner can unlink a held lock file,
per-user temp directories vary, and a shared `/tmp` needs an ownership and
permissions story. A persistent per-user directory has none of those problems.

Not inside the store, because the store may be a synced folder. A lock file
there would be replicated, would accumulate conflict copies, and — on Windows
with OneDrive Files-On-Demand — holding an open handle on it can stall. Since a
file lock cannot cross devices anyway, putting it in the synced tree buys
nothing and costs litter in the user's library.

**Store identity.** `os.path.normcase(os.path.realpath(home()))`: `realpath`
resolves symlinks, junctions, DOS 8.3 names and `subst` drives (Python 3.8+
uses `GetFinalPathNameByHandle` on Windows); `normcase` lowercases and
normalizes separators on Windows and is a **no-op on POSIX**, so genuinely
distinct case-sensitive paths stay distinct. The first draft's unconditional
`casefold()` would have aliased them. Residual, accepted and documented: a
mapped drive versus its UNC form, or a bind mount, can still hash differently;
two processes that reach one store by such different names are not excluded.

**Filename.** The cid arrives from a route parameter and a lock path is not the
place to trust it (cf. #240). The readable prefix is the cid filtered to
`[A-Za-z0-9._-]` and truncated to 40 characters — the same bound
`atomic.py:_MAX_NAME_HINT` uses, and well inside `NAME_MAX` and Windows'
component limit once the 16-hex suffix and `.lock` are added. The suffix is 64
bits of SHA-256 over the full cid, so distinct cids that sanitize to the same
prefix still get distinct files and a collision is not reachable by accident or
by construction.

**POSIX**: `fcntl.flock(fd, LOCK_EX | LOCK_NB)` in a retry loop. After
acquiring, compare `(st_dev, st_ino)` from `os.fstat(fd)` against
`os.stat(path)`; on a mismatch, unlock, close, and reopen. This is
defence-in-depth, not a proof: an unlink between the check and the return is
still theoretically possible. It is not load-bearing, because the chosen
directory is not one anything cleans while the process runs — the first draft
claimed this check "closes the race" and that was false.

**Windows**: `os.lseek(fd, 0, os.SEEK_SET)` then `msvcrt.locking(fd,
msvcrt.LK_NBLCK, 1)` in the same retry loop, and the same seek before
`msvcrt.LK_UNLCK`. `msvcrt.locking` locks a byte range starting at the *current
file position*, unlike whole-file `flock`, so the seek is mandatory in both
directions. Always byte 0, length 1; locking beyond EOF is legal on Windows, so
the lock file stays zero-length. No inode check — an open file cannot be
deleted on Windows, so the POSIX race does not exist there.

**Contention versus failure.** The retry loop retries *only* the errors that
mean "held elsewhere": `BlockingIOError` (EAGAIN/EWOULDBLOCK) on POSIX,
`OSError` with `errno.EACCES` or `errno.EDEADLOCK` on Windows. Everything else
— `ENOSYS`, `ENOTSUP`, `ENOLCK`, `EBADF`, a permission error on the lock
directory — propagates unchanged. Reporting a filesystem that cannot lock, or a
directory the user cannot write, as HTTP 409 "another process is editing this
campaign" would send the user hunting a process that does not exist.

Stdlib only — no new dependency, and the base dependency list must stay
Android-installable. `fcntl` is present under Chaquopy, so the Android build
takes the POSIX path.

### Contention

A new `locks.CampaignBusy` exception, raised by `__enter__` after
`LOCK_TIMEOUT = 30.0` seconds.

Bounded rather than indefinite, because a cross-process lock-order inversion is
a real possibility (below) and an unbounded wait would turn it into a hung
request with no diagnostic.

Thirty rather than the five the first draft proposed. Five is not longer than
every legitimate hold, and the review named three counterexamples: a module
rename holds every campaign lock across `_run_migration`, which rewrites every
governed sheet through `atomic.write_text` (flush, `fsync`, metadata copy,
replace — per file); `audit.capture_baseline` reads every sheet plus the
schema; `context._mechanics` reads the pack, every actor sheet and multiple
rule docs. On a large library over a synced or removable filesystem, with an
antivirus scanner in the path, five seconds is reachable. Thirty is not a proof
either — a big enough migration can still exceed it — and the honest statement
is that a waiter gets a 409 naming the cause rather than a wedged server, and
the operation is retryable.

**One deadline per acquisition span, not per lock.** `module_edit._campaign_
locks()` and the world-module rebind route each acquire N campaign locks while
holding the earlier ones. Applying `LOCK_TIMEOUT` per lock would give an N×30 s
convoy. Both call a new `locks.hold_all(cids)` context manager that computes a
single deadline and passes the remaining budget to each `acquire`, so the whole
span is bounded by `LOCK_TIMEOUT`. `hold_all` is also where the sorted-order
rule below is enforced, in one place rather than two.

The deadline covers the retry loop only. `realpath`, `mkdir`, `open` and `stat`
on an unavailable network path can block outside it — an OS-level hang that no
userspace deadline can bound, and one that today's code has equally.

#### Where contention must not be swallowed

A single `@app.exception_handler(locks.CampaignBusy)` in `main.create_app()`
returns HTTP 409 with a message naming the cause — one handler rather than ~35
route-level `try`/`except` blocks, matching how the app already handles
`HTTPException` centrally. But three paths defeat it, and each needs a
deliberate decision:

1. **`audit.capture_baseline`** wraps its whole body in `except Exception:
   return` — "never raises, a capture failure must not fail scene creation".
   That would turn contention into a scene with no baseline, which silently
   loses that scene's audit delta. `CampaignBusy` is re-raised ahead of the
   broad catch: a 409 on scene creation is trivially retryable, a scene that
   quietly cannot be audited is not. The docstring's "never raises" gains its
   one stated exception.
2. **The proposal adjudication route** catches every exception from
   `checks.resolve_check` (which takes the campaign lock internally) and turns
   it into a `check_error` SSE frame. `CampaignBusy` is caught first, the
   proposal is reverted `resolving → pending` exactly as the broad path does,
   and then it propagates as a 409 — contention is not a check error and must
   not be reported as one.
3. **Startup.** `_lifespan` calls `module_edit.recover()` before serving, which
   takes `_M` and, when a journal must be replayed, every campaign lock. A
   second backend starting while the first is mid-edit would fail to start
   after the timeout. `CampaignBusy` out of `recover()` is caught **in
   `_lifespan` only** and logged: recovery is idempotent, the process holding
   those locks is itself running recovery or an edit, and refusing to start is
   strictly worse than starting and serializing per request. The in-request
   `_apply → recover()` path is untouched and still propagates as a 409.

#### The SSE paths

`_fence_stream` calls `finalize(watcher)` **outside** its `try`, so a
`CampaignBusy` raised there would abort the stream with no frame emitted. The
`finalize` call is wrapped, but **not** routed through `on_error`.

The first draft proposed exactly that, and it was wrong: `_chat_stream`'s
`on_error` persists `watcher.narration`, and doing so when the proposal could
not be written would persist narration whose roll fence has no proposal record
— destroying the proposal-before-narration invariant that `_chat_stream`'s
docstring calls out as the whole point of the ordering.

So on `CampaignBusy` the wrapper persists **nothing** and emits
`{"error": {"detail": …, "kind": "busy"}}`, the shape an `LLMError` already
produces. The turn is lost and the user re-sends. That is a real cost, stated
rather than hidden: a lost turn is recoverable, a transcript whose mechanical
decision point has no proposal is not.

### The deadlock this would otherwise create

`module_edit._campaign_locks()` acquires every campaign's lock in
`campaigns.list_campaigns()` order. `routes.py:1036` (the world-module rebind)
acquires its set in **sorted** order. These are the only two multi-lock
holders. In-process the inversion is masked, because the global `_M` module-edit
lock serializes module publication against everything that could race it.
Across processes `_M` masks nothing, and two publications acquiring the same
campaigns in opposite orders deadlock.

Both call sites move to `locks.hold_all(cids)`, which sorts. `locks.py`
documents the full hierarchy, since the review correctly noted that ordering
among campaign locks is only part of the picture:

```
module-edit lock (_M)
  └─ campaign locks, always in sorted cid order   (locks.hold_all)
       ├─ audit baseline lock   (store/audit.py)
       └─ rolls lock            (store/rolls.py, via _project_resolution)
```

No inverse edge exists today; the rule is written down so the next one is
caught in review.

`_M` itself becomes cross-process, using the same primitive with a lock file at
`<lockdir>/<store-hash>/module-edit.lock`. Leaving it process-local would leave
whole-directory pack publication — which mutates the shared user library, not
just one campaign — unguarded against the exact scenario this spec exists for.
It is the same `_ProcessLock` primitive, so there is one implementation of the
platform branching, and it is reentrant, which `_apply → recover()` requires.

**A limit of the ordering rule, pre-existing and unchanged.**
`_campaign_locks()` snapshots `campaigns.list_campaigns()` *before* acquiring.
A campaign created by another process after the snapshot is not locked, and
`campaigns.delete_campaign` takes no lock at all, so a campaign tree can be
removed during a migration that believes it holds it. Both are true today
between threads; making the locks cross-process widens the window without
creating the bug. Fixing it means locking campaign creation and deletion, which
is What remains unlocked, below.

### What remains unlocked

The opening sentence of this spec says "every mutation that takes
`campaign_lock`" rather than "every campaign mutation", because these writers
take no campaign lock before this change and none after:

| Writer | Status |
|---|---|
| `scenes.append_message` — whole-file read-modify-write | #254 |
| `rolls` — private process-local `_LOCKS` registry | #255 |
| `campaigns.rename_campaign` / `set_campaign_response` / `touch` / `delete_campaign` | no lock at all — follow-up |
| `assets._image_locks` — private process-local registry | follow-up |
| `routes.put_data_dir` — switches the store root under any held lock | follow-up |

Listing them is the point: after this change the campaign lock is a real
cross-process guarantee, and it would be easy to read that as covering more
than it does.

### Documentation

The overclaim is the other half of the bug, so the doc changes are part of the
fix, not a footnote:

- **README** "Where your data lives": keep the synced-folder workflow, add that
  grimoire must not be *actively used on two devices at once* — sync clients
  resolve simultaneous edits with conflict copies and grimoire cannot merge
  them — and state that concurrent access by processes of one user on one
  machine is serialized.
- **`docs/android-architecture.md`** §synced-folder mode: the same caveat, since
  that section describes precisely the two-device workflow.
- **`store/locks.py`** module docstring: replace "Locks are process-local; the
  store is single-process by design" with the new guarantee, its three limits,
  and the lock hierarchy above.
- **`store/module_edit.py`** threat model: "exactly two actors — the User (UI)
  and the LLM (play flows)" gains the third actor (a second process) and says
  what covers it.

## Testing

New tests in `backend/tests/test_locks_store.py`. Cross-process behaviour is
tested with a real `subprocess` child inheriting `GRIMOIRE_HOME` and the
parent's `sys.path` — an in-process test cannot demonstrate cross-process
exclusion.

**Exclusion**

- A child holding the campaign lock makes the parent's `with` raise
  `CampaignBusy` within the timeout; the parent succeeds once the child exits.
- Killing the holder releases the lock — the guard on the no-reaping decision.
- Two different campaigns do not block each other across processes.
- Reentrant acquisition takes the file lock exactly once: a child stays blocked
  while the parent is at depth 2 and is released only after the outermost
  release.
- **A child holding `module-edit.lock` blocks another process's module
  publication.** The first draft's plan tested only campaign locks, so the
  second stated goal could have been entirely broken with every test passing.

**Contract and failure paths**

- `acquire(timeout=…)` returns `False` on contention and does **not** raise —
  the `RLock` contract `test_locks_store.py:57` depends on.
- After a timed-out `with`, the thread lock is free: another thread in the same
  process acquires it once the child exits.
- Repeated timeouts and repeated inode-mismatch retries leak no file
  descriptors (compare fd count before and after).
- A permanent lock error (injected `OSError(ENOLCK)`) propagates rather than
  becoming `CampaignBusy` / 409.
- An exception raised inside a held `with` block still releases both the file
  lock and the thread lock.
- `hold_all` over N campaigns is bounded by one `LOCK_TIMEOUT`, not N of them.

**Placement and integration**

- No new file appears under `GRIMOIRE_HOME` — the guard that lock files stay
  out of the synced library.
- `CampaignBusy` reaches the client as HTTP 409, through `TestClient`.
- `capture_baseline` propagates `CampaignBusy` instead of swallowing it.
- `_lifespan` startup survives a `CampaignBusy` from `recover()`.
- The SSE `finalize` wrapper emits a `busy` error frame and persists **no**
  narration.
- `hold_all` acquires in sorted cid order, and both multi-lock holders use it.

The existing suite is the regression guard for the drop-in claim:
`test_locks_store.py`'s identity, reentrancy, per-campaign and
domain-serialization tests, and the two `_is_owned()` tests in
`test_sheets_store.py`, all pass unchanged.

## Follow-ups

- Optimistic concurrency (CAS on a content digest) to *detect* the cross-device
  clobber that no lock can prevent.
- Lock campaign creation and deletion, closing the enumerate-then-lock window
  in `_campaign_locks()`.
- `campaigns.rename_campaign` / `set_campaign_response` / `touch` take no lock.
- `assets._image_locks` is still process-local.
- `routes.put_data_dir` switches the store root without excluding held locks.
