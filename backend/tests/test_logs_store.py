"""The structured log store: what gets written, what never does, and how a
filter and a live tail read it back (#155)."""

from __future__ import annotations

import json
import logging

import pytest

from grimoire.store import errors, logs


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


def test_a_traceback_is_clipped_from_the_front_so_the_exception_survives(home):
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
    assert logs.level() == "debug"


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


def test_installing_the_bridge_does_not_create_the_store(monkeypatch, tmp_path):
    """`install` runs from `create_app`, and grimoire's rule is that nothing
    exists on disk until the first API call that needs it -- the installers end
    by PRINTING where the store will land, which is a promise that building the
    app has not already put it there. Reading the level through
    `config.read_config` broke that: it calls `ensure_home` and materializes a
    default `config.md`."""
    root = tmp_path / "never-created"
    monkeypatch.setenv("GRIMOIRE_HOME", str(root))

    assert logs.install() == "info"

    assert not root.exists()


def test_a_quiet_floor_does_not_take_warnings_off_the_terminal(home):
    """The floor is a setting about a FILE. Raising the logger's own level with
    it would silently stop `logging.lastResort` printing grimoire's warnings to
    stderr, which it has always done and which has nothing to do with what this
    module keeps."""
    logs.install()
    logs.apply_level("error")

    assert logging.getLogger("grimoire").isEnabledFor(logging.WARNING)
    logging.getLogger("grimoire.runner").warning("still reaches stderr")
    assert _rows(home) == []            # ...but not the file


def test_two_threads_recording_at_once_lose_no_rows_and_tear_no_lines(home):
    """`atomic.append_line` publishes a row with one `O_APPEND` write, so
    concurrent writers interleave whole lines rather than halves of one."""
    import threading

    def write(n):
        for i in range(50):
            logs.record("info", f"worker{n}", f"row {n}-{i}")

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = _rows(home)                  # parses every line, so a torn one fails here
    assert len(rows) == 200
    assert len({r["message"] for r in rows}) == 200


def test_the_reentrancy_latch_is_per_thread_not_global(home):
    """A second thread logging while the first is mid-record is not a
    recursion, and silencing it would drop rows under any concurrency at all."""
    import threading

    started, done = threading.Event(), threading.Event()
    real_home = logs.paths.home

    def slow_home():
        # Inside thread A's `record`: hold there while thread B records.
        if threading.current_thread().name == "A":
            started.set()
            done.wait(5)
        return real_home()

    logs.paths.home = slow_home
    try:
        a = threading.Thread(target=lambda: logs.record("info", "runner", "from A"), name="A")
        a.start()
        assert started.wait(5)
        logs.record("info", "runner", "from B")   # main thread, A still latched
        done.set()
        a.join(5)
    finally:
        logs.paths.home = real_home

    assert {r["message"] for r in _rows(home)} == {"from A", "from B"}


def test_a_crafted_message_cannot_forge_a_second_row(home):
    """One row is one line, and `json.dumps` escapes the newlines that would
    otherwise end it early -- so a scene title (or a provider's error text)
    carrying a newline and a JSON object cannot write a row of its own
    invention into the file."""
    logs.record("info", "runner",
                'x"}\n{"ts":"2026-01-01T00:00:00.000Z","level":"error",'
                '"module":"forged","message":"pwned')

    lines = (home / "logs" / f"{logs._now()[:7]}.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert [r["module"] for r in _rows(home)] == ["runner"]


def test_no_third_party_logger_reaches_the_file_however_it_is_named(home):
    """The privacy rule, stated as a property rather than one example: nothing
    outside the package's own logger tree is recorded, because that is where
    request URLs -- and, for some providers, the key inside one -- get logged."""
    logs.install()
    for name in ("httpx", "httpcore", "urllib3", "openai", "uvicorn.access",
                 "root", "grimoirefoo"):
        logging.getLogger(name).error("GET https://api.example/v1?key=sk-secret")

    assert _rows(home) == []


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


def test_picking_a_module_leaves_the_other_modules_in_the_dropdown(home):
    """`modules` is what the filter control is built from, so narrowing it to
    the current selection makes a control with no way back out of itself."""
    _seed(home)

    out = logs.read(module="runner", since="2026-08-01", until="2026-08-31")

    assert [r["module"] for r in out["rows"]] == ["runner", "runner"]
    assert out["modules"] == ["llm", "runner", "store.replay"]


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


def test_rows_are_ordered_by_their_timestamp_not_by_their_place_in_the_file(home):
    """Usually the same thing -- rows are appended as they happen -- and the
    version that assumed it rendered 08-21, 08-18, 08-19, 08-20 while calling
    itself newest-first. Two writers on a synced store with skewed clocks do
    this, and so does a hand-edited file."""
    for day in ("2026-08-21", "2026-08-18", "2026-08-19", "2026-08-20"):
        logs.record("info", "runner", day, ts=f"{day}T10:00:00.000Z")

    out = logs.read(since="2026-08-01", until="2026-08-31")
    assert [r["message"] for r in out["rows"]] == [
        "2026-08-21", "2026-08-20", "2026-08-19", "2026-08-18"]


def test_the_page_is_the_newest_rows_even_when_the_file_is_out_of_order(home):
    """The cap has to pick by timestamp too. Taking "the last `limit` the file
    yielded" would hand back the oldest three here and call them the newest."""
    for day in ("2026-08-21", "2026-08-18", "2026-08-19", "2026-08-20"):
        logs.record("info", "runner", day, ts=f"{day}T10:00:00.000Z")

    out = logs.read(since="2026-08-01", until="2026-08-31", limit=2)
    assert [r["message"] for r in out["rows"]] == ["2026-08-21", "2026-08-20"]
    assert out["truncated"] is True
    assert out["total"] == 4          # ...and the count still saw all of them


def test_rows_sharing_a_millisecond_do_not_make_the_page_raise(home):
    """`heapq` falls through to comparing the row dicts on a tie, which
    raises. Rows are stamped to the millisecond and a burst produces several
    inside one, so the tie is the normal case, not the exotic one."""
    for i in range(5):
        logs.record("info", "runner", f"row {i}", ts="2026-08-21T10:00:00.000Z")

    out = logs.read(since="2026-08-01", until="2026-08-31", limit=3)
    assert len(out["rows"]) == 3


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


def test_a_read_with_no_dates_is_still_bounded(home):
    """An absent `since` used to mean "no lower bound", so the default read
    opened every month file the install had ever written -- a cost that grew
    with the age of the library, on the page somebody opens BECAUSE something
    is wrong."""
    logs.record("info", "runner", "ancient", ts="2019-01-01T00:00:00.000Z")
    logs.record("info", "runner", "recent")

    out = logs.read()
    assert [r["message"] for r in out["rows"]] == ["recent"]
    assert out["since"]                      # a real day, not ""

    # The ceiling holds however the window is asked for. Clamping only the
    # DERIVED `since` left `?since=1970-01-01` reading every month file the
    # install ever wrote -- the same unbounded scan, reachable by typing a date.
    assert len(logs.read(days=4000)["rows"]) == 1
    assert len(logs.read(since="2019-01-01", until="2026-12-31")["rows"]) == 1
    assert logs.read(since="1970-01-01")["since"] >= "2025-"


def test_a_window_skips_month_files_it_cannot_overlap(home, monkeypatch):
    logs.record("info", "runner", "july", ts="2026-07-01T01:00:00.000Z")
    logs.record("info", "runner", "august", ts="2026-08-01T01:00:00.000Z")

    out = logs.read(since="2026-08-01", until="2026-08-31")
    assert [r["message"] for r in out["rows"]] == ["august"]


def test_a_hand_mangled_file_costs_its_bad_lines_and_nothing_else(home):
    """The store is a folder the user can edit, sync and corrupt. Every reader
    here is a report, and a report drawn short beats no report at all."""
    logs.record("info", "runner", "good", ts="2026-08-12T01:00:00.000Z")
    with (home / "logs" / "2026-08.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('not json at all\n')
        fh.write('[1, 2, 3]\n')                                  # json, not a row
        fh.write('"just a string"\n')
        fh.write('{"ts": 12345, "level": 9, "module": null, "message": {"a": 1}}\n')
        fh.write('{"ts": "zzz", "level": "nope", "module": [], "message": "x"}\n')
        fh.write('{"unterminated": ')                            # no newline either

    out = logs.read(since="2026-08-01", until="2026-08-31")
    assert "good" in [r.get("message") for r in out["rows"]]
    assert all(isinstance(r, dict) for r in out["rows"])
    assert errors.summary(366)["total"] >= 0        # and the rollups still draw


def test_a_cursor_naming_a_file_outside_the_log_directory_is_refused(home):
    """The cursor is a filename off the wire. It only ever selects from the
    files the glob found, so a traversal names nothing and starts at the end."""
    logs.record("info", "runner", "kept", ts="2026-08-12T01:00:00.000Z")

    out = logs.tail("../../etc/passwd:0")

    assert out["rows"] == []
    assert out["cursor"].startswith("2026-08.jsonl:")


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


def test_a_burst_bigger_than_one_poll_loses_nothing(home):
    """The tail used to be bounded by ROWS, which cannot be reconciled with a
    byte cursor: it advanced the offset past the rows that did not fit and
    they were gone. Ten rows against a budget that holds three delivered three
    and silently destroyed seven -- under exactly the burst somebody would be
    watching for."""
    start = logs.cursor()
    for i in range(10):
        logs.record("info", "runner", f"row {i}")

    seen, out = [], {"cursor": start, "more": True}
    while out["more"]:
        out = logs.tail(out["cursor"], budget=300)
        seen += [r["message"] for r in out["rows"]]

    assert seen == [f"row {i}" for i in range(10)]


def test_a_partial_poll_says_there_is_more_rather_than_looking_idle(home):
    start = logs.cursor()
    for i in range(10):
        logs.record("info", "runner", f"row {i}")

    assert logs.tail(start, budget=300)["more"] is True
    assert logs.tail(logs.cursor())["more"] is False


def test_a_trailing_partial_line_does_not_leave_the_tail_spinning(home):
    """`more` means rows are WAITING, not that the file holds more bytes. The
    route re-polls immediately while it is true, so a line with no newline --
    a half-written row, or a hand-edited file -- would spin at full speed on a
    byte count that never becomes a row."""
    start = logs.cursor()
    path = home / "logs" / f"{logs._now()[:7]}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"ts": "2026-08-21T10:00:00.000Z", "level": "info", '
                    '"module": "runner", "message": "half', encoding="utf-8")

    out = logs.tail(start)
    assert out["rows"] == []
    assert out["more"] is False

    # And it is still delivered the moment the line is finished.
    with path.open("a", encoding="utf-8") as fh:
        fh.write('"}\n')
    assert [r["message"] for r in logs.tail(out["cursor"])["rows"]] == ["half"]


def test_an_unterminated_line_over_the_budget_does_not_leave_the_tail_spinning(home):
    """The round-six fix covered a SHORT partial line. One longer than the
    budget took a different path: the read filled its budget, reported `more`,
    consumed nothing, and could not advance -- so the route's no-sleep re-poll
    branch spun on it at full speed forever."""
    logs.record("info", "runner", "seed")
    start = logs.cursor()
    path = home / "logs" / f"{logs._now()[:7]}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"message": "' + "x" * 4000)      # no newline, longer than the budget

    for _ in range(3):
        out = logs.tail(start, budget=1000)
        assert out["rows"] == []
        assert out["more"] is False                 # nothing is waiting; go back to sleep


def test_a_line_longer_than_the_budget_still_advances_the_cursor(home):
    """Otherwise one long row wedges the tail permanently: the budget never
    reaches a newline, the offset never moves, and nothing after it arrives."""
    start = logs.cursor()
    logs.record("info", "runner", "x" * 500)
    logs.record("info", "runner", "after")

    out = logs.tail(start, budget=10)
    assert out["rows"] and out["rows"][0]["message"].startswith("xxx")
    assert [r["message"] for r in logs.tail(out["cursor"], budget=10)["rows"]] == ["after"]


def test_a_filtered_tail_consumes_the_log_at_the_same_rate_as_an_open_one(home):
    """Filtering happens after the byte boundary, so a narrow filter cannot
    fall behind a busy log and start reporting minutes-old lines as live."""
    start = logs.cursor()
    for i in range(20):
        logs.record("info", "runner", f"noise {i}")
    logs.record("error", "llm", "429", kind="rate_limit")

    out = logs.tail(start, level="error")
    assert [r["message"] for r in out["rows"]] == ["429"]
    assert out["more"] is False          # every one of those 21 rows was read


def test_the_tail_applies_the_same_filters_the_page_does(home):
    logs.apply_level("debug")
    start = logs.cursor()
    logs.record("debug", "runner", "noise")
    logs.record("error", "llm", "rate limited")

    assert [r["message"] for r in logs.tail(start, level="error")["rows"]] == [
        "rate limited"]
