"""Campaign export: shared collector + markdown/HTML/text/JSON renderers."""

import base64
import io
import json
import re
import zipfile

import pytest

from grimoire.store import (
    appearances,
    assets,
    campaign_images,
    campaigns,
    characters,
    chronicle,
    covers,
    entities,
    export,
    image_library,
    pcs,
    scenes,
    worlds,
)


def _img(fmt: str = "PNG", color=(10, 20, 30), size=(8, 8)) -> bytes:
    """A real image.

    Since #321 an export names each packed image from its own bytes and drops
    one whose bytes name no format it can declare, so a fixture that stores a
    marker string is not storing an image the book will carry.
    """
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


def _jpeg(size=(8, 8)) -> bytes:
    return _img("JPEG", (200, 40, 40), size)


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    cid = campaigns.create_campaign("Run One", wid)
    return wid, cid


def test_rewrite_images_maps_local_and_drops_remote(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    docks = entities.create_entity(croot, "locations", "The Docks", body="piers")
    assets.put_image(croot, docks, "default", "pier", _img(), "png", base="locations")
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
    assets.put_image(wroot, "g1", "default", "vista", _jpeg(), "jpg", base="greetings")
    images = export.Images()
    out = export.rewrite_images(f"![v](/api/worlds/{wid}/greetings/g1/images/vista)", cid, images)
    assert "![v](images/img-000.jpg)" in out
    # missing file degrades to alt text
    out2 = export.rewrite_images(f"![gone](/api/worlds/{wid}/greetings/g1/images/nope)", cid, images)
    assert out2 == "gone"


def test_rewrite_images_packs_a_campaign_library_image(monkeypatch, tmp_path):
    """#376: a URL shape missing from `_IMG_URL` is not a rendering bug, it is a
    book shipped with the image silently degraded to its alt text."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    campaign_images.put_image(cid, "coastline", _jpeg(), "jpg")
    images = export.Images()
    out = export.rewrite_images(
        f"![The coast](/api/campaigns/{cid}/images/coastline)", cid, images)
    assert out == "![The coast](images/img-000.jpg)"
    assert list(images.by_path) == [campaign_images.image_path(cid, "coastline")]

    # missing degrades to alt text, like every other shape
    assert export.rewrite_images(
        f"![gone](/api/campaigns/{cid}/images/nope)", cid, images) == "gone"


@pytest.mark.parametrize("name", ["coastline", "The-Inn_2", "海岸線", "a&b", "a,b;c", "a+b=c"])
def test_every_name_the_library_accepts_is_a_url_the_export_can_read_back(monkeypatch, tmp_path, name):
    """The two ends of one rule, checked against each other.

    `image_library.addressable` decides what may be stored; `export._IMG_URL`
    decides what can be resolved back out of a post. They are written in
    different files in different notations, and a name the first accepts and the
    second cannot match is a book that silently drops the image -- so the
    interesting cases are the ones neither author would think to try.
    """
    _wid, cid = _campaign(monkeypatch, tmp_path)
    assert image_library.addressable(name)
    campaign_images.put_image(cid, name, _img(), "png")
    images = export.Images()
    out = export.rewrite_images(f"![{name}](/api/campaigns/{cid}/images/{name})", cid, images)
    assert out == f"![{name}](images/img-000.png)", name


def test_a_forked_campaigns_book_carries_its_own_library_copy(monkeypatch, tmp_path):
    """`store.fork` copies a campaign's text verbatim, so a branch's posts still
    name the campaign they were written in. The book is unaffected: every
    localized URL resolves against the campaign being EXPORTED, not against the
    id written into it, so the fork packs the fork's own bytes."""
    from grimoire.store import fork
    _wid, cid = _campaign(monkeypatch, tmp_path)
    campaign_images.put_image(cid, "coastline", _img(color=(1, 2, 3)), "png")
    branch = fork.fork_campaign(cid, "Branch")["id"]
    campaign_images.put_image(branch, "coastline", _img(color=(9, 9, 9)), "png")

    images = export.Images()
    # the URL still names `cid` -- the source -- because that is what a fork copies
    export.rewrite_images(f"![c](/api/campaigns/{cid}/images/coastline)", branch, images)
    assert list(images.by_path) == [campaign_images.image_path(branch, "coastline")]


def test_a_world_scoped_library_url_is_now_a_shape_the_app_writes(monkeypatch, tmp_path):
    """This shape used to be one nothing served and nothing wrote, and the
    campaign branch was spelled out separately precisely so it could not match.

    The world now has an image library of its own, and a lore body can embed
    one of its pictures -- a body every campaign on that world inherits. A URL
    shape missing from `_IMG_URL` is not a rendering bug, it is a book shipped
    with that image silently degraded to its alt text, so it is matched now and
    the two shapes have separate groups: the group has to say which scope was
    written, and `(?P<lib>images)` captures the segment rather than the scope.
    """
    wid, cid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import world_images
    world_images.put_image(wid, "coastline", _img(), "png")

    m = export._IMG_URL.match(f"/api/worlds/{wid}/images/coastline")
    assert m is not None and m["wlib"] and not m["lib"]

    images = export.Images()
    out = export.rewrite_images(
        f"![c](/api/worlds/{wid}/images/coastline)", cid, images)
    assert out == "![c](images/img-000.png)"
    assert list(images.by_path) == [world_images.image_path(wid, "coastline")]


def test_a_hidden_library_image_degrades_to_alt_text_in_either_url_shape(
        monkeypatch, tmp_path):
    """Both shapes resolve through the campaign's own view of the library, so a
    picture this campaign hid does not reach its book by the back door. A
    world-shaped URL in an inherited lore body is still being exported *for
    this campaign*."""
    from grimoire.store import world_images
    wid, cid = _campaign(monkeypatch, tmp_path)
    world_images.put_image(wid, "coastline", _img(), "png")
    campaign_images.delete_image(cid, "coastline")      # hidden here

    for url in (f"/api/campaigns/{cid}/images/coastline",
                f"/api/worlds/{wid}/images/coastline"):
        images = export.Images()
        assert export.rewrite_images(f"![c]({url})", cid, images) == "c"
        assert list(images.by_path) == []


def test_rewrite_images_honors_asset_tombstone(monkeypatch, tmp_path):
    from grimoire.store import overlay
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    wroot = worlds.world_root(wid)
    aid, _ = characters.create_character(wroot, "Seraphine")
    assets.put_image(wroot, aid, "default", "avatar", _img(), "png")
    cid = campaigns.create_campaign("Run One", wid)
    # the campaign deletes the inherited avatar -> only a tombstone, no copy
    overlay.delete_image(cid, aid, "default", "avatar")
    images = export.Images()
    out = export.rewrite_images(
        f"![face](/api/campaigns/{cid}/characters/{aid}/versions/default/images/avatar)", cid, images)
    assert out == "face"   # tombstoned: the world image must not leak into the export
    assert images.by_path == {}


@pytest.mark.parametrize("name,encoded", [
    ("art(1)", "art%281%29"), ("my art", "my%20art"), ("a#b", "a%23b"),
])
def test_a_percent_encoded_record_image_url_still_reaches_the_book(
        monkeypatch, tmp_path, name, encoded):
    """`assets.storable` accepts names a markdown link cannot carry bare, so
    `context.art.url_for` percent-encodes what it writes into a transcript. The
    bytes are on disk under the DECODED name, and a resolver that only tried
    the raw segment lost exactly the images the encoding existed to rescue."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    docks = entities.create_entity(croot, "locations", "The Docks", body="piers")
    assets.put_image(croot, docks, "default", name, _img(), "png", base="locations")
    out = export.rewrite_images(
        f"![A pier](/api/campaigns/{cid}/locations/{docks}/images/{encoded})",
        cid, export.Images())
    assert out == "![A pier](images/img-000.png)"


@pytest.mark.parametrize("kind", list(entities.ENTITY_KINDS))
def test_every_entity_kind_can_carry_an_image_into_the_book(monkeypatch, tmp_path, kind):
    """The URL pattern used to name `locations|lore` by hand, so the three kinds
    added since exported as alt text and their pictures never reached the book.
    Parametrized over the roster so a sixth kind fails here rather than
    silently."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    eid = entities.create_entity(croot, kind, "The Thing", body="x")
    assets.put_image(croot, eid, "default", "gallery_1", _img(), "png", base=kind)
    out = export.rewrite_images(
        f"![A thing](/api/campaigns/{cid}/{kind}/{eid}/images/gallery_1)",
        cid, export.Images())
    assert out == "![A thing](images/img-000.png)"


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
    assets.put_image(croot, docks, "default", "pier", _img(), "png", base="locations")

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
    assert z.read("images/img-000.png") == _img()
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


def test_packed_name_comes_from_the_bytes_not_the_stored_suffix(monkeypatch, tmp_path):
    """A JPEG stored as `pier.png` -- which an upload could produce before #321
    and which stores already on disk still hold -- must pack under `.jpg`, so
    the media type every renderer derives from that suffix is the true one."""
    wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    docks = entities.create_entity(croot, "locations", "The Docks", body="piers")
    assets.put_image(croot, docks, "default", "pier", _jpeg(), "png", base="locations")

    images = export.Images()
    out = export.rewrite_images(f"![d](/api/campaigns/{cid}/locations/{docks}/images/pier)",
                                cid, images)
    assert "![d](images/img-000.jpg)" in out
    assert list(images.by_path.values()) == ["img-000.jpg"]


def test_an_image_whose_bytes_name_no_format_is_dropped_from_the_export(monkeypatch, tmp_path):
    """Bytes in no format we can declare leave nothing to correct, so the book
    does not carry them (#321): packing them under the store's own guess is
    exactly the wrong media type that makes a book invalid. The image degrades
    to its alt text, like a remote or missing one, and the file stays on disk.

    `fetch.download_url` is what puts such a file there -- its last-resort
    `ext = "png"` for bytes it cannot sniff -- so this is not a hypothetical
    store, it is what downloading an AVIF avatar produces today.
    """
    wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    docks = entities.create_entity(croot, "locations", "The Docks", body="piers")
    assets.put_image(croot, docks, "default", "pier", b"BM not really", "png", base="locations")

    images = export.Images()
    out = export.rewrite_images(f"![the docks](/api/campaigns/{cid}/locations/{docks}/images/pier)",
                                cid, images)
    assert out == "the docks"          # alt text, no image reference
    assert images.by_path == {}        # nothing to pack, nothing to declare
    # still in the store: only the book declined it
    assert assets.image_path(croot, docks, "default", "pier", base="locations") is not None


def test_an_image_we_cannot_open_is_dropped_rather_than_failing_the_export(monkeypatch, tmp_path):
    """It used to register at collect and raise at zip time, taking the whole
    export with it. A book missing one image beats no book.

    A directory standing where the file was is the reachable version of this:
    `image_path` resolves by glob and does not check what it found, so the
    export is handed a path it cannot open. A permission-denied file is the
    same branch and cannot be set up as root."""
    wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    docks = entities.create_entity(croot, "locations", "The Docks", body="piers")
    assets.put_image(croot, docks, "default", "pier", _img(), "png", base="locations")
    p = assets.image_path(croot, docks, "default", "pier", base="locations")
    p.unlink()
    p.mkdir()  # a directory where the file was: open() raises, and it is not an image

    images = export.Images()
    out = export.rewrite_images(f"![d](/api/campaigns/{cid}/locations/{docks}/images/pier)",
                                cid, images)
    assert out == "d" and images.by_path == {}


def test_a_portrait_that_cannot_be_named_leaves_the_actor_without_one(monkeypatch, tmp_path):
    """The appendix already renders an actor with no avatar; an avatar the book
    cannot declare has to reach that same path rather than a broken <img>."""
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    assets.put_image(croot, "seraphine", "default", assets.AVATAR, b"BM not really", "png")

    entry = next(e for e in export.collect(cid)["appendix"] if e["name"] == "Seraphine")
    assert entry["portrait"] is None


def test_a_pcs_portrait_is_packed_like_a_characters(monkeypatch, tmp_path):
    """The appendix used to hard-code `"characters"` as the asset base and hand
    every PC a `None` portrait -- so a player character was the one member of
    the cast a book could never show (#219)."""
    wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    assets.put_image(worlds.world_root(wid), "elara", "default", assets.AVATAR,
                     _img(), "png", pcs.ASSET_BASE)

    data = export.collect(cid)
    portrait = next(e for e in data["appendix"] if e["name"] == "Elara")["portrait"]
    assert portrait is not None and portrait.endswith(".png")
    assert portrait.removeprefix("images/") in set(data["images"].by_path.values())
    # ...and a character's still resolves against its own base, not the PC's
    assert next(e for e in data["appendix"] if e["name"] == "Seraphine")["portrait"] is None


def test_rewrite_images_maps_a_pc_image_url(monkeypatch, tmp_path):
    """A PC's description can carry a localized image the same way a card's
    can, so the export's URL matcher has to name `pcs` as well as
    `characters`."""
    wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    pcs.create_pc(croot, "Elara", [])
    assets.put_image(croot, "elara", "default", "embed-abc123", _img(), "png", pcs.ASSET_BASE)
    images = export.Images()
    out = export.rewrite_images(
        f"![her](/api/campaigns/{cid}/pcs/elara/versions/default/images/embed-abc123)",
        cid, images)
    assert "![her](images/img-000.png)" in out


def test_build_html_data_uri_mime_follows_the_bytes(monkeypatch, tmp_path):
    """The data URI carried the suffix's mime, which browsers survive by
    sniffing -- not a reason to write a wrong one (#321)."""
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    docks = "the-docks"  # the fixture's one image-bearing location
    assert assets.image_path(croot, docks, "default", "pier", base="locations") is not None
    assets.put_image(croot, docks, "default", "pier", _jpeg(), "png", base="locations")

    html = export.build_html(cid)[0].decode()
    assert "data:image/jpeg;base64," in html
    assert "data:image/png" not in html


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


def test_transitions_export_as_unlabelled_narration(monkeypatch, tmp_path):
    """The transition tag is internal drift metadata, never a speaker. HTML and
    plain text must render a transition exactly as they did before it was
    tagged — and identically to an untagged (pre-existing) transition line."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Arrival")
    scenes.append_message(cid, sid, "assistant", "The docks reek.")
    scenes.append_message(cid, sid, "assistant", "*Time passes. It is now dusk.*",
                          speaker=scenes.TRANSITION_SPEAKER)
    chapter = export.collect(cid)["chapters"][0]
    assert [m["speaker"] for m in chapter["messages"]] == [None, None]
    html = export.build_html(cid)[0].decode()
    assert scenes.TRANSITION_SPEAKER not in html
    assert "Scene</span>" not in html
    assert "Time passes. It is now dusk." in html
    txt = export.build_text(cid)[0].decode()
    assert scenes.TRANSITION_SPEAKER not in txt
    assert "Time passes. It is now dusk." in txt


def test_json_export_keeps_the_transition_tag(monkeypatch, tmp_path):
    """build_json is the nearest-to-disk dump; the tag is real stored metadata
    and must survive there even though no rendered surface shows it."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Arrival")
    scenes.append_message(cid, sid, "assistant", "*Time passes.*",
                          speaker=scenes.TRANSITION_SPEAKER)
    payload = json.loads(export.build_json(cid)[0].decode())
    assert payload["scenes"][0]["messages"][0]["speaker"] == scenes.TRANSITION_SPEAKER


def test_cover_is_not_packed_into_the_other_exports(monkeypatch, tmp_path):
    """The cover must stay out of the shared image registry: every other
    renderer packs everything in it."""
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    before = sorted(export.collect(cid)["images"].by_path.values())
    covers.put_cover(cid, b"\x89PNG-cover", "png")
    data = export.collect(cid)
    assert data["cover"] is not None
    assert sorted(data["images"].by_path.values()) == before  # no renumbering

    cover_bytes = covers.cover_path(cid).read_bytes()
    blob, _ = export.build_markdown_bundle(cid)
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert not [n for n in z.namelist()
                if n.startswith("images/") and z.read(n) == cover_bytes]

    html, _ = export.build_html(cid)
    # asserted on the BYTES, not the word "cover": the campaign's own prose can
    # contain that word, which would make a substring check fail for nothing
    assert base64.b64encode(cover_bytes) not in html


# ---- table of contents / chapter markers, per format ----

def test_markdown_bundle_index_is_a_numbered_table_of_contents(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    z = zipfile.ZipFile(io.BytesIO(export.build_markdown_bundle(cid)[0]))
    index = z.read("index.md").decode()
    assert "## Scenes" in index
    assert "1. [Arrival](001-arrival.md)" in index
    assert "2. [Below](002-below.md)" in index
    assert index.index("1. [Arrival]") < index.index("2. [Below]")
    assert "## Appendix" in index
    assert "- [Elara](actor-pcs-elara.md)" in index
    # every link resolves inside the bundle
    for link in re.findall(r"\]\(([^)]+)\)", index):
        assert link in z.namelist(), link
    # the chapter marker: a numbered heading matching the index entry
    assert z.read("001-arrival.md").decode().startswith("# 1. Arrival\n")
    assert z.read("002-below.md").decode().startswith("# 2. Below\n")


def test_html_export_has_a_contents_nav_linking_every_section(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    html = export.build_html(cid)[0].decode()
    assert '<nav class="toc" id="contents">' in html
    assert '<a href="#chapter-001">Arrival</a>' in html
    assert '<a href="#chapter-002">Below</a>' in html
    assert '<a href="#actor-pcs-elara">Elara</a>' in html
    assert '<a href="#location-the-docks">The Docks</a>' in html
    # the chapter markers those anchors land on
    assert '<section class="chapter" id="chapter-001">' in html
    assert '<section class="chapter" id="chapter-002">' in html
    assert '<section class="appendix" id="actor-pcs-elara">' in html
    assert '<section class="divider" id="appendix">' in html
    # no link points at an id the page does not define
    ids = set(re.findall(r'<section class="[^"]+" id="([^"]+)">', html))
    ids.add("contents")
    assert set(re.findall(r'href="#([^"]+)"', html)) <= ids


def test_html_export_of_an_empty_campaign_has_no_empty_contents_nav(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    html = export.build_html(cid)[0].decode()
    assert "<nav" not in html and "<h2>Contents</h2>" not in html
    # the stylesheet still carries the ToC rules, like every other unconditional
    # rule in it — varying the CSS per campaign would buy nothing
    assert "nav.toc {" in html


def test_text_export_has_a_contents_block_and_form_feed_chapter_breaks(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    txt = export.build_text(cid)[0].decode()
    assert txt.startswith("Run One\nSaltmarch")
    contents, _, body = txt.partition("\f")
    assert "CONTENTS" in contents
    assert "  1. Arrival" in contents and "  2. Below" in contents
    # one form feed per scene — the plain-text page break, ahead of a numbered
    # title line matching the contents entry
    assert txt.count("\f") == 2
    assert body.startswith("\n1. Arrival\n")
    assert "\f\n2. Below\n" in txt
    assert "---" not in txt  # the rule the form feed replaced


def test_text_export_of_an_empty_campaign_has_no_contents_block(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    txt = export.build_text(cid)[0].decode()
    assert "CONTENTS" not in txt and "\f" not in txt


def test_json_export_states_the_scene_order_as_contents(monkeypatch, tmp_path):
    _wid, cid, sid1, sid2 = _fixture_campaign(monkeypatch, tmp_path)
    payload = json.loads(export.build_json(cid)[0].decode())
    assert payload["contents"] == [{"number": 1, "id": sid1, "title": "Arrival"},
                                   {"number": 2, "id": sid2, "title": "Below"}]
    # every entry names a scene the dump actually carries
    assert [c["id"] for c in payload["contents"]] == [s["meta"]["id"] for s in payload["scenes"]]


def test_anchors_survive_scenes_that_share_a_title(monkeypatch, tmp_path):
    """`chapter_anchor` numbers rather than slugifies, so two scenes with the
    same title get two anchors instead of one anchor with two definitions."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    for _ in range(2):
        sid = scenes.create_scene(cid, "Arrival")
        scenes.append_message(cid, sid, "assistant", "The docks reek.")
    chapters = export.collect(cid)["chapters"]
    assert [export.chapter_anchor(c) for c in chapters] == ["chapter-001", "chapter-002"]
    assert [export.toc_label(c) for c in chapters] == ["1. Arrival", "2. Arrival"]
    html = export.build_html(cid)[0].decode()
    assert html.count('id="chapter-001"') == 1 and html.count('id="chapter-002"') == 1


def test_html_contents_numbers_from_the_scene_number_not_the_browser(monkeypatch, tmp_path):
    """The `<li value>` is what keeps the one-page export's numbering the same
    numbering the EPUB, markdown and text exports print."""
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    html = export.build_html(cid)[0].decode()
    assert '<li value="1"><a href="#chapter-001">Arrival</a></li>' in html
    assert '<li value="2"><a href="#chapter-002">Below</a></li>' in html


def test_html_export_escapes_titles_that_are_markup(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    cid = campaigns.create_campaign("Run One & <Saltmarch>", wid)
    sid = scenes.create_scene(cid, 'Arrival & "Below" <hr/>')
    scenes.append_message(cid, sid, "assistant", "The docks reek.")

    html = export.build_html(cid)[0].decode()
    assert "<hr/>" not in html                      # the title never becomes markup
    assert "&lt;hr/&gt;" in html and "Run One &amp; &lt;Saltmarch&gt;" in html


def test_a_canonical_url_wins_over_a_file_literally_named_like_its_escape(
        monkeypatch, tmp_path):
    """Both URL writers escape a literal `%` as `%25`, so `/images/my%20art` is
    unambiguously the file `my art`. A raw-first lookup would hand back a second
    file literally named `my%20art` and export the wrong picture."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    docks = entities.create_entity(croot, "locations", "The Docks", body="piers")
    assets.put_image(croot, docks, "default", "my art", _img(color=(1, 2, 3)), "png",
                     base="locations")
    assets.put_image(croot, docks, "default", "my%20art", _img(color=(9, 9, 9)), "png",
                     base="locations")
    images = export.Images()
    export.rewrite_images(
        f"![A pier](/api/campaigns/{cid}/locations/{docks}/images/my%20art)", cid, images)
    assert [p.stem for p in images.by_path] == ["my art"]


def test_a_raw_name_with_a_percent_still_resolves_when_nothing_else_does(
        monkeypatch, tmp_path):
    """The raw attempt stays as a fallback for a URL written by hand before any
    of the encoding existed."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    docks = entities.create_entity(croot, "locations", "The Docks", body="piers")
    assets.put_image(croot, docks, "default", "a%2Fb", _img(), "png", base="locations")
    out = export.rewrite_images(
        f"![A pier](/api/campaigns/{cid}/locations/{docks}/images/a%2Fb)",
        cid, export.Images())
    assert out == "![A pier](images/img-000.png)"
