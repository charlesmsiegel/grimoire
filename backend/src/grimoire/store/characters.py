"""Character containers: one folder per character, one JSON V3 card per version.

Unlike generic entities (one markdown file each), a character is a directory:
  <root>/characters/<cid>/character.md   # frontmatter: name, default_version
  <root>/characters/<cid>/<vid>.json     # a SillyTavern V3 card
  <root>/characters/<cid>/assets/        # optional images (from PNG/CHARX import)
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import shutil
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import assets
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


def _version_ids(root: Path, cid: str) -> list[str]:
    return sorted(p.stem for p in _char_dir(root, cid).glob("*.json"))


def read_card(root: Path, cid: str, vid: str) -> dict:
    _require_char(root, cid)
    p = _card_path(root, cid, vid)
    if not _safe(vid) or not p.exists():
        raise VersionNotFound(vid)
    return json.loads(p.read_text(encoding="utf-8"))


def read_character(root: Path, cid: str) -> dict:
    _require_char(root, cid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    versions = []
    for vid in _version_ids(root, cid):
        card = read_card(root, cid, vid)
        versions.append({
            "id": vid,
            "name": card["data"].get("name", vid),
            "card": card,
            "images": [i["name"] for i in assets.list_images(root, cid, vid)],
        })
    return {
        "meta": {"id": cid, "name": meta.get("name", cid), "default_version": meta.get("default_version", "")},
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
                "versions": [{"id": v, "name": read_card(root, cid, v)["data"].get("name", v)}
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


_AVATAR_MAX_BYTES = 8 * 1024 * 1024
_CT_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
           "image/gif": "gif", "image/webp": "webp"}


def _avatar_url(card: dict) -> str | None:
    data = card.get("data", {})
    for a in data.get("assets") or []:
        if isinstance(a, dict) and a.get("type") in ("icon", "avatar"):
            uri = a.get("uri", "")
            if isinstance(uri, str) and uri.startswith(("http://", "https://")):
                return uri
    av = data.get("avatar")
    return av if isinstance(av, str) and av.startswith(("http://", "https://")) else None


_MAX_REDIRECTS = 5


def _host_is_blocked(host: str) -> bool:
    """True if the host resolves to (or is) a private/loopback/link-local/reserved address.

    A proportionate SSRF guard for a local single-user app: it blocks the obvious
    internal targets. A determined DNS-rebinding attacker could still slip past
    (httpx re-resolves on connect); pinning the socket to the validated IP would
    close that, at more complexity than this best-effort fetch warrants.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # unresolvable -> block (best-effort: just means no avatar)
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return True
    return False


def _http_get_bytes(url: str) -> tuple[bytes, str | None]:
    """Fetch an image, validating each redirect hop and aborting early past the cap."""
    with httpx.Client(timeout=10.0, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("bad avatar url")
            if _host_is_blocked(parsed.hostname):
                raise ValueError("blocked avatar host")
            with client.stream("GET", url) as r:
                if r.is_redirect:
                    loc = r.headers.get("location")
                    if not loc:
                        raise ValueError("redirect without location")
                    url = str(r.url.join(loc))
                    continue
                r.raise_for_status()
                cl = r.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > _AVATAR_MAX_BYTES:
                    raise ValueError("avatar too large")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > _AVATAR_MAX_BYTES:
                        raise ValueError("avatar too large")
                return bytes(buf), r.headers.get("content-type")
        raise ValueError("too many redirects")


def _download_avatar(card: dict) -> tuple[bytes, str] | None:
    url = _avatar_url(card)
    if not url:
        return None
    try:
        content, ctype = _http_get_bytes(url)
    except Exception:  # noqa: BLE001 — best-effort; import never fails on download
        return None
    if not content or len(content) > _AVATAR_MAX_BYTES:
        return None
    ct = (ctype or "").split(";")[0].strip().lower()
    if ct and not ct.startswith("image/"):
        return None
    ext = _CT_EXT.get(ct) or url.rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
        ext = "png"
    return content, ext


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
