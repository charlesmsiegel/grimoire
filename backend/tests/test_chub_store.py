from grimoire.store import chub


def test_parse_full_path_from_url():
    url = "https://chub.ai/characters/Vanlos1/lakshmi-white-snake-a17db356c017"
    assert chub.parse_full_path(url) == "Vanlos1/lakshmi-white-snake-a17db356c017"


def test_parse_full_path_strips_query_and_trailing_slash():
    assert chub.parse_full_path("https://chub.ai/characters/a/b/?ref=share") == "a/b"


def test_parse_full_path_from_bare_path():
    assert chub.parse_full_path("creator/slug") == "creator/slug"


def test_parse_full_path_rejects_garbage():
    assert chub.parse_full_path("not a url") is None
    assert chub.parse_full_path("https://example.com/characters/a/b") is None
    assert chub.parse_full_path("a/b/c") is None
    assert chub.parse_full_path("") is None


def test_fetch_character_node_returns_node(monkeypatch):
    monkeypatch.setattr(chub, "_get_json", lambda url: {"node": {"id": 1, "hasGallery": False}})
    assert chub.fetch_character_node("a/b") == {"id": 1, "hasGallery": False}


def test_fetch_character_node_requests_expected_url(monkeypatch):
    captured = {}

    def fake(url):
        captured["url"] = url
        return {"node": {}}

    monkeypatch.setattr(chub, "_get_json", fake)
    chub.fetch_character_node("creator/slug")
    assert captured["url"] == "https://api.chub.ai/api/characters/creator/slug?full=true"


def test_fetch_character_node_none_on_failure(monkeypatch):
    monkeypatch.setattr(chub, "_get_json", lambda url: None)
    assert chub.fetch_character_node("a/b") is None


def test_fetch_lorebook_node_requests_expected_url(monkeypatch):
    captured = {}

    def fake(url):
        captured["url"] = url
        return {"node": {"id": 7}}

    monkeypatch.setattr(chub, "_get_json", fake)
    assert chub.fetch_lorebook_node(7) == {"id": 7}
    assert captured["url"] == "https://api.chub.ai/api/lorebooks/7?full=true"


def test_fetch_lorebook_node_none_on_failure(monkeypatch):
    monkeypatch.setattr(chub, "_get_json", lambda url: None)
    assert chub.fetch_lorebook_node(7) is None


def test_fetch_gallery_paths_extracts_primary_image_path(monkeypatch):
    captured = {}

    def fake(url):
        captured["url"] = url
        return {"count": 2, "nodes": [
            {"primary_image_path": "https://x/1.jpg"},
            {"primary_image_path": "https://x/2.jpg"},
        ], "page": 1}

    monkeypatch.setattr(chub, "_get_json", fake)
    assert chub.fetch_gallery_paths(42) == ["https://x/1.jpg", "https://x/2.jpg"]
    assert captured["url"] == "https://gateway.chub.ai/api/gallery/project/42?limit=48&count=false"


def test_fetch_gallery_paths_skips_malformed_nodes(monkeypatch):
    monkeypatch.setattr(chub, "_get_json", lambda url: {"nodes": [
        {"primary_image_path": "https://x/1.jpg"},
        {"primary_image_path": ""},
        {},
        "not even a dict",
    ]})
    assert chub.fetch_gallery_paths(42) == ["https://x/1.jpg"]


def test_fetch_gallery_paths_empty_on_failure(monkeypatch):
    monkeypatch.setattr(chub, "_get_json", lambda url: None)
    assert chub.fetch_gallery_paths(42) == []
