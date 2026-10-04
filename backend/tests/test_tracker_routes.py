"""The scene tracker's record routes, and the transcript routes that keep the
records consistent: an edit flags, a cut prunes, a swipe flags what follows.

Driven through the real routes the way `test_tracker_flow.py` is: every post
schedules a `background` update on the lifespan loop, and only its completion
is waited on.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from grimoire import routes, store
from grimoire.routes import character_turns
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


def test_a_mood_a_layer_made_a_list_reaches_the_tiles_as_text(client):
    """A world may retype `visible_mood` to a list; the cast tiles take one
    string, so a list is rendered as its items joined, never as an array."""
    _use(client, _llm({"changes": {"Mara": {"visible_mood": ["fear", "", "uncertain"]}}}))
    cid, sid = _scene(client)
    wid = store.campaigns.read_campaign(cid)["meta"]["world"]
    store.tracker.fields.write_world_layer(
        wid, {"change": {"visible_mood": {"type": "list"}}})
    _played(client, cid, sid, sends=1)
    moods = client.get(_base(cid, sid)).json()["moods"]
    assert moods == {"characters:mara": "fear, uncertain"}


def test_summary_serves_a_hand_mangled_index_in_the_shape_it_promises(client):
    """The transcript reads `entry.changed.length` and `entry.flags.*` off
    every entry; one written by hand (or by an older version) must not reach
    it with either missing."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    path = store.tracker.paths.scene_dir(cid, _ident(cid, sid)) / "index.json"
    path.write_text(json.dumps({"version": 1, "entries": {
        keys[0]: {}, keys[1]: {"status": "ok", "changed": 3}}}), encoding="utf-8")
    r = client.get(_base(cid, sid))
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    assert set(entries) == set(keys)
    for k in keys:
        assert entries[k]["status"] == "ok"
        assert entries[k]["changed"] == []
        assert entries[k]["flags"] == {"upstream_changed": False, "text_changed": False}


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


@pytest.mark.parametrize("change", ["record", "text"])
def test_an_upstream_change_flags_every_variant_of_a_later_response(client, change):
    """Each variant of a later response keeps a record, and each was built on
    the state before it -- so an upstream change flags the inactive ones too.
    Otherwise swiping to one makes its pre-change state current with no
    warning, since a swipe flags only what comes after the response."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    rid = keys[3][2:34]
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    r = client.post(f"{base}/responses/{rid}/regenerate", json={})
    assert '"error"' not in r.text, r.text
    _settle(client, cid, sid)
    old = keys[3]
    assert _keys(cid, sid)[3] != old and _index(cid, sid)[old]["status"] == "ok"
    if change == "record":
        r = client.put(f"{_base(cid, sid)}/records/{keys[1]}",
                       json={"edits": {"characters:mara": {"clothing": {"value": "red coat"}}}})
    else:
        r = client.put(f"{base}/messages/{_msg_index(cid, sid, keys[1])}",
                       json={"content": "Mara answers, differently."})
    assert r.status_code == 200, r.text
    index = _index(cid, sid)
    assert index[_keys(cid, sid)[3]]["flags"]["upstream_changed"]
    assert index[old]["flags"]["upstream_changed"], "an inactive later variant stayed fresh"
    assert not index[keys[0]]["flags"]["upstream_changed"]
    r = client.post(f"{base}/responses/{rid}/variants/{old[35:]}/activate")
    assert r.status_code == 200, r.text
    summary = client.get(_base(cid, sid)).json()
    assert summary["keys"][-1]["key"] == old
    assert summary["entries"][old]["flags"]["upstream_changed"]


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


# --- a second process on the same store ----------------------------------------
#
# `_scene_lock` orders ONE process's updates for a scene. A second grimoire
# process on the same store (docs/store-guarantees.md) has a lock of its own,
# so two adjacent posts' updates can run side by side there. Handing every
# update a fresh lock is that second process, in-process; the campaign lock
# both still share is the real cross-process one.

def _unordered(monkeypatch) -> None:
    monkeypatch.setattr(tracker_routes, "_scene_lock",
                        lambda app, cid, identity: asyncio.Lock())


def _count_prepares(monkeypatch) -> list[threading.Event]:
    """`[first, second]`: set once that many updates have read their inputs."""
    real, seen = tracker_routes._prepare, []
    reached = [threading.Event(), threading.Event()]

    def prepare(*a, **k):
        out = real(*a, **k)
        seen.append(a)
        for n, event in enumerate(reached, 1):
            if len(seen) >= n:
                event.set()
        return out

    monkeypatch.setattr(tracker_routes, "_prepare", prepare)
    return reached


def test_an_update_read_while_its_predecessor_was_pending_lands_flagged(client, monkeypatch):
    """Post N's update is still answering when post N+1's reads its base: the
    newest `ok` record is then N-1's, and N+1's result lacks whatever N
    changes. Whichever of the two commits first, N+1 must not land fresh."""
    _use(client, _llm())
    cid, sid = _scene(client)
    _played(client, cid, sid, sends=1)
    _unordered(monkeypatch)
    reached = _count_prepares(monkeypatch)
    llm = _held(client)
    try:
        _send(client, cid, sid, "Words number 1.")
        assert reached[1].wait(RUN_TIMEOUT), "both updates should have read their inputs"
    finally:
        llm.release()
    _settle(client, cid, sid)
    post, reply = _keys(cid, sid)[2:]
    index = _index(cid, sid)
    assert index[post]["status"] == "ok"
    assert index[post]["flags"] == {"upstream_changed": False, "text_changed": False}
    assert index[reply]["status"] == "ok"
    assert index[reply]["flags"]["upstream_changed"], \
        "a record built without its pending predecessor landed fresh"


def test_an_update_whose_base_was_rerun_under_it_lands_flagged(client, monkeypatch):
    """The base was `ok` when the update read it, and was then re-run (by the
    other process) before the update committed: the result is built on a
    snapshot that is no longer the base's final state."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    _unordered(monkeypatch)
    reached = _count_prepares(monkeypatch)
    llm = _held(client)
    try:
        assert client.post(f"{_base(cid, sid)}/records/{keys[3]}/retry").status_code == 200
        assert reached[0].wait(RUN_TIMEOUT)
        assert client.post(f"{_base(cid, sid)}/records/{keys[2]}/retry").status_code == 200
        assert reached[1].wait(RUN_TIMEOUT)
    finally:
        llm.release()
    _settle(client, cid, sid)
    index = _index(cid, sid)
    assert index[keys[2]]["flags"] == {"upstream_changed": False, "text_changed": False}
    assert index[keys[3]]["status"] == "ok"
    assert index[keys[3]]["flags"]["upstream_changed"], \
        "a record built on a superseded base landed fresh"


def test_updates_one_after_another_still_land_clean(client):
    """The ordinary path: each update reads its base after the one before it
    landed, so nothing moved under it and nothing is flagged."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    assert client.post(f"{_base(cid, sid)}/records/{keys[1]}/rerun-from").status_code == 200
    _settle(client, cid, sid)
    index = _index(cid, sid)
    for key in keys:
        assert index[key]["status"] == "ok"
        assert index[key]["flags"] == {"upstream_changed": False, "text_changed": False}, key


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


@pytest.mark.parametrize("action", ["retry", "rerun-from"])
def test_a_rerun_keeps_an_awareness_only_edit(client, action):
    """Who knows a value is a person's word as much as the value is. An edit
    that only narrows awareness moves no value, so it is in no change list --
    and a re-run that restored only what the change list named rebuilt the
    record with the base's awareness and lost it. Kept across a second re-run
    too: what a re-run restored is still the person's."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    mara = "characters:mara"
    before = client.get(f"{_base(cid, sid)}/records/{keys[1]}").json()["snapshot"]
    assert before[mara]["fields"]["visible_mood"]["aware"] == "present"
    _edit(client, cid, sid, keys[1], {mara: {"visible_mood": {"aware": []}}})
    assert not any(c[1] == "visible_mood" for c in _index(cid, sid)[keys[1]]["changed"])
    for _ in range(2):
        r = client.post(f"{_base(cid, sid)}/records/{keys[1]}/{action}")
        assert r.status_code == 200, r.text
        _settle(client, cid, sid)
        body = client.get(f"{_base(cid, sid)}/records/{keys[1]}").json()
        assert body["status"] == "ok"
        mood = body["snapshot"][mara]["fields"]["visible_mood"]
        assert mood == {"value": "joy", "aware": [], "set_by": "user"}, \
            "the re-run dropped an awareness-only edit"


def test_a_second_edit_keeps_what_the_first_touched(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    mara = "characters:mara"
    _edit(client, cid, sid, keys[1], {mara: {"visible_mood": {"aware": []}}})
    _edit(client, cid, sid, keys[1], {mara: {"pose": {"value": "kneeling"}}})
    assert sorted(_index(cid, sid)[keys[1]]["touched"]) == [
        [mara, "pose"], [mara, "visible_mood"]]


def test_a_rerun_does_not_revert_a_newer_edit_it_only_inherited(client):
    """A record carries forward what came before it, `set_by` included, so a
    later record's own snapshot holds the earlier record's hand-set value
    without anyone having set it there. Re-running that later record must
    take the newer edit from its base, not restore the stale inherited one."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    _edit(client, cid, sid, keys[1], {"characters:mara": {"clothing": {"value": "red coat"}}})
    _send(client, cid, sid, "Words number 1.")
    _settle(client, cid, sid)
    keys = _keys(cid, sid)
    inherited = client.get(f"{_base(cid, sid)}/records/{keys[3]}").json()["snapshot"]
    clothing = inherited["characters:mara"]["fields"]["clothing"]
    assert clothing["value"] == "red coat" and clothing["set_by"] == "user"
    _edit(client, cid, sid, keys[1], {"characters:mara": {"clothing": {"value": "blue coat"}}})
    r = client.post(f"{_base(cid, sid)}/records/{keys[2]}/rerun-from")
    assert r.status_code == 200, r.text
    _settle(client, cid, sid)
    index = _index(cid, sid)
    for key in keys[2:]:
        body = client.get(f"{_base(cid, sid)}/records/{key}").json()
        assert body["snapshot"]["characters:mara"]["fields"]["clothing"]["value"] == "blue coat", key
        assert not any(c[1] == "clothing" for c in index[key]["changed"]), key


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


# --- a failed turn that takes its player post back ---------------------------
#
# The combined-generation producer (`character_turns.enabled()` False) is the
# one that removes an unanswered player post when the turn fails having
# produced nothing (`scenes._take_the_post_back`). The post's record goes with
# it, whichever of the two finished first.

CHAT = {"system_contains": "Continue the fictional roleplay"}


def _failing_turn(client, monkeypatch, hold: dict) -> HeldCassette:
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    return _use(client, HeldCassette(
        [{"when": dict(TRACKER), "reply": json.dumps({"changes": {"Mara": {"pose": "x"}}})},
         {"when": dict(CHAT), "error": {"kind": "network", "message": "connection reset"}}],
        hold=hold))


def _chat_url(cid, sid) -> str:
    return f"/api/campaigns/{cid}/scenes/{sid}/chat"


def _no_record_left(cid, sid) -> None:
    assert store.scenes.read_scene(cid, sid)["messages"] == []
    assert _index(cid, sid) == {}, "the taken-back post kept its record"
    snaps = list(store.tracker.paths.scene_dir(cid, _ident(cid, sid)).glob("p-*.json"))
    assert snaps == [], "the taken-back post kept its snapshot file"


def test_a_post_taken_back_after_its_update_landed_leaves_no_record(client, monkeypatch):
    llm = _failing_turn(client, monkeypatch, hold=CHAT)
    cid, sid = _scene(client)
    box: dict = {}
    sender = threading.Thread(target=lambda: box.setdefault(
        "r", client.post(_chat_url(cid, sid), json={"content": "Mara, the tide is turning."})))
    sender.start()
    try:
        llm.await_held()
        _settle(client, cid, sid)
        [key] = _keys(cid, sid)
        assert _index(cid, sid)[key]["status"] == "ok"
    finally:
        llm.release()
    sender.join(RUN_TIMEOUT)
    assert '"post_returned": true' in box["r"].text, box["r"].text
    _no_record_left(cid, sid)


def test_a_post_taken_back_while_its_update_runs_leaves_no_record(client, monkeypatch):
    llm = _failing_turn(client, monkeypatch, hold=TRACKER)
    cid, sid = _scene(client)
    try:
        r = client.post(_chat_url(cid, sid), json={"content": "Mara, the tide is turning."})
        assert '"post_returned": true' in r.text, r.text
        llm.await_held()
        assert _index(cid, sid) == {}, "a record outlived the post it was pending for"
    finally:
        llm.release()
    assert [(run.state, run.result) for run in _settle(client, cid, sid)] == [
        ("landed", {"skipped": True})], "the update was not refused"
    _no_record_left(cid, sid)


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


def test_a_hand_mangled_snapshot_is_served_in_the_shape_it_promises(client):
    """The cast tiles read each tail entry's `fields`, the disclosure each
    value's `value` and `aware`; a snapshot file written by hand must not
    turn either read into a 500."""
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid, sends=1)
    path = store.tracker.paths.scene_dir(cid, _ident(cid, sid)) / f"{keys[-1]}.json"
    path.write_text(json.dumps({"version": 1, "snapshot": {
        "characters:mara": "bad",
        "characters:winifred": {"present": True, "fields": {
            "visible_mood": {"value": ["calm", 2], "aware": {"x": 1}},
            "pose": {"value": 5, "aware": "present"}}}}}), encoding="utf-8")
    r = client.get(_base(cid, sid))
    assert r.status_code == 200, r.text
    assert r.json()["moods"] == {"characters:winifred": "calm"}
    r = client.get(f"{_base(cid, sid)}/records/{keys[-1]}")
    assert r.status_code == 200, r.text
    assert r.json()["snapshot"] == {"characters:winifred": {"present": True, "fields": {
        "visible_mood": {"value": ["calm"], "aware": []}}}}
