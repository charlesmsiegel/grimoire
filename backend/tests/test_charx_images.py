"""#26: a CHARX's named bundled pictures belong in the version's gallery."""

import json
import struct
import zipfile
import zlib
from io import BytesIO
from pathlib import Path

import pytest

from grimoire.store import assets, cards, image_refs, image_store, image_subjects, locks
from grimoire.store import characters as ch
from grimoire.store.characters import _bundled as character_bundled


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


@pytest.mark.parametrize("name", ['happy|sad', 'happy<', 'happy>', 'happy"', 'happy\x01', 'art#1', 'art%41', 'art?x',
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
    monkeypatch.setattr(character_bundled, "_MAX_BUNDLED_IMAGES", 3)

    cid, _vid = ch.import_card(tmp_path, _bundle(entries, members), "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["avatar"]
    assert reads == ["assets/main.png", "assets/empty_0.png", "assets/empty_1.png"]


def test_charx_import_bounds_placements_even_when_members_are_shared(tmp_path, monkeypatch):
    entries = [_entry(f"emotion_{i}", "assets/shared.png") for i in range(5)]
    monkeypatch.setattr(character_bundled, "_MAX_BUNDLED_IMAGES", 3)

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


def _broken_placement(monkeypatch, after: int):
    """Let `after` placements succeed, then fail the next with a disk error."""
    real = assets.link_if_vacant
    calls = []

    def flaky(*args, **kwargs):
        calls.append(args[1])
        if len(calls) > after:
            raise OSError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(assets, "link_if_vacant", flaky)


def _two_picture_card():
    png = _picture()
    entries = [_entry("happy", "assets/a.png"), _entry("sad", "assets/b.png")]
    return _bundle(entries, {"assets/a.png": png, "assets/b.png": png})


def test_failed_charx_import_leaves_no_new_character(tmp_path, monkeypatch):
    _broken_placement(monkeypatch, after=1)

    with pytest.raises(OSError):
        ch.import_card(tmp_path, _two_picture_card(), "charx", name="Seraphine")

    assert ch.list_characters(tmp_path) == []


def test_failed_charx_import_leaves_an_existing_character_as_it_was(tmp_path, monkeypatch):
    cid, _vid = ch.create_character(tmp_path, "Seraphine")
    before = ch.read_character(tmp_path, cid)
    _broken_placement(monkeypatch, after=1)

    with pytest.raises(OSError):
        ch.import_card(tmp_path, _two_picture_card(), "charx", into_cid=cid)

    assert ch.read_character(tmp_path, cid) == before


def test_charx_update_fills_empty_slots_and_keeps_existing_pictures(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    png, other = _picture(), _picture("png_onepixel.png")
    first = _bundle([_entry("happy", "assets/a.png")], {"assets/a.png": png})
    ch.import_card(tmp_path, first, "charx", into_cid=cid, update_vid=vid)
    second = _bundle([_entry("happy", "assets/a.png"), _entry("sad", "assets/b.png")],
                     {"assets/a.png": other, "assets/b.png": other})

    ch.import_card(tmp_path, second, "charx", into_cid=cid, update_vid=vid)

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["happy", "sad"]
    assert assets.image_path(tmp_path, cid, vid, "happy").read_bytes() == png
    assert assets.image_path(tmp_path, cid, vid, "sad").read_bytes() == other


def test_failed_charx_update_removes_only_what_it_added(tmp_path, monkeypatch):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    png, other = _picture(), _picture("png_onepixel.png")
    ch.import_card(tmp_path, _bundle([_entry("happy", "assets/a.png")], {"assets/a.png": png}),
                   "charx", into_cid=cid, update_vid=vid)
    before_card = ch.read_card(tmp_path, cid, vid)
    update = _bundle([_entry("happy", "assets/a.png"), _entry("sad", "assets/b.png"),
                      _entry("angry", "assets/c.png")],
                     {"assets/a.png": other, "assets/b.png": other, "assets/c.png": other})
    _broken_placement(monkeypatch, after=1)

    with pytest.raises(OSError):
        ch.import_card(tmp_path, update, "charx", into_cid=cid, update_vid=vid)

    assert ch.read_card(tmp_path, cid, vid) == before_card
    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["happy"]
    assert assets.image_path(tmp_path, cid, vid, "happy").read_bytes() == png


def test_failed_charx_update_keeps_a_legacy_file_it_did_not_replace(tmp_path, monkeypatch):
    # A picture stored before placement records existed: a legacy file, no placement.
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    png = _picture()
    directory = assets.version_dir(tmp_path, cid, vid)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "happy.png").write_bytes(png)
    update = _bundle([_entry("happy", "assets/a.png"), _entry("sad", "assets/b.png")],
                     {"assets/a.png": _picture("png_onepixel.png"), "assets/b.png": png})
    _broken_placement(monkeypatch, after=0)

    with pytest.raises(OSError):
        ch.import_card(tmp_path, update, "charx", into_cid=cid, update_vid=vid)

    assert (directory / "happy.png").read_bytes() == png
    assert assets.image_path(tmp_path, cid, vid, "happy").read_bytes() == png


def test_rollback_leaves_a_picture_uploaded_during_the_import(tmp_path, monkeypatch):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    png, upload = _picture(), _picture("png_onepixel.png")
    real = assets.link_if_vacant
    calls = []

    def upload_then_fail(d, name, image_id, **kwargs):
        calls.append(name)
        if len(calls) == 1:
            placed = real(d, name, image_id, **kwargs)
            # A user uploads a picture into the slot this import has just filled ...
            assets.put_image(tmp_path, cid, vid, name, upload, "png")
            return placed
        raise OSError("disk full")

    monkeypatch.setattr(assets, "link_if_vacant", upload_then_fail)
    blob = _bundle([_entry("happy", "assets/a.png"), _entry("sad", "assets/b.png")],
                   {"assets/a.png": png, "assets/b.png": png})

    with pytest.raises(OSError):
        ch.import_card(tmp_path, blob, "charx", into_cid=cid, update_vid=vid)

    # ... and the rollback must not delete it: `happy` still holds the upload.
    assert assets.image_path(tmp_path, cid, vid, "happy").read_bytes() == upload
    assert "sad" not in ch.read_character(tmp_path, cid)["versions"][0]["images"]


def test_link_if_vacant_refuses_an_occupied_slot_and_a_legacy_file(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    directory = assets.version_dir(tmp_path, cid, vid)
    png = _picture()
    assets.put_image(tmp_path, cid, vid, "happy", png, "png")
    (directory / "sad.png").write_bytes(png)  # a legacy file, no placement

    assert assets.link_if_vacant(directory, "happy", "x" * 64) is False
    assert assets.link_if_vacant(directory, "sad", "x" * 64) is False
    assert assets.image_path(tmp_path, cid, vid, "happy").read_bytes() == png


def test_bad_destination_is_refused_before_any_picture_is_stored(tmp_path, monkeypatch):
    png = _picture()
    blob = _bundle([_entry("happy", "assets/a.png")], {"assets/a.png": png})
    stored = []
    real = image_store.ingest

    def counting(data, ext, **kwargs):
        stored.append(len(data))
        return real(data, ext, **kwargs)

    monkeypatch.setattr(image_store, "ingest", counting)

    with pytest.raises(ch.CharacterNotFound):
        ch.import_card(tmp_path, blob, "charx", into_cid="no-such-character")
    assert stored == []


def test_failed_update_leaves_a_card_saved_meanwhile(tmp_path, monkeypatch):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    png = _picture()
    blob = _bundle([_entry("happy", "assets/a.png"), _entry("sad", "assets/b.png")],
                   {"assets/a.png": png, "assets/b.png": png})
    real = assets.link_if_vacant
    calls = []

    def save_then_fail(d, name, image_id, **kwargs):
        calls.append(name)
        if len(calls) == 1:
            placed = real(d, name, image_id, **kwargs)
            # Another request saves the version while this import is placing.
            card = ch.read_card(tmp_path, cid, vid)
            card["data"]["description"] = "saved meanwhile"
            ch.update_version(tmp_path, cid, vid, card)
            return placed
        raise OSError("disk full")

    monkeypatch.setattr(assets, "link_if_vacant", save_then_fail)

    with pytest.raises(OSError):
        ch.import_card(tmp_path, blob, "charx", into_cid=cid, update_vid=vid)

    assert ch.read_card(tmp_path, cid, vid)["data"]["description"] == "saved meanwhile"


def test_update_ingests_nothing_for_names_the_version_already_has(tmp_path, monkeypatch):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    png, other = _picture(), _picture("png_onepixel.png")
    assets.put_image(tmp_path, cid, vid, "happy", png, "png")
    blob = _bundle([_entry("happy", "assets/a.png"), _entry("sad", "assets/b.png")],
                   {"assets/a.png": other, "assets/b.png": png})
    ingested = []
    real = image_store.ingest

    def counting(data, ext, **kwargs):
        ingested.append(data)
        return real(data, ext, **kwargs)

    monkeypatch.setattr(image_store, "ingest", counting)

    ch.import_card(tmp_path, blob, "charx", into_cid=cid, update_vid=vid)

    assert ingested == [png]  # only `sad`; the taken `happy` slot ingested nothing
    assert assets.image_path(tmp_path, cid, vid, "happy").read_bytes() == png


def test_failed_new_version_import_leaves_a_version_saved_meanwhile(tmp_path, monkeypatch):
    cid, _vid = ch.create_character(tmp_path, "Seraphine")
    png = _picture()
    blob = _bundle([_entry("happy", "assets/a.png"), _entry("sad", "assets/b.png")],
                   {"assets/a.png": png, "assets/b.png": png})
    real = assets.link_if_vacant
    calls, new = [], {}

    def save_then_fail(d, name, image_id, **kwargs):
        calls.append(name)
        if len(calls) == 1:
            placed = real(d, name, image_id, **kwargs)
            # Another request saves the version this import has just created.
            nvid = d.name
            card = ch.read_card(tmp_path, cid, nvid)
            card["data"]["description"] = "saved meanwhile"
            ch.update_version(tmp_path, cid, nvid, card)
            new["vid"] = nvid
            return placed
        raise OSError("disk full")

    monkeypatch.setattr(assets, "link_if_vacant", save_then_fail)

    with pytest.raises(OSError):
        ch.import_card(tmp_path, blob, "charx", into_cid=cid, version_name="later")

    assert ch.read_card(tmp_path, cid, new["vid"])["data"]["description"] == "saved meanwhile"


def test_sidecar_stems_match_the_sidecar_files():
    expected = {image_refs.fold(Path(f).stem)
                for f in (assets.FOCUS_FILE, assets.DESCRIPTIONS_FILE, image_subjects.SUBJECTS_FILE)}
    assert expected == ch._SIDECAR_STEMS


@pytest.mark.parametrize("name", ["focus", "descriptions", "subjects", "Focus"])
def test_charx_import_skips_names_reserved_for_sidecars(tmp_path, name):
    blob = _bundle([_entry(name, "assets/happy.png"), _entry("happy", "assets/happy.png")],
                   {"assets/happy.png": _picture()})

    cid, _vid = ch.import_card(tmp_path, blob, "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["happy"]


@pytest.mark.parametrize("name", ["embed-0123456789ab", "Embed-x"])
def test_charx_import_skips_names_reserved_for_localized_images(tmp_path, name):
    blob = _bundle([_entry(name, "assets/happy.png"), _entry("happy", "assets/happy.png")],
                   {"assets/happy.png": _picture()})

    cid, _vid = ch.import_card(tmp_path, blob, "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["happy"]


def test_a_case_variant_of_an_existing_picture_counts_as_taken(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    png, other = _picture(), _picture("png_onepixel.png")
    assets.put_image(tmp_path, cid, vid, "Happy", png, "png")
    blob = _bundle([_entry("happy", "assets/a.png")], {"assets/a.png": other})

    ch.import_card(tmp_path, blob, "charx", into_cid=cid, update_vid=vid)

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["Happy"]
    assert assets.slot_taken(assets.version_dir(tmp_path, cid, vid), "HAPPY")


def test_names_one_slot_under_the_store_fold_stage_once(tmp_path, monkeypatch):
    # `i` and dotless i (U+0131) are one file on NTFS; staging must not ingest both.
    png, other = _picture(), _picture("png_onepixel.png")
    blob = _bundle([_entry("i", "assets/a.png"), _entry("\u0131", "assets/b.png")],
                   {"assets/a.png": png, "assets/b.png": other})
    ingested = []
    real = image_store.ingest

    def counting(data, ext, **kwargs):
        ingested.append(data)
        return real(data, ext, **kwargs)

    monkeypatch.setattr(image_store, "ingest", counting)

    cid, _vid = ch.import_card(tmp_path, blob, "charx")

    assert ingested == [png]
    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["i"]


def test_gallery_walk_stops_at_the_candidate_cap(tmp_path, monkeypatch):
    png = _picture()
    entries = [_entry(f"missing_{i}", f"assets/missing_{i}.png") for i in range(5)]
    entries.append(_entry("happy", "assets/happy.png"))
    monkeypatch.setattr(character_bundled, "_MAX_BUNDLED_CANDIDATES", 3)

    cid, _vid = ch.import_card(tmp_path, _bundle(entries, {"assets/happy.png": png}), "charx")

    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == []


def test_archive_parses_do_not_grow_with_gallery_entries(tmp_path, monkeypatch):
    real = zipfile.ZipFile
    parses = []

    class Counting(real):
        def __init__(self, *args, **kwargs):
            parses.append(1)
            super().__init__(*args, **kwargs)

    def parses_for(n):
        png = _picture()
        entries = [_entry(f"e{i}", f"assets/{i}.png") for i in range(n)]
        blob = _bundle(entries, {f"assets/{i}.png": png for i in range(n)})
        parses.clear()
        monkeypatch.setattr(zipfile, "ZipFile", Counting)
        try:
            ch.import_card(tmp_path / f"w{n}", blob, "charx")
        finally:
            monkeypatch.setattr(zipfile, "ZipFile", real)
        return len(parses)

    assert parses_for(3) == parses_for(20)


def test_names_one_slot_share_one_name_lock(tmp_path):
    assert locks._image_stripe(tmp_path, "i") == locks._image_stripe(tmp_path, "\u0131")
    assert locks._image_stripe(tmp_path, "Happy") == locks._image_stripe(tmp_path, "happy")


def test_gallery_badge_counts_imported_named_art(tmp_path):
    png = _picture()
    blob = _bundle([_entry("main", "assets/a.png", "icon"), _entry("happy", "assets/a.png"),
                    _entry("sad", "assets/a.png")], {"assets/a.png": png})

    cid, _vid = ch.import_card(tmp_path, blob, "charx")

    row = next(r for r in ch.list_characters(tmp_path) if r["id"] == cid)
    assert row["gallery_count"] == 2  # happy and sad; the avatar is not gallery
