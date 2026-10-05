"""The character-turn engine following a scene's group-play speaker order."""

import asyncio
import json
from types import SimpleNamespace

from grimoire import routes, store
from grimoire.llm_errors import LLMError
from grimoire.routes import character_turns, streaming
from grimoire.store import group_play
from tests.llm_fakes import FakeLLM

MARA = "characters:mara"
WINIFRED = "characters:winifred"
SELECTOR = "Choose at most one initial speaker"


def seed(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in ("Mara", "Winifred"):
        response = client.post(f"/api/campaigns/{cid}/characters", json={"name": name})
        assert response.status_code == 200, response.text
        actor = response.json()["character"]
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert response.status_code == 200, response.text
    return cid, sid, f"/api/campaigns/{cid}/scenes/{sid}"


def group(client, base, **settings):
    response = client.put(base + "/group", json=settings)
    assert response.status_code == 200, response.text


def use(client, fake):
    client.app.dependency_overrides[routes.get_llm] = lambda: fake


def speakers(cid, sid):
    return [m["speaker"] for m in store.scenes.read_scene(cid, sid)["messages"]
            if m.get("response_id")]


def asked_selector(fake):
    return any(SELECTOR in m["content"] for r in fake.requests for m in r["messages"])


def reply(text, nxt):
    return [f'{text}\n```handoff\n{{"next":{json.dumps(nxt)}}}\n```']


def latest_round(cid, sid):
    rounds = store.responses._scope(cid, sid, store.responses._read(cid))["rounds"]
    return list(rounds.values())[-1]


def test_list_mode_speaks_in_order_without_selector(client):
    cid, sid, base = seed(client)
    group(client, base, order="list", order_list=[WINIFRED, MARA])
    fake = FakeLLM([reply("Winifred speaks.", MARA), reply("Mara speaks.", WINIFRED)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "error" not in response.text, response.text
    assert fake.calls == 2
    assert speakers(cid, sid) == ["Winifred", "Mara"]
    assert not asked_selector(fake)


def test_list_mode_prompt_offers_no_handoff_candidates(client):
    _cid, _sid, base = seed(client)
    group(client, base, order="list", order_list=[WINIFRED, MARA])
    fake = FakeLLM([reply("Winifred speaks.", None), reply("Mara speaks.", None)])
    use(client, fake)
    client.post(base + "/chat", json={"content": "Hello"})
    prompt = "\n".join(m["content"] for m in fake.requests[0]["messages"])
    section = prompt.split("Eligible next speakers:", 1)[1].split("The current actor", 1)[0]
    assert "characters:" not in section
    assert "grimoire" not in section


def test_natural_ignores_handoff_and_puts_named_first(client):
    cid, sid, base = seed(client)
    group(client, base, order="natural", talkativeness={MARA: 0, WINIFRED: 0})
    fake = FakeLLM([reply("Winifred answers.", MARA)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Winifred?"})
    assert "error" not in response.text, response.text
    assert fake.calls == 1
    assert speakers(cid, sid) == ["Winifred"]


def test_manual_post_generates_nothing(client):
    cid, sid, base = seed(client)
    group(client, base, order="manual")
    fake = FakeLLM([reply("Nobody should say this.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert response.status_code == 200
    assert "error" not in response.text, response.text
    assert fake.calls == 0
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["role"] == "user" and messages[-1]["content"] == "Hello"
    assert latest_round(cid, sid)["status"] == "complete"


def test_manual_reply_as_generates_one(client):
    cid, sid, base = seed(client)
    group(client, base, order="manual")
    fake = FakeLLM([reply("Mara answers.", WINIFRED)])
    use(client, fake)
    response = client.post(base + "/chat", json={"speaker_ref": MARA})
    assert "error" not in response.text, response.text
    assert fake.calls == 1
    assert speakers(cid, sid) == ["Mara"]


def test_manual_post_with_text_and_reply_as_generates_only_the_lead(client):
    cid, sid, base = seed(client)
    group(client, base, order="manual")
    fake = FakeLLM([reply("Mara answers.", WINIFRED)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello", "speaker_ref": MARA})
    assert "error" not in response.text, response.text
    assert fake.calls == 1
    assert speakers(cid, sid) == ["Mara"]


def test_directed_talkativeness_zero_filters_selector_roster(client):
    cid, sid, base = seed(client)
    group(client, base, order="directed", talkativeness={WINIFRED: 0, MARA: 100})
    fake = FakeLLM([reply("Mara answers.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "error" not in response.text, response.text
    assert fake.calls == 1
    assert speakers(cid, sid) == ["Mara"]


def test_continue_in_list_mode_picks_next_after_last_speaker(client):
    cid, sid, base = seed(client)
    group(client, base, order="list", order_list=[MARA, WINIFRED])
    use(client, FakeLLM([reply("Mara answers.", None)]))
    client.post(base + "/chat", json={"speaker_ref": MARA})
    fake = FakeLLM([reply("Winifred continues.", MARA)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": ""})
    assert "error" not in response.text, response.text
    assert fake.calls == 1
    assert not asked_selector(fake)
    assert speakers(cid, sid) == ["Mara", "Winifred"]


def test_sitting_out_mid_round_is_skipped(client):
    cid, sid, _base = seed(client)
    settings = {**group_play.parse(""), "order": "list", "order_list": [MARA, WINIFRED]}
    store.scenes.set_group(cid, sid, group_play.dump(settings))
    round_record = store.responses.new_round(
        cid, sid, eligible=character_turns.roster(cid, sid), automatic=True, post=None,
        run_id="test", actor_ref=MARA, mode="list", plan=[WINIFRED])
    run = SimpleNamespace(
        id="test", scene_identity=store.scenes.scene_identity(cid, sid), cancel_requested=False)
    token = streaming._claim_turn(cid, sid)
    fake = FakeLLM([reply("Mara answers.", WINIFRED), reply("Winifred answers.", None)])

    async def collect():
        async for frame in character_turns._frames(
            cid, sid, fake, {"kind": "openrouter", "model": "test"}, run, token,
            round_record, streaming.StreamOutcome(),
        ):
            if "response_end" in json.loads(frame.removeprefix("data: ")):
                store.scenes.set_group(
                    cid, sid, group_play.dump({**settings, "sitting_out": [WINIFRED]}))

    asyncio.run(collect())
    assert fake.calls == 1
    assert speakers(cid, sid) == ["Mara"]


def test_continue_selector_never_sees_sitting_out(client):
    cid, sid, base = seed(client)
    group(client, base, order="directed", sitting_out=[WINIFRED])
    fake = FakeLLM([reply("Mara continues.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": ""})
    assert "error" not in response.text, response.text
    assert fake.calls == 1
    assert speakers(cid, sid) == ["Mara"]


def test_retry_resumes_plan_without_replanning(client, monkeypatch):
    cid, sid, base = seed(client)
    group(client, base, order="list", order_list=[MARA, WINIFRED])
    fake = FakeLLM([reply("Mara answers.", None), ["Partial."]],
                   error=LLMError("rate_limit", "Wait"), fail_after=1)
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "rate_limit" in response.text

    def boom():
        raise AssertionError("retry must not re-plan")

    monkeypatch.setattr(character_turns, "_rng", boom)
    retry = FakeLLM([reply("Winifred finishes.", None)])
    use(client, retry)
    response = client.post(base + "/retry")
    assert "error" not in response.text, response.text
    assert retry.calls == 1
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["speaker"] == "Winifred"
    assert messages[-1]["content"] == "Winifred finishes."
    assert speakers(cid, sid) == ["Mara", "Winifred"]


def test_reply_as_sitting_out_npc_in_directed_keeps_its_name(client):
    cid, sid, base = seed(client)
    group(client, base, order="directed", sitting_out=[WINIFRED])
    fake = FakeLLM([reply("Winifred answers anyway.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello", "speaker_ref": WINIFRED})
    assert "error" not in response.text, response.text
    assert fake.calls == 1
    assert speakers(cid, sid) == ["Winifred"]


def test_reply_as_with_text_leads_then_the_list_continues(client):
    cid, sid, base = seed(client)
    group(client, base, order="list", order_list=[MARA, WINIFRED], sitting_out=[WINIFRED])
    fake = FakeLLM([reply("Winifred answers anyway.", None), reply("Mara follows.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello", "speaker_ref": WINIFRED})
    assert "error" not in response.text, response.text
    # Winifred leads by explicit pick, sitting out or not; the list then
    # continues with everyone but her.
    assert fake.calls == 2
    assert speakers(cid, sid) == ["Winifred", "Mara"]


def test_manual_post_needs_no_connection(client):
    cid, sid, base = seed(client)
    group(client, base, order="manual")
    assert client.delete("/api/llm-connections/openrouter").status_code in (200, 204)
    fake = FakeLLM([reply("Nobody should say this.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert response.status_code == 200, response.text
    assert "error" not in response.text, response.text
    assert fake.calls == 0
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["role"] == "user" and messages[-1]["content"] == "Hello"


def test_manual_reply_as_still_needs_a_connection(client):
    _cid, _sid, base = seed(client)
    group(client, base, order="manual")
    client.delete("/api/llm-connections/openrouter")
    use(client, FakeLLM([reply("Mara answers.", None)]))
    response = client.post(base + "/chat", json={"speaker_ref": MARA})
    assert response.status_code == 409
    assert response.json()["kind"] == "missing_key"


def test_scene_without_settings_keeps_the_whole_cast_eligible(client):
    cid, sid, base = seed(client)
    fake = FakeLLM([['{"next":"characters:winifred"}'], reply("Winifred answers.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "error" not in response.text, response.text
    assert asked_selector(fake)
    record = latest_round(cid, sid)
    assert [e["ref"] for e in record["eligible"]] == [MARA, WINIFRED]
    assert record["mode"] == "directed" and record["plan"] == []


def test_directed_with_saved_defaults_keeps_full_roster(client):
    cid, sid, base = seed(client)
    group(client, base, order="directed", sitting_out=[], auto_rounds=0)
    fake = FakeLLM([['{"next":"characters:mara"}'], reply("Mara answers.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "error" not in response.text, response.text
    selector = "\n".join(m["content"] for m in fake.requests[0]["messages"])
    assert SELECTOR in selector
    assert MARA in selector and WINIFRED in selector
    assert [e["ref"] for e in latest_round(cid, sid)["eligible"]] == [MARA, WINIFRED]


def _legacy_presence(cid, sid, ref):
    """Drop `ref`'s presence interval for the scene, as cast that predates
    intervals has none, so the round is what must anchor it."""
    from grimoire.store.appearances import paths as appearance_paths

    data = appearance_paths.record(cid)
    data[ref.replace(":", "/", 1)].get("presence", {}).pop(sid, None)
    appearance_paths._write(cid, data)


def _presence(cid, sid, ref):
    from grimoire.store.appearances import paths as appearance_paths

    return appearance_paths.record(cid)[ref.replace(":", "/", 1)].get("presence", {}).get(sid)


def test_sitting_out_npc_still_gets_its_observation_anchor(client):
    cid, sid, base = seed(client)
    _legacy_presence(cid, sid, WINIFRED)
    assert _presence(cid, sid, WINIFRED) is None
    group(client, base, order="directed", sitting_out=[WINIFRED])
    use(client, FakeLLM([reply("Mara answers.", None)]))
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "error" not in response.text, response.text
    assert [e["ref"] for e in latest_round(cid, sid)["eligible"]] == [MARA]
    assert _presence(cid, sid, WINIFRED) == [{"start": 0, "end": None}]


def test_sitting_out_npc_anchored_by_a_continue_too(client):
    cid, sid, base = seed(client)
    _legacy_presence(cid, sid, WINIFRED)
    group(client, base, order="directed", sitting_out=[WINIFRED])
    use(client, FakeLLM([reply("Mara continues.", None)]))
    response = client.post(base + "/chat", json={"content": ""})
    assert "error" not in response.text, response.text
    assert _presence(cid, sid, WINIFRED) is not None
