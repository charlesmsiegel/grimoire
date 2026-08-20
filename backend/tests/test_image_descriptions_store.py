import pytest

from grimoire.store import assets, characters, entities, image_descriptions


def _chars(tmp_path, images=("avatar", "gallery_1")):
    cid, vid = characters.create_character(tmp_path, "Seraphine", "main")
    for name in images:
        assets.put_image(tmp_path, cid, vid, name, b"png", "png")
    return cid, vid


def _dir_of(tmp_path, cid, vid):
    return tmp_path / "characters" / cid / "assets" / vid


def test_roundtrip_and_missing_file(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    assert image_descriptions.read_in(d) == {}
    image_descriptions.write_in(d, {"avatar": "Rain-soaked, the keep burning."})
    assert image_descriptions.read_in(d) == {"avatar": "Rain-soaked, the keep burning."}


def test_write_rejects_unknown_image_and_persists_empty(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    with pytest.raises(ValueError):
        image_descriptions.write_in(d, {"nope": "a picture"})
    image_descriptions.write_in(d, {"avatar": "In half-plate.", "gallery_1": ""})
    # An explicit "" persists: "reviewed, deliberately no description".
    assert image_descriptions.read_in(d) == {"avatar": "In half-plate.", "gallery_1": ""}


def test_read_drops_vanished_images(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "one", "gallery_1": "two"})
    assets.delete_image(tmp_path, cid, vid, "gallery_1")
    assert image_descriptions.read_in(d) == {"avatar": "one"}


def test_read_tolerates_garbled_sidecar(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    (d / image_descriptions.DESCRIPTIONS_FILE).write_text("{not json", encoding="utf-8")
    assert image_descriptions.read_in(d) == {}


def test_read_tolerates_non_string_values(tmp_path):
    _cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, _cid, vid)
    (d / image_descriptions.DESCRIPTIONS_FILE).write_text(
        '{"avatar": ["a", "list"], "gallery_1": "kept"}', encoding="utf-8")
    assert image_descriptions.read_in(d) == {"gallery_1": "kept"}


def test_set_in_updates_one_entry(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.set_in(d, "avatar", "one")
    image_descriptions.set_in(d, "gallery_1", "two")
    image_descriptions.set_in(d, "avatar", "")
    assert image_descriptions.read_in(d) == {"avatar": "", "gallery_1": "two"}


def test_set_in_rejects_unknown_image(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    with pytest.raises(ValueError):
        image_descriptions.set_in(d, "nope", "a picture")


def test_set_in_preserves_entries_for_images_that_vanished_outside_the_api(tmp_path):
    """Raw read on the modify path: editing one image's entry does not discard
    another's just because its file is missing right now.

    The case is a synced store, not a deletion -- `assets.delete_image` takes
    the entry with the bytes, so a file that is gone *and* whose entry is gone
    is the API's own doing. A sync client that has not yet brought the file
    down leaves the entry with nothing behind it, and a `write_in`-style strict
    rewrite here would silently discard the author's text for an image that is
    about to come back.
    """
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.set_in(d, "avatar", "one")
    image_descriptions.set_in(d, "gallery_1", "two")
    (d / "gallery_1.png").unlink()          # vanished behind our back
    assert image_descriptions.read_in(d) == {"avatar": "one"}   # not offered while absent
    image_descriptions.set_in(d, "avatar", "one edited")
    (d / "gallery_1.png").write_bytes(b"png")                   # sync catches up
    assert image_descriptions.read_in(d) == {"avatar": "one edited", "gallery_1": "two"}


def test_version_wrappers(tmp_path):
    cid, vid = _chars(tmp_path)
    assert image_descriptions.read(tmp_path, cid, vid, "avatar") == ""
    image_descriptions.set_description(tmp_path, cid, vid, "avatar", "In half-plate.")
    assert image_descriptions.read(tmp_path, cid, vid, "avatar") == "In half-plate."
    assert image_descriptions.read_all(tmp_path, cid, vid) == {"avatar": "In half-plate."}


def test_version_wrappers_reject_unsafe_ids(tmp_path):
    _cid, vid = _chars(tmp_path)
    assert image_descriptions.read(tmp_path, "../evil", vid, "avatar") == ""
    with pytest.raises(ValueError):
        image_descriptions.set_description(tmp_path, "../evil", vid, "avatar", "x")


def test_entity_base(tmp_path):
    eid = entities.create_entity(tmp_path, "locations", "Saltmarch Harbour", "A grey quay.")
    assets.put_image(tmp_path, eid, "default", "gallery_1", b"png", "png", base="locations")
    image_descriptions.set_description(tmp_path, eid, "default", "gallery_1",
                                       "The quay at low tide.", base="locations")
    assert image_descriptions.read(tmp_path, eid, "default", "gallery_1",
                                   base="locations") == "The quay at low tide."


def test_delete_image_drops_the_description(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "one", "gallery_1": "two"})
    assets.delete_image(tmp_path, cid, vid, "avatar")
    # The raw sidecar, not the filtered read: the entry is really gone, so
    # re-uploading under the same name does not resurrect a stale description.
    assert image_descriptions.read_raw(d) == {"gallery_1": "two"}


def test_undescribed_lists_only_images_with_no_key(tmp_path):
    cid, vid = _chars(tmp_path, images=("avatar", "gallery_1", "gallery_2"))
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "described", "gallery_1": ""})
    # `avatar` has text and `gallery_1` an explicit "" -- both reviewed.
    assert image_descriptions.undescribed(tmp_path, "characters") == [
        {"id": cid, "vid": vid, "name": "gallery_2"}]


def test_undescribed_empty_when_no_base_dir(tmp_path):
    assert image_descriptions.undescribed(tmp_path, "characters") == []


def test_promotion_moves_descriptions_with_the_bytes(tmp_path):
    """A description is a claim about particular bytes, so it travels with them.
    Without this the swap left each picture wearing the other's sentence."""
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "The old portrait.",
                                    "gallery_1": "Half-plate in the rain."})
    assets.promote_image(tmp_path, cid, vid, "gallery_1")
    assert image_descriptions.read_in(d) == {"avatar": "Half-plate in the rain.",
                                             "gallery_1": "The old portrait."}


def test_promotion_with_no_avatar_takes_the_description_along(tmp_path):
    """With nothing to swap back the promoted image LEAVES its gallery slot, so
    its description has to leave with it rather than be dropped."""
    cid, vid = _chars(tmp_path, images=("gallery_1",))
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"gallery_1": "Half-plate in the rain."})
    assets.promote_image(tmp_path, cid, vid, "gallery_1")
    assert image_descriptions.read_in(d) == {"avatar": "Half-plate in the rain."}


def test_promoting_an_undescribed_image_clears_the_avatars_description(tmp_path):
    """The avatar slot must not keep words written about the picture that just
    left it: the new occupant is different art and is simply undescribed."""
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "The old portrait."})
    assets.promote_image(tmp_path, cid, vid, "gallery_1")
    assert image_descriptions.read_raw(d) == {"gallery_1": "The old portrait."}


def test_stranded_promotion_residue_is_never_describable(tmp_path):
    """`assets.list_in` shows a stranded `promote-tmp` on purpose -- crash
    residue is worth seeing (#253) -- but nothing can serve, promote or delete
    it. Unfiltered it entered the describe queue and could take a sidecar entry
    that the next ordinary listing would strand under a key no image has."""
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    (d / "promote-tmp.png").write_bytes(b"png")
    assert "promote-tmp" in {i["name"] for i in assets.list_in(d)}   # visible...
    assert "promote-tmp" not in image_descriptions._names(d)         # ...not describable
    assert {i["name"] for i in image_descriptions.undescribed(tmp_path, "characters")} == {
        "avatar", "gallery_1"}
    with pytest.raises(ValueError):
        image_descriptions.set_in(d, "promote-tmp", "crash residue")
