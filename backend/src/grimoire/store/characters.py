"""Character containers: one folder per character, one JSON V3 card per version.

Unlike generic entities (one markdown file each), a character is a directory:
  <root>/characters/<cid>/character.md   # frontmatter: name, default_version
  <root>/characters/<cid>/<vid>.json     # a SillyTavern V3 card
  <root>/characters/<cid>/assets/        # optional images (from PNG/CHARX import)
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from . import assets, fetch
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import slugify, uniquify


class CharacterNotFound(Exception):
    pass


class VersionNotFound(Exception):
    pass


def _safe(part: str) -> bool:
    return part not in ("", ".", "..") and "/" not in part and "\\" not in part


def _chars_dir(root: Path) -> Path:
    return root / "characters"


def _char_dir(root: Path, cid: str) -> Path:
    return _chars_dir(root) / cid


def _meta_path(root: Path, cid: str) -> Path:
    return _char_dir(root, cid) / "character.md"


def _card_path(root: Path, cid: str, vid: str) -> Path:
    return _char_dir(root, cid) / f"{vid}.json"


def _dumps(card: dict) -> str:
    return json.dumps(card, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def blank_card(name: str) -> dict:
    return {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {
            "name": name,
            "description": "",
            "personality": "",
            "scenario": "",
            "first_mes": "",
            "mes_example": "",
            "alternate_greetings": [],
            "tags": [],
            "extensions": {},
        },
    }


def _require_char(root: Path, cid: str) -> Path:
    d = _char_dir(root, cid)
    if not _safe(cid) or not _meta_path(root, cid).exists():
        raise CharacterNotFound(cid)
    return d


def create_character(root: Path, name: str, version_name: str = "default", card: dict | None = None) -> tuple[str, str]:
    _chars_dir(root).mkdir(parents=True, exist_ok=True)
    cid = uniquify(slugify(name), lambda c: _char_dir(root, c).exists())
    _char_dir(root, cid).mkdir(parents=True)
    vid = slugify(version_name)
    _card_path(root, cid, vid).write_text(_dumps(card or blank_card(name)), encoding="utf-8")
    _meta_path(root, cid).write_text(
        dump_frontmatter({"name": name, "default_version": vid}, ""), encoding="utf-8"
    )
    return cid, vid


def create_version(root: Path, cid: str, version_name: str, card: dict) -> str:
    _require_char(root, cid)
    vid = uniquify(slugify(version_name), lambda v: _card_path(root, cid, v).exists())
    _card_path(root, cid, vid).write_text(_dumps(card), encoding="utf-8")
    return vid


def update_version(root: Path, cid: str, vid: str, card: dict) -> None:
    _require_char(root, cid)
    p = _card_path(root, cid, vid)
    if not _safe(vid) or not p.exists():
        raise VersionNotFound(vid)
    p.write_text(_dumps(card), encoding="utf-8")


def set_default_version(root: Path, cid: str, vid: str) -> None:
    _require_char(root, cid)
    if not _safe(vid) or not _card_path(root, cid, vid).exists():
        raise VersionNotFound(vid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    meta["default_version"] = vid
    _meta_path(root, cid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def set_birthdate(root: Path, cid: str, birthdate: str) -> None:
    _require_char(root, cid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    meta["birthdate"] = birthdate
    _meta_path(root, cid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def set_chub_source(root: Path, cid: str, full_path: str) -> None:
    _require_char(root, cid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    meta["chub_source"] = full_path
    _meta_path(root, cid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def clear_chub_source(root: Path, cid: str) -> None:
    _require_char(root, cid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    meta.pop("chub_source", None)
    _meta_path(root, cid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def _version_ids(root: Path, cid: str) -> list[str]:
    return sorted(p.stem for p in _char_dir(root, cid).glob("*.json"))


def read_card(root: Path, cid: str, vid: str) -> dict:
    _require_char(root, cid)
    p = _card_path(root, cid, vid)
    if not _safe(vid) or not p.exists():
        raise VersionNotFound(vid)
    return json.loads(p.read_text(encoding="utf-8"))


def _version_label(card: dict, vid: str) -> str:
    """Display label for a version: an explicit grimoire_label override (kept in
    extensions so it never leaks into {{char}}), else the card's own name."""
    data = card.get("data", {})
    return (data.get("extensions") or {}).get("grimoire_label") or data.get("name", vid)


def read_character(root: Path, cid: str) -> dict:
    _require_char(root, cid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    versions = []
    for vid in _version_ids(root, cid):
        card = read_card(root, cid, vid)
        versions.append({
            "id": vid,
            "name": _version_label(card, vid),
            "card": card,
            "images": [i["name"] for i in assets.list_images(root, cid, vid)],
        })
    return {
        "meta": {"id": cid, "name": meta.get("name", cid),
                 "default_version": meta.get("default_version", ""),
                 "birthdate": meta.get("birthdate", ""),
                 "chub_source": meta.get("chub_source", "")},
        "versions": versions,
    }


def list_characters(root: Path) -> list[dict]:
    out: list[dict] = []
    d = _chars_dir(root)
    if d.exists():
        for cd in sorted(p for p in d.iterdir() if p.is_dir() and (p / "character.md").exists()):
            cid = cd.name
            meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
            default = meta.get("default_version", "")
            out.append({
                "id": cid,
                "name": meta.get("name", cid),
                "default_version": default,
                "has_avatar": assets.image_path(root, cid, default, assets.AVATAR) is not None,
                "versions": [{"id": v, "name": _version_label(read_card(root, cid, v), v)}
                             for v in _version_ids(root, cid)],
            })
    return out


def delete_version(root: Path, cid: str, vid: str) -> None:
    _require_char(root, cid)
    p = _card_path(root, cid, vid)
    if not _safe(vid) or not p.exists():
        raise VersionNotFound(vid)
    if len(_version_ids(root, cid)) == 1:
        raise ValueError("cannot delete the last version of a character")
    p.unlink()
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    if meta.get("default_version") == vid:
        meta["default_version"] = _version_ids(root, cid)[0]
        _meta_path(root, cid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def delete_character(root: Path, cid: str) -> None:
    _require_char(root, cid)
    shutil.rmtree(_char_dir(root, cid))


def card_hash(root: Path, cid: str, vid: str) -> str | None:
    p = _card_path(root, cid, vid)
    if not _safe(cid) or not _safe(vid) or not p.exists():
        return None
    return hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def character_count(root: Path) -> int:
    d = _chars_dir(root)
    return sum(1 for p in d.iterdir() if p.is_dir() and (p / "character.md").exists()) if d.exists() else 0


def character_refs(root: Path) -> list[str]:
    d = _chars_dir(root)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "character.md").exists())


def _avatar_candidates(card: dict) -> list[str]:
    """Every place a card might carry an avatar: V3 `assets`, a top-level `avatar`
    string, and either relocated into `extensions` by the V2->V3 upconvert."""
    data = card.get("data", {})
    ext = data.get("extensions") or {}
    out: list[str] = []
    for assets_src in (data.get("assets"), ext.get("assets")):
        for a in assets_src or []:
            if isinstance(a, dict) and a.get("type") in ("icon", "avatar"):
                uri = a.get("uri")
                if isinstance(uri, str) and uri:
                    out.append(uri)
    for src in (data.get("avatar"), ext.get("avatar"), card.get("avatar")):
        if isinstance(src, str) and src:
            out.append(src)
    return out


def _download_avatar(card: dict) -> tuple[bytes, str] | None:
    """Best-effort avatar bytes from a card: embedded data-URI first, else a URL fetch.

    Scans every avatar location (assets/avatar, and their extensions-relocated forms);
    never raises into the import path — a miss just means no avatar.
    """
    for uri in _avatar_candidates(card):
        embedded = fetch.decode_data_uri(uri)
        if embedded:
            return embedded
        if uri.startswith(("http://", "https://")):
            got = fetch.download_url(uri)
            if got:
                return got
    return None


def import_card(root: Path, data: bytes, fmt: str, into_cid: str | None = None,
                name: str | None = None) -> tuple[str, str]:
    from . import cards
    card = cards.loads(data, fmt)  # raises cards.CardParseError on bad input
    cname = name or card["data"].get("name", "Imported")
    if into_cid is None:
        cid, vid = create_character(root, cname, "default", card)
    else:
        cid = into_cid
        vid = create_version(root, into_cid, card.get("data", {}).get("character_version") or cname, card)
    if fmt == "png":
        assets.put_image(root, cid, vid, assets.AVATAR, data, "png")  # the PNG is the avatar
    else:
        dl = _download_avatar(card)
        if dl:
            assets.put_image(root, cid, vid, assets.AVATAR, dl[0], dl[1])
    return cid, vid


def export_card(root: Path, cid: str, vid: str, fmt: str) -> bytes:
    from . import cards
    return cards.dumps(read_card(root, cid, vid), fmt)
