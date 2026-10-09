"""Rollups price each call at its own model's rates, and say what they estimated
(slice E, Task 4; spec 9.1).

Rates come from two places: a model's own rates on its provider (model facts),
then the user's `pricing.json` table. A row only reaches the first when it
names a provider (`provider_id`) -- every row filed before that field prices
exactly as it did. `cost_basis` alone moves a figure between columns; `billing`
is a label, counted beside the figures and never as one.
"""

from __future__ import annotations

import pytest

from grimoire import decisions, llm_usage, wire
from grimoire.store import llm_connections, pricing, usage
from grimoire.store.inference import facts

FACTS = {"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002}
TABLE = {"prompt_usd_per_1k": 0.01, "completion_usd_per_1k": 0.02}


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def pid(home):
    return llm_connections.create_connection("openai_compatible", "Saltmarch",
                                             base_url="http://localhost:1/v1")


def _call(**kw) -> dict:
    kw.setdefault("task", "chat")
    kw.setdefault("campaign", "saltmarch")
    kw.setdefault("model", "vendor/model-a")
    kw.setdefault("prompt_tokens", 1000)
    kw.setdefault("completion_tokens", 1000)
    row = usage.record(**kw)
    assert row is not None
    return row


def _totals() -> dict:
    return usage.summary(days=1)["totals"]


# ---- precedence ----
def test_facts_rates_price_before_the_pricing_table(pid):
    facts.state(pid, "vendor/model-a", rates=FACTS)
    pricing.write_pricing({"vendor/model-a": TABLE})
    _call(provider_id=pid)

    # 1000 x 0.001/1k + 1000 x 0.002/1k, never the table's 0.01 + 0.02.
    assert _totals()["modelled_usd"] == pytest.approx(0.003)
    assert _totals()["modelled_calls"] == 1


def test_a_dated_snapshot_is_priced_by_the_requested_models_rates(pid):
    """Review Focus 2: rates are stated under what was asked for, and the
    provider answered under a dated snapshot of it."""
    facts.state(pid, "gpt-4o", rates=FACTS)
    row = _call(provider_id=pid, model="gpt-4o-2024-08-06", requested_model="gpt-4o")
    assert row["requested_model"] == "gpt-4o"

    totals = _totals()
    assert totals["modelled_usd"] == pytest.approx(0.003)
    assert totals["unpriced_calls"] == 0


def test_a_row_without_provider_id_uses_only_the_pricing_table(pid):
    """Ruling 3: a row filed before `provider_id` existed names only a display
    connection, so it prices from `pricing.json` alone -- no name matching."""
    facts.state(pid, "vendor/model-a", rates=FACTS)
    _call(connection="Saltmarch")
    assert _totals()["unpriced_calls"] == 1
    assert _totals()["modelled_usd"] == 0.0

    pricing.write_pricing({"vendor/model-a": TABLE})
    assert _totals()["modelled_usd"] == pytest.approx(0.03)


# ---- billing is a label ----
def test_a_billed_price_on_a_subscription_provider_stays_spend(pid):
    """Ruling 4: `cost_basis` alone decides the column."""
    _call(provider_id=pid, billing="subscription", cost_usd=0.5, cost_basis="billed")

    totals = _totals()
    assert totals["cost_usd"] == pytest.approx(0.5)
    assert totals["estimated_usd"] == 0.0
    assert usage.budget("saltmarch", 10.0)["spent_usd"] == pytest.approx(0.5)


def test_subscription_modelled_rows_are_counted_and_never_budgeted(pid):
    facts.state(pid, "vendor/model-a", rates=FACTS)
    _call(provider_id=pid, cost_usd=1.0)
    before = usage.budget("saltmarch", 10.0)["spent_usd"]
    _call(provider_id=pid, billing="subscription")

    totals = _totals()
    assert totals["modelled_subscription_calls"] == 1
    assert totals["modelled_calls"] == 1
    assert totals["modelled_usd"] == pytest.approx(0.003)
    assert totals["cost_usd"] == pytest.approx(1.0)
    assert usage.budget("saltmarch", 10.0)["spent_usd"] == before == pytest.approx(1.0)


def test_unpriced_subscription_rows_are_counted(pid):
    """M13: a subscription row nothing prices sits inside `unpriced_calls`."""
    _call(provider_id=pid, billing="subscription")
    _call(provider_id=pid)

    totals = _totals()
    assert totals["unpriced_calls"] == 2
    assert totals["unpriced_subscription_calls"] == 1
    assert "modelled_subscription_calls" not in totals


def test_estimated_token_rows_are_counted(pid):
    _call(provider_id=pid, tokens_estimated=True)
    _call(provider_id=pid)
    assert _totals()["estimated_token_calls"] == 1


def test_the_breakdown_counts_are_lazy(pid):
    """Like `images`: absent from a bucket no row added one to, so `_ZERO` and
    every stored shape built from it keep theirs."""
    _call(provider_id=pid, cost_usd=0.1)
    totals = _totals()
    for key in ("estimated_token_calls", "modelled_subscription_calls",
                "unpriced_subscription_calls"):
        assert key not in totals
        assert key not in usage._ZERO


# ---- embed rows (ruling 8) ----
def test_an_embed_row_with_no_completion_count_is_priced(pid):
    facts.state(pid, "vendor/embed-a", rates=FACTS)
    row = _call(task="embed", operation="embed", provider_id=pid,
                model="vendor/embed-a", completion_tokens=None)
    assert "completion_tokens" not in row

    totals = _totals()
    assert totals["modelled_usd"] == pytest.approx(0.001)
    assert totals["modelled_calls"] == 1
    assert totals["unpriced_calls"] == 0
    assert totals["unmetered_calls"] == 0


def test_an_unpriced_embed_row_is_unpriced_never_zero(pid):
    _call(task="embed", operation="embed", provider_id=pid,
          model="vendor/embed-a", completion_tokens=None)
    totals = _totals()
    assert totals["unpriced_calls"] == 1
    assert totals["modelled_calls"] == 0
    # Metered: a rate WOULD price it, so the chore can send the reader there.
    assert totals["unmetered_calls"] == 0


def test_an_account_only_holder_files_the_meters_model(pid):
    """I6: an embed call whose holder got only `account()` and a prompt count
    files the meter's model, and that model's rates price it."""
    facts.state(pid, "vendor/embed-a", rates=FACTS)
    m = usage.meter("embed", campaign="saltmarch", model="vendor/embed-a")
    llm_usage.account(m.usage, wire.Target(provider_id=pid, kind="openai_compatible",
                                           model="vendor/embed-a",
                                           account=wire.Account(operation="embed")))
    m.usage["prompt_tokens"] = 1000
    row = m.done()

    assert row is not None
    assert row["model"] == "vendor/embed-a"
    assert row["provider_id"] == pid
    assert row["operation"] == "embed"
    assert _totals()["modelled_usd"] == pytest.approx(0.001)


def test_an_embed_row_with_a_campaign_reaches_its_budget(pid):
    _call(task="embed", operation="embed", provider_id=pid, model="vendor/embed-a",
          completion_tokens=None, cost_usd=0.25)
    assert usage.budget("saltmarch", 10.0)["spent_usd"] == pytest.approx(0.25)


def test_a_generate_row_with_one_count_stays_unpriced(pid):
    """Today's rule, unchanged: only an embed row may lack a completion count."""
    facts.state(pid, "vendor/model-a", rates=FACTS)
    pricing.write_pricing({"": TABLE})
    _call(operation="generate", provider_id=pid, completion_tokens=None)
    _call(provider_id=pid, completion_tokens=None)

    totals = _totals()
    assert totals["unpriced_calls"] == 2
    assert totals["unmetered_calls"] == 2
    assert totals["modelled_calls"] == 0


# ---- per-turn rows ----
def test_turn_rows_carry_billing_and_the_estimate_flag(pid):
    facts.state(pid, "vendor/model-a", rates=FACTS)
    _call(scene="harbour", provider_id=pid, billing="subscription", tokens_estimated=True)
    _call(scene="harbour", ts=usage._now()[:10] + "T00:00:00Z")

    turns = usage.scene_usage("saltmarch", "harbour")["turns"]
    newest, oldest = turns
    assert newest["billing"] == "subscription"
    assert newest["tokens_estimated"] is True
    assert newest["modelled_usd"] == pytest.approx(0.003)
    assert oldest["billing"] == ""
    assert oldest["tokens_estimated"] is False


# ---- zero is a price; a mangled file is no price ----
def test_zero_rates_model_a_zero_not_unpriced(pid):
    """Review Focus 5: zero rates the user entered for a local model."""
    facts.state(pid, "vendor/model-a",
                rates={"prompt_usd_per_1k": 0, "completion_usd_per_1k": 0})
    _call(provider_id=pid)

    totals = _totals()
    assert totals["modelled_calls"] == 1
    assert totals["modelled_usd"] == 0.0
    assert totals["unpriced_calls"] == 0


@pytest.mark.parametrize("doc", [
    "{not json",
    '{"vendor/model-a": {"rates": {"prompt_usd_per_1k": 0.001}}}',
    '{"vendor/model-a": {"rates": {"prompt_usd_per_1k": "x", "completion_usd_per_1k": 1}}}',
    '{"vendor/model-a": {"rates": {"prompt_usd_per_1k": -1, "completion_usd_per_1k": 1}}}',
    '["not", "an", "object"]',
])
def test_a_mangled_facts_file_costs_the_estimates_not_the_report(pid, doc):
    """Review Focus 4: the model reads unpriced (never $0), falls back to the
    table, and the report still draws its real spend."""
    llm_connections.facts_path(pid).write_text(doc, encoding="utf-8")
    _call(provider_id=pid, cost_usd=0.5)
    _call(provider_id=pid)

    totals = _totals()
    assert totals["cost_usd"] == pytest.approx(0.5)
    assert totals["unpriced_calls"] == 1
    assert totals["modelled_usd"] == 0.0

    pricing.write_pricing({"vendor/model-a": TABLE})
    assert _totals()["modelled_usd"] == pytest.approx(0.03)


def test_a_table_entry_for_either_name_prices_the_call_and_clears_both_chores(pid):
    """CODE-M2: the provider is configured as `vendor/model-a` and answers as a
    dated snapshot. A table entry under the name the user configured -- the
    only name the Housekeeping chore can know -- prices the recorded calls
    too, so the configuration chore and the ledger chore never disagree:
    both judge through `pricing.rate_for_call`, which matches the table
    against the model that answered and then the model asked for."""
    from grimoire import store
    from grimoire.store import inference_keys as keys
    from grimoire.store.inference import in_use
    store.write_config(**{keys.FORMAT_KEY: keys.CURRENT_FORMAT,
                          keys.role_key("primary", "provider"): pid,
                          keys.role_key("primary", "model"): "vendor/model-a"})
    _call(provider_id=pid, model="vendor/model-a-2026-08", requested_model="vendor/model-a")
    assert [m["model"] for m in in_use.unpriced()] == ["vendor/model-a"]
    assert [m["model"] for m in usage.unpriced_models()] == ["vendor/model-a-2026-08"]

    pricing.write_pricing({"vendor/model-a": TABLE})

    assert in_use.unpriced() == []
    assert usage.unpriced_models() == []
    totals = _totals()
    assert totals["modelled_usd"] == pytest.approx(0.03)
    assert totals["unpriced_calls"] == 0


def test_an_entry_for_the_model_that_answered_still_comes_first():
    """The table matches the recorded model first, as it always has; the name
    asked for is tried only where the answer matched no entry of the same
    tier, so a row without `requested_model` prices exactly as before."""
    answered = {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 1.0}
    asked = {"prompt_usd_per_1k": 2.0, "completion_usd_per_1k": 2.0}
    family = {"prompt_usd_per_1k": 3.0, "completion_usd_per_1k": 3.0}
    default = {"prompt_usd_per_1k": 4.0, "completion_usd_per_1k": 4.0}
    call = {"model": "vendor/model-a-2026-08", "requested_model": "vendor/model-a"}
    table = {"vendor/model-a-2026-08": answered, "vendor/model-a": asked,
             "vendor/*": family, "": default}
    assert pricing.rate_for_call(table, {}, **call) is answered
    del table["vendor/model-a-2026-08"]
    # An exact entry under the name asked for beats a family wildcard.
    assert pricing.rate_for_call(table, {}, **call) is asked
    del table["vendor/model-a"]
    assert pricing.rate_for_call(table, {}, **call) is family
    del table["vendor/*"]
    assert pricing.rate_for_call(table, {}, **call) is default
    # No requested model: exactly `rate_for`.
    assert pricing.rate_for_call({"vendor/model-a": asked}, {},
                                 model="vendor/model-a-2026-08") is None


# ---- the unpriced list ----
def test_unpriced_models_judges_with_model_rates(pid):
    _call(provider_id=pid, model="gpt-4o-2024-08-06", requested_model="gpt-4o")
    _call(provider_id=pid, model="vendor/model-b")
    _call(provider_id=pid, model="vendor/model-b")
    _call(model="vendor/legacy")

    assert usage.unpriced_models() == [
        {"model": "vendor/model-b", "facts_model": "vendor/model-b",
         "provider_id": pid, "calls": 2},
        {"model": "gpt-4o-2024-08-06", "facts_model": "gpt-4o",
         "provider_id": pid, "calls": 1},
        {"model": "vendor/legacy", "facts_model": "vendor/legacy",
         "provider_id": "", "calls": 1},
    ]

    facts.state(pid, "gpt-4o", rates=FACTS)
    facts.state(pid, "vendor/legacy", rates=FACTS)
    # The facts rates price the snapshot; the legacy row names no provider, so
    # a provider's rates for a same-named model never reach it (ruling 3).
    assert [m["model"] for m in usage.unpriced_models()] == [
        "vendor/model-b", "vendor/legacy"]


def test_an_embed_model_with_no_completion_count_is_listed(pid):
    _call(task="embed", operation="embed", provider_id=pid, model="vendor/embed-a",
          completion_tokens=None)
    assert [m["model"] for m in usage.unpriced_models()] == ["vendor/embed-a"]


# ---- native decisions are never modelled (slice H, ruling 10) ----
@pytest.mark.parametrize("provider", ["openai_compatible", "openrouter"])
def test_a_native_decision_row_without_a_cost_is_unpriced_whatever_the_rates(pid, provider):
    # Counts as reported (OpenAI's input/output pair, or OpenRouter's without
    # its `cost`), and a rate on both layers for exactly this model.
    facts.state(pid, "vendor/judge", rates=FACTS)
    pricing.write_pricing({"vendor/judge": TABLE})
    _call(task="speaker", operation="decide", decision_mode=decisions.NATIVE_BACKEND, provider=provider,
          provider_id=pid, model="vendor/judge", scene="harbour",
          prompt_tokens=420, completion_tokens=0, cache_read_tokens=0)

    totals = _totals()
    assert totals["unpriced_calls"] == 1
    assert totals["modelled_calls"] == 0 and totals["modelled_usd"] == 0.0
    assert totals["unmetered_calls"] == 0  # its counts were reported
    assert totals["prompt_tokens"] == 420  # and still sum into the totals
    turn = usage.scene_usage("saltmarch", "harbour")["turns"][0]
    assert turn["modelled_usd"] is None and turn["cost_usd"] is None
    # No rate can price it, so the chore never names its model.
    assert usage.unpriced_models() == []


def test_a_native_decision_row_nobody_priced_is_counted_apart(pid):
    """The Costs card offers a rate only for calls a rate could price. A
    native decision is never one (`_modellable`), with counts or without, so
    it is counted in `unpriced_native_calls` -- a slice of `unpriced_calls`
    -- and never in `unmetered_calls`, whose reason (no token counts) is not
    its reason."""
    _call(task="speaker", operation="decide", decision_mode=decisions.NATIVE_BACKEND,
          provider_id=pid, model="vendor/judge", prompt_tokens=420, completion_tokens=0)
    _call(task="speaker", operation="decide", decision_mode=decisions.NATIVE_BACKEND,
          provider_id=pid, model="vendor/judge", prompt_tokens=None, completion_tokens=None)
    _call(provider_id=pid, model="vendor/plain", completion_tokens=None)
    _call(provider_id=pid, model="vendor/plain")

    totals = _totals()
    assert totals["unpriced_calls"] == 4
    assert totals["unpriced_native_calls"] == 2
    assert totals["unmetered_calls"] == 1
    # A priced native row is spend, and no slice of the unpriced count.
    _call(task="speaker", operation="decide", decision_mode=decisions.NATIVE_BACKEND,
          provider_id=pid, model="vendor/judge", cost_usd=0.25, cost_basis=llm_usage.BILLED)
    assert _totals()["unpriced_native_calls"] == 2


def test_a_native_decision_row_with_a_reported_cost_is_spend(pid):
    facts.state(pid, "vendor/judge", rates=FACTS)
    _call(task="speaker", operation="decide", decision_mode=decisions.NATIVE_BACKEND, provider="openrouter",
          provider_id=pid, model="vendor/judge", prompt_tokens=412, completion_tokens=58,
          cost_usd=0.25, cost_basis=llm_usage.BILLED)

    totals = _totals()
    assert totals["cost_usd"] == pytest.approx(0.25)
    assert totals["modelled_usd"] == 0.0 and totals["unpriced_calls"] == 0
    assert usage.budget("saltmarch", 10.0)["spent_usd"] == pytest.approx(0.25)


def test_a_structured_decision_row_is_modelled_as_before(pid):
    facts.state(pid, "vendor/judge", rates=FACTS)
    _call(task="speaker", operation="decide", decision_mode=decisions.STRUCTURED_BACKEND,
          provider_id=pid, model="vendor/judge")

    totals = _totals()
    # 1000 x 0.001/1k + 1000 x 0.002/1k, exactly as a chat row of that shape.
    assert totals["modelled_calls"] == 1
    assert totals["modelled_usd"] == pytest.approx(0.003)
    assert totals["unpriced_calls"] == 0
