"""The scene tracker's record routes, and the transcript routes that keep the
records consistent: an edit flags, a cut prunes, a swipe flags what follows.

Driven through the real routes the way `test_tracker_flow.py` is: every post
schedules a `background` update on the lifespan loop, and only its completion
is waited on.
"""

from __future__ import annotations

import json

import pytest

from grimoire import routes, store
from grimoire.routes import tracker as tracker_routes

from .llm_fakes import FakeLLM, HeldCassette, from_entries

pytestmark = pytest.mark.tracker

TRACKER = {"system_contains": "You maintain the scene state tracker"}
MARA_SAYS = 'Mara answers.\n```handoff\n{"next":null}\n```'
RUN_TIMEOUT = 10.0


def _llm(tracker_reply: dict | str | None = None) -> FakeLLM:
    if tracker_reply is None:
        tracker_reply = {"changes": {"Mara": {"visible_mood": "joy"}}}
    body = tracker_reply if isinstance(tracker_reply, str) else json.dumps(tracker_reply)
    return from_entries([{"when": dict(TRACKER), "reply": body},
                         {"when": {}, "reply": MARA_SAYS}])


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
    assert '"error"' not in r.text, r.text


def _ident(cid, sid) -> str:
    return store.scenes.scene_identity(cid, sid)


def _updates(client, cid, ident) -> list:
    return [r for r in client.app.state.runs.for_subject(("scene", cid, ident))
            if r.cls == "background" and r.kind == "tracker-update"]


def _settle(client, cid, sid) -> list:
    found = _updates(client, cid, _ident(cid, sid))
    for run in found:
        assert run.terminal.wait(timeout=RUN_TIMEOUT), "a tracker update never finished"
    return found


def _keys(cid, sid) -> list[str]:
    return [k for _, k in store.tracker.walk.ordered_keys(cid, sid)]


def _index(cid, sid) -> dict:
    return store.tracker.records.read_index(cid, _ident(cid, sid))


def _played(client, cid, sid, sends=2) -> list[str]:
    """`sends` exchanges, each a player post and Mara's reply, all tracked."""
    for n in range(sends):
        _send(client, cid, sid, f"Words number {n}.")
        _settle(client, cid, sid)
    keys = _keys(cid, sid)
    assert len(keys) == 2 * sends
    assert all(_index(cid, sid)[k]["status"] == "ok" for k in keys)
    return keys


def _base(cid, sid) -> str:
    return f"/api/campaigns/{cid}/scenes/{sid}/tracker"


def _msg_index(cid, sid, key) -> int:
    return {k: i for i, k in store.tracker.walk.ordered_keys(cid, sid)}[key]


def _tracker_posts(llm: FakeLLM) -> list[str]:
    """The `# New post` section of every tracker request, in the order made."""
    out = []
    for req in llm.requests:
        msgs = req["messages"]
        if any("You maintain the scene state tracker" in m.get("content", "")
               for m in msgs if m.get("role") == "system"):
            user = next(m["content"] for m in msgs if m.get("role") == "user")
            out.append(user.split("# New post", 1)[1])
    return out


# --- reading -----------------------------------------------------------------

def test_summary_lists_keys_in_order_with_entries(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    r = client.get(_base(cid, sid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert [k["key"] for k in body["keys"]] == keys
    assert [k["index"] for k in body["keys"]] == sorted(k["index"] for k in body["keys"])
    assert set(body["entries"]) == set(keys)
    assert all(body["entries"][k]["status"] == "ok" for k in keys)
    assert set(body["names"].values()) >= {"Mara", "Winifred"}
    assert body["moods"]["characters:mara"] == "joy"
    assert body["labels"]["visible_mood"] == "Visible mood"


def test_record_carries_snapshot_fields_and_names(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    r = client.get(f"{_base(cid, sid)}/records/{keys[-1]}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == keys[-1] and body["status"] == "ok"
    assert body["flags"] == {"upstream_changed": False, "text_changed": False}
    assert body["snapshot"]["characters:mara"]["fields"]["visible_mood"]["value"] == "joy"
    assert any(f["key"] == "clothing" for f in body["fields"])
    assert "Mara" in body["names"].values()
    assert "error" not in body


def test_unknown_key_404(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    _played(client, cid, sid, sends=1)
    stranger = store.tracker.paths.post_key("0" * 32)
    for key in (stranger, "not-a-key", "p-" + "A" * 32):
        assert client.get(f"{_base(cid, sid)}/records/{key}").status_code == 404
        assert client.put(f"{_base(cid, sid)}/records/{key}",
                          json={"edits": {}}).status_code == 404
        assert client.post(f"{_base(cid, sid)}/records/{key}/retry").status_code == 404
        assert client.post(f"{_base(cid, sid)}/records/{key}/rerun-from").status_code == 404


# --- the user edit -----------------------------------------------------------

def test_edit_marks_user_and_flags_later(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    runs_before = len(_settle(client, cid, sid))
    r = client.put(f"{_base(cid, sid)}/records/{keys[1]}",
                   json={"edits": {"characters:mara": {"clothing": {"value": "red coat"}}}})
    assert r.status_code == 200, r.text
    clothing = r.json()["snapshot"]["characters:mara"]["fields"]["clothing"]
    assert clothing["value"] == "red coat" and clothing["set_by"] == "user"
    index = _index(cid, sid)
    assert index[keys[1]]["status"] == "ok"
    assert ["characters:mara", "clothing", "red coat"] in index[keys[1]]["changed"]
    assert not index[keys[0]]["flags"]["upstream_changed"]
    assert not index[keys[1]]["flags"]["upstream_changed"]
    assert index[keys[2]]["flags"]["upstream_changed"]
    assert index[keys[3]]["flags"]["upstream_changed"]
    body = store.tracker.records.read_snapshot(cid, _ident(cid, sid), keys[1])
    assert body["model"] == "user"
    assert len(_updates(client, cid, _ident(cid, sid))) == runs_before, "an edit re-ran something"


def test_edit_rejects_bad_enum(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    before = store.tracker.records.read_snapshot(cid, _ident(cid, sid), keys[0])
    r = client.put(f"{_base(cid, sid)}/records/{keys[0]}",
                   json={"edits": {"characters:mara": {"visible_mood": {"value": "furious"}}}})
    assert r.status_code == 400, r.text
    assert store.tracker.records.read_snapshot(cid, _ident(cid, sid), keys[0]) == before


def test_edits_refused_when_tracker_off(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    assert client.put(f"/api/campaigns/{cid}/tracker",
                      json={"setting": "off"}).status_code == 200
    edit = {"edits": {"characters:mara": {"clothing": {"value": "red coat"}}}}
    r = client.put(f"{_base(cid, sid)}/records/{keys[0]}", json=edit)
    assert r.status_code == 409 and r.json() == {"detail": "tracker_off"}
    for action in ("retry", "rerun-from"):
        r = client.post(f"{_base(cid, sid)}/records/{keys[0]}/{action}")
        assert r.status_code == 409 and r.json() == {"detail": "tracker_off"}
    assert client.get(f"{_base(cid, sid)}/records/{keys[0]}").status_code == 200
    summary = client.get(_base(cid, sid))
    assert summary.status_code == 200 and summary.json()["enabled"] is False


# --- transcript hooks --------------------------------------------------------

def test_editing_post_text_flags_it_and_later(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    at = _msg_index(cid, sid, keys[1])
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/{at}",
                   json={"content": "Mara answers, differently."})
    assert r.status_code == 200, r.text
    index = _index(cid, sid)
    assert index[keys[0]]["flags"] == {"upstream_changed": False, "text_changed": False}
    assert index[keys[1]]["flags"]["text_changed"]
    assert not index[keys[1]]["flags"]["upstream_changed"]
    assert index[keys[2]]["flags"]["upstream_changed"]
    assert index[keys[3]]["flags"]["upstream_changed"]


def test_a_retcon_flags_it_and_later(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    at = _msg_index(cid, sid, keys[0])
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/messages/{at}/retcon",
                    json={"content": "Words, rewritten."})
    assert r.status_code == 200, r.text
    index = _index(cid, sid)
    assert index[keys[0]]["flags"]["text_changed"]
    assert all(index[k]["flags"]["upstream_changed"] for k in keys[1:])


def test_cut_prunes_records(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    ident = _ident(cid, sid)
    at = _msg_index(cid, sid, keys[2])
    r = client.delete(f"/api/campaigns/{cid}/scenes/{sid}/messages/{at}")
    assert r.status_code == 200, r.text
    index = _index(cid, sid)
    assert set(index) == set(keys[:2])
    for key in keys[2:]:
        assert store.tracker.records.read_snapshot(cid, ident, key) is None


def test_replay_begin_prunes_records(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    at = _msg_index(cid, sid, keys[2])
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": at})
    assert r.status_code == 200, r.text
    assert set(_index(cid, sid)) == set(keys[:2])


def test_deleting_a_response_prunes_it_and_flags_later(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    rid = keys[1][2:34]
    r = client.delete(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}")
    assert r.status_code == 200, r.text
    index = _index(cid, sid)
    assert keys[1] not in index
    assert not index[keys[0]]["flags"]["upstream_changed"]
    assert index[keys[2]]["flags"]["upstream_changed"]
    assert index[keys[3]]["flags"]["upstream_changed"]


def test_swiping_an_earlier_response_flags_later(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    _send(client, cid, sid, "Words number 0.")
    _settle(client, cid, sid)
    rid = _keys(cid, sid)[1][2:34]
    first_vid = store.responses.variants_by_response(cid, sid)[rid][0]
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    r = client.post(f"{base}/responses/{rid}/regenerate", json={})
    assert '"error"' not in r.text, r.text
    _settle(client, cid, sid)
    _send(client, cid, sid, "Words number 1.")
    _settle(client, cid, sid)
    keys = _keys(cid, sid)
    assert all(not _index(cid, sid)[k]["flags"]["upstream_changed"] for k in keys)
    before = len(_updates(client, cid, _ident(cid, sid)))
    r = client.post(f"{base}/responses/{rid}/variants/{first_vid}/activate")
    assert r.status_code == 200, r.text
    after = _keys(cid, sid)
    assert after[1] == store.tracker.paths.response_key(rid, first_vid)
    index = _index(cid, sid)
    assert not index[after[0]]["flags"]["upstream_changed"]
    assert not index[after[1]]["flags"]["upstream_changed"]
    assert index[after[2]]["flags"]["upstream_changed"]
    assert index[after[3]]["flags"]["upstream_changed"]
    assert len(_updates(client, cid, _ident(cid, sid))) == before, "a swipe re-ran something"


def test_a_failing_tracker_hook_does_not_fail_the_edit(client, monkeypatch):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)

    def boom(*a, **k):
        raise OSError("disk on fire")

    monkeypatch.setattr(store.tracker.walk, "flag_edited", boom)
    at = _msg_index(cid, sid, keys[0])
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/{at}",
                   json={"content": "Changed anyway."})
    assert r.status_code == 200, r.text
    assert store.scenes.read_scene(cid, sid)["messages"][at]["content"] == "Changed anyway."


# --- re-running --------------------------------------------------------------

def test_rerun_from_reschedules_in_order_and_clears_flags(client):
    llm = _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    at = _msg_index(cid, sid, keys[0])
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/{at}",
                      json={"content": "Words number 0, edited."}).status_code == 200
    assert any(any(e["flags"].values()) for e in _index(cid, sid).values())
    asked = len(_tracker_posts(llm))
    r = client.post(f"{_base(cid, sid)}/records/{keys[1]}/rerun-from")
    assert r.status_code == 200, r.text
    _settle(client, cid, sid)
    posts = _tracker_posts(llm)[asked:]
    assert len(posts) == 3
    assert "Mara answers" in posts[0]
    assert "Words number 1." in posts[1]
    assert "Mara answers" in posts[2]
    index = _index(cid, sid)
    for key in keys[1:]:
        assert index[key]["status"] == "ok"
        assert index[key]["flags"] == {"upstream_changed": False, "text_changed": False}
    assert index[keys[0]]["flags"]["text_changed"], "a key before the re-run was touched"


def test_retry_turns_failed_into_ok(client):
    _use(client, _llm("not json"))
    cid, sid = _scene(client)
    for n in range(2):
        _send(client, cid, sid, f"Words number {n}.")
        _settle(client, cid, sid)
    keys = _keys(cid, sid)
    index = _index(cid, sid)
    assert all(index[k]["status"] == "failed" for k in keys)
    _use(client, _llm())
    r = client.post(f"{_base(cid, sid)}/records/{keys[1]}/retry")
    assert r.status_code == 200, r.text
    _settle(client, cid, sid)
    index = _index(cid, sid)
    assert index[keys[1]]["status"] == "ok"
    assert "error" not in index[keys[1]]
    assert not index[keys[0]]["flags"]["upstream_changed"]
    assert index[keys[2]]["flags"]["upstream_changed"]
    assert index[keys[3]]["flags"]["upstream_changed"]


def test_retry_of_an_ok_record_flags_nothing(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    r = client.post(f"{_base(cid, sid)}/records/{keys[1]}/retry")
    assert r.status_code == 200, r.text
    _settle(client, cid, sid)
    index = _index(cid, sid)
    assert all(not index[k]["flags"]["upstream_changed"] for k in keys)


def test_a_pending_record_with_no_run_reads_as_interrupted(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    ident = _ident(cid, sid)
    # What a crash between the mark and the run leaves behind.
    store.tracker.records.mark_pending(cid, ident, keys[1])
    summary = client.get(_base(cid, sid)).json()
    assert summary["entries"][keys[1]]["status"] == "failed"
    assert summary["entries"][keys[1]]["error"] == "interrupted"
    record = client.get(f"{_base(cid, sid)}/records/{keys[1]}").json()
    assert record["status"] == "failed" and record["error"] == "interrupted"
    assert _index(cid, sid)[keys[1]]["status"] == "pending", "a GET rewrote the index"
    r = client.post(f"{_base(cid, sid)}/records/{keys[1]}/retry")
    assert r.status_code == 200, r.text
    _settle(client, cid, sid)
    index = _index(cid, sid)
    assert index[keys[1]]["status"] == "ok"
    assert index[keys[2]]["flags"]["upstream_changed"]


def test_a_pending_record_with_a_live_run_reads_as_pending(client):
    llm = _use(client, HeldCassette(
        [{"when": dict(TRACKER), "reply": "{}"}, {"when": {}, "reply": MARA_SAYS}],
        hold=TRACKER))
    cid, sid = _scene(client)
    try:
        _send(client, cid, sid)
        llm.await_held()
        summary = client.get(_base(cid, sid)).json()
        statuses = {e["status"] for e in summary["entries"].values()}
        assert statuses == {"pending"}
    finally:
        llm.release()
    _settle(client, cid, sid)


# --- writes that land while an update is running ------------------------------

def _held(client) -> HeldCassette:
    return _use(client, HeldCassette(
        [{"when": dict(TRACKER), "reply": json.dumps({"changes": {"Mara": {"pose": "x"}}})},
         {"when": {}, "reply": MARA_SAYS}], hold=TRACKER))


def test_a_text_edit_during_its_own_update_stays_flagged(client):
    """The update read the old text; its save must not clear the flag the
    edit raised after it read."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    llm = _held(client)
    try:
        assert client.post(f"{_base(cid, sid)}/records/{keys[1]}/retry").status_code == 200
        llm.await_held()
        at = _msg_index(cid, sid, keys[1])
        r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/{at}",
                       json={"content": "Mara answers, differently."})
        assert r.status_code == 200, r.text
    finally:
        llm.release()
    _settle(client, cid, sid)
    entry = _index(cid, sid)[keys[1]]
    assert entry["status"] == "ok"
    assert entry["flags"]["text_changed"], "the update's save cleared a later edit's flag"


def test_an_earlier_edit_during_an_update_stays_flagged(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    llm = _held(client)
    try:
        assert client.post(f"{_base(cid, sid)}/records/{keys[2]}/retry").status_code == 200
        llm.await_held()
        r = client.put(f"{_base(cid, sid)}/records/{keys[1]}",
                       json={"edits": {"characters:mara": {"clothing": {"value": "red coat"}}}})
        assert r.status_code == 200, r.text
    finally:
        llm.release()
    _settle(client, cid, sid)
    entry = _index(cid, sid)[keys[2]]
    assert entry["status"] == "ok"
    assert entry["flags"]["upstream_changed"], "the update's save cleared an earlier edit's flag"


def test_a_rerun_still_clears_flags_raised_before_it_read(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    at = _msg_index(cid, sid, keys[1])
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/{at}",
                      json={"content": "Mara answers, differently."}).status_code == 200
    assert _index(cid, sid)[keys[1]]["flags"]["text_changed"]
    assert client.post(f"{_base(cid, sid)}/records/{keys[1]}/retry").status_code == 200
    _settle(client, cid, sid)
    assert _index(cid, sid)[keys[1]]["flags"] == {"upstream_changed": False,
                                                 "text_changed": False}


def test_editing_a_record_whose_update_is_running_is_refused(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    llm = _held(client)
    try:
        assert client.post(f"{_base(cid, sid)}/records/{keys[1]}/retry").status_code == 200
        llm.await_held()
        r = client.put(f"{_base(cid, sid)}/records/{keys[1]}",
                       json={"edits": {"characters:mara": {"clothing": {"value": "red coat"}}}})
        assert r.status_code == 409 and r.json() == {"detail": "tracker_busy"}
    finally:
        llm.release()
    _settle(client, cid, sid)


def test_rerolling_an_earlier_response_flags_later(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    rid = keys[1][2:34]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate", json={})
    assert r.status_code == 200 and '"error"' not in r.text, r.text
    _settle(client, cid, sid)
    after = _keys(cid, sid)
    assert after[1] != keys[1] and after[2:] == keys[2:]
    index = _index(cid, sid)
    assert not index[after[0]]["flags"]["upstream_changed"]
    assert index[keys[2]]["flags"]["upstream_changed"]
    assert index[keys[3]]["flags"]["upstream_changed"]


def test_a_failed_retry_keeps_the_flags_and_a_hand_edit_after_it_too(client):
    """A run that never produced a result answered nothing: the flags it found
    must still be raised after it fails, and after a hand edit on top."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    at = _msg_index(cid, sid, keys[1])
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/{at}",
                      json={"content": "Mara answers, differently."}).status_code == 200
    _use(client, _llm("not json"))
    assert client.post(f"{_base(cid, sid)}/records/{keys[1]}/retry").status_code == 200
    _settle(client, cid, sid)
    entry = _index(cid, sid)[keys[1]]
    assert entry["status"] == "failed"
    assert entry["flags"]["text_changed"], "a failed run cleared the flags"
    r = client.put(f"{_base(cid, sid)}/records/{keys[1]}",
                   json={"edits": {"characters:mara": {"clothing": {"value": "red coat"}}}})
    assert r.status_code == 200, r.text
    assert r.json()["flags"]["text_changed"]
    assert _index(cid, sid)[keys[1]]["flags"]["text_changed"]


def test_an_interrupted_run_keeps_the_flags(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    at = _msg_index(cid, sid, keys[1])
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/{at}",
                      json={"content": "Mara answers, differently."}).status_code == 200
    ident = _ident(cid, sid)
    # A run that read its inputs and then died: prepared, never committed.
    assert tracker_routes._prepare(cid, ident, sid, keys[1]) is not None
    store.tracker.records.mark_pending(cid, ident, keys[1])
    record = client.get(f"{_base(cid, sid)}/records/{keys[1]}").json()
    assert record["status"] == "failed" and record["error"] == "interrupted"
    assert record["flags"]["text_changed"]
