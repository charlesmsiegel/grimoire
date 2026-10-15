# Scoped model-settings safety archives (#496)

Status: proposed; implementation awaits design approval and the repository's
review gates. Origin: <https://github.com/charlesmsiegel/grimoire/issues/496>.

## Intent and evidence

A model-settings upgrade must protect every record it can change without
waiting for unrelated library content, and must say what it is doing while
the settings editors wait. Ordinary full and image backups keep their current
scope and failure policy.

A disposable-store reproduction against this branch confirmed that
`migrate.ensure()` archives an image under `assets/image-store/` and an
unrelated scene transcript, exposes only `state: running` during the archive,
and recopies the first member after an archive failure. No real library was
read. The issue is caused by both migration archive sites calling the
whole-store `backups.create_backup`, with no progress seam.

## Choice and alternatives

Use a settings-only selection of the existing ZIP writer and publish file
counts through migration status. Keep the current completed-archive reuse
rules. An interrupted, unpublished ZIP restarts; resuming an incomplete ZIP
is outside this change. The issue makes that extension optional, and removing
unrelated content removes the principal cost of restarting.

Keeping full safety archives and adding progress would leave the dominant
cost intact. Adding a resumable archive format would introduce an inventory,
validation and recovery protocol for content this upgrade never changes.

This is a recovery-contract change: a settings archive supplies protected
record bytes, rather than a complete library that can be restored into an
empty folder. It must be distinguishable everywhere full archives matter.

## Protected records

Both migration and retirement use the same selection, preserving the exact
bytes and store-relative paths of existing files:

| Location | Reason |
| --- | --- |
| `config.md` | Mapping, global format and retirement markers, legacy-key removal |
| `llm_connections/`, recursively | Connection metadata, model facts, and sidecars removed during connection retirement |
| `sampler_presets/`, recursively | Persisted derived reasoning presets |
| `inference-retired.json` | Retirement's preserved fields and explanatory notes |
| `campaigns/<id>/campaign.md` | Campaign mapping, legacy keys and both markers; preserve the whole file, including its body |
| `campaigns/<id>/revision.txt` | The write token bumped by migration and retirement |

The selection does not descend into assets, worlds, scenes, logs, other
campaign subtrees or rebuildable caches. Enumerate only the two selected
settings directories and the immediate campaign directories. Do not walk the
whole store and filter afterward. A missing optional location contributes no
members; an unreadable selected file or directory fails the archive. Keep the
existing policy for files or directories that disappear during a live backup.
Do not follow directory symlinks below the store root. If a selected settings
directory or campaign directory is a symlink, fail the safety archive rather
than authorizing writes to unprotected linked records. A store root itself
reached through a symlink retains its existing supported behavior. Keep
backup artifacts out, including when
the configured backup directory is the store root. A settings archive must
fail clearly before protected writes if its output-directory exclusion would
omit a protected settings subtree or campaign record. In particular, output
at `llm_connections/`, `sampler_presets/`, `campaigns/` or an existing
campaign's directory must not silently discard those records. Output at the
store root remains supported by excluding recognized archive artifacts rather
than the root itself. Test both settings-directory and campaign-directory
overlap. Existing file-symlink behavior is unchanged.

The backup lock, resolved root/directory, compression choices, sorted members,
timestamp handling and `atomic.streaming_write` publication remain shared
with existing archives. A failure must publish no ZIP and must leave the
migration or retirement's protected writes unstarted. Existing connection
seeding before the safety archive remains outside those protected writes, as
it is today.

## Names, listing, retention and recovery

New settings archives have explicit names:

- `pre-inference-settings-grimoire-<stamp>.zip`
- `pre-retirement-settings-grimoire-<stamp>.zip`

Keep `SAFETY_PREFIX` and `RETIRE_PREFIX` as the family prefixes. Recognition,
same-second allocation, backup-artifact exclusion and abandoned-temp cleanup
must accept both these names and the older full safety archive names.

`GET /backups` continues listing both safety families alongside ordinary
backups. Add an optional-compatible row scope (`full` or `settings`) so the
panel identifies settings safety archives as partial. Older safety names
remain full archives. Image archives retain their separate listing. Scope
comes from the recognized filename; listing need not read ZIP contents.

Retention still counts and deletes only ordinary full archives. The schedule
must ignore settings archives when deciding whether a full backup is due;
older full safety archives retain their current scheduling behavior. A store
with settings archives alone is due for a full backup.

The panel explains that settings safety archives omit images and story
content, cannot recreate a library in an empty folder, and require the
original library or a separate full backup for recovery. They preserve the
listed files as rollback material; this change adds no automatic restore or
claim that overlaying an archive removes files created after it. Full-restore
instructions must explicitly apply only to full archives. Never suggest
restoring over a running app.

Reuse still requires this root's status note to name an existing completed
archive in the relevant family. Both old full and new scoped archives qualify.
A reused migration archive does not replace retirement's archive; only one
created by the same run may do that. Preserve the existing immediate note
after successful publication. Do not infer a reusable archive from an orphaned
temp or from an arbitrary archive filename. Partial ZIP resumption, archive
discovery after losing the status note, and new cross-device snapshot
guarantees are outside scope.

## Progress contract

Add an optional archive progress callback to the existing backup path, used
by migration and retirement. Inventory only selected files before writing,
then report an initial count and monotonically increasing processed-file
counts. Counts describe work processed; a file that vanished under the
existing tolerated-race rule still completes its inventory slot. Byte counts,
per-file names, paths and time estimates are unnecessary.

`Status.as_dict()` adds optional `progress`, absent when there is no active
archive:

```json
{"phase": "safety_backup", "done": 3, "total": 8}
```

The other phase is `retirement_backup`. During inventory, `total` may be
`null`, with `done: 0`; the banner says the archive is being prepared rather
than drawing an invented percentage. Once known, totals are nonnegative and
`0 <= done <= total`. A zero-file selection reports `0 / 0` without division.

The run carries progress in its pinned-root state and best-effort status
note so another server process can read it. Throttle intermediate note writes
to at most four per second, with immediate publication for phase transitions
and the final count. Failed progress persistence must not prevent a valid
safety archive or any existing migration behavior. Clear progress on archive
exit, success or failure, and at run termination. A new run starts without
the previous run's counts. Preserve the existing root provenance and
cross-process running-note semantics; this is not a process-liveness redesign.

Migration state, failure reasons and retirement accounting retain their
existing meaning. In particular, retirement may run while migration state is
already `done`; progress must remain visible then without marking model
settings unwritable.

Add an optional-compatible `active: boolean` status field, independent of
`state` and `progress`. It covers the complete live `ensure` hold, including
retirement planning in `_idle`, the interval after the global marker switches,
and gaps between archive phases. A response with `state: done`, `active: true`
and no `progress` therefore means writable settings and a run still in flight.
Do not derive activity solely from the progress note: planning starts before
that note is written. Use the existing in-process root activity and a
nonblocking migration-lock probe for another process's hold, preserving the
no-write behavior of an idle start. Scope the probe to the resolved root;
never wait on the migration from a status read. A storage move holding the
same exclusion can conservatively report active until it releases it. Stop
reporting activity once the hold ends, even if an old process left a running
note. The existing migration state rules themselves remain unchanged.

## UI behavior

Use the shared migration presentation helpers and existing settings poller:

- While the global layout is pending, the banner says it is preparing the
  model-settings safety archive, or shows `done of total files` while copying.
- On a current layout, active retirement archive progress appears in the
  existing quiet migration line; it does not disable editors.
- Continue the existing poll cadence while the run is `active` or either
  archive has progress, including retirement planning and inter-phase gaps
  at format 2. Keep polling when a first response says `done`, `active: true`
  and no progress, so a page already open discovers the subsequent archive.
  Stop polling once activity and progress end and normal completion rules
  allow it. Keep the newest-read/save-wins behavior and timer cleanup.
- Existing pending, failed, newer and no-status messages continue working.
  Clients accept responses without progress and rows without the new scope.

No new progress endpoint, run class, settings switch or notification is needed.

## Verification and review requirements

Write and observe failing regressions before product changes. Cover:

1. Exact member sets and byte preservation for both safety families, including
   model facts, retired records, campaign bodies and revisions. Assert that
   irrelevant asset and transcript directories are never traversed.
2. Unreadable selected content fails before protected writes; missing optional
   paths, vanished members, symlinks and unusual backup-directory locations
   retain the stated behavior. No partial archive is listed.
3. Old and new names are recognized, excluded from nested archives, and never
   pruned. Only full archives satisfy the schedule. Lists and the panel
   distinguish full restore points from partial settings archives.
4. During actual archive writes, `migrate.status()` exposes valid monotonic
   progress for both phases. Failure, restart and successful completion clear
   it; persistence failure remains best effort. Throttling uses a controlled
   clock rather than wall-time sleeps.
5. Completed-archive reuse, retirement's same-run exception, retry after a
   failed archive, marker ordering and ordinary/image backup behavior continue
   passing their existing tests.
6. Frontend tests cover both presentation forms and polling from progress to
   completion on the page already open, without overwriting a newer save.
   Include the first-read race: pause retirement planning before its archive,
   serve format 2 with `done`, `active: true` and no progress, then resume it
   and verify that polling discovers and displays the archive. Verify the
   analogous transition through the global marker, and read-only,
   nonblocking activity detection from a second process on the same root.

Run focused regressions, repeat the original fixture reproduction, then run
`make check` using the main checkout's venv through explicit `PY`. Follow the
repository's spec, plan, implementation and final spec-conformance Codex
review gates. Update `docs/store-guarantees.md`, `CLAUDE.md` and other current
user-facing full-archive claims affected by this change. Historical design
documents remain historical; this spec records the amended contract.
