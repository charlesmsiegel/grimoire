"""The scenario-card import routes (#217).

Three routes, and the property worth the most across all three: **parse writes
nothing**. The review gate is only real if the extraction leaves the world
exactly as it found it, so every parse test here asserts an untouched world
rather than trusting the store tests to have covered it.
"""

import importlib
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from tests import draft_runs as drafts
from tests.llm_fakes import FailingOpenRouter, FakeOpenRouterComplete

CARD = {
    "spec": "chara_card_v3",
    "spec_version": "3.0",
    "data": {
        "name": "Saltmarch",
        "description": "A drowned town where Mara keeps the tide-gate.",
        "first_mes": "Mara is waiting at the tide-gate.",
        "alternate_greetings": ["The square is empty."],
        "character_book": {"entries": [
            {"keys": ["gate"], "name": "The Tide-Gate", "content": "Iron and barnacle.",
             "enabled": True},
        ]},
        "extensions": {},
    },
}

REPLY = json.dumps({
    "characters": [{"name": "Mara", "description": "Tends the gate.", "personality": "Watchful."}],
    "entries": [{"name": "The Tide-Gate", "keys": [], "body": "", "category": "locations"}],
})


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete(REPLY)
    # `with`, so the lifespan runs: producing routes hand their work to a
    # runner that lives on it, and a client without one cannot drive a turn.
    with TestClient(app) as c:
        yield c


@pytest.fixture
def wid(client):
    """A world with a usable LLM connection — every parse route needs one."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    return client.post("/api/worlds", json={"name": "Realm"}).json()["id"]


def _upload(client, wid, card=None, fmt="json"):
    blob = json.dumps(card if card is not None else CARD).encode()
    return drafts.post(client, f"/api/worlds/{wid}/scenario/parse",
                       files={"file": ("card.json", blob, "application/json")},
                       data={"format": fmt})


def _counts(client, wid) -> dict:
    return {
        "characters": len(client.get(f"/api/worlds/{wid}/characters").json()),
        "lore": len(client.get(f"/api/worlds/{wid}/lore").json()),
        "locations": len(client.get(f"/api/worlds/{wid}/locations").json()),
        "greetings": len(client.get(f"/api/worlds/{wid}/greetings").json()),
    }


# ------------------------------------------------------------------- parsing
def test_parsing_an_uploaded_card_proposes_a_cast_and_writes_nothing(client, wid):
    r = _upload(client, wid)
    assert r.status_code == 200
    body = r.json()
    assert [c["name"] for c in body["characters"]] == ["Mara"]
    assert [e["name"] for e in body["entries"]] == ["The Tide-Gate"]
    assert body["entries"][0]["category"] == "locations"          # re-filed by the model
    assert [g["name"] for g in body["greetings"]] == ["Saltmarch", "Saltmarch (alt 1)"]
    assert body["greetings"][0]["character"] == "Mara"
    assert _counts(client, wid) == {"characters": 0, "lore": 0, "locations": 0, "greetings": 0}


def test_a_parse_reaches_the_client_the_app_itself_holds(monkeypatch, tmp_path):
    """Every other test here injects at `dependency_overrides`, which replaces
    `get_llm` whole -- so none of them would notice if the real provider stopped
    resolving. This one leaves `get_llm` in place and swaps what the app holds
    (#215), which is the path a shipped server actually takes."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.state.llm = FakeOpenRouterComplete(REPLY)
    # `with`, like the shared fixture: the parse is detached now, so its work is
    # handed to a runner that lives on the lifespan and a client without one
    # cannot drive it.
    with TestClient(app) as client:
        client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
        wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]

        body = _upload(client, wid).json()

        assert [c["name"] for c in body["characters"]] == ["Mara"]


def test_a_parse_says_which_of_the_cast_the_world_already_has(client, wid):
    assert _upload(client, wid).json()["characters"][0]["exists"] is False
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    assert _upload(client, wid).json()["characters"][0]["exists"] is True


def test_parsing_an_unreadable_card_is_a_400(client, wid):
    r = drafts.post(client, f"/api/worlds/{wid}/scenario/parse",
                    files={"file": ("card.json", b"not a card", "application/json")},
                    data={"format": "json"})
    assert r.status_code == 400
    assert "could not parse card" in r.json()["detail"]


def test_parsing_without_a_connection_is_a_409_and_beats_the_cards_own_errors(client):
    """A setup mistake is reported ahead of anything about the card: telling
    someone their card is unreadable when the real problem is that no model is
    configured sends them to fix the wrong thing."""
    world = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    for r in (_upload(client, world),
              drafts.post(client, f"/api/worlds/{world}/scenario/parse",
                          files={"file": ("c.json", b"not a card", "application/json")},
                          data={"format": "json"}),
              drafts.post(client, f"/api/worlds/{world}/scenario/parse-url", json={"url": "not a url"})):
        assert r.status_code == 409
        assert r.json()["kind"] == "missing_key"


def test_a_provider_failure_surfaces_as_its_own_status_and_still_writes_nothing(client, wid):
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "rate_limit", "slow down")
    r = _upload(client, wid)
    assert r.status_code == 429                 # the kind's own status (#213)
    assert r.json()["kind"] == "rate_limit"
    assert _counts(client, wid) == {"characters": 0, "lore": 0, "locations": 0, "greetings": 0}


def test_a_reply_with_no_json_still_proposes_what_the_card_alone_holds(client, wid):
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("I would rather not.")
    body = _upload(client, wid).json()
    assert body["characters"] == []
    assert [e["name"] for e in body["entries"]] == ["The Tide-Gate"]
    assert len(body["greetings"]) == 2


def test_parsing_a_card_from_a_url(client, wid, monkeypatch):
    png = store.cards.dumps(CARD, "png")
    monkeypatch.setattr(store.chub, "fetch_character_node", lambda fp: {
        "id": 1, "max_res_url": "https://avatars.charhub.io/avatars/creator/saltmarch/c.png"})
    monkeypatch.setattr(store.fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    r = drafts.post(client, f"/api/worlds/{wid}/scenario/parse-url",
                    json={"url": "https://chub.ai/characters/creator/saltmarch"})
    assert r.status_code == 200
    assert [c["name"] for c in r.json()["characters"]] == ["Mara"]
    assert _counts(client, wid)["characters"] == 0


def test_the_attempt_is_discoverable_before_the_download_starts(client, wid, monkeypatch):
    """A POST WITH NO RESPONSE IS AMBIGUOUS, and the download is the longest
    window in which one can be lost -- a whole HTTP fetch against somebody
    else's host. Reserved after it, a client whose connection died mid-download
    asks `?attempt=` and is told, truthfully, that there is no such run; the
    handler then reserves one and spends a provider call anyway, so the user's
    retry pays for a second one.

    Asked at the seam rather than by racing the request: the download itself
    looks the registry up, which is the only place that can observe the
    ordering from inside.
    """
    png = store.cards.dumps(CARD, "png")
    seen = []

    def looking(url):
        seen.append(client.get(f"/api/worlds/{wid}/runs?attempt=mine").json()["runs"])
        return png, "png", url, None

    monkeypatch.setattr(store.characters, "download_card", looking)
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete(REPLY)

    r = drafts.post(client, f"/api/worlds/{wid}/scenario/parse-url",
                    json={"url": "https://chub.ai/characters/creator/saltmarch"},
                    headers={"X-Grimoire-Attempt": "mine"})

    assert r.status_code == 200
    assert [run["kind"] for run in seen[0]] == ["scenario"], \
        "the attempt was not addressable while the download was running"


def test_a_url_that_is_not_one_is_a_400_and_an_unreachable_one_a_404(client, wid, monkeypatch):
    assert drafts.post(client, f"/api/worlds/{wid}/scenario/parse-url",
                       json={"url": "not a url"}).status_code == 400
    monkeypatch.setattr(store.chub, "fetch_character_node", lambda fp: None)
    r = drafts.post(client, f"/api/worlds/{wid}/scenario/parse-url",
                    json={"url": "https://chub.ai/characters/creator/missing"})
    assert r.status_code == 404


def test_parsing_an_unknown_world_is_a_404(client):
    assert _upload(client, "nope").status_code == 404


# ------------------------------------------------------------------ importing
def test_importing_the_reviewed_proposal_creates_the_records(client, wid):
    prop = _upload(client, wid).json()
    prop["art"] = False
    r = client.post(f"/api/worlds/{wid}/scenario/import", json=prop)
    assert r.status_code == 200
    out = r.json()
    assert [c["name"] for c in out["characters"]] == ["Mara"]
    assert out["entries"] == [{"kind": "locations", "id": "the-tide-gate"}]
    assert len(out["greetings"]) == 2
    assert _counts(client, wid) == {"characters": 1, "lore": 0, "locations": 1, "greetings": 2}

    gid = out["greetings"][0]["id"]
    greeting = client.get(f"/api/worlds/{wid}/greetings/{gid}").json()
    assert greeting["meta"]["character"] == out["characters"][0]["id"]


def test_a_row_the_reviewer_removed_is_never_written(client, wid):
    prop = _upload(client, wid).json()
    prop["characters"] = []
    prop["greetings"] = prop["greetings"][:1]
    prop["art"] = False
    client.post(f"/api/worlds/{wid}/scenario/import", json=prop)
    assert _counts(client, wid) == {"characters": 0, "lore": 0, "locations": 1, "greetings": 1}


def test_importing_localizes_the_openers_art_into_the_greeting(client, wid, monkeypatch):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
    monkeypatch.setattr(store.fetch, "_http_get_bytes", lambda url: (buf.getvalue(), "image/png"))

    prop = {"characters": [], "entries": [], "greetings": [
        {"name": "Opener", "body": "![](https://example.com/a.png)", "character": "", "present": []}]}
    out = client.post(f"/api/worlds/{wid}/scenario/import", json=prop).json()
    assert out["art"] == {"total": 1, "localized": 1, "skipped": 0, "failed": 0, "capped": False}

    gid = out["greetings"][0]["id"]
    body = client.get(f"/api/worlds/{wid}/greetings/{gid}").json()["body"]
    assert f"/api/worlds/{wid}/greetings/{gid}/images/" in body
    # ...and the image the body now names is actually servable
    name = body.split("/images/")[1].rstrip(")\n ")
    assert client.get(f"/api/worlds/{wid}/greetings/{gid}/images/{name}").status_code == 200


def test_importing_an_unknown_category_is_a_400(client, wid):
    r = client.post(f"/api/worlds/{wid}/scenario/import", json={
        "entries": [{"name": "X", "keys": [], "body": "y", "category": "bogus"}], "art": False})
    assert r.status_code == 400


def test_importing_into_an_unknown_world_is_a_404(client):
    assert client.post("/api/worlds/nope/scenario/import",
                       json={"art": False}).status_code == 404


def test_importing_needs_no_llm_connection(client):
    """The write half is pure file IO — a world that cannot reach a model can
    still import a proposal it saved, or one built from the card alone."""
    world = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    r = client.post(f"/api/worlds/{world}/scenario/import", json={
        "greetings": [{"name": "Opener", "body": "text"}], "art": False})
    assert r.status_code == 200
    assert len(client.get(f"/api/worlds/{world}/greetings").json()) == 1
