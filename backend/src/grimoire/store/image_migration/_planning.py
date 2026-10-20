"""Pinned inventory, case-alias rejection, hash/group planning and the shared dry-run report."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .. import image_collections, image_hash, image_refs, image_store, image_surfaces
from . import _contracts, _preview

# ---- the walk ----------------------------------------------------------------

@dataclass(frozen=True)
class _Stream:
    ext: str
    size: int
    identity: image_hash.PixelIdentity
    #: Why it is not a picture (`_NOT_PICTURES`), or None.
    refused: str | None


def _untouch(plan: _contracts.MigrationPlan, path: Path, reason: str) -> None:
    plan.untouched.append({"path": plan.rel(path), "reason": reason})


def _inventory(plan: _contracts.MigrationPlan, cancel: Callable[[], bool]
               ) -> tuple[list[_contracts.Occurrence], list[_contracts.Occurrence]] | None:
    """`(files to place, metadata-only)`; untouched files and manifests are
    recorded as they pass. None when cancelled."""
    files: list[_contracts.Occurrence] = []
    meta: list[_contracts.Occurrence] = []
    for occ in image_surfaces.occurrences(plan.root):
        if cancel():
            return None
        if occ.path is not None and occ.untouched is not None:
            _untouch(plan, occ.path, occ.untouched)
        elif occ.kind == image_surfaces.COLLECTION:
            _manifest(plan, occ)
        elif occ.metadata_only is not None:
            meta.append(occ)
        elif occ.path is not None:
            files.append(occ)
    return files, meta


def _manifest(plan: _contracts.MigrationPlan, occ: _contracts.Occurrence) -> None:
    assert occ.path is not None
    try:
        raw = json.loads(occ.path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        _untouch(plan, occ.path, "unreadable-manifest")
        return
    fmt = raw.get("format") if isinstance(raw, dict) else None
    if type(fmt) is int and fmt == 1:
        plan.format1_collections.append(plan.rel(occ.path))


def _aliases(plan: _contracts.MigrationPlan, files: list[_contracts.Occurrence],
             meta: list[_contracts.Occurrence]) -> tuple[list[_contracts.Occurrence], list[_contracts.Occurrence]]:
    """Drop every file and same-directory key whose name aliases another
    name in its directory by case -- legacy or placed (M7)."""
    by_dir: dict[Path, set[str]] = {}
    for occ in files:
        by_dir.setdefault(occ.dir, set()).add(occ.name)
    aliased: dict[Path, set[str]] = {}
    for d, legacy in sorted(by_dir.items()):
        names = legacy | set(image_refs.scan(d))
        folded: dict[str, set[str]] = {}
        for n in names:
            folded.setdefault(n.casefold(), set()).add(n)
        for spellings in folded.values():
            if len(spellings) > 1 and spellings & legacy:
                aliased.setdefault(d, set()).update(spellings)
                plan.case_aliases.append({"dir": plan.rel(d), "names": sorted(spellings)})
    kept = []
    for occ in files:
        if occ.name in aliased.get(occ.dir, ()):
            assert occ.path is not None
            _untouch(plan, occ.path, "case-alias")
        else:
            kept.append(occ)
    return kept, [m for m in meta if m.name not in aliased.get(m.dir, ())]


def _read(path: Path) -> tuple[bytes, os.stat_result] | None:
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            return f.read(), st
    except OSError:
        return None


def _stream(streams: dict[str, _Stream], data: bytes, ext: str) -> tuple[str, _Stream]:
    """The stream `data` sanitizes to, decoded only the first time it is seen."""
    p = image_store.prepare(data, ext)
    got = streams.get(p.sha)
    if got is None:
        identity = image_store.identity_of(p)
        refused = p.raw_reason if p.raw_reason is not None else (
            identity.reason if identity.reason == "undecodable" else None)
        got = streams[p.sha] = _Stream(p.ext, len(p.data), identity, refused)
    return p.sha, got


def _member_differs(occ: _contracts.Occurrence, data: bytes) -> bool:
    """A world-library file named as a format-1 collection member whose bytes
    do not hash to that name."""
    if occ.kind != image_surfaces.LIBRARY or not occ.scope.startswith("world:"):
        return False
    prefix = image_collections.MEMBER_PREFIX
    if not _contracts._MEMBER_NAME.fullmatch(occ.name.casefold()):
        return False
    return hashlib.sha256(data).hexdigest() != occ.name.casefold()[len(prefix):]


def _resolves(root: Path, image_id: str) -> bool:
    """Whether `image_id`'s object and its blob are both under `root`."""
    obj = image_store.read_fresh(image_id, root=root)
    if obj is None:
        return False
    return image_store.blob_path(obj.blob_sha256, obj.ext, root=root).is_file()


def _hash(plan: _contracts.MigrationPlan, files: list[_contracts.Occurrence], cancel: Callable[[], bool]) -> bool:
    """Hash and identify every file, filing each as an item or untouched.
    False when cancelled."""
    streams: dict[str, _Stream] = {}
    for occ in files:
        if cancel():
            return False
        assert occ.path is not None
        got = _read(occ.path)
        if got is None:
            _untouch(plan, occ.path, "unreadable")
            continue
        data, st = got
        if _member_differs(occ, data):
            # A format-1 member's name is its bytes' hash, and conversion
            # trusts a member's placement as that identity (M10): a file the
            # name no longer describes must never become that placement.
            _untouch(plan, occ.path, "member-differs-from-name")
            continue
        sha, stream = _stream(streams, data, occ.path.suffix)
        if stream.refused is not None:
            _untouch(plan, occ.path, stream.refused)
            continue
        image_id = stream.identity.id
        ref = image_refs.read(occ.dir, occ.name)
        if ref is not None and ref.image is not None and ref.image != image_id:
            reason = "differs" if _resolves(plan.root, ref.image) else "not-available"
            plan.legacy_differs.append({"path": plan.rel(occ.path), "placement": ref.image,
                                        "reason": reason})
            _untouch(plan, occ.path, "legacy-differs" if reason == "differs"
                     else "placement-not-available")
            continue
        plan.items.append(_contracts.Item(occ, sha, image_id, stream.size, st.st_size,
                               (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns),
                               placed=ref.image if ref is not None else None))
    return True


# ---- groups -------------------------------------------------------------------

def _group(plan: _contracts.MigrationPlan, image_id: str) -> _contracts.Group:
    got = plan.groups.get(image_id)
    if got is None:
        got = plan.groups[image_id] = _contracts.Group(image_id)
        obj = image_store.read_fresh(image_id, root=plan.root)
        if obj is not None:
            got.existing = True
            if image_store.blob_path(obj.blob_sha256, obj.ext, root=plan.root).is_file():
                got.retained = obj.blob_sha256
    return got


def _groups(plan: _contracts.MigrationPlan) -> None:
    """Group the items, choose each new object's blob, order the items so
    that blob is ingested first, and count the bytes."""
    sizes: dict[str, int] = {}
    for item in plan.items:
        g = _group(plan, item.image_id)
        g.streams[item.stream] = g.streams.get(item.stream, 0) + 1
        sizes[item.stream] = item.size
        plan.bytes_before += item.raw_size
    for g in plan.groups.values():
        if g.retained is None and g.streams:
            g.retained = _contracts.choose_retained({s: (n, sizes[s]) for s, n in g.streams.items()})
            plan.bytes_after += sizes[g.retained]
    plan.items.sort(key=lambda i: (i.image_id, i.stream != plan.groups[i.image_id].retained,
                                   str(i.occurrence.path)))


def _slot_ids(plan: _contracts.MigrationPlan) -> dict[tuple[Path, str], str]:
    return {(i.occurrence.dir, i.occurrence.name): i.image_id for i in plan.items}


def _target_id(occ: _contracts.Occurrence, slots: dict[tuple[Path, str], str]) -> str | None:
    """The picture a metadata-only occurrence's target holds: its
    image-bearing placement, else the file this plan places there."""
    if occ.target is None:
        return None
    name = occ.target_name or occ.name
    ref = image_refs.read(occ.target, name)
    if ref is not None and ref.image is not None:
        return ref.image
    return slots.get((occ.target, name))


def _metadata(plan: _contracts.MigrationPlan, meta: list[_contracts.Occurrence]) -> None:
    slots = _slot_ids(plan)
    for occ in meta:
        if (occ.metadata_only == "c" and occ.target_name is None
                and (occ.target, occ.name) in slots):
            continue                # this file's own fold already carries the key
        image_id = _target_id(occ, slots)
        plan.metadata.append((occ, image_id))
        if image_id is not None and occ.metadata_only in ("a", "c"):
            _group(plan, image_id)


# ---- plan and report ------------------------------------------------------

def _cancelled(p: _contracts.MigrationPlan) -> _contracts.MigrationPlan:
    """An empty plan that says it was cancelled: nothing gathered before the
    cancel survives, so nothing half-walked can be mistaken for the store."""
    return _contracts.MigrationPlan(p.root, cancelled=True)


def plan(root: Path, *, cancel: Callable[[], bool] | None = None) -> _contracts.MigrationPlan:
    """The migration plan for store root `root` (module docstring).

    `root` is the caller's pinned root (M4); nothing is resolved through
    `paths.home()`. `cancel` is asked between occurrences and between files:
    once it answers True the plan is empty, says ``cancelled`` and must not
    be run. Reads only -- writes nothing anywhere."""
    asked = cancel if cancel is not None else (lambda: False)
    out = _contracts.MigrationPlan(Path(root))
    walked = _inventory(out, asked)
    if walked is None:
        return _cancelled(out)
    out.legacy_files = len(walked[0])
    files, meta = _aliases(out, *walked)
    if not _hash(out, files, asked):
        return _cancelled(out)
    _groups(out)
    _metadata(out, meta)
    sources = _preview._sources(out)
    _preview._describe(out, sources)
    _preview._subjects(out, sources)
    return out


def _count(values: Iterable[str | None]) -> dict[str, int]:
    out = dict.fromkeys("abcd", 0)
    for v in values:
        if v is not None:
            out[v] += 1
    return out


def report(p: _contracts.MigrationPlan) -> dict:
    """The §11 report of plan `p`, plain JSON. A real run's report has the
    same shape (M2), with what it did in place of what it would do."""
    streams = {i.stream for i in p.items}
    images = {i.image_id for i in p.items}
    conflicted = [g for g in p.groups.values() if "conflicts" in g.description]
    return {
        "legacy_files": p.legacy_files,
        "unique_streams": len(streams),
        "unique_images": len(images),
        "exact_duplicates": len(p.items) - len(streams),
        "pixel_variants": len(streams) - len(images),
        "descriptions_merged": p.descriptions_merged,
        "description_conflicts": len(conflicted),
        "subject_disagreements": list(p.subject_disagreements),
        "untouched": sorted(p.untouched, key=lambda u: (u["path"], u["reason"])),
        "bytes_before": p.bytes_before,
        "bytes_after": p.bytes_after,
        "bytes_reclaimed": p.bytes_before - p.bytes_after,
        "case_aliases": list(p.case_aliases),
        "legacy_differs": list(p.legacy_differs),
        "overcap_keys": list(p.overcap_keys),
        "conflicts_capped": sorted(
            ({"image_id": g.image_id, "count": len(g.description["conflicts"])}
             for g in conflicted if len(g.description["conflicts"]) > _contracts.MAX_CONFLICTS),
            key=lambda c: c["image_id"]),
        "metadata_only": _count(occ.metadata_only for occ, _id in p.metadata),
        "format1_collections": sorted(p.format1_collections),
        "cancelled": p.cancelled,
    }


def _run_fields(dry_run: bool) -> dict:
    """What a run reports beyond the plan's §11 fields -- zero for a dry run,
    so a dry run and a real run have one shape (M2)."""
    return {
        "dry_run": dry_run, "outcome": _contracts.DONE, "error": None, "stopped_at": None,
        "placed": 0, "already_placed": 0, "legacy_deleted": 0, "thumbnails_removed": 0,
        "skipped": [], "errors": [], "keys_kept": [],
        "descriptions_folded": 0, "subjects_folded": 0,
        "focus_moved": 0, "focus_dropped": 0, "overrides_written": 0,
        "collections_converted": [], "collections_kept": [],
        "journals": {"converted": 0, "retired": 0, "kept": 0},
        "url_subject_keys_kept": 0, "campaigns_written": 0,
    }


def failed_report(error: str, *, dry_run: bool) -> dict:
    """A run's report when the pass raised before it could write its own: the
    full shape (M2) with every count zero and every list empty, so a reader
    of the stored report never meets a missing field. `error` is what
    `_error_text` would say -- a type, never a message."""
    return {**report(_contracts.MigrationPlan(Path())), **_run_fields(dry_run),
            "outcome": _contracts.FAILED, "error": error}
