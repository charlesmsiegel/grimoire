# Viewer removal and recycle-bin collection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the shared image viewer remove the picture it shows (inline confirm, then step on), and make the image collector send unused pictures to the OS recycle bin, collectable once unlinked and an hour old.

**Architecture:** Backend: a new `store/recycle_bin.py` owns "is there a real bin here, and send these files to it" (including the Windows guards); `store/image_gc.py` drops the sighting rule, commits each blob to a per-device outbox under the ingest/GC lock and hands the outbox to `recycle_bin` after the lock. Frontend: `ImageViewer` gains an `onRemove` capability and an inline confirmation; three surfaces (combined gallery, record Art panel, World art panel) supply it by calling the API directly; `ImageStoreCard` speaks recycle bin.

**Tech Stack:** Python 3.11+, FastAPI, pytest; `Send2Trash>=2.1,<3` (desktop extra); `ctypes`/`winreg` on Windows; React + TypeScript, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-12-viewer-removal-and-recycle-bin-collection-design.md` — read it alongside this plan; section numbers below (§) refer to it.

## Global Constraints

- `Send2Trash>=2.1,<3` goes in `backend/pyproject.toml`'s `desktop` extra only; the base deps and `android/app/build.gradle.kts`'s pip block do not change.
- No picture is ever unlinked by the collector: a blob leaves the store only by `os.replace` into the outbox and then `recycle_bin`; nothing falls back to deletion. The outbox is **not** in `_DELETABLE`, so `_delete_file` can never unlink a waiting picture.
- `GRACE_HOURS = 1`; token life stays `TOKEN_HOURS = 24`; `image_gc.FORMAT` goes from 1 to 2.
- Bin name: `grimoire - <description> - <sha8>.<ext>` / `grimoire - <sha8>.<ext>`; description cut to 60 chars at a word boundary, `<>:"/\|?*` and control chars removed, whitespace collapsed, trailing dots/spaces trimmed (§3.2). If that name already exists in the outbox, ` (2)`, ` (3)`… goes before the extension.
- Outbox: `<root>/.cache/image-store/recycle/<maintenance_reports.device_key(root)>/`.
- Error text in reports is an exception's class name or a code, never its message.
- Structured HTTP refusals use the app's shape: `HTTPException(status, detail={"kind": …, "detail": <sentence>, …})` — `main.py`'s handler returns a dict `detail` as the whole body, and `ApiError` reads `kind`/`detail` from it.
- Every backend import at module scope (`test_import_guard.py`); the optional import uses the `store/tokens.py` `try: import … except Exception:` shape; Windows-only imports sit in a module-scope `if sys.platform == "win32":` block (the `store/proclock.py` pattern), so mypy checks one branch per platform.
- Every new `store/` module is imported by `store/__init__.py` and listed in `backend/tests/store_api_baseline.json` `"dir"`.
- A test name cited in `docs/store-guarantees.md` is kept, or the citation changes in the same commit; every backend task runs `tests/test_docs_guard.py`.
- Keys go through `useHotkeys` only; no `enter` binding in the viewer (§1.3).
- Copy is exactly the spec's and this plan's (§1.2 questions as pinned in Task 6, §1.3 sentences, §3.5 refusal sentences, §4 card text).
- Placeholder names only in tests (Mara, Seraphine, Realm…). No real store contents anywhere.
- Lint gates are ratcheted: a fixed finding means `make baseline` and committing the smaller baseline with the fix.

## Review Focus

1. **A full Windows Recycle Bin** (common): binning must not stop as "unverified" when the bin purged old items to fit — test in Task 1 (`test_batch_that_may_have_purged_is_not_unverified`).
2. **The reader removes the last picture of a filtered gallery** (e.g. "unfinished" filter on): the viewer closes plainly, not with "This image is no longer available" — test in Task 6 (`closes plainly when the last image is removed`).
3. **Delete pressed while focus is in a text field** — the bindings are not `whileTyping`, so typing never opens the confirmation — test in Task 6 (`Backspace in a text field does not open the confirmation`).
4. **A collection cancelled after some blobs reached the outbox** (including lifespan shutdown, which sets every maintenance run's cancel flag): those files stay in the outbox, are not binned during the cancelled run, and are binned once by the next collection — test in Task 3 (`test_cancel_after_outbox_move_drains_next_time`).
5. **The library sits on a network drive, or on a removable drive mounted into a folder of `C:`** on Windows: collection is refused before any file moves — tests in Task 1 (`test_windows_volume_is_the_outboxs_mount`) and Task 4 (`test_route_refuses_when_bin_unavailable`).

---

### Task 1: `store/recycle_bin.py` — availability and sending

**Files:**
- Create: `backend/src/grimoire/store/recycle_bin.py`
- Modify: `backend/src/grimoire/store/__init__.py` (import list), `backend/tests/store_api_baseline.json` (`"dir"` gains `"recycle_bin"`)
- Modify: `backend/src/grimoire/store/proclock.py` (rename `_on_android` → public `on_android`; its caller in `proclock.py` and `backend/tests/test_proclock.py:230`'s `monkeypatch.setattr(proclock, "_on_android", …)`)
- Modify: `backend/pyproject.toml` (desktop extra), `mypy.ini` (`[mypy-send2trash.*]` with `ignore_missing_imports = True` and `follow_imports = skip`, beside `tiktoken`'s, extending that comment)
- Modify: `backend/tests/conftest.py` (autouse fake bin)
- Test: `backend/tests/test_recycle_bin.py`

**Interfaces:**
- Produces:
  - `proclock.on_android() -> bool`
  - `@dataclass(frozen=True) class Availability: ok: bool; code: str | None = None; message: str | None = None` with `def to_dict(self) -> dict` → `{"ok", "code", "message"}`
  - `MESSAGES: dict[str, str]` — `android`: "This device has no recycle bin. Run the collection from a computer."; `not_installed`: "The recycle-bin helper is not installed. Re-run the installer, then try again."; `volume_has_no_bin`: "This library's drive has no Recycle Bin (a network, mapped or removable drive), so pictures can't be sent to it."; `bin_deletes_immediately`: "Windows is set to delete files immediately instead of using the Recycle Bin on this drive."; `too_large_for_bin`: "This picture is larger than the Recycle Bin on its drive can hold."
  - `check(root: Path) -> Availability` — judged for the outbox's volume: the deepest *existing* ancestor of `root / ".cache" / "image-store" / "recycle"` (the root always exists), creating nothing; `volume_of` returning `None` ⇒ `volume_has_no_bin`.
  - `@dataclass class BatchResult: sent: list[Path]; refused: Path | None = None; code: str | None = None; error: str | None = None; unverified: list[Path] = field(default_factory=list)`
  - `send_batch(root: Path, paths: list[Path], before: Callable[[Path], None]) -> BatchResult` — for each path in order: `before(path)` (raises to stop the run; propagates), then the bin call; stops at the first refusal (`code="refused"`, `error=type(exc).__name__`) or, on Windows, an oversized file (`code="too_large_for_bin"`, not sent).
  - **Windows capacity rule:** `capacity` is `MaxCapacity` MB ×1 MiB from `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{GUID}`; when that key or value is absent, `capacity = 10%` of the volume's total bytes (`GetDiskFreeSpaceExW`) — at or above Windows' default (≈10% of the first 40 GB plus 5% beyond), so an error over-estimates capacity and errs toward stopping as unverified rather than missing a permanent delete. Before the batch: `pre = query_bin(volume)`. A file larger than a *known* `MaxCapacity` is `too_large_for_bin`. After the batch: `post = query_bin(volume)`; `unverified = sent` when `post.items - pre.items < len(sent)` **and** `pre.size + bytes_sent <= capacity` (no purge could have happened); otherwise `[]`.
  - Seams (module level, monkeypatched in tests): `_send2trash: Callable[[str], None] | None` (from the optional import), `_platform() -> str` (`"windows"`/`"darwin"`/`"linux"`), and `_win`, a namespace of `volume_of(path: Path) -> str | None` (`GetVolumePathNameW`), `drive_type(volume) -> int`, `query_bin(volume) -> tuple[int, int] | None` (size, items), `volume_guid(volume) -> str | None`, `volume_bytes(volume) -> int | None`, `reg_dword(hive: str, path: str, name: str) -> int | None` (`hive` `"HKCU"`/`"HKLM"`). Real implementations live in an `if sys.platform == "win32":` block (`ctypes.windll`, `winreg` imported there); the `else` branch defines the same names returning `None`.
  - `DRIVE_FIXED = 3`.
- Produces for every other suite: conftest fixture `fake_bin` (autouse) with `.paths: list[Path]` — sets `recycle_bin._send2trash` to a fake that moves the file into a lazily created bin directory and records it, `_platform` → `"linux"`, and `recycle_bin.check` → `Availability(True)`. It does **not** patch `proclock.on_android`.

- [ ] **Step 1: Write the failing tests** in `test_recycle_bin.py` (each restores the real `check` / `_platform` it needs):
  - `test_android_refuses` — `proclock.on_android` → True ⇒ `Availability(False, "android", MESSAGES["android"])`.
  - `test_not_installed_refuses` — `_send2trash=None`, not android ⇒ `not_installed`.
  - `test_linux_and_macos_ok` — `_platform` → `"linux"`, `"darwin"`, fake `_send2trash` ⇒ `ok`.
  - Windows (`_platform` → `"windows"`, `_win` faked): `test_windows_volume_is_the_outboxs_mount` (`volume_of` returns `"C:\\mnt\\usb\\"` for the outbox dir; `drive_type` asserts it was asked that volume and returns 2 ⇒ `volume_has_no_bin`); `test_windows_network_drive_refuses` (4 ⇒ `volume_has_no_bin`); `test_windows_query_fails_refuses` (`query_bin` → None ⇒ `volume_has_no_bin`); `test_windows_policy_hkcu_and_hklm` (parametrized hive, `NoRecycleFiles`=1 ⇒ `bin_deletes_immediately`); `test_windows_nuke_on_delete_refuses`; `test_windows_absent_volume_key_is_ok`; `test_windows_fresh_store_uses_an_existing_ancestor` (no `.cache` yet ⇒ `volume_of` is asked about `root`); `test_windows_unknown_volume_refuses` (`volume_of` → None ⇒ `volume_has_no_bin`).
  - `send_batch`: `test_sends_in_order_calling_before_each` (`before` records; order a, b); `test_before_raising_stops_and_propagates` (raises on b ⇒ exception out, b not sent); `test_stops_at_first_refusal` (fake raises `OSError` on b ⇒ `sent=[a]`, `refused=b`, `code="refused"`, `error="OSError"`, c never sent); `test_too_large_for_bin` (Windows, `MaxCapacity`=1, a 2 MiB file ⇒ refused, not sent); `test_unverified_when_count_does_not_rise` (pre `(0, 5)`, post `(0, 5)`, capacity 100 MiB, 1 KiB sent ⇒ `unverified == sent`); `test_batch_that_may_have_purged_is_not_unverified` (pre size 99.9 MiB of 100, post count unchanged ⇒ `[]`); `test_unknown_capacity_uses_ten_percent_of_the_volume` (no `MaxCapacity`, `volume_bytes` 1 GiB, pre size 110 MiB ⇒ purge possible ⇒ `[]`; pre size 1 MiB ⇒ `unverified`).
- [ ] **Step 2:** Run `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_recycle_bin.py -q` — FAIL (no module).
- [ ] **Step 3: Implement** `recycle_bin.py` to the interfaces. Module docstring: why the Windows guards exist (Send2Trash 2.1's flags lack `FOF_WANTNUKEWARNING`; a volume with no bin deletes permanently and reports success), the capacity rule and its 5% estimate, that the backstop says *unverified*.
- [ ] **Step 4:** `proclock.on_android` rename (both callers); `pyproject.toml` desktop gains `"Send2Trash>=2.1,<3",`; `mypy.ini` entry; `store/__init__.py` import and baseline `"dir"` entry; the conftest fixture.
- [ ] **Step 5:** Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_recycle_bin.py tests/test_proclock.py tests/test_store_api_baseline.py tests/test_import_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_lock_domain_guard.py tests/test_docs_guard.py -q` and `make check-mypy check-lint` — PASS.
- [ ] **Step 6:** Commit `feat(store): recycle_bin — where a real bin exists, and sending to it`.

### Task 2: Collector timing — one hour, no sightings, scan agrees with collect

**Files:**
- Modify: `backend/src/grimoire/store/image_gc.py`, `backend/tests/test_image_gc.py`, `docs/store-guarantees.md` (citations only)

**Interfaces:**
- Produces:
  - `GRACE_HOURS = 1`; `FORMAT = 2`; removed: `GRACE_DAYS_DEFAULT`, `GRACE_DAYS_MIN`, `SCAN_GAP_HOURS`, `sightings_path`, `_load_sightings`, `_save_sightings`, `_check_grace`.
  - `scan(root, *, cancel, run_id=None, now=None) -> dict` (no `grace_days`).
  - `_judge(now: float, grace: float, mtimes: list[float]) -> _Verdict` with `_Verdict(why: str | None, at: float | None, skew: str | None)`; `why` ∈ `None`, `"grace"`, `"clock-skew"`.
  - Report: `grace_hours` replaces `grace_days`; the token record carries `grace_hours`; `_read_token` requires `format == 2` and `grace_hours == GRACE_HOURS`.
  - Protected object rows for held blobs: `{"id", "blob", "why": "unreadable-sidecars" | "unarrived-objects", "collectable_at": None}`, excluded from `collectable` and the token.

- [ ] **Step 1: Rewrite the timing tests.** `OLD = T - 2 * 3600`; `_ready(root)` becomes one `_scan(root, now=T)`; `_collect` defaults to `now=T + 60`. Replace every hard-coded `LATER`-based collect/scan (`test_an_ingest_that_touched_the_object_after_the_scan_is_skipped`, `test_a_token_is_single_use_device_bound_and_expires`, `test_a_root_that_is_not_the_live_one_is_refused`, and any other use) with times inside the token's 24 hours. Delete `test_collectable_only_after_grace_old_first_sighting_and_a_second_scan`, `test_the_grace_period_has_a_floor`, `test_a_reachable_sighting_resets_the_clock`, `test_a_future_sighting_is_flagged_and_restarts`; drop `sightings_path` from `test_collect_deletes_at_most_the_reported_ids`. Change for held objects: `test_an_unreadable_sidecar_keeps_every_blob` and `test_a_blob_shared_with_an_unarrived_object_is_kept` now assert the object is `protected` with its hold reason and absent from the token (no collect step). Add:
  - `test_collectable_on_the_first_scan_after_an_hour` — `_obj(1, mtime=T - 3601)` ⇒ collectable, token set.
  - `test_younger_than_an_hour_is_protected_with_a_time` — `mtime=T - 600` ⇒ `why == "grace"`, `collectable_at == approx(T + 3000)`, no token.
  - `test_no_sightings_file_is_written` — `.cache/image-store/gc/` holds no `<device key>.json` after a scan.
  - `test_an_older_format_token_is_refused` — rewrite the token's `format` to 1 ⇒ `state == "refused"`.
  - `test_held_objects_are_protected_not_offered` — unreadable-sidecar and unarrived cases, as above.
- [ ] **Step 2:** Run `pytest tests/test_image_gc.py -q` — new tests FAIL.
- [ ] **Step 3: Implement** the constants; `_judge` on mtimes only (`at = max(m + grace)`); `_classify*` lose the sightings parameters; `_classify_objects` takes `hold: str | None` (`_orphan_hold(side, report["unarrived_objects"])`) and protects every otherwise-collectable object when it is set; `_Run.grace = GRACE_HOURS * 3600`; `_collect` reads `rec["grace_hours"]`. Module docstring **Candidates** → §2.1. In `docs/store-guarantees.md` replace the citation of the deleted test with `test_collectable_on_the_first_scan_after_an_hour` and the candidate bullet's "30 days … sighted" text with §2.1's rule (the rest of the section is Task 5's).
- [ ] **Step 4:** Run `pytest tests/test_image_gc.py tests/test_maintenance_runs.py tests/test_maintenance_routes.py tests/test_docs_guard.py -q` — PASS (fix `grace_days` uses there).
- [ ] **Step 5:** Commit `feat(image-gc): collectable after an hour; drop the sighting rule`.

### Task 3: Collector disposal — outbox under the lock, bin after it

**Files:**
- Modify: `backend/src/grimoire/store/image_gc.py`, `backend/src/grimoire/store/image_store.py` (≈ line 627 comment), `backend/src/grimoire/store/assets.py` (docstring "grace period and two scans apart"), `backend/tests/test_image_gc.py`, `docs/store-guarantees.md` (citations only)

**Interfaces:**
- Consumes: `recycle_bin.check`, `recycle_bin.send_batch`, `BatchResult`, `Availability`, fixture `fake_bin` (Task 1).
- Produces (report fields; Tasks 4 and 9 read them):
  - `recycled: [{"id": str | None, "blob": str, "name": str, "bytes": int}]` — appended when the bin accepted the file.
  - `released: [{"id": str, "blob": str, "reason": "shared" | "blob-missing"}]`.
  - `outbox: [str]` — this device's waiting file names, at the end of every scan and collection.
  - `foreign_outboxes: int` — regular files in other `recycle/*` directories.
  - `unverified: [str]` — names Windows did not confirm.
  - `recycle_bin: {"ok", "code", "message"}` — on every report.
  - `deleted: {"objects": [...sidecar rows], "bytes": int}` — `deleted.blobs` removed.
  - States `TRASH_UNAVAILABLE = "trash-unavailable"`, `RECYCLE_BIN_UNVERIFIED = "recycle-bin-unverified"`; `REFUSED` with `error == "no_recycle_bin"` when `check` is not ok.
  - `outbox_dir(root: Path) -> Path`; `bin_name(description: str | None, sha: str, ext: str) -> str`.

- [ ] **Step 1: Write the failing tests** (all with `fake_bin`). Keep every test name `docs/store-guarantees.md` cites; rewrite bodies where the order changed.
  - `test_bin_name` — parametrized: `("Mara at the harbour", sha, "png") → f"grimoire - Mara at the harbour - {sha[:8]}.png"`; `None` and `""` → `f"grimoire - {sha[:8]}.png"`; `'a/b:c*d?"e<f>g|h\\i'` → characters stripped; 100 chars → ≤ 60 cut at a word boundary; trailing `". "` trimmed; only-invalid characters → the no-description form.
  - `test_a_collected_blob_goes_to_the_bin_named_and_its_rest_is_deleted` — object whose sidecar `description` is set ⇒ one `fake_bin` file with the bin name and the blob's bytes; sidecar, index entry and thumbnails gone; `recycled[0]["name"]` matches; `deleted.objects` has the sidecar.
  - `test_description_conflicts_bins_without_a_description`.
  - `test_a_shared_blob_survives_its_unreachable_object` (existing name): sidecar gone, blob stays, `released` reason `"shared"`, nothing binned.
  - `test_held_object_keeps_its_sidecar_at_collect` — hold arises between scan and collect ⇒ `skipped` with the hold reason, sidecar present.
  - `test_a_missing_blob_releases_the_sidecar` — blob deleted after the scan ⇒ sidecar deleted, thumbnails deleted, `released` reason `"blob-missing"`, state complete; `test_a_gone_blobs_thumbnails_still_go` still passes.
  - `test_bin_runs_after_the_ingest_lock_is_released` — the fake asserts `locks.image_ingest_gc_lock()` is acquirable from another thread at call time.
  - `test_a_refused_bin_leaves_the_file_in_the_outbox` — fake raises `OSError` ⇒ `trash-unavailable`, `error == "OSError"`, the readable-named file in `outbox_dir(root)`, `outbox` names it; the next scan lists it in `outbox` and issues a token with nothing else collectable; the next collect (fake accepting) bins it first.
  - `test_drain_refusal_stops_before_the_first_batch`.
  - `test_cancel_after_outbox_move_drains_next_time` — cancel flips after the first move ⇒ state cancelled, file in the outbox, `fake_bin.paths == []`; the next collection bins it exactly once.
  - `test_another_devices_outbox_is_counted_not_touched` — a file in `recycle/<other 64-hex>/` ⇒ `foreign_outboxes == 1`, untouched.
  - `test_a_symlinked_outbox_blocks` — `recycle/<device key>` is a link (skip on platforms without symlink rights) ⇒ scan and collect `blocked`, nothing sent.
  - `test_a_root_flip_before_a_bin_call_stops` — `paths.home` flips after the move ⇒ `root-changed`, the file stays in the outbox, nothing sent.
  - `test_collect_refuses_without_a_bin` — `recycle_bin.check` → not ok ⇒ `refused`, `error == "no_recycle_bin"`, token file still present, `recycle_bin` on the report; a scan reports `recycle_bin.ok == False`.
  - `test_unverified_stops_the_run` — `send_batch` returns `unverified=[p]` ⇒ `recycle-bin-unverified`, `unverified == [p.name]`.
  - `test_a_name_already_waiting_gets_a_suffix` — two orphan blobs whose names collide (fake the description) ⇒ second is `… (2).png`.
  - Existing tests with new bodies, same names: `test_a_concurrent_ingest_is_never_left_dangling` (an ingest of the same id waits on the lock and then recreates the object with a fresh blob), `test_a_root_flip_between_sidecar_and_blob_reports_the_sidecar` (a flip between the move and the sidecar delete stops with the move in `outbox`, sidecar intact), `test_nothing_outside_image_store_is_deleted` (outbox allowed, `outside/` untouched); remove `deleted.blobs` assertions everywhere.
- [ ] **Step 2:** Run — new tests FAIL.
- [ ] **Step 3: Implement.**
  - `outbox_dir`; `_store_blocks` adds `.cache/image-store/recycle` and this device's outbox as link-checked paths.
  - `_move_to_outbox(root: Path, blob: Path, name: str) -> Path | None` — `_check_root`; the blob path under `assets/image-store/blobs`, no link in its parents, a regular file; the outbox path the same checks; `mkdir` the outbox; pick a free name (suffix rule); `os.replace`. `FileNotFoundError` on the blob ⇒ `None`.
  - `_collect_object`: hold ⇒ `run.skip(id, key, hold)`, return. `others = run.side.refs.get(key, set()) - {path}`; `others` ⇒ delete sidecar, `released` `"shared"`. Else `description = None if "description_conflicts" in obj.raw else obj.raw.get("description")`; `moved = _move_to_outbox(...)`; delete sidecar; `moved is None` ⇒ `released` `"blob-missing"` else `run.pending.append(moved)`; thumbnails and index entry as today (both branches).
  - `_collect_blob`: same move, no description.
  - `_flush(run) -> str | None` — `recycle_bin.send_batch(root, run.pending, before=_bin_guard(run))` where `_bin_guard` does `_check_root` and the path checks; accepted ⇒ `recycled` rows, `run.pending` cleared; `refused` ⇒ `TRASH_UNAVAILABLE` (`error = result.error or result.code`); `unverified` ⇒ `RECYCLE_BIN_UNVERIFIED`. Called after each `with locks.image_ingest_gc_lock()` block and once before `COMPLETE`. **Not called on cancel or root change**: moved files stay in the outbox for the next drain.
  - `_collect`: `availability = recycle_bin.check(root)`; not ok ⇒ `REFUSED`, `error="no_recycle_bin"`, **before** `_claim_token`. After claiming, drain: the outbox's regular files sorted by name through `send_batch` with the same guard; a stop returns before the walk.
  - Every report: `recycle_bin`, `outbox`, `foreign_outboxes`. `_issue_token` also issues when `outbox` is non-empty.
  - Module docstring **Deleting** → **Disposal** per §3; the `image_store.py` comment and the `assets.py` docstring per spec §3.1/§5.
- [ ] **Step 4:** Run `pytest tests/test_image_gc.py tests/test_recycle_bin.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_docs_guard.py tests/test_maintenance_runs.py -q` — PASS.
- [ ] **Step 5:** Commit `feat(image-gc): send collected images to the recycle bin through a device outbox`.

### Task 4: Routes — refuse without a bin; library rows carry provenance

**Files:**
- Modify: `backend/src/grimoire/routes/maintenance.py`, `backend/src/grimoire/routes/characters.py` (`_campaign_library_rows`, and the world-library gallery rows in the same module)
- Test: `backend/tests/test_maintenance_routes.py`, and the test file that covers the campaign gallery (`grep -ln "_campaign_library_rows\|Campaign library" backend/tests`)

**Interfaces:**
- Consumes: `recycle_bin.check` (Task 1).
- Produces: `POST /maintenance/images/gc` `{"dry_run": false}` with an unavailable bin ⇒ `HTTPException(409, detail={"kind": "no_recycle_bin", "code": a.code, "detail": a.message})`, raised beside the `token_required` 400 and before reservation. Gallery rows: campaign library `provenance: "campaign" | "inherited"`; world library `provenance: "world"`.

- [ ] **Step 1: Failing tests:** `test_route_refuses_when_bin_unavailable` — `recycle_bin.check` → `Availability(False, "volume_has_no_bin", MESSAGES["volume_has_no_bin"])`; real run ⇒ 409 with `body["kind"] == "no_recycle_bin"`, `body["code"] == "volume_has_no_bin"`, `body["detail"] == MESSAGES[...]`; `GET /api/runs` lists no new run; a dry run still answers 202. Gallery: a campaign's own library row has `provenance == "campaign"`, an inherited one `"inherited"`; a world gallery's library row `"world"`.
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run both files plus `tests/test_docs_guard.py` — PASS.
- [ ] **Step 5:** Commit `feat(routes): refuse a collection without a recycle bin; library rows say whose they are`.

### Task 5: Documentation

**Files:**
- Modify: `docs/store-guarantees.md` (The image store → Garbage collection; "Nothing is collected unless someone asks"; the "does not promise" list item), `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md` (dated amendment under §12), `docs/superpowers/specs/2026-10-11-unified-image-surfaces-design.md` (§5.1/§5.2 amendment per spec §1.4)
- Test: `backend/tests/test_docs_guard.py` (`test_store_guarantees_names_the_maintenance_modules` tuple gains `"recycle_bin"`)

- [ ] **Step 1:** Add `"recycle_bin"` to that tuple; run it — FAIL.
- [ ] **Step 2:** Rewrite the Garbage collection section per spec §2–§3.6: candidates (an hour, no sightings), held objects protected, disposal (outbox, bin after the lock, blob-before-sidecar for recycled objects and why it is safe), the Windows guards and the *unverified* backstop as the residual, the accepted sync risk replacing the offline-device bound; cite tests by their current names. Update the two other paragraphs; add both spec amendments.
- [ ] **Step 3:** Run `pytest tests/test_docs_guard.py -q` — PASS.
- [ ] **Step 4:** Commit `docs: the collector recycles, after an hour`.

### Task 6: `ImageViewer` — Remove with inline confirmation

**Files:**
- Create: `frontend/src/components/art/removal.ts`
- Modify: `frontend/src/components/art/types.ts`, `frontend/src/components/art/ImageViewer.tsx`
- Test: `frontend/src/components/art/removal.test.ts`, `frontend/src/components/art/ImageViewer.test.tsx`

**Interfaces:**
- Produces:
  - `types.ts`: `ImageDescriptor.removal?: { question: string }`; `export type RemoveOutcome = "removed" | "changed";`
  - `ImageViewerProps.onRemove?: (key: string) => Promise<RemoveOutcome>`
  - `removal.ts`:
    - `CHANGED_NOT_REMOVED = "This picture changed since it was loaded, so it was not removed."`
    - `EDIT_FIRST = "Finish or cancel the edit in the Art panel first."`
    - `IN_COLLECTION = "This picture is part of a collection, so it can't be deleted from the library."`
    - `slotPhrase(name: string, primaryLabel?: string): string` — `"avatar"` ⇒ `(primaryLabel ?? "Primary image").toLowerCase()`; `/^gallery_(\d+)$/` ⇒ `"gallery N"`; else `name.replace(/_/g, " ")`.
    - `removalQuestion(q: { surface: "record"; scope: "world" | "campaign"; record: string; name: string; primaryLabel?: string } | { surface: "campaign-library"; inherited: boolean; name: string } | { surface: "world-library"; name: string }): string` —
      - record/world: `` `Remove ${record}'s ${slotPhrase(name, primaryLabel)}?` ``
      - record/campaign: `` `Hide ${record}'s ${slot} only in this campaign? Any inherited world image in this slot is hidden too.` ``
      - campaign-library inherited: `` `Hide ${name} in this campaign? The world keeps it.` ``
      - campaign-library own: `` `Remove ${name} from this campaign's library?` ``
      - world-library: `` `Delete ${name} from the world library? Every campaign on this world loses it.` ``
    - `removalError(error: unknown): string` — `ApiError` with detail or kind `image_in_collection` ⇒ `IN_COLLECTION`; another `ApiError` ⇒ its `detail`; anything else ⇒ `"Could not remove this picture."`.

- [ ] **Step 1: Failing tests.** `removal.test.ts`: `slotPhrase` (`avatar`+"Avatar" ⇒ "avatar"; `avatar` alone ⇒ "primary image"; `gallery_3` ⇒ "gallery 3"; `cover_art` ⇒ "cover art"); the five questions with `record: "Mara"`, `name: "gallery_3"` / `"harbour"` — expected strings exactly, e.g. `"Remove Mara's gallery 3?"`, `"Hide Mara's gallery 3 only in this campaign? Any inherited world image in this slot is hidden too."`, `"Hide harbour in this campaign? The world keeps it."`, `"Remove harbour from this campaign's library?"`, `"Delete harbour from the world library? Every campaign on this world loses it."`; `removalError` three cases. `ImageViewer.test.tsx` (the `it(...)` names):
  - `draws Remove only with onRemove and a removal` (three cases).
  - `Remove opens the confirmation with focus on its Remove button` — a `role="group"` `aria-label="Confirm removal"` with the question; focus on its "Remove" button.
  - `Cancel and Escape cancel without closing` — `onClose` not called; a second Escape closes.
  - `Delete and Backspace open the confirmation`; `Backspace in a text field does not open the confirmation`.
  - `Enter on a focused Cancel does not remove`.
  - `Previous/Next and arrows do nothing while the confirmation is open` — buttons disabled.
  - `steps to the next image after removal` — [A,B,C] remove A ⇒ B, both when `images` drops A **before** the promise resolves and **after**; never C.
  - `keeps the image, its keys and focus while a removal is pending after a refresh dropped it` — drop A from `images` while pending: A is still shown with "Removing…", Escape does not reach a page-level binding registered in the test, focus is inside the dialog; after resolving, focus is inside the dialog.
  - `steps to the previous when the last is removed`; `closes plainly when the last image is removed` (no `"unavailable"`).
  - `"changed" with the key still present stays and shows the sentence`; `"changed" with the key gone steps on`; `a rejection stays and shows its message`; `controls are disabled while pending` ("Removing…").
  - `a removal resolving after the viewer closed does nothing` — unmount, then resolve; `onClose` not called again.
- [ ] **Step 2:** `cd frontend && npx vitest run src/components/art/removal.test.ts src/components/art/ImageViewer.test.tsx` — FAIL.
- [ ] **Step 3: Implement.** State: `confirming`, `pending`, `message`, `mounted` ref, and `removing: { key: string; index: number; image: ImageDescriptor } | null` set **when the reader confirms**. While `removing` is set, the unavailable effect ignores that key, the viewer does not move, and the viewer keeps rendering `removing.image` (its `ActiveImage`, modal hotkey scope, confirmation and "Removing…") even when a refresh has already dropped it from `images` — so focus, Tab/Shift+Tab trapping and Escape stay inside the dialog for the whole request. After the move, focus goes to the dialog's Close button if the focused element left the document. On `"removed"` (or `"changed"` with the key absent): if the key is still in `images`, select its neighbour (next, else previous); otherwise select `images[Math.min(removing.index, images.length - 1)]`; no images ⇒ `onClose()`; clear `removing`. On `"changed"` with the key present, or a rejection: clear `removing`, show `CHANGED_NOT_REMOVED` or the message. Every post-await step checks `mounted`. Bindings: `delete`/`backspace` (label "Remove image", group `IMAGE VIEWER`, `enabled: canRemove && !confirming && !pending`); the close binding `enabled: !confirming`; `{ keys: "escape", label: "Cancel removal", group: "IMAGE VIEWER", enabled: confirming && !pending, run: cancel }`; arrows and the Previous/Next buttons require `!confirming && !pending`. Remove/confirmation render in `art-viewer-tools` through new `ActiveImage` props; the confirm button takes focus via a ref when the confirmation opens. Selecting another key resets `confirming` and `message`.
- [ ] **Step 4:** Run those files and `npx vitest run src/shortcuts` — PASS.
- [ ] **Step 5:** Commit `feat(viewer): remove the shown image with an inline confirmation`.

### Task 7: Combined gallery supplies removal

**Files:**
- Modify: `frontend/src/api/types.ts` (`GalleryImage.provenance` accepts `"world"` for library rows), `frontend/src/api/client.ts` (`removeRecordArt`'s image parameter narrowed to `Pick<RecordArtImage, "name" | "v" | "placement">`), `frontend/src/components/art/recordArtAdapter.ts` (`remove` and `checkImage` take the same `Pick`), `frontend/src/components/art/galleryState.ts`, `frontend/src/components/ImagesView.tsx`
- Test: `frontend/src/components/art/galleryState.test.ts`, `frontend/src/components/ImagesView.test.tsx`

**Interfaces:**
- Consumes: Task 6 (`removalQuestion`, `removalError`, `RemoveOutcome`); Task 4 row `provenance`.
- Produces: `galleryImageDescriptor(image, scope, wid)` sets `removal` — record rows (`ART_RECORD_KINDS`, `v` and `placement` present, `provenance !== "world-original"`): `{surface: "record", scope: scope.kind, record: image.record_name, name: image.name, primaryLabel}`; `kind === "campaign"`: `{surface: "campaign-library", inherited: image.provenance === "inherited", name: image.name}`; `kind === "world"`: `{surface: "world-library", name: image.name}` (needs no provenance, so `WorldArtPanel`'s descriptors get it too); greetings: none. In `ImagesView`'s `GalleryBody`: a `loadGallery(fresh: boolean): Promise<GalleryImage[] | null>` that re-reads and commits the gallery rows only (not characters, greetings or the untagged queue) and returns what it committed — `null` when superseded or failed, which rejects the removal with "Could not check this picture again. Reload the gallery and try again." — and `removeFromGallery(key: string): Promise<RemoveOutcome>` uses **`effectiveScope`** (not `scope`, which is always the world).

- [ ] **Step 1: Failing tests:** `galleryState.test.ts` — the `removal.question` for each row kind in world and campaign scope, and none for greetings, `world-original`, or a record row without `v`. `ImagesView.test.tsx` — in a campaign gallery a record row's Remove calls `api.removeRecordArt` with `{scope: {kind: "campaign", id: cid}, …}` and `{name, v, placement}` and the viewer moves on; a campaign library row calls `deleteCampaignImage(cid, name)`; a world gallery's library row `deleteWorldImage(wid, name)`; a 409 `stale_image` awaits `loadGallery(true)` (`listCampaignGallery(cid, true)` / `listWorldImages(wid, true)`), a superseded re-read rejects with the reload sentence, and resolves `"removed"` when the row is gone and `"changed"` when present — and the gallery shows the refreshed rows (so a retry uses the new `v`); `ApiError(409, "image_in_collection")` rejects with `IN_COLLECTION`.
- [ ] **Step 2:** `npx vitest run src/components/art/galleryState.test.ts src/components/ImagesView.test.tsx` — FAIL.
- [ ] **Step 3: Implement**; pass `onRemove={removeFromGallery}` to the gallery's `<ImageViewer>`. On success start `load(true)` without awaiting it.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `feat(gallery): remove from the viewer`.

### Task 8: Record Art panel and World art panel supply removal

**Files:**
- Modify: `frontend/src/components/art/useRecordArt.ts`, `frontend/src/components/WorldArtPanel.tsx`
- Test: `frontend/src/components/art/useRecordArt.test.ts`, `frontend/src/components/art/RecordArtPanel.test.tsx`, `frontend/src/components/WorldArtPanel.test.tsx`

**Interfaces:**
- Consumes: Task 6 (`removalQuestion`, `removalError`, `EDIT_FIRST`); Task 7's `galleryImageDescriptor` (World panel).
- Produces: `RecordArtController.removeFromViewer(key: string): Promise<RemoveOutcome>`, included in `controller.viewerProps` as `onRemove` (so `RecordArtViewer` passes it through `{...controller.viewerProps}` unchanged); `describe()` sets `removal` — `{surface: "record", scope: target.scope.kind, record: recordName || target.record_id, name: image.name, primaryLabel}` — when `operations` include `"remove"` and the image is not a world original.

- [ ] **Step 1: Failing tests:** `useRecordArt.test.ts` — `removeFromViewer` rejects with `EDIT_FIRST` and sends nothing while a draft is held or `pending` is set; holds `pending` for the call; resolves `"removed"` before the refresh completes (refresh on a deferred); on `stale_image` awaits `refresh(true)` and resolves by presence — a `null` re-read (superseded or failed) rejects with "Could not check this picture again. Reload the Art panel and try again." and never guesses; other errors reject with `removalError(e)`. `RecordArtPanel.test.tsx` — the viewer opened from the panel shows Remove for a placement and not for a world original; confirming calls the adapter's `remove`. `WorldArtPanel.test.tsx` — the viewer's Remove calls `api.deleteWorldImage(wid, name)` with no `window.confirm`; `image_in_collection` shows `IN_COLLECTION`; the ✕ path is unchanged.
- [ ] **Step 2:** Run the three files — FAIL.
- [ ] **Step 3: Implement.** `removeFromViewer` sets `pending.current.value` (the flag `mutation` uses) and `busy`, calls `adapter.remove(image)` directly, releases in `finally`; success starts `void afterSaved()`. `WorldArtPanel` passes an `onRemove` that sets `busy`, calls `api.deleteWorldImage`, then `void load(true)`; on `stale_image`-like refusals it has none (the route takes no guard).
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `feat(art): remove from the viewer on the Art panel and World art`.

### Task 9: Settings card speaks recycle bin

**Files:**
- Modify: `frontend/src/api/types.ts` (`ImageGcReport`: optional `grace_hours`, optional `grace_days` kept; `state` gains `"trash-unavailable" | "recycle-bin-unverified"`; optional `recycled`, `released`, `outbox`, `foreign_outboxes`, `unverified`, `recycle_bin: { ok: boolean; code: string | null; message: string | null }`; `deleted.blobs` optional), `frontend/src/components/ImageStoreCard.tsx`
- Test: `frontend/src/components/ImageStoreCard.test.tsx`

**Interfaces:**
- Consumes: Task 3's report fields; Task 4's 409 (`ApiError.kind == "no_recycle_bin"`, `.detail` the sentence).

- [ ] **Step 1: Failing tests:** the button reads "Send 3 unused images to the recycle bin" (2 collectable + 1 outbox file); its confirm text says the pictures go to this computer's recycle bin, their descriptions and tags are not kept, and a picture another device linked but has not synced yet would be binned too; `recycle_bin.ok == false` hides the button and shows `recycle_bin.message`; a 409 `no_recycle_bin` on start shows its `detail`; a `refused` collect report with `error == "no_recycle_bin"` shows `recycle_bin.message`, never the code; a too-new row shows a time (`toLocaleTimeString`), not a date; waiting and foreign outbox files are listed with `.cache/image-store/recycle/`; the collect report reads "Sent 2 images to the recycle bin (<size>). The space is freed when you empty the bin." and "Their names start with grimoire - ."; `trash-unavailable` reads "The recycle bin refused a file; N are waiting to be sent. Nothing more was collected." and `recycle-bin-unverified` names the files with "Windows did not confirm these went to the Recycle Bin. Check the bin before collecting again."; an older report with `grace_days` and `deleted.blobs` and no `recycled` renders "Deleted N unused images".
- [ ] **Step 2:** `npx vitest run src/components/ImageStoreCard.test.tsx` — FAIL.
- [ ] **Step 3: Implement**; remove the "two scans a grace period apart" copy.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `feat(settings): collection sends to the recycle bin`.

### Task 10: Gate

- [ ] **Step 1:** `make check` from the repo root (backend venv per `CONTRIBUTING.md`). If `check-pydantic1` cannot build its venv in the container, run the other targets and say so.
- [ ] **Step 2:** If a ratchet reports an improvement, `make baseline` and commit the smaller baselines.
- [ ] **Step 3:** Stand-in diff review (user-approved substitute for `/codex:review`), then the final review against the spec (substitute for the final `/codex:adversarial-review`); fix findings; commit.
- [ ] **Step 4:** Push; update PR charlesmsiegel/grimoire#508's description.
