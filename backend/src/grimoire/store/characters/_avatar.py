"""characters: avatar responsibilities."""

from __future__ import annotations

import hashlib
from io import BytesIO

from PIL import Image

from .. import cards, fetch, image_hash
from . import _bundled


def _avatar_candidates(card: dict) -> list[str]:
    """Every place a card might carry an avatar: V3 `assets`, a top-level `avatar`
    string, and either relocated into `extensions` by the V2->V3 upconvert."""
    data = cards.card_data(card)
    ext = data.get("extensions") or {}
    out: list[str] = []
    for assets_src in (data.get("assets"), ext.get("assets")):
        for a in assets_src or []:
            if isinstance(a, dict) and a.get("type") in _bundled._AVATAR_TYPES:
                uri = a.get("uri")
                if isinstance(uri, str) and uri:
                    out.append(uri)
    for src in (data.get("avatar"), ext.get("avatar"), card.get("avatar")):
        if isinstance(src, str) and src:
            out.append(src)
    return out


def _carried_uri(uri: str) -> bool:
    """Does this URI *contain* the image, rather than point at one elsewhere?"""
    return uri.startswith("data:") or cards.embedded_path(uri) is not None


def _shown(blob: bytes) -> tuple[tuple[int, int], bytes] | None:
    """`(size, digest)` of what `blob` shows first: frame 0, turned upright the
    way a browser turns it (`image_hash.orientation`), as RGBA8 -- the colour
    profile ignored, alpha kept. None when it does not decode, or is larger
    than pixel identity would decode."""
    try:
        with Image.open(BytesIO(blob)) as im:
            if im.size[0] * im.size[1] > image_hash.STATIC_BUDGET:
                return None
            im.seek(0)
            if im.size[0] * im.size[1] > image_hash.STATIC_BUDGET:
                return None             # a GIF's frame 0 can grow the canvas
            turn = image_hash.orientation(im)
            rgba = im.convert("RGBA")
            if turn is not None:
                rgba = rgba.transpose(turn)
            return rgba.size, hashlib.sha256(rgba.tobytes()).digest()
    except Exception:  # noqa: BLE001 -- an undecodable plane is not the same picture
        return None


def _plane_is_the_carried_avatar(png: bytes, carried: bytes) -> bool:
    """Is the PNG's image plane the card's carried avatar, so that the carried
    copy (the original bytes, lossless) should be the one stored? True for our
    placeholder plane, and for a plane that IS the same picture -- what a PNG
    export of a non-PNG avatar writes (`_png_plane_of`).

    Neither side is decoded past `image_hash.STATIC_BUDGET`: an over-budget
    carried avatar is exported under the placeholder (so the check above
    already took it), and one beside a real plane is no export of ours, so
    the plane stays the picture.

    "The same picture" is what that export would have drawn from the carried
    copy, compared as decoded pixels -- not pixel identity, which also counts
    the colour profile, the orientation and the animation, and a plane carries
    none of those: comparing ids sent an EXIF-turned JPEG, an ICC JPEG and an
    animated GIF back in as their flat re-encodes."""
    if cards.is_placeholder_png(png):
        return True
    plane = _png_plane_of((carried, fetch.sniff_ext(carried) or ""))
    mine = _shown(png)
    return mine is not None and mine == _shown(plane if plane is not None else carried)


def _resolve_avatar(card: dict, data: bytes, fmt: str, *,
                    network: bool, bundled: _bundled._BundledImages | None = None) -> tuple[bytes, str, str] | None:
    """Best-effort avatar bytes from a card: (bytes, ext, the URI they came from).

    Scans every avatar location (assets/avatar, and their extensions-relocated
    forms) in the order `_avatar_candidates` yields them and takes the first
    that resolves — an embedded data-URI, a file bundled in this CHARX under
    `embeded://` (#25), or an http(s) URL — leaving that long-standing
    precedence exactly as it was. `network=False` covers PNG import, which has
    never fetched anything (the file is normally its own avatar). Never raises
    into the import path; a miss just means no avatar.

    Bundled and embedded bytes are sniffed rather than trusted: the extension
    written in the URI decides nothing, since `assets.put_image` will store the
    file under whatever type we name here.

    Candidates are deduplicated, and bundled lookups are bounded twice over,
    because an uploaded CHARX is untrusted (Codex review): by bytes, so a card
    naming over-compressed members as its avatar cannot inflate each of them in
    full, and by count, because every lookup re-parses the archive — a member
    that reads as empty or refuses to open costs nothing in bytes and would
    otherwise buy an unlimited number of those parses.
    """
    if bundled is None:
        bundled = _bundled._BundledImages(data)
    lookups = _bundled._MAX_BUNDLED_READS
    for uri in dict.fromkeys(_avatar_candidates(card)):
        embedded = fetch.decode_data_uri(uri)
        if embedded:
            return embedded[0], embedded[1], uri
        path = cards.embedded_path(uri)
        if path is not None:
            if fmt != "charx" or lookups <= 0:
                continue  # nothing to open it against, or a budget is spent
            lookups -= 1
            image = bundled.read(path)
            if image is not None:
                return image[0], image[1], uri
        elif network and uri.startswith(("http://", "https://")):
            got = fetch.download_url(uri)
            if got:
                return got[0], got[1], uri
    return None


def _drop_avatar_uri(card: dict, uri: str) -> None:
    """Remove the avatar reference whose bytes we are about to store on disk.

    Avatars live under assets/ and are deliberately outside `card_hash`, so a
    consumed data-URI (or `embeded://` path, which means nothing outside its
    container) must not reach the stored card: it would bloat every imported
    card with base64 and give a re-imported character a different hash from the
    one it was exported from. Remote URLs are left alone — they are cheap
    provenance, not a copy of the image.

    Only avatar-ish entries go: an asset of another kind that happens to share
    the URI (one image serving as both icon and background) is somebody else's
    reference, and dropping it would lose an asset nothing replaced.

    And only the FIRST match — the one export prepends — walking the locations
    in the order `_avatar_candidates` yields them, so the reference removed is
    the reference resolved. (Searching in any other order removes a different
    copy and leaves the consumed one on the card, base64 and all — Codex
    review.) A card that already listed that exact URI therefore keeps its own
    entry, and comes back from a round trip as the character it started as.
    """
    data = cards.card_data(card)
    extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    for holder in (data, extensions):
        entries = holder.get("assets")
        if not isinstance(entries, list):
            continue
        for i, a in enumerate(entries):
            if isinstance(a, dict) and a.get("uri") == uri and a.get("type") in _bundled._AVATAR_TYPES:
                kept = entries[:i] + entries[i + 1:]
                # An emptied list goes with it: a card that arrived carrying
                # nothing but its avatar must come back out of a round trip
                # byte-identical, hash included. (An `assets: []` a card
                # authored itself is normalized away the same way — the two
                # spellings mean the same thing to every reader of the card.)
                if kept:
                    holder["assets"] = kept
                else:
                    holder.pop("assets")
                return
    for holder in (data, extensions, card):
        if holder.get("avatar") == uri:
            holder.pop("avatar")
            return


def _png_plane_of(avatar: tuple[bytes, str] | None) -> bytes | None:
    """The avatar re-encoded as a PNG, for a PNG export's image plane -- None
    for a missing avatar, an avatar that is already a PNG (it is used as is) or
    one Pillow cannot decode (the exporter then falls back to its placeholder,
    and the original still rides in the card).

    The plane is the first frame turned upright by the browser's rule
    (`image_hash.orientation`): a PNG written without the EXIF that turned the
    original would otherwise show it on its side."""
    if avatar is None or avatar[1] == "png":
        return None
    try:
        with Image.open(BytesIO(avatar[0])) as im:
            # Held to the budget pixel identity holds a decode to, read from
            # the header before anything is decoded (and again after the seek,
            # which can grow a GIF's canvas): an avatar stored as over-budget
            # is drawn as the placeholder, and still rides in the card whole.
            if im.size[0] * im.size[1] > image_hash.STATIC_BUDGET:
                return None
            im.seek(0)  # an animation's first frame
            if im.size[0] * im.size[1] > image_hash.STATIC_BUDGET:
                return None
            turn = image_hash.orientation(im)
            has_alpha = "A" in im.getbands() or "transparency" in im.info
            frame = im.convert("RGBA" if has_alpha else "RGB")
            if turn is not None:
                frame = frame.transpose(turn)
            out = BytesIO()
            frame.save(out, "PNG")
    except Exception:  # noqa: BLE001 -- an export must not fail over an image it cannot decode
        return None
    return out.getvalue()
