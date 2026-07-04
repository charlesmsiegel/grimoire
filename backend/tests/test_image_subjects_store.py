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


def test_write_rejects_unknown_image_and_drops_empty(tmp_path):
    cid, gid = _world(tmp_path)
    with pytest.raises(ValueError):
        image_subjects.write_subjects(tmp_path, gid, {"nope": [cid]})
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid], "art_2": []})
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [cid]}


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
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_2": [cid]}
