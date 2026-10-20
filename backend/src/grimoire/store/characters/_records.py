"""characters: records responsibilities."""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import assets, atomic, cards
from .. import frontmatter as _store_frontmatter
from .. import paths as _store_paths
from . import _card_fields, _paths, _reads


def create_character(root: Path, name: str, version_name: str = "default", card: dict | None = None,
                     taken=None) -> tuple[str, str]:
    _paths._chars_dir(root).mkdir(parents=True, exist_ok=True)

    def exists(c: str) -> bool:
        # `taken` widens the id namespace (overlay: world dirs + tombstones)
        return _paths._char_dir(root, c).exists() or (taken is not None and taken(c))

    cid = _store_paths.uniquify(_store_paths.slugify(name), exists)
    _paths._char_dir(root, cid).mkdir(parents=True)
    vid = _store_paths.slugify(version_name)
    card = card or _card_fields.blank_card(name)
    _card_fields._apply_label(card, version_name, vid)
    cards.bake_char_name(card)  # #137: {{char}} is always self-reference, baked at write time
    atomic.write_text(_paths._card_path(root, cid, vid), _card_fields._dumps(card))
    atomic.write_text(_paths._meta_path(root, cid), _store_frontmatter.dump_frontmatter({"name": name, "default_version": vid}, ""))
    return cid, vid


def create_version(root: Path, cid: str, version_name: str, card: dict) -> str:
    _paths._require_char(root, cid)
    vid = _store_paths.uniquify(_store_paths.slugify(version_name), lambda v: _paths._card_path(root, cid, v).exists())
    _card_fields._apply_label(card, version_name, vid)
    cards.bake_char_name(card)  # #137: {{char}} is always self-reference, baked at write time
    atomic.write_text(_paths._card_path(root, cid, vid), _card_fields._dumps(card))
    return vid


def update_version(root: Path, cid: str, vid: str | None, card: dict) -> None:
    p = _paths.require_version(root, cid, vid)
    cards.bake_char_name(card)  # #137: {{char}} is always self-reference, baked at write time
    atomic.write_text(p, _card_fields._dumps(card))


def set_default_version(root: Path, cid: str, vid: str) -> None:
    _paths.require_version(root, cid, vid)
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    meta["default_version"] = vid
    atomic.write_text(_paths._meta_path(root, cid), _store_frontmatter.dump_frontmatter(meta, ""))


def set_name(root: Path, cid: str, name: str) -> None:
    """The container's display name -- what the grid, the cast panel and every
    `meta.name` prompt section read (#13).

    The id deliberately does NOT move with it: every reference in the store is
    by id (manifest refs, appearance records, greetings, relationships), and
    re-slugging the directory would strand all of them. A renamed character
    keeps the slug it was created under, visible only in URLs.
    """
    _paths._require_char(root, cid)
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    meta["name"] = name
    atomic.write_text(_paths._meta_path(root, cid), _store_frontmatter.dump_frontmatter(meta, ""))


def set_birthdate(root: Path, cid: str, birthdate: str) -> None:
    _paths._require_char(root, cid)
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    meta["birthdate"] = birthdate
    atomic.write_text(_paths._meta_path(root, cid), _store_frontmatter.dump_frontmatter(meta, ""))


def set_tags(root: Path, cid: str, tags: list[str]) -> None:
    _paths._require_char(root, cid)
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    meta["tags"] = ",".join(tags)
    atomic.write_text(_paths._meta_path(root, cid), _store_frontmatter.dump_frontmatter(meta, ""))


def set_chub_source(root: Path, cid: str, vid: str, full_path: str) -> None:
    """Link one version to a chub.ai card. Stored in that version's own card
    (extensions, same spot as grimoire_label) so each variant of a character
    carries its own link rather than sharing one character-wide value."""
    card = _reads.read_card(root, cid, vid)
    # Through the same guarded writer as the label: `setdefault` does not
    # replace a non-dict, so a hand-edited `extensions` string raised here.
    _card_fields._writable_extensions(card)["chub_source"] = full_path
    update_version(root, cid, vid, card)


def clear_chub_source(root: Path, cid: str, vid: str) -> None:
    card = _reads.read_card(root, cid, vid)
    ext = _card_fields._extensions(card)
    if "chub_source" in ext:
        del ext["chub_source"]
        update_version(root, cid, vid, card)


def delete_version(root: Path, cid: str, vid: str) -> None:
    p = _paths.require_version(root, cid, vid)
    if len(_paths._version_ids(root, cid)) == 1:
        raise ValueError("cannot delete the last version of a character")
    p.unlink()
    # The card was the only thing that made this version's art addressable
    # (#360). After the unlink, not before: a failure here then strands bytes
    # exactly as this code did before, where the other order would lose art
    # for a version that still exists.
    assets.delete_version_images(root, cid, vid)
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    if meta.get("default_version") == vid:
        meta["default_version"] = _paths._version_ids(root, cid)[0]
        atomic.write_text(_paths._meta_path(root, cid), _store_frontmatter.dump_frontmatter(meta, ""))


def delete_character(root: Path, cid: str) -> None:
    _paths._require_char(root, cid)
    shutil.rmtree(_paths._char_dir(root, cid))
