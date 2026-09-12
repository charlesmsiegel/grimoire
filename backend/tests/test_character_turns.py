"""Bounded individual response orchestration over the shared fake gateway."""

from grimoire import routes, store
from tests.llm_fakes import FakeLLM


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
    fake = FakeLLM([['{"next":null}']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Hello"})
    assert response.status_code == 200
    assert fake.calls == 1
    assert not any(m.get("response_id") for m in store.scenes.read_scene(cid, sid)["messages"])


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


def test_foreign_state_is_not_recorded(client):
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
    assert "FOREIGN_SENTINEL" not in str(store.turnstate.read(cid))
    assert "watchful" in str(store.turnstate.read(cid))


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
            ['{"next":"characters:mara"}'],
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
