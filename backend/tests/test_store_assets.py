"""Blobs, image objects and placements in the native store (Sprint 018): the same guarded transaction.

Covers the additive family ``assets/blobs/<xx>/<sha256>.<ext>``, ``<owner>/images/<id>.json`` and
``<owner>/placements/<kind>/<id>/<slot>.json``: the sniffer, canonical paths, the
``grimoire.asset-record/1`` envelope, create-only images and blobs, blob repair, source and
campaign existence, authorization, links, containment on load and recovery. Synthetic fixtures.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from grimoire.core.identity import ImageRef, InstanceRef, PlacementRef, SourceRef
from grimoire.library import LibraryPathError
from grimoire.store import (
    MAX_BLOB_BYTES, MEDIA, AssetRecord, BlobCorrupt, ImageBlob, ImmutableBlob, ImmutableImage, InvalidBlob,
    InvalidSourceState, LibraryStore, LinkRefused, NameCollision, ProcessLocker, RecordEnvelope, RecordNotFound,
    SourceManifest, StaleWrite, UnstorableIdentifier, WriteDenied, etag, sniff,
)
from grimoire.store.envelope import EnvelopeError, asset_record_document, parse_asset_record
from grimoire.store.layout import address_parts, blob_parts, classify, conflict_parts
from grimoire.testing.fileops import snapshot

from image_fixtures import (
    CARD, GIF, HARBOR, JPEG, MIRA, PNG, PNG_2, SAILOR, WEBP, WREN_A, handle, seed, source_puts,
)
from store_helpers import OWNER, make_dir_link

IMAGE = ImageRef(HARBOR, "wren")
AVATAR = PlacementRef(SAILOR, "avatar")
BLOB = ImageBlob(PNG)


def image_record(ref=IMAGE, blob=BLOB, **extra) -> AssetRecord:
    payload = {"blob": {"sha256": blob.sha256, "media_type": blob.media_type, "size": blob.size}}
    return AssetRecord(ref, "test.image/0", payload, owner=OWNER, extra=extra)


def placement_record(ref=AVATAR, image=IMAGE, **extra) -> AssetRecord:
    return AssetRecord(ref, "test.placement/0", {"mode": "set", "image": str(image)}, extra=extra)


@pytest.fixture
def library(tmp_path):
    store = LibraryStore.create(tmp_path / "lib")
    seed(store)
    yield store
    store.close()


def _commit(store, *puts, auth=None):
    txn = store.transaction()
    for envelope, expected in puts:
        txn.put(envelope, expected)
    return txn.commit(auth or handle())


def _write(root: Path, relative: str, content) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())


def _blob_path(store, blob=BLOB) -> Path:
    return store.root.joinpath(*blob_parts(blob.sha256, blob.ext))


# -- sniffer and blob value ------------------------------------------------------------------

@pytest.mark.parametrize("data, ext", [(PNG, "png"), (JPEG, "jpg"), (GIF, "gif"), (WEBP, "webp"),
                                       (b"GIF87a...", "gif")])
def test_sniff_decides_the_type_from_magic_bytes(data, ext):
    assert sniff(data) == ext
    assert ImageBlob(data).media_type == MEDIA[ext]


@pytest.mark.parametrize("data", [b"<svg/>", b"RIFF\x00\x00\x00\x00WAVE", b"\x89PNX", b"BM bitmap", b"%PDF-1.7"])
def test_sniff_refuses_everything_else(data):
    assert sniff(data) is None


def test_image_blob_is_content_addressed_and_bounded():
    blob = ImageBlob(PNG)
    assert blob.sha256 == hashlib.sha256(PNG).hexdigest() and blob.etag == "sha256:" + blob.sha256
    assert blob.size == len(PNG) and blob.ext == "png"
    for data, reason in ((b"", "empty"), (b"plain text", "unsupported_media"),
                         (b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_BLOB_BYTES, "too_large")):
        with pytest.raises(InvalidBlob) as caught:
            ImageBlob(data)
        assert caught.value.reason == reason


# -- layout ----------------------------------------------------------------------------------

@pytest.mark.parametrize("address", [
    IMAGE, ImageRef("campaign:c1", "scarred"), AVATAR, PlacementRef(WREN_A, "expr.default.smile"),
    ImageRef("world:harbor:dlc", "x"), PlacementRef(SourceRef("world:harbor:dlc", "item", "oar"), "gallery_1"),
])
def test_asset_paths_classify_exactly(address):
    found = classify(address_parts(address))
    assert found is not None and found[1] == address


def test_blob_paths_classify_exactly():
    parts = blob_parts(BLOB.sha256, "png")
    assert parts == ("assets", "blobs", BLOB.sha256[:2], f"{BLOB.sha256}.png")
    assert classify(parts) == ("blob", (BLOB.sha256, "png"))


@pytest.mark.parametrize("parts", [
    ("assets", "blobs", "zz", "a" * 64 + ".png"),           # wrong shard
    ("assets", "blobs", "AA", "A" * 64 + ".png"),           # uppercase hex
    ("assets", "blobs", "aa", "a" * 64 + ".jpeg"),          # not the canonical ext
    ("assets", "blobs", "aa", "a" * 63 + ".png"),           # short digest
    ("assets", "blobs", "aa", "x", "a" * 64 + ".png"),      # extra depth
    ("assets", "aa", "a" * 64 + ".png"),
    ("sources", "world", "harbor", "_source", "images", "wren"),          # no .json
    ("sources", "world", "harbor", "_source", "images", "_x.json"),
    ("sources", "world", "harbor", "_source", "images", "a", "b.json"),
    ("sources", "world", "harbor", "_source", "placements", "character", "wren", "x", "avatar.json"),
    ("campaigns", "c1", "images", "Wren.json"),
    ("campaigns", "c1", "placements", "character", "avatar.json"),
])
def test_look_alike_asset_paths_are_not_records(parts):
    assert classify(parts) is None


def test_unstorable_asset_identifiers_are_refused():
    from grimoire.store import UnstorableIdentifier as Unstorable
    with pytest.raises(Unstorable):
        address_parts(PlacementRef(SAILOR, "con"))  # a Windows device name
    with pytest.raises(Unstorable):
        address_parts(ImageRef(HARBOR, "aux"))


def test_the_blob_conflict_path_fits_the_windows_budget():
    # .conflicts/<16>/<64 hex>.webp~<12> is the longest path a blob commit checks under the root.
    worst = conflict_parts(blob_parts("f" * 64, "webp"), "sha256:" + "0" * 64)
    assert len("/".join(worst)) < 160


# -- envelope --------------------------------------------------------------------------------

def test_the_asset_envelope_keeps_unknown_fields():
    record = placement_record(**{"x-note": {"by": "hand"}})
    document = asset_record_document(record, None)
    assert document["format"] == "grimoire.asset-record/1" and document["x-note"] == {"by": "hand"}
    assert parse_asset_record(document) == (record, None)


@pytest.mark.parametrize("document, code", [
    ({"format": "grimoire.asset-record/1", "address": "world:harbor/character/x", "schema": "s", "payload": 1},
     "malformed"),
    ({"format": "grimoire.asset-record/1", "address": str(IMAGE), "schema": "", "payload": 1}, "malformed"),
    ({"format": "grimoire.asset-record/1", "address": str(IMAGE), "schema": "s"}, "malformed"),
    ({"format": "grimoire.asset-record/9", "address": str(IMAGE), "schema": "s", "payload": 1},
     "unsupported_format"),
])
def test_strict_asset_parsing_names_the_problem(document, code):
    with pytest.raises(EnvelopeError) as caught:
        parse_asset_record(document)
    assert caught.value.code == code


# -- writing ---------------------------------------------------------------------------------

def test_blob_image_and_placement_commit_with_definitions_and_read_back(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        _commit(store, *source_puts(), (BLOB, None), (image_record(), None), (placement_record(), None))
    with LibraryStore.open(tmp_path / "lib") as store:
        assert store.read(IMAGE).record == image_record()
        assert store.read(AVATAR).record == placement_record()
        assert store.read_blob(BLOB.sha256, "png") == PNG
        assert store.blob_stat(BLOB.sha256, "png") == ("present", len(PNG))
        assert store.blob_etag(BLOB.sha256, "png") == BLOB.etag
        assert store.list_images(HARBOR) == ((IMAGE,), ())
        assert store.list_placements(SAILOR) == ((AVATAR,), ())
        assert store.load().problems == ()


def test_images_and_blobs_are_create_only(library):
    _commit(library, (BLOB, None), (image_record(), None))
    current = library.read(IMAGE).etag
    with pytest.raises(ImmutableImage):
        library.transaction().put(image_record(), current)
    with pytest.raises(StaleWrite):
        _commit(library, (image_record(), None))
    with pytest.raises(ImmutableBlob):
        library.transaction().put(BLOB, BLOB.etag)
    with pytest.raises(StaleWrite):
        _commit(library, (BLOB, None))


def test_a_corrupt_blob_is_detected_and_repaired_only_by_cas(library):
    _commit(library, (BLOB, None))
    _blob_path(library).write_bytes(PNG_2)  # bit rot, or the wrong file dropped in by hand
    with pytest.raises(BlobCorrupt) as caught:
        library.read_blob(BLOB.sha256, "png")
    actual = caught.value.actual
    assert actual == etag(PNG_2) == library.blob_etag(BLOB.sha256, "png")
    with pytest.raises(StaleWrite):
        _commit(library, (BLOB, "sha256:" + "0" * 64))
    _commit(library, (BLOB, actual))
    assert library.read_blob(BLOB.sha256, "png") == PNG


def test_an_oversize_blob_file_is_corrupt_and_never_read(library):
    _commit(library, (BLOB, None))
    with _blob_path(library).open("wb") as handle_:
        handle_.truncate(MAX_BLOB_BYTES + 1)
    assert library.blob_stat(BLOB.sha256, "png") == ("present", MAX_BLOB_BYTES + 1)
    with pytest.raises(BlobCorrupt) as caught:
        library.blob_etag(BLOB.sha256, "png")
    assert caught.value.actual is None
    with pytest.raises(BlobCorrupt):
        library.read_blob(BLOB.sha256, "png")


def test_source_assets_need_an_admitted_manifest(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        with pytest.raises(InvalidSourceState):
            _commit(store, (BLOB, None), (image_record(), None))
        _commit(store, (BLOB, None), (SourceManifest(HARBOR, OWNER), None), (image_record(), None))
        assert store.read(IMAGE).record == image_record()


def test_campaign_assets_need_a_campaign(library):
    scarred = ImageRef("campaign:c9", "scarred")
    with pytest.raises(InvalidSourceState):
        _commit(library, (BLOB, None), (image_record(scarred), None))
    _write(library.root, "campaigns/c9/stray.txt", b"not a campaign")
    _write(library.root, "campaigns/c9/scope.json/inside.json", b"{}")  # a folder is not a scope file
    with pytest.raises(InvalidSourceState):
        _commit(library, (BLOB, None), (image_record(scarred), None))
    instance = InstanceRef("c9", "character", "w")
    _commit(library, (BLOB, None), (RecordEnvelope(instance, {}), None), (image_record(scarred), None))
    assert library.campaign_exists("c9")
    # an existing campaign (seeded c1) needs nothing more
    _commit(library, (image_record(ImageRef("campaign:c1", "x")), None))


def test_a_campaign_holding_only_assets_does_not_exist(library):
    assert library.campaign_exists("c1") and not library.campaign_exists("c7")
    _write(library.root, "campaigns/c7/images/x.json", b"{}")
    assert not library.campaign_exists("c7")


def test_a_newer_placement_format_is_never_overwritten(library):
    _commit(library, (BLOB, None), (image_record(), None), (placement_record(), None))
    path = library.root.joinpath(*address_parts(AVATAR))
    newer = {"format": "grimoire.asset-record/9", "address": str(AVATAR), "schema": "s", "payload": {}}
    path.write_bytes(json.dumps(newer).encode())
    with pytest.raises(StaleWrite, match="unsupported_format"):
        _commit(library, (placement_record(), etag(path.read_bytes())))


def test_unknown_placement_fields_survive_a_cas_rewrite(library):
    _commit(library, (BLOB, None), (image_record(), None), (placement_record(**{"x-a": 1}), None))
    stored = library.read(AVATAR)
    _commit(library, (stored.record, stored.etag))
    assert library.read(AVATAR).record.extra == {"x-a": 1}


def test_blob_writes_are_authorized_and_denial_writes_nothing(library):
    before = snapshot(library.root)
    with pytest.raises(WriteDenied):
        _commit(library, (BLOB, None), (image_record(), None), auth=handle("sources", "campaigns"))
    assert snapshot(library.root) == before
    _commit(library, (BLOB, None), (image_record(), None), auth=handle("sources/world/harbor", "assets/blobs"))


def test_a_look_alike_blob_name_is_a_collision(library):
    _write(library.root, f"assets/blobs/{BLOB.sha256[:2]}/{BLOB.sha256.upper()}.png", PNG)
    if not (library.root / "assets/blobs" / BLOB.sha256[:2] / f"{BLOB.sha256}.png").exists():
        with pytest.raises(NameCollision):  # case-sensitive filesystem: an exact name never matches
            _commit(library, (BLOB, None))
        assert library.blob_stat(BLOB.sha256, "png") == ("lookalike", None)
        with pytest.raises(NameCollision):
            library.blob_etag(BLOB.sha256, "png")


def _v1_like(path: Path) -> Path:
    (path / "campaigns" / "c1" / "instances").mkdir(parents=True)
    (path / "worlds").mkdir()
    (path / "regex.json").write_text("[]")
    return path


@pytest.mark.parametrize("linked", ["assets", "assets/blobs", "shard"])
def test_linked_asset_folders_are_never_written_or_read(library, tmp_path, linked):
    v1 = _v1_like(tmp_path / "old")
    shard = f"assets/blobs/{BLOB.sha256[:2]}"
    where = shard if linked == "shard" else linked
    (library.root / where).parent.mkdir(parents=True, exist_ok=True)
    make_dir_link(library.root / where, v1)
    before = snapshot(v1)
    with pytest.raises(LibraryPathError):  # LinkRefused, or V1 isolation where the link resolves
        _commit(library, (BLOB, None))
    with pytest.raises(LinkRefused):
        library.blob_stat(BLOB.sha256, "png")
    with pytest.raises(LinkRefused):
        library.read_blob(BLOB.sha256, "png")
    assert snapshot(v1) == before


def test_a_non_regular_blob_entry_is_refused(library):
    (library.root / f"assets/blobs/{BLOB.sha256[:2]}/{BLOB.sha256}.png").mkdir(parents=True)
    with pytest.raises(UnstorableIdentifier):
        library.blob_stat(BLOB.sha256, "png")


def test_missing_assets_are_not_found(library):
    with pytest.raises(RecordNotFound):
        library.read(IMAGE)
    with pytest.raises(RecordNotFound):
        library.read_blob(BLOB.sha256, "png")
    assert library.blob_stat(BLOB.sha256, "png") == ("absent", None)
    assert library.blob_etag(BLOB.sha256, "png") is None
    assert library.list_images("campaign:c1") == ((), ())
    assert library.list_placements(MIRA) == ((), ())


# -- loading ---------------------------------------------------------------------------------

def test_asset_folders_load_without_problems_and_strangers_are_reported(library):
    _commit(library, (BLOB, None), (image_record(), None), (placement_record(), None),
            (image_record(ImageRef("campaign:c1", "x")), None),
            (placement_record(PlacementRef(WREN_A, "avatar"), ImageRef("campaign:c1", "x")), None))
    assert library.load().problems == ()
    root = library.root
    base = "sources/world/harbor/_source"
    _write(root, f"{base}/images/Ghost.json", b"{}")
    _write(root, f"{base}/images/sub/x.json", b"{}")
    _write(root, f"{base}/placements/character/wren-sailor/sub/x.json", b"{}")
    _write(root, f"{base}/placements/character/avatar.json", b"{}")
    _write(root, "sources/card/mira/_source/placements", b"a file, not a folder")
    _write(root, "campaigns/c1/images/notes.txt", b"x")
    codes = {(p.path, p.code) for p in library.load().problems}
    assert codes == {
        (f"{base}/images/Ghost.json", "name_mismatch"),
        (f"{base}/images/sub", "unrecognized"),
        (f"{base}/placements/character/wren-sailor/sub", "unrecognized"),
        (f"{base}/placements/character/avatar.json", "unrecognized"),
        ("sources/card/mira/_source/placements", "unrecognized"),
        ("campaigns/c1/images/notes.txt", "unrecognized"),
    }
    refs, problems = library.list_images(HARBOR)
    assert refs == (IMAGE,)
    assert {(p.path, p.code) for p in problems} == {(f"{base}/images/Ghost.json", "name_mismatch"),
                                                    (f"{base}/images/sub", "unrecognized")}
    refs, problems = library.list_placements(SAILOR)
    assert refs == (AVATAR,) and [p.code for p in problems] == ["unrecognized"]


def test_asset_folders_do_not_change_what_load_admits(library):
    before = library.load()
    _commit(library, (BLOB, None), (image_record(), None), (placement_record(), None))
    after = library.load()
    assert after.sources == before.sources and after.instances == before.instances
    assert after.excluded == before.excluded and after.dropped == before.dropped
    assert library.check_definition(SAILOR).ref == SAILOR.at("r1")


def test_a_library_without_assets_is_unchanged(library):
    assert library.load().problems == ()
    assert not (library.root / "assets").exists()


def test_a_journal_naming_asset_targets_recovers(tmp_path):
    from grimoire.testing import Crash, RootedFileOps, Target
    root = tmp_path / "root"
    root.mkdir()
    locker = ProcessLocker()
    with LibraryStore.create(root / "lib", locker=locker) as store:
        seed(store)
    ops = RootedFileOps(root, plan=[Crash(Target("replace", target="lib/.txn/journal.json"), "after")],
                        record=False)
    with pytest.raises(BaseException):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
            _commit(store, (BLOB, None), (image_record(), None), (placement_record(), None))
    with LibraryStore.open(root / "lib", locker=ProcessLocker()) as store:
        assert len(store.last_recovery.published) == 3
        assert store.read(AVATAR).record == placement_record()
        assert store.read_blob(BLOB.sha256, "png") == PNG
