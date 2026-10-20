"""Usage keeps one shared accounting vocabulary, memo and clock owner."""

import importlib

import pytest

from grimoire.store import usage


@pytest.mark.parametrize("leaf, binding", [
    ("_rows", "_ZERO"),
    ("_unpriced", "_UNPRICED_POOL"),
])
def test_exported_usage_state_has_one_owner(leaf, binding):
    owner = importlib.import_module(f"grimoire.store.usage.{leaf}")
    assert getattr(usage, binding) is getattr(owner, binding)


def test_ledger_and_report_use_the_same_clock_owner(monkeypatch, tmp_path):
    clock = importlib.import_module("grimoire.store.usage._clock")
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(clock, "_now", lambda: "2026-08-14T12:00:00Z")
    monkeypatch.setattr(clock, "_today", lambda: "2026-08-14")
    row = usage.record(task="chat", cost_usd=0.01)
    assert row["ts"] == "2026-08-14T12:00:00Z"
    assert usage.summary()["totals"]["cost_usd"] == 0.01
