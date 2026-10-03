"""The scene tracker's update run: scheduled after every post, in order per scene.

Each post (a player's, a character's, an opener) gets a `background` run whose
outcome is that post's snapshot. These drive the real routes and wait on the
runs the way `test_turn_follow_ups.py` does -- they are scheduled before the
POST answers and driven on the lifespan loop, so only their completion is in
question.
"""

from __future__ import annotations

import json

import pytest

from grimoire import routes, store

from .llm_fakes import FakeLLM, HeldCassette, from_entries

pytestmark = pytest.mark.tracker

#: The system prompt that owns a tracker update (templates/tracker/update_system.j2).
TRACKER = {"system_contains": "You maintain the scene state tracker"}

#: Mara's reply, ending her contribution so the round stops after one response.
MARA_SAYS = 'Mara answers.\n```handoff\n{"next":null}\n```'

RUN_TIMEOUT = 10.0


def _llm(*tracker_entries: dict, reply: str = MARA_SAYS) -> list[dict]:
    """Cassette entries: the tracker's own first, then a catch-all turn reply."""
    return [*tracker_entries, {"when": {}, "reply": reply}]


def _tracked(reply: dict | str, **when) -> dict:
    body = reply if isinstance(reply, str) else json.dumps(reply)
    return {"when": {**TRACKER, **when}, "reply": body}


def _use(client, llm: FakeLLM) -> FakeLLM:
    client.app.dependency_overrides[routes.get_llm] = lambda: llm
    return llm


def _scene(client) -> tuple[str, str]:
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Harbour")
    for name in ("Mara", "Winifred"):
        actor = client.post(f"/api/campaigns/{cid}/characters",
                            json={"name": name}).json()["character"]
        r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert r.status_code == 200, r.text
    return cid, sid


def _send(client, cid, sid, text="Mara, the tide is turning."):
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                    json={"content": text, "speaker_ref": "characters:mara"})
    assert r.status_code == 200, r.text
    return r


def _identity(cid, sid) -> str:
    return store.scenes.scene_identity(cid, sid)


def _updates(client, cid, ident) -> list:
    runs = client.app.state.runs.for_subject(("scene", cid, ident))
    return [r for r in runs if r.cls == "background" and r.kind == "tracker-update"]


def _settle(client, cid, ident) -> list:
    found = _updates(client, cid, ident)
    for run in found:
        assert run.terminal.wait(timeout=RUN_TIMEOUT), "a tracker update never finished"
    return found


def _index(cid, ident) -> dict:
    return store.tracker.records.read_index(cid, ident)


def _tracker_requests(llm: FakeLLM) -> list[str]:
    """The user message of every tracker request, in the order they were made."""
    out = []
    for req in llm.requests:
        msgs = req["messages"]
        if any("You maintain the scene state tracker" in m.get("content", "")
               for m in msgs if m.get("role") == "system"):
            out.append(next(m["content"] for m in msgs if m.get("role") == "user"))
    return out


def test_a_send_tracks_the_user_post_and_the_reply(client):
    _use(client, from_entries(_llm(_tracked({"changes": {"Mara": {"visible_mood": "joy"}}}))))
    cid, sid = _scene(client)
    _send(client, cid, sid)
    ident = _identity(cid, sid)
    assert len(_settle(client, cid, ident)) == 2
    keys = [k for _, k in store.tracker.walk.ordered_keys(cid, sid)]
    assert len(keys) == 2 and keys[0].startswith("p-") and keys[1].startswith("r-")
    index = _index(cid, ident)
    assert [index[k]["status"] for k in keys] == ["ok", "ok"]
    _, state = store.tracker.walk.current(cid, sid)
    assert state["characters:mara"]["fields"]["visible_mood"]["value"] == "joy"


def test_updates_in_one_scene_run_in_order(client):
    """The second update waits for the first and starts from what it wrote.

    The first (the player's post) is HELD at the provider until the turn has
    landed and scheduled the second, so without the per-scene lock the second
    would read its prior while the first had written nothing."""
    llm = _use(client, HeldCassette(_llm(
        _tracked({}, user_contains="# New post\nMara:"),
        _tracked({"changes": {"Mara": {"clothing": "cloak"}}})), hold=TRACKER))
    cid, sid = _scene(client)
    try:
        _send(client, cid, sid)
        llm.await_held()
    finally:
        llm.release()
    ident = _identity(cid, sid)
    assert len(_settle(client, cid, ident)) == 2
    asked = _tracker_requests(llm)
    assert len(asked) == 2
    assert "clothing: cloak" not in asked[0]
    assert "clothing: cloak" in asked[1]


def test_failure_marks_failed_and_play_continues(client):
    _use(client, from_entries(_llm(_tracked("not json"))))
    cid, sid = _scene(client)
    r = _send(client, cid, sid)
    assert '"error"' not in r.text
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["content"] == "Mara answers."
    ident = _identity(cid, sid)
    _settle(client, cid, ident)
    index = _index(cid, ident)
    keys = [k for _, k in store.tracker.walk.ordered_keys(cid, sid)]
    assert keys and all(index[k]["status"] == "failed" for k in keys)
    assert all(index[k]["error"] for k in keys)


def test_reroll_gets_its_own_record_and_swipe_back_is_free(client):
    _use(client, from_entries(_llm(_tracked({"changes": {"Mara": {"pose": "standing"}}}))))
    cid, sid = _scene(client)
    _send(client, cid, sid)
    ident = _identity(cid, sid)
    _settle(client, cid, ident)
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    first_vid = store.responses.variants_by_response(cid, sid)[rid][0]
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    r = client.post(f"{base}/responses/{rid}/regenerate", json={})
    assert '"error"' not in r.text, r.text
    _settle(client, cid, ident)
    active, vids = store.responses.variants_by_response(cid, sid)[rid]
    assert active != first_vid and len(vids) == 2
    index = _index(cid, ident)
    for vid in vids:
        assert index[store.tracker.paths.response_key(rid, vid)]["status"] == "ok"
    before = len(_updates(client, cid, ident))
    r = client.post(f"{base}/responses/{rid}/variants/{first_vid}/activate")
    assert r.status_code == 200, r.text
    keys = [k for _, k in store.tracker.walk.ordered_keys(cid, sid)]
    assert keys[-1] == store.tracker.paths.response_key(rid, first_vid)
    assert len(_updates(client, cid, ident)) == before


def test_update_for_a_cut_post_is_discarded(client):
    llm = _use(client, HeldCassette(_llm(_tracked({"changes": {"Mara": {"pose": "x"}}})),
                                    hold=TRACKER))
    cid, sid = _scene(client)
    ident = _identity(cid, sid)
    try:
        _send(client, cid, sid)
        llm.await_held()
        keys = [k for _, k in store.tracker.walk.ordered_keys(cid, sid)]
        assert len(keys) == 2
        first = next(i for i, m in enumerate(store.scenes.read_scene(cid, sid)["messages"])
                     if m.get("role") == "user")
        r = client.delete(f"/api/campaigns/{cid}/scenes/{sid}/messages/{first}")
        assert r.status_code == 200, r.text
    finally:
        llm.release()
    _settle(client, cid, ident)
    index = _index(cid, ident)
    for key in keys:
        assert key not in index
        assert store.tracker.records.read_snapshot(cid, ident, key) is None


def test_update_survives_a_rename(client):
    llm = _use(client, HeldCassette(_llm(_tracked({"changes": {"Mara": {"pose": "seated"}}})),
                                    hold=TRACKER))
    cid, sid = _scene(client)
    ident = _identity(cid, sid)
    try:
        _send(client, cid, sid)
        llm.await_held()
        r = client.put(f"/api/campaigns/{cid}/scenes/{sid}", json={"title": "Low Tide"})
        assert r.status_code == 200, r.text
        new_sid = r.json()["id"]
        assert new_sid != sid
    finally:
        llm.release()
    _settle(client, cid, ident)
    keys = [k for _, k in store.tracker.walk.ordered_keys(cid, new_sid)]
    index = _index(cid, ident)
    assert len(keys) == 2 and all(index[k]["status"] == "ok" for k in keys)
    _, state = store.tracker.walk.current(cid, new_sid)
    assert state["characters:mara"]["fields"]["pose"]["value"] == "seated"


def test_campaign_off_schedules_nothing(client):
    llm = _use(client, from_entries(_llm(_tracked({}))))
    cid, sid = _scene(client)
    assert client.put(f"/api/campaigns/{cid}/tracker",
                      json={"setting": "off"}).status_code == 200
    _send(client, cid, sid)
    ident = _identity(cid, sid)
    assert _updates(client, cid, ident) == []
    assert not store.tracker.paths.scene_dir(cid, ident).exists()
    assert _tracker_requests(llm) == []


def test_opener_and_greeting_posts_are_tracked(client):
    _use(client, from_entries(_llm(_tracked({"changes": {"Mara": {"pose": "waiting"}}}))))
    cid, sid = _scene(client)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/first-post",
                    json={"text": "The harbour bell rings twice."})
    assert r.status_code == 200, r.text
    ident = _identity(cid, sid)
    assert _settle(client, cid, ident)
    keys = [k for _, k in store.tracker.walk.ordered_keys(cid, sid)]
    index = _index(cid, ident)
    assert keys and all(index[k]["status"] == "ok" for k in keys)


def test_a_greeting_start_is_tracked(client):
    _use(client, from_entries(_llm(_tracked({"changes": {"Mara": {"pose": "leaning"}}}))))
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "At the Ledger", "character": "mara",
                            "version": "default", "body": "Mara looks up."}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Opening"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                    json={"greeting": gid})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    ident = _identity(cid, sid)
    assert _settle(client, cid, ident)
    keys = [k for _, k in store.tracker.walk.ordered_keys(cid, sid)]
    index = _index(cid, ident)
    assert keys and all(index[k]["status"] == "ok" for k in keys)
    _, state = store.tracker.walk.current(cid, sid)
    assert state["characters:mara"]["fields"]["pose"]["value"] == "leaning"


def test_switching_off_mid_run_settles_what_was_still_queued(client):
    """The update already at the provider lands; the one still waiting is not
    paid for, and says why instead of sitting at `pending` forever."""
    llm = _use(client, HeldCassette(_llm(_tracked({"changes": {"Mara": {"pose": "x"}}})),
                                    hold=TRACKER))
    cid, sid = _scene(client)
    ident = _identity(cid, sid)
    try:
        _send(client, cid, sid)
        llm.await_held()
        assert client.put(f"/api/campaigns/{cid}/tracker",
                          json={"setting": "off"}).status_code == 200
    finally:
        llm.release()
    _settle(client, cid, ident)
    first, second = [k for _, k in store.tracker.walk.ordered_keys(cid, sid)]
    index = _index(cid, ident)
    assert index[first]["status"] == "ok"
    assert index[second]["status"] == "failed" and "switched off" in index[second]["error"]
    assert len(_tracker_requests(llm)) == 1


def test_an_update_for_a_deleted_scene_leaves_nothing_behind(client):
    """`delete_scene` drops the scene's tracker directory; a run that was at the
    provider at the time must not write it back as an orphan."""
    llm = _use(client, HeldCassette(_llm(_tracked({"changes": {"Mara": {"pose": "x"}}})),
                                    hold=TRACKER))
    cid, sid = _scene(client)
    ident = _identity(cid, sid)
    try:
        _send(client, cid, sid)
        llm.await_held()
        assert client.delete(f"/api/campaigns/{cid}/scenes/{sid}").status_code == 200
    finally:
        llm.release()
    _settle(client, cid, ident)
    assert not store.tracker.paths.scene_dir(cid, ident).exists()
