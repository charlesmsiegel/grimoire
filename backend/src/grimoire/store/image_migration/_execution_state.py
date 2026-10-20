"""One run state, root/identity guards, campaign-write accounting and restartable GC-root work journal."""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .. import atomic, image_hash, image_refs, image_surfaces, locks, paths, revision
from . import _contracts


def work_map_path(root: Path) -> Path:
    """Where a real run keeps the plan's pending entries (M7): restartable,
    and losing it costs only time. The dry run never writes it."""
    return Path(root) / ".cache" / "image-store" / "migration" / "map.json"


def _linked_slot(root: Path, d: Path, name: str | None = None) -> bool:
    """Whether a write in `d` could land outside `root`: `d` reached through
    a symlink or junction, its ``image-refs/`` folder one too, or -- for `name`
    -- that placement file itself one. `linked` checks the components from the
    root down to the path it is given, so each of those is asked separately: a
    placement is written one and two levels below `d`, and a link there would
    put the placement the legacy file is then deleted behind somewhere else
    entirely. `is_symlink()` alone misses a Windows junction (#478)."""
    if image_surfaces.linked(root, d) or image_surfaces.linked(root, d / image_refs.REFS_DIR):
        return True
    try:
        return name is not None and image_surfaces.linked(root, image_refs.ref_path(d, name))
    except ValueError:
        return True


def _same_root(root: Path) -> bool:
    try:
        return paths.home().resolve() == root
    except (OSError, RuntimeError):
        return False


def _snap(st: os.stat_result) -> tuple[int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _lstat(p: Path) -> os.stat_result | None:
    try:
        return os.stat(p, follow_symlinks=False)
    except OSError:
        return None


def _campaign_of(occ: _contracts.Occurrence) -> str | None:
    side, _, cid = occ.scope.partition(":")
    return cid if side == "campaign" else None


@contextmanager
def _campaign_held(cid: str | None):
    """The occurrence's campaign lock, when it has a campaign."""
    if cid is None:
        yield
        return
    with locks.campaign_lock(cid):
        yield


@contextmanager
def _campaign_write(ex: _Exec, cid: str | None):
    """`_campaign_held`, and the revision bump for whatever the step inside it
    wrote -- in a ``finally`` still under the lock, so a step that writes and
    is then stopped (a `locks.StoreBusy`, a moved root, an I/O error) leaves
    the campaign's token moved past the write rather than vouching for the
    state before it (CLAUDE.md, revision)."""
    with _campaign_held(cid):
        ex.wrote = False
        try:
            yield
        finally:
            if ex.wrote:
                ex.bump(cid)
            ex.wrote = False


@contextmanager
def _collection_held(occ: _contracts.Occurrence):
    """`image_collection_lock` for a world-library occurrence (M8): a write
    there is what format-1 membership checks guard."""
    side, _, wid = occ.scope.partition(":")
    if occ.kind != image_surfaces.LIBRARY or side != "world":
        yield
        return
    with locks.image_collection_lock(wid):
        yield


@dataclass
class _Exec:
    """One real run's state."""
    plan: _contracts.MigrationPlan
    cancel: Callable[[], bool]
    out: dict
    #: The work map: image id -> legacy paths ingested, not yet verified.
    pending: dict[str, list[str]] = field(default_factory=dict)
    #: ``(dir, name)`` placements this run verified.
    verified: set[tuple[Path, str]] = field(default_factory=set)
    #: Campaigns this run wrote, each bumped where it was written.
    written: set[str] = field(default_factory=set)
    #: Set by each write to a record's files inside `_campaign_write`, and read
    #: in its ``finally``: a step stopped part-way after a write still bumps.
    wrote: bool = False
    #: On-disk bytes of the legacy files this run deleted.
    deleted_bytes: int = 0
    #: image id -> the blob bytes this run's ingest added for a picture the
    #: plan found no object for; the plan's `bytes_after`, as it happened.
    added_bytes: dict[str, int] = field(default_factory=dict)
    #: stream -> sanitized size, built on the first ingest that needs it.
    stream_sizes: dict[str, int] = field(default_factory=dict)

    def bytes_reclaimed(self) -> int:
        """What the run freed, by the plan's arithmetic (legacy bytes gone,
        less the blobs that replaced them) over what actually happened rather
        than what was planned (Codex review, P2d). Never below zero: a run
        stopped after an ingest and before any delete freed nothing."""
        return max(0, self.deleted_bytes - sum(self.added_bytes.values()))

    @property
    def root(self) -> Path:
        return self.plan.root

    def guard(self, step: str) -> None:
        if not _same_root(self.root):
            raise _contracts._RootChangedError(step)

    def guard_dir(self, step: str, d: Path, name: str | None = None) -> bool:
        """`guard`, and whether `d` may be written in (`_linked_slot`)."""
        self.guard(step)
        return not _linked_slot(self.root, d, name)

    def skip(self, p: Path, reason: str) -> None:
        self.out["skipped"].append({"path": self.plan.rel(p), "reason": reason})

    def keep(self, side: Path, key: str | None, reason: str) -> None:
        entry = {"path": self.plan.rel(side), "key": key, "reason": reason}
        if entry not in self.out["keys_kept"]:
            self.out["keys_kept"].append(entry)

    def bump(self, cid: str | None) -> None:
        """Under `cid`'s lock, after its write (CLAUDE.md, revision)."""
        if cid is not None:
            revision.bump(cid)
            self.written.add(cid)


# ---- the work map ------------------------------------------------------------

def _save_map(ex: _Exec) -> None:
    p = work_map_path(ex.root)
    ex.guard("before-map-write")
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, json.dumps({"format": 1, "pending": ex.pending},
                                    indent=2, sort_keys=True) + "\n")


def _pend(ex: _Exec, image_id: str, rel: str) -> None:
    """Record `rel` as about to be ingested as `image_id`: a GC root (M12)
    until it verifies, so an object ingested and not yet placed survives a
    crash here."""
    ex.pending.setdefault(image_id, []).append(rel)
    _save_map(ex)


def _unpend(ex: _Exec, image_id: str, rel: str) -> None:
    got = ex.pending.get(image_id, [])
    if rel in got:
        got.remove(rel)
        if not got:
            del ex.pending[image_id]
        _save_map(ex)


def _load_map(root: Path) -> dict[str, list[str]]:
    """The work map's pending entries; `ValueError` when it does not read."""
    try:
        raw = json.loads(work_map_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError("unreadable migration work map") from exc
    pending = raw.get("pending") if isinstance(raw, dict) else None
    if (not isinstance(pending, dict) or raw.get("format") != 1
            or not all(image_hash.is_image_id(k) and isinstance(v, list)
                       and all(isinstance(r, str) for r in v) for k, v in pending.items())):
        raise ValueError("unreadable migration work map")
    return {k: list(v) for k, v in pending.items()}


def pending_ids(root: Path) -> set[str]:
    """The image ids the migration work map under `root` holds pending --
    ingested and not yet verified, so GC roots (M12). Empty without a map;
    `ValueError` for one that does not read, which a collector must treat as
    a root it cannot see (fail closed)."""
    return set(_load_map(Path(root)))


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


# ---- the run -----------------------------------------------------------------------

def _error_text(exc: BaseException) -> str:
    """What a report says about a failure: the exception's type, and an
    `OSError`'s errno name when it has one -- never its message. An OS error's
    message carries the absolute path it failed on, which names the store's
    location and, below it, a world or a character; a report is a file a
    person may hand to someone else, and its paths are relative for that
    reason (M2)."""
    name = type(exc).__name__
    code = getattr(exc, "errno", None) if isinstance(exc, OSError) else None
    if isinstance(code, int):
        return f"{name} [{errno.errorcode.get(code, code)}]"
    return name


def _attempt(ex: _Exec, where: Path | None, step: Callable[[], object]) -> None:
    """Run one step; a failure there is reported and the run goes on to the
    next (one corrupt file does not stop the rest). A moved root is not a
    failure of the step: it stops the run."""
    try:
        step()
    except _contracts._RootChangedError:
        raise
    except Exception as exc:  # noqa: BLE001 -- reported per step; the rest still runs
        ex.out["errors"].append({"path": ex.plan.rel(where) if where is not None else None,
                                 "error": _error_text(exc)})


def _stopped(ex: _Exec) -> bool:
    if ex.cancel():
        ex.out["outcome"] = _contracts.CANCELLED
        return True
    return False


def _safe_map(root: Path) -> dict[str, list[str]]:
    """The work map a crashed run left (its entries stay roots until this run
    completes), or nothing for one that does not read -- it is rewritten."""
    try:
        return _load_map(root)
    except ValueError:
        return {}
