"""Opt-in: drive the real, unmodified 0.2.1 app through a dry-run capture.

Skipped unless GRIMOIRE_V021_CHECKOUT (a clean checkout of the pinned commit) and
GRIMOIRE_V021_PYTHON (an interpreter with that checkout installed) are set. Never billed.
"""

import json
import os
from pathlib import Path

import pytest

from grimoire.capture import v021 as capture
from grimoire.evals.format2 import parse_run_v2
from grimoire.evals.loader import benchmark_file, load_scenario

CHECKOUT = os.environ.get("GRIMOIRE_V021_CHECKOUT")
PYTHON = os.environ.get("GRIMOIRE_V021_PYTHON")
pytestmark = pytest.mark.skipif(not (CHECKOUT and PYTHON),
                                reason="set GRIMOIRE_V021_CHECKOUT and GRIMOIRE_V021_PYTHON")


def real_state() -> tuple:
    import pwd  # POSIX only; 0.2.1's lock directory comes from the account's home
    state = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "state" / "grimoire"
    return state.exists(), sorted(p.name for p in state.rglob("*")) if state.exists() else []


@pytest.mark.parametrize("kind", ["openrouter", "anthropic"])
def test_real_dry_run(kind, tmp_path):
    before = real_state()
    out = tmp_path / "out"
    assert capture.main(["--scenario", "guild_betrayal", "--v1-checkout", CHECKOUT,
                         "--v1-python", PYTHON, "--dry-run", "--provider", kind,
                         "--out", str(out)]) == 0
    assert real_state() == before
    evidence = json.loads((out / "evidence.json").read_text())
    assert len([r for r in evidence["usage_rows"] if r.get("kind") == "llm"]) == 6
    scenario = load_scenario(benchmark_file("guild_betrayal", "scenario.json"))
    run = parse_run_v2(json.loads((out / "run.json").read_text()), scenario)
    assert len(run.calls) == 6 and run.source.mode == "dry_run"
    assert "findings (0):" in (out / "capture-report.txt").read_text()


def test_real_dry_run_with_a_retry_is_captured_as_incomplete(tmp_path, monkeypatch):
    # 0.2.1 fails the first call once and retries it: the capture still succeeds, the retried
    # call books an unreported failed attempt, and the run is "incomplete", never complete.
    monkeypatch.setattr(capture, "DRIVER_EXTRA_ARGS", ("--fault", "retry-first"))
    out = tmp_path / "out"
    assert capture.main(["--scenario", "guild_betrayal", "--v1-checkout", CHECKOUT,
                         "--v1-python", PYTHON, "--dry-run", "--provider", "openrouter",
                         "--out", str(out)]) == 0
    scenario = load_scenario(benchmark_file("guild_betrayal", "scenario.json"))
    run = parse_run_v2(json.loads((out / "run.json").read_text()), scenario)
    assert run.usage.total.attempts == 7
    assert [a.outcome for a in run.calls[0].attempts] == ["failed", "completed"]
    assert not run.usage.complete
    assert "incomplete" in (out / "capture-report.txt").read_text()
