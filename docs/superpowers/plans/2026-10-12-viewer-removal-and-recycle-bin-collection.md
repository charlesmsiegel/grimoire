# Viewer removal and recycle-bin collection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the shared image viewer remove the picture it shows (inline confirm, then step on), and make the image collector send unused pictures to the OS recycle bin, collectable once unlinked and an hour old.

**Architecture:** Backend: a new `store/recycle_bin.py` owns "is there a real bin here, and send these files to it" (including the Windows guards); `store/image_gc.py` drops the sighting rule, commits each blob to a per-device outbox under the ingest/GC lock and hands the outbox to `recycle_bin` after the lock. Frontend: `ImageViewer` gains an `onRemove` capability and an inline confirmation; three surfaces (combined gallery, record Art panel, World art panel) supply it by calling the API directly; `ImageStoreCard` speaks recycle bin.

**Tech Stack:** Python 3.11+, FastAPI, pytest; `Send2Trash>=2.1,<3` (desktop extra); `ctypes`/`winreg` on Windows; React + TypeScript, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-12-viewer-removal-and-recycle-bin-collection-design.md` — read it alongside this plan; section numbers below (§) refer to it.

## Global Constraints

- `Send2Trash>=2.1,<3` goes in `backend/pyproject.toml`'s `desktop` extra only; the base deps and `android/app/build.gradle.kts`'s pip block do not change.
- No picture is ever unlinked by the collector: a blob leaves the store only by `os.replace` into the outbox and then `recycle_bin`; nothing falls back to deletion.
- `GRACE_HOURS = 1`; token life stays `TOKEN_HOURS = 24`; `image_gc.FORMAT` goes from 1 to 2.
- Bin name: `grimoire - <description> - <sha8>.<ext>` / `grimoire - <sha8>.<ext>`; description cut to 60 chars at a word boundary, `<>:"/\|?*` and control chars removed, whitespace collapsed, trailing dots/spaces trimmed (§3.2).
- Outbox: `<root>/.cache/image-store/recycle/<maintenance_reports.device_key(root)>/`.
- Error text in reports is an exception's class name or a code, never its message.
- Every backend import at module scope (`test_import_guard.py`); the optional import uses the `store/tokens.py` `try: import … except Exception:` shape.
- Store writes: run `test_atomic_guard.py`, `test_paths_guard.py`; a genuinely safe `os.replace` gets `# atomic-ok: <reason>` only if the guard flags it.
- Keys go through `useHotkeys` only; no `enter` binding in the viewer (§1.3).
- Copy is exactly the spec's (§1.2 questions, §1.3 sentences, §3.5 refusal sentences, §4 card text).
- Placeholder names only in tests (Mara, Seraphine, Realm…). No real store contents anywhere.
- Lint gates are ratcheted: a fixed finding means `make baseline` and committing the smaller baseline with the fix.

## Review Focus

1. **A full Windows Recycle Bin** (common): binning must not stop as "unverified" when the count falls because the bin purged old items — test in Task 1 (`test_batch_at_capacity_is_not_unverified`).
2. **The reader removes the last picture of a filtered gallery** (e.g. "unfinished" filter on): the viewer must close plainly, not show "This image is no longer available" — test in Task 6 (`closes plainly when the last image is removed`).
3. **Delete pressed while focus is in the gallery's search box behind the viewer** — the viewer is modal and the bindings are not `whileTyping`, so typing in a text field never opens the confirmation — test in Task 6 (`Backspace in a text field does not open the confirmation`).
4. **A collection interrupted by Cancel after some blobs reached the outbox**: those files must still be binned (drained) at the next collection and must not be lost or double-counted — test in Task 3 (`test_cancel_after_outbox_move_drains_next_time`).
5. **The library sits on a network drive on Windows**: Settings must refuse collection with the drive sentence before any file moves — test in Task 4 (`test_route_refuses_when_bin_unavailable`) with `check` answering `volume_has_no_bin`.

---

### Task 1: `store/recycle_bin.py` — availability and sending

**Files:**
- Create: `backend/src/grimoire/store/recycle_bin.py`
- Modify: `backend/src/grimoire/store/proclock.py` (rename `_on_android` → public `on_android`, update its one caller)
- Modify: `backend/pyproject.toml` (desktop extra), `mypy.ini` (`[mypy-send2trash.*]` with `ignore_missing_imports = True` and `follow_imports = skip`, beside `tiktoken`'s, extending that comment)
- Modify: `backend/tests/conftest.py` (autouse fake bin)
- Modify: `backend/src/grimoire/store/locks.py` only if `test_lock_domain_guard.py` asks to classify the module (it takes no `cid`, so it should not)
- Test: `backend/tests/test_recycle_bin.py`

**Interfaces:**
- Produces:
  - `proclock.on_android() -> bool`
  - `@dataclass(frozen=True) class Availability: ok: bool; code: str | None = None; message: str | None = None` with `def to_dict(self) -> dict` → `{"ok", "code", "message"}`
  - `check(root: Path) -> Availability` — codes `android`, `not_installed`, `volume_has_no_bin`, `bin_deletes_immediately`; messages verbatim from spec §3.5
  - `@dataclass class BatchResult: sent: list[Path]; refused: Path | None = None; code: str | None = None; error: str | None = None; unverified: list[Path] = field(default_factory=list)`
  - `send_batch(root: Path, paths: list[Path]) -> BatchResult` — sends in order, stops at the first refusal (`code="refused"`, `error=type(exc).__name__`) or oversized file (`code="too_large_for_bin"`); on Windows runs the capacity pre-read and the count backstop (`unverified` lists the files sent in that batch when the count rose by fewer than `len(sent)` and the post-batch size is below capacity)
  - Test seams (module-level, monkeypatched in tests): `_send2trash: Callable[[str], None] | None` (bound from the optional import), `_platform() -> str` (`"windows"`/`"darwin"`/`"linux"`), and a `_win` namespace with `drive_type(volume: str) -> int`, `query_bin(volume: str) -> tuple[int, int] | None` (size, items), `volume_guid(volume: str) -> str | None`, `reg_dword(hive: str, path: str, name: str) -> int | None` (`hive` `"HKCU"`/`"HKLM"`)
  - Constants: `DRIVE_FIXED = 3`; `MESSAGES: dict[str, str]` (code → sentence; includes `too_large_for_bin`)

- [ ] **Step 1: Write the failing tests** in `test_recycle_bin.py`:
  - `test_android_refuses` — monkeypatch `proclock.on_android` → True; `check(root)` is `Availability(False, "android", "This device has no recycle bin. Run the collection from a computer.")`.
  - `test_not_installed_refuses` — `_send2trash=None`, not android → code `not_installed`, message "The recycle-bin helper is not installed. Re-run the installer, then try again."
  - `test_linux_and_macos_ok` — `_platform` → `"linux"` and `"darwin"`, fake `_send2trash` → `ok`.
  - Windows (`_platform` → `"windows"`, `_win` faked; volume derived from `root`'s anchor): `test_windows_network_drive_refuses` (`drive_type`→4 ⇒ `volume_has_no_bin`), `test_windows_query_fails_refuses` (`query_bin`→None ⇒ `volume_has_no_bin`), `test_windows_policy_hkcu_and_hklm` (parametrized: `NoRecycleFiles`=1 in either hive ⇒ `bin_deletes_immediately`), `test_windows_nuke_on_delete_refuses` (`NukeOnDelete`=1 under `Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{GUID}` ⇒ `bin_deletes_immediately`), `test_windows_absent_volume_key_is_ok` (all `reg_dword` → None ⇒ `ok`).
  - `send_batch`: `test_sends_in_order` (fake records paths, returns `BatchResult(sent=[a, b])`); `test_stops_at_first_refusal` (fake raises `OSError` on `b` ⇒ `sent=[a]`, `refused=b`, `code="refused"`, `error="OSError"`, `c` never sent); `test_too_large_for_bin` (Windows, `MaxCapacity` dword in MB = 1, a 2 MB file ⇒ `refused`=that file, `code="too_large_for_bin"`, not sent); `test_unverified_when_count_does_not_rise` (Windows, `query_bin` before `(10, 5)` after `(10, 5)`, capacity unknown or above size ⇒ `unverified == sent`); `test_batch_at_capacity_is_not_unverified` (post size ≥ capacity ⇒ `unverified == []`).
- [ ] **Step 2:** Run `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_recycle_bin.py -q` — expect ImportError/FAIL.
- [ ] **Step 3: Implement** `recycle_bin.py` with the interfaces above. Module docstring: why the Windows guards exist (Send2Trash 2.1 flags lack `FOF_WANTNUKEWARNING`; a volume with no bin deletes permanently and reports success), what an absent per-volume key means, that the backstop says *unverified*. `_win` real functions: `ctypes.windll.kernel32.GetDriveTypeW`, `shell32.SHQueryRecycleBinW` with a `SHQUERYRBINFO` struct (`cbSize`, `i64Size`, `i64NumItems`), `kernel32.GetVolumeNameForVolumeMountPointW` (strip `\\?\Volume` → `{GUID}`), `winreg.OpenKey`/`QueryValueEx` — each returning None on any `OSError`; imported/used only when `_platform()=="windows"` (`ctypes` is stdlib; reference `ctypes.windll` lazily inside the functions so other platforms import cleanly). `MaxCapacity` is MB.
- [ ] **Step 4:** `proclock.on_android` rename; `pyproject.toml` desktop gains `"Send2Trash>=2.1,<3",`; `mypy.ini` entry.
- [ ] **Step 5:** `conftest.py`: an autouse fixture that sets `recycle_bin._send2trash` to a fake which moves the file into `tmp_path_factory.mktemp("bin")` and records it on a `FakeBin` object (`.paths: list[Path]`), forces `_platform` → `"linux"`, and patches `proclock.on_android` → False; exposes it as fixture `fake_bin`. Tests about availability override these.
- [ ] **Step 6:** Run `make check-py` subset: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_recycle_bin.py tests/test_import_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_lock_domain_guard.py tests/test_proclock*.py -q` — PASS.
- [ ] **Step 7:** Commit `feat(store): recycle_bin — where a real bin exists, and sending to it`.

### Task 2: Collector timing — one hour, no sightings, scan agrees with collect

**Files:**
- Modify: `backend/src/grimoire/store/image_gc.py`
- Modify: `backend/tests/test_image_gc.py`

**Interfaces:**
- Consumes: nothing from Task 1 yet.
- Produces:
  - `GRACE_HOURS = 1`; `FORMAT = 2`; removed: `GRACE_DAYS_DEFAULT`, `GRACE_DAYS_MIN`, `SCAN_GAP_HOURS`, `sightings_path`, `_load_sightings`, `_save_sightings`, `_check_grace`.
  - `scan(root, *, cancel, run_id=None, now=None) -> dict` (no `grace_days`).
  - `_judge(now: float, grace: float, mtimes: list[float]) -> _Verdict` with `_Verdict(why: str | None, at: float | None, skew: str | None)`; `why` ∈ `None`, `"grace"`, `"clock-skew"`.
  - Report: `grace_hours` replaces `grace_days` (from `_new_report`); token record carries `grace_hours`; `_read_token` requires `format == 2` and a numeric `grace_hours`.
  - Protected object rows for held blobs: `{"id", "blob", "why": "unreadable-sidecars" | "unarrived-objects", "collectable_at": None}`, excluded from `collectable` and the token.

- [ ] **Step 1: Rewrite the timing tests.** Delete `test_collectable_only_after_grace_old_first_sighting_and_a_second_scan`, `test_the_grace_period_has_a_floor`, `test_a_reachable_sighting_resets_the_clock`, `test_a_future_sighting_is_flagged_and_restarts`. Change `_ready(root)` to a single `_scan(root, now=T)` and `OLD = T - 2 * 3600`, `_collect` default `now=T + 60`. Add:
  - `test_collectable_on_the_first_scan_after_an_hour` — `_obj(1, mtime=T - 3601)` ⇒ in `collectable`, `token` set.
  - `test_younger_than_an_hour_is_protected_with_a_time` — `_obj(1, mtime=T - 600)` ⇒ protected `why == "grace"`, `collectable_at == approx(T - 600 + 3600)`, no token.
  - `test_no_sightings_file_is_written` — after a scan, `.cache/image-store/gc/` holds no `<device key>.json` (tokens dir may exist).
  - `test_an_older_format_token_is_refused` — rewrite a token file's `format` to 1 ⇒ collect `state == "refused"`.
  - `test_held_objects_are_protected_not_offered` — an unreadable sidecar present (reuse `test_an_unreadable_sidecar_keeps_every_blob`'s setup) ⇒ an otherwise collectable object is in `protected` with `why == "unreadable-sidecars"` and absent from the token; likewise an unarrived placement ⇒ `"unarrived-objects"`.
  Adjust the rest of the file for the new `_ready`/`now` (e.g. `test_recent_orphans_are_grace_protected` becomes the one-hour version; `test_future_mtimes_are_never_collectable` keeps its meaning).
- [ ] **Step 2:** Run `pytest tests/test_image_gc.py -q` — the new tests FAIL.
- [ ] **Step 3: Implement** in `image_gc.py`: the constants above; `_judge` on mtimes only (`at = max(m + grace)`); `_classify`/`_classify_objects`/`_classify_blobs` lose the sightings parameters; `_classify_objects` takes `hold: str | None` (`_orphan_hold(side, report["unarrived_objects"])`) and protects every otherwise-collectable object when it is set; `_reclaimable`'s `keep_all` stays; `_Run.grace` is `GRACE_HOURS * 3600`; `_collect` reads `rec["grace_hours"]`. `_scan` no longer loads/saves sightings. Update the module docstring's **Candidates** paragraph to §2.1.
- [ ] **Step 4:** Run `pytest tests/test_image_gc.py tests/test_maintenance_runs.py tests/test_maintenance_routes.py -q` — PASS (fix any `grace_days` reference in those).
- [ ] **Step 5:** Commit `feat(image-gc): collectable after an hour; drop the sighting rule`.

### Task 3: Collector disposal — outbox under the lock, bin after it

**Files:**
- Modify: `backend/src/grimoire/store/image_gc.py`
- Modify: `backend/tests/test_image_gc.py`

**Interfaces:**
- Consumes: `recycle_bin.check`, `recycle_bin.send_batch`, `BatchResult`, `Availability` (Task 1); `fake_bin` fixture.
- Produces (report fields, read by Tasks 4 and 9):
  - `recycled: [{"id": str | None, "blob": str, "name": str, "bytes": int}]` — appended when the bin accepted the file.
  - `released: [{"id": str, "blob": str, "reason": "shared" | "blob-missing"}]`.
  - `outbox: [str]` — this device's waiting file names (scan and collect end state).
  - `foreign_outboxes: int` — files in other `recycle/*` dirs.
  - `unverified: [str]` — bin names Windows did not confirm.
  - `recycle_bin: {"ok", "code", "message"}` — on every report (scan and collect).
  - `deleted: {"objects": [...sidecar rows], "bytes": int}` — `deleted.blobs` is removed.
  - New states: `TRASH_UNAVAILABLE = "trash-unavailable"`, `RECYCLE_BIN_UNVERIFIED = "recycle-bin-unverified"`; `REFUSED` with `error == "no_recycle_bin"` when `check` is not ok.
  - `outbox_dir(root: Path) -> Path` (this device's outbox); `bin_name(description: str | None, sha: str, ext: str) -> str`.

- [ ] **Step 1: Write the failing tests** (all with `fake_bin`):
  - `test_bin_name` — parametrized: `("Mara at the harbour", sha, "png") → "grimoire - Mara at the harbour - <sha[:8]>.png"`; `None`/`""` → `"grimoire - <sha8>.png"`; `'a/b:c*d?"e<f>g|h\\i'` stripped; a 100-char description cut ≤ 60 at a word boundary; trailing `". "` trimmed; only-invalid chars → no-description form.
  - `test_a_collected_blob_goes_to_the_bin_named_and_its_rest_is_deleted` — object with `raw["description"]` set (write it via the existing description API or sidecar fixture) ⇒ `fake_bin.paths` has one file with the bin name and the blob's bytes; sidecar, index entry, thumbnails gone; `report["recycled"][0]["name"]` matches; `deleted.objects` has the sidecar.
  - `test_description_conflicts_bins_without_a_description`.
  - `test_shared_blob_is_released_not_recycled` (adapt `test_a_shared_blob_survives_its_unreachable_object`): sidecar gone, blob stays, `released == [{..., "reason": "shared"}]`, `fake_bin.paths == []`.
  - `test_held_object_keeps_its_sidecar_at_collect` — make the hold arise between scan and collect ⇒ `skipped` reason the hold, sidecar still present.
  - `test_a_missing_blob_releases_the_sidecar` — delete the blob after the scan ⇒ sidecar deleted, `released` reason `"blob-missing"`, state complete.
  - `test_bin_runs_after_the_ingest_lock_is_released` — fake asserts `locks.image_ingest_gc_lock()` can be acquired non-blocking from another thread at call time (mirror `test_deletion_is_batched_and_releases_the_ingest_lock`).
  - `test_a_refused_bin_leaves_the_file_in_the_outbox` — fake raises `OSError` ⇒ state `trash-unavailable`, `error == "OSError"`, the readable-named file in `outbox_dir(root)`, `report["outbox"]` names it; the next scan reports it in `outbox` and issues a token with nothing else collectable; the next collect (fake now accepting) bins it first.
  - `test_drain_refusal_stops_before_the_first_batch` — outbox holds a file, fake refuses ⇒ no object of the new token touched.
  - `test_cancel_after_outbox_move_drains_next_time` — cancel after the first object's move (cancel callable flips after `os.replace`) ⇒ file in outbox, not in `recycled`; next collection bins it once.
  - `test_another_devices_outbox_is_counted_not_touched` — a file in `recycle/<other 64-hex>/` ⇒ `foreign_outboxes == 1`, file untouched after collect.
  - `test_collect_refuses_without_a_bin` — patch `recycle_bin.check` → not ok ⇒ `state == "refused"`, `error == "no_recycle_bin"`, token not consumed (file still present), `recycle_bin` on the report; a scan reports `recycle_bin.ok == False`.
  - `test_unverified_stops_the_run` — `send_batch` patched to return `unverified=[p]` ⇒ state `recycle-bin-unverified`, `unverified` names it.
  - Rewrite `test_a_concurrent_ingest_is_never_left_dangling` and `test_a_root_flip_between_sidecar_and_blob_reports_the_sidecar` for the new order (blob → outbox, then sidecar): a concurrent ingest of the same id waits on the lock and recreates the object with a fresh blob; a root flip after the move stops with the move reported (`outbox`), sidecar intact. Update `test_nothing_outside_image_store_is_deleted` to allow the outbox tree and assert nothing left `outside/`. Remove `deleted.blobs` assertions.
  - Run all of `test_image_gc.py`.
- [ ] **Step 2:** Run — new tests FAIL.
- [ ] **Step 3: Implement.**
  - `_DELETABLE` gains `(".cache", "image-store", "recycle")`; a `_move_to_outbox(root, blob: Path, name: str) -> Path` that applies `_delete_file`'s checks (live root, under `_DELETABLE`, no links, regular file) to both ends and `os.replace`s (`FileNotFoundError` → returns None, the caller releases).
  - `_collect_object`: hold ⇒ `run.skip(id, key, hold)` and return (sidecar kept); `shared` ⇒ delete sidecar, `released`; else read `obj.raw.get("description")` unless `description_conflicts` is present, `_move_to_outbox` (None ⇒ delete sidecar, `released` `"blob-missing"`), delete sidecar, then index entry and thumbnails; remember the outbox path on `run.pending`.
  - `_collect_blob`: same move, no description.
  - `_batches`: after each `with locks.image_ingest_gc_lock()` block, `_flush(run)` — `recycle_bin.send_batch(root, run.pending)`; accepted ⇒ `recycled` rows; `refused` ⇒ return `TRASH_UNAVAILABLE` with `error = result.error or result.code`; `unverified` ⇒ `RECYCLE_BIN_UNVERIFIED`. Flush also on cancel and before returning any state after a move.
  - `_collect`: `availability = recycle_bin.check(root)`; not ok ⇒ `REFUSED`, `error="no_recycle_bin"` **before** `_claim_token`. After claiming: drain `sorted(outbox_dir(root).iterdir())` regular files through `send_batch`; a stop returns before the walk.
  - Every report: `recycle_bin = check(root).to_dict()`, `outbox`, `foreign_outboxes`. `_issue_token` issues when `outbox` is non-empty too.
  - Module docstring **Deleting** → **Disposal** per §3; the "never a sidecar naming nothing" comment in `image_store.py` (≈ line 627) and the `store/assets.py` docstring that says "grace period and two scans apart" updated.
- [ ] **Step 4:** Run `pytest tests/test_image_gc.py tests/test_recycle_bin.py tests/test_atomic_guard.py tests/test_paths_guard.py -q` — PASS.
- [ ] **Step 5:** Commit `feat(image-gc): send collected images to the recycle bin through a device outbox`.

### Task 4: Routes — refuse without a bin; library rows carry provenance

**Files:**
- Modify: `backend/src/grimoire/routes/maintenance.py`
- Modify: `backend/src/grimoire/routes/characters.py` (`_campaign_library_rows`, and the world-library gallery rows in the same module)
- Test: `backend/tests/test_maintenance_routes.py`, the gallery route tests (`grep -l "listWorldImages\|/images?gallery\|_campaign_library_rows" backend/tests` — extend the file that covers the campaign gallery)

**Interfaces:**
- Consumes: `recycle_bin.check` (Task 1).
- Produces: `POST /maintenance/images/gc` `{"dry_run": false}` ⇒ 409 `{"detail": {"kind": "no_recycle_bin", "code": <code>, "message": <sentence>}}` before reservation (same shape the route's other structured refusals use; follow it). Gallery rows: campaign library `provenance: "campaign" | "inherited"`; world library `provenance: "world"`.

- [ ] **Step 1: Failing tests:** `test_route_refuses_when_bin_unavailable` — patch `recycle_bin.check` → `Availability(False, "volume_has_no_bin", "…")`; POST real run ⇒ 409, `kind == "no_recycle_bin"`, `code == "volume_has_no_bin"`, and no run listed under `/api/global/runs` (or the route's run listing); a dry run still answers 202. Gallery: a campaign gallery's own library row has `provenance == "campaign"`, an inherited one `"inherited"`; a world gallery's library row `"world"`.
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3: Implement** the refusal next to the missing-token 400 in `post_images_gc`; add `provenance` to both row builders.
- [ ] **Step 4:** Run the two test files — PASS.
- [ ] **Step 5:** Commit `feat(routes): refuse a collection without a recycle bin; library rows say whose they are`.

### Task 5: Documentation

**Files:**
- Modify: `docs/store-guarantees.md` (The image store → Garbage collection; "Nothing is collected unless someone asks"; the "does not promise" list item), `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md` (dated amendment under §12), `docs/superpowers/specs/2026-10-11-unified-image-surfaces-design.md` (§5.1/§5.2 amendment per spec §1.4)
- Test: `backend/tests/test_docs_guard.py` (extend `test_store_guarantees_names_the_maintenance_modules` with `"recycle_bin"`)

- [ ] **Step 1:** Add `"recycle_bin"` to that test's tuple; run it — FAIL.
- [ ] **Step 2:** Rewrite the Garbage collection section per spec §2–§3.6: candidate bullet (an hour, no sightings), held objects protected, disposal bullet (outbox, bin after the lock, blob-before-sidecar for recycled objects and why it is safe), the Windows guards and the *unverified* backstop as the residual, the accepted sync risk replacing the offline-device bound, cite the renamed tests by their new names. Update the two other paragraphs. Add the two spec amendments.
- [ ] **Step 3:** Run `pytest tests/test_docs_guard.py -q` — PASS (every cited test name must exist).
- [ ] **Step 4:** Commit `docs: the collector recycles, after an hour`.

### Task 6: `ImageViewer` — Remove with inline confirmation

**Files:**
- Modify: `frontend/src/components/art/types.ts`, `frontend/src/components/art/ImageViewer.tsx`
- Test: `frontend/src/components/art/ImageViewer.test.tsx`

**Interfaces:**
- Produces:
  - `ImageDescriptor.removal?: { question: string }`
  - `export type RemoveOutcome = "removed" | "changed";`
  - `ImageViewerProps.onRemove?: (key: string) => Promise<RemoveOutcome>`
  - Exported copy: `CHANGED_NOT_REMOVED = "This picture changed since it was loaded, so it was not removed."`

- [ ] **Step 1: Failing tests** (names are the `it(...)` strings):
  - `draws Remove only with onRemove and a removal` (three cases).
  - `Remove opens the confirmation with focus on its Remove button` — question text visible; `document.activeElement` is the confirm button (`name: "Remove"` inside the confirmation group, `role="group"` `aria-label="Confirm removal"`).
  - `Cancel and Escape cancel without closing` — `onClose` not called; second Escape closes.
  - `Delete and Backspace open the confirmation`; `Backspace in a text field does not open the confirmation` (an `<input>` inside the document, focused).
  - `Enter on a focused Cancel does not remove` — focus Cancel, `fireEvent.keyDown(cancel, {key: "Enter"})` + click semantics ⇒ `onRemove` not called.
  - `arrows do nothing while the confirmation is open`.
  - `steps to the next image after removal`; `steps to the previous when the last is removed`; `closes plainly when the last image is removed` — `onClose` called with no reason; rerender with the removed key dropped from `images` **before** resolving the promise and **after** it: no `"unavailable"` close either way.
  - `"changed" stays and shows the sentence`; `a rejection stays and shows its message`; `controls are disabled while pending` (Previous/Next/Remove disabled, "Removing…").
- [ ] **Step 2:** `cd frontend && npx vitest run src/components/art/ImageViewer.test.tsx` — FAIL.
- [ ] **Step 3: Implement.** State in `ImageViewer`: `confirming: boolean`, `pending: boolean`, `message: string | null`, `removed: useRef<Set<string>>`. The unavailable effect skips when `removed.current.has(key)`; a removal adds the key, then moves selection (next, else previous, else `onClose()`); an effect also handles a sequence that lost the removed key by picking the neighbour at the old index. Bindings: `delete`/`backspace` (label "Remove image", group `IMAGE VIEWER`, `enabled: canRemove && !confirming && !pending`); escape: close binding `enabled: !confirming`, plus `{ keys: "escape", label: "Cancel removal", enabled: confirming && !pending, run: cancel }`; arrows also require `!confirming && !pending`. Remove/confirmation render in `art-viewer-tools` via `ActiveImage` props (`canRemove`, `confirming`, `pending`, `message`, handlers); focus the confirm button with a ref on open. Reset `confirming`/`message` when the selected key changes.
- [ ] **Step 4:** Run the file — PASS; run `npx vitest run src/shortcuts` — PASS.
- [ ] **Step 5:** Commit `feat(viewer): remove the shown image with an inline confirmation`.

### Task 7: Combined gallery supplies removal

**Files:**
- Modify: `frontend/src/api/types.ts` (`GalleryImage.provenance` already typed — confirm library rows can carry `"campaign" | "inherited" | "world"`), `frontend/src/api/client.ts` (`removeRecordArt` parameter narrowed to `Pick<RecordArtImage, "name" | "v" | "placement">`; same narrowing on the adapter's `remove` and `checkImage`), `frontend/src/components/art/galleryState.ts`, `frontend/src/components/ImagesView.tsx`
- Test: `frontend/src/components/art/galleryState.test.ts`, `frontend/src/components/ImagesView.test.tsx`

**Interfaces:**
- Consumes: Task 6 types; Task 4 row `provenance`.
- Produces: `galleryRemovalQuestion(image: GalleryImage, scope: EntityScope, label: string): string | null` in `galleryState.ts` (null ⇒ not removable); `galleryImageDescriptor` sets `removal` from it. `removeGalleryImage(image: GalleryImage, scope: EntityScope, wid: string): Promise<RemoveOutcome>` in `ImagesView.tsx` (or a small `galleryRemove.ts` beside it).

- [ ] **Step 1: Failing tests:**
  - `galleryState.test.ts`: the five §1.2 questions verbatim (world record row; campaign record row; campaign library inherited; campaign library own; world library), and null for greetings, `world-original`, and a record row without `v`.
  - `ImagesView.test.tsx`: removing a record row calls `api.removeRecordArt` with the row's target and `{name, v, placement}` and the viewer moves on; a library row calls `deleteWorldImage` / `deleteCampaignImage`; a 409 `stale_image` re-reads (`listWorldImages(wid, true)`) and resolves `"removed"` when the row is gone, `"changed"` when present; `ApiError(409, "image_in_collection")` rejects with "This picture is part of a collection, so it can't be deleted from the library."; any other error rejects with its detail.
- [ ] **Step 2:** `npx vitest run src/components/art/galleryState.test.ts src/components/ImagesView.test.tsx` — FAIL.
- [ ] **Step 3: Implement**; pass `onRemove={(key) => removeGalleryImage(rowByKey(key), scope, wid)}` to the gallery's `<ImageViewer>` only (not the World/campaign-library tabs, which have their own surfaces). After a successful remove, call the existing `load(true)` without awaiting it in the resolve path.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `feat(gallery): remove from the viewer`.

### Task 8: Record Art panel and World art panel supply removal

**Files:**
- Modify: `frontend/src/components/art/useRecordArt.ts`, `frontend/src/components/art/RecordArtEntry.tsx`, `frontend/src/components/WorldArtPanel.tsx`
- Test: `frontend/src/components/art/useRecordArt.test.ts`, `frontend/src/components/art/RecordArtPanel.test.tsx`, `frontend/src/components/WorldArtPanel.test.tsx`

**Interfaces:**
- Consumes: Task 6 types; `CHANGED_NOT_REMOVED` not needed here.
- Produces: `RecordArtController.removeFromViewer(key: string): Promise<RemoveOutcome>`; `describe()` sets `removal` (Art panel question per §1.2: world scope "Remove <record>'s <role>?", campaign scope the hide sentence) when `operations` include `"remove"` and the image is not a world original; `EDIT_FIRST = "Finish or cancel the edit in the Art panel first."`; `IN_COLLECTION = "This picture is part of a collection, so it can't be deleted from the library."` (exported from one place — `art/types.ts` — and reused by Task 7).

- [ ] **Step 1: Failing tests:**
  - `useRecordArt.test.ts`: `removeFromViewer` rejects with `EDIT_FIRST` and sends nothing while a draft is held or a mutation is pending; resolves `"removed"` before the refresh completes (refresh mock held on a deferred); on `stale_image` awaits `refresh(true)` and resolves by presence; rejects with the API's detail otherwise; holds `pending` for the call.
  - `RecordArtPanel.test.tsx`: the viewer opened from the panel shows Remove for a placement and not for a world original.
  - `WorldArtPanel.test.tsx`: the viewer's Remove calls `api.deleteWorldImage` with no `window.confirm`; `image_in_collection` shows `IN_COLLECTION`; the panel's ✕ path is unchanged.
- [ ] **Step 2:** Run the three files — FAIL.
- [ ] **Step 3: Implement.** `removeFromViewer` takes `pending.current.value` itself (same flag `mutation` uses), calls `adapter.remove(image)` directly, releases in `finally`, then starts `refresh(true)` / `afterSaved()` without awaiting on success. `RecordArtEntry` passes `onRemove={controller.removeFromViewer}`. `WorldArtPanel` passes an `onRemove` that sets `busy`, calls `api.deleteWorldImage(wid, name)`, then `void load(true)`.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `feat(art): remove from the viewer on the Art panel and World art`.

### Task 9: Settings card speaks recycle bin

**Files:**
- Modify: `frontend/src/api/types.ts` (`ImageGcReport`: `grace_hours?: number | null`, keep optional `grace_days`; states gain `"trash-unavailable" | "recycle-bin-unverified"`; `recycled`, `released`, `outbox`, `foreign_outboxes`, `unverified`, `recycle_bin: { ok: boolean; code: string | null; message: string | null }` — all optional for older reports; `deleted.blobs` optional), `frontend/src/api/client.ts` (surface a 409 `no_recycle_bin` body), `frontend/src/components/ImageStoreCard.tsx`
- Test: `frontend/src/components/ImageStoreCard.test.tsx`

- [ ] **Step 1: Failing tests:** button reads "Send 3 unused images to the recycle bin" (2 collectable + 1 outbox); its confirm text carries the three facts of §4; `recycle_bin.ok == false` hides the button and shows `message`; a 409 `no_recycle_bin` on start shows its `message`; a too-new row shows a time (`toLocaleTimeString`), not a date; waiting and foreign outbox files are listed; collect report "Sent 2 images to the recycle bin (…). The space is freed when you empty the bin." and "Their names start with grimoire - ."; `trash-unavailable` and `recycle-bin-unverified` stop sentences per §4; an older report with `grace_days` and `deleted.blobs` and no `recycled` still renders (as "Deleted N unused images").
- [ ] **Step 2:** `npx vitest run src/components/ImageStoreCard.test.tsx` — FAIL.
- [ ] **Step 3: Implement**; remove the "two scans a grace period apart" copy.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `feat(settings): collection sends to the recycle bin`.

### Task 10: Gate

- [ ] **Step 1:** `make check` from the repo root (backend venv per `CONTRIBUTING.md`; in this container, `make check-py check-lint check-mypy check-web check-eslint check-templates` if `check-pydantic1` cannot build its venv — say so). Expected: all green.
- [ ] **Step 2:** If a ratchet reports an improvement, `make baseline` and commit the smaller baselines.
- [ ] **Step 3:** Stand-in diff review (user-approved substitute for `/codex:review`), then the final review against the spec (substitute for the final `/codex:adversarial-review`); fix findings; commit.
- [ ] **Step 4:** Push; update PR charlesmsiegel/grimoire#508's description.
