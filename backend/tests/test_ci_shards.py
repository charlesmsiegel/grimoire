"""The CI shards together run every test file exactly once (docs/TESTING.md#ci-gate)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import SHARDS, shard_of

TESTS = Path(__file__).parent
WORKFLOW = TESTS.parents[1] / ".github" / "workflows" / "ci.yml"


def _collected(shard: str) -> set[str]:
    env = {**os.environ, "GRIMOIRE_TEST_SHARD": shard}
    out = subprocess.run([sys.executable, "-m", "pytest", str(TESTS), "--collect-only", "-q", "-p", "no:cacheprovider"],
                         capture_output=True, text=True, env=env, cwd=TESTS.parent, check=True).stdout
    return {line.split("::")[0].replace("\\", "/").rsplit("/", 1)[-1] for line in out.splitlines() if "::" in line}


def test_the_shards_partition_every_test_file():
    files = {path.name for path in TESTS.glob("test_*.py")}
    parts = {shard: {name for name in files if shard_of(name) == shard} for shard in SHARDS}
    assert all(parts.values()) and set().union(*parts.values()) == files
    assert sum(len(part) for part in parts.values()) == len(files)
    assert {"test_sync_faults.py", "test_import_v1_recovery.py"} <= parts["explorations"]
    assert {"test_store_recovery.py", "test_rebasestore_recovery.py"} <= parts["store-explorations"]
    assert {"test_sync_shards.py", "test_peersync.py"} <= parts["sync"]
    assert {"test_store_replica.py", "test_imagestore_scenarios.py"} <= parts["stores"]
    assert "test_ci_shards.py" in parts["rest"]


@pytest.mark.skipif(bool(os.environ.get("GRIMOIRE_TEST_SHARD")),
                    reason="checked once, by the unsharded jobs")
def test_collection_under_each_shard_is_disjoint_and_complete():
    whole = _collected("")
    parts = [_collected(shard) for shard in SHARDS]
    assert all(parts) and set().union(*parts) == whole and sum(len(part) for part in parts) == len(whole)


def test_the_workflow_runs_every_windows_shard():
    """Read structurally (no YAML dependency): the compat matrix has exactly one windows-latest entry per
    shard, no other entry is sharded, and the compat job's pytest step passes the shard on."""
    text = WORKFLOW.read_text(encoding="utf-8")
    compat = text[text.index("\n  compat:"):]
    entries = re.findall(r"- os: (\S+)\n((?:\s+\w+: .+\n)*)", compat)
    sharded = sorted((os_name, match.group(1)) for os_name, body in entries
                     for match in [re.search(r"shard: (\S+)", body)] if match)
    assert sharded == sorted(("windows-latest", shard) for shard in SHARDS)
    assert not [os_name for os_name, body in entries if os_name == "windows-latest" and "shard:" not in body]
    step = compat[compat.index("run: python -m pytest"):]
    assert re.match(r"run: python -m pytest backend/tests -q\n\s+env:\n\s+GRIMOIRE_TEST_SHARD: \$\{\{ matrix.shard \}\}", step)
