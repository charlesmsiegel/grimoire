"""What produced a response is recorded on it: the response settings its prompt
rendered (on the record) and the call that wrote each variant (`made_by`)."""

from grimoire import llm, routes, store
from grimoire.llm_errors import LLMError
from grimoire.routes import character_turns
from tests.llm_fakes import FailingOpenRouter, FakeLLM, ScriptedProvider
from tests.test_character_turns import seed

HANDOFF = 'Hi.\n```handoff\n{"next":null}\n```'


def _record(client, cid, sid):
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    return base, rid, client.get(base + f"/responses/{rid}").json()


def test_first_take_records_the_settings_the_prompt_rendered(client):
    cid, sid = seed(client)
    store.scenes.set_response(cid, sid, {
        "response_continuation_words": "300", "response_continuation_paragraphs": "4"})
    fake = FakeLLM([[HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    _base, _rid, record = _record(client, cid, sid)
    # The whole record, so a key added to (or leaked into) it is seen: the
    # style is the one the scene resolves, no style being "".
    assert record["settings"] == {
        "style_id": "", "phase": "continuation", "words": 300, "paragraphs": 4}


def test_one_shot_override_is_in_the_recorded_settings(client):
    cid, sid = seed(client)
    fake = FakeLLM([[HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={
        "content": "Hello", "speaker_ref": "characters:mara",
        "response": {"response_continuation_words": "90"}})
    _base, _rid, record = _record(client, cid, sid)
    assert record["settings"]["words"] == 90


def test_reroll_does_not_re_resolve_settings(client):
    cid, sid = seed(client)
    store.scenes.set_response(cid, sid, {"response_continuation_words": "300"})
    fake = FakeLLM([[HANDOFF], [HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    rid = _record(client, cid, sid)[1]
    store.scenes.set_response(cid, sid, {"response_continuation_words": "120"})
    response = client.post(base + f"/responses/{rid}/regenerate", json={})
    assert response.status_code == 200, response.text
    assert fake.calls == 2
    record = client.get(base + f"/responses/{rid}").json()
    assert record["settings"]["words"] == 300


def test_roll_continuation_records_resume_settings(client):
    cid, sid = seed(client)
    store.scenes.set_response(cid, sid, {"response_continuation_words": "300"})
    fake = FakeLLM([
        ['Wait.\n```roll\n{"check":"notice"}\n```'],
        ['No roll.\n```handoff\n{"next":null}\n```'],
    ])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    proposal = store.proposals.get(cid, sid)
    store.scenes.set_response(cid, sid, {"response_continuation_words": "120"})
    response = client.post(
        base + "/roll-proposal", json={"proposal": proposal["id"], "action": "decline"})
    assert response.status_code == 200 and "error" not in response.text, response.text
    _base, _rid, record = _record(client, cid, sid)
    assert record["settings"]["words"] == 300
    assert record["resume_settings"]["words"] == 120


# ---- made_by: what produced each saved variant ----

def _made_by(client, cid, sid, rid=None):
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    if rid is None:
        rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    record = client.get(base + f"/responses/{rid}").json()
    return record, record["variants"][-1].get("made_by")


def _seeded_connection_id():
    return store.llm_connections.read_connection_raw("openrouter")["id"]


def test_turn_variant_says_what_made_it(client):
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([[HANDOFF]])
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    _record, made_by = _made_by(client, cid, sid)
    assert made_by["task"] == "chat" and made_by["composed"] == "primary"
    assert made_by["model"] and made_by["provider"] == "openrouter"
    assert made_by["connection"]
    assert made_by["connection_id"] == _seeded_connection_id() == "openrouter"
    assert made_by["guidance"] == "" and made_by["note"] == ""


def test_director_turn_and_its_reroll_carry_the_typed_note(client):
    cid, sid = seed(client)
    fake = FakeLLM([[HANDOFF], [HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    response = client.post(base + "/chat", json={
        "content": "Make it rain.", "director": True, "speaker_ref": "characters:mara"})
    assert "error" not in response.text, response.text
    record, made_by = _made_by(client, cid, sid)
    assert made_by["note"] == "Make it rain." and made_by["task"] == "chat"
    response = client.post(base + f"/responses/{record['id']}/regenerate", json={})
    assert "error" not in response.text, response.text
    record, made_by = _made_by(client, cid, sid, record["id"])
    assert len(record["variants"]) == 2
    assert made_by["note"] == "Make it rain." and made_by["task"] == "regenerate"


def test_typed_note_is_clipped_like_guidance(client):
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([[HANDOFF]])
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    response = client.post(base + "/chat", json={
        "content": "y" * 600, "director": True, "speaker_ref": "characters:mara"})
    assert "error" not in response.text, response.text
    _record, made_by = _made_by(client, cid, sid)
    assert made_by["note"] == "y" * 500


def test_empty_send_records_no_note(client):
    # The round's `note` holds the director template here; that is app wording.
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([[HANDOFF]])
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    response = client.post(base + "/chat", json={
        "content": "", "director": True, "speaker_ref": "characters:mara"})
    assert "error" not in response.text, response.text
    _record, made_by = _made_by(client, cid, sid)
    assert made_by["note"] == ""


def test_reroll_variant_carries_clipped_guidance(client):
    cid, sid = seed(client)
    fake = FakeLLM([[HANDOFF], [HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    rid = _made_by(client, cid, sid)[0]["id"]
    response = client.post(base + f"/responses/{rid}/regenerate", json={"guidance": "x" * 600})
    assert "error" not in response.text, response.text
    _record, made_by = _made_by(client, cid, sid, rid)
    assert made_by["guidance"] == "x" * 500
    assert made_by["task"] == "regenerate" and made_by["composed"] == "primary"
    assert made_by["note"] == ""


def test_fallback_records_the_served_connection(client):
    cid, sid = seed(client)
    primary = ScriptedProvider(chunks=(), error=LLMError("auth", "refused"))
    fallback = ScriptedProvider(chunks=(HANDOFF,))
    facade = llm.LLMClient(openrouter=primary, openai_compatible=fallback, retries=0,
                           fallback={"id": "backup", "kind": "openai_compatible",
                                     "model": "vendor/fallback",
                                     "base_url": "https://example.test/v1"})
    client.app.dependency_overrides[routes.get_llm] = lambda: facade
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    response = client.post(base + "/chat", json={"content": "Hello",
                                                 "speaker_ref": "characters:mara"})
    assert "error" not in response.text, response.text
    _record, made_by = _made_by(client, cid, sid)
    assert made_by["model"] == "vendor/fallback"
    assert made_by["connection_id"] == "backup"
    assert made_by["provider"] == "openai_compatible"


class _NoModel(FakeLLM):
    """A holder the provider left without a model."""

    async def stream(self, messages, conn, usage=None):
        async for delta in super().stream(messages, conn, usage):
            if usage is not None:
                usage.pop("model", None)
            yield delta


def test_holder_without_a_model_leaves_model_absent(client):
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: _NoModel([[HANDOFF]])
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    record, made_by = _made_by(client, cid, sid)
    assert record["variants"][-1]["status"] == "complete"
    assert "model" not in made_by
    assert made_by["provider"] == "openrouter"


def test_ledger_append_failure_still_records_made_by(client, monkeypatch):
    def refuse(row, ts):
        raise OSError("disk full")

    monkeypatch.setattr(store.usage, "_append", refuse)
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([[HANDOFF]])
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    _record, made_by = _made_by(client, cid, sid)
    assert made_by["model"]


def test_prose_before_a_roll_fence_and_a_rescue_carry_made_by(client):
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM(
        [['Wait.\n```roll\n{"check":"notice"}\n```']])
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    assert store.proposals.get(cid, sid)
    record, made_by = _made_by(client, cid, sid)
    assert record["variants"][-1]["status"] == "incomplete"
    assert made_by["task"] == "chat" and made_by["model"]

    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FailingOpenRouter(["Partial."])
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    response = client.post(base + "/chat", json={"content": "Hello",
                                                 "speaker_ref": "characters:mara"})
    assert "network" in response.text
    record, made_by = _made_by(client, cid, sid)
    assert record["variants"][-1]["status"] == "incomplete"
    assert made_by["task"] == "chat" and made_by["model"]
    assert made_by["composed"] == "primary"


def test_continuation_variant_is_composed_resume(client):
    cid, sid = seed(client)
    fake = FakeLLM([
        ['Wait.\n```roll\n{"check":"notice"}\n```'],
        ['No roll.\n```handoff\n{"next":null}\n```'],
    ])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    proposal = store.proposals.get(cid, sid)
    response = client.post(
        base + "/roll-proposal", json={"proposal": proposal["id"], "action": "decline"})
    assert response.status_code == 200 and "error" not in response.text, response.text
    record, made_by = _made_by(client, cid, sid)
    assert record["variants"][-1]["status"] == "complete"
    assert made_by["composed"] == "resume" and made_by["task"] == "continuation"
    assert record["variants"][0]["made_by"]["composed"] == "primary"


def test_old_variant_without_made_by_round_trips(client):
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([[HANDOFF]])
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    record, made_by = _made_by(client, cid, sid)
    rid, first = record["id"], record["variants"][0]["id"]
    old = store.responses.save_variant(cid, sid, rid, "Older words.", "complete", activate=False)
    assert "made_by" not in old
    for vid in (old["id"], first):
        response = client.post(base + f"/responses/{rid}/variants/{vid}/activate")
        assert response.status_code == 200, response.text
    variants = {v["id"]: v for v in client.get(base + f"/responses/{rid}").json()["variants"]}
    assert "made_by" not in variants[old["id"]]
    assert variants[first]["made_by"] == made_by


def test_made_by_is_fail_soft():
    class Broken:
        task = "chat"
        usage = None  # .get on None raises

    assert character_turns._made_by(None, "chat", "primary", "") is None
    assert character_turns._made_by(Broken(), "chat", "primary", "") is None


def test_ledger_reroll_records_its_steer(client):
    cid, sid = seed(client)
    fake = FakeLLM([[HANDOFF], [HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    rid = _made_by(client, cid, sid)[0]["id"]
    response = client.post(base + f"/responses/{rid}/regenerate", json={"guidance": "Colder."})
    assert "error" not in response.text, response.text
    assert store.steering.texts(cid, sid) == ["Colder."]


def test_empty_guidance_records_no_steer(client):
    cid, sid = seed(client)
    fake = FakeLLM([[HANDOFF], [HANDOFF], [HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    rid = _made_by(client, cid, sid)[0]["id"]
    for body in (None, {}, {"guidance": ""}):
        response = client.post(base + f"/responses/{rid}/regenerate", json=body)
        assert "error" not in response.text, response.text
    assert fake.calls == 4
    assert store.steering.texts(cid, sid) == []


def test_refused_reroll_records_no_steer(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "assistant", "Old narration.")
    fake = FakeLLM([["Must not run"]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = client.get(base).json()["messages"][-1]["response_id"]
    result = client.post(base + f"/responses/{rid}/regenerate", json={"guidance": "Colder."})
    assert result.status_code == 409 and "historical_context_unavailable" in result.text
    assert fake.calls == 0
    assert store.steering.texts(cid, sid) == []
