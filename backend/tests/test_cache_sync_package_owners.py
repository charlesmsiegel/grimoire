"""Warm-up log records retain their logical module label."""

import logging

from grimoire.store import cache_sync, logs


def test_logical_warm_logger_label_is_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.install()
    monkeypatch.setattr(cache_sync._log, "level", logging.WARNING)
    cache_sync._log.warning("Cache sync package ownership probe")
    rows = logs.read(module="store.cache_sync", contains="Cache sync package ownership probe")["rows"]
    assert [(row["module"], row["message"]) for row in rows] == [
        ("store.cache_sync", "Cache sync package ownership probe")]
