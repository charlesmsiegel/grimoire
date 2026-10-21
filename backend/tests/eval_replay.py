"""Replay one recorded eval case offline and require it to pass -- the check a
suite makes that the case registered for its task still grades its own
recordings the way they were recorded."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import runner  # noqa: E402


def assert_replays(monkeypatch, tmp_path, case, recording) -> None:
    """Replay `recording` of `case` over a store at `tmp_path`: no error, and
    every check passes (the failing ones are named if not)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    result = runner.replay(case, recording)
    assert not result.error, result.error
    assert result.passed, "\n".join(f"{c.name}: {c.detail}" for c in result.failures)
