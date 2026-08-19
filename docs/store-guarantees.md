# What the store promises

Grimoire's library is a tree of Markdown and JSON files under `store.home()`,
written by a server that handles requests concurrently and may not be the only
process holding that directory. Over #233, #234, #255 and #267 the store grew
real crash-safety and real mutual exclusion — but the rules ended up spread
across five modules and three AST-parsing tests, so the only way to find out
what a caller may rely on was to read all eight. This page is that answer in
one place.

It is a description of what the code does today, not a wish list. Where a
promise stops, the stopping point is stated: [What is **not**
promised](#what-is-not-promised) is the section to read before assuming
anything beyond it.

Enforcement lives in the tests, not here. Each rule below names the guard that
fails when code breaks it, because a paragraph cannot fail a test run — that
lesson is why `store/locks.py` turned its domain from prose into constants
(#255).

---

## Contents

- [Atomic writes](#atomic-writes)
- [The campaign lock](#the-campaign-lock)
- [A second process on the same store](#a-second-process-on-the-same-store)
- [What is **not** promised](#what-is-not-promised)

---

## Atomic writes

**Module:** `backend/src/grimoire/store/atomic.py` (#233) ·
**Guard:** `backend/tests/test_atomic_guard.py`

Every Markdown/JSON record in the store is published through `store.atomic`.
A plain `Path.write_text` truncates and then writes, so a crash between those
two steps leaves a truncated file — and a scene transcript cannot be
regenerated from anywhere.

### The guarantee

**A reader sees the whole previous version or the whole new version, never a
partial one.** That covers a process crash, an exception mid-write, a full
disk, and a reader racing a writer.

The mechanism is write-to-temp, `fsync`, then `os.replace` onto the target —
`os.replace` being the one operation a POSIX or Windows filesystem publishes as
a unit.

### The four primitives

| Primitive | For | Publishes by |
|---|---|---|
| `atomic.write_text(path, text)` | a whole record | temp + `os.replace` |
| `atomic.write_bytes(path, data)` | a whole binary record | temp + `os.replace` |
| `atomic.streaming_write(path)` | a payload too big to hold in memory (a store backup) | temp + `os.replace`, built *into* the temp |
| `atomic.append_line(path, line)` | an append-only ledger (`store.usage`) | one `write` on an `O_APPEND` descriptor |

`append_line` is the odd one out on purpose. Temp-and-replace is the wrong
shape for a ledger: it rewrites the whole file to add a row, and two writers
racing lose one row outright rather than interleaving them. Instead the row is
published by a single `write` on a descriptor opened `O_APPEND`, so the kernel
resolves the offset and the write as one step and concurrent appenders land
whole lines in some order. (`O_APPEND` is honoured on Windows too — CPython
maps it to `FILE_APPEND_DATA`.)

`streaming_write` yields the temp's **file object**, never its pathname. That
is deliberate: handing out the path opens an interval in which another process
can unlink the temp and substitute a symlink for the write, the `chmod` and the
rename to follow. A caller that needs a real path is out of scope.

### What it does not guarantee

- **Durability across power loss.** CPython's `os.replace` uses `MoveFileExW`
  without `MOVEFILE_WRITE_THROUGH`, and grimoire does not fsync the parent
  directory on POSIX, so the rename itself may not survive a power cut. The
  fsync that *is* performed buys the narrower thing: if the rename lands, the
  bytes behind it are complete rather than a page-cache ghost. **You may find
  the old version; you will not find a shredded one.**
- **Durability for `append_line`.** No fsync at all — a ledger row is not worth
  one on the generating path. A lost row costs a statistic.
- **Atomicity for a very long appended line.** If the kernel returns a short
  write, the remainder goes out in a loop and the line can tear. Readers of
  those files skip a line they cannot parse for exactly that reason; rows are a
  few hundred bytes, orders of magnitude under any platform's atomic-write
  floor.
- **Linked records.** Unlike `write_text`, which follows a leaf symlink and
  writes through to its target, `os.replace` replaces the directory entry.
  Nothing in grimoire creates linked records; one you create yourself becomes a
  regular file on its next save.
- **Full metadata preservation.** The surviving file is the temp, not the
  original inode. Permission bits, owning group and extended attributes are
  copied across best-effort; POSIX ACLs proper, a Windows target's explicit
  non-inherited DACL, alternate data streams and per-file
  compression/encryption flags are not. Grimoire records are plain files in a
  user-owned directory that inherit their parent's ACL.

### How it is enforced

`test_atomic_guard.py` walks the package's own ASTs and fails on a bare
`Path.write_text` / `write_bytes` / `open(...,"w")` in `store/`. A genuinely
safe one is cleared with `# atomic-ok: <reason>` — **a marker with no reason
fails, deliberately**, and the guard caps how many exist.

---

## The campaign lock

**Module:** `backend/src/grimoire/store/locks.py` ·
**Guards:** `test_lock_domain_guard.py`, `test_lock_order_guard.py`,
`test_locks_store.py`

A campaign is the unit of mutual exclusion. Everything that
reads-validates-writes campaign-scoped state — scene transcripts, sheets, audit
baselines, roll proposals, the roll log, and the module-pack swap that can
invalidate most of them — serializes on the *same* `locks.campaign_lock(cid)`.

The unification is deliberate. A module edit holding a campaign's lock must
exclude a proposal derived from the pack it is about to replace, so the two
cannot live in separate lock domains.

`campaign_lock` is a re-entrant thread lock **and** an OS advisory file lock
(`store/proclock.py`, #234), so it excludes concurrent processes and not merely
threads.

### What it covers

The domain is declared, not described. `store/locks.py` carries three
constants, and `test_lock_domain_guard.py` holds them and the code to each
other:

| Constant | Meaning |
|---|---|
| `DOMAIN_MODULES` | every public `cid`-taking mutator here takes the lock |
| `OUTSIDE_DOMAIN` | deliberately not, each with the reason, as a decision someone can defend |
| `UNREVIEWED` | a **frozen backlog** of campaign-scoped mutators nobody has assessed — the guard forbids it from growing, so it can only shrink |

Adding a module that mutates campaign-scoped state means classifying it in one
of the three, or the guard fails naming your module. A single function inside a
member module can be exempted with `# lock-domain-ok: <reason>`.

Scene transcripts are the artifact this protects. They cannot be regenerated,
which is why `store/scenes` serializes its whole mutator surface through
`@_serialized` rather than lock-by-lock.

### What it does not cover

- **Not every campaign mutation.** `OUTSIDE_DOMAIN` and `UNREVIEWED` are real
  and are listed in the module. Campaign rename/delete, the campaign manifest
  writer, dice-roll history's home-scoped ledger and image assets are among the
  writers that do not take it.
- **Not across devices.** The lock file is machine-local, so a lock held on one
  device is invisible to another sharing the store through a synced folder. No
  filesystem lock on a sync-replicated store can do better.
- **Not across OS users**, whose lock directories differ.
- **Not a transaction.** The lock serializes writers; it does not roll anything
  back. A crash mid-sequence leaves each individual record whole (see [Atomic
  writes](#atomic-writes)) but the sequence half-applied.

### Ordering, and why `hold_all` is the only way to hold two

```
module-edit lock  (module_edit._M)
  └─ campaign locks, always in sorted cid order  (locks.hold_all)
       ├─ audit baseline lock  (store/audit/baselines.py)
       └─ rolls lock           (store/rolls.py)
```

**Every multi-campaign holder goes through `locks.hold_all(cids)`, which
sorts.** Nothing else may hold more than one campaign lock at a time.

This is not a formality. The two multi-campaign holders that exist — module
publication and the world-module rebind route — did *not* agree before
`hold_all`, though the code claimed they did: the route sorted by cid while
module publication took `list_campaigns()` order, which is recency. Whenever
those disagree, two concurrent requests wedge permanently. That is #267, and it
happened.

`hold_all` also applies **one** deadline across all N locks rather than
`LOCK_TIMEOUT` each, which would give an N × `LOCK_TIMEOUT` convoy.

`test_lock_order_guard.py` walks the package's ASTs and fails any function
outside `hold_all` whose *shape* can hold two campaign locks at once — an
acquisition registered on an `ExitStack`, one carried around a loop, two open
at the same time for different campaigns. What it cannot see is a lock reached
through an alias or a wrapper object, or two locks taken on either side of a
function call. Exemption marker: `# lock-order-ok: <reason>`.

No LLM play flow ever holds more than its own campaign's lock.

### The variants, and when each is right

| Call | Behaviour on contention |
|---|---|
| `campaign_lock(cid)` | waits up to `LOCK_TIMEOUT` (30 s), then raises `CampaignBusy` |
| `campaign_lock_nowait(cid)` | never waits; **yields whether it got the lock** — the caller must honour the boolean |
| `best_effort_campaign_lock(cid, timeout=2.0)` | proceeds *without* the lock after the timeout; yields whether it was held |

`campaign_lock_nowait` is for work that belongs in the domain but must not
delay the caller — `store.prompt_log.record` runs before a streaming response
returns, so a 30-second wait would stall a turn and then discard the debug
snapshot anyway.

`best_effort_campaign_lock` is for **read** paths whose only stake is seeing
two files in a consistent state. Under contention it returns an unlocked read,
which can observe one writer's two files a moment apart. That costs one prompt
a stale field; refusing would cost the turn.

### The other locks

| Lock | Scope | Notes |
|---|---|---|
| `locks.config_lock()` | global `config.md` | a leaf — nothing under it takes another lock |
| `locks.backup_lock()` | one archive of one store at a time (#32) | a leaf; deliberately does **not** take the campaign locks |
| `locks.module_edit_lock()` | whole-directory module-pack publication | outermost in the ordering above |

The backup lock's exclusion is the interesting one: an archive of a hundred
campaigns cannot hold a hundred locks for the length of a zip without stalling
play, so **a backup taken mid-write can catch one campaign's two files a moment
apart.** The archive is a coarse restore point, not a transactional snapshot.

### What a caller sees when a lock is contended

Entering any of the four locks — campaign, config, backup, module-edit — raises
that lock's subclass of `locks.StoreBusy` after `LOCK_TIMEOUT` (30 seconds):
`CampaignBusy`, `ConfigBusy`, `BackupBusy`, `ModuleEditBusy`. One handler in
`main.create_app` turns any of them into **HTTP 409** with a message naming
what is busy, so the failure is retryable rather than a wedged server. (The two
non-blocking variants above never raise — they report with a boolean instead,
which is the whole point of them.)

`LOCK_TIMEOUT` is longer than any legitimate hold but is not a proof: a module
migration over a large library on a synced or removable filesystem can exceed
it, and its waiter then gets that retryable 409.

---

## A second process on the same store

**Modules:** `store/proclock.py` (#234), `store/statcache.py`,
`store/external.py` (#35)

You can run two grimoire processes against one `GRIMOIRE_HOME` on one machine,
signed in as one user. This section is what happens when you do.

### Cross-process exclusion

`store/locks.py` owns the *domain* (which campaign, which hierarchy);
`store/proclock.py` owns the *mechanism* and knows nothing about campaigns. It
takes an OS advisory file lock — `fcntl` on POSIX, `msvcrt` on Windows.

**Where the lock files live:** machine-local, per-user, **outside the store**,
and outside any directory subject to automatic cleaning.

| Platform | Directory |
|---|---|
| POSIX | `~/.local/state/grimoire/locks` |
| Windows | `%LOCALAPPDATA%\grimoire\locks` |
| Android | under the app's writable files directory |

Not inside the store, because the store may be a synced folder: a lock file
there would be replicated, would collect conflict copies, and on Windows with
OneDrive Files-On-Demand an open handle on it can stall. A file lock cannot
cross devices anyway, so putting it in the synced tree buys nothing.
`~/.local/state` rather than `~/.cache` because a cleaner unlinking a held lock
file would split two processes onto different inodes with both believing they
hold the lock.

Two details that look incidental and are not:

- **The home directory comes from the account database (`pwd`), not the
  environment.** `$XDG_RUNTIME_DIR` is set in a desktop session and unset under
  cron or systemd, and `$HOME` can be rewritten; either would make two
  processes of the *same user* pick *different* lock files and exclude nothing.
  Android is the deliberate exception, where `$HOME` is the app's files
  directory and passwd reports an unwritable `/`.
- **The store is identified by `(st_dev, st_ino)`, not by its pathname.** On a
  case-insensitive volume (the macOS default) `/Users/A/Store` and
  `/Users/a/store` are one directory with two spellings; hashing the spelling
  would put two processes on different lock files while both believed they held
  the campaign. Inode identity also absorbs bind mounts, a mapped drive versus
  its UNC form, and hard-linked roots.

### Reads notice external writes

Every request re-reads from `paths.home()`, and `store/statcache.py` keys its
memos on `(path, mtime_ns, size)` — so any write, including one arriving from a
sync client, invalidates the entry. Filesystem timestamps tick coarsely (up to
~15 ms on Windows), so a same-size rewrite moments after a cached read could
leave the signature unchanged; `RACY_WINDOW_NS` handles that the way git
handles racy-clean, by never caching a file whose mtime is that recent.

### Conflicted copies

What no read notices is the wreckage a sync client leaves when two *devices*
wrote the same record. Syncthing renames the loser to
`pact.sync-conflict-<date>-<id>.md`, Dropbox to
`pact (Winifred's conflicted copy 2026-01-01).md`, a hand merge leaves
`pact.md.orig`. None of those names is a record id the app will ever resolve,
so the file sits in the store being read by nothing while the user believes
their edit survived.

`store/external.py` finds them. `GET /api/store/conflicts` runs the scan on
demand — its own route rather than a field on `GET /config`, because it costs a
directory walk of the whole library — and the Configuration page's Storage
section asks for it and renders the result in `StoreConflictNotice.tsx`.

**It never opens, moves, renames or deletes one.** Which side of a conflict to
keep is a question only the person who made both edits can answer, so this
mirrors `sync.py`: flag, and let the user choose.

Both the walk and its results are bounded (`MAX_ENTRIES` / `MAX_RESULTS`), and
a truncated answer says so — "found nothing" and "stopped looking" must not
read the same. A scan that could not run is a 500 rather than an empty list,
for the same reason.

**What a clean scan does not prove:** renamed-away copies with no marker in the
name (iCloud's `pact 2.md`, Drive's `pact (1).md`) are indistinguishable from
records a user deliberately named that way, and flagging them would cry wolf on
an ordinary library. Two devices whose edits the sync client merged silently,
or clobbered without leaving a copy, leave nothing on disk to find at all.

### So: is two-at-once supported?

**Two processes on one machine, one user:** yes, for the writers in the lock
domain — scene, sheet, proposal and module-pack writes exclude each other
across processes, so the desktop app and a dev server can share a store without
shredding a transcript. It makes accidents survivable; it is not a blanket
guarantee, because the writers listed in `OUTSIDE_DOMAIN` and `UNREVIEWED` do
not participate.

**Two devices through a synced folder:** no. Sync clients resolve simultaneous
edits by making conflict copies on their own schedule, and grimoire cannot
merge those — one side's edit wins and the other becomes a stray file that
`GET /api/store/conflicts` can, at best, point at afterwards. Let the sync
settle before switching devices.

---

## What is **not** promised

Collected, so that nothing here has to be inferred from an absence.

- **Frontmatter is not validated, by design.** `store/frontmatter.py` is a
  minimal `---`-fenced format with **string scalars only** — no types, no
  schema, no required keys, no YAML. A record whose frontmatter says something
  the code does not expect is not rejected at the store layer. This was ruled
  out of scope when the store was written and still is; a dependency-light
  parser that never fails on a user's hand-edited file is the trade.
- **No durability across power loss.** See [Atomic
  writes](#atomic-writes) — the property is *never truncated*, not *never
  lost*.
- **No cross-record transaction.** Nothing rolls back. A sequence interrupted
  halfway leaves each record whole and the sequence half-applied.
- **No snapshot isolation for backups.** `backup_lock` does not take the
  campaign locks, so an archive can catch one campaign's two files a moment
  apart.
- **Not every campaign-scoped mutator serializes.** `OUTSIDE_DOMAIN` and the
  frozen `UNREVIEWED` backlog in `store/locks.py` are the current, honest list.
- **Nothing across devices**, and nothing across OS users.
- **No background watcher.** The rebuilt app runs no resident machinery;
  conflict detection is on demand, and nothing notices an external write until
  something reads the file.
- **The guards do not prove absence.** Each names its own reach in its
  docstring and stops short of aliases, wrappers and cross-call shapes. They
  catch the idioms that have actually gone wrong; they are not a proof that
  nothing else can.

---

## Where to look next

| Question | File |
|---|---|
| how a record is published | `backend/src/grimoire/store/atomic.py` |
| who takes which lock, and the domain lists | `backend/src/grimoire/store/locks.py` |
| how the cross-process lock is taken | `backend/src/grimoire/store/proclock.py` |
| what a sync client leaves behind | `backend/src/grimoire/store/external.py` |
| the memo that makes external writes visible | `backend/src/grimoire/store/statcache.py` |
| the rules, as tests | `backend/tests/test_atomic_guard.py`, `test_lock_domain_guard.py`, `test_lock_order_guard.py` |
| designs | `docs/superpowers/specs/2026-07-28-atomic-store-writes-design.md`, `docs/superpowers/specs/2026-07-28-cross-process-campaign-locks-design.md` |
