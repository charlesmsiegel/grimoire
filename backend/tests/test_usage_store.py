"""The usage ledger: what one LLM call cost, and what a month of them adds up to."""

from __future__ import annotations

import json

import pytest

from grimoire.store import usage


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def _rows(home):
    return [json.loads(line)
            for path in sorted((home / "usage").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---- one row ----
def test_record_appends_a_json_line_to_the_months_ledger(home):
    usage.record(task="chat", campaign="saltmarch", scene="001-arrival",
                 model="realm/opus", prompt_tokens=1200, completion_tokens=340,
                 cost_usd=0.0125, duration_ms=4210, ts="2026-08-14T10:00:00Z")

    assert (home / "usage" / "2026-08.jsonl").exists()
    row, = _rows(home)
    assert row["kind"] == "llm"
    assert row["task"] == "chat"
    assert row["campaign"] == "saltmarch"
    assert row["scene"] == "001-arrival"
    assert row["model"] == "realm/opus"
    assert row["prompt_tokens"] == 1200
    assert row["completion_tokens"] == 340
    assert row["cost_usd"] == 0.0125
    assert row["duration_ms"] == 4210
    assert row["status"] == "ok"


def test_a_call_the_provider_did_not_price_records_no_cost_at_all(home):
    usage.record(task="chat", model="local/glm", prompt_tokens=10,
                 completion_tokens=2, ts="2026-08-14T10:00:00Z")

    row, = _rows(home)
    assert "cost_usd" not in row, "an absent price must not be recorded as $0"


def test_each_record_is_one_more_line_not_a_rewrite(home):
    for n in range(3):
        usage.record(task="chat", model="realm/opus", prompt_tokens=n,
                     ts="2026-08-14T10:00:00Z")

    lines = (home / "usage" / "2026-08.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["prompt_tokens"] for line in lines] == [0, 1, 2]


def test_a_ledger_that_cannot_be_written_costs_the_row_and_nothing_else(home, monkeypatch):
    monkeypatch.setattr(usage.paths, "home", lambda: home / "nope" / "\0bad")
    assert usage.record(task="chat", model="realm/opus") is None


# ---- rollups ----
def _seed(day: str, **fields):
    fields.setdefault("task", "chat")
    fields.setdefault("model", "realm/opus")
    usage.record(ts=f"{day}T12:00:00Z", **fields)


def test_summary_totals_the_window(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", prompt_tokens=100, completion_tokens=20, cost_usd=0.01)
    _seed("2026-08-13", prompt_tokens=200, completion_tokens=40, cost_usd=0.02)

    out = usage.summary(days=30)
    assert out["totals"]["calls"] == 2
    assert out["totals"]["prompt_tokens"] == 300
    assert out["totals"]["completion_tokens"] == 60
    assert out["totals"]["total_tokens"] == 360
    assert out["totals"]["cost_usd"] == 0.03


def test_summary_ignores_rows_older_than_the_window(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", prompt_tokens=100)
    _seed("2026-07-01", prompt_tokens=999)

    out = usage.summary(days=7)
    assert out["since"] == "2026-08-08"
    assert out["totals"]["prompt_tokens"] == 100


def test_summary_reads_the_previous_month_when_the_window_reaches_back_into_it(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-02")
    _seed("2026-08-02", prompt_tokens=1)
    _seed("2026-07-31", prompt_tokens=10)

    out = usage.summary(days=30)
    assert out["totals"]["prompt_tokens"] == 11
    assert {b["key"] for b in out["by_day"]} == {"2026-07-31", "2026-08-02"}


def test_summary_buckets_by_day_model_task_and_campaign(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", task="chat", model="realm/opus", campaign="saltmarch",
          prompt_tokens=100)
    _seed("2026-08-14", task="absorb", model="realm/haiku", campaign="saltmarch",
          prompt_tokens=10)
    _seed("2026-08-13", task="chat", model="realm/opus", campaign="winifred",
          prompt_tokens=5)

    out = usage.summary(days=30)
    assert [(b["key"], b["prompt_tokens"]) for b in out["by_day"]] == [
        ("2026-08-13", 5), ("2026-08-14", 110)]
    assert {b["key"]: b["calls"] for b in out["by_model"]} == {"realm/opus": 2, "realm/haiku": 1}
    assert {b["key"]: b["calls"] for b in out["by_task"]} == {"chat": 2, "absorb": 1}
    assert {b["key"]: b["calls"] for b in out["by_campaign"]} == {"saltmarch": 2, "winifred": 1}


def test_summary_can_be_scoped_to_one_campaign(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", prompt_tokens=100)
    _seed("2026-08-14", campaign="winifred", prompt_tokens=7)

    out = usage.summary(days=30, campaign="saltmarch")
    assert out["campaign"] == "saltmarch"
    assert out["totals"]["prompt_tokens"] == 100


def test_summary_counts_failures_without_letting_them_look_like_traffic(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", prompt_tokens=100)
    _seed("2026-08-14", status="error", error="rate_limit")

    out = usage.summary(days=30)
    assert out["totals"]["calls"] == 2
    assert out["totals"]["errors"] == 1


def test_a_cancelled_turn_is_a_call_but_not_a_failure(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", status="aborted", prompt_tokens=80)

    out = usage.summary(days=30)
    assert out["totals"]["calls"] == 1
    assert out["totals"]["errors"] == 0, (
        "a player pressing Cancel is not the provider failing them")
    assert out["totals"]["prompt_tokens"] == 80


def test_summary_keeps_subscription_billed_dollars_out_of_the_spend_total(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", cost_usd=0.02, cost_basis="billed")
    _seed("2026-08-14", cost_usd=0.50, cost_basis="equivalent")

    out = usage.summary(days=30)
    assert out["totals"]["cost_usd"] == 0.02
    assert out["totals"]["estimated_usd"] == 0.5


def test_summary_says_how_much_of_the_window_carries_no_price_at_all(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", cost_usd=0.02, cost_basis="billed")
    _seed("2026-08-14", prompt_tokens=5)

    out = usage.summary(days=30)
    assert out["totals"]["priced_calls"] == 1
    assert out["totals"]["unpriced_calls"] == 1


def test_the_session_bucket_holds_only_calls_made_since_this_process_started(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    monkeypatch.setattr(usage, "_SESSION_START", "2026-08-14T09:00:00Z")
    usage.record(task="chat", model="realm/opus", prompt_tokens=3,
                 ts="2026-08-14T08:59:59Z")
    usage.record(task="chat", model="realm/opus", prompt_tokens=40,
                 ts="2026-08-14T09:00:01Z")

    out = usage.summary(days=30)
    assert out["totals"]["prompt_tokens"] == 43
    assert out["session"]["prompt_tokens"] == 40
    assert out["session_started"] == "2026-08-14T09:00:00Z"


def test_todays_bucket_is_the_calendar_day_not_the_process(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    monkeypatch.setattr(usage, "_SESSION_START", "2026-08-14T09:00:00Z")
    _seed("2026-08-14", prompt_tokens=40)
    _seed("2026-08-13", prompt_tokens=5)

    out = usage.summary(days=30)
    assert out["today"]["prompt_tokens"] == 40


def test_an_empty_ledger_summarizes_to_zero_rather_than_failing(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    out = usage.summary(days=30)
    assert out["totals"] == {"calls": 0, "errors": 0, "prompt_tokens": 0,
                            "completion_tokens": 0, "total_tokens": 0,
                            "cache_read_tokens": 0, "cache_write_tokens": 0,
                            "cost_usd": 0.0, "estimated_usd": 0.0,
                            "priced_calls": 0, "unpriced_calls": 0, "duration_ms": 0}
    assert out["by_day"] == []


def test_a_torn_or_hand_edited_line_is_skipped_not_fatal(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", prompt_tokens=100)
    path = home / "usage" / "2026-08.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"ts": "2026-08-1\n[]\n',
                    encoding="utf-8")

    out = usage.summary(days=30)
    assert out["totals"]["calls"] == 1


def test_days_is_clamped_so_a_hostile_query_cannot_walk_the_whole_disk(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    assert usage.summary(days=0)["days"] == 1
    assert usage.summary(days=10_000)["days"] == usage.MAX_DAYS


#: What `llm._stamp` puts in the holder before the first provider attempt. A
#: meter whose holder is still empty describes a request that never went out.
_SENT = {"model": "realm/opus", "connection": "Main", "provider": "openrouter",
         "attempts": 1}


# ---- the meter ----
def test_a_call_that_never_went_out_is_not_a_row(home):
    """The absorb budget refuses a step it has no time left for, and the client
    is never awaited. A row for it would be a call that cost nothing, took no
    time and never happened -- pure noise in every rollup."""
    from grimoire.llm_errors import LLMError

    with pytest.raises(LLMError), usage.meter("dossier", campaign="saltmarch"):
        raise LLMError("timeout", "the absorb budget is spent")

    assert _rows(home) == []



def test_the_meter_records_what_the_facade_filled_in(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    with usage.meter("chat", campaign="saltmarch", scene="001-arrival") as m:
        m.usage.update({"model": "realm/opus", "connection": "Main",
                        "prompt_tokens": 12, "completion_tokens": 3,
                        "cost_usd": 0.004, "cost_basis": "billed"})

    row, = _rows(home)
    assert row["model"] == "realm/opus"
    assert row["connection"] == "Main"
    assert row["completion_tokens"] == 3
    assert row["cost_usd"] == 0.004
    assert row["campaign"] == "saltmarch"
    assert row["status"] == "ok"
    assert row["duration_ms"] >= 0


def test_a_failed_call_is_still_a_row_carrying_the_failure_kind(home):
    from grimoire.llm_errors import LLMError

    with pytest.raises(LLMError), usage.meter("chat", campaign="saltmarch") as m:
        m.usage.update(_SENT)       # the facade stamped the route, then failed
        raise LLMError("rate_limit", "slow down")

    row, = _rows(home)
    assert row["status"] == "error"
    assert row["error"] == "rate_limit"


def test_the_meter_records_once_however_many_times_it_is_finished(home):
    m = usage.meter("chat")
    m.usage.update(_SENT)
    m.done()
    m.done()
    with m:
        pass
    assert len(_rows(home)) == 1


def test_the_session_bucket_is_clipped_to_the_window(home, monkeypatch):
    """`summary` only reads the window, so a process older than it reports a
    session that is really the window. Pinned rather than fixed -- see
    `summary`'s docstring for why widening the scan is the worse trade."""
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    monkeypatch.setattr(usage, "_SESSION_START", "2026-07-01T09:00:00Z")
    _seed("2026-07-02", prompt_tokens=500)      # this session, outside days=2
    _seed("2026-08-14", prompt_tokens=7)

    out = usage.summary(days=2)
    assert out["session"]["prompt_tokens"] == 7
    assert out["session"] == out["totals"]


def test_a_hand_edited_negative_count_cannot_subtract_from_a_total(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", prompt_tokens=100)
    path = home / "usage" / "2026-08.jsonl"
    path.write_text(path.read_text(encoding="utf-8")
                    + json.dumps({"ts": "2026-08-14T12:00:00Z", "task": "chat",
                                  "prompt_tokens": -999999, "status": "ok"}) + "\n",
                    encoding="utf-8")

    assert usage.summary(days=30)["totals"]["prompt_tokens"] == 100


def test_a_month_file_is_never_slurped_whole(home, monkeypatch):
    """A year of heavy play is tens of megabytes; a report that needs two rows
    at a time must not hold all of them plus a list of every line."""
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", prompt_tokens=1)

    def refuse(*_a, **_kw):
        raise AssertionError("the ledger was read whole")

    monkeypatch.setattr(usage.Path, "read_text", refuse)
    assert usage.summary(days=30)["totals"]["calls"] == 1


def test_a_call_the_provider_did_not_count_records_no_tokens_at_all(home):
    """The same rule the price gets, for the same reason: a row saying zero
    tokens is a row saying the call used none, and an endpoint that reports
    nothing has not said that."""
    usage.record(task="chat", model="local/glm", ts="2026-08-14T10:00:00Z")

    row, = _rows(home)
    assert "prompt_tokens" not in row
    assert "completion_tokens" not in row


def test_a_provider_that_counted_exactly_zero_is_recorded_as_zero(home):
    """`0` and "not reported" are different answers and must stay different --
    an empty reply really does complete zero tokens."""
    usage.record(task="chat", model="realm/opus", prompt_tokens=12,
                 completion_tokens=0, ts="2026-08-14T10:00:00Z")

    row, = _rows(home)
    assert row["completion_tokens"] == 0


def test_a_row_is_never_written_as_a_json_no_other_reader_can_parse(home):
    """`json.dumps` writes `Infinity` for an infinite float and reads it back
    happily, so a Python-only round trip hides it -- and every other JSON reader
    in the world rejects the line. The row is dropped instead."""
    assert usage.record(task="chat", model="realm/opus", cost_usd=float("inf"),
                        ts="2026-08-14T10:00:00Z") is None
    assert usage.record(task="chat", model="realm/opus", cost_usd=float("nan"),
                        ts="2026-08-14T10:00:00Z") is None
    assert _rows(home) == []


def test_a_value_that_will_not_serialize_costs_the_row_and_nothing_else(home):
    assert usage.record(task="chat", model=object()) is None


def test_no_field_can_turn_a_bookkeeping_call_into_a_failed_turn(home):
    """`record` runs inside the SSE finalizers, so "never raises" has to hold
    for the coercions as well as the write -- the row is built inside the same
    guard for that reason."""
    for bad in ({"prompt_tokens": "many"}, {"duration_ms": "ages"},
                {"cost_usd": "free"}, {"attempts": "twice"},
                {"completion_tokens": []}):
        assert usage.record(task="chat", model="realm/opus", **bad) is None, bad
    assert _rows(home) == []


def test_a_caller_walking_away_from_a_one_shot_call_is_aborted_not_an_error(home):
    """`asyncio.CancelledError` unwinds the `with` at every `.complete()` site
    when a client disconnects. Recording it as an error would make a user who
    closes a tab mid-suggestion look like a provider failing them -- which is
    exactly what the streaming path takes care to avoid."""
    import asyncio

    for cancellation in (asyncio.CancelledError(), GeneratorExit()):
        with pytest.raises(BaseException):
            with usage.meter("suggestions", campaign="saltmarch") as m:
                m.usage.update(_SENT)
                raise cancellation

    rows = _rows(home)
    assert [r["status"] for r in rows] == ["aborted", "aborted"]
    assert all("error" not in r for r in rows)


# ---- the cache split (#148) ----

def test_a_cached_prompt_records_its_split_beside_the_prompt_count(home):
    usage.record(task="chat", prompt_tokens=5000, completion_tokens=120,
                 cache_read_tokens=4096, cache_write_tokens=512,
                 ts="2026-08-14T10:00:00Z")

    row, = _rows(home)
    assert row["prompt_tokens"] == 5000
    assert row["cache_read_tokens"] == 4096
    assert row["cache_write_tokens"] == 512


def test_a_provider_that_says_nothing_about_caching_records_no_cache_columns(home):
    """Absent, not zero — the rule the token counts already follow. Zero would
    claim a call cached nothing, which is not what an endpoint that never
    mentions caching has said."""
    usage.record(task="chat", prompt_tokens=100, completion_tokens=10,
                 ts="2026-08-14T10:00:00Z")

    row, = _rows(home)
    assert "cache_read_tokens" not in row and "cache_write_tokens" not in row


def test_the_cache_split_is_summed_apart_from_the_token_total(home, monkeypatch):
    """The line worth reading twice: a cache read is part of the prompt the
    provider already counted, so folding it into `total_tokens` would bill a
    cached prefix to the total twice over."""
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    usage.record(task="chat", prompt_tokens=5000, completion_tokens=100,
                 cache_read_tokens=4000, cache_write_tokens=500,
                 ts="2026-08-14T10:00:00Z")
    usage.record(task="chat", prompt_tokens=6000, completion_tokens=200,
                 cache_read_tokens=5500, ts="2026-08-14T11:00:00Z")

    totals = usage.summary(days=30)["totals"]

    assert totals["prompt_tokens"] == 11000
    assert totals["total_tokens"] == 11000 + 300      # prompt + completion, and nothing else
    assert totals["cache_read_tokens"] == 9500
    assert totals["cache_write_tokens"] == 500


def test_a_window_with_no_caching_reports_zeroes_rather_than_missing_keys(home, monkeypatch):
    """A bucket is read straight by the frontend, so the columns are always
    there even when nothing cached — absence is a per-ROW claim, not a
    per-bucket one."""
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    usage.record(task="chat", prompt_tokens=100, completion_tokens=10,
                 ts="2026-08-14T10:00:00Z")

    totals = usage.summary(days=30)["totals"]

    assert totals["cache_read_tokens"] == 0 and totals["cache_write_tokens"] == 0


def test_a_hand_edited_cache_count_costs_that_field_and_not_the_report(home, monkeypatch):
    """Same defensive read the other counts get: a ledger line is a file a human
    can edit, and a string where a number belongs must not take the rollup down
    or subtract from a month."""
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    (home / "usage").mkdir(parents=True)
    (home / "usage" / "2026-08.jsonl").write_text(
        json.dumps({"ts": "2026-08-14T10:00:00Z", "kind": "llm", "task": "chat",
                    "prompt_tokens": 100, "completion_tokens": 10,
                    "cache_read_tokens": "loads", "cache_write_tokens": -999}) + "\n",
        encoding="utf-8")

    totals = usage.summary(days=30)["totals"]

    assert totals["calls"] == 1
    assert totals["cache_read_tokens"] == 0 and totals["cache_write_tokens"] == 0


# ---- one scene's turns (#153) ----
def test_scene_usage_lists_only_that_scenes_calls(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", scene="001-arrival",
          prompt_tokens=100, completion_tokens=20, cost_usd=0.01)
    _seed("2026-08-14", campaign="saltmarch", scene="002-departure",
          prompt_tokens=999, cost_usd=9.0)
    _seed("2026-08-14", campaign="realm", scene="001-arrival",
          prompt_tokens=888, cost_usd=8.0)

    out = usage.scene_usage("saltmarch", "001-arrival", since="2026-08-01")
    assert out["totals"]["calls"] == 1
    assert out["totals"]["total_tokens"] == 120
    assert out["totals"]["cost_usd"] == 0.01
    assert [turn["prompt_tokens"] for turn in out["turns"]] == [100]


def test_scene_usage_breaks_the_scene_down_by_task(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    for task, cost in (("chat", 0.01), ("chat", 0.02), ("retry", 0.05)):
        _seed("2026-08-14", task=task, campaign="saltmarch", scene="001-arrival",
              cost_usd=cost)

    out = usage.scene_usage("saltmarch", "001-arrival", since="2026-08-01")
    assert [(b["key"], b["calls"], b["cost_usd"]) for b in out["by_task"]] == [
        ("chat", 2, 0.03), ("retry", 1, 0.05)]


def test_scene_turns_come_back_newest_first_whatever_order_they_landed_in(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    # A slow turn that finished after a fast one started files its row later,
    # so file order is not turn order. Sorting on the stamp is what fixes it.
    for stamp in ("2026-08-14T10:00:00Z", "2026-08-14T09:00:00Z", "2026-08-14T11:00:00Z"):
        usage.record(task="chat", campaign="saltmarch", scene="001-arrival", ts=stamp,
                     prompt_tokens=1)

    out = usage.scene_usage("saltmarch", "001-arrival", since="2026-08-01")
    assert [turn["ts"] for turn in out["turns"]] == [
        "2026-08-14T11:00:00Z", "2026-08-14T10:00:00Z", "2026-08-14T09:00:00Z"]


def test_a_long_scenes_list_is_cut_but_its_totals_are_not(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    for _ in range(5):
        _seed("2026-08-14", campaign="saltmarch", scene="001-arrival",
              prompt_tokens=10, cost_usd=0.01)

    out = usage.scene_usage("saltmarch", "001-arrival", since="2026-08-01", limit=2)
    assert len(out["turns"]) == 2
    assert out["listed"] == 2
    assert out["truncated"] is True
    assert out["totals"]["calls"] == 5, "the numbers must not move when the list is cut"
    assert out["totals"]["prompt_tokens"] == 50


def test_a_scene_turn_the_provider_never_priced_is_unpriced_not_free(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", scene="001-arrival", prompt_tokens=10)

    turn, = usage.scene_usage("saltmarch", "001-arrival", since="2026-08-01")["turns"]
    assert turn["cost_usd"] is None, "$0.00 would be a claim the provider never made"


def test_a_scenes_window_starts_at_the_date_it_was_created(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-06-02", campaign="saltmarch", scene="001-arrival", cost_usd=0.5)
    _seed("2026-08-14", campaign="saltmarch", scene="001-arrival", cost_usd=0.25)

    # The default 30-day window would miss June entirely and report a scene
    # played all summer as having cost a quarter.
    out = usage.scene_usage("saltmarch", "001-arrival", since="2026-06-01T09:00:00Z")
    assert out["since"] == "2026-06-01"
    assert out["totals"]["cost_usd"] == 0.75


def test_a_scene_with_no_usable_created_stamp_falls_back_to_the_default_window(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    out = usage.scene_usage("saltmarch", "001-arrival", since="not a date")
    assert out["since"] == "2026-07-16"


def test_a_scene_stamped_in_the_future_scans_today_rather_than_nothing(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", scene="001-arrival", cost_usd=0.25)

    out = usage.scene_usage("saltmarch", "001-arrival", since="2027-01-01")
    assert out["since"] == "2026-08-14"
    assert out["totals"]["cost_usd"] == 0.25


def test_a_scenes_scan_cannot_be_widened_past_the_ledgers_own_bound(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    out = usage.scene_usage("saltmarch", "001-arrival", since="2019-01-01")
    assert out["since"] == "2025-08-14", "MAX_DAYS back, not the date asked for"


def test_a_scene_that_has_generated_nothing_reports_zeroes(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    out = usage.scene_usage("saltmarch", "001-arrival", since="2026-08-01")
    assert out["turns"] == []
    assert out["totals"]["calls"] == 0
    assert out["totals"]["cost_usd"] == 0.0


def test_a_scene_turn_keeps_the_cache_split_out_of_its_token_total(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", scene="001-arrival",
          prompt_tokens=1000, completion_tokens=50,
          cache_read_tokens=900, cache_write_tokens=100)

    turn, = usage.scene_usage("saltmarch", "001-arrival", since="2026-08-01")["turns"]
    assert turn["total_tokens"] == 1050, "a cached prefix must not be counted twice"
    assert turn["cache_read_tokens"] == 900
    assert turn["cache_write_tokens"] == 100


def test_a_hand_edited_turn_row_cannot_take_the_panel_down(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    path = home / "usage" / "2026-08.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "ts": "2026-08-14T10:00:00Z", "campaign": "saltmarch", "scene": "001-arrival",
        "task": {"nested": "dict"}, "model": None, "prompt_tokens": "lots",
        "attempts": -4, "error": 17}) + "\n", encoding="utf-8")

    turn, = usage.scene_usage("saltmarch", "001-arrival", since="2026-08-01")["turns"]
    assert turn["task"] == "unknown"
    assert turn["model"] == "unknown"
    assert turn["prompt_tokens"] == 0
    assert turn["attempts"] == 1
    assert turn["error"] == ""


# ---- budgets (#153) ----
def test_a_campaign_with_no_budget_is_not_measured_at_all(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", cost_usd=5.0)

    out = usage.budget("saltmarch", None)
    assert out["level"] == "off"
    assert "spent_usd" not in out, "nobody asked what this campaign cost"


def test_a_budget_reports_the_campaigns_share_of_it(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", cost_usd=2.0)
    _seed("2026-08-14", campaign="realm", cost_usd=90.0)

    out = usage.budget("saltmarch", "10.00")
    assert out["spent_usd"] == 2.0
    assert out["fraction"] == 0.2
    assert out["level"] == "ok"
    assert out["limit_usd"] == 10.0


def test_the_warning_starts_before_the_budget_is_broken(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", cost_usd=8.0)

    assert usage.budget("saltmarch", 10)["level"] == "warn"


def test_spending_the_whole_budget_is_over_not_merely_warned(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", cost_usd=10.0)

    assert usage.budget("saltmarch", 10)["level"] == "over"


def test_a_monthly_budget_starts_again_on_the_first(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-07-31", campaign="saltmarch", cost_usd=40.0)
    _seed("2026-08-01", campaign="saltmarch", cost_usd=1.0)

    out = usage.budget("saltmarch", 10, "monthly")
    assert out["since"] == "2026-08-01"
    assert out["spent_usd"] == 1.0
    assert out["level"] == "ok", "last month's spend is last month's problem"


def test_a_total_budget_counts_the_months_before_this_one(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-07-31", campaign="saltmarch", cost_usd=40.0)
    _seed("2026-08-01", campaign="saltmarch", cost_usd=1.0)

    out = usage.budget("saltmarch", 10, "total")
    assert out["spent_usd"] == 41.0
    assert out["level"] == "over"


def test_a_total_budget_still_scans_no_further_than_the_ledger_ever_does(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    assert usage.budget("saltmarch", 10, "total")["since"] == "2025-08-14"


def test_subscription_billed_dollars_are_reported_but_never_charged(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", cost_usd=50.0, cost_basis="equivalent")
    _seed("2026-08-14", campaign="saltmarch", cost_usd=1.0, cost_basis="billed")

    out = usage.budget("saltmarch", 10)
    assert out["spent_usd"] == 1.0, "a budget is money paid, not money saved"
    assert out["estimated_usd"] == 50.0
    assert out["level"] == "ok"


def test_a_budget_says_how_much_of_the_period_carries_no_price(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", cost_usd=1.0)
    _seed("2026-08-14", campaign="saltmarch", prompt_tokens=5)

    out = usage.budget("saltmarch", 10)
    assert out["unpriced_calls"] == 1, "the spend figure is a floor, and says so"
    assert out["calls"] == 2


@pytest.mark.parametrize("stored", ["twelve", "", "-4", "0", None, float("inf"), {}])
def test_a_budget_nobody_can_read_is_no_budget_rather_than_a_broken_one(home, stored):
    assert usage.budget("saltmarch", stored)["level"] == "off"


def test_an_unknown_period_is_read_as_the_monthly_one(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    out = usage.budget("saltmarch", 10, "fortnightly")
    assert out["period"] == "monthly"
    assert out["since"] == "2026-08-01"


# ---- a renamed scene keeps its cost (#153) ----
def _pin(monkeypatch, day: str) -> None:
    """Freeze both clocks the ledger reads. `_seed` stamps its own rows, but a
    rename stamps itself from `_now`, and a rename dated by the real wall clock
    falls outside a window pinned to a made-up day."""
    monkeypatch.setattr(usage, "_today", lambda: day)
    monkeypatch.setattr(usage, "_now", lambda: f"{day}T12:00:00Z")


def test_a_renamed_scene_still_reports_what_it_spent_before_the_rename(home, monkeypatch):
    """Setting a date on a scene renames its file, which happens to most scenes
    a turn or two in. Without the trail, the panel reports the spend since the
    rename and calls it the scene's."""
    _pin(monkeypatch, "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", scene="001--arrival", cost_usd=0.25)
    usage.repoint_scenes("saltmarch", {"001--arrival": "001--2026-08-14--arrival"})
    _seed("2026-08-14", campaign="saltmarch", scene="001--2026-08-14--arrival", cost_usd=0.75)

    out = usage.scene_usage("saltmarch", "001--2026-08-14--arrival", since="2026-08-01")
    assert out["totals"]["cost_usd"] == 1.0
    assert out["totals"]["calls"] == 2


def test_a_rename_is_appended_rather_than_rewriting_what_was_already_filed(home):
    usage.record(task="chat", campaign="saltmarch", scene="001--arrival",
                 cost_usd=0.25, ts="2026-08-14T10:00:00Z")
    usage.repoint_scenes("saltmarch", {"001--arrival": "001--dated"})

    call, rename = _rows(home)
    assert call["scene"] == "001--arrival", "an append-only ledger rewrites nothing"
    # Spelled out rather than compared against a dict holding `rename["ts"]`,
    # which would have asserted the stamp equals itself and proved nothing about
    # it -- and the stamp is exactly what the read side windows and cuts on.
    assert rename["kind"] == "rename"
    assert rename["campaign"] == "saltmarch"
    assert rename["scene"] == "001--dated"
    assert rename["was"] == "001--arrival"
    assert usage._valid_day(rename["ts"]) == rename["ts"][:10], \
        "a rename outside a scannable window is a trail nothing can follow"
    assert set(rename) == {"ts", "kind", "campaign", "scene", "was"}


def test_a_rename_row_is_not_a_call_anybody_was_charged_for(home, monkeypatch):
    _pin(monkeypatch, "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", scene="001--arrival", cost_usd=0.25)
    usage.repoint_scenes("saltmarch", {"001--arrival": "001--dated"})

    assert usage.summary(days=30)["totals"]["calls"] == 1
    assert [b["key"] for b in usage.summary(days=30)["by_task"]] == ["chat"]
    assert usage.budget("saltmarch", 10)["calls"] == 1
    assert usage.scene_usage("saltmarch", "001--dated", since="2026-08-01")["totals"]["calls"] == 1


def test_a_chain_of_renames_is_followed_all_the_way_back(home, monkeypatch):
    _pin(monkeypatch, "2026-08-14")
    _seed("2026-08-14", campaign="saltmarch", scene="a", cost_usd=0.1)
    usage.repoint_scenes("saltmarch", {"a": "b"})
    _seed("2026-08-14", campaign="saltmarch", scene="b", cost_usd=0.2)
    usage.repoint_scenes("saltmarch", {"b": "c"})
    _seed("2026-08-14", campaign="saltmarch", scene="c", cost_usd=0.3)

    assert usage.scene_usage("saltmarch", "c", since="2026-08-01")["totals"]["cost_usd"] == 0.6


def test_another_campaigns_rename_cannot_pull_its_scene_into_this_one(home, monkeypatch):
    _pin(monkeypatch, "2026-08-14")
    _seed("2026-08-14", campaign="realm", scene="a", cost_usd=9.0)
    usage.repoint_scenes("realm", {"a": "c"})
    _seed("2026-08-14", campaign="saltmarch", scene="c", cost_usd=0.3)

    assert usage.scene_usage("saltmarch", "c", since="2026-08-01")["totals"]["cost_usd"] == 0.3


def test_a_rename_trail_that_loops_cannot_hang_the_report(home, monkeypatch):
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    path = home / "usage" / "2026-08.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(row) for row in [
        {"ts": "2026-08-14T10:00:00Z", "kind": "rename", "campaign": "c",
         "scene": "b", "was": "a"},
        {"ts": "2026-08-14T10:00:01Z", "kind": "rename", "campaign": "c",
         "scene": "a", "was": "b"},
    ]) + "\n", encoding="utf-8")

    assert usage.scene_usage("c", "a", since="2026-08-01")["totals"]["calls"] == 0


def test_a_rename_nothing_can_be_written_for_does_not_fail_the_rename(home, monkeypatch):
    monkeypatch.setattr(usage.paths, "home", lambda: home / "nope" / "\0bad")
    usage.repoint_scenes("saltmarch", {"a": "b"})   # must not raise


def test_a_rename_to_the_same_id_files_nothing(home):
    usage.repoint_scenes("saltmarch", {"a": "a"})
    assert not (home / "usage").exists()


def test_a_recycled_scene_id_is_not_charged_to_the_scene_that_gave_it_up(home, monkeypatch):
    """`paths.uniquify` checks only what exists now, so the id a scene is
    renamed off is free for the next scene to take. Rows written under it after
    that rename are somebody else's."""
    _pin(monkeypatch, "2026-08-14")
    usage.record(task="chat", campaign="saltmarch", scene="001--x", cost_usd=0.25,
                 ts="2026-08-14T10:00:00Z")
    usage.repoint_scenes("saltmarch", {"001--x": "001--dated"})   # stamped 12:00
    # A different scene, created later, takes the id the first one gave up.
    usage.record(task="chat", campaign="saltmarch", scene="001--x", cost_usd=9.0,
                 ts="2026-08-14T13:00:00Z")

    out = usage.scene_usage("saltmarch", "001--dated", since="2026-08-01")
    assert out["totals"]["cost_usd"] == 0.25, "only the rows written before the rename"
    assert out["totals"]["calls"] == 1


def test_a_chain_cuts_each_id_at_its_own_rename_not_at_the_end(home, monkeypatch):
    _pin(monkeypatch, "2026-08-14")
    usage.record(task="chat", campaign="c", scene="a", cost_usd=0.1,
                 ts="2026-08-14T09:00:00Z")
    usage.repoint_scenes("c", {"a": "b"})                          # stamped 12:00
    # Somebody else takes `a` back, and spends under it after the rename.
    usage.record(task="chat", campaign="c", scene="a", cost_usd=5.0,
                 ts="2026-08-14T13:00:00Z")
    usage.record(task="chat", campaign="c", scene="b", cost_usd=0.2,
                 ts="2026-08-14T14:00:00Z")

    out = usage.scene_usage("c", "b", since="2026-08-01")
    assert out["totals"]["cost_usd"] == 0.3


@pytest.mark.parametrize("stored, expected", [
    ("total", "total"), ("Total", "total"), ("  TOTAL ", "total"),
    ("monthly", "monthly"), ("fortnightly", "monthly"), (None, "monthly"), (7, "monthly"),
])
def test_a_period_is_read_as_written_whatever_case_it_was_written_in(home, stored, expected):
    """The value reaches here from a hand-edited campaign.md as readily as from
    the form, and reading `Total` as `monthly` would silently halve the window
    somebody plainly asked for."""
    assert usage.normalize_period(stored) == expected


def test_a_title_renamed_and_renamed_back_does_not_silence_the_scene(home, monkeypatch):
    """One typo and its fix walks a trail that returns to where it started
    (a -> b -> a). The live id must never take a cutoff from its own trail, or
    every row the scene has filed since would drop out of its own total."""
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    # Two renames at two different moments, which is what makes the trail a
    # cycle rather than a pair of simultaneous edits.
    stamps = ["2026-08-14T10:00:00Z", "2026-08-14T12:00:00Z"]
    # Holds its last value rather than running out: `scene_usage` stamps its own
    # `generated_at` from the same clock, and a StopIteration there would fail
    # the test for a reason that has nothing to do with renames.
    monkeypatch.setattr(usage, "_now", lambda: stamps.pop(0) if len(stamps) > 1
                        else stamps[0])
    usage.record(task="chat", campaign="c", scene="a", cost_usd=0.1,
                 ts="2026-08-14T09:00:00Z")
    usage.repoint_scenes("c", {"a": "b"})                      # 10:00
    usage.record(task="chat", campaign="c", scene="b", cost_usd=0.2,
                 ts="2026-08-14T11:00:00Z")
    usage.repoint_scenes("c", {"b": "a"})                      # 12:00
    usage.record(task="chat", campaign="c", scene="a", cost_usd=0.3,
                 ts="2026-08-14T13:00:00Z")

    out = usage.scene_usage("c", "a", since="2026-08-01")
    assert out["totals"]["cost_usd"] == 0.6, "every row is this one scene's"
    assert out["totals"]["calls"] == 3
