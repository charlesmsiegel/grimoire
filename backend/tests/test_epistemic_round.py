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
