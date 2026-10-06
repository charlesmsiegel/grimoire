"""Migrating legacy images into the image store: the plan (stage 4, spec §11).

A maintenance operation, never a startup step. This module holds the half a
dry run and a real run share (M7): `plan(root, cancel=...)` walks the
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
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import assets, image_hash, image_refs, image_store, image_subjects, image_surfaces

#: The most `description_conflicts` entries an object keeps (M9).
MAX_CONFLICTS = 20
#: Identities that are not a picture anybody can show: left where they are.
_NOT_PICTURES = frozenset({"unsniffable", "unsanitizable", "undecodable"})
_OBJECT = "object"

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
        if occ.kind == image_surfaces.COLLECTION:
            _manifest(plan, occ)
        elif occ.metadata_only is not None:
            meta.append(occ)
        elif occ.path is not None and occ.untouched is not None:
            _untouch(plan, occ.path, occ.untouched)
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
    if obj is None:
        return []
    out = []
    desc = obj.raw.get("description")
    if isinstance(desc, str):
        out.append((desc, _OBJECT))
    held = obj.raw.get("description_conflicts")
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
