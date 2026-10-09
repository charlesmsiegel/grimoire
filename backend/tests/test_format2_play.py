"""The core play path on a format-2 store: one chat turn and a reroll.

Every suite plays at format 2 now: `tests/conftest.py` sets
`GRIMOIRE_TEST_BIRTH`, so each store a test creates is born an upgraded
default library. This file is the focused run of the turn path at that
layout, with every format-2 ingredient on the way to the facade in one
place: roles, a role fallback riding the chain the facade is sent, a
model's facts overlaid on the wire, and a reroll naming another preset --
on a store born at format 2, and on one that started a format-1 library and
was migrated and retired (slice I, Task 6) before it was configured.

Invented names, fake keys and a scripted fake only; no LLM call is made.
"""

from __future__ import annotations

import pytest

import grimoire.store as store
from grimoire import routes, wire
from grimoire.store import inference_keys as keys
from grimoire.store.inference import migrate
from tests.llm_fakes import FakeLLM

REPLY = 'The tide turns.\n```handoff\n{"next":null}\n```'
MODEL = "vendor/active"


@pytest.fixture(params=["born", "retired"])
def played(request):
    """A format-2 store: Primary on the keyed OpenRouter connection at
    `vendor/active` with the `warm` preset, a fallback on `spare`, the model's
    facts saying strict post-processing, and a scene with one cast member.

    `born`: the store is born at format 2. `retired`: it starts a format-1
    library, is migrated and retired, and only then configured."""
    retired = request.param == "retired"
    client = request.getfixturevalue("legacy_client" if retired else "client")
    assert keys.is_current(store.read_config()) is not retired
    assert client.put("/api/llm-connections/openrouter",
                      json={"api_key": "sk-or-x"}).status_code == 200
    spare = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "spare", "api_key": "sk-spare"})
    assert spare.status_code == 200, spare.text
    store.sampler_presets.create_preset("warm", {"temperature": 0.9})
    store.sampler_presets.create_preset("hot", {"temperature": 1.4})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    actor = client.post(f"/api/campaigns/{cid}/characters", json={"name": "Mara"})
    assert actor.status_code == 200, actor.text
    cast = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"id": actor.json()["character"]})
    assert cast.status_code == 200, cast.text

    if retired:
        assert migrate.ensure().state == "done"
        assert migrate.status().retirement["left"] == []
    assert keys.is_current(store.read_config())
    assert store.read_config()[keys.RETIRED_KEY] == "1"
    got = client.put("/api/inference/settings", json={"roles": {"primary": {
        "selection": {"provider": "openrouter", "model": MODEL, "preset": "warm"},
        "fallback": {"provider": "spare", "model": "vendor/spare"}}}})
    assert got.status_code == 200, got.text
    facts = client.put("/api/llm-connections/openrouter/facts",
                       json={"model": MODEL, "post_process": "strict"})
    assert facts.status_code == 200, facts.text
    return client, cid, sid


def _sent(fake: FakeLLM) -> wire.Chain:
    (request,) = fake.requests
    return request["chain"]


def test_a_turn_and_a_reroll_play_on_the_roles(played):
    client, cid, sid = played
    base = f"/api/campaigns/{cid}/scenes/{sid}"

    turn = FakeLLM([[REPLY]])
    client.app.dependency_overrides[routes.get_llm] = lambda: turn
    r = client.post(base + "/chat", json={"content": "Hello",
                                          "speaker_ref": "characters:mara"})
    assert r.status_code == 200, r.text
    assert "error" not in r.text, r.text

    chain = _sent(turn)
    # The Primary role, its preset, and the model's facts on the wire.
    assert (chain.primary.provider_id, chain.primary.model) == ("openrouter", MODEL)
    assert chain.primary.sampling.preset_id == "warm"
    assert chain.primary.post_process == "strict"
    # The role's fallback rides the chain, for the facade to send.
    assert chain.fallback is not None
    assert (chain.fallback.provider_id, chain.fallback.model) == ("spare", "vendor/spare")
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["content"].startswith("The tide turns.")

    reroll = FakeLLM([['Again, the tide.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: reroll
    r = client.post(base + "/regenerate", json={"preset": "hot"})
    assert r.status_code == 200, r.text
    assert "error" not in r.text, r.text

    chain = _sent(reroll)
    assert (chain.primary.provider_id, chain.primary.model) == ("openrouter", MODEL)
    assert chain.primary.sampling.preset_id == "hot"
    assert chain.primary.sampling.scope == "override"
    assert chain.primary.post_process == "strict"
    # The override is the primary's alone: the fallback keeps what it had.
    assert chain.fallback is not None and chain.fallback.provider_id == "spare"
    assert chain.fallback.sampling.preset_id != "hot"
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"].startswith(
        "Again, the tide.")


def test_a_keyless_primary_refuses_the_turn_naming_provider_and_role(played):
    client, cid, sid = played
    keyless = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "Winifred Router"}).json()["id"]
    got = client.put("/api/inference/settings", json={"roles": {"primary": {
        "selection": {"provider": keyless, "model": MODEL}}}})
    assert got.status_code == 200, got.text
    fake = FakeLLM([[REPLY]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                    json={"content": "Hello", "speaker_ref": "characters:mara"})

    assert r.status_code == 409, r.text
    assert r.json()["kind"] == "missing_key"
    assert "Winifred Router, the Primary role" in r.json()["detail"]
    assert fake.calls == 0
