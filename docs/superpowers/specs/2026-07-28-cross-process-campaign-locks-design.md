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
  store is on Dropbox, NFS, or SMB. No *filesystem* lock on a sync-replicated
  store can fix the multi-device case: the sync client owns that domain and
  resolves simultaneous edits with conflict copies on its own schedule. A
  shared lock *service* could, and is out of scope for a local-first app with
  no server.
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
- **Widening the lock domain**, with exactly one forced exception.
  `scenes.create_scene` takes the campaign lock so that contention cannot leave
  an orphaned scene without a baseline (see Where contention must not be
  swallowed). Every other unlocked writer listed in What remains unlocked stays
  unlocked: locking them is a behaviour change with its own deadlock analysis,
  and bundling it here would make this diff unreviewable.
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
`with` block waits `LOCK_TIMEOUT` in total, not twice that. It bounds *lock
contention* only — see the note on blocking syscalls under Contention.

```
def _remaining(deadline):              # never pass a negative timeout: only
    if deadline is None: return -1     # -1 means "no timeout" to RLock, and
    return max(0.0, deadline - monotonic())   # any other negative is invalid

acquire(blocking=True, timeout=-1):
    if not blocking:
        if timeout != -1:              # RLock parity: this combination is a
            raise ValueError(...)      # ValueError, not a silent success
        ok = rlock.acquire(False)      # and carries no timeout argument
        deadline = NO_WAIT             # sentinel: ONE file-lock attempt
    else:
        deadline = monotonic() + timeout if timeout >= 0 else None
        ok = rlock.acquire(True, _remaining(deadline))
    if not ok:
        return False
    fd = None
    try:
        if depth > 0:                  # we already hold the file lock
            depth += 1
            return True
        fd, path = open_and_lock(deadline)    # (None, None) on timeout
        if fd is None:
            rlock.release()
            return False
        self._fd, self._path = fd, path       # installed BEFORE depth is 1
        fd = None                             # ownership transferred to self
        depth = 1
        return True
    except BaseException:
        if fd is not None:             # locked but never installed: unlock
            unlock_and_close(fd)       # and close, or it leaks forever
        rlock.release()                # never strand the thread lock
        raise

release():
    if not rlock._is_owned():          # check ownership BEFORE touching state
        raise RuntimeError("cannot release un-acquired lock")
    if depth > 1:
        depth -= 1
        rlock.release()
        return
    try:
        try:
            unlock(self._fd)           # seek+LK_UNLCK / flock(LOCK_UN)
        finally:
            os.close(self._fd)         # closed even if the unlock raises
    finally:
        self._fd = self._path = None
        depth = 0
        rlock.release()
```

`depth` is only ever read or written while the thread lock is held, so it needs
no guard of its own and no second thread can observe an intermediate state.
The invariants, each one a round-2 finding:

- **Ownership is checked before state.** `release()` tests `rlock._is_owned()`
  first. A non-owner thread raises `RuntimeError` without decrementing `depth`
  or closing the fd; the naive version corrupted the recursion count and
  dropped cross-process exclusion before `rlock.release()` got to complain.
- **Nothing between `acquire` succeeding and `return` is outside the `try`.**
  The reentrant `depth += 1` is inside it, so a `KeyboardInterrupt` landing
  there cannot strand a thread-lock acquisition.
- **`blocking=False` never carries a timeout, and never retries.**
  `RLock.acquire(False, t)` raises `ValueError`, so the two cases are separate
  calls and the invalid combination raises rather than silently succeeding.
  Crucially it also passes a `NO_WAIT` sentinel rather than `None` to
  `open_and_lock`: `None` means "no deadline", which would have made a
  non-blocking acquire **retry the file lock forever** — the exact opposite of
  what it asks for. One attempt, then `False`.
- **A file lock that is acquired but never installed is unlocked and closed.**
  If a `BaseException` lands between `open_and_lock` returning and `self._fd =
  fd`, the local `fd` still owns a held OS lock. Releasing only the thread lock
  would leak it permanently — the campaign would stay locked for the life of
  the process with no object referencing it. The `fd = None` after installation
  is what makes the handler able to tell the two cases apart.
- **Remaining time is clamped to zero.** A slightly negative computed remainder
  is not "expire immediately" to `RLock` — every negative other than `-1` is
  invalid.
- **The fd is closed even if unlocking raises**, via the nested `try/finally`.
  A failed unlock that leaked an open descriptor would keep the OS lock held
  while the wrapper believed it released — the worst available outcome.
- Depth stays 0 until the OS lock is held, and the fd is installed before depth
  becomes 1, so a failure at any line leaves the object usable.

**The one class of residual, stated once and not pretended away.** CPython can
deliver an asynchronous exception (`KeyboardInterrupt`, or anything a signal
handler raises) between any two bytecodes. There is therefore an unclosable
window in each of these:

- between `acquire()` returning `True` and the `with` body starting, so
  `__exit__` never runs;
- between `depth += 1` on the reentrant path and the `return`, leaving depth
  overstated by one;
- inside `ExitStack.enter_context`, between the acquisition succeeding and its
  release being registered.

None of these is introduced here. The first two are exactly `threading.RLock`'s
own exposure — a plain `with rlock:` has both — and the third is `ExitStack`'s,
already present at `routes.py:1036` and `module_edit._campaign_locks()` today.
Python offers no way to make acquire-and-register atomic against asynchronous
exceptions; the standard library's own lock helpers do not try. The blast
radius is bounded by process lifetime: every leaked file lock is released by
the kernel when the process exits, which is the same property that makes stale
locks a non-problem.

What *is* fixed above is every window that is not asynchronous-exception-only —
the ones reachable by an ordinary raised exception, a timeout, or a permission
error.

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

`<lockdir>` is derived from the user's home directory, read from the least
environment-dependent source each platform offers:

- **POSIX, including Android/Chaquopy**: `pwd.getpwuid(os.getuid()).pw_dir /
  ".local/state/grimoire/locks"`, falling back to `Path.home()` if the passwd
  lookup fails. `pwd` reads the account database, **not** `$HOME`, so a cron
  job, a systemd unit and a desktop session all agree — which `Path.home()`
  alone would not guarantee, since it prefers `$HOME` when set.
- **Windows**: `Path.home() / "AppData/Local/grimoire/locks"`. There is no
  env-independent equivalent short of a `ctypes` call to
  `SHGetKnownFolderPath`, which is not worth a compiled-API dependency for a
  case that requires someone to deliberately run two grimoire processes under
  differing `USERPROFILE` values.

Created with `mkdir(parents=True, exist_ok=True)`, mode `0o700` on POSIX.

Two earlier attempts were wrong, and why matters more than the answer:

- The **first draft** used `tempfile.gettempdir()`. A `/tmp` cleaner can unlink
  a held lock file, per-user temp directories vary, and a shared `/tmp` needs
  an ownership and permissions story it did not have.
- The **second draft** used `$XDG_RUNTIME_DIR` when set and `~/.cache`
  otherwise. That breaks the *primary* guarantee: `XDG_RUNTIME_DIR` is set in a
  desktop session and unset under cron, systemd, or a bare `ssh` command, so
  two processes of the same user on the same machine would pick **different
  lock files for the same store** and neither would exclude the other. Any
  environment-conditional path has this defect. And `~/.cache` is a cleanable
  directory, contradicting the very reason the first draft was rejected —
  `~/.local/state` is the XDG location for state that must persist and is not
  swept.

Depending on the home directory at all is acceptable because grimoire already
depends on it unconditionally: `paths.DEFAULT_HOME` is `Path.home() /
".grimoire"` and the bootstrap pointer is `Path.home() / ".grimoire.json"`. If
the home directory cannot be resolved, the store cannot be located either and
the lock directory is moot. `%LOCALAPPDATA%` is deliberately *not* read, for
the environment-conditional reason above; `Path.home() / "AppData" / "Local"`
is where it points on any normal Windows profile.

**The residual, stated plainly because it is the guarantee's weak point.** Two
processes that resolve *different* home directories while pointing at the *same*
explicit `GRIMOIRE_HOME` will choose different lock files and will not exclude
each other. `pwd` removes the common POSIX cause (an unset or rewritten `$HOME`
under cron/systemd); a deliberately altered `USERPROFILE` on Windows, or two
distinct OS accounts, still reach it. And if the home directory is itself
network-mounted and shared between machines, `flock` semantics over that mount
decide the outcome — which degrades to today's behaviour (no exclusion) rather
than to corruption. This is the price of a machine-local lock; the alternative,
a lock file inside the store, is the one location both processes provably agree
on but pays sync replication, conflict copies, and cloud-placeholder stalls for
it.

Android is the weakest assumption: under Chaquopy `Path.home()` resolves to the
app's private files directory. If that is ever wrong the lock file still lands
somewhere app-private and writable, and the consequence is nil in practice —
the APK runs a single backend process, so there is no second process on the
device for it to exclude.

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
prefix still get distinct files. Truncation to 64 bits does not make collisions
*impossible* — the round-2 review is right that "not reachable by construction"
was false — only negligible, and the consequence is bounded: two colliding cids
would share one lock file and therefore serialize against each other, producing
a spurious 409 rather than a lost lock or a corrupted record.

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
span is bounded by one `LOCK_TIMEOUT`. `hold_all` is also where the
sorted-order rule below is enforced, in one place rather than two.

Partial acquisition must unwind completely, so `hold_all` is built on
`contextlib.ExitStack` rather than a hand-rolled reversed loop:

```
@contextmanager
def hold_all(cids):
    deadline = monotonic() + LOCK_TIMEOUT
    with ExitStack() as stack:
        for cid in sorted(set(cids)):          # sorted: the ordering rule
            lock = campaign_lock(cid)
            if not lock.acquire(timeout=_remaining(deadline)):
                raise CampaignBusy(cid)        # ExitStack unwinds the rest
            stack.push(lock)                   # registered IMMEDIATELY
        yield
```

`stack.push(lock)` registers each lock the instant it is acquired, so an
exception at any later point — `CampaignBusy`, a `BaseException`, anything
raised by the `yield` body — unwinds every lock already held. `ExitStack`
runs **all** registered exits even when one of them raises, chaining the
exceptions, which a `for lock in reversed(held): lock.release()` loop does not:
there, one raising `release()` strands every remaining lock. When the budget is
exhausted `_remaining` returns `0.0`, so the next `acquire` fails immediately
rather than waiting again.

The deadline bounds **lock contention**. It does not bound `realpath`, `mkdir`,
`open` or `stat` on an unavailable network path — those are OS-level blocking
calls that no userspace deadline can interrupt, and today's code has exactly
the same exposure. The earlier claim that "the whole acquisition" is bounded
overstated this; contention is bounded, syscalls are not.

#### Where contention must not be swallowed

A single `@app.exception_handler(locks.CampaignBusy)` in `main.create_app()`
returns HTTP 409 with a message naming the cause — one handler rather than ~35
route-level `try`/`except` blocks, matching how the app already handles
`HTTPException` centrally. But three paths defeat it, and each needs a
deliberate decision:

1. **`audit.capture_baseline`** wraps its whole body in `except Exception:
   return` — "never raises, a capture failure must not fail scene creation".
   That would turn contention into a scene with no baseline, silently losing
   that scene's audit delta.

   The second draft proposed simply re-raising `CampaignBusy` there. The
   round-2 review was right to reject it: `capture_baseline` has exactly one
   caller, `scenes.create_scene` (`scenes.py:163`), and it runs **after**
   `atomic.write_text` has already durably written the scene file. Re-raising
   would return 409 while leaving an orphaned scene on disk — a worse outcome
   than the one being fixed.

   So the lock moves **outward**: `create_scene` takes
   `locks.campaign_lock(cid)` around its **entire body**, from the first line.
   Not merely around the write and the capture — the round-3 review is right
   that everything above them is already load-bearing. `_numbering()` reads the
   existing scenes to pick the next number, `repad()` **renames every scene in
   the campaign** when the width grows, and `uniquify()` resolves the SID
   against what exists on disk. Two concurrent creators that lock only the tail
   would select the same SID and one would overwrite the other, and contention
   would strike after `repad` had already renamed files. Contention now fails
   before any durable side effect, and a scene that exists is guaranteed to
   have a baseline. `capture_baseline` re-raises
   `CampaignBusy` ahead of its broad catch, which is now safe because its only
   caller holds the lock already and the acquisition is reentrant — the
   re-raise is a guard against a future caller that does not, not a live path.

   This is the one place the spec widens the lock domain, and it is forced:
   there is no way to make contention fail loudly here without moving the lock
   above the first durable write.

   It adds no edge to the lock hierarchy. `create_scene` has exactly one
   production caller — `POST /campaigns/{cid}/scenes` (`routes.py:2611`), which
   holds no lock — plus three in `scripts/verify_templates.py`. It is a leaf
   acquisition, so no ordering constraint changes. Its error contract does
   change: the route can now answer 409, which it could not before.
2. **The proposal adjudication route** catches every exception from
   `checks.resolve_check` (which takes the campaign lock internally) and turns
   it into a `check_error` SSE frame. `CampaignBusy` is caught first, the
   proposal is reverted `resolving → pending` exactly as the broad path does,
   and then it propagates as a 409 — contention is not a check error and must
   not be reported as one.

   The revert can itself contend, since `proposals.transition` takes the same
   lock. It is therefore best effort: if the revert also raises `CampaignBusy`
   the record stays `resolving` and the 409 still propagates. That state is
   already recoverable and needs no new machinery — `resolving` is in
   `proposals.NON_TERMINAL`, so the next send's `supersede()` retires it, and
   until then the route answers 409 "adjudication in progress", which is
   accurate.
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

So on `CampaignBusy` the wrapper emits `{"error": {"detail": …, "kind":
"busy"}}` — the shape an `LLMError` already produces — and persists neither the
generated narration nor a new proposal record. Stated that precisely, because
"persists nothing" was too broad: `proposals.claim` and
`_heal_current_proposal` can have persisted state earlier in the turn, and the
guarantee being made is only about the two writes the failing finalizer would
otherwise have made. The turn is lost and the user re-sends. That is a real
cost, stated rather than hidden: a lost turn is recoverable, a transcript whose
mechanical decision point has no proposal is not.

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

`_M` itself becomes cross-process, using the same primitive with a lock file in
the same per-store directory, named through the same `lock_path` sanitizer as
the campaign locks (so `module-edit-<hash>.lock`, not a hand-written
`module-edit.lock` — one naming rule, no second code path). Leaving it process-local would leave
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
- `acquire(blocking=False)` returns a bool rather than raising `ValueError` —
  the case the second draft's pseudocode got wrong.
- After a timed-out `with`, the thread lock is free: another thread in the same
  process acquires it once the child exits.
- `release()` from a thread that does not hold the lock raises `RuntimeError`
  and leaves `depth`, the fd and the cross-process lock untouched — a
  subsequent legitimate release by the owner still works.
- Repeated timeouts and repeated inode-mismatch retries leak no file
  descriptors (compare fd count before and after).
- A permanent lock error (injected `OSError(ENOLCK)`) propagates rather than
  becoming `CampaignBusy` / 409.
- An exception raised inside a held `with` block still releases both the file
  lock and the thread lock.
- An `unlock` that raises still closes the fd and releases the thread lock
  (injected failure).
- `hold_all` over N campaigns is bounded by one `LOCK_TIMEOUT`, not N of them.
- `hold_all` unwinds every already-acquired lock when a later one is busy —
  including when one `release()` raises mid-unwind, which must not strand the
  remaining locks.
- Clearing `XDG_RUNTIME_DIR`, `LOCALAPPDATA` — and, on POSIX, `HOME` — from the
  environment does not change which file is locked. This is the regression
  guard for the finding that an environment-conditional path silently breaks
  same-user exclusion, and the reason POSIX reads `pwd` rather than `$HOME`.
- `acquire(blocking=False)` against a lock another process holds returns
  `False` promptly instead of retrying to the deadline.
- `acquire(blocking=False, timeout=5)` raises `ValueError`, as `RLock` does.

**Placement and integration**

- No new file appears under `GRIMOIRE_HOME` — the guard that lock files stay
  out of the synced library.
- `CampaignBusy` reaches the client as HTTP 409, through `TestClient`.
- `create_scene` holds the campaign lock across its whole body, so contention
  leaves **no** scene file behind — the guard on the orphaned-scene finding.
- Two threads creating scenes concurrently get distinct SIDs, and a `repad`
  triggered by one is not interleaved with the other's numbering — the guard on
  the wider boundary.
- `capture_baseline` propagates `CampaignBusy` instead of swallowing it.
- Adjudication contention returns 409 rather than a `check_error` SSE frame,
  and a revert that is itself busy leaves the record `resolving` (recoverable
  by the next send's `supersede()`) rather than raising a second error.
- `_lifespan` startup survives a `CampaignBusy` from `recover()`.
- The SSE `finalize` wrapper emits a `busy` error frame and persists **no**
  narration.
- `hold_all` acquires in sorted cid order, and both multi-lock holders use it.

The existing suite is the regression guard for the drop-in claim:
`test_locks_store.py`'s identity, reentrancy, per-campaign and
domain-serialization tests, and the two `_is_owned()` tests in
`test_sheets_store.py`, all pass unchanged.

## What changed during implementation

This section is appended rather than folded in, so the design record stays
honest about what was decided up front and what a review or the code forced
later. Where this section and the text above disagree, this section is right.

**Two premises above are false, and were only found by writing the code:**

- **There is no lock-order inversion.** The Problem statement and the
  ordering section both assume `module_edit._campaign_locks()` acquires in
  `list_campaigns()` order against the rebind route's sorted order.
  `list_campaigns()` walks `sorted(base.iterdir())` and reports the directory
  name as the id — the same key the route sorts by. The two holders already
  agree. `hold_all()` still lands, because that agreement is incidental and
  undocumented and one refactor of `list_campaigns` away from becoming a real
  cross-process deadlock, but it is a guardrail, not a bug fix.
- **`scenes.append_message` is not unlocked**, and `create_scene` did not need
  the widening this spec describes. #254 landed on main first and put every
  scene mutator under the campaign lock — including `create_scene`'s whole
  body, and *better* than planned here: it hoists the user-authored calendar
  plugin call **out** of the lock, which this spec would have wrongly pulled
  in, holding a campaign-wide lock across arbitrary plugin code. Only the
  `capture_baseline` re-raise remained to do.

**Changes the review gates forced, which the sections above predate:**

- **Lock files are domain-namespaced.** The formula above
  (`<cid-slug>-<sha256(cid)>.lock`) lets a campaign whose id is literally
  `module-edit` hash onto the module-edit lock's own file. Publication takes
  the module lock and *then* every campaign lock, so that campaign made
  publication block on itself to the timeout and could make journal recovery
  skip forever. `lock_path` now takes a `domain` and folds it into the digest,
  not just the readable prefix.
- **Startup guards all three steps**, not only `recover()`.
  `migrate_scene_ids` reaches a campaign lock through `scenes.repad`, so
  contention could abort boot *after* a partially applied migration. Each step
  is guarded separately and logs at WARNING.
- **SSE `on_error` contention is suppressed too.** It persists the partial
  reply, which now takes a lock and can raise — from an already-started
  response, where the 409 handler cannot reach it, truncating the stream with
  no error frame at all.
- **`finalize` is materialized with `list(...)`.** Guarding only the call would
  let a generator finalizer's `StoreBusy` escape the `for` outside the handler.
- **The busy SSE path does not "persist neither narration nor a new
  proposal".** That claim was too strong: `finalize` is not transactional —
  `_chat_stream` writes the proposal under one lock, releases it, then
  `_persist_reply` takes it again. Contention on that second acquisition leaves
  a proposal with no narration, which is the *sanctioned* recoverable direction
  (the same state the documented fence crash-window produces). What is
  guaranteed is that the busy path adds no narration without its proposal.
- **The acquire handler keys on `depth`.** "A failure at any line leaves the
  object usable" was false for an asynchronous exception landing after
  `depth = 1`: the handler released the thread lock while leaving depth and the
  fd set, so the lock claimed to be held with no owner and the next acquire
  would skip the file lock entirely. It now unwinds an established hold through
  `release()`.
- **The `hold_all` deadline test was replaced.** The original could not
  discriminate: `hold_all` raises on the first failure, so per-lock and shared
  deadlines take identical wall-clock however many locks are contended. The
  test now uses a slow-but-successful first acquisition followed by a contended
  one.

## Follow-ups

- Optimistic concurrency (CAS on a content digest) to *detect* the cross-device
  clobber that no lock can prevent.
- Lock campaign creation and deletion, closing the enumerate-then-lock window
  in `_campaign_locks()`.
- `campaigns.rename_campaign` / `set_campaign_response` / `touch` take no lock.
- `assets._image_locks` is still process-local.
- `routes.put_data_dir` switches the store root without excluding held locks.
