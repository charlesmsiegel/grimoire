"""A recovery fence cancels a delayed send before its setup can write."""

import pytest

from grimoire import store


@pytest.mark.parametrize("director", [False, True])
def test_precancelled_turn_leaves_posts_and_pending_proposal_untouched(client, director):
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Arrival")
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-test"})
    proposal = store.proposals.new(cid, sid, {})
    before = store.scenes.read_scene(cid, sid)["messages"]
    base = f"/api/campaigns/{cid}/scenes/{sid}"

    # Recovery runs before the delayed POST reserves anything. Recording this
    # fence must cover synchronous route setup as well as provider execution.
    cancelled = client.post(f"{base}/attempt-cancel", params={"attempt": "delayed/1"})
    assert cancelled.status_code == 200 and cancelled.json()["run"] is None
    body = {"content": "Mara waits.", "director": director}
    for _ in range(2):  # the second delivery must replay the cancelled outcome
        response = client.post(f"{base}/chat", json=body,
                               headers={"X-Grimoire-Attempt": "delayed/1"})
        assert response.status_code == 200, response.text
        assert store.scenes.read_scene(cid, sid)["messages"] == before
        assert store.proposals.get(cid, sid) == proposal
    state = client.get(f"{base}/attempt-state", params={"attempt": "delayed/1"}).json()
    assert state["run"]["state"] == "cancelled"
    assert state["retained"] is False

    # The cancelled attempt must release scene exclusion for a genuine send.
    next_send = client.post(f"{base}/chat", json={"content": "Mara steps onto the dock."},
                            headers={"X-Grimoire-Attempt": "next/1"})
    assert next_send.status_code == 200, next_send.text
    assert any(post["content"] == "Mara steps onto the dock."
               for post in store.scenes.read_scene(cid, sid)["messages"])
