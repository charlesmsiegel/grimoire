"""A paused character turn validates its transcript before dice are adjudicated."""

import pytest
from fastapi import HTTPException

from grimoire import store
from grimoire.routes import mechanics


@pytest.fixture
def paused_round(client):
    wid = store.worlds.create_world("Realm")
    root = store.worlds.world_root(wid)
    store.characters.create_character(root, "Mara", "default",
                                      store.characters.blank_card("Mara"))
    cid = store.campaigns.create_campaign("Saltmarch", wid, module="pool-basic")
    sid = store.scenes.create_scene(cid, "Arrival")
    store.appearances.appear(cid, sid, "characters", "mara", "default", "npc")
    store.sheets.write(cid, "characters", "mara", "medium",
                       {"vigor": 3, "brawl": 2}, expected=None)
    store.scenes.append_message(cid, sid, "user", "Wait for my signal.")
    record = store.responses.new_round(
        cid, sid, eligible=[{"ref": "characters:mara", "name": "Mara"}],
        automatic=True, post=0, run_id="run-one", actor_ref="characters:mara")
    proposal = store.proposals.new(cid, sid, {"check": "brawl", "actor": "characters:mara",
                                             "difficulty": 6, "modifier": 0}, 0)
    record = store.responses.update_round(cid, sid, record["id"], status="paused",
                                          proposal_id=proposal["id"])
    return cid, sid, record, proposal


def test_paused_round_rejects_same_length_history_edit_before_adjudication(paused_round):
    cid, sid, _, proposal = paused_round
    store.scenes.edit_message(cid, sid, 0, "Strike now.")
    with pytest.raises(HTTPException) as error:
        mechanics._paused_response_round(cid, sid, proposal)
    assert error.value.status_code == 409
    assert store.proposals.get(cid, sid)["status"] == "pending"
    assert store.rolls.read(cid) == []


def test_paused_round_accepts_unchanged_history(paused_round):
    cid, sid, record, proposal = paused_round
    assert mechanics._paused_response_round(cid, sid, proposal)["id"] == record["id"]


def test_projection_crash_recovers_only_the_exact_proposals_roll_line(paused_round):
    cid, sid, record, proposal = paused_round
    pid = proposal["id"]
    assert store.proposals.claim(cid, sid, pid)
    result = store.checks.resolve_check(cid, "brawl", "characters:mara", 6, 0)
    assert store.proposals.transition(cid, sid, pid, ("resolving",), "resolved", result)
    resolution = store.proposals.project(cid, sid, pid)
    # Simulate process exit before the character round watermark catches up.
    fresh = store.proposals.get(cid, sid)
    assert mechanics._paused_response_round(cid, sid, fresh)["id"] == record["id"]
    assert resolution["line_intent"] == record["watermark"]
    store.scenes.append_message(cid, sid, "assistant", "An unrelated arrival.")
    with pytest.raises(HTTPException) as error:
        mechanics._paused_response_round(cid, sid, fresh)
    assert error.value.status_code == 409


def test_changed_paused_round_refuses_accept_before_any_dice_or_provider_call(client, paused_round):
    cid, sid, _, proposal = paused_round
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})
    store.scenes.edit_message(cid, sid, 0, "Strike now.")
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal",
                           json={"proposal": proposal["id"], "action": "accept"})
    assert response.status_code == 409
    assert store.proposals.get(cid, sid)["status"] == "pending"
    assert store.rolls.read(cid) == []


@pytest.mark.parametrize("endpoint,body", [
    ("roll", {"notation": "1d6"}),
    ("check", {"check": "brawl", "actor": "characters:mara", "difficulty": 6}),
])
def test_manual_mechanics_lock_survives_cutting_its_transcript_line(client, paused_round, endpoint, body):
    cid, sid, _, _ = paused_round
    store.scenes.append_message(cid, sid, "assistant", "Mara waits.", speaker="Mara")
    rid = store.responses.migrate(cid, sid)[-1]["id"]
    at = len(store.scenes.read_scene(cid, sid)["messages"])
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/{endpoint}", json=body)
    assert response.status_code == 200
    assert store.responses.get(cid, sid, rid, private=True).get("mechanically_locked")
    store.scenes.delete_from(cid, sid, at)
    assert store.rolls.read(cid), "the audit still exists after a transcript cut"
    with pytest.raises(store.responses.ResponseConflict) as error:
        store.responses.delete(cid, sid, rid)
    assert error.value.kind == "applied_mechanics"
