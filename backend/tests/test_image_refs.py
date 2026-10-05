"""Placement records (`store/image_refs.py`): `<d>/image-refs/<name>.json`.

Every image is generated with Pillow from arithmetic; names are invented.
"""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from grimoire.store import assets, image_refs, image_store


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _png(seed: int = 0) -> bytes:
    im = Image.new("RGB", (8, 6))
    im.putdata([((x * 9 + seed) % 256, (y * 17 + seed) % 256, (x * y) % 256)
                for y in range(6) for x in range(8)])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _obj(seed: int = 0):
    return image_store.ingest(_png(seed), "png")


def _raw(d, name, text):
    p = image_refs.ref_path(d, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_constants():
    assert image_refs.REFS_DIR == "image-refs"
    assert image_refs.JOURNAL == ".promote.json"


def test_round_trip_read_and_scan(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "avatar", o.id)
    assert image_refs.read(d, "avatar") == image_refs.Ref("avatar", o.id, None)
    assert image_refs.scan(d) == {"avatar": image_refs.Ref("avatar", o.id, None)}
    assert image_refs.ref_path(d, "avatar") == d / "image-refs" / "avatar.json"


def test_file_content_is_exact_and_deterministic(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "avatar", o.id)
    expect = json.dumps({"format": 1, "image": o.id}, sort_keys=True) + "\n"
    assert image_refs.ref_path(d, "avatar").read_text(encoding="utf-8") == expect
    image_refs.write(d, "avatar", o.id, focus=43)
    expect = json.dumps({"format": 1, "image": o.id, "focus": 43}, sort_keys=True) + "\n"
    assert image_refs.ref_path(d, "avatar").read_text(encoding="utf-8") == expect
    image_refs.write(d, "avatar", None, focus=7)
    expect = json.dumps({"format": 1, "focus": 7}, sort_keys=True) + "\n"
    assert image_refs.ref_path(d, "avatar").read_text(encoding="utf-8") == expect


@pytest.mark.parametrize("focus", [True, False, -1, 101, "5", 4.5, None])
def test_bad_focus_reads_as_none_but_ref_is_kept(tmp_path, focus):
    d = tmp_path / "rec"
    o = _obj()
    _raw(d, "avatar", json.dumps({"format": 1, "image": o.id, "focus": focus}))
    assert image_refs.read(d, "avatar") == image_refs.Ref("avatar", o.id, None)


@pytest.mark.parametrize("focus", [0, 50, 100])
def test_focus_bounds_are_kept(tmp_path, focus):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "avatar", o.id, focus=focus)
    assert image_refs.read(d, "avatar").focus == focus


@pytest.mark.parametrize("text", [
    "{not json",
    "[1, 2]",
    '"px1-x"',
    json.dumps({"format": 1, "image": "px1-short"}),
    json.dumps({"format": 1, "image": 5}),
    json.dumps({"format": 2, "image": "px1-" + "0" * 32}),
    json.dumps({"image": "px1-" + "0" * 32}),
    json.dumps({"format": True, "focus": 3}),
    json.dumps({"format": 1}),
    json.dumps({"format": 1, "focus": 500}),
    "",
])
def test_garbled_refs_are_absent(tmp_path, text):
    d = tmp_path / "rec"
    _raw(d, "avatar", text)
    assert image_refs.read(d, "avatar") is None
    assert image_refs.scan(d) == {}
    assert image_refs.image_names(d) == set()


def test_invalid_image_with_valid_focus_is_absent(tmp_path):
    d = tmp_path / "rec"
    _raw(d, "avatar", json.dumps({"format": 1, "image": "px1-short", "focus": 4}))
    assert image_refs.read(d, "avatar") is None


def test_missing_file_reads_none(tmp_path):
    assert image_refs.read(tmp_path / "rec", "avatar") is None


def test_focus_only_ref_is_scanned_but_not_an_image_name(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "avatar", None, focus=40)
    image_refs.write(d, "gallery_1", o.id)
    scan = image_refs.scan(d)
    assert scan["avatar"] == image_refs.Ref("avatar", None, 40)
    assert set(scan) == {"avatar", "gallery_1"}
    assert image_refs.image_names(d) == {"gallery_1"}
    assert image_refs.resolve(d, "avatar") is None


def test_write_none_none_deletes(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "avatar", o.id)
    assert image_refs.ref_path(d, "avatar").exists()
    image_refs.write(d, "avatar", None)
    assert not image_refs.ref_path(d, "avatar").exists()
    image_refs.write(d, "avatar", None)  # missing is fine


def test_write_rejects_invalid_image_and_focus(tmp_path):
    d = tmp_path / "rec"
    with pytest.raises(ValueError):
        image_refs.write(d, "avatar", "px1-short")
    with pytest.raises(ValueError):
        image_refs.write(d, "avatar", None, focus=101)
    with pytest.raises(ValueError):
        image_refs.write(d, "avatar", None, focus=True)  # type: ignore[arg-type]


def test_delete_reports_whether_it_removed(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    assert image_refs.delete(d, "avatar") is False
    image_refs.write(d, "avatar", o.id)
    assert image_refs.delete(d, "avatar") is True
    assert image_refs.delete(d, "avatar") is False
    assert image_refs.delete(d, "../x") is False


@pytest.mark.parametrize("name", ["../x", "a.b", "*", "a?", "a[b]", "", "a/b"])
def test_unsafe_names(tmp_path, name):
    d = tmp_path / "rec"
    o = _obj()
    with pytest.raises(ValueError):
        image_refs.write(d, name, o.id)
    with pytest.raises(ValueError):
        image_refs.ref_path(d, name)
    assert image_refs.read(d, name) is None
    assert image_refs.resolve(d, name) is None


def test_scan_missing_dir_is_empty(tmp_path):
    assert image_refs.scan(tmp_path / "nope") == {}
    assert image_refs.image_names(tmp_path / "nope") == set()


def test_scan_ignores_strays_and_the_journal(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "avatar", o.id)
    image_refs.write_journal(d, {"name": "avatar"})
    refs = d / "image-refs"
    (refs / "notes.txt").write_text("x")
    (refs / "a.b.json").write_text(json.dumps({"format": 1, "image": o.id}))
    (refs / "sub.json").mkdir()
    assert set(image_refs.scan(d)) == {"avatar"}


def test_resolve_returns_store_built_blob_path(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "avatar", o.id, focus=12)
    r = image_refs.resolve(d, "avatar")
    assert r == image_refs.ResolvedImage(
        name="avatar", image_id=o.id, blob_sha256=o.blob_sha256,
        blob_path=image_store.blob_path(o.blob_sha256, o.ext), ext=o.ext,
        mime=o.mime, width=o.width, height=o.height, focus=12)
    assert r.blob_path.is_file()
    assert image_store.blob_sha_of(r.blob_path) == o.blob_sha256


def test_resolve_none_when_object_missing(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "avatar", o.id)
    image_store.object_path(o.id).unlink()
    assert image_refs.resolve(d, "avatar") is None
    # the ref itself is still there
    assert image_refs.read(d, "avatar") is not None


def test_resolve_none_when_blob_missing(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "avatar", o.id)
    image_store.blob_path(o.blob_sha256, o.ext).unlink()
    assert image_refs.resolve(d, "avatar") is None


def test_resolve_none_when_blob_path_is_a_directory(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "avatar", o.id)
    blob = image_store.blob_path(o.blob_sha256, o.ext)
    blob.unlink()
    blob.mkdir()                     # something that exists, but is no file
    assert image_refs.resolve(d, "avatar") is None
    # ...so a legacy file of the name answers, as for any unresolved placement.
    legacy = d / "avatar.png"
    legacy.write_bytes(_png(1))
    assert assets.path_in(d, "avatar") == legacy


def test_resolve_ref_needs_an_image():
    assert image_refs.resolve_ref(image_refs.Ref("avatar", None, 5)) is None


def test_journal_round_trip_and_clear(tmp_path):
    d = tmp_path / "rec"
    assert image_refs.read_journal(d) is None
    image_refs.write_journal(d, {"from": "a", "to": "b", "n": 2})
    assert image_refs.read_journal(d) == {"from": "a", "to": "b", "n": 2}
    assert (d / "image-refs" / ".promote.json").is_file()
    assert image_refs.scan(d) == {}
    image_refs.clear_journal(d)
    assert image_refs.read_journal(d) is None
    image_refs.clear_journal(d)  # missing is fine


@pytest.mark.parametrize("text", ["{bad", "[1]", '"s"', ""])
def test_unreadable_journal_is_none(tmp_path, text):
    d = tmp_path / "rec"
    (d / "image-refs").mkdir(parents=True)
    (d / "image-refs" / ".promote.json").write_text(text)
    assert image_refs.read_journal(d) is None


def test_is_transient(tmp_path):
    d = tmp_path / "rec"
    assert image_refs.is_transient(d / "image-refs" / ".promote.json")
    assert not image_refs.is_transient(d / "image-refs" / "avatar.json")
    assert not image_refs.is_transient(d / "other" / ".promote.json")
    assert not image_refs.is_transient(d / ".promote.json")


def test_deeply_nested_json_is_absent_not_fatal(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "good", o.id)
    _raw(d, "avatar", "[" * 200000)
    (d / "image-refs" / ".promote.json").write_text("[" * 200000)
    assert image_refs.read(d, "avatar") is None
    assert set(image_refs.scan(d)) == {"good"}
    assert image_refs.read_journal(d) is None


def test_float_format_is_rejected(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    _raw(d, "avatar", json.dumps({"format": 1.0, "image": o.id}))
    assert image_refs.read(d, "avatar") is None


def test_files_are_written_as_bytes_with_lf(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    image_refs.write(d, "avatar", o.id)
    image_refs.write_journal(d, {"a": 1})
    for p in (image_refs.ref_path(d, "avatar"), d / "image-refs" / ".promote.json"):
        data = p.read_bytes()
        assert data.endswith(b"}\n") and b"\r" not in data


def test_nul_name_is_unsafe(tmp_path):
    d = tmp_path / "rec"
    o = _obj()
    assert image_refs.delete(d, "a\0b") is False
    assert image_refs.read(d, "a\0b") is None
    assert image_refs.resolve(d, "a\0b") is None
    with pytest.raises(ValueError):
        image_refs.write(d, "a\0b", o.id)


@pytest.mark.parametrize("name", [
    "avatar", "Avatar_1", "gallery-3", "a.b", "*", "a?", "a[b]", "", "..", ".",
    "é-name", "promote-tmp", "Promote-Tmp", "a/b", "../x", "a\0b",
])
def test_valid_name_parity_with_assets(name):
    from grimoire.store import assets
    mine = image_refs._valid_name(name)
    if "\0" in name:
        # assets' `safe_id` admits NUL (a latent gap there, out of scope here);
        # placement names must never reach a path call with one.
        assert mine is False
        return
    assert mine == assets._addressable_name(name)


# ---- walk_ids (world bundle export, spec section 10) ----

def test_walk_ids_finds_every_placed_image_at_any_depth(tmp_path):
    a, b = _obj(1), _obj(2)
    root = tmp_path / "realm"
    image_refs.write(root / "assets", "cover", a.id)
    image_refs.write(root / "characters" / "seraphine" / "assets" / "default", "avatar",
                     b.id, focus=40)
    image_refs.write(root / "assets" / "images", "coastline", a.id)
    image_refs.write(root / "assets" / "images", "crop", None, focus=10)   # focus-only
    assert image_refs.walk_ids(root) == {a.id, b.id}


def test_walk_ids_skips_journals_garbage_and_non_ref_dirs(tmp_path):
    a = _obj(3)
    root = tmp_path / "realm"
    d = root / "lore" / "tide" / "assets" / "default"
    image_refs.write_journal(d, {"image": a.id})
    _raw(d, "broken", "{not json")
    # A JSON file holding an image id outside an `image-refs/` folder is not a
    # placement.
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "x.json").write_text(json.dumps({"format": 1, "image": a.id}),
                                           encoding="utf-8")
    assert image_refs.walk_ids(root) == set()
    assert image_refs.walk_ids(tmp_path / "missing") == set()


def test_walk_ids_does_not_follow_symlinks(tmp_path):
    a, b = _obj(4), _obj(5)
    outside = tmp_path / "outside"
    image_refs.write(outside, "avatar", a.id)
    root = tmp_path / "realm"
    image_refs.write(root / "assets", "cover", b.id)
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
        (root / "assets" / "image-refs" / "alias.json").symlink_to(
            image_refs.ref_path(outside, "avatar"))
    except (OSError, NotImplementedError):
        pytest.skip("this platform/user cannot create symlinks")
    assert image_refs.walk_ids(root) == {b.id}
