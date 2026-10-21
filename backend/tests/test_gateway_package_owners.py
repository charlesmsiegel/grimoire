"""Gateway log records keep the labels used by log filters."""

import importlib
import logging

import pytest

from grimoire.store import logs


@pytest.mark.parametrize("name", ["inference", "decisions", "llm"])
def test_logical_gateway_logger_label_is_persisted(name, monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    package = importlib.import_module(f"grimoire.{name}")
    logs.install()
    monkeypatch.setattr(package.log, "level", logging.WARNING)
    package.log.warning("Gateway package ownership probe")
    rows = logs.read(module=name, contains="Gateway package ownership probe")["rows"]
    assert [(row["module"], row["message"]) for row in rows] == [
        (name, "Gateway package ownership probe")]
