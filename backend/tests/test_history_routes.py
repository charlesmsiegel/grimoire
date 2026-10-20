"""Narrator history is gathered before composition, never under its lock."""

import asyncio
import importlib.util
import threading
import time
from concurrent.futures import CancelledError as PortalCancelledError
from dataclasses import replace
from types import SimpleNamespace

import anyio
import pytest

from grimoire import routes, store
from grimoire.llm_errors import LLMError
from grimoire.routes import character_turns, history_recall
from grimoire.routes.character_turns import _rounds as character_turns_rounds
from grimoire.store.context import epistemic, layout
from grimoire.store.history import model, query, retrieval
from tests.history_fixtures import absorbed, actor, played
from tests.history_fixtures import cid as cid
from tests.inference_fixtures import embedding
from tests.llm_fakes import FakeAsyncEmbeddings, FakeLLM


def test_async_retrieval_and_route_bridge_exist():
    assert callable(getattr(store.history, "retrieve", None))
    assert importlib.util.find_spec("grimoire.routes.history_recall") is not None


@pytest.mark.parametrize("off", ["depth", "layout"])
def test_off_never_prepares_reads_history_or_retrieves(cid, monkeypatch, off):
    sid = played(cid)
    if off == "depth":
        store.config.write_config(history_recall_depth="0")
    else:
        store.config.write_config(prompt_layout_enabled="on")
        layout.write_layout([{"id": "history_recall", "enabled": False}])
    monkeypatch.setattr(query, "prepare", lambda *a, **k: pytest.fail("off prepared history"))
    monkeypatch.setattr(store.history, "retrieve", lambda *a, **k: pytest.fail("off retrieved"))
    ev = asyncio.run(history_recall.gather(None, cid, sid, seed="", resolved=None,
                                         llm_client=None, embed_client=None))
    assert not ev.items and not ev.candidates


def test_gather_refills_depth_one_before_composition(cid, monkeypatch):
    store.config.write_config(history_recall_depth="1", recap_depth="0")
    usable = played(cid, text="silver ledger")
    absorbed(cid, usable)
    first = played(cid, text="nothing matching")
    absorbed(cid, first)
    sid = played(cid, text="x " * 5000 + "silver ledger")
    real = store.history.retrieve
    seen = []
    async def capture(*args, **kwargs):
        assert not store.locks.holds_campaign(cid)
        seen.append(kwargs["summary_omissions"])
        return await real(*args, **kwargs)
    monkeypatch.setattr(store.history, "retrieve", capture)
    ev = asyncio.run(history_recall.gather(None, cid, sid, seed="", resolved=None,
                                         llm_client=None, embed_client=None))
    assert seen == [{first: "omitted:archive", usable: "omitted:archive"}]
    assert [i.scene.sid for i in ev.items] == [usable]
    monkeypatch.setattr(retrieval, "retrieve_preview", lambda *a, **k: pytest.fail("compose retrieved"))
    _messages, detail = store.context.compose_turn(cid, sid, history_view=epistemic.narrator_view(cid, sid, ev))
    assert next(s for s in detail["sections"] if s["id"] == "history_recall")["history"]["items"][0]["scene"] == usable


def test_bridge_checks_calling_worker_lock_before_portal(cid):
    portal = SimpleNamespace(call=lambda *a, **k: pytest.fail("locked bridge crossed portal"))
    app = SimpleNamespace(state=SimpleNamespace(run_portal=portal))
    with store.locks.campaign_lock(cid):
        ev = history_recall.gather_sync(app, cid, "001--saltmarch", seed="", resolved=None,
                                        llm_client=None, embed_client=None)
    assert ev.coverage.semantic == "skipped:locked" and not ev.items
    assert ev.coverage.verdict == "error"


def test_bridge_closed_portal_degrades_to_error(cid):
    def closed(*a, **k):
        raise RuntimeError("portal closed")
    app = SimpleNamespace(state=SimpleNamespace(run_portal=SimpleNamespace(call=closed)))
    ev = history_recall.gather_sync(app, cid, "001--saltmarch", seed="", resolved=None,
                                    llm_client=None, embed_client=None)
    assert ev.coverage.verdict == "error"


def test_gather_failure_and_cancellation(cid, monkeypatch, caplog):
    sid = played(cid)
    async def fail(*a, **k):
        raise RuntimeError("private content must not be logged")
    monkeypatch.setattr(store.history, "retrieve", fail)
    ev = asyncio.run(history_recall.gather(None, cid, sid, seed="", resolved=None,
                                         llm_client=None, embed_client=None))
    assert ev.coverage.verdict == "error"
    assert "private content" not in caplog.text and cid in caplog.text and sid in caplog.text
    async def cancel(*a, **k):
        raise asyncio.CancelledError
    monkeypatch.setattr(store.history, "retrieve", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(history_recall.gather(None, cid, sid, seed="", resolved=None,
                                         llm_client=None, embed_client=None))


@pytest.mark.parametrize("kwargs", [{"perspective": "actor"}, {"scopes": frozenset({"invalid"})}, {"tier_limit": 3}])
def test_async_retrieval_refuses_invalid_turn_query(cid, kwargs):
    q = replace(model.Query(("silver ledger",)), **kwargs)
    with pytest.raises(ValueError):
        asyncio.run(store.history.retrieve(cid, "001--saltmarch", q, ceiling=1200,
                                           embed_client=None, deadline=time.monotonic()+4))


def test_expired_file_phase_keeps_evidence_without_network(cid, monkeypatch):
    old = played(cid)
    absorbed(cid, old)
    sid = played(cid)
    ev = asyncio.run(store.history.retrieve(cid, sid, query.build(cid, sid), ceiling=1200,
                                            embed_client=None, deadline=time.monotonic()-1))
    # No Embedding role: there was no semantic stage to skip.
    assert ev.items and ev.coverage.semantic == "off"
    monkeypatch.setattr(store.embed_space, "endpoint", lambda: {"space": "history-test"})
    unsendable = SimpleNamespace(embed=lambda *a, **k: pytest.fail("sent past the deadline"))
    ev = asyncio.run(store.history.retrieve(cid, sid, query.build(cid, sid), ceiling=1200,
                                            embed_client=unsendable, deadline=time.monotonic()-1))
    assert ev.items and ev.coverage.semantic == "skipped:deadline"


def route_seed(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    campaign = store.campaigns.create_campaign("Saltmarch", wid)
    old = played(campaign)
    absorbed(campaign, old)
    carried = played(campaign, title="Realm", text="nothing matching")
    absorbed(campaign, carried)
    sid = store.scenes.create_scene(campaign, "Saltmarch")
    store.config.write_config(history_recall_depth="1", recap_depth="0", prompt_log_depth="50")
    return campaign, sid


@pytest.mark.parametrize("door", ["chat", "director", "retry", "regenerate", "group", "group_director", "continue", "legacy_continue"])
def test_designated_routes_inject_native_client_and_attribute_embed(client, monkeypatch, door):
    campaign, sid = route_seed(client)
    group = door in {"group", "group_director", "continue"}
    monkeypatch.setattr(character_turns, "enabled", lambda: group)
    conn = store.llm_connections.create_connection(
        "openai_compatible", "Vectors", base_url="https://vectors.example/v1",
        api_key="sk-fake", model="", post_process="none")
    embedding(conn, "embed-1")
    native = FakeAsyncEmbeddings()
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.app.dependency_overrides[routes.get_embeddings] = lambda: native
    if door in {"retry", "regenerate", "continue", "legacy_continue"}:
        store.scenes.append_message(campaign, sid, "user", "silver ledger")
    if door == "regenerate":
        store.scenes.append_message(campaign, sid, "assistant", "Mara waits.")
    body = {"content": "" if door in {"continue", "legacy_continue"} else "silver ledger",
            "director": door in {"director", "group_director"}}
    if group:
        body["speaker_ref"] = "grimoire"
    url = "chat" if door in {"group", "group_director", "continue", "legacy_continue", "director"} else door
    reply = client.post(f"/api/campaigns/{campaign}/scenes/{sid}/{url}", json=body)
    assert reply.status_code == 200 and '"error"' not in reply.text, reply.text
    assert len(native.calls) == 1 and native.queries == [1]
    rows = [r for r in store.usage.calls(days=1) if r["task"] == "history-recall"]
    assert len(rows) == 1
    assert rows[0]["campaign"] == campaign and rows[0]["scene"] == sid and rows[0]["run_id"]
    assert rows[0].get("post") == (None if door in {"continue", "legacy_continue"} else 0)


@pytest.mark.parametrize("director", [False, True])
def test_group_narrator_gathers_and_captures_frozen_evidence(client, monkeypatch, director):
    campaign, sid = route_seed(client)
    fake = FakeLLM([['Mara keeps the ledger.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    seen = []
    real = query.prepare
    def prepared(*args, **kwargs):
        assert not store.locks.holds_campaign(campaign)
        seen.append(kwargs["seed"])
        return real(*args, **kwargs)
    monkeypatch.setattr(query, "prepare", prepared)
    reply = client.post(f"/api/campaigns/{campaign}/scenes/{sid}/chat",
                        json={"content": "silver ledger", "speaker_ref": "grimoire", "director": director})
    assert reply.status_code == 200 and '"error"' not in reply.text, reply.text
    assert seen == (["silver ledger"] if director else [""])
    assert "# Earlier in the campaign" in str(fake.messages)
    entries = store.prompt_log.list_entries(campaign, sid)
    capture = store.prompt_log.read_entry(campaign, entries[-1]["id"])
    section = next(s for s in capture["sections"] if s["id"] == "history_recall")
    assert section["history"]["items"][0]["scene"] == min(r["id"] for r in store.scenes.list_scenes(campaign))
    assert section["history"]["items"][0]["summary"] == "omitted:archive"
    assert fake.calls == 1


@pytest.mark.parametrize("door", ["chat", "director", "retry", "regenerate"])
def test_legacy_compose_doors_gather_without_lock(client, monkeypatch, door):
    campaign, sid = route_seed(client)
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    fake = FakeLLM([["Mara keeps the ledger."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    seen = []
    real = history_recall.gather_view_sync
    def gather(*args, **kwargs):
        assert not store.locks.holds_campaign(campaign)
        seen.append(kwargs["seed"])
        return real(*args, **kwargs)
    monkeypatch.setattr(history_recall, "gather_view_sync", gather)
    base = f"/api/campaigns/{campaign}/scenes/{sid}"
    if door in ("retry", "regenerate"):
        store.scenes.append_message(campaign, sid, "user", "silver ledger")
    if door == "regenerate":
        store.scenes.append_message(campaign, sid, "assistant", "Mara waits.")
    reply = client.post(base + "/" + ("chat" if door == "director" else door),
                        json={"content": "silver ledger", "director": door == "director"})
    assert reply.status_code == 200 and '"error"' not in reply.text, reply.text
    assert seen == (["silver ledger"] if door == "director" else [""])
    assert "# Earlier in the campaign" in str(fake.messages)
    entries = store.prompt_log.list_entries(campaign, sid)
    capture = store.prompt_log.read_entry(campaign, entries[-1]["id"])
    section = next(s for s in capture["sections"] if s["id"] == "history_recall")
    assert section["history"]["items"][0]["scene"] == min(r["id"] for r in store.scenes.list_scenes(campaign))
    assert section["history"]["items"][0]["summary"] == "omitted:archive"
    assert fake.calls == 1


def test_round_views_cached_per_round_and_speaker(cid, monkeypatch):
    # 11-S3: each step gathers for its own perspective, once per (round,
    # speaker). A director note seeds only the narrator's query.
    sid = played(cid)
    app = object()
    turn = character_turns._Progress({"id": "round-1", "note": "silver ledger"}, (), None)
    calls = []
    async def gather(*a, **k):
        calls.append((k["seed"], k["actor_ref"]))
        return epistemic.narrator_view(cid, sid, model.Evidence(ceiling=len(calls)))
    monkeypatch.setattr(history_recall, "gather_view", gather)
    async def run():
        npc = await character_turns._round_history(app, cid, sid, None, None, None, turn, "characters:mara")
        first = await character_turns._round_history(app, cid, sid, None, None, None, turn, "grimoire")
        again = await character_turns._round_history(app, cid, sid, None, None, None, turn, "grimoire")
        npc_again = await character_turns._round_history(app, cid, sid, None, None, None, turn, "characters:mara")
        assert npc is not first and first is again and npc is npc_again
        turn.round_record = {"id": "round-2", "note": ""}
        second = await character_turns._round_history(app, cid, sid, None, None, None, turn, "grimoire")
        assert second is not first
        # A roll continuation retrieves nothing, but its second half is
        # composed with the view the round already gathered for that speaker...
        turn.appended = (("Roll", "system", "result"),)
        assert await character_turns._round_history(app, cid, sid, None, None, None, turn, "grimoire") is second
        assert await character_turns._round_history(app, cid, sid, None, None, None, turn, "characters:mara") is None
        turn.appended = ()
        turn.continuation = "roll"
        assert await character_turns._round_history(app, cid, sid, None, None, None, turn, "grimoire") is second
        # ...and a resume in a later request, which gathered nothing, has none.
        turn.round_record = {"id": "round-3", "note": ""}
        assert await character_turns._round_history(app, cid, sid, None, None, None, turn, "grimoire") is None
    asyncio.run(run())
    assert calls == [("", "characters:mara"), ("silver ledger", None), ("", None)]


def test_each_step_carries_its_rounds_history_cost(cid, monkeypatch):
    # Spec 11 §14 Q7: what recall per speaker costs a round is on the row.
    sid = played(cid)
    turn = character_turns._Progress({"id": "round-1", "note": ""}, (), None)
    semantics = iter(["ok", "skipped:deadline", "failed:network"])

    async def gather(*a, **k):
        coverage = model.Coverage("thin", semantic=next(semantics))
        return epistemic.narrator_view(cid, sid, model.Evidence(coverage=coverage))
    monkeypatch.setattr(history_recall, "gather_view", gather)

    async def run():
        return [await character_turns._round_history(object(), cid, sid, None, None, None,
                                                     turn, actor)
                for actor in ("grimoire", "characters:mara", "characters:win")]
    views = asyncio.run(run())
    assert [v.round_cost for v in views] == [{"retrievals": 1, "embeds": 1},
                                            {"retrievals": 2, "embeds": 1},
                                            {"retrievals": 3, "embeds": 2}]
    assert views[2].detail()["round"] == {"retrievals": 3, "embeds": 2}


@pytest.mark.parametrize("stale_peek", [False, True])
def test_pending_retry_and_regenerate_replay_without_retrieval(client, monkeypatch, stale_peek):
    campaign, sid = route_seed(client)
    fake = FakeLLM([["Partial."]], error=LLMError("rate_limit", "Wait"))
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{campaign}/scenes/{sid}"
    failed = client.post(base + "/chat", json={"content": "silver ledger", "speaker_ref": "grimoire"})
    assert "rate_limit" in failed.text
    frozen = fake.messages
    assert "# Earlier in the campaign" in str(frozen)
    store.config.write_config(history_recall_depth="0", history_recall_budget="1")
    old = next(r["id"] for r in store.scenes.list_scenes(campaign) if r["id"] < sid)
    absorbed(campaign, old, "CHANGED_HISTORY_SENTINEL")
    gathers = []
    if stale_peek:
        # A peek that missed the replay, for a recall still on as far as the
        # driver knows: the gather is wasted, and the snapshot still wins.
        monkeypatch.setattr(history_recall, "enabled", lambda: True)
        monkeypatch.setattr(character_turns_rounds, "_history_replays", lambda *a: False)
        async def stale(*a, **k):
            gathers.append(True)
        monkeypatch.setattr(history_recall, "gather_view", stale)
    else:
        monkeypatch.setattr(history_recall, "gather_view", lambda *a, **k: pytest.fail("snapshot retrieved"))
    finish = FakeLLM([['Finished.\n```handoff\n{"next":null}\n```'], ["Rerolled."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: finish
    retry = client.post(base + "/retry")
    assert retry.status_code == 200 and '"error"' not in retry.text, retry.text
    regen = client.post(base + "/regenerate")
    assert regen.status_code == 200 and '"error"' not in regen.text, regen.text
    assert finish.requests[0]["messages"] == frozen
    assert finish.requests[1]["messages"] == frozen
    assert gathers == ([True] if stale_peek else [])


def test_bridge_propagates_cancellation(cid):
    def cancelled(*a, **k):
        raise asyncio.CancelledError
    app = SimpleNamespace(state=SimpleNamespace(run_portal=SimpleNamespace(call=cancelled)))
    with pytest.raises(asyncio.CancelledError):
        history_recall.gather_sync(app, cid, "001--saltmarch", seed="", resolved=None,
                                   llm_client=None, embed_client=None)


def test_bridge_degrades_a_portal_cancelled_at_shutdown(cid):
    """Spec 10.1 (M7): a portal closing at shutdown cancels its pending
    futures; the player's post is already appended, so that is an error
    verdict, never an exception into the route."""
    def cancelled(*a, **k):
        raise PortalCancelledError
    app = SimpleNamespace(state=SimpleNamespace(run_portal=SimpleNamespace(call=cancelled)))
    ev = history_recall.gather_sync(app, cid, "001--saltmarch", seed="", resolved=None,
                                    llm_client=None, embed_client=None)
    assert ev.coverage.verdict == "error" and not ev.items


def test_portal_failure_after_player_append_does_not_strand_turn(client, monkeypatch):
    campaign, sid = route_seed(client)
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    fake = FakeLLM([["Mara answers."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    original = client.app.state.run_portal.call
    def call(fn, *args):
        if getattr(fn, "func", None) is history_recall.gather:
            raise RuntimeError("portal closed for history")
        return original(fn, *args)
    monkeypatch.setattr(client.app.state.run_portal, "call", call)
    reply = client.post(f"/api/campaigns/{campaign}/scenes/{sid}/chat", json={"content": "silver ledger"})
    assert reply.status_code == 200 and '"error"' not in reply.text, reply.text
    assert [m["content"] for m in store.scenes.read_scene(campaign, sid)["messages"]] == ["silver ledger", "Mara answers."]


def test_roll_continuation_never_gathers(client, monkeypatch):
    campaign, sid = route_seed(client)
    fake = FakeLLM([['Wait.\n```roll\n{"check":"notice"}\n```'],
                    ['After.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{campaign}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "silver ledger", "speaker_ref": "grimoire"})
    proposal = store.proposals.get(campaign, sid)
    monkeypatch.setattr(history_recall, "gather_view", lambda *a, **k: pytest.fail("roll continuation retrieved"))
    reply = client.post(base + "/roll-proposal", json={"proposal": proposal["id"], "action": "decline"})
    assert reply.status_code == 200 and '"error"' not in reply.text, reply.text
    assert fake.calls == 2


def test_async_file_stages_run_in_worker(cid, monkeypatch):
    sid = played(cid)
    caller = threading.get_ident()
    threads = []
    real = retrieval._prepare
    def files(*a, **k):
        threads.append(threading.get_ident())
        return real(*a, **k)
    monkeypatch.setattr(retrieval, "_prepare", files)
    asyncio.run(store.history.retrieve(cid, sid, query.build(cid, sid), ceiling=1200,
                                      embed_client=None, deadline=time.monotonic()+4))
    assert threads and all(t != caller for t in threads)


def test_an_npc_contribution_gathers_its_own_view(client, monkeypatch):
    # 11-C2: an NPC step retrieves for its own perspective, and its prompt
    # holds only what that view allows -- here nothing, for Mara was in none
    # of the earlier scenes.
    campaign, sid = route_seed(client)
    aid, _vid = actor(campaign, sid, "Mara")
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    seen = []
    real = history_recall.gather_view
    async def gather(*a, **k):
        seen.append(k["actor_ref"])
        view = await real(*a, **k)
        assert view.perspective.actor == "characters:"+aid
        return view
    monkeypatch.setattr(history_recall, "gather_view", gather)
    reply = client.post(f"/api/campaigns/{campaign}/scenes/{sid}/chat",
                        json={"content": "silver ledger", "speaker_ref": "characters:"+aid})
    assert reply.status_code == 200 and '"error"' not in reply.text, reply.text
    assert seen == ["characters:"+aid]
    assert "# Earlier in the campaign" not in str(fake.messages)
    assert "Mara gives Seraphine" not in str(fake.messages)


def test_explicit_replay_never_gathers(client, monkeypatch):
    campaign, sid = route_seed(client)
    store.scenes.append_message(campaign, sid, "assistant", "Original opener.")
    store.responses.migrate(campaign, sid)
    store.replay.begin(campaign, sid, 0)
    fake = FakeLLM([['New opener.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    monkeypatch.setattr(history_recall, "gather_view", lambda *a, **k: pytest.fail("replay retrieved"))
    reply = client.post(f"/api/campaigns/{campaign}/scenes/{sid}/replay/turn")
    assert reply.status_code == 200 and '"error"' not in reply.text, reply.text
    assert "# Earlier in the campaign" not in str(fake.messages)


def test_an_off_round_reads_nothing_past_the_config(cid, monkeypatch):
    """9.1 on the round path: with recall off the driver never peeks at the
    pending response or the transcript, let alone gathers."""
    store.config.write_config(history_recall_depth="0")
    monkeypatch.setattr(character_turns_rounds, "_history_replays",
                        lambda *a: pytest.fail("peeked while off"))
    monkeypatch.setattr(character_turns_rounds, "_round_post", lambda *a: pytest.fail("read while off"))
    monkeypatch.setattr(history_recall, "gather_view", lambda *a, **k: pytest.fail("gathered while off"))
    turn = SimpleNamespace(appended=(), continuation=None, history_by_round={},
                           round_record={"id": "r1", "pending_response": "x", "typed_note": True})
    got = asyncio.run(character_turns._round_history(None, cid, "001--saltmarch", None, None,
                                                     None, turn, "grimoire"))
    assert got is None


def test_the_phase_never_waits_on_the_shared_worker_pool(cid):
    """A `def` turn route blocks in `gather_sync` holding a shared-pool token
    while the phase runs; were the phase's stages to need that pool too,
    enough concurrent turns would starve it for good. With every shared token
    held, the phase still completes on its own threads."""
    old = played(cid)
    absorbed(cid, old)
    sid = played(cid, text="silver ledger")

    async def run():
        pool = anyio.to_thread.current_default_thread_limiter()
        pool.total_tokens = 1
        async with pool:  # the only shared token, held as a blocked route holds one
            with anyio.fail_after(10):
                return await history_recall.gather(None, cid, sid, seed="", resolved=None,
                                                   llm_client=None, embed_client=None)

    ev = anyio.run(run)
    assert [i.scene.sid for i in ev.items] == [old]
