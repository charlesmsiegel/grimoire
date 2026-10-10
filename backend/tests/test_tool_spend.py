"""The tool loop's spend ceiling (01g-S5; spec 3.9 "How spend is estimated
before a send", §6 spend tests and gate additions).

The guard projects each send from a price -- the attempt's catalog, else the
user's rates for a provider that does not report its own -- times a counted
prompt with a margin and the turn's output cap, maximised over the chain. It
is never accounting: a reported `cost_usd` never enters it.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

import grimoire.store as store
from grimoire import inference, tool_calls, wire
from grimoire.store import pricing
from grimoire.tool_calls import RunBudget, RunRefused, ToolOutput, Toolset, ToolSpec
from tests import wire_kit
from tests.llm_fakes import FakeToolTurns

RUN = "runspend0123"
CHEAP = wire_kit.target(provider_id="openrouter", model="vendor/cheap", api_key="k")
DEAR = wire_kit.target(provider_id="spare", kind="anthropic", model="claude-dear", api_key="k")
PROMPT = [{"role": "user", "content": "Who keeps the Saltmarch ledger?"}]
NONE = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
TOOLS = Toolset((ToolSpec("read", "Reads one record.", NONE,
                          lambda args, ctx: ToolOutput(text="Mara keeps it.")),))

#: Per-token catalog prices, as an OpenRouter row states them.
CATALOG = {("openrouter", "vendor/cheap"): {"prompt": "0.001", "completion": "0.001"},
           ("spare", "claude-dear"): {"prompt": "0.01", "completion": "0.01"}}


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(store.routing, "TOOLS_OPTIONAL",
                        frozenset({store.routing.route("chat").key}))
    monkeypatch.setattr(store.llm_connections, "cached_row",
                        lambda conn, model: CATALOG.get((conn, model)))


def _resolved(chain, preset: str = ""):
    resolved = wire_kit.resolution(chain)
    return replace(resolved, attempts=tuple(replace(a, provider_preset=preset)
                                            for a in resolved.attempts))


def _run(fake, resolved, budget):
    return asyncio.run(inference.run_tools(
        "chat", PROMPT, toolset=TOOLS, execute=tool_calls.registered(TOOLS), client=fake,
        resolved=resolved, budget=budget, run_id=RUN))


# ---- the price ----
def test_an_attempt_with_catalog_prices_is_priced_from_the_catalog():
    (attempt,) = _resolved(CHEAP).attempts
    price = inference.price_for(attempt)
    assert price is not None and price.basis == "catalog" and price.prompt == 0.001
    assert inference.tool_run_refusal("chat", _resolved(CHEAP),
                                      RunBudget(spend_ceiling_usd=1.0)) is None


def test_a_zero_default_rate_never_prices_a_billed_attempt(monkeypatch):
    """A provider that reports its own price is never priced from the user's
    rates: a `"": 0` default meant for local models would project a billed
    turn at nothing."""
    zero = {pricing.PROMPT: 0.0, pricing.COMPLETION: 0.0}
    monkeypatch.setattr(store.pricing, "rate_for_call", lambda *a, **k: zero)
    unlisted = wire_kit.target(provider_id="or-2", model="vendor/unlisted", api_key="k")
    billed = _resolved(unlisted, preset="openrouter")
    assert inference.price_for(billed.attempts[0]) is None
    refused = inference.tool_run_refusal("chat", billed, RunBudget(spend_ceiling_usd=1.0))
    assert isinstance(refused, RunRefused) and refused.kind == "unpriceable"
    assert "vendor/unlisted" in str(refused) and "set rates" in str(refused)
    # A provider that does not report its own price is priced at the rates.
    local = _resolved(unlisted, preset="custom")
    price = inference.price_for(local.attempts[0])
    assert price is not None and price.basis == "rates"


def test_an_uncapped_attempt_is_unpriceable_under_a_ceiling():
    uncapped = replace(CHEAP, model_params=("temperature",))
    assert not inference.cap_sent(inference.call_chain(
        _resolved(uncapped), max_tokens=100).primary)
    refused = inference.tool_run_refusal("chat", _resolved(uncapped),
                                         RunBudget(spend_ceiling_usd=1.0))
    assert refused is not None and "output cap" in str(refused)


def test_no_ceiling_refuses_nothing():
    unpriced = wire_kit.target(provider_id="nowhere", model="m", api_key="k")
    assert inference.tool_run_refusal("chat", _resolved(unpriced, "openrouter"),
                                      RunBudget()) is None


def test_an_unpriced_chain_is_refused_with_nothing_sent():
    unpriced = wire_kit.target(provider_id="nowhere", model="m", api_key="k")
    fake = FakeToolTurns(("ok", []))
    with pytest.raises(RunRefused):
        _run(fake, _resolved(unpriced, "openrouter"), RunBudget(spend_ceiling_usd=5.0))
    assert fake.calls == 0
    assert list(store.usage.calls(days=1)) == []


# ---- the check before each send ----
def test_a_priced_chain_stops_before_the_turn_that_would_cross():
    fake = FakeToolTurns(("", [("read", {})]), ("Mara.", []),
                         usage={"prompt_tokens": 50, "completion_tokens": 100})
    result = _run(fake, _resolved(CHEAP), RunBudget(spend_ceiling_usd=0.25,
                                                    max_output_tokens=100))
    assert fake.calls == 1
    assert (result.status, result.limit) == ("budget_exhausted", "spend")
    assert result.trace[-1].name == "spend"


def test_under_the_ceiling_the_run_completes():
    fake = FakeToolTurns(("", [("read", {})]), ("Mara.", []),
                         usage={"prompt_tokens": 50, "completion_tokens": 100})
    result = _run(fake, _resolved(CHEAP), RunBudget(spend_ceiling_usd=5.0,
                                                    max_output_tokens=100))
    assert result.status == "completed" and fake.calls == 2


def test_a_reported_cost_is_never_read_into_the_guard():
    """The provider says each turn cost a fortune; the guard prices the
    counts itself and lets the run go on."""
    fake = FakeToolTurns(("", [("read", {})]), ("Mara.", []),
                         usage={"prompt_tokens": 20, "completion_tokens": 20,
                                "cost_usd": 1000.0, "cost_basis": "billed"})
    result = _run(fake, _resolved(CHEAP), RunBudget(spend_ceiling_usd=1.0,
                                                    max_output_tokens=50))
    assert result.status == "completed" and fake.calls == 2


def test_the_projection_is_the_dearest_attempt_on_the_chain():
    """The cheap primary alone would fit; the fallback that could serve the
    same turn would not, so the turn is not sent."""
    fake = FakeToolTurns(("ok", []))
    result = _run(fake, _resolved(wire.Chain(CHEAP, DEAR)),
                  RunBudget(spend_ceiling_usd=0.5, max_output_tokens=100,
                            reserve_final=False))
    assert fake.calls == 0
    assert (result.status, result.limit) == ("budget_exhausted", "spend")
    alone = FakeToolTurns(("ok", []))
    assert _run(alone, _resolved(CHEAP), RunBudget(spend_ceiling_usd=0.5,
                                                   max_output_tokens=100)).status == "completed"


def test_a_ceiling_is_finite_and_not_negative():
    for bad in (-1.0, float("inf"), True):
        with pytest.raises(ValueError):
            RunBudget(spend_ceiling_usd=bad)
    assert RunBudget(spend_ceiling_usd=0).spend_ceiling_usd == 0
