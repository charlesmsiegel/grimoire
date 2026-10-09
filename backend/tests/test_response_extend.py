"""Keep writing (play controls IV): continue the trailing response as a new variant."""

from types import SimpleNamespace

import pytest

from grimoire import content_parts, llm, routes, store
from grimoire.llm import ATTEMPTED
from grimoire.llm_errors import LLMError
from grimoire.routes import character_turns
from grimoire.routes import runs as runs_mod
from grimoire.store import response_protocol
from tests.inference_fixtures import put_settings
from tests.llm_fakes import FakeLLM, ScriptedProvider
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


@pytest.mark.parametrize("conn, mode", [
    ({"kind": "anthropic", "model": "m", "prefill": True}, "instruction"),
    ({"kind": "anthropic", "model": "claude-opus-4-6", "prefill": True}, "instruction"),
    # Claude 4.5 and earlier take a prefill; 4.6 and later refuse one.
    ({"kind": "anthropic", "model": "claude-haiku-4-5-20251001", "prefill": True}, "prefill"),
    ({"kind": "openrouter", "model": "m", "prefill": True}, "prefill"),
    ({"kind": "openai_compatible", "model": "m", "base_url": "http://localhost:11434/v1",
      "prefill": True}, "prefill"),
    # `claude`'s preset lists prefill under `never` too, but an existing store's
    # opt-in keeps today's answer (slice B changes no existing store).
    ({"kind": "claude", "model": "", "prefill": True}, "prefill"),
    ({"kind": "openrouter", "model": "m"}, "instruction"),
])
def test_keep_writing_prefills_only_where_the_provider_does_not_rule_it_out(conn, mode):
    """The user's opt-in is not enough on the Anthropic API, whose models from
    Claude 4.6 on refuse a trailing assistant turn: prefill is ruled out per
    model there, and allowed on the older ones that take it."""
    assert character_turns._extend_mode(conn) == mode
    tailed = character_turns._extend_messages(_SNAP, conn, "Mara waits", "", None)
    assert tailed.mode_for(conn) == mode


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
    assert response_protocol.strip_preparation("```perception\nnotes\n```\nThen.") == (
        "Then.", "\n\n[Perception preparation]\nnotes\n")
    assert response_protocol.strip_preparation(" and left.") == (" and left.", "")
    assert response_protocol.strip_preparation("\n\nThen ```perception\n```") == (
        "\n\nThen ```perception\n```", "")


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
    """Prefill on for the model Primary runs (a fact of the model at format 2)."""
    model = store.read_config()[store.inference_keys.role_key("primary", "model")]
    assert client.put("/api/llm-connections/openrouter/facts",
                      json={"model": model, "prefill": True}).status_code == 200


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
        conn_id, model = "claude", store.config.DEFAULT_CLAUDE_MODEL
    else:
        conn_id, model = client.post("/api/llm-connections", json={
            "kind": "openai_compatible", "name": "Saltmarch Local",
            "base_url": "http://localhost:9/v1"}).json()["id"], "local-model"
        assert client.put(f"/api/llm-connections/{conn_id}/facts", json={
            "model": model, "post_process": "strict"}).status_code == 200
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    # A provider names no model of its own at format 2: the override names both.
    result, fake = _extend(client, base, rid, "Then the door opened.",
                           {"connection_id": conn_id, "model": model})
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


def test_a_prefill_primary_failing_over_to_an_instruction_fallback_strips_its_fence(client):
    """Only this path reaches `_accept_extend`'s strip: the watcher ran with
    perception off for the prefill primary, and the instruction fallback that
    answered was asked for a reply, so it may open with a perception fence."""
    cid, sid = seed(client)
    _prefill_on(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    backup = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Saltmarch Backup",
        "base_url": "https://example.test/v1"}).json()["id"]
    put_settings(client, {"roles": {"primary": {"fallback": {
        "provider": backup, "model": "vendor/unknown"}}}})
    primary = ScriptedProvider(chunks=(), error=LLMError("auth", "refused"))
    fallback = ScriptedProvider(chunks=(
        "```perception\nShe notes", " the door.\n```\n", "Then she left." + _HANDOFF))
    # No `fallback`, as the shipped client: the turn's conn carries its own.
    facade = llm.LLMClient(openrouter=primary, openai_compatible=fallback, retries=0)
    client.app.dependency_overrides[routes.get_llm] = lambda: facade
    result = client.post(base + f"/responses/{rid}/extend", json={})
    assert result.status_code == 200 and "error" not in result.text, result.text
    assert primary.requests[0]["messages"][-1] == {"role": "assistant", "content": "Original."}
    assert fallback.requests[0]["messages"][-1]["role"] == "user"
    after = store.responses.get(cid, sid, rid)
    assert after["content"] == "Original.\n\nThen she left."
    assert after["variants"][-1]["made_by"]["mode"] == "instruction"
    private = store.responses.get(cid, sid, rid, private=True)["variants"][-1]
    assert "[Perception preparation]\nShe notes the door." in private["reasoning"]


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


# --- refusals -------------------------------------------------------------------


def _cut_short(client, base):
    fake = FakeLLM([["Partial"]], error=LLMError("rate_limit", "Wait"))
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.post(base + "/chat", json={"content": "Stay", "speaker_ref": "characters:mara"})


def _not_last(client, monkeypatch, cid, sid, base):
    rid = _answer(client, base)
    _answer(client, base, "Later.")
    return rid


def _roll_after(client, monkeypatch, cid, sid, base):
    rid = _answer(client, base)
    store.scenes.append_message(cid, sid, "assistant", "\U0001f3b2 1d20 = 12",
                                speaker=store.scenes.serialize.ROLL_SPEAKER)
    return rid


def _legacy(client, monkeypatch, cid, sid, base):
    store.scenes.append_message(cid, sid, "assistant", "Old narration.")
    return client.get(base).json()["messages"][-1]["response_id"]


def _hidden_before(client, monkeypatch, cid, sid, base):
    fake = FakeLLM([["Original." + _HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    # The exclusion stamp must be later than the snapshot, which a same-second
    # tie cannot prove -- so the record is made to look older.
    ledger = store.responses._read(cid)
    for scope in ledger["scenes"].values():
        if rid in scope["responses"]:
            scope["responses"][rid]["created"] = "2000-01-01T00:00:00Z"
    store.responses._write(cid, ledger)
    r = client.put(base + "/messages/0/excluded", json={"excluded": True})
    assert r.status_code == 200, r.text
    return rid


def _open_round(client, monkeypatch, cid, sid, base):
    _cut_short(client, base)
    return store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]


def _incomplete(client, monkeypatch, cid, sid, base):
    rid = _open_round(client, monkeypatch, cid, sid, base)
    round_id = store.responses.get(cid, sid, rid)["round_id"]
    store.responses.update_round(cid, sid, round_id, status="superseded")
    return rid


def _proposed(client, monkeypatch, cid, sid, base):
    rid = _answer(client, base)
    store.proposals.new(cid, sid, {"check": "notice"})
    return rid


def _reviewed(client, monkeypatch, cid, sid, base):
    rid = _answer(client, base)
    monkeypatch.setattr(store.pending_reviews, "read", lambda c, s: {"review": {}})
    return rid


def _running(client, monkeypatch, cid, sid, base):
    rid = _answer(client, base)
    runs_mod.reserve_turn(client.app, cid, sid, "chat", "a-1")
    return rid


#: Each refusal kind, and how to arrange the response that should earn it.
_REFUSALS = {
    "not_last_response": _not_last,
    "applied_mechanics": _roll_after,
    "historical_context_unavailable": _legacy,
    "context_excluded": _hidden_before,
    "round_open": _open_round,
    "variant_incomplete": _incomplete,
    "proposal_pending": _proposed,
    "review_pending": _reviewed,
    "run_in_flight": _running,
}


@pytest.mark.parametrize("case", sorted(_REFUSALS))
def test_extend_refusals(client, monkeypatch, case):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _REFUSALS[case](client, monkeypatch, cid, sid, base)
    before = store.scenes.read_scene(cid, sid)["messages"]
    steering = store.steering.texts(cid, sid)
    fake = FakeLLM([["Must not run." + _HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(base + f"/responses/{rid}/extend", json={"guidance": "colder"})
    assert result.status_code == 409, result.text
    assert result.json()["kind"] == case, result.text
    assert fake.calls == 0
    assert store.steering.texts(cid, sid) == steering
    assert store.scenes.read_scene(cid, sid)["messages"] == before


def test_extend_of_a_missing_response_is_a_404(client):
    cid, sid = seed(client)
    fake = FakeLLM([["Must not run."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/missing/extend")
    assert result.status_code == 404 and fake.calls == 0


def test_trailing_transition_lines_do_not_make_a_reply_not_last(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    store.scenes.append_message(cid, sid, "assistant", "The scene moves to the docks.",
                                speaker=store.scenes.serialize.TRANSITION_SPEAKER)
    result, _ = _extend(client, base, rid, "Then rain.")
    assert result.status_code == 200 and "error" not in result.text, result.text
    assert store.responses.get(cid, sid, rid)["content"] == "Original.\n\nThen rain."


def test_extend_on_a_closed_branch_is_refused(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    _answer(client, base)
    rid = _answer(client, base, "Later.")
    branch = store.branch.branch_scene(cid, sid, len(store.scenes.read_scene(cid, sid)["messages"]) - 1)
    store.scenes.mark_absorbed(cid, sid, "x", "y")
    branch_rid = store.scenes.read_scene(cid, branch)["messages"][-1]["response_id"]
    assert branch_rid and rid
    fake = FakeLLM([["Must not run." + _HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(f"/api/campaigns/{cid}/scenes/{branch}/responses/{branch_rid}/extend")
    assert result.status_code == 409 and result.json()["kind"] == "branch_closed"
    assert fake.calls == 0


def test_a_fallback_capture_records_the_tail_that_fallback_was_sent(monkeypatch, tmp_path):
    """Plan-gate ruling 5: the prompt log holds a fallback's own ending."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    recorded = []
    monkeypatch.setattr(store.prompt_log, "capturing", lambda: True)
    monkeypatch.setattr(character_turns, "_record_prompt",
                        lambda cid, sid, task, breakdown, **kw: recorded.append((task, kw)))
    # The fallback this call carries, as the resolver attaches it.
    conn = {"kind": "openrouter", "model": "m", "prefill": True,
            llm.FALLBACK_KEY: {"kind": "openai_compatible", "model": "fb", "prefill": False}}
    tailed = character_turns._extend_messages(_SNAP, conn, "Mara waits", "", None)
    character_turns._capture("c", "s", "extend", tailed, conn)
    assert recorded[-1][1]["messages"][-1] == {"role": "assistant", "content": "Mara waits"}
    tailed.for_model("fb")          # what dispatch does on the fallback attempt
    task, kw = recorded[-1]
    assert task == "extend" and kw["model"] == "fb"
    assert kw["messages"][-2] == {"role": "assistant", "content": "Mara waits"}
    assert kw["messages"][-1]["role"] == "user"
