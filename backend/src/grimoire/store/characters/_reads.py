"""characters: reads responsibilities."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .. import assets, cards, chub, image_descriptions, statcache
from .. import frontmatter as _store_frontmatter
from .. import paths as _store_paths
from . import _card_fields, _memo, _paths


def birthdate_meta(root: Path, cid: str) -> tuple[str, str]:
    """(display name, birthdate) without reading versions or image sidecars."""
    _paths._require_char(root, cid)
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    return meta.get("name", cid), meta.get("birthdate", "")


def read_tags(root: Path, cid: str) -> list[str]:
    """The tags this character carries when cast as the player (#18).

    Container-level, like a PC's tags: one set for every version, so a greeting
    gated on a tag does not flip with which version of the character is played.
    Stored as the world tag vocabulary's ids, comma-joined, the same shape as a
    PC's `pc.md` tags, so `greetings.requires_tags` compares verbatim."""
    _paths._require_char(root, cid)
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    return [t for t in (x.strip() for x in meta.get("tags", "").split(",")) if t]


def read_card(root: Path, cid: str, vid: str | None) -> dict:
    return json.loads(_paths.require_version(root, cid, vid).read_text(encoding="utf-8"))


def default_version(root: Path, cid: str) -> str:
    """The version `read_character` reports as the default: the stored one when
    it is addressable, else the first that is. `""` when no card is.

    Two stats and a frontmatter parse rather than the full read, for a caller
    that wants ONE version's id and not every version's card."""
    _paths._require_char(root, cid)
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    return _paths._addressable_default(meta.get("default_version", ""), _paths._version_ids(root, cid))


def version_label(root: Path, cid: str, vid: str) -> str:
    """`_version_label` for a stored version, by id."""
    return _card_fields._version_label(read_card(root, cid, vid), vid)


def version_ids(root: Path, cid: str) -> list[str]:
    """Every addressable version id of a character, sorted. Empty for a
    character the root does not hold, rather than raising: the overlay asks
    this of both roots and an answer of "none here" is a normal one."""
    try:
        _paths._require_char(root, cid)
    except _paths.CharacterNotFound:
        return []
    return _paths._version_ids(root, cid)


def name_and_versions(root: Path, cid: str) -> tuple[str, list[str]]:
    """`read_character`'s `meta.name` and version ids, reading no card.

    For a caller that only needs to know what a character is called and which
    versions it still has -- the describe queue, once per record behind an
    image -- where the full read opens every card, lists every version's art
    and reads its focus and descriptions, all to be thrown away. Raises
    `CharacterNotFound` exactly where `read_character` would before it reads a
    card: no such character, or not one addressable version.

    Memoized on two stamps (`_build_identity`), so an unchanged character
    costs two stats: the queue asks this for every record with art behind it
    whenever the character page mounts.
    """
    name, version_ids = statcache.memo_stamped(
        ("identity", os.fspath(_paths._char_dir(root, cid))), lambda: _build_identity(root, cid),
        pool=_memo._IDENTITY_POOL, max_entries=_memo.POOL_ENTRIES)
    return name, list(version_ids)


def _build_identity(root: Path, cid: str) -> tuple[tuple[str, tuple[str, ...]], tuple | None]:
    """`name_and_versions`' value, vouched for by the character directory
    (whose listing is the version ids) and `character.md` (the name), each
    stamped before it is read. A miss raises and is not remembered."""
    here = statcache.stamp(_paths._char_dir(root, cid))
    meta_stamp = statcache.stamp(_paths._meta_path(root, cid))
    _paths._require_char(root, cid)
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    version_ids = _paths._version_ids(root, cid)
    if not version_ids:
        raise _paths.CharacterNotFound(cid)
    value = (meta.get("name", cid), tuple(version_ids))
    if here is None or meta_stamp is None:
        return value, None
    return value, (here, meta_stamp)


def read_character(root: Path, cid: str) -> dict:
    _paths._require_char(root, cid)
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    version_ids = _paths._version_ids(root, cid)
    if not version_ids:
        # every card is unaddressable: nothing here can be opened or edited
        raise _paths.CharacterNotFound(cid)
    default_version = _paths._addressable_default(meta.get("default_version", ""), version_ids)
    # One-time fallback for data written before chub_source became
    # per-version: a value still sitting in character.md frontmatter only
    # ever applied to the default version, so that's the only place it
    # surfaces now -- a sibling version with no per-version value of its own
    # shows no link, prompting an explicit (and now easy) re-link.
    legacy_chub_source = meta.get("chub_source", "")
    versions = []
    for vid in version_ids:
        card = read_card(root, cid, vid)
        version_images = assets.list_images(root, cid, vid)
        chub_source = _card_fields._version_chub_source(card)
        if not chub_source and vid == default_version:
            chub_source = legacy_chub_source
        versions.append({
            "id": vid,
            "name": _card_fields._version_label(card, vid),
            "card": card,
            "images": [i["name"] for i in version_images],
            # `list_images` is one entry per logical image, resolved the way
            # the serve route resolves, so the token always names the bytes a
            # `?v=` URL will return -- and that URL is cached immutable.
            "image_v": {i["name"]: i["v"] for i in version_images},
            # Only for placements backed by the image store: a legacy file
            # has no identity to report, so it has no entry here.
            "image_ids": {i["name"]: i["image_id"] for i in version_images
                          if i.get("image_id")},
            "avatar_focus": assets.read_focus(root, cid, vid),
            # Beside `images`/`image_v`/`avatar_focus`: asset-derived, so it
            # travels with them rather than through a second round trip.
            # Off the listing above: no second listing, no placement scan.
            "image_descriptions": image_descriptions.read_all(
                root, cid, vid, "characters",
                *image_descriptions.listing_names_ids(version_images)),
            "chub_source": chub_source,
            "is_chub": bool(chub_source) and chub.parse_full_path(chub_source) is not None,
            "importable_lore": _card_fields._importable_lore(card),
        })
    return {
        "meta": {"id": cid, "name": meta.get("name", cid),
                 "default_version": default_version,
                 "birthdate": meta.get("birthdate", "")},
        "versions": versions,
    }


def _greeting_count(data: dict) -> int:
    """Selectable greetings on a card: a non-empty first_mes plus the alternates."""
    greetings = data.get("alternate_greetings")
    return ((1 if str(data.get("first_mes") or "").strip() else 0)
            + (len(greetings) if isinstance(greetings, list) else 0))


def _card_summary(root: Path, cid: str, vid: str,
                  stamped: tuple[Path, statcache.Stamp | None] | None = None) -> dict:
    """The small derived view of a card that list endpoints need (label,
    greeting count, chub link), memoized by stat so unchanged cards are
    never re-read or re-parsed.

    `stamped` is the card's path and a `statcache.stamp` of it that a caller
    already took, standing in for the path's construction and the stat: the
    listing stamps every card before asking for its summary, and doing both
    again per version is all it would buy.

    Keyed on the stamp's ctime as well as `statcache.signature`'s fields. The
    listing row holding this summary is re-validated on ctime, so a card
    rewritten in place at the same size with its old mtime handed back misses
    the row -- and a summary keyed without ctime would then answer the rebuild
    with the old label, which the row stores under its new stamps for good.
    The ctime rides as an entry of its own rather than a fifth field, so
    `memo`'s racy check still reads the four-field one."""
    if not _store_paths.safe_id(vid):
        raise _paths.VersionNotFound(vid)
    if stamped is None:
        p = _paths._card_path(root, cid, vid)
        stamped = (p, statcache.stamp(p))
    p, stamp = stamped
    if stamp is None:
        raise _paths.VersionNotFound(vid)
    sig = ((stamp[0], stamp[1], stamp[3], stamp[4]), (stamp[0], "ctime", stamp[2]))

    def compute() -> dict:
        card = json.loads(p.read_text(encoding="utf-8"))
        return {"label": _card_fields._version_label(card, vid),
                "greeting_count": _greeting_count(cards.card_data(card)),
                "chub_source": _card_fields._version_chub_source(card)}

    if not statcache.vouches(stamp):
        return compute()
    return statcache.memo("card_summary", sig, compute,
                          pool=_memo._SUMMARY_POOL, max_entries=_memo.POOL_ENTRIES)
