"""Migrating legacy images into the image store (stage 4, spec §11).

A maintenance operation, never a startup step. `run(root, dry_run=...,
cancel=...)` is the whole of it and always returns its report; the plan below
is the half a dry run and a real run share (M7): `plan(root, cancel=...)` walks the
inventory (`image_surfaces.occurrences`), hashes every legacy file the way
ingest would, groups the files by picture and previews what folding their
metadata would do; `report(plan)` is the §11 report. The dry run is exactly
`report(plan(...))`, and it writes nothing at all: the caller persists the
report (M2).

**Pinned root (M4).** Every path is built from the `root` the caller captured
-- the placements read in each directory under it, the objects and blobs
through `image_store`'s `root=` keyword -- never from `paths.home()` asked
again, so a data-dir move mid-plan cannot splice two trees into one plan.

**What is planned.** One file per logical name per directory (the one
`assets._legacy_path` serves today); everything else is left untouched and
reported with its path and the reason:

- the inventory's own refusals (`Occurrence.untouched`): other siblings of a
  name, an extension nothing accepts, a name nothing can store;
- ``case-alias``: names in one directory that differ only by case, across
  legacy files and existing placements alike. Placing either would leave two
  placements that alias as soon as the tree syncs to a case-insensitive
  device, so neither is placed (M7, which overrides §11.B's "first wins");
- ``unreadable``, and the identities that are not pictures: bytes that do not
  sniff (``unsniffable``), that sniff and do not parse (``unsanitizable``), or
  that do not decode (``undecodable``);
- ``legacy-differs`` / ``placement-not-available``: a legacy file beside an
  image-bearing placement of its name that is another picture (or whose
  picture is not under `root` to compare). The placement is never overwritten
  (M8), so neither is touched.

**Grouping.** Files are hashed after sanitizing (so a card avatar and the same
pixels without the card's JSON are one stream) and decoded once per distinct
stream. A group is one image id. A NEW object keeps the stream most
placements use, then the smaller, then the lowest hash (`choose_retained`);
an object already under `root` keeps the blob it has. `items` is ordered so
each group's retained stream comes first: ingest keeps the first arrival.

**Metadata preview (§11.D, M9).** A group's description is previewed from the
object's own text, its existing conflicts, and every legacy key that would
fold into it -- its files' keys and the metadata-only occurrences (M6 a and
c) whose target places it. Several distinct texts are a conflict, never a
choice; past `MAX_CONFLICTS` the cap is reported. A key that is not a string,
or is over `image_store.MAX_DESCRIPTION`, stays where it is and is reported.
Subjects are unioned per scope and a disagreement is reported. These are
previews: a real run re-reads every value at fold time (M9).

**The real run (M8-M10)** -- see the section that starts at `_RootChangedError`:
per legacy file, re-read and re-hash, ingest outside every lock, then under
the campaign lock (a campaign occurrence), `image_collection_lock` (a world
library) and the name lock: never overwrite an image-bearing placement, write
one when there is none, verify it under the pinned root, unlink exactly the
file that was hashed, drop its legacy thumbnails, fold its keys. Then the
metadata-only occurrences, format-1 manifests and harvest journals. Every
campaign written is revision-bumped under its lock.

**Lock domain.** Every campaign write here happens under
`locks.campaign_lock(cid)` (`_campaign_held`), but no public function takes a
`cid` -- `run` takes a store root -- so `test_lock_domain_guard` does not
survey this module, and listing it in `DOMAIN_MODULES` would be the phantom
entry that guard refuses.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterable
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from . import (
    assets,
    atomic,
    greeting_images,
    image_collection_imports,
    image_collections,
    image_hash,
    image_refs,
    image_scopes,
    image_store,
    image_subjects,
    image_surfaces,
    locks,
    paths,
    revision,
    thumbs,
)

#: The most `description_conflicts` entries an object keeps (M9).
MAX_CONFLICTS = 20
#: Identities that are not a picture anybody can show: left where they are.
_NOT_PICTURES = frozenset({"unsniffable", "unsanitizable", "undecodable"})
_OBJECT = "object"
_MEMBER_NAME = re.compile(re.escape(image_collections.MEMBER_PREFIX) + r"[0-9a-f]{64}\Z")

Occurrence = image_surfaces.Occurrence


def work_map_path(root: Path) -> Path:
    """Where a real run keeps the plan's pending entries (M7): restartable,
    and losing it costs only time. The dry run never writes it."""
    return Path(root) / ".cache" / "image-store" / "migration" / "map.json"


@dataclass(frozen=True)
class Item:
    """One legacy file the plan places."""
    occurrence: Occurrence
    #: The sha256 of its sanitized bytes: the blob it would be.
    stream: str
    image_id: str
    #: Its sanitized size, and its size on disk when hashed.
    size: int
    raw_size: int
    #: ``(st_dev, st_ino, st_size, st_mtime_ns)`` when it was hashed, so a run
    #: can tell the file it planned from one changed since.
    stat: tuple[int, int, int, int]
    #: The image-bearing placement already holding this very picture under
    #: the name, if one does (the file is then redundant, not placed).
    placed: str | None = None


@dataclass
class Group:
    """One picture: every stream and every occurrence that is it."""
    image_id: str
    #: Its object already exists under the root.
    existing: bool = False
    #: The stream the object keeps; None for a group no file of this plan
    #: belongs to (only metadata folds into it) whose object is not there.
    retained: str | None = None
    #: stream -> how many placements use it.
    streams: dict[str, int] = field(default_factory=dict)
    #: The previewed description: ``{}`` (absent), ``{"text": t}`` or
    #: ``{"conflicts": [{text, from}, ...]}``.
    description: dict = field(default_factory=dict)
    #: The previewed subjects, per scope, sorted.
    subjects: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class MigrationPlan:
    root: Path
    items: list[Item] = field(default_factory=list)
    #: Metadata-only occurrences, each with the image id its target places
    #: (None when the target holds no picture this run can name).
    metadata: list[tuple[Occurrence, str | None]] = field(default_factory=list)
    groups: dict[str, Group] = field(default_factory=dict)
    untouched: list[dict] = field(default_factory=list)
    case_aliases: list[dict] = field(default_factory=list)
    legacy_differs: list[dict] = field(default_factory=list)
    overcap_keys: list[dict] = field(default_factory=list)
    subject_disagreements: list[dict] = field(default_factory=list)
    format1_collections: list[str] = field(default_factory=list)
    #: Legacy files inventoried (one per name per directory).
    legacy_files: int = 0
    descriptions_merged: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    cancelled: bool = False

    def rel(self, p: Path) -> str:
        """`p` as the report spells it: relative to the root, POSIX."""
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return p.as_posix()


def choose_retained(streams: dict[str, tuple[int, int]]) -> str:
    """The stream a NEW object keeps, from ``{sha: (placements, size)}``:
    most placements, then the smaller file, then the lowest hash (M7)."""
    return min(streams, key=lambda sha: (-streams[sha][0], streams[sha][1], sha))


# ---- the walk ----------------------------------------------------------------

@dataclass(frozen=True)
class _Stream:
    ext: str
    size: int
    identity: image_hash.PixelIdentity
    #: Why it is not a picture (`_NOT_PICTURES`), or None.
    refused: str | None


def _untouch(plan: MigrationPlan, path: Path, reason: str) -> None:
    plan.untouched.append({"path": plan.rel(path), "reason": reason})


def _inventory(plan: MigrationPlan, cancel: Callable[[], bool]
               ) -> tuple[list[Occurrence], list[Occurrence]] | None:
    """`(files to place, metadata-only)`; untouched files and manifests are
    recorded as they pass. None when cancelled."""
    files: list[Occurrence] = []
    meta: list[Occurrence] = []
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


def _manifest(plan: MigrationPlan, occ: Occurrence) -> None:
    assert occ.path is not None
    try:
        raw = json.loads(occ.path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        _untouch(plan, occ.path, "unreadable-manifest")
        return
    fmt = raw.get("format") if isinstance(raw, dict) else None
    if type(fmt) is int and fmt == 1:
        plan.format1_collections.append(plan.rel(occ.path))


def _aliases(plan: MigrationPlan, files: list[Occurrence],
             meta: list[Occurrence]) -> tuple[list[Occurrence], list[Occurrence]]:
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


def _member_differs(occ: Occurrence, data: bytes) -> bool:
    """A world-library file named as a format-1 collection member whose bytes
    do not hash to that name."""
    if occ.kind != image_surfaces.LIBRARY or not occ.scope.startswith("world:"):
        return False
    prefix = image_collections.MEMBER_PREFIX
    if not _MEMBER_NAME.fullmatch(occ.name.casefold()):
        return False
    return hashlib.sha256(data).hexdigest() != occ.name.casefold()[len(prefix):]


def _resolves(root: Path, image_id: str) -> bool:
    """Whether `image_id`'s object and its blob are both under `root`."""
    obj = image_store.read_fresh(image_id, root=root)
    if obj is None:
        return False
    return image_store.blob_path(obj.blob_sha256, obj.ext, root=root).is_file()


def _hash(plan: MigrationPlan, files: list[Occurrence], cancel: Callable[[], bool]) -> bool:
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
        plan.items.append(Item(occ, sha, image_id, stream.size, st.st_size,
                               (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns),
                               placed=ref.image if ref is not None else None))
    return True


# ---- groups -------------------------------------------------------------------

def _group(plan: MigrationPlan, image_id: str) -> Group:
    got = plan.groups.get(image_id)
    if got is None:
        got = plan.groups[image_id] = Group(image_id)
        obj = image_store.read_fresh(image_id, root=plan.root)
        if obj is not None:
            got.existing = True
            if image_store.blob_path(obj.blob_sha256, obj.ext, root=plan.root).is_file():
                got.retained = obj.blob_sha256
    return got


def _groups(plan: MigrationPlan) -> None:
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
            g.retained = choose_retained({s: (n, sizes[s]) for s, n in g.streams.items()})
            plan.bytes_after += sizes[g.retained]
    plan.items.sort(key=lambda i: (i.image_id, i.stream != plan.groups[i.image_id].retained,
                                   str(i.occurrence.path)))


def _slot_ids(plan: MigrationPlan) -> dict[tuple[Path, str], str]:
    return {(i.occurrence.dir, i.occurrence.name): i.image_id for i in plan.items}


def _target_id(occ: Occurrence, slots: dict[tuple[Path, str], str]) -> str | None:
    """The picture a metadata-only occurrence's target holds: its
    image-bearing placement, else the file this plan places there."""
    if occ.target is None:
        return None
    ref = image_refs.read(occ.target, occ.name)
    if ref is not None and ref.image is not None:
        return ref.image
    return slots.get((occ.target, occ.name))


def _metadata(plan: MigrationPlan, meta: list[Occurrence]) -> None:
    slots = _slot_ids(plan)
    for occ in meta:
        image_id = _target_id(occ, slots)
        plan.metadata.append((occ, image_id))
        if image_id is not None and occ.metadata_only in ("a", "c"):
            _group(plan, image_id)


# ---- metadata preview -------------------------------------------------------

def _sidecar(cache: dict[Path, dict], path: Path) -> dict:
    got = cache.get(path)
    if got is None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            raw = {}
        got = cache[path] = raw if isinstance(raw, dict) else {}
    return got


def _label(plan: MigrationPlan, occ: Occurrence) -> str:
    return f"{plan.rel(occ.dir)}/{occ.name}"


@dataclass
class _Sources:
    """Where each group's legacy metadata would come from."""
    #: image id -> [(label, sidecar path, key)] for description keys.
    texts: dict[str, list[tuple[str, Path, str]]] = field(default_factory=dict)
    #: image id -> [(scope, sidecar path, key)] for subject keys.
    subjects: dict[str, list[tuple[str, Path, str]]] = field(default_factory=dict)


def _sources(plan: MigrationPlan) -> _Sources:
    out = _Sources()
    for item in plan.items:
        occ = item.occurrence
        out.texts.setdefault(item.image_id, []).append(
            (_label(plan, occ), occ.dir / assets.DESCRIPTIONS_FILE, occ.name))
        if occ.kind == "greetings" and occ.scope.startswith("world:"):
            # A campaign greeting's subjects are never folded (M5).
            out.subjects.setdefault(item.image_id, []).append(
                (occ.scope, occ.dir / image_subjects.SUBJECTS_FILE, occ.name))
    for occ, image_id in plan.metadata:
        if image_id is None or occ.metadata_only not in ("a", "c") or occ.sidecar is None:
            continue
        where = occ.dir / occ.sidecar
        if occ.sidecar == assets.DESCRIPTIONS_FILE:
            out.texts.setdefault(image_id, []).append((_label(plan, occ), where, occ.name))
        else:
            out.subjects.setdefault(image_id, []).append((occ.scope, where, occ.name))
    return out


def _key_text(plan: MigrationPlan, cache: dict[Path, dict], where: Path,
              key: str) -> str | None:
    """A description key's text, or None when absent, or not foldable (then
    reported: it stays where it is)."""
    sidecar = _sidecar(cache, where)
    if key not in sidecar:
        return None
    value = sidecar[key]
    reason = ("not-a-string" if not isinstance(value, str)
              else "too-long" if len(value) > image_store.MAX_DESCRIPTION else None)
    if reason is not None:
        entry = {"path": plan.rel(where), "key": key, "reason": reason}
        if entry not in plan.overcap_keys:
            plan.overcap_keys.append(entry)
        return None
    return value


def _object_texts(obj: image_store.ImageObject | None) -> list[tuple[str, str]]:
    """``(text, from)`` from the object itself: its description, then the
    conflicts it already carries (never dropped here, M9)."""
    return [] if obj is None else _raw_texts(obj.raw)


def _raw_texts(raw: dict) -> list[tuple[str, str]]:
    """`_object_texts` of a sidecar's raw dict."""
    out = []
    desc = raw.get("description")
    if isinstance(desc, str):
        out.append((desc, _OBJECT))
    held = raw.get("description_conflicts")
    out.extend((c["text"], str(c.get("from", "")))
               for c in (held if isinstance(held, list) else [])
               if isinstance(c, dict) and isinstance(c.get("text"), str))
    return out


def _decide(texts: list[tuple[str, str]]) -> dict:
    """§11.D over ``(text, from)``: absent, one text (``""`` beside it or
    alone), or a conflict listing every distinct non-empty text once."""
    if not texts:
        return {}
    first: dict[str, str] = {}
    for text, src in texts:
        if text:
            first.setdefault(text, src)
    if not first:
        return {"text": ""}
    if len(first) == 1:
        return {"text": next(iter(first))}
    return {"conflicts": sorted(({"text": t, "from": s} for t, s in first.items()),
                                key=lambda c: (c["text"], c["from"]))}


def _describe(plan: MigrationPlan, sources: _Sources) -> None:
    cache: dict[Path, dict] = {}
    for image_id, keys in sorted(sources.texts.items()):
        g = plan.groups[image_id]
        texts = _object_texts(image_store.read_fresh(image_id, root=plan.root))
        folded = 0
        for label, where, key in keys:
            text = _key_text(plan, cache, where, key)
            if text is not None:
                texts.append((text, label))
                folded += 1
        g.description = _decide(texts)
        if "conflicts" not in g.description:
            plan.descriptions_merged += folded


def _list_of_str(v: object) -> list[str] | None:
    return [s for s in v if isinstance(s, str)] if isinstance(v, list) else None


def _object_subjects(obj: image_store.ImageObject | None, scope: str) -> list[str] | None:
    """The object's subjects in `scope` when it was reviewed there (R1)."""
    if obj is None:
        return None
    reviews = obj.raw.get("reviews")
    listed = reviews.get("subjects") if isinstance(reviews, dict) else None
    if not isinstance(listed, list) or scope not in listed:
        return None
    assoc = obj.raw.get("associations")
    return [a["id"] for a in (assoc if isinstance(assoc, list) else [])
            if isinstance(a, dict) and a.get("scope") == scope
            and a.get("kind") == "character" and a.get("relation") == "subject"
            and isinstance(a.get("id"), str)]


def _subjects(plan: MigrationPlan, sources: _Sources) -> None:
    cache: dict[Path, dict] = {}
    for image_id, keys in sorted(sources.subjects.items()):
        obj = image_store.read_fresh(image_id, root=plan.root)
        per_scope: dict[str, list[frozenset[str]]] = {}
        for scope, where, key in keys:
            got = _list_of_str(_sidecar(cache, where).get(key))
            if got is not None:
                per_scope.setdefault(scope, []).append(frozenset(got))
        for scope, sets in sorted(per_scope.items()):
            held = _object_subjects(obj, scope)
            if held is not None:
                sets.append(frozenset(held))
            plan.groups[image_id].subjects[scope] = sorted(frozenset().union(*sets))
            if len(set(sets)) > 1:
                plan.subject_disagreements.append(
                    {"image_id": image_id, "scope": scope,
                     "sets": sorted(sorted(s) for s in set(sets))})


# ---- plan and report ------------------------------------------------------

def _cancelled(p: MigrationPlan) -> MigrationPlan:
    """An empty plan that says it was cancelled: nothing gathered before the
    cancel survives, so nothing half-walked can be mistaken for the store."""
    return MigrationPlan(p.root, cancelled=True)


def plan(root: Path, *, cancel: Callable[[], bool] | None = None) -> MigrationPlan:
    """The migration plan for store root `root` (module docstring).

    `root` is the caller's pinned root (M4); nothing is resolved through
    `paths.home()`. `cancel` is asked between occurrences and between files:
    once it answers True the plan is empty, says ``cancelled`` and must not
    be run. Reads only -- writes nothing anywhere."""
    asked = cancel if cancel is not None else (lambda: False)
    out = MigrationPlan(Path(root))
    walked = _inventory(out, asked)
    if walked is None:
        return _cancelled(out)
    out.legacy_files = len(walked[0])
    files, meta = _aliases(out, *walked)
    if not _hash(out, files, asked):
        return _cancelled(out)
    _groups(out)
    _metadata(out, meta)
    sources = _sources(out)
    _describe(out, sources)
    _subjects(out, sources)
    return out


def _count(values: Iterable[str | None]) -> dict[str, int]:
    out = dict.fromkeys("abcd", 0)
    for v in values:
        if v is not None:
            out[v] += 1
    return out


def report(p: MigrationPlan) -> dict:
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
             for g in conflicted if len(g.description["conflicts"]) > MAX_CONFLICTS),
            key=lambda c: c["image_id"]),
        "metadata_only": _count(occ.metadata_only for occ, _id in p.metadata),
        "format1_collections": sorted(p.format1_collections),
        "cancelled": p.cancelled,
    }


# ---- the real run (M8-M10) ---------------------------------------------------
#
# Everything below writes. The one rule it is arranged around: the only working
# copy of a picture is never deleted. A legacy file goes only once a placement
# naming its picture has been read back under the PINNED root and its blob
# re-hashed there; it goes by identity (the exact file that was hashed, by
# dev/inode/size/mtime), never by name; and the live root is asked to still be
# the pinned one before every destructive step (M4), so a data-dir move mid-run
# stops it with nothing deleted from then on, in either tree.

#: How a run ended (``outcome`` in its report).
DONE, CANCELLED, FAILED, ROOT_CHANGED = "done", "cancelled", "failed", "root-changed"
#: The ``kind`` a stored migration report carries (`maintenance_reports`).
KIND = "image-migration"
#: How many times one key's fold is retried when its value moves under it.
_FOLD_TRIES = 4
_SUBJECTS = image_subjects.SUBJECTS_FILE
_KIND, _RELATION = "character", "subject"


class _RootChangedError(Exception):
    """The live store root is no longer the pinned one: stop, delete nothing."""

    def __init__(self, step: str):
        super().__init__(step)
        self.step = step


def _run_fields(dry_run: bool) -> dict:
    """What a run reports beyond the plan's §11 fields -- zero for a dry run,
    so a dry run and a real run have one shape (M2)."""
    return {
        "dry_run": dry_run, "outcome": DONE, "error": None, "stopped_at": None,
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
    return {**report(MigrationPlan(Path())), **_run_fields(dry_run),
            "outcome": FAILED, "error": error}


def _linked_slot(root: Path, d: Path, name: str | None = None) -> bool:
    """Whether a write in `d` could land outside `root`: `d` reached through
    a symlink (`image_surfaces.linked`), its ``image-refs/`` folder a symlink,
    or -- for `name` -- that placement file itself one. `linked` checks the
    components from the root down to `d` only; a placement is written one
    and two levels below it, and a link there would put the placement the
    legacy file is then deleted behind somewhere else entirely."""
    if image_surfaces.linked(root, d) or (d / image_refs.REFS_DIR).is_symlink():
        return True
    try:
        return name is not None and image_refs.ref_path(d, name).is_symlink()
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


def _campaign_of(occ: Occurrence) -> str | None:
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
def _collection_held(occ: Occurrence):
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
    plan: MigrationPlan
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

    @property
    def root(self) -> Path:
        return self.plan.root

    def guard(self, step: str) -> None:
        if not _same_root(self.root):
            raise _RootChangedError(step)

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


# ---- one legacy file ------------------------------------------------------------

def _write_placement(d: Path, name: str, image_id: str, focus: int | None) -> None:
    """Publish the placement (a named step, so a crash can be injected here)."""
    image_refs.write(d, name, image_id, focus=focus)


def _hashes_to(p: Path, sha: str) -> bool:
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return False
    return h.hexdigest() == sha


def _object_under(root: Path, image_id: str, blob: str | None = None) -> bool:
    """Whether `image_id`'s object is under `root` (retaining `blob`, when
    named) and its blob there re-hashes to its name."""
    obj = image_store.read_fresh(image_id, root=root)
    if obj is None or (blob is not None and obj.blob_sha256 != blob):
        return False
    return _hashes_to(image_store.blob_path(obj.blob_sha256, obj.ext, root=root),
                      obj.blob_sha256)


def _verify(root: Path, d: Path, name: str, image_id: str, blob: str | None,
            focus: int | None) -> bool:
    """M8's verify, under the pinned root and never through `path_in`: the
    placement reads back as written, its object is under `root` retaining the
    intended blob, and that blob re-hashes."""
    ref = image_refs.read(d, name)
    if ref is None or ref.image != image_id or ref.focus != focus:
        return False
    return _object_under(root, image_id, blob)


def _focus_file(d: Path) -> int | None:
    """The legacy crop in `d`'s `focus.json` (as `assets` reads it), or None
    for none, a garbled one, or one reached through a symlink."""
    p = d / assets.FOCUS_FILE
    if p.is_symlink():
        return None
    try:
        val = json.loads(p.read_text(encoding="utf-8"))
        v = val.get(assets.AVATAR) if isinstance(val, dict) else None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return max(0, min(100, int(v)))
    except (OSError, ValueError, OverflowError):
        return None


def _unlink_same(ex: _Exec, p: Path, snap: tuple[int, int, int, int]) -> bool:
    """Unlink exactly the file that was hashed: the same regular file by
    dev, inode, size and mtime, in a directory not reached through a link."""
    if not ex.guard_dir("before-unlink", p.parent):
        return False
    st = _lstat(p)
    if st is None or not stat.S_ISREG(st.st_mode) or _snap(st) != snap:
        return False
    p.unlink()
    ex.wrote = True
    return True


def _drop_thumbs(ex: _Exec, rel: str, st: os.stat_result) -> None:
    """The legacy file's current-generation thumbnails (§11.F)."""
    ex.guard("before-thumbnails")
    for key in thumbs.legacy_keys(rel, st):
        p = ex.root / key
        if p.is_file() and not image_surfaces.linked(ex.root, p):
            with suppress(FileNotFoundError):
                p.unlink()
                ex.out["thumbnails_removed"] += 1


def _drop_focus_file(ex: _Exec, d: Path, moved: bool) -> bool:
    """`focus.json` beside a verified avatar placement, which answers for the
    crop from now on (M9 d). Caller holds the AVATAR name lock."""
    p = d / assets.FOCUS_FILE
    if p.is_symlink():
        ex.keep(p, assets.AVATAR, "symlinked-sidecar")
        return False
    if not p.is_file() or not ex.guard_dir("before-focus-delete", d):
        return False
    p.unlink()
    ex.wrote = True
    ex.out["focus_moved" if moved else "focus_dropped"] += 1
    return True


def _move_focus(ex: _Exec, d: Path, focus: int | None) -> bool:
    """`focus.json` after its crop moved onto a placement written over a
    legacy avatar: dropped only while it still reads as that crop. One that
    did not read (unreadable, garbled, changed since) stays, reported: the
    placement was written without it, and it is the only copy of the crop."""
    p = d / assets.FOCUS_FILE
    if not (p.exists() or p.is_symlink()):
        return False
    if focus is None or _focus_file(d) != focus:
        ex.keep(p, assets.AVATAR, "focus-not-moved")
        return False
    return _drop_focus_file(ex, d, True)


def _reread(ex: _Exec, item: Item) -> bytes | None:
    """The item's file again, when it is still exactly what was hashed."""
    assert item.occurrence.path is not None
    got = _read(item.occurrence.path)
    if (got is None or _snap(got[1]) != item.stat
            or image_store.prepare(got[0], item.occurrence.path.suffix).sha != item.stream):
        ex.skip(item.occurrence.path, "changed-since-hashing")
        return None
    return got[0]


def _item(ex: _Exec, item: Item) -> None:
    """Ingest, place, verify, delete and fold one legacy file (M8)."""
    occ = item.occurrence
    assert occ.path is not None
    rel = ex.plan.rel(occ.path)
    if not ex.guard_dir("before-ingest", occ.dir, occ.name):
        # Not even a promotion recovery: it writes placements in there.
        ex.skip(occ.path, image_surfaces.SYMLINKED)
        return
    assets.recover_promotion(occ.dir)
    data = _reread(ex, item)
    if data is None:
        return
    _pend(ex, item.image_id, rel)
    # Outside every lock: the decode is the slow part (M8.2).
    obj = image_store.ingest(data, occ.path.suffix)
    ex.guard("after-ingest")
    if obj.id != item.image_id:
        ex.skip(occ.path, "identity-changed")
    elif not _object_under(ex.root, obj.id, obj.blob_sha256):
        ex.skip(occ.path, "not-under-root")
    else:
        with _campaign_write(ex, _campaign_of(occ)), _collection_held(occ), \
                locks.image_name_lock(occ.dir, occ.name):
            if _place_and_clean(ex, item, obj.blob_sha256):
                ex.wrote = True
    # Not in a `finally`: a process that dies mid-item leaves the entry, and
    # with it the object it ingested, a GC root until a run completes.
    _unpend(ex, item.image_id, rel)


def _placement_for(ex: _Exec, item: Item) -> tuple[bool, int | None, bool] | None:
    """Under the name lock: `(written, focus, written over a legacy avatar)`
    once a placement of the item's picture is there -- written now when there
    was none -- or None when the slot holds another picture, which is never
    overwritten (M8.3). An image-less placement (a crop override) is filled
    with the picture under its own crop."""
    occ = item.occurrence
    assert occ.path is not None
    ref = image_refs.read(occ.dir, occ.name)
    if ref is not None and ref.image is not None:
        if ref.image != item.image_id:
            ex.plan.legacy_differs.append({"path": ex.plan.rel(occ.path), "placement": ref.image,
                                           "reason": "concurrent-placement"})
            ex.skip(occ.path, "legacy-differs")
            return None
        ex.out["already_placed"] += 1
        return False, ref.focus, False
    legacy = ref is None and occ.name == assets.AVATAR
    focus = ref.focus if ref is not None else (_focus_file(occ.dir) if legacy else None)
    _write_placement(occ.dir, occ.name, item.image_id, focus)
    ex.wrote = True
    ex.out["placed"] += 1
    return True, focus, legacy


def _place_and_clean(ex: _Exec, item: Item, blob: str) -> bool:
    """Everything under the item's locks; whether anything was written."""
    occ = item.occurrence
    assert occ.path is not None
    rel = ex.plan.rel(occ.path)
    now = _lstat(occ.path)
    if not ex.guard_dir("before-place", occ.dir, occ.name):
        ex.skip(occ.path, image_surfaces.SYMLINKED)
        return False
    if now is None or not stat.S_ISREG(now.st_mode) or _snap(now) != item.stat:
        ex.skip(occ.path, "changed-since-hashing")
        return False
    if image_refs.read_journal(occ.dir) is not None:
        # A promotion the recovery could not finish: a write now could make
        # its journal stale, and with it lose the picture it moved (spec §8).
        ex.skip(occ.path, "promotion-unfinished")
        return False
    placed = _placement_for(ex, item)
    if placed is None:
        return False
    written, focus, moved = placed
    if not _verify(ex.root, occ.dir, occ.name, item.image_id, blob, focus):
        ex.skip(occ.path, "verify-failed")
        return written
    ex.guard("after-verify")
    ex.verified.add((occ.dir, occ.name))
    _unpend(ex, item.image_id, rel)
    if _unlink_same(ex, occ.path, item.stat):
        ex.out["legacy_deleted"] += 1
        written = True
        _drop_thumbs(ex, rel, now)
    if occ.name == assets.AVATAR:
        dropped = (_move_focus(ex, occ.dir, focus) if moved
                   else _drop_focus_file(ex, occ.dir, False))
        written = dropped or written
    return _fold_keys(ex, occ, item.image_id) or written


# ---- folding keys (M9) ------------------------------------------------------------

def _sidecar_now(ex: _Exec, side: Path) -> dict | None:
    """A sidecar as it is now, or None (reported) for one reached through a
    symlink, which is never followed, rewritten or deleted."""
    if side.is_symlink():
        ex.keep(side, None, "symlinked-sidecar")
        return None
    try:
        raw = json.loads(side.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError):
        ex.keep(side, None, "unreadable-sidecar")
        return None
    return raw if isinstance(raw, dict) else None


def _delete_key_if(ex: _Exec, side: Path, key: str, value: object) -> bool | None:
    """Compare-and-delete: drop `key` only while it still holds `value`; an
    emptied sidecar goes with it. True when deleted, None when the key is
    already gone, False when it now holds something else (fold again)."""
    if not ex.guard_dir("before-key-delete", side.parent):
        ex.keep(side, key, image_surfaces.SYMLINKED)
        return None
    cur = _sidecar_now(ex, side)
    if cur is None or key not in cur:
        return None if cur is not None else False
    if cur[key] != value:
        return False
    del cur[key]
    if cur:
        atomic.write_text(side, json.dumps(cur, indent=2, sort_keys=True) + "\n")
    else:
        side.unlink()
    ex.wrote = True
    return True


def _unfoldable(value: object) -> str | None:
    if not isinstance(value, str):
        return "not-a-string"
    return "too-long" if len(value) > image_store.MAX_DESCRIPTION else None


def _with_text(raw: dict, value: str, label: str, state: dict) -> dict | None:
    """The `update` callback folding one description key (§11.D): a pure edit."""
    decided = _decide([*_raw_texts(raw), (value, label)])
    new = dict(raw)
    if "conflicts" in decided:
        if len(decided["conflicts"]) > MAX_CONFLICTS:
            state["outcome"] = "capped"
            return None
        new.pop("description", None)
        new["description_conflicts"] = decided["conflicts"]
    else:
        new["description"] = decided["text"]
        new.pop("description_conflicts", None)
    state["outcome"] = "held" if new == raw else "written"
    return None if new == raw else new


def _says(root: Path, image_id: str, value: str) -> bool:
    """Whether the object under `root` now carries `value`: as its text or a
    conflict -- or, for ``""``, any text at all."""
    obj = image_store.read_fresh(image_id, root=root)
    texts = {t for t, _src in _raw_texts(obj.raw)} if obj is not None else set()
    return bool(texts) if value == "" else value in texts


def _fold_text_once(ex: _Exec, side: Path, key: str, image_id: str, label: str
                    ) -> bool | None:
    """One attempt: True folded and deleted, None stop (kept or gone), False
    the key moved under the delete."""
    cur = _sidecar_now(ex, side)
    if cur is None or key not in cur:
        return None
    value = cur[key]
    why = _unfoldable(value)
    if why is not None:
        ex.keep(side, key, why)
        return None
    assert isinstance(value, str)
    ex.guard("before-fold")
    state = {"outcome": "absent"}
    image_store.update(image_id, lambda raw: _with_text(raw, value, label, state))
    if state["outcome"] in ("absent", "capped"):
        ex.keep(side, key, "conflicts-capped" if state["outcome"] == "capped"
                else "object-absent")
        return None
    if not _says(ex.root, image_id, value):
        ex.keep(side, key, "fold-not-confirmed")
        return None
    done = _delete_key_if(ex, side, key, value)
    if done:
        ex.out["descriptions_folded"] += 1
    return done


def _fold_description(ex: _Exec, d: Path, key: str, image_id: str, label: str) -> bool:
    """Fold `d`'s `descriptions.json[key]` into `image_id` under the sidecar's
    lock, held from the read through the delete (M9); whether it changed."""
    side = d / assets.DESCRIPTIONS_FILE
    with locks.image_sidecar_lock(d, assets.DESCRIPTIONS_FILE):
        for _ in range(_FOLD_TRIES):
            done = _fold_text_once(ex, side, key, image_id, label)
            if done is not False:
                return bool(done)
        ex.keep(side, key, "changed-during-fold")
    return False


def _ours(a: object, scope: str) -> bool:
    return (isinstance(a, dict) and a.get("kind") == _KIND
            and a.get("relation") == _RELATION and a.get("scope") == scope)


def _with_subjects(raw: dict, scope: str, cids: list[str]) -> dict | None:
    """The `update` callback unioning `cids` into `scope`'s subjects and
    marking it reviewed (§11.D); None when that changes nothing."""
    assoc = raw.get("associations")
    assoc = list(assoc) if isinstance(assoc, list) else []
    have = {a["id"] for a in assoc if _ours(a, scope) and isinstance(a.get("id"), str)}
    assoc += [{"kind": _KIND, "relation": _RELATION, "scope": scope, "id": c}
              for c in sorted(set(cids) - have)]
    reviews = raw.get("reviews")
    reviews = dict(reviews) if isinstance(reviews, dict) else {}
    done = reviews.get("subjects")
    done = [s for s in done if isinstance(s, str)] if isinstance(done, list) else []
    if set(cids) <= have and scope in done:
        return None
    reviews["subjects"] = sorted({*done, scope})
    return {**raw, "associations": assoc, "reviews": reviews}


def _tags(root: Path, image_id: str, scope: str) -> set[str] | None:
    """The object's subjects in `scope` under `root`, None when unreviewed."""
    got = _object_subjects(image_store.read_fresh(image_id, root=root), scope)
    return None if got is None else set(got)


def _fold_subjects_once(ex: _Exec, side: Path, key: str, image_id: str, scope: str
                        ) -> bool | None:
    cur = _sidecar_now(ex, side)
    if cur is None or key not in cur:
        return None
    value = cur[key]
    if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
        ex.keep(side, key, "not-a-list-of-ids")
        return None
    ex.guard("before-fold")
    before = _tags(ex.root, image_id, scope)
    image_store.update(image_id, lambda raw: _with_subjects(raw, scope, value))
    after = _tags(ex.root, image_id, scope)
    if after is None or not set(value) <= after:
        ex.keep(side, key, "fold-not-confirmed")
        return None
    if before is not None and before != set(value):
        ex.plan.subject_disagreements.append(
            {"image_id": image_id, "scope": scope,
             "sets": sorted([sorted(before), sorted(set(value))])})
    done = _delete_key_if(ex, side, key, value)
    if done:
        ex.out["subjects_folded"] += 1
    return done


def _fold_subjects(ex: _Exec, d: Path, key: str, image_id: str, scope: str) -> bool:
    """`_fold_description` for a world greeting's `subjects.json[key]`."""
    side = d / _SUBJECTS
    with locks.image_sidecar_lock(d, _SUBJECTS):
        for _ in range(_FOLD_TRIES):
            done = _fold_subjects_once(ex, side, key, image_id, scope)
            if done is not False:
                return bool(done)
        ex.keep(side, key, "changed-during-fold")
    return False


def _subjects_folded_here(occ: Occurrence) -> bool:
    """Subjects fold only in a WORLD greeting (M5)."""
    return occ.kind == "greetings" and occ.scope.startswith("world:")


def _fold_keys(ex: _Exec, occ: Occurrence, image_id: str) -> bool:
    """A verified file's own keys, in its own directory."""
    changed = _fold_description(ex, occ.dir, occ.name, image_id, _label(ex.plan, occ))
    if _subjects_folded_here(occ):
        changed = _fold_subjects(ex, occ.dir, occ.name, image_id, occ.scope) or changed
    return changed


# ---- metadata-only occurrences (M6 a-d, M9) -------------------------------------------

def _target_verified(ex: _Exec, occ: Occurrence, image_id: str) -> bool:
    """Whether the occurrence's target placement verified: in this run, or
    now, under the pinned root."""
    if occ.target is None:
        return False
    if (occ.target, occ.name) in ex.verified:
        return True
    if _linked_slot(ex.root, occ.target, occ.name):
        return False
    ref = image_refs.read(occ.target, occ.name)
    return (ref is not None and ref.image == image_id
            and _object_under(ex.root, image_id))


def _override(ex: _Exec, occ: Occurrence) -> bool:
    """(b): a campaign's bare `focus.json` over an inherited avatar becomes
    the image-less occurrence override, under the campaign lock (held by the
    caller) and the campaign directory's AVATAR name lock -- only while no
    avatar of the campaign's own is there."""
    d = occ.dir
    with locks.image_name_lock(d, assets.AVATAR):
        side = d / assets.FOCUS_FILE
        if image_refs.read(d, assets.AVATAR) is not None or assets.has_legacy(d, assets.AVATAR):
            ex.keep(side, assets.AVATAR, "campaign-avatar-exists")
            return False
        focus = _focus_file(d)
        if focus is None:
            ex.keep(side, assets.AVATAR, "unreadable-focus")
            return False
        if not ex.guard_dir("before-override", d, assets.AVATAR):
            ex.keep(side, assets.AVATAR, image_surfaces.SYMLINKED)
            return False
        image_refs.write(d, assets.AVATAR, None, focus=focus)
        ex.wrote = True
        if image_refs.read(d, assets.AVATAR) != image_refs.Ref(assets.AVATAR, None, focus):
            ex.keep(side, assets.AVATAR, "fold-not-confirmed")
            return True
        ex.out["overrides_written"] += 1
        if _focus_file(d) == focus and ex.guard_dir("before-focus-delete", d):
            side.unlink()
        return True


def _dropped_focus(ex: _Exec, occ: Occurrence) -> bool:
    """(d): the placement's focus is authoritative; `focus.json` goes, under
    the AVATAR name lock, and nothing folds."""
    with locks.image_name_lock(occ.dir, assets.AVATAR):
        ref = image_refs.read(occ.dir, assets.AVATAR)
        if ref is None or ref.image is None or _linked_slot(ex.root, occ.dir, assets.AVATAR):
            return False            # gone meanwhile, or not ours to trust: leave the crop
        return _drop_focus_file(ex, occ.dir, False)


def _metadata_one(ex: _Exec, occ: Occurrence, image_id: str | None) -> None:
    side = occ.dir / (occ.sidecar or "")
    if image_id is None or not _target_verified(ex, occ, image_id):
        ex.keep(side, occ.name, "target-not-verified")
        return
    with _campaign_write(ex, _campaign_of(occ)):
        if occ.metadata_only == "b":
            changed = _override(ex, occ)
        elif occ.metadata_only == "d":
            changed = _dropped_focus(ex, occ)
        elif occ.sidecar == _SUBJECTS:
            changed = _fold_subjects(ex, occ.dir, occ.name, image_id, occ.scope)
        else:
            changed = _fold_description(ex, occ.dir, occ.name, image_id, _label(ex.plan, occ))
        if changed:
            ex.wrote = True


# ---- collections and journals (M10) -----------------------------------------------------

def _world_roots(root: Path) -> list[Path]:
    """Every world directory under `root` the walk would enter."""
    try:
        found = sorted(p for p in (root / "worlds").iterdir() if p.is_dir())
    except OSError:
        return []
    return [p for p in found if paths.safe_id(p.name) and not image_surfaces.linked(root, p)]


def _greeting_dirs(root: Path) -> list[tuple[Path, str, Path]]:
    """``(world root, greeting id, its image dir)`` for every world greeting
    image directory holding a `subjects.json`."""
    out = []
    for wroot in _world_roots(root):
        try:
            gids = sorted(p.name for p in (wroot / "greetings").iterdir() if p.is_dir())
        except OSError:
            continue
        for gid in gids:
            try:
                d = assets.version_dir(wroot, gid, "default", base="greetings")
            except ValueError:
                continue
            if (d / _SUBJECTS).is_file() and not image_surfaces.linked(root, d):
                out.append((wroot, gid, d))
    return out


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


@dataclass(frozen=True)
class _Member:
    """One converted member: its library name, index and image id."""
    name: str
    index: int
    image_id: str


def _member_keys(wroot: Path, gid: str, wid: str, collection: str, m: _Member
                 ) -> tuple[str, str]:
    """``(library-URL key, member-URL key)`` as a greeting's catalog spells them."""
    lib = greeting_images.image_key(wroot, gid, f"/api/worlds/{wid}/images/{m.name}")
    member = greeting_images.image_key(
        wroot, gid, f"/api/worlds/{wid}/image-collections/{collection}/members/{m.index}")
    return lib, member


def _carry_member_key(ex: _Exec, cur: dict, same: bool, scope: str, keys: tuple[str, str],
                      m: _Member) -> bool | None:
    """Make the member key say what the library key says: on the object for
    the same world, in the sidecar (`cur`, written by the caller) for another
    (R11). True when `cur` changed, None when the library key cannot move."""
    value = cur[keys[0]]
    if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
        return None
    if same:
        image_store.update(m.image_id, lambda raw: _with_subjects(raw, scope, value))
        tags = _tags(ex.root, m.image_id, scope)
        return False if tags is not None and set(value) <= tags else None
    if keys[1] not in cur:
        cur[keys[1]] = list(value)
        return True
    return False if cur[keys[1]] == value else None


def _rekey_one(ex: _Exec, where: tuple[Path, str, Path], wid: str, collection: str,
               members: list[_Member], delete: bool) -> None:
    """One greeting's library-URL keys for `members`, under its sidecar lock:
    carried to the member (before the manifest is written), and -- once it is,
    `delete` -- dropped where the member holds them and the library URL is
    no longer one of the greeting's pictures (M10)."""
    wroot, gid, d = where
    side = d / _SUBJECTS
    same = _same_dir(wroot, ex.root / "worlds" / wid)
    scope = image_scopes.world_scope_of_dir(wroot.name)
    with locks.image_sidecar_lock(d, _SUBJECTS):
        cur = _sidecar_now(ex, side)
        if not cur:
            return
        shown = set(greeting_images.catalog_with_slots(wroot, gid)) if delete else set()
        dirty = False
        for m in members:
            keys = _member_keys(wroot, gid, wid, collection, m)
            if keys[0] not in cur:
                continue
            carried = _carry_member_key(ex, cur, same, scope, keys, m)
            if carried is None:
                ex.keep(side, keys[0], "member-key-not-carried")
                continue
            dirty = carried or dirty
            if delete and keys[0] not in shown:
                del cur[keys[0]]
                dirty = True
        if dirty and ex.guard_dir("before-key-delete", d):
            if cur:
                atomic.write_text(side, json.dumps(cur, indent=2, sort_keys=True) + "\n")
            else:
                side.unlink()


def _rekey(ex: _Exec, wid: str, collection: str, members: list[_Member], *,
           delete: bool) -> None:
    for where in _greeting_dirs(ex.root):
        _rekey_one(ex, where, wid, collection, members, delete)


def _converted_members(ex: _Exec, wid: str, ids: list[str]) -> list[_Member]:
    """The members of a format-2 manifest that a format-1 one named by their
    library placements: what its greetings' library-URL keys pointed at."""
    lib = ex.root / "worlds" / wid / "assets" / "images"
    by_id = {r.image: n for n, r in image_refs.scan(lib).items()
             if r.image is not None and _MEMBER_NAME.fullmatch(n)}
    return [_Member(by_id[i], n, i) for n, i in enumerate(ids) if i in by_id]


def _convert(ex: _Exec, rel: str) -> None:
    path = ex.root / rel
    wid, collection = path.parent.parent.parent.name, path.stem
    if not ex.guard_dir("before-collection", path.parent):
        ex.out["collections_kept"].append({"path": rel, "reason": image_surfaces.SYMLINKED})
        return

    def carry(names: list[str], ids: list[str]) -> None:
        members = [_Member(n, i, x) for i, (n, x) in enumerate(zip(names, ids, strict=True))]
        _rekey(ex, wid, collection, members, delete=False)

    try:
        if image_collections.convert_format1(ex.root, wid, collection, before=carry,
                                             guard=partial(ex.guard, "before-manifest")):
            ex.out["collections_converted"].append(rel)
    except image_collections.CollectionInvalidError as exc:
        ex.out["collections_kept"].append({"path": rel, "reason": str(exc)})


def _format2_manifests(ex: _Exec) -> list[tuple[str, str, list[str]]]:
    """``(world, collection id, member ids)`` of every format-2 manifest
    under the pinned root."""
    out = []
    for wroot in _world_roots(ex.root):
        d = wroot.joinpath(*image_surfaces.COLLECTIONS_DIR)
        if image_surfaces.linked(ex.root, d):
            continue
        for p in sorted(d.glob("*.json")):
            try:
                got = image_collections.validate(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError, RecursionError):
                continue
            if got["format"] == 2:
                out.append((wroot.name, p.stem, got["members"]))
    return out


def _settle_converted(ex: _Exec) -> None:
    """Drop the greeting keys that named a converted member's library URL,
    where the member now holds them (M10). Over EVERY format-2 manifest whose
    members a format-1 one named, not only this run's: a run that stopped
    between a manifest and this pass leaves keys a rerun has to find."""
    for wid, collection, ids in _format2_manifests(ex):
        members = _converted_members(ex, wid, ids)
        if members:
            _rekey(ex, wid, collection, members, delete=True)


def _collections(ex: _Exec) -> None:
    """Every format-1 manifest the plan listed, converted where M10 allows,
    then the greeting keys the conversions moved."""
    for rel in ex.plan.format1_collections:
        if ex.cancel():
            ex.out["outcome"] = CANCELLED
            return
        _attempt(ex, ex.root / rel, partial(_convert, ex, rel))
    _attempt(ex, None, partial(_settle_converted, ex))


def _journals(ex: _Exec) -> None:
    """Leftover format-1 harvest journals, converted or retired (M10)."""
    base = ex.root / ".cache" / "image-collection-imports"
    try:
        wids = sorted(p.name for p in base.iterdir() if p.is_dir() and paths.safe_id(p.name))
    except OSError:
        return
    for wid in wids:
        d = image_collections.raw_journal_directory(ex.root, wid)
        if image_surfaces.linked(ex.root, d):
            continue
        for p in sorted(d.glob("*.json")):
            _attempt(ex, p, partial(_journal, ex, wid, p))


def _journal(ex: _Exec, wid: str, p: Path) -> None:
    if ex.guard_dir("before-journal", p.parent):
        got = image_collection_imports.convert_or_retire(
            wid, p, guard=partial(ex.guard, "before-journal-retire"))
        if got != image_collection_imports.FORMAT2:
            ex.out["journals"][got] += 1


def _url_keys_kept(root: Path) -> int:
    """Same-world URL keys in world greetings' `subjects.json`: no ruling
    folds them, so they stay, and are counted (stage-4 Task 8 records it)."""
    count = 0
    for wroot, _gid, d in _greeting_dirs(root):
        try:
            raw = json.loads((d / _SUBJECTS).read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        prefix = f"/api/worlds/{wroot.name}/"
        count += sum(1 for k in (raw if isinstance(raw, dict) else {}) if k.startswith(prefix))
    return count


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
    except _RootChangedError:
        raise
    except Exception as exc:  # noqa: BLE001 -- reported per step; the rest still runs
        ex.out["errors"].append({"path": ex.plan.rel(where) if where is not None else None,
                                 "error": _error_text(exc)})


def _stopped(ex: _Exec) -> bool:
    if ex.cancel():
        ex.out["outcome"] = CANCELLED
        return True
    return False


def _execute(ex: _Exec) -> None:
    ex.guard("start")
    ex.pending = _safe_map(ex.root)
    for item in ex.plan.items:
        if _stopped(ex):
            return
        _attempt(ex, item.occurrence.path, partial(_item, ex, item))
    for occ, image_id in ex.plan.metadata:
        if _stopped(ex):
            return
        _attempt(ex, occ.dir / (occ.sidecar or ""), partial(_metadata_one, ex, occ, image_id))
    _collections(ex)
    if ex.out["outcome"] != DONE or _stopped(ex):
        return
    _attempt(ex, None, partial(_journals, ex))
    ex.guard("before-map-delete")
    if not image_surfaces.linked(ex.root, work_map_path(ex.root)):
        work_map_path(ex.root).unlink(missing_ok=True)


def _safe_map(root: Path) -> dict[str, list[str]]:
    """The work map a crashed run left (its entries stay roots until this run
    completes), or nothing for one that does not read -- it is rewritten."""
    try:
        return _load_map(root)
    except ValueError:
        return {}


def run(root: Path, *, dry_run: bool, cancel: Callable[[], bool] | None = None) -> dict:
    """Migrate the legacy images under store root `root` (spec §11, M7-M10);
    the report, always -- on success, on cancel and on failure alike.

    `root` is the pinned root (``paths.home().resolve()`` as the run captured
    it, M4); a live root that is not it stops the run before anything is
    written, and one that moves mid-run stops it with nothing deleted after.
    A dry run is the plan's report and writes nothing. `cancel` is asked
    between files and between steps; a cancelled run reports what it did.

    The report is the plan's §11 fields plus what the run did
    (`_run_fields`), with ``outcome`` one of ``done``, ``cancelled``,
    ``failed`` (``error`` says why) or ``root-changed`` (``stopped_at`` says
    where). A step that fails is listed under ``errors`` and the run goes on.
    Persisting the report is the caller's (M2)."""
    root = Path(root)
    asked = cancel if cancel is not None else (lambda: False)
    fields = _run_fields(dry_run)
    p = MigrationPlan(root)
    try:
        if not _same_root(root):
            raise _RootChangedError("start")
        p = plan(root, cancel=asked)
        if p.cancelled:
            fields["outcome"] = CANCELLED
        elif not dry_run:
            ex = _Exec(p, asked, fields)
            try:
                _execute(ex)
            finally:
                fields["campaigns_written"] = len(ex.written)
        fields["url_subject_keys_kept"] = _url_keys_kept(root)
    except _RootChangedError as exc:
        fields["outcome"], fields["stopped_at"] = ROOT_CHANGED, exc.step
    except Exception as exc:  # noqa: BLE001 -- a failed run still reports (M1)
        fields["outcome"], fields["error"] = FAILED, _error_text(exc)
    return {**report(p), **fields}
