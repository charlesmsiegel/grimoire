"""The structured log store: what gets written, what never does, and how a
filter and a live tail read it back (#155)."""

from __future__ import annotations

import json
import logging

import pytest

from grimoire.store import logs


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    # Module state: the threshold and the per-file byte counters outlive one
    # test's store, and a count left over from the previous tmp_path would be
    # charged against a file this one has not written yet.
    logs.forget_file_sizes()
    logs.apply_level("info")
    yield tmp_path
    logs.forget_file_sizes()
    logs.apply_level("info")


def _rows(home):
    return [json.loads(line)
            for path in sorted((home / "logs").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---- one row ----
def test_record_appends_a_json_line_to_the_months_log(home):
    logs.record("warning", "grimoire.store.overlay", "tombstones unreadable",
                kind="ValueError", campaign="saltmarch", ts="2026-08-14T10:00:00.500Z")

    assert (home / "logs" / "2026-08.jsonl").exists()
    row, = _rows(home)
    assert row == {"ts": "2026-08-14T10:00:00.500Z", "level": "warning",
                   "module": "store.overlay", "message": "tombstones unreadable",
                   "kind": "ValueError", "campaign": "saltmarch"}


def test_the_package_prefix_is_stripped_but_a_plain_subsystem_name_is_kept(home):
    logs.record("info", "grimoire.runner", "started")
    logs.record("info", "dossier", "refreshed")
    logs.record("info", "grimoire", "boot")

    assert [r["module"] for r in _rows(home)] == ["runner", "dossier", "app"]


def test_empty_identity_fields_are_omitted_rather_than_written_blank(home):
    logs.record("info", "runner", "tick")

    row, = _rows(home)
    assert "campaign" not in row and "scene" not in row and "kind" not in row


def test_extra_fields_ride_along_and_unserializable_ones_cost_only_their_key(home):
    logs.record("info", "runner", "tick", elapsed_ms=12, ok=True, note=None,
                subject=object())

    row, = _rows(home)
    assert row["elapsed_ms"] == 12 and row["ok"] is True and row["note"] is None
    assert "subject" not in row
    assert row["message"] == "tick"     # the row survived the bad field


def test_a_runaway_message_is_clipped_and_says_so(home):
    logs.record("info", "runner", "x" * (logs.MAX_MESSAGE + 500))

    row, = _rows(home)
    assert row["message"].endswith("…[clipped]")
    assert len(row["message"]) < logs.MAX_MESSAGE + 20


def test_a_traceback_is_clipped_from_the_FRONT_so_the_exception_survives(home):
    trace = "old frames\n" * 500 + "ValueError: the part that matters"

    logs.record("error", "runner", "boom", trace=trace)

    row, = _rows(home)
    assert row["trace"].startswith("…[clipped]")
    assert row["trace"].endswith("ValueError: the part that matters")


def test_a_timestamp_whose_month_is_junk_still_lands_in_a_real_month_file(home):
    logs.record("info", "runner", "tick", ts="not-a-date")

    files = [p.name for p in (home / "logs").glob("*.jsonl")]
    assert len(files) == 1 and files[0] != "not-a-.jsonl"


# ---- the threshold ----
def test_a_row_below_the_threshold_is_not_written(home):
    logs.apply_level("warning")

    assert logs.record("info", "runner", "chatty") is None
    assert logs.record("warning", "runner", "loud") is not None
    assert [r["message"] for r in _rows(home)] == ["loud"]


def test_the_threshold_comes_from_the_config_and_moves_when_it_is_rewritten(home):
    from grimoire.store import config
    config.write_config(log_level="debug")

    assert logs.apply_level() == "debug"
    assert logs.enabled("debug")


def test_an_unrecognized_configured_level_falls_back_rather_than_crashing(home):
    from grimoire.store import config
    config.write_config(log_level="chatty")

    assert logs.apply_level() == "info"


# ---- it cannot fail its caller ----
def test_record_returns_none_instead_of_raising_when_the_log_cannot_be_written(
        home, monkeypatch):
    monkeypatch.setattr(logs.atomic, "append_line",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only store")))

    assert logs.record("error", "runner", "boom") is None


def test_a_record_made_while_recording_is_dropped_rather_than_recursing(home):
    """`paths.home()` reads the bootstrap pointer through `store.failsoft`,
    which logs when it is corrupt -- so writing a row can log, which would
    write a row. The latch is what makes that terminate."""
    depth = {"n": 0}
    real_home = logs.paths.home

    def logging_home():
        depth["n"] += 1
        logs.record("error", "store.failsoft", "the pointer is corrupt")
        return real_home()

    logs.paths.home = logging_home
    try:
        logs.record("info", "runner", "tick")
    finally:
        logs.paths.home = real_home

    assert depth["n"] == 1              # the nested call never reached `home` again
    assert [r["message"] for r in _rows(home)] == ["tick"]


# ---- the stdlib bridge ----
def test_the_handler_records_what_the_package_already_logs(home):
    logs.install()
    logging.getLogger("grimoire.store.replay").warning("scene %s is unreadable", "001")

    row, = _rows(home)
    assert row["level"] == "warning"
    assert row["module"] == "store.replay"
    assert row["message"] == "scene 001 is unreadable"


def test_a_third_party_logger_is_not_captured(home):
    """httpx logs full request URLs, and an OpenAI-compatible endpoint can
    carry its key in one. This file is meant to be attachable to a bug report."""
    logs.install()
    logging.getLogger("httpx").error("GET https://api.example/v1?key=sk-secret")

    assert _rows(home) == []


def test_an_exception_arrives_as_a_kind_and_a_traceback(home):
    logs.install()
    try:
        raise ValueError("bad frontmatter")
    except ValueError:
        logging.getLogger("grimoire.store.fork").exception("could not fork")

    row, = _rows(home)
    assert row["kind"] == "ValueError"
    assert "bad frontmatter" in row["trace"]


def test_a_log_call_whose_own_formatting_raises_costs_the_row_and_nothing_else(home):
    """`getMessage()` runs the CALLER's `%` formatting, which can raise from a
    bad log call somewhere else entirely -- and `emit` must not turn that into
    an exception on the path of whatever was merely trying to log.

    Driven through the handler rather than through `logging.getLogger`, because
    pytest attaches its own capture handler to the root logger and *that* one
    does raise here; the assertion is about ours."""
    class Explodes:
        def __str__(self):
            raise RuntimeError("no")

    entry = logging.LogRecord("grimoire.runner", logging.INFO, __file__, 1,
                              "subject=%s", (Explodes(),), None)

    logs.Handler().emit(entry)          # must not raise

    assert _rows(home) == []


def test_install_is_idempotent_so_a_second_app_does_not_double_every_row(home):
    logs.install()
    logs.install()
    logging.getLogger("grimoire.runner").warning("once")

    assert len(_rows(home)) == 1


# ---- the size backstop ----
def test_past_the_cap_only_errors_are_recorded_and_the_cap_says_so(home, monkeypatch):
    monkeypatch.setattr(logs, "MAX_MONTH_BYTES", 400)
    monkeypatch.setattr(logs, "_STAT_EVERY", 1)

    for i in range(40):
        logs.record("info", "runner", f"tick {i}")
    logs.record("info", "runner", "dropped")
    logs.record("error", "runner", "kept")

    messages = [r["message"] for r in _rows(home)]
    assert "dropped" not in messages
    assert "kept" in messages
    assert sum(1 for r in _rows(home) if r.get("kind") == "log_capped") == 1


# ---- reading ----
def _seed(home):
    logs.apply_level("debug")
    logs.record("debug", "runner", "tick one", ts="2026-08-10T01:00:00.000Z")
    logs.record("info", "store.replay", "replayed", ts="2026-08-11T01:00:00.000Z")
    logs.record("warning", "runner", "slow provider", ts="2026-08-12T01:00:00.000Z")
    logs.record("error", "llm", "rate limited", kind="rate_limit",
                campaign="saltmarch", ts="2026-08-13T01:00:00.000Z")


def test_read_returns_rows_newest_first(home):
    _seed(home)

    out = logs.read(since="2026-08-01", until="2026-08-31")
    assert [r["message"] for r in out["rows"]] == [
        "rate limited", "slow provider", "replayed", "tick one"]


def test_read_filters_by_level_floor_module_campaign_and_text(home):
    _seed(home)
    window = {"since": "2026-08-01", "until": "2026-08-31"}

    assert [r["message"] for r in logs.read(level="warning", **window)["rows"]] == [
        "rate limited", "slow provider"]
    assert [r["message"] for r in logs.read(module="runner", **window)["rows"]] == [
        "slow provider", "tick one"]
    assert [r["message"] for r in logs.read(campaign="saltmarch", **window)["rows"]] == [
        "rate limited"]
    assert [r["message"] for r in logs.read(contains="RATE", **window)["rows"]] == [
        "rate limited"]


def test_the_filter_vocabulary_describes_the_window_not_the_page(home):
    """A dropdown built from the newest 200 rows loses an option the moment
    something else gets chatty."""
    _seed(home)

    out = logs.read(since="2026-08-01", until="2026-08-31", limit=1)
    assert len(out["rows"]) == 1
    assert out["truncated"] is True
    assert out["modules"] == ["llm", "runner", "store.replay"]
    assert out["counts"] == {"debug": 1, "info": 1, "warning": 1, "error": 1, "critical": 0}
    assert out["total"] == 4


def test_a_row_stamped_later_in_the_until_day_is_still_inside_the_window(home):
    logs.record("info", "runner", "late", ts="2026-08-12T23:59:59.999Z")

    out = logs.read(since="2026-08-12", until="2026-08-12")
    assert [r["message"] for r in out["rows"]] == ["late"]


def test_a_torn_line_costs_that_line_and_not_the_view(home):
    logs.record("info", "runner", "good", ts="2026-08-12T01:00:00.000Z")
    with (home / "logs" / "2026-08.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"ts": "2026-08-12T02:00:00.0\n')

    out = logs.read(since="2026-08-01", until="2026-08-31")
    assert [r["message"] for r in out["rows"]] == ["good"]


def test_a_reversed_day_range_is_read_rather_than_refused(home):
    logs.record("info", "runner", "tick", ts="2026-08-12T01:00:00.000Z")

    out = logs.read(since="2026-08-31", until="2026-08-01")
    assert [r["message"] for r in out["rows"]] == ["tick"]


def test_a_window_skips_month_files_it_cannot_overlap(home, monkeypatch):
    logs.record("info", "runner", "july", ts="2026-07-01T01:00:00.000Z")
    logs.record("info", "runner", "august", ts="2026-08-01T01:00:00.000Z")

    out = logs.read(since="2026-08-01", until="2026-08-31")
    assert [r["message"] for r in out["rows"]] == ["august"]


# ---- live tailing ----
def test_a_cursor_taken_now_yields_only_what_is_written_after_it(home):
    logs.record("info", "runner", "before")
    start = logs.cursor()
    logs.record("info", "runner", "after")

    out = logs.tail(start)
    assert [r["message"] for r in out["rows"]] == ["after"]
    assert logs.tail(out["cursor"])["rows"] == []


def test_the_tail_returns_rows_oldest_first(home):
    start = logs.cursor()
    logs.record("info", "runner", "one")
    logs.record("info", "runner", "two")

    assert [r["message"] for r in logs.tail(start)["rows"]] == ["one", "two"]


def test_a_partial_line_is_withheld_until_it_is_whole(home):
    start = logs.cursor()
    path = home / "logs" / f"{logs._now()[:7]}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"ts": "2026-08-12T01:00:00.000Z", "level": "info", '
                    '"module": "runner", "message": "half', encoding="utf-8")

    out = logs.tail(start)
    assert out["rows"] == []

    with path.open("a", encoding="utf-8") as fh:
        fh.write('"}\n')
    assert [r["message"] for r in logs.tail(out["cursor"])["rows"]] == ["half"]


def test_the_tail_follows_the_month_over(home):
    logs.record("info", "runner", "july", ts="2026-07-31T23:59:00.000Z")
    start = "2026-07.jsonl:" + str((home / "logs" / "2026-07.jsonl").stat().st_size)
    logs.record("info", "runner", "august", ts="2026-08-01T00:01:00.000Z")

    out = logs.tail(start)
    assert [r["message"] for r in out["rows"]] == ["august"]
    assert out["cursor"].startswith("2026-08.jsonl:")


def test_an_offset_past_the_end_restarts_the_file_rather_than_reading_garbage(home):
    logs.record("info", "runner", "kept", ts="2026-08-12T01:00:00.000Z")

    out = logs.tail("2026-08.jsonl:999999")
    assert [r["message"] for r in out["rows"]] == ["kept"]


def test_an_unknown_cursor_starts_at_the_end_rather_than_replaying_the_month(home):
    logs.record("info", "runner", "history", ts="2026-08-12T01:00:00.000Z")

    out = logs.tail("2020-01.jsonl:0")
    assert out["rows"] == []
    assert out["cursor"].startswith("2026-08.jsonl:")


def test_the_tail_applies_the_same_filters_the_page_does(home):
    logs.apply_level("debug")
    start = logs.cursor()
    logs.record("debug", "runner", "noise")
    logs.record("error", "llm", "rate limited")

    assert [r["message"] for r in logs.tail(start, level="error")["rows"]] == [
        "rate limited"]
