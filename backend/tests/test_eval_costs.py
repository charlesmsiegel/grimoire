"""`evals/costs.py`: eval buckets folded by the production `usage._add`, and
rendered by `cost.tsx`'s rules (spec 01a, §6-§7; §11 tests 3, 4, 9, 13, 14,
16, 20, 21). Pure: synthetic rows, no store."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import costs  # noqa: E402
from grimoire.store import pricing, usage  # noqa: E402

#: A rate table pricing every model at $1 per 1k tokens on both sides.
RATES = usage.Rates({pricing.DEFAULT_KEY: {pricing.PROMPT: 1.0, pricing.COMPLETION: 1.0}})


def _row(**fields) -> dict:
    return {"task": "chat", "status": "ok", "duration_ms": 10, **fields}


def test_money_follows_cost_tsx():
    assert costs.money(0.0042) == "$0.0042"
    assert costs.money(4e-7) == "<$0.0001"
    assert costs.money(0.0001) == "$0.0001"
    assert costs.money(0.0) == "$0.00"
    assert costs.money(0.01) == "$0.01"
    assert costs.money(1234.5) == "$1,234.50"


def test_an_all_unpriced_bucket_is_not_reported_never_zero():
    bucket = costs.fold([_row(prompt_tokens=10, completion_tokens=2)])
    assert costs.cost_text(bucket) == "cost: not reported"
    assert "$0.00" not in costs.bucket_line(bucket)


def test_the_billed_count_is_derived():
    bucket = costs.fold([_row(cost_usd=0.002),
                         _row(cost_usd=0.003, cost_basis="equivalent")])
    assert costs.billed_calls(bucket) == bucket["priced_calls"] - bucket["subscription_calls"] == 1
    assert costs.cost_text(bucket) == "billed $0.0020  sub-equiv ~$0.0030"


def test_a_stated_zero_price_is_a_price():
    bucket = costs.fold([_row(cost_usd=0.0, prompt_tokens=1, completion_tokens=1)])
    assert costs.cost_text(bucket) == "billed $0.00"


def test_a_modelled_figure_needs_a_rate_and_is_marked():
    rows = [_row(prompt_tokens=1000, completion_tokens=1000, model="vendor/m")]
    assert costs.cost_text(costs.fold(rows)) == "cost: not reported"
    priced = costs.fold(rows, RATES)
    assert costs.cost_text(priced) == "modelled ~$2.00"
    assert "billed" not in costs.cost_text(priced)


def test_a_partly_priced_bucket_says_incomplete():
    bucket = costs.fold([_row(cost_usd=0.01), _row(), _row()])
    assert costs.cost_text(bucket) == (
        "billed $0.01  incomplete: 2 unpriced  2 had no token counts")


def test_a_native_row_is_never_modelled():
    """Test 3: a native decision with counts and a rate stays unpriced."""
    native = _row(task="scene-break", operation="decide", decision_mode="native",
                  prompt_tokens=500, completion_tokens=5)
    bucket = costs.fold([native], RATES)
    assert bucket["modelled_calls"] == 0 and bucket["unpriced_calls"] == 1
    assert bucket["unpriced_native_calls"] == 1 and bucket["unmetered_calls"] == 0
    assert costs.cost_text(bucket) == "cost: not reported  1 native, no rate applies"


def test_the_subscription_columns():
    """Test 4: an equivalent price is sub-equivalent only; a billed price on
    a subscription-tagged provider is billed."""
    equivalent = costs.fold([_row(cost_usd=0.004, cost_basis="equivalent",
                                  billing="subscription")])
    assert costs.cost_text(equivalent) == "sub-equiv ~$0.0040"
    billed = costs.fold([_row(cost_usd=0.004, billing="subscription")])
    assert costs.cost_text(billed) == "billed $0.0040"


def test_folding_is_the_production_add(monkeypatch):
    """Test 9: the fold calls `usage._add` itself, never a private copy."""
    seen: list[dict] = []
    real = usage._add

    def spy(bucket, row, rates=None):
        seen.append(row)
        real(bucket, row, rates)

    monkeypatch.setattr(usage, "_add", spy)
    row = _row(cost_usd=0.001)
    costs.fold([row])
    costs.aggregate([([row], None)])
    assert seen == [row, row]


def test_token_coverage():
    """Test 16: an absent count is never printed as zero."""
    assert costs.tokens_text(costs.fold([_row(cost_usd=0.001)])) == "tokens: not reported"
    native = _row(operation="decide", decision_mode="native", prompt_tokens=412)
    assert costs.tokens_text(costs.fold([native])) == "tokens 412 / not reported"
    mixed = costs.fold([_row(prompt_tokens=10, completion_tokens=2), _row()])
    assert costs.tokens_text(mixed) == "tokens 10 (1 of 2 counted) / 2 (1 of 2 counted)"
    embed = _row(task="semantic-recall", operation="embed", prompt_tokens=30)
    assert costs.tokens_text(costs.fold([embed])) == "tokens 30 / 0"
    estimated = costs.fold([_row(prompt_tokens=10, completion_tokens=2,
                                 tokens_estimated=True)])
    assert costs.tokens_text(estimated) == "tokens 10 / 2 (1 estimated)"


def test_the_aggregate_is_keyed_by_route_backend_and_hop():
    """Tests 14 and 21: a row with no hop is under `-`, an escalation row
    apart; an unrouted task aggregates under its own name; each case's rows
    fold with that case's rates."""
    chain = _row(task="scene-break", operation="decide", decision_mode="structured",
                 cost_usd=0.001)
    hop = {**chain, "hop": "escalation", "duration_ms": 250}
    stray = _row(task="planted-task", prompt_tokens=1000, completion_tokens=0)
    recall = _row(task="semantic-recall", operation="embed", prompt_tokens=5)
    got = costs.aggregate([([chain, hop], None), ([stray, recall], RATES)])
    keys = [(a["route"], a["backend"], a["hop"]) for a in got]
    assert keys == sorted([("scene_break", "structured", "-"),
                           ("scene_break", "structured", "escalation"),
                           ("planted-task", "generate", "-"),
                           ("embed", "embed", "-")])
    by = {(a["route"], a["hop"]): a["bucket"] for a in got}
    assert by[("scene_break", "escalation")]["duration_ms"] == 250
    assert costs.cost_text(by[("planted-task", "-")]) == "modelled ~$1.00"


def test_merge_carries_every_count():
    first = costs.fold([_row(operation="decide", decision_mode="native", prompt_tokens=3)],
                       RATES)
    second = costs.fold([_row(cost_usd=0.002, prompt_tokens=1, completion_tokens=1,
                              tokens_estimated=True)])
    merged = costs.merge([first, second])
    assert merged["calls"] == 2 and merged["unpriced_native_calls"] == 1
    assert merged["estimated_token_calls"] == 1
    assert (merged["prompt_counted_calls"], merged["completion_counted_calls"]) == (2, 1)
    assert merged["cost_usd"] == 0.002
    assert costs.merge([]) == costs.fold([])


def test_by_task_buckets_each_task():
    rows = [_row(task="response-selector", cost_usd=0.001), _row(task="chat", cost_usd=0.01),
            _row(task="scene-break", cost_usd=0.002)]
    split = costs.by_task(rows)
    assert list(split) == ["response-selector", "chat", "scene-break"]
    assert costs.merge(split.values())["cost_usd"] == costs.fold(rows)["cost_usd"]
