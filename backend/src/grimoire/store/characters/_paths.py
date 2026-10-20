"""characters: paths responsibilities."""

from __future__ import annotations

from pathlib import Path

from .. import paths as _store_paths


class CharacterNotFound(Exception):
    pass


class VersionNotFound(Exception):
    pass


def _chars_dir(root: Path) -> Path:
    return root / "characters"


def _char_dir(root: Path, cid: str) -> Path:
    """The character's directory. Raises CharacterNotFound for an id that
    doesn't name a child of the characters dir, so every path built from it
    inherits the guard rather than having to repeat it (#240)."""
    if not _store_paths.safe_id(cid):
        raise CharacterNotFound(cid)
    return _chars_dir(root) / cid


def _meta_path(root: Path, cid: str) -> Path:
    return _char_dir(root, cid) / "character.md"


def _card_path(root: Path, cid: str, vid: str | None) -> Path:
    return _char_dir(root, cid) / f"{vid}.json"


def _require_char(root: Path, cid: str) -> Path:
    d = _char_dir(root, cid)   # raises CharacterNotFound for an unsafe id
    if not _meta_path(root, cid).exists():
        raise CharacterNotFound(cid)
    return d


def _version_ids(root: Path, cid: str) -> list[str]:
    return _version_ids_in(_char_dir(root, cid))


def _version_ids_in(d: Path) -> list[str]:
    # enumeration agrees with the lookups: a version id read_card would
    # refuse must not reach it through a listing (#259 review)
    return sorted(p.stem for p in d.glob("*.json") if _store_paths.safe_id(p.stem))


def require_version(root: Path, cid: str, vid: str | None) -> Path:
    """Assert `cid`/`vid` name a real character version; return the card's path.

    Two stats and no read, which is the point: the image routes gate on this
    per request (#360), and reading the card to answer a question `Path.exists`
    answers would put a file read and a JSON parse on every upload.

    Raises the same two exceptions the lookups here already raised inline; this
    is the guard pair they were each repeating, named once -- the same shape
    `pcs.require_version` is for the PC surface. An unlocked appearance can
    supply None: it is rejected as a version only AFTER character existence,
    so a missing character keeps its own failure rather than being relabelled.
    """
    _require_char(root, cid)
    p = _card_path(root, cid, vid)
    if not _store_paths.safe_id(vid) or not p.exists():
        raise VersionNotFound(vid)
    return p


def _addressable_default(stored: str, version_ids: list[str]) -> str:
    """The default version to report, given the versions callers can actually
    reach.

    Filtering an unaddressable version out of the listing (#259 review) leaves
    the actor's stored `default_version` pointing at something no longer in the
    set; the editor then asks for a version that isn't there and for
    `versions[0]` that doesn't exist either. Report the first addressable
    version instead, so meta and versions always agree.
    """
    return stored if stored in version_ids else (version_ids[0] if version_ids else "")
