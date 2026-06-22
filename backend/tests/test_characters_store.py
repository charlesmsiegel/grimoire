import pytest

from grimoire.store import characters as ch


def test_create_and_read_single_card(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    assert cid == "seraphine"
    assert vid == "default"
    card = ch.read_card(tmp_path, cid, vid)
    assert card["spec"] == "chara_card_v3"
    assert card["data"]["name"] == "Seraphine"
    meta = ch.read_character(tmp_path, cid)
    assert meta["meta"]["default_version"] == "default"
    assert [v["id"] for v in meta["versions"]] == ["default"]


def test_add_second_version_and_set_default(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Seraphine")
    v2 = ch.create_version(tmp_path, cid, "Corrupted", ch.blank_card("Seraphine"))
    assert v2 == "corrupted"
    ch.set_default_version(tmp_path, cid, v2)
    assert ch.read_character(tmp_path, cid)["meta"]["default_version"] == "corrupted"
    assert {v["id"] for v in ch.list_characters(tmp_path)[0]["versions"]} == {"default", "corrupted"}


def test_hash_is_content_stable(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    h1 = ch.card_hash(tmp_path, cid, vid)
    # rewriting identical content does not change the hash
    ch.update_version(tmp_path, cid, vid, ch.read_card(tmp_path, cid, vid))
    assert ch.card_hash(tmp_path, cid, vid) == h1
    # a content change changes the hash
    card = ch.read_card(tmp_path, cid, vid)
    card["data"]["description"] = "the drowned keeper"
    ch.update_version(tmp_path, cid, vid, card)
    assert ch.card_hash(tmp_path, cid, vid) != h1


def test_delete_last_version_refused(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    with pytest.raises(ch.VersionNotFound):
        ch.delete_version(tmp_path, cid, "ghost")
    with pytest.raises(ValueError):
        ch.delete_version(tmp_path, cid, vid)  # last one


def test_missing_character_and_version(tmp_path):
    with pytest.raises(ch.CharacterNotFound):
        ch.read_character(tmp_path, "nobody")
    cid, _ = ch.create_character(tmp_path, "Seraphine")
    with pytest.raises(ch.VersionNotFound):
        ch.read_card(tmp_path, cid, "nope")


def test_traversal_ids_are_rejected(tmp_path):
    # ids that would escape the characters dir are never read/written
    assert ch.card_hash(tmp_path, "../../secret", "default") is None
    assert ch.card_hash(tmp_path, "seraphine", "../x") is None
    with pytest.raises(ch.CharacterNotFound):
        ch.read_character(tmp_path, "..")
    cid, _ = ch.create_character(tmp_path, "Seraphine")
    with pytest.raises(ch.VersionNotFound):
        ch.read_card(tmp_path, cid, "../../secret")


def test_counts_and_refs(tmp_path):
    ch.create_character(tmp_path, "A")
    ch.create_character(tmp_path, "B")
    assert ch.character_count(tmp_path) == 2
    assert set(ch.character_refs(tmp_path)) == {"a", "b"}
