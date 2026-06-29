import base64
import json
import struct
import zipfile
import zlib
from io import BytesIO

import pytest

from grimoire.store import cards


def _v3():
    return {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "keeper", "extensions": {}}}


def _png_with_text(keyword: str, text: str) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(typ: bytes, payload: bytes) -> bytes:
        body = typ + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    text_chunk = chunk(b"tEXt", keyword.encode("latin-1") + b"\x00" + text.encode("latin-1"))
    return sig + chunk(b"IHDR", ihdr) + text_chunk + chunk(b"IEND", b"")


def test_loads_bare_v3_json():
    card = cards.loads(json.dumps(_v3()).encode(), "json")
    assert card["spec"] == "chara_card_v3"
    assert card["data"]["name"] == "Seraphine"


def test_loads_v2_json_upconverts():
    v2 = {"spec": "chara_card_v2", "spec_version": "2.0",
          "data": {"name": "Old", "description": "d", "first_mes": "hi", "some_unknown": 7}}
    card = cards.loads(json.dumps(v2).encode(), "json")
    assert card["spec"] == "chara_card_v3"
    assert card["data"]["name"] == "Old"
    assert card["data"]["extensions"]["some_unknown"] == 7  # unknown preserved


def test_loads_png_ccv3_then_chara_fallback():
    text = base64.b64encode(json.dumps(_v3()).encode()).decode()
    png = _png_with_text("ccv3", text)
    assert cards.loads(png, "png")["data"]["name"] == "Seraphine"
    # chara fallback (V2 payload) upconverts
    v2 = {"spec": "chara_card_v2", "data": {"name": "Fall", "description": ""}}
    png2 = _png_with_text("chara", base64.b64encode(json.dumps(v2).encode()).decode())
    assert cards.loads(png2, "png")["data"]["name"] == "Fall"


def test_loads_png_keeps_first_of_duplicate_chara_chunks():
    """Some exporters append a second, stripped `chara` tEXt chunk after the
    original. The first chunk is canonical (SillyTavern reads it); the later one
    must not clobber it — otherwise greeting images vanish on import."""
    def chunk(typ: bytes, payload: bytes) -> bytes:
        body = typ + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    def text_chunk(keyword: str, obj: dict) -> bytes:
        text = base64.b64encode(json.dumps(obj).encode()).decode()
        return chunk(b"tEXt", keyword.encode("latin-1") + b"\x00" + text.encode("latin-1"))
    full = {"spec": "chara_card_v2", "data": {"name": "Akane", "first_mes": '<img src="https://x/a.webp">'}}
    stripped = {"spec": "chara_card_v2", "data": {"name": "Akane", "first_mes": "no image"}}
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + text_chunk("chara", full) + text_chunk("chara", stripped)
           + chunk(b"IEND", b""))
    card = cards.loads(png, "png")
    assert card["data"]["first_mes"] == '<img src="https://x/a.webp">'


def test_loads_charx():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("card.json", json.dumps(_v3()))
    assert cards.loads(buf.getvalue(), "charx")["data"]["name"] == "Seraphine"


def test_garbage_raises():
    with pytest.raises(cards.CardParseError):
        cards.loads(b"not json", "json")
    with pytest.raises(cards.CardParseError):
        cards.loads(b"\x89PNG\r\n\x1a\n", "png")  # no tEXt chunk


def test_json_roundtrip():
    out = cards.dumps(_v3(), "json")
    assert cards.loads(out, "json")["data"]["name"] == "Seraphine"


def test_png_roundtrip():
    out = cards.dumps(_v3(), "png")
    assert cards.loads(out, "png")["data"]["name"] == "Seraphine"


def test_charx_roundtrip():
    out = cards.dumps(_v3(), "charx")
    assert cards.loads(out, "charx")["data"]["name"] == "Seraphine"
