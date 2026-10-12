"""Focus is in the initial scene publication, with a strict string API."""

import pytest

from grimoire import store
from tests.test_runs_routes import _reserve


@pytest.fixture
def cid(client):
    return store.campaigns.create_campaign("Saltmarch", store.worlds.create_world("Realm"))


def test_create_scene_with_focus(client, cid):
    response = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Mara", "focus": "  Let Mara choose.\nGently. "})
    assert response.status_code == 200
    sid = response.json()["id"]
    assert store.scenes.focus.read(cid, sid) == {"text": "Let Mara choose. Gently.", "status": "ok"}


def test_omitted_focus_is_compatible(client, cid):
    response = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Mara"})
    assert response.status_code == 200
    assert "focus" not in store.scenes.read_scene_meta(cid, response.json()["id"])


@pytest.mark.parametrize("value", [None, 42, True, {}, []])
def test_non_string_focus_is_rejected(client, cid, value):
    response = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Mara", "focus": value})
    assert response.status_code == 422
    assert list(store.scenes._scenes_dir(cid).glob("*.md")) == []


def test_focus_get_put_and_clear(client, cid):
    sid = store.scenes.create_scene(cid, "Mara")
    base = f"/api/campaigns/{cid}/scenes/{sid}/focus"
    assert client.get(base).json() == {"text": "", "status": "empty"}
    saved = client.put(base, json={"text": "  Let Mara choose.\nGently. "})
    assert saved.status_code == 200
    assert saved.json() == {"text": "Let Mara choose. Gently.", "status": "ok"}
    assert client.get(base).json() == saved.json()
    assert client.put(base, json={"text": ""}).json() == {"text": "", "status": "empty"}


@pytest.mark.parametrize("body", [{}, {"text": None}, {"text": 42}, {"text": True}, {"text": {}}, {"text": []}])
def test_focus_put_requires_a_string(client, cid, body):
    sid = store.scenes.create_scene(cid, "Mara")
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/focus", json=body).status_code == 422


@pytest.mark.parametrize("method", ["get", "put"])
def test_unknown_scene_focus_is_404(client, cid, method):
    kwargs = {"json": {"text": "Let Mara choose."}} if method == "put" else {}
    assert getattr(client, method)(f"/api/campaigns/{cid}/scenes/seraphine/focus", **kwargs).status_code == 404


def test_closed_branch_focus_put_does_not_publish(client, cid):
    sid = store.scenes.create_scene(cid, "Mara", focus="Let Mara choose.")
    store.scenes.append_message(cid, sid, "user", "Mara waits.")
    sibling = store.branch.branch_scene(cid, sid, 0, title="Seraphine")
    store.scenes.mark_absorbed(cid, sibling, "Mara waited.", "Mara waited.")
    before = store.scenes._scene_path(cid, sid).read_bytes()
    result = client.put(f"/api/campaigns/{cid}/scenes/{sid}/focus", json={"text": "Offer Seraphine a choice."})
    assert result.status_code == 409 and result.json()["kind"] == "branch_closed"
    assert store.scenes._scene_path(cid, sid).read_bytes() == before


def test_focus_put_during_generation_changes_no_transcript(client, cid):
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "user", "Mara waits.")
    run = _reserve(client, cid, sid)
    before = store.scenes.read_scene(cid, sid)
    result = client.put(f"/api/campaigns/{cid}/scenes/{sid}/focus", json={"text": "Let Mara choose."})
    assert result.status_code == 200
    after = store.scenes.read_scene(cid, sid)
    assert after == {**before, "meta": {**before["meta"], "focus": "Let Mara choose."}}
    assert not run.cancel_requested and not run.terminal.is_set()
