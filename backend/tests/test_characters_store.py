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


def test_read_exposes_images_and_list_has_avatar(tmp_path):
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    # no images yet
    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == []
    assert ch.list_characters(tmp_path)[0]["has_avatar"] is False
    # add an avatar to the default version
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"img", "png")
    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["avatar"]
    assert ch.list_characters(tmp_path)[0]["has_avatar"] is True


def test_png_import_saves_avatar(tmp_path):
    from grimoire.store import assets, cards
    blob = cards.dumps(ch.blank_card("Imp"), "png")
    cid, vid = ch.import_card(tmp_path, blob, "png")
    p = assets.image_path(tmp_path, cid, vid, assets.AVATAR)
    assert p is not None and p.read_bytes() == blob


def test_json_import_downloads_avatar_url(tmp_path, monkeypatch):
    import json as _json
    from grimoire.store import assets
    card = ch.blank_card("Imp")
    card["data"]["assets"] = [{"type": "icon", "uri": "https://x/pic.png", "name": "main", "ext": "png"}]
    monkeypatch.setattr(ch, "_http_get_bytes", lambda url: (b"DOWNLOADED", "image/png"))
    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")
    p = assets.image_path(tmp_path, cid, vid, assets.AVATAR)
    assert p is not None and p.read_bytes() == b"DOWNLOADED" and p.suffix == ".png"


def test_json_import_download_failure_is_swallowed(tmp_path, monkeypatch):
    import json as _json
    from grimoire.store import assets
    card = ch.blank_card("Imp")
    card["data"]["assets"] = [{"type": "icon", "uri": "https://x/pic.png"}]

    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(ch, "_http_get_bytes", boom)
    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")  # must not raise
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR) is None


def test_download_avatar_blocks_internal_hosts(tmp_path):
    # SSRF guard: internal/loopback targets resolve but must be refused (no avatar, no raise).
    card = ch.blank_card("Imp")
    card["data"]["assets"] = [{"type": "icon", "uri": "http://127.0.0.1/pic.png"}]
    assert ch._download_avatar(card) is None
    card["data"]["assets"] = [{"type": "icon", "uri": "http://10.0.0.1/pic.png"}]
    assert ch._download_avatar(card) is None


def test_json_import_no_url_makes_no_call(tmp_path, monkeypatch):
    import json as _json
    from grimoire.store import assets

    def boom(url):
        raise AssertionError("should not be called")

    monkeypatch.setattr(ch, "_http_get_bytes", boom)
    cid, vid = ch.import_card(tmp_path, _json.dumps(ch.blank_card("Imp")).encode(), "json")
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR) is None
