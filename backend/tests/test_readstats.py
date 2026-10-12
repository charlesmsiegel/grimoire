"""04-S1: request-scoped read counters and the `overview_read` debug row."""

from __future__ import annotations

import contextvars
import threading

import pytest
from fastapi.testclient import TestClient

from grimoire.main import create_app
from grimoire.routes import overview_log
from grimoire.store import frontmatter, logs, readstats, statcache


def test_bump_without_a_collector_is_a_no_op():
    readstats.bump("frontmatter.parse")
    assert readstats.snapshot() == {}
    assert not readstats.active()


def test_collect_counts_and_snapshots():
    with readstats.collect() as counters:
        readstats.bump("a")
        readstats.bump("a", 2)
        readstats.bump("b.c")
        assert readstats.snapshot() == {"a": 3, "b.c": 1}
        assert counters.get("a") == 3 and counters.total("b.") == 1
    assert readstats.snapshot() == {}


def test_nested_collectors_count_apart_and_merge_upward():
    with readstats.collect() as outer:
        readstats.bump("x")
        with readstats.collect() as inner:
            readstats.bump("x")
            readstats.bump("y")
            assert inner.snapshot() == {"x": 1, "y": 1}
        assert inner.snapshot() == {"x": 1, "y": 1}
        assert outer.snapshot() == {"x": 2, "y": 1}


def test_threads_count_into_the_context_they_were_handed():
    with readstats.collect() as counters:
        ctx = contextvars.copy_context()

        def work():
            for _ in range(1000):
                readstats.bump("t")

        threads = [threading.Thread(target=ctx.run, args=(work,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # A bare thread carries no context, so it counts nowhere.
        bare = threading.Thread(target=work)
        bare.start()
        bare.join()
    assert counters.get("t") == 4000


def test_parsers_and_memos_bump_their_counters(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("---\ntitle: Seraphine\n---\nbody\n", encoding="utf-8")
    with readstats.collect() as counters:
        frontmatter.parse_frontmatter(p.read_text(encoding="utf-8"))
        frontmatter.parse_frontmatter_head(p)
        statcache.memo("readstats-test", None, lambda: 1)
        pool: dict = {}
        statcache.memo_stamped(("readstats-kind", str(p)), lambda: (1, None), pool=pool, max_entries=4)
    snap = counters.snapshot()
    assert snap["frontmatter.parse"] == 1 and snap["frontmatter.parse_head"] == 1
    assert snap["statcache.miss.readstats-kind"] == 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    app = create_app()  # installs the configured level, so lower it after
    logs.apply_level("debug")
    yield TestClient(app)
    logs.apply_level("info")


def _overview_rows():
    return [r for r in logs.read(level="debug")["rows"] if r.get("kind") == "overview_read"]


@pytest.mark.parametrize("url,endpoint,scope", [
    ("/api/worlds", "worlds", "library"),
    ("/api/campaigns", "campaigns", "library"),
    ("/api/shell", "shell", "library"),
    ("/api/shell?campaign=saltmarch", "shell", "campaign"),
    ("/api/todo", "todo", "library"),
])
def test_each_overview_read_writes_one_row_with_only_its_stated_fields(client, url, endpoint, scope):
    assert client.get(url).status_code == 200
    [row] = _overview_rows()
    assert set(row) == {"ts", "level", "module", "message", "kind", "endpoint", "scope", "ms", "cache"}
    assert (row["level"], row["endpoint"], row["scope"]) == ("debug", endpoint, scope)
    assert row["cache"] in ("on", "off", "degraded")
    assert isinstance(row["ms"], float)


def test_a_failing_read_still_logs_once(client):
    assert client.get("/api/todo?campaign=missing").status_code == 404
    [row] = _overview_rows()
    assert (row["endpoint"], row["scope"]) == ("todo", "campaign")


def test_cache_state_names_off_and_degraded(monkeypatch):
    monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    with readstats.collect() as counters:
        assert overview_log.cache_state(counters) == "off"
    monkeypatch.delenv("GRIMOIRE_COMPILED_CACHE")
    with readstats.collect() as counters:
        readstats.bump("overview.live_path.overview.scene_turns")
        expected = "degraded" if overview_log.compiled.enabled() else "off"
        assert overview_log.cache_state(counters) == expected


def test_the_row_never_raises_and_never_changes_an_answer(monkeypatch):
    monkeypatch.setattr(overview_log, "cache_state", lambda c: 1 / 0)
    with overview_log.overview_read("worlds", "library"):
        answer = 42
    assert answer == 42
