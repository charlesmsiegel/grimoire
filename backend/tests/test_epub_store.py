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


from grimoire.store import assets, campaigns, epub, worlds


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


def test_rewrite_images_maps_local_and_drops_remote(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    wroot = worlds.world_root(wid)
    from grimoire.store import entities
    docks = entities.create_entity(croot, "locations", "The Docks", body="piers")
    assets.put_image(croot, docks, "default", "pier", b"pierbytes", "png", base="locations")
    images = epub._Images()
    text = (f"Look: ![The docks](/api/campaigns/{cid}/locations/{docks}/images/pier) "
            "and ![lost](https://example.com/x.png)")
    out = epub._rewrite_images(text, croot, wroot, images)
    assert "![The docks](../images/img-000.png)" in out
    assert "lost" in out and "example.com" not in out
    # same file referenced again reuses the entry
    epub._rewrite_images(f"![again](/api/campaigns/{cid}/locations/{docks}/images/pier)",
                         croot, wroot, images)
    assert len(images.by_path) == 1


def test_rewrite_images_world_fallback(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    wroot = worlds.world_root(wid)
    # greeting images only ever live world-side
    assets.put_image(wroot, "g1", "default", "vista", b"vistabytes", "jpg", base="greetings")
    images = epub._Images()
    out = epub._rewrite_images(f"![v](/api/worlds/{wid}/greetings/g1/images/vista)",
                               croot, wroot, images)
    assert "![v](../images/img-000.jpg)" in out
    # missing file degrades to alt text
    out2 = epub._rewrite_images(f"![gone](/api/worlds/{wid}/greetings/g1/images/nope)",
                                croot, wroot, images)
    assert out2 == "gone"
