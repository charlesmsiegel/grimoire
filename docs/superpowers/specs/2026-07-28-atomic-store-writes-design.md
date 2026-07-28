# Atomic store writes (#233)

Every Markdown and JSON record in the store is currently rewritten with a plain
`Path.write_text`: truncate, then write. A crash, a full disk, or a sync-client
conflict between those two steps leaves a truncated file. For a scene transcript
that is unrecoverable — it cannot be regenerated from the chronicle, from the
model, or from anywhere.

Six modules from the newer mechanics work already write via a temp file +
`os.replace`. The older core never got the retrofit. This spec closes that gap
everywhere at once and adds a test that keeps it closed.

## What this actually guarantees

Stated precisely, because the first draft of this spec overclaimed and a review
caught it:

**A reader of a store record sees either the complete previous version or the
complete new version — never a partial one.** That is per-file atomicity against
a process crash, an exception mid-write, a full disk, or a reader racing a
writer.

It is **not** a durability guarantee against sudden power loss. CPython's
`os.replace` uses `MoveFileExW(..., MOVEFILE_REPLACE_EXISTING)` without
`MOVEFILE_WRITE_THROUGH`, and on POSIX we do not fsync the parent directory, so
the *rename* itself is not guaranteed to survive a power cut. The `fsync` we do
perform guarantees something narrower but still worth having: if the rename does
land, the bytes behind it are complete rather than a page-cache ghost. After a
power cut you may find the old version — you will not find a shredded one.

It is **not** consistency across sync clients. OneDrive and Dropbox resolve
simultaneous edits with conflict copies on their own schedule; a remote version
can supersede the local pathname after a perfectly successful local replace.
That is the sync client's domain, not this helper's. (One useful accident:
OneDrive does not sync `.TMP` files, which supports the temp suffix chosen
below.)

## Goals

- No store write can leave a partially-written record on disk.
- One mechanism, used by every writer — not six near-copies.
- A regression guard, so the next writer added to `store/` cannot reintroduce
  the bug silently.

## Non-goals

- **Multi-file atomicity.** `characters.write_card` writes a card *and* a meta
  file; a crash between them still leaves the pair inconsistent. Each file is
  individually valid, which is what this spec buys.
- **`assets.promote_image` (`assets.py:141-144`).** The avatar swap is a
  three-rename sequence through `promote-tmp<ext>`; a crash between renames
  strands the temp and leaves the avatar missing. That is a multi-rename
  recovery problem, not a torn write. Out of scope — but it is a real
  image-loss path, so it gets a filed issue, not a hand-wave (see Follow-ups).
- **Append-only reworking of `scenes.append_message`.** It stays a whole-file
  read-modify-write (O(n) per message).
- **Concurrency control.** No locking is added. In particular
  `scenes.append_message` has no per-scene lock today, so two concurrent
  appenders can already lose a message (A reads v0, B writes v1, A writes
  v0+delta). This spec does not fix that and must not *widen* it — see the
  retry decision below.
- **Linked records.** A record file that is itself a symlink or hard link is
  unsupported after this change (see Decisions). A symlinked store *root* or
  parent directory is fine and stays supported.

## The mechanism

A new `backend/src/grimoire/store/atomic.py`. It imports nothing from the
package, so it cannot participate in an import cycle.

```python
@contextmanager
def tempfile_for(path: Path) -> Iterator[Path]: ...
def write_text(path: Path, text: str) -> None: ...
def write_bytes(path: Path, data: bytes) -> None: ...
```

`tempfile_for` is the single mechanism; `write_text` and `write_bytes` are thin
wrappers over it. The context manager exists because one caller (`thumbs.py`)
hands a *path* to PIL's `im.save()` and never holds the bytes itself.

### Handle lifecycle

Specified explicitly, because `mkstemp` returns an already-open descriptor while
the PIL caller wants to open the path itself:

1. `mkstemp` in the target's directory, then **close the returned descriptor
   immediately**. The context manager yields only a path; it never holds a
   handle across the caller's block. This is what makes the PIL caller safe on
   Windows — no sharing-mode negotiation, nothing of ours blocking the replace.
2. The caller writes to that path however it likes.
3. On clean exit: reopen with `os.open(tmp, os.O_RDWR)` — writable, because
   Windows `FlushFileBuffers` requires write access — `os.fsync`, close.
4. Copy the target's file mode onto the temp (below), then `os.replace`.
5. On any exception: best-effort unlink the temp (swallowing `OSError`, since a
   scanner can hold it briefly on Windows) and re-raise.

### Decisions

**Same-directory temp.** `os.replace` is only atomic within a filesystem, so
`mkstemp(dir=path.parent)` guarantees the rename is a rename, not a copy.

**Temp naming `.<name[:40]>.<random>.tmp`.** Dot-prefixed and `.tmp`-suffixed so
the `glob("*.md")` / `glob("*.json")` listers cannot see one. The target name is
**truncated to 40 characters** — embedding a full name would add ~10 characters
to an already long component and could break the 255-character per-component
limit on a path that was previously writable. `mkstemp`'s exclusive creation
also removes a latent hazard in the four existing copies, which share a fixed
`p.with_suffix(".json.tmp")`: safe today only because `rolls`, `proposals` and
`audit` each hold a per-campaign in-process lock, and not safe across two
processes on one synced store.

**Preserve the file mode.** `mkstemp` creates `0600`. Without this step the
first atomic write would silently narrow every previously group/world-readable
record to owner-only — a real regression on Linux, macOS, and the Android build.
So: `os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))` when the target exists,
otherwise the process umask default (`0o666 & ~umask`). The chmod happens
*before* the replace, so the file is never briefly visible under its real name
with the temp's `0600`. This is a no-op on Windows, where the mode bits are
vestigial.

The umask must be sampled **once at import**, not per write: there is no getter
— you set it to read it — so doing that inside a request thread would briefly
expose a `0` umask to every other thread creating a file, making their files
world-writable. (Found by the implementation-stage review.)

What is *not* preserved, and is accepted: on Windows the surviving file is the
temp, so a target's explicit (non-inherited) DACL, alternate data streams, and
per-file compression/encryption flags are lost. Win32 has `ReplaceFileW`
precisely to preserve those, but Python does not expose it without `ctypes`.
Grimoire records are plain files in a user-owned folder that inherit their
directory's DACL and its compression/encryption state, so a new sibling gets the
same treatment. Documented as a known limitation rather than fixed.

**Leaf symlinks and hard links change meaning.** `Path.write_text` follows a leaf
symlink and rewrites its target, and updates the shared inode behind every hard
link. `os.replace` replaces the *directory entry*, breaking both. Grimoire has no
feature that creates linked records, so this is declared unsupported and covered
by a test that documents the behavior rather than one that pretends to prevent it.

**fsync before replace.** Makes the new bytes complete-if-the-rename-lands, per
the guarantee section. Measured on this machine (NVMe, local NTFS, 4 KB record):
**median 1.04 ms** per atomic write vs 0.11 ms for a plain `write_text`, p95
1.48 ms. So roughly 1 ms absolute, ~10× relative; a 100-file bulk copy pays
~100 ms. These are local-disk numbers — inside a OneDrive-filtered directory,
behind antivirus, or on a spinning disk, `FlushFileBuffers` can cost tens to
hundreds of milliseconds. Store writes are human-paced, so this is accepted, but
it is a measurement on one machine, not a universal constant.

**No `mkdir` in the helper.** `mkstemp` raises `FileNotFoundError` on a missing
parent exactly as `write_text` did, so callers keep their current semantics.
`sheets` and `assets` keep their own explicit `mkdir` line.

**Retry only Windows sharing violations, not every `PermissionError`.** The
first draft retried all of them, which was wrong twice over. First, most causes
are *permanent* — a read-only target, or a directory ACL that permits rewriting a
file but denies creating/deleting children (a real distinction: temp+replace
needs child-create and child-delete rights that an in-place rewrite does not).
Retrying those five times only delays the correct error. Second, and worse:
retrying widens the unlocked read-modify-write race described in Non-goals.
Writer A can fail its replace, sleep, and then land a stale temp on top of
writer B's completed update — turning a transient lock into a lost message, in a
change whose entire purpose is to stop losing data.

So the retry is narrowed to `PermissionError` with `winerror` 32
(`ERROR_SHARING_VIOLATION`) or 33 (`ERROR_LOCK_VIOLATION`) — exactly the
transient concurrent-reader / scanner / sync-client case, never the permanent
ACL and read-only cases, which now fail fast. Total backoff is capped at ~50 ms
across 4 retries to keep the widened race window small. The pre-existing
lost-update race is unchanged in kind and gets a filed follow-up.

### Accepted residue

A hard power loss between `mkstemp` and `os.replace`, or a failed best-effort
cleanup, orphans a `.tmp` file. The `glob`-based listers ignore it, but
`assets.py:56`, `overlay.py:333` and `campaigns.py:225` use bare `iterdir()` /
`rglob("*")` and would see it — `overlay`'s "is this directory empty" check
would read a stray temp as non-empty. Litter, not corruption; a sweeper would
bring its own failure modes. Documented, not fixed. (OneDrive ignores `.TMP`;
Dropbox does not ignore `.tmp` without a configured ignore rule, so a Dropbox
user may see one sync.)

## The retrofit

All ~100 `write_text` / `write_bytes` sites across the 35 modules in `store/`
move to the helper. The dominant shape is a one-line substitution:

```python
p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
atomic.write_text(p, dump_frontmatter(meta, body))
```

The heaviest files are `scenes.py` (15 sites), `module_edit.py` (14),
`characters.py` (7), `campaigns.py` (7), `pcs.py` (4). Scene writes land first —
highest frequency, least recoverable.

The six existing copies collapse onto the shared helper: `sheets._atomic_write_json`,
`proposals._write`, `rolls._write`, `audit._write`, `module_edit` (two sites),
and `thumbs` (via `tempfile_for`). `rolls.py`'s module docstring, which credits
the temp-file pattern for concurrency safety it does not provide alone, is
corrected to credit the lock.

Verified: no caller reads `write_text`'s return value (the character count) and
none passes `errors=` or `newline=`, so wrappers returning `None` are safe.

Verified: `os.fdopen(fd, "w", encoding="utf-8")` and `Path.write_text(text,
encoding="utf-8")` produce byte-identical output — same `TextIOWrapper` path,
no BOM, same `\n`→`\r\n` translation on Windows (`b"x\r\ny\r\n"`), same strict
error handling. Forcing `newline=""` would rewrite every file in the user's
store from CRLF to LF on its next save, churning a synced folder for nothing.

### Writers that are not `write_text` calls

- **`assets.put_image` (`assets.py:112-114`) — an ordering bug, not just a torn
  write.** It unlinks every prior-extension file *before* writing the new one,
  so a crash after the unlink loses the image outright; atomicity alone does not
  help. Reordered to write first, then unlink stale siblings.

  That reorder alone is not sufficient, because `image_path` (`assets.py:45-46`)
  returns `sorted(d.glob(f"{name}.*"))[0]` — the *first alphabetically*. Writing
  `avatar.webp` over an existing `avatar.png` leaves both files for a moment,
  and the sort hands back the stale `.png`. So `image_path` also gains a
  newest-`st_mtime` tie-break, which makes the transient two-extension state
  resolve to the correct image and makes a failed sibling-unlink self-healing
  instead of permanently wrong. The tie-break tolerates a sibling vanishing
  between the glob and the stat — the cleanup below is exactly such a deleter,
  and the old `sorted(...)[0]` never stat'd, so raising there would be a
  regression rather than a new check.

  The cleanup must snapshot its siblings **before** writing and delete only
  those. Globbing afterwards instead lets two concurrent `put_image` calls
  delete each other's brand-new file — A writes `.jpg`, B writes `.png`, each
  cleanup removes the other's — leaving no image at all, which is strictly
  worse than the delete-first ordering being replaced. (Found by the
  implementation-stage review.)
- **`sheets.seed` (`sheets.py:547`) — `shutil.copy2` into the live campaign
  sheets directory.** Routed through the helper, so a partial copy can never
  appear under a real sheet name.
- **`thumbs` (`thumbs.py:50-51`) — PIL `im.save(tmp)` then `tmp.replace(out)`.**
  Moves onto `tempfile_for`, which also removes its pid-based temp name (two
  threads in one process collide on it today).
- **`module_edit` zip extraction (`module_edit.py:213`)** writes members with a
  bare `write_bytes` into an *unpublished staging directory*, published by a
  single `staging.rename(dest)` (`module_edit.py:68`). Partially extracted files
  never enter the live namespace, which is the same class of protection this
  spec provides elsewhere — and, consistent with the guarantee section, the same
  limit: neither the member data nor the publish rename is fsynced, so it is
  crash-safe, not power-loss-durable. Left as-is with a marker comment.
- `shutil.copytree`/`rmtree` in `module_edit` write into staging or delete whole
  trees; neither can tear a live record. Unchanged.
- No `json.dump(..., fp)` or `extractall` sites exist under `store/`.

### The guard test

An AST check, not a grep — a textual scan silently misses calls split across
lines. It walks every `.py` under `store/`, collecting `Call` nodes for the
write APIs actually used in this package: `.write_text(`, `.write_bytes(`,
`open(..., "w"|"wb"|"a")`, `io.open`, and `os.write`. Each hit must be inside
`atomic.py` or carry an `# atomic-ok: <reason>` marker.

Described honestly: this is a **regression check over known write APIs**, not a
proof that every writer uses one mechanism. A determined writer can still reach
the disk through a library that takes an output path (PIL was exactly that), and
no lint can catch that. What it does buy is that the specific drift that caused
this issue — a new record writer added with a plain `write_text` — fails a test
instead of sitting unnoticed for a year.

## Testing

**Helper unit tests** (`backend/tests/test_atomic.py`):

- Round-trips text and bytes; byte-identical to the old `Path.write_text(...,
  encoding="utf-8")` output, CRLF included.
- `os.replace` patched to raise: the pre-existing file is untouched and readable,
  and no `.tmp` survives.
- The write itself raising mid-stream: same two assertions.
- A temp is invisible to `glob("*.md")` / `glob("*.json")`.
- Missing parent raises `FileNotFoundError`, as before.
- The written file keeps the target's prior mode (POSIX-only assertion).
- `PermissionError(winerror=32)` twice then success completes; a permanent
  `PermissionError` (read-only target, no winerror 32/33) raises immediately
  without burning retries.
- The temp name stays within the 255-character component limit for a long
  target name.
- Ordering is asserted explicitly: write → flush → fsync → close → replace.

**Guard test:** the AST scan described above.

**Integration tests:**

- `scenes.append_message` with `os.replace` patched to raise — the prior
  transcript still parses and holds every earlier message.
- `assets.put_image` with the write patched to raise — the *previous* image
  survives. Fails against the current delete-first ordering, passes after.
- `assets.put_image` changing extension — `image_path` returns the new image
  even while a stale sibling is still present.

**What the tests cannot show, stated plainly:** patching `os.replace` proves
Python-level cleanup, not NTFS power-loss recovery, filter-driver behavior, ACL
retention, or sync-client outcomes. Those are argued from documented platform
behavior in this spec and verified by hand, not by pytest.

## Follow-ups (filed, not hand-waved)

1. `assets.promote_image` three-rename swap — needs a recovery protocol.
2. `scenes.append_message` has no per-scene lock; concurrent appends can lose a
   message independently of this change.

## Risks

The retrofit is wide (~100 sites) but each edit is mechanical, and the existing
suite exercises these writers heavily (2035 tests). The real risk is a missed
site, which the guard converts into a failing test rather than a silent gap.
