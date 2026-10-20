"""Version image names, accepted extensions and placement sidecar filenames."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..paths import safe_id

AVATAR = "avatar"
#: What `store.localize` names the images it writes, which the grid counts
#: apart from gallery art.
LOCALIZED_PREFIX = "embed-"
FOCUS_FILE = "focus.json"
#: The per-image description sidecar (`store.image_descriptions`). Its NAME
#: lives here, with `FOCUS_FILE`, because this module owns the directories both
#: sit in and has to take an entry with a deleted image; its SEMANTICS -- what
#: an absent key means, what a write will accept -- live in the module named
#: after it. Spelling the string there instead would be one rule in two places.
DESCRIPTIONS_FILE = "descriptions.json"


def gallery_count(names: Iterable[str]) -> int:
    """Gallery art among a version's image names: neither avatar nor localized."""
    return sum(1 for n in names if n != AVATAR and not n.startswith(LOCALIZED_PREFIX))


def localized_count(names: Iterable[str]) -> int:
    return sum(1 for n in names if n.startswith(LOCALIZED_PREFIX))
_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
_PROMOTE_TMP = "promote-tmp"  # the temp name the pre-#253 three-rename swap used


def _addressable_name(name: str) -> bool:
    """Can this name be used as an image id at all?

    Reject "." (ambiguous with ext) and glob metacharacters (the cleanup/lookup
    globs name.*), on top of the shared id guard. Split out from `_safe_name`
    because listing and writing want different answers about `promote-tmp`:
    it is not writable, but a stranded one must stay visible (see list_images).
    """
    return (safe_id(name) and "." not in name
            and not any(c in name for c in "*?[]"))


def _safe_name(name: str) -> bool:
    #
    # `promote-tmp` is reserved, not rejected on a whim: the old three-rename
    # swap renamed onto that exact path, so an image stored under it would have
    # been clobbered (POSIX) or would have broken every promotion (Windows) --
    # the name was never usable. Reserving it is what lets
    # `_heal_stranded_promotion` treat such a file as crash residue rather than
    # as somebody's image (PR review).
    #
    # Case-folded, because the reservation has to hold on the filesystem's terms
    # rather than Python's: on Windows and macOS `Promote-Tmp.png` *is*
    # `promote-tmp.png`, so a case variant would otherwise slip an image into the
    # name the recovery scan claims (PR review).
    return _addressable_name(name) and name.casefold() != _PROMOTE_TMP


def storable(name: str) -> bool:
    """Is `name` one this module will both store under and resolve back?

    The public form of `_safe_name`, for a caller that has to filter a listing
    by what a write and a read will accept. `list_in` cannot answer this itself:
    it filters on `_addressable_name`, one name looser, because a stranded
    `promote-tmp` is shown on purpose (#253) -- and that exception belongs to a
    per-version folder, not to every directory built on these primitives. See
    `image_library.addressable` for what a caller without promotions does
    with it.
    """
    return _safe_name(name)


def norm_ext(ext: str) -> str:
    ext = ext.lstrip(".").lower()
    return ext if ext in _EXTS else ""


def _dir(root: Path, cid: str, vid: str, base: str = "characters") -> Path:
    return root / base / cid / "assets" / vid


def version_dir(root: Path, cid: str, vid: str, base: str = "characters") -> Path:
    """The per-version image directory of record `cid`, version `vid` --
    `ValueError` for an unsafe id. For a caller that works at the directory
    level (`link_in`, `resolve`) on a record's images, so the layout is spelled
    once, here."""
    if not (safe_id(cid) and safe_id(vid)):
        raise ValueError("unsafe image id")
    return _dir(root, cid, vid, base)
