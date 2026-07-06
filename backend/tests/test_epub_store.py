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
