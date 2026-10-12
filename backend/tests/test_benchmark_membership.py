"""07-S8's cost-gate harness runs, stays synthetic, and registers nothing."""

import importlib
import importlib.util
import os

from grimoire.store import compiled


def test_membership_cost_gate_runs_on_a_throwaway_store(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "caller"))
    assert importlib.util.find_spec("scripts.benchmark_membership") is not None
    harness = importlib.import_module("scripts.benchmark_membership")
    kinds = dict(compiled.KINDS)
    report = harness.measure(sizes=(3,), repeats=1)
    [row] = report["rows"]
    assert row["groups"] == 3 and row["fresh_ms"] >= 0 and row["capture_ms"] >= 0
    assert set(report) == {"members_per_group", "repeats", "rows", "selected"}
    # The probe kinds were patched in for the run only; the caller's store and
    # environment are untouched.
    assert kinds == compiled.KINDS
    assert os.environ["GRIMOIRE_HOME"] == str(tmp_path / "caller")
    assert not (tmp_path / "caller").exists()
