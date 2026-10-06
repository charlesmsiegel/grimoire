"""Stored rewrites meet the play controls: a branch carries its posts' records,
and Keep writing keeps Restore original for the whole reply it extends; its
live seed and the partial the model continues are in the display and prompt
views like every other reader of transcript text."""

from __future__ import annotations

from grimoire import routes, store
from tests.llm_fakes import FakeLLM
from tests.test_regex_rewrites import ELLIPSIS, messages, put_rules, records, seed, send
from tests.test_runs_routes import _events

_HANDOFF = '\n```handoff\n{"next":null}\n```'


def _extend(client, cid, sid, rid, reply):
    fake = FakeLLM([[reply + _HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/extend", json={})
    assert result.status_code == 200 and '"error"' not in result.text, result.text
    return result, fake


def _prefill_on(client):
    assert client.put("/api/llm-connections/openrouter",
                      json={"prefill": True}).status_code == 200


def test_a_branch_carries_the_records_of_the_posts_it_keeps(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    send(client, cid, sid, "Later... much later.", content="Then?",
         speaker_ref="characters:mara")
    source = records(client, cid, sid)
    assert len(source) == 2

    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/branch", json={"through": 1})
    assert response.status_code == 200, response.text
    new = response.json()["id"]

    kept = messages(client, cid, new)
    assert len(kept) == 2 and kept[-1]["rewritten"] is True
    copied = records(client, cid, new)
    # Under the reply's reissued id, and only the post the branch kept.
    assert set(copied) == {kept[-1]["response_id"]}
    assert copied[kept[-1]["response_id"]]["original"] == "She paused... then spoke."
    assert records(client, cid, sid) == source       # the source is never written

    restored = client.put(f"/api/campaigns/{cid}/scenes/{new}/messages/1",
                          json={"content": "She paused... then spoke.", "restore": True})
    assert restored.status_code == 200, restored.text


def test_keep_writing_a_rewritten_reply_records_the_whole_original(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    _prefill_on(client)
    send(client, cid, sid, "She paused... then", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]

    _extend(client, cid, sid, rid, " spoke... softly.")

    reply = messages(client, cid, sid)[-1]
    assert reply["content"] == "She paused… then spoke… softly."
    assert reply["rewritten"] is True
    record = records(client, cid, sid)[rid]
    assert record["original"] == "She paused... then spoke... softly."
    assert len(record["rules"]) == 1


def test_keep_writing_an_unrewritten_continuation_keeps_the_replys_original(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    _prefill_on(client)
    send(client, cid, sid, "She paused... then", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]

    _extend(client, cid, sid, rid, " spoke softly.")

    assert messages(client, cid, sid)[-1]["rewritten"] is True
    assert records(client, cid, sid)[rid]["original"] == "She paused... then spoke softly."


def test_keep_writing_seeds_the_display_view_and_continues_the_prompt_view(client):
    cid, sid = seed(client)
    put_rules(client, cid, {"name": "Asides", "pattern": r"\s*\[\[.*?\]\]", "replacement": "",
                            "applies": ["display", "prompt"]})
    _prefill_on(client)
    send(client, cid, sid, "Mara paused [[aside]]", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]

    result, fake = _extend(client, cid, sid, rid, ", then left.")

    start = next(f["response_start"] for f in _events(result.text) if "response_start" in f)
    assert start["extend"] == {"seed": "Mara paused"}
    assert fake.messages[-1] == {"role": "assistant", "content": "Mara paused"}
    # What is stored is still raw: the continuation joins the stored text.
    assert store.responses.get(cid, sid, rid)["content"] == "Mara paused [[aside]], then left."
