"""Per-greeting image subjects — which characters appear in each picture.

Deliberately named "subjects", not "tags" — tags mean player-trait gating
elsewhere in the store.

## Where an answer lives (spec section 9)

On the image OBJECT, scoped to the world, for every key whose picture is a
placement in this world: the associations
``{"kind": "character", "relation": "subject", "scope": "world:<wid>", "id":
<cid>}`` and the scope in ``reviews.subjects``. So tagging a picture in one
greeting tags it in every placement of it in the world, and in no other world
(the same bytes in two worlds are one object; the description is shared, the
subjects are not).

In the greeting's SIDECAR, ``<root>/greetings/<gid>/assets/default/subjects.json``
(the focus.json pattern, ``{"<image-name-or-reference-URL>": ["<cid>", ...]}``),
for everything else: a remote http(s) reference, a reference to a picture
placed in ANOTHER world (R11 -- the tag must travel with this greeting's world
bundle, not with the other world's object), and a legacy name with no
placement. And for a placement whose object write was not confirmed (R8):
the answer is never lost.

Reads follow R1: a sidecar list for the key wins while it is there, which is
how an unmigrated answer keeps showing; a write that lands on the object
deletes the key, after the write and never before. Tolerant reads, strict
writes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from . import assets, atomic, characters, greeting_images, image_refs, image_scopes, image_store

SUBJECTS_FILE = "subjects.json"
_BASE = "greetings"
_VID = "default"
_KIND = "character"
_RELATION = "subject"

_Item = tuple[dict, greeting_images.Target]


def subjects_path(root: Path, gid: str) -> Path:
    return root / _BASE / gid / "assets" / _VID / SUBJECTS_FILE


def _image_names(root: Path, gid: str) -> set[str]:
    return set(greeting_images.catalog(root, gid))


def _read_raw(root: Path, gid: str) -> dict:
    """The sidecar as stored: {} on missing/garbled file, no filtering."""
    p = subjects_path(root, gid)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _scope(root: Path) -> str:
    """This world's scope (R10): the id as the filesystem spells it, so a root
    reached as `REALM` keeps its tags where `realm` does. Spelled once, in
    `image_scopes`, which strips and copies the same scope."""
    return image_scopes.world_scope(root.name)


def _slot_of(root: Path, item: _Item) -> tuple[Path, str] | None:
    """The catalog item's slot when it lies under `root` -- None for a remote
    URL, and for a picture placed in another world's root (R11)."""
    target = item[1]
    if target is None or target[0] != "slot":
        return None
    d, name = target[1]
    return (d, name) if d.is_relative_to(root) else None


def _id_of(root: Path, item: _Item) -> str | None:
    """The image the item's slot resolved to while the catalog was built, when
    that slot is this world's (`_slot_of`). The read side's question: carried
    by the catalog, so no placement is read a second time and no blob is
    resolved."""
    target = item[1]
    return target[2] if target is not None and _slot_of(root, item) is not None else None


def _placement_of(root: Path, item: _Item) -> image_refs.ResolvedImage | None:
    """The placement behind `_slot_of`, resolved NOW (object and blob) -- the
    write side's question, as `image_descriptions.object_id_in` is for text:
    only a picture that is there may have its object written."""
    slot = _slot_of(root, item)
    return assets.resolve(*slot) if slot is not None else None


def _is_ours(a: object, scope: str) -> bool:
    return (isinstance(a, dict) and a.get("kind") == _KIND
            and a.get("relation") == _RELATION and a.get("scope") == scope)


def _object_subjects(image_id: str | None, scope: str) -> list[str] | None:
    """R1's second step: the object's subjects in `scope` when the scope is in
    its ``reviews.subjects``, else None (unreviewed here). Tolerant: a
    malformed association list, or an association whose id is not a string,
    contributes nothing."""
    obj = image_store.read(image_id) if image_id is not None else None
    if obj is None:
        return None
    reviews = obj.raw.get("reviews")
    reviewed = reviews.get("subjects") if isinstance(reviews, dict) else None
    if not isinstance(reviewed, list) or scope not in reviewed:
        return None
    assoc = obj.raw.get("associations")
    if not isinstance(assoc, list):
        return []
    return sorted({a["id"] for a in assoc if _is_ours(a, scope) and isinstance(a.get("id"), str)})


class _Scope:
    """`_scope(root)` on first use, then kept: a sweep over every greeting of a
    world asks the filesystem once, and a greeting with no placement never."""

    def __init__(self, root: Path):
        self._root = root
        self._value: str | None = None

    def __call__(self) -> str:
        if self._value is None:
            self._value = _scope(self._root)
        return self._value


def _answers(root: Path, gid: str, items: dict[str, _Item],
             scope: _Scope) -> tuple[dict, dict[str, list[str]]]:
    """`(the sidecar as stored, key -> its object's subjects)` for one
    greeting's catalog. An object is read only for a key the sidecar does not
    hold (R1), by the id the catalog carries: no placement is read again and
    no blob is resolved."""
    raw = _read_raw(root, gid)
    objects: dict[str, list[str]] = {}
    for key, item in items.items():
        if key in raw:
            continue
        image_id = _id_of(root, item)
        subjects = _object_subjects(image_id, scope()) if image_id is not None else None
        if subjects is not None:
            objects[key] = subjects
    return raw, objects


def _subjects(root: Path, gid: str, known_cids: set[str] | None,
              scope: _Scope) -> dict[str, list[str]]:
    items = greeting_images.catalog_with_slots(root, gid)
    raw, objects = _answers(root, gid, items, scope)
    lists: dict[str, list] = {}
    for name in items:
        legacy = raw.get(name)
        if isinstance(legacy, list):
            lists[name] = legacy
        elif name in objects:
            lists[name] = objects[name]
    if not lists:
        return {}
    cids = set(characters.character_refs(root)) if known_cids is None else known_cids
    # `isinstance(c, str)` FIRST, and not for tidiness: `c in cids` against a
    # set raises TypeError for an unhashable member, so a hand-edited or
    # half-synced sidecar holding a nested list or object took the caller
    # down. That was survivable while every caller read one greeting; the
    # world gallery reads them all, so one malformed member 500'd the whole
    # Images view. Same tolerance the rest of this module reads with.
    return {name: [c for c in subs if isinstance(c, str) and c in cids]
            for name, subs in lists.items()}


def read_subjects(root: Path, gid: str, known_cids: set[str] | None = None) -> dict[str, list[str]]:
    """What each answered image of this greeting shows (R1): the sidecar's list
    while it holds the key, else the object's subjects in this world.

    Tolerant: a missing or garbled sidecar or object answers nothing; vanished
    images and deleted characters drop out silently (no dangling chips), and a
    sidecar value that is not a list answers nothing here (see
    `reviewed_names`). An entry that empties out stays as [] — it still means
    'reviewed'. Sweeps over many greetings pass `known_cids` so character ids
    are enumerated once, not per greeting."""
    return _subjects(root, gid, known_cids, _Scope(root))


def write_subjects(root: Path, gid: str, subjects: dict[str, list[str]]) -> None:
    """Strict: every key must identify a current picture of this greeting. An explicit
    empty list persists — it means 'reviewed, no subjects' and keeps the image
    out of the untagged queue (key absent = unreviewed). Writes the sidecar
    only: the legacy writer, which R1 reads ahead of any object."""
    names = _image_names(root, gid)
    unknown = set(subjects) - names
    if unknown:
        raise ValueError(f"unknown image(s): {sorted(unknown)}")
    trimmed = {n: list(subs) for n, subs in subjects.items()}
    p = subjects_path(root, gid)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, json.dumps(trimmed, indent=2, sort_keys=True) + "\n")


def _tagged(scope: str, cids: list[str]) -> Callable[[dict], dict]:
    """The `image_store.update` callback replacing `scope`'s character
    subjects with `cids` and marking the scope reviewed. A pure dict edit (the
    callback contract): other scopes' entries, and anything this does not
    recognise, are kept as found."""
    ours = [{"kind": _KIND, "relation": _RELATION, "scope": scope, "id": c}
            for c in sorted(set(cids))]

    def change(raw: dict) -> dict:
        assoc = raw.get("associations")
        kept = [a for a in assoc if not _is_ours(a, scope)] if isinstance(assoc, list) else []
        raw["associations"] = kept + ours
        reviews = raw.get("reviews")
        reviews = dict(reviews) if isinstance(reviews, dict) else {}
        done = reviews.get("subjects")
        done = [s for s in done if isinstance(s, str)] if isinstance(done, list) else []
        reviews["subjects"] = sorted({*done, scope})
        raw["reviews"] = reviews
        return raw
    return change


def set_image_subjects(root: Path, gid: str, name: str, cids: list[str]) -> None:
    """Answer one image of this greeting (R2): on its object when the key is a
    placement in this world that resolves and the write is confirmed, then the
    key leaves the sidecar; otherwise in the sidecar, as before. `ValueError`
    for a key that is not a current picture of the greeting."""
    p = subjects_path(root, gid)
    # Under the sidecar's lock for the whole operation, as a description save
    # holds its own: the key decision and the key's removal are one step.
    with assets.sidecar_lock(p.parent, SUBJECTS_FILE):
        items = greeting_images.catalog_with_slots(root, gid)
        if name not in items:
            raise ValueError(f"unknown image: {name}")
        placed = _placement_of(root, items[name])
        if placed is not None and image_store.update(placed.image_id, _tagged(_scope(root), cids)):
            # Object first, key second: a failure between the two leaves the
            # old key answering (R1), never nothing. Strict, as
            # `image_descriptions._clear_legacy` is: a key left behind masks
            # the new answer, so the save must fail rather than answer ok.
            cur = _read_raw(root, gid)
            if name in cur:
                del cur[name]
                if cur:
                    atomic.write_text(p, json.dumps(cur, indent=2, sort_keys=True) + "\n")
                else:
                    p.unlink(missing_ok=True)
            return
        # Raw read-modify-write: preserves entries for images we aren't
        # touching even if their character was deleted. Validate the entry
        # being edited, not stale untouched references. Removing an image from
        # the body must not make another image's save impossible.
        cur = _read_raw(root, gid)
        cur[name] = list(cids)
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(p, json.dumps(cur, indent=2, sort_keys=True) + "\n")


def reviewed_names(root: Path, gid: str) -> set[str]:
    """Which of this greeting's images have been ANSWERED: a sidecar key (any
    value), or the object reviewed in this world's scope.

    Public, and shared by the two listings that turn on it, because they must
    agree: the tagging queue offers what is not here, and the world gallery
    (#200) marks what is not here as unfinished. `read_subjects` cannot answer
    it, and that is the point -- it drops an entry whose value is not a list, so
    a hand-edited or half-synced sidecar reads there as untagged while the queue
    considers it done, leaving an unfinished tile with no way to resolve it.
    """
    raw, objects = _answers(root, gid, greeting_images.catalog_with_slots(root, gid), _Scope(root))
    return set(raw) | set(objects)


def _gids(gdir: Path) -> list[str]:
    return sorted({p.name for p in gdir.iterdir() if p.is_dir()} | {p.stem for p in gdir.glob("*.md")})


def untagged(root: Path) -> list[dict]:
    """Every stored or referenced image with no answer — the tagging queue.
    Unanswered = unreviewed; an explicit [] counts as reviewed."""
    out: list[dict] = []
    gdir = root / _BASE
    if not gdir.exists():
        return out
    scope = _Scope(root)
    for gid in _gids(gdir):
        items = greeting_images.catalog_with_slots(root, gid)
        raw, objects = _answers(root, gid, items, scope)
        reviewed = set(raw) | set(objects)
        for name, (image, _target) in sorted(items.items()):
            if name not in reviewed:
                out.append({"gid": gid, "name": name, **image})
    return out


def appearances(root: Path, cid: str) -> list[dict]:
    """Every tagged image featuring `cid`, across all greetings — the
    character page's 'Appears in' gallery. Every greeting is visited, since an
    answer on the object leaves no sidecar behind. Sorted by (gid, name)."""
    out: list[dict] = []
    gdir = root / _BASE
    if not gdir.exists() or cid not in characters.character_refs(root):
        return out
    known = {cid}  # only this character's membership matters; skip re-filtering the rest
    scope = _Scope(root)
    for gid in _gids(gdir):
        images = greeting_images.catalog_with_slots(root, gid)
        for name, subs in sorted(_subjects(root, gid, known, scope).items()):
            # The body can change between the two reads. A newly restored
            # reference belongs to the next inventory, not this snapshot.
            if cid in subs and name in images:
                out.append({"gid": gid, "name": name, **images[name][0]})
    return out


def copy_to_character(root: Path, gid: str, name: str, cid: str, vid: str, slot: str,
                      src_root: Path | None = None, taken_names: set[str] | None = None) -> str:
    """Place a greeting image in a character version's assets -- by reference.

    The character's slot names the very image the source holds (spec section
    8), so no bytes are written: a ref-backed source is linked as it stands,
    and a legacy source file is ingested once (the store keeps its blob) and
    then linked; the source itself is left as it was.
    slot 'avatar' overwrites the avatar (focus resets, as a new avatar's does);
    slot 'gallery' takes the next free gallery_N. Returns the stored name.
    `src_root` defaults to `root`; a campaign caller passes the overlay-resolved
    root so an inherited (unmaterialized) greeting image can still be copied,
    while the destination character write always lands under `root`.
    `taken_names` overrides the free-slot scan: a campaign caller must pass
    the overlay-resolved union (overlay.list_images) so an inherited world
    gallery image can't be silently shadowed by a reused gallery_N name."""
    if slot not in ("avatar", "gallery"):
        raise ValueError(f"unknown slot: {slot}")
    source = root if src_root is None else src_root
    if name.startswith("/api/worlds/"):
        if name not in greeting_images.catalog(source, gid):
            raise FileNotFoundError(name)
        src = greeting_images.local_path(source, name)
        held = greeting_images.local_slot(source, name) if src is not None else None
    else:
        src = assets.image_path(source, gid, _VID, name, base=_BASE)
        # `image_path` answering at all proves both ids safe
        held = (assets.version_dir(source, gid, _VID, base=_BASE), name) if src is not None else None
    if src is None or held is None:
        raise FileNotFoundError(name)
    placed = assets.resolve(*held)
    image_id = (placed.image_id if placed is not None
                else image_store.ingest(src.read_bytes(), src.suffix[1:]).id)
    dst = assets.version_dir(root, cid, vid)
    if slot == "avatar":
        assets.link_in(dst, assets.AVATAR, image_id)
        assets.clear_focus(root, cid, vid)
        return assets.AVATAR
    taken = ({i["name"] for i in assets.list_images(root, cid, vid)}
             if taken_names is None else taken_names)
    n = 1
    while f"gallery_{n}" in taken:
        n += 1
    assets.link_in(dst, f"gallery_{n}", image_id)
    return f"gallery_{n}"
