"""Thumbnail generation + the ?w= image route parameter."""

import io
import re
from pathlib import Path

from PIL import Image

from grimoire.routes.common import THUMB_BUCKETS, THUMB_W
from grimoire.store import assets, thumbs

REPO = Path(__file__).resolve().parents[2]


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


def test_an_encoder_change_is_a_new_cache_entry(tmp_path, monkeypatch):
    # The key names the source's identity and stat, which an encoder change
    # leaves alone -- so without the encoder in the key, a cache written under
    # the old settings would go on being served as though it were the new.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assets.put_image(tmp_path, "g1", "default", "embed-0", _png_bytes(), "png", base="greetings")
    src = assets.image_path(tmp_path, "g1", "default", "embed-0", base="greetings")
    before = thumbs.thumbnail(src, 320)
    monkeypatch.setattr(thumbs, "ENCODER", thumbs.ENCODER + "-next")
    after = thumbs.thumbnail(src, 320)
    assert before is not None and after is not None and after != before


def test_the_encoder_settings_are_what_the_key_says(tmp_path, monkeypatch):
    # The salt is only honest if it is the settings the save uses: a quality
    # bumped in one place and not the other serves new bytes under an old key.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    seen: dict = {}
    real = Image.Image.save

    def spy(self, fp, *args, **kw):
        seen.update(kw)
        return real(self, fp, *args, **kw)

    monkeypatch.setattr(Image.Image, "save", spy)
    assets.put_image(tmp_path, "g1", "default", "embed-0", _png_bytes(), "png", base="greetings")
    src = assets.image_path(tmp_path, "g1", "default", "embed-0", base="greetings")
    assert thumbs.thumbnail(src, 320) is not None
    assert seen["format"] == "WEBP"
    assert f"webp-q{seen['quality']}-m{seen['method']}" == thumbs.ENCODER


# ---- the route's width buckets ----
def _avatar_route(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()["character"]
    base = f"/api/worlds/{wid}/characters/{cid}/versions/default/images/avatar"
    client.put(base, files={"file": ("a.png", io.BytesIO(_png_bytes()), "image/png")})
    return base


def _cached(tmp_path):
    d = tmp_path / ".cache" / "thumbs"
    return sorted(d.iterdir()) if d.exists() else []


def test_nearby_widths_share_one_cache_entry(client, tmp_path):
    # Every layout asking for its own exact width is a cache entry per layout
    # per picture, and a cold resize for each. 154 and 160 are two tiles'
    # widths that want the same picture.
    base = _avatar_route(client)
    a = client.get(f"{base}?w=154")
    b = client.get(f"{base}?w=160")
    assert a.headers["content-type"] == b.headers["content-type"] == "image/webp"
    assert a.content == b.content
    assert len(_cached(tmp_path)) == 1


def test_a_width_snaps_up_to_its_bucket(client):
    base = _avatar_route(client)
    for asked, served in ((1, 128), (32, 128), (128, 128), (129, 256), (300, 320),
                          (320, 320), (321, 512), (1024, 1024), (5000, 1024)):
        r = client.get(f"{base}?w={asked}")
        assert r.headers["content-type"] == "image/webp"
        with Image.open(io.BytesIO(r.content)) as im:
            assert max(im.size) == served, (asked, im.size)


def test_the_client_buckets_are_server_buckets():
    # `frontend/src/api/thumbs.ts` asks for exactly its THUMB widths, so each
    # one must be a bucket already -- one that snapped to a neighbour would be
    # served a different size than the srcset descriptor it was chosen by
    # claims. Read from the client's own source, so a width added on either
    # side alone fails here rather than going soft in a browser.
    src = (REPO / "frontend" / "src" / "api" / "thumbs.ts").read_text(encoding="utf-8")
    m = re.search(r"export const THUMB = \{(.*?)\} as const;", src, re.DOTALL)
    assert m, "THUMB is not declared in thumbs.ts in the shape this guard reads"
    client = [int(n) for n in re.findall(r":\s*(\d+)", m.group(1))]
    assert client and set(client) <= set(THUMB_BUCKETS), (client, THUMB_BUCKETS)
    assert THUMB_W in THUMB_BUCKETS
    assert list(THUMB_BUCKETS) == sorted(THUMB_BUCKETS)
