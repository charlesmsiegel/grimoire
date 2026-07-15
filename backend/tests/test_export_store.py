"""Campaign export: shared collector + markdown/HTML/text/JSON renderers."""

import io
import json
import zipfile

from grimoire.store import appearances, assets, campaigns, characters, chronicle, entities, export, pcs, scenes, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    cid = campaigns.create_campaign("Run One", wid)
    return wid, cid


def test_rewrite_images_maps_local_and_drops_remote(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    docks = entities.create_entity(croot, "locations", "The Docks", body="piers")
    assets.put_image(croot, docks, "default", "pier", b"pierbytes", "png", base="locations")
    images = export.Images()
    text = (f"Look: ![The docks](/api/campaigns/{cid}/locations/{docks}/images/pier) "
            "and ![lost](https://example.com/x.png)")
    out = export.rewrite_images(text, cid, images)
    assert "![The docks](images/img-000.png)" in out
    assert "lost" in out and "example.com" not in out
    # same file referenced again reuses the entry
    export.rewrite_images(f"![again](/api/campaigns/{cid}/locations/{docks}/images/pier)",
                          cid, images)
    assert len(images.by_path) == 1


def test_rewrite_images_world_fallback(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    # greeting images only ever live world-side
    assets.put_image(wroot, "g1", "default", "vista", b"vistabytes", "jpg", base="greetings")
    images = export.Images()
    out = export.rewrite_images(f"![v](/api/worlds/{wid}/greetings/g1/images/vista)", cid, images)
    assert "![v](images/img-000.jpg)" in out
    # missing file degrades to alt text
    out2 = export.rewrite_images(f"![gone](/api/worlds/{wid}/greetings/g1/images/nope)", cid, images)
    assert out2 == "gone"


def test_rewrite_images_honors_asset_tombstone(monkeypatch, tmp_path):
    from grimoire.store import overlay
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    wroot = worlds.world_root(wid)
    aid, _ = characters.create_character(wroot, "Seraphine")
    assets.put_image(wroot, aid, "default", "avatar", b"worldbytes", "png")
    cid = campaigns.create_campaign("Run One", wid)
    # the campaign deletes the inherited avatar -> only a tombstone, no copy
    overlay.delete_image(cid, aid, "default", "avatar")
    images = export.Images()
    out = export.rewrite_images(
        f"![face](/api/campaigns/{cid}/characters/{aid}/versions/default/images/avatar)", cid, images)
    assert out == "face"   # tombstoned: the world image must not leak into the export
    assert images.by_path == {}


def test_drop_images_strips_to_alt_text():
    text = "See ![a vista](images/img-000.png) and ![missing](https://x.example/y.png) too."
    assert export.drop_images(text) == "See a vista and missing too."


def _fixture_campaign(monkeypatch, tmp_path):
    """World + campaign with 2 scenes, cast, dates, locations, images, epigraph."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    wroot = worlds.world_root(wid)
    card = characters.blank_card("Seraphine")
    card["data"]["description"] = "the drowned keeper"
    card["data"]["personality"] = "grim"
    characters.create_character(wroot, "Seraphine", "default", card)
    pcs.create_pc(wroot, "Elara", [], persona={"name": "Elara", "pronouns": "she/her",
                                               "summary": "scholar", "description": "A wanderer."})
    cid = campaigns.create_campaign("Run One", wid)
    croot = campaigns.campaign_root(cid)
    docks = entities.create_entity(croot, "locations", "The Docks", body="Salt-stained piers.")
    entities.create_entity(croot, "locations", "The Keep", body="Never visited.")
    assets.put_image(croot, docks, "default", "pier", b"pierbytes", "png", base="locations")

    sid1 = scenes.create_scene(cid, "Arrival")
    appearances.appear(cid, sid1, "pcs", "elara", "default", "player")
    appearances.appear(cid, sid1, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid1, "user", "I step off the boat.", speaker="Elara")
    scenes.append_message(cid, sid1, "assistant",
                          f"The docks reek. ![The docks](/api/campaigns/{cid}/locations/{docks}/images/pier) "
                          "![lost](https://example.com/x.png)")
    scenes.append_message(cid, sid1, "assistant", "\"Welcome,\" she says.", speaker="Seraphine")
    sid1 = scenes.set_datetime(cid, sid1, "2025-03-01")["id"]  # renames; appearances repoint
    scenes.set_location(cid, sid1, docks)
    scenes.mark_absorbed(cid, sid1, "They arrive.", "A long summary.")
    chronicle.absorb(cid, {"id": sid1, "one_line": "They arrive.", "summary": "A long summary.",
                           "keywords": [], "cast": [], "location": "The Docks", "date": "2025-03-01"})

    sid2 = scenes.create_scene(cid, "Below")
    scenes.append_message(cid, sid2, "assistant", "Deeper still.")
    return wid, cid, sid1, sid2


def test_collect_chapters_and_appendix(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    data = export.collect(cid)
    assert data["title"] == "Run One"
    assert data["world_name"] == "Saltmarch"
    assert len(data["chapters"]) == 2
    ch1, ch2 = data["chapters"]
    assert ch1["title"] == "Arrival" and ch2["title"] == "Below"
    assert ch1["location"] == "The Docks"
    assert set(ch1["cast"]) == {"Elara", "Seraphine"}
    assert ch1["epigraph"] == "They arrive."
    assert any(m["speaker"] == "Seraphine" for m in ch1["messages"])
    assert "images/img-000.png" in ch1["messages"][1]["content"]
    assert "example.com" not in ch1["messages"][1]["content"]
    names = {e["name"] for e in data["appendix"]}
    assert {"Elara", "Seraphine", "The Docks"} <= names
    assert "The Keep" not in names  # never visited


def test_build_markdown_bundle(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, filename = export.build_markdown_bundle(cid)
    assert filename == f"{cid}-markdown.zip"
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    assert "index.md" in names
    assert "001-arrival.md" in names and "002-below.md" in names
    assert "images/img-000.png" in names
    assert z.read("images/img-000.png") == b"pierbytes"
    ch1 = z.read("001-arrival.md").decode()
    assert "**Seraphine:** \"Welcome,\" she says." in ch1
    assert "![The docks](images/img-000.png)" in ch1
    assert "**Cast:** Seraphine, Elara" in ch1
    assert "> They arrive." in ch1
    assert "actor-pcs-elara.md" in names
    assert "location-the-docks.md" in names
    assert "location-the-keep.md" not in names


def test_build_html_self_contained(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, filename = export.build_html(cid)
    assert filename == f"{cid}.html"
    html = blob.decode()
    assert "<title>Run One</title>" in html
    assert '<span class="speaker">Seraphine</span>' in html
    assert "data:image/png;base64," in html  # image inlined, no external ref
    assert "images/img-000.png" not in html
    assert "example.com" not in html


def test_build_text_transcript(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, filename = export.build_text(cid)
    assert filename == f"{cid}.txt"
    txt = blob.decode()
    assert txt.startswith("Run One\nSaltmarch")
    assert "**Seraphine:** \"Welcome,\" she says." in txt
    assert "The docks reek." in txt
    assert "example.com" not in txt and "img-000" not in txt  # images dropped entirely
    assert "The Docks" in txt  # location header


def test_build_json_dump(monkeypatch, tmp_path):
    _wid, cid, sid1, sid2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, filename = export.build_json(cid)
    assert filename == f"{cid}.json"
    data = json.loads(blob)
    assert data["campaign"]["name"] == "Run One"
    sids = {s["meta"]["id"] for s in data["scenes"]}
    assert sids == {sid1, sid2}
    assert sid1 in data["chronicle"]
    assert data["chronicle"][sid1]["one_line"] == "They arrive."
    roster_refs = {(a["kind"], a["id"]) for a in data["roster"]}
    assert ("pcs", "elara") in roster_refs and ("characters", "seraphine") in roster_refs
    # raw, unresolved image URL -- JSON dump does no image rewriting
    scene1 = next(s for s in data["scenes"] if s["meta"]["id"] == sid1)
    assert any("/api/campaigns/" in m["content"] for m in scene1["messages"])


def test_export_unknown_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    import pytest
    with pytest.raises(campaigns.CampaignNotFound):
        export.collect("nope")
    with pytest.raises(campaigns.CampaignNotFound):
        export.build_markdown_bundle("nope")
    with pytest.raises(campaigns.CampaignNotFound):
        export.build_html("nope")
    with pytest.raises(campaigns.CampaignNotFound):
        export.build_text("nope")
    with pytest.raises(campaigns.CampaignNotFound):
        export.build_json("nope")
