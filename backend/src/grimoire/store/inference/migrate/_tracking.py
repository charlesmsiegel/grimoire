"""Single owner of process activity, queued roots and archive progress; root-keyed status notes and read-only status projection."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from ... import atomic, backups, config, frontmatter, locks, paths, proclock
from ... import inference_keys as keys
from ...campaigns import paths as campaign_paths
from ...campaigns import read as campaign_read
from .. import retire
from . import _contracts

log = logging.getLogger('grimoire.store.inference.migrate')


# ---- what is remembered ----
_active_guard = threading.Lock()
#: Roots a migration is running on in THIS process -- `running` for a caller
#: on the migrating thread itself, which the reentrant lock would let through.
_active: dict[str, dict | None] = {}


@contextmanager
def _running_on(root: Path) -> Iterator[None]:
    with _active_guard:
        _active[str(root)] = None
    try:
        yield
    finally:
        with _active_guard:
            _active.pop(str(root), None)


def _status_path(root: Path) -> Path:
    return root / ".cache" / "inference-migration.json"


def _recall(root: Path) -> dict:
    """What the last run on `root` left: `{}` when nothing, when unreadable, or
    when it was written for another root (a copied or synced store)."""
    try:
        raw = json.loads(_status_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("root") != str(root):
        return {}
    return raw


def _remember(root: Path, *, running: bool, failed: str = "",
              skipped: list[str] | tuple[str, ...] = (), safety: str = "",
              retire_safety: str = "", retire_failed: str = "",
              progress: dict | None = None, progress_owner: str = "") -> None:
    """Record the run's state for `root`. Best effort: derived data, and a
    status that cannot be written must not stop the migration it describes.
    `retire_safety` is the `pre-retirement-` archive the pass took or reused,
    and `retire_failed` why the last retirement pass stopped short."""
    note = {"root": str(root), "running": running, "pid": os.getpid(),
            "failed": failed, "skipped": list(skipped), "safety": safety,
            "retire_safety": retire_safety, "retire_failed": retire_failed,
            "progress": progress, "progress_owner": progress_owner}
    path = _status_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(path, json.dumps(note, indent=2) + "\n")
    except OSError as exc:
        log.warning("inference migration: could not record its status -- %s", exc)


def _is_running(root: Path, note: dict) -> bool:
    """A run on `root` in this process, or one another process says it has.

    Another process's note can outlive a crash; that lasts until the next
    start on this root, whose own run rewrites it -- or until the store is
    done, which `status` checks first."""
    with _active_guard:
        if str(root) in _active:
            return True
    return bool(note.get("running")) and note.get("pid") != os.getpid()


#: Roots whose startup pass is queued in THIS process and has not yet ended.
#: A status read can take the migration lock in the gap before the queued
#: thread takes it, which would otherwise read as no activity (#496).
_queued: set[str] = set()


def queue(root: Path) -> None:
    """Mark `root`'s startup pass as queued, before its thread starts."""
    with _active_guard:
        _queued.add(str(root))


def dequeue(root: Path) -> None:
    """Clear the queued mark once the pass that was queued has ended."""
    with _active_guard:
        _queued.discard(str(root))


def _activity(root: Path) -> bool:
    """Activity spans planning and retirement, independently of format (#496).

    Readers only try the existing outer lock, then release it immediately. A
    local run on another root must not be attributed to this resolved root.
    """
    with _active_guard:
        if str(root) in _active or str(root) in _queued:
            return True
        if _active:
            return False
    lock = locks.inference_migration_lock()
    got = False
    try:
        if paths.home() != root:
            return False
        got = lock.acquire(blocking=False)
        return not got and paths.home() == root
    except OSError:
        return False
    finally:
        if got:
            with suppress(OSError):
                lock.release()


@contextmanager
def _progress_lease(root: Path) -> Iterator[str]:
    """Optional archive ownership, outside the synced library (#496).

    A fresh nonce per archive prevents a crashed run's note resurfacing when
    the next run holds the main lock but has not started archiving yet.
    Failure only hides foreign progress; it cannot fail a valid backup.
    """
    nonce = uuid.uuid4().hex
    fd = None
    with suppress(OSError):
        fd = proclock.acquire(proclock.lock_path(root, "inference-progress", nonce),
                              proclock.NO_WAIT)
    try:
        yield nonce if fd is not None else ""
    finally:
        if fd is not None:
            with suppress(OSError):
                proclock.release(fd)


def _owns_progress(root: Path, nonce: object) -> bool:
    """Verify a foreign archive lease, excluding simultaneous probe readers.

    Without the observer mutex, reader B's successful probe of an old free
    lease impersonates a writer to reader C. All readers try the observer
    first; the writer never does, so this cannot delay the archive (#496).
    """
    if (not isinstance(nonce, str) or len(nonce) != 32
            or any(c not in "0123456789abcdef" for c in nonce)):
        return False
    observer = lease = None
    try:
        path = proclock.lock_path(root, "inference-progress", nonce)
        if not path.is_file():
            return False
        observer = proclock.acquire(
            proclock.lock_path(root, "inference-progress-reader", "probe"), proclock.NO_WAIT)
        if observer is None:
            return False
        lease = proclock.acquire(path, proclock.NO_WAIT)
        owned = lease is None
    except OSError:
        return False
    finally:
        # Release a successful lease probe BEFORE allowing another reader in.
        # Cleanup errors make ownership unverifiable as well.
        try:
            if lease is not None:
                proclock.release(lease)
        finally:
            if observer is not None:
                proclock.release(observer)
    return owned


def _progress(root: Path, note: dict, active: bool) -> dict | None:
    if not active or paths.home() != root:
        return None
    with _active_guard:
        if str(root) in _active:
            live = _active[str(root)]
            return dict(live) if live is not None else None
    progress = note.get("progress")
    if not isinstance(progress, dict) or progress.get("phase") not in {
            "safety_backup", "retirement_backup"}:
        return None
    done, total = progress.get("done"), progress.get("total")
    if type(done) is not int or done < 0:
        return None
    if total is None:
        if done != 0:
            return None
    elif type(total) is not int or total < done:
        return None
    try:
        owned = _owns_progress(root, note.get("progress_owner"))
    except OSError:
        owned = False
    return {"phase": progress["phase"], "done": done, "total": total} if owned else None


def _kept_safety(note: dict, key: str = "safety",
                prefix: str = backups.SAFETY_PREFIX) -> str:
    """The safety archive an earlier run on this root took (`key` in its note,
    named under `prefix`), when it is still in the backup directory; "" when
    there is none to reuse. Retirement's is `_kept_retire_safety`."""
    name = note.get(key)
    if (not isinstance(name, str) or not name.startswith(prefix)
            or Path(name).name != name):
        return ""
    try:
        return name if (backups.backup_dir() / name).is_file() else ""
    except OSError:
        return ""


def _kept_retire_safety(note: dict) -> str:
    """The `pre-retirement-` archive an earlier pass on this root took, when
    it is still there (R2-3): reused, so a resumed pass takes no second one."""
    return _kept_safety(note, "retire_safety", backups.RETIRE_PREFIX)


def _note_retire_failed(root: Path, failed: str) -> None:
    """Record `failed` as why retirement stopped short on `root`, keeping the
    rest of the note -- unless the note already says it."""
    note = _recall(root)
    if _note_text(note, "retire_failed") == failed:
        return
    skipped = [s for s in note.get("skipped") or () if isinstance(s, str)]
    _remember(root, running=False, failed=_note_text(note, "failed"), skipped=skipped,
              safety=_kept_safety(note), retire_safety=_kept_retire_safety(note),
              retire_failed=failed)


def _retirement(failed: str) -> dict:
    """`Status.retirement`: what `retire.left()` says now, and `failed`."""
    return {"left": list(retire.left()), "failed": failed}


def _note_text(note: dict, key: str) -> str:
    value = note.get(key)
    return value if isinstance(value, str) else ""


# ---- reading the store ----
def _config_exists(root: Path) -> bool:
    return (root / "config.md").exists()


#: C's strict record read, moved to `store/frontmatter.py` (slice I) so
#: retirement -- which this module calls, and so cannot import -- shares it.
#: Bound here too: `migrate.RecordUnreadableError` is what callers catch.
RecordUnreadableError = frontmatter.RecordUnreadableError


def _config_record(root: Path) -> dict[str, str]:
    """`config.md`'s raw frontmatter; `RecordUnreadableError` (or the read's
    own `OSError`/`UnicodeDecodeError`) when it cannot be read as a record."""
    return frontmatter.read_record(root / "config.md", "config.md")[0]


def _campaign_marks() -> tuple[dict[str, bool], dict[str, str]]:
    """(cid -> whether its `campaign.md` carries the current (or a newer)
    marker, cid -> why it cannot be read) -- every campaign is in exactly
    one of the two."""
    marks: dict[str, bool] = {}
    unreadable: dict[str, str] = {}
    for cid, _name, _world in campaign_read.world_refs():
        if not paths.safe_id(cid):
            continue
        try:
            meta, _ = frontmatter.read_record(campaign_paths.campaign_meta_path(cid),
                                              f"campaign {cid}'s campaign.md")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            unreadable[cid] = str(exc)
            continue
        marks[cid] = keys.is_current(meta) or keys.is_newer(meta)
    return marks, unreadable


def _unreadable_skips(unreadable: dict[str, str]) -> list[str]:
    return [f"campaign {cid}: {why}" for cid, why in unreadable.items()]


def _merged(*groups: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """The skips of every group, in order, each once."""
    return tuple(dict.fromkeys(s for group in groups for s in group))


def status() -> _contracts.Status:
    """Where the store at `paths.home()` stands. Never writes."""
    root = paths.home()
    if not _config_exists(root):
        # A fresh store is born current (ruling 13); there is nothing to move.
        return _contracts.Status("done")
    cfg = config.read_config()
    if keys.is_newer(cfg):
        return _contracts.Status("newer")
    note = _recall(root)
    active = _activity(root)
    progress = _progress(root, note, active)
    marks, unreadable = _campaign_marks()
    remembered = [str(s) for s in note.get("skipped") or () if isinstance(s, str)]
    skipped = _merged(remembered, _unreadable_skips(unreadable))
    # Retirement's account rides beside the state and never moves it.
    retirement = _retirement(_note_text(note, "retire_failed"))
    # Done first: what the store says outranks a note that can outlive a crash.
    if keys.is_current(cfg) and False not in marks.values():
        return _contracts.Status("done", "", skipped, retirement, active, progress)
    if _is_running(root, note):
        return _contracts.Status("running", "", skipped, retirement, active, progress)
    failed = _note_text(note, "failed")
    return _contracts.Status("failed" if failed else "pending", failed, skipped, retirement, active, progress)
