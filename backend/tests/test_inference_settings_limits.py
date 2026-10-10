"""What the Models summary reads of each card's size (spec 01i 6.3): `limits`
on the role cards, the route rows and the Embedding card -- the resolved
primary's window and max output, the riding fallback's window, and the prompt
ceiling of the same resolution -- and `null` where nothing resolves or a
native decision packs no prompt. Placeholder names only."""

from __future__ import annotations

import pytest

import grimoire.store as store
from grimoire.store.inference import facts, limits, resolve

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


def _catalog(conn_id: str, rows: list[dict]) -> None:
    rev = store.llm_connections.read_connection_raw(conn_id)["rev"]
    store.llm_connections.set_cached_models(conn_id, rows, rev)


def test_a_cards_limits_follow_what_it_resolves(client):
    fx.format2(client)
    _catalog("openrouter", [{"id": "vendor/active", "context": 200000, "max_output": 64000}])
    card = _view(client)["roles"]["primary"]
    assert card["resolves"]["model"] == "vendor/active"
    limits = card["limits"]
    assert limits["model"] == "vendor/active"
    assert limits["window"] == {"value": 200000, "source": "catalog"}
    assert limits["max_output"] == {"value": 64000, "source": "catalog"}
    assert "fallback_window" not in limits
    assert limits["ceiling"] == {"tokens": 200000 - 4096,
                                 "binding": ["openrouter", "vendor/active"], "reason": ""}
    # A route row carries the same shape, off its own resolution.
    row = next(r for r in _view(client)["routes"] if r["key"] == "scene")
    assert row["limits"] == limits


def test_an_unknown_window_is_unknown_with_no_ceiling(client):
    fx.format2(client)
    limits = _view(client)["roles"]["primary"]["limits"]
    assert limits["window"] == {"value": None, "source": "unknown"}
    assert limits["ceiling"] == {"tokens": None, "binding": None, "reason": ""}


def test_the_riding_fallbacks_window_is_carried_and_can_bind(client):
    fx.format2(client)
    _catalog("openrouter", [{"id": "vendor/active", "context": 200000}])
    _catalog("spare", [{"id": "vendor/spare", "context": 8192}])
    fx.primary_falling_back(client, ("openrouter", "vendor/active"), fx.SPARE)
    limits = _view(client)["roles"]["primary"]["limits"]
    assert limits["fallback_window"] == {"value": 8192, "source": "catalog"}
    assert limits["ceiling"]["binding"] == ["spare", "vendor/spare"]
    assert limits["ceiling"]["tokens"] == 8192 - 2048


def test_a_preset_reserve_past_the_window_carries_its_reason(client):
    fx.format2(client)
    store.sampler_presets.create_preset("long", {"max_tokens": 32000})
    facts.state("openrouter", "vendor/active", context_window=8192)
    fx.primary(client, "vendor/active", api_key="", preset="long")
    ceiling = _view(client)["roles"]["primary"]["limits"]["ceiling"]
    assert ceiling["tokens"] == 0
    assert ceiling["reason"].startswith("The preset asks for 32,000 reply tokens")


def test_nothing_resolved_is_null(client):
    fx.format2(client)
    card = _view(client)["roles"]["embedding"]
    assert card["resolves"] is None and card["limits"] is None


def test_a_native_decision_packs_no_prompt_so_its_limits_are_null(client):
    fx.decide_only(client, fallback=False)
    got = _view(client)
    assert got["roles"]["decision"]["decision_mode"] == "native"
    assert got["roles"]["decision"]["limits"] is None
    native = [r for r in got["routes"] if r["decision_mode"] == "native"]
    assert native and all(r["limits"] is None for r in native)
    # A generating role on the same store still carries its own.
    assert got["roles"]["primary"]["limits"] is not None


def test_an_embedding_holds_no_reply_back(client):
    fx.format2(client)
    _catalog("spare", [{"id": "vendor/embed", "context": 8192}])
    fx.embedding("spare", "vendor/embed")
    card = _view(client)["roles"]["embedding"]
    assert card["on"] is True
    assert card["limits"]["window"] == {"value": 8192, "source": "catalog"}
    assert card["limits"]["ceiling"]["tokens"] == 8192


def test_a_campaign_view_carries_limits_too(client):
    cid = base._fresh(client)["cid"]
    fx.format2(client)
    facts.state("openrouter", "vendor/active", context_window=32768)
    got = client.get(f"/api/campaigns/{cid}/inference")
    assert got.status_code == 200, got.text
    assert got.json()["roles"]["primary"]["limits"]["window"] == {"value": 32768,
                                                                 "source": "user"}


def test_a_decide_fallback_sent_as_its_own_stage_never_lowers_the_ceiling(client):
    """A native Decision primary with a generating fallback: the fallback is
    a stage of its own (`rides` False), never sent the primary's prompt, so
    its window does not bound one."""
    fx.decide_only(client, fallback=True)
    _catalog("spare", [{"id": "vendor/spare", "context": 4096}])
    got = resolve.resolve("", role="decision", operation="decide")
    assert got.decision_mode == "native" and len(got.attempts) == 2 and not got.rides
    ceiling = limits.prompt_ceiling(got)
    assert ceiling.tokens is None and ceiling.binding is None
