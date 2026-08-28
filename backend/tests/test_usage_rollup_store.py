"""The all-time money aggregate: a bookmark into the ledger, never a source."""

from __future__ import annotations

import json

import pytest

from grimoire.store import pricing, usage, usage_rollup


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def _call(**kw):
    kw.setdefault("task", "chat")
    kw.setdefault("campaign", "saltmarch")
    kw.setdefault("model", "realm/opus")
    kw.setdefault("prompt_tokens", 1000)
    kw.setdefault("completion_tokens", 200)
    return usage.record(**kw)


# ---- the totals themselves ----
def test_a_campaign_with_no_rows_is_zero_and_not_partial(home):
    _call(cost_usd=0.5, campaign="elsewhere", ts="2026-08-01T00:00:00Z")

    out = usage_rollup.campaign_totals("saltmarch")
    assert out["calls"] == 0
    assert out["cost_usd"] == 0.0
    # A campaign the ledger genuinely never mentioned HAS an answer, and the
    # answer is zero. `partial` is reserved for "nobody could compute this".
    assert out["partial"] is False


def test_a_library_with_no_ledger_at_all_is_zero(home):
    out = usage_rollup.library_totals()
    assert out["calls"] == 0
    assert out["partial"] is False


def test_the_three_money_columns_stay_apart(home):
    _call(cost_usd=0.25, cost_basis="billed", ts="2026-08-01T00:00:00Z")
    _call(cost_usd=0.75, cost_basis="equivalent", ts="2026-08-01T00:01:00Z")
    _call(ts="2026-08-01T00:02:00Z")   # no price at all, and no rate table

    out = usage_rollup.campaign_totals("saltmarch")
    assert out["cost_usd"] == 0.25
    assert out["estimated_usd"] == 0.75
    assert out["modelled_usd"] == 0.0
    assert out["unpriced_calls"] == 1
    assert out["calls"] == 3


def test_a_rows_campaign_decides_which_bucket_it_lands_in(home):
    _call(cost_usd=1.0, campaign="saltmarch", ts="2026-08-01T00:00:00Z")
    _call(cost_usd=2.0, campaign="the-silting-channel", ts="2026-08-01T00:01:00Z")
    # A call with no campaign at all -- a tagline, a connection test.
    _call(cost_usd=4.0, campaign="", ts="2026-08-01T00:02:00Z")

    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 1.0
    assert usage_rollup.campaign_totals("the-silting-channel")["cost_usd"] == 2.0
    # The library total counts all three, which is what makes it the library's.
    assert usage_rollup.library_totals()["cost_usd"] == 7.0


def test_rename_rows_are_not_counted_as_calls(home):
    _call(cost_usd=1.0, ts="2026-08-01T00:00:00Z")
    usage.repoint_scenes("saltmarch", {"001-old": "001-new"})

    assert usage_rollup.campaign_totals("saltmarch")["calls"] == 1


def test_the_total_spans_every_month_file(home):
    _call(cost_usd=1.0, ts="2026-06-14T00:00:00Z")
    _call(cost_usd=2.0, ts="2026-07-14T00:00:00Z")
    _call(cost_usd=4.0, ts="2026-08-14T00:00:00Z")

    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 7.0


# ---- the bookmark ----
def test_a_second_read_folds_in_only_what_arrived_since(home):
    _call(cost_usd=1.0, ts="2026-08-01T00:00:00Z")
    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 1.0

    stored = json.loads(usage_rollup.rollup_path().read_text(encoding="utf-8"))
    first = stored["months"]["2026-08"]
    assert first > 0

    _call(cost_usd=2.0, ts="2026-08-02T00:00:00Z")
    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 3.0

    stored = json.loads(usage_rollup.rollup_path().read_text(encoding="utf-8"))
    assert stored["months"]["2026-08"] > first


def test_deleting_the_aggregate_rebuilds_it(home):
    _call(cost_usd=3.0, ts="2026-08-01T00:00:00Z")
    usage_rollup.campaign_totals("saltmarch")
    usage_rollup.rollup_path().unlink()

    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 3.0


def test_a_torn_final_line_is_not_consumed_and_is_read_once_it_lands(home):
    _call(cost_usd=1.0, ts="2026-08-01T00:00:00Z")
    path = home / "usage" / "2026-08.jsonl"
    # A half-written row: `atomic.append_line` documents how one is produced.
    torn = json.dumps({"ts": "2026-08-02T00:00:00Z", "kind": "llm",
                       "task": "chat", "campaign": "saltmarch", "cost_usd": 2.0})
    with open(path, "a", encoding="utf-8") as f:
        f.write(torn[:20])

    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 1.0

    # The rest of the line lands. The bookmark stopped in front of it, so it is
    # counted now rather than skipped forever.
    with open(path, "a", encoding="utf-8") as f:
        f.write(torn[20:] + "\n")

    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 3.0


def test_a_month_file_that_shrank_forces_a_rebuild(home):
    _call(cost_usd=1.0, ts="2026-08-01T00:00:00Z")
    _call(cost_usd=2.0, ts="2026-08-02T00:00:00Z")
    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 3.0

    # Hand-edited down to one row. The ledger only grows, so this is the one
    # shape that proves the bookmark describes bytes that are no longer there.
    path = home / "usage" / "2026-08.jsonl"
    kept = path.read_text(encoding="utf-8").splitlines()[0]
    path.write_text(kept + "\n", encoding="utf-8")

    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 1.0


def test_a_month_file_that_vanished_forces_a_rebuild(home):
    _call(cost_usd=1.0, ts="2026-07-01T00:00:00Z")
    _call(cost_usd=2.0, ts="2026-08-01T00:00:00Z")
    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 3.0

    (home / "usage" / "2026-07.jsonl").unlink()

    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 2.0


def test_rollup_json_is_not_mistaken_for_a_ledger_month(home):
    _call(cost_usd=1.0, ts="2026-08-01T00:00:00Z")
    usage_rollup.campaign_totals("saltmarch")

    assert usage_rollup.rollup_path().name == "rollup.json"
    stored = json.loads(usage_rollup.rollup_path().read_text(encoding="utf-8"))
    assert list(stored["months"]) == ["2026-08"]


# ---- rates ----
def test_a_changed_rate_table_reprices_the_whole_aggregate(home):
    _call(ts="2026-08-01T00:00:00Z")           # unpriced
    out = usage_rollup.campaign_totals("saltmarch")
    assert out["modelled_usd"] == 0.0
    assert out["unpriced_calls"] == 1

    pricing.write_pricing({"realm/opus": {"prompt_usd_per_1k": 1.0,
                                          "completion_usd_per_1k": 2.0}})

    # Same rows, a different table: 1000 prompt at $1/1k plus 200 completion at
    # $2/1k. A stale aggregate would still be reporting the row as unpriced.
    out = usage_rollup.campaign_totals("saltmarch")
    assert out["modelled_usd"] == pytest.approx(1.4)
    assert out["modelled_calls"] == 1
    assert out["unpriced_calls"] == 0


def test_an_unreadable_rate_table_does_not_break_the_aggregate(home):
    _call(cost_usd=1.0, ts="2026-08-01T00:00:00Z")
    pricing.pricing_path().parent.mkdir(parents=True, exist_ok=True)
    pricing.pricing_path().write_text("{ not json", encoding="utf-8")

    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 1.0


# ---- no forget, on purpose ----
def test_a_reused_slug_inherits_the_ledgers_rows_exactly_as_a_full_scan_does(home):
    """There is no `forget`, and this is the shape that says why.

    A campaign id is a slug and a slug is reusable. The ledger keeps the dead
    campaign's rows because it is append-only and they record money actually
    spent -- so `usage.campaign_scenes` reports them for the new campaign too.
    The aggregate must agree with that rather than invent a second answer.
    """
    _call(cost_usd=1.0, campaign="saltmarch", ts="2026-08-01T00:00:00Z")

    assert not hasattr(usage_rollup, "forget")
    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] ==         usage.campaign_scenes("saltmarch")["totals"]["cost_usd"]


# ---- it agrees with the view it is standing in for ----
def test_the_aggregate_agrees_with_a_full_scan_of_the_same_rows(home):
    for day, cost, basis in [("01", 0.25, "billed"), ("02", 0.75, "equivalent"),
                             ("03", None, ""), ("04", 0.5, "billed")]:
        _call(ts=f"2026-08-{day}T00:00:00Z",
              **({"cost_usd": cost, "cost_basis": basis} if cost is not None else {}))

    scanned = usage.campaign_scenes("saltmarch")["totals"]
    rolled = usage_rollup.campaign_totals("saltmarch")
    for field in ("calls", "cost_usd", "estimated_usd", "modelled_usd",
                  "unpriced_calls"):
        assert rolled[field] == scanned[field], field


# ---- fail-soft ----
def test_an_unreadable_ledger_directory_reports_partial_rather_than_zero(
        home, monkeypatch):
    _call(cost_usd=1.0, ts="2026-08-01T00:00:00Z")

    def boom():
        raise OSError("ledger is locked by a sync client")

    monkeypatch.setattr(usage_rollup, "_refresh", boom)

    out = usage_rollup.campaign_totals("saltmarch")
    assert out["cost_usd"] == 0.0
    # The one field that stops a caller rendering the zero above as a fact.
    assert out["partial"] is True


def test_a_corrupt_aggregate_file_is_rebuilt_rather_than_believed(home):
    _call(cost_usd=1.0, ts="2026-08-01T00:00:00Z")
    usage_rollup.campaign_totals("saltmarch")
    usage_rollup.rollup_path().write_text("{ not json", encoding="utf-8")

    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 1.0


def test_an_aggregate_from_an_older_version_is_discarded(home):
    _call(cost_usd=1.0, ts="2026-08-01T00:00:00Z")
    usage_rollup.campaign_totals("saltmarch")
    stored = json.loads(usage_rollup.rollup_path().read_text(encoding="utf-8"))
    stored["version"] = usage_rollup.VERSION - 1
    stored["campaigns"]["saltmarch"]["cost_usd"] = 999.0
    usage_rollup.rollup_path().write_text(json.dumps(stored), encoding="utf-8")

    assert usage_rollup.campaign_totals("saltmarch")["cost_usd"] == 1.0
