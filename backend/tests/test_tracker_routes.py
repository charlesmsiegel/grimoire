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


# --- a stale base makes a stale result ----------------------------------------

def _edit(client, cid, sid, key, edits) -> None:
    r = client.put(f"{_base(cid, sid)}/records/{key}", json={"edits": edits})
    assert r.status_code == 200, r.text


def test_records_built_on_a_hand_edited_base_stay_flagged(client):
    """A record's update starts from the newest `ok` record before it. When
    that base carries a staleness flag, the new record is built on a state the
    transcript no longer stands behind, however fresh its own reading of its
    post -- so it lands flagged rather than clean."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    _edit(client, cid, sid, keys[0],
          {"characters:mara": {"clothing": {"value": "red coat"}}})
    # (a) a reroll of the last response
    rid = keys[3][2:34]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate", json={})
    assert r.status_code == 200 and '"error"' not in r.text, r.text
    _settle(client, cid, sid)
    after = _keys(cid, sid)
    assert after[3] != keys[3]
    entry = _index(cid, sid)[after[3]]
    assert entry["status"] == "ok"
    assert entry["flags"]["upstream_changed"], "a reroll on a stale base landed clean"
    # (b) a new post, and the reply to it
    _send(client, cid, sid, "Words number 2.")
    _settle(client, cid, sid)
    later = _keys(cid, sid)[4:]
    assert len(later) == 2
    index = _index(cid, sid)
    for key in later:
        assert index[key]["status"] == "ok"
        assert index[key]["flags"]["upstream_changed"], "a new post on a stale base landed clean"
    # Re-running from post 2 brings every record after the edit back in line.
    r = client.post(f"{_base(cid, sid)}/records/{keys[1]}/rerun-from")
    assert r.status_code == 200, r.text
    _settle(client, cid, sid)
    index = _index(cid, sid)
    for key in _keys(cid, sid)[1:]:
        assert index[key]["status"] == "ok"
        assert index[key]["flags"] == {"upstream_changed": False, "text_changed": False}


def test_a_post_after_an_edited_post_text_lands_flagged(client):
    """The other staleness flag counts too: the last post's text was edited,
    so the record the next post starts from no longer matches its post."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    at = _msg_index(cid, sid, keys[1])
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/{at}",
                      json={"content": "Mara answers, differently."}).status_code == 200
    _send(client, cid, sid, "Words number 1.")
    _settle(client, cid, sid)
    index = _index(cid, sid)
    for key in _keys(cid, sid)[2:]:
        assert index[key]["flags"]["upstream_changed"]


# --- a person's values survive a re-run ---------------------------------------

@pytest.mark.parametrize("action", ["retry", "rerun-from"])
def test_a_rerun_keeps_what_a_person_set_unless_the_reply_moves_it(client, action):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    _edit(client, cid, sid, keys[1], {"characters:mara": {
        "clothing": {"value": "red coat"}, "pose": {"value": "kneeling"}}})
    _use(client, _llm({"changes": {"Mara": {"pose": "standing"}}}))
    r = client.post(f"{_base(cid, sid)}/records/{keys[1]}/{action}")
    assert r.status_code == 200, r.text
    _settle(client, cid, sid)
    body = client.get(f"{_base(cid, sid)}/records/{keys[1]}").json()
    assert body["status"] == "ok"
    mara = body["snapshot"]["characters:mara"]["fields"]
    assert mara["clothing"]["value"] == "red coat", "the re-run dropped a hand-set value"
    assert mara["clothing"]["set_by"] == "user"
    assert mara["pose"]["value"] == "standing", "the reply's own change was overridden"
    assert "set_by" not in mara["pose"]
    changed = _index(cid, sid)[keys[1]]["changed"]
    assert ["characters:mara", "clothing", "red coat"] in changed
    assert ["characters:mara", "pose", "standing"] in changed


# --- a run superseded while it waited -----------------------------------------

def test_an_obsolete_run_neither_prepares_nor_lands(client):
    """A run carries the generation its mark returned. A newer mark for the
    same key (a second Retry) or a hand edit moves it on, and the older run is
    then skipped at prepare and discarded at commit -- its failure is not
    written either -- so nothing it computed lands over what superseded it."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    ident, key = _ident(cid, sid), keys[1]
    first = tracker_routes.mark(cid, sid, key)
    assert first is not None
    assert tracker_routes._prepare(cid, ident, sid, key, first[1]) is not None
    second = tracker_routes.mark(cid, sid, key)
    assert second is not None and second[1] > first[1]
    before = store.tracker.records.read_snapshot(cid, ident, key)["snapshot"]
    stale = {"characters:mara": {"present": True, "fields": {
        "pose": {"value": "from the first run", "aware": "present"}}}}
    assert not tracker_routes._commit(cid, ident, sid, key, stale, [], "d", "m", gen=first[1])
    assert _index(cid, sid)[key]["status"] == "pending"
    assert store.tracker.records.read_snapshot(cid, ident, key)["snapshot"] == before
    # Nothing is live, so the edit is allowed; it moves the generation again.
    _edit(client, cid, sid, key, {"characters:mara": {"clothing": {"value": "red coat"}}})
    assert tracker_routes._prepare(cid, ident, sid, key, second[1]) is None
    assert not tracker_routes._commit(cid, ident, sid, key, stale, [], "d", "m", gen=second[1])
    tracker_routes._fail(cid, ident, sid, key, "the model said no", second[1])
    entry = _index(cid, sid)[key]
    assert entry["status"] == "ok" and "error" not in entry
    clothing = store.tracker.records.read_snapshot(cid, ident, key)["snapshot"][
        "characters:mara"]["fields"]["clothing"]
    assert clothing == {"value": "red coat", "aware": "present", "set_by": "user"}


def test_a_duplicate_retry_cannot_land_over_an_edit(client, monkeypatch):
    """Two Retries queue two runs. The first is obsolete the moment the second
    is marked, so it cannot settle the record `ok` -- which is what used to
    open the edit guard while the second was still to run, and let it
    overwrite the edit. The record stays `pending` (an edit refused) until the
    second lands; an edit after that is kept."""
    import threading

    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    ident = _ident(cid, sid)
    llm = _held(client)
    # The second run is held at its prepare, so the window between the two
    # runs' commits is the test's to look into.
    real_prepare, go_second, prepared = tracker_routes._prepare, threading.Event(), []

    def prepare(*a, **k):
        prepared.append(a)
        if len(prepared) == 2:
            assert go_second.wait(RUN_TIMEOUT)
        return real_prepare(*a, **k)

    monkeypatch.setattr(tracker_routes, "_prepare", prepare)
    edit = {"edits": {"characters:mara": {"clothing": {"value": "red coat"}}}}
    try:
        assert client.post(f"{_base(cid, sid)}/records/{keys[1]}/retry").status_code == 200
        llm.await_held()
        assert client.post(f"{_base(cid, sid)}/records/{keys[1]}/retry").status_code == 200
    finally:
        llm.release()
    try:
        first = _updates(client, cid, ident)[-2]
        assert first.terminal.wait(timeout=RUN_TIMEOUT)
        assert _index(cid, sid)[keys[1]]["status"] == "pending", \
            "the superseded run settled the record"
        r = client.put(f"{_base(cid, sid)}/records/{keys[1]}", json=edit)
        assert r.status_code == 409 and r.json() == {"detail": "tracker_busy"}
    finally:
        go_second.set()
    _settle(client, cid, sid)
    assert _index(cid, sid)[keys[1]]["status"] == "ok"
    r = client.put(f"{_base(cid, sid)}/records/{keys[1]}", json=edit)
    assert r.status_code == 200, r.text
    _settle(client, cid, sid)
    mara = client.get(f"{_base(cid, sid)}/records/{keys[1]}").json()["snapshot"][
        "characters:mara"]["fields"]
    assert mara["clothing"]["value"] == "red coat"
    assert mara["pose"]["value"] == "x"


def test_a_roll_pause_and_its_resumption_are_both_tracked(client):
    """`_pause` writes the prose before a roll fence as a variant of its own,
    and the resumed part writes the whole response as another: both are marked
    inside their holds and both get a record, and the walk reads the finished
    one -- built from the response's full text."""
    llm = _use(client, from_entries([
        {"when": dict(TRACKER), "reply": json.dumps({"changes": {"Mara": {"pose": "x"}}})},
        {"when": {"contains": "The proposed check was declined"},
         "reply": 'No roll.\n```handoff\n{"next":null}\n```'},
        {"when": {}, "reply": 'Wait.\n```roll\n{"check":"notice"}\n```'},
    ]))
    cid, sid = _scene(client)
    _send(client, cid, sid)
    _settle(client, cid, sid)
    paused = _keys(cid, sid)
    assert len(paused) == 2
    assert _index(cid, sid)[paused[1]]["status"] == "ok"
    proposal = store.proposals.get(cid, sid)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal",
                    json={"proposal": proposal["id"], "action": "decline"})
    assert r.status_code == 200 and '"error"' not in r.text, r.text
    _settle(client, cid, sid)
    keys = _keys(cid, sid)
    assert len(keys) == 2 and keys[0] == paused[0]
    assert keys[1] != paused[1] and keys[1][2:34] == paused[1][2:34]
    index = _index(cid, sid)
    assert index[keys[1]]["status"] == "ok"
    assert index[paused[1]]["status"] == "ok", "the pre-roll variant's record was lost"
    posts = _tracker_posts(llm)
    assert any("Wait." in p and "No roll." in p for p in posts), posts


# --- characters who left ------------------------------------------------------

def test_a_departed_character_keeps_a_name_in_the_scenes_records(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    r = client.delete(f"/api/campaigns/{cid}/scenes/{sid}/cast/characters/winifred")
    assert r.status_code == 200, r.text
    assert client.get(_base(cid, sid)).json()["names"]["characters:winifred"] == "Winifred"
    body = client.get(f"{_base(cid, sid)}/records/{keys[0]}").json()
    assert body["names"]["characters:winifred"] == "Winifred"


def test_an_update_for_a_post_before_a_leave_keeps_the_leavers_changes(client):
    """Winifred was in the room for the post; she walks out while its update
    is still waiting on the model. The update names her in its prompt and
    applies what the reply says about her -- she was present at the post."""
    llm = _use(client, HeldCassette(
        [{"when": dict(TRACKER),
          "reply": json.dumps({"changes": {"Winifred": {"pose": "leaning on the rail"}}})},
         {"when": {}, "reply": MARA_SAYS}], hold=TRACKER))
    cid, sid = _scene(client)
    try:
        _send(client, cid, sid)
        llm.await_held()
        r = client.delete(f"/api/campaigns/{cid}/scenes/{sid}/cast/characters/winifred")
        assert r.status_code == 200, r.text
    finally:
        llm.release()
    _settle(client, cid, sid)
    key = _keys(cid, sid)[0]
    assert _index(cid, sid)[key]["status"] == "ok"
    snap = client.get(f"{_base(cid, sid)}/records/{key}").json()["snapshot"]
    assert snap["characters:winifred"]["fields"]["pose"]["value"] == "leaning on the rail"
    users = [next(m["content"] for m in req["messages"] if m.get("role") == "user")
             for req in llm.requests
             if any(TRACKER["system_contains"] in m.get("content", "")
                    for m in req["messages"] if m.get("role") == "system")]
    assert users and all("characters:winifred" not in u for u in users), \
        "the update prompt named a departed character by her raw ref"
