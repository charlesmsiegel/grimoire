"""Keep writing (play controls IV): continue the trailing response as a new variant."""

from types import SimpleNamespace

import pytest

from grimoire import content_parts, routes, store
from grimoire.llm import ATTEMPTED
from grimoire.routes import character_turns
from grimoire.store import response_protocol
from tests.llm_fakes import FakeLLM
from tests.test_character_turns import seed
from tests.test_response_controls_routes import _answer
from tests.test_runs_routes import _events

# --- pure helpers -----------------------------------------------------------


@pytest.mark.parametrize("lead,text,mode,joiner", [
    ("\n\n", "Then rain.", "prefill", "\n\n"), ("\n", "Then rain.", "prefill", "\n"),
    (" ", "then rain.", "prefill", " "), ("", "ing.", "prefill", ""),
    ("  \n \n", "Then.", "prefill", "\n\n"),
    ("", "Then rain.", "instruction", "\n\n"), ("", "and left.", "instruction", " "),
    ("", "— or not.", "instruction", " "), ("", "”", "instruction", " "),
    ("", "…", "instruction", " "), ("\n\n", "and left.", "instruction", " "),
    ("", "*She turns.*", "instruction", " "), ("", '"Then."', "instruction", "\n\n")])
def test_extend_joiner(lead, text, mode, joiner):
    assert character_turns._extend_joiner(lead, text, mode) == joiner


_SNAP = {"version": 1, "primary_model": "m",
         "unprofiled": [[{"role": "user", "content": "Go."}], None], "profiles": {}}


def test_extend_tails_carry_the_partial_reply_and_the_steer():
    m = character_turns._extend_messages(_SNAP, {"model": "m", "prefill": True},
                                         "Mara ![a lamp](/api/x.png) waits", "colder", 150)
    assert m[0] == {"role": "user", "content": "Go."}
    assert list(m)[-2]["role"] == "system" and "colder" in list(m)[-2]["content"]
    assert "continuing" in list(m)[-2]["content"]
    assert list(m)[-1] == {"role": "assistant", "content": "Mara a lamp waits"}
    assert m.mode_for({"prefill": True}) == "prefill"
    instr = m.for_connection({"model": "m"}, "m")
    assert instr[-2] == {"role": "assistant", "content": "Mara a lamp waits"}
    assert instr[-1]["role"] == "user"
    assert "Continue exactly where" in instr[-1]["content"]
    assert "150" in instr[-1]["content"] and "colder" in instr[-1]["content"]
    assert not any(x["role"] == "system" and "colder" in x["content"] for x in instr)


def test_extend_instruction_without_words_or_steer():
    m = character_turns._extend_messages(_SNAP, {"model": "m"}, "Mara waits", "", None)
    assert [x["role"] for x in m] == ["user", "assistant", "user"]
    assert "words" not in m[-1]["content"] and "Direction" not in m[-1]["content"]


def test_strip_preparation_removes_only_a_leading_fence():
    assert response_protocol.strip_preparation("```perception\nnotes\n```\nThen.") == "Then."
    assert response_protocol.strip_preparation(" and left.") == " and left."
    assert response_protocol.strip_preparation("\n\nThen ```perception\n```") == (
        "\n\nThen ```perception\n```")


def test_is_trailing_skips_synthetic_lines():
    transition = store.scenes.serialize.TRANSITION_SPEAKER
    msgs = [{"role": "user", "speaker": "You", "content": "Hi"},
            {"role": "assistant", "speaker": "Mara", "content": "A", "response_id": "r1",
             "response_part": "p0"},
            {"role": "assistant", "speaker": "Mara", "content": "B", "response_id": "r1",
             "response_part": "p1"},
            {"role": "assistant", "speaker": transition, "content": "-> dock"}]
    assert store.responses.is_trailing(msgs, "r1")
    assert not store.responses.is_trailing(msgs, "r2")
    assert not store.responses.is_trailing(
        [*msgs, {"role": "user", "speaker": "You", "content": "Hi"}], "r1")
    assert not store.responses.is_trailing(
        [*msgs, {"role": "assistant", "speaker": "Winifred", "content": "C",
                 "response_id": "r2"}], "r1")


# --- the route ----------------------------------------------------------------

_HANDOFF = '\n```handoff\n{"next":null}\n```'


def _extend(client, base, rid, reply, body=None, *, fence=True):
    fake = FakeLLM([[reply + (_HANDOFF if fence else "")]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return client.post(base + f"/responses/{rid}/extend", json=body or {}), fake


def _start(result):
    return next(f["response_start"] for f in _events(result.text) if "response_start" in f)


def _prefill_on(client):
    assert client.put("/api/llm-connections/openrouter",
                      json={"prefill": True}).status_code == 200


def test_prefill_extend_appends_the_partial_reply_and_saves_a_joined_variant(client):
    cid, sid = seed(client)
    _prefill_on(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base, "Mara paused")
    before = store.responses.get(cid, sid, rid)
    result, fake = _extend(client, base, rid, ", then left.")
    assert result.status_code == 200 and "error" not in result.text, result.text
    assert _start(result)["extend"] == {"seed": "Mara paused"}
    assert fake.messages[-1] == {"role": "assistant", "content": "Mara paused"}
    after = store.responses.get(cid, sid, rid)
    assert after["content"] == "Mara paused, then left."
    assert len(after["variants"]) == len(before["variants"]) + 1
    assert after["active_variant"] == after["variants"][-1]["id"]
    assert after["variants"][0]["content"] == "Mara paused"
    made_by = after["variants"][-1]["made_by"]
    assert made_by["task"] == "extend" and made_by["mode"] == "prefill"
    assert made_by["extends"] == before["active_variant"] and made_by["composed"] == "primary"
    # The reply's own handoff is kept: the continuation's fence decided nothing.
    assert after["variants"][-1]["handoff"] == after["variants"][0]["handoff"]
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] == "Mara paused, then left."


@pytest.mark.parametrize("kind", ["claude", "openai_compatible"])
def test_instruction_extend_on_a_non_prefill_connection(client, kind):
    cid, sid = seed(client)
    _prefill_on(client)   # the standing route; the override below is not prefill
    if kind == "claude":
        conn_id = "claude"
    else:
        conn_id = client.post("/api/llm-connections", json={
            "kind": "openai_compatible", "name": "Saltmarch Local",
            "base_url": "http://localhost:9/v1", "model": "local-model",
            "post_process": "strict"}).json()["id"]
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    result, fake = _extend(client, base, rid, "Then the door opened.",
                           {"connection_id": conn_id})
    assert result.status_code == 200 and "error" not in result.text, result.text
    assert fake.conn["id"] == conn_id
    assert fake.messages[-1]["role"] == "user"
    assert "Continue exactly where" in fake.messages[-1]["content"]
    assert fake.messages[-2] == {"role": "assistant", "content": "Original."}
    after = store.responses.get(cid, sid, rid)
    assert after["content"] == "Original.\n\nThen the door opened."
    assert after["variants"][-1]["made_by"]["mode"] == "instruction"


def test_instruction_extend_joins_a_lowercase_continuation_with_a_space(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    result, _ = _extend(client, base, rid, "and she left.")
    assert "error" not in result.text, result.text
    assert store.responses.get(cid, sid, rid)["content"] == "Original. and she left."


def test_instruction_extend_strips_a_leading_perception_fence(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    result, _ = _extend(client, base, rid, "```perception\nShe notes the door.\n```\nThen she left.")
    assert "error" not in result.text, result.text
    assert "She notes" not in result.text
    after = store.responses.get(cid, sid, rid)
    assert after["content"] == "Original.\n\nThen she left."
    assert "She notes the door." in store.responses.get(
        cid, sid, rid, private=True)["variants"][-1]["reasoning"]


def test_extend_continues_the_trimmed_transcript_prose(client):
    cid, sid = seed(client)
    _prefill_on(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    index = next(i for i, m in enumerate(store.scenes.read_scene(cid, sid)["messages"])
                 if m.get("response_id") == rid)
    store.scenes.edit_message(cid, sid, index, "Orig")
    result, fake = _extend(client, base, rid, "inal, she said.")
    assert "error" not in result.text, result.text
    assert fake.messages[-1] == {"role": "assistant", "content": "Orig"}
    assert store.responses.get(cid, sid, rid)["content"] == "Original, she said."


@pytest.mark.parametrize("prefill", [True, False])
def test_empty_continuation_keeps_the_active_variant(client, prefill):
    cid, sid = seed(client)
    if prefill:
        _prefill_on(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    before = store.responses.get(cid, sid, rid)
    result, fake = _extend(client, base, rid, "")
    assert fake.calls == 1 and "replacement_incomplete" in result.text
    after = store.responses.get(cid, sid, rid)
    assert after["variants"] == before["variants"]
    assert after["active_variant"] == before["active_variant"]
    assert after["content"] == "Original."


def test_a_roll_fence_in_the_continuation_is_refused(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    before = store.responses.get(cid, sid, rid)
    result, _ = _extend(client, base, rid, 'She reaches.\n```roll\n{"check":"notice"}\n```',
                        fence=False)
    assert "extend_roll_refused" in result.text
    assert "cannot propose a roll" in result.text
    after = store.responses.get(cid, sid, rid)
    assert after["variants"] == before["variants"] and after["content"] == "Original."
    assert store.proposals.get(cid, sid) is None


def _two_part(client, cid, sid):
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    fake = FakeLLM([['Wait.\n```roll\n{"check":"notice"}\n```'],
                    ["No roll." + _HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    proposal = store.proposals.get(cid, sid)
    result = client.post(base + "/roll-proposal",
                         json={"proposal": proposal["id"], "action": "decline"})
    assert "error" not in result.text, result.text
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    parts = [m for m in store.scenes.read_scene(cid, sid)["messages"]
             if m.get("response_id") == rid]
    assert [m["content"] for m in parts] == ["Wait.", "No roll."]
    return base, rid


def _count(messages, needle):
    return sum(content_parts.text_of(m["content"]).count(needle) for m in messages)


def test_a_multi_part_response_extends_from_its_resume_snapshot(client):
    cid, sid = seed(client)
    base, rid = _two_part(client, cid, sid)
    record = store.responses.get(cid, sid, rid, private=True)
    assert record["resume_snapshot"]
    result, fake = _extend(client, base, rid, "Then she sat.")
    assert "error" not in result.text, result.text
    model = fake.conn["model"]
    resumed = list(character_turns.PreparedMessages.from_snapshot(record["resume_snapshot"], model))
    assert list(fake.messages)[:-2] == resumed
    assert fake.messages[-2] == {"role": "assistant", "content": "No roll."}
    assert _count(fake.messages, "Wait.") == 1
    after = store.responses.get(cid, sid, rid)
    assert after["content"] == "Wait.\n\nNo roll.\n\nThen she sat."
    made_by = after["variants"][-1]["made_by"]
    assert made_by["composed"] == "resume"
    assert made_by["settings"] == record.get("resume_settings")
    # Activating the joined variant collapses the parts into one message.
    assert len([m for m in store.scenes.read_scene(cid, sid)["messages"]
                if m.get("response_id") == rid]) == 1
    # A second extend sees one whole reply: the primary snapshot, part one once.
    result, fake = _extend(client, base, rid, "And then stood.")
    assert "error" not in result.text, result.text
    assert _count(fake.messages, "Wait.") == 1
    assert fake.messages[-2]["content"] == "Wait.\n\nNo roll.\n\nThen she sat."
    assert store.responses.get(cid, sid, rid)["variants"][-1]["made_by"]["composed"] == "primary"


def test_a_rerolled_multi_part_response_extends_from_the_primary_snapshot(client):
    cid, sid = seed(client)
    base, rid = _two_part(client, cid, sid)
    fake = FakeLLM([["Replacement." + _HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    assert "error" not in client.post(base + f"/responses/{rid}/regenerate").text
    result, fake = _extend(client, base, rid, "Then she sat.")
    assert "error" not in result.text, result.text
    assert _count(fake.messages, "Replacement.") == 1
    assert _count(fake.messages, "No roll.") == 0
    assert fake.messages[-2] == {"role": "assistant", "content": "Replacement."}
    assert store.responses.get(cid, sid, rid)["variants"][-1]["made_by"]["composed"] == "primary"


def test_steer_rides_the_instruction_and_reaches_the_steering_log(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    result, fake = _extend(client, base, rid, "Then rain.", {"guidance": "colder"})
    assert "error" not in result.text, result.text
    assert "colder" in fake.messages[-1]["content"] and fake.messages[-1]["role"] == "user"
    assert not any(m["role"] == "system" and "colder" in m["content"] for m in fake.messages)
    assert store.steering.texts(cid, sid)[-1] == "colder"
    made_by = store.responses.get(cid, sid, rid)["variants"][-1]["made_by"]
    assert made_by["guidance"] == "colder"


def test_steer_rides_before_the_partial_in_prefill(client):
    cid, sid = seed(client)
    _prefill_on(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    result, fake = _extend(client, base, rid, " Then rain.", {"guidance": "colder"})
    assert "error" not in result.text, result.text
    assert fake.messages[-2]["role"] == "system" and "colder" in fake.messages[-2]["content"]
    assert fake.messages[-1] == {"role": "assistant", "content": "Original."}
    assert store.responses.get(cid, sid, rid)["content"] == "Original. Then rain."


def test_served_mode_follows_the_attempt_that_answered():
    tailed = character_turns._extend_messages(_SNAP, {"model": "m", "prefill": True},
                                              "Mara waits", "", None)
    meter = SimpleNamespace(usage={ATTEMPTED: {"kind": "openai_compatible"}})
    assert character_turns._served_mode(tailed, meter, {"prefill": True}) == "instruction"
    assert character_turns._served_mode(tailed, None, {"prefill": True}) == "prefill"
    assert character_turns._served_mode(tailed, SimpleNamespace(usage={}),
                                        {"prefill": True}) == "prefill"


def test_extend_is_metered_as_extend_and_not_counted_as_a_reroll(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    fake = FakeLLM([["Original." + _HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    result, _ = _extend(client, base, rid, "Then rain.")
    assert "error" not in result.text, result.text
    row = list(store.usage.calls(1, cid))[-1]
    assert row["task"] == "extend" and row["response_id"] == rid
    assert isinstance(row.get("post"), int)
    usage = store.usage.scene_usage(cid, sid)
    assert [b["rerolls"] for b in usage["by_post"]] == [0]
