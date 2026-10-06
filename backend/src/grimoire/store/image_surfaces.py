"""Where legacy images live: the migration's inventory (stage 4, M6).

Every surface that stores an image is listed here, in `SURFACES`, and walked
under a store root the caller hands in -- a maintenance run's pinned root
(M4), never `paths.home()` asked again. The directories come from the code's
own path builders and directory names (`assets.version_dir` per base,
`world_images.DIRNAME`, `campaign_images.DIRNAME`, `covers.NAME`), and
`tests/test_image_surfaces.py` holds this roster to the one that drives every
surface end to end, so a surface added there and forgotten here fails.

`occurrences(root)` yields, per directory:

- **a legacy file** to place: the one file per logical name that
  `assets._legacy_path` would serve (library and cover directories with
  `supported_only`, as their own modules read them), sidecars excepted;
- **a legacy file left alone** (`untouched` says why): a name's other
  siblings, an extension nothing accepts, a name nothing can store;
- **a collection manifest** (kind ``collection``), whose format the planner
  reads;
- **a metadata-only occurrence**, which has no file of its own (M6):

  - ``a``: a campaign `descriptions.json` key for an image the campaign
    inherits and the overlay would show -- not tombstoned, not hidden, the
    record not detached;
  - ``b``: a campaign's bare `focus.json` (no avatar of its own, no avatar
    placement) over an inherited avatar the overlay would show;
  - ``c``: a `descriptions.json` or `subjects.json` key whose name has an
    image-bearing placement, and no legacy file, in the same directory;
  - ``d``: a `focus.json` beside an avatar placement.

  ``target`` names the directory whose placement the key or crop describes:
  the world's for ``a``/``b``, its own for ``c``/``d``.

A rostered directory reached through a symlink anywhere between the root and
itself is never walked into: it is one occurrence, untouched as
``symlinked-directory``, because what it holds may lie outside the pinned
root, where a run would unlink it.

Read-only: nothing here writes, and nothing resolves through the live root.
Visibility for ``a``/``b`` is decided from the campaign's own ledgers under
`root` (`deleted.json`, `detached.json`, the `world` in `campaign.md`), by the
overlay's rules and ref shapes. A ledger that does not read hides everything
inherited: a key nobody can prove visible is left where it is, which is the
direction a migration may fail in.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from . import (
    assets,
    campaign_images,
    covers,
    entities,
    image_refs,
    image_scopes,
    image_subjects,
    overlay,
    pcs,
    world_images,
)
from .frontmatter import parse_frontmatter_head
from .paths import safe_id

#: Occurrence kinds beside the version-directory bases.
LIBRARY = "library"
COVER = "cover"
COLLECTION = "collection"
#: Where a world's collection manifests sit under its root, as
#: `image_collections.directory` builds it (the roster test checks the two
#: agree; that builder resolves the live root, so it cannot be called here).
COLLECTIONS_DIR = ("assets", "image-collections")
#: Sidecars that live beside images and are not images themselves.
SIDECARS = frozenset({assets.DESCRIPTIONS_FILE, assets.FOCUS_FILE,
                      image_subjects.SUBJECTS_FILE})

_GREETINGS = "greetings"
_CHARACTERS = "characters"
_VERSION_BASES = (_CHARACTERS, pcs.ASSET_BASE, *entities.ENTITY_KINDS)
_WORLD, _CAMPAIGN = "world", "campaign"


@dataclass(frozen=True)
class Surface:
    #: The id the surface roster test knows it by.
    id: str
    #: Which roots carry it: "world", "campaign" or both.
    scopes: tuple[str, ...]
    #: The occurrence kind: a version-directory base, or LIBRARY/COVER/COLLECTION.
    kind: str

    @property
    def supported_only(self) -> bool:
        """Read as its own module reads it: a library or cover directory is
        one a human browses, so only accepted extensions count."""
        return self.kind in (LIBRARY, COVER)


def _roster() -> dict[str, Surface]:
    out = {f"{scope}-{base}": Surface(f"{scope}-{base}", (scope,), base)
           for scope in (_WORLD, _CAMPAIGN) for base in _VERSION_BASES}
    for scope in (_WORLD, _CAMPAIGN):
        out[f"{scope}-library"] = Surface(f"{scope}-library", (scope,), LIBRARY)
        out[f"{scope}-cover"] = Surface(f"{scope}-cover", (scope,), COVER)
    out["collection-member"] = Surface("collection-member", (_WORLD,), COLLECTION)
    # A campaign greeting holds art only by `overlay.copy_record_dir_down`
    # (no writer of its own), and is walked like the world's.
    out["greeting"] = Surface("greeting", (_WORLD, _CAMPAIGN), _GREETINGS)
    return out


SURFACES: dict[str, Surface] = _roster()


@dataclass(frozen=True)
class Occurrence:
    #: A version-directory base ("characters", "pcs", an entity kind,
    #: "greetings"), LIBRARY, COVER or COLLECTION.
    kind: str
    #: ``world:<wid>`` or ``campaign:<cid>`` (`image_scopes`, R10).
    scope: str
    #: The directory the name lives in (for a collection, its manifests').
    dir: Path
    #: The logical name (for a collection, its id).
    name: str
    #: The legacy file (for a collection, its manifest); None when metadata-only.
    path: Path | None
    #: "a" to "d" for a metadata-only occurrence (module docstring), else None.
    metadata_only: str | None = None
    #: Metadata-only: the file the key or crop lives in, in `dir`.
    sidecar: str | None = None
    #: Metadata-only: the directory whose placement of `name` it describes.
    target: Path | None = None
    #: A legacy file the inventory will not place, and why.
    untouched: str | None = None
    #: How the directory's own module reads it (`Surface.supported_only`).
    supported_only: bool = False


# ---- directories --------------------------------------------------------

#: Why a directory the walk met is reported instead of walked.
SYMLINKED = "symlinked-directory"
UNREADABLE = "unreadable"
#: The kind of a reported entry directly under ``worlds/`` or ``campaigns/``,
#: which belongs to no surface.
ROOT = "root"

#: A directory the walk cannot enter, with the reason.
_Bad = tuple[Path, str]


def _why(p: Path) -> str:
    """The reason to report `p`, a directory entry the walk could not test or
    list: a symlink (a loop, a target nobody may stat) or plainly unreadable."""
    try:
        return SYMLINKED if p.is_symlink() else UNREADABLE
    except OSError:
        return UNREADABLE


def _subdirs(d: Path) -> tuple[list[Path], list[_Bad]]:
    """`(subdirectories of d, entries it could not test)`, each sorted.

    A symlinked subdirectory is listed too, so that a rostered directory
    reached through it can be REPORTED (`linked`): it is never planned.
    Listing through a link is a read; the walk is a fixed depth, so a loop
    cannot run away with it. Each entry is tested on its own: one that cannot
    be (a `loop -> loop` link, a target behind a closed directory) is
    reported and the rest of the listing stands. A `d` that is not there
    lists nothing; one that is there and cannot be listed is reported."""
    good: list[str] = []
    bad: list[_Bad] = []
    try:
        with os.scandir(d) as it:
            for e in it:
                try:
                    if e.is_dir():
                        good.append(e.name)
                except OSError:
                    bad.append((d / e.name, _why(d / e.name)))
    except (FileNotFoundError, NotADirectoryError):
        return [], []
    except OSError:
        return [], [(d, _why(d))]
    return [d / n for n in sorted(good)], sorted(bad)


def linked(root: Path, d: Path) -> bool:
    """Whether any component between `root` and `d` (inclusive) is a
    symlink -- or `d` is not under `root` at all. Such a directory may hold
    files outside the pinned root, and a run would unlink them there (M4)."""
    try:
        parts = d.relative_to(root).parts
    except ValueError:
        return True
    cur = root
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


def _roots(root: Path) -> Iterator[tuple[str, Path, str | None]]:
    """``(world|campaign, its root, None)`` for every safe-named directory
    directly under ``worlds/`` and ``campaigns/``, as `image_usage` walks
    them -- and ``(side, entry, reason)`` for an entry there (or the parent
    itself) that could not be tested."""
    for side, dirname in ((_WORLD, "worlds"), (_CAMPAIGN, "campaigns")):
        good, bad = _subdirs(root / dirname)
        for d, why in bad:
            yield side, d, why
        for d in good:
            if safe_id(d.name):
                yield side, d, None


def _scope(side: str, sroot: Path) -> str:
    if side == _WORLD:
        return image_scopes.world_scope_of_dir(sroot.name)
    return image_scopes.campaign_scope(sroot.name)


def _library_dir(side: str, sroot: Path) -> Path:
    name = world_images.DIRNAME if side == _WORLD else campaign_images.DIRNAME
    return sroot / "assets" / name


def _version_dirs(sroot: Path, base: str) -> Iterator[tuple[Path, str | None]]:
    recs, bad = _subdirs(sroot / base)
    yield from bad
    for rec in recs:
        vids, bad = _subdirs(rec / "assets")
        yield from bad
        for v in vids:
            try:
                d = assets.version_dir(sroot, rec.name, v.name, base=base)
            except ValueError:
                continue
            if d == v:
                yield d, None


def _dirs_of(surface: Surface, side: str, sroot: Path) -> Iterator[tuple[Path, str | None]]:
    """`(directory, None)` for each of the surface's directories under
    `sroot`, or `(path, reason)` for one the walk could not enter."""
    if surface.kind == LIBRARY:
        found = _library_dir(side, sroot)
    elif surface.kind == COVER:
        found = sroot / "assets"
    elif surface.kind == COLLECTION:
        found = sroot.joinpath(*COLLECTIONS_DIR)
    else:
        yield from _version_dirs(sroot, surface.kind)
        return
    # A symlink that is not a directory to `is_dir` (a loop) is still
    # reported: `_walk` sees it as `linked`.
    if found.is_dir() or found.is_symlink():
        yield found, None


def _walk(root: Path) -> Iterator[tuple[Surface | None, str, Path, str | None]]:
    """`directories`' walk: each directory with None, or with the reason it is
    reported instead (`SYMLINKED`, `UNREADABLE`). A root-level entry that
    could not be tested has no surface."""
    for side, sroot, why in _roots(root):
        scope = _scope(side, sroot)
        if why is not None:
            yield None, scope, sroot, why
            continue
        for surface in SURFACES.values():
            if side in surface.scopes:
                for d, bad in _dirs_of(surface, side, sroot):
                    via_link = bad is None and linked(root, d)
                    yield surface, scope, d, SYMLINKED if via_link else bad


def directories(root: Path) -> Iterator[tuple[Surface, str, Path]]:
    """``(surface, scope, directory)`` for every rostered directory that
    exists under `root`, world roots first, then campaigns, in name order.
    A directory reached through a symlink, or one the walk could not test,
    is not one of them (`occurrences` reports it as untouched)."""
    for surface, scope, d, why in _walk(Path(root)):
        if surface is not None and why is None:
            yield surface, scope, d


# ---- legacy files -------------------------------------------------------

def _files(d: Path) -> list[Path]:
    """Regular files directly in `d` (not symlinks), sorted."""
    try:
        with os.scandir(d) as it:
            names = [e.name for e in it if e.is_file(follow_symlinks=False)]
    except OSError:
        return []
    return [d / n for n in sorted(names)]


def _accepted(p: Path) -> bool:
    """An extension `assets` stores under (its private rule, read not copied)."""
    return bool(assets._norm_ext(p.suffix))


def _name_files(surface: Surface, scope: str, d: Path, name: str
                ) -> tuple[list[Occurrence], set[Path]]:
    """The occurrences for logical `name` in `d`, and every file they cover."""
    # `assets._legacy_path`'s rule (M7): its sibling glob, newest by
    # (mtime, name), `supported_only` where the directory's module reads so --
    # over every file of the name EXCEPT a sidecar. `focus.json` globs as a
    # file of `focus`, and is never anybody's picture.
    sibs = {p for p in assets._siblings(d, name, False) if p.name not in SIDECARS}
    pool = [p for p in sibs if _accepted(p)] if surface.supported_only else list(sibs)
    chosen = max(pool, key=lambda p: (assets._mtime_ns(p), p.name)) if pool else None

    def occ(p: Path, why: str | None) -> Occurrence:
        return Occurrence(surface.kind, scope, d, name, p, untouched=why,
                          supported_only=surface.supported_only)

    if chosen is None or not _accepted(chosen):
        return [occ(p, "unsupported-extension") for p in sorted(sibs)], sibs
    if chosen.is_symlink() or not chosen.is_file():
        # Served today, but not a file the inventory may read and later
        # unlink as though it were the picture's only copy.
        return [occ(p, "not-a-regular-file") for p in sorted(sibs)], sibs
    others = [occ(p, "extra-sibling" if _accepted(p) else "unsupported-extension")
              for p in sorted(sibs - {chosen})]
    return [occ(chosen, None), *others], sibs


def _stems(files: list[Path]) -> list[str]:
    return sorted({p.name.rpartition(".")[0] for p in files} - {""})


def _legacy(surface: Surface, scope: str, d: Path) -> tuple[list[Occurrence], set[str]]:
    """Every legacy file in `d` as an occurrence, and the names that have one."""
    files = [p for p in _files(d) if p.name not in SIDECARS]
    if surface.kind == COVER:
        names = [covers.NAME]
        files = [p for p in files if p.name.rpartition(".")[0] == covers.NAME]
    else:
        names = [n for n in _stems(files) if assets.storable(n)]
    out: list[Occurrence] = []
    covered: set[Path] = set()
    held: set[str] = set()
    for name in names:
        found, sibs = _name_files(surface, scope, d, name)
        if found:
            held.add(name)
        out.extend(found)
        covered |= sibs
    for p in files:
        if p not in covered:
            stem = p.name.rpartition(".")[0]
            why = "unsupported-extension" if assets.storable(stem) else "unstorable-name"
            out.append(Occurrence(surface.kind, scope, d, stem or p.name, p, untouched=why,
                                  supported_only=surface.supported_only))
    return out, held


# ---- metadata ---------------------------------------------------------------

def _json_dict(p: Path) -> dict:
    """A sidecar's mapping, ``{}`` for one missing or garbled."""
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return got if isinstance(got, dict) else {}


def _keyed_sidecars(surface: Surface, scope: str) -> tuple[str, ...]:
    """The key files of a directory. Subjects only in a WORLD greeting:
    `image_subjects` is world-scoped, and nothing reads a campaign scope, so a
    campaign greeting's `subjects.json` is not folded anywhere (M5)."""
    if surface.kind == _GREETINGS and scope.startswith(f"{_WORLD}:"):
        return (assets.DESCRIPTIONS_FILE, image_subjects.SUBJECTS_FILE)
    return (assets.DESCRIPTIONS_FILE,)


def _same_dir(surface: Surface, scope: str, d: Path, held: set[str]) -> Iterator[Occurrence]:
    """(c) and (d) in `d`: keys and a crop beside placements of their own."""
    refs = image_refs.scan(d)
    bearing = {n for n, r in refs.items() if r.image is not None}
    for sidecar in _keyed_sidecars(surface, scope):
        for key in sorted(_json_dict(d / sidecar)):
            if key in bearing and key not in held:
                yield Occurrence(surface.kind, scope, d, key, None, metadata_only="c",
                                 sidecar=sidecar, target=d,
                                 supported_only=surface.supported_only)
    if assets.AVATAR in refs and (d / assets.FOCUS_FILE).is_file():
        yield Occurrence(surface.kind, scope, d, assets.AVATAR, None, metadata_only="d",
                         sidecar=assets.FOCUS_FILE, target=d,
                         supported_only=surface.supported_only)


@dataclass(frozen=True)
class _Inherits:
    """What a campaign inherits, read under the pinned root."""
    root: Path
    wroot: Path
    gone: frozenset[str]
    off: frozenset[str]


def _ledger(p: Path) -> frozenset[str] | None:
    """A tombstone or detachment ledger: empty when absent, None when it is
    there and does not read (so nothing inherited can be proved visible)."""
    if not p.exists():
        return frozenset()
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return frozenset(r for r in got if isinstance(r, str)) if isinstance(got, list) else None


def _inherits(root: Path, croot: Path) -> _Inherits | None:
    try:
        wid = parse_frontmatter_head(croot / "campaign.md").get("world", "")
    except (OSError, UnicodeDecodeError):
        return None
    wroot = root / "worlds" / wid if safe_id(wid) else None
    if wroot is None or not wroot.is_dir():
        return None
    gone = _ledger(croot / "deleted.json")
    off = _ledger(croot / "detached.json")
    if gone is None or off is None:
        return None
    return _Inherits(root, wroot, gone, off)


def _world_side(surface: Surface, d: Path, inh: _Inherits
                ) -> tuple[Path, str, str | None] | None:
    """For campaign directory `d`: the world directory it inherits from, the
    tombstone-ref prefix of its names, and its record's ref -- or None when
    nothing there is inherited (a cover, or a detached or deleted record)."""
    if surface.kind == LIBRARY:
        return _library_dir(_WORLD, inh.wroot), overlay.library_ref(""), None
    if surface.kind in (COVER, COLLECTION):
        return None
    base, rid, vid = surface.kind, d.parent.parent.name, d.name
    record = overlay._flat_ref(base, rid)
    if record in inh.gone or record in inh.off:
        return None
    wdir = assets.version_dir(inh.wroot, rid, vid, base=base)
    return wdir, overlay._asset_ref(base, rid, vid, ""), record


def _inherited(surface: Surface, scope: str, d: Path, inh: _Inherits | None
               ) -> Iterator[Occurrence]:
    """(a) and (b) in campaign directory `d`."""
    side = _world_side(surface, d, inh) if inh is not None else None
    if side is None or inh is None:
        return
    wdir, prefix, record = side
    if linked(inh.root, wdir):
        return              # the world side is reached through a symlink
    own = assets.names_in(d)[0]
    theirs = assets.names_in(wdir)[0] if wdir.is_dir() else set()

    def visible(name: str) -> bool:
        return name in theirs and name not in own and f"{prefix}{name}" not in inh.gone

    def occ(name: str, kind: str, sidecar: str) -> Occurrence:
        return Occurrence(surface.kind, scope, d, name, None, metadata_only=kind,
                          sidecar=sidecar, target=wdir,
                          supported_only=surface.supported_only)

    for key in sorted(_json_dict(d / assets.DESCRIPTIONS_FILE)):
        if assets.storable(key) and visible(key):
            yield occ(key, "a", assets.DESCRIPTIONS_FILE)
    if (record is not None and (d / assets.FOCUS_FILE).is_file()
            and image_refs.read(d, assets.AVATAR) is None and visible(assets.AVATAR)):
        yield occ(assets.AVATAR, "b", assets.FOCUS_FILE)


def _manifests(scope: str, d: Path) -> Iterator[Occurrence]:
    for p in _files(d):
        if p.suffix == ".json":
            yield Occurrence(COLLECTION, scope, d, p.stem, p)


def occurrences(root: Path) -> Iterator[Occurrence]:
    """Every occurrence under store root `root` (module docstring), directory
    by directory in `directories` order. Reads only; writes nothing."""
    root = Path(root)
    inherits: dict[Path, _Inherits | None] = {}
    for surface, scope, d, why in _walk(root):
        if surface is None or why is not None:
            yield Occurrence(surface.kind if surface is not None else ROOT, scope, d,
                             d.name, d, untouched=why or UNREADABLE,
                             supported_only=surface is not None and surface.supported_only)
            continue
        if surface.kind == COLLECTION:
            yield from _manifests(scope, d)
            continue
        found, held = _legacy(surface, scope, d)
        yield from found
        if surface.kind != COVER:
            yield from _same_dir(surface, scope, d, held)
        if scope.startswith(f"{_CAMPAIGN}:"):
            croot = root / "campaigns" / scope.partition(":")[2]
            if croot not in inherits:
                inherits[croot] = _inherits(root, croot)
            yield from _inherited(surface, scope, d, inherits[croot])
