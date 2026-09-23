"""Thumbnail generation + the ?w= image route parameter."""

import io
import os
import re
import shutil
import threading
import time
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

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


# ---- a key that travels with the library ----
def _greeting_art(root, data=None):
    assets.put_image(root, "g1", "default", "embed-0", data or _png_bytes(), "png", base="greetings")
    return assets.image_path(root, "g1", "default", "embed-0", base="greetings")


def _counting_open(monkeypatch, delay=0.0):
    """Patch `Image.open` to count decodes; `delay` holds each one open so that
    concurrent askers are all inside before the first can publish."""
    real = Image.open
    opened: list = []

    def counting(*args, **kwargs):
        opened.append(args[0])
        if delay:
            time.sleep(delay)
        return real(*args, **kwargs)

    monkeypatch.setattr(thumbs.Image, "open", counting)
    return opened


def test_a_moved_library_reuses_the_thumbnails_it_carries(tmp_path, monkeypatch):
    # The cache lives inside the library, so it moves (or syncs) with it -- and
    # a key naming the absolute source path could never be hit from the new
    # location: every picture was paid for again, and the entries it carried
    # were garbage nobody could reach.
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(a))
    first = thumbs.thumbnail(_greeting_art(a), 320)
    assert first is not None
    shutil.copytree(a, b, symlinks=True)  # copy2: modification times survive, as they do a `mv`
    monkeypatch.setenv("GRIMOIRE_HOME", str(b))
    opened = _counting_open(monkeypatch)
    again = thumbs.thumbnail(assets.image_path(b, "g1", "default", "embed-0", base="greetings"), 320)
    assert again == b / first.relative_to(a)
    assert opened == []  # served from the copy it carried, not decoded again


def test_a_sync_that_keeps_only_microseconds_still_hits(tmp_path, monkeypatch):
    # NTFS stores 100 ns ticks and ext4/APFS store nanoseconds, so a copy synced
    # across that boundary comes back with its last two digits gone. A key that
    # named them would miss on every such file.
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(a))
    src = _greeting_art(a)
    os.utime(src, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
    first = thumbs.thumbnail(src, 320)
    shutil.copytree(a, b, symlinks=True)
    moved = b / src.relative_to(a)
    os.utime(moved, ns=(1_700_000_000_123_456_700, 1_700_000_000_123_456_700))
    monkeypatch.setenv("GRIMOIRE_HOME", str(b))
    opened = _counting_open(monkeypatch)
    assert thumbs.thumbnail(moved, 320) == b / first.relative_to(a)
    assert opened == []


def test_a_rewrite_that_moves_the_mtime_is_a_new_entry(tmp_path, monkeypatch):
    # The coarser clock must still tell two writes apart: same bytes, same
    # size, a millisecond later -> a new entry, not the old one served again.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    src = _greeting_art(tmp_path)
    os.utime(src, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    before = thumbs.thumbnail(src, 320)
    os.utime(src, ns=(1_700_000_000_001_000_000, 1_700_000_000_001_000_000))
    assert thumbs.thumbnail(src, 320) != before


def test_a_source_outside_the_library_still_gets_a_thumbnail(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    src = elsewhere / "a.png"
    src.write_bytes(_png_bytes())
    tp = thumbs.thumbnail(src, 320)
    assert tp is not None and tp.parent == home / ".cache" / "thumbs" / thumbs.generation()
    with Image.open(tp) as im:
        assert max(im.size) == 320
    # A sibling that merely shares the library's name as a prefix is outside
    # it too: named absolutely, whichever library asks.
    sibling = tmp_path / "home-other" / "a.png"
    st = src.stat()
    assert thumbs._key(sibling, st, 320, home) == thumbs._key(sibling, st, 320, tmp_path / "x")


def test_entries_live_in_the_current_generations_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    tp = thumbs.thumbnail(_greeting_art(tmp_path), 320)
    assert tp is not None
    assert tp.parent == tmp_path / ".cache" / "thumbs" / thumbs.generation()
    assert re.fullmatch(r"[0-9a-f]{32}\.webp", tp.name)
    # the generation names the encoder, so an encoder change is a namespace change
    assert thumbs.ENCODER in thumbs.generation()


# ---- retired generations are swept ----
def _hex(i: int) -> str:
    return f"{i:032x}.webp"


def _put(p: Path, data: bytes = b"x" * 100) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_the_sweep_retires_old_generations_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cache = tmp_path / ".cache" / "thumbs"
    current = [_put(cache / thumbs.generation() / _hex(i)) for i in range(3)]
    flat = [_put(cache / _hex(i)) for i in range(3)]  # the layout before generations
    old = cache / "webp-q80-m4"
    retired = [_put(old / _hex(i)) for i in range(3)] + [
        _put(old / f".{_hex(9)}.abcd_123.tmp")]  # a write that crashed there
    kept = cache / "webp-q70-m4"
    ours_beside_a_stranger = _put(kept / _hex(5))
    strangers = [_put(cache / "notes.txt"), _put(cache / "readme.webp"), _put(cache / f"{_hex(4)}.bak"),
                 _put(kept / "mine.png"), _put(old / "deeper" / _hex(6)),
                 _put(tmp_path / ".cache" / "embeddings" / _hex(7))]
    # `old/deeper` is not the cache's shape, so it and what it holds stay --
    # and so does `old`, which is then not empty.
    freed = thumbs.sweep(tmp_path)
    assert all(p.exists() for p in current + strangers)
    assert not any(p.exists() for p in flat + retired + [ours_beside_a_stranger])
    assert kept.is_dir() and old.is_dir()
    assert freed == 100 * (len(flat) + len(retired) + 1)
    # an emptied generation goes with its entries
    (old / "deeper" / _hex(6)).unlink()
    (old / "deeper").rmdir()
    _put(old / _hex(1))
    thumbs.sweep(tmp_path)
    assert not old.exists()


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks on Windows needs elevation")
def test_the_sweep_does_not_follow_a_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cache = tmp_path / ".cache" / "thumbs"
    outside = tmp_path / "outside"
    targets = [_put(outside / _hex(i)) for i in range(2)]
    cache.mkdir(parents=True)
    (cache / "webp-q60-m4").symlink_to(outside, target_is_directory=True)
    (cache / _hex(8)).symlink_to(targets[0])
    assert thumbs.sweep(tmp_path) == 0
    assert all(p.exists() for p in targets)
    assert (cache / "webp-q60-m4").is_symlink() and (cache / _hex(8)).is_symlink()


def test_the_sweep_of_a_library_with_no_cache_is_a_no_op(tmp_path):
    assert thumbs.sweep(tmp_path) == 0


def test_a_first_miss_starts_one_background_sweep_per_library(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbs, "_swept", set())
    spawned: list = []

    class Recorder:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            spawned.append((target, args, daemon))

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", Recorder)
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        root.mkdir()
        monkeypatch.setenv("GRIMOIRE_HOME", str(root))
        src = _greeting_art(root)
        for w in (128, 256, 256):  # two misses and a hit
            assert thumbs.thumbnail(src, w) is not None
    assert spawned == [(thumbs.sweep, (a,), True), (thumbs.sweep, (b,), True)]


def test_a_sweep_that_cannot_start_does_not_cost_the_tile(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))

    class Exhausted:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading, "Thread", Exhausted)
    assert thumbs.thumbnail(_greeting_art(tmp_path), 320) is not None


# ---- one decode per key ----
def test_concurrent_requests_for_one_thumbnail_decode_it_once(tmp_path, monkeypatch):
    # srcset candidates, a second tab, a re-render: the same tile asked for at
    # once used to decode the same source once per asker.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    src = _greeting_art(tmp_path)
    opened = _counting_open(monkeypatch, delay=0.2)
    n = 6
    barrier = threading.Barrier(n)
    got: list = [None] * n

    def ask(i):
        barrier.wait()
        got[i] = thumbs.thumbnail(src, 320)

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(opened) == 1
    assert got[0] is not None and got == [got[0]] * n
    assert thumbs._flights == {}  # a key's lock goes when its last asker does


def test_a_failed_decode_does_not_wedge_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    src = _greeting_art(tmp_path, b"not an image")
    assert thumbs.thumbnail(src, 320) is None
    assert thumbs._flights == {}
    _greeting_art(tmp_path)  # replaced with something decodable
    assert thumbs.thumbnail(assets.image_path(tmp_path, "g1", "default", "embed-0", base="greetings"),
                            320) is not None


# ---- downscale before converting ----
def _art(mode: str, size=(600, 900)) -> Image.Image:
    """Gradients with hard edges: flat colour would hide a resampling change."""
    w, h = size
    g = Image.linear_gradient("L").resize(size)
    rgb = Image.merge("RGB", (g, g.rotate(90).resize(size), g.transpose(Image.Transpose.FLIP_TOP_BOTTOM)))
    d = ImageDraw.Draw(rgb)
    for i in range(12):
        d.ellipse([i * 40, i * 60, i * 40 + 180, i * 60 + 140], outline=(250 - i * 20, i * 20, 90), width=5)
    alpha = Image.new("L", size, 0)
    ImageDraw.Draw(alpha).ellipse([w * 0.1, h * 0.05, w * 0.9, h * 0.95], fill=255)

    def with_alpha(im):
        im = im.copy()
        im.putalpha(alpha)
        return im

    return {
        "RGB": lambda: rgb,
        "RGBA": lambda: with_alpha(rgb),
        "L": lambda: rgb.convert("L"),
        "LA": lambda: with_alpha(rgb.convert("L")),
        "P": lambda: rgb.quantize(64),
        # a palette with transparency: what a quantized RGBA saves as
        "P-alpha": lambda: with_alpha(rgb).quantize(64),
        "1": lambda: rgb.convert("1"),
        "CMYK": lambda: rgb.convert("CMYK"),
        "I;16": lambda: rgb.convert("L").point(lambda v: v * 200, "I").convert("I;16"),
    }[mode]()


_MATRIX = [("RGB", "PNG"), ("RGBA", "PNG"), ("L", "PNG"), ("LA", "PNG"), ("P", "PNG"),
           ("P-alpha", "PNG"), ("1", "PNG"), ("I;16", "PNG"), ("RGB", "JPEG"), ("L", "JPEG"),
           ("CMYK", "JPEG")]


def _saved(mode, fmt, size=(600, 900)) -> bytes:
    buf = io.BytesIO()
    _art(mode, size).save(buf, format=fmt, **({"quality": 90} if fmt == "JPEG" else {}))
    return buf.getvalue()


def _old_downscale(im: Image.Image, width: int) -> Image.Image:
    """The pipeline this replaced: everything to RGB(A) at full size, then down."""
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")
    im.thumbnail((width, width))
    return im


def _as_seen(im: Image.Image) -> Image.Image:
    """What a reader sees: composited over a background, so the colour a fully
    transparent pixel happens to carry (anything; nobody sees it) is not a diff."""
    bg = Image.new("RGBA", im.size, (128, 128, 128, 255))
    return Image.alpha_composite(bg, im.convert("RGBA")).convert("RGB")


@pytest.mark.parametrize("mode,fmt", _MATRIX, ids=[f"{m}-{f}" for m, f in _MATRIX])
@pytest.mark.parametrize("width", [128, 320, 1024])
def test_the_downscale_matches_the_convert_first_pipeline(mode, fmt, width):
    # Same size, and the same picture to within a level or two. What does move
    # is the reducing pass `thumbnail` has always given RGB, which now reaches
    # the modes that used to go through RGBA (whose resize skips it): widest on
    # a bilevel dither, which is all edges, and a level or two elsewhere.
    data = _saved(mode, fmt)
    with Image.open(io.BytesIO(data)) as im:
        new = thumbs._downscale(im, width).copy()  # copy: loads it before the file closes
    with Image.open(io.BytesIO(data)) as im:
        old = _old_downscale(im, width).copy()
    assert new.size == old.size
    assert new.mode in ("RGB", "RGBA")
    diff = ImageChops.difference(_as_seen(new), _as_seen(old))
    mean, peak = max(ImageStat.Stat(diff).mean), max(hi for _lo, hi in diff.getextrema())
    assert mean <= (2.5 if mode == "1" else 1.0), (mode, fmt, width, mean)
    assert peak <= 16, (mode, fmt, width, peak)
    if mode in ("RGBA", "LA", "P-alpha"):  # the alpha itself survives too
        a_new = new.getchannel("A") if new.mode == "RGBA" else Image.new("L", new.size, 255)
        a_diff = ImageChops.difference(a_new, old.convert("RGBA").getchannel("A"))
        assert max(ImageStat.Stat(a_diff).mean) <= 1.5


@pytest.mark.parametrize("mode,fmt", _MATRIX, ids=[f"{m}-{f}" for m, f in _MATRIX])
def test_every_mode_thumbnails_through_the_cache(tmp_path, monkeypatch, mode, fmt):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    src = tmp_path / f"art.{fmt.lower()}"
    src.write_bytes(_saved(mode, fmt))
    tp = thumbs.thumbnail(src, 256)
    assert tp is not None
    with Image.open(tp) as im:
        assert im.format == "WEBP" and im.size == (171, 256)


@pytest.mark.parametrize("mode,fmt", [("L", "PNG"), ("L", "JPEG"), ("CMYK", "JPEG")])
def test_no_full_size_conversion_runs_before_the_downscale(mode, fmt, monkeypatch):
    # Converting first made every palette-less non-RGB source pay a full
    # resolution pass (and 4 bytes a pixel) before anything shrank it, and for
    # a JPEG it loaded the image outright, which is what defeats the reduced
    # size draft decode `thumbnail` would otherwise ask for.
    converted: list = []
    real = Image.Image.convert

    def spy(self, *args, **kwargs):
        converted.append(self.size)
        return real(self, *args, **kwargs)

    data = _saved(mode, fmt, size=(1200, 1800))
    monkeypatch.setattr(Image.Image, "convert", spy)
    with Image.open(io.BytesIO(data)) as im:
        out = thumbs._downscale(im, 256)
    assert out.size == (171, 256)
    assert all(max(s) <= 256 for s in converted), converted


def test_a_premultiplied_mode_needing_no_downscale_is_left_exact():
    # Premultiplying and back loses precision wherever alpha is low; a picture
    # already inside the box must not pay that for nothing.
    px = bytes(v for y in range(80) for x in range(100) for v in (x * 2, y * 3, 77, (x + y) % 256))
    im = Image.frombytes("RGBA", (100, 80), px)
    assert ImageChops.difference(thumbs._downscale(im.copy(), 320), im).getbbox() is None


# ---- the route's width buckets ----
def _avatar_route(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()["character"]
    base = f"/api/worlds/{wid}/characters/{cid}/versions/default/images/avatar"
    client.put(base, files={"file": ("a.png", io.BytesIO(_png_bytes()), "image/png")})
    return base


def _cached(tmp_path):
    d = tmp_path / ".cache" / "thumbs" / thumbs.generation()
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
