"""`store.post_images`: whether a connection can be sent images, and how many
(#377) -- plus the settings and the catalog field that answer it."""

import importlib

import pytest

from grimoire import catalog, store
from grimoire.store import config, llm_connections, post_images


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return tmp_path


def _conn(kind="openrouter", vision="", model="m"):
    cid = llm_connections.create_connection(kind, f"{kind}-{vision or 'auto'}-{model}",
                                            api_key="k", model=model, vision=vision)
    return llm_connections.read_connection_raw(cid)


def _catalog(conn, rows):
    llm_connections.set_cached_models(conn["id"], rows, conn["rev"])


# ---- catalog ----
def test_catalog_entry_reads_input_modalities():
    assert catalog.entry({"id": "m", "architecture": {"input_modalities": ["text", "image"]}})["vision"] is True
    assert catalog.entry({"id": "m", "architecture": {"input_modalities": ["text"]}})["vision"] is False
    assert catalog.entry({"id": "m"})["vision"] is None
    assert catalog.entry({"id": "m", "architecture": "x"})["vision"] is None
    assert catalog.entry({"id": "m", "architecture": {"input_modalities": "image"}})["vision"] is None


# ---- capability ----
def test_claude_never_reads_images_whatever_it_says(home):
    assert post_images.capability(_conn("claude", "on")) == "no"
    assert post_images.capability(None) == "no"


def test_the_override_wins(home):
    assert post_images.capability(_conn(vision="on")) == "yes"
    assert post_images.capability(_conn(vision="off")) == "no"


def test_auto_reads_the_cached_catalog(home):
    conn = _conn()
    assert post_images.capability(conn) == "unknown"
    _catalog(conn, [{"id": "m", "vision": True}])
    assert post_images.capability(conn) == "yes"
    _catalog(conn, [{"id": "m", "vision": False}])
    assert post_images.capability(conn) == "no"
    _catalog(conn, [{"id": "m", "vision": None}])
    assert post_images.capability(conn) == "unknown"
    _catalog(conn, [{"id": "other", "vision": True}])
    assert post_images.capability(conn) == "unknown"
    _catalog(conn, [{"id": "m"}])  # a sidecar written before #377
    assert post_images.capability(conn) == "unknown"


def test_an_unknown_stored_vision_value_reads_as_auto(home):
    conn = {**_conn(), "vision": "sometimes"}
    assert post_images.capability(conn) == "unknown"


# ---- limit / images_for / reach ----
def test_limit_is_zero_while_off_and_the_default_when_on(home):
    assert post_images.limit() == 0
    config.write_config(send_images="on")
    assert post_images.limit() == 3


@pytest.mark.parametrize("raw, want", [("abc", 3), ("-2", 3), ("0", 0), ("5", 5)])
def test_limit_reads_fail_soft(home, raw, want):
    config.write_config(send_images="on", send_images_limit=raw)
    assert post_images.limit() == want


def test_images_for_needs_both_the_setting_and_the_capability(home):
    yes, no = _conn(vision="on"), _conn(vision="off")
    assert post_images.images_for(yes) == 0
    config.write_config(send_images="on")
    assert post_images.images_for(yes) == 3
    assert post_images.images_for(no) == 0
    assert post_images.reach(no) == "no"
    assert post_images.reach(yes) == "yes"
    config.write_config(send_images="off")
    assert post_images.reach(yes) == "off"


# ---- routes ----
def test_send_images_round_trips_and_is_validated(client):
    cfg = client.get("/api/config").json()
    assert cfg["send_images"] == "off" and cfg["send_images_limit"] == "3"
    assert cfg["send_images_reach"] == "off"
    assert client.put("/api/config", json={"send_images": "maybe"}).status_code == 400
    r = client.put("/api/config", json={"send_images": "on", "send_images_limit": "2"})
    assert r.status_code == 200
    body = client.get("/api/config").json()
    assert body["send_images"] == "on" and body["send_images_limit"] == "2"


def test_reach_describes_the_chat_connection(client):
    cid = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "Seraphine", "api_key": "k", "model": "m"}).json()["id"]
    client.put("/api/config", json={"active_connection_id": cid, "send_images": "on"})
    assert client.get("/api/config").json()["send_images_reach"] == "unknown"
    client.put(f"/api/llm-connections/{cid}", json={"vision": "on"})
    assert client.get("/api/config").json()["send_images_reach"] == "yes"


def test_connection_vision_is_constrained_and_round_trips(client):
    bad = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "Mara", "api_key": "k", "vision": "sometimes"})
    assert bad.status_code == 422
    cid = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "Mara", "api_key": "k", "vision": "on"}).json()["id"]
    assert client.get(f"/api/llm-connections/{cid}").json()["vision"] == "on"
    client.put(f"/api/llm-connections/{cid}", json={"vision": ""})
    assert client.get(f"/api/llm-connections/{cid}").json()["vision"] == ""


# ---- resolving and encoding (Task 4) ----
import base64  # noqa: E402
import io  # noqa: E402

from PIL import Image  # noqa: E402

from grimoire.store import campaign_images, campaigns, export, image_drafts, worlds  # noqa: E402


def _image(size=(4, 4), mode="RGB", fmt="PNG", exif=None) -> bytes:
    buf = io.BytesIO()
    color = (10, 20, 30, 128) if mode == "RGBA" else (10, 20, 30)
    kwargs = {"exif": exif} if exif is not None else {}
    Image.new(mode, size, color).save(buf, fmt, **kwargs)
    return buf.getvalue()


@pytest.fixture
def cid(home):
    post_images._CACHE.clear()
    return campaigns.create_campaign("Saltmarch Nights", worlds.create_world("Realm"))


def _url(cid, name):
    return f"/api/campaigns/{cid}/images/{name}"


def _decode(uri):
    head, payload = uri.split(",", 1)
    return head, Image.open(io.BytesIO(base64.b64decode(payload)))


def test_resolve_url_finds_the_library_image_and_refuses_remote(cid):
    campaign_images.put_image(cid, "coastline", _image(), "png")
    assert export.resolve_url(cid, _url(cid, "coastline")) is not None
    assert export.resolve_url(cid, "https://e.example/x.png") is None
    assert export.resolve_url(cid, _url(cid, "missing")) is None


def test_resolve_url_resolves_against_the_campaign_asked_not_the_url(cid):
    campaign_images.put_image(cid, "coastline", _image(), "png")
    assert export.resolve_url(cid, "/api/campaigns/some-other/images/coastline") is not None


def test_eligible_needs_a_resolvable_sniffable_image(cid):
    campaign_images.put_image(cid, "coastline", _image(), "png")
    assert post_images.eligible(cid, _url(cid, "coastline"))
    assert not post_images.eligible(cid, _url(cid, "missing"))
    assert not post_images.eligible(cid, "https://e.example/x.png")
    path = export.resolve_url(cid, _url(cid, "coastline"))
    path.write_bytes(b"not an image at all")
    assert not post_images.eligible(cid, _url(cid, "coastline"))


def test_decodable_gates_webp_on_the_build():
    from PIL import features
    assert post_images.decodable("png") and post_images.decodable("jpg")
    assert post_images.decodable("webp") == bool(features.check("webp"))
    assert not post_images.decodable("svg")


def test_load_downscales_to_a_jpeg_within_the_send_edge(cid):
    campaign_images.put_image(cid, "big", _image((3000, 2000)), "png")
    head, im = _decode(post_images.load(cid, {"url": _url(cid, "big")}))
    assert head == "data:image/jpeg;base64"
    assert im.size == (1024, 683)


def test_load_keeps_alpha_as_png(cid):
    campaign_images.put_image(cid, "glass", _image((10, 10), "RGBA"), "png")
    head, im = _decode(post_images.load(cid, {"url": _url(cid, "glass")}))
    assert head == "data:image/png;base64"
    assert im.mode == "RGBA"


def test_load_applies_exif_orientation(cid):
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW to display
    campaign_images.put_image(cid, "phone", _image((40, 20), fmt="JPEG", exif=exif.tobytes()), "jpg")
    _head, im = _decode(post_images.load(cid, {"url": _url(cid, "phone")}))
    assert im.size == (20, 40)


def test_load_caches_until_the_file_changes(cid, monkeypatch):
    campaign_images.put_image(cid, "coastline", _image((8, 8)), "png")
    opened = []
    real = post_images.Image.open
    monkeypatch.setattr(post_images.Image, "open", lambda *a, **k: opened.append(1) or real(*a, **k))
    first = post_images.load(cid, {"url": _url(cid, "coastline")})
    assert post_images.load(cid, {"url": _url(cid, "coastline")}) == first
    assert len(opened) == 1
    campaign_images.put_image(cid, "coastline", _image((9, 9)), "png")
    assert post_images.load(cid, {"url": _url(cid, "coastline")}) != first
    assert len(opened) == 2


def test_load_refuses_oversize_sources_and_encodings(cid, monkeypatch):
    campaign_images.put_image(cid, "coastline", _image((64, 64)), "png")
    monkeypatch.setattr(image_drafts, "MAX_BYTES", 10)
    assert post_images.load(cid, {"url": _url(cid, "coastline")}) is None
    monkeypatch.undo()
    post_images._CACHE.clear()
    monkeypatch.setattr(post_images, "MAX_SEND_BYTES", 10)
    assert post_images.load(cid, {"url": _url(cid, "coastline")}) is None


def test_load_never_raises(cid, monkeypatch):
    campaign_images.put_image(cid, "coastline", _image(), "png")

    def boom(*_a, **_k):
        raise OSError("decoder exploded")

    monkeypatch.setattr(post_images.Image, "open", boom)
    assert post_images.load(cid, {"url": _url(cid, "coastline")}) is None
    assert post_images.load(cid, {}) is None
    assert post_images.load(cid, {"url": "https://e.example/x.png"}) is None


def test_the_cache_is_bounded_by_bytes(cid, monkeypatch):
    monkeypatch.setattr(post_images, "CACHE_BYTES", 1)
    campaign_images.put_image(cid, "a", _image((8, 8)), "png")
    assert post_images.load(cid, {"url": _url(cid, "a")}) is not None
    assert len(post_images._CACHE) == 0  # bigger than the whole budget: not kept
