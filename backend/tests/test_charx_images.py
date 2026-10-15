"""#26: a CHARX's named bundled pictures belong in the version's gallery."""

import json
import struct
import zipfile
import zlib
from io import BytesIO
from pathlib import Path

import pytest

from grimoire.store import assets, cards, image_store
from grimoire.store import characters as ch


def _picture(name="base.png"):
    return (Path(__file__).parent / "fixtures" / "images" / name).read_bytes()


def _bundle(entries, members, *, relocated=False):
    card = ch.blank_card("Seraphine")
    holder = card["data"]["extensions"] if relocated else card["data"]
    holder["assets"] = entries
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("card.json", json.dumps(card))
        for path, data in members.items():
            archive.writestr(path, data)
    return out.getvalue()


def _entry(name, path, kind="emotion"):
    # The metadata's extension deliberately disagrees: bytes decide the type.
    return {"type": kind, "name": name, "uri": f"embeded://{path}", "ext": "gif"}


@pytest.mark.parametrize("destination", ["new", "version", "update"])
@pytest.mark.parametrize("relocated", [False, True])
def test_charx_import_lists_named_images_beside_the_avatar(tmp_path, destination, relocated):
    png = _picture()
    entries = [_entry("main", "assets/main.bin", "icon")]
    members = {"assets/main.bin": png}
    for i in range(6):
        entries.append(_entry(f"emotion_{i}", f"assets/{i}.bin"))
        members[f"assets/{i}.bin"] = png
    kwargs = {}
    if destination != "new":
        cid, vid = ch.create_character(tmp_path, "Seraphine")
        kwargs["into_cid"] = cid
        if destination == "update":
            kwargs["update_vid"] = vid

    cid, vid = ch.import_card(tmp_path, _bundle(entries, members, relocated=relocated),
                              "charx", **kwargs)

    version = next(v for v in ch.read_character(tmp_path, cid)["versions"] if v["id"] == vid)
    assert set(version["images"]) == {"avatar", *(f"emotion_{i}" for i in range(6))}
    if destination != "version":
        assert ch.list_characters(tmp_path)[0]["has_avatar"]
    for name in version["images"]:
        path = assets.image_path(tmp_path, cid, vid, name)
        assert path is not None and path.suffix == ".png"
        assert path.read_bytes() == png
        assert name in version["image_ids"]  # placements use the content-addressed store


@pytest.mark.parametrize("path", ["../happy.png", "/happy.png", "C:/happy.png",
                                 "assets/../happy.png", "assets\\..\\happy.png"])
def test_charx_import_skips_unsafe_member_paths(tmp_path, path):
    png = _picture()
    blob = _bundle([_entry("unsafe", path), _entry("happy", "assets/happy.png")],
                   {path: png, "assets/happy.png": png})

    cid, vid = ch.import_card(tmp_path, blob, "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["happy"]
    assert assets.image_path(tmp_path, cid, vid, "unsafe") is None


def test_charx_import_skips_unusable_entries_without_losing_valid_images(tmp_path):
    png = _picture()
    entries = [None, "invalid", {}, _entry("missing", "assets/missing.png"),
               _entry("text", "assets/text.png"), _entry("large", "assets/large.png"),
               _entry("../escape", "assets/happy.png"), _entry("bad\0name", "assets/happy.png"),
               _entry("promote-tmp", "assets/happy.png"),
               _entry("Avatar", "assets/happy.png"), _entry("happy", "assets/happy.png")]
    blob = _bundle(entries, {"assets/text.png": b"not an image",
                             "assets/large.png": png * 16, "assets/happy.png": png})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cards, "MAX_ASSET_BYTES", 8192)
        cid, _vid = ch.import_card(tmp_path, blob, "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["happy"]


@pytest.mark.parametrize("name", ['happy|sad', 'happy<', 'happy>', 'happy"', 'happy\x01',
                                  "CON", "COM1", "NUL", "x" * 256])
def test_charx_import_skips_names_that_cannot_be_portable_placements(tmp_path, name):
    blob = _bundle([_entry(name, "assets/happy.png"), _entry("happy", "assets/happy.png")],
                   {"assets/happy.png": _picture()})

    cid, _vid = ch.import_card(tmp_path, blob, "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["happy"]

def test_charx_import_keeps_first_valid_name_and_protects_avatar(tmp_path):
    png, other = _picture(), _picture("png_onepixel.png")
    entries = [_entry("main", "assets/main.png", "icon"),
               _entry("happy", "assets/missing.png"), _entry("happy", "assets/main.png"),
               _entry("HAPPY", "assets/other.png"), _entry("avatar", "assets/other.png")]

    cid, vid = ch.import_card(tmp_path, _bundle(entries, {"assets/main.png": png,
                                                        "assets/other.png": other}), "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["avatar", "happy"]
    assert assets.image_path(tmp_path, cid, vid, "happy").read_bytes() == png
    assert assets.image_path(tmp_path, cid, vid, "avatar").read_bytes() == png


def test_charx_shared_avatar_and_gallery_member_is_read_once(tmp_path, monkeypatch):
    png = _picture()
    blob = _bundle([_entry("main", "assets/main.png", "icon"),
                    _entry("background", "assets/main.png", "background")],
                   {"assets/main.png": png})
    real_read = cards.read_charx_asset
    reads = []

    def read(data, path, max_bytes=cards.MAX_ASSET_BYTES, **kwargs):
        reads.append(path)
        return real_read(data, path, max_bytes, **kwargs)

    monkeypatch.setattr(cards, "read_charx_asset", read)
    monkeypatch.setattr(cards, "MAX_ASSET_BYTES", 4096)
    cid, _vid = ch.import_card(tmp_path, blob, "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["avatar", "background"]
    assert reads == ["assets/main.png"]


def test_charx_avatar_and_gallery_share_the_decompression_budget(tmp_path, monkeypatch):
    png = _picture()
    blob = _bundle([_entry("main", "assets/main.png", "icon"),
                    _entry("decoy", "assets/decoy.png"), _entry("happy", "assets/happy.png")],
                   {"assets/main.png": png, "assets/decoy.png": b"x" * len(png),
                    "assets/happy.png": png})
    monkeypatch.setattr(cards, "MAX_ASSET_BYTES", 8192)

    cid, _vid = ch.import_card(tmp_path, blob, "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["avatar"]


def test_charx_import_bounds_member_lookups_including_empty_members(tmp_path, monkeypatch):
    png = _picture()
    entries = [_entry("main", "assets/main.png", "icon")]
    entries += [_entry(f"empty_{i}", f"assets/empty_{i}.png") for i in range(5)]
    entries.append(_entry("happy", "assets/happy.png"))
    members = {"assets/main.png": png, "assets/happy.png": png}
    members.update({f"assets/empty_{i}.png": b"" for i in range(5)})
    real_read = cards.read_charx_asset
    reads = []

    def read(data, path, max_bytes=cards.MAX_ASSET_BYTES, **kwargs):
        reads.append(path)
        return real_read(data, path, max_bytes, **kwargs)

    monkeypatch.setattr(cards, "read_charx_asset", read)
    monkeypatch.setattr(ch, "_MAX_BUNDLED_IMAGES", 3)

    cid, _vid = ch.import_card(tmp_path, _bundle(entries, members), "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["avatar"]
    assert reads == ["assets/main.png", "assets/empty_0.png", "assets/empty_1.png"]


def test_charx_import_bounds_placements_even_when_members_are_shared(tmp_path, monkeypatch):
    entries = [_entry(f"emotion_{i}", "assets/shared.png") for i in range(5)]
    monkeypatch.setattr(ch, "_MAX_BUNDLED_IMAGES", 3)

    cid, _vid = ch.import_card(tmp_path, _bundle(entries, {"assets/shared.png": _picture()}), "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == [
        "emotion_0", "emotion_1", "emotion_2"]

def test_charx_corrupt_crc_still_spends_the_decompression_budget(tmp_path, monkeypatch):
    png = _picture()
    decoy = b"x" * 8192
    blob = _bundle([_entry("decoy", "assets/decoy.png"),
                    _entry("happy", "assets/happy.png")],
                   {"assets/decoy.png": decoy, "assets/happy.png": png})
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        checksum = archive.getinfo("assets/decoy.png").CRC
    # Change both copies of this member's CRC, leaving its deflate stream
    # intact: the work happens before ZipExtFile notices the bad checksum.
    blob = blob.replace(struct.pack("<I", checksum), struct.pack("<I", checksum ^ 1))
    monkeypatch.setattr(cards, "MAX_ASSET_BYTES", len(decoy))

    cid, _vid = ch.import_card(tmp_path, blob, "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == []


@pytest.mark.parametrize('compression', [zipfile.ZIP_DEFLATED, zipfile.ZIP_LZMA])
def test_charx_corrupt_compression_is_skipped_before_a_valid_image(tmp_path, compression):
    if compression == zipfile.ZIP_LZMA:
        pytest.importorskip('lzma')
    decoy = zipfile.ZipInfo("assets/decoy.png")
    decoy.compress_type = compression
    blob = bytearray(_bundle([_entry("decoy", "assets/decoy.png"),
                              _entry("happy", "assets/happy.png")],
                             {decoy: b"x" * 1024, "assets/happy.png": _picture()}))
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        offset = archive.getinfo("assets/decoy.png").header_offset
    name_length, extra_length = struct.unpack_from("<HH", blob, offset + 26)
    payload = offset + 30 + name_length + extra_length
    if compression == zipfile.ZIP_LZMA:
        blob[payload + 9] ^= 0xFF
    else:
        blob[payload] = 0x07  # invalid deflate block type

    cid, _vid = ch.import_card(tmp_path, bytes(blob), "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["happy"]

@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED,
                                          zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_charx_forged_sizes_cannot_inflate_past_the_charged_budget(monkeypatch, compression):
    if compression == zipfile.ZIP_BZIP2:
        pytest.importorskip("bz2")
    if compression == zipfile.ZIP_LZMA:
        pytest.importorskip("lzma")
    prefix = b"\x89PNG\r\n\x1a\n"
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression) as archive:
        archive.writestr("assets/happy.png", prefix + b"x" * (4 * 1024 * 1024))
    blob = bytearray(out.getvalue())
    central = blob.index(b"PK\x01\x02")
    # Lie about size AND checksum so ZipExtFile accepts the first eight bytes.
    struct.pack_into("<I", blob, 14, zlib.crc32(prefix))
    struct.pack_into("<I", blob, 22, len(prefix))
    struct.pack_into("<I", blob, central + 16, zlib.crc32(prefix))
    struct.pack_into("<I", blob, central + 24, len(prefix))
    real_decompressor = zipfile._get_decompressor
    inflated = 0

    class Measured:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def decompress(self, *args, **kwargs):
            nonlocal inflated
            result = self.inner.decompress(*args, **kwargs)
            inflated += len(result)
            return result

    def measured(*args, **kwargs):
        inner = real_decompressor(*args, **kwargs)
        return Measured(inner) if inner is not None else None

    monkeypatch.setattr(zipfile, "_get_decompressor", measured)
    charged = []

    cards.read_charx_asset(bytes(blob), "assets/happy.png", max_bytes=65536, spend=charged.append)

    assert inflated <= sum(charged)
    assert sum(charged) <= 65536

def test_charx_without_bundled_images_keeps_the_card(tmp_path):
    card = ch.blank_card("Seraphine")
    blob = cards.dumps(card, "charx")

    cid, vid = ch.import_card(tmp_path, blob, "charx")

    assert ch.read_card(tmp_path, cid, vid) == card
    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == []


def test_charx_gallery_names_over_one_member_ingest_it_once(tmp_path, monkeypatch):
    # Regression: each put re-sanitized the picture, so 128 names over one large
    # member cost 128 full decodes inside a request (#26).
    png = _picture()
    entries = [_entry(f"emotion_{i}", "assets/shared.png") for i in range(8)]
    real_ingest = image_store.ingest
    ingests = []

    def counting(data, ext, **kwargs):
        ingests.append(len(data))
        return real_ingest(data, ext, **kwargs)

    monkeypatch.setattr(image_store, "ingest", counting)

    cid, vid = ch.import_card(tmp_path, _bundle(entries, {"assets/shared.png": png}), "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == [f"emotion_{i}" for i in range(8)]
    assert ingests == [len(png)]
    for i in range(8):
        assert assets.image_path(tmp_path, cid, vid, f"emotion_{i}").read_bytes() == png
