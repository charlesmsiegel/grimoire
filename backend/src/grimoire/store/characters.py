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

from . import assets, atomic, cards, chub, fetch, lorebook, statcache, taglines
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


def create_character(root: Path, name: str, version_name: str = "default", card: dict | None = None,
                     taken=None) -> tuple[str, str]:
    _chars_dir(root).mkdir(parents=True, exist_ok=True)

    def exists(c: str) -> bool:
        # `taken` widens the id namespace (overlay: world dirs + tombstones)
        return _char_dir(root, c).exists() or (taken is not None and taken(c))

    cid = uniquify(slugify(name), exists)
    _char_dir(root, cid).mkdir(parents=True)
    vid = slugify(version_name)
    card = card or blank_card(name)
    cards.bake_char_name(card)  # #137: {{char}} is always self-reference, baked at write time
    atomic.write_text(_card_path(root, cid, vid), _dumps(card))
    atomic.write_text(_meta_path(root, cid), dump_frontmatter({"name": name, "default_version": vid}, ""))
    return cid, vid


def create_version(root: Path, cid: str, version_name: str, card: dict) -> str:
    _require_char(root, cid)
    vid = uniquify(slugify(version_name), lambda v: _card_path(root, cid, v).exists())
    cards.bake_char_name(card)  # #137: {{char}} is always self-reference, baked at write time
    atomic.write_text(_card_path(root, cid, vid), _dumps(card))
    return vid


def update_version(root: Path, cid: str, vid: str, card: dict) -> None:
    _require_char(root, cid)
    p = _card_path(root, cid, vid)
    if not _safe(vid) or not p.exists():
        raise VersionNotFound(vid)
    cards.bake_char_name(card)  # #137: {{char}} is always self-reference, baked at write time
    atomic.write_text(p, _dumps(card))


def set_default_version(root: Path, cid: str, vid: str) -> None:
    _require_char(root, cid)
    if not _safe(vid) or not _card_path(root, cid, vid).exists():
        raise VersionNotFound(vid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    meta["default_version"] = vid
    atomic.write_text(_meta_path(root, cid), dump_frontmatter(meta, ""))


def set_birthdate(root: Path, cid: str, birthdate: str) -> None:
    _require_char(root, cid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    meta["birthdate"] = birthdate
    atomic.write_text(_meta_path(root, cid), dump_frontmatter(meta, ""))


def set_chub_source(root: Path, cid: str, vid: str, full_path: str) -> None:
    """Link one version to a chub.ai card. Stored in that version's own card
    (extensions, same spot as grimoire_label) so each variant of a character
    carries its own link rather than sharing one character-wide value."""
    card = read_card(root, cid, vid)
    card.setdefault("data", {}).setdefault("extensions", {})["chub_source"] = full_path
    update_version(root, cid, vid, card)


def clear_chub_source(root: Path, cid: str, vid: str) -> None:
    card = read_card(root, cid, vid)
    ext = (card.get("data") or {}).get("extensions") or {}
    if "chub_source" in ext:
        del ext["chub_source"]
        update_version(root, cid, vid, card)


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


def _version_chub_source(card: dict) -> str:
    return (card.get("data", {}).get("extensions") or {}).get("chub_source", "")


def read_character(root: Path, cid: str) -> dict:
    _require_char(root, cid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    default_version = meta.get("default_version", "")
    # One-time fallback for data written before chub_source became
    # per-version: a value still sitting in character.md frontmatter only
    # ever applied to the default version, so that's the only place it
    # surfaces now -- a sibling version with no per-version value of its own
    # shows no link, prompting an explicit (and now easy) re-link.
    legacy_chub_source = meta.get("chub_source", "")
    versions = []
    for vid in _version_ids(root, cid):
        card = read_card(root, cid, vid)
        chub_source = _version_chub_source(card)
        if not chub_source and vid == default_version:
            chub_source = legacy_chub_source
        versions.append({
            "id": vid,
            "name": _version_label(card, vid),
            "card": card,
            "images": [i["name"] for i in assets.list_images(root, cid, vid)],
            "avatar_focus": assets.read_focus(root, cid, vid),
            "chub_source": chub_source,
            "is_chub": bool(chub_source) and chub.parse_full_path(chub_source) is not None,
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


def _card_summary(root: Path, cid: str, vid: str) -> dict:
    """The small derived view of a card that list endpoints need (label,
    greeting count, chub link), memoized by stat so unchanged cards are
    never re-read or re-parsed."""
    if not _safe(vid):
        raise VersionNotFound(vid)
    p = _card_path(root, cid, vid)
    sig = statcache.signature(p)
    if sig is None:
        raise VersionNotFound(vid)

    def compute() -> dict:
        card = json.loads(p.read_text(encoding="utf-8"))
        return {"label": _version_label(card, vid),
                "greeting_count": _greeting_count(card.get("data", {})),
                "chub_source": _version_chub_source(card)}

    return statcache.memo("card_summary", sig, compute)


def list_characters(root: Path) -> list[dict]:
    out: list[dict] = []
    d = _chars_dir(root)
    if d.exists():
        for cd in sorted(p for p in d.iterdir() if p.is_dir() and (p / "character.md").exists()):
            cid = cd.name
            meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
            default = meta.get("default_version", "")
            names = [i["name"] for i in assets.list_images(root, cid, default)]
            try:
                greeting_count = _card_summary(root, cid, default)["greeting_count"]
            except VersionNotFound:
                greeting_count = 0
            out.append({
                "id": cid,
                "name": meta.get("name", cid),
                "default_version": default,
                "has_avatar": assets.AVATAR in names,
                "avatar_focus": assets.read_focus(root, cid, default),
                "gallery_count": sum(1 for n in names if n.startswith("gallery_")),
                "localized_count": sum(1 for n in names if n.startswith("embed-")),
                "greeting_count": greeting_count,
                "tagline": taglines.read(root, cid),
                "versions": [{"id": v, "name": _card_summary(root, cid, v)["label"]}
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
        atomic.write_text(_meta_path(root, cid), dump_frontmatter(meta, ""))


def delete_character(root: Path, cid: str) -> None:
    _require_char(root, cid)
    shutil.rmtree(_char_dir(root, cid))


def card_hash(root: Path, cid: str, vid: str) -> str | None:
    if not _safe(cid) or not _safe(vid):
        return None
    p = _card_path(root, cid, vid)
    sig = statcache.signature(p)
    if sig is None:
        return None
    return statcache.memo(
        "card_hash", sig,
        lambda: hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest())


def dir_content_hash(files: list[tuple[str, str]]) -> str:
    """`dir_hash` over (name, text) pairs you are holding rather than files on
    disk — see `snapshot`."""
    h = hashlib.sha256()
    for name, text in files:
        h.update(name.encode("utf-8"))
        h.update(text.encode("utf-8"))
    return h.hexdigest()


def snapshot(root: Path, cid: str) -> tuple[str, list[tuple[str, str]]] | None:
    """One read of the whole actor: its `dir_hash` and the (name, text) pairs
    that hash covers, meta first. A copier writes exactly these and records
    exactly that hash, so the sync base cannot describe content the copy never
    got even if the source moves mid-copy (#247). None when not an actor."""
    if not _safe(cid) or not _meta_path(root, cid).exists():
        return None
    files = [_meta_path(root, cid)] + [_card_path(root, cid, v) for v in _version_ids(root, cid)]
    pairs = [(p.name, p.read_text(encoding="utf-8")) for p in files]
    return dir_content_hash(pairs), pairs


def _dir_hash_compute(files: list[Path]) -> str:
    return dir_content_hash([(p.name, p.read_text(encoding="utf-8")) for p in files])


def dir_hash(root: Path, cid: str) -> str | None:
    """Whole-actor content hash: character.md plus every version card, name-tagged.
    Assets are excluded so an image-only change never surfaces in sync."""
    if not _safe(cid) or not _meta_path(root, cid).exists():
        return None
    files = [_meta_path(root, cid)] + [_card_path(root, cid, v) for v in _version_ids(root, cid)]
    # the signature spans the whole file set, so adding/removing a version invalidates too
    return statcache.memo("dir_hash", statcache.signature(*files),
                          lambda: _dir_hash_compute(files))


def character_count(root: Path) -> int:
    d = _chars_dir(root)
    return sum(1 for p in d.iterdir() if p.is_dir() and (p / "character.md").exists()) if d.exists() else 0


def character_refs(root: Path) -> list[str]:
    d = _chars_dir(root)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "character.md").exists())


def find_unlinked_versions(root: Path) -> list[dict]:
    """Every (character, version) pair with no chub.ai link, across every
    character in root -- for surfacing versions worth linking."""
    out: list[dict] = []
    for cid in character_refs(root):
        meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
        default = meta.get("default_version", "")
        legacy_chub_source = meta.get("chub_source", "")  # same fallback as read_character
        for vid in _version_ids(root, cid):
            summary = _card_summary(root, cid, vid)
            chub_source = summary["chub_source"] or (legacy_chub_source if vid == default else "")
            if not chub_source:
                out.append({
                    "character": cid, "character_name": meta.get("name", cid),
                    "version": vid, "version_name": summary["label"],
                })
    return out


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
                name: str | None = None, update_vid: str | None = None) -> tuple[str, str]:
    card = cards.loads(data, fmt)  # raises cards.CardParseError on bad input
    cards.bake_char_name(card)
    if update_vid is not None:
        cid, vid = into_cid, update_vid
        update_version(root, cid, vid, card)
    else:
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


def _sniff_card_format(data: bytes) -> str | None:
    """Best-effort card-format detection for an arbitrary downloaded file: a
    PNG (embedded card metadata) or a JSON object. None if neither -- in
    particular, valid-but-non-object JSON (an array, string, number...) is
    rejected here rather than reaching cards.loads, which assumes a dict."""
    if fetch.sniff_ext(data) == "png":
        return "png"
    try:
        parsed = json.loads(data)
    except ValueError:
        return None
    return "json" if isinstance(parsed, dict) else None


# chub definition key -> card data key. chub's field names predate the tavern
# spec: `personality` holds the card description, `tavern_personality` the
# personality, and `description` the page notes (card creator_notes).
_CHUB_DEF_FIELDS = (
    ("name", "name"),
    ("personality", "description"),
    ("tavern_personality", "personality"),
    ("description", "creator_notes"),
    ("scenario", "scenario"),
    ("first_message", "first_mes"),
    ("example_dialogs", "mes_example"),
    ("system_prompt", "system_prompt"),
    ("post_history_instructions", "post_history_instructions"),
)


def merge_chub_definition(card: dict, definition: dict) -> bool:
    """Overlay chub's current API definition onto a card parsed from the
    max-res PNG. The PNG's embedded card can be a stale revision (chub doesn't
    always regenerate it after a creator edits), so the definition wins — but
    only field-by-field where it is non-empty, so a field chub omits or blanks
    never wipes what the PNG carried. Mutates `card`; True if anything changed."""
    data = card.setdefault("data", {})
    changed = False
    for def_key, card_key in _CHUB_DEF_FIELDS:
        v = definition.get(def_key)
        if isinstance(v, str) and v.strip() and v != data.get(card_key):
            data[card_key] = v
            changed = True
    greetings = definition.get("alternate_greetings")
    if isinstance(greetings, list):
        greetings = [g for g in greetings if isinstance(g, str) and g.strip()]
        if greetings and greetings != data.get("alternate_greetings"):
            data["alternate_greetings"] = greetings
            changed = True
    book = definition.get("embedded_lorebook")
    if isinstance(book, dict) and book.get("entries") and book != data.get("character_book"):
        data["character_book"] = book
        changed = True
    return changed


def import_from_chub(root: Path, url_or_path: str, into_cid: str | None = None,
                      into_vid: str | None = None) -> dict:
    """Download a character card from a URL and import/update it. A chub.ai
    URL or "creator/slug" shorthand gets the full chub.ai treatment (avatar,
    gallery, linked lorebooks); any other URL is fetched directly and parsed
    as a PNG or JSON card -- gallery/lorebooks stay empty there, since that
    metadata only exists on chub.ai."""
    stored_url = chub.normalize_link(url_or_path)
    if stored_url is None:
        raise chub.ChubParseError(url_or_path)
    chub_path = chub.parse_full_path(stored_url)

    node = None
    if chub_path is not None:
        node = chub.fetch_character_node(chub_path)
        if node is None:
            raise chub.ChubFetchError(chub_path)
        png = fetch.download_url(node.get("max_res_url") or "")
        if png is None:
            raise chub.ChubFetchError(chub_path)
        data, fmt = png[0], "png"
    else:
        raw = fetch.download_bytes(stored_url)
        if raw is None:
            raise chub.ChubFetchError(stored_url)
        fmt = _sniff_card_format(raw)
        if fmt is None:
            raise chub.ChubFetchError(stored_url)
        data = raw

    # Re-downloading into a version already linked to this same URL overwrites
    # that version in place rather than piling up near-duplicates. The match
    # is checked against that *specific* version's own link (each variant
    # carries its own), not any sibling version's. Without into_vid (which
    # version is "open") there's nothing safe to overwrite, so that case
    # always creates a version, same as a mismatch.
    updated = False
    if into_cid and into_vid:
        existing = read_character(root, into_cid)
        target = next((v for v in existing["versions"] if v["id"] == into_vid), None)
        if target is None:
            raise VersionNotFound(into_vid)
        # Compare against the *normalized* stored value, not the raw one --
        # legacy data still stores chub.ai's bare "creator/slug" shorthand
        # rather than a full URL, and would never match otherwise.
        existing_source = target["chub_source"]
        updated = bool(existing_source) and chub.normalize_link(existing_source) == stored_url

    try:
        if updated:
            cid, vid = import_card(root, data, fmt, into_cid, update_vid=into_vid)
        else:
            cid, vid = import_card(root, data, fmt, into_cid)
    except cards.CardParseError as exc:
        raise chub.ChubFetchError(str(exc)) from exc
    if not updated:
        set_chub_source(root, cid, vid, stored_url)

    definition = (node or {}).get("definition")
    if isinstance(definition, dict):
        card = read_card(root, cid, vid)
        merged = merge_chub_definition(card, definition)
        baked = cards.bake_char_name(card)  # definition text carries {{char}} again
        if merged or baked:
            update_version(root, cid, vid, card)

    gallery = _download_gallery(root, cid, vid, node) if node else {"attempted": 0, "stored": 0}
    lore = _download_lorebooks(root, node) if node else {"lorebooks_found": 0, "created": []}

    return {"character": cid, "version": vid, "updated": updated, "gallery": gallery, "lore": lore}


def download_chub_gallery_stream(root: Path, cid: str, vid: str, node: dict):
    """Generator: download every chub.ai gallery image for an already-resolved
    node, yielding {"total": N}, then {"done": k, "total": N} per image, then
    {"summary": {"attempted": N, "stored": M}}. Clears any previously
    downloaded gallery images first -- otherwise a gallery that shrank between
    downloads would leave orphaned gallery_N files past the new (smaller)
    count."""
    for img in assets.list_images(root, cid, vid):
        if img["name"].startswith("gallery_"):
            assets.delete_image(root, cid, vid, img["name"])

    paths = chub.fetch_gallery_paths(node.get("id")) if node.get("hasGallery") else []
    total = len(paths)
    yield {"total": total}

    stored = 0
    for i, path in enumerate(paths):
        got = fetch.download_url(path)
        if got:
            assets.put_image(root, cid, vid, f"gallery_{i}", got[0], got[1])
            stored += 1
        yield {"done": i + 1, "total": total}

    yield {"summary": {"attempted": total, "stored": stored}}


def _download_gallery(root: Path, cid: str, vid: str, node: dict) -> dict:
    summary = {"attempted": 0, "stored": 0}
    for ev in download_chub_gallery_stream(root, cid, vid, node):
        if "summary" in ev:
            summary = ev["summary"]
    return summary


def _download_lorebooks(root: Path, node: dict) -> dict:
    lorebook_ids = [i for i in dict.fromkeys(node.get("related_lorebooks") or [])
                    if isinstance(i, int) and i > 0]
    created: list[dict] = []
    for lid in lorebook_ids:
        lb_node = chub.fetch_lorebook_node(lid)
        if not lb_node:
            continue
        book = (lb_node.get("definition") or {}).get("embedded_lorebook")
        if not book:
            continue
        created.extend(lorebook.commit(root, lorebook.from_character_book(book)))
    return {"lorebooks_found": len(lorebook_ids), "created": created}


def resolve_chub_node(root: Path, cid: str, vid: str) -> dict:
    """Resolve the chub.ai node for a version that's already linked, or raise
    ChubFetchError if it has no link or chub.ai can't be reached. Goes through
    read_character so the legacy character.md-level fallback (applied there
    for the default version) is honored the same way it is for display --
    not just shown as linked in the UI but actually usable here too. Exposed
    publicly so a route can resolve the link before streaming (clean 404 vs.
    a mid-stream error event)."""
    detail = read_character(root, cid)
    target = next((v for v in detail["versions"] if v["id"] == vid), None)
    if target is None:
        raise VersionNotFound(vid)
    if not target["is_chub"]:
        raise chub.ChubFetchError("version is not linked to a chub.ai card")
    full_path = chub.parse_full_path(target["chub_source"])
    node = chub.fetch_character_node(full_path)
    if node is None:
        raise chub.ChubFetchError(full_path)
    return node


def download_chub_gallery(root: Path, cid: str, vid: str) -> dict:
    return _download_gallery(root, cid, vid, resolve_chub_node(root, cid, vid))


def download_chub_lorebooks(root: Path, cid: str, vid: str) -> dict:
    return _download_lorebooks(root, resolve_chub_node(root, cid, vid))


def export_card(root: Path, cid: str, vid: str, fmt: str) -> bytes:
    return cards.dumps(read_card(root, cid, vid), fmt)
