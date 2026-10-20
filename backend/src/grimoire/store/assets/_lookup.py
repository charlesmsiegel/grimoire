"""Placement-first resolution, legacy fallback and coherent logical image listings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, overload

from .. import image_refs, image_store
from . import _contracts


def mtime_ns(p: Path) -> int:
    """Sort key that tolerates the file vanishing mid-scan. put_in writes
    the new extension and then unlinks the stale sibling, so a concurrent
    reader can genuinely glob a path that is gone by the time it stats -- and
    the old `sorted(...)[0]` never stat'd at all, so raising here would be a
    regression, not a new safety check."""
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return -1


def siblings(d: Path, name: str, supported_only: bool) -> list[Path]:
    """Every file in `d` whose stem is `name`.

    `supported_only` narrows that to the extensions we actually accept. The
    cover directory is one a human browses and a sync client writes into, so a
    `cover.txt` left beside `cover.png` must neither win resolution (it would
    be served as octet-stream and packed into a book) nor be deleted by a
    replace or a remove -- it is not ours. Record images keep the unfiltered
    behaviour: `promote_image` raises `ValueError` for "an externally-placed
    file whose extension we never accepted", which requires `image_path` to
    still hand one back.
    """
    found = list(d.glob(f"{name}.*"))
    return [p for p in found if _contracts.norm_ext(p.suffix)] if supported_only else found


def legacy_path(d: Path, name: str, supported_only: bool) -> Path | None:
    """The newest legacy ``<name>.*`` file in `d` -- `path_in`'s pre-placement
    rule, and its fallback."""
    matches = siblings(d, name, supported_only)
    if not matches:
        return None
    return max(matches, key=lambda p: (mtime_ns(p), p.name))


def has_legacy(d: Path, name: str) -> bool:
    """Whether a legacy ``<name>.*`` file sits in `d`, whatever its extension
    -- one `path_in` would fall back to if the placement of `name` went."""
    return _contracts._safe_name(name) and bool(siblings(d, name, False))


def resolve(d: Path, name: str) -> image_refs.ResolvedImage | None:
    """The placement `name` in `d`, resolved to its object and blob -- None
    when there is no image-bearing placement or it does not resolve (yet)."""
    if not _contracts._safe_name(name):
        return None
    return image_refs.resolve(d, name)


def path_in(d: Path, name: str, *, supported_only: bool = False) -> Path | None:
    """The current image file; `file_in` also carries its placement identity."""
    found = file_in(d, name, supported_only=supported_only)
    return found[0] if found is not None else None


def file_in(d: Path, name: str, *, supported_only: bool = False) -> tuple[Path, str | None] | None:
    """`(path, image id or None)` for logical image `name`, or None if absent.

    A placement that resolves answers with its blob path. Otherwise -- no
    placement, an image-less one, or one whose object or blob has not arrived
    -- the legacy rule below answers, so redundant data beats lost data.

    Legacy: newest wins, not alphabetically first: `put_in` used to write the
    new file before unlinking stale other-extension siblings (so a crash can't
    lose the image), which leaves both present for a moment -- and a plain
    `sorted()[0]` would hand back the stale one. Also self-heals if that
    unlink ever fails.

    Lock-agnostic: this takes a directory and no campaign identity, so a caller
    that mutates campaign-scoped state through `put_in`/`delete_in` is the one
    that must hold `locks.campaign_lock` (`store.covers` does).
    """
    if not _contracts._safe_name(name) or not d.exists():
        return None
    resolved = image_refs.resolve(d, name)
    if resolved is not None:
        return resolved.blob_path, resolved.image_id
    legacy = legacy_path(d, name, supported_only)
    return (legacy, None) if legacy is not None else None


def slot_taken(d: Path, name: str) -> bool:
    """Whether `name`, or a case variant of it, already holds a picture in `d`:
    a placement with an image, or a legacy file.

    Case-folded because a store syncs to case-insensitive filesystems, where
    `Happy` and `happy` are one slot -- `_image_lock` folds the same way. A
    caller that must act on the answer holds that lock, as `link_if_vacant`
    does; unlocked it is a hint, not a guarantee.
    """
    return image_refs.fold(name) in taken_slots(d)


def taken_slots(d: Path) -> set[str]:
    """Every folded name in `d` that holds a picture -- `slot_taken`'s answer
    for all names at once, for a caller that asks about many of them."""
    taken = {image_refs.fold(n) for n in image_refs.image_names(d)}
    if d.is_dir():
        taken.update(image_refs.fold(p.name.split(".", 1)[0]) for p in d.iterdir() if p.is_file())
    return taken


def is_slot_file(p: Path) -> bool:
    """Whether file `p`, directly inside a version folder, is a legacy image
    file -- a slot `copy_slots` answers for -- rather than one of the folder's
    other files (`descriptions.json`, `focus.json`, ...). The rule is
    `names_in`'s, so the two agree on what counts as a slot."""
    return bool(_contracts.norm_ext(p.suffix)) and _contracts._addressable_name(p.stem)


@overload
def names_in(d: Path, also: str = "", *,
             with_refs: Literal[False] = False) -> tuple[set[str], bool]: ...


@overload
def names_in(d: Path, also: str = "", *,
             with_refs: Literal[True]) -> tuple[set[str], bool, dict[str, image_refs.Ref]]: ...


def names_in(d: Path, also: str = "", *, with_refs: bool = False
             ) -> tuple[set[str], bool] | tuple[set[str], bool, dict[str, image_refs.Ref]]:
    """Every logical image NAME in `d`: the legacy stems plus every placement
    that holds an image -- `list_in`'s name set, and nothing else.

    Placements are counted WITHOUT being resolved, which keeps a sweep to one
    directory read per folder (plus the placements' own small reads) rather
    than an object read and a blob stat per image. The price is a transient
    disagreement with `list_in`, accepted on purpose: mid-sync, a placement
    whose object or blob has not arrived yet is counted here but omitted
    there (unless a legacy file backs the name), until the sync completes.

    `list_in` additionally decides which of two files sharing a stem answers for
    it, and that tie-break is an mtime stat per file. A caller that only counts
    names, or asks whether one is present, never uses the answer — and on a
    sweep over every version of every record of every base, those stats are the
    bulk of the walk (`image_descriptions.undescribed_count`).

    The filter is `list_in`'s, deliberately identical: a name this admits and
    that one does not is a count that disagrees with the list behind it.

    `also` names one extra file to report the presence of, and it is here so a
    caller that wants both facts pays for one directory read instead of two.
    The sidecar beside a version's images is the case (`image_descriptions`):
    statting it separately doubles the traversals on a sweep over every version
    folder in the store. Returns (names, whether `also` was present); with no
    `also`, the flag is always False.

    `with_refs` adds a third item: the placements (`image_refs.scan`, image-less
    overrides included) from the scan this already makes, so a backlog walk
    that needs each name's image id never scans the placements a second time.
    """
    out: set[str] = set()
    found = False
    try:
        # `os.scandir`, not `iterdir()`: the entry carries its type from the
        # directory read, so this is one syscall for the directory rather than
        # one more stat per file in it. On the sweep above that is the
        # difference the function was added for.
        with os.scandir(d) as it:
            for e in it:
                if also and e.name == also:
                    found = True
                    continue
                stem, _, ext = e.name.rpartition(".")
                if stem and e.is_file() and _contracts.norm_ext("." + ext) and _contracts._addressable_name(stem):
                    out.add(stem)
    except OSError:
        return (set(), False, {}) if with_refs else (set(), False)
    refs = image_refs.scan(d)
    out.update(n for n, r in refs.items() if r.image is not None and _contracts._addressable_name(n))
    return (out, found, refs) if with_refs else (out, found)


def name_rows_in(d: Path, also: str = "") -> tuple[list[dict], bool]:
    """`(image_refs.name_rows, presence of `also`)` for every name in `d`: the
    one read a describe backlog needs. The rows are unfiltered and unsorted;
    each backlog applies its own name policy, which is the part that differs."""
    names, found, refs = names_in(d, also, with_refs=True)
    return image_refs.name_rows(names, refs), found


def list_in(d: Path) -> list[dict]:
    """One entry per logical image in directory `d`, newest sibling winning.

    The directory-level half of `list_images`, split out so a flat directory
    built on `path_in`/`put_in`/`delete_in` -- the campaign image library
    (`store.campaign_images`) -- enumerates by the very same rule the
    per-version folders do, rather than by a second copy of it that agrees
    right up until one of the two is fixed. What stays behind in `list_images`
    is only what a version has and a flat directory does not: ids to check,
    and a stranded promotion to repair.

    ONE ENTRY PER LOGICAL IMAGE, not per file. Two files can share a stem:
    `put_in` writes the new extension before dropping the old one, and
    `path_in` self-heals an unlink that never happened, so the state is
    reachable and can persist. Listing both double-counts galleries, hands the
    frontend two tiles under one key, and lets a caller read a cache token off
    the sibling the server will not serve -- which a `?v=` URL, answered
    `immutable, max-age=1y`, then pins for a year.

    Newest wins, the same rule and the same tie-break `path_in` resolves by,
    so the entry always describes the bytes the serve route returns.

    Placements: a name whose placement resolves is listed from it,
    ``{name, ext, v, image_id}`` with `v` the blob's sha256, and shadows any
    legacy file of that name; one that does not resolve is omitted unless a
    legacy file backs the name (the same fallback as `path_in`). Sorted by
    name. A listing never scans the global store -- each placement costs its
    object read and a blob stat.
    """
    found = _candidates(d)
    return _listing(found, _resolve_refs(found[1])[0] if found else None)


_Files = dict[str, list[tuple[str, int, int]]]


def _candidates(d: Path) -> tuple[_Files, dict[str, image_refs.Ref]] | None:
    """`(files, refs)`: every legacy file in `d` that could answer for a
    logical image, by stem, each as `(name, st_mtime_ns, st_size)`, and every
    placement in `d` (`image_refs.scan`, image-less ones included) -- or None
    when `d` is not there to list.

    ONE stat per file, and it is the one the listing needs anyway: `os.scandir`
    carries each entry's type from the directory read, so `is_file` costs
    nothing for a plain file, and the stat that ranks two siblings is the same
    stat the `v` token is formatted from. `iterdir` paid for the type check, a
    stat per comparison and a fresh stat for the token -- three per image on
    the listing every character grid row and describe queue is built from.

    The token coming from the ranking stat is a tightening, not only a saving:
    the two used to be taken apart, so a file rewritten between them could be
    ranked by one version of itself and tokened by another.

    The stem and extension split is `names_in`'s, which that function's
    docstring holds to this one: for every name either accepts, a
    `rpartition(".")` and pathlib's `stem`/`suffix` agree.
    """
    try:
        it = os.scandir(d)
    except OSError:
        # `d.exists()` was the old guard, and it reads a missing directory and
        # a missing PARENT the same way -- while a `d` that exists but cannot
        # be listed (a file, a permissions problem) raised out of `iterdir`.
        # Both halves are kept.
        if not d.exists():
            return None
        raise
    found: dict[str, list[tuple[str, int, int]]] = {}
    with it:
        for e in it:
            stem, _, ext = e.name.rpartition(".")
            # filter on addressability, not just the extension: a name
            # image_path could never resolve would advertise a gallery entry
            # that cannot be served, promoted or deleted (#259 review).
            # `promote-tmp` is the one deliberate exception -- unwritable, but a
            # stranded one is shown on purpose so failed recovery is visible
            # rather than silent (#253).
            if not (stem and _contracts.norm_ext(ext) and _contracts._addressable_name(stem)):
                continue
            try:
                if not e.is_file():
                    continue
                st = e.stat()
            except OSError:
                continue   # vanished mid-scan; a listing must not fail over one file
            found.setdefault(stem, []).append((e.name, st.st_mtime_ns, st.st_size))
    return found, image_refs.scan(d)


def _resolve_refs(refs: dict[str, image_refs.Ref]
                  ) -> tuple[dict[str, image_refs.ResolvedImage], bool]:
    """Each image-bearing placement that resolves, by name, and whether EVERY
    image-bearing placement did. Image-less ones (occurrence overrides) are not
    images and are skipped."""
    out: dict[str, image_refs.ResolvedImage] = {}
    complete = True
    for name, ref in refs.items():
        if ref.image is None or not _contracts._addressable_name(name):
            continue
        r = image_refs.resolve_ref(ref)
        if r is None:
            complete = False
        else:
            out[name] = r
    return out, complete


def _listing(found: tuple[_Files, dict[str, image_refs.Ref]] | None,
             resolved: dict[str, image_refs.ResolvedImage] | None = None) -> list[dict]:
    """`list_in`'s entries from `_candidates` and `_resolve_refs`: a resolved
    placement answers for its name; otherwise the newest legacy sibling per
    stem by (mtime, name), which is order-independent because names are
    unique -- so the directory's own enumeration order, unlike `iterdir`'s
    sorted one, cannot change which file wins."""
    files = found[0] if found else {}
    resolved = resolved or {}
    out: list[dict] = []
    for stem in sorted(set(files) | set(resolved)):
        r = resolved.get(stem)
        if r is not None:
            out.append({"name": stem, "ext": r.ext, "v": r.blob_sha256,
                        "image_id": r.image_id})
            continue
        name, mtime_ns, size = max(files[stem], key=lambda c: (c[1], c[0]))
        out.append({"name": stem, "ext": name.rpartition(".")[2].lower(),
                    "v": _token(mtime_ns, size)})
    return out


def image_version(p: Path) -> str:
    """Cache-busting token for an image file's current bytes; a `?v=` URL
    carrying it is served immutable, so the browser never revalidates.

    A blob's token is its sha256 -- its name says what its bytes are, so
    nothing is statted -- and a legacy file's is `_token` of its stat."""
    sha = image_store.blob_sha_of(p)
    if sha is not None:
        return sha
    st = p.stat()
    return _token(st.st_mtime_ns, st.st_size)


def _token(mtime_ns: int, size: int) -> str:
    """`image_version`'s token from a stat already in hand."""
    return f"{mtime_ns:x}-{size:x}"


def _stranded(found: _Files) -> bool:
    """Whether `_heal_stranded_promotion` could find something to rescue among
    these candidates. Case-folded, so it answers yes wherever the heal's glob
    might match (a case-insensitive filesystem): a false yes only costs a
    listing that is not memoized."""
    return any(stem.casefold() == _contracts._PROMOTE_TMP for stem in found)
