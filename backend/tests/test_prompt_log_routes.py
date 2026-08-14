"""The prompt-snapshot endpoints (#157), driven through the real routes.

`test_prompt_log_store.py` covers the store; this covers the half the store
cannot: that a real generating turn captures at all, that the detail endpoint
returns the same shape `GET .../context` does, and that an entry cannot be
read through a scene it does not belong to.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from grimoire import routes, store
from grimoire.main import create_app

from .llm_fakes import FakeOpenRouter


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])
    return TestClient(app)


def _scene(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    return cid, sid


def _chat(client, cid, sid, content="we make for the harbor"):
    return client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": content})


def test_a_chat_turn_captures_a_snapshot(client):
    cid, sid = _scene(client)
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"] == []

    _chat(client, cid, sid)

    entries = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["task"] == "chat"
    assert entries[0]["total_tokens"] > 0


def test_the_detail_endpoint_matches_the_live_context_shape(client):
    """The point of the shape being identical: the inspector renders a frozen
    turn with the code it already has."""
    cid, sid = _scene(client)
    _chat(client, cid, sid)
    eid = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"][0]["id"]

    frozen = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts/{eid}").json()
    live = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()

    assert set(live) <= set(frozen)
    for row in frozen["sections"]:
        assert set(row) == {"id", "label", "text", "tier", "dropped", "trimmed", "tokens"}


def test_the_snapshot_holds_while_the_live_view_moves(client):
    """The whole feature, end to end."""
    cid, sid = _scene(client)
    _chat(client, cid, sid)
    eid = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"][0]["id"]
    before = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts/{eid}").json()

    assert client.post(f"/api/campaigns/{cid}/lore",
                       json={"name": "Harbor Pact",
                             "body": "The pact was signed at dusk."}).status_code < 400
    _chat(client, cid, sid, "tell me of the pact")

    # The live view really did move — without this the assertion below would
    # pass just as happily against a store that never changed.
    live = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert "The pact was signed at dusk." in str(live["sections"])
    assert "The pact was signed at dusk." not in str(before["sections"])

    after = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts/{eid}").json()
    assert after == before


def test_each_generating_route_records_its_own_task(client):
    cid, sid = _scene(client)
    _chat(client, cid, sid)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/retry")
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate",
                json={"guidance": "shorter, please"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": ""})

    tasks = [e["task"] for e in
             client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"]]
    assert tasks == ["director", "regenerate", "retry", "chat"]


def test_a_regenerate_snapshot_reports_the_guidance_the_model_read(client):
    """The block is appended after the system message, so a snapshot built only
    from the packed sections would omit it — on exactly the turns people re-run
    because something looked wrong."""
    cid, sid = _scene(client)
    _chat(client, cid, sid)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate",
                json={"guidance": "make it shorter"})

    entries = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"]
    eid = next(e["id"] for e in entries if e["task"] == "regenerate")
    frozen = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts/{eid}").json()

    row = next(r for r in frozen["sections"] if r["label"] == "Regenerate guidance")
    assert "make it shorter" in row["text"]
    assert row["tokens"] > 0


def test_an_entry_cannot_be_read_through_another_scene(client):
    """Ids are campaign-scoped, so the scene in the path is checked against the
    entry rather than trusted."""
    cid, sid = _scene(client)
    other = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Elsewhere"}).json()["id"]
    _chat(client, cid, sid)
    eid = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"][0]["id"]

    assert client.get(f"/api/campaigns/{cid}/scenes/{other}/prompts/{eid}").status_code == 404


def test_an_unknown_entry_is_a_404(client):
    cid, sid = _scene(client)
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts/999999").status_code == 404


def test_the_endpoints_404_on_an_unknown_scene(client):
    cid, _sid = _scene(client)
    assert client.get(f"/api/campaigns/{cid}/scenes/nope/prompts").status_code == 404
    assert client.get(f"/api/campaigns/{cid}/scenes/nope/prompts/000001").status_code == 404


def test_a_renamed_scene_keeps_its_snapshots(client):
    cid, sid = _scene(client)
    _chat(client, cid, sid)
    eid = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"][0]["id"]

    new_sid = client.put(f"/api/campaigns/{cid}/scenes/{sid}",
                         json={"title": "The Long Road"}).json()["id"]
    assert new_sid != sid

    entries = client.get(f"/api/campaigns/{cid}/scenes/{new_sid}/prompts").json()["entries"]
    assert [e["id"] for e in entries] == [eid]
    assert client.get(
        f"/api/campaigns/{cid}/scenes/{new_sid}/prompts/{eid}").status_code == 200


def test_capture_off_leaves_the_list_empty_and_the_turn_intact(client):
    cid, sid = _scene(client)
    client.put("/api/config", json={"prompt_log_depth": "0"})

    assert _chat(client, cid, sid).status_code == 200
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"] == []
    # the turn itself still landed
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]


def test_a_regenerate_that_never_reaches_the_model_records_nothing(client, monkeypatch):
    """`supersede` runs after the context is built and can still refuse, which
    unwinds the reroll without a single token being sent. A snapshot written
    beside the compose would leave Turn history showing a regeneration that
    never happened."""
    cid, sid = _scene(client)
    _chat(client, cid, sid)
    before = len(client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"])

    def boom(*_a, **_k):
        raise RuntimeError("proposals write failed")

    monkeypatch.setattr(store.proposals, "supersede", boom)
    with pytest.raises(RuntimeError):
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")

    entries = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"]
    assert len(entries) == before
    assert not any(e["task"] == "regenerate" for e in entries)


def test_a_turn_that_never_claims_records_nothing(client, monkeypatch):
    """`_chat_stream` claims the turn under the campaign lock synchronously,
    before it returns — so a contended campaign raises StoreBusy there and
    nothing is ever sent. Recording ahead of that left Turn history showing a
    request the model never saw.

    The failure is injected at `_chat_stream` itself rather than at
    `campaign_lock`, which the route also takes BEFORE composing — patching that
    would raise above the snapshot anyway and prove nothing about the order.
    """
    cid, sid = _scene(client)
    _chat(client, cid, sid)
    before = len(client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"])

    def busy(*_a, **_k):
        raise store.locks.StoreBusy("campaign is busy")

    real_stream = routes.scenes._chat_stream
    monkeypatch.setattr(routes.scenes, "_chat_stream", busy)
    assert _chat(client, cid, sid).status_code == 409
    # restore just this one -- `monkeypatch.undo()` would also revert the
    # GRIMOIRE_HOME the fixture set, pointing the assertions at the real store
    monkeypatch.setattr(routes.scenes, "_chat_stream", real_stream)

    entries = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"]
    assert len(entries) == before


def test_a_scene_deleted_mid_turn_records_no_orphan_row(client, monkeypatch):
    """The rename/delete cleanup has already run by the time capture lands, so a
    row appended under the obsolete id is one nothing will ever repoint or
    remove — it just waits for the id to be recycled."""
    cid, sid = _scene(client)
    _chat(client, cid, sid)
    entries = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"]
    assert len(entries) == 1

    # Capture is reached with the scene already gone: `read_scene_meta` is the
    # check, and it runs under the same lock the append takes.
    def vanished(*_a, **_k):
        raise store.scenes.SceneNotFound(sid)

    real_meta = store.scenes.read_scene_meta
    monkeypatch.setattr(store.scenes, "read_scene_meta", vanished)
    _chat(client, cid, sid)
    monkeypatch.setattr(store.scenes, "read_scene_meta", real_meta)

    after = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts").json()["entries"]
    assert len(after) == 1, "a second row was appended for a scene that was gone"
