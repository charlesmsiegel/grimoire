"""What the Models screen says of each role's and route's model size (01i,
spec 6.3).

`limits` is computed from the same resolution as `resolves` and `rate`: the
resolved primary's window and max output, the riding fallback's window, and
the prompt ceiling (`limits.prompt_ceiling`) with its reason. It is null
where nothing resolves and on a native decision, which sends no prompt to
pack -- the server decides, so the page holds no rule of its own.
Placeholder names only.
"""

from __future__ import annotations

import pytest

import grimoire.store as store
from grimoire.store.inference import facts, limits

from . import inference_baseline as base
from . import inference_fixtures as fx


@pytest.fixture
def client(tmp_path):
    with base.client_at(tmp_path) as c:
        yield c


def _view(client) -> dict:
    got = client.get("/api/inference/settings")
    assert got.status_code == 200, got.text
    return got.json()


def _listed(conn_id: str, *rows: dict) -> None:
    rev = store.llm_connections.read_connection_raw(conn_id)["rev"]
    store.llm_connections.set_cached_models(conn_id, list(rows), rev)


def _limit(value, source) -> dict:
    return {"value": value, "source": source}


def test_each_cards_limits_follow_its_resolution(client):
    fx.format2(client)
    _listed("openrouter", {"id": "vendor/active", "context": 131072, "max_output": 16000})
    got = _view(client)
    primary = got["roles"]["primary"]
    assert primary["resolves"]["model"] == "vendor/active"
    assert primary["limits"] == {
        "window": _limit(131072, "catalog"), "max_output": _limit(16000, "catalog"),
        "fallback_window": None,
        "ceiling": {"tokens": 131072 - limits.DEFAULT_REPLY_RESERVE,
                    "binding": ["openrouter", "vendor/active"], "reason": ""}}
    # A route resolving to the same model says the same.
    scene = next(r for r in got["routes"] if r["key"] == "scene")
    assert scene["limits"] == primary["limits"]


def test_an_unknown_window_is_said_and_binds_nothing(client):
    fx.format2(client)
    card = _view(client)["roles"]["primary"]
    assert card["limits"] == {
        "window": _limit(None, "unknown"), "max_output": _limit(None, "unknown"),
        "fallback_window": None, "ceiling": {"tokens": None, "binding": None, "reason": ""}}


def test_a_stated_window_is_the_users(client):
    fx.format2(client)
    facts.state("openrouter", "vendor/active", context_window=8192)
    window = _view(client)["roles"]["primary"]["limits"]["window"]
    assert window == _limit(8192, "user")


def test_limits_are_null_when_nothing_resolves(client):
    fx.format2(client)
    fx.put_settings(client, {"roles": {"primary": {"selection": {"provider": ""}}}})
    card = _view(client)["roles"]["primary"]
    assert card["resolves"] is None
    assert card["limits"] is None


def test_a_native_decide_card_and_route_have_no_limits(client):
    fx.decide_only(client, fallback=True)
    _listed("openrouter", {"id": "vendor/decider", "outputs": ["decisions"], "context": 32768},
            {"id": "vendor/active", "outputs": ["text"], "context": 131072})
    got = _view(client)
    assert got["roles"]["decision"]["decision_mode"] == "native"
    assert got["roles"]["decision"]["resolves"] is not None
    assert got["roles"]["decision"]["limits"] is None
    native = [r for r in got["routes"] if r["decision_mode"] == "native"]
    assert native and all(r["limits"] is None for r in native)
    # A generative card beside it is unaffected.
    assert got["roles"]["primary"]["limits"]["window"] == _limit(131072, "catalog")


def test_the_riding_fallbacks_window_rides_the_card_and_can_bind(client):
    fx.format2(client)
    _listed("openrouter", {"id": "vendor/active", "context": 131072})
    _listed("spare", {"id": "vendor/spare", "context": 8192})
    fx.put_settings(client, {"roles": {"primary": {
        "selection": {"provider": "openrouter", "model": "vendor/active"},
        "fallback": {"provider": "spare", "model": "vendor/spare"}}}})
    card = _view(client)["roles"]["primary"]
    assert card["limits"]["window"] == _limit(131072, "catalog")
    assert card["limits"]["fallback_window"] == _limit(8192, "catalog")
    assert card["limits"]["ceiling"] == {"tokens": 8192 - 2048,
                                         "binding": ["spare", "vendor/spare"], "reason": ""}


def test_a_ceiling_reason_shows_when_the_preset_reserve_exceeds_the_window(client):
    fx.format2(client)
    _listed("openrouter", {"id": "vendor/active", "context": 8192})
    pid = store.sampler_presets.create_preset("Long", {"max_tokens": 32000})
    fx.put_settings(client, {"roles": {"primary": {"selection": {
        "provider": "openrouter", "model": "vendor/active", "preset": pid}}}})
    ceiling = _view(client)["roles"]["primary"]["limits"]["ceiling"]
    assert ceiling["tokens"] == 0
    assert ceiling["reason"] == (
        "The preset asks for 32,000 reply tokens; vendor/active's window is 8,192.")


def test_the_embedding_cards_limits_reserve_nothing(client):
    """An embedding has no reply, so its ceiling is the window itself."""
    fx.format2(client)
    assert _view(client)["roles"]["embedding"]["limits"] is None
    _listed("spare", {"id": "vendor/embed", "outputs": ["embeddings"], "context": 8192})
    got = client.put("/api/inference/settings", json={
        "roles": {"embedding": {"selection": {"provider": "spare", "model": "vendor/embed"}}},
        "confirm_embedding": True})
    assert got.status_code == 200, got.text
    card = _view(client)["roles"]["embedding"]
    assert card["on"] is True
    assert card["limits"]["window"] == _limit(8192, "catalog")
    assert card["limits"]["ceiling"] == {"tokens": 8192, "binding": ["spare", "vendor/embed"],
                                         "reason": ""}


def test_a_campaign_view_carries_limits_too(client):
    cid = base._fresh(client)["cid"]
    fx.format2(client)
    _listed("openrouter", {"id": "vendor/active", "context": 65536})
    got = client.get(f"/api/campaigns/{cid}/inference")
    assert got.status_code == 200, got.text
    assert got.json()["roles"]["primary"]["limits"]["window"] == _limit(65536, "catalog")
