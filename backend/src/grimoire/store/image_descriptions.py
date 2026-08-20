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

Two readers, and only one of them is human. The author writes it; ``search``
can match it; and ``store.art_catalog`` ranks it against the moment and offers
the closest few to the model. A non-empty description is also what makes an
image *reachable* by a model handle at all — see ``context.art.resolve_handles``
— so "described" is a deliberate act of publication, not merely a note.

Nothing detects a description drifting from the art it describes. An image
replaced under the same name keeps the old text, exactly the way ``focus.json``
keeps a crop that no longer frames anything. Stated rather than solved.
"""

from __future__ import annotations

import json
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
    d.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path_in(d), json.dumps(trimmed, indent=2, sort_keys=True) + "\n")


def set_in(d: Path, name: str, text: str, names: set[str] | None = None) -> None:
    """Read-modify-write of one image's entry.

    Raw read, then a strict write of only the key being touched: entries for
    images we are not touching survive even if their file has since vanished,
    while the key being written still has to name a real image. `names`
    overrides what "real" means — see `read_in`.
    """
    if name not in (_names(d) if names is None else names):
        raise ValueError(f"unknown image(s): [{name!r}]")
    cur = read_raw(d)
    cur[name] = str(text)
    # Not `write_in`: `cur` may legitimately carry entries for vanished images
    # (see the docstring), which `write_in` would reject as unknown.
    d.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path_in(d), json.dumps({k: str(v) for k, v in cur.items()},
                                             indent=2, sort_keys=True) + "\n")


# ---- per-version wrappers (characters / pcs / entity kinds) ----------------

def _dir(root: Path, aid: str, vid: str, base: str) -> Path:
    return root / base / aid / "assets" / vid


def read_all(root: Path, aid: str, vid: str, base: str = "characters",
             names: set[str] | None = None) -> dict[str, str]:
    if not (safe_id(aid) and safe_id(vid)):
        return {}
    return read_in(_dir(root, aid, vid, base), names)


def raw_keys(root: Path, aid: str, vid: str, base: str = "characters") -> set[str]:
    """The sidecar's keys as stored, unfiltered — "which images have been
    reviewed", which is the absent-vs-empty question. Through `_dir` rather
    than a path the caller assembles, so `tests/test_paths_guard.py`'s rule
    that filesystem access goes through the resolvers keeps holding one module
    further out."""
    if not (safe_id(aid) and safe_id(vid)):
        return set()
    return set(read_raw(_dir(root, aid, vid, base)))


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


def undescribed(root: Path, base: str = "characters") -> list[dict]:
    """Every stored image of `base` with NO sidecar key — the authoring queue.

    Key absent = unreviewed; an explicit ``""`` counts as reviewed. Sorted by
    (id, vid, name), which is the order the editors list versions and images in.
    """
    out: list[dict] = []
    bdir = root / base
    if not bdir.exists():
        return out
    for rec in sorted(p for p in bdir.iterdir() if p.is_dir()):
        adir = rec / "assets"
        if not adir.is_dir():
            continue
        for vdir in sorted(p for p in adir.iterdir() if p.is_dir()):
            reviewed = set(read_raw(vdir))  # key presence alone marks 'reviewed'
            out.extend({"id": rec.name, "vid": vdir.name, "name": name}
                       for name in sorted(_names(vdir)) if name not in reviewed)
    return out
