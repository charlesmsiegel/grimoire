"""The content-addressed blob and object store (`store/image_store.py`).

Every image here is generated with Pillow from arithmetic -- nothing is a real
picture -- and every URL is under `example.invalid`.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import time

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from grimoire.store import image_hash, image_store, locks, paths


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _img(seed: int = 0, size=(16, 12)) -> Image.Image:
    im = Image.new("RGB", size)
    im.putdata([((x * 9 + seed) % 256, (y * 17 + seed) % 256, (x * y) % 256)
                for y in range(size[1]) for x in range(size[0])])
    return im


def _png(im: Image.Image, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", **kw)
    return buf.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files() -> list[str]:
    root = image_store.store_root()
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def _blobs() -> list:
    return sorted((image_store.store_root() / "blobs").rglob("*.*"))


def _sidecar(image_id: str) -> dict:
    return json.loads(image_store.object_path(image_id).read_text(encoding="utf-8"))


def _index_dir():
    return paths.home() / ".cache" / "image-store" / "blob-index"


def test_first_ingest_writes_blob_and_object():
    obj = image_store.ingest(_png(_img()), "png")
    blob = image_store.blob_path(obj.blob_sha256, "png")
    assert blob.is_file()
    assert _sha(blob.read_bytes()) == obj.blob_sha256 == blob.stem
    assert blob.parent.name == obj.blob_sha256[:2]
    assert image_store.read(obj.id) == obj
    raw = _sidecar(obj.id)
    assert set(raw) == {"format", "id", "identity", "blob"}
    assert raw["format"] == 1 and raw["id"] == obj.id and raw["identity"] == "pixels"
    assert raw["blob"] == {"sha256": obj.blob_sha256, "ext": "png", "mime": "image/png",
                           "size": blob.stat().st_size, "width": 16, "height": 12,
                           "animated": False}
    assert image_store.object_path(obj.id).read_text(encoding="utf-8") == (
        json.dumps(raw, indent=2, sort_keys=True) + "\n")
    assert (obj.ext, obj.mime, obj.width, obj.height, obj.animated) == (
        "png", "image/png", 16, 12, False)


def test_exact_duplicate_creates_nothing():
    data = _png(_img())
    first = image_store.ingest(data, "png")
    before = _files()
    again = image_store.ingest(data, "png")
    assert again == first
    assert _files() == before


def test_pixel_duplicate_keeps_first_blob():
    a = _png(_img(), dpi=(72, 72))
    b = _png(_img(), dpi=(300, 300))
    first = image_store.ingest(a, "png")
    second = image_store.ingest(b, "png")
    assert _sha(a) != _sha(b)
    assert second.id == first.id
    assert second.blob_sha256 == first.blob_sha256
    assert len(_blobs()) == 1


def test_different_pixels_new_object():
    first = image_store.ingest(_png(_img(0)), "png")
    second = image_store.ingest(_png(_img(1)), "png")
    assert first.id != second.id
    assert first.blob_sha256 != second.blob_sha256
    assert len(_blobs()) == 2
    assert image_store.read(first.id) == first and image_store.read(second.id) == second


def test_sanitized_bytes_are_stored():
    info = PngInfo()
    info.add_text("chara", "eyJuYW1lIjogIk1hcmEifQ==")
    data = _png(_img(), pnginfo=info)
    assert b"chara" in data
    obj = image_store.ingest(data, "png")
    stored = image_store.blob_path(obj.blob_sha256, obj.ext).read_bytes()
    assert b"chara" not in stored
    assert obj.blob_sha256 == _sha(stored) != _sha(data)


def test_unsniffable_bytes_take_callers_ext_and_opaque_identity():
    obj = image_store.ingest(b"a", "png")
    assert obj.ext == "png" and obj.identity == "bytes"
    assert image_store.blob_path(obj.blob_sha256, "png").read_bytes() == b"a"
    raw = _sidecar(obj.id)
    assert raw["reason"] == "undecodable"
    assert "width" not in raw["blob"] and "height" not in raw["blob"]
    assert obj.width is None and obj.height is None


def test_sniffed_format_beats_callers_ext():
    obj = image_store.ingest(_png(_img()), "gif")
    assert obj.ext == "png"


def test_sources_merge_deduplicated():
    data = _png(_img())
    image_store.ingest(data, "png", source_url="https://example.invalid/a#frag")
    image_store.ingest(data, "png", source_url="https://example.invalid/a#frag")
    obj = image_store.ingest(data, "png", source_url="https://example.invalid/b")
    assert obj.raw["sources"] == [{"url": "https://example.invalid/a"},
                                  {"url": "https://example.invalid/b"}]
    assert image_store.read(obj.id).raw["sources"] == obj.raw["sources"]


def test_sources_ignore_non_http_and_cap_at_twenty():
    data = _png(_img())
    image_store.ingest(data, "png", source_url="file:///etc/passwd")
    image_store.ingest(data, "png", source_url="data:image/png;base64,AAAA")
    obj = image_store.ingest(data, "png", source_url="")
    assert "sources" not in obj.raw
    for i in range(25):
        obj = image_store.ingest(data, "png", source_url=f"http://example.invalid/{i}")
    assert [s["url"] for s in obj.raw["sources"]] == [
        f"http://example.invalid/{i}" for i in range(20)]


def test_index_hit_validated():
    data = _png(_img())
    first = image_store.ingest(data, "png")
    image_store.object_path(first.id).unlink()
    again = image_store.ingest(data, "png")
    assert image_store.object_path(again.id).is_file()
    assert again == first
    assert image_store.read(first.id) == first


def test_missing_retained_blob_adopted():
    a = _png(_img(), dpi=(72, 72))
    b = _png(_img(), dpi=(300, 300))
    first = image_store.ingest(a, "png")
    image_store.blob_path(first.blob_sha256, "png").unlink()
    obj = image_store.ingest(b, "png")
    assert obj.id == first.id
    assert image_store.read(first.id).blob_sha256 == _sha(b) == obj.blob_sha256
    assert image_store.blob_path(_sha(b), "png").read_bytes() == b


def test_missing_blob_restored_on_exact_hit():
    data = _png(_img())
    first = image_store.ingest(data, "png")
    blob = image_store.blob_path(first.blob_sha256, "png")
    blob.unlink()
    assert image_store.ingest(data, "png") == first
    assert blob.read_bytes() == data


def test_corrupt_blob_repaired(monkeypatch):
    data = _png(_img())
    first = image_store.ingest(data, "png")
    blob = image_store.blob_path(first.blob_sha256, "png")
    blob.write_bytes(b"junk")
    rows = []
    monkeypatch.setattr(image_store.logs, "record",
                        lambda *a, **k: rows.append((a, k)))
    assert image_store.ingest(data, "png") == first
    assert blob.read_bytes() == data
    assert len(rows) == 1


def test_same_size_corrupt_blob_repaired():
    data = _png(_img())
    first = image_store.ingest(data, "png")
    blob = image_store.blob_path(first.blob_sha256, "png")
    blob.write_bytes(bytes(len(data)))
    image_store.ingest(data, "png")
    assert blob.read_bytes() == data


def test_index_rebuilt_after_deletion(monkeypatch):
    data = _png(_img())
    first = image_store.ingest(data, "png")
    shutil.rmtree(_index_dir())
    assert image_store.rebuild_index() == 1

    def boom(*a, **k):
        raise AssertionError("decoded on an exact-byte hit")

    monkeypatch.setattr(image_hash, "pixel_identity", boom)
    assert image_store.ingest(data, "png") == first


def test_absent_index_rebuilt_on_lookup(monkeypatch):
    data = _png(_img())
    first = image_store.ingest(data, "png")
    shutil.rmtree(_index_dir())
    monkeypatch.setattr(image_hash, "pixel_identity",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("decoded")))
    assert image_store.ingest(data, "png") == first
    assert (_index_dir() / first.blob_sha256[:2] / first.blob_sha256).read_text(
        encoding="utf-8").strip() == first.id


def test_stale_index_entry_falls_through():
    data = _png(_img(0))
    other = image_store.ingest(_png(_img(1)), "png")
    obj = image_store.ingest(data, "png")
    entry = _index_dir() / obj.blob_sha256[:2] / obj.blob_sha256
    entry.write_text(other.id, encoding="utf-8")       # names the wrong object
    assert image_store.ingest(data, "png") == obj
    assert entry.read_text(encoding="utf-8").strip() == obj.id


def test_jpeg_ext_normalized():
    assert image_store.ingest(b"a", "jpeg").ext == "jpg"
    assert image_store.ingest(b"b", ".JPEG").ext == "jpg"


def test_unsupported_ext_for_unsniffable_bytes_rejected():
    with pytest.raises(ValueError):
        image_store.ingest(b"a", "svg")


def test_ingest_touches_sidecar_mtime():
    data = _png(_img())
    obj = image_store.ingest(data, "png")
    side = image_store.object_path(obj.id)
    os.utime(side, (0, 0))
    image_store.ingest(data, "png")
    assert side.stat().st_mtime > time.time() - 60


def test_ingest_touches_sidecar_on_pixel_duplicate():
    obj = image_store.ingest(_png(_img(), dpi=(72, 72)), "png")
    side = image_store.object_path(obj.id)
    os.utime(side, (0, 0))
    image_store.ingest(_png(_img(), dpi=(300, 300)), "png")
    assert side.stat().st_mtime > time.time() - 60


def test_sanitize_false_stores_bytes_verbatim():
    info = PngInfo()
    info.add_text("Comment", "kept")
    data = _png(_img(), pnginfo=info)
    obj = image_store.ingest(data, "png", sanitize=False)
    assert obj.blob_sha256 == _sha(data)
    assert image_store.blob_path(obj.blob_sha256, "png").read_bytes() == data


def test_blob_sha_of(tmp_path):
    obj = image_store.ingest(_png(_img()), "png")
    p = image_store.blob_path(obj.blob_sha256, "png")
    assert image_store.blob_sha_of(p) == obj.blob_sha256
    sha = obj.blob_sha256
    lookalike = tmp_path / "elsewhere" / "assets" / "image-store" / "blobs" / sha[:2] / f"{sha}.png"
    assert image_store.blob_sha_of(lookalike) is None
    assert image_store.blob_sha_of(p.parent.parent / "zz" / p.name) is None
    assert image_store.blob_sha_of(p.with_suffix(".svg")) is None
    assert image_store.blob_sha_of(p.with_name(f"{sha}")) is None
    assert image_store.blob_sha_of(image_store.object_path(obj.id)) is None
    assert image_store.blob_sha_of(tmp_path / "characters" / "a.png") is None


def test_blob_sha_of_does_not_stat(monkeypatch):
    sha = "ab" * 32
    p = image_store.blob_path(sha, "webp")
    assert not p.exists()
    assert image_store.blob_sha_of(p) == sha


def test_update_read_modify_write():
    obj = image_store.ingest(_png(_img()), "png")

    def add(raw):
        raw["description"] = "A plain test card."
        return raw

    image_store.update(obj.id, add)
    assert image_store.read(obj.id).raw["description"] == "A plain test card."
    before = image_store.object_path(obj.id).stat().st_mtime_ns
    os.utime(image_store.object_path(obj.id), ns=(1, 1))
    image_store.update(obj.id, lambda raw: None)
    assert image_store.object_path(obj.id).stat().st_mtime_ns == 1 != before


def test_update_does_not_mutate_cached_raw():
    obj = image_store.ingest(_png(_img()), "png")

    def mangle(raw):
        raw["blob"]["size"] = -1

    image_store.update(obj.id, mangle)
    assert image_store.read(obj.id) == obj
    assert obj.raw["blob"]["size"] > 0


def test_update_rejects_bad_id_and_unreadable_result():
    with pytest.raises(ValueError):
        image_store.update("px1-+f", lambda raw: raw)
    obj = image_store.ingest(_png(_img()), "png")
    with pytest.raises(ValueError):
        image_store.update(obj.id, lambda raw: {**raw, "id": "px1-" + "1" * 64})
    assert image_store.read(obj.id) == obj


def test_update_missing_object_is_noop():
    calls = []
    image_store.update("px1-" + "0" * 64, calls.append)
    assert calls == []


def test_project_filters_scope():
    raw = {
        "format": 1, "id": "px1-" + "a" * 64, "identity": "pixels",
        "blob": {"sha256": "b" * 64, "ext": "png", "mime": "image/png", "size": 3,
                 "animated": False},
        "description": "Seraphine by the arch.",
        "associations": [
            {"kind": "character", "relation": "subject", "scope": "world:realm",
             "id": "seraphine"},
            {"kind": "character", "relation": "subject", "scope": "campaign:saltmarch",
             "id": "mara"},
        ],
        "reviews": {"subjects": ["world:realm", "campaign:saltmarch"]},
        "sources": [{"url": "https://example.invalid/a"}],
        "description_conflicts": [{"scope": "campaign:saltmarch", "text": "x"}],
    }
    out = image_store.project(raw, "world:realm")
    assert "sources" not in out and "description_conflicts" not in out
    assert out["associations"] == [raw["associations"][0]]
    assert out["reviews"] == {"subjects": ["world:realm"]}
    assert out["description"] == raw["description"] and out["blob"] == raw["blob"]
    assert raw["reviews"]["subjects"] == ["world:realm", "campaign:saltmarch"]
    assert len(raw["associations"]) == 2
    bare = image_store.project({"format": 1, "id": raw["id"]}, "world:realm")
    assert bare == {"format": 1, "id": raw["id"]}


def test_bad_ids_rejected():
    with pytest.raises(ValueError):
        image_store.object_path("px1-zz")
    with pytest.raises(ValueError):
        image_store.blob_path("../x", "png")
    with pytest.raises(ValueError):
        image_store.blob_path("a" * 64, "svg")
    assert image_store.read("nope") is None


@pytest.mark.parametrize("text", [
    "not json",
    "[]",
    '{"format": 2}',
])
def test_garbled_sidecar_reads_none(text):
    obj = image_store.ingest(_png(_img()), "png")
    image_store.object_path(obj.id).write_text(text, encoding="utf-8")
    assert image_store.read(obj.id) is None


def test_sidecar_naming_another_id_reads_none():
    a = image_store.ingest(_png(_img(0)), "png")
    b = image_store.ingest(_png(_img(1)), "png")
    image_store.object_path(a.id).write_text(
        image_store.object_path(b.id).read_text(encoding="utf-8"), encoding="utf-8")
    assert image_store.read(a.id) is None


def test_store_roots_do_not_share_index(tmp_path, monkeypatch):
    data = _png(_img())
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "one"))
    first = image_store.ingest(data, "png")
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "two"))
    assert image_store.read(first.id) is None
    second = image_store.ingest(data, "png")
    assert second.id == first.id
    assert image_store.blob_path(second.blob_sha256, "png").is_file()


def test_image_locks_are_keyed_per_store(tmp_path, monkeypatch):
    image_id = "px1-" + "c4" + "0" * 62
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "one"))
    a = locks.image_object_lock(image_id)
    g1 = locks.image_ingest_gc_lock()
    assert locks.image_object_lock(image_id) is a
    assert locks.image_object_lock("px1-" + "84" + "0" * 62) is a     # 0xc4 % 64 == 0x84 % 64
    assert locks.image_object_lock("px1-" + "c5" + "0" * 62) is not a
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "two"))
    assert locks.image_object_lock(image_id) is not a
    assert locks.image_ingest_gc_lock() is not g1
    with locks.image_ingest_gc_lock(), locks.image_object_lock(image_id):
        pass
