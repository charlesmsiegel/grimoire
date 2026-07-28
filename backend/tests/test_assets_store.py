import pytest

from grimoire.store import assets


def _named(imgs):
    """(name, ext) pairs — the identity part of a listing, ignoring the v token."""
    return [(i["name"], i["ext"]) for i in imgs]


def test_put_list_get_round_trip(tmp_path):
    assert assets.list_images(tmp_path, "sera", "default") == []
    ext = assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"\x89PNG", "png")
    assert ext == "png"
    assert _named(assets.list_images(tmp_path, "sera", "default")) == [("avatar", "png")]
    p = assets.image_path(tmp_path, "sera", "default", "avatar")
    assert p is not None and p.read_bytes() == b"\x89PNG"


def test_replace_with_different_ext_leaves_one_file(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"b", "jpg")
    imgs = assets.list_images(tmp_path, "sera", "default")
    assert _named(imgs) == [("avatar", "jpg")]  # exactly one, new ext
    assert assets.image_path(tmp_path, "sera", "default", "avatar").read_bytes() == b"b"


def test_delete_and_absent(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.delete_image(tmp_path, "sera", "default", assets.AVATAR)
    assert assets.image_path(tmp_path, "sera", "default", "avatar") is None
    assets.delete_image(tmp_path, "sera", "default", "ghost")  # no error


def test_unsafe_and_unsupported_rejected(tmp_path):
    with pytest.raises(ValueError):
        assets.put_image(tmp_path, "sera", "default", "../x", b"a", "png")
    with pytest.raises(ValueError):
        assets.put_image(tmp_path, "sera", "default", "a.b", b"a", "png")  # dot in name
    with pytest.raises(ValueError):
        assets.put_image(tmp_path, "sera", "default", "*", b"a", "png")  # glob metacharacter
    with pytest.raises(ValueError):
        assets.put_image(tmp_path, "sera", "default", "avatar", b"a", "svg")  # not allowlisted
    assert assets.image_path(tmp_path, "..", "default", "avatar") is None  # unsafe cid


def test_promote_swaps_gallery_with_avatar(tmp_path):
    assets.put_image(tmp_path, "sera", "default", "avatar", b"old", "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_2", b"new", "webp")
    assets.promote_image(tmp_path, "sera", "default", "gallery_2")
    av = assets.image_path(tmp_path, "sera", "default", "avatar")
    gal = assets.image_path(tmp_path, "sera", "default", "gallery_2")
    assert av.read_bytes() == b"new" and av.suffix == ".webp"
    assert gal.read_bytes() == b"old" and gal.suffix == ".png"


def test_promote_without_existing_avatar_renames(tmp_path):
    assets.put_image(tmp_path, "sera", "default", "gallery_1", b"n", "png")
    assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    assert assets.image_path(tmp_path, "sera", "default", "avatar").read_bytes() == b"n"
    assert assets.image_path(tmp_path, "sera", "default", "gallery_1") is None


def test_promote_missing_image_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_9")


def test_promote_avatar_itself_is_a_noop(tmp_path):
    assets.put_image(tmp_path, "sera", "default", "avatar", b"a", "png")
    assets.promote_image(tmp_path, "sera", "default", "avatar")
    assert assets.image_path(tmp_path, "sera", "default", "avatar").read_bytes() == b"a"


def test_focus_round_trip_and_clamp(tmp_path):
    assert assets.read_focus(tmp_path, "sera", "default") is None
    assets.write_focus(tmp_path, "sera", "default", 62)
    assert assets.read_focus(tmp_path, "sera", "default") == 62
    assets.write_focus(tmp_path, "sera", "default", 250)
    assert assets.read_focus(tmp_path, "sera", "default") == 100
    assets.clear_focus(tmp_path, "sera", "default")
    assert assets.read_focus(tmp_path, "sera", "default") is None


def test_focus_sidecar_not_listed_as_image(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.write_focus(tmp_path, "sera", "default", 30)
    assert _named(assets.list_images(tmp_path, "sera", "default")) == [("avatar", "png")]


def test_focus_cleared_when_avatar_changes(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.write_focus(tmp_path, "sera", "default", 30)
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"b", "png")  # re-upload clears
    assert assets.read_focus(tmp_path, "sera", "default") is None

    assets.write_focus(tmp_path, "sera", "default", 30)
    assets.put_image(tmp_path, "sera", "default", "gallery_1", b"g", "png")  # non-avatar keeps it
    assert assets.read_focus(tmp_path, "sera", "default") == 30

    assets.promote_image(tmp_path, "sera", "default", "gallery_1")  # promote clears
    assert assets.read_focus(tmp_path, "sera", "default") is None

    assets.write_focus(tmp_path, "sera", "default", 30)
    assets.delete_image(tmp_path, "sera", "default", assets.AVATAR)  # delete clears
    assert assets.read_focus(tmp_path, "sera", "default") is None


def test_base_param_roots_other_kinds(tmp_path):
    assets.put_image(tmp_path, "docks", "default", "avatar", b"i", "png", base="locations")
    p = assets.image_path(tmp_path, "docks", "default", "avatar", base="locations")
    assert p is not None and "locations" in p.parts
    # not visible under the default characters/ base
    assert assets.image_path(tmp_path, "docks", "default", "avatar") is None
    assert _named(assets.list_images(tmp_path, "docks", "default", base="locations")) == [
        ("avatar", "png")]
    assets.delete_image(tmp_path, "docks", "default", "avatar", base="locations")
    assert assets.image_path(tmp_path, "docks", "default", "avatar", base="locations") is None


def test_list_images_version_token_tracks_content(tmp_path):
    from grimoire.store import assets

    assets.put_image(tmp_path, "c", "v1", "avatar", b"png-one", "png")
    first = assets.list_images(tmp_path, "c", "v1")
    assert first[0]["v"]
    import os
    p = tmp_path / "characters" / "c" / "assets" / "v1" / "avatar.png"
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 1_000_000))
    second = assets.list_images(tmp_path, "c", "v1")
    assert second[0]["v"] != first[0]["v"]


def test_a_failed_image_write_keeps_the_previous_image(tmp_path, monkeypatch):
    """put_image used to unlink prior-extension files BEFORE writing the new
    one, so anything that failed in between lost the image outright -- a bug no
    amount of write atomicity can fix, because the delete came first (#233)."""
    from grimoire.store import atomic
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"original", "png")

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(atomic, "write_bytes", boom)
    with pytest.raises(OSError):
        assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"new", "jpg")

    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert p is not None and p.read_bytes() == b"original"


def test_a_stale_sibling_extension_does_not_win_the_lookup(tmp_path):
    """Writing before unlinking leaves both extensions present for a moment.
    image_path used to return sorted(...)[0], which hands back the STALE file
    whenever the old extension sorts first (jpg < png)."""
    d = tmp_path / "characters" / "sera" / "assets" / "default"
    d.mkdir(parents=True)
    (d / "avatar.jpg").write_bytes(b"stale")
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"fresh", "png")

    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert p is not None and p.read_bytes() == b"fresh"


def test_an_orphaned_sibling_still_resolves_to_the_newest(tmp_path, monkeypatch):
    """If the stale-sibling unlink ever fails, the wrong image must not become
    permanently sticky -- the mtime tie-break makes that state self-healing."""
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"old", "jpg")
    monkeypatch.setattr("pathlib.Path.unlink", lambda self, **kw: None)
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"new", "png")

    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert p is not None and p.read_bytes() == b"new"
