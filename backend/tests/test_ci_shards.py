"""The CI shards together run every test file exactly once (docs/TESTING.md#ci-gate)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import SHARDS, is_exploration

TESTS = Path(__file__).parent
WORKFLOW = TESTS.parents[1] / ".github" / "workflows" / "ci.yml"


def _collected(shard: str) -> set[str]:
    env = {**os.environ, "GRIMOIRE_TEST_SHARD": shard}
    out = subprocess.run([sys.executable, "-m", "pytest", str(TESTS), "--collect-only", "-q", "-p", "no:cacheprovider"],
                         capture_output=True, text=True, env=env, cwd=TESTS.parent, check=True).stdout
    return {line.split("::")[0].replace("\\", "/").rsplit("/", 1)[-1] for line in out.splitlines() if "::" in line}


def test_the_shards_partition_every_test_file():
    files = {path.name for path in TESTS.glob("test_*.py")}
    explorations, rest = {name for name in files if is_exploration(name)}, {name for name in files if not is_exploration(name)}
    assert explorations and rest and explorations | rest == files and not explorations & rest
    assert {"test_sync_faults.py", "test_store_recovery.py"} <= explorations


@pytest.mark.skipif(bool(os.environ.get("GRIMOIRE_TEST_SHARD")),
                    reason="checked once, by the unsharded jobs")
def test_collection_under_each_shard_is_disjoint_and_complete():
    whole = _collected("")
    parts = [_collected(shard) for shard in SHARDS]
    assert parts[0] and parts[1] and not parts[0] & parts[1] and parts[0] | parts[1] == whole


def test_the_workflow_runs_both_windows_shards():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "GRIMOIRE_TEST_SHARD: ${{ matrix.shard }}" in text
    for shard in SHARDS:
        assert f"shard: {shard}" in text
