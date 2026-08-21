"""The three observability endpoints, and the live tail (#154/#155/#156)."""

from __future__ import annotations

import json

import httpx
import pytest

from grimoire.routes import observability
from grimoire.store import logs, usage


@pytest.fixture(autouse=True)
def _quiet_log_state():
    """The threshold and the size cache are module state that outlives one
    app. `client` builds a fresh one per test, so reset around each."""
    logs.forget_file_sizes()
    logs.apply_level("info")
    yield
    logs.forget_file_sizes()
    logs.apply_level("info")


def _today() -> str:
    return usage._today()


# ---- GET /stats (#154) ----
def test_stats_answers_an_untouched_library_with_zeroes_not_a_404(client):
    body = client.get("/api/stats").json()

    assert body["totals"]["calls"] == 0
    assert body["totals"]["p50"] == 0
    assert body["by_task"] == []
    assert body["percentiles"] == [50, 90, 99]


def test_stats_reports_percentiles_per_task(client):
    for ms in (100, 200, 300, 400):
        usage.record(task="chat", model="realm/opus", duration_ms=ms,
                     ts=f"{_today()}T10:00:00Z")

    body = client.get("/api/stats").json()
    assert body["totals"]["calls"] == 4
    assert body["totals"]["p50"] == 250
    assert body["by_task"][0]["key"] == "chat"


def test_stats_can_be_scoped_to_one_campaign_and_window(client):
    usage.record(task="chat", duration_ms=100, campaign="saltmarch",
                 ts=f"{_today()}T10:00:00Z")
    usage.record(task="chat", duration_ms=9000, campaign="realm",
                 ts=f"{_today()}T10:00:00Z")

    body = client.get("/api/stats?days=7&campaign=saltmarch").json()
    assert body["days"] == 7 and body["campaign"] == "saltmarch"
    assert body["totals"]["calls"] == 1 and body["totals"]["max"] == 100


def test_a_silly_window_draws_a_chart_rather_than_a_422(client):
    assert client.get("/api/stats?days=100000").json()["days"] == logs.MAX_DAYS


def test_stats_carries_the_error_store_beside_the_ledgers_own_failures(client):
    usage.record(task="chat", duration_ms=100, status="ok", ts=f"{_today()}T10:00:00Z")
    logs.record("error", "dossier", "empty dossier reply", kind="empty_reply")

    body = client.get("/api/stats").json()
    assert body["totals"]["errors"] == 0        # no CALL failed
    assert body["errors"]["total"] == 1         # but something did


# ---- GET /errors (#156) ----
def test_errors_aggregates_per_module(client):
    logs.record("error", "llm", "429", kind="rate_limit")
    logs.record("error", "llm", "429", kind="rate_limit")
    logs.record("error", "dossier", "empty", kind="empty_reply")

    body = client.get("/api/errors").json()
    assert body["total"] == 3
    assert body["modules"][0] == {"module": "llm", "count": 2,
                                  "kinds": [{"kind": "rate_limit", "count": 2}],
                                  "last": body["modules"][0]["last"],
                                  "last_detail": "429"}


def test_errors_counts_the_window_but_pages_the_rows(client):
    for i in range(9):
        logs.record("error", "llm", f"429 #{i}", kind="rate_limit")

    body = client.get("/api/errors?limit=3").json()
    assert body["total"] == 9
    assert len(body["rows"]) == 3
    assert body["truncated"] is True


def test_errors_can_be_filtered_to_one_module(client):
    logs.record("error", "llm", "429", kind="rate_limit")
    logs.record("error", "dossier", "empty", kind="empty_reply")

    assert client.get("/api/errors?module=dossier").json()["total"] == 1


# ---- GET /logs (#155) ----
def test_logs_returns_rows_newest_first_with_the_windows_vocabulary(client):
    logs.record("info", "runner", "one", ts=f"{_today()}T10:00:00.000Z")
    logs.record("warning", "store.replay", "two", ts=f"{_today()}T11:00:00.000Z")

    body = client.get("/api/logs").json()
    assert [r["message"] for r in body["rows"]] == ["two", "one"]
    assert body["modules"] == ["runner", "store.replay"]
    assert body["levels"] == list(logs.LEVELS)
    assert body["counts"]["warning"] == 1


def test_the_level_filter_is_a_floor_rather_than_an_equality(client):
    logs.record("info", "runner", "quiet")
    logs.record("warning", "runner", "loud")
    logs.record("error", "runner", "louder")

    body = client.get("/api/logs?level=warning").json()
    assert [r["message"] for r in body["rows"]] == ["louder", "loud"]


def test_logs_filters_by_module_text_and_campaign(client):
    logs.record("info", "runner", "tick", campaign="saltmarch")
    logs.record("info", "store.replay", "replayed a scene")

    assert client.get("/api/logs?module=runner").json()["total"] == 1
    assert client.get("/api/logs?q=replayed").json()["total"] == 1
    assert client.get("/api/logs?campaign=saltmarch").json()["total"] == 1


def test_a_route_error_reaches_the_log_through_the_meter(client):
    """End to end: a failing LLM call is recorded against its task, with no
    call site having had to remember to do it."""
    from grimoire.llm_errors import LLMError

    with pytest.raises(LLMError):
        with usage.meter("suggestions", campaign="saltmarch") as m:
            m.usage.update({"model": "realm/opus"})
            raise LLMError("rate_limit", "429 Too Many Requests")

    body = client.get("/api/logs?level=error").json()
    assert body["rows"][0]["module"] == "suggestions"
    assert body["rows"][0]["kind"] == "rate_limit"


# ---- the level, and the one writer that moves it ----
def test_the_level_endpoint_reports_what_is_being_recorded(client):
    body = client.get("/api/logs/level").json()

    assert body["level"] == "info"
    assert body["levels"] == list(logs.LEVELS)


def test_saving_the_config_moves_the_threshold_without_a_restart(client):
    assert logs.record("debug", "runner", "before") is None

    client.put("/api/config", json={"log_level": "debug"})

    assert client.get("/api/logs/level").json()["level"] == "debug"
    assert logs.record("debug", "runner", "after") is not None


def test_an_unrecognized_level_leaves_the_threshold_where_it_was(client):
    client.put("/api/config", json={"log_level": "chatty"})

    assert client.get("/api/logs/level").json()["level"] == "info"


# ---- GET /logs/tail ----
#
# On a real socket, not `TestClient`: it buffers a streaming response to
# completion (see `conftest.LiveServer`), and this stream never completes, so
# every assertion about WHEN a frame arrives would hang forever there.
def _frames(chunk: str) -> list[dict]:
    return [json.loads(block[len("data: "):])
            for block in chunk.split("\n\n")
            if block.startswith("data: ")]


def _tail(live_server, query: str = "", *, write=None,
          timeout: float = 10.0) -> list[dict]:
    """Open the tail, run ``write`` once the opening frame has landed, and
    return the frames seen up to the first one carrying rows.

    ``write`` runs *after* the opening cursor frame on purpose: a row written
    before the stream has taken its cursor is backlog, and the tail deliberately
    does not replay backlog.
    """
    seen: list[dict] = []
    with httpx.stream("GET", f"{live_server.url}/api/logs/tail{query}",
                      timeout=timeout) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        opened = False
        for chunk in response.iter_text():
            for frame in _frames(chunk):
                seen.append(frame)
                if not opened:
                    opened = True
                    if write is not None:
                        write()
                elif frame.get("rows"):
                    return seen
        return seen


@pytest.fixture
def fast_tail(monkeypatch):
    """A tail that polls fast enough for a test to watch. The server runs in
    this process, so patching the module global reaches it."""
    monkeypatch.setattr(observability, "TAIL_INTERVAL", 0.02)


def test_the_tail_opens_with_a_cursor_and_no_backlog(live_server, fast_tail):
    """A client opening a tail asked for what happens NEXT; `GET /logs` is how
    it gets the history. Otherwise the two would have to agree on where "now"
    is, and every reconnect would replay the month."""
    logs.record("info", "runner", "history")

    seen = _tail(live_server, write=lambda: logs.record("warning", "runner", "live"))

    assert "cursor" in seen[0] and "rows" not in seen[0]
    rows = [r["message"] for frame in seen for r in frame.get("rows", [])]
    assert rows == ["live"]                 # "history" was never replayed


def test_every_frame_carries_a_cursor_so_a_dropped_one_can_be_resumed_from(
        live_server, fast_tail):
    seen = _tail(live_server, write=lambda: logs.record("warning", "runner", "live"))

    assert all(frame.get("cursor") for frame in seen)


def test_the_tail_applies_the_same_filters_the_page_does(live_server, fast_tail):
    def write():
        logs.record("info", "runner", "noise")
        logs.record("error", "llm", "429", kind="rate_limit")

    seen = _tail(live_server, "?level=error", write=write)

    rows = [r["message"] for frame in seen for r in frame.get("rows", [])]
    assert rows == ["429"]


def test_a_cursor_handed_back_resumes_without_missing_a_row(live_server, fast_tail):
    """The reconnect promise: a row written while nothing was listening is
    still delivered when the client comes back with its cursor."""
    start = logs.cursor()
    logs.record("info", "runner", "written while disconnected")

    seen = _tail(live_server, f"?cursor={start}")

    rows = [r["message"] for frame in seen for r in frame.get("rows", [])]
    assert rows == ["written while disconnected"]
