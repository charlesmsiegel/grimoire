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

## On the image object (stage 2)

A description belongs to a *picture*, and since the content-addressed store a
picture is one image object however many places show it. So text written for a
placement-backed image lives on that object (``description`` in its sidecar,
written only through `image_store.update`), and one picture shows one
description in every world that holds it (R4). ``descriptions.json`` stays as
the *legacy* store: what a name says until migration folds it in.

**Precedence (R1).** For directory `d` and name `n`: a string
``descriptions.json[n]`` in `d` wins; otherwise the object's string
``description`` behind `n`'s image-bearing placement; otherwise `n` is
undescribed. A legacy name with no placement reads only its key, and a
non-string legacy value counts as absent. `text_in` is the rule for one name;
`read_in`, `catalog`, `described_names` and the backlog walks apply it many
times without re-reading what their caller's listing already read.

**Writes (R2).** `set_in` writes the object behind a *resolving* placement and,
once `image_store.update` confirms the write, drops the name's legacy key here
(and in `also_clear`, the visible placement's directory when that differs).
Object first, keys second, so a failure in between leaves text showing rather
than none. An unconfirmed write, an unarrived placement or a name with no
placement writes the legacy key exactly as before: no text is ever lost.

Nothing detects a description drifting from the art it describes, except one
case: an image replaced under the same name by a *different, already
described* picture sheds the old legacy key (`assets._sheds_caption`, R12).
Otherwise the old text stays, exactly the way ``focus.json`` keeps a crop that
no longer frames anything. Stated rather than solved.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

from . import assets, atomic, image_refs, image_store
from .paths import safe_id

#: Re-exported from `assets`, which owns the names of the sidecars living in
#: its directories -- see the note beside it there. One string, one place.
DESCRIPTIONS_FILE = assets.DESCRIPTIONS_FILE

#: `text_in`'s default `known_id`: "I have not read the placement -- you do".
UNKNOWN: object = object()


class DescriptionTooLongError(ValueError):
    """A description over `image_store.MAX_DESCRIPTION` characters.

    A `ValueError`, so a caller that only knew "refused" still refuses; its
    own class, so a route can tell "too long" (422) from "no such image"
    (404) -- which is what every description PUT route does."""


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


def _object_text(image_id: str | None) -> str | None:
    """The object's string ``description``, or None (no id, no object, none
    written, or not a string). Never resolves a blob."""
    if not image_id:
        return None
    obj = image_store.read(image_id)
    if obj is None:
        return None
    text = obj.raw.get("description")
    return text if isinstance(text, str) else None


def _text(raw: dict, name: str, image_id: str | None) -> str | None:
    """R1 for one name, given the directory's raw sidecar and the id of the
    name's image-bearing placement (None for none): the legacy key if it holds
    a string, else the object's text. An object is read only without a key."""
    legacy = raw.get(name)
    if isinstance(legacy, str):
        return legacy
    return _object_text(image_id)


def legacy_text_in(d: Path, name: str) -> str | None:
    """Only the raw legacy key: ``descriptions.json[name]`` in `d` when it is
    a string, else None. No placement and no object is read."""
    legacy = read_raw(d).get(name)
    return legacy if isinstance(legacy, str) else None


def text_in(d: Path, name: str, *, known_id: object = UNKNOWN) -> str | None:
    """What `name` in `d` says (R1), or None for undescribed.

    `known_id` is what the caller already knows about the name's placement:

    - `UNKNOWN` (the default): read the placement here;
    - None: the caller's listing row has no id (a legacy row), so no object
      is read;
    - an image id: use it, and read no placement.

    An explicit ``""`` is returned as ``""`` -- reviewed, nothing to say -- and
    is not None.
    """
    raw = read_raw(d)
    legacy = raw.get(name)
    if isinstance(legacy, str):
        return legacy
    if known_id is UNKNOWN:
        ref = image_refs.read(d, name)
        image_id = ref.image if ref is not None else None
    else:
        image_id = known_id if isinstance(known_id, str) else None
    return _object_text(image_id)


def _placed_ids(d: Path) -> dict[str, str]:
    """name -> image id for every image-bearing placement in `d`, off one scan."""
    return {n: r.image for n, r in image_refs.scan(d).items() if r.image is not None}


def read_in(d: Path, names: set[str] | None = None,
            ids: dict[str, str] | None = None) -> dict[str, str]:
    """What every present image of `d` says (R1); undescribed ones are absent.

    Tolerant: ``{}`` on a missing or garbled file; entries whose image has
    vanished drop out silently, so nothing can offer a description for art that
    is not there. A non-string value drops out too — a hand-edited or
    half-synced store must not hand a list to a template — and the object's
    text, if any, answers instead.

    `names` overrides which images count as present. A campaign caller passes
    the overlay-resolved union (`overlay.list_images`), because a thin campaign
    may hold the *description* of an image whose bytes it still inherits from
    its world — filtering on this directory alone would drop exactly those. The
    same override, for the same reason, as `image_subjects.copy_to_character`'s
    `taken_names`.

    `ids` is name -> image id from the caller's own listing, so the placements
    it already read are not read again; a present name missing from it is a
    legacy row and reads only its key. Without `ids`, one `image_refs.scan(d)`
    answers -- and only when some present name has no string key at all.
    """
    raw = read_raw(d)
    present = _names(d) if names is None else names
    if ids is None:
        keyless = any(not isinstance(raw.get(n), str) for n in present)
        ids = _placed_ids(d) if keyless else {}
    out: dict[str, str] = {}
    for n in present:
        text = _text(raw, n, ids.get(n))
        if text is not None:
            out[n] = text
    return out


def object_id_in(d: Path, name: str) -> str | None:
    """The image id of `name`'s placement in `d` when it RESOLVES (object and
    blob both present), else None. The write side's question: only a resolving
    placement's object is one a description may be written to."""
    resolved = assets.resolve(d, name)
    return resolved.image_id if resolved is not None else None


def described_names(d: Path, rows: list[dict]) -> set[str]:
    """The names among listing `rows` (``name``, optional ``image_id``) that
    are R1-described in `d`.

    Off the rows' own ids: an object is read only for a row that carries an
    ``image_id`` and has no string legacy key, no placement is read, and no
    blob is resolved -- which is what lets every describe backlog afford it.
    """
    return _described(read_raw(d), rows)


def _described(raw: dict, rows: list[dict]) -> set[str]:
    return {r["name"] for r in rows
            if _text(raw, r["name"], r.get("image_id")) is not None}


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


def set_in(d: Path, name: str, text: str, names: set[str] | None = None, *,
           also_clear: Path | None = None, fallback_dir: Path | None = None) -> None:
    """Describe one image of `d` (R2).

    A name whose placement resolves is described on its image object, and
    once `image_store.update` confirms the write, the name's legacy key is
    dropped here and in `also_clear` (the visible placement's directory, when a
    caller edits through another one). Anything else -- the write not
    confirmed, a placement whose object has not arrived, a name with no
    placement -- writes the legacy key, exactly as before: `fallback_dir`'s
    when given, else `d`'s. A caller describing the visible placement of
    ANOTHER directory (a campaign editing inherited art) names its own
    directory there, so text it could not put on the object lands where it
    was edited (R2/R8) rather than in the directory it merely reads through.

    The key being written still has to name a real image (`ValueError`);
    `names` overrides what "real" means — see `read_in`. Text over
    `image_store.MAX_DESCRIPTION` is refused with `DescriptionTooLongError`.
    """
    # Under the sidecar lock for the WHOLE operation, and the existence check
    # with it: an image lifecycle event holds the same lock while it moves
    # entries around, so a check made outside it could validate a slot that has
    # already been promoted away.
    with assets.sidecar_lock(d, DESCRIPTIONS_FILE):
        if name not in (_names(d) if names is None else names):
            raise ValueError(f"unknown image(s): [{name!r}]")
        text = str(text)
        if len(text) > image_store.MAX_DESCRIPTION:
            raise DescriptionTooLongError(
                f"description longer than {image_store.MAX_DESCRIPTION} characters")
        image_id = object_id_in(d, name)
        # A pure dict edit: the callback contract (`image_store.update`).
        if image_id is not None and image_store.update(
                image_id, lambda raw: {**raw, "description": text}):
            # Object first, keys second: a failure between the two leaves the
            # old key showing (R1), never nothing. Cleared STRICTLY, not with
            # `assets.edit_sidecar` (which swallows a failed write): a key left
            # behind keeps masking the new text, so the save must say it failed
            # rather than answer ok over the old words. A retry finishes it.
            _clear_legacy(d, name)
            if also_clear is not None and also_clear != d:
                _clear_legacy(also_clear, name)
            return
        _write_legacy(d if fallback_dir is None else fallback_dir, name, text)


def _clear_legacy(d: Path, name: str) -> None:
    """Remove `name`'s legacy key from `d`'s sidecar, under its lock; the file
    goes when that empties it. `OSError` propagates. A missing or garbled
    sidecar holds no string key to mask anything, and is left as found."""
    with assets.sidecar_lock(d, DESCRIPTIONS_FILE):
        cur = read_raw(d)
        if name not in cur:
            return
        del cur[name]
        if cur:
            atomic.write_text(path_in(d), json.dumps(cur, indent=2, sort_keys=True) + "\n")
        else:
            path_in(d).unlink(missing_ok=True)


def carry_legacy(d: Path, name: str, text: str) -> None:
    """Write `text` as `name`'s legacy key in `d` and touch nothing else -- no
    name check, no object. For a promote carrying visible text up into a
    campaign (R5), which must never write the shared object."""
    with assets.sidecar_lock(d, DESCRIPTIONS_FILE):
        _write_legacy(d, name, str(text))


def _write_legacy(d: Path, name: str, text: str) -> None:
    """Raw read-modify-write of one legacy key, under the sidecar lock
    (reentrant: `set_in`'s own hold covers it at no cost).

    Raw read, then a write of only the key being touched: entries for images
    we are not touching survive even if their file has since vanished."""
    with assets.sidecar_lock(d, DESCRIPTIONS_FILE):
        cur = read_raw(d)
        cur[name] = text
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
             names: set[str] | None = None,
             ids: dict[str, str] | None = None) -> dict[str, str]:
    if not (safe_id(aid) and safe_id(vid)):
        return {}
    return read_in(_dir(root, aid, vid, base), names, ids)


def listing_names_ids(rows: list[dict]) -> tuple[set[str], dict[str, str]]:
    """`(names, ids)` for `read_all`/`read_in` from an image listing the caller
    already built (`assets.list_images`/`list_in` rows), so the description
    read neither lists the folder again nor scans its placements. Filtered by
    `assets.storable`, the rule `_names` applies to its own listing."""
    names = {r["name"] for r in rows if assets.storable(r["name"])}
    return names, {r["name"]: r["image_id"] for r in rows
                   if r["name"] in names and r.get("image_id")}


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
    cache-busting ``v`` token ``assets.list_in`` resolves, and the two facts R1
    answers. ``described`` is whether any text answers -- the legacy key or the
    object's -- including ``""``, the distinction this module's docstring turns
    on: an image reviewed and deliberately left blank IS described, and its
    ``description`` is then ``""``. A non-string legacy value counts as absent
    for the reason ``read_in`` drops it: a hand-edited or half-synced store
    must not hand a list to a caller expecting text.

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
            text = _text(reviewed, img["name"], img.get("image_id"))
            out.append({"id": rec.name, "vid": vdir.name, "name": img["name"],
                        "ext": img["ext"], "v": img["v"],
                        "described": text is not None,
                        "description": text or "",
                        # Only a placement backed by the image store has one.
                        **({"image_id": img["image_id"]} if img.get("image_id") else {})})
    return out


def undescribed(root: Path, base: str = "characters") -> list[dict]:
    """Every stored image of `base` that nothing describes (R1: no string
    legacy key and no string on its object) — the authoring queue.

    Text absent = unreviewed; an explicit ``""`` counts as reviewed. Sorted by
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
    """How many images of `base` nothing describes, without building the list.

    The walk `undescribed` takes (`_undescribed_names`), with its two rules --
    `assets.storable`, and R1 text PRESENCE rather than non-empty text -- and
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
        #
        # The placements come from that same call (`with_refs`), so an image
        # with no legacy key is checked against its object by the id its
        # placement names -- an object read, never a blob resolution, and never
        # a second scan of the placements.
        todo = sorted(_todo_in(vdir))
        if todo:
            yield rec.name, vdir.name, todo


def _backlog_rows(d: Path) -> tuple[list[dict], dict]:
    """`(rows, raw sidecar)` for a backlog walk over `d`: one ``{"name",
    "image_id"?}`` row per name `assets.names_in` reports, ids off the
    placements that one call scanned. Unfiltered; `_todo_in` applies
    `assets.storable`. (A flat library builds its rows with
    `image_library.backlog_rows`, by its own name policy.)"""
    names, has_sidecar, refs = assets.names_in(d, DESCRIPTIONS_FILE, with_refs=True)
    rows = []
    for n in names:
        ref = refs.get(n)
        image_id = ref.image if ref is not None else None
        rows.append({"name": n, "image_id": image_id} if image_id else {"name": n})
    return rows, (read_raw(d) if has_sidecar and names else {})


def _todo_in(d: Path) -> list[str]:
    """The storable names of `d` nothing describes (R1), unsorted."""
    rows, raw = _backlog_rows(d)
    rows = [r for r in rows if assets.storable(r["name"])]
    done = _described(raw, rows)
    return [r["name"] for r in rows if r["name"] not in done]


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
    """Whether ANY image of `base` is undescribed (R1) — `undescribed_count`
    stopping at the first one.

    The rail's badge counts chores, not instances, so "is this backlog empty"
    is the whole question it asks, and answering it by summing the backlog is
    a whole-store walk per navigation to learn a bit. Where a backlog exists
    this returns on the first record it opens.

    It is only expensive in the case where it returns False, which is the case
    where the chore does not appear at all -- and a library with no undescribed
    art anywhere has little for the walk to visit.
    """
    return any(_todo_in(vdir) for _rec, vdir in _walk(root, base))
