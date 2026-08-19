import pytest
from grimoire.store import styles


def _write(dir_path, sid, name, description="", tags="", body=""):
    dir_path.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: {description}\ntags: {tags}\n---\n\n{body}"
    (dir_path / f"{sid}.md").write_text(text, encoding="utf-8")


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GRIMOIRE_TEMPLATES", str(tmp_path / "templates"))


def test_list_merges_builtin_and_custom(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", "Gothic Horror",
           "Atmospheric dread.", "horror,gothic", "Atmosphere first.")
    _write(tmp_path / "home" / "styles", "my-style", "My Style",
           "A custom one.", "custom", "Write it my way.")

    items = {s["id"]: s for s in styles.list_styles()}
    assert items["gothic-horror"]["built_in"] is True
    assert items["gothic-horror"]["name"] == "Gothic Horror"
    assert items["gothic-horror"]["tags"] == ["horror", "gothic"]
    assert items["my-style"]["built_in"] is False
    assert items["my-style"]["description"] == "A custom one."


def test_create_read_update_delete_custom(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    sid = styles.create_style("Cozy Mystery", "Gentle whodunits.", ["cozy", "mystery"], "Keep it warm.")
    got = styles.read_style(sid)
    assert got["meta"]["name"] == "Cozy Mystery"
    assert got["meta"]["tags"] == ["cozy", "mystery"]
    assert got["meta"]["built_in"] is False
    assert got["body"].strip() == "Keep it warm."

    styles.update_style(sid, body="Keep it warmer.")
    assert styles.read_style(sid)["body"].strip() == "Keep it warmer."

    styles.delete_style(sid)
    with pytest.raises(styles.StyleNotFound):
        styles.read_style(sid)


def test_built_in_cannot_be_updated_or_deleted(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", "Gothic Horror")

    with pytest.raises(styles.BuiltInStyleImmutable):
        styles.update_style("gothic-horror", body="nope")
    with pytest.raises(styles.BuiltInStyleImmutable):
        styles.delete_style("gothic-horror")


def test_duplicate_creates_an_editable_custom_copy(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", "Gothic Horror",
           "Atmospheric dread.", "horror,gothic", "Atmosphere first.")

    new_id = styles.duplicate_style("gothic-horror")
    assert new_id != "gothic-horror"
    copy = styles.read_style(new_id)
    assert copy["meta"]["built_in"] is False
    assert copy["meta"]["name"] == "Gothic Horror (copy)"
    assert copy["body"].strip() == "Atmosphere first."
    styles.update_style(new_id, body="edited")
    assert styles.read_style(new_id)["body"].strip() == "edited"


def test_ids_are_unique_across_builtin_and_custom(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "cozy-mystery", "Cozy Mystery")

    sid = styles.create_style("Cozy Mystery")
    assert sid == "cozy-mystery-2"


def test_a_malformed_custom_file_is_skipped_without_crashing_the_list(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    d = tmp_path / "home" / "styles"
    d.mkdir(parents=True)
    (d / "broken.md").write_bytes(b"\xff\xfe not valid utf-8 \x00\x01")
    _write(d, "fine", "Fine")

    ids = {s["id"] for s in styles.list_styles()}
    assert ids == {"fine"}

