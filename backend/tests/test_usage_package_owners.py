"""Usage records and summaries share the injected clock."""

import importlib

from grimoire.store import usage


def test_ledger_and_report_use_the_same_clock_owner(monkeypatch, tmp_path):
    clock = importlib.import_module("grimoire.store.usage._clock")
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(clock, "_now", lambda: "2026-08-14T12:00:00Z")
    monkeypatch.setattr(clock, "_today", lambda: "2026-08-14")
    row = usage.record(task="chat", cost_usd=0.01)
    assert row["ts"] == "2026-08-14T12:00:00Z"
    assert usage.summary()["totals"]["cost_usd"] == 0.01
