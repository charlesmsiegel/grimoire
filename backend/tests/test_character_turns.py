"""Bounded individual response orchestration over the shared fake gateway."""

import pytest

from grimoire import routes, store
from tests.inference_fixtures import SAME_PROVIDER, SPARE, decide_only, format2, put_settings
from tests.llm_fakes import FakeLLM, decision_reply


def seed(client, module=None):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid, module=module)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in ("Mara", "Winifred"):
        response = client.post(f"/api/campaigns/{cid}/characters", json={"name": name})
        assert response.status_code == 200, response.text
        actor = response.json()["character"]
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert response.status_code == 200, response.text
    return cid, sid


def test_round_rejects_repeated_actor(client):
    cid, sid = seed(client)
    fake = FakeLLM(
        [
            ['Mara answers.\n```handoff\n{"next":"characters:winifred"}\n```'],
            ['Winifred answers.\n```handoff\n{"next":"characters:mara"}\n```'],
        ]
    )
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    response = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/chat",
        json={"content": "Hello", "speaker_ref": "characters:mara"},
    )
    assert response.status_code == 200
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert [m.get("speaker") for m in messages if m.get("response_id")] == ["Mara", "Winifred"]
    shown = client.get(f"/api/campaigns/{cid}/scenes/{sid}", params={"limit": 2}).json()
    assert [m.get("actor_ref") for m in shown["messages"]] == [
        "characters:mara", "characters:winifred"]
    assert "actor_ref" not in store.scenes.read_scene(cid, sid)["messages"][-1]
    assert fake.calls == 2


def test_explicit_continue_does_not_follow_handoff(client):
    cid, sid = seed(client)
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":"characters:winifred"}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    response = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"speaker_ref": "characters:mara"}
    )
    assert response.status_code == 200
    assert fake.calls == 1
    assert (
        len([m for m in store.scenes.read_scene(cid, sid)["messages"] if m.get("response_id")]) == 1
    )


def test_selector_can_stop(client):
    cid, sid = seed(client)
    fake = FakeLLM([[decision_reply({"next": None})]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Hello"})
    assert response.status_code == 200
    assert fake.calls == 1
    assert not any(m.get("response_id") for m in store.scenes.read_scene(cid, sid)["messages"])


def test_old_combined_setting_cannot_disable_individual_generation(client):
    cid, sid = seed(client)
    store.config.write_config(character_response_mode="combined")
    fake = FakeLLM([
        [decision_reply({"next": "characters:mara"})],
        ['Mara answers.\n```handoff\n{"next":null}\n```'],
    ])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Hello"})
    assert response.status_code == 200
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert [m["speaker"] for m in messages if m.get("response_id")] == ["Mara"]
    assert fake.calls == 2


def test_failure_second_preserves_first_and_retry_only_unfinished(client):
    from grimoire.llm_errors import LLMError

    cid, sid = seed(client)
    fake = FakeLLM(
        [['First.\n```handoff\n{"next":"characters:winifred"}\n```'], ["Partial."]],
        error=LLMError("rate_limit", "Wait"),
        fail_after=1,
    )
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    response = client.post(
        base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"}
    )
    assert "rate_limit" in response.text
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-2]["content"] == "First."
    first_id = messages[-2]["response_id"]
    assert messages[-1]["response_status"] == "incomplete"
    retry = FakeLLM([['Finished.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: retry
    response = client.post(base + "/retry")
    assert "error" not in response.text, response.text
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-2]["response_id"] == first_id
    assert messages[-2]["content"] == "First."
    assert messages[-1]["content"] == "Finished."
    assert retry.calls == 1


def test_middle_reroll_frozen_and_later_retained(client):
    cid, sid = seed(client)
    fake = FakeLLM(
        [
            ['First.\n```handoff\n{"next":"characters:winifred"}\n```'],
            ['Future sentinel.\n```handoff\n{"next":null}\n```'],
        ]
    )
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    messages = store.scenes.read_scene(cid, sid)["messages"]
    rid = messages[-2]["response_id"]
    later = messages[-1]["response_id"]
    retry = FakeLLM([['Replacement.\n```handoff\n{"next":"characters:winifred"}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: retry
    result = client.post(base + f"/responses/{rid}/regenerate", json={"guidance": "Short."})
    assert "error" not in result.text, result.text
    assert retry.calls == 1
    assert "Future sentinel" not in str(retry.noted)
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-2]["content"] == "Replacement."
    assert messages[-1]["response_id"] == later and messages[-1]["context_changed"]
    record = client.get(base + f"/responses/{rid}").json()
    assert len(record["variants"]) == 2 and "snapshot" not in record


def test_roll_decline_continues_same_actor_then_handoff(client):
    cid, sid = seed(client)
    fake = FakeLLM(
        [
            ['Wait.\n```roll\n{"check":"notice"}\n```'],
            ['No roll.\n```handoff\n{"next":"characters:winifred"}\n```'],
            ['Answer.\n```handoff\n{"next":null}\n```'],
        ]
    )
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    response = client.post(
        base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"}
    )
    assert fake.calls == 1
    before = store.scenes.read_scene(cid, sid)["messages"][-1]
    proposal = store.proposals.get(cid, sid)
    response = client.post(
        base + "/roll-proposal", json={"proposal": proposal["id"], "action": "decline"}
    )
    assert response.status_code == 200 and "error" not in response.text, response.text
    messages = store.scenes.read_scene(cid, sid)["messages"]
    parts = [m for m in messages if m.get("response_id") == before["response_id"]]
    assert [m["content"] for m in parts] == ["Wait.", "No roll."]
    assert messages[-1]["speaker"] == "Winifred"
    assert fake.calls == 3


def test_accepted_roll_is_before_same_response_outcome_and_never_reapplied(client):
    cid, sid = seed(client, module="pool-basic")
    store.sheets.write(cid, "characters", "mara", "medium", {"vigor": 3, "brawl": 2}, expected=None)
    fake = FakeLLM(
        [
            ['Before.\n```roll\n{"check":"brawl","actor":"characters:mara","difficulty":6}\n```'],
            ['After.\n```handoff\n{"next":null}\n```'],
        ]
    )
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    proposal = store.proposals.get(cid, sid)
    body = {"proposal": proposal["id"], "action": "accept"}
    response = client.post(base + "/roll-proposal", json=body)
    assert "error" not in response.text, response.text
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-3]["content"] == "Before."
    assert messages[-2]["speaker"] == store.scenes.ROLL_SPEAKER
    assert messages[-1]["content"] == "After."
    assert messages[-3]["response_id"] == messages[-1]["response_id"]
    client.post(base + "/roll-proposal", json=body)
    assert fake.calls == 2 and len(store.rolls.read(cid)) == 1
    delete = client.delete(base + f"/responses/{messages[-1]['response_id']}")
    assert delete.status_code == 409 and delete.json()["kind"] == "applied_mechanics"


def test_completed_response_crash_retry_never_replays_it(client, monkeypatch):
    from grimoire.routes import character_turns

    cid, sid = seed(client)
    original = character_turns._round_state
    failed = False

    def fail_once(*args, **fields):
        nonlocal failed
        if fields.get("used") and not failed:
            failed = True
            raise RuntimeError("simulated process interruption after response publication")
        return original(*args, **fields)

    monkeypatch.setattr(character_turns, "_round_state", fail_once)
    first = FakeLLM([['Saved.\n```handoff\n{"next":"characters:winifred"}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: first
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    assert first.calls == 1
    retry = FakeLLM([['Successor.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: retry
    response = client.post(base + "/retry")
    assert "error" not in response.text, response.text
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert [m["content"] for m in messages if m.get("response_id")] == ["Saved.", "Successor."]
    assert retry.calls == 1


def test_roll_proposal_exists_before_prose_publication(client, monkeypatch):
    cid, sid = seed(client)
    original = store.responses.save_variant
    observed = []

    def check(*args, **kwargs):
        observed.append(store.proposals.get(cid, sid))
        return original(*args, **kwargs)

    monkeypatch.setattr(store.responses, "save_variant", check)
    fake = FakeLLM([['Wait.\n```roll\n{"check":"notice"}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/chat",
        json={"content": "Hello", "speaker_ref": "characters:mara"},
    )
    assert observed and all(p and p["status"] == "pending" for p in observed)


def test_authority_violation_preserves_own_prose_stops_handoff(client):
    cid, sid = seed(client)
    fake = FakeLLM(
        [
            [
                'Own prose.\n\n**You:** Invented player action.\n\n**Winifred:** Other dialogue.\n```handoff\n{"next":"characters:winifred"}\n```'
            ]
        ]
    )
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/chat",
        json={"content": "Hello", "speaker_ref": "characters:mara"},
    )
    assert fake.calls == 1
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["content"] == "Own prose."
    assert messages[-1]["speaker"] == "Mara"
    record = store.responses.get(cid, sid, messages[-1]["response_id"])
    assert record["variants"][-1]["issue"]


def test_single_npc_skips_selector_and_stop_blocks_successor(client, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace

    from grimoire.routes import character_turns, streaming

    cid, sid = seed(client)
    round_record = store.responses.new_round(
        cid,
        sid,
        eligible=character_turns.roster(cid, sid),
        automatic=True,
        post=None,
        run_id="test",
        actor_ref="characters:mara",
    )
    run = SimpleNamespace(
        id="test", scene_identity=store.scenes.scene_identity(cid, sid), cancel_requested=False
    )
    token = streaming._claim_turn(cid, sid)
    fake = FakeLLM([['First.\n```handoff\n{"next":"characters:winifred"}\n```']])

    async def collect():
        async for frame in character_turns._frames(
            cid,
            sid,
            fake,
            {"kind": "openrouter", "model": "test"},
            run,
            token,
            round_record,
            streaming.StreamOutcome(),
        ):
            if "response_end" in json.loads(frame.removeprefix("data: ")):
                run.cancel_requested = True

    asyncio.run(collect())
    assert fake.calls == 1


def test_one_present_npc_needs_no_selector(client):
    cid, sid = seed(client)
    store.appearances.leave(cid, sid, "characters", "winifred")
    fake = FakeLLM([['Only Mara.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Hello"})
    assert "error" not in result.text, result.text
    assert fake.calls == 1
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["speaker"] == "Mara"


def test_a_legacy_state_block_is_stripped_and_nothing_records_it(client):
    cid, sid = seed(client)
    fake = FakeLLM(
        [
            [
                'Wait.\n```state\n{"Mara":{"mood":"watchful"},"Winifred":{"mood":"FOREIGN_SENTINEL"}}\n```\n```handoff\n{"next":null}\n```'
            ]
        ]
    )
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/chat",
        json={"content": "Hello", "speaker_ref": "characters:mara"},
    )
    assert "error" not in result.text, result.text
    stored = str(store.scenes.read_scene(cid, sid)["messages"])
    assert "Wait." in stored
    assert "```state" not in stored
    assert "FOREIGN_SENTINEL" not in stored
    assert "watchful" not in stored


def test_declined_roll_whole_response_reroll_removes_old_parts(client):
    cid, sid = seed(client)
    fake = FakeLLM(
        [
            ['Before.\n```roll\n{"check":"notice"}\n```'],
            ['After.\n```handoff\n{"next":null}\n```'],
            ['Replacement.\n```handoff\n{"next":null}\n```'],
        ]
    )
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    proposal = store.proposals.get(cid, sid)
    client.post(base + "/roll-proposal", json={"proposal": proposal["id"], "action": "decline"})
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    old = store.responses.get(cid, sid, rid)
    assert old["variants"][-1]["content"] == "Before.\n\nAfter."
    result = client.post(base + f"/responses/{rid}/regenerate", json={})
    assert "error" not in result.text, result.text
    parts = [
        m for m in store.scenes.read_scene(cid, sid)["messages"] if m.get("response_id") == rid
    ]
    assert [m["content"] for m in parts] == ["Replacement."]
    response = client.post(base + f"/responses/{rid}/variants/{old['active_variant']}/activate")
    assert response.status_code == 200, response.text
    assert store.responses.get(cid, sid, rid)["content"] == "Before.\n\nAfter."


def test_reroll_wrong_actor_cannot_create_a_second_message(client):
    cid, sid = seed(client)
    fake = FakeLLM(
        [
            ['Original.\n```handoff\n{"next":null}\n```'],
            [
                'Safe.\n\n**Winifred:** Not hers.\n\n**You:** Not mine.\n```handoff\n{"next":null}\n```'
            ],
        ]
    )
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    result = client.post(base + f"/responses/{rid}/regenerate", json={})
    assert "error" not in result.text, result.text
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["content"] == "Safe." and messages[-1]["speaker"] == "Mara"
    assert len([m for m in messages if m.get("response_id")]) == 1


def test_frozen_reroll_failure_retains_previous_variant(client):
    from grimoire.llm_errors import LLMError

    cid, sid = seed(client)
    first = FakeLLM([['Original.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: first
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    fake = FakeLLM([["Partial."]], error=LLMError("rate_limit", "Wait"))
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(base + f"/responses/{rid}/regenerate", json={})
    assert "rate_limit" in result.text
    record = store.responses.get(cid, sid, rid)
    assert record["content"] == "Original." and len(record["variants"]) == 1


def test_selector_and_reroll_capture_and_meter_each_actual_call(client):
    cid, sid = seed(client)
    fake = FakeLLM(
        [
            [decision_reply({"next": "characters:mara"})],
            ['Original.\n```handoff\n{"next":null}\n```'],
            ['Replacement.\n```handoff\n{"next":null}\n```'],
        ]
    )
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello"})
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    client.post(base + f"/responses/{rid}/regenerate", json={"guidance": "Short"})
    captures = store.prompt_log.list_entries(cid, sid)
    assert {c["task"] for c in captures} >= {"response-selector", "chat", "regenerate"}
    rows = [row for row in store.usage.calls(campaign=cid) if row.get("round_id")]
    assert len(rows) == 3 and len({row["round_id"] for row in rows}) == 1
    assert all(row.get("post") is not None for row in rows)
    assert len([row for row in rows if row.get("response_id") == rid]) == 2


def test_response_survives_rename_and_delete_cleans_its_snapshot(client):
    import pytest

    cid, sid = seed(client)
    fake = FakeLLM([['Original.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/chat",
        json={"content": "Hello", "speaker_ref": "characters:mara"},
    )
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    record = store.responses.get(cid, sid, rid, private=True)
    renamed = store.scenes.rename_scene(cid, sid, "Winifred")
    assert store.responses.get(cid, renamed, rid)["content"] == "Original."
    store.scenes.delete_scene(cid, renamed)
    with pytest.raises(FileNotFoundError):
        store.response_snapshots.read(cid, record["snapshot_ref"])


def test_empty_reply_is_incomplete_and_retryable(client):
    cid, sid = seed(client)
    fake = FakeLLM([['```handoff\n{"next":"characters:winifred"}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/chat",
        json={"content": "Hello", "speaker_ref": "characters:mara"},
    )
    assert "empty_response" in result.text
    assert fake.calls == 1 and store.responses.unfinished(cid, sid)["status"] == "incomplete"


def test_legacy_opener_replay_and_cancel_preserve_identity(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "assistant", "Original opener.")
    rid = store.responses.migrate(cid, sid)[0]["id"]
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    assert client.get(base + "/replay/preview?index=0").status_code == 200
    store.replay.begin(cid, sid, 0)
    store.replay.cancel(cid, restore=True)
    assert store.scenes.read_scene(cid, sid)["messages"][0]["response_id"] == rid
    store.replay.begin(cid, sid, 0)
    fake = FakeLLM([['New opener.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(base + "/replay/turn")
    assert "error" not in result.text, result.text
    assert store.scenes.read_scene(cid, sid)["messages"][0]["content"] == "New opener."
    assert fake.calls == 1


def test_stop_on_final_delta_preserves_pending_response_for_retry(client):
    import asyncio
    import json
    from types import SimpleNamespace

    import pytest

    from grimoire.routes import character_turns, streaming

    cid, sid = seed(client)
    round_record = store.responses.new_round(
        cid, sid, eligible=character_turns.roster(cid, sid), automatic=True,
        post=None, run_id="test", actor_ref="characters:mara")
    run = SimpleNamespace(id="test", scene_identity=store.scenes.scene_identity(cid, sid),
                          cancel_requested=False)
    token = streaming._claim_turn(cid, sid)
    fake = FakeLLM([['First.\n```handoff\n{"next":"characters:winifred"}\n```']])

    async def collect():
        async for frame in character_turns._frames(
            cid, sid, fake, {"kind": "openrouter", "model": "test"}, run, token,
            round_record, streaming.StreamOutcome()):
            if "delta" in json.loads(frame.removeprefix("data: ")):
                run.cancel_requested = True

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(collect())
    pending = store.responses.unfinished(cid, sid)
    assert pending["status"] == "incomplete"
    assert pending["actor_ref"] == "characters:mara" and pending["used"] == []
    record = store.responses.get(cid, sid, pending["pending_response"])
    assert record["status"] == "incomplete" and record["content"] == "First."
    assert fake.calls == 1


def test_handoff_prompt_offers_only_remaining_slots(client):
    cid, sid = seed(client)
    fake = FakeLLM([
        ['"Ready."\n```handoff\n{"next":"characters:winifred"}\n```'],
        ['"So am I."\n```handoff\n{"next":"grimoire"}\n```'],
        ['The door opens.\n```handoff\n{"next":null}\n```'],
    ])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                         json={"content": "Are you both ready?", "speaker_ref": "characters:mara"})
    assert result.status_code == 200 and fake.calls == 3
    candidates = [request["messages"][0]["content"].split("Eligible next speakers:\n", 1)[1]
                  .split("\n\n", 1)[0] for request in fake.requests]
    assert "characters:mara" not in candidates[0]
    assert "characters:winifred" in candidates[0] and "grimoire" in candidates[0]
    assert "characters:mara" not in candidates[1] and "characters:winifred" not in candidates[1]
    assert "grimoire" in candidates[1]
    assert not any(ref in candidates[2] for ref in ("characters:mara", "characters:winifred", "grimoire"))


def test_explicit_single_response_has_no_successor_candidates(client):
    cid, sid = seed(client)
    fake = FakeLLM([['"Ready."\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                         json={"speaker_ref": "characters:mara"})
    assert result.status_code == 200 and fake.calls == 1
    candidates = fake.requests[0]["messages"][0]["content"].split("Eligible next speakers:\n", 1)[1]
    candidates = candidates.split("\n\n", 1)[0]
    assert "characters:" not in candidates and "grimoire" not in candidates


def test_narrator_scope_reaches_selector_and_writer_after_npc_turn(client):
    cid, sid = seed(client)
    fake = FakeLLM([
        [decision_reply({"next": "characters:mara"})],
        ['"Ready."\n```handoff\n{"next":"grimoire"}\n```'],
        ['The door opens.\n```handoff\n{"next":null}\n```'],
    ])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                         json={"content": "Is it time?"})
    assert result.status_code == 200 and fake.calls == 3
    # The selector's criteria are its decide item's question, in the user
    # message; the system message is the decide contract's.
    selector = fake.requests[0]["messages"][1]["content"]
    actor, narrator = [r["messages"][0]["content"] for r in fake.requests[1:]]
    assert "An established NPC's actions or physical reactions belong to that NPC" in selector
    assert "Do not select Grimoire to extend an established NPC's turn" in actor
    assert "Do not select Grimoire to extend an established NPC's turn" in narrator
    assert "Each established NPC owns their speech, actions, physical reactions and decisions" in narrator
    assert "genuinely new characters" in narrator
    # Already-used Mara is still protected by the roster, even though she is
    # no longer offered as a successor; an unused NPC can still respond.
    roster, candidates = narrator.split("Present people:", 1)[1].split("Eligible next speakers:", 1)
    assert "characters:mara" in roster and "characters:winifred" in roster
    assert "characters:mara" not in candidates and "characters:winifred" in candidates


# --- the speaker pick through decide() on the Decision role (slice F) ------

def _round(cid, sid):
    rounds = store.responses._scope(cid, sid, store.responses._read(cid))["rounds"]
    return list(rounds.values())[-1]


def _speakers(cid, sid):
    return [m["speaker"] for m in store.scenes.read_scene(cid, sid)["messages"]
            if m.get("response_id")]


def _chat(client, cid, sid, content="Hello"):
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": content})
    assert response.status_code == 200, response.text
    return response


def test_the_selector_meters_one_row_with_its_round(client):
    """`decide()` opens the pick's meter, and the row still carries the
    player post and the round it opened -- what attributes the pick's cost to
    the post it answered -- plus how the decision was served."""
    cid, sid = seed(client)
    fake = FakeLLM([[decision_reply({"next": None})]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    _chat(client, cid, sid)
    record = _round(cid, sid)
    (row,) = [r for r in store.usage.calls(campaign=cid) if r.get("task") == "response-selector"]
    assert (row["post"], row["round_id"]) == (record["post"], record["id"])
    assert row["post"] is not None and row["round_id"]
    assert (row["operation"], row["decision_mode"]) == ("decide", "structured")
    (schema,) = fake.schemas
    assert schema is not None


def test_the_selector_capture_is_the_decide_prompt(client):
    """The prompt log records what was sent (spec 9.4, ruling 10): the decide
    contract's system message with the reply's schema, and the item's context
    and question -- not the retired one-call prompt."""
    cid, sid = seed(client)
    fake = FakeLLM([[decision_reply({"next": None})]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    _chat(client, cid, sid, "Winifred, the lamps.")
    (entry,) = [e for e in store.prompt_log.list_entries(cid, sid)
                if e["task"] == "response-selector"]
    captured = store.prompt_log.read_entry(cid, entry["id"], scene=sid)
    assert captured is not None
    sent = fake.requests[0]["messages"]
    assert [row["text"] for row in captured["sections"]] == [m["content"] for m in sent]
    system, user = sent
    assert '"next"' in system["content"] and "characters:mara" in system["content"]
    assert "Observable transcript:" in user["content"]
    assert "Winifred, the lamps." in user["content"]
    assert "Choose at most one initial speaker" in user["content"]
    assert "Available NPCs:" not in system["content"] + user["content"]


@pytest.mark.parametrize("on", [SPARE, SAME_PROVIDER], ids=["spare", "same-provider"])
def test_the_speaker_on_a_decide_only_model_answers_on_the_fallback(client, on):
    """Review Focus 1: a Decision model that cannot generate is skipped for a
    role fallback that can. The pick is asked of the fallback, nothing is
    sent to the decide-only model, and the actor still writes on Primary --
    the fallback on the decide-only model's own provider too (spec I-1)."""
    cid, sid = seed(client)
    decide_only(client, fallback=True, on=on)
    fake = FakeLLM([[decision_reply({"next": "characters:mara"})],
                    ['Mara answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    _chat(client, cid, sid)
    pick, actor = fake.requests
    assert (pick["conn"]["id"], pick["conn"]["model"]) == on
    assert all(r["conn"].get("model") != "vendor/decider" for r in fake.requests)
    assert actor["conn"]["id"] == "openrouter"
    assert _speakers(cid, sid) == ["Mara"]


def _tracker_runs(client, cid, sid):
    ident = store.scenes.scene_identity(cid, sid)
    return [r for r in client.app.state.runs.for_subject(("scene", cid, ident))
            if r.kind == "tracker-update"]


@pytest.mark.tracker
@pytest.mark.parametrize("body", [
    {"content": "Hello there"},
    {"content": "Somebody steer this.", "director": True},
    {"content": ""},
], ids=["post", "note", "continue"])
def test_the_speaker_on_a_decide_only_model_without_a_fallback_is_refused(client, body):
    """With no generating fallback the seam refuses the pick (spec 5.3's 409
    `incapable`, naming the Decision role), as it refuses scene-break and
    voice drift: nothing is sent, neither the pick nor an actor turn.

    And the refusal comes BEFORE anything is written (`post_chat`'s
    reserved-before-the-first-mutator rule): a 409 tells the player nothing
    happened, so the post, the director note, the pending roll proposal and
    the tracker are all as they were -- and sending again adds no copy."""
    cid, sid = seed(client)
    decide_only(client, fallback=False)
    store.proposals.new(cid, sid, {"check": "brawl"})
    before = store.scenes.read_scene(cid, sid)["messages"]
    fake = FakeLLM([["must not be sent"]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    for _ in range(2):
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json=body)
        assert response.status_code == 409, response.text
        got = response.json()
        assert got["kind"] == "incapable"
        assert got["detail"].startswith("The Next speaker route runs on the Decision role "
                                        "(vendor/decider on OpenRouter)"), got["detail"]
    assert fake.calls == 0
    assert store.scenes.read_scene(cid, sid)["messages"] == before
    assert store.proposals.get(cid, sid)["status"] == "pending"
    assert _tracker_runs(client, cid, sid) == []


def test_a_scene_with_one_speaker_is_not_refused_over_the_pick(client):
    """The refusal is for a pick: with one NPC present nobody is picked, so a
    decide-only Decision model with no fallback refuses nothing."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    actor = client.post(f"/api/campaigns/{cid}/characters",
                        json={"name": "Mara"}).json()["character"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"id": actor}).status_code == 200
    decide_only(client, fallback=False)
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    _chat(client, cid, sid)
    assert _speakers(cid, sid) == ["Mara"]


def test_a_pick_refused_inside_the_run_takes_the_post_back(client, monkeypatch):
    """The request-time check is the one that answers; `start` still resolves
    the pick as a defence (settings can change between the two). If THAT one
    refuses, the post it was answering comes back off, as a failed turn's
    does, so the 409 still means nothing happened."""
    from grimoire.routes import character_turns

    cid, sid = seed(client)
    decide_only(client, fallback=False)
    monkeypatch.setattr(character_turns, "refuse_an_unanswerable_pick",
                        lambda *a, **k: None)
    before = store.scenes.read_scene(cid, sid)["messages"]
    fake = FakeLLM([["must not be sent"]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                           json={"content": "Hello there"})
    assert response.status_code == 409, response.text
    assert response.json()["kind"] == "incapable"
    assert fake.calls == 0
    assert store.scenes.read_scene(cid, sid)["messages"] == before


def test_a_provider_error_at_the_pick_fails_the_turn_and_is_filed_once(client):
    """The pick's `LLMError` is the turn's, as it was before the switch: the
    turn fails with that kind, `decide()`'s meter files exactly one error row
    under `response-selector`, and no actor is asked to write."""
    from grimoire.llm_errors import LLMError

    cid, sid = seed(client)
    fake = FakeLLM([[""]], error=LLMError("rate_limit", "Wait"))
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Hello"})
    assert "rate_limit" in response.text, response.text
    assert fake.calls == 1
    rows = [r for r in store.usage.calls(campaign=cid) if r.get("task") == "response-selector"]
    assert [(r["status"], r["error"]) for r in rows] == [("error", "rate_limit")]
    assert [r for r in store.usage.calls(campaign=cid)
            if r.get("task") != "response-selector"] == []
    assert not any(m.get("response_id") for m in store.scenes.read_scene(cid, sid)["messages"])


def test_the_decision_role_now_serves_the_speaker(client):
    """The `speaker` route's default flipped from Fast to Decision: a Decision
    role set on its own is what the pick runs on, and the turn stays where it
    was."""
    cid, sid = seed(client)
    format2(client)
    put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "spare", "model": "vendor/spare"}}}})
    fake = FakeLLM([[decision_reply({"next": "characters:mara"})],
                    ['Mara answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    _chat(client, cid, sid)
    pick, actor = fake.requests
    assert (pick["conn"]["id"], pick["conn"]["model"]) == ("spare", "vendor/spare")
    assert (actor["conn"]["id"], actor["conn"]["model"]) == ("openrouter", "vendor/active")


def test_a_fenced_selector_reply_now_reads(client):
    """A gate "beats" entry, end to end: today's parse refused a reply wrapped
    in a code fence as a missing handoff; `decide()` reads the object inside
    it, so the named NPC answers."""
    cid, sid = seed(client)
    fenced = "```json\n" + decision_reply({"next": "characters:winifred"}) + "\n```"
    fake = FakeLLM([[fenced], ['Winifred answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    _chat(client, cid, sid)
    assert fake.calls == 2 and _round(cid, sid)["issue"] is None
    assert _speakers(cid, sid) == ["Winifred"]


def test_an_off_roster_selection_keeps_the_ineligible_issue(client):
    """An answer naming nobody the round offers is today's ineligible issue,
    never a guess and never the invalid-handoff one: control returns to the
    player and no actor is called."""
    cid, sid = seed(client)
    fake = FakeLLM([[decision_reply({"next": "pcs:seraphine"})]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    _chat(client, cid, sid)
    record = _round(cid, sid)
    assert record["issue"] == store.response_protocol.INELIGIBLE
    assert record["actor_ref"] is None and record["status"] == "complete"
    assert fake.calls == 1


def test_a_colliding_roster_raises_an_issue_not_a_500(client, monkeypatch, caplog):
    """Two eligible refs that read as one once normalised are a request
    `decide()` refuses before anything is sent (plan Minor 15). The round
    carries today's invalid-handoff issue -- control returns to the player --
    rather than the turn failing, and the refusal is logged against the
    campaign and scene."""
    import logging

    from grimoire.routes import character_turns

    cid, sid = seed(client)
    monkeypatch.setattr(character_turns, "roster", lambda _cid, _sid: [
        {"ref": "characters:mara", "name": "Mara"},
        {"ref": "Characters:Mara", "name": "Mara again"}])
    fake = FakeLLM([["must not be sent"]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    with caplog.at_level(logging.WARNING, logger="grimoire.character_turns"):
        response = _chat(client, cid, sid)
    assert "error" not in response.text, response.text
    record = _round(cid, sid)
    assert record["issue"] == store.response_protocol.INVALID_HANDOFF
    assert record["actor_ref"] is None and record["status"] == "complete"
    assert fake.calls == 0
    assert [r for r in store.usage.calls(campaign=cid)
            if r.get("task") == "response-selector"] == []
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(cid in m and sid in m for m in warned), warned


PERCEPTION_REPLY = (
    '```perception\n{"known": [], "heard_or_seen": [], "unknown": []}\n```\n'
    'Mara answers.\n```handoff\n{"next":null}\n```'
)


def _chat_mara(client, cid, sid):
    return client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/chat",
        json={"content": "Hello", "speaker_ref": "characters:mara"},
    )


def test_perception_block_is_stripped_whatever_the_rider_setting(client):
    # The setting gates only the prompt instruction; the watcher always strips
    # a leading fence, which is harmless when nothing asked for one.
    for rider in ("on", "off"):
        store.config.write_config(perception_rider=rider)
        cid, sid = seed(client)
        client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([[PERCEPTION_REPLY]])
        assert _chat_mara(client, cid, sid).status_code == 200
        messages = store.scenes.read_scene(cid, sid)["messages"]
        assert messages[-1]["content"] == "Mara answers."


def test_reroll_strips_a_fence_the_frozen_prompt_asked_for(client):
    # The reroll prompt is a frozen snapshot taken with the rider on; switching
    # it off afterwards must not leave the fence in the stored variant.
    store.config.write_config(perception_rider="on")
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([[PERCEPTION_REPLY]])
    assert _chat_mara(client, cid, sid).status_code == 200
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    store.config.write_config(perception_rider="off")
    retry = FakeLLM([[PERCEPTION_REPLY.replace("Mara answers.", "Replacement.")]])
    client.app.dependency_overrides[routes.get_llm] = lambda: retry
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    result = client.post(base + f"/responses/{rid}/regenerate", json={})
    assert "error" not in result.text, result.text
    assert retry.calls == 1
    content = store.scenes.read_scene(cid, sid)["messages"][-1]["content"]
    assert content == "Replacement."
    record = client.get(base + f"/responses/{rid}").json()
    assert not any("perception" in str(v) for v in record["variants"])
