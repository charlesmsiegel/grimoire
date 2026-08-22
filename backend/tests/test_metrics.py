"""Performance metrics: percentile math, and what a window of calls says about
latency, failure and trend (#154)."""

from __future__ import annotations

import pytest

from grimoire.store import logs, metrics, usage


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.forget_file_sizes()
    logs.apply_level("info")
    yield tmp_path
    logs.forget_file_sizes()
    logs.apply_level("info")


def _today() -> str:
    return usage._today()


def _call(ms: int, *, task: str = "chat", model: str = "realm/opus",
          status: str = "ok", day: str = "", campaign: str = "") -> None:
    usage.record(task=task, model=model, duration_ms=ms, status=status,
                 campaign=campaign, ts=f"{day or _today()}T10:00:00Z")


# ---- the percentile itself ----
def test_a_known_distribution_gives_the_textbook_cut_points():
    """1..100 by the inclusive method: rank (n-1)·q, interpolated, then rounded
    half up. `statistics.quantiles(..., method="inclusive")` gives 50.5 / 90.1 /
    99.01 for these three, which is what this must agree with."""
    values = list(range(1, 101))

    assert metrics.percentile(values, 50) == 51   # 50.5, half UP -- not round()'s 50
    assert metrics.percentile(values, 90) == 90   # 90.1
    assert metrics.percentile(values, 99) == 99   # 99.01
    assert metrics.percentile(values, 0) == 1
    assert metrics.percentile(values, 100) == 100


def test_it_agrees_with_the_stdlib_method_it_claims_to_implement():
    """The docstring says "inclusive"; this is what holds it to that, so a
    future rewrite cannot quietly become a different percentile."""
    import statistics

    for values in ([1, 2, 3, 4], list(range(1, 101)), [5, 5, 5, 900]):
        cuts = statistics.quantiles(values, n=100, method="inclusive")
        for q in metrics.PERCENTILES:
            assert metrics.percentile(values, q) == int(cuts[q - 1] + 0.5)


def test_a_percentile_interpolates_between_neighbours():
    assert metrics.percentile([0, 100], 50) == 50
    assert metrics.percentile([0, 10, 20, 30], 50) == 15


def test_one_sample_is_its_own_every_percentile():
    """`statistics.quantiles` raises here, and one call in the window is the
    normal state of a fresh install."""
    assert metrics.percentile([4210], 50) == 4210
    assert metrics.percentile([4210], 99) == 4210


def test_no_samples_is_zero_rather_than_an_exception():
    assert metrics.percentile([], 90) == 0
    assert metrics.percentiles([]) == {"p50": 0, "p90": 0, "p99": 0, "min": 0, "max": 0}


def test_a_percentile_outside_the_range_is_clamped_not_indexed_out_of_bounds():
    assert metrics.percentile([1, 2, 3], 500) == 3
    assert metrics.percentile([1, 2, 3], -20) == 1


def test_percentiles_do_not_reorder_the_callers_list():
    values = [30, 10, 20]

    metrics.percentiles(values)

    assert values == [30, 10, 20]


# ---- the report ----
def test_latency_is_reported_per_task_and_per_model(home):
    for ms in (100, 200, 300, 400):
        _call(ms, task="chat", model="realm/opus")
    _call(9000, task="dossier", model="realm/haiku")

    out = metrics.performance(30)
    assert out["totals"]["calls"] == 5
    chat = next(b for b in out["by_task"] if b["key"] == "chat")
    assert chat["calls"] == 4
    assert chat["p50"] == 250 and chat["min"] == 100 and chat["max"] == 400
    assert [b["key"] for b in out["by_model"]] == ["realm/opus", "realm/haiku"]


def test_the_error_rate_has_the_successful_calls_as_its_denominator(home):
    """This is why `by_task` counts errors from the LEDGER: the error store
    holds failures, and a rate needs the calls that worked."""
    for _ in range(3):
        _call(100, status="ok")
    _call(100, status="error")

    chat = next(b for b in metrics.performance(30)["by_task"] if b["key"] == "chat")
    assert chat["calls"] == 4 and chat["errors"] == 1
    assert chat["error_rate"] == 0.25


def test_the_error_block_comes_from_the_error_store_and_may_differ(home):
    """The two totals are different questions, not a disagreement: the ledger
    counts calls that failed, the store counts failures -- which includes the
    ones that were never a call."""
    _call(100, status="ok")
    logs.record("error", "dossier", "empty dossier reply", kind="empty_reply")

    out = metrics.performance(30)
    assert out["totals"]["errors"] == 0            # no ledger row failed
    assert out["errors"]["total"] == 1             # but something did go wrong
    assert out["errors"]["modules"][0]["module"] == "dossier"


def test_by_day_is_a_trend_of_that_days_own_percentiles(home):
    from datetime import date, timedelta
    today = date.fromisoformat(_today())
    yesterday = (today - timedelta(days=1)).isoformat()
    _call(100, day=yesterday)
    _call(200, day=yesterday)
    _call(9000, day=today.isoformat())

    out = metrics.performance(30)
    assert [b["key"] for b in out["by_day"]] == [yesterday, today.isoformat()]
    assert out["by_day"][0]["p50"] == 150
    assert out["by_day"][1]["p50"] == 9000


def test_a_rename_row_is_not_a_call_that_took_no_time(home):
    _call(400)
    usage.record(task="rename", kind=usage.KIND_RENAME, campaign="saltmarch",
                 ts=f"{_today()}T10:00:00Z")

    out = metrics.performance(30)
    assert out["totals"]["calls"] == 1
    assert out["totals"]["p50"] == 400


def test_a_hand_edited_duration_costs_its_own_row_and_not_the_report(home):
    _call(400)
    usage.record(task="chat", duration_ms=0, status="ok", ts=f"{_today()}T10:00:00Z")
    # A ledger a human edited: a string where the milliseconds belong.
    ledger = home / "usage" / f"{_today()[:7]}.jsonl"
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(f'{{"ts": "{_today()}T10:00:00Z", "kind": "llm", "task": "chat", '
                 '"duration_ms": "ages", "status": "ok"}\n')

    out = metrics.performance(30)
    assert out["totals"]["calls"] == 3
    assert out["totals"]["max"] == 400             # the junk counted as 0, not as a crash


def test_a_call_belonging_to_no_task_is_bucketed_rather_than_dropped(home):
    ledger_day = _today()
    usage.record(task="", duration_ms=50, ts=f"{ledger_day}T10:00:00Z")

    out = metrics.performance(30)
    assert [b["key"] for b in out["by_task"]] == ["unknown"]


def test_the_window_can_be_scoped_to_one_campaign(home):
    _call(100, campaign="saltmarch")
    _call(9000, campaign="realm")

    out = metrics.performance(30, campaign="saltmarch")
    assert out["totals"]["calls"] == 1 and out["totals"]["max"] == 100
    assert out["campaign"] == "saltmarch"


def test_a_silly_window_is_clamped_rather_than_refused(home):
    assert metrics.performance(100000)["days"] == logs.MAX_DAYS
    assert metrics.performance(0)["days"] == 1


def test_an_empty_store_reports_zeroes_rather_than_failing(home):
    out = metrics.performance(30)

    assert out["totals"] == {"key": "", "calls": 0, "errors": 0, "error_rate": 0.0,
                             "sampled": False, "p50": 0, "p90": 0, "p99": 0,
                             "min": 0, "max": 0}
    assert out["by_task"] == [] and out["by_day"] == []


# ---- the sample cap ----
def test_past_the_cap_the_distribution_is_sampled_and_says_so(home, monkeypatch):
    monkeypatch.setattr(metrics, "MAX_SAMPLES", 10)
    series = metrics._Series()
    for ms in range(1, 31):
        series.add(ms, failed=False)

    report = series.report("chat")
    assert report["calls"] == 30
    assert report["sampled"] is True
    # Both tails survive: replacement happens in the middle, never at the ends.
    assert report["min"] == 1
    assert report["max"] == 30


def test_under_the_cap_nothing_claims_to_have_been_sampled(home):
    _call(100)

    assert metrics.performance(30)["totals"]["sampled"] is False


def test_a_breakdown_stops_opening_buckets_and_folds_the_rest_into_one(home,
                                                                      monkeypatch):
    """`MAX_SAMPLES` bounds what one bucket holds; this bounds how many there
    are. `model` is a string out of a file a human can edit, and a drifted
    column -- a per-request id where a model name belongs -- would otherwise
    open a `_Series`, each with its own `random.Random`, per row."""
    monkeypatch.setattr(metrics, "MAX_BUCKETS", 3)
    for i in range(10):
        _call(100, model=f"realm/model-{i}")

    by_model = metrics.performance(30)["by_model"]
    # The cap bounds how many labels open a bucket; the overflow bucket is the
    # one they are folded into, so a capped breakdown is `cap` + it.
    assert len(by_model) == 4
    assert {b["key"] for b in by_model} == {"realm/model-0", "realm/model-1",
                                            "realm/model-2", metrics.OVERFLOW}
    # The numbers still add up to what was read -- only the naming got coarse.
    assert sum(b["calls"] for b in by_model) == 10
    assert metrics.performance(30)["totals"]["calls"] == 10


def test_the_trend_is_not_capped_because_its_labels_are_the_window(home,
                                                                  monkeypatch):
    """`by_day`'s labels come from the row's own `ts`, and every row outside
    the window has already been dropped -- so it is bounded by `MAX_DAYS`,
    which is larger than `MAX_BUCKETS`. Capping it would fold the far end of a
    year-long trend into a bucket called "other" and draw it as a day."""
    monkeypatch.setattr(metrics, "MAX_BUCKETS", 2)
    for day in ("2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"):
        _call(100, day=day)

    days = [b["key"] for b in metrics.performance(366)["by_day"]]
    assert days == ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"]


def test_every_half_of_the_report_reads_the_same_window(home, monkeypatch):
    """The ledger read, the error read and the reported header used to resolve
    "the last N days" from three separate clock reads. Across UTC midnight they
    disagreed: a p90 from one window beside an error count from another, under
    a header naming a third. The window is resolved once and handed to both."""
    asked: dict[str, str] = {}
    real = usage.calls

    def spy(days, campaign="", *, since="", until=""):
        asked.update(since=since, until=until)
        return real(days, campaign, since=since, until=until)

    monkeypatch.setattr(usage, "calls", spy)
    _call(100)

    report = metrics.performance(7)
    assert asked == {"since": report["since"], "until": report["until"]}
    assert report["since"] == report["errors"]["since"]
    assert report["until"] == report["errors"]["until"]
