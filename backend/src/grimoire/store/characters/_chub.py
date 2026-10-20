"""characters: chub responsibilities."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .. import assets, cards, chub, fetch, image_store, lorebook
from . import _importing, _paths, _reads, _records


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


def download_card(url_or_path: str) -> tuple[bytes, str, str, dict | None]:
    """`(bytes, format, normalized url, chub node)` for the card at a URL.

    Downloads only -- nothing here touches the store, which is what lets the
    scenario importer (#217) show a card for review before anything is written.
    A chub.ai URL (or its "creator/slug" shorthand) resolves through chub's API
    so the node comes back with it; any other URL is fetched directly and
    sniffed as a PNG or JSON card, and its node is None -- gallery/lorebook
    metadata only exists on chub.ai.

    Raises `chub.ChubParseError` for something that is not a URL at all, and
    `chub.ChubFetchError` when the URL yields no card this can read.
    """
    stored_url = chub.normalize_link(url_or_path)
    if stored_url is None:
        raise chub.ChubParseError(url_or_path)
    chub_path = chub.parse_full_path(stored_url)

    if chub_path is not None:
        node = chub.fetch_character_node(chub_path)
        if node is None:
            raise chub.ChubFetchError(chub_path)
        png = fetch.download_url(node.get("max_res_url") or "")
        if png is None:
            raise chub.ChubFetchError(chub_path)
        return png[0], "png", stored_url, node
    raw = fetch.download_bytes(stored_url)
    if raw is None:
        raise chub.ChubFetchError(stored_url)
    fmt = _sniff_card_format(raw)
    if fmt is None:
        raise chub.ChubFetchError(stored_url)
    return raw, fmt, stored_url, None


def import_from_chub(root: Path, url_or_path: str, into_cid: str | None = None,
                      into_vid: str | None = None,
                      version_name: str | None = None,
                      validate_name: Callable[[str], None] | None = None) -> dict:
    """Download a character card from a URL and import/update it. A chub.ai
    URL or "creator/slug" shorthand gets the full chub.ai treatment (avatar,
    gallery, linked lorebooks); any other URL is fetched directly and parsed
    as a PNG or JSON card -- gallery/lorebooks stay empty there, since that
    metadata only exists on chub.ai. `version_name` names the version the
    card lands in; it applies only when one is created, since an overwrite
    keeps the name the existing version already carries."""
    data, fmt, stored_url, node = download_card(url_or_path)

    # Re-downloading into a version already linked to this same URL overwrites
    # that version in place rather than piling up near-duplicates. The match
    # is checked against that *specific* version's own link (each variant
    # carries its own), not any sibling version's. Without into_vid (which
    # version is "open") there's nothing safe to overwrite, so that case
    # always creates a version, same as a mismatch.
    updated = False
    if into_cid and into_vid:
        existing = _reads.read_character(root, into_cid)
        target = next((v for v in existing["versions"] if v["id"] == into_vid), None)
        if target is None:
            raise _paths.VersionNotFound(into_vid)
        # Compare against the *normalized* stored value, not the raw one --
        # legacy data still stores chub.ai's bare "creator/slug" shorthand
        # rather than a full URL, and would never match otherwise.
        existing_source = target["chub_source"]
        updated = bool(existing_source) and chub.normalize_link(existing_source) == stored_url

    try:
        if updated:
            cid, vid = _importing.import_card(root, data, fmt, into_cid, update_vid=into_vid,
                                   validate_name=validate_name)
        else:
            cid, vid = _importing.import_card(root, data, fmt, into_cid, version_name=version_name,
                                   validate_name=validate_name)
    except cards.CardParseError as exc:
        raise chub.ChubFetchError(str(exc)) from exc
    if not updated:
        _records.set_chub_source(root, cid, vid, stored_url)

    definition = (node or {}).get("definition")
    if isinstance(definition, dict):
        card = _reads.read_card(root, cid, vid)
        merged = merge_chub_definition(card, definition)
        baked = cards.bake_char_name(card)  # definition text carries {{char}} again
        if merged or baked:
            _records.update_version(root, cid, vid, card)

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
    paths = chub.fetch_gallery_paths(node.get("id")) if node.get("hasGallery") else []
    total = len(paths)
    yield {"total": total}

    # Download and ingest EVERYTHING before touching the existing gallery: a
    # download that raises half way must not leave the character with no gallery
    # at all. Ingest only adds objects to the store; no slot has changed yet.
    fresh: list[tuple[int, str]] = []
    for i, path in enumerate(paths):
        got = fetch.download_url(path)
        if got:
            fresh.append((i, image_store.ingest(got[0], got[1], source_url=path).id))
        yield {"done": i + 1, "total": total}

    _replace_gallery(root, cid, vid, fresh)
    yield {"summary": {"attempted": total, "stored": len(fresh)}}


def _replace_gallery(root: Path, cid: str, vid: str, fresh: list[tuple[int, str]]) -> None:
    """Make `gallery_<i>` -> id the version's whole gallery.

    Slots not being replaced go (a gallery that shrank would otherwise leave
    orphaned slots past the new count); a slot that now holds a different image
    loses its caption, which described the old one -- a slot that holds the same
    image keeps it, whether as a placement or as a legacy file. All of it under
    the slots' locks, so an upload cannot land between the delete and the
    re-link.

    The slots are listed INSIDE that hold, so the replacement acts on the
    gallery as it stands once it holds the locks -- a listing taken first
    left out an upload landing before the locks, which then survived on a
    stale view. A per-name lock can only be taken for a name already known,
    so a hold whose listing finds a slot it does not cover is released and
    retaken with that slot too (sorted as a whole, `_image_locks_held`); each
    retry covers strictly more names. A slot created after the listing, while
    the hold stands, belongs to an upload that lands after this replacement."""
    d = assets.version_dir(root, cid, vid)
    wanted = {f"gallery_{i}": image_id for i, image_id in fresh}
    # Before the hold: `list_images`, which also runs the read-side repairs.
    covered = set(_gallery_names(assets.list_images(root, cid, vid))) | set(wanted)
    while True:
        # The sidecar lock with them, up front (names, then sidecar): every
        # caption drop below is then a reentrant acquisition that cannot time
        # out, so a `StoreBusy` stops this before its first write or not at
        # all -- never between a slot's new image and the drop of the old
        # image's caption, which a re-run would find nothing left to redo.
        with (assets._image_locks_held(d, *sorted(covered)),
              assets.sidecar_lock(d, assets.DESCRIPTIONS_FILE)):
            # Under the hold: `list_in`, which repairs nothing. `list_images`
            # heals a stranded promotion, and that takes {avatar, a gallery
            # slot, promote-tmp} BLOCKING -- while these gallery locks are held,
            # against a promotion holding `avatar` and waiting for one of them,
            # that is an ABBA wedge. (A heal reached under a held stripe no
            # longer waits -- `assets._heal_locks` -- but it would only ever
            # skip here, so the listing that repairs stays outside.)
            existing = _gallery_names(assets.list_in(d))
            if set(existing) <= covered:
                _replace_held_gallery(root, cid, vid, d, existing, wanted)
                return
        covered |= set(existing)


def _gallery_names(rows: list[dict]) -> list[str]:
    return [img["name"] for img in rows if img["name"].startswith("gallery_")]


def _replace_held_gallery(root: Path, cid: str, vid: str, d: Path,
                          existing: list[str], wanted: dict[str, str]) -> None:
    """`_replace_gallery`'s writes; the caller holds every slot's lock."""
    for name in existing:
        if name not in wanted:
            assets.delete_image(root, cid, vid, name)
    for name, image_id in wanted.items():
        placed = assets.image_id(root, cid, vid, name)
        if placed != image_id:
            held = placed if placed is not None else _legacy_identity(d, name)
            assets.link_in(d, name, image_id)
            if held != image_id:
                assets.drop_sidecar_entry(d, assets.DESCRIPTIONS_FILE, name)


def _legacy_identity(d: Path, name: str) -> str | None:
    """The id a slot's legacy file (one written before the image store) would
    be ingested under, without ingesting it; None when there is none to read."""
    p = assets.path_in(d, name)
    if p is None:
        return None
    try:
        return image_store.identify(p.read_bytes(), p.suffix)
    except (OSError, ValueError):
        return None


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
    detail = _reads.read_character(root, cid)
    target = next((v for v in detail["versions"] if v["id"] == vid), None)
    if target is None:
        raise _paths.VersionNotFound(vid)
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
