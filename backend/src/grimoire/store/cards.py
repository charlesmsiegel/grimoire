"""Import/export SillyTavern cards: V3/V2 JSON, PNG tEXt, and CHARX zip.

Pure stdlib (struct/zlib/base64/zipfile) — no Pillow. PNG export writes the card
into a `ccv3` tEXt chunk; avatars live on disk under assets/ and are embedded on
the way out (#25), each format carrying the bytes the way its container allows:

- json  — a `data:` URI in `data.assets`, so one file is the whole character.
- png   — the avatar's own pixels become the image plane. Without Pillow that
          only works for a PNG; any other type falls back to the 1x1
          placeholder plus a `data:` URI, which the import prefers.
- charx — the file is bundled at `assets/avatar.<ext>` and referenced with the
          V3 spec's `embeded://` scheme (spelled as the spec spells it).

Embedding happens at export time only: the stored card never carries the avatar,
so `characters.card_hash` — and therefore sync — stays blind to image edits.
"""

from __future__ import annotations

import base64
import json
import re
import struct
import zipfile
import zlib
from io import BytesIO

_V2_KNOWN = {
    "name", "description", "personality", "scenario", "first_mes", "mes_example",
    "creator_notes", "system_prompt", "post_history_instructions",
    "alternate_greetings", "character_book", "tags", "creator",
    "character_version", "extensions",
}

# The V3 spec's scheme for an asset bundled inside the card's own container --
# spelled with the spec's typo, because interoperability beats orthography.
EMBEDDED_SCHEME = "embeded://"
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# tEXt keywords that carry a card payload rather than a caption: an avatar that
# is itself an exported card PNG arrives with one already in it.
_CARD_KEYWORDS = (b"ccv3", b"chara")
# Local, rather than fetch's copy: this module is deliberately stdlib-only.
_EXT_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "gif": "image/gif", "webp": "image/webp"}
_MAX_ASSET_BYTES = 100 * 1024 * 1024  # matches fetch.MAX_BYTES


class CardParseError(Exception):
    pass


def _canonical(card: dict) -> str:
    """The one card serialization, shared by on-disk writes and embedded payloads."""
    return json.dumps(card, sort_keys=True, ensure_ascii=False)


def to_v3(obj: dict) -> dict:
    """Normalize a V2/bare object into a V3 card; preserve unknown data fields.

    Pure: never mutates the caller's dict.
    """
    if obj.get("spec") == "chara_card_v3":
        out = dict(obj)
        out.setdefault("spec_version", "3.0")
        data = dict(out.get("data") or {})
        data.setdefault("extensions", {})
        out["data"] = data
        return out
    data = dict(obj.get("data") or obj)  # V2 has .data; some exports are bare data
    known = {k: data[k] for k in _V2_KNOWN if k in data}
    extensions = dict(known.get("extensions") or {})
    for k, v in data.items():
        if k not in _V2_KNOWN:
            extensions[k] = v
    known["extensions"] = extensions
    known.setdefault("name", data.get("name", "Unnamed"))
    return {"spec": "chara_card_v3", "spec_version": "3.0", "data": known}


_BAKE_FIELDS = ("description", "personality", "scenario", "first_mes", "mes_example",
                "system_prompt", "post_history_instructions", "creator_notes")
_CHAR_MACRO = re.compile(r"\{\{char\}\}", re.IGNORECASE)


def bake_char_token(text: str, name: str) -> str:
    """Replace the {{char}} macro with `name` (self-reference baking). `name`
    empty -> no-op, since there's nothing to bake against (e.g. a greeting with
    no associated character). A `name` that itself contains {{char}} is also
    treated as unusable (no-op): substituting it in would reintroduce the very
    token being resolved, so a second (redundant but otherwise harmless) bake
    pass -- e.g. characters.update_version() re-baking after an overlay-level
    pre-bake -- would keep expanding it further on every call. Shared by card
    fields (bake_char_name) and standalone greetings (greetings.py), so every
    piece of authored content with a clear "self" resolves {{char}} the same
    way, at creation time."""
    if not name or _CHAR_MACRO.search(name):
        return text
    return _CHAR_MACRO.sub(lambda _m: name, text)


def bake_char_name(card: dict) -> bool:
    """Replace the {{char}} macro with the card's own name in every text field,
    greeting, and lorebook entry. Baked at creation/import: scene-time {{char}}
    is never resolved to the present NPC cast (ambiguous with multiple NPCs) --
    a card's own macro would expand wrongly in multi-character scenes, and the
    raw token must never surface in chats. {{user}} stays literal (unknown
    until a scene). Mutates `card`; True if anything changed."""
    data = card.get("data") or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return False
    changed = False

    def sub(text: str) -> str:
        nonlocal changed
        new = bake_char_token(text, name)
        changed = changed or new != text
        return new

    for key in _BAKE_FIELDS:
        if isinstance(data.get(key), str):
            data[key] = sub(data[key])
    greetings = data.get("alternate_greetings")
    if isinstance(greetings, list):
        data["alternate_greetings"] = [sub(g) if isinstance(g, str) else g for g in greetings]
    book = data.get("character_book")
    entries = book.get("entries") if isinstance(book, dict) else None
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict) and isinstance(e.get("content"), str):
                e["content"] = sub(e["content"])
    return changed


def _loads_json(data: bytes) -> dict:
    try:
        return to_v3(json.loads(data.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CardParseError(f"invalid card JSON: {exc}") from exc


def _iter_png_chunks(data: bytes):
    """(type, payload, raw bytes of the whole chunk) for each complete chunk.

    Stops at the first chunk the file is too short to hold, rather than yielding
    a truncated payload as if it were whole — `_png_image_plane` re-emits these
    verbatim, so a half-read chunk would become a corrupt export.
    """
    if data[:8] != _PNG_SIG:
        raise CardParseError("not a PNG")
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        end = pos + 12 + length  # 4 len + 4 type + payload + 4 crc
        if end > len(data):
            return
        yield data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length], data[pos:end]
        pos = end


def _iter_png_text(data: bytes):
    for ctype, payload, _raw in _iter_png_chunks(data):
        if ctype == b"tEXt":
            keyword, _, text = payload.partition(b"\x00")
            yield keyword.decode("latin-1"), text.decode("latin-1")


def _loads_png(data: bytes) -> dict:
    # First occurrence wins: some exporters append a second, stripped `chara`
    # chunk after the canonical one. SillyTavern reads the first; dict() would
    # keep the last, silently dropping greeting images. setdefault keeps first.
    chunks: dict[str, str] = {}
    for keyword, text in _iter_png_text(data):
        chunks.setdefault(keyword, text)
    for key in ("ccv3", "chara"):
        if key in chunks:
            try:
                raw = base64.b64decode(chunks[key])
            except Exception as exc:
                raise CardParseError(f"bad base64 in {key}") from exc
            return to_v3(json.loads(raw.decode("utf-8")))
    raise CardParseError("no ccv3/chara tEXt chunk in PNG")


def _loads_charx(data: bytes) -> dict:
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            return to_v3(json.loads(z.read("card.json").decode("utf-8")))
    except (KeyError, zipfile.BadZipFile, ValueError) as exc:
        raise CardParseError(f"invalid charx: {exc}") from exc


def read_charx_asset(data: bytes, path: str) -> bytes | None:
    """Bytes of a file bundled inside a CHARX, or None if it isn't readable.

    `path` comes out of a card's `embeded://` URI, i.e. from an uploaded file:
    nothing is written to disk from it (a zip member is only *read* by name, so
    a `../` in there escapes nothing), and a member claiming to inflate past the
    asset cap is refused rather than decompressed.
    """
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            if z.getinfo(path).file_size > _MAX_ASSET_BYTES:
                return None
            return z.read(path)
    # RuntimeError covers an encrypted member (and NotImplementedError, an
    # unsupported compression method, which subclasses it): a file we cannot
    # open is an unresolved candidate, not a 500 out of the import route.
    except (KeyError, OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        return None


def embedded_path(uri: str) -> str | None:
    """The in-container path an `embeded://` URI names, or None for any other URI."""
    return uri[len(EMBEDDED_SCHEME):] if uri.startswith(EMBEDDED_SCHEME) else None


def charx_avatar_path(ext: str) -> str:
    return f"assets/avatar.{ext}"


def loads(data: bytes, fmt: str) -> dict:
    if fmt == "json":
        return _loads_json(data)
    if fmt == "png":
        return _loads_png(data)
    if fmt == "charx":
        return _loads_charx(data)
    raise CardParseError(f"unknown format: {fmt}")


def _png_chunk(typ: bytes, payload: bytes) -> bytes:
    body = typ + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _placeholder_png_pixels() -> bytes:
    # 1x1 truecolor: IHDR + minimal IDAT (one filtered black scanline)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\x00\x00\x00"  # filter byte 0 + RGB black
    idat = zlib.compress(raw)
    return _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat)


def _png_image_plane(data: bytes) -> bytes | None:
    """`data`'s picture-bearing chunks, ready to sit under a fresh card chunk.

    IEND is dropped (the caller re-appends it last) and so is any card payload a
    previous export left in a tEXt chunk: `_loads_png` keeps the FIRST such
    chunk it finds, so carrying an old one through would export the stale card
    rather than this one. None if `data` is not a PNG we can take apart, which
    is the signal to fall back to the placeholder.
    """
    try:
        chunks = list(_iter_png_chunks(data))
    except CardParseError:
        return None
    kinds = {ctype for ctype, _payload, _raw in chunks}
    if not {b"IHDR", b"IEND"} <= kinds:
        return None  # truncated or not an image; nothing safe to re-emit
    keep = [raw for ctype, payload, raw in chunks
            if ctype != b"IEND"
            and not (ctype == b"tEXt" and payload.partition(b"\x00")[0] in _CARD_KEYWORDS)]
    return b"".join(keep)


def _data_uri(blob: bytes, ext: str) -> str:
    return f"data:{_EXT_MIME[ext]};base64," + base64.b64encode(blob).decode("ascii")


def _with_avatar_asset(card: dict, uri: str, ext: str) -> dict:
    """A copy of `card` with an avatar asset for `uri` at the head of `assets`.

    A copy because export reads the caller's stored card and must not touch it.

    Prepended, not substituted: `characters._avatar_candidates` walks these in
    order and takes the first that RESOLVES, so being first is all it takes to
    win the re-import — while deleting what was there would quietly strip a
    card's own provenance (the remote URL an import kept) and hand the
    re-imported character a different `card_hash` from the one it came from.
    """
    out = dict(card)
    data = dict(out.get("data") or {})
    data["assets"] = [{"type": "icon", "uri": uri, "name": "main", "ext": ext},
                      *(data.get("assets") or [])]
    out["data"] = data
    return out


def _usable_avatar(avatar: tuple[bytes, str] | None) -> tuple[bytes, str] | None:
    """Drop an avatar whose type we have no MIME for — it could only produce an
    asset entry no reader could interpret. `store.assets` allows exactly the
    types in `_EXT_MIME`, so this is a guard, not a path exports take."""
    if avatar is None:
        return None
    blob, ext = avatar
    ext = ext.lstrip(".").lower()
    return (blob, ext) if blob and ext in _EXT_MIME else None


def dumps(card: dict, fmt: str, avatar: tuple[bytes, str] | None = None) -> bytes:
    """Serialize `card`, embedding `(bytes, ext)` as its avatar where the format
    allows (see the module docstring). Pure: `card` is never mutated."""
    card = to_v3(card)
    avatar = _usable_avatar(avatar)
    if fmt == "json":
        if avatar:
            card = _with_avatar_asset(card, _data_uri(*avatar), avatar[1])
        return (json.dumps(card, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    if fmt == "png":
        pixels = _png_image_plane(avatar[0]) if avatar else None
        if avatar and pixels is None:
            # A jpg/gif/webp avatar cannot become the image plane without a
            # decoder, and this module has none. Rather than lose the picture,
            # send it in the card and let the import prefer it over these pixels.
            card = _with_avatar_asset(card, _data_uri(*avatar), avatar[1])
        text = base64.b64encode(_canonical(card).encode("utf-8")).decode("latin-1")
        return _PNG_SIG + (pixels or _placeholder_png_pixels()) + _png_chunk(
            b"tEXt", b"ccv3\x00" + text.encode("latin-1")
        ) + _png_chunk(b"IEND", b"")
    if fmt == "charx":
        path = charx_avatar_path(avatar[1]) if avatar else None
        if path:
            card = _with_avatar_asset(card, EMBEDDED_SCHEME + path, avatar[1])
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("card.json", _canonical(card))
            if path:
                z.writestr(path, avatar[0])
        return buf.getvalue()
    raise CardParseError(f"unknown format: {fmt}")
