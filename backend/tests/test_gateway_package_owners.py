"""A package move must retain one owner for the gateway's shared state.

The original interface contract pins exported names separately. These cases
pin the state those names expose and the logical labels saved in the log.
"""

import importlib
import logging

import pytest

from grimoire.store import logs


@pytest.mark.parametrize("name, owner, binding", [
    ("inference", "_tool_policy", "_LOOPING"),
    ("inference", "_decision", "_BACKENDS"),
    ("llm", "_accounting", "_count_stall"),
    ("llm", "_accounting", "_count_executor"),
    ("tool_calls", "_runs", "_RUNNING"),
    ("decisions", "_models", "UNSTATED"),
])
def test_exported_shared_state_has_one_defining_owner(name, owner, binding):
    package = importlib.import_module(f"grimoire.{name}")
    implementation = importlib.import_module(f"grimoire.{name}.{owner}")
    assert getattr(package, binding) is getattr(implementation, binding)


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
