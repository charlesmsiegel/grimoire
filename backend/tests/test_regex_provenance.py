"""Which connection served a reply is recorded on the reply (regex spec 3.1).

A connection-level output rule applies only to that connection's replies, so
the message has to say who wrote it. Stored beside the other response
metadata, and absent on anything written before it existed.
"""

import contextlib
import json

from grimoire import llm, routes, store
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.routes import character_turns
from grimoire.store.scenes import serialize
from tests.inference_fixtures import primary, put_settings
from tests.llm_fakes import FakeLLM, FlakyProvider


def seed(client):
    primary(client)
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    response = client.post(f"/api/campaigns/{cid}/characters", json={"name": "Mara"})
    assert response.status_code == 200, response.text
    actor = response.json()["character"]
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
    assert response.status_code == 200, response.text
    return cid, sid


def send(client, cid, sid, content="Hello"):
    response = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/chat",
        json={"content": content, "speaker_ref": "characters:mara"},
    )
    assert response.status_code == 200, response.text
    return response


def replies(cid, sid):
    return [m for m in store.scenes.read_scene(cid, sid)["messages"] if m.get("response_id")]


def active_variant(cid, sid, rid):
    ledger = json.loads((store.campaigns.paths.campaign_root(cid) / "responses.json").read_text())
    record = next(r for scope in ledger["scenes"].values() for k, r in scope["responses"].items()
                  if k == rid)
    return next(v for v in record["variants"] if v["id"] == record["active_variant"])


def test_chat_turn_records_connection(client):
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Mara answers."]])
    send(client, cid, sid)

    last = store.scenes.read_scene(cid, sid)["messages"][-1]
    assert last["connection"] == "openrouter"
    assert active_variant(cid, sid, last["response_id"])["connection"] == "openrouter"


def test_connection_survives_edit_and_cut(client):
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Mara answers."]])
    send(client, cid, sid, "one")
    send(client, cid, sid, "two")
    base = f"/api/campaigns/{cid}/scenes/{sid}/messages"
    assert [m["connection"] for m in replies(cid, sid)] == ["openrouter", "openrouter"]

    assert client.put(f"{base}/1", json={"content": "Mara answers, edited."}).status_code == 200
    assert client.put(f"{base}/0", json={"content": "A user post, edited."}).status_code == 200
    assert client.delete(f"{base}/3").status_code == 200   # the last reply

    survivors = replies(cid, sid)
    assert [m["content"] for m in survivors] == ["Mara answers, edited."]
    assert survivors[0]["connection"] == "openrouter"


def test_reroll_records_connection(client):
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Mara answers."]])
    send(client, cid, sid)
    rid = replies(cid, sid)[0]["response_id"]
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Mara, again."]])
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate")
    assert response.status_code == 200, response.text

    last = replies(cid, sid)[0]
    assert last["content"] == "Mara, again."
    assert last["connection"] == "openrouter"


def with_fallback(client, monkeypatch):
    """The real facade over a provider that fails its first attempt: with no
    retries the primary is exhausted at once and the fallback serves."""
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)
    backup = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "Backup", "api_key": "sk-backup"}).json()["id"]
    assert client.put("/api/config", json={"llm_retries": "0"}).status_code == 200
    put_settings(client, {"roles": {"primary": {"fallback": {
        "provider": backup, "model": "backup"}}}})
    provider = FlakyProvider(LLMError("rate_limit", "upstream is busy"), chunks=("Mara answers.",))
    client.app.dependency_overrides[routes.get_llm] = lambda: LLMClient(
        openrouter=provider, claude=provider, openai_compatible=provider,
        timeout=120, retries=store.config.llm_retries)  # each call carries its fallback
    return backup, provider


def test_fallback_records_serving_connection(client, monkeypatch):
    cid, sid = seed(client)
    backup, provider = with_fallback(client, monkeypatch)
    send(client, cid, sid)

    assert provider.calls == 2
    last = replies(cid, sid)[-1]
    assert last["connection"] == backup != "openrouter"
    assert active_variant(cid, sid, last["response_id"])["connection"] == backup


def test_a_rescued_partial_keeps_the_serving_connection(client, monkeypatch):
    """The terminal write fails after the fallback answered, so the rescue (not
    `_frames`) persists the partial -- and must file it under the fallback."""
    cid, sid = seed(client)
    backup, _ = with_fallback(client, monkeypatch)
    real_save, filed = character_turns._save, []

    def failing_once(*args, **kwargs):
        filed.append(args[-1] if len(args) > 10 else kwargs.get("connection"))
        if len(filed) == 1:
            raise RuntimeError("the terminal write failed")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(character_turns, "_save", failing_once)
    with contextlib.suppress(Exception):   # the rescue re-raises what broke the turn
        send(client, cid, sid)

    assert filed == [backup, backup]
    assert replies(cid, sid)[-1]["connection"] == backup


def test_legacy_stream_fallback_records_serving_connection(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    backup, provider = with_fallback(client, monkeypatch)
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    primary(client)
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Hello"})
    assert response.status_code == 200, response.text

    assert provider.calls == 2
    last = store.scenes.read_scene(cid, sid)["messages"][-1]
    assert last["content"] == "Mara answers."
    assert last["connection"] == backup != "openrouter"


def test_swipe_carries_variant_connection(client):
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Mara answers."]])
    send(client, cid, sid)
    first = replies(cid, sid)[0]
    rid = first["response_id"]
    original = active_variant(cid, sid, rid)["id"]

    other = store.responses.save_variant(
        cid, sid, rid, "Mara, elsewhere.", "complete", activate=False, connection="backup")
    store.responses.activate(cid, sid, rid, other["id"])
    assert replies(cid, sid)[0]["connection"] == "backup"

    store.responses.activate(cid, sid, rid, original)
    assert replies(cid, sid)[0]["connection"] == "openrouter"


def test_a_variant_with_no_connection_swipes_to_a_message_with_no_key(client):
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Mara answers."]])
    send(client, cid, sid)
    rid = replies(cid, sid)[0]["response_id"]
    legacy = store.responses.save_variant(
        cid, sid, rid, "Written before provenance.", "complete", activate=False)
    assert "connection" not in legacy

    store.responses.activate(cid, sid, rid, legacy["id"])
    assert "connection" not in replies(cid, sid)[0]


def test_legacy_stream_records_connection(client, monkeypatch):
    """The retired combined producer still persists through `_persist_reply`."""
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["The tide turns."]])
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Hello"})
    assert response.status_code == 200, response.text

    last = store.scenes.read_scene(cid, sid)["messages"][-1]
    assert last["content"] == "The tide turns."
    assert last["connection"] == "openrouter"
    # the player's own post is nobody's reply
    assert "connection" not in store.scenes.read_scene(cid, sid)["messages"][0]


def test_a_message_without_connection_serializes_unchanged():
    message = {"role": "assistant", "speaker": "Mara", "content": "Hi.", "response_id": "r1"}
    block = serialize._message_block(message)
    assert "connection" not in block
    assert block == ('**Mara:** <!-- grimoire-response {"response_id": "r1"} -->\nHi.\n')
    with_connection = serialize._message_block({**message, "connection": "backup"})
    assert '"connection": "backup"' in with_connection


ANSWERS = {"name": "Answers", "pattern": "answers", "replacement": "replies",
           "applies": ["display"]}


def shown_text(response) -> str:
    """What a client shows for a one-part body: deltas appended, display
    frames applied as `keep`/`tail`."""
    buffer = ""
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        frame = json.loads(line[len("data: "):])
        if "delta" in frame:
            buffer += frame["delta"]
        elif "display" in frame:
            buffer = buffer[:frame["display"]["keep"]] + frame["display"]["tail"]
    return buffer


def connection_rules(client, conn, *rules):
    response = client.put(f"/api/llm-connections/{conn}/regex", json={"rules": list(rules)})
    assert response.status_code == 200, response.text


def test_live_display_follows_the_serving_connection(client, monkeypatch):
    """After a fallback, the reply on screen is shaped by the fallback's own
    rules -- the ones its saved message is filed under -- not the failed
    primary's."""
    cid, sid = seed(client)
    backup, provider = with_fallback(client, monkeypatch)
    connection_rules(client, "openrouter", {**ANSWERS, "replacement": "WRONG"})
    connection_rules(client, backup, ANSWERS)
    response = send(client, cid, sid)

    assert provider.calls == 2
    assert shown_text(response) == "Mara replies."
    assert replies(cid, sid)[-1]["content"] == "Mara answers."


def test_live_display_follows_the_serving_connection_legacy(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    backup, _ = with_fallback(client, monkeypatch)
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    primary(client)
    connection_rules(client, "openrouter", {**ANSWERS, "replacement": "WRONG"})
    connection_rules(client, backup, ANSWERS)
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Hello"})
    assert response.status_code == 200, response.text
    assert shown_text(response) == "Mara replies."


def test_a_migrated_post_keeps_its_connection_through_a_swipe(client):
    cid, sid = seed(client)
    store.scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Old reply.",
                                          "connection": "openrouter"}])
    store.responses.migrate(cid, sid)
    rid = replies(cid, sid)[0]["response_id"]
    original = active_variant(cid, sid, rid)
    assert original["connection"] == "openrouter"

    other = store.responses.save_variant(
        cid, sid, rid, "Another reply.", "complete", activate=False, connection="backup")
    store.responses.activate(cid, sid, rid, other["id"])
    store.responses.activate(cid, sid, rid, original["id"])
    assert replies(cid, sid)[0]["connection"] == "openrouter"


def test_an_alternate_keeps_its_connection_through_a_promote(client):
    cid, sid = seed(client)
    store.scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "First take.",
                                          "connection": "openrouter"}])
    store.alternates.archive(cid, sid)
    store.scenes.remove_trailing_assistant_run(cid, sid)
    store.scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Second take.",
                                          "connection": "backup"}])
    store.alternates.promote(cid, sid, 0)
    last = store.scenes.read_scene(cid, sid)["messages"][-1]
    assert (last["content"], last["connection"]) == ("First take.", "openrouter")
    store.alternates.promote(cid, sid, 1)
    last = store.scenes.read_scene(cid, sid)["messages"][-1]
    assert (last["content"], last["connection"]) == ("Second take.", "backup")
