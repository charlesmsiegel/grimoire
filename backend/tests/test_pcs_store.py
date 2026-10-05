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
               "birthdate": "1990-06-29", "goals": "find the archive",
               "player_notes": "never speak for her", "description": "A wanderer."}
    pid, vid = pcs.create_pc(tmp_path, "Elara", [], persona=persona)
    assert pcs.read_persona(tmp_path, pid, vid) == persona


def test_a_persona_written_before_the_profile_fields_reads_them_empty(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    p = tmp_path / "pcs" / pid / f"{vid}.md"
    p.write_text("---\nname: Elara\npronouns: she/her\nsummary: ''\nbirthdate: ''\n---\n\nhero",
                 encoding="utf-8")
    persona = pcs.read_persona(tmp_path, pid, vid)
    assert persona["goals"] == "" and persona["player_notes"] == ""
    assert persona["description"] == "hero"


def test_a_multi_line_scalar_folds_rather_than_splitting_the_frontmatter(tmp_path):
    # A newline inside a frontmatter value would read back as a second key and
    # lose the rest of the value -- every scalar folds to one line instead.
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    pcs.update_version(tmp_path, pid, vid, {
        **pcs.blank_persona("Elara"), "summary": "a scholar\nof salt",
        "goals": "find the archive\n\n- and the key: below", "description": "line one\nline two"})
    persona = pcs.read_persona(tmp_path, pid, vid)
    assert persona["summary"] == "a scholar of salt"
    assert persona["goals"] == "find the archive - and the key: below"
    assert persona["description"] == "line one\nline two"   # the body is untouched
    assert set(persona) == {*pcs.PERSONA_FIELDS, "description"}


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


def test_list_pcs_carries_the_avatar_cache_token(tmp_path):
    """The rail's portrait builds `?w=…&v=<token>`, and only a URL carrying
    `v` is served immutable -- without it every PC portrait is revalidated on
    every visit, where a character's is not (Codex review). The token must
    name the BYTES, so it moves when they do."""
    pid, vid = pcs.create_pc(tmp_path, "Winifred", [])
    assert pcs.list_pcs(tmp_path)[0]["avatar_v"] is None
    assets.put_image(tmp_path, pid, vid, assets.AVATAR, b"\x89PNG-one", "png", pcs.ASSET_BASE)
    first = pcs.list_pcs(tmp_path)[0]["avatar_v"]
    assert first == assets.image_version(
        assets.image_path(tmp_path, pid, vid, assets.AVATAR, pcs.ASSET_BASE))
    assets.put_image(tmp_path, pid, vid, assets.AVATAR, b"\x89PNG-two!", "png", pcs.ASSET_BASE)
    assert pcs.list_pcs(tmp_path)[0]["avatar_v"] not in (None, first)


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


def test_version_named_like_the_meta_file_does_not_land_on_it(tmp_path):
    """`version_name` is a field the create route has always accepted, and one
    value of it used to build a PC nothing could read: `pc.md` is the
    container's meta, so a version slugging to `pc` was written and then
    overwritten by the meta, leaving a directory holding the name's slug with
    no version in it at all (#14)."""
    pid, vid = pcs.create_pc(tmp_path, "Rook", [], "PC")
    assert vid != "pc"
    assert pcs.read_persona(tmp_path, pid, vid)["name"] == "Rook"
    pc = pcs.read_pc(tmp_path, pid)
    assert [v["id"] for v in pc["versions"]] == [vid]
    assert pc["meta"]["default_version"] == vid
    # and the second one through the other door gets its own id, as before
    assert pcs.create_version(tmp_path, pid, "pc", pcs.blank_persona("Rook")) not in ("pc", vid)


@pytest.mark.parametrize("version_name", ["", "!!!"])
def test_version_name_survives_a_slug_with_nothing_in_it(tmp_path, version_name):
    """The other end of the same argument, and the one a form sends: `slugify`
    answers "untitled" rather than "", so a version name with nothing to slug
    still names an addressable file. An empty id would write `<pid>/.md`, whose
    stem is `.md` -- listed by `_version_ids` (the glob matches it and `safe_id`
    allows it) and resolving right back to `.md.md`, so every read of the
    version the listing offers would 404."""
    pid, vid = pcs.create_pc(tmp_path, "Rook", [], version_name)
    assert vid == "untitled"
    assert pcs.read_persona(tmp_path, pid, vid)["name"] == "Rook"


# ---- revision history (#67) ----

def _edit(root, pid, vid, **fields):
    pcs.update_version(root, pid, vid, {**pcs.read_persona(root, pid, vid), **fields})


def test_an_edit_keeps_the_text_it_replaced(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    _edit(tmp_path, pid, vid, description="first")
    _edit(tmp_path, pid, vid, description="second")
    revs = pcs.list_revisions(tmp_path, pid, vid)
    assert [pcs.read_revision(tmp_path, pid, vid, r["id"])["description"] for r in revs] == ["first", ""]
    assert all(r["name"] == "Elara" and r["saved"].endswith("Z") for r in revs)
    assert pcs.read_persona(tmp_path, pid, vid)["description"] == "second"


def test_a_save_that_changes_nothing_records_nothing(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    _edit(tmp_path, pid, vid)
    assert pcs.list_revisions(tmp_path, pid, vid) == []


def test_history_keeps_only_the_newest_revisions(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    for i in range(pcs.HISTORY_KEEP + 5):
        _edit(tmp_path, pid, vid, description=f"text {i}")
    revs = pcs.list_revisions(tmp_path, pid, vid)
    assert len(revs) == pcs.HISTORY_KEEP
    newest = pcs.HISTORY_KEEP + 3     # the text the last edit replaced
    assert pcs.read_revision(tmp_path, pid, vid, revs[0]["id"])["description"] == f"text {newest}"


def test_history_is_invisible_to_sync_and_to_the_version_list(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    _edit(tmp_path, pid, vid, description="first")
    before = (pcs.dir_hash(tmp_path, pid), pcs.snapshot(tmp_path, pid))
    _edit(tmp_path, pid, vid, description="second")
    _edit(tmp_path, pid, vid, description="first")   # back to the same bytes, two snapshots later
    assert len(pcs.list_revisions(tmp_path, pid, vid)) == 3
    assert (pcs.dir_hash(tmp_path, pid), pcs.snapshot(tmp_path, pid)) == before
    assert [v["id"] for v in pcs.read_pc(tmp_path, pid)["versions"]] == [vid]


def test_restore_puts_the_text_back_and_can_itself_be_undone(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    _edit(tmp_path, pid, vid, description="first", goals="the archive")
    _edit(tmp_path, pid, vid, description="second", goals="")
    old = pcs.list_revisions(tmp_path, pid, vid)[0]["id"]
    restored = pcs.restore_revision(tmp_path, pid, vid, old)
    assert restored["description"] == "first"
    assert pcs.read_persona(tmp_path, pid, vid)["goals"] == "the archive"
    undo = pcs.list_revisions(tmp_path, pid, vid)[0]["id"]
    assert pcs.read_revision(tmp_path, pid, vid, undo)["description"] == "second"


def test_an_unknown_or_unsafe_revision_is_not_found(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    for rid in ("nope", "../default", ""):
        with pytest.raises(pcs.PCRevisionNotFoundError):
            pcs.read_revision(tmp_path, pid, vid, rid)
    with pytest.raises(pcs.PCVersionNotFound):
        pcs.list_revisions(tmp_path, pid, "missing")


def test_deleting_a_version_deletes_its_history(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    v2 = pcs.create_version(tmp_path, pid, "Older", pcs.blank_persona("Elara"))
    _edit(tmp_path, pid, v2, description="draft")
    pcs.delete_version(tmp_path, pid, v2)
    again = pcs.create_version(tmp_path, pid, "Older", pcs.blank_persona("Elara"))
    assert again == v2 and pcs.list_revisions(tmp_path, pid, again) == []
    assert pcs.list_revisions(tmp_path, pid, vid) == []
