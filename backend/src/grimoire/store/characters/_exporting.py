"""characters: exporting responsibilities."""

from __future__ import annotations

from pathlib import Path

from .. import assets, cards, fetch
from .. import frontmatter as _store_frontmatter
from .. import paths as _store_paths
from . import _avatar, _paths, _reads


def _stored_avatar(root: Path, cid: str, vid: str) -> tuple[bytes, str] | None:
    p = assets.image_path(root, cid, vid, assets.AVATAR)
    if p is None:
        return None
    try:
        data = p.read_bytes()
    except OSError:
        return None  # an export must not fail over an image it cannot read
    # The bytes name the type (#321), the same rule the packers and the image
    # routes hold: this pair becomes the exported card's `data:` mime and, for
    # CHARX, the name its bundled member is written under, so a JPEG stored as
    # `avatar.png` would otherwise leave the app still claiming to be a PNG.
    # A file that sniffs as nothing keeps its stored suffix -- `_usable_avatar`
    # drops the avatar outright if that is not a type cards can carry.
    return data, fetch.sniff_ext(data) or p.suffix.lstrip(".").lower()


def _export_filename(root: Path, cid: str, vid: str, card: dict, fmt: str) -> str:
    """`<card name>[-<version>].<fmt>` — the name a download lands under.

    The version id is only appended when it is not the default one, so the
    common case is just the character's name; a card whose name slugifies to
    nothing falls back to the character id, which is a slug by construction.
    """
    stem = _store_paths.slugify(cards.card_data(card).get("name") or "")
    if stem == "untitled":  # slugify's sentinel: the name held nothing usable
        stem = cid
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    if vid != meta.get("default_version", "default"):
        stem = f"{stem}-{vid}"
    return f"{stem}.{fmt}"


def export_card(root: Path, cid: str, vid: str, fmt: str) -> tuple[bytes, str]:
    """The card as `fmt` bytes, plus the filename to offer it under.

    A pair, like the campaign export builders: the route has nothing else to
    name the download by, and the stored avatar rides along inside the bytes
    (#25) so an export is the whole character rather than only its text.
    """
    card = _reads.read_card(root, cid, vid)
    avatar = _stored_avatar(root, cid, vid)
    plane = _avatar._png_plane_of(avatar) if fmt == "png" else None
    blob = cards.dumps(card, fmt, avatar=avatar, plane_png=plane)
    return blob, _export_filename(root, cid, vid, card, fmt)
