import json

import pytest

from grimoire.store import characters as ch
from grimoire.store import fetch, image_sanitize, voice_anchors


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


def test_create_character_bakes_char_macro(tmp_path):
    # #137: {{char}} is self-reference, resolved at write time -- not just on
    # import, so a hand-authored card (create_character, not import_card) also
    # never carries a raw {{char}} token into a scene.
    card = ch.blank_card("Seraphine")
    card["data"]["description"] = "{{char}} keeps the harbor."
    cid, vid = ch.create_character(tmp_path, "Seraphine", card=card)
    assert ch.read_card(tmp_path, cid, vid)["data"]["description"] == "Seraphine keeps the harbor."


def test_create_version_bakes_char_macro(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Seraphine")
    card = ch.blank_card("Seraphine")
    card["data"]["description"] = "{{char}} is corrupted."
    vid = ch.create_version(tmp_path, cid, "Corrupted", card)
    assert ch.read_card(tmp_path, cid, vid)["data"]["description"] == "Seraphine is corrupted."


def test_update_version_bakes_char_macro(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    card = ch.read_card(tmp_path, cid, vid)
    card["data"]["description"] = "{{char}} remembers the flood."
    ch.update_version(tmp_path, cid, vid, card)
    assert ch.read_card(tmp_path, cid, vid)["data"]["description"] == "Seraphine remembers the flood."


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


def test_list_characters_includes_tagline(tmp_path):
    from grimoire.store import taglines
    cid, _ = ch.create_character(tmp_path, "Sera")
    assert ch.list_characters(tmp_path)[0]["tagline"] == ""
    taglines.write(tmp_path, cid, "Keeper of the salt ledgers.")
    assert ch.list_characters(tmp_path)[0]["tagline"] == "Keeper of the salt ledgers."


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
    # the store keeps the picture with its metadata (the card chunk) stripped
    assert p is not None and p.read_bytes() == image_sanitize.sanitize(blob)


def test_json_import_downloads_avatar_url(tmp_path, monkeypatch):
    import json as _json

    from grimoire.store import assets
    card = ch.blank_card("Imp")
    card["data"]["assets"] = [{"type": "icon", "uri": "https://x/pic.png", "name": "main", "ext": "png"}]
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"DOWNLOADED", "image/png"))
    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")
    p = assets.image_path(tmp_path, cid, vid, assets.AVATAR)
    assert p is not None and p.read_bytes() == b"DOWNLOADED" and p.suffix == ".png"


def test_import_card_update_vid_overwrites_in_place(tmp_path):
    from grimoire.store import assets, cards
    cid, vid = ch.create_character(tmp_path, "Imp")
    new_png = cards.dumps(ch.blank_card("Imp Updated"), "png")

    got_cid, got_vid = ch.import_card(tmp_path, new_png, "png", into_cid=cid, update_vid=vid)

    assert (got_cid, got_vid) == (cid, vid)
    assert {v["id"] for v in ch.read_character(tmp_path, cid)["versions"]} == {vid}  # no new version
    assert ch.read_card(tmp_path, cid, vid)["data"]["name"] == "Imp Updated"
    p = assets.image_path(tmp_path, cid, vid, assets.AVATAR)
    assert p is not None and p.read_bytes() == image_sanitize.sanitize(new_png)


def test_import_card_update_vid_downloads_avatar_for_json(tmp_path, monkeypatch):
    import json as _json

    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Imp")
    card = ch.blank_card("Imp Updated")
    card["data"]["assets"] = [{"type": "icon", "uri": "https://x/pic.png", "name": "main", "ext": "png"}]
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"DOWNLOADED", "image/png"))

    ch.import_card(tmp_path, _json.dumps(card).encode(), "json", into_cid=cid, update_vid=vid)

    p = assets.image_path(tmp_path, cid, vid, assets.AVATAR)
    assert p is not None and p.read_bytes() == b"DOWNLOADED"


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
    assert ch._resolve_avatar(card, b"", "json", network=True) is None
    card["data"]["assets"] = [{"type": "icon", "uri": "http://10.0.0.1/pic.png"}]
    assert ch._resolve_avatar(card, b"", "json", network=True) is None


def test_resolving_without_the_network_never_fetches(tmp_path, monkeypatch):
    # what PNG import gets: the file is normally its own avatar, so that path
    # has never reached out to the network and still must not.
    card = ch.blank_card("Imp")
    card["data"]["assets"] = [{"type": "icon", "uri": "https://x/pic.png"}]
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: pytest.fail("no fetch expected"))
    assert ch._resolve_avatar(card, b"", "json", network=False) is None


def test_json_import_no_url_makes_no_call(tmp_path, monkeypatch):
    import json as _json

    from grimoire.store import assets

    def boom(url):
        raise AssertionError("should not be called")

    monkeypatch.setattr(fetch, "_http_get_bytes", boom)
    cid, vid = ch.import_card(tmp_path, _json.dumps(ch.blank_card("Imp")).encode(), "json")
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR) is None


def _chub_sources(tmp_path, cid):
    return {v["id"]: v["chub_source"] for v in ch.read_character(tmp_path, cid)["versions"]}


def test_set_and_clear_chub_source(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    assert _chub_sources(tmp_path, cid)[vid] == ""
    ch.set_chub_source(tmp_path, cid, vid, "creator/slug")
    assert _chub_sources(tmp_path, cid)[vid] == "creator/slug"
    ch.clear_chub_source(tmp_path, cid, vid)
    assert _chub_sources(tmp_path, cid)[vid] == ""


def test_clear_chub_source_when_absent_is_a_noop(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    ch.clear_chub_source(tmp_path, cid, vid)  # must not raise
    assert _chub_sources(tmp_path, cid)[vid] == ""


def test_chub_source_setters_require_known_character_and_version(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    with pytest.raises(ch.CharacterNotFound):
        ch.set_chub_source(tmp_path, "nobody", vid, "creator/slug")
    with pytest.raises(ch.CharacterNotFound):
        ch.clear_chub_source(tmp_path, "nobody", vid)
    with pytest.raises(ch.VersionNotFound):
        ch.set_chub_source(tmp_path, cid, "ghost", "creator/slug")
    with pytest.raises(ch.VersionNotFound):
        ch.clear_chub_source(tmp_path, cid, "ghost")


def test_chub_source_is_per_version(tmp_path):
    # each variant of a character gets its own link -- setting one version's
    # chub_source must never leak onto a sibling version.
    cid, v1 = ch.create_character(tmp_path, "Abelha", "main")
    v2 = ch.create_version(tmp_path, cid, "futa", ch.blank_card("Abelha"))
    ch.set_chub_source(tmp_path, cid, v1, "creator/abelha-main")
    sources = _chub_sources(tmp_path, cid)
    assert sources[v1] == "creator/abelha-main"
    assert sources[v2] == ""


def test_is_chub_flag_distinguishes_chub_links_from_arbitrary_urls(tmp_path):
    cid, chub_vid = ch.create_character(tmp_path, "Abelha", "main")
    ch.set_chub_source(tmp_path, cid, chub_vid, "creator/abelha")
    direct_vid = ch.create_version(tmp_path, cid, "direct", ch.blank_card("Abelha"))
    ch.set_chub_source(tmp_path, cid, direct_vid, "https://example.com/card.png")
    unlinked_vid = ch.create_version(tmp_path, cid, "unlinked", ch.blank_card("Abelha"))

    by_id = {v["id"]: v for v in ch.read_character(tmp_path, cid)["versions"]}
    assert by_id[chub_vid]["is_chub"] is True
    assert by_id[direct_vid]["is_chub"] is False
    assert by_id[unlinked_vid]["is_chub"] is False


def test_chub_source_legacy_character_level_value_falls_back_to_default_version(tmp_path):
    # Simulates data written before chub_source became per-version: a value
    # sitting in character.md frontmatter with no per-version value yet. It
    # should surface only on the default version, never on a sibling.
    from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter

    cid, default_vid = ch.create_character(tmp_path, "Abelha", "main")
    extra_vid = ch.create_version(tmp_path, cid, "futa", ch.blank_card("Abelha"))
    meta_path = tmp_path / "characters" / cid / "character.md"
    meta, _ = parse_frontmatter(meta_path.read_text(encoding="utf-8"))
    meta["chub_source"] = "creator/legacy-link"
    meta_path.write_text(dump_frontmatter(meta, ""), encoding="utf-8")

    sources = _chub_sources(tmp_path, cid)
    assert sources[default_vid] == "creator/legacy-link"
    assert sources[extra_vid] == ""


def test_find_unlinked_versions_lists_only_versions_with_no_chub_source(tmp_path):
    linked_cid, linked_vid = ch.create_character(tmp_path, "Abelha", "main")
    ch.set_chub_source(tmp_path, linked_cid, linked_vid, "creator/abelha")

    partial_cid, main_vid = ch.create_character(tmp_path, "Kalinci", "main")
    ch.set_chub_source(tmp_path, partial_cid, main_vid, "creator/kalinci")
    futa_vid = ch.create_version(tmp_path, partial_cid, "futa", ch.blank_card("Kalinci (futa)"))

    unlinked_cid, unlinked_vid = ch.create_character(tmp_path, "Loose End")

    result = ch.find_unlinked_versions(tmp_path)
    # The version names here are ids rather than card names since the label
    # chain dropped its card-name fallback. That is the point of the change and
    # not a loss: the chip these rows feed reads `character_name
    # (version_name)`, so this pair used to render "Kalinci (Kalinci (futa))"
    # and "Loose End (Loose End)".
    assert result == [
        {"character": partial_cid, "character_name": "Kalinci", "version": futa_vid, "version_name": "futa"},
        {"character": unlinked_cid, "character_name": "Loose End", "version": unlinked_vid, "version_name": "default"},
    ]


def test_find_unlinked_versions_empty_world(tmp_path):
    assert ch.find_unlinked_versions(tmp_path) == []


def test_find_unlinked_versions_all_linked(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    ch.set_chub_source(tmp_path, cid, vid, "creator/abelha")
    assert ch.find_unlinked_versions(tmp_path) == []


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
    assert _chub_sources(tmp_path, cid)[vid] == "https://chub.ai/characters/creator/imp"
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
    assert result["updated"] is False
    assert {v["id"] for v in ch.read_character(tmp_path, cid)["versions"]} == {"default", result["version"]}


def test_import_from_chub_into_existing_character_names_the_new_version(tmp_path, monkeypatch):
    from grimoire.store import cards, chub

    cid, _ = ch.create_character(tmp_path, "Seraphine")
    png = cards.dumps(ch.blank_card("Variant"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/variant/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/variant", into_cid=cid,
                                 version_name="after the flood")

    # The importer's name decides both the slug and what the list shows --
    # without it the version is called after the card, so every card pulled
    # from one creator's page lands indistinguishable from the last.
    assert result["version"] == "after-the-flood"
    versions = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert versions["after-the-flood"] == "after the flood"


def test_import_from_chub_into_matching_chub_source_updates_in_place(tmp_path, monkeypatch):
    from grimoire.store import assets, cards, chub

    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    ch.set_chub_source(tmp_path, cid, vid, "creator/abelha")
    png = cards.dumps(ch.blank_card("Abelha Updated"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/abelha/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/abelha", into_cid=cid, into_vid=vid)

    assert result == {
        "character": cid, "version": vid, "updated": True,
        "gallery": {"attempted": 0, "stored": 0},
        "lore": {"lorebooks_found": 0, "created": []},
    }
    detail = ch.read_character(tmp_path, cid)
    assert {v["id"] for v in detail["versions"]} == {vid}  # no new version created
    assert detail["versions"][0]["card"]["data"]["name"] == "Abelha Updated"
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR) is not None  # avatar overwritten


def test_import_from_chub_into_mismatched_chub_source_creates_new_version(tmp_path, monkeypatch):
    from grimoire.store import cards, chub

    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    ch.set_chub_source(tmp_path, cid, vid, "creator/a-different-card")
    png = cards.dumps(ch.blank_card("Variant"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/abelha/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/abelha", into_cid=cid, into_vid=vid)
    assert result["updated"] is False
    assert result["version"] != vid
    assert {v["id"] for v in ch.read_character(tmp_path, cid)["versions"]} == {vid, result["version"]}


def test_import_from_chub_into_without_version_creates_new_version(tmp_path, monkeypatch):
    # chub_source matches, but the caller didn't say which version is "open" --
    # without into_vid there's nothing to safely overwrite, so default to create.
    from grimoire.store import cards, chub

    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    ch.set_chub_source(tmp_path, cid, vid, "creator/abelha")
    png = cards.dumps(ch.blank_card("Refreshed"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/abelha/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/abelha", into_cid=cid)
    assert result["updated"] is False
    assert result["version"] != vid


def test_import_from_chub_matching_source_on_a_sibling_version_still_creates(tmp_path, monkeypatch):
    # the character has two versions; only "main" is linked to this chub.ai
    # card. Targeting the *other* version ("futa") must not be treated as a
    # match just because some sibling version happens to share the link.
    from grimoire.store import cards, chub

    cid, main_vid = ch.create_character(tmp_path, "Abelha", "main")
    futa_vid = ch.create_version(tmp_path, cid, "futa", ch.blank_card("Abelha"))
    ch.set_chub_source(tmp_path, cid, main_vid, "creator/abelha")
    png = cards.dumps(ch.blank_card("Refreshed"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/abelha/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/abelha", into_cid=cid, into_vid=futa_vid)
    assert result["updated"] is False
    assert result["version"] not in (main_vid, futa_vid)  # a third, new version


def test_import_from_chub_into_unknown_version_raises(tmp_path, monkeypatch):
    from grimoire.store import cards, chub

    cid, _ = ch.create_character(tmp_path, "Abelha", "main")
    png = cards.dumps(ch.blank_card("Imp"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))
    with pytest.raises(ch.VersionNotFound):
        ch.import_from_chub(tmp_path, "creator/imp", into_cid=cid, into_vid="ghost")


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


def test_import_from_chub_direct_url_png_creates_character_with_no_gallery_or_lore(tmp_path, monkeypatch):
    from grimoire.store import assets, cards

    png = cards.dumps(ch.blank_card("Direct"), "png")
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "https://example.com/card.png")

    assert result["updated"] is False
    assert result["gallery"] == {"attempted": 0, "stored": 0}
    assert result["lore"] == {"lorebooks_found": 0, "created": []}
    cid, vid = result["character"], result["version"]
    assert ch.read_card(tmp_path, cid, vid)["data"]["name"] == "Direct"
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR) is not None
    sources = _chub_sources(tmp_path, cid)
    assert sources[vid] == "https://example.com/card.png"


def test_import_from_chub_direct_url_json_creates_character(tmp_path, monkeypatch):
    import json as _json

    card = ch.blank_card("Direct JSON")
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (_json.dumps(card).encode(), "application/json"))

    result = ch.import_from_chub(tmp_path, "https://example.com/card.json")
    assert ch.read_card(tmp_path, result["character"], result["version"])["data"]["name"] == "Direct JSON"


def test_import_from_chub_direct_url_unparseable_content_raises(tmp_path, monkeypatch):
    from grimoire.store import chub

    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"just some text", "text/plain"))
    with pytest.raises(chub.ChubFetchError):
        ch.import_from_chub(tmp_path, "https://example.com/nope")


def test_import_from_chub_direct_url_non_dict_json_raises(tmp_path, monkeypatch):
    from grimoire.store import chub

    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"[1, 2, 3]", "application/json"))
    with pytest.raises(chub.ChubFetchError):
        ch.import_from_chub(tmp_path, "https://example.com/nope.json")


def test_import_from_chub_direct_url_fetch_failure_raises(tmp_path, monkeypatch):
    from grimoire.store import chub

    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(fetch, "_http_get_bytes", boom)
    with pytest.raises(chub.ChubFetchError):
        ch.import_from_chub(tmp_path, "https://example.com/card.png")


def test_import_from_chub_direct_url_updates_in_place_when_already_linked(tmp_path, monkeypatch):
    from grimoire.store import cards

    cid, vid = ch.create_character(tmp_path, "Direct", "main")
    ch.set_chub_source(tmp_path, cid, vid, "https://example.com/card.png")
    png = cards.dumps(ch.blank_card("Direct Updated"), "png")
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "https://example.com/card.png", into_cid=cid, into_vid=vid)

    assert result["updated"] is True
    assert result["version"] == vid
    assert {v["id"] for v in ch.read_character(tmp_path, cid)["versions"]} == {vid}  # no new version
    assert ch.read_card(tmp_path, cid, vid)["data"]["name"] == "Direct Updated"


def _stale_png_and_node(definition):
    """A PNG whose embedded card is a stale revision, plus a chub node carrying
    `definition` (chub regenerates the PNG lazily, so the two can disagree)."""
    from grimoire.store import cards

    stale = ch.blank_card("Imp")
    stale["data"].update({
        "description": "old description",
        "personality": "old personality",
        "first_mes": "old hello",
        "creator_notes": "old notes",
        "alternate_greetings": ["old alt"],
    })
    png = cards.dumps(stale, "png")
    node = {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    }
    if definition is not None:
        node["definition"] = definition
    return png, node


def test_import_from_chub_definition_overrides_stale_png_card(tmp_path, monkeypatch):
    from grimoire.store import chub

    # chub's definition field names predate the tavern spec: `personality` holds
    # the card description, `tavern_personality` the personality, `description`
    # the creator notes.
    png, node = _stale_png_and_node({
        "name": "Imp",
        "personality": "new description",
        "tavern_personality": "new personality",
        "description": "new notes",
        "scenario": "new scenario",
        "first_message": "new hello",
        "example_dialogs": "new examples",
        "system_prompt": "new sys",
        "post_history_instructions": "new phi",
        "alternate_greetings": ["new alt 1", "new alt 2"],
        "embedded_lorebook": {"entries": [{"keys": ["k"], "content": "book"}]},
    })
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: node)
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/imp")

    data = ch.read_card(tmp_path, result["character"], result["version"])["data"]
    assert data["description"] == "new description"
    assert data["personality"] == "new personality"
    assert data["creator_notes"] == "new notes"
    assert data["scenario"] == "new scenario"
    assert data["first_mes"] == "new hello"
    assert data["mes_example"] == "new examples"
    assert data["system_prompt"] == "new sys"
    assert data["post_history_instructions"] == "new phi"
    assert data["alternate_greetings"] == ["new alt 1", "new alt 2"]
    assert data["character_book"] == {"entries": [{"keys": ["k"], "content": "book"}]}


def test_import_from_chub_empty_definition_fields_keep_png_values(tmp_path, monkeypatch):
    from grimoire.store import chub

    # An empty/missing definition field must not wipe the PNG card's value —
    # fail-safe if chub stops populating a field.
    png, node = _stale_png_and_node({
        "personality": "",
        "first_message": "",
        "alternate_greetings": [],
    })
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: node)
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/imp")

    data = ch.read_card(tmp_path, result["character"], result["version"])["data"]
    assert data["description"] == "old description"
    assert data["first_mes"] == "old hello"
    assert data["creator_notes"] == "old notes"
    assert data["alternate_greetings"] == ["old alt"]


def test_import_from_chub_no_definition_keeps_png_card(tmp_path, monkeypatch):
    from grimoire.store import chub

    png, node = _stale_png_and_node(None)
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: node)
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/imp")

    data = ch.read_card(tmp_path, result["character"], result["version"])["data"]
    assert data["description"] == "old description"
    assert data["first_mes"] == "old hello"
    assert data["alternate_greetings"] == ["old alt"]


def test_import_card_bakes_char_macro(tmp_path):
    import json as _json

    card = ch.blank_card("Sera")
    card["data"]["first_mes"] = "{{char}} smiles at {{user}}"
    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")
    assert ch.read_card(tmp_path, cid, vid)["data"]["first_mes"] == "Sera smiles at {{user}}"


def test_import_from_chub_bakes_char_macro_in_definition_fields(tmp_path, monkeypatch):
    from grimoire.store import chub

    png, node = _stale_png_and_node({"name": "Imp", "first_message": "{{char}} bows",
                                     "alternate_greetings": ["{{char}} waves"]})
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: node)
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/imp")

    data = ch.read_card(tmp_path, result["character"], result["version"])["data"]
    assert data["first_mes"] == "Imp bows"
    assert data["alternate_greetings"] == ["Imp waves"]


def test_import_from_chub_definition_applies_on_in_place_update(tmp_path, monkeypatch):
    from grimoire.store import chub

    cid, vid = ch.create_character(tmp_path, "Imp", "main")
    ch.set_chub_source(tmp_path, cid, vid, "creator/imp")
    png, node = _stale_png_and_node({"first_message": "new hello",
                                     "alternate_greetings": ["new alt"]})
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: node)
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/imp", into_cid=cid, into_vid=vid)

    assert result["updated"] is True
    data = ch.read_card(tmp_path, cid, vid)["data"]
    assert data["first_mes"] == "new hello"
    assert data["alternate_greetings"] == ["new alt"]


def test_download_chub_gallery_for_linked_version(tmp_path, monkeypatch):
    from grimoire.store import assets, chub

    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    ch.set_chub_source(tmp_path, cid, vid, "creator/abelha")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": True,
    })
    monkeypatch.setattr(chub, "fetch_gallery_paths", lambda pid: ["https://g/1.jpg", "https://g/2.jpg"])
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"\xff\xd8\xffJPEGDATA", "image/jpeg"))

    result = ch.download_chub_gallery(tmp_path, cid, vid)
    assert result == {"attempted": 2, "stored": 2}
    assert {i["name"] for i in assets.list_images(tmp_path, cid, vid)} == {"gallery_0", "gallery_1"}


def test_download_chub_gallery_clears_stale_images_from_a_shrinking_gallery(tmp_path, monkeypatch):
    from grimoire.store import assets, chub

    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    ch.set_chub_source(tmp_path, cid, vid, "creator/abelha")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {"id": 1, "hasGallery": True})
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"\xff\xd8\xffJPEGDATA", "image/jpeg"))

    monkeypatch.setattr(chub, "fetch_gallery_paths",
                        lambda pid: ["https://g/1.jpg", "https://g/2.jpg", "https://g/3.jpg"])
    ch.download_chub_gallery(tmp_path, cid, vid)
    assert {i["name"] for i in assets.list_images(tmp_path, cid, vid)} == {
        "gallery_0", "gallery_1", "gallery_2"}

    monkeypatch.setattr(chub, "fetch_gallery_paths", lambda pid: ["https://g/1.jpg"])
    ch.download_chub_gallery(tmp_path, cid, vid)
    # gallery_1 and gallery_2 from the larger first run must not linger
    assert {i["name"] for i in assets.list_images(tmp_path, cid, vid)} == {"gallery_0"}


def test_download_chub_gallery_no_gallery_on_chub(tmp_path, monkeypatch):
    from grimoire.store import chub

    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    ch.set_chub_source(tmp_path, cid, vid, "creator/abelha")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {"id": 1, "hasGallery": False})

    assert ch.download_chub_gallery(tmp_path, cid, vid) == {"attempted": 0, "stored": 0}


def test_download_chub_gallery_unlinked_version_raises(tmp_path):
    from grimoire.store import chub

    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    with pytest.raises(chub.ChubFetchError):
        ch.download_chub_gallery(tmp_path, cid, vid)


def test_download_chub_gallery_linked_to_a_non_chub_url_raises(tmp_path):
    # gallery/lorebook downloads only make sense for a chub.ai-recognized
    # link -- a version linked to some other site's URL has neither.
    from grimoire.store import chub

    cid, vid = ch.create_character(tmp_path, "Direct", "main")
    ch.set_chub_source(tmp_path, cid, vid, "https://example.com/card.png")
    with pytest.raises(chub.ChubFetchError):
        ch.download_chub_gallery(tmp_path, cid, vid)
    with pytest.raises(chub.ChubFetchError):
        ch.download_chub_lorebooks(tmp_path, cid, vid)


def test_download_chub_gallery_unknown_character_or_version_raises(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    with pytest.raises(ch.CharacterNotFound):
        ch.download_chub_gallery(tmp_path, "nobody", vid)
    with pytest.raises(ch.VersionNotFound):
        ch.download_chub_gallery(tmp_path, cid, "ghost")


def test_download_chub_gallery_honors_legacy_character_level_link(tmp_path, monkeypatch):
    # A version whose link only exists via the legacy character.md fallback
    # (read_character's display path) must still work for an explicit
    # gallery/lorebook download -- not just for showing the link in the UI.
    from grimoire.store import chub
    from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter

    cid, default_vid = ch.create_character(tmp_path, "Abelha", "main")
    meta_path = tmp_path / "characters" / cid / "character.md"
    meta, _ = parse_frontmatter(meta_path.read_text(encoding="utf-8"))
    meta["chub_source"] = "creator/legacy-link"
    meta_path.write_text(dump_frontmatter(meta, ""), encoding="utf-8")

    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {"id": 1, "hasGallery": False})
    assert ch.download_chub_gallery(tmp_path, cid, default_vid) == {"attempted": 0, "stored": 0}


def test_download_chub_gallery_stream_yields_progress_per_image(tmp_path, monkeypatch):
    from grimoire.store import chub

    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    node = {"id": 1, "hasGallery": True}
    monkeypatch.setattr(chub, "fetch_gallery_paths", lambda pid: ["https://g/1.jpg", "https://g/2.jpg"])
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"\xff\xd8\xffJPEGDATA", "image/jpeg"))

    events = list(ch.download_chub_gallery_stream(tmp_path, cid, vid, node))
    assert events == [
        {"total": 2},
        {"done": 1, "total": 2},
        {"done": 2, "total": 2},
        {"summary": {"attempted": 2, "stored": 2}},
    ]


def test_download_chub_gallery_stream_one_image_fails(tmp_path, monkeypatch):
    from grimoire.store import chub

    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    node = {"id": 1, "hasGallery": True}
    monkeypatch.setattr(chub, "fetch_gallery_paths", lambda pid: ["https://g/1.jpg", "https://g/2.jpg"])

    def fake_get_bytes(url):
        if url == "https://g/1.jpg":
            raise RuntimeError("one image failed")
        return (b"\xff\xd8\xffJPEGDATA", "image/jpeg")

    monkeypatch.setattr(fetch, "_http_get_bytes", fake_get_bytes)

    events = list(ch.download_chub_gallery_stream(tmp_path, cid, vid, node))
    assert events[-1] == {"summary": {"attempted": 2, "stored": 1}}


def test_download_chub_gallery_stream_no_gallery_short_circuits(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    events = list(ch.download_chub_gallery_stream(tmp_path, cid, vid, {"id": 1, "hasGallery": False}))
    assert events == [{"total": 0}, {"summary": {"attempted": 0, "stored": 0}}]


def test_download_chub_lorebooks_for_linked_version(tmp_path, monkeypatch):
    from grimoire.store import chub

    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    ch.set_chub_source(tmp_path, cid, vid, "creator/abelha")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "related_lorebooks": [7, 7, -1],
    })
    monkeypatch.setattr(chub, "fetch_lorebook_node", lambda lid: {
        "definition": {"embedded_lorebook": {"entries": [{"keys": ["k"], "content": "lore body"}]}},
    })

    result = ch.download_chub_lorebooks(tmp_path, cid, vid)
    assert result["lorebooks_found"] == 1  # [7, 7, -1] dedup'd to one positive id
    assert len(result["created"]) == 1


def test_download_chub_lorebooks_unlinked_version_raises(tmp_path):
    from grimoire.store import chub

    cid, vid = ch.create_character(tmp_path, "Abelha", "main")
    with pytest.raises(chub.ChubFetchError):
        ch.download_chub_lorebooks(tmp_path, cid, vid)


def test_list_characters_counts_gallery_and_localized(tmp_path):
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Sera")
    row = ch.list_characters(tmp_path)[0]
    assert (row["gallery_count"], row["localized_count"]) == (0, 0)
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"a", "png")
    row = ch.list_characters(tmp_path)[0]  # avatar alone counts as nothing
    assert (row["gallery_count"], row["localized_count"]) == (0, 0)
    assets.put_image(tmp_path, cid, vid, "gallery_1", b"g", "png")
    assets.put_image(tmp_path, cid, vid, "gallery_2", b"g", "png")
    assets.put_image(tmp_path, cid, vid, "embed-abc123def456", b"e", "png")
    row = ch.list_characters(tmp_path)[0]
    assert (row["gallery_count"], row["localized_count"]) == (2, 1)
    assert row["has_avatar"] is True


def test_list_characters_counts_greetings_of_default_version(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Sera")
    assert ch.list_characters(tmp_path)[0]["greeting_count"] == 0  # blank card

    card = ch.read_card(tmp_path, cid, vid)
    card["data"]["first_mes"] = "hello"
    card["data"]["alternate_greetings"] = ["alt one", "alt two"]
    ch.update_version(tmp_path, cid, vid, card)
    assert ch.list_characters(tmp_path)[0]["greeting_count"] == 3  # first_mes + 2 alts


def test_avatar_focus_exposed_on_read_and_list(tmp_path):
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Sera")
    assert ch.read_character(tmp_path, cid)["versions"][0]["avatar_focus"] is None
    assert ch.list_characters(tmp_path)[0]["avatar_focus"] is None
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"a", "png")
    assets.write_focus(tmp_path, cid, vid, 20)
    assert ch.read_character(tmp_path, cid)["versions"][0]["avatar_focus"] == 20
    assert ch.list_characters(tmp_path)[0]["avatar_focus"] == 20


def test_dir_hash_tracks_meta_and_versions_not_assets(tmp_path):
    assert ch.dir_hash(tmp_path, "nope") is None
    cid, vid = ch.create_character(tmp_path, "Mara")
    h1 = ch.dir_hash(tmp_path, cid)
    assert h1
    ch.create_version(tmp_path, cid, "grim", ch.blank_card("Mara"))
    h2 = ch.dir_hash(tmp_path, cid)
    assert h2 != h1
    # an assets-only change does not move the hash
    (tmp_path / "characters" / cid / "assets").mkdir()
    (tmp_path / "characters" / cid / "assets" / "x.png").write_bytes(b"png")
    assert ch.dir_hash(tmp_path, cid) == h2


def _age_tree(root):
    """Back-date every file past statcache's racy window so caches may hold them."""
    import os
    import time

    from grimoire.store import statcache

    old = time.time_ns() - 2 * statcache.RACY_WINDOW_NS
    for f in root.rglob("*"):
        if f.is_file():
            os.utime(f, ns=(old, old))


def test_list_characters_reads_no_cards_when_unchanged(tmp_path, monkeypatch):
    from pathlib import Path

    cid, vid = ch.create_character(tmp_path, "Ada")
    ch.create_version(tmp_path, cid, "alt", ch.blank_card("Ada"))
    _age_tree(tmp_path)
    ch.list_characters(tmp_path)  # warm the summary cache
    reads: list[str] = []
    orig = Path.read_text

    def counting(self, *args, **kwargs):
        reads.append(str(self))
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    out = ch.list_characters(tmp_path)
    assert [r for r in reads if r.endswith(".json")] == []
    assert out[0]["greeting_count"] == 0
    assert {v["id"] for v in out[0]["versions"]} == {"default", "alt"}


def test_list_characters_reflects_card_edits_after_warm_cache(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Ada")
    assert ch.list_characters(tmp_path)[0]["greeting_count"] == 0  # warm
    card = ch.read_card(tmp_path, cid, vid)
    card["data"]["first_mes"] = "hello there"
    card["data"]["extensions"]["grimoire_label"] = "Custom Label"
    ch.update_version(tmp_path, cid, vid, card)
    got = ch.list_characters(tmp_path)[0]
    assert got["greeting_count"] == 1
    assert got["versions"][0]["name"] == "Custom Label"


def test_find_unlinked_versions_reads_no_cards_when_unchanged(tmp_path, monkeypatch):
    from pathlib import Path

    cid, vid = ch.create_character(tmp_path, "Ada")
    ch.set_chub_source(tmp_path, cid, vid, "https://chub.ai/characters/a/b")
    ch.create_version(tmp_path, cid, "alt", ch.blank_card("Ada"))
    _age_tree(tmp_path)
    ch.find_unlinked_versions(tmp_path)  # warm
    reads: list[str] = []
    orig = Path.read_text

    def counting(self, *args, **kwargs):
        reads.append(str(self))
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    out = ch.find_unlinked_versions(tmp_path)
    assert [r for r in reads if r.endswith(".json")] == []
    assert [v["version"] for v in out] == ["alt"]


# ---- export: the avatar rides along, the stored card does not move (#25) ----


def _avatar_png(w: int = 2, h: int = 2) -> bytes:
    """A PNG with real pixels, unlike the 1x1 placeholder `cards.dumps` writes."""
    import struct
    import zlib

    def chunk(typ: bytes, payload: bytes) -> bytes:
        body = typ + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _image_plane(png: bytes) -> bytes:
    """Everything but the tEXt chunks: PNG export writes the card into one, so a
    re-imported avatar is never byte-equal to the original even when the picture
    is."""
    import struct

    out, pos = b"", 8
    while pos + 8 <= len(png):
        (length,) = struct.unpack(">I", png[pos:pos + 4])
        end = pos + 12 + length
        if png[pos + 4:pos + 8] != b"tEXt":
            out += png[pos:end]
        pos = end
    return out


@pytest.mark.parametrize("fmt", ["json", "png", "charx"])
def test_export_roundtrips_the_stored_avatar(tmp_path, fmt):
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    avatar = _avatar_png(4, 3)
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, avatar, "png")

    blob, _filename = ch.export_card(tmp_path, cid, vid, fmt)

    dest = tmp_path / "elsewhere"
    dest.mkdir()
    new_cid, new_vid = ch.import_card(dest, blob, fmt)
    p = assets.image_path(dest, new_cid, new_vid, assets.AVATAR)
    assert p is not None
    assert _image_plane(p.read_bytes()) == _image_plane(avatar)


def test_export_names_the_avatar_from_its_bytes_not_its_stored_suffix(tmp_path):
    """#321: the exported card's `data:` mime and its CHARX member name both
    come from the stored avatar's extension, so a JPEG a store holds as
    `avatar.png` used to leave the app still claiming to be a PNG."""
    import json
    import zipfile
    from io import BytesIO

    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    jpg = b"\xff\xd8\xff" + b"JPEGDATA"
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, jpg, "png")  # misnamed on disk

    card = json.loads(ch.export_card(tmp_path, cid, vid, "json")[0])
    icon = card["data"]["assets"][0]
    assert icon["ext"] == "jpg" and icon["uri"].startswith("data:image/jpeg;base64,")

    z = zipfile.ZipFile(BytesIO(ch.export_card(tmp_path, cid, vid, "charx")[0]))
    member = json.loads(z.read("card.json"))["data"]["assets"][0]["uri"]
    assert member.endswith(".jpg") and z.read(member.split("://", 1)[1]) == jpg


def test_png_export_roundtrips_a_non_png_avatar_through_the_card(tmp_path):
    # The image plane can only hold a PNG, so a jpg avatar travels in the card's
    # `assets` instead -- and the import has to prefer that over the placeholder
    # pixels it would otherwise adopt as the avatar.
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    jpg = b"\xff\xd8\xff" + b"JPEGDATA"
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, jpg, "jpg")

    blob, _filename = ch.export_card(tmp_path, cid, vid, "png")

    dest = tmp_path / "elsewhere"
    dest.mkdir()
    new_cid, new_vid = ch.import_card(dest, blob, "png")
    p = assets.image_path(dest, new_cid, new_vid, assets.AVATAR)
    assert p is not None and p.read_bytes() == jpg and p.suffix == ".jpg"


def test_export_leaves_the_stored_card_and_its_hash_alone(tmp_path):
    # Images are deliberately outside the card hash (sync must not see an avatar
    # edit), so embedding one at export time must not reach the stored card.
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, _avatar_png(), "png")
    before_hash = ch.card_hash(tmp_path, cid, vid)
    before_text = ch._card_path(tmp_path, cid, vid).read_text(encoding="utf-8")

    for fmt in ("json", "png", "charx"):
        ch.export_card(tmp_path, cid, vid, fmt)

    assert ch._card_path(tmp_path, cid, vid).read_text(encoding="utf-8") == before_text
    assert ch.card_hash(tmp_path, cid, vid) == before_hash


@pytest.mark.parametrize("fmt", ["json", "png", "charx"])
def test_import_keeps_the_embedded_avatar_out_of_the_stored_card(tmp_path, fmt):
    # The consumed asset entry is dropped once the bytes are in the asset store:
    # keeping it would bloat every card with base64 and give a re-imported
    # character a different card_hash from the one it was exported from.
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, _avatar_png(), "png")
    blob, _filename = ch.export_card(tmp_path, cid, vid, fmt)

    dest = tmp_path / "elsewhere"
    dest.mkdir()
    new_cid, new_vid = ch.import_card(dest, blob, fmt)

    card = ch.read_card(dest, new_cid, new_vid)
    assert card["data"].get("assets", []) == []
    assert ch.card_hash(dest, new_cid, new_vid) == ch.card_hash(tmp_path, cid, vid)


def test_export_without_an_avatar_is_the_bare_card(tmp_path):
    from grimoire.store import cards
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    for fmt in ("json", "png", "charx"):
        blob, _filename = ch.export_card(tmp_path, cid, vid, fmt)
        assert blob == cards.dumps(ch.read_card(tmp_path, cid, vid), fmt)


def test_export_keeps_a_remote_avatar_reference_across_a_round_trip(tmp_path):
    # An import stores the picture and leaves the URL in the card. Export must
    # not strip that provenance to make room for its own entry: the character
    # would come back from a round trip hashing differently (Codex review).
    from grimoire.store import assets
    card = ch.blank_card("Seraphine")
    card["data"]["assets"] = [{"type": "icon", "uri": "https://x/pic.png", "name": "main", "ext": "png"}]
    cid, vid = ch.create_character(tmp_path, "Seraphine", card=card)
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, _avatar_png(), "png")

    blob, _filename = ch.export_card(tmp_path, cid, vid, "json")

    dest = tmp_path / "elsewhere"
    dest.mkdir()
    new_cid, new_vid = ch.import_card(dest, blob, "json")
    assert [a["uri"] for a in ch.read_card(dest, new_cid, new_vid)["data"]["assets"]] \
        == ["https://x/pic.png"]
    assert ch.card_hash(dest, new_cid, new_vid) == ch.card_hash(tmp_path, cid, vid)


def test_import_keeps_the_long_standing_candidate_order(tmp_path, monkeypatch):
    # An embedded copy does NOT jump the queue: candidates resolve in the order
    # the card lists them, exactly as before #25 -- our own exports put their
    # entry first, so nothing about the round trip needs that precedence changed.
    import base64 as _b64
    import json as _json

    from grimoire.store import assets
    card = ch.blank_card("Seraphine")
    card["data"]["assets"] = [
        {"type": "icon", "uri": "https://x/remote.png", "name": "remote", "ext": "png"},
        {"type": "icon", "uri": "data:image/png;base64," + _b64.b64encode(b"\x89PNG\r\n\x1a\nLOCAL").decode(),
         "name": "main", "ext": "png"},
    ]
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"\x89PNG\r\n\x1a\nREMOTE", "image/png"))

    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")

    p = assets.image_path(tmp_path, cid, vid, assets.AVATAR)
    assert p is not None and p.read_bytes() == b"\x89PNG\r\n\x1a\nREMOTE"
    # a remote URL is a pointer, not a copy: nothing is consumed, nothing dropped
    assert [a["name"] for a in ch.read_card(tmp_path, cid, vid)["data"]["assets"]] \
        == ["remote", "main"]


def test_png_import_still_prefers_the_pngs_own_pixels(tmp_path):
    # A third-party card PNG whose payload happens to carry an embedded icon
    # must still be imported as the picture it shows (Codex review) -- the
    # card's copy only wins when the pixels are our own placeholder.
    import base64 as _b64

    from grimoire.store import assets, cards
    card = ch.blank_card("Seraphine")
    card["data"]["assets"] = [
        {"type": "icon", "uri": "data:image/png;base64," + _b64.b64encode(b"\x89PNG\r\n\x1a\nOTHER").decode(),
         "name": "main", "ext": "png"},
    ]
    blob = cards.dumps(card, "png", avatar=(_avatar_png(4, 3), "png"))

    cid, vid = ch.import_card(tmp_path, blob, "png")

    p = assets.image_path(tmp_path, cid, vid, assets.AVATAR)
    # the file (its card chunk stripped by the store), not the card's icon
    assert p is not None and p.read_bytes() == image_sanitize.sanitize(blob)
    # and the entry we did not consume is still on the card
    assert len(ch.read_card(tmp_path, cid, vid)["data"]["assets"]) == 1


def test_round_trip_keeps_a_card_that_already_embedded_its_own_avatar(tmp_path):
    # Export prepends its entry, so a card that already carried the identical
    # data URI ends up listing it twice -- and the import must consume only the
    # copy export added, or the character comes back an asset lighter with a
    # different hash (Codex review).
    import base64 as _b64
    import json as _json

    from grimoire.store import assets
    avatar = _avatar_png()
    uri = "data:image/png;base64," + _b64.b64encode(avatar).decode()
    card = ch.blank_card("Seraphine")
    card["data"]["assets"] = [{"type": "icon", "uri": uri, "name": "main", "ext": "png"}]
    cid, vid = ch.create_character(tmp_path, "Seraphine", card=_json.loads(_json.dumps(card)))
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, avatar, "png")

    blob, _filename = ch.export_card(tmp_path, cid, vid, "json")

    dest = tmp_path / "elsewhere"
    dest.mkdir()
    new_cid, new_vid = ch.import_card(dest, blob, "json")
    assert [a["name"] for a in ch.read_card(dest, new_cid, new_vid)["data"]["assets"]] == ["main"]
    assert ch.card_hash(dest, new_cid, new_vid) == ch.card_hash(tmp_path, cid, vid)


def test_import_unhooks_the_reference_it_actually_resolved(tmp_path):
    # `_avatar_candidates` reads data.assets before any bare `avatar` string, so
    # removal has to walk the same order: dropping the other copy would leave
    # the consumed one -- base64 and all -- on the stored card (Codex review).
    import base64 as _b64
    import json as _json
    uri = "data:image/png;base64," + _b64.b64encode(b"\x89PNG\r\n\x1a\nSAME").decode()
    card = ch.blank_card("Seraphine")
    card["data"]["assets"] = [{"type": "icon", "uri": uri, "name": "main", "ext": "png"}]
    card["data"]["avatar"] = uri

    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")

    stored = ch.read_card(tmp_path, cid, vid)["data"]
    assert "assets" not in stored           # the entry that resolved is gone
    assert stored.get("avatar") == uri      # the other copy is not ours to touch


def test_import_keeps_an_asset_of_another_kind_sharing_the_avatar_uri(tmp_path):
    # One image can serve as both icon and background. Consuming the icon must
    # not take the background's reference with it (Codex review).
    import base64 as _b64
    import json as _json
    uri = "data:image/png;base64," + _b64.b64encode(b"\x89PNG\r\n\x1a\nSHARED").decode()
    card = ch.blank_card("Seraphine")
    card["data"]["assets"] = [{"type": "icon", "uri": uri, "name": "main", "ext": "png"},
                              {"type": "background", "uri": uri, "name": "bg", "ext": "png"}]

    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")

    kept = ch.read_card(tmp_path, cid, vid)["data"]["assets"]
    assert [a["type"] for a in kept] == ["background"]


def test_import_bounds_the_bytes_one_card_can_inflate(tmp_path, monkeypatch):
    # A hostile CHARX can list over-compressed members as its avatar many times
    # over. Every miss costs a decompression, so one byte budget is spent across
    # the whole walk rather than granted per candidate (Codex review).
    import json as _json
    import zipfile as _zip
    from io import BytesIO

    from grimoire.store import cards
    card = ch.blank_card("Seraphine")
    card["data"]["assets"] = [
        {"type": "icon", "uri": f"embeded://assets/decoy_{i}.png", "name": f"d{i}", "ext": "png"}
        for i in range(50)
    ]
    buf = BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        z.writestr("card.json", _json.dumps(card))
        for i in range(50):
            z.writestr(f"assets/decoy_{i}.png", b"\x00" * 8192)  # not an image: never resolves

    inflated, lookups = 0, 0
    real = cards.read_charx_asset

    def counting(data: bytes, path: str, max_bytes: int = cards.MAX_ASSET_BYTES):
        nonlocal inflated, lookups
        lookups += 1
        blob = real(data, path, max_bytes)
        inflated += len(blob or b"")
        return blob

    monkeypatch.setattr(cards, "MAX_ASSET_BYTES", 20_000)
    monkeypatch.setattr(cards, "read_charx_asset", counting)
    ch.import_card(tmp_path, buf.getvalue(), "charx")

    assert 0 < inflated <= 20_000  # two decoys' worth, not fifty
    assert lookups <= ch._MAX_BUNDLED_READS


def test_import_bounds_lookups_even_when_no_bytes_are_read(tmp_path):
    # Empty members cost no budget, so a byte cap alone bounds nothing: every
    # candidate would still re-parse the whole archive (Codex review).
    import json as _json
    import zipfile as _zip
    from io import BytesIO

    from grimoire.store import cards
    card = ch.blank_card("Seraphine")
    card["data"]["assets"] = [
        {"type": "icon", "uri": f"embeded://assets/empty_{i}.png", "name": f"e{i}", "ext": "png"}
        for i in range(200)
    ]
    buf = BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        z.writestr("card.json", _json.dumps(card))
        for i in range(200):
            z.writestr(f"assets/empty_{i}.png", b"")

    lookups = 0
    real = cards.read_charx_asset

    def counting(data: bytes, path: str, max_bytes: int = cards.MAX_ASSET_BYTES):
        nonlocal lookups
        lookups += 1
        return real(data, path, max_bytes)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cards, "read_charx_asset", counting)
        ch.import_card(tmp_path, buf.getvalue(), "charx")

    assert lookups <= ch._MAX_BUNDLED_READS


def test_export_filename_is_derived_from_the_card_name_and_version(tmp_path):
    # from the VERSION's card name -- that is the card being exported -- and
    # only the non-default version needs its id to tell the files apart.
    cid, vid = ch.create_character(tmp_path, "Seraphine of the Saltmarch")
    alt = ch.create_version(tmp_path, cid, "Winter Court", ch.blank_card("Winifred"))

    assert ch.export_card(tmp_path, cid, vid, "json")[1] == "seraphine-of-the-saltmarch.json"
    assert ch.export_card(tmp_path, cid, alt, "charx")[1] == f"winifred-{alt}.charx"


def test_export_filename_survives_a_card_name_with_nothing_sluggable(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    card = ch.read_card(tmp_path, cid, vid)
    card["data"]["name"] = "???"
    ch.update_version(tmp_path, cid, vid, card)

    assert ch.export_card(tmp_path, cid, vid, "png")[1] == f"{cid}.png"


def test_set_name_renames_the_container_without_moving_the_id(tmp_path):
    # #13: the container name is what the grid, the cast panel and every
    # meta-name prompt section read; before this it was write-once at creation.
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    ch.set_name(tmp_path, cid, "Winifred")
    detail = ch.read_character(tmp_path, cid)
    assert detail["meta"]["name"] == "Winifred"
    # The id is every reference in the store; a rename must not re-slug it.
    assert detail["meta"]["id"] == cid == "seraphine"
    assert [v["id"] for v in detail["versions"]] == [vid]


def test_set_name_keeps_the_rest_of_the_frontmatter(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Seraphine")
    ch.set_birthdate(tmp_path, cid, "1985-03-14")
    ch.set_name(tmp_path, cid, "Winifred")
    meta = ch.read_character(tmp_path, cid)["meta"]
    assert (meta["birthdate"], meta["default_version"]) == ("1985-03-14", "default")


def test_set_name_on_a_missing_character_raises(tmp_path):
    with pytest.raises(ch.CharacterNotFound):
        ch.set_name(tmp_path, "nobody", "Winifred")


def test_set_name_leaves_the_version_cards_alone(tmp_path):
    # A version's own `data.name` is its rail label (`_version_label`), so a
    # container rename must not reach into sibling cards.
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    ch.set_name(tmp_path, cid, "Winifred")
    assert ch.read_card(tmp_path, cid, vid)["data"]["name"] == "Seraphine"


def test_list_characters_carries_the_avatar_cache_token(tmp_path):
    # The grid tile builds `?v=<token>`, and a `?v=` URL is served immutable
    # (`routes.common._serve_image_file`) -- so the token has to name the
    # BYTES. A value that does not change with the file pins a stale avatar in
    # the browser cache for a year.
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    assert ch.list_characters(tmp_path)[0]["avatar_v"] is None
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"\x89PNG-one", "png")
    first = ch.list_characters(tmp_path)[0]
    assert first["has_avatar"] and first["avatar_v"] == assets.image_version(
        assets.image_path(tmp_path, cid, vid, assets.AVATAR))


def test_avatar_cache_token_changes_when_the_bytes_do(tmp_path):
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"\x89PNG-one", "png")
    before = ch.list_characters(tmp_path)[0]["avatar_v"]
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"\x89PNG-two-longer", "png")
    assert ch.list_characters(tmp_path)[0]["avatar_v"] != before


def test_read_character_carries_a_cache_token_per_image(tmp_path):
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"\x89PNG-one", "png")
    assets.put_image(tmp_path, cid, vid, "gallery_0", b"\x89PNG-two", "png")
    version = ch.read_character(tmp_path, cid)["versions"][0]
    assert sorted(version["images"]) == ["avatar", "gallery_0"]
    assert version["image_v"]["avatar"] == assets.image_version(
        assets.image_path(tmp_path, cid, vid, assets.AVATAR))
    assert version["image_v"]["gallery_0"] != version["image_v"]["avatar"]


def _stale_sibling(tmp_path, cid, vid, ext="gif"):
    """The state `put_in` leaves for a moment and leaves for good if its unlink
    fails: two files sharing one stem. `path_in` resolves newest-wins, and
    self-heals exactly this, so it is reachable state and not a hypothetical."""
    import os

    from grimoire.store import assets
    served = assets.image_path(tmp_path, cid, vid, assets.AVATAR)
    stale = served.with_suffix(f".{ext}")
    stale.write_bytes(b"stale bytes of a different length")
    os.utime(stale, (1, 1))                      # decisively older than the served file
    return assets.image_version(served)


# Both extensions matter: one sorts BEFORE the served file's and one after, so
# a token picked by listing order is wrong in one direction or the other. The
# pair proves the resolution is order-independent rather than luckily aligned.
@pytest.mark.parametrize("ext", ["gif", "webp"])
def test_avatar_token_names_the_file_the_server_actually_serves(tmp_path, ext):
    # `?v=` is served IMMUTABLE for a year. A token taken from the listing's
    # order rather than `image_path`'s resolution names one file while the
    # server returns another -- pinning the wrong bytes, permanently.
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"\x89PNG-served", "png")
    served_v = _stale_sibling(tmp_path, cid, vid, ext)
    assert ch.list_characters(tmp_path)[0]["avatar_v"] == served_v


@pytest.mark.parametrize("ext", ["gif", "webp"])
def test_per_image_token_names_the_file_the_server_actually_serves(tmp_path, ext):
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"\x89PNG-served", "png")
    served_v = _stale_sibling(tmp_path, cid, vid, ext)
    version = ch.read_character(tmp_path, cid)["versions"][0]
    assert version["image_v"]["avatar"] == served_v


def test_a_hand_edited_non_object_data_does_not_take_out_the_roster(tmp_path):
    """The store is user-editable markdown and JSON by design, so a card whose
    `data` is a list is something a text editor can produce — and every read of
    it used to raise one attribute access in, taking `list_characters` (and with
    it the whole roster grid) down over a single odd card.

    The write path stays strict: version PUT never stores one. This is only
    about surviving what is already on disk.
    """
    cid, vid = ch.create_character(tmp_path, "Mara", "main")
    (tmp_path / "characters" / cid / f"{vid}.json").write_text(
        '{"data": ["speech"]}', encoding="utf-8")

    rows = ch.list_characters(tmp_path)

    assert [r["id"] for r in rows] == [cid]
    assert rows[0]["greeting_count"] == 0
    assert ch.read_character(tmp_path, cid)["meta"]["id"] == cid


# ---- the voice-anchor backlog (world scope) ----
def test_list_characters_reports_a_written_anchor(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Mara")
    voice_anchors.write(tmp_path, cid, "Clipped. Never contracts.")
    row = next(c for c in ch.list_characters(tmp_path) if c["id"] == cid)
    assert row["has_voice_anchor"] is True


def test_list_characters_reports_a_missing_anchor(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Mara")
    row = next(c for c in ch.list_characters(tmp_path) if c["id"] == cid)
    assert row["has_voice_anchor"] is False


def test_a_world_level_tombstone_reads_as_no_anchor(tmp_path):
    """At world level a tombstone and an absence are the same state -- nothing
    beneath to inherit from -- so both are a gap the backlog should show."""
    cid, _ = ch.create_character(tmp_path, "Mara")
    voice_anchors.disable(tmp_path, cid)
    row = next(c for c in ch.list_characters(tmp_path) if c["id"] == cid)
    assert row["has_voice_anchor"] is False


# ---------------------------------------------------------------- version labels
#
# A version's label used to fall back to the CARD'S NAME, which is the same
# string for every version of a character by construction -- so a character
# with three versions listed the same name three times and the only thing
# telling them apart, the slug, was never shown. The chain is
# `grimoire_label` -> the spec's own `character_version` -> the id.


def test_create_version_stores_its_name_as_the_label(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Winifred Ashcroft")
    vid = ch.create_version(tmp_path, cid, "after the flood", ch.blank_card("Winifred Ashcroft"))
    assert vid == "after-the-flood"
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[vid] == "after the flood"


def test_version_label_never_falls_back_to_the_card_name(tmp_path):
    # The bug this chain replaces: three versions, one name, nothing to pick by.
    cid, first = ch.create_character(tmp_path, "Winifred Ashcroft")
    ch.create_version(tmp_path, cid, "young", ch.blank_card("Winifred Ashcroft"))
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels == {first: "default", "young": "young"}


def test_version_label_falls_back_to_character_version_then_id(tmp_path):
    # `character_version` is the card spec's own field for exactly this, so a
    # third-party card that fills it in is labelled by it without any grimoire
    # metadata being written.
    cid, _ = ch.create_character(tmp_path, "Winifred Ashcroft")
    card = ch.blank_card("Winifred Ashcroft")
    card["data"]["character_version"] = "v2 · rewritten"
    vid = ch.create_version(tmp_path, cid, "second", card)
    # An explicit label still wins over `character_version`.
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[vid] == "second"
    # ...and with no label at all, `character_version` carries it.
    stored = ch.read_card(tmp_path, cid, vid)
    del stored["data"]["extensions"]["grimoire_label"]
    ch.update_version(tmp_path, cid, vid, stored)
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[vid] == "v2 · rewritten"
    # ...and with neither, the id, which at least differs per version.
    stored = ch.read_card(tmp_path, cid, vid)
    del stored["data"]["character_version"]
    ch.update_version(tmp_path, cid, vid, stored)
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[vid] == "second"


def test_a_label_survives_a_card_update(tmp_path):
    # The silent-loss case. The client PUTs WHOLE CARDS, so a label stored in
    # extensions is only as durable as the client's round trip -- and renaming
    # a version is itself a card update, which would be self-defeating if the
    # write path dropped what it was setting.
    cid, _ = ch.create_character(tmp_path, "Winifred Ashcroft")
    vid = ch.create_version(tmp_path, cid, "young", ch.blank_card("Winifred Ashcroft"))
    card = ch.read_card(tmp_path, cid, vid)
    card["data"]["description"] = "Twenty-six, and talks more than she will."
    ch.update_version(tmp_path, cid, vid, card)
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[vid] == "young"


def test_create_version_overwrites_a_label_copied_from_its_source(tmp_path):
    # `+ Version` copies the open version's card, labels and all, so a new
    # version made from a labelled one must take its OWN name rather than
    # inheriting the label it was cloned from.
    cid, _ = ch.create_character(tmp_path, "Winifred Ashcroft")
    first = ch.create_version(tmp_path, cid, "young", ch.blank_card("Winifred Ashcroft"))
    second = ch.create_version(tmp_path, cid, "older", ch.read_card(tmp_path, cid, first))
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[second] == "older"
    assert labels[first] == "young"


def test_blank_label_falls_back_rather_than_showing_an_empty_row(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Winifred Ashcroft")
    vid = ch.create_version(tmp_path, cid, "young", ch.blank_card("Winifred Ashcroft"))
    card = ch.read_card(tmp_path, cid, vid)
    card["data"]["extensions"]["grimoire_label"] = "   "
    ch.update_version(tmp_path, cid, vid, card)
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[vid] == "young"      # the id, not ""


def test_import_card_names_the_version_it_creates(tmp_path):
    import json as _json
    cid, _ = ch.create_character(tmp_path, "Winifred Ashcroft")
    card = ch.blank_card("Winifred Ashcroft")
    data = _json.dumps(card).encode()
    _, vid = ch.import_card(tmp_path, data, "json", into_cid=cid, version_name="after the flood")
    assert vid == "after-the-flood"
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[vid] == "after the flood"


def test_import_without_a_version_name_keeps_the_character_version_fallback(tmp_path):
    import json as _json
    cid, _ = ch.create_character(tmp_path, "Winifred Ashcroft")
    card = ch.blank_card("Winifred Ashcroft")
    card["data"]["character_version"] = "chub-v3"
    _, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json", into_cid=cid)
    assert vid == "chub-v3"
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[vid] == "chub-v3"


def test_a_label_the_caller_did_not_choose_never_masks_the_card_version(tmp_path):
    # Importing a card that names its own version as a NEW character used to
    # store "default" as the label -- the placeholder `create_character` uses
    # for a version nobody named -- which then beat the card's own
    # `character_version` in the fallback chain and hid it.
    import json as _json
    card = ch.blank_card("Winifred Ashcroft")
    card["data"]["character_version"] = "young"
    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")
    assert vid == "default"      # the id is unchanged: nothing re-slugs
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[vid] == "young"


def test_an_in_place_re_import_keeps_the_name_the_version_was_given(tmp_path):
    # A chub re-download REPLACES the stored card, and the downloaded one knows
    # nothing about a label somebody typed here.
    import json as _json
    cid, _ = ch.create_character(tmp_path, "Winifred Ashcroft")
    vid = ch.create_version(tmp_path, cid, "after the flood", ch.blank_card("Winifred Ashcroft"))
    fresh = ch.blank_card("Winifred Ashcroft")
    fresh["data"]["description"] = "redownloaded"
    ch.import_card(tmp_path, _json.dumps(fresh).encode(), "json", into_cid=cid, update_vid=vid)
    stored = ch.read_card(tmp_path, cid, vid)
    assert stored["data"]["description"] == "redownloaded"
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[vid] == "after the flood"


def test_a_card_whose_extensions_are_not_an_object_still_reads(tmp_path):
    # The store promises no schema, so `extensions` is whatever a text editor
    # left there. A string used to raise on `.get` and take the whole roster
    # read down rather than falling through to the next source.
    cid, vid = ch.create_character(tmp_path, "Winifred Ashcroft")
    p = tmp_path / "characters" / cid / f"{vid}.json"
    card = json.loads(p.read_text(encoding="utf-8"))
    card["data"]["extensions"] = "legacy"
    card["data"]["character_version"] = "salvaged"
    p.write_text(json.dumps(card), encoding="utf-8")
    labels = {v["id"]: v["name"] for v in ch.read_character(tmp_path, cid)["versions"]}
    assert labels[vid] == "salvaged"
    assert [c["id"] for c in ch.list_characters(tmp_path)] == [cid]


# ---- chub gallery: ingest everything first, replace only after -------------

def _gallery_png(seed: int, size=(8, 6)) -> bytes:
    import io

    from PIL import Image
    im = Image.new("RGB", size, (seed * 40 % 256, seed * 90 % 256, 20))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _gallery_setup(tmp_path, monkeypatch, paths, served):
    from grimoire.store import chub
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(chub, "fetch_gallery_paths", lambda pid: list(paths))

    def fake_download(url):
        got = served[url]
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(fetch, "download_url", fake_download)
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    return cid, vid, {"id": 9, "hasGallery": True}


def _gallery_ids(tmp_path, cid, vid):
    from grimoire.store import assets
    return {i["name"]: assets.image_id(tmp_path, cid, vid, i["name"])
            for i in assets.list_images(tmp_path, cid, vid) if i["name"].startswith("gallery_")}


def test_chub_gallery_failure_midway_keeps_old_gallery(tmp_path, monkeypatch):
    from grimoire.store import assets
    old = [_gallery_png(1), _gallery_png(2)]
    cid, vid, node = _gallery_setup(
        tmp_path, monkeypatch, ["u/1", "u/2"],
        {"u/1": (old[0], "png"), "u/2": (old[1], "png")})
    list(ch.download_chub_gallery_stream(tmp_path, cid, vid, node))
    before = _gallery_ids(tmp_path, cid, vid)
    assert len(before) == 2 and None not in before.values()

    _gallery_setup(tmp_path, monkeypatch, ["n/1", "n/2"],
                   {"n/1": (_gallery_png(7), "png"), "n/2": RuntimeError("network down")})
    with pytest.raises(RuntimeError):
        list(ch.download_chub_gallery_stream(tmp_path, cid, vid, node))

    assert _gallery_ids(tmp_path, cid, vid) == before
    for name in before:
        assert assets.image_path(tmp_path, cid, vid, name) is not None


def test_chub_gallery_replaces_after_download(tmp_path, monkeypatch):
    cid, vid, node = _gallery_setup(
        tmp_path, monkeypatch, ["u/1", "u/2", "u/3"],
        {"u/1": (_gallery_png(1), "png"), "u/2": (_gallery_png(2), "png"),
         "u/3": (_gallery_png(3), "png")})
    events = list(ch.download_chub_gallery_stream(tmp_path, cid, vid, node))
    assert events[0] == {"total": 3}
    assert events[-1] == {"summary": {"attempted": 3, "stored": 3}}
    assert set(_gallery_ids(tmp_path, cid, vid)) == {"gallery_0", "gallery_1", "gallery_2"}

    # The gallery shrank: the slot past the new count goes, the rest re-point.
    _gallery_setup(tmp_path, monkeypatch, ["n/1"], {"n/1": (_gallery_png(9), "png")})
    list(ch.download_chub_gallery_stream(tmp_path, cid, vid, node))
    got = _gallery_ids(tmp_path, cid, vid)
    assert list(got) == ["gallery_0"] and got["gallery_0"] is not None


def test_chub_replaced_slot_drops_description(tmp_path, monkeypatch):
    from grimoire.store import assets, image_descriptions
    cid, vid, node = _gallery_setup(
        tmp_path, monkeypatch, ["u/1", "u/2"],
        {"u/1": (_gallery_png(1), "png"), "u/2": (_gallery_png(2), "png")})
    list(ch.download_chub_gallery_stream(tmp_path, cid, vid, node))
    d = assets.version_dir(tmp_path, cid, vid)
    assets.edit_sidecar(d, assets.DESCRIPTIONS_FILE,
                        {"gallery_0": "kept: same picture", "gallery_1": "dropped: new picture"})

    # slot 0 serves the same bytes again; slot 1 now serves different art
    _gallery_setup(tmp_path, monkeypatch, ["u/1", "n/2"],
                   {"u/1": (_gallery_png(1), "png"), "n/2": (_gallery_png(5), "png")})
    list(ch.download_chub_gallery_stream(tmp_path, cid, vid, node))

    descs = image_descriptions.read_all(tmp_path, cid, vid)
    assert descs.get("gallery_0") == "kept: same picture"
    assert "gallery_1" not in descs


def test_chub_gallery_records_each_url_as_a_source(tmp_path, monkeypatch):
    from grimoire.store import assets, image_store
    cid, vid, node = _gallery_setup(
        tmp_path, monkeypatch, ["https://g.example/1.png"],
        {"https://g.example/1.png": (_gallery_png(1), "png")})
    list(ch.download_chub_gallery_stream(tmp_path, cid, vid, node))
    obj = image_store.read(assets.image_id(tmp_path, cid, vid, "gallery_0"))
    assert obj.raw["sources"] == [{"url": "https://g.example/1.png"}]


def test_import_card_url_avatar_records_source_url(tmp_path, monkeypatch):
    from grimoire.store import assets, image_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    card = ch.blank_card("Mara")
    card["data"]["assets"] = [{"type": "icon", "name": "main", "uri": "https://a.example/mara.png",
                               "ext": "png"}]
    monkeypatch.setattr(fetch, "download_url", lambda url: (_gallery_png(4), "png"))
    cid, vid = ch.import_card(tmp_path, json.dumps(card).encode(), "json")
    obj = image_store.read(assets.image_id(tmp_path, cid, vid, assets.AVATAR))
    assert obj.raw["sources"] == [{"url": "https://a.example/mara.png"}]


def test_png_export_of_jpeg_avatar_embeds_real_pixels(tmp_path):
    import io

    from PIL import Image

    from grimoire.store import assets, cards
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    buf = io.BytesIO()
    Image.new("RGB", (24, 16), (200, 30, 30)).save(buf, "JPEG")
    jpg = buf.getvalue()
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, jpg, "jpg")

    blob, _name = ch.export_card(tmp_path, cid, vid, "png")

    with Image.open(io.BytesIO(blob)) as im:
        assert im.size == (24, 16)  # the avatar's pixels, not the 1x1 placeholder
    assert not cards.is_placeholder_png(blob)
    # the original still travels in the card, as ever
    icon = cards.loads(blob, "png")["data"]["assets"][0]
    assert icon["ext"] == "jpg"


def _real_jpeg(color, size=(24, 16)) -> bytes:
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def test_png_export_of_jpeg_avatar_reimports_the_original_bytes(tmp_path, monkeypatch):
    from grimoire.store import assets
    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(src))
    cid, vid = ch.create_character(src, "Seraphine")
    assets.put_image(src, cid, vid, assets.AVATAR, _real_jpeg((200, 30, 30)), "jpg")
    original = assets.image_path(src, cid, vid, assets.AVATAR).read_bytes()
    source_hash = ch.card_hash(src, cid, vid)
    blob, _name = ch.export_card(src, cid, vid, "png")

    monkeypatch.setenv("GRIMOIRE_HOME", str(dest))  # a fresh store
    new_cid, new_vid = ch.import_card(dest, blob, "png")

    p = assets.image_path(dest, new_cid, new_vid, assets.AVATAR)
    assert p.read_bytes() == original and p.suffix == ".jpg"
    stored = json.dumps(ch.read_card(dest, new_cid, new_vid))
    assert "data:image" not in stored
    assert ch.card_hash(dest, new_cid, new_vid) == source_hash


def test_png_import_keeps_plane_when_carried_avatar_is_a_different_picture(tmp_path, monkeypatch):
    import base64

    from grimoire.store import assets, cards
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    plane = _gallery_png(3, size=(10, 10))
    other = _real_jpeg((10, 200, 10))
    card = ch.blank_card("Mara")
    card["data"]["assets"] = [{
        "type": "icon", "name": "main", "ext": "jpg",
        "uri": "data:image/jpeg;base64," + base64.b64encode(other).decode()}]
    # a third-party style PNG: real pixels, with a card whose icon is another picture
    png = cards.dumps(card, "png", avatar=(plane, "png"))
    cid, vid = ch.import_card(tmp_path, png, "png")
    p = assets.image_path(tmp_path, cid, vid, assets.AVATAR)
    assert p.suffix == ".png" and p.read_bytes() != other


# ---- final review: the PNG plane is the carried avatar as a browser shows it ----

def _noise_rgb(seed: int, size=(24, 16)):
    import random

    from PIL import Image
    rng = random.Random(seed)
    im = Image.new("RGB", size)
    im.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                for _ in range(size[0] * size[1])])
    return im


def _jpeg_bytes(im, **kw) -> bytes:
    import io
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90, **kw)
    return buf.getvalue()


def _exif6_jpeg() -> bytes:
    from PIL import Image
    ex = Image.Exif()
    ex[0x0112] = 6
    return _jpeg_bytes(_noise_rgb(31), exif=ex.tobytes())


def _icc_jpeg() -> bytes:
    from PIL import ImageCms
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    return _jpeg_bytes(_noise_rgb(32), icc_profile=icc)


def _animated_gif() -> bytes:
    import io

    from PIL import Image
    frames = [Image.new("RGB", (12, 10), c) for c in ((255, 0, 0), (0, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:],
                   duration=[90, 140], loop=0)
    return buf.getvalue()


def _round_trip_png(tmp_path, monkeypatch, avatar: bytes, ext: str):
    """Export a character whose avatar is `avatar` as a PNG card, import that
    card into a fresh store. Returns (stored original, exported blob, the
    source card hash, the imported avatar path, the imported card hash)."""
    from grimoire.store import assets
    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(src))
    cid, vid = ch.create_character(src, "Seraphine")
    assets.put_image(src, cid, vid, assets.AVATAR, avatar, ext)
    original = assets.image_path(src, cid, vid, assets.AVATAR).read_bytes()
    source_hash = ch.card_hash(src, cid, vid)
    blob, _name = ch.export_card(src, cid, vid, "png")

    monkeypatch.setenv("GRIMOIRE_HOME", str(dest))  # a fresh store
    new_cid, new_vid = ch.import_card(dest, blob, "png")
    p = assets.image_path(dest, new_cid, new_vid, assets.AVATAR)
    return original, blob, source_hash, p, ch.card_hash(dest, new_cid, new_vid)


def test_png_round_trip_of_an_exif_oriented_jpeg_keeps_the_original(tmp_path, monkeypatch):
    import io

    from PIL import Image, ImageOps
    original, blob, source_hash, p, new_hash = _round_trip_png(
        tmp_path, monkeypatch, _exif6_jpeg(), "jpg")
    assert p.read_bytes() == original and p.suffix == ".jpg"
    assert new_hash == source_hash
    # The plane is drawn upright: the stored JPEG turned the way a browser turns it.
    with Image.open(io.BytesIO(original)) as im:
        upright = ImageOps.exif_transpose(im).convert("RGB")
    with Image.open(io.BytesIO(blob)) as plane:
        assert plane.size == upright.size == (16, 24)
        assert plane.convert("RGB").tobytes() == upright.tobytes()


def test_png_round_trip_of_an_icc_jpeg_keeps_the_original(tmp_path, monkeypatch):
    original, _blob, source_hash, p, new_hash = _round_trip_png(
        tmp_path, monkeypatch, _icc_jpeg(), "jpg")
    assert b"ICC_PROFILE" in original
    assert p.read_bytes() == original and p.suffix == ".jpg"
    assert new_hash == source_hash


def test_png_round_trip_of_an_animated_gif_keeps_the_animation(tmp_path, monkeypatch):
    import io

    from PIL import Image
    original, _blob, source_hash, p, new_hash = _round_trip_png(
        tmp_path, monkeypatch, _animated_gif(), "gif")
    assert p.read_bytes() == original and p.suffix == ".gif"
    with Image.open(io.BytesIO(p.read_bytes())) as im:
        assert im.n_frames == 2
    assert new_hash == source_hash


def test_png_import_keeps_a_plane_that_is_the_carried_picture_turned(tmp_path, monkeypatch):
    """The same raster, but not as a browser shows the carried copy: the plane
    is a different picture on screen, so it stays."""
    import base64
    import io

    from grimoire.store import assets, cards
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    carried = _exif6_jpeg()
    from PIL import Image
    with Image.open(io.BytesIO(carried)) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "PNG")              # NOT turned
    card = ch.blank_card("Mara")
    card["data"]["assets"] = [{
        "type": "icon", "name": "main", "ext": "jpg",
        "uri": "data:image/jpeg;base64," + base64.b64encode(carried).decode()}]
    png = cards.dumps(card, "png", avatar=(buf.getvalue(), "png"))
    cid, vid = ch.import_card(tmp_path, png, "png")
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR).suffix == ".png"


def test_chub_redownload_keeps_the_caption_of_a_legacy_slot(tmp_path, monkeypatch):
    """A gallery slot written before the image store (a bare `gallery_0.png`)
    holding the very picture chub serves again keeps its caption: identity is
    compared for legacy slots too, not only for placements."""
    from grimoire.store import assets, image_descriptions
    picture = _gallery_png(4)
    cid, vid, node = _gallery_setup(tmp_path, monkeypatch, ["u/1"],
                                    {"u/1": (picture, "png")})
    d = assets.version_dir(tmp_path, cid, vid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "gallery_0.png").write_bytes(picture)
    assets.edit_sidecar(d, assets.DESCRIPTIONS_FILE, {"gallery_0": "Mara at the quay"})
    assert assets.image_id(tmp_path, cid, vid, "gallery_0") is None   # legacy, not placed

    list(ch.download_chub_gallery_stream(tmp_path, cid, vid, node))

    assert image_descriptions.read_all(tmp_path, cid, vid).get("gallery_0") == "Mara at the quay"
    assert assets.image_id(tmp_path, cid, vid, "gallery_0") is not None


def _cmyk_jpeg() -> bytes:
    from PIL import Image
    return _jpeg_bytes(Image.merge("CMYK", (*_noise_rgb(33).split(), _noise_rgb(34).getchannel(0))))


def _palette(fmt: str) -> bytes:
    import io
    im = _noise_rgb(35).quantize(colors=16)
    assert im.mode == "P"
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


@pytest.mark.parametrize("make, ext", [
    (_cmyk_jpeg, "jpg"),
    (lambda: _palette("GIF"), "gif"),
    (lambda: _palette("PNG"), "png"),
])
def test_png_round_trip_of_a_cmyk_or_palette_avatar_keeps_the_original(
        tmp_path, monkeypatch, make, ext):
    """A CMYK JPEG's plane is its RGB conversion and a palette image's is its
    expansion, so neither shares a pixel identity with the plane -- which is
    why the plane is compared as decoded pixels (`_shown`), not by id. The
    carried original comes back, not the plane."""
    import io

    from PIL import Image
    avatar = make()
    with Image.open(io.BytesIO(avatar)) as im:
        assert im.mode in ("CMYK", "P")
    original, _blob, source_hash, p, new_hash = _round_trip_png(
        tmp_path, monkeypatch, avatar, ext)
    assert p.read_bytes() == original and p.suffix == f".{ext}"
    assert new_hash == source_hash


def test_chub_gallery_replace_lists_slots_inside_its_lock_hold(tmp_path, monkeypatch):
    """An upload landing between the gallery listing and the lock hold was
    left out of a listing taken before the locks, so it survived the replace
    on a stale view. The listing is taken under the hold now: a slot that
    exists when the replacement takes its locks is replaced like the rest."""
    import contextlib

    from grimoire.store import assets
    cid, vid, node = _gallery_setup(tmp_path, monkeypatch, ["u/1"],
                                    {"u/1": (_gallery_png(1), "png")})
    assets.put_image(tmp_path, cid, vid, "gallery_0", _gallery_png(2), "png")
    real = assets._image_locks_held
    injected = []

    @contextlib.contextmanager
    def upload_first(d, *names):
        if not injected:            # the gap: after any early listing, before the locks
            assets.put_image(tmp_path, cid, vid, "gallery_5", _gallery_png(3), "png")
            injected.append("gallery_5")
        with real(d, *names):
            yield

    monkeypatch.setattr(assets, "_image_locks_held", upload_first)
    list(ch.download_chub_gallery_stream(tmp_path, cid, vid, node))

    assert injected == ["gallery_5"]
    assert set(_gallery_ids(tmp_path, cid, vid)) == {"gallery_0"}


def test_chub_gallery_replace_cannot_wedge_against_a_promotion(tmp_path, monkeypatch):
    """The listing taken under the gallery locks must not heal a stranded
    promotion: healing takes {avatar, slot, promote-tmp} blocking, and a
    promotion holding `avatar` while it waits for `gallery_0` -- which the
    replace holds -- would wedge both for good (ABBA). A stray promote-tmp
    whose rename keeps failing makes every healing listing try."""
    import threading
    import time
    from pathlib import Path

    from grimoire.store import assets, image_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    assets.put_image(tmp_path, cid, vid, "avatar", _gallery_png(1), "png")
    assets.put_image(tmp_path, cid, vid, "gallery_0", _gallery_png(2), "png")
    d = assets.version_dir(tmp_path, cid, vid)
    (d / "promote-tmp.png").write_bytes(_gallery_png(3))      # pre-#253 residue
    real_rename = Path.rename

    def rename(self, target):
        if self.name.startswith("promote-tmp"):
            raise OSError("held by a sync client")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    fresh = [(0, image_store.ingest(_gallery_png(9), "png").id)]
    real_held = assets._image_locks_held
    inside = threading.Event()
    replacer = []

    import contextlib

    @contextlib.contextmanager
    def held(dd, *names):
        with real_held(dd, *names):
            # The replace's own hold (gallery slots only), not a heal's nested
            # inside a listing, which also names `avatar`.
            if (threading.current_thread() in replacer and assets.AVATAR not in names
                    and not inside.is_set()):
                inside.set()
                time.sleep(0.5)      # let the promotion take `avatar` and wait on gallery_0
            yield

    monkeypatch.setattr(assets, "_image_locks_held", held)
    errors = []

    def replace():
        try:
            ch._replace_gallery(tmp_path, cid, vid, fresh)
        except Exception as exc:  # noqa: BLE001 -- reported below
            errors.append(exc)

    def promote():
        inside.wait(5)
        try:
            assets.promote_image(tmp_path, cid, vid, "gallery_0")
        except Exception as exc:  # noqa: BLE001 -- a refusal is fine, a wedge is not
            errors.append(exc)

    a = threading.Thread(target=replace, daemon=True)
    b = threading.Thread(target=promote, daemon=True)
    replacer.append(a)
    a.start()
    b.start()
    a.join(5)
    b.join(5)
    assert inside.is_set()
    assert not a.is_alive() and not b.is_alive(), "replace and promote wedged"
    assert not [e for e in errors if not isinstance(e, OSError)], errors
