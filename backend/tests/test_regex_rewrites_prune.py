"""A stored rewrite's record leaves with its message (regex spec 5.1).

The record holds the original prose of a post, so a post the transcript no
longer has -- cut, deleted, taken back, rerolled or swiped away -- must not
keep it on disk or hand it out from `GET .../rewrites`. Each seam that removes
messages prunes the scene's records down to the messages still there; posts
a running replay is holding to put back are still there for this purpose.
"""

from __future__ import annotations

import json

from grimoire import routes, store
from grimoire.routes import character_turns
from grimoire.store.regex import rewrites
from tests.llm_fakes import FailingOpenRouter, FakeLLM
from tests.test_regex_rewrites import (
    ELLIPSIS,
    _declined_roll_with_two_rewritten_parts,
    messages,
    put_rules,
    records,
    seed,
    send,
)

BOTH = {**ELLIPSIS, "targets": ["model", "user"]}


def _file(cid, sid):
    return rewrites.path(cid, store.scenes.scene_identity(cid, sid))


def test_a_cut_drops_the_records_of_the_posts_it_removes(client):
    cid, sid = seed(client)
    put_rules(client, cid, BOTH)
    send(client, cid, sid, "One... reply.", content="First...", speaker_ref="characters:mara")
    send(client, cid, sid, "Two... reply.", content="Second...", speaker_ref="characters:mara")
    kept = {m["rewrite_key"] for m in messages(client, cid, sid)[:2]}
    assert len(records(client, cid, sid)) == 4

    r = client.delete(f"/api/campaigns/{cid}/scenes/{sid}/messages/2")
    assert r.status_code == 200, r.text
    assert set(records(client, cid, sid)) == kept
    assert set(json.loads(_file(cid, sid).read_text(encoding="utf-8"))) == kept

    r = client.delete(f"/api/campaigns/{cid}/scenes/{sid}/messages/0")
    assert r.status_code == 200, r.text
    assert records(client, cid, sid) == {}
    assert not _file(cid, sid).exists()


def test_a_legacy_cut_drops_its_segments_records(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.")
    assert len(records(client, cid, sid)) == 1
    assert client.delete(f"/api/campaigns/{cid}/scenes/{sid}/messages/1").status_code == 200
    assert records(client, cid, sid) == {}


def test_deleting_a_response_drops_its_record(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]
    r = client.delete(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}")
    assert r.status_code == 200, r.text
    assert records(client, cid, sid) == {}


def test_a_post_taken_back_after_a_failed_turn_drops_its_record(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    cid, sid = seed(client)
    put_rules(client, cid, {**ELLIPSIS, "targets": ["user"]})
    client.app.dependency_overrides[routes.get_llm] = FailingOpenRouter
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                    json={"content": "Wait..."})
    assert '"post_returned": true' in r.text, r.text
    assert messages(client, cid, sid) == []
    assert records(client, cid, sid) == {}


def test_a_legacy_reroll_and_a_swipe_drop_the_records_of_what_they_replace(client, monkeypatch):
    """A legacy reply's records are keyed by the `post_id` each segment was
    minted, which the reroll sidecar does not keep: once the run is replaced,
    nothing can bring that id back."""
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.")
    first = messages(client, cid, sid)[-1]["post_id"]

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Again... no."]])
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")
    assert '"error"' not in r.text, r.text
    second = messages(client, cid, sid)[-1]["post_id"]
    assert set(records(client, cid, sid)) == {second} and second != first

    vid = client.get(f"/api/campaigns/{cid}/scenes/{sid}/alternates").json()["alternates"][0]["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/alternates/{vid}").status_code == 200
    assert messages(client, cid, sid)[-1]["content"] == "She paused… then spoke."
    assert records(client, cid, sid) == {}


def test_a_reroll_that_folds_a_continued_response_drops_the_later_parts_record(client):
    """A reroll puts the response back as one message of the new variant, so
    the continuation's own message -- and its record -- is gone; the first
    part's key is still the response's."""
    cid, sid = _declined_roll_with_two_rewritten_parts(client)
    rid = messages(client, cid, sid)[1]["response_id"]
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Plain words."]])
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate", json={})
    assert '"error"' not in r.text, r.text
    assert [m["content"] for m in messages(client, cid, sid)[1:]] == ["Plain words."]
    assert set(records(client, cid, sid)) == {rid}


def test_a_replay_keeps_the_records_of_the_posts_it_holds(client):
    """The posts a replay cut are held to be put back; until the walk lets go
    of them they are still the scene's, and so are their records."""
    cid, sid = seed(client)
    put_rules(client, cid, BOTH)
    send(client, cid, sid, "She paused... then spoke.", content="Wait...",
         speaker_ref="characters:mara")
    before = records(client, cid, sid)
    assert len(before) == 2

    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 0})
    assert r.status_code == 200, r.text
    assert records(client, cid, sid) == before
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay/cancel", json={"restore": True})
    assert r.status_code == 200, r.text
    assert records(client, cid, sid) == before
    assert all(m.get("rewritten") for m in messages(client, cid, sid))

    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 0})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay/cancel", json={"restore": False})
    assert r.status_code == 200, r.text
    assert records(client, cid, sid) == {}


def test_prune_keeps_records_whose_message_is_there(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]
    path = _file(cid, sid)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["0" * 32] = {**data[rid]}
    path.write_text(json.dumps(data), encoding="utf-8")

    rewrites.prune(cid, sid)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {rid}
    held = {"role": "assistant", "content": "x", "post_id": "0" * 32}
    data["0" * 32] = {**data[rid]}
    path.write_text(json.dumps(data), encoding="utf-8")
    rewrites.prune(cid, sid, held=[held])
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {rid, "0" * 32}
    # A scene with no record file gets none from a prune.
    path.unlink()
    rewrites.prune(cid, sid)
    assert not path.exists()
