"""Per-image descriptions — what a stored picture depicts, in the author's own
words. Sidecar at ``<dir>/descriptions.json``: ``{"<image-name>": "<text>"}``.

The third instance of the ``focus.json`` pattern, after ``image_subjects.py``
(which names the same precedent): tolerant reads, strict writes, one small JSON
file living beside the images it describes rather than a key in a record the
store read-modify-writes for other reasons.

## Why a sidecar and not a field on the record

The same reason ``store.covers`` and ``store.campaign_images`` give. A
description belongs to an *image*, and images are not hashed into the character
card (see ``assets``' module docstring) precisely so that editing art does not
make a character look edited to the world/campaign sync. Putting the text in
the card would undo that: describing a picture would show up as a diverged
record, and a campaign that had only ever described its own art would
materialize the whole card.

## Directory-level primitives

``read_in``/``write_in``/``set_in`` take a *directory*, so the three
per-version surfaces (characters, pcs, entity kinds) and the campaign's flat
image library (``store.campaign_images``) enumerate and store by one rule
rather than by two copies of it that agree until one is fixed. That is the same
split ``assets.list_in`` was carved out of ``assets.list_images`` for, and for
the same reason.

## Absent key is not the empty string

Key **absent** means *undescribed*: the image has never been looked at, and it
belongs in the authoring backlog ``undescribed`` builds. An explicit ``""``
means *reviewed, deliberately no description*: it leaves the backlog and it is
never offered to the model.

``image_subjects`` earned this distinction the hard way and it carries over
unchanged. Without it, "I looked at this and it needs no description" cannot be
said, so the queue never empties and the reader is asked about the same image
forever.

## What a description is for

Two readers, and only one of them is human. The author writes it, and
``context.art`` ranks it against the moment and offers the closest few to the
model. A non-empty description is also what makes an image *reachable* by a
model handle at all — see ``context.art.resolve_handles`` — so "described" is a
deliberate act of publication, not merely a note.

``store.search`` does **not** see any of this. Its corpus is built from
markdown records, character cards and campaign fact files; nothing walks
``assets/*/descriptions.json``, so text that lives only in a description finds
nothing in the search page. That is the design's choice of consumer (the
narrator turn, not the library index) rather than an oversight, and folding the
sidecar into the corpus is a separate change with its own indexing cost.

Nothing detects a description drifting from the art it describes. An image
replaced under the same name keeps the old text, exactly the way ``focus.json``
keeps a crop that no longer frames anything. Stated rather than solved.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

from . import assets, atomic
from .paths import safe_id

#: Re-exported from `assets`, which owns the names of the sidecars living in
#: its directories -- see the note beside it there. One string, one place.
DESCRIPTIONS_FILE = assets.DESCRIPTIONS_FILE


def path_in(d: Path) -> Path:
    return d / DESCRIPTIONS_FILE


def _names(d: Path) -> set[str]:
    """The logical images of `d` that can actually be described.

    `assets.list_in`, not a fresh `iterdir`: one entry per logical image with
    the newest sibling winning, so a name this accepts is a name
    `assets.path_in` will hand bytes back for.

    Filtered by `assets.storable`, which that listing deliberately is not.
    `list_in` shows a stranded `promote-tmp` on purpose -- crash residue is
    worth seeing in an editor (#253) -- but it is a name nothing can serve,
    promote or delete. Unfiltered, it entered the describe queue and could take
    a sidecar entry; then the next ordinary listing heals the residue into
    `avatar`, and the description is stranded under a key no image has.
    """
    return {i["name"] for i in assets.list_in(d) if assets.storable(i["name"])}


def read_raw(d: Path) -> dict:
    """The sidecar as stored: ``{}`` on a missing or garbled file, no filtering.

    The modify path reads through this rather than through `read_in` so that an
    entry for an image which has vanished survives an edit to a *different*
    image — the judgement `image_subjects.set_image_subjects` already makes.
    """
    p = path_in(d)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def read_in(d: Path, names: set[str] | None = None) -> dict[str, str]:
    """Tolerant: ``{}`` on a missing or garbled file; entries whose image has
    vanished drop out silently, so nothing can offer a description for art that
    is not there. A non-string value drops out too — a hand-edited or
    half-synced store must not hand a list to a template.

    `names` overrides which images count as present. A campaign caller passes
    the overlay-resolved union (`overlay.list_images`), because a thin campaign
    may hold the *description* of an image whose bytes it still inherits from
    its world — filtering on this directory alone would drop exactly those. The
    same override, for the same reason, as `image_subjects.copy_to_character`'s
    `taken_names`.
    """
    raw = read_raw(d)
    if not raw:
        return {}
    present = _names(d) if names is None else names
    return {n: v for n, v in raw.items() if n in present and isinstance(v, str)}


def write_in(d: Path, descriptions: dict[str, str], names: set[str] | None = None) -> None:
    """Strict: every key must be a stored image of `d` (or of `names`, see
    `read_in`). An explicit ``""`` persists — it means "reviewed, no
    description" and keeps the image out of the `undescribed` backlog (key
    absent = unreviewed)."""
    names = _names(d) if names is None else names
    unknown = set(descriptions) - names
    if unknown:
        raise ValueError(f"unknown image(s): {sorted(unknown)}")
    trimmed = {n: str(v) for n, v in descriptions.items()}
    with assets.sidecar_lock(d, DESCRIPTIONS_FILE):
        d.mkdir(parents=True, exist_ok=True)
        atomic.write_text(path_in(d), json.dumps(trimmed, indent=2, sort_keys=True) + "\n")


def set_in(d: Path, name: str, text: str, names: set[str] | None = None) -> None:
    """Read-modify-write of one image's entry.

    Raw read, then a strict write of only the key being touched: entries for
    images we are not touching survive even if their file has since vanished,
    while the key being written still has to name a real image. `names`
    overrides what "real" means — see `read_in`.
    """
    # Under the sidecar lock for the WHOLE read-modify-write, and the existence
    # check with it: an image lifecycle event holds the same lock while it moves
    # entries around, so a check made outside it could validate a slot that has
    # already been promoted away.
    with assets.sidecar_lock(d, DESCRIPTIONS_FILE):
        if name not in (_names(d) if names is None else names):
            raise ValueError(f"unknown image(s): [{name!r}]")
        cur = read_raw(d)
        cur[name] = str(text)
        # Not `write_in`: `cur` may legitimately carry entries for vanished
        # images (see the docstring), which `write_in` would reject as unknown.
        #
        # Written back VERBATIM apart from the key being touched. Stringifying
        # the whole mapping undid the tolerance `read_in` exists for: a
        # hand-edited or half-synced non-string value, which reads ignore,
        # became `"['not', 'a', 'string']"` -- a real description from then on,
        # able to mask an inherited one and to reach a prompt as alt text (PR
        # review). Leaving it as found keeps it ignorable and hand-fixable.
        d.mkdir(parents=True, exist_ok=True)
        atomic.write_text(path_in(d),
                          json.dumps(cur, indent=2, sort_keys=True) + "\n")


# ---- per-version wrappers (characters / pcs / entity kinds) ----------------

def _dir(root: Path, aid: str, vid: str, base: str) -> Path:
    return root / base / aid / "assets" / vid


def read_all(root: Path, aid: str, vid: str, base: str = "characters",
             names: set[str] | None = None) -> dict[str, str]:
    if not (safe_id(aid) and safe_id(vid)):
        return {}
    return read_in(_dir(root, aid, vid, base), names)


def read(root: Path, aid: str, vid: str, name: str, base: str = "characters",
         names: set[str] | None = None) -> str:
    """One image's description, or ``""`` for undescribed *and* for
    reviewed-empty. The two differ only to the backlog, which reads the mapping
    (`read_all`) and asks about key presence; every other caller wants the text
    and treats both the same way."""
    return read_all(root, aid, vid, base, names).get(name, "")


def set_description(root: Path, aid: str, vid: str, name: str, text: str,
                    base: str = "characters", names: set[str] | None = None) -> None:
    if not (safe_id(aid) and safe_id(vid)):
        raise ValueError("unsafe image id")
    set_in(_dir(root, aid, vid, base), name, text, names)


def _walk(root: Path, base: str) -> Iterator[tuple[Path, Path]]:
    """(record dir, version dir) for every stored version of `base`.

    The traversal, in ONE place -- "where the images of a base live", which is
    the sentence `catalog`'s docstring was written to keep from being said
    twice. Said twice it drifts, and a base reaches the gallery but not the
    describe queue, or the other way round.

    Only the traversal. `catalog` and the backlog (`_undescribed_names`) need
    very different amounts of each version folder -- the gallery resolves an
    extension and a cache-busting `v` per image, which are a stat apiece, and
    the backlog needs neither -- so what they read inside a folder is theirs.
    What they must NOT disagree on is which images count, and that is
    `assets.storable` plus key presence in both, held to one answer by
    `test_image_descriptions_store.py`.
    """
    bdir = root / base
    if not bdir.exists():
        return
    for rec in _subdirs(bdir):
        adir = rec / "assets"
        try:
            vdirs = _subdirs(adir)
        except (FileNotFoundError, NotADirectoryError):
            continue        # no art folder: the `is_dir()` this replaced said False
        except OSError:
            # Anything else, ask as the old walk did: a directory that cannot
            # be listed raises, as its `iterdir` did, and whatever `is_dir()`
            # calls no directory is skipped.
            if adir.is_dir():
                raise
            continue
        yield from ((rec, vdir) for vdir in vdirs)


def _subdirs(d: Path) -> list[Path]:
    """`sorted(p for p in d.iterdir() if p.is_dir())`, off one `os.scandir`.

    The entry carries its type from the directory read, so a plain directory
    costs no stat to recognise; the `iterdir` form paid one per entry, per
    record and per version folder, on a walk the describe queue, its count,
    the gallery and the to-do list's chores all take. Same order: siblings
    sort by name, case-folded where the platform's paths are
    (`os.path.normcase`), as pathlib sorts them. Raises what `os.scandir`
    raises for a `d` that cannot be listed.
    """
    with os.scandir(d) as it:
        names = [e.name for e in it if e.is_dir()]
    return [d / name for name in sorted(names, key=os.path.normcase)]


def catalog(root: Path, base: str = "characters") -> list[dict]:
    """Every stored image of `base`, whether described or not — the gallery's
    listing (#200). The backlog walks the same folders (`_walk`) but reads
    less of each; see ``undescribed``.

    One entry per logical image: ``id``, ``vid``, ``name``, the ``ext`` and
    cache-busting ``v`` token ``assets.list_in`` resolves, and the two facts the
    sidecar holds. ``described`` is key PRESENCE, the distinction this module's
    docstring turns on — an image reviewed and deliberately left blank IS
    described, and its ``description`` is then ``""``. A non-string value reads
    as ``""`` here for the reason ``read_in`` drops it: a hand-edited or
    half-synced store must not hand a list to a caller expecting text.

    Sorted by (id, vid, name), which is the order the editors list versions and
    images in.

    The whole-tree walk lives here, in one function, rather than once per
    listing: "where the images of a base live" said twice is how a base gets
    added to the gallery and not to the describe queue, or the other way round.
    """
    out: list[dict] = []
    for rec, vdir in _walk(root, base):
        reviewed = read_raw(vdir)
        for img in assets.list_in(vdir):
            # `assets.storable`, the same filter `_names` applies and for
            # the same reason: a stranded `promote-tmp` is shown in the
            # editor it belongs to on purpose, but it is a name no serve
            # route answers, so a gallery tile over it is a broken image.
            if not assets.storable(img["name"]):
                continue
            text = reviewed.get(img["name"])
            out.append({"id": rec.name, "vid": vdir.name, "name": img["name"],
                        "ext": img["ext"], "v": img["v"],
                        "described": img["name"] in reviewed,
                        "description": text if isinstance(text, str) else ""})
    return out


def undescribed(root: Path, base: str = "characters") -> list[dict]:
    """Every stored image of `base` with NO sidecar key — the authoring queue.

    Key absent = unreviewed; an explicit ``""`` counts as reviewed. Sorted by
    (id, vid, name), which is the order the editors list versions and images in.

    Only the three keys the queue reads, not `catalog`'s row whole: widening a
    response nobody asked to widen is how a second, quieter contract grows on a
    route that already has one.

    Off `_undescribed_names` rather than `catalog`, for the reason
    `undescribed_count` gives: the gallery's `ext` and `v` are a stat per
    image, and this list carries neither. Walking one way for the count and
    another for the list also left the two free to disagree; now a count is
    this list's length by construction (`undescribed_by_version`).
    """
    return [{"id": rid, "vid": vid, "name": name}
            for rid, vid, names in _undescribed_names(root, base) for name in names]


def undescribed_count(root: Path, base: str = "characters") -> int:
    """How many images of `base` have no sidecar key, without building the list.

    The walk `undescribed` takes (`_undescribed_names`), with its two rules --
    `assets.storable`, and key PRESENCE rather than non-empty text -- and
    neither asks `assets.list_in` for an `ext` or a `v`, which are a stat per
    image. On a whole-library sweep that stat is most of the cost: the to-do
    list needs this number for every world on every read, and the list itself
    only for the one world a reader expanded.

    `test_image_descriptions_store.py` holds the two to the same answer. A cheap count
    that can disagree with the list behind it is worse than no count -- it is
    the stale number the to-do list exists to not have.
    """
    return sum(n for _rid, _vid, n in undescribed_by_version(root, base))


def _undescribed_names(root: Path, base: str) -> Iterator[tuple[str, str, list[str]]]:
    """`(record id, version id, sorted undescribed names)` per version folder
    holding any: the one walk behind `undescribed`, `undescribed_count` and
    `undescribed_by_version`."""
    for rec, vdir in _walk(root, base):
        # ONE directory read per version, answering both questions it is asked:
        # which images are here, and is there a sidecar at all. Asking
        # separately -- `read_raw`, which stats the sidecar before opening it,
        # then a second scan for the names -- is two directory traversals per
        # version folder of every record in the store, and on the whole-library
        # sweep this exists for, that is the bulk of the time.
        names, has_sidecar = assets.names_in(vdir, DESCRIPTIONS_FILE)
        if not names:
            continue
        reviewed = read_raw(vdir) if has_sidecar else {}
        todo = sorted(n for n in names if assets.storable(n) and n not in reviewed)
        if todo:
            yield rec.name, vdir.name, todo


def undescribed_by_version(root: Path, base: str = "characters") -> Iterator[tuple[str, str, int]]:
    """`(record id, version id, how many)` for every version folder of `base`
    holding undescribed images: `undescribed_count`, not yet summed.

    For a caller that has to filter the backlog by record before counting it --
    the describe queue drops an image whose record or version is gone
    (`routes.characters.list_undescribed_images`), and a count of the queue has
    to drop the same ones or it is a number for a different list. The record
    and version ids are `undescribed`'s `id` and `vid`, so a filter written
    against one reads the other.
    """
    for rid, vid, names in _undescribed_names(root, base):
        yield rid, vid, len(names)


def has_undescribed(root: Path, base: str = "characters") -> bool:
    """Whether ANY image of `base` lacks a sidecar key — `undescribed_count`
    stopping at the first one.

    The rail's badge counts chores, not instances, so "is this backlog empty"
    is the whole question it asks, and answering it by summing the backlog is
    a whole-store walk per navigation to learn a bit. Where a backlog exists
    this returns on the first record it opens.

    It is only expensive in the case where it returns False, which is the case
    where the chore does not appear at all -- and a library with no undescribed
    art anywhere has little for the walk to visit.
    """
    for _rec, vdir in _walk(root, base):
        names, has_sidecar = assets.names_in(vdir, DESCRIPTIONS_FILE)
        if not names:
            continue
        reviewed = read_raw(vdir) if has_sidecar else {}
        if any(assets.storable(n) and n not in reviewed for n in names):
            return True
    return False
