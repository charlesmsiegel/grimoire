"""The model-drafted first pass at a description.

Preview only — nothing here writes to the store — and available on the two
connection kinds whose client can carry an image.
"""

import base64
import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import llm, routes
from grimoire.main import create_app
from tests import draft_runs as drafts
from tests.llm_fakes import CapturingOpenRouter, FakeOpenRouterComplete

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    # `with`, so the lifespan runs: producing routes hand their work to a
    # runner that lives on it, and a client without one cannot drive a turn.
    with TestClient(app) as c:
        yield c


@pytest.fixture
def art(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-secret"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    made = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()
    cid, vid = made["character"], made["version"]
    client.put(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/gallery_1",
               files={"file": ("a.png", PNG, "image/png")})
    return wid, cid, vid


def _url(wid, cid, vid, name="gallery_1"):
    return (f"/api/worlds/{wid}/characters/{cid}/versions/{vid}"
            f"/images/{name}/description/draft")


def test_a_draft_comes_back_as_a_preview_and_is_not_stored(client, art):
    wid, cid, vid = art
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(
        "A rain-slick stone quay at dusk.\nFishing boats ride at anchor.")
    r = drafts.post(client, _url(wid, cid, vid))
    assert r.status_code == 200
    # Lines are joined, not truncated to the first: a description may run to
    # two or three sentences (unlike a tagline).
    assert r.json()["description"] == (
        "A rain-slick stone quay at dusk. Fishing boats ride at anchor.")
    # Preview only: the store still says nothing about this image.
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {}


def test_the_request_carries_the_image_bytes_and_the_subject_name(client, art):
    wid, cid, vid = art
    fake = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    assert drafts.post(client, _url(wid, cid, vid)).status_code == 200

    user = next(m for m in fake.messages if m["role"] == "user")
    parts = {p["type"]: p for p in user["content"]}
    assert "Seraphine" in parts["text"]["text"]
    uri = parts["image_url"]["image_url"]["url"]
    assert uri.startswith("data:image/png;base64,")
    # The bytes themselves, not a URL the provider could never reach.
    assert base64.b64decode(uri.split(",", 1)[1]) == PNG


def test_a_claude_connection_is_refused_with_a_reason_rather_than_crashing(client, art):
    """`claude_agent` joins message content as a string, so a multimodal message
    would raise deep inside the SDK path. The refusal names the fix."""
    wid, cid, vid = art
    client.put("/api/config", json={"active_connection_id": "claude"})
    client.app.dependency_overrides[routes.get_llm] = CapturingOpenRouter
    r = drafts.post(client, _url(wid, cid, vid))
    assert r.status_code == 409
    assert "cannot read images" in str(r.json()["detail"])


def test_drafting_an_image_that_is_not_there_is_a_404(client, art):
    wid, cid, vid = art
    client.app.dependency_overrides[routes.get_llm] = CapturingOpenRouter
    assert drafts.post(client, _url(wid, cid, vid, "nope")).status_code == 404


def test_parse_output_joins_and_trims():
    assert store.image_drafts.parse_output("  one \n\n two  \n") == "one two"
    assert store.image_drafts.parse_output("") == ""


def test_data_uri_refuses_a_type_we_cannot_label(tmp_path):
    p = tmp_path / "art.tiff"
    p.write_bytes(b"II*\x00")
    with pytest.raises(ValueError):
        store.image_drafts.data_uri(p)


# ---- the other three surfaces ---------------------------------------------

def test_a_pc_image_can_be_drafted(client, art):
    wid, _cid, _vid = art
    made = client.post(f"/api/worlds/{wid}/pcs", json={"name": "Mara"}).json()
    pid, pvid = made["pc"], made["version"]
    client.put(f"/api/worlds/{wid}/pcs/{pid}/versions/{pvid}/images/avatar",
               files={"file": ("a.png", PNG, "image/png")})
    fake = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    r = drafts.post(client, f"/api/worlds/{wid}/pcs/{pid}/versions/{pvid}"
                    f"/images/avatar/description/draft")
    assert r.status_code == 200
    user = next(m for m in fake.messages if m["role"] == "user")
    assert "Mara" in {p["type"]: p for p in user["content"]}["text"]["text"]


def test_an_entity_image_can_be_drafted(client, art):
    wid, _cid, _vid = art
    eid = client.post(f"/api/worlds/{wid}/locations",
                      json={"name": "Saltmarch Harbour"}).json()["id"]
    client.put(f"/api/worlds/{wid}/locations/{eid}/images/gallery_1",
               files={"file": ("a.png", PNG, "image/png")})
    fake = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    r = drafts.post(client, f"/api/worlds/{wid}/locations/{eid}/images/gallery_1/description/draft")
    assert r.status_code == 200
    user = next(m for m in fake.messages if m["role"] == "user")
    assert "Saltmarch Harbour" in {p["type"]: p for p in user["content"]}["text"]["text"]


def test_a_library_image_can_be_drafted_with_no_subject(client, art):
    """Library art belongs to the campaign and to no record -- which is the
    whole reason the library exists -- so there is no name to offer."""
    wid, _cid, _vid = art
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": wid}).json()["id"]
    client.put(f"/api/campaigns/{camp}/images/coastline",
               files={"file": ("a.png", PNG, "image/png")})
    fake = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    r = drafts.post(client, f"/api/campaigns/{camp}/images/coastline/description/draft")
    assert r.status_code == 200
    user = next(m for m in fake.messages if m["role"] == "user")
    assert "belongs to a record" not in {p["type"]: p for p in user["content"]}["text"]["text"]


def test_every_surface_refuses_a_claude_connection_the_same_way(client, art):
    """One helper serves all four, so the refusal cannot drift between them."""
    wid, cid, vid = art
    client.put("/api/config", json={"active_connection_id": "claude"})
    client.app.dependency_overrides[routes.get_llm] = CapturingOpenRouter
    eid = client.post(f"/api/worlds/{wid}/locations", json={"name": "Harbour"}).json()["id"]
    client.put(f"/api/worlds/{wid}/locations/{eid}/images/gallery_1",
               files={"file": ("a.png", PNG, "image/png")})
    for url in (_url(wid, cid, vid),
                f"/api/worlds/{wid}/locations/{eid}/images/gallery_1/description/draft"):
        r = drafts.post(client, url)
        assert (r.status_code, "cannot read images" in str(r.json()["detail"])) == (409, True), url


def test_every_connection_kind_is_classified_as_image_capable_or_not():
    """The rule lives in two places for two different callers -- the route
    refuses an unsupported PRIMARY with a message the reader can act on, and
    `llm` silently drops an unsupported FALLBACK it never chose. Neither can
    be right on its own, and a new connection kind that lands in neither list
    is a kind whose behaviour with an image is nobody's decision."""
    supported = set(store.image_drafts.SUPPORTED_KINDS)
    text_only = set(llm.TEXT_ONLY_KINDS)
    assert not supported & text_only
    assert supported | text_only == set(store.llm_connections.KINDS)


def test_an_oversized_image_is_refused_before_its_bytes_are_read(tmp_path, monkeypatch):
    """One draft holds the file three times over -- the bytes, their base64
    buffer, and the ~4/3-sized string that sits in the request. Only the
    campaign library caps its uploads, so a record image can be arbitrarily
    large, and on Android (Chaquopy) that is a killed process rather than an
    error anyone can act on."""
    p = tmp_path / "huge.png"
    p.write_bytes(PNG)
    monkeypatch.setattr(store.image_drafts, "MAX_BYTES", len(PNG) - 1)
    with pytest.raises(store.image_drafts.ImageTooLargeError):
        store.image_drafts.data_uri(p)
    # ...and never more than one byte past the cap, however big the file is:
    # the size and the bytes come from ONE open, so a file swapped or grown
    # after a `stat()` would have been read unbounded.
    read = []
    real_open = type(p).open

    def counting_open(self, *a, **kw):
        fh = real_open(self, *a, **kw)
        real_read = fh.read
        fh.read = lambda n=-1: read.append(n) or real_read(n)  # type: ignore[method-assign]
        return fh

    monkeypatch.setattr(type(p), "open", counting_open)
    with pytest.raises(store.image_drafts.ImageTooLargeError):
        store.image_drafts.data_uri(p)
    assert read == [store.image_drafts.MAX_BYTES + 1]


def test_the_route_reports_an_oversized_image_as_a_413(client, art, monkeypatch):
    wid, cid, vid = art
    monkeypatch.setattr(store.image_drafts, "MAX_BYTES", 1)
    r = drafts.post(client, _url(wid, cid, vid))
    assert (r.status_code, r.json()["detail"]) == (413, store.image_drafts.TOO_LARGE)
