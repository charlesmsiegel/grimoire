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


def test_bake_char_name_replaces_macro_across_text_fields():
    card = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {
        "name": "Seraphine",
        "description": "{{char}} keeps the ledgers.",
        "first_mes": "{{Char}}: hello",
        "mes_example": "{{user}}: hi\n{{char}}: welcome",
        "alternate_greetings": ["{{CHAR}} waves"],
        "character_book": {"entries": [{"content": "{{char}} lore"}]},
        "extensions": {},
    }}
    assert cards.bake_char_name(card) is True
    d = card["data"]
    assert d["description"] == "Seraphine keeps the ledgers."
    assert d["first_mes"] == "Seraphine: hello"  # case-insensitive
    assert d["mes_example"] == "{{user}}: hi\nSeraphine: welcome"  # {{user}} untouched
    assert d["alternate_greetings"] == ["Seraphine waves"]
    assert d["character_book"]["entries"][0]["content"] == "Seraphine lore"


def test_bake_char_name_noop_without_name_or_macro():
    unnamed = {"spec": "chara_card_v3", "spec_version": "3.0",
               "data": {"name": "", "description": "{{char}} x", "extensions": {}}}
    assert cards.bake_char_name(unnamed) is False
    assert unnamed["data"]["description"] == "{{char}} x"  # no name -> left alone

    plain = {"spec": "chara_card_v3", "spec_version": "3.0",
             "data": {"name": "Sera", "description": "no macros here", "extensions": {}}}
    assert cards.bake_char_name(plain) is False
    assert plain["data"]["description"] == "no macros here"


def test_bake_char_token_name_containing_macro_is_never_substituted_in():
    # A name containing {{char}} would reintroduce the very token being
    # resolved -- substituting it in would make a second (redundant but
    # otherwise harmless) bake pass corrupt the text further each time.
    assert cards.bake_char_token("{{char}} arrives.", "A {{char}} B") == "{{char}} arrives."
    # repeated calls must never compound, whatever the (degenerate) name is
    once = cards.bake_char_token("{{char}} arrives.", "A {{char}} B")
    twice = cards.bake_char_token(once, "A {{char}} B")
    assert once == twice == "{{char}} arrives."


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


# ---- avatar embedding on export (#25) -------------------------------------
# The card format's own way of carrying an image, so an export is a whole
# character rather than its text: every format has to hand the avatar back to
# `loads` -> `import_card`, which is what the round-trip tests below check.


def _chunk(typ: bytes, payload: bytes) -> bytes:
    body = typ + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _real_png(w: int = 2, h: int = 2) -> bytes:
    """A PNG with real pixels — distinguishable from dumps' 1x1 placeholder."""
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))


def _png_size(data: bytes) -> tuple[int, int]:
    return struct.unpack(">II", data[16:24])  # IHDR payload: 8 sig + 4 len + 4 type


def _icons(card: dict) -> list[dict]:
    return [a for a in card["data"].get("assets", []) if a.get("type") == "icon"]


def test_json_dumps_embeds_the_avatar_as_a_data_uri():
    avatar = _real_png()
    card = cards.loads(cards.dumps(_v3(), "json", avatar=(avatar, "png")), "json")
    (icon,) = _icons(card)
    assert icon["uri"].startswith("data:image/png;base64,")
    assert base64.b64decode(icon["uri"].split(",", 1)[1]) == avatar
    assert icon["ext"] == "png"


def test_png_dumps_carries_the_avatar_pixels_not_the_placeholder():
    out = cards.dumps(_v3(), "png", avatar=(_real_png(4, 3), "png"))
    assert _png_size(out) == (4, 3)
    assert cards.loads(out, "png")["data"]["name"] == "Seraphine"


def test_png_dumps_drops_a_stale_card_chunk_carried_by_the_avatar():
    """An avatar that is itself an imported card PNG already holds a `ccv3`
    chunk. `_loads_png` keeps the FIRST chunk it finds, so re-using those bytes
    without dropping the old one would export the stale card, not this one."""
    stale = cards.dumps({"spec": "chara_card_v3", "spec_version": "3.0",
                         "data": {"name": "Winifred", "extensions": {}}}, "png")
    out = cards.dumps(_v3(), "png", avatar=(stale, "png"))
    assert cards.loads(out, "png")["data"]["name"] == "Seraphine"


@pytest.mark.parametrize("broken", ["no-idat", "bad-crc", "no-ihdr-first"])
def test_png_dumps_refuses_an_image_plane_it_cannot_vouch_for(broken):
    """These bytes are re-emitted as an image other apps open, so a file that
    merely starts with the PNG magic is not enough: a plane with no IDAT, a
    corrupt CRC, or chunks out of order falls back to the placeholder (and the
    card still carries the picture as a data URI)."""
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    idat = _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00" * 2))
    if broken == "no-idat":
        bad = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")
    elif broken == "bad-crc":
        good = _chunk(b"IHDR", ihdr)
        bad = b"\x89PNG\r\n\x1a\n" + good[:-4] + b"\x00\x00\x00\x00" + idat + _chunk(b"IEND", b"")
    else:
        bad = b"\x89PNG\r\n\x1a\n" + idat + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")

    out = cards.dumps(_v3(), "png", avatar=(bad, "png"))

    assert _png_size(out) == (1, 1)
    assert base64.b64decode(_icons(cards.loads(out, "png"))[0]["uri"].split(",", 1)[1]) == bad


def test_is_placeholder_png_tells_our_stand_in_from_a_picture():
    assert cards.is_placeholder_png(cards.dumps(_v3(), "png")) is True
    assert cards.is_placeholder_png(cards.dumps(_v3(), "png", avatar=(_real_png(), "png"))) is False
    assert cards.is_placeholder_png(b"not a png") is False


def test_png_dumps_falls_back_to_a_data_uri_for_a_non_png_avatar():
    """No Pillow here, so a JPEG cannot become the PNG's image plane. The card
    still has to carry it, or a jpg avatar is simply lost on PNG export."""
    jpg = b"\xff\xd8\xff" + b"JPEGDATA"
    out = cards.dumps(_v3(), "png", avatar=(jpg, "jpg"))
    assert _png_size(out) == (1, 1)  # the placeholder stays
    (icon,) = _icons(cards.loads(out, "png"))
    assert icon["uri"].startswith("data:image/jpeg;base64,")
    assert base64.b64decode(icon["uri"].split(",", 1)[1]) == jpg


def test_charx_dumps_bundles_the_avatar_and_references_it():
    avatar = _real_png()
    out = cards.dumps(_v3(), "charx", avatar=(avatar, "png"))
    with zipfile.ZipFile(BytesIO(out)) as z:
        assert z.read("assets/avatar.png") == avatar
        card = json.loads(z.read("card.json"))
    (icon,) = _icons(card)
    assert icon["uri"] == "embeded://assets/avatar.png"


def test_dumps_puts_its_icon_first_and_keeps_what_was_there():
    """Being first is enough — `_avatar_candidates` takes the first entry that
    RESOLVES — and deleting the rest would strip the card's own provenance: the
    remote URL an import kept would vanish on a round trip, taking the
    character's `card_hash` with it (Codex review). None of this may mutate the
    caller's card, which export reads straight from the store."""
    card = _v3()
    card["data"]["assets"] = [
        {"type": "icon", "uri": "https://x/old.png", "name": "main", "ext": "png"},
        {"type": "background", "uri": "https://x/bg.png", "name": "bg", "ext": "png"},
    ]
    out = cards.loads(cards.dumps(card, "json", avatar=(_real_png(), "png")), "json")
    assets_out = out["data"]["assets"]
    assert assets_out[0]["uri"].startswith("data:image/png;base64,")
    assert [a["uri"] for a in assets_out[1:]] == ["https://x/old.png", "https://x/bg.png"]
    assert len(card["data"]["assets"]) == 2  # the caller's card is untouched


def test_dumps_without_an_avatar_adds_no_asset_entry():
    assert _icons(cards.loads(cards.dumps(_v3(), "json"), "json")) == []


def test_read_charx_asset_refuses_a_member_that_would_inflate_past_the_cap():
    """An uploaded CHARX is untrusted, and a zip states up front how big a
    member inflates to: believe the header and refuse, rather than decompress a
    few compressed kilobytes into a great deal of memory."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("assets/avatar.png", b"\x00" * 4096)
    blob = buf.getvalue()
    assert cards.read_charx_asset(blob, "assets/avatar.png") is not None
    assert cards.read_charx_asset(blob, "assets/avatar.png", max_bytes=1024) is None


def test_read_charx_asset_treats_an_unopenable_member_as_a_miss(monkeypatch):
    """An encrypted member raises RuntimeError out of `read` (an unsupported
    compression method raises NotImplementedError, which subclasses it). Import
    is best-effort: that is a candidate we could not resolve, not a 500."""
    out = cards.dumps(_v3(), "charx", avatar=(_real_png(), "png"))

    def encrypted(self, name):
        raise RuntimeError(f"File {name} is encrypted, password required")

    monkeypatch.setattr(zipfile.ZipFile, "read", encrypted)
    assert cards.read_charx_asset(out, "assets/avatar.png") is None


def test_read_charx_asset_reads_a_bundled_file_and_misses_cleanly():
    out = cards.dumps(_v3(), "charx", avatar=(_real_png(), "png"))
    assert cards.read_charx_asset(out, "assets/avatar.png") == _real_png()
    assert cards.read_charx_asset(out, "assets/nope.png") is None
    assert cards.read_charx_asset(b"not a zip", "assets/avatar.png") is None
