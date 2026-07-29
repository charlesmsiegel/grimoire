# Cross-process campaign locks (#234)

Every campaign-scoped mutation in grimoire serializes on `locks.campaign_lock(cid)`,
a `threading.RLock`. A `threading.RLock` protects nothing across process
boundaries. Meanwhile the README tells users to point the data directory at a
synced folder to share one library across devices, and the same store is
reachable from the desktop app and the Android APK.

Two backends — two devices, or two processes on one machine — editing one
campaign therefore have no mutual exclusion at all. Because most records are
rewritten whole, the loser's edit is silently overwritten: no detection, no
conflict marker, no user-visible symptom.

## What this actually guarantees

Stated precisely, because the issue's framing conflates two different problems:

**Concurrent access to one store from multiple processes on one machine is
serialized.** That is the desktop app running alongside `run.ps1`, two backends
started by accident, or a CLI script racing the server. This is a real
guarantee, enforced by the operating system.

It is **not** mutual exclusion across devices. OS advisory file locks are
local-kernel objects: `flock` and `LockFileEx` do not traverse Dropbox,
OneDrive, iCloud or Syncthing, and they are unreliable over SMB. A lock held on
device A is invisible to device B. No lock-based design can fix the
multi-device case — the sync client owns that domain and resolves simultaneous
edits with conflict copies on its own schedule.

So this spec does two things: it makes the guarantee real where a guarantee is
achievable, and it stops the documentation from claiming the part that is not.

Detecting the cross-device clobber after the fact — optimistic concurrency,
compare-and-swap on a content hash — is a separate, larger change. See
Non-goals.

## Goals

- The campaign lock domain excludes concurrent processes, not just threads.
- The module-edit domain likewise: whole-directory pack publication mutates the
  shared user library and is guarded today by a process-local `_M`.
- One mechanism, at the existing choke point, with the public surface of
  `campaign_lock()` unchanged so its ~35 call sites do not move.
- Contention fails loudly and quickly rather than hanging.
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
- **`assets._image_locks` and `rolls._LOCKS`.** Two other process-local
  registries. `rolls` is #255; the image-lock registry gets a follow-up. Both
  are narrower domains than the campaign lock and neither is in this diff.
- **Stale-lock reaping.** Not needed: both `flock` and `LockFileEx` are
  released by the kernel when the holding process dies, however it dies. There
  is no lease, no heartbeat, and no orphaned-lock recovery path to write.
- **Multi-file transactions.** Unchanged from #233; the lock makes a
  read-modify-write span exclusive, it does not make it atomic.

## Design

### The hybrid lock

`campaign_lock(cid)` keeps its registry — same `setdefault` under
`_registry_guard`, same identity stability (`campaign_lock(c) is
campaign_lock(c)`), same key (the cid alone). What changes is the object it
hands back: a `_CampaignLock` composing the process-local `threading.RLock`
with an OS advisory file lock.

The file lock is taken on the **outermost** acquisition only and released on
the outermost release. Reentrancy is therefore free: `audit.apply_delta`
calling `sheets.set_field` under an already-held lock touches the filesystem
once, not twice. Depth tracking is safe because the thread lock is always held
before the depth counter is read.

Acquisition order inside the wrapper is thread lock, then file lock. On file
lock timeout the wrapper **releases the thread lock before raising**, so a busy
campaign never strands the in-process lock and wedges the rest of the process.

The public surface is preserved exactly — the context-manager protocol,
`acquire(timeout=…)`, `release()`, and `_is_owned()` — because call sites use
all four. `_is_owned()` in particular is what two `test_sheets_store.py` tests
use to assert that `modules.resolve` happens inside the lock. Every existing
call site and every existing lock test is expected to pass unchanged; that is
the check that the wrapper is a genuine drop-in.

### The lock file

Machine-local, outside the store:

```
<tempdir>/grimoire-locks/<sha256(realpath(home()).casefold())[:16]>/<cid-slug>-<sha256(cid)[:8]>.lock
```

Keyed by a hash of the normalized real path of the store root, so every process
pointing at the same store shares a lock and two different stores do not
collide. Resolved at acquire time rather than cached, because `home()` resolves
live on every call and the **Storage location** setting can change at runtime.

Machine-local rather than inside the store, because the store may be a synced
folder. A lock file in there would be replicated, would accumulate conflict
copies, and — on Windows with OneDrive Files-On-Demand — holding an open handle
on it can stall. Since a file lock cannot cross devices anyway, putting it in
the synced tree buys nothing and costs litter in the user's library. The
trade-off accepted: two processes reaching one store by genuinely different
paths (an SMB mount from two machines) get different lock files, which is the
case OS locks fail at regardless.

The filename sanitizes the cid *and* appends a hash of it. Campaign ids are
slugs today, but they arrive from route parameters, and a lock path is not the
place to trust that (cf. #240). Sanitizing alone would collide distinct ids;
the hash suffix keeps them distinct while the readable prefix keeps the
directory debuggable.

Platform implementations, stdlib only — no new dependency, and the base
dependency list must stay Android-installable:

- **POSIX**: `fcntl.flock(fd, LOCK_EX | LOCK_NB)` in a bounded retry loop.
  After acquiring, compare `os.fstat(fd).st_ino` with `os.stat(path).st_ino`
  and retry if they differ: a `/tmp` cleaner can unlink the lock file while it
  is held, after which two processes would hold locks on different inodes and
  both proceed. This is the standard lockfile-in-a-cleanable-directory check.
- **Windows**: `msvcrt.locking(fd, LK_NBLCK, 1)` in the same retry loop. No
  inode check — an open file cannot be deleted on Windows, so the race the
  POSIX check closes cannot occur.

`fcntl` is present under Chaquopy, so the Android build takes the POSIX path.

### Contention

A new `locks.CampaignBusy` exception. The retry loop waits up to a
module-level constant (5 seconds) before raising it.

Bounded rather than indefinite, because a cross-process lock-order inversion is
a real possibility (below) and an unbounded wait would turn it into a hung
request with no diagnostic. Five seconds is comfortably longer than any
legitimate hold: no campaign lock is held across a network LLM call —
`_fence_stream` calls `finalize(watcher)` only after the stream completes — so
every span under the lock is short local file I/O.

`main.create_app()` registers one `@app.exception_handler(locks.CampaignBusy)`
returning HTTP 409 with a plain message naming the cause. One handler rather
than ~35 route-level `try`/`except` blocks, matching how the app already
handles `HTTPException` centrally.

The SSE routes need explicit handling and do not get it for free.
`_fence_stream` calls `finalize(watcher)` **outside** its `try`, so a
`CampaignBusy` raised there would abort the stream with no frame emitted and —
in `_chat_stream` — drop the narration that had not been persisted yet. The
fix: wrap the `finalize` call so `CampaignBusy` routes through the existing
`on_error` path (which persists the partial narration) and emits an
`{"error": …}` SSE frame, the same shape an `LLMError` already produces.

### The deadlock this would otherwise create

`module_edit._campaign_locks()` acquires every campaign's lock in
`campaigns.list_campaigns()` order. `routes.py:1036` (the world-module rebind)
acquires its set in **sorted** order. These are the only two multi-lock
holders. In-process the inversion is masked, because the global `_M`
module-edit lock serializes module publication against everything that could
race it. Across processes `_M` masks nothing, and two publications acquiring
the same campaigns in opposite orders deadlock.

Two changes close it:

1. `module_edit._campaign_locks()` acquires in sorted order. Its docstring
   currently says order is irrelevant; that stops being true here. A test
   guards the ordering, and `locks.py` states the rule: **every multi-campaign
   holder acquires in sorted cid order.**
2. `module_edit._M` becomes cross-process too, using the same primitive with a
   lock file at `<lockdir>/module-edit.lock`. Leaving it process-local would
   leave whole-directory pack publication — which mutates the shared user
   library, not just one campaign — unguarded against the exact scenario this
   spec exists for.

Both reuse a single `_ProcessLock` primitive so there is one implementation of
the platform branching, not two.

The 5-second timeout is the backstop: an ordering bug that survives this
analysis surfaces as a 409 naming the campaign, not as a wedged server.

### Documentation

The overclaim is the other half of the bug, so the doc changes are part of the
fix, not a footnote:

- **README** "Where your data lives": keep the synced-folder workflow, add that
  grimoire must not be *actively used on two devices at once* — sync clients
  resolve simultaneous edits with conflict copies and grimoire cannot merge
  them — and state that concurrent access on one machine is serialized.
- **`docs/android-architecture.md`** §synced-folder mode: the same caveat, since
  that section describes precisely the two-device workflow.
- **`store/locks.py`** module docstring: replace "Locks are process-local; the
  store is single-process by design" with the new guarantee, its limit, and the
  sorted-order rule.
- **`store/module_edit.py`** threat model: "exactly two actors — the User (UI)
  and the LLM (play flows)" gains the third actor (a second process) and says
  what covers it.

## Testing

New tests in `backend/tests/test_locks_store.py`, using a real `subprocess`
child that inherits `GRIMOIRE_HOME` and the parent's `sys.path`. An in-process
test cannot demonstrate cross-process exclusion, so these spawn for real:

- A child holding the campaign lock makes the parent's acquire raise
  `CampaignBusy` within the timeout; the parent succeeds once the child exits.
- Killing the holder releases the lock — the guard on the no-reaping decision.
- Reentrant acquisition takes the file lock exactly once: a child stays blocked
  while the parent is at depth 2 and is released only after the outermost
  release.
- Two different campaigns do not block each other across processes.
- No new file appears under `GRIMOIRE_HOME` — the guard that lock files stay
  out of the synced library.
- `CampaignBusy` reaches the client as HTTP 409, through `TestClient`.
- `module_edit._campaign_locks()` acquires in sorted cid order.

The existing suite is the regression guard for the drop-in claim:
`test_locks_store.py`'s identity, reentrancy, per-campaign and
domain-serialization tests, and the two `_is_owned()` tests in
`test_sheets_store.py`, all pass unchanged.

## Follow-ups

- Optimistic concurrency (CAS on a content digest) to *detect* the cross-device
  clobber that no lock can prevent.
- `assets._image_locks` is still process-local.
