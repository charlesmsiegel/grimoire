"""11-S3 phase C: the round driver gathers a view for each step's speaker.

An NPC that was present for an earlier scene gets what it saw, under its own
heading, and never the narrator's annotations or another speaker's view.
"""

from grimoire import routes, store
from grimoire.routes import history_recall
from tests.history_fixtures import absorbed, actor, appear, played
from tests.llm_fakes import FakeLLM


def test_an_npc_that_was_there_gets_its_own_history(client, monkeypatch):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    campaign = store.campaigns.create_campaign("Saltmarch", wid)
    store.config.write_config(history_recall_depth="2", recap_depth="0", prompt_log_depth="50")
    old = store.scenes.create_scene(campaign, "The Pier")
    sid = store.scenes.create_scene(campaign, "The Harbour")
    aid, vid = actor(campaign, sid, "Mara")
    appear(campaign, old, aid, vid)
    store.scenes.append_message(campaign, old, "user", "Seraphine hands Mara the silver ledger.")
    absorbed(campaign, old, "WITHHELDSUMMARY: the ledger was forged.")
    hidden = played(campaign, title="The Vault", text="Winifred hides a second silver ledger.")
    absorbed(campaign, hidden, "Winifred hides a second silver ledger.")
    seen = []
    real = history_recall.gather_view

    async def gather(*a, **k):
        seen.append(k["actor_ref"])
        return await real(*a, **k)
    monkeypatch.setattr(history_recall, "gather_view", gather)
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    reply = client.post(f"/api/campaigns/{campaign}/scenes/{sid}/chat",
                        json={"content": "Where is the silver ledger?",
                              "speaker_ref": "characters:" + aid})
    assert reply.status_code == 200 and '"error"' not in reply.text, reply.text
    assert seen == ["characters:" + aid]
    sent = str(fake.messages)
    assert "What Mara remembers or knows" in sent
    assert "Seraphine hands Mara the silver ledger." in sent
    for needle in ("WITHHELDSUMMARY", "Winifred hides", "Known to:", "# Earlier in the campaign"):
        assert needle not in sent, needle
    # Her turn's capture carries her view's classification, basis and all.
    entries = store.prompt_log.list_entries(campaign, sid)
    capture = store.prompt_log.read_entry(campaign, entries[-1]["id"])
    row = next(s for s in capture["sections"] if s["id"] == "history_known")
    assert row["history"]["epistemic"]["perspective"] == "actor:characters:" + aid
    assert "presence_interval" in str(row["history"]["epistemic"])


def test_a_captured_turns_fingerprints_are_what_the_accept_sends(client):
    """Phase E: the capture carries each classified post's fingerprint, as the
    Knowledge ledger computes it, so an accept from a past turn is stored --
    and refused as `moved` once the post changed since that turn."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    campaign = store.campaigns.create_campaign("Saltmarch", wid)
    store.config.write_config(history_recall_depth="2", recap_depth="0", prompt_log_depth="50")
    old = store.scenes.create_scene(campaign, "The Pier")
    sid = store.scenes.create_scene(campaign, "The Harbour")
    aid, vid = actor(campaign, sid, "Mara")
    appear(campaign, old, aid, vid)
    store.scenes.append_message(campaign, old, "user", "Seraphine hands Mara the silver ledger.",
                                post_id="a" * 32)
    absorbed(campaign, old, "The silver ledger changes hands.")
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    reply = client.post(f"/api/campaigns/{campaign}/scenes/{sid}/chat",
                        json={"content": "The silver ledger?", "speaker_ref": "characters:" + aid})
    assert reply.status_code == 200, reply.text
    entries = store.prompt_log.list_entries(campaign, sid)
    capture = store.prompt_log.read_entry(campaign, entries[-1]["id"])
    row = next(s for s in capture["sections"] if s["id"] == "history_known")
    detail = row["history"]["epistemic"]
    assert detail["names"]["characters:" + aid] == "Mara"
    [item] = detail["items"]
    assert item["scene"] == old and item["by_ref"]["characters:" + aid]["0"][0] == "witnessed"
    [piece] = [s for s in item["slices"] if s["part"] == "posts"]
    keys = piece["keys"]
    assert keys and keys[0]["key"] == "p-" + "a" * 32 and keys[0]["shown"]
    body = {"audience": "characters:" + aid, "status": "knows", "scene": item["scene"],
            "posts": [{"key": k["key"], "part": k["part"]} for k in keys],
            "whole_scene": False, "expect": keys, "expect_digest": "", "expect_shown": "",
            "source": "accepted"}
    base = f"/api/campaigns/{campaign}/ledger/knowledge"
    assert client.post(base, json=body).status_code == 200
    store.scenes.edit_message(campaign, old, 0, "Seraphine keeps the silver ledger.")
    moved = client.post(base, json={**body, "status": "suspects"})
    assert moved.status_code == 409 and moved.json()["kind"] == "moved"
