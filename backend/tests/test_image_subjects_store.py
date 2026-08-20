import pytest

from grimoire.store import assets, characters, greetings, image_subjects


def _world(tmp_path, images=("art_1", "art_2")):
    cid, vid = characters.create_character(tmp_path, "Mira", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "body")
    for name in images:
        assets.put_image(tmp_path, gid, "default", name, b"png", "png", base="greetings")
    return cid, gid


def test_subjects_roundtrip_and_missing_file(tmp_path):
    cid, gid = _world(tmp_path)
    assert image_subjects.read_subjects(tmp_path, gid) == {}
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid]})
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [cid]}


def test_write_rejects_unknown_image_and_persists_empty(tmp_path):
    cid, gid = _world(tmp_path)
    with pytest.raises(ValueError):
        image_subjects.write_subjects(tmp_path, gid, {"nope": [cid]})
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid], "art_2": []})
    # explicit [] persists: "reviewed, nobody in it"
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [cid], "art_2": []}


def test_read_drops_vanished_images_and_characters(tmp_path):
    cid, gid = _world(tmp_path)
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid, "ghost"], "art_2": [cid]})
    assets.delete_image(tmp_path, gid, "default", "art_2", base="greetings")
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [cid]}


def test_read_tolerates_garbled_sidecar(tmp_path):
    _cid, gid = _world(tmp_path)
    image_subjects.subjects_path(tmp_path, gid).write_text("{not json", encoding="utf-8")
    assert image_subjects.read_subjects(tmp_path, gid) == {}


def test_set_image_subjects_updates_one_entry(tmp_path):
    cid, gid = _world(tmp_path)
    image_subjects.set_image_subjects(tmp_path, gid, "art_1", [cid])
    image_subjects.set_image_subjects(tmp_path, gid, "art_2", [cid])
    image_subjects.set_image_subjects(tmp_path, gid, "art_1", [])
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [], "art_2": [cid]}


def test_untagged_lists_only_unreviewed_images(tmp_path):
    cid, _vid = characters.create_character(tmp_path, "Mira", "main")
    g1 = greetings.create_greeting(tmp_path, "One", cid, "main", "x")
    g2 = greetings.create_greeting(tmp_path, "Two", cid, "main", "x")
    for gid, names in ((g1, ("a_tagged", "b_reviewed", "c_new")), (g2, ("d_new",))):
        for n in names:
            assets.put_image(tmp_path, gid, "default", n, b"p", "png", base="greetings")
    image_subjects.set_image_subjects(tmp_path, g1, "a_tagged", [cid])
    image_subjects.set_image_subjects(tmp_path, g1, "b_reviewed", [])  # reviewed, none
    got = image_subjects.untagged(tmp_path)
    assert got == sorted(got, key=lambda a: (a["gid"], a["name"]))
    assert {(a["gid"], a["name"]) for a in got} == {(g1, "c_new"), (g2, "d_new")}


def test_appearances_scans_across_greetings_in_order(tmp_path):
    cid, _vid = characters.create_character(tmp_path, "Mira", "main")
    g1 = greetings.create_greeting(tmp_path, "B scene", cid, "main", "x")
    g2 = greetings.create_greeting(tmp_path, "A scene", cid, "main", "x")
    for gid in (g1, g2):
        assets.put_image(tmp_path, gid, "default", "art_1", b"p", "png", base="greetings")
    image_subjects.set_image_subjects(tmp_path, g1, "art_1", [cid])
    image_subjects.set_image_subjects(tmp_path, g2, "art_1", [cid])
    got = image_subjects.appearances(tmp_path, cid)
    assert got == sorted(got, key=lambda a: (a["gid"], a["name"]))
    assert {a["gid"] for a in got} == {g1, g2}
    assert image_subjects.appearances(tmp_path, "nobody") == []


def test_sweeps_skip_full_character_enumeration(tmp_path, monkeypatch):
    """appearances/untagged scan every greeting; enumerating full character
    detail per greeting made them O(greetings x characters) in disk reads."""
    cid, gid = _world(tmp_path)
    image_subjects.set_image_subjects(tmp_path, gid, "art_1", [cid])

    def boom(root):
        raise AssertionError("list_characters must not run during sweeps")
    monkeypatch.setattr(image_subjects.characters, "list_characters", boom)
    real_refs = image_subjects.characters.character_refs
    refs_calls = []
    monkeypatch.setattr(image_subjects.characters, "character_refs",
                        lambda root: (refs_calls.append(1), real_refs(root))[1])

    got = image_subjects.appearances(tmp_path, cid)
    assert {(a["gid"], a["name"]) for a in got} == {(gid, "art_1")}
    assert len(refs_calls) <= 1

    refs_calls.clear()
    got = image_subjects.untagged(tmp_path)
    assert {(a["gid"], a["name"]) for a in got} == {(gid, "art_2")}
    assert len(refs_calls) <= 1


def test_copy_to_character_gallery_numbers_and_avatar(tmp_path):
    cid, vid = characters.create_character(tmp_path, "Mira", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "x")
    assets.put_image(tmp_path, gid, "default", "art_1", b"artbytes", "png", base="greetings")
    assets.put_image(tmp_path, cid, vid, "gallery_1", b"old", "png")  # occupy slot 1

    n1 = image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "gallery")
    assert n1 == "gallery_2"
    p = assets.image_path(tmp_path, cid, vid, "gallery_2")
    assert p is not None and p.read_bytes() == b"artbytes"

    assets.write_focus(tmp_path, cid, vid, 30)
    n2 = image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "avatar")
    assert n2 == "avatar"
    assert assets.image_path(tmp_path, cid, vid, "avatar").read_bytes() == b"artbytes"
    assert assets.read_focus(tmp_path, cid, vid) is None  # avatar semantics reset the crop

    with pytest.raises(FileNotFoundError):
        image_subjects.copy_to_character(tmp_path, gid, "missing", cid, vid, "gallery")
    with pytest.raises(ValueError):
        image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "banner")


def test_copy_to_character_honors_taken_names_override(tmp_path):
    """A campaign-side caller passes the overlay-resolved union of gallery
    names so an inherited world gallery image can't be shadowed by a reused
    gallery_N name — even though nothing occupies that slot in `root` itself."""
    cid, vid = characters.create_character(tmp_path, "Mira", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "x")
    assets.put_image(tmp_path, gid, "default", "art_1", b"artbytes", "png", base="greetings")

    n = image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "gallery",
                                         taken_names={"gallery_1"})
    assert n == "gallery_2"
