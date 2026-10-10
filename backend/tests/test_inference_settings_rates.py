"""What the Models screen says prices each role's and route's calls (spec 3.5),
and why a provider cannot send (spec 3.2).

`rate` is `pricing.rate_for_call` asked about the RESOLVED selection -- the
ledger's own precedence, so the screen cannot drift from what Costs will
apply -- with where it came from. A native decision is never modelled
(`usage.py`), so it carries no figures. Placeholder names only.
"""

from __future__ import annotations

import json

import pytest

from grimoire.store import pricing
from grimoire.store.inference import facts

from . import inference_baseline as base
from . import inference_fixtures as fx

BOTH = {"prompt_usd_per_1k": 0.003, "completion_usd_per_1k": 0.015}
ZERO = {"prompt_usd_per_1k": 0.0, "completion_usd_per_1k": 0.0}


@pytest.fixture
def client(tmp_path):
    with base.client_at(tmp_path) as c:
        yield c


def _view(client) -> dict:
    got = client.get("/api/inference/settings")
    assert got.status_code == 200, got.text
    return got.json()


def _expected(provider: str, model: str) -> dict | None:
    return pricing.rate_for_call(pricing.read_pricing(), pricing.provider_rates(),
                                 provider_id=provider, model=model)


def test_a_rate_the_provider_states_is_the_providers(client):
    fx.format2(client)
    facts.state("openrouter", "vendor/active", rates=BOTH)
    rate = _view(client)["roles"]["primary"]["rate"]
    assert rate == {"source": "provider", "entry": _expected("openrouter", "vendor/active")}
    assert rate["entry"] == BOTH


def test_a_table_entry_is_the_tables_including_a_wildcard(client):
    fx.format2(client)
    pricing.write_pricing({"vendor/*": BOTH})
    rate = _view(client)["roles"]["primary"]["rate"]
    assert rate == {"source": "table", "entry": _expected("openrouter", "vendor/active")}


def test_no_rate_anywhere_is_none_never_zero(client):
    fx.format2(client)
    assert _view(client)["roles"]["primary"]["rate"] == {"source": "none"}
    # "none" exactly where the ledger's own precedence finds nothing.
    assert _expected("openrouter", "vendor/active") is None


def test_a_zero_rate_keeps_its_zeros(client):
    fx.format2(client)
    pricing.write_pricing({"vendor/active": ZERO})
    rate = _view(client)["roles"]["primary"]["rate"]
    assert rate["source"] == "table"
    assert rate["entry"]["prompt_usd_per_1k"] == 0.0
    assert rate["entry"]["completion_usd_per_1k"] == 0.0
    # A zero is an entry the ledger prices with, not an absence.
    assert _expected("openrouter", "vendor/active") is not None
    assert rate == {"source": "table", "entry": _expected("openrouter", "vendor/active")}


def test_a_half_entry_reads_as_none(client):
    fx.format2(client)
    # Written as a hand edit would leave it -- `write_pricing` itself drops a
    # half entry on the way in -- so this is the READ side: pricing keeps an
    # entry only with both base rates (`pricing.entry`), and the view says
    # nothing prices the model rather than drawing half of it.
    pricing.pricing_path().write_text(
        json.dumps({"vendor/active": {"prompt_usd_per_1k": 0.001}}), encoding="utf-8")
    assert _view(client)["roles"]["primary"]["rate"] == {"source": "none"}
    assert _expected("openrouter", "vendor/active") is None


def test_a_route_row_carries_its_resolved_rate(client):
    fx.format2(client)
    facts.state("openrouter", "vendor/active", rates=BOTH)
    row = next(r for r in _view(client)["routes"] if r["key"] == "scene")
    assert row["rate"] == {"source": "provider", "entry": BOTH}


def test_a_native_decision_has_no_figures(client):
    fx.decide_only(client, fallback=False)
    pricing.write_pricing({"": BOTH})       # a catch-all that WOULD price it
    got = _view(client)
    assert got["roles"]["decision"]["decision_mode"] == "native"
    assert got["roles"]["decision"]["rate"] == {"source": "native"}
    # The catch-all WOULD price it: "native" is the view declining on
    # purpose (a native row is never modelled), not the precedence finding
    # nothing.
    served = got["roles"]["decision"]["resolves"]
    assert _expected(served["provider"], served["model"]) == BOTH
    native_rows = [r for r in got["routes"] if r["decision_mode"] == "native"]
    assert native_rows and all(r["rate"] == {"source": "native"} for r in native_rows)


def test_an_embedding_that_does_not_resolve_has_no_rate(client):
    fx.format2(client)
    card = _view(client)["roles"]["embedding"]
    assert card["resolves"] is None and card["rate"] is None


def test_an_embedding_that_resolves_is_priced_like_any_model(client):
    fx.format2(client)
    pricing.write_pricing({"vendor/embed": BOTH})
    got = client.put("/api/inference/settings", json={
        "roles": {"embedding": {"selection": {"provider": "spare", "model": "vendor/embed"}}},
        "confirm_embedding": True})
    assert got.status_code == 200, got.text
    card = _view(client)["roles"]["embedding"]
    assert card["on"] is True
    assert card["rate"] == {"source": "table", "entry": _expected("spare", "vendor/embed")}


def test_a_campaign_view_carries_rates_too(client):
    cid = base._fresh(client)["cid"]
    fx.format2(client)
    facts.state("openrouter", "vendor/active", rates=BOTH)
    got = client.get(f"/api/campaigns/{cid}/inference")
    assert got.status_code == 200, got.text
    assert got.json()["roles"]["primary"]["rate"]["source"] == "provider"


def test_each_provider_says_why_it_cannot_send(client):
    fx.format2(client)
    made = client.post("/api/llm-connections", json={"kind": "openrouter", "name": "keyless"})
    assert made.status_code == 200, made.text
    providers = {p["id"]: p for p in _view(client)["providers"]}
    keyless = providers[made.json()["id"]]
    assert keyless["usable"] is False
    assert isinstance(keyless["problem"], str) and keyless["problem"]
    for p in providers.values():
        assert (p["problem"] is None) == p["usable"]
