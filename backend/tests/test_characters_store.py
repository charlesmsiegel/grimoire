import pytest

from grimoire.store import characters as ch
from grimoire.store import fetch


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
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"DOWNLOADED", "image/png"))
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

    monkeypatch.setattr(fetch, "_http_get_bytes", boom)
    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")  # must not raise
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR) is None


def test_json_import_decodes_data_uri_avatar(tmp_path):
    # an embedded base64 data-URI avatar is decoded and stored without any network.
    import base64 as _b64
    import json as _json
    from grimoire.store import assets
    png = b"\x89PNG\r\n\x1a\nDATA"
    card = ch.blank_card("Imp")
    card["data"]["avatar"] = "data:image/png;base64," + _b64.b64encode(png).decode()
    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")
    p = assets.image_path(tmp_path, cid, vid, assets.AVATAR)
    assert p is not None and p.read_bytes() == png and p.suffix == ".png"


def test_avatar_candidate_found_in_extensions(tmp_path):
    # the V2->V3 upconvert relocates an unknown `avatar` field into extensions; still found.
    card = {"data": {"name": "x", "extensions": {"avatar": "https://x/p.png"}}}
    assert ch._avatar_candidates(card) == ["https://x/p.png"]


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

    monkeypatch.setattr(fetch, "_http_get_bytes", boom)
    cid, vid = ch.import_card(tmp_path, _json.dumps(ch.blank_card("Imp")).encode(), "json")
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR) is None


def test_set_and_clear_chub_source(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Seraphine")
    assert ch.read_character(tmp_path, cid)["meta"]["chub_source"] == ""
    ch.set_chub_source(tmp_path, cid, "creator/slug")
    assert ch.read_character(tmp_path, cid)["meta"]["chub_source"] == "creator/slug"
    ch.clear_chub_source(tmp_path, cid)
    assert ch.read_character(tmp_path, cid)["meta"]["chub_source"] == ""


def test_clear_chub_source_when_absent_is_a_noop(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Seraphine")
    ch.clear_chub_source(tmp_path, cid)  # must not raise
    assert ch.read_character(tmp_path, cid)["meta"]["chub_source"] == ""


def test_chub_source_setters_require_known_character(tmp_path):
    with pytest.raises(ch.CharacterNotFound):
        ch.set_chub_source(tmp_path, "nobody", "creator/slug")
    with pytest.raises(ch.CharacterNotFound):
        ch.clear_chub_source(tmp_path, "nobody")


def test_import_from_chub_happy_path(tmp_path, monkeypatch):
    from grimoire.store import assets, cards, chub

    png = cards.dumps(ch.blank_card("Imp"), "png")
    node = {
        "id": 42, "fullPath": "creator/imp", "hasGallery": True,
        "related_lorebooks": [7, 7, -1],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    }
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: node)
    monkeypatch.setattr(chub, "fetch_gallery_paths", lambda pid: ["https://g/1.jpg", "https://g/2.jpg"])
    monkeypatch.setattr(chub, "fetch_lorebook_node", lambda lid: {
        "definition": {"embedded_lorebook": {"entries": [{"keys": ["k"], "content": "lore body"}]}},
    })

    def fake_get_bytes(url):
        if "/g/" in url:
            return (b"\xff\xd8\xffJPEGDATA", "image/jpeg")
        return (png, "image/png")

    monkeypatch.setattr(fetch, "_http_get_bytes", fake_get_bytes)

    result = ch.import_from_chub(tmp_path, "https://chub.ai/characters/creator/imp")

    cid, vid = result["character"], result["version"]
    assert ch.read_character(tmp_path, cid)["meta"]["chub_source"] == "creator/imp"
    assert assets.image_path(tmp_path, cid, vid, "avatar") is not None
    names = {i["name"] for i in assets.list_images(tmp_path, cid, vid)}
    assert names == {"avatar", "gallery_0", "gallery_1"}
    assert result["gallery"] == {"attempted": 2, "stored": 2}
    assert result["lore"]["lorebooks_found"] == 1  # [7, 7, -1] -> dedup'd to one positive id
    assert len(result["lore"]["created"]) == 1


def test_import_from_chub_bad_url_raises_parse_error(tmp_path):
    from grimoire.store import chub
    with pytest.raises(chub.ChubParseError):
        ch.import_from_chub(tmp_path, "not a url")


def test_import_from_chub_unreachable_character_raises_fetch_error(tmp_path, monkeypatch):
    from grimoire.store import chub
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: None)
    with pytest.raises(chub.ChubFetchError):
        ch.import_from_chub(tmp_path, "creator/missing")


def test_import_from_chub_png_download_failure_raises_fetch_error(tmp_path, monkeypatch):
    from grimoire.store import chub
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })

    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(fetch, "_http_get_bytes", boom)
    with pytest.raises(chub.ChubFetchError):
        ch.import_from_chub(tmp_path, "creator/imp")
    assert ch.character_count(tmp_path) == 0  # nothing partially created


def test_import_from_chub_gallery_failure_is_best_effort(tmp_path, monkeypatch):
    from grimoire.store import cards, chub

    png = cards.dumps(ch.blank_card("Imp"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": True, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })
    monkeypatch.setattr(chub, "fetch_gallery_paths", lambda pid: ["https://g/1.jpg", "https://g/2.jpg"])

    def fake_get_bytes(url):
        if url == "https://g/1.jpg":
            raise RuntimeError("one image failed")
        if "/g/" in url:
            return (b"\xff\xd8\xffJPEGDATA", "image/jpeg")
        return (png, "image/png")

    monkeypatch.setattr(fetch, "_http_get_bytes", fake_get_bytes)
    result = ch.import_from_chub(tmp_path, "creator/imp")  # must not raise
    assert result["gallery"] == {"attempted": 2, "stored": 1}


def test_import_from_chub_lorebook_failure_is_best_effort(tmp_path, monkeypatch):
    from grimoire.store import cards, chub

    png = cards.dumps(ch.blank_card("Imp"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [7, 8],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))
    monkeypatch.setattr(chub, "fetch_lorebook_node", lambda lid: None if lid == 7 else {
        "definition": {"embedded_lorebook": {"entries": [{"keys": ["k"], "content": "x"}]}},
    })
    result = ch.import_from_chub(tmp_path, "creator/imp")  # must not raise
    assert result["lore"]["lorebooks_found"] == 2
    assert len(result["lore"]["created"]) == 1  # only id 8 resolved


def test_import_from_chub_into_existing_character_adds_a_version(tmp_path, monkeypatch):
    from grimoire.store import cards, chub

    cid, _ = ch.create_character(tmp_path, "Seraphine")
    png = cards.dumps(ch.blank_card("Variant"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/variant/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/variant", into_cid=cid)
    assert result["character"] == cid
    assert {v["id"] for v in ch.read_character(tmp_path, cid)["versions"]} == {"default", result["version"]}


def test_import_from_chub_into_unknown_character_raises(tmp_path, monkeypatch):
    from grimoire.store import cards, chub

    png = cards.dumps(ch.blank_card("Imp"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))
    with pytest.raises(ch.CharacterNotFound):
        ch.import_from_chub(tmp_path, "creator/imp", into_cid="nobody")
