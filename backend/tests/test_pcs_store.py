import pytest

from grimoire.store import assets, pcs


def test_create_read_single_version(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", ["student"])
    assert (pid, vid) == ("elara", "default")
    pc = pcs.read_pc(tmp_path, pid)
    assert pc["meta"]["name"] == "Elara"
    assert pc["meta"]["tags"] == ["student"]
    assert pc["versions"][0]["persona"]["name"] == "Elara"


def test_persona_fields_round_trip(tmp_path):
    persona = {"name": "Elara", "pronouns": "she/her", "summary": "scholar",
               "birthdate": "1990-06-29", "description": "A wanderer."}
    pid, vid = pcs.create_pc(tmp_path, "Elara", [], persona=persona)
    assert pcs.read_persona(tmp_path, pid, vid) == persona


def test_versions_and_default(tmp_path):
    pid, _ = pcs.create_pc(tmp_path, "Elara", [])
    v2 = pcs.create_version(tmp_path, pid, "Older", pcs.blank_persona("Elara"))
    assert v2 == "older"
    pcs.set_default_version(tmp_path, pid, v2)
    assert pcs.read_pc(tmp_path, pid)["meta"]["default_version"] == "older"


def test_hash_stable_then_changes(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    h1 = pcs.version_hash(tmp_path, pid, vid)
    pcs.update_version(tmp_path, pid, vid, pcs.read_persona(tmp_path, pid, vid))
    assert pcs.version_hash(tmp_path, pid, vid) == h1
    p = pcs.read_persona(tmp_path, pid, vid)
    p["description"] = "changed"
    pcs.update_version(tmp_path, pid, vid, p)
    assert pcs.version_hash(tmp_path, pid, vid) != h1


def test_set_tags_and_counts(tmp_path):
    pid, _ = pcs.create_pc(tmp_path, "Elara", ["student"])
    pcs.set_tags(tmp_path, pid, ["student", "hannah-s-father"])
    assert pcs.read_pc(tmp_path, pid)["meta"]["tags"] == ["student", "hannah-s-father"]
    pcs.create_pc(tmp_path, "Rook", [])
    assert pcs.pc_count(tmp_path) == 2
    assert set(pcs.pc_refs(tmp_path)) == {"elara", "rook"}


def test_delete_last_version_refused_and_missing(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    with pytest.raises(pcs.PCVersionNotFound):
        pcs.read_persona(tmp_path, pid, "ghost")
    with pytest.raises(ValueError):
        pcs.delete_version(tmp_path, pid, vid)
    with pytest.raises(pcs.PCNotFound):
        pcs.read_pc(tmp_path, "nobody")


def test_dir_hash_tracks_meta_and_versions(tmp_path):
    assert pcs.dir_hash(tmp_path, "nope") is None
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    h1 = pcs.dir_hash(tmp_path, pid)
    assert h1
    pcs.set_tags(tmp_path, pid, ["vip"])
    assert pcs.dir_hash(tmp_path, pid) != h1


# ---- per-version images (#219) ----
def test_read_pc_and_list_pcs_carry_image_fields(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Winifred", [])
    assert pcs.read_pc(tmp_path, pid)["versions"][0]["images"] == []
    assert pcs.list_pcs(tmp_path)[0]["has_avatar"] is False

    assets.put_image(tmp_path, pid, vid, assets.AVATAR, b"png-bytes", "png", pcs.ASSET_BASE)
    assets.put_image(tmp_path, pid, vid, "gallery_1", b"more", "png", pcs.ASSET_BASE)
    assets.write_focus(tmp_path, pid, vid, 30, pcs.ASSET_BASE)

    version = pcs.read_pc(tmp_path, pid)["versions"][0]
    assert version["images"] == ["avatar", "gallery_1"] and version["avatar_focus"] == 30
    row = pcs.list_pcs(tmp_path)[0]
    assert (row["has_avatar"], row["gallery_count"], row["avatar_focus"]) == (True, 1, 30)


def test_pc_images_live_under_the_pcs_base(tmp_path):
    """`ASSET_BASE` is what keeps a PC's art out of the character folder -- a
    character and a PC can share an id, and their portraits must not."""
    pid, vid = pcs.create_pc(tmp_path, "Mara", [])
    assets.put_image(tmp_path, pid, vid, assets.AVATAR, b"pc", "png", pcs.ASSET_BASE)
    assert (tmp_path / "pcs" / pid / "assets" / vid / "avatar.png").read_bytes() == b"pc"
    assert not (tmp_path / "characters" / pid).exists()


def test_list_pcs_reads_images_off_the_default_version(tmp_path):
    """The rail shows one row per PC, so the derived fields describe the
    version that row opens -- the default, not whichever sorts first."""
    pid, _ = pcs.create_pc(tmp_path, "Winifred", [])
    older = pcs.create_version(tmp_path, pid, "Older", pcs.blank_persona("Winifred"))
    assets.put_image(tmp_path, pid, older, assets.AVATAR, b"art", "png", pcs.ASSET_BASE)
    assert pcs.list_pcs(tmp_path)[0]["has_avatar"] is False
    pcs.set_default_version(tmp_path, pid, older)
    assert pcs.list_pcs(tmp_path)[0]["has_avatar"] is True


def test_images_are_invisible_to_sync(tmp_path):
    """Images are outside `dir_hash`/`snapshot` exactly as a character's are,
    so uploading one must not make sync see a changed record -- and
    `overlay.materialize_actor` copies the bytes the snapshot covers, so a
    disagreement between the two would record a base for content it never
    copied."""
    pid, vid = pcs.create_pc(tmp_path, "Winifred", [])
    before, files_before = pcs.snapshot(tmp_path, pid)
    assert before == pcs.dir_hash(tmp_path, pid)

    assets.put_image(tmp_path, pid, vid, assets.AVATAR, b"art", "png", pcs.ASSET_BASE)

    after, files_after = pcs.snapshot(tmp_path, pid)
    assert (after, files_after) == (before, files_before)
    assert pcs.dir_hash(tmp_path, pid) == before
