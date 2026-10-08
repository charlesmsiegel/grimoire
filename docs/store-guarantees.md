# What the store promises

Grimoire's library is a tree of Markdown and JSON files under `store.home()`,
written by a server that handles requests concurrently and may not be the only
process holding that directory. Over #233, #234, #255 and #267 the store grew
real crash-safety and real mutual exclusion — but the rules ended up spread
across a handful of `store/` modules and the AST-parsing tests that enforce
them, so the only way to find out what a caller may rely on was to read every
one. This page is that answer in one place.

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
- [The image store](#the-image-store)
- [Model settings migration](#model-settings-migration)
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

### The primitives

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

One more public name is not a writer at all: `atomic.is_write_temp(path)`
answers whether a name is one of these temps caught mid-write. It exists for
the walks that copy or pack a whole directory of records — a world export, a
world fork — which must skip such a file: it is not part of the store's
content, and the writer that owns it will rename or unlink it out from under
the walk. The pattern lives beside the code that generates the name rather
than in those callers, where it would drift the day the prefix changes. Two
other readers delete what it matches. The backup: a process killed mid-archive
never reaches `streaming_write`'s cleanup, so `create_backup` deletes a temp
whose embedded name is one of its own archives once it has sat untouched for an
hour. And the thumbnail cache's sweep (`store/thumbs.py`), which deletes a temp
whose embedded name is a thumbnail entry inside a generation older than the
running build's, with no age test: nothing on this device writes into a retired
generation, and a device on an older build sharing the library through a sync
client, writing there at that moment, loses one thumbnail — it serves that
picture's original and makes the thumbnail again on the next request.

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
| `locks.world_actor_lock(wid)` | world actor name claims | serializes character and PC creation against the same name in one World |
| `locks.config_lock()` | global `config.md`, and every model-settings read-modify-write (`llm_connections.LOCK` is this same lock; `config.format_hold()` is it with the format check) | cross-process; taken inside a campaign lock (a campaign settings write), never around one, and the only lock under it is `inference.facts`' process-local file lock; see [Model settings across processes](#model-settings-across-processes) |
| `locks.backup_lock()` | one archive of one store at a time (#32) | a leaf; deliberately does **not** take the campaign locks |
| `locks.module_edit_lock()` | whole-directory module-pack publication | outermost in the ordering above |
| `locks.inference_migration_lock()` | the inference-settings migration (`store/inference/migrate.py`), one per store | only ever **tried**, never waited on: `migrate.ensure` returns the current status and `PUT /config/data-dir` answers 409 `busy`; outermost for its holder, which takes the backup, config and facts locks and, one at a time and never waiting, a campaign lock under it |
| `locks.image_object_lock(image_id)`, `locks.image_ingest_gc_lock()` | the image store | leaves; see [The image store](#the-image-store) |
| `locks.image_name_lock(d, name)`, `locks.image_sidecar_lock(d, filename)` | one image slot in one directory; one `descriptions.json` or `subjects.json` | striped, process-scoped, and taken **after** the campaign and collection locks and **before** the leaves above; see [Image locks](#image-locks-and-the-order-they-are-taken-in) |
| `locks.image_collection_job_lock(wid, job)`, `locks.image_collection_lock(wid)` | one harvest journal; one world's collection manifests and library-name checks | taken in that order, and before any image lock; see [Collections](#collections-members-by-id) |

The backup lock's exclusion is the interesting one: an archive of a hundred
campaigns cannot hold a hundred locks for the length of a zip without stalling
play, so **a backup taken mid-write can catch one campaign's two files a moment
apart.** The archive is a coarse restore point, not a transactional snapshot.

### What a caller sees when a lock is contended

Entering any of the five locks — campaign, world actor roster, config, backup,
module-edit — raises that lock's subclass of `locks.StoreBusy` after
`LOCK_TIMEOUT` (30 seconds): `CampaignBusy`, `WorldActorBusy`, `ConfigBusy`,
`BackupBusy`, `ModuleEditBusy`. One handler in
`main.create_app` turns any of them into **HTTP 409** with a message naming
what is busy, so the failure is retryable rather than a wedged server. (The two
non-blocking variants above never raise — they report with a boolean instead,
which is the whole point of them.) The inference migration lock's own
subclass, `MigrationBusy`, is raised by the same `with` shape, but nothing
enters that lock that way: every holder tries it and reports a boolean.

The image store's locks are the other shape of the same thing. The name and
sidecar stripes raise the base `StoreBusy` itself, worded "this image is busy;
try again in a moment", and the one handler answers 409 for it too. A
maintenance start that the store cannot take answers its own 409s: `busy` (a
tree operation or a data-dir move holds the store), `run_in_flight` (a
maintenance run is already live), `maintenance_elsewhere` (another process or
device is running one) and, for the tree operation refused *by* a run,
`maintenance_running`; see [Maintenance runs](#maintenance-runs).

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

### Model settings across processes

The model settings are spread over files that are read, checked and rewritten
together: `config.md`'s inference keys, a campaign's inference keys, the
connection records in `llm_connections/`, each connection's catalog cache and
model facts, and the sampler presets. Every read-modify-write of them holds
**one** cross-process lock for its whole span — from the read and every check
through the last write — so a second process's write to the same files never
lands in between and is never lost. That lock is `config_lock`;
`llm_connections.LOCK` is the same object rather than a second, process-local
lock taken before it, and `config.format_hold()` is that lock with the store's
format read inside it, which is how every one of those writes also refuses a
store a newer build switched (below). A campaign's inference keys are written
under its campaign lock and then this one. The migration's facts copy and the
marker it publishes share one hold of it, so another process's provider edit
cannot land between them either. `test_format_hold.py` enumerates the writers
and checks that each one's read is inside the hold.

### Reads notice external writes

Every request re-reads from `paths.home()`, and `store/statcache.py` keys its
memos on `(path, mtime_ns, size)` — so any write, including one arriving from a
sync client, invalidates the entry. Filesystem timestamps tick coarsely (up to
~15 ms on Windows), so a same-size rewrite moments after a cached read could
leave the signature unchanged; `RACY_WINDOW_NS` handles that the way git
handles racy-clean, by never caching a file whose mtime is that recent.

The character listings' rows (`statcache.memo_stamped`) are keyed wider,
because a row is built from directory listings as well as files — which card
files exist, which images a version holds. Each row carries a stamp of every
file it read *and* of every directory whose listing it read: a directory's
mtime moves on every create, unlink and rename inside it (every atomic write
included), so a card or an image arriving from a sync client is noticed
without the row having to list anything again. The stamp adds `st_ctime_ns`,
which on POSIX catches a same-size in-place rewrite whose mtime was handed back
— the residual the `(path, mtime_ns, size)` signature above accepts. On Windows
`st_ctime` is the creation time, which an in-place rewrite does not move, so
there that residual stands as it does for every other memo. What it cannot
see is a mount whose directories do not advance their mtime when an entry
changes; on such a store a new file shows up at the next change to anything
else the row read, or at restart.

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
directory walk of the whole library — and the Settings page's Storage
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

**Two processes on one machine, one user:** yes — but "yes" means exactly the
lock domain and nothing wider. A mutator in `DOMAIN_MODULES` is excluded across
processes; one in a module listed under `OUTSIDE_DOMAIN` or `UNREVIEWED` has no
such promise, and both lists are real. Read that as a floor rather than a
ceiling: the lists classify MODULES, so a single mutator in one of them may
still take the lock on its own account — `set_campaign_routing` does — and the
module stays out there for the neighbours that do not. So the honest summary is that the failure this
protects against — two processes interleaving a read-modify-write of the same
transcript — is closed, while a rename racing a `touch` is still open and
documented as such in `store/locks.py`.

**Two devices through a synced folder:** no, and nothing in this file changes
that. Every mechanism above is machine-local by construction — the lock is an
inode on one filesystem, the statcache watches one `mtime` — so the losing
device's edit is gone before any of them is consulted. `GET
/api/store/conflicts` is the only after-the-fact recourse, and only for the
subset of cases the sync client marks in a filename.

[`README.md`](../README.md) states this as user-facing advice, and it is the
copy to edit if the wording needs to change; what belongs here is only the
engineering reason it cannot be fixed at this layer.

---

## The image store

**Modules:** `store/image_store.py`, `store/image_refs.py`, `store/assets.py`,
`store/image_descriptions.py`, `store/image_subjects.py`,
`store/image_scopes.py`, `store/image_usage.py`,
`store/image_collections.py`, `store/image_collection_imports.py`,
`store/image_surfaces.py`, `store/image_migration.py`, `store/image_gc.py`,
`store/maintenance_reports.py` ·
**Spec:** [the content-addressed image store](superpowers/specs/2026-10-05-content-addressed-image-store-design.md) ·
**Tests:** `test_image_store.py`, `test_image_refs.py`, `test_assets_store.py`,
`test_image_surfaces.py`, `test_image_descriptions_store.py`,
`test_image_subjects_store.py`, `test_image_scopes.py`, `test_image_usage.py`,
`test_image_collections.py`, `test_image_collection_imports.py`,
`test_image_collection_routes.py`, `test_image_name_locks.py`,
`test_image_migration_plan.py`, `test_image_migration_run.py`,
`test_image_gc.py`, `test_maintenance_runs.py`, `test_maintenance_routes.py`

A picture's bytes are kept once, under `<home>/assets/image-store/`, and
everything that shows one holds a small placement naming it. Two kinds of file
live in the store: a **blob** (`blobs/<xx>/<sha256>.<ext>`, the encoded bytes)
and an **image object** (`objects/<xx>/px1-<hex>.json`, a sidecar naming the one
blob it keeps). A record, a library or a cover holds a **placement**,
`image-refs/<name>.json`, which is the image id plus occurrence-only data such
as `focus`. `test_image_surfaces.py` drives every upload route and both
store-side writers (greeting localization and collection members) and fails
if any of them leaves an image file in a record directory.

### Blobs are immutable

A blob's name is the SHA-256 of its contents, and `image_store._publish_blob`
writes one exactly once, through `atomic.write_bytes`. Publishing onto a path
that already exists checks size and hash first, and a match is left untouched.

**The single rewrite is a repair.** A blob whose bytes disagree with its own
name (a truncated sync, a disk fault) is overwritten with the bytes being
ingested, which are by construction the right ones, and the repair is logged as
`image_blob_repaired`. Nothing else ever replaces a blob's contents. Pinned by
`test_corrupt_blob_repaired` and `test_same_size_corrupt_blob_repaired`.

**Until then, a damaged blob is not served.** Its sha is the ETag and the
`?v=` token a browser caches for good, so a blob whose bytes no longer match
its name (`image_store.blob_intact`, memoized on the blob's stat signature
and re-hashed every time inside the racy window) is never answered under its
sha: no ETag, no immutable `?v=`, no 304 -- the original is a 404 -- and it is
never the source of a new thumbnail. A thumbnail already made under the sha
was made from intact bytes and is still served, without hashing the original.
Every other lookup -- listings, existence checks, promote, delete,
tombstones, `assets.path_in` -- still sees the placement as it is: integrity
is a serving question, not an existence one. Re-ingesting the same picture
in any encoding that yields the same id repairs it (an exact-byte re-upload
rewrites the blob, any other adopts the newcomer's bytes), and a world bundle
export leaves a damaged blob out. Logged as `image_blob_damaged`, once per
stat signature. Pinned by `test_a_damaged_blob_is_never_served_under_its_sha`,
`test_a_made_thumbnail_is_served_without_hashing_its_source`,
`test_no_thumbnail_is_made_from_a_damaged_blob`,
`test_ingest_adopts_another_encoding_over_a_damaged_retained_blob` and
`test_promote_copies_up_a_world_avatar_whose_blob_is_damaged`.

An image object keeps exactly one blob. A later upload of the same pixels in a
different encoding finds the object and discards the newcomer, so the slot goes
on serving the format that was ingested first: re-uploading a PNG avatar as a
lossless WebP does not turn it into a WebP
(`test_the_same_picture_in_another_format_keeps_the_first_format`). That is
the release note for anyone used to the old behaviour: **re-uploading the same
picture in another format keeps the first-ingested format.**

### Image locks, and the order they are taken in

| Lock | Scope | Notes |
|---|---|---|
| `locks.image_name_lock(d, name)` | one image slot in one directory (an avatar, a gallery picture, a library name) | one of `IMAGE_NAME_STRIPES` (256) stripes; the keys of two spellings that differ only by case share a stripe |
| `locks.image_sidecar_lock(d, filename)` | one `descriptions.json` or `subjects.json` in one directory | its own family of the same stripe count, so a name and a sidecar never wait on each other by accident |
| `locks.image_object_lock(image_id)` | read-modify-write of one object sidecar | one of `IMAGE_OBJECT_STRIPES` stripes keyed by the id's first byte; two objects on one stripe wait on each other, which costs time and never an update |
| `locks.image_ingest_gc_lock()` | the whole store | held across ingest's find-or-create (blob publish, sidecar write, mtime touch), so a garbage collector that deletes under it cannot interleave with an ingest |

**The order is** campaign, then `image_collection_job_lock`, then
`image_collection_lock`, then name, then sidecar, then the ingest/GC lock, then
an object stripe, never the reverse. All of them are process-scoped like the
campaign lock and keyed by the live store root
(`test_image_locks_are_keyed_per_store`). The two striped families also take a
file lock under `proclock.lock_dir()`, so a second grimoire process on the same
machine serializes on a stripe too
(`test_two_instances_serialize_across_the_file_lock`,
`test_a_second_process_waits_for_the_stripe`).

**Striping.** A stripe is chosen by hashing the directory and the case-folded
name, and the directory is taken **relative to the resolved store root** (an
absolute path only for a directory outside it), so two processes that spell the
root differently, as on a case-insensitive volume, still meet
(`test_a_directory_keys_relative_to_the_store_root`). The scheme is part of the
lock's domain string, so a build that changed the hash could not share a lock
with one that had not (`test_the_stripe_scheme_is_in_the_lock_domain`). Two
slots on one stripe wait on each other: that costs time and never a write, and
the file count is bounded.

**Holding several.** `assets._image_locks_held` takes a family's stripes sorted
and deduplicated, all up front; `set_in` takes the sidecar stripes of the edited
directory and of the fallback directory together, sorted. Forcing every stripe
to collide does not deadlock (`test_forced_stripe_collisions_do_not_deadlock`).
A heal of a stranded promotion reached while a name is already held is
**non-blocking**: it takes what is free and otherwise skips until the next scan
(`test_a_nested_heal_never_waits`).

**A writer refuses over an unfinished promotion.** Promotion writes a journal
before it swaps an avatar, and a reader's recovery finishes it. A writer that
finds a journal still standing for its slot, after that recovery could not take
the lock, raises `OSError` ("an earlier promotion is still unfinished; retry")
rather than write over it, because the journal may hold the only copy of an
avatar the collector would otherwise see as unreferenced
(`test_a_writer_never_writes_over_a_journal_its_recovery_skipped`,
`test_copy_slots_never_writes_over_a_skipped_journal`). `assets.delete_in` can
therefore now raise in that rare state, and its callers already treat an
`OSError` as retryable.

**Callers that can now stop part-way** (a stripe timing out between two of
their steps) were each checked safe to re-run: `copy_slots`, `sync._copy_tree`,
`overlay.copy_record_dir_down`, `characters._replace_gallery` and bundle
containment (`test_each_multi_step_caller_is_safe_to_rerun_after_store_busy`).

The ingest lock and the stripe are leaves relative to the campaign lock: either
may be taken while a campaign lock is held, and nothing acquires a campaign lock
while holding one of them. No AST guard holds that rule; `store/locks.py`
states it beside the code. The one place a caller's code runs under an object
stripe is an `image_store.update` callback, and it may not call back into the
store: `ingest`, `update` and `identify` raise `RuntimeError` from inside one
(`test_update_callback_may_not_reenter_the_store`). The collector deletes under
the ingest lock and then each object's stripe, which is ingest's own order.

### Collections: members by id

A collection is an immutable, ordered manifest under the world,
`<world>/assets/image-collections/<id>.json`. Two formats are read and only one is
written. **Format 1**, left by an older grimoire, names world-library images
(`collection-image-<sha256>`) and is served under the library's URLs. **Format
2**, the only one written now, is `{"format": 2, "members": [<image id>, ...]}`:
a member is its image object and never a library file, `put_member` returns the
id and places nothing, and `publish` refuses a member whose object does not
resolve. A published manifest is immutable; publishing again must be equal in
format and members (`test_a_published_manifest_is_immutable_across_formats`).
The collection's JSON keeps its `"id"` in both formats.

Member `n` of a format-2 collection is served at
`/api/worlds/{wid}/image-collections/{id}/members/{n}?v=<blob sha>`. **An index
is never reused.** A member whose picture is missing is skipped by
`available()`, every other member keeps its index, and the missing member's own
URL answers 404 rather than another picture, because those URLs are cached
immutable (`test_available_keeps_indices_stable_when_a_member_is_missing`). The
index is canonical ASCII decimal (`0` or a digit string with no leading zero,
length-capped), parsed by `image_collections.member_index`, which the route,
greeting `local_path`/`local_target` and export all share, so one picture has
one URL: `/members/01`, `-1`, `abc` and an index past the end are all 404
(`test_member_index_is_canonical_decimal`). Format-1 members keep their
library URLs, and the member route serves a format-1 index too.

**Locks.** Two process-scoped locks, both keyed by the live store root:
`locks.image_collection_lock(wid)` serializes one world's manifest
publication and the library-name checks that read manifests;
`locks.image_collection_job_lock(wid, job)` serializes one transient harvest
journal. The order is the job lock, then the collection lock, then any image
lock (the two leaf locks above), never the reverse. On Windows a world's two
spellings share both locks
(`test_windows_world_aliases_share_both_locks`).

**The library guard is retired by condition.** `guard_write` and
`check_member_name` protect format-1 names, which are library files, and do
nothing for a format-2 member, which is not one. They therefore return at once
unless `image_collections.has_format1(wid)`: the world holds a format-1
manifest, a manifest that does not read (it fails closed, as `referenced`
does) or a harvest journal that is not format 2 (one that does not parse
counts as format 1). A world holding both formats guards its format-1 names
and never lets its format-2 manifest fail a library write
(`test_format_2_manifests_never_guard_library_names`,
`test_format_1_members_stay_guarded_beside_format_2`,
`test_has_format1_fails_closed`). Reading a journal through `sample()` or
`accept()` converts it, and the migration converts or retires the rest (see
[Migration](#migration-legacy-files-into-the-store)); until a world has none
left, the guard stays on. The guard code itself stays.

**Harvest journals hold ids.** A new journal is format 2 and `accept` publishes
format 2. A format-1 journal in flight across the upgrade is converted when it
is first read, under the job lock and then the collection lock: a name becomes
the id of its library placement (a legacy file's bytes are hashed against the
name first, then ingested), the names are kept as `legacy_members`, and the
journal is saved as format 2 at once. A name that maps to nothing refuses the
journal. An accepted journal whose format-1 manifest is already published is
the exception: the manifest is authoritative, so it reconciles against the
names it already holds. Reconciling a crashed publication compares in the
manifest's own format (`test_image_collection_imports.py`).

**World bundles.** An export packs a format-2 manifest and the blobs of the ids
it names, and the bundle is format 3 only when it does (otherwise it is written
as format 2, so a collection-free world still opens on an older build). Export
validates only the manifests its import would refuse: a file that is not a JSON
object, or that says format 1, is packed unchecked as before; anything else
must read as strict UTF-8 and pass `image_collections.validate`, or the export
is refused before anything is written. Import contains manifests by folded
path and rewrites members through the bundle's id map; **a member id the
bundle does not carry is dropped, which shifts the index of every later
member**, a duplicate the rewrite creates keeps its first place, and a
manifest left empty is deleted. A format-1 or format-2 bundle still refuses any
manifest whose format is not 1.

**What a format-2 member is not.** Because it is not a library image, it is
absent from the library listing, the gallery, the world describe queue and art
recall, and cannot be hidden per campaign. It is tagged through greetings like
any picture, harvest deduplicates it by pixel identity rather than by bytes,
and an older grimoire sharing the store through a synced folder raises on that
world's library writes: upgrade every device, as stage 1 already asks.

### Placements are deterministic

`image_refs.write` serializes a placement as
`json.dumps(obj, sort_keys=True)` plus a newline, written as bytes so no
platform translates it, and nothing else goes in the file. Two placements of
the same image with the same focus are therefore byte-identical, which is what
lets campaign slimming compare them with `filecmp`
(`test_file_content_is_exact_and_deterministic`,
`test_files_are_written_as_bytes_with_lf`). An object sidecar is likewise
written with sorted keys.

### A placement beats a legacy file

Images written before the store existed are still read where they lie. When a
name has both a placement and a legacy `<name>.<ext>` file, **the placement
wins**. A placement that does not resolve (its object or blob not synced in
yet) falls back to the legacy file if one is there, on the rule that redundant
data beats lost data, and a version's art memo that includes such a placement
is not cached, so a blob that syncs in later is noticed (`test_ref_wins_over_legacy_and_unresolved_ref_falls_back`,
`test_version_art_uncacheable_while_ref_unresolved`). For the same reason
campaign slimming never prunes a placement that has a legacy file of its name
beside it, even one identical to the world's: the legacy file would become the
campaign's picture again
(`test_slimming_keeps_an_identical_placement_beside_a_divergent_legacy_file`).

### Shared metadata: descriptions and subjects

What a picture depicts is a fact about the picture, so it lives on the image
object, written only through `image_store.update`. Who appears in it is a fact
about a story, so it stays scoped.

- **A description is global (R4).** One picture has one description in every
  world and campaign that places it. Two uploads of the same pixels are one
  object, so they share the text. The one cap is `image_store.MAX_DESCRIPTION`;
  a longer write is refused with `DescriptionTooLongError` and answered 422,
  while a longer legacy key still reads.
- **Subjects are scoped (R10).** A tag is an association on the object under a
  scope, `world:<canonical id>` or `campaign:<cid>`, plus the scope's entry in
  `reviews.subjects`. Both spellings are made in one place,
  `image_scopes.world_scope` and `campaign_scope`, with one derived shortcut for
  a name a directory listing already spelled, `world_scope_of_dir`. Tagging a
  picture in one greeting tags it in every placement of it in that world, and in
  no other world. A remote reference, a reference to a picture placed in another world
  (R11) and a legacy name keep their tag in the greeting's `subjects.json`.
- **The legacy sidecar is read first (R1).** `descriptions.json` and
  `subjects.json` are what a name said before the object held it. For one
  directory a string key there wins; otherwise the object answers; otherwise the
  image is undescribed, or unreviewed. A key therefore masks the shared text
  until a write or a migration clears it, and a non-string value counts as
  absent. A greeting's image catalog carries each placement's image id, so
  answering for a subject re-reads no placement. An unarrived placement beside
  a legacy file answers its subjects from the greeting's sidecar.
- **A write goes to the object behind the visible placement (R2).** The object
  is written first. Only when `image_store.update` has returned `True` (R8) are
  the name's legacy keys deleted, in the directory being edited and in the
  visible placement's directory when that is another one. A write that is not
  confirmed, a placement that has not arrived, or a name with no placement
  writes the legacy key exactly as before, so a failure between the two steps
  leaves text showing twice and never none. Other directories' keys keep
  masking the shared text until migration. A campaign edit of art it inherits
  writes the shared object (R3); only a library image the world holds as a
  legacy file is refused, with a 409 telling the author to describe it in its
  world.
- **A promotion carries a text up only where it would otherwise be lost (R5):**
  as a campaign legacy key, never as a write to the object.
- **A replaced picture sheds its stale caption (R12).** Replacing a slot that
  held a known, different picture, with one whose object already has a
  description, drops the name's legacy key in the same operation. A first
  placement, an adoption of the same picture and a promotion never do. Anything
  else keeps the old text, as it always did.
- **Delete strips a scope; fork copies it.** Both scopes follow their record's
  lifecycle in `image_scopes`, because a slug is reusable and the object
  outlives the directory that held its tags. Deleting a world or campaign
  first moves its tree aside into `.world-staging` by one rename, then strips
  its scope from every object, then removes the moved-aside tree
  (`worlds.staging.remove_published`). A move that fails strips nothing. A
  strip that fails moves the tree back, with its tags intact, and the delete
  can be run again. A removal that fails after both is logged rather than
  raised: the record is gone and its tags are stripped, and the leftover stays
  under `.world-staging`, which nothing sweeps. A campaign delete holds the
  campaign lock across all three, so it can be refused with 409 behind a long
  hold before anything moves. A world
  fork copies the source's scope onto the fork after it is published, best
  effort: a failure is logged and the fork stands without its tags. A campaign
  fork copies its scope too, and a fork that fails strips the new scope again.
  The sweeps call `update` only for an object with something to change.
- **Usage is derived (R6).** `GET /api/images/{id}/usage` is an on-demand full
  walk of every world and campaign, answered by `image_usage.find` from the
  placements. Nothing records an inverse, so nothing can drift, and an image
  placed nowhere, or an id that is not one, gets an empty answer or a 400, never
  a 404.
- **The hot paths add no blob resolution beyond the listing they already
  make.** The shell badge, the to-do counts and the
  per-turn art catalogue count a placement-backed image as described from the
  object text, reading an object only for a name with no legacy key and using
  the ids the caller's listing already read.

Pinned by `test_a_local_legacy_key_wins_over_the_object`,
`test_an_unconfirmed_object_write_keeps_the_text_on_the_legacy_key`,
`test_put_in_drops_a_stale_caption_when_a_different_described_picture_replaces_it`,
`test_a_first_placement_keeps_its_legacy_key`,
`test_a_legacy_key_wins_until_retagged`,
`test_an_unarrived_placement_beside_a_legacy_file_answers_from_the_sidecar`,
`test_a_recreated_world_slug_starts_untagged`,
`test_a_world_whose_strip_fails_is_put_back_with_its_tags`,
`test_a_world_whose_final_rmtree_fails_is_still_deleted`,
`test_a_world_that_cannot_be_moved_strips_nothing`,
`test_forking_a_world_carries_its_tags`,
`test_a_fork_whose_tag_copy_fails_is_still_published`,
`test_a_failed_campaign_fork_leaves_no_scope` and
`test_usage_is_empty_for_an_unknown_id`.

### Migration: legacy files into the store

**Modules:** `store/image_surfaces.py`, `store/image_migration.py`

Pictures written before the store are legacy files in record directories.
Migration moves each into the store and leaves a placement, **on request, from
Settings, never at startup**. It is the `migrate` kind of a maintenance run
(see [Maintenance runs](#maintenance-runs)); a check run is a dry run that plans
everything and writes only its report, and it has the same shape as a real one
(`test_a_dry_run_writes_nothing_and_has_the_real_runs_shape`).

**What it promises.**

- **The only working copy is never deleted.** A legacy file is unlinked only
  after its placement was written and verified (the placement resolves to the
  object, the object to a blob under the pinned root, the blob re-hashes), and
  only if the file still has the identity that was hashed. A crash leaves
  legacy only, both, or the new form only, and a rerun finishes it
  (`test_crash_before_ref_write_leaves_legacy_only`,
  `test_crash_after_ref_write_leaves_both_and_rerun_cleans`,
  `test_crash_after_cleanup_is_new_only`,
  `test_a_file_changed_after_hashing_is_skipped`).
- **An existing placement is never overwritten.** A legacy file beside a
  placement of a different picture stays and is reported
  (`test_a_legacy_file_differing_from_an_existing_placement_is_kept_and_reported`).
- **It works on one root.** The root is captured when the run is reserved and
  every path is built from it. If the live root changes, the run stops and
  deletes nothing more (`test_a_root_flip_deletes_nothing_in_either_root`). A
  flip can leave a stray write in the *other* tree, from a call that had
  already resolved the live root, but never a delete.
- **It never goes through a link.** A symlinked directory, a symlinked
  `image-refs/` folder, a symlinked placement or a symlinked sidecar is
  reported and left, and nothing is written or unlinked behind it
  (`test_a_symlinked_placement_is_never_written_through`). The inventory
  reports a directory it cannot read or whose type it cannot tell, and never
  walks into it.
- **Names that differ only by case are left alone**, all of them and reported,
  since placing one would alias the other.
- **Metadata is folded only after its target verified**, under the directory's
  sidecar locks, and a key is deleted only if it still holds the value folded.
  Several differing descriptions become `description_conflicts` on the object
  (capped at twenty; a fold past the cap keeps the key), and the describe queue
  offers each as a choice. A saved description clears them.
- **Collections convert without moving anything a reader has cached.** A
  format-1 manifest becomes format 2 only when every member resolves, keeping
  its members' order and indices and its library placements, and greeting
  subject keys that named a member's library URL are rewritten with it
  (`test_format_1_manifest_converts_keeping_indices_library_urls_and_tags`).
  Harvest journals are converted, or retired only when unaccepted and
  unmappable.
- **A cancel is honoured between journals as between files.** Journals not yet
  reached are left as they were, and the work map is kept for the rerun
  (`test_a_cancel_while_journals_settle_keeps_the_rest_and_the_map`).
- **A real run reports what it freed.** `bytes_reclaimed` is the legacy bytes
  it deleted less the blobs its ingests added, so a run that stopped part-way
  reports less than its plan; a dry run reports the plan's figure
  (`test_a_real_run_reports_the_bytes_it_freed_not_the_plan`).

**What it does not do.**

- **It does not fold every key.** A same-world URL key in a greeting's
  `subjects.json` is kept (counted as `url_subject_keys_kept`), and so is every
  key in a *campaign* greeting's `subjects.json`: nothing reads a campaign
  scope there. A key it cannot confirm on the object also stays.
- **A folded description's provenance can read "object"** rather than the file
  it came from; the text itself is never lost.
- **It leaves what it cannot place**: a file that does not decode, a second
  file of a name, an unsupported extension, a member whose bytes disagree with
  its name. Each is in the report with its path.
- **A thumbnail it misses stays in the cache.** Removing a migrated file's
  entries is best effort.
- **It is not in the campaign lock domain's survey.** Every campaign write it
  makes takes `campaign_lock(cid)` and bumps the campaign's token, but the
  domain guard surveys modules by their public `cid`-taking mutators and
  `run(root)` has none, so neither this module nor the collector is in
  `DOMAIN_MODULES` or `OUTSIDE_DOMAIN`; a declaration for either would be a
  phantom the guard rejects.

### Garbage collection

**Module:** `store/image_gc.py`

Deleting a placement deletes only the placement. An object and its blob go only
when the collector, run from Settings as the `gc` kind of a maintenance run,
finds them unreferenced for long enough. A run is a **scan** (a dry run) or a
**collection** (which needs the scan's token), and it **fails closed**: when it
cannot be sure what is reachable it deletes nothing.

- **Roots are strict.** Every file in every `image-refs/` folder below an
  `assets/` component counts, whatever it is called, so a sync client's conflict copy of a placement keeps its image
  (`test_a_sync_conflict_copy_of_a_placement_is_a_root`); so do promotion
  journals, format-2 manifests, harvest journals, the migration's pending work
  and staged world and module trees. An unreadable directory, an unparseable
  file, an unknown format, a symlink or a folder spelled `Image-Refs` stops the
  run, naming the path
  (`test_an_unreadable_dir_an_unknown_format_and_a_symlink_abort`). A record
  whose own slug is `image-refs`, outside any `assets/`, is not a placement
  folder and stops nothing
  (`test_a_record_named_image_refs_is_not_a_placement_folder`). A staged
  tree younger than an hour, or a `.cache` that is a link, stops it too.
- **A candidate is old and seen twice.** It must be unreachable, with sidecar
  and blob both older than the **grace period** (30 days by default, never
  below 7), and **sighted unreachable** by this device at least a grace period
  ago and again on a later scan; being seen reachable resets the clock
  (`test_collectable_only_after_grace_old_first_sighting_and_a_second_scan`).
  A time in the future is never collectable and is reported as clock skew.
  Sightings are kept per device in `.cache/image-store/gc/`, which travels with
  a synced store, so one is keyed to the device that made it.
- **A blob goes only when nothing names it.** The **refcount** is every
  readable sidecar's blob, rebuilt under the lock; a blob another object still
  names survives
  (`test_a_shared_blob_survives_its_unreachable_object`), an unreadable sidecar
  keeps every blob, and so does a placement whose object has not arrived yet
  (`test_an_unarrived_object_keeps_every_orphan_blob`). An orphan blob (named by
  no sidecar) is a candidate under the same rules.
- **The token.** A collection takes the token a scan on this device issued. It
  is single-use, good for 24 hours, bound to its device and root, and a report
  that synced in from another device is refused
  (`test_a_token_is_single_use_device_bound_and_expires`). It is spent even if
  the collection then stops, so a stopped one needs a new scan.
- **Deletion is ordered against ingest.** Batches run under the ingest lock and
  then each object's stripe, re-checking mtime, root, reachability and refcount,
  and delete the sidecar before the blob
  (`test_a_concurrent_ingest_is_never_left_dangling`). It deletes nothing
  outside `image-store/` apart from the matching cache entries.
- **Reporting.** Each deletion is written to the report as it happens, so a
  stop part-way loses no row. Removing a blob's index entry and thumbnails is
  best effort and recorded in the report, never a reason to stop.

**What it does not promise.** A device that stays offline longer than the grace
period can bring back a placement whose image the collector has already
deleted: the grace period is the bound, and nothing here can see an offline
device. The refcount is rebuilt per batch, so a sidecar a sync client delivers
mid-batch is a residual risk. A stray non-JSON file inside an `objects/` shard
keeps every blob until it is removed, which is safe and can be noisy. A case
variant is checked only for `image-refs`: a folder spelled `Image-Collections`
stops nothing, and the manifests in it are not counted as references.

### Maintenance runs

**Modules:** `routes/runs.py`, `runner.py`, `routes/maintenance.py`,
`store/maintenance_reports.py`

Migration and collection are the `maintenance` run class, started from
Settings, Storage, Image store (`POST /api/maintenance/images/migrate` and
`.../gc`, both answering 202). They differ from every other run in what they
exclude.

- **One at a time, and not against a tree operation.** A maintenance run holds
  its own key, so a second start answers 409 `run_in_flight`
  (`test_maintenance_has_its_own_key_and_refuses_a_second_start`) and no other
  run is refused by it. In both directions with it: world fork, world delete,
  campaign delete, campaign fork, bundle export, **both manual backups** and the
  scheduled backup each hold `runs.maintenance_excluded` for their whole
  operation, a live run refuses them with 409 `maintenance_running`, and a held
  one refuses a start with 409 `busy`; the backup ticker skips its turn and logs
  it. The data-dir move is refused while any run is live
  (`test_a_data_dir_move_fork_delete_and_export_are_refused_while_maintenance_runs`).
- **One process, one device.** A second process on the same machine is refused
  by a `proclock` lock held for the run
  (`test_another_process_on_this_machine_refuses_a_start`), and a run on another
  device sharing a synced store is refused while its marker,
  `.cache/image-store/maintenance.json`, has a heartbeat under five minutes old
  (`test_another_devices_live_marker_refuses_a_start`); both answer
  `maintenance_elsewhere`. The marker is refreshed every 30 seconds by its own
  thread. The device id is machine-local, outside the store, so a copied
  `.cache` never shares one. The proclock excludes maintenance from
  maintenance only: the hold on tree operations is `runs.maintenance_excluded`,
  which lives in one process, so a second grimoire process on the same machine
  can still fork or delete a world or campaign while a run goes on in the first.
- **Cancel and shutdown wait.** The work runs in a thread whose await is
  shielded; cancellation is a flag the work reads between items, and the run
  stays live, with its exclusions held, until the thread returns. Shutdown sets
  the flag on every live run first. The report is written in the work's
  `finally`, so a cancelled or failed run still leaves one
  (`test_cancel_waits_for_the_thread_and_still_reports`).
- **Reports.** `.cache/image-store/reports/<run id>.json`, 30 days, one shape
  for a dry and a real run. A report holds counts and relative paths, and a
  path can carry a name; it is a file under `.cache`, never committed.
  Maintenance never runs on its own.

### What it does not promise

- **Devices on different versions.** A build from before the store writes a
  legacy file, and on a device running this one that file is shadowed by any
  placement of the same name, so the older device's upload seems not to land.
  Upgrade every device that shares a library before uploading from either.
- **One id per JPEG across decoders.** Pixel identity decodes the image, and
  JPEG decoders are not bit-identical: the desktop wheels and the Android build
  may decode one file differently. Identical bytes still meet while the blob
  index is current, since an index hit never decodes, but on a miss a second
  device can mint a second object for the same file. The cost is a duplicate,
  never two different pictures merged under one id.
- **Nothing is collected unless someone asks.** Deleting a placement never
  deletes an object or a blob. They go only when the person starts a collection
  from Settings, after a scan, and only once they are past the grace period and
  were seen unreferenced twice; see [Garbage
  collection](#garbage-collection). Until then an unreferenced image stays on
  disk.
- **A description is global, so it travels.** A world's bundle carries the
  description of every picture that world holds, including text written while
  the picture was placed in another world. The same sharing means a text typed
  in one world is read in all of them.
- **A world URL inside an object description is not rewritten.** A fork or an
  import re-points the URLs in the world's own files; a description sits on the
  object, which belongs to no one world, so it is left as written.
- **`delete_world` takes no lock.** A tag written between the strip and the
  removal survives on the object. It is rare, and running the delete again
  clears it.
- **Two journal edges close only when migration has run.** An unaccepted
  format-1 journal whose manifest an older build published, and which has since
  lost a member, does not reconcile until the migration retires it; an accepted
  format-1 journal whose manifest is gone is converted before it is refused
  until the migration keeps or retires it. Conversion keeps every member's
  index, including a missing member's.
- **A migration that cannot place a file leaves it.** The report names it. The
  image stays served as a legacy file, and a later run tries again.
- **Usage is not cached.** Each request walks every world and campaign, so it
  costs the size of the library and is asked for only when someone opens the
  control.

---

## Model settings migration

**Module:** `backend/src/grimoire/store/inference/migrate.py` · **Keys:**
`store/inference_keys.py` · **Started by:** `main.start`

Which provider, model and sampler preset each kind of call runs on used to be
stored as connections and per-route connection ids; it is now roles, routes
and per-model facts. A store records which layout it holds in one key of
`config.md`, `inference_keys.FORMAT_KEY` (`inference_format`), and a store at
`inference_keys.CURRENT_FORMAT` is on the new one. `migrate.ensure` moves a
store across, and this is what it promises.

### The safety archive comes first, and is never pruned

Nothing is written before `ensure` has zipped the whole store into
`pre-inference-grimoire-<stamp>.zip` in the backup directory
(`backups.SAFETY_PREFIX`). If that archive cannot be made, nothing else is
written, the status is `failed` with the reason, and the next start tries
again. It is a full restore point and `GET /backups` lists it with the others,
but `backups.sweep` counts and deletes the ordinary series alone: the safety
archive takes none of the `backup_keep` places and stays until a person
deletes it. One is taken per root — a run resuming after a later step stopped
reuses the archive its status note names while that file is still there, so
retries do not pile up copies nothing will ever prune.

### The global marker is written before the campaigns

After the archive, in order: each connection gains its provider preset and
billing basis (fields that leave its `rev` alone, so model catalogs, verified
tests and vector caches survive); each connection's stated vision, prefill and
post-processing become facts of its model; and then **one** `config.md` write
lands the roles, the routes and the format marker together; the campaigns are
migrated after it (below). The facts copy and that write share one hold of the
cross-process model-settings lock, and the write's input is read inside it, so
a legacy edit made while the archive was being built is carried over rather
than reverted or switched past, whichever server made it. It writes every global
key the migration owns, empty where unset, so a new-layout key left behind by
an interrupted run or a hand edit is overwritten by what the legacy settings
say now rather than surviving because nothing named it.

Until that write lands the resolver reads the legacy settings through the
translation (`store/inference/translate.py`), which answers as the new layout
would, so play is the same on either side of the switch.

### Idempotent and resumable

Every value is derived from the legacy settings deterministically, so two runs
— or two devices migrating one synced store — write the same bytes. A step
either passes over what is already done — a connection that has a preset, a
campaign carrying its marker, a `config.md` already at or past the current
format, checked again inside the lock that would write it — or, for a model's
facts, states the same value again. Every write is atomic (see
[Atomic writes](#atomic-writes)), so a run killed anywhere leaves each file
whole — the store still on the old layout before the marker, and after it
some campaigns still unmarked and read through the translation — and the
next start goes on from there.

The archive, the final `config.md` write and a connection file that exists
but cannot be read fail the migration. The last is not treated as a missing
connection: every step writes down what it read, so a read a sync client
blocked would otherwise be persisted beside the marker as a selection with
an empty model and no preset. A connection that does not exist at all is a
dangling reference and is persisted as one. An item that cannot be moved — an
unreadable `campaign.md`, a campaign naming an unreadable connection, a
connection or facts write that fails or is refused — is skipped with its
reason in the status's `skipped`, and the run carries on.

### Campaigns, each under its own lock

Each unmarked campaign is moved in one atomic write of its `campaign.md` (its
route choices and its own marker together) under `campaign_lock_nowait`, one
campaign at a time. A campaign whose lock is held is **skipped as busy**, not
waited for: it keeps resolving through the translation and is finished by the
next `ensure`, or by the first new-layout write to it —
`store/inference/settings.py` moves an unmarked campaign inside the same lock
hold as that write, so the marker it stamps never lands over overrides nobody
translated. Campaigns go AFTER the global switch: a campaign's routes are
translated from the connections they name, and before the switch a
connection's legacy `model` could still be edited — by this server or another
— leaving a campaign migrated earlier on the model the edit replaced. Once
the store is at format 2 those fields are refused on write, so what a campaign
is translated from cannot move under its step. The switch needs nothing from
the campaigns, so a busy one does not hold it back; the status stays
`pending` until it is done. The write bumps the campaign's write token
(`store/revision.py`) and leaves its `updated` stamp alone, since the library
is ordered by it and an upgrade is not something that happened in the
campaign.

### In the background, and 409 until it lands

Neither play nor any request runs it. `main.start` runs `ensure` on a daemon
thread at startup and again after `PUT /config/data-dir` moves the store,
holding the run registry's maintenance exclusion (`runs.exclude_maintenance`)
for its length: while an image-store maintenance run is live the pass is
deferred to the next start, and it logs that. `ensure` itself holds
`locks.inference_migration_lock` — a process lock plus a proclock, so two
processes never interleave — **tried, never waited on**: a second caller gets
the current status back at once. The data-dir move tries the same lock and
answers 409 `busy` while a migration holds it, and a run re-checks its root
before every step and item, so a root that moves underneath it gets no marker
in either tree. Lifespan shutdown sets the run's stop flag and waits briefly
(`MIGRATION_STOP_WAIT_SECONDS`); a stopped run is left `pending` and resumes on
the next start.

`migrate.status()` answers `done`, `pending`, `running`, `failed` or `newer`,
derived on every call from the global marker and whether any readable campaign
is still unmarked. Only `running`, a failure's reason, the last run's skips and
the safety archive's name are remembered, in `.cache/inference-migration.json`,
which no backup includes.

While the global layout is not current, writing the new settings (`PUT
/inference/settings`, `PUT /campaigns/{cid}/inference`) answers **409
`not_migrated`** carrying that status (`routes.common.refuse_unmigrated`): a
new-layout key written into a store the resolver still reads the old way would
be a 200 that changed nothing. Play is not refused.

### Older builds keep the old settings, frozen

The legacy keys (`active_connection_id`, `fallback_connection_id`, the
`route_*` keys, the two embeddings keys) and the legacy connection fields
(`llm_connections.MODEL_FIELDS`) are left as they were at the switch. A build
from before it keeps running on them, and nothing changed in the new settings
reaches it. Once a store is current, writing one of them is refused, because
it would reach older builds only: `PUT /config` answers 400 (`this setting
moved to Models`) and a connection edit setting a legacy field answers 400
too, each checked inside the
hold that writes, so a switch cannot land between the check and the write.

### A newer format is refused, not migrated

A marker that parses as a whole number above `CURRENT_FORMAT` means a newer
build switched this store to something this one does not understand
(`inference_keys.is_newer`; a malformed marker is not newer). `ensure` never
touches such a store, its status is `newer`, and every model-settings write —
providers, presets, model facts, the model test, the new settings and the
legacy keys alike — answers **409 `newer_format`** (`routes.common.refuse_newer`)
so this build cannot overwrite what it would misread. Play continues on what
it can read. A campaign a newer build marked is likewise left alone.

The route's check is the cheap answer; the binding one is in the store. Every
model-settings writer — the global and campaign settings writes, every
provider create, edit and delete, every model-facts write, every
sampler-preset write and the catalog cache — lands inside `config.format_hold()`: `config_lock`,
which the switch writes its marker in and which is cross-process, with the
format read inside it and held through the write. A newer build switching the
store after the route checked is therefore refused there too
(`config.NewerFormatError`, answered as the same 409), never written past; a
model test's verdicts that arrive after such a switch are not filed.
`test_format_hold.py` enumerates the writers.

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
- **The derived sidecars do not expire.** A tagline, a voice anchor and a
  campaign dossier stand until something writes over them; none carries a hash
  of what it was derived from, so none is ever marked out of date when the card
  or the campaign underneath it moves on. `store/taglines.py`,
  `store/voice_anchors.py` and `store/dossiers.py` each record the reason
  beside the code — three absences with two different arguments behind them,
  and all three deliberate (#57). Unlike these three, the continuity candidate
  cache does notice: each finding keeps the fingerprint of the records it was
  found against, and one whose records have since changed is shown as stale
  until the next sweep (`store/continuity/pending.py`).
- **The campaign write token is evidence, not proof.** `store/revision.py`
  gives each campaign one opaque value that changes whenever this app records a
  write to it, so a caller can price an operation against a state and be refused
  if the campaign has left it (`POST /advance` does; `POST /fork` keys a repeat
  on it). What it cannot see is a write this app did not make — a hand edit, a
  sync client landing a file, an older process — and a bump that lands after the
  mutation it records leaves a window where a committed write has not moved the
  token yet. So a token that has not changed is a strong hint that nothing has
  happened, never a guarantee; the refusal it earns is a re-price, and no write
  depends on it being complete.
- **Nothing across devices**, and nothing across OS users.
- **No mixed-version image writes, and no cross-decoder JPEG identity.** See
  [The image store](#the-image-store).
- **No automatic image maintenance, and no collection of an image a device
  outside the grace period still holds.** See [Garbage
  collection](#garbage-collection).
- **No background watcher.** The rebuilt app runs no resident machinery;
  conflict detection is on demand, and nothing notices an external write until
  something reads the file.
- **No way back that keeps what changed since.** An older build runs on the
  model settings as they stood at the switch, and the `pre-inference-` archive
  is the whole store as it stood before it; neither carries a change made
  afterwards. See [Model settings migration](#model-settings-migration).
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
| what the campaign write token is, and is not | `backend/src/grimoire/store/revision.py` |
| how an image is stored, placed and resolved | `backend/src/grimoire/store/image_store.py`, `image_refs.py`, `assets.py` |
| how legacy images migrate, and how unused ones are collected | `backend/src/grimoire/store/image_migration.py`, `image_gc.py`, `routes/maintenance.py` |
| how model settings move to the new layout, and what is frozen for older builds | `backend/src/grimoire/store/inference/migrate.py`, `store/inference_keys.py` |
| the rules, as tests | `backend/tests/test_atomic_guard.py`, `test_lock_domain_guard.py`, `test_lock_order_guard.py` |
| designs | `docs/superpowers/specs/2026-07-28-atomic-store-writes-design.md`, `docs/superpowers/specs/2026-07-28-cross-process-campaign-locks-design.md`, `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md` |
