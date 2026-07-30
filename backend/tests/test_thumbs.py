"""Thumbnail generation + the ?w= image route parameter."""

import io

from PIL import Image

from grimoire.store import assets, thumbs


def _png_bytes(w=1200, h=800, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def test_thumbnail_downscales_to_max_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assets.put_image(tmp_path, "g1", "default", "embed-0", _png_bytes(), "png", base="greetings")
    src = assets.image_path(tmp_path, "g1", "default", "embed-0", base="greetings")
    tp = thumbs.thumbnail(src, 320)
    assert tp is not None and tp.exists()
    with Image.open(tp) as im:
        assert max(im.size) == 320
        assert im.format == "WEBP"
    assert tp.stat().st_size < src.stat().st_size


def test_thumbnail_is_cached_until_source_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assets.put_image(tmp_path, "g1", "default", "embed-0", _png_bytes(), "png", base="greetings")
    src = assets.image_path(tmp_path, "g1", "default", "embed-0", base="greetings")
    first = thumbs.thumbnail(src, 320)
    again = thumbs.thumbnail(src, 320)
    assert again == first  # same cache file, not regenerated elsewhere
    assets.put_image(tmp_path, "g1", "default", "embed-0", _png_bytes(600, 600, (0, 90, 200)), "png",
                     base="greetings")
    src2 = assets.image_path(tmp_path, "g1", "default", "embed-0", base="greetings")
    changed = thumbs.thumbnail(src2, 320)
    assert changed != first  # content change -> new cache entry


def test_thumbnail_smaller_source_is_not_upscaled(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assets.put_image(tmp_path, "g1", "default", "embed-0", _png_bytes(100, 80), "png", base="greetings")
    src = assets.image_path(tmp_path, "g1", "default", "embed-0", base="greetings")
    tp = thumbs.thumbnail(src, 320)
    with Image.open(tp) as im:
        assert im.size == (100, 80)


def test_thumbnail_garbled_source_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assets.put_image(tmp_path, "g1", "default", "embed-0", b"not an image", "png", base="greetings")
    src = assets.image_path(tmp_path, "g1", "default", "embed-0", base="greetings")
    assert thumbs.thumbnail(src, 320) is None
