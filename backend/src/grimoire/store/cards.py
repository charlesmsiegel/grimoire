"""Import/export SillyTavern cards: V3/V2 JSON, PNG tEXt, and CHARX zip.

Pure stdlib (struct/zlib/base64/zipfile) — no Pillow. PNG export writes the card
into a `ccv3` tEXt chunk over a 1x1 placeholder. Avatars from imported PNG/CHARX
are preserved on disk under assets/ (not re-embedded this iteration).
"""

from __future__ import annotations

import base64
import json
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


def _loads_json(data: bytes) -> dict:
    try:
        return to_v3(json.loads(data.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CardParseError(f"invalid card JSON: {exc}") from exc


def _iter_png_text(data: bytes):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise CardParseError("not a PNG")
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length  # 4 len + 4 type + payload + 4 crc
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
            except Exception as exc:  # noqa: BLE001
                raise CardParseError(f"bad base64 in {key}") from exc
            return to_v3(json.loads(raw.decode("utf-8")))
    raise CardParseError("no ccv3/chara tEXt chunk in PNG")


def _loads_charx(data: bytes) -> dict:
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            return to_v3(json.loads(z.read("card.json").decode("utf-8")))
    except (KeyError, zipfile.BadZipFile, ValueError) as exc:
        raise CardParseError(f"invalid charx: {exc}") from exc


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


def dumps(card: dict, fmt: str, avatar: bytes | None = None) -> bytes:
    card = to_v3(card)
    if fmt == "json":
        return (json.dumps(card, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    if fmt == "png":
        text = base64.b64encode(_canonical(card).encode("utf-8")).decode("latin-1")
        sig = b"\x89PNG\r\n\x1a\n"
        return sig + _placeholder_png_pixels() + _png_chunk(
            b"tEXt", b"ccv3\x00" + text.encode("latin-1")
        ) + _png_chunk(b"IEND", b"")
    if fmt == "charx":
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("card.json", _canonical(card))
        return buf.getvalue()
    raise CardParseError(f"unknown format: {fmt}")
