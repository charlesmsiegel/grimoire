"""The character-turn engine following a scene's group-play speaker order."""

import asyncio
import json
import random
from itertools import pairwise
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


# ---- auto-continue rounds ----

SERAPHINE = "characters:seraphine"


def _run(cid, sid):
    return SimpleNamespace(
        id="test", scene_identity=store.scenes.scene_identity(cid, sid), cancel_requested=False)


def _drive(cid, sid, fake, run, round_record, on_frame):
    """Drive `_frames` directly, handing each decoded frame to `on_frame`."""
    token = streaming._claim_turn(cid, sid)

    async def collect():
        async for frame in character_turns._frames(
            cid, sid, fake, {"kind": "openrouter", "model": "test"}, run, token,
            round_record, streaming.StreamOutcome(),
        ):
            if frame.startswith("data: "):
                on_frame(json.loads(frame.removeprefix("data: ")))

    asyncio.run(collect())


def test_list_auto_rounds_stop_at_cap(client):
    cid, sid, base = seed(client)
    group(client, base, order="list", order_list=[MARA, WINIFRED], auto_rounds=1)
    fake = FakeLLM([reply("Mara one.", None), reply("Winifred one.", None),
                    reply("Mara two.", None), reply("Winifred two.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "error" not in response.text, response.text
    assert fake.calls == 4
    assert speakers(cid, sid) == ["Mara", "Winifred", "Mara", "Winifred"]
    assert response.text.count('"round_start": {"index": 2, "of": 2}') == 1
    assert response.text.count('"round_start"') == 1


def test_directed_null_handoff_ends_chain(client):
    cid, sid, base = seed(client)
    group(client, base, order="directed", auto_rounds=3)
    fake = FakeLLM([reply("Mara answers.", None), reply("Nobody else.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello", "speaker_ref": MARA})
    assert "error" not in response.text, response.text
    assert fake.calls == 1
    assert speakers(cid, sid) == ["Mara"]


def test_repeat_handoff_leads_next_round(client):
    cid, sid, base = seed(client)
    group(client, base, order="directed", auto_rounds=1)
    fake = FakeLLM([reply("Mara answers.", WINIFRED), reply("Winifred answers.", MARA),
                    reply("Mara again.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello", "speaker_ref": MARA})
    assert "error" not in response.text, response.text
    assert fake.calls == 3
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["speaker"] == "Mara"
    assert speakers(cid, sid) == ["Mara", "Winifred", "Mara"]
    prompt = "\n".join(m["content"] for m in fake.requests[1]["messages"])
    section = prompt.split("Eligible next speakers:", 1)[1].split("The current actor", 1)[0]
    assert MARA in section
    assert WINIFRED not in section


def test_repeat_handoff_without_rounds_is_rejected(client):
    cid, sid, base = seed(client)
    group(client, base, order="directed", auto_rounds=0)
    fake = FakeLLM([reply("Mara answers.", WINIFRED), reply("Winifred answers.", MARA)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello", "speaker_ref": MARA})
    assert "error" not in response.text, response.text
    assert fake.calls == 2
    assert speakers(cid, sid) == ["Mara", "Winifred"]
    assert latest_round(cid, sid)["issue"] == "repeated speaker"


def test_stop_zeroes_remaining_rounds_and_retry_starts_none(client):
    import pytest

    cid, sid, base = seed(client)
    group(client, base, order="list", order_list=[MARA, WINIFRED], auto_rounds=2)
    round_record = store.responses.new_round(
        cid, sid, eligible=character_turns.roster(cid, sid), automatic=True, post=None,
        run_id="test", actor_ref=MARA, mode="list", plan=[WINIFRED], auto_remaining=2,
        round_index=1, auto_total=2)
    run = _run(cid, sid)
    fake = FakeLLM([reply("Mara one.", None), reply("Winifred one.", None),
                    reply("Mara two.", None)])
    starts = []

    def on_frame(data):
        if "response_start" in data:
            starts.append(data["response_start"]["actor_ref"])
        if "delta" in data and len(starts) == 2:
            run.cancel_requested = True

    with pytest.raises(asyncio.CancelledError):
        _drive(cid, sid, fake, run, round_record, on_frame)
    assert fake.calls == 2
    pending = store.responses.unfinished(cid, sid)
    assert pending["auto_remaining"] == 0
    assert pending["plan"] == [] and pending["stopped"] is True
    retry = FakeLLM([reply("Winifred finishes.", None), reply("Must not run.", None)])
    use(client, retry)
    response = client.post(base + "/retry")
    assert "error" not in response.text, response.text
    assert retry.calls == 1
    assert "round_start" not in response.text
    assert speakers(cid, sid) == ["Mara", "Winifred"]


def test_stop_mid_round_retry_generates_only_the_interrupted_reply(client):
    import pytest

    cid, sid, base = seed(client)
    response = client.post(f"/api/campaigns/{cid}/characters", json={"name": "Seraphine"})
    assert response.status_code == 200, response.text
    actor = response.json()["character"]
    response = client.post(base + "/cast", json={"id": actor})
    assert response.status_code == 200, response.text
    group(client, base, order="list", order_list=[MARA, WINIFRED, SERAPHINE], auto_rounds=0)
    round_record = store.responses.new_round(
        cid, sid, eligible=character_turns.roster(cid, sid), automatic=True, post=None,
        run_id="test", actor_ref=MARA, mode="list", plan=[WINIFRED, SERAPHINE])
    run = _run(cid, sid)
    fake = FakeLLM([reply("Mara one.", None)])

    def on_frame(data):
        if "delta" in data:
            run.cancel_requested = True

    with pytest.raises(asyncio.CancelledError):
        _drive(cid, sid, fake, run, round_record, on_frame)
    retry = FakeLLM([reply("Mara finishes.", None), reply("Must not run.", None)])
    use(client, retry)
    response = client.post(base + "/retry")
    assert "error" not in response.text, response.text
    assert retry.calls == 1
    assert speakers(cid, sid) == ["Mara"]
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] == "Mara finishes."


def test_roll_pause_continues_chain_after_resolution(client):
    cid, sid, base = seed(client)
    group(client, base, order="list", order_list=[MARA, WINIFRED], auto_rounds=1)
    fake = FakeLLM([
        ['Wait.\n```roll\n{"check":"notice"}\n```'],
        reply("No roll.", None),
        reply("Winifred one.", None),
        reply("Mara two.", None),
        reply("Winifred two.", None),
    ])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert fake.calls == 1
    assert latest_round(cid, sid)["auto_remaining"] == 1
    proposal = store.proposals.get(cid, sid)
    response = client.post(
        base + "/roll-proposal", json={"proposal": proposal["id"], "action": "decline"})
    assert response.status_code == 200 and "error" not in response.text, response.text
    assert fake.calls == 5
    assert speakers(cid, sid) == ["Mara", "Mara", "Winifred", "Mara", "Winifred"]
    assert response.text.count('"round_start": {"index": 2, "of": 2}') == 1


def test_manual_never_auto_continues(client):
    cid, sid, base = seed(client)
    group(client, base, order="manual", auto_rounds=3)
    fake = FakeLLM([reply("Mara answers.", WINIFRED), reply("Nobody else.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello", "speaker_ref": MARA})
    assert "error" not in response.text, response.text
    assert fake.calls == 1
    assert speakers(cid, sid) == ["Mara"]


def test_follow_ups_fire_once_per_chain(client, monkeypatch):
    _cid, _sid, base = seed(client)
    group(client, base, order="list", order_list=[MARA, WINIFRED], auto_rounds=1)
    fired = []

    async def count(after_turn, outcome):
        fired.append(after_turn)

    monkeypatch.setattr(streaming, "_fire_follow_up", count)
    fake = FakeLLM([reply("Mara one.", None), reply("Winifred one.", None),
                    reply("Mara two.", None), reply("Winifred two.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "error" not in response.text, response.text
    assert fake.calls == 4
    assert len(fired) == 1


def test_follow_on_round_keeps_the_original_post(client):
    cid, sid, base = seed(client)
    group(client, base, order="list", order_list=[MARA, WINIFRED], auto_rounds=1)
    fake = FakeLLM([reply("Mara one.", None), reply("Winifred one.", None),
                    reply("Mara two.", None), reply("Winifred two.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "error" not in response.text, response.text
    rounds = list(store.responses._scope(cid, sid, store.responses._read(cid))["rounds"].values())
    assert [r["round_index"] for r in rounds] == [1, 2]
    assert [r["auto_remaining"] for r in rounds] == [1, 0]
    assert rounds[0]["post"] is not None and rounds[1]["post"] == rounds[0]["post"]
    assert rounds[1]["automatic"] and rounds[1]["note"]


def test_stop_in_directed_retry_ignores_the_interrupted_handoff(client):
    import pytest

    cid, sid, base = seed(client)
    round_record = store.responses.new_round(
        cid, sid, eligible=character_turns.roster(cid, sid), automatic=True, post=None,
        run_id="test", actor_ref=MARA)
    run = _run(cid, sid)
    fake = FakeLLM([reply("Mara one.", WINIFRED)])

    def on_frame(data):
        if "delta" in data:
            run.cancel_requested = True

    with pytest.raises(asyncio.CancelledError):
        _drive(cid, sid, fake, run, round_record, on_frame)
    retry = FakeLLM([reply("Mara finishes.", WINIFRED), reply("Must not run.", None)])
    use(client, retry)
    response = client.post(base + "/retry")
    assert "error" not in response.text, response.text
    assert retry.calls == 1
    assert speakers(cid, sid) == ["Mara"]


def _assert_nothing_to_retry(client, base):
    retry = FakeLLM([reply("Must not run.", None)])
    use(client, retry)
    response = client.post(base + "/retry")
    assert response.status_code == 409, response.text
    assert response.json()["kind"] == "nothing_to_retry"
    assert retry.calls == 0


def test_stop_at_round_start_of_a_selector_round_leaves_nothing_to_retry(client):
    cid, sid, base = seed(client)
    group(client, base, order="directed", auto_rounds=1)
    # A List round that ran out of plan continues the chain, and the follow-on
    # plans by the scene's order now: Directed, with no lead, so its first
    # speaker would come from the selector.
    round_record = store.responses.new_round(
        cid, sid, eligible=character_turns.roster(cid, sid), automatic=True, post=None,
        run_id="test", actor_ref=MARA, mode="list", plan=[], auto_remaining=1,
        round_index=1, auto_total=1)
    run = _run(cid, sid)
    fake = FakeLLM([reply("Mara one.", None), ['{"next":"characters:winifred"}'],
                    reply("Winifred one.", None)])

    def on_frame(data):
        if "round_start" in data:
            run.cancel_requested = True

    _drive(cid, sid, fake, run, round_record, on_frame)
    assert fake.calls == 1
    assert latest_round(cid, sid)["round_index"] == 2
    assert latest_round(cid, sid)["status"] == "complete"
    _assert_nothing_to_retry(client, base)
    assert speakers(cid, sid) == ["Mara"]


def test_stop_at_round_start_of_a_planned_round_leaves_nothing_to_retry(client):
    from contextlib import aclosing

    cid, sid, base = seed(client)
    group(client, base, order="list", order_list=[MARA, WINIFRED], auto_rounds=1)
    round_record = store.responses.new_round(
        cid, sid, eligible=character_turns.roster(cid, sid), automatic=True, post=None,
        run_id="test", actor_ref=MARA, mode="list", plan=[WINIFRED], auto_remaining=1,
        round_index=1, auto_total=1)
    run = _run(cid, sid)
    fake = FakeLLM([reply("Mara one.", None), reply("Winifred one.", None),
                    reply("Mara two.", None)])
    token = streaming._claim_turn(cid, sid)

    async def collect():
        frames = character_turns._frames(
            cid, sid, fake, {"kind": "openrouter", "model": "test"}, run, token,
            round_record, streaming.StreamOutcome())
        # Stop lands while the announcement is being delivered: the run is
        # closed at that yield, so the rescue path is what ends the round.
        async with aclosing(frames) as stream:
            async for frame in stream:
                if "round_start" in json.loads(frame.removeprefix("data: ")):
                    run.cancel_requested = True
                    break

    asyncio.run(collect())
    assert fake.calls == 2
    following = latest_round(cid, sid)
    assert following["round_index"] == 2 and following["actor_ref"] is None
    assert following["status"] == "complete" and following["stopped"] is True
    _assert_nothing_to_retry(client, base)
    assert speakers(cid, sid) == ["Mara", "Winifred"]


def test_handoff_text_says_a_repeat_starts_a_new_round(client):
    _cid, _sid, base = seed(client)
    group(client, base, order="directed", auto_rounds=1)
    fake = FakeLLM([reply("Mara answers.", WINIFRED), reply("Winifred answers.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello", "speaker_ref": MARA})
    assert "error" not in response.text, response.text
    first, second = ("\n".join(m["content"] for m in r["messages"]) for r in fake.requests)
    # Mara speaks first: nobody has responded yet, so today's sentence stands.
    assert "The current actor and everyone who has already responded are excluded." in first
    assert "begins another round" not in first
    # Winifred is offered Mara, who already spoke.
    assert "begins another round of the conversation" in second
    assert "everyone who has already responded are excluded" not in second


def test_handoff_text_is_unchanged_without_rounds(client):
    _cid, _sid, base = seed(client)
    fake = FakeLLM([reply("Mara answers.", WINIFRED), reply("Winifred answers.", None)])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello", "speaker_ref": MARA})
    assert "error" not in response.text, response.text
    second = "\n".join(m["content"] for m in fake.requests[1]["messages"])
    assert "The current actor and everyone who has already responded are excluded." in second
    assert "begins another round" not in second


def test_list_chain_survives_replies_without_handoff(client):
    # List ignores the handoff, so a reply that writes none is no reason to
    # end the chain: both rounds run.
    cid, sid, base = seed(client)
    group(client, base, order="list", order_list=[MARA, WINIFRED], auto_rounds=1)
    fake = FakeLLM([["Mara one."], ["Winifred one."], ["Mara two."], ["Winifred two."]])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "error" not in response.text, response.text
    assert fake.calls == 4
    assert speakers(cid, sid) == ["Mara", "Winifred", "Mara", "Winifred"]
    # The issue is still recorded on the variant, as before.
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    record = store.responses.get(cid, sid, rid, private=True)
    variant = next(v for v in record["variants"] if v["id"] == record["active_variant"])
    assert variant["issue"] == "missing or invalid handoff"


def test_natural_chain_survives_replies_without_handoff(client):
    _cid, _sid, base = seed(client)
    group(client, base, order="natural", talkativeness={MARA: 100, WINIFRED: 100},
          auto_rounds=1)
    fake = FakeLLM([["One."], ["Two."], ["Three."], ["Four."]])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "error" not in response.text, response.text
    assert fake.calls == 4


def test_list_chain_stops_on_authority_violation(client):
    cid, sid, base = seed(client)
    group(client, base, order="list", order_list=[MARA, WINIFRED], auto_rounds=1)
    fake = FakeLLM([["Mara one."], ["Winifred one.\n\n**Mara:** I speak for her."],
                    ["Mara two."], ["Winifred two."]])
    use(client, fake)
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert "error" not in response.text, response.text
    assert fake.calls == 2
    assert speakers(cid, sid) == ["Mara", "Winifred"]


def test_natural_follow_on_never_repeats_the_last_speaker(client, monkeypatch):
    # Round 2 answers round 1's last contribution; its writer must not open it.
    cid, _sid, _base = seed(client)
    for n in range(8):
        # A fresh seed per planning call, so the two rounds shuffle apart.
        seeds = iter(range(n * 10, n * 10 + 10))
        monkeypatch.setattr(character_turns, "_rng", lambda seeds=seeds: random.Random(next(seeds)))
        sid = store.scenes.create_scene(cid, f"Round {n}")
        base = f"/api/campaigns/{cid}/scenes/{sid}"
        for actor in ("mara", "winifred"):
            cast = client.post(base + "/cast", json={"id": actor})
            assert cast.status_code == 200, cast.text
        group(client, base, order="natural", talkativeness={MARA: 100, WINIFRED: 100},
              auto_rounds=1)
        fake = FakeLLM([["One."], ["Two."], ["Three."], ["Four."]])
        use(client, fake)
        response = client.post(base + "/chat", json={"content": "Hello"})
        assert "error" not in response.text, response.text
        said = speakers(cid, sid)
        assert len(said) == 4
        assert all(a != b for a, b in pairwise(said)), (n, said)


def _chain_with_rounds_changed_to(client, rounds):
    """A List chain started with two follow-on rounds whose `auto_rounds` is
    set to `rounds` as round 1's first contribution ends."""
    cid, sid, _base = seed(client)
    settings = {**group_play.parse(""), "order": "list", "order_list": [MARA, WINIFRED],
                "auto_rounds": 2}
    store.scenes.set_group(cid, sid, group_play.dump(settings))
    round_record = store.responses.new_round(
        cid, sid, eligible=character_turns.roster(cid, sid), automatic=True, post=None,
        run_id="test", actor_ref=MARA, mode="list", plan=[WINIFRED], auto_remaining=2,
        round_index=1, auto_total=2)
    fake = FakeLLM([reply(f"Line {n}.", None) for n in range(6)])
    frames = []

    def on_frame(frame):
        frames.append(frame)
        if "response_end" in frame and len(frames) < 4:
            store.scenes.set_group(
                cid, sid, group_play.dump({**settings, "auto_rounds": rounds}))

    _drive(cid, sid, fake, _run(cid, sid), round_record, on_frame)
    return fake, [f["round_start"] for f in frames if "round_start" in f]


def test_auto_rounds_lowered_to_zero_mid_chain_starts_no_follow_on(client):
    fake, starts = _chain_with_rounds_changed_to(client, 0)
    assert fake.calls == 2
    assert starts == []


def test_auto_rounds_lowered_mid_chain_clamps_the_remaining_rounds(client):
    fake, starts = _chain_with_rounds_changed_to(client, 1)
    assert fake.calls == 4
    assert starts == [{"index": 2, "of": 2}]
