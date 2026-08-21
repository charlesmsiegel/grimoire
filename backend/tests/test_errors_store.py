"""The error store: one writer, one file, and what a window of failures adds
up to per module (#156)."""

from __future__ import annotations

import json

import pytest

from grimoire.store import errors, logs, usage


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.forget_file_sizes()
    logs.apply_level("info")
    yield tmp_path
    logs.forget_file_sizes()
    logs.apply_level("info")


def _log_rows(home):
    return [json.loads(line)
            for path in sorted((home / "logs").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---- one row, in one place ----
def test_an_error_is_recorded_as_one_row_in_the_log_and_nowhere_else(home):
    """#156 asks whether errors get their own store or a view over the log.
    They get the view: two appends are two things that can disagree."""
    errors.record("dossier", "empty_reply", "the model returned nothing",
                  campaign="saltmarch", scene="001-arrival")

    assert not (home / "errors").exists()
    assert not (home / "errors.jsonl").exists()
    row, = _log_rows(home)
    assert row["level"] == "error"
    assert row["module"] == "dossier"
    assert row["kind"] == "empty_reply"
    assert row["message"] == "the model returned nothing"
    assert row["campaign"] == "saltmarch"
    assert row["scene"] == "001-arrival"


def test_no_threshold_can_switch_the_error_store_off(home):
    """Configuration says "errors are recorded whatever this says", and the
    size backstop already promises it. A floor above `error` would have made
    both statements false -- and the failures this store exists for are the
    ones nothing else in the app records at all."""
    for asked in logs.LEVELS:
        assert logs.apply_level(asked) in logs.FLOORS
        assert errors.record("dossier", "empty_reply", f"at {asked}") is not None

    # Quieter levels still obey the floor -- that is what it is for.
    logs.apply_level("error")
    assert logs.record("warning", "runner", "slow") is None


def test_a_quieter_floor_than_error_still_records_everything_above_it(home):
    logs.apply_level("warning")

    assert logs.record("info", "runner", "chatty") is None
    assert logs.record("warning", "runner", "loud") is not None
    assert errors.record("llm", "rate_limit", "429") is not None


def test_a_kind_with_no_detail_still_says_something(home):
    errors.record("llm", "missing_key", "")

    row, = _log_rows(home)
    assert row["message"] == "missing_key"


def test_record_never_raises_when_the_log_cannot_be_written(home, monkeypatch):
    monkeypatch.setattr(logs.atomic, "append_line",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))

    assert errors.record("dossier", "empty_reply", "nothing") is None


# ---- aggregation ----
def test_summary_groups_by_module_and_keeps_each_modules_own_kinds(home):
    """Two flat lists cannot answer "which kind is failing in THAT module":
    rate_limit-in-llm plus empty_reply-in-dossier produces the same pair as the
    other way round."""
    logs.record("error", "llm", "429", kind="rate_limit")
    logs.record("error", "llm", "429", kind="rate_limit")
    logs.record("error", "dossier", "empty", kind="empty_reply")

    out = errors.summary(30)
    assert out["total"] == 3
    assert [m["module"] for m in out["modules"]] == ["llm", "dossier"]
    assert out["modules"][0]["count"] == 2
    assert out["modules"][0]["kinds"] == [{"kind": "rate_limit", "count": 2}]
    assert out["modules"][1]["kinds"] == [{"kind": "empty_reply", "count": 1}]


def test_a_warning_is_not_an_error(home):
    logs.record("warning", "runner", "slow provider")
    logs.record("error", "llm", "429", kind="rate_limit")

    assert errors.summary(30)["total"] == 1


def test_a_failure_with_no_kind_gets_a_bucket_rather_than_being_dropped(home):
    logs.record("error", "store.overlay", "tombstones unreadable")

    out = errors.summary(30)
    assert out["kinds"] == [{"kind": "unspecified", "count": 1}]


def test_counts_cover_the_window_and_the_list_is_only_a_page(home):
    for i in range(12):
        logs.record("error", "llm", f"429 #{i}", kind="rate_limit")

    out = errors.summary(30, rows=5)
    assert out["total"] == 12
    assert len(out["rows"]) == 5
    assert out["truncated"] is True
    assert next(r["message"] for r in out["rows"]) == "429 #11"   # newest first


def test_the_daily_series_is_the_trend_half(home):
    logs.record("error", "llm", "a", kind="network", ts="2026-08-14T01:00:00.000Z")
    logs.record("error", "llm", "b", kind="network", ts="2026-08-14T02:00:00.000Z")
    logs.record("error", "llm", "c", kind="network", ts="2026-08-15T01:00:00.000Z")

    out = errors.summary(366)
    assert {"day": "2026-08-14", "count": 2} in out["daily"]
    assert {"day": "2026-08-15", "count": 1} in out["daily"]
    assert out["daily"] == sorted(out["daily"], key=lambda d: d["day"])


def test_summary_can_be_scoped_to_one_module_or_one_campaign(home):
    logs.record("error", "llm", "429", kind="rate_limit", campaign="saltmarch")
    logs.record("error", "dossier", "empty", kind="empty_reply", campaign="realm")

    assert errors.summary(30, module="llm")["total"] == 1
    assert errors.summary(30, campaign="realm")["modules"][0]["module"] == "dossier"


def test_a_silly_window_is_clamped_rather_than_refused(home):
    out = errors.summary(100000)

    assert out["days"] == logs.MAX_DAYS


def test_each_module_carries_its_most_recent_failure(home):
    logs.record("error", "llm", "older", kind="network", ts="2026-08-14T01:00:00.000Z")
    logs.record("error", "llm", "newer", kind="network", ts="2026-08-15T01:00:00.000Z")

    module, = errors.summary(366)["modules"]
    assert module["last_detail"] == "newer"
    assert module["last"] == "2026-08-15T01:00:00.000Z"


# ---- the meter is the LLM choke point ----
def test_a_failed_llm_call_is_recorded_against_the_task_that_made_it(home):
    with pytest.raises(RuntimeError), \
            usage.meter("dossier", campaign="saltmarch", scene="001-arrival") as m:
        m.usage.update({"model": "realm/opus", "prompt_tokens": 10})
        raise RuntimeError("provider said no")

    row, = _log_rows(home)
    assert row["module"] == "dossier"
    assert row["kind"] == "RuntimeError"
    assert row["message"] == "provider said no"
    assert row["campaign"] == "saltmarch" and row["scene"] == "001-arrival"


def test_an_llm_error_carries_its_kind_rather_than_its_class_name(home):
    from grimoire.llm_errors import LLMError

    with pytest.raises(LLMError), usage.meter("chat", campaign="saltmarch") as m:
        m.usage.update({"model": "realm/opus"})
        raise LLMError("rate_limit", "429 Too Many Requests")

    row, = _log_rows(home)
    assert row["kind"] == "rate_limit"
    assert row["message"] == "429 Too Many Requests"


def test_a_failure_before_anything_was_sent_is_recorded_though_no_ledger_row_is(home):
    """`missing_key` never reaches a provider, so `usage` stays empty and the
    ledger records nothing -- and a provider that was never configured is
    exactly the failure a user most needs written down."""
    from grimoire.llm_errors import LLMError

    with pytest.raises(LLMError), usage.meter("tagline"):   # `usage` never filled
        raise LLMError("missing_key", "no connection is configured")

    assert not list((home / "usage").glob("*.jsonl"))
    row, = _log_rows(home)
    assert row["module"] == "tagline" and row["kind"] == "missing_key"


def test_a_cancelled_call_is_not_an_error(home):
    """A user closing a tab mid-suggestion must not read as a provider failing
    them -- the distinction `Meter.__exit__` already draws for the ledger."""
    import asyncio

    with pytest.raises(asyncio.CancelledError), usage.meter("suggestions") as m:
        m.usage.update({"model": "realm/opus"})
        raise asyncio.CancelledError

    assert errors.summary(30)["total"] == 0


def test_a_successful_call_records_no_error(home):
    with usage.meter("chat") as m:
        m.usage.update({"model": "realm/opus", "prompt_tokens": 5})

    assert errors.summary(30)["total"] == 0


def test_one_failure_is_one_error_row_however_the_meter_is_closed(home):
    """`done()` explicitly and then unwound by the same exception files one
    ledger row; it must file one error row too."""
    with pytest.raises(RuntimeError), usage.meter("chat") as m:
        m.usage.update({"model": "realm/opus"})
        m.done("error", "network", detail="connection reset")
        raise RuntimeError("and then this")

    assert errors.summary(30)["total"] == 1
