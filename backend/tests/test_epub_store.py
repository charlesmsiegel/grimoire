"""Campaign → EPUB export."""

from pathlib import Path

import grimoire

FONTS = Path(grimoire.__file__).parent / "assets" / "fonts"


def test_fonts_vendored():
    ttfs = sorted(p.name for p in FONTS.glob("*.ttf"))
    assert ttfs == ["Cinzel-SemiBold.ttf", "EBGaramond-Italic.ttf",
                    "EBGaramond-Regular.ttf", "EBGaramond-SemiBold.ttf"]
    for p in FONTS.glob("*.ttf"):
        assert p.stat().st_size > 50_000, p.name
        assert p.read_bytes()[:4] == b"\x00\x01\x00\x00", p.name  # TrueType sfnt magic
    assert (FONTS / "OFL-EBGaramond.txt").exists()
    assert (FONTS / "OFL-Cinzel.txt").exists()


def test_markdown_dependency():
    import markdown
    assert markdown.markdown("**hi**") == "<p><strong>hi</strong></p>"


from grimoire.store import assets, campaigns, epub, worlds  # noqa: E402 - deliberate late import; see the lines above


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    cid = campaigns.create_campaign("Run One", wid)
    return wid, cid


def test_message_html_speaker_label():
    html = epub._message_html("Seraphine", "\"Welcome,\" she says.")
    assert html.startswith('<p><span class="speaker">Seraphine</span> ')
    assert epub._message_html(None, "The docks reek.") == "<p>The docks reek.</p>"
    # speaker names are escaped
    assert "&lt;b&gt;" in epub._message_html("<b>", "hi")


import io  # noqa: E402 - deliberate late import; see the lines above
import pathlib  # noqa: E402 - deliberate late import; see the lines above
import xml.etree.ElementTree as ET  # noqa: E402 - deliberate late import; see the lines above
import zipfile  # noqa: E402 - deliberate late import; see the lines above

from PIL import Image  # noqa: E402 - deliberate late import; see the lines above

from grimoire.store import appearances, characters, covers, entities, pcs, scenes  # noqa: E402 - deliberate late import; see the lines above

OPF_NS = {"opf": "http://www.idpf.org/2007/opf"}


def _png(size=(4, 4)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


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

    sid2 = scenes.create_scene(cid, "Below")
    scenes.append_message(cid, sid2, "assistant", "Deeper still.")
    return wid, cid, sid1, sid2


def _open(blob: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(blob))


def test_build_epub_ocf_shape(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, filename = epub.build_epub(cid)
    assert filename == f"{cid}.epub"
    z = _open(blob)
    infos = z.infolist()
    assert infos[0].filename == "mimetype"
    assert infos[0].compress_type == zipfile.ZIP_STORED
    assert z.read("mimetype") == b"application/epub+zip"
    assert "full-path=\"package.opf\"" in z.read("META-INF/container.xml").decode()


def test_build_epub_manifest_and_spine_complete(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    opf = ET.fromstring(z.read("package.opf"))
    items = opf.findall(".//opf:item", OPF_NS)
    by_id = {i.get("id"): i for i in items}
    for i in items:
        assert i.get("href") in z.namelist(), i.get("href")
    for ref in opf.findall(".//opf:itemref", OPF_NS):
        assert ref.get("idref") in by_id
    navs = [i for i in items if i.get("properties") == "nav"]
    assert len(navs) == 1 and navs[0].get("href") == "nav.xhtml"
    title = opf.find(".//{http://purl.org/dc/elements/1.1/}title")
    assert title.text == "Run One"


def test_build_epub_chapters_in_scene_order_with_meta(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    ch1 = z.read("text/chapter-001.xhtml").decode()
    ch2 = z.read("text/chapter-002.xhtml").decode()
    assert "Arrival" in ch1 and "Below" in ch2
    assert '<span class="speaker">Seraphine</span>' in ch1
    assert '<span class="speaker">Elara</span>' in ch1
    assert "The docks reek." in ch1
    assert 'class="epigraph"' in ch1 and "They arrive." in ch1
    assert 'class="scene-date"' in ch1
    assert "The Docks" in ch1          # location line
    assert "Elara" in ch1 and "Seraphine" in ch1  # cast line
    assert 'class="scene-date"' not in ch2  # undated scene has no date line
    # title page
    tp = z.read("text/titlepage.xhtml").decode()
    assert "Run One" in tp and "Saltmarch" in tp and 'class="daterange"' in tp


def test_build_epub_packs_images_and_fonts(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    imgs = [n for n in z.namelist() if n.startswith("images/")]
    assert imgs == ["images/img-000.png"]
    assert z.read("images/img-000.png") == b"pierbytes"
    ch1 = z.read("text/chapter-001.xhtml").decode()
    assert 'src="../images/img-000.png"' in ch1
    assert "example.com" not in ch1 and "lost" in ch1  # remote image degraded to alt
    fonts = sorted(n for n in z.namelist() if n.startswith("fonts/"))
    assert fonts == ["fonts/Cinzel-SemiBold.ttf", "fonts/EBGaramond-Italic.ttf",
                     "fonts/EBGaramond-Regular.ttf", "fonts/EBGaramond-SemiBold.ttf"]
    assert "css/stylesheet.css" in z.namelist()
    assert "@font-face" in z.read("css/stylesheet.css").decode()


def _jpeg(size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 40, 40)).save(buf, "JPEG")
    return buf.getvalue()


def test_manifest_media_type_follows_the_bytes_not_the_stored_name(monkeypatch, tmp_path):
    """#321: a JPEG stored as `pier.png` was packed as `img-000.png` and declared
    `media-type="image/png"` -- an epubcheck error, and unrenderable in some
    readers. Nothing renames the file on disk, so this has to hold for the
    mislabelled images stores already contain, not just for new uploads."""
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    docks = "the-docks"  # the fixture's one image-bearing location
    assert assets.image_path(croot, docks, "default", "pier", base="locations") is not None
    jpeg = _jpeg()
    assets.put_image(croot, docks, "default", "pier", jpeg, "png", base="locations")

    z = _open(epub.build_epub(cid)[0])
    assert z.read("images/img-000.jpg") == jpeg          # packed name agrees with the bytes
    opf = ET.fromstring(z.read("package.opf"))
    item = next(i for i in opf.findall(".//opf:item", OPF_NS)
                if i.get("href").startswith("images/"))
    assert item.get("media-type") == "image/jpeg"
    assert 'src="../images/img-000.jpg"' in z.read("text/chapter-001.xhtml").decode()


def test_manifest_falls_back_to_the_stored_name_for_unrecognizable_bytes(monkeypatch, tmp_path):
    """Bytes in no format we can name have no truth to substitute; the book
    still packs, declaring the store's own guess rather than octet-stream."""
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    docks = "the-docks"  # the fixture's one image-bearing location
    assert assets.image_path(croot, docks, "default", "pier", base="locations") is not None
    assets.put_image(croot, docks, "default", "pier", b"BM not really", "png", base="locations")

    z = _open(epub.build_epub(cid)[0])
    opf = ET.fromstring(z.read("package.opf"))
    item = next(i for i in opf.findall(".//opf:item", OPF_NS)
                if i.get("href").startswith("images/"))
    assert item.get("href") == "images/img-000.png"
    assert item.get("media-type") == "image/png"


def test_cover_is_packed_under_the_extension_its_bytes_are(monkeypatch, tmp_path):
    """An uploaded cover is validated, so this only answers for one a sync
    client or a hand-edit dropped into the campaign's assets directory."""
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    jpeg = _jpeg()
    covers.put_cover(cid, jpeg, "png")  # bypasses `covers.validate`, as a stray file does

    z = _open(epub.build_epub(cid)[0])
    assert z.read("images/cover.jpg") == jpeg
    opf = ET.fromstring(z.read("package.opf"))
    items = {i.get("id"): i for i in opf.findall(".//opf:item", OPF_NS)}
    assert items["cover-img"].get("href") == "images/cover.jpg"
    assert items["cover-img"].get("media-type") == "image/jpeg"
    assert "../images/cover.jpg" in z.read("text/cover.xhtml").decode()


def test_build_epub_unknown_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    import pytest
    with pytest.raises(campaigns.CampaignNotFound):
        epub.build_epub("nope")


def test_chapter_omits_deleted_location(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    (croot / "locations" / "the-docks.md").unlink()
    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    ch1 = z.read("text/chapter-001.xhtml").decode()
    assert 'class="scene-location"' not in ch1   # deleted location: line silently omitted
    assert 'class="scene-date"' in ch1           # rest of the header intact


def test_chapter_and_appendix_include_inherited_world_location(monkeypatch, tmp_path):
    """A thin campaign never copies world locations up front; a scene set at
    one of them must still show its name in the chapter header and get a
    full appendix entry, not be silently dropped as if deleted."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    wroot = worlds.world_root(wid)
    harbor = entities.create_entity(wroot, "locations", "The Harbor", body="Gulls and salt air.")
    cid = campaigns.create_campaign("Run One", wid)
    sid = scenes.create_scene(cid, "Arrival")
    scenes.append_message(cid, sid, "assistant", "The gulls cry.")
    scenes.set_location(cid, sid, harbor)

    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    ch1 = z.read("text/chapter-001.xhtml").decode()
    assert 'class="scene-location"' in ch1 and "The Harbor" in ch1
    assert "text/location-the-harbor.xhtml" in z.namelist()
    loc_doc = z.read("text/location-the-harbor.xhtml").decode()
    assert "Gulls and salt air." in loc_doc


def test_appendix_actors_and_visited_locations(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    assets.put_image(croot, "seraphine", "default", assets.AVATAR, b"face", "png")
    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    names = z.namelist()
    assert "text/appendix.xhtml" in names  # divider page
    sera = z.read("text/actor-characters-seraphine.xhtml").decode()
    assert "the drowned keeper" in sera and "grim" in sera
    assert "Description" in sera and "Personality" in sera
    assert 'class="portrait"' in sera
    elara = z.read("text/actor-pcs-elara.xhtml").decode()
    assert "A wanderer." in elara and "Player character" in elara
    docks = z.read("text/location-the-docks.xhtml").decode()
    assert "Salt-stained piers." in docks
    assert "text/location-the-keep.xhtml" not in names  # never visited
    # players come before NPCs in the spine
    spine_docs = [n for n in names if n.startswith("text/actor-")]
    assert spine_docs.index("text/actor-pcs-elara.xhtml") >= 0
    import xml.etree.ElementTree as ET
    opf = ET.fromstring(z.read("package.opf"))
    hrefs = [i.get("href") for i in opf.findall(".//opf:item", OPF_NS)]
    a = hrefs.index("text/actor-pcs-elara.xhtml")
    b = hrefs.index("text/actor-characters-seraphine.xhtml")
    c = hrefs.index("text/location-the-docks.xhtml")
    assert a < b < c
    # nav lists the appendix
    nav = z.read("nav.xhtml").decode()
    assert "Appendix" in nav and "text/actor-pcs-elara.xhtml" in nav


def test_appendix_skips_unreadable_actor(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    (croot / "characters" / "seraphine" / "default.json").unlink()
    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    assert "text/actor-characters-seraphine.xhtml" not in z.namelist()
    assert "text/actor-pcs-elara.xhtml" in z.namelist()  # book still builds


def test_no_appendix_no_divider(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Bare")
    cid = campaigns.create_campaign("Empty Run", wid)
    blob, _ = epub.build_epub(cid)  # zero scenes: title page + nothing else
    z = _open(blob)
    assert "text/appendix.xhtml" not in z.namelist()
    assert "text/titlepage.xhtml" in z.namelist()
    nav = z.read("nav.xhtml").decode()
    assert "Scenes" not in nav          # no empty Scenes <ol> in a zero-scene book
    ET.fromstring(nav)                  # nav stays well-formed XML


def test_epub_renders_transitions_without_a_speaker_plate(monkeypatch, tmp_path):
    """A scene transition is unlabelled narration in the book — the internal
    drift tag must never reach a chapter's XHTML."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Arrival")
    scenes.append_message(cid, sid, "assistant", "The docks reek.")
    scenes.append_message(cid, sid, "assistant", "*Time passes. It is now dusk.*",
                          speaker=scenes.TRANSITION_SPEAKER)
    z = _open(epub.build_epub(cid)[0])
    ch = z.read("text/chapter-001.xhtml").decode()
    assert scenes.TRANSITION_SPEAKER not in ch
    assert "Scene</span>" not in ch
    assert "Time passes. It is now dusk." in ch


def test_build_epub_without_a_cover_is_structurally_unchanged(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    z = _open(epub.build_epub(cid)[0])
    assert "text/cover.xhtml" not in z.namelist()
    assert "images/cover.png" not in z.namelist()
    opf = ET.fromstring(z.read("package.opf"))
    assert not [i for i in opf.findall(".//opf:item", OPF_NS)
                if i.get("properties") == "cover-image"]
    assert opf.find(".//opf:meta[@name='cover']", OPF_NS) is None
    first = opf.findall(".//opf:itemref", OPF_NS)[0].get("idref")
    assert {i.get("id"): i.get("href") for i in opf.findall(".//opf:item", OPF_NS)}[first] \
        == "text/titlepage.xhtml"
    # The stylesheet is the one file that DOES change relative to a pre-cover
    # book: it carries the cover-page rules unconditionally, even for a
    # no-cover book. Rendering it conditionally would make the stylesheet vary
    # per campaign for no benefit, so a no-cover book still ships it.
    css = z.read("css/stylesheet.css").decode()
    assert "body.cover" in css and ".cover img" in css


def test_build_epub_with_a_cover(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    data = _png()
    covers.put_cover(cid, data, "png")

    z = _open(epub.build_epub(cid)[0])
    assert z.read("images/cover.png") == data
    page = z.read("text/cover.xhtml").decode()
    assert "../images/cover.png" in page and "Run One" in page

    opf = ET.fromstring(z.read("package.opf"))
    items = {i.get("id"): i for i in opf.findall(".//opf:item", OPF_NS)}
    assert items["cover-img"].get("properties") == "cover-image"
    assert items["cover-img"].get("href") == "images/cover.png"
    assert opf.find(".//opf:meta[@name='cover']", OPF_NS).get("content") == "cover-img"

    spine = [ref.get("idref") for ref in opf.findall(".//opf:itemref", OPF_NS)]
    assert items[spine[0]].get("href") == "text/cover.xhtml"
    assert items[spine[1]].get("href") == "text/titlepage.xhtml"
    # not a ToC entry, by convention
    assert "cover.xhtml" not in z.read("nav.xhtml").decode()


def test_build_epub_drops_a_cover_that_vanishes_mid_export(monkeypatch, tmp_path):
    """The panel that replaces a cover sits next to the Export menu, so this
    window is reachable. An export must degrade, not 500."""
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    covers.put_cover(cid, _png(), "png")
    real = pathlib.Path.read_bytes

    def vanishing(self, *a, **k):
        if self.name == "cover.png":
            raise OSError("gone")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_bytes", vanishing)
    z = _open(epub.build_epub(cid)[0])
    assert "text/cover.xhtml" not in z.namelist()
    assert "images/cover.png" not in z.namelist()
    opf = ET.fromstring(z.read("package.opf"))
    assert not [i for i in opf.findall(".//opf:item", OPF_NS)
                if i.get("properties") == "cover-image"]
