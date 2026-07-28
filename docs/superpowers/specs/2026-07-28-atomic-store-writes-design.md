# Atomic store writes (#233)

Every Markdown and JSON record in the store is currently rewritten with a plain
`Path.write_text`: truncate, then write. A crash, a full disk, or a sync-client
conflict between those two steps leaves a truncated file. For a scene transcript
that is unrecoverable — it cannot be regenerated from the chronicle, from the
model, or from anywhere.

Six modules from the newer mechanics work already write via a temp file +
`os.replace`. The older core never got the retrofit. This spec closes that gap
everywhere at once and adds a test that keeps it closed.

## Goals

- No store write can leave a partially-written record on disk.
- One mechanism, used by every writer — not six near-copies.
- A regression guard, so the next writer added to `store/` cannot reintroduce
  the bug silently.

## Non-goals

- **Multi-file atomicity.** `characters.write_card` writes a card *and* a meta
  file; a crash between them still leaves the pair inconsistent. Each file is
  individually valid, which is what this spec buys. Transactional grouping is a
  much larger change and is not attempted.
- **`assets.promote_image` (`assets.py:141-144`).** The avatar swap is a
  three-rename sequence through `promote-tmp<ext>`. Each rename is atomic, but
  the *sequence* is not: a crash between them strands the temp and leaves the
  avatar missing. Fixing it needs a recovery journal or a documented restart
  protocol — a different problem from a torn write, and out of scope here. Filed
  as a follow-up.
- **Append-only reworking of `scenes.append_message`.** It stays a whole-file
  read-modify-write (O(n) per message). Making it a true append is a separate
  performance concern, not a durability one.
- **Concurrency control.** This spec does not add locking. Writers that race
  today still race; they just can't tear a file while doing it.

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
wrappers. The context manager exists because one caller (`thumbs.py`) hands a
*path* to PIL's `im.save()` and never holds the bytes itself.

The sequence is: `mkstemp` in the target's own directory → caller writes → flush
+ `os.fsync` → `os.replace` onto the target. On any exception the temp is
unlinked and the exception re-raised, so a failed write leaves the previous
record exactly as it was.

### Decisions

**Same-directory temp.** `os.replace` is only atomic within a filesystem;
a temp in the system temp dir could land on another volume and degrade to a
copy. `mkstemp(dir=path.parent)` guarantees the rename is a rename.

**Temp naming `.<name>.<random>.tmp`.** Dot-prefixed *and* `.tmp`-suffixed, so
the `glob("*.md")` / `glob("*.json")` listers throughout the store cannot see
one. `mkstemp`'s randomness also removes a latent hazard in the four existing
copies, which use a fixed `p.with_suffix(".json.tmp")`: safe today only because
`rolls`, `proposals` and `audit` each hold a per-campaign in-process lock, but
not safe across two processes pointed at one synced store.

**fsync before replace.** Without it a power loss can land the rename while the
data is still in page cache, yielding a zero-length or garbage record — the
exact unrecoverable outcome this spec exists to prevent. Cost is one flush per
write (~0.1–1 ms on SSD). Store writes are human-paced; the worst case is a bulk
path like campaign copy paying ~100 ms per 100 files. No directory fsync — it is
not meaningful on Windows, the primary platform here.

**Default `newline`, not `newline=""`.** Verified: `os.fdopen(fd, "w",
encoding="utf-8")` and `Path.write_text(text, encoding="utf-8")` produce
byte-identical output (`b"x\r\ny\r\n"` on Windows). Forcing `newline=""` would
silently rewrite every file in the user's store from CRLF to LF on its next
save, churning a synced folder for no benefit.

**No `mkdir` in the helper.** `mkstemp` raises `FileNotFoundError` on a missing
parent exactly as `write_text` did, so every caller keeps its current semantics.
Callers that need the directory (`sheets`, `assets`) keep their own explicit
`mkdir` line.

**Bounded retry on `PermissionError` around `os.replace`.** This is the one
genuinely new failure mode. CPython opens files on Windows without
`FILE_SHARE_DELETE`, so a concurrent reader — a list endpoint reading scene
heads while a message appends — can make the replace fail where a plain
`write_text` would have succeeded. OneDrive and antivirus scanners cause the
same transient lock. Five attempts with short backoff, under 100 ms total, then
raise. The trade is a rare 500 in place of a silent truncation.

### Accepted residue

A hard power loss between `mkstemp` and `os.replace` orphans a `.tmp` file. The
`glob`-based listers ignore it, but `assets.py:56`, `overlay.py:333` and
`campaigns.py:225` use bare `iterdir()` / `rglob("*")` and would see it —
`overlay`'s "is this directory empty" check would read a stray temp as
non-empty. This is litter, not corruption, and cleaning it up would need a
sweeper with its own failure modes. Documented, not fixed.

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
the temp-file pattern for concurrency safety it does not actually provide alone,
is corrected to credit the lock.

No call site changes behavior: same bytes, same exceptions, same
`FileNotFoundError` on a missing parent.

### Writers that are not `write_text` calls

A grep for `.write_text(` alone would miss these, so they are named explicitly:

- **`assets.put_image` (`assets.py:112-114`) — an ordering bug, not just a torn
  write.** It unlinks every prior-extension file *before* writing the new one,
  so a crash after the unlink loses the image entirely; making the write atomic
  does not help. Reordered: write the new file atomically first, then unlink the
  now-stale siblings of other extensions.
- **`sheets.seed` (`sheets.py:547`) — `shutil.copy2` into the live campaign
  sheets directory.** Copied through the helper instead, so a partial copy can
  never appear under a real sheet name.
- **`thumbs` (`thumbs.py:50-51`) — PIL `im.save(tmp)` then `tmp.replace(out)`.**
  Moves onto `tempfile_for`, which also removes its pid-based temp name (two
  threads in one process collide on it today).
- **`module_edit` zip extraction (`module_edit.py:213`)** writes members with a
  bare `write_bytes`, but into an *unpublished staging directory* that is later
  published by a single `staging.rename(dest)` (`module_edit.py:68`). It is
  already crash-safe at the granularity that matters, and per-member temp+fsync
  would slow large module imports for nothing. Left as-is, with a marker
  comment (see below).
- `shutil.copytree`/`rmtree` in `module_edit` write into staging or delete
  whole trees; neither can tear a live record. Unchanged.
- No `json.dump(..., fp)` or `extractall` sites exist under `store/`.

### The guard test needs reviewed exceptions, not a blanket ban

A flat "no `.write_text(` in `store/`" rule would flag the staged zip extraction
above, and the honest fix for that is an exception, not a contortion. So the
guard allows a site when it carries an explicit trailing marker:

```python
dest.write_bytes(z.read(i))  # atomic-ok: staging dir, published by rename at L68
```

The guard asserts every `.write_text(` / `.write_bytes(` under `store/` is
either inside `atomic.py` or carries an `# atomic-ok:` marker with a reason.
Exceptions stay possible but become visible and reviewable, which is the
property the issue actually wants — the original bug was invisible drift.

## Testing

**Helper unit tests** (`backend/tests/test_atomic.py`):

- Round-trips text and bytes; output is byte-identical to what
  `Path.write_text(..., encoding="utf-8")` produced, CRLF included.
- With `os.replace` patched to raise, the pre-existing file is untouched and
  readable, and no `.tmp` file survives in the directory.
- With the write itself raising mid-stream, same two assertions.
- A temp file present in a directory is invisible to `glob("*.md")` and
  `glob("*.json")`.
- A missing parent directory raises `FileNotFoundError`, as before.
- `os.replace` raising `PermissionError` twice then succeeding completes; raising
  every time surfaces the error after the bounded retries.

**Guard test:** scans every `.py` under `backend/src/grimoire/store/` for
`.write_text(` / `.write_bytes(` and asserts each hit is either in `atomic.py`
or carries an `# atomic-ok:` marker. This is the piece that prevents the
recurrence the issue describes. The test also asserts the marker list itself is
short and each marker has a non-empty reason, so `# atomic-ok:` cannot become a
rubber stamp.

**Integration tests:**

- `scenes.append_message` with `os.replace` patched to raise — the prior
  transcript still parses and still holds every earlier message.
- `assets.put_image` with the write patched to raise — the *previous* image is
  still present, which fails against the current delete-then-write ordering and
  passes after the reorder.

## Risks

The retrofit is wide (~100 sites) but each edit is mechanical and type-checked by
the existing suite, which exercises these writers heavily (2035 tests). The real
risk is a missed site, which the guard test converts into a failing test rather
than a silent gap.
