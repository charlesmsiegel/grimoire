"""A user post carries a stable opaque id, so the scene tracker can key a
record to it across edits, cuts and renumbering -- a transcript index cannot do
that, and the text is not an identifier."""

import re

import pytest

from grimoire import store
from grimoire.store import campaigns, scenes, worlds

pytestmark = pytest.mark.tracker

POST_ID = re.compile(r"^[0-9a-f]{32}$")


def _scene(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    return cid, scenes.create_scene(cid, "Opening")


def test_post_id_round_trips_through_every_rewrite(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    i = scenes.append_message(cid, sid, "user", "Hello.", post_id="a" * 32)
    assert scenes.read_scene(cid, sid)["messages"][i]["post_id"] == "a" * 32
    # edit_message rewrites the whole transcript; the id must come through it.
    scenes.edit_message(cid, sid, i, "Hello again.")
    msg = scenes.read_scene(cid, sid)["messages"][i]
    assert msg["content"] == "Hello again."
    assert msg["post_id"] == "a" * 32


def test_post_id_survives_a_cut_after_it(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    i = scenes.append_message(cid, sid, "user", "First.", post_id="b" * 32)
    scenes.append_message(cid, sid, "assistant", "Reply.")
    # delete_from rewrites the whole transcript from the cut onward.
    scenes.delete_from(cid, sid, i + 1)
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert len(msgs) == 1 and msgs[i]["post_id"] == "b" * 32


def test_plain_append_carries_no_post_id(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    i = scenes.append_message(cid, sid, "user", "Hello.")
    assert scenes.read_scene(cid, sid)["messages"][i] == {"role": "user", "content": "Hello."}
    # Byte-identical to what it was before post ids existed: no metadata comment.
    assert "grimoire-response" not in scenes.paths._scene_path(cid, sid).read_text(encoding="utf-8")


def _chat(client, cid, sid, content):
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": content}) as r:
        r.read()


def test_chat_stamps_a_post_id_on_every_post(client):
    """Whatever the tracker's setting: a post written while it was off is
    still one a campaign that switches it back on can track."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "k"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={}).json()["id"]

    assert client.put(f"/api/campaigns/{cid}/tracker", json={"setting": "on"}).status_code == 200
    _chat(client, cid, sid, "tracked")
    assert client.put(f"/api/campaigns/{cid}/tracker", json={"setting": "off"}).status_code == 200
    _chat(client, cid, sid, "untracked")

    users = [m for m in client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
             if m["role"] == "user"]
    assert [m["content"] for m in users] == ["tracked", "untracked"]
    assert POST_ID.match(users[0]["post_id"])
    assert POST_ID.match(users[1]["post_id"])
    assert users[0]["post_id"] != users[1]["post_id"]
    assert store.tracker.settings.enabled(cid) is False
