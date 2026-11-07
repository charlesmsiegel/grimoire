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
    assert parts["deletion-explorations"] == {"test_store_deletion_recovery.py"}
    assert {"test_sync_shards.py", "test_sync_ledger.py"} <= parts["sync"]
    assert {"test_peersync.py", "test_peersync_resolve.py", "test_peersync_ledger.py"} <= parts["peersync"]
    assert {"test_store_replica.py", "test_imagestore_scenarios.py", "test_store_delete.py"} <= parts["stores"]
    assert "test_ci_shards.py" in parts["rest"]


@pytest.mark.skipif(bool(os.environ.get("GRIMOIRE_TEST_SHARD")),
                    reason="checked once, by the unsharded jobs")
def test_collection_under_each_shard_is_disjoint_and_complete():
    whole = _collected("")
    parts = [_collected(shard) for shard in SHARDS]
    assert all(parts) and set().union(*parts) == whole and sum(len(part) for part in parts) == len(whole)


def _compat_cells() -> list[tuple[str, str, str | None]]:
    """The compat matrix's cells, read structurally (no YAML dependency): the ``os`` x ``python`` x
    ``shard`` product, and one cell per ``include`` entry. Each entry must name an OS outside the
    product, so that GitHub adds it as a cell of its own rather than merging it into the product's."""
    text = WORKFLOW.read_text(encoding="utf-8")
    compat = text[text.index("\n  compat:"):]
    matrix = compat[compat.index("      matrix:\n"):compat.index("    steps:")]
    product, _, include = matrix.partition("        include:\n")
    dimensions = {key: [value.strip().strip('"') for value in values.split(",")]
                  for key, values in re.findall(r"\n        (os|python|shard): \[(.+?)\]", product)}
    cells = [(os_name, python, shard) for os_name in dimensions["os"] for python in dimensions["python"]
             for shard in dimensions.get("shard", [None])]
    for os_name, body in re.findall(r"- os: (\S+)\n((?:\s+\w+: .+\n)*)", include):
        assert os_name not in dimensions["os"], f"an include entry for {os_name} would merge into the product"
        python = re.search(r'python: "([\d.]+)"', body)
        shard = re.search(r"shard: (\S+)", body)
        cells.append((os_name, python.group(1), shard.group(1) if shard else None))
    return cells


def test_the_workflow_runs_every_shard_on_every_sharded_os():
    """Every sharded OS and Python runs each shard exactly once and is never also run whole; Ubuntu and
    Windows are sharded and macOS is not; and the compat job's pytest step passes the shard on."""
    cells = _compat_cells()
    assert len(cells) == len(set(cells))
    runs: dict[tuple[str, str], list[str | None]] = {}
    for os_name, python, shard in cells:
        runs.setdefault((os_name, python), []).append(shard)
    for (os_name, python), shards in runs.items():
        assert shards == [None] or sorted(shards) == sorted(SHARDS), f"{os_name} py{python} runs {shards}"
    sharded = {os_name for (os_name, _), shards in runs.items() if shards != [None]}
    assert sharded == {"ubuntu-24.04", "windows-latest"}
    assert {python for os_name, python in runs if os_name == "ubuntu-24.04"} == {"3.12", "3.13"}
    assert runs[("macos-latest", "3.11")] == [None]
    text = WORKFLOW.read_text(encoding="utf-8")
    compat = text[text.index("\n  compat:"):]
    step = compat[compat.index("run: python -m pytest"):]
    assert re.match(r"run: python -m pytest backend/tests -q\n\s+env:\n\s+GRIMOIRE_TEST_SHARD: \$\{\{ matrix.shard \}\}", step)


def test_the_domain_job_runs_every_shard_once():
    """The domain job (Ubuntu, Python 3.11) runs each shard exactly once and passes the shard on."""
    text = WORKFLOW.read_text(encoding="utf-8")
    domain = text[text.index("\n  domain:"):text.index("\n  compat:")]
    shards = re.search(r"\n        shard: \[(.+?)\]", domain)
    assert shards, "the domain job is not sharded"
    listed = [value.strip() for value in shards.group(1).split(",")]
    assert len(listed) == len(set(listed)) and sorted(listed) == sorted(SHARDS)
    step = domain[domain.index("run: python -m pytest"):]
    assert re.match(r"run: python -m pytest backend/tests -q\n\s+env:\n\s+GRIMOIRE_TEST_SHARD: \$\{\{ matrix.shard \}\}", step)
