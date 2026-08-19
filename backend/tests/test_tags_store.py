import pytest
from grimoire.store import tags


def test_add_read_rename_delete(tmp_path):
    tid = tags.add_tag(tmp_path, "Student")
    assert tid == "student"
    assert tags.read_tags(tmp_path) == {"student": "Student"}
    assert tags.has_tag(tmp_path, "student")
    # rename keeps the id, changes the display
    tags.rename_tag(tmp_path, "student", "Pupil")
    assert tags.read_tags(tmp_path) == {"student": "Pupil"}
    tags.delete_tag(tmp_path, "student")
    assert tags.read_tags(tmp_path) == {}


def test_collision_uniquifies(tmp_path):
    assert tags.add_tag(tmp_path, "Hannah's Father") == "hannah-s-father"
    assert tags.add_tag(tmp_path, "Hannah's Father") == "hannah-s-father-2"


def test_rename_missing_raises(tmp_path):
    with pytest.raises(tags.TagNotFound):
        tags.rename_tag(tmp_path, "ghost", "X")
