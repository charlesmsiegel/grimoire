import pytest

from grimoire.store import entities, worlds


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def test_create_list_read(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Drowned Realm")
    assert wid == "drowned-realm"
    entities.create_entity(worlds.world_root(wid), "locations", "Drowned Library")
    listed = worlds.list_worlds()
    assert len(listed) == 1
    assert listed[0]["name"] == "Drowned Realm"
    assert listed[0]["counts"]["locations"] == 1
    w = worlds.read_world(wid)
    assert w["meta"]["id"] == wid
    assert w["counts"]["locations"] == 1


def test_rename_keeps_id(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Old")
    worlds.rename_world(wid, "New Name")
    assert worlds.read_world(wid)["meta"]["name"] == "New Name"  # id unchanged


def test_missing_world_raises(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    with pytest.raises(worlds.WorldNotFound):
        worlds.read_world("nope")
    with pytest.raises(worlds.WorldNotFound):
        worlds.rename_world("nope", "x")
    with pytest.raises(worlds.WorldNotFound):
        worlds.delete_world("nope")


def test_delete_removes_world(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Doomed")
    worlds.delete_world(wid)
    assert worlds.list_worlds() == []


def test_world_counts_include_greetings(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import worlds
    wid = worlds.create_world("Saltmarch")
    root = worlds.world_root(wid)
    (root / "greetings").mkdir()
    (root / "greetings" / "gala.md").write_text("---\nname: Gala\n---\n", encoding="utf-8")
    assert worlds.read_world(wid)["counts"]["greetings"] == 1
    assert worlds.list_worlds()[0]["counts"]["greetings"] == 1


# ---- the world profile (#38) ----

def test_a_name_only_world_writes_the_file_it_always_wrote(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    text = worlds.world_meta_path(wid).read_text(encoding="utf-8")
    assert "genre" not in text and "themes" not in text and text.endswith("---\n\n")
    w = worlds.read_world(wid)
    assert (w["meta"]["genre"], w["meta"]["tone"], w["meta"]["themes"]) == ("", "", [])
    assert worlds.profile_of(worlds.world_root(wid)) == {
        "genre": "", "tone": "", "themes": [], "description": ""}


def test_create_and_read_a_profile(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm", genre="Coastal gothic", tone="Wry\ndread",
                              themes=["salt", " debt ", "salt", "toll, roads", ""],
                              description="A drowned coast.\n\nTwo paragraphs.")
    w = worlds.read_world(wid)
    assert w["meta"]["genre"] == "Coastal gothic"
    assert w["meta"]["tone"] == "Wry dread"           # folded to one line
    assert w["meta"]["themes"] == ["salt", "debt", "toll roads"]
    assert w["body"].strip() == "A drowned coast.\n\nTwo paragraphs."
    assert worlds.list_worlds()[0]["genre"] == "Coastal gothic"


def test_update_is_partial_and_an_emptied_field_leaves_the_file(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm", genre="Gothic", tone="Dread", themes=["salt"],
                              description="Old text.")
    worlds.update_world(wid, tone="")
    w = worlds.read_world(wid)
    assert (w["meta"]["name"], w["meta"]["genre"], w["meta"]["tone"]) == ("Realm", "Gothic", "")
    assert "tone" not in worlds.world_meta_path(wid).read_text(encoding="utf-8")
    assert w["body"].strip() == "Old text."
    worlds.update_world(wid, name="Saltmarch", description="")
    w = worlds.read_world(wid)
    assert w["meta"]["name"] == "Saltmarch" and w["body"] == "" and w["meta"]["themes"] == ["salt"]


def test_update_keeps_other_frontmatter(monkeypatch, tmp_path):
    # `module` is written by the mechanics binding, not by this path
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    mp = worlds.world_meta_path(wid)
    mp.write_text(mp.read_text(encoding="utf-8").replace("---\n\n", "module: keeper\n---\n\n", 1),
                  encoding="utf-8")
    worlds.update_world(wid, genre="Gothic")
    assert worlds.read_world(wid)["meta"]["module"] == "keeper"


def _rewrite_during_update(monkeypatch, wid, times):
    """Land a competing `world.md` write (a module bind) inside `update_world`'s
    read-modify-write, `times` times. `now_iso` is called between the read and
    the re-check, which is exactly the window a concurrent writer uses."""
    from grimoire.store.worlds import lifecycle
    mp = worlds.world_meta_path(wid)
    real_now = lifecycle.now_iso
    left = {"n": times}

    def racing_now():
        if left["n"]:
            left["n"] -= 1
            text = mp.read_text(encoding="utf-8")
            # a new key just before the closing fence
            mp.write_text(text.replace("\n---\n", f"\nmodule: bind{left['n']}\n---\n", 1),
                          encoding="utf-8")
        return real_now()
    monkeypatch.setattr(lifecycle, "now_iso", racing_now)


def test_update_retries_when_the_file_moves_under_it(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    _rewrite_during_update(monkeypatch, wid, 1)
    worlds.update_world(wid, genre="Gothic")
    meta = worlds.read_world(wid)["meta"]
    assert meta["genre"] == "Gothic" and meta["module"] == "bind0"   # neither write lost


def test_update_refuses_a_file_that_never_settles(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    _rewrite_during_update(monkeypatch, wid, 10)
    with pytest.raises(worlds.WorldChangedError):
        worlds.update_world(wid, genre="Gothic")
    assert worlds.read_world(wid)["meta"]["genre"] == ""


def test_a_fork_carries_the_profile(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm", genre="Gothic", themes=["salt"], description="Coast.")
    fid = worlds.fork_world(wid, "Realm Copy")
    assert worlds.profile_of(worlds.world_root(fid)) == worlds.profile_of(worlds.world_root(wid))
