# Native record store

**Status:** first slice of [Native File Store And Revisions](roadmap/sprints/native-file-store-and-revisions.md) (Wave 2, #010), implemented as the filesystem adapter `grimoire.store`. It persists **facet-neutral record envelopes** for source-qualified definitions and campaign instances. It never interprets a payload, and it does not decide whether a record is a character, a PC, a creature or an item; that meaning belongs to [Entity Facets (009)](roadmap/sprints/entity-facets-and-instance-model.md), which joins through codecs in Wave 3. There is no application or UI yet. A green test suite proves this contract, not a playable app.

Not in this slice:
- the image blob store (018), sync (019), events and evidence (020), and effective views (022);
- **Markdown.** The sprint's outcome names "Markdown/JSON" records. By the requester's decision this slice is JSON-only (stdlib, no YAML dependency); a Markdown payload codec is a remaining 010 obligation, re-homed by the requester to its own later pull request.

The security audit, grants and approvals, and player sovereignty are persisted as [authority ledgers](#authority-ledgers-010b) (010b).

## Library layout

```text
<library>/
  grimoire-library.json                                           # {"format": "grimoire.library/1"}
  sources/<seg1>/…/<segN>/_source/manifest.json                   # one source's manifest
  sources/<segs>/_source/records/<kind>/<entity_id>/<revision>.json   # one definition revision
  campaigns/<campaign_id>/instances/<kind>/<instance_id>.json     # one campaign instance
  .txn/lock, .txn/journal.json, .txn/*.new                        # writer lock, journal, staging
  .txn/discard.json                                               # an unfinished discard_journal
  .conflicts/<hash16>/<name>~<digest12>[.N]                       # preserved versions awaiting a human
  .conflicts/discarded-<N>/…                                      # output of discard_journal (first unused N)
```

- **One directory per source segment; a source's own files live under its own `_source/`.** An ID segment can never start with `_`, so `_source` never collides with a sub-source. `world:harbor:dlc` lives at `sources/world/harbor/dlc/_source/`, a *sibling* of harbor's `_source/`.
  - A grant scoped to `sources/world/harbor/_source` covers exactly harbor.
  - A grant scoped to `sources/world/harbor` deliberately covers harbor and its sub-sources.
  - Moving or deleting a source *folder* (`sources/world/harbor/`) includes its sub-sources.
- Every authored path segment must be a valid [`ResourcePath`](TOOL_AUTHORITY.md#logical-addresses) segment. The whole root-relative path is the record's **logical address** for authorization, for example `sources/world/harbor/_source/records/character/wren/r1.json`. An identifier that cannot be mapped raises `UnstorableIdentifier` and is never renamed. Examples:
  - a Windows device name such as `con`;
  - a segment ending in `.`;
  - a file name with no room left for `.json` (IDs used as file names are limited to 123 characters);
  - a source ID deeper than 26 segments.
- **Reserved names for later families**, which the loader skips silently:
  - at the root: `authority`, `config`, `assets`, `secrets`;
  - inside a campaign: `scenes`, `events`, `evidence`, `relations`.

  These follow the [architecture layout](ARCHITECTURE.md#storage-root-and-provisional-organization) and are a deliberate deviation from an earlier draft that reserved root-level `events`/`evidence`. No root entry is a [V1 marker name](ARCHITECTURE.md#library-location-and-v1-isolation-implemented-grimoirelibrary).
- **Ignored silently:** OS and editor droppings (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `*~`, `*.swp`, `*.swo`, `.#*`) and directories with no file below them. Empty directories are not state: a failed commit can leave some behind, and nothing can remove them through the file seam. Per-target `.conflicts/<hash16>/` folders accumulate in the same way.
- **Device-local:** `.txn/` and `.conflicts/` must never be transferred by sync (019/028).
- **Root entries the store does not own** (for example the `worlds/` or `catalogs/` of the [architecture's example tree](ARCHITECTURE.md#storage-root-and-provisional-organization), including a `worlds` link into V1) are reported by `load` and never written, because no store address maps to them.

## File formats

All files are JSON: UTF-8 without a BOM, a top-level object, at most 8 MiB.

**Reading is strict.** Each of the following is a load problem for that one file, never a crashed load:
- invalid UTF-8, a BOM, duplicate keys;
- `NaN`/`Infinity`, numbers that overflow, integers longer than 4300 digits;
- lone surrogates, nesting deeper than 100 levels;
- an unreadable file, or anything other than a regular file (a directory, FIFO or device) where a file belongs. These are never opened, so they cannot hang a reader.

**Writing is deterministic:** sorted keys, two-space indent, `ensure_ascii=False`, trailing newline. A caller value that could not be read back is refused as `InvalidEnvelope`; this includes nesting deeper than 99 levels inside an envelope and a rendered document larger than 8 MiB. A rewrite may reformat a hand-edited file; *values*, including unknown ones, are preserved. For the envelope's own optional fields (`name`, `owner`, `derived_from`, `base`, `base_etag`, `title`), an explicit `null` means absent and is not written back.

**Etag (content revision):** `sha256:<hex>` of a file's exact bytes. It is never stored inside the file.

### Record envelope (`grimoire.record/1`)

| Field | Written by | Meaning |
|---|---|---|
| `format` | store | Exact version. Another version is `unsupported_format` and is never overwritten. |
| `address` | caller | A pinned `SourceRef` (definition revision) or an `InstanceRef` ([text forms](IDENTITY.md)). Must equal the address its path implies. |
| `payload` | caller | Opaque JSON. |
| `name` | caller, optional | Display name, used only for `find_by_name` candidates. An absent name stays absent. |
| `owner` | caller, optional | Attribution (nonempty text). |
| `derived_from` | caller, optional, instances only | `SourceRef` text, pinned or unpinned, kept exactly as written. |
| `base` | caller, optional, instances only | Pinned `SourceRef`: the concrete revision the instance's content was last based on. This is the retained common ancestor for 022/042. |
| `base_etag` | caller, optional | The etag of `base`'s file when it was adopted. `load` reports `base_drift` if that file has since been edited by hand. |
| `previous` | store | The etag of the file this write replaced (`null` on create). This is the per-record lineage for 019. |
| *other keys* | preserved | Unknown authored fields. |

The store retains **definition revisions** (they are immutable through the store) and the `base`/`previous` *identifiers* of instances. It does **not** retain the *content* an instance write replaced; ancestor-content retention for three-way merges belongs to 019/022.

### Source manifest (`grimoire.source/1`)

Fields:
- `source`: must equal the directory-derived ID.
- `owner`: nonempty.
- `title`: optional.
- `heads`: `{"<kind>/<entity_id>": "<revision>"}`. Keys are relative to the manifest's own source, so a manifest cannot declare a head for another source.
- `links`: `[{subject, relation, object}]`.
- `previous`.
- Unknown keys are preserved.

**`declared_by` is persisted by location, not as a field.** A link's declarer is always the manifest's own source. This is a deliberate deviation from "persist `declared_by`": it makes smuggling structurally impossible. A link that carries a `declared_by` key, or whose `subject` belongs to another source, is an `ownership` violation, and the whole manifest's source is excluded.

## Writing

`LibraryStore` is trusted application code. It is never handed to a worker; workers reach it only through a `WorkerHandle` decision. Reads take no handle.

```python
with LibraryStore.open(path) as store:                 # writer lock + recovery
    txn = store.transaction()
    address = txn.put(envelope_or_manifest, expected)  # expected: the etag read, or None = "must not exist"
    txn.preview()                                      # {logical path: exact bytes} -- what a human approves
    txn.commit(handle, approvals={address: approval_id})
```

**Definition revisions are create-only.** To edit a definition, add a revision and move its head in the same transaction. A pinned reference therefore always means the same bytes, and replacing a revision raises `ImmutableRevision`.

`commit` runs in a fixed order. Steps 1–7 mutate nothing.

1. A pending journal (from a crash) refuses with `PendingRecovery`.
2. Envelopes are validated and mapped to paths. This happens at `put`.
3. **Isolation and preflight.** Every one of these is read-only:
   - the root is the directory that was opened, by device and inode (or by resolved path where the filesystem reports no inode);
   - [`check_write_destination`](ARCHITECTURE.md#library-location-and-v1-isolation-implemented-grimoirelibrary) passes for the root and for each target's deepest existing directory, which catches bind mounts;
   - no existing component of a target path, its `.conflicts` path, `.txn` or `.txn/lock` is a link, junction or reparse point (`LinkRefused`), or a non-directory where a directory must be. Every open, recovery, `discard_journal` and read applies the same check to `.txn` before touching anything inside it;
   - `.txn` and each target are on the same filesystem (`CrossDevice`);
   - paths fit the platform's limits: 259 UTF-16 units on Windows, `pathconf` elsewhere.
4. **Compare-and-swap.** Any target whose current etag differs from `expected` (including exists versus absent), or whose current file is in a newer format, raises `StaleWrite` and lists every stale address.
5. **Name collisions.** A new name that differs only by case or Unicode form from an existing sibling raises `NameCollision`. Existence is always decided from directory listings by exact spelling, never by opening a composed path, because macOS and Windows filesystems are case-insensitive.
6. **Source consistency.** New or worsened problems in the entities and links the transaction touches raise `InvalidSourceState`:
   - a head naming a missing or unreadable revision;
   - several revision files and no head;
   - an entity ID reused under another kind;
   - a new link the loader would drop: a duplicate (including a reversed symmetric one) or one that closes a derivation cycle, checked against the links of every other source the loader would register. Only the committing source's own links are judged. Under the link-order policy below, a commit can cause *another* source's cross-source link to be dropped, and the next `load` reports it;
   - a revision without a manifest.

   A hand-made problem in an unrelated entity does not block a write, so repairs can be made incrementally.
7. **Authorization.** Every target is authorized with `Permission.APPLY` on its logical address over its exact bytes. Any denial raises `WriteDenied` and no target is written. **Trade-off:** every target is decided, so approvals already allowed for other targets in that transaction are consumed; `WriteDenied.consumed_approvals` lists them. Steps 1–6 never consume an approval.
8. **Staging.** Missing target directories are created and each file is staged as `.txn/*.new`. A `.conflicts/<hash16>/` directory is created only when something is actually preserved (after the commit point); its path was already checked in step 3, so only a genuine I/O error can fail there. An `OSError` here propagates and nothing is published; any consumed approvals are named in an exception note.
9. **Commit point:** the root is re-verified (step 3's session check), then the journal is written and atomically replaced to `.txn/journal.json`. If that fails before the replace, nothing is published.
10. **Pre-publish guard.** If any target now equals neither its old nor its new etag, an external edit raced the commit. The journal is first durably rewritten as `abort`, then every staged version is preserved in `.conflicts/`, and `WriteConflict(published=())` is raised.
11. **Publish.** Each target is re-hashed immediately before its `replace`. An edit caught there is kept, the store's version is preserved, the remaining entries publish, and `WriteConflict` names both what was published and what was preserved.
12. The journal is removed. Any failure in steps 10–12 raises `CommitIncomplete`, and the on-disk journal then blocks reads and commits until recovery. If staged files vanished before publication (removed by another program), commit also raises `CommitIncomplete` naming the targets that were not written; it never reports success for them.

**Residual race (R7, pinned by a test):** an external edit landing between step 11's per-entry re-hash and its `replace` is overwritten. Multi-file atomicity holds only when no external edit happens concurrently.

## Recovery

A writable `open` (and `store.recover()`) recovers before anything else.

**Validation first.** Nothing changes, not even cleanup of uncommitted staging when there is no journal, unless the root is still the one opened and outside V1. Recovery also requires that the journal and every staged file it names are regular files, not links or FIFOs, that the journal parses strictly, that every target is a canonical record or manifest path, and that every temp is a bare staging name in `.txn/`. All paths are re-checked as in step 3, so a link swapped in while the app was down is never written through. Any failure raises `RecoveryFailed`.

Then each entry follows one table, shared with publication:

| Temp | Temp digest | Target | Action |
|---|---|---|---|
| absent | — | any | already handled; the disk is kept (reported `incomplete` if the target is still the old version) |
| present | ≠ new | any | preserved; never published |
| present | = new | = old | published |
| present | = new | = new | temp removed |
| present | = new | other, or only a differently spelled look-alike | preserved; the human's file is kept |

A preserved version is named by the digest of its *actual* bytes. If that name already holds different bytes (for example, a human edited the conflict file), the version goes beside it as `….N`, so distinct bytes are never dropped. An `abort` journal never publishes. A commit journal whose temps are all still present, and which would preserve any entry, is first rewritten as `abort`. A crash part-way through preserving can therefore never resume as a partial publish. Recovery then removes the journal and any uncommitted staging, and records `store.last_recovery`: what was published, preserved and left incomplete.

**Recovery blocked.** A persistent I/O error makes a writable `open` raise `RecoveryBlocked` and release the lock. A read-only open still works, but its reads raise `PendingRecovery`. The explicit human way out is `LibraryStore.discard_journal(path)`:
- it takes the writer lock;
- it does **not** interpret the journal's paths;
- it moves the journal and all staging into the first unused `.conflicts/discarded-<N>/`, so an earlier discard is never overwritten.
- it first records its intent durably (`.txn/discard.json` names the folder). Until the discard finishes, recovery refuses to publish anything (`RecoveryFailed`) and reads raise `PendingRecovery`; running `discard_journal` again resumes into the same folder. An interrupted discard can never resume as a publishing commit.

Anything already published stays published, and `load` reports what is inconsistent.

## Reopening: `load()`

`load()` never writes and never follows a link: a link is reported as a `link` problem, and its target is never listed. It returns:
- a `DefinitionIndex` of `StoredDefinition`s;
- the readable sources and instances;
- `excluded` sources and `dropped` entities;
- `problems` (`LoadProblem(path, code, detail, source)`);
- `broken` references (`BrokenRef(path, field, text, reason)`, with the text exactly as written).

Damage is contained as locally as blame allows:

| Scope | Codes | Effect |
|---|---|---|
| file | `malformed`, `unsupported_format`, `address_mismatch` | that file is skipped (it still counts as a revision of its entity) |
| entity | `missing_head`, `bad_head`, `kind_reuse` | the entity is dropped; the rest of the source loads |
| link | `bad_link` (invalid, duplicate, or closes a derivation cycle) | the link is dropped; sources stay |
| source | `malformed`/`unsupported_format` manifest, `ownership`, `identity_conflict` | the source is excluded |
| library | `link`, `unrecognized`, `name_mismatch`, `unreadable` (a directory that cannot be listed) | reported only |

**Link order.** In-source links register first, then cross-source links, each group ordered by declaring source and then by file order. When two sources' cross-source links together close a derivation cycle, the link from the alphabetically later declarer is the one dropped. **Accepted policy** (requester decision on PR 568), as is judging only the committing source's own links in step 6.

Broken-reference reasons are the index's `missing_source`, `missing_entity` and `missing_revision`, plus four store reasons:
- `excluded_source`: the source exists on disk but was excluded;
- `entity_dropped`: the entity was dropped;
- `invalid_reference`: the text does not parse;
- `base_drift`: `base` no longer has its `base_etag`.

**Removing an add-on** leaves every other file byte-identical, and its references are reported as `missing_source`.

## Sessions and concurrency

- **One writer per library.** A writable store holds an OS advisory lock on `.txn/lock` until it is closed: `flock` on POSIX, `msvcrt.locking` on Windows. The OS drops the lock when the process dies.
  - A second writer gets `LibraryLocked`.
  - On a filesystem that cannot lock (some network shares), a writable open fails with `LockUnavailable`; a read-only open still works.
  - The locker is injectable. Crash-exploration tests use a process-local `ProcessLocker`.
- **Threads.** Listing caches are per thread, so a `load` in one thread never changes what a commit in another sees. Commits, ledger writes, authority decisions and in-process reads (`read`, `read_source`, `load`, `read_ledger`) all take the store's re-entrant lock (`store.lock`). A read in one thread waits for a commit in another, never sees it half-done, and so never raises `PendingRecovery` for a live commit. Other processes' readers are not covered: see Readers below.
- **A linked `.txn`** is never entered. Writable opens, commits and `discard_journal` refuse with `LinkRefused`; a read-only open succeeds, but its reads raise `LinkRefused`. Remove the link by hand.
- **Readers** (`writable=False`) take no lock and never recover. They are consistent only while no writer is active: a `load` that overlaps another process's commit may see a mix of old and new files. Cross-process read isolation is deferred to Effective World Snapshots (022) and Durable Action Recovery (030).
- **Durability:** files are fsynced before they are renamed. On POSIX the parent directory is fsynced after each rename and after each directory the store creates; this is best-effort where a filesystem does not support it, but genuine I/O errors are raised. Power loss is not simulated by the harness.
- **Windows reparse points:** every reparse point counts as a link and is refused, not only symlinks and junctions. A library inside a cloud-synced folder that uses placeholder files (such as OneDrive Files-On-Demand) is therefore refused. Narrowing this to specific reparse tags is future work.
- A malformed **instance** can be replaced by passing its exact etag (`MalformedRecord.etag`); the caller has, in effect, seen those bytes. The old bytes are not copied first. A malformed definition revision can only be fixed by hand.

## Facet independence

`grimoire.store` imports only `grimoire.core.identity`, `grimoire.core.index`, `grimoire.library` and `grimoire.security`, and pure packages may not import it (`backend/tests/purity.py`). A subprocess test pins the exact set of `grimoire` modules that `import grimoire.store` loads. `grimoire.core/__init__` currently pulls in `model` and `authority`, so 009 must not re-export facet classes from `grimoire.core`, or that test fails.

## Authority ledgers (010b)

A **ledger** is an append-only, hash-chained run of batch files under the reserved root `authority/`. There are two ledger kinds: `security`, and one `campaigns/<campaign_id>` ledger per campaign.

```text
authority/security/<seq:010d>.json
authority/campaigns/<campaign_id>/<seq:010d>.json
  {"format": "grimoire.ledger/1", "ledger": "<name>", "seq": N, "previous": "<etag of N-1>" | null,
   "entries": [ 1–500 JSON objects ]}
```

### Reading

`store.read_ledger(name)` verifies the whole chain:
- `seq` runs gap-free from 1, and each batch's `seq` matches its file name;
- each `previous` matches the etag of the batch before it, and `ledger` matches;
- the format is exact, the JSON is strict, and every batch is a regular file.

Any break raises `LedgerBroken(name, seq, reason)`; a ledger is never read partially. Other files beside the batches (for example a sync tool's "conflicted copy") are reported in `Ledger.ignored` and never replayed. For 019, that means another device's fork is dropped and only reported.

**Limit (pinned by a test):** with local file access, the chain cannot detect changes at the tail. Appending a valid batch (even one with consistent forged provenance), editing or truncating the tail, or restoring an older copy all keep a valid chain. Interior edits, deletions and reordering are detected.

### Writing

- Only trusted code writes, through one `LedgerWriter` per ledger per store session.
- Appends never pass through a worker's authorization; the `authority` root is reserved in `grimoire.security` too, so no grant or approval can name it.
- An append is one transaction of create-only batch files, split at 500 entries or 4 MiB. Each append checks the caller's expected head in O(1); if the head moved, it raises `StaleWrite`.
- Ledger batches ride the same journal, preflight, recovery and crash guarantees as records.

### Binding

`DurableAuthority.open` binds the store for the rest of its session; binding is permanent. Once bound:
- record commits need handles that authority issued; any other handle gets `WriteDenied`, and nothing is consumed;
- commit step 7 runs inside the authority's consequential scope, so every allow is on disk before step 8 stages anything;
- only the authority can obtain ledger writers.

An unbound store whose `authority/security` ledger exists refuses record commits. An unbound store without one keeps 010 behaviour: test fixtures may use that, production must not. Callers must not continue with a store after a failed `DurableAuthority.open`.

### Concurrency and limits

- **One lock:** the store and its authority share one re-entrant lock. A ledger flush from inside a commit re-enters it, and another thread waits.
- **No nested writes during staging:** a ledger commit started while a record commit is staging is refused, so a nested `settle` can never touch the outer commit's staged files.
- **One per process:** use one store and one authority per library per process.
- **Close order:** call `authority.close()` before `store.close()`; it flushes buffered decisions. `store.close()` never raises (requester decision, deviating from the 010b spec's `StoreError`). Closing the store first is not detected there; the later `authority.close()` raises `AuthorityUnavailable`, and the buffered non-consequential decisions are lost, as in a crash. Consequential allows are already on disk.

### Repair

`LibraryStore.repair_ledger(path, name, from_seq)` is an explicit human action:
- it takes the writer lock;
- it records its intent first in `.txn/repair.json`;
- it moves batches `>= from_seq`, newest first, into `.conflicts/ledger-<name>-<N>/`, where `/` in the name becomes `-` (`ledger-campaigns-c1-1`) and `N` is the first free number;
- if it is interrupted, running it again with the same arguments resumes it. Until it finishes, recovery and reads refuse.

**Warning:** history in the moved batches, including revocations and consumed approvals, is undone. `from_seq=1` resets the ledger, and the next authority open writes a fresh genesis.

### Growth

Ledgers grow without compaction, about one file per 500 decisions. Opening replays the whole security ledger.

## Remaining 010 obligations

- **A Markdown payload codec** (its own later pull request, by requester decision), so prose-heavy records can be authored as Markdown while the envelope stays JSON.
- **Later milestones** of the sprint: versioned snapshots and change journals, external-edit watching, ledger compaction, and device-level tests of real disk-full and permission errors.
